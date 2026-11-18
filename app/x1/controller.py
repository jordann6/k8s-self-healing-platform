"""X1 Quarantine operator (ADR-0026). Standard library only, like the O1 controller and the R2 worker.

A Quarantine custom resource names one pod. The controller isolates it (relabels it out of the
Service and ReplicaSet selector, which also selects the quarantine NetworkPolicy), waits for an
evidence record that names the same pod UID, and releases only on an explicit request made after
that. Everything it knows lives in the resource's status, so a replacement controller resumes
from status and never from memory."""
from __future__ import annotations

import json
import os
import signal
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

GROUP, VERSION, PLURAL = "selfhealing.lab", "v1alpha1", "quarantines"
API = "https://kubernetes.default.svc"
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
NS_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"

QUARANTINED = "selfheal.lab/quarantined"
POOL = "lab/pool"
PROTECTED = "selfheal.lab/protected"
ISOLATE_LABELS = {QUARANTINED: "true", POOL: "quarantine"}
RESTORE_LABELS = {QUARANTINED: None, POOL: "serving"}

PENDING, ISOLATED, PRESERVED, RELEASED, FAILED = "Pending", "Isolated", "Preserved", "Released", "Failed"
TERMINAL = (RELEASED, FAILED)


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def condition(kind: str, status: str, reason: str, now: str) -> dict[str, str]:
    return {"type": kind, "status": status, "reason": reason, "lastTransitionTime": now}


def merge_conditions(old: list[dict[str, Any]], new: dict[str, str]) -> list[dict[str, Any]]:
    kept = [c for c in old if c.get("type") != new["type"]]
    return kept + [new]


def labels_present(pod: dict[str, Any], wanted: dict[str, str]) -> bool:
    labels = (pod.get("metadata") or {}).get("labels") or {}
    return all(labels.get(key) == value for key, value in wanted.items())


def evidence_for(evidence: dict[str, Any] | None, uid: str) -> str | None:
    """The capture time, only when the record names this exact pod UID (a stale record never counts)."""
    data = (evidence or {}).get("data") or {}
    if data.get("target_uid") == uid and data.get("captured_at"):
        return data["captured_at"]
    return None


def reconcile(cr: dict[str, Any], pod: dict[str, Any] | None, evidence: dict[str, Any] | None,
              now: str) -> dict[str, Any]:
    """Pure decision. Returns {"pod_labels": dict|None, "status": dict|None}; None means do nothing."""
    status = dict(cr.get("status") or {})
    phase = status.get("phase") or PENDING
    spec = cr.get("spec") or {}
    conditions = list(status.get("conditions") or [])
    if phase in TERMINAL:
        return {"pod_labels": None, "status": None}

    def fail(reason: str) -> dict[str, Any]:
        return {"pod_labels": None, "status": {
            "phase": FAILED, "reason": reason,
            "conditions": merge_conditions(conditions, condition("Failed", "True", reason, now))}}

    if pod is None:
        return fail("PodNotFound")
    meta = pod.get("metadata") or {}
    uid = meta.get("uid", "")
    labels = meta.get("labels") or {}
    if labels.get(PROTECTED) == "true":
        return fail("Protected")
    if status.get("targetUID") and status["targetUID"] != uid:
        return fail("PodReplaced")

    if phase == PENDING:
        restarts = sum(int(c.get("restartCount", 0))
                       for c in (pod.get("status") or {}).get("containerStatuses") or [])
        return {"pod_labels": dict(ISOLATE_LABELS), "status": {
            "phase": ISOLATED, "targetUID": uid, "isolatedAt": now, "restartCountAtIsolation": restarts,
            "conditions": merge_conditions(conditions, condition("Isolated", "True", "LabelsApplied", now))}}

    # Isolated or Preserved. Desired state, so a drifted label is put back every pass.
    pod_labels = None if labels_present(pod, ISOLATE_LABELS) else dict(ISOLATE_LABELS)
    out: dict[str, Any] = {}
    captured = evidence_for(evidence, uid)
    if phase == ISOLATED and captured:
        phase = PRESERVED
        out.update({"phase": PRESERVED, "evidenceCapturedAt": captured, "preservedAt": now})
        conditions = merge_conditions(conditions, condition("Preserved", "True", "EvidenceRecorded", now))
        out["conditions"] = conditions
    request = (spec.get("release") or {}).get("requestId")
    if request and request != status.get("handledReleaseId"):
        out["handledReleaseId"] = request
        if phase == PRESERVED and "preservedAt" not in out:
            # A release asked for in a pass after the evidence was already recorded.
            return {"pod_labels": dict(RESTORE_LABELS), "status": {
                **out, "phase": RELEASED, "releasedAt": now,
                "conditions": merge_conditions(conditions, condition("Released", "True", "EvidencePresent", now))}}
        out["conditions"] = merge_conditions(
            out.get("conditions", conditions), condition("ReleaseRefused", "True", "NoEvidence", now))
    return {"pod_labels": pod_labels, "status": out or None}


def api(method: str, path: str, body: dict | None = None) -> dict[str, Any]:
    with open(TOKEN_PATH, encoding="utf-8") as handle:
        token = handle.read().strip()
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(API + path, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        request.add_header("Content-Type", "application/merge-patch+json")
    context = ssl.create_default_context(cafile=CA_PATH)
    with urllib.request.urlopen(request, context=context, timeout=5) as response:
        return json.loads(response.read() or b"{}")


def get_or_none(path: str) -> dict[str, Any] | None:
    try:
        return api("GET", path)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def step(namespace: str) -> int:
    base = f"/apis/{GROUP}/{VERSION}/namespaces/{namespace}/{PLURAL}"
    acted = 0
    for cr in api("GET", base).get("items", []):
        name = cr["metadata"]["name"]
        spec = cr.get("spec") or {}
        pod = get_or_none(f"/api/v1/namespaces/{namespace}/pods/{spec.get('podName', '')}")
        evidence = get_or_none(f"/api/v1/namespaces/{namespace}/configmaps/{spec.get('evidenceRef', '')}")
        plan = reconcile(cr, pod, evidence, stamp())
        if plan["pod_labels"] is not None and pod is not None:
            api("PATCH", f"/api/v1/namespaces/{namespace}/pods/{pod['metadata']['name']}",
                {"metadata": {"labels": plan["pod_labels"]}})
            print("X1_LABELS " + json.dumps({"quarantine": name, "labels": plan["pod_labels"]}), flush=True)
        if plan["status"] is not None:
            api("PATCH", f"{base}/{name}/status", {"status": plan["status"]})
            print("X1_STATUS " + json.dumps({"quarantine": name, "phase": plan["status"].get("phase")}), flush=True)
            acted += 1
    return acted


def main() -> int:
    with open(NS_PATH, encoding="utf-8") as handle:
        namespace = os.environ.get("X1_NAMESPACE") or handle.read().strip()
    # PID 1 ignores SIGTERM unless it installs a handler. Without this a deleted controller keeps
    # reconciling for the whole grace period, so a replacement is not the only one acting.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print("X1_CONTROLLER_STARTED " + namespace, flush=True)
    while True:
        try:
            step(namespace)
        except (KeyError, urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            print(f"X1_RETRY {type(exc).__name__}: {exc}", flush=True)
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
