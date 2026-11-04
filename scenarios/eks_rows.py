"""The EKS-only rows: A4, S1b, S4, S5 and R2.

Same contract as the rows in run.py: each injects one fault, observes what the
system does with its own clock, and returns observations; judge.py decides.
Every function takes `h`, the runner module, for its helpers (kubectl, the
prober, wait_for, the abort line), so the rows share one implementation of
measurement with kind.

AWS calls go through the AWS CLI with the lab account's profile (AWS_PROFILE,
set by `make demo TARGET=eks`). Terraform outputs are read with MGMT_PROFILE.
"""
from __future__ import annotations

import gzip
import json
import os
import subprocess
import threading
import time
from collections import Counter
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAB_TAG = "selfheal.lab/managed"


# --------------------------------------------------------------------------- #
# AWS plumbing
# --------------------------------------------------------------------------- #

def aws(*args: str, timeout: int = 60, check: bool = True) -> dict:
    p = subprocess.run(["aws", *args, "--output", "json"], capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        if check:
            raise RuntimeError(f"aws {' '.join(args[:2])}: {p.stderr.strip()[:300]}")
        return {}
    return json.loads(p.stdout or "{}")


@lru_cache(maxsize=1)
def outputs() -> dict:
    env = {**os.environ, "AWS_PROFILE": os.environ.get("MGMT_PROFILE", os.environ.get("AWS_PROFILE", ""))}
    p = subprocess.run(["terraform", f"-chdir={ROOT / 'infra' / 'aws'}", "output", "-json", "runner"],
                       capture_output=True, text=True, timeout=60, env=env)
    if p.returncode != 0:
        raise RuntimeError(f"terraform output runner: {p.stderr.strip()[:300]}")
    return json.loads(p.stdout)


def instance_of(h, node: str) -> str:
    pid = h.kj("get", "node", node).get("spec", {}).get("providerID", "")
    return pid.rsplit("/", 1)[-1]


def workload_nodes(h) -> list[dict]:
    out = []
    for n in h.kj("get", "nodes", "-l", "selfheal.lab/nodes=workload").get("items", []):
        ready = any(c["type"] == "Ready" and c["status"] == "True" for c in n["status"].get("conditions", []))
        out.append({"name": n["metadata"]["name"], "ready": ready,
                    "unschedulable": bool(n["spec"].get("unschedulable")),
                    "deleting": bool(n["metadata"].get("deletionTimestamp"))})
    return out


# --------------------------------------------------------------------------- #
# profile switch: the AWS-side half of "only manifests and policy change"
# --------------------------------------------------------------------------- #

def set_profile(h, profile: str) -> None:
    """The two AWS-side controls that differ between the runs (ADR-0001
    addendum). Terraform creates both in their hardened state and ignores
    these fields, so the runner owns them during a session."""
    o = outputs()
    hardened = profile == "hardened"
    if hardened:
        aws("events", "enable-rule", "--name", o["findings_rule"])
    else:
        aws("events", "disable-rule", "--name", o["findings_rule"])
    conf = aws("configservice", "describe-remediation-configurations",
               "--config-rule-names", o["config_rule"])["RemediationConfigurations"][0]
    conf["Automatic"] = hardened
    for k in ("Arn", "CreatedByService"):
        conf.pop(k, None)
    aws("configservice", "put-remediation-configurations", "--remediation-configurations", json.dumps([conf]))
    h.log(f"  aws: findings rule {'enabled' if hardened else 'disabled'}, "
          f"automatic remediation {'on' if hardened else 'off'}")


def steady(h) -> None:
    """EKS extras for the steady state: the S4 target is closed, no FIS
    experiment runs, no workload node is cordoned or quarantined, the db
    probe is clean."""
    o = outputs()
    sg = aws("ec2", "describe-security-groups", "--group-ids", o["exposure_sg"])["SecurityGroups"][0]
    if sg.get("IpPermissions"):
        aws("ec2", "revoke-security-group-ingress", "--group-id", o["exposure_sg"],
            "--ip-permissions", json.dumps(sg["IpPermissions"]))
        h.log("  steady: closed the S4 target group")
    running = [e["id"] for e in aws("fis", "list-experiments")["experiments"]
               if e["state"]["status"] in ("initiating", "running")
               and e.get("experimentTemplateId") == o["fis_template_id"]]
    for e in running:
        aws("fis", "stop-experiment", "--id", e)
    for n in workload_nodes(h):
        if n["unschedulable"] and not n["deleting"]:
            h.log(f"  steady: node {n['name']} is still cordoned (an earlier R2?), replacing it")
            eradicate_node(h, n["name"])
    h.k("-n", h.NS, "rollout", "status", "deploy/dbprobe", "--timeout=180s", timeout=200)


def eradicate_node(h, node: str) -> None:
    """Lift termination protection and let Karpenter replace the node."""
    iid = instance_of(h, node)
    if iid.startswith("i-"):
        aws("ec2", "modify-instance-attribute", "--instance-id", iid, "--no-disable-api-termination", check=False)
    claim = h.kj("get", "node", node).get("metadata", {}).get("labels", {}).get("karpenter.sh/nodeclaim")
    h.k("annotate", "node", node, "karpenter.sh/do-not-disrupt-", "karpenter.sh/do-not-repair-")
    if claim:
        h.k("delete", "nodeclaim", claim, "--wait=false")
    else:
        h.k("delete", "node", node, "--wait=false")


# --------------------------------------------------------------------------- #
# A4: FIS terminates the node hosting the most web pods
# --------------------------------------------------------------------------- #

class MetricRelay:
    """Publishes the prober's last-10s error rate as a 1s-resolution
    CloudWatch metric, the FIS stop condition's input. Started before the
    experiment so the alarm is OK at start; if the runner dies the metric goes
    missing, which the alarm treats as breaching."""

    def __init__(self, h, lab) -> None:
        self.h, self.lab, self.stop = h, lab, threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        o = outputs()
        while not self.stop.is_set():
            try:
                st = self.lab.stats("http", self.h.now() - 10, self.h.now())
                rate = 100.0 * (st.get("error_rate") or 0.0)
                aws("cloudwatch", "put-metric-data", "--namespace", o["prober_metric_ns"],
                    "--metric-data", json.dumps([{"MetricName": "ErrorRate", "Value": rate, "Unit": "Percent",
                                                  "StorageResolution": 1,
                                                  "Dimensions": [{"Name": "Cluster", "Value": "selfheal"}]}]),
                    check=False)
            except Exception:  # noqa: BLE001 - a gap is treated as breaching by the alarm
                pass
            self.stop.wait(5)

    def __enter__(self) -> "MetricRelay":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop.set()
        self.thread.join(timeout=15)


def row_a4(h, lab, profile: str, d: Path) -> dict:
    o = outputs()
    serving = [p for p in h.pods() if p["ready"]]
    node, n_web = Counter(p["node"] for p in serving).most_common(1)[0]
    iid = instance_of(h, node)
    desired = len(serving)
    aws("ec2", "create-tags", "--resources", iid, "--tags", "Key=selfheal.lab/a4-target,Value=true")
    with MetricRelay(h, lab):
        h.wait_for(lambda: aws("cloudwatch", "describe-alarms", "--alarm-names", o["prober_alarm"])
                   ["MetricAlarms"][0]["StateValue"] == "OK", 180, step=10, what="the FIS stop-condition alarm to be OK")
        t0 = h.now()
        exp = aws("fis", "start-experiment", "--experiment-template-id", o["fis_template_id"],
                  "--tags", "Project=k8s-self-healing-platform")["experiment"]["id"]
        ev = {"terminated": None, "node_gone": None, "replacement": None, "recovered": None, "state": None}
        nodes_before = {n["name"] for n in workload_nodes(h)}

        def poll(t: float) -> None:
            st = aws("fis", "get-experiment", "--id", exp)["experiment"]["state"]
            ev["state"] = st
            if ev["terminated"] is None:
                ins = aws("ec2", "describe-instances", "--instance-ids", iid, check=False)
                state = (((ins.get("Reservations") or [{}])[0].get("Instances") or [{}])[0].get("State") or {}).get("Name")
                if state in ("shutting-down", "terminated"):
                    ev["terminated"] = t
            if ev["node_gone"] is None and not h.kj("get", "node", node):
                ev["node_gone"] = t
            if ev["replacement"] is None:
                new = [n for n in workload_nodes(h) if n["name"] not in nodes_before and n["ready"]]
                if new:
                    ev["replacement"] = (t, new[0]["name"])
            if ev["recovered"] is None and ev["terminated"]:
                ready = [p for p in h.pods() if p["ready"] and p["node"] != node]
                if len(ready) >= desired:
                    ev["recovered"] = t

        def done() -> bool:
            return ev["recovered"] is not None and ev["replacement"] is not None and h.now() - ev["recovered"] > 20

        h.watch(lab, t0 + 420, poll, done, step=5)
        # Fixed window (phase 1 lesson: stopping early inflates error rates).
        time.sleep(max(0.0, t0 + 300 - h.now()))
        end = h.now()
    st = h.window(lab, "http", t0, end, d, profile)
    final = aws("fis", "get-experiment", "--id", exp)["experiment"]["state"]
    rel = lambda t: None if t is None else round(t - t0, 1)  # noqa: E731
    return {"t0": t0, "experiment_id": exp, "experiment_state": final, "instance_id": iid, "node": node,
            "web_pods_on_node": n_web, "terminated": ev["terminated"] is not None,
            "stopped_by_alarm": final.get("status") == "stopped" and "stop condition" in (final.get("reason") or "").lower(),
            "node_gone_s": rel(ev["node_gone"]), "recovered_s": rel(ev["recovered"]),
            "replacement_node": ev["replacement"][1] if ev["replacement"] else None,
            "replacement_s": rel(ev["replacement"][0]) if ev["replacement"] else None,
            "error_rate": st["error_rate"] or 0.0, "samples": st["n"], "expected": st["expected"],
            "window": "T0 to T0+300s (fixed)", "impact": st}


# --------------------------------------------------------------------------- #
# S1b: two alternating-users rotations under live database traffic
# --------------------------------------------------------------------------- #

def _rotate(secret: str) -> str:
    """Start a rotation and wait for it to finish. Returns the new
    AWSCURRENT version id."""
    def stages() -> dict:
        return aws("secretsmanager", "describe-secret", "--secret-id", secret).get("VersionIdsToStages", {})

    before = next(v for v, s in stages().items() if "AWSCURRENT" in s)
    aws("secretsmanager", "rotate-secret", "--secret-id", secret)
    deadline = time.time() + 240
    while time.time() < deadline:
        st = stages()
        cur = next((v for v, s in st.items() if "AWSCURRENT" in s), before)
        pending = any("AWSPENDING" in s for s in st.values())
        if cur != before and not pending:
            return cur
        time.sleep(3)
    raise RuntimeError(f"rotation of {secret} did not finish within 240s")


def row_s1b(h, lab, profile: str, d: Path) -> dict:
    o = outputs()
    secret = o["db_app_secret"]
    pod_name = h.kj("-n", h.NS, "get", "pod", "-l", "app=dbprobe")["items"][0]["metadata"]["name"]
    original = json.loads(aws("secretsmanager", "get-secret-value", "--secret-id", secret)["SecretString"])
    with h.PortForward(h.NS, "svc/dbprobe", 8080) as pf:
        t1 = h.now()
        offset = pf.get("/now")["now"] - (t1 + h.now()) / 2
        t0 = h.now()
        ev: dict = {"rotation1": None, "rotation2": None}
        _rotate(secret)
        ev["rotation1"] = h.now()
        new_user = json.loads(aws("secretsmanager", "get-secret-value", "--secret-id", secret)["SecretString"])["username"]
        # Give the client a full chance to see rotation 1 before the original
        # user's password changes: up to 150s, or as soon as it holds the new
        # current credential (the hardened mount does; the baseline env never will).
        try:
            h.wait_for(lambda: (pf.get("/creds").get("usernames") or [""])[0] == new_user, 150, step=5,
                       what="the db probe to hold the rotated credential")
        except Exception:  # noqa: BLE001 - expected in the baseline
            pass
        _rotate(secret)
        ev["rotation2"] = h.now()
        time.sleep(60)
        end = h.now()
        st = pf.get(f"/stats?since={t0 + offset}&until={end + offset}")
    check = h.k("-n", h.NS, "exec", "-i", pod_name, "--", "python3", "/app/dbprobe.py", "--check-login",
                input=json.dumps({"username": original["username"], "password": original["password"]}), timeout=30)
    try:
        login = json.loads(check.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        login = {"error": check.stderr.strip()[:200]}
    (d / f"dbprobe-{profile}.json").write_text(json.dumps({"stats": st, "original_login": login}, indent=2))
    rel = lambda t: None if t is None else round(t - t0, 1)  # noqa: E731
    return {"t0": t0, "rotations": sum(1 for v in ev.values() if v), "rotation1_s": rel(ev["rotation1"]),
            "rotation2_s": rel(ev["rotation2"]), "original_user": original["username"],
            "original_credential_rejected": login.get("login") is False and login.get("auth_error") is True,
            "failed": st["failed"], "dropped": st["dropped"], "users_seen": st["users_seen"],
            "first_error": st.get("first_error"), "samples": st["samples"], "expected": end - t0,
            "window": "T0 (first rotation) to second rotation + 60s",
            "impact": {"n": st["samples"], "errors": st["failed"],
                       "error_rate": (st["failed"] / st["samples"]) if st["samples"] else None}}


# --------------------------------------------------------------------------- #
# S4: a lab security group opened to the internet
# --------------------------------------------------------------------------- #

def row_s4(h, lab, profile: str, d: Path) -> dict:
    o = outputs()
    sg, rule = o["exposure_sg"], o["config_rule"]
    limit = h.THRESHOLDS[h.TARGET]["S4"]["revert_s"]
    perm = [{"IpProtocol": "tcp", "FromPort": 8080, "ToPort": 8080,
             "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "selfheal S4 drift"}]}]

    def public() -> bool:
        g = aws("ec2", "describe-security-groups", "--group-ids", sg)["SecurityGroups"][0]
        return any(r.get("CidrIp") == "0.0.0.0/0" for p in g.get("IpPermissions", []) for r in p.get("IpRanges", []))

    t0 = h.now()
    aws("ec2", "authorize-security-group-ingress", "--group-id", sg, "--ip-permissions", json.dumps(perm))
    injected = public()
    ev: dict = {"noncompliant": None, "reverted": None, "remediation": None}
    trace = []
    deadline = t0 + limit + 60
    while h.now() < deadline:
        t = h.now()
        if ev["noncompliant"] is None:
            res = aws("configservice", "get-compliance-details-by-config-rule", "--config-rule-name", rule,
                      "--compliance-types", "NON_COMPLIANT", check=False).get("EvaluationResults", [])
            if any(r["EvaluationResultIdentifier"]["EvaluationResultQualifier"]["ResourceId"] == sg for r in res):
                ev["noncompliant"] = t
        rem = aws("configservice", "describe-remediation-execution-status", "--config-rule-name", rule,
                  "--resource-keys", json.dumps([{"resourceType": "AWS::EC2::SecurityGroup", "resourceId": sg}]),
                  check=False).get("RemediationExecutionStatuses", [])
        if rem:
            ev["remediation"] = rem[0]
        if ev["reverted"] is None and not public():
            ev["reverted"] = t
        trace.append({"t": round(t - t0, 1), "noncompliant": ev["noncompliant"] is not None,
                      "remediation": (ev["remediation"] or {}).get("State"), "public": ev["reverted"] is None})
        if ev["reverted"] and (ev["remediation"] or {}).get("State") in ("SUCCEEDED", "FAILED"):
            break
        time.sleep(10)
    if public():  # cleanup only after the measurement, never during it
        aws("ec2", "revoke-security-group-ingress", "--group-id", sg, "--ip-permissions", json.dumps(perm), check=False)
    (d / f"s4-{profile}-trace.json").write_text(json.dumps({"trace": trace, "remediation": ev["remediation"]},
                                                            indent=2, default=str))
    rem = ev["remediation"] or {}
    rem_at = rem.get("LastUpdatedTime") or rem.get("InvocationTime")
    rem_epoch = _epoch(rem_at) if rem_at else None
    by_automation = (ev["reverted"] is not None and rem.get("State") == "SUCCEEDED"
                     and (rem_epoch is None or rem_epoch >= t0 - 5))
    rel = lambda t: None if t is None else round(t - t0, 1)  # noqa: E731
    return {"t0": t0, "security_group": sg, "injected": injected, "noncompliant_s": rel(ev["noncompliant"]),
            "reverted_s": rel(ev["reverted"]), "remediation_state": rem.get("State"),
            "reverted_by_automation": by_automation, "window": f"T0 to revert or T0+{limit + 60}s", "impact": {}}


# --------------------------------------------------------------------------- #
# S5: every workload node replaced for a different AMI (Karpenter drift)
# --------------------------------------------------------------------------- #

def row_s5(h, lab, profile: str, d: Path) -> dict:
    nc = h.kj("get", "ec2nodeclass", "workload")
    alias_before = nc["spec"]["amiSelectorTerms"][0]["alias"]
    pinned = nc["metadata"].get("annotations", {}).get("selfheal.lab/pinned-alias", alias_before)
    # Swing between the pinned release and the newest, so both runs drift.
    alias_after = "al2023@latest" if alias_before != "al2023@latest" else pinned
    if alias_after == alias_before:
        raise RuntimeError(f"no AMI to drift to (alias {alias_before}, pinned {pinned})")
    ami_before = sorted({a["id"] for a in nc.get("status", {}).get("amis", [])})
    before = {n["name"] for n in workload_nodes(h)}
    desired = len([p for p in h.pods() if p["ready"]])
    t0 = h.now()
    h.k("patch", "ec2nodeclass", "workload", "--type", "merge",
        "-p", json.dumps({"spec": {"amiSelectorTerms": [{"alias": alias_after}]}}), check=True)
    ev: dict = {"drifted": None, "replaced": None, "max_disrupted": 0, "trace": []}

    def poll(t: float) -> None:
        claims = h.kj("get", "nodeclaims").get("items", [])
        drifted = [c for c in claims if any(x["type"] == "Drifted" and x["status"] == "True"
                                            for x in c.get("status", {}).get("conditions", []))]
        if drifted and ev["drifted"] is None:
            ev["drifted"] = t
        nodes = workload_nodes(h)
        disrupted = sum(1 for n in nodes if n["name"] in before and (n["unschedulable"] or n["deleting"]))
        ev["max_disrupted"] = max(ev["max_disrupted"], disrupted)
        ev["trace"].append({"t": round(t - t0, 1), "drifted": len(drifted), "disrupted": disrupted,
                            "old_left": len([n for n in nodes if n["name"] in before])})
        if ev["replaced"] is None and not [n for n in nodes if n["name"] in before]:
            if len([p for p in h.pods() if p["ready"]]) >= desired:
                ev["replaced"] = t

    limit = h.THRESHOLDS[h.TARGET]["S5"]["replaced_s"]
    h.watch(lab, t0 + limit + 120, poll, lambda: ev["replaced"] is not None and h.now() - ev["replaced"] > 30, step=5)
    end = h.now()
    st = h.window(lab, "http", t0, end, d, profile)
    nc_after = h.kj("get", "ec2nodeclass", "workload")
    with gzip.open(d / f"s5-{profile}-trace.json.gz", "wt") as f:
        json.dump(ev["trace"], f)
    rel = lambda t: None if t is None else round(t - t0, 1)  # noqa: E731
    return {"t0": t0, "alias_before": alias_before, "alias_after": alias_after,
            "ami_before": ",".join(ami_before),
            "ami_after": ",".join(sorted({a["id"] for a in nc_after.get("status", {}).get("amis", [])})),
            "nodes_before": len(before), "drifted": ev["drifted"] is not None, "drifted_s": rel(ev["drifted"]),
            "replaced_s": rel(ev["replaced"]), "max_disrupted": ev["max_disrupted"],
            "failed": st["errors"], "samples": st["n"], "expected": st["expected"],
            "window": "T0 (AMI change) to every node replaced + 30s", "impact": st}


# --------------------------------------------------------------------------- #
# R2: a runtime threat answered by GuardDuty -> EventBridge -> Step Functions
# --------------------------------------------------------------------------- #

# GuardDuty's documented test domain for Backdoor:Runtime/C&CActivity.B!DNS.
C2_TEST_DOMAIN = "guarddutyc2activityb.com"


def _findings_since(t0: float) -> list[dict]:
    det = aws("guardduty", "list-detectors")["DetectorIds"][0]
    crit = {"Criterion": {"resource.eksClusterDetails.name": {"Eq": ["selfheal"]},
                          "updatedAt": {"GreaterThanOrEqual": int((t0 - 60) * 1000)}}}
    ids = aws("guardduty", "list-findings", "--detector-id", det, "--finding-criteria", json.dumps(crit))["FindingIds"]
    if not ids:
        return []
    return aws("guardduty", "get-findings", "--detector-id", det, "--finding-ids", *ids[:50])["Findings"]


def _matches(f: dict, target: dict) -> bool:
    wl = ((f.get("Resource") or {}).get("KubernetesDetails") or {}).get("KubernetesWorkloadDetails") or {}
    return wl.get("Namespace") == "selfheal" and (wl.get("Uid") == target["uid"] or wl.get("Name") == target["name"]
                                                  or wl.get("Uid") in target["owner_uids"])


def row_r2(h, lab, profile: str, d: Path) -> dict:
    o = outputs()
    limit = h.THRESHOLDS[h.TARGET]["R2"]
    serving = [p for p in h.pods() if p["ready"]]
    tp = serving[0]
    raw = h.kj("-n", h.NS, "get", "pod", tp["name"])
    target = {"name": tp["name"], "uid": raw["metadata"]["uid"],
              "owner_uids": [x["uid"] for x in raw["metadata"].get("ownerReferences", [])]}
    node, iid = tp["node"], instance_of(h, tp["node"])
    prober = h.kj("-n", h.NS, "get", "pod", "-l", "app=prober")["items"][0]["metadata"]["name"]
    url_in = f"http://{tp['ip']}:8080/healthz"
    reachable_before = h.reach(prober, "prober", url_in)
    t0 = h.now()
    # The attack: the process inside the web container resolves a command and
    # control domain. python3, not a shell, so R1's Falco rule stays out of it.
    h.k("-n", h.NS, "exec", tp["name"], "-c", "web", "--", "python3", "-c",
        "import socket\nfor _ in range(5):\n    try: socket.getaddrinfo('%s', 443)\n    except OSError: pass"
        % C2_TEST_DOMAIN, timeout=30)
    ev: dict = {"finding": None, "finding_t": None, "isolated": None, "evidence": None, "exec": None}
    prefix = f"evidence/{node}/{tp['name']}/"

    def isolated_now() -> bool:
        p = h.kj("-n", h.NS, "get", "pod", tp["name"])
        labelled = (p.get("metadata", {}).get("labels") or {}).get("quarantine") == "true"
        cordoned = bool(h.kj("get", "node", node).get("spec", {}).get("unschedulable"))
        enis = aws("ec2", "describe-network-interfaces", "--filters", f"Name=attachment.instance-id,Values={iid}"
                   )["NetworkInterfaces"]
        quarantined = bool(enis) and all([g["GroupId"] for g in e["Groups"]] == [o["quarantine_sg"]] for e in enis)
        return labelled and cordoned and quarantined

    deadline = t0 + limit["evidence_s"] + 120
    while h.now() < deadline:
        t = h.now()
        if ev["finding"] is None:
            mine = [f for f in _findings_since(t0) if _matches(f, target)]
            if mine:
                ev["finding"], ev["finding_t"] = mine[0], t
        if ev["exec"] is None:
            ex = aws("stepfunctions", "list-executions", "--state-machine-arn", o["responder_arn"],
                     "--max-results", "10")["executions"]
            ex = [e for e in ex if _epoch(e["startDate"]) >= t0 - 5]
            if ex:
                ev["exec"] = ex[-1]
        if ev["isolated"] is None and isolated_now():
            ev["isolated"] = t
        if ev["evidence"] is None:
            ls = aws("s3api", "list-objects-v2", "--bucket", o["evidence_bucket"], "--prefix", prefix, check=False)
            if any(x["Key"].endswith("/manifest.json") for x in ls.get("Contents", [])):
                ev["evidence"] = t
        if ev["evidence"] is not None and ev["isolated"] is not None:
            break
        time.sleep(10)
    end = h.now()
    reachable_after = h.reach(prober, "prober", url_in) if ev["isolated"] is not None else None
    st = h.window(lab, "http", t0, end, d, profile)

    # Preserve the evidence in the repo before teardown can take the bucket.
    rdir = d / f"r2-{profile}"
    rdir.mkdir(exist_ok=True)
    capture_bytes, manifest = 0, {}
    ls = aws("s3api", "list-objects-v2", "--bucket", o["evidence_bucket"], "--prefix", prefix, check=False)
    for x in ls.get("Contents", []):
        local = rdir / x["Key"].rsplit("/", 1)[-1]
        subprocess.run(["aws", "s3", "cp", f"s3://{o['evidence_bucket']}/{x['Key']}", str(local), "--only-show-errors"],
                       timeout=60)
        if local.name == "manifest.json" and local.exists():
            manifest = json.loads(local.read_text())
            capture_bytes = manifest.get("evidence", {}).get("capture", {}).get("bytes", 0)
    if ev["finding"]:
        (rdir / "finding.json").write_text(json.dumps(ev["finding"], indent=2, default=str))
    if ev["exec"]:
        hist = aws("stepfunctions", "get-execution-history", "--execution-arn", ev["exec"]["executionArn"],
                   "--max-results", "500", check=False)
        (rdir / "execution-history.json").write_text(json.dumps(hist, indent=2, default=str))
    prot = aws("ec2", "describe-instance-attribute", "--instance-id", iid, "--attribute", "disableApiTermination",
               check=False).get("DisableApiTermination", {}).get("Value")
    snaps = aws("ec2", "describe-snapshots", "--owner-ids", "self", "--filters", f"Name=tag:{LAB_TAG},Values=true",
                "Name=tag:selfheal.lab/evidence,Values=source", check=False).get("Snapshots", [])
    snaps = [s for s in snaps if _epoch(s["StartTime"]) >= t0 - 5]
    p = h.pod(tp["name"])
    kept = p is not None and p["running"] and p["restarts"] == tp["restarts"]

    # Eradicate: a human decision in production; here, after the evidence.
    h.k("-n", h.NS, "delete", "pod", tp["name"], "--wait=false")
    if ev["isolated"] is not None or prot:
        eradicate_node(h, node)
    f = ev["finding"] or {}
    rel = lambda t: None if t is None else round(t - t0, 1)  # noqa: E731
    return {"t0": t0, "target_pod": tp["name"], "node": node, "instance_id": iid, "reachable_before": reachable_before,
            "reachable_after": reachable_after,
            "finding": ({"source": "aws.guardduty", "id": f.get("Id"), "type": f.get("Type"),
                         "severity": f.get("Severity"), "first_seen": (f.get("Service") or {}).get("EventFirstSeen"),
                         "sample": _is_sample(f)}
                        if f else None),
            "finding_s": rel(ev["finding_t"]), "execution": (ev["exec"] or {}).get("status"),
            "isolated_s": rel(ev["isolated"]), "evidence_s": rel(ev["evidence"]), "pod_kept": kept,
            "termination_protected": prot is True, "snapshots": len(snaps), "capture_bytes": capture_bytes,
            "error_rate": st["error_rate"] or 0.0, "samples": st["n"], "expected": st["expected"],
            "window": "T0 (attack) to evidence or the deadline", "impact": st}


def _is_sample(f: dict) -> bool:
    """Sample findings (CreateSampleFindings) carry "sample": true inside
    Service.AdditionalInfo.Value, a JSON string."""
    info = (f.get("Service") or {}).get("AdditionalInfo") or {}
    try:
        return bool(json.loads(info.get("Value") or "{}").get("sample"))
    except (ValueError, AttributeError):
        return "sample" in str(info).lower()


def _epoch(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    import datetime as dt
    return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()


ROWS = {"A4": row_a4, "S1b": row_s1b, "S4": row_s4, "S5": row_s5, "R2": row_r2}
ACTIONS = {
    "A4": "Karpenter replaces the node; topology spread and the PDB keep replicas serving; FIS stops on the error-rate alarm",
    "S1b": "both credential versions mounted and re-read per connection; alternating users keep the previous one valid",
    "S4": "AWS Config flags the group; the custom SSM document revokes the public rule",
    "S5": "Karpenter drift replaces nodes one at a time under the budget and the PDB",
    "R2": "GuardDuty Runtime Monitoring -> EventBridge -> Step Functions: isolate pod and node, preserve, investigate",
}
DETECTED = {"A4": "node_gone_s", "S1b": "rotation1_s", "S4": "noncompliant_s", "S5": "drifted_s", "R2": "finding_s"}
RECOVERED = {"A4": "recovered_s", "S1b": "rotation2_s", "S4": "reverted_s", "S5": "replaced_s", "R2": "isolated_s"}
