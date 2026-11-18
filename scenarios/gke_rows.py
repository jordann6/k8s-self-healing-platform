"""The GKE-only row: A4.

Same contract as eks_rows.py and aks_rows.py: the row injects one fault,
observes what the system does with its own clock, and returns observations;
judge.py decides (ADR-0003, "GKE thresholds"). Every function takes `h`, the
runner module, for its helpers (kubectl, the prober, wait_for, the abort
line), so the row shares one implementation of measurement with kind, EKS
and AKS.

GKE runs the in-cluster rows only (ADR-0027, Scope); S1, S4 and R2 on GCP are
the landing zone proofs. Google Cloud calls go through gcloud with the
operator's own sign-in. Terraform outputs come from infra/gcp (`runner`).
Nothing here creates infrastructure: A4 stops and starts one existing VM.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import time
from collections import Counter
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKLOAD_NODES = 4  # fixed pool, autoscaler off (ADR-0027, D3)


# --------------------------------------------------------------------------- #
# Google Cloud plumbing
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=1)
def outputs() -> dict:
    p = subprocess.run(["terraform", f"-chdir={ROOT / 'infra' / 'gcp'}", "output", "-json", "runner"],
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"terraform output runner failed: {p.stderr.strip()}")
    return json.loads(p.stdout)


def gcloud(*args: str, timeout: int = 120, check: bool = True):
    o = outputs()
    p = subprocess.run(["gcloud", *args, "--project", o["project_id"], "--format=json", "--quiet"],
                       capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        if check:
            raise RuntimeError(f"gcloud {' '.join(args[:3])} failed: {p.stderr.strip()[:400]}")
        return None
    out = p.stdout.strip()
    return json.loads(out) if out else None


def epoch(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()


# --------------------------------------------------------------------------- #
# nodes
# --------------------------------------------------------------------------- #

def instance_of(h, node: str) -> tuple[str, str]:
    """(zone, instance name) from the node's providerID:
    gce://<project>/<zone>/<instance>. Resolved at injection time, never from
    a label or tag (ADR-0027 row 9)."""
    pid = h.kj("get", "node", node).get("spec", {}).get("providerID", "")
    parts = pid.removeprefix("gce://").split("/")
    if not pid.startswith("gce://") or len(parts) != 3 or parts[0] != outputs()["project_id"]:
        raise RuntimeError(f"node {node} has no GCE providerID in the lab project ({pid!r})")
    return parts[1], parts[2]


def vm_status(zone: str, name: str) -> str | None:
    v = gcloud("compute", "instances", "describe", name, "--zone", zone, check=False) or {}
    return v.get("status")


def workload_nodes(h) -> list[dict]:
    out = []
    for n in h.kj("get", "nodes", "-l", "selfheal.lab/nodes=workload").get("items", []):
        cond = next((c for c in n["status"].get("conditions", []) if c["type"] == "Ready"), {})
        out.append({"name": n["metadata"]["name"], "ready": cond.get("status") == "True",
                    "unschedulable": bool(n["spec"].get("unschedulable")),
                    "taints": [t.get("key") for t in n["spec"].get("taints", [])]})
    return out


def repair_operations(node: str, since: float) -> list[str]:
    """GKE auto-repair operations touching the node since a time (ADR-0027
    row 17: `gcloud container operations list`, operationReason such as
    AUTO_REPAIR_LONG_UNHEALTHY)."""
    o = outputs()
    ops = gcloud("container", "operations", "list", "--location", o["zone"], check=False) or []
    out = []
    for op in ops:
        kind = op.get("operationType", "")
        if "REPAIR" not in kind:
            continue
        when = op.get("startTime")
        pool = f"nodePools/{o['workload_pool']}"
        if when and epoch(when) >= since - 5 and (node in json.dumps(op) or pool in op.get("targetLink", "")):
            out.append(f"{kind}:{op.get('statusMessage') or op.get('status')}")
    return out


def start_instance(h, node: str, zone: str, name: str) -> None:
    if vm_status(zone, name) != "RUNNING":
        gcloud("compute", "instances", "start", name, "--zone", zone, timeout=600)
    h.wait_for(lambda: any(n["name"] == node and n["ready"] for n in workload_nodes(h)), 600, step=10,
               what=f"node {node} Ready again")


# --------------------------------------------------------------------------- #
# profile switch and steady state
# --------------------------------------------------------------------------- #

def set_profile(h, profile: str) -> None:
    """Nothing Google-side differs between the runs: every GKE control under
    test is a manifest (ADR-0001)."""
    h.log(f"  gke: no cloud-side switch for {profile}")


def steady(h) -> None:
    """GKE extras for the steady state: every workload VM is RUNNING and its
    node Ready and schedulable, all four of them (the pool is fixed, D3)."""
    for n in workload_nodes(h):
        if not n["ready"]:
            zone, name = instance_of(h, n["name"])
            state = vm_status(zone, name)
            if state in ("STOPPING", "STOPPED", "TERMINATED", "SUSPENDED"):
                h.log(f"  steady: node {n['name']} VM is {state} (an earlier A4?), starting it")
                start_instance(h, n["name"], zone, name)
        elif n["unschedulable"]:
            raise RuntimeError(f"workload node {n['name']} is cordoned; nothing in the GKE rows cordons, "
                               "so a person or an upgrade did (inspect before rerunning)")
    h.wait_for(lambda: len([n for n in workload_nodes(h) if n["ready"] and not n["unschedulable"]]) >= WORKLOAD_NODES,
               900, step=15, what=f"{WORKLOAD_NODES} Ready, schedulable workload nodes")


# --------------------------------------------------------------------------- #
# A4: the VM hosting the most web pods is stopped
# --------------------------------------------------------------------------- #

A4_WINDOW_S = 600


def not_ready_since(h, node: str, t0: float, observed: float) -> float | None:
    """When the node went NotReady: the earlier of the runner's observation
    and the condition's own lastTransitionTime, never before T0 (ADR-0003,
    the AKS D3 clock, which GKE shares)."""
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
    zone, name = instance_of(h, node)
    desired = len(serving)
    t0 = h.now()
    # By instance name, chosen now (ADR-0027 row 9). A stop, never a MIG
    # delete-instances: that would shrink the pool's target size.
    gcloud("compute", "instances", "stop", name, "--zone", zone, "--async")
    ev: dict = {"stopped": None, "vm": None, "not_ready": None, "recovered": None, "shutdown_taint": None,
                "vm_restarted": None}
    trace = []

    def poll(t: float) -> None:
        ev["vm"] = vm_status(zone, name)
        if ev["stopped"] is None and ev["vm"] == "TERMINATED":
            ev["stopped"] = t
        # Live check L4: whether the MIG or anything else starts it again.
        if ev["stopped"] is not None and ev["vm_restarted"] is None and ev["vm"] in ("PROVISIONING", "STAGING", "RUNNING"):
            ev["vm_restarted"] = t
        if ev["not_ready"] is None:
            ev["not_ready"] = not_ready_since(h, node, t0, t)
        if ev["shutdown_taint"] is None:
            taints = next((n["taints"] for n in workload_nodes(h) if n["name"] == node), [])
            if "node.cloudprovider.kubernetes.io/shutdown" in taints:
                ev["shutdown_taint"] = t
        if ev["recovered"] is None and ev["not_ready"] is not None:
            ready = [p for p in h.pods() if p["ready"] and p["node"] != node]
            if len(ready) >= desired:
                ev["recovered"] = t
        # The abort line reads only samples after NotReady, and only marks the
        # evidence: a stopped VM cannot be un-stopped mid-row without hiding
        # what happens next.
        if ev["not_ready"] is not None and not h.ABORT:
            st = lab.stats("http", max(t - 10, ev["not_ready"]), t)
            if st["n"] >= 10 and st["error_rate"] is not None and st["error_rate"] > h.ABORT_ERROR_RATE:
                h.log(f"  abort line: {st['error_rate']:.0%} http errors in the last 10s (after NotReady)")
                h.ABORT.update({"t": t, "kind": "http", "n": st["n"], "errors": st["errors"],
                                "error_rate": st["error_rate"], "error_kinds": st.get("error_kinds")})
        trace.append({"t": round(t - t0, 1), "vm": ev["vm"], "not_ready": ev["not_ready"] is not None,
                      "recovered": ev["recovered"] is not None})

    # Fixed window (phase 1 lesson: stopping early inflates error rates).
    while h.now() < t0 + A4_WINDOW_S:
        poll(h.now())
        time.sleep(5)
    end = h.now()
    repairs = repair_operations(node, t0)
    nr = ev["not_ready"]
    st = h.window(lab, "http", nr if nr is not None else t0, end, d, profile)
    gap = h.window(lab, "http", t0, nr, d, f"{profile}-before-notready") if nr is not None else None
    (d / f"a4-{profile}-trace.json").write_text(json.dumps(trace, indent=2))
    h.log(f"  A4: starting {zone}/{name} again")
    start_instance(h, node, zone, name)
    rel = lambda t: None if t is None else round(t - t0, 1)  # noqa: E731
    return {"t0": t0, "node": node, "zone": zone, "instance": name, "web_pods_on_node": n_web,
            "stopped": ev["stopped"] is not None, "vm_status": ev["vm"], "stopped_s": rel(ev["stopped"]),
            "vm_restarted_s": rel(ev["vm_restarted"]),
            "not_ready_s": rel(nr), "shutdown_taint_s": rel(ev["shutdown_taint"]),
            "recovered_s": rel(ev["recovered"]), "repair_operations": repairs,
            "error_rate": st["error_rate"] or 0.0, "samples": st["n"], "expected": st["expected"],
            "gap_error_rate": (gap or {}).get("error_rate"), "gap": gap,
            "window": f"NotReady to T0+{A4_WINDOW_S}s (fixed), T0 to NotReady reported", "impact": st}


ROWS = {"A4": row_a4}
ACTIONS = {
    "A4": "the node controller marks the stopped node NotReady; pods leave after a 30s toleration; the spread "
          "(minDomains 3, nodeTaintsPolicy Honor) places them on the three remaining fixed workload nodes",
}
DETECTED = {"A4": "not_ready_s"}
RECOVERED = {"A4": "recovered_s"}
