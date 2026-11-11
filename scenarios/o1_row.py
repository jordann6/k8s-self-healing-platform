"""O1 resource boundaries, least privilege, alert, recovery, and release proof."""
from __future__ import annotations

import datetime as dt
import json
import time
from typing import Any


ACTIONS = {
    "O1": "namespace budgets and least-privilege roles preserve evidence, deliver an alert, recover the controller, and gate operator release",
}
NS = "selfheal-o1"
OPERATOR = f"system:serviceaccount:{NS}:o1-operator"
# The runner sets IMAGE from gitops/targets/<cloud>/generated/O1_IMAGE on the clouds (ADR-0019).
IMAGE = "selfheal-o1:v1"
NODE_LABEL = "selfheal.lab/o1"
SYSTEM_NODE_SELECTOR = "selfheal.lab/nodes=system"


def parse_marker(text: str, marker: str) -> dict[str, Any]:
    for line in reversed(text.splitlines()):
        if marker in line:
            raw = line.split(marker, 1)[1].strip()
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}
    return {}


def _server_dry_run(h: Any, name: str, resources: dict | None = None) -> Any:
    container: dict[str, Any] = {
        "name": "probe", "image": IMAGE,
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "runAsNonRoot": True,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
    }
    if resources is not None:
        container["resources"] = resources
    manifest = {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": name, "namespace": NS},
        "spec": {"restartPolicy": "Never", "containers": [container]},
    }
    return h.k("create", "-f", "-", "--dry-run=server", "-o", "json",
               input=json.dumps(manifest), timeout=30)


def _can(h: Any, verb: str, resource: str, name: str | None = None) -> bool:
    target = f"{resource}/{name}" if name else resource
    args = ["auth", "can-i", verb, target]
    args.extend(["-n", NS, "--as", OPERATOR])
    result = h.k(*args, timeout=20)
    return result.returncode == 0 and result.stdout.strip() == "yes"


def _operator_patch(h: Any, kind: str, name: str, patch: dict) -> Any:
    return h.k("-n", NS, "--as", OPERATOR, "patch", kind, name, "--type", "merge",
               "-p", json.dumps(patch), timeout=30)


def _target(h: Any) -> dict:
    return h.kj("-n", NS, "get", "pod", "o1-target")


def _quarantined(h: Any) -> bool:
    policy = h.kj("-n", NS, "get", "networkpolicy", "quarantine-ingress")
    return bool(policy.get("metadata")) and policy.get("spec", {}).get("ingress", []) == []


def _probe_target(h: Any) -> bool:
    code = (
        "import urllib.request; "
        "print(urllib.request.urlopen('http://o1-target:8080/healthz', timeout=3).status)"
    )
    result = h.k("-n", NS, "exec", "deploy/o1-alert-receiver", "--", "python3", "-c", code,
                 timeout=10)
    return result.returncode == 0 and result.stdout.strip().endswith("200")


def _iso_timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def label_system_node(h: Any) -> str:
    """Pin O1 to one system-pool node so a replacement controller pulls from a warm cache (ADR-0019).

    Reuses a node that already carries the label, so a rerun never moves the pin."""
    labelled = h.kj("get", "nodes", "-l", f"{NODE_LABEL}=true").get("items", [])
    if labelled:
        return labelled[0]["metadata"]["name"]
    nodes = h.kj("get", "nodes", "-l", SYSTEM_NODE_SELECTOR).get("items", [])
    if not nodes:
        raise RuntimeError(f"no node matches {SYSTEM_NODE_SELECTOR} to carry the O1 pin")
    name = nodes[0]["metadata"]["name"]
    h.k("label", "node", name, f"{NODE_LABEL}=true", "--overwrite", check=True)
    return name


def check_impersonation(h: Any) -> None:
    """The caller must be allowed to impersonate the operator (ADR-0019 decision 5, live check L11).

    `_can` treats any failure as "no", so without this a Forbidden on impersonation would read as a
    hardened pass. The named Lease is the one thing the operator may read in both profiles."""
    result = h.k("auth", "can-i", "get", "lease.coordination.k8s.io/o1-release",
                 "-n", NS, "--as", OPERATOR, timeout=20)
    answer = result.stdout.strip()
    if result.returncode == 0 and answer == "yes":
        return
    detail = (result.stderr or result.stdout).strip()[:300]
    raise RuntimeError(f"O1 precondition: cannot evaluate access as {OPERATOR} ({detail or 'answered ' + answer!r}); "
                       "the runner identity needs the impersonate permission, this is not a hardening verdict")


def run(h: Any, _lab: Any, profile: str, _evidence_dir: Any) -> dict[str, Any]:
    check_impersonation(h)
    t0 = h.now()
    quota = h.kj("-n", NS, "get", "resourcequota", "o1-budget")
    limits = h.kj("-n", NS, "get", "limitrange", "o1-defaults")
    default_probe = _server_dry_run(h, f"o1-default-{profile}")
    default_resources: dict[str, Any] = {}
    if default_probe.returncode == 0:
        default_resources = json.loads(default_probe.stdout)["spec"]["containers"][0].get("resources", {})
    oversized = _server_dry_run(
        h, f"o1-oversized-{profile}",
        {"requests": {"cpu": "9", "memory": "32Mi"},
         "limits": {"cpu": "9", "memory": "32Mi"}},
    )
    rbac = {
        "get_secret": _can(h, "get", "secret", "o1-synthetic-sensitive"),
        "delete_target": _can(h, "delete", "pod", "o1-target"),
        "patch_quarantine": _can(h, "patch", "networkpolicy", "quarantine-ingress"),
        "patch_release": _can(h, "patch", "lease.coordination.k8s.io", "o1-release"),
    }
    direct = _operator_patch(
        h, "networkpolicy", "quarantine-ingress",
        {"spec": {"ingress": [{}]}},
    )
    direct_allowed = direct.returncode == 0
    evidence_before = h.kj("-n", NS, "get", "configmap", "o1-evidence").get("data", {})

    common = {
        "t0": t0,
        "quota_present": bool(quota.get("metadata")),
        "limit_range_present": bool(limits.get("metadata")),
        "default_resources": default_resources,
        "oversized_admitted": oversized.returncode == 0,
        "oversized_reason": (oversized.stderr or oversized.stdout).strip()[:500],
        "rbac": rbac,
        "direct_release_allowed": direct_allowed,
        "evidence_before_release": evidence_before,
    }
    if profile == "baseline":
        common.update({
            "quarantined_after_direct_attempt": _quarantined(h),
            "controller_present": bool(h.kj("-n", NS, "get", "deploy", "o1-controller").get("metadata")),
            "receiver_present": bool(h.kj("-n", NS, "get", "deploy", "o1-alert-receiver").get("metadata")),
        })
        return common

    # A direct target patch must be denied. The named Lease is the only
    # operator mutation, and requesting release before evidence must not act.
    request_at = h.now()
    request = _operator_patch(
        h, "lease.coordination.k8s.io", "o1-release",
        {"metadata": {"annotations": {"o1.security/release": "true"}},
         "spec": {"holderIdentity": "o1-operator"}},
    )
    time.sleep(3)
    gated_before_evidence = _quarantined(h)

    pods = h.kj("-n", NS, "get", "pods", "-l", "app.kubernetes.io/name=o1-controller").get("items", [])
    old_uid = pods[0]["metadata"]["uid"] if pods else None
    failure_at = h.now()
    if pods:
        h.k("-n", NS, "delete", "pod", pods[0]["metadata"]["name"], "--wait=false", check=True)

    def replacement() -> dict | None:
        items = h.kj("-n", NS, "get", "pods", "-l", "app.kubernetes.io/name=o1-controller").get("items", [])
        for item in items:
            ready = any(c.get("type") == "Ready" and c.get("status") == "True"
                        for c in item.get("status", {}).get("conditions", []))
            if ready and item.get("metadata", {}).get("uid") != old_uid:
                return item
        return None

    new_pod = h.wait_for(replacement, 60, step=1, what="O1 controller replacement")
    recovered_at = h.now()
    blocked_before = not _probe_target(h)
    incident_at = h.now()
    h.k("-n", NS, "patch", "pod", "o1-target", "--type", "merge", "-p",
        json.dumps({"metadata": {"annotations": {"o1.security/incident": "true"}}}), check=True)

    def captured() -> dict | None:
        data = h.kj("-n", NS, "get", "configmap", "o1-evidence").get("data", {})
        return data if data.get("target_uid") else None

    evidence = h.wait_for(captured, 30, step=1, what="O1 evidence capture")
    captured_at = _iso_timestamp(evidence.get("captured_at")) or h.now()

    def alert() -> dict | None:
        logs = h.k("-n", NS, "logs", "deploy/o1-alert-receiver", timeout=20).stdout
        found = parse_marker(logs, "O1_ALERT_DELIVERED ")
        return found if found.get("target_uid") else None

    alert_data = h.wait_for(alert, 30, step=1, what="O1 alert delivery")
    alert_at = h.now()
    h.wait_for(lambda: not _quarantined(h), 30, step=1, what="O1 evidence-gated release")
    released_at = h.now()
    connectivity_after = h.wait_for(lambda: _probe_target(h), 30, step=1,
                                    what="O1 post-release connectivity")

    common.update({
        "release_request_accepted": request.returncode == 0,
        "release_request_at": request_at,
        "gated_before_evidence": gated_before_evidence,
        "controller_old_uid": old_uid,
        "controller_new_uid": new_pod.get("metadata", {}).get("uid"),
        "controller_recovered_s": round(recovered_at - failure_at, 3),
        "quarantine_blocked_before": blocked_before,
        "incident_at": incident_at,
        "evidence": evidence,
        "evidence_s": round(captured_at - incident_at, 3),
        "alert": alert_data,
        "alert_s": round(alert_at - captured_at, 3),
        "released_at": released_at,
        "release_s": round(released_at - captured_at, 3),
        "release_after_evidence": released_at >= captured_at,
        "connectivity_after_release": bool(connectivity_after),
    })
    return common
