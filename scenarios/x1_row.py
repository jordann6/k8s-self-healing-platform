"""X1 Quarantine operator experiment and pure helpers (ADR-0026)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

ACTIONS = {
    "X1": "A Quarantine custom resource makes a controller isolate the named pod without restarting it, refuse "
          "a release that comes before evidence, release only on a later request, resume from status after a "
          "controller kill, and refuse to isolate its own pod",
}

NS = "selfheal-x1"
CLIENT = "x1-client"
CLIENT_IMAGE = "selfheal-x1-target:v1"
CONTROLLER = "x1-controller"
EVIDENCE = "x1-evidence"
GROUP = "selfhealing.lab/v1alpha1"
SA = f"system:serviceaccount:{NS}:{CONTROLLER}"
BLOCK_WAIT_S = 40
ISOLATE = {"selfheal.lab/quarantined": "true", "lab/pool": "quarantine"}

PROBE = """
import sys, urllib.request
try:
    with urllib.request.urlopen(f"http://{sys.argv[1]}:8080/healthz", timeout=3) as r:
        print(r.status)
except Exception:
    print("blocked")
"""


def client_manifest() -> dict[str, Any]:
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": CLIENT, "namespace": NS, "labels": {"app": CLIENT}},
        "spec": {
            "automountServiceAccountToken": False,
            "securityContext": {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [{
                "name": "client", "image": CLIENT_IMAGE, "imagePullPolicy": "IfNotPresent",
                "command": ["python3", "-c", "import time; time.sleep(86400)"],
                "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]}},
                "resources": {"requests": {"cpu": "10m", "memory": "16Mi"}, "limits": {"memory": "64Mi"}},
            }],
        },
    }


def quarantine_manifest(name: str, pod: str, request: str | None = None) -> dict[str, Any]:
    release = {"requestId": request} if request else {}
    return {"apiVersion": GROUP, "kind": "Quarantine", "metadata": {"name": name, "namespace": NS},
            "spec": {"podName": pod, "reason": "x1 experiment", "evidenceRef": EVIDENCE, "release": release}}


def evidence_manifest(uid: str, captured_at: str) -> dict[str, Any]:
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": EVIDENCE, "namespace": NS},
            "data": {"target_uid": uid, "captured_at": captured_at}}


def parse_probe(text: str) -> bool:
    """True only for an explicit 200. Anything else, including garbage, is 'not reachable'."""
    lines = text.strip().splitlines()
    return bool(lines) and lines[-1].strip() == "200"


def condition(cr: dict[str, Any], kind: str) -> dict[str, Any] | None:
    for item in (cr.get("status") or {}).get("conditions") or []:
        if item.get("type") == kind:
            return item
    return None


def count_actions(log: str, quarantine: str) -> int:
    """How many times a controller instance patched pod labels for this Quarantine."""
    count = 0
    for line in log.splitlines():
        if line.startswith("X1_LABELS "):
            try:
                if json.loads(line[len("X1_LABELS "):]).get("quarantine") == quarantine:
                    count += 1
            except ValueError:
                continue
    return count


def serving_pods(h: Any) -> list[dict[str, Any]]:
    items = h.kj("-n", NS, "get", "pods", "-l", "app=x1-target,lab/pool=serving").get("items", [])
    return [p for p in items if not p["metadata"].get("deletionTimestamp")
            and p.get("status", {}).get("phase") == "Running" and p["status"].get("podIP")]


def restarts(pod: dict[str, Any]) -> int:
    return sum(int(c.get("restartCount", 0)) for c in (pod.get("status") or {}).get("containerStatuses") or [])


def reachable(h: Any, ip: str) -> bool:
    result = h.k("-n", NS, "exec", CLIENT, "--", "python3", "-c", PROBE, ip, timeout=20)
    return parse_probe(result.stdout)


def ensure_client(h: Any) -> None:
    if not h.kj("-n", NS, "get", "pod", CLIENT).get("metadata"):
        h.k("create", "-f", "-", input=json.dumps(client_manifest()), check=True, timeout=30)
    h.wait_for(lambda: h.k("-n", NS, "wait", "--for=condition=Ready", f"pod/{CLIENT}", "--timeout=5s",
                           timeout=15).returncode == 0, 90, step=2, what="X1 client pod")


def one_serving(h: Any) -> dict[str, Any]:
    def ready() -> dict[str, Any] | None:
        pods = serving_pods(h)
        return pods[0] if len(pods) == 1 else None
    return h.wait_for(ready, 120, step=2, what="X1 exactly one serving target pod")


def wait_blocked(h: Any, ip: str, limit_s: float) -> float | None:
    start = h.now()
    while h.now() - start < limit_s:
        if not reachable(h, ip):
            return round(h.now() - start, 3)
        time.sleep(0.5)
    return None


def wait_reachable(h: Any, ip: str, limit_s: float) -> float | None:
    """Cilium applies a label change to the datapath after a delay, so release is measured, not assumed."""
    start = h.now()
    while h.now() - start < limit_s:
        if reachable(h, ip):
            return round(h.now() - start, 3)
        time.sleep(0.5)
    return None


def get_cr(h: Any, name: str) -> dict[str, Any]:
    return h.kj("-n", NS, "get", "quarantine", name)


def wait_phase(h: Any, name: str, phase: str, limit_s: float) -> dict[str, Any] | None:
    try:
        return h.wait_for(lambda: (cr if (cr := get_cr(h, name)).get("status", {}).get("phase") == phase else None),
                          limit_s, step=0.5, what=f"X1 {name} phase {phase}")
    except TimeoutError:
        return None


def cleanup(h: Any) -> None:
    h.k("-n", NS, "delete", "quarantine", "--all", "--ignore-not-found", "--wait=true", timeout=60)
    h.k("-n", NS, "delete", "configmap", EVIDENCE, "--ignore-not-found", timeout=30)


def run_baseline(h: Any, ip: str, pod: dict[str, Any]) -> dict[str, Any]:
    """The manual flow: a person relabels the pod and, later, relabels it back. Nothing records what
    happened and nothing checks that evidence exists first."""
    name = pod["metadata"]["name"]
    # Argo CD prunes the controller but never the CRD, so absence of the CRD is not the baseline's claim.
    # The claim is that nothing is acting on Quarantines.
    out: dict[str, Any] = {"controller_present": bool(h.kj("-n", NS, "get", "deploy", CONTROLLER).get("metadata")),
                           "reachable_before": reachable(h, ip)}
    args = [f"{k}={v}" for k, v in ISOLATE.items()]
    h.k("-n", NS, "label", "pod", name, "--overwrite", *args, check=True, timeout=30)
    out["blocked_s"] = wait_blocked(h, ip, BLOCK_WAIT_S)
    h.k("-n", NS, "label", "pod", name, "--overwrite", "selfheal.lab/quarantined-", "lab/pool=serving", check=True, timeout=30)
    out["released_reachable_s"] = wait_reachable(h, ip, BLOCK_WAIT_S)
    out["released_without_evidence"] = out["released_reachable_s"] is not None
    out["evidence_checked"] = False
    out["status_available"] = False
    return out


def run_hardened(h: Any, ip: str, pod: dict[str, Any], directory: Path) -> dict[str, Any]:
    name, uid = pod["metadata"]["name"], pod["metadata"]["uid"]
    out: dict[str, Any] = {"reachable_before": reachable(h, ip)}
    # X1a isolation
    t = h.now()
    h.k("apply", "-f", "-", input=json.dumps(quarantine_manifest("q-a", name)), check=True, timeout=30)
    cr = wait_phase(h, "q-a", "Isolated", 30)
    isolate_s = round(h.now() - t, 3) if cr else None
    after = h.kj("-n", NS, "get", "pod", name)
    out["x1a"] = {"phase": (cr or {}).get("status", {}).get("phase"), "isolate_s": isolate_s,
                  "blocked_s": wait_blocked(h, ip, BLOCK_WAIT_S),
                  "same_uid": after.get("metadata", {}).get("uid") == uid,
                  "restarts_unchanged": restarts(after) == restarts(pod),
                  "replacement_serving": len(serving_pods(h)) == 1}
    # X1b release before evidence
    h.k("-n", NS, "patch", "quarantine", "q-a", "--type", "merge",
        "-p", json.dumps({"spec": {"release": {"requestId": "r1"}}}), check=True, timeout=30)
    time.sleep(4)
    cr = get_cr(h, "q-a")
    refused = condition(cr, "ReleaseRefused")
    out["x1b"] = {"phase": cr.get("status", {}).get("phase"), "handled": cr.get("status", {}).get("handledReleaseId"),
                  "refused_reason": (refused or {}).get("reason"), "blocked": not reachable(h, ip)}
    # evidence, then X1c release
    h.k("apply", "-f", "-", input=json.dumps(evidence_manifest(uid, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))),
        check=True, timeout=30)
    preserved = wait_phase(h, "q-a", "Preserved", 30)
    h.k("-n", NS, "patch", "quarantine", "q-a", "--type", "merge",
        "-p", json.dumps({"spec": {"release": {"requestId": "r2"}}}), check=True, timeout=30)
    released = wait_phase(h, "q-a", "Released", 30)
    rstatus = (released or {}).get("status", {})
    labels = h.kj("-n", NS, "get", "pod", name).get("metadata", {}).get("labels", {})
    out["x1c"] = {"preserved": bool(preserved), "released": bool(released),
                  "after_evidence": bool(rstatus.get("releasedAt") and rstatus.get("preservedAt")
                                         and rstatus["releasedAt"] > rstatus["preservedAt"]),
                  "reachable_s": wait_reachable(h, ip, BLOCK_WAIT_S),
                  "labels_restored": labels.get("lab/pool") == "serving" and "selfheal.lab/quarantined" not in labels}
    # X1d resume after a controller kill
    # After the release the ReplicaSet sees two serving pods and deletes one, so wait for exactly one
    # and use it, whichever it is. The second Quarantine is about the controller, not the pod identity.
    target = one_serving(h)
    killed = h.now()
    h.k("-n", NS, "delete", "pod", "-l", f"app={CONTROLLER}", "--grace-period=0", "--force", "--wait=true", timeout=60)
    time.sleep(1)
    h.k("apply", "-f", "-", input=json.dumps(quarantine_manifest("q-d", target["metadata"]["name"])),
        check=True, timeout=30)
    cr = wait_phase(h, "q-d", "Isolated", 60)
    resume_s = round(h.now() - killed, 3) if cr else None
    new_pods = [p["metadata"]["name"] for p in h.kj("-n", NS, "get", "pods", "-l", f"app={CONTROLLER}").get("items", [])
                if not p["metadata"].get("deletionTimestamp")]
    log = h.k("-n", NS, "logs", new_pods[0], timeout=30).stdout if new_pods else ""
    after = h.kj("-n", NS, "get", "pod", target["metadata"]["name"])
    out["x1d"] = {"resumed": bool(cr), "resume_s": resume_s, "label_actions": count_actions(log, "q-d"),
                  "same_uid": after.get("metadata", {}).get("uid") == target["metadata"]["uid"],
                  "controller_pods": len(new_pods)}
    # X1e the controller's own pod, plus its permissions
    own = new_pods[0] if new_pods else ""
    h.k("apply", "-f", "-", input=json.dumps(quarantine_manifest("q-e", own)), check=True, timeout=30)
    cr = wait_phase(h, "q-e", "Failed", 30)
    own_labels = h.kj("-n", NS, "get", "pod", own).get("metadata", {}).get("labels", {})

    def can(verb: str, resource: str) -> bool:
        return h.k("auth", "can-i", verb, resource, "-n", NS, "--as", SA, timeout=20).stdout.strip() == "yes"

    out["x1e"] = {"phase": (cr or {}).get("status", {}).get("phase"), "reason": (cr or {}).get("status", {}).get("reason"),
                  "labels_unchanged": "selfheal.lab/quarantined" not in own_labels,
                  "can_delete_pods": can("delete", "pods"), "can_get_secrets": can("get", "secrets"),
                  "can_patch_pods": can("patch", "pods")}
    return out


def run(h: Any, _lab: Any, profile: str, directory: Path) -> dict[str, Any]:
    t0 = h.now()
    ensure_client(h)
    if profile == "hardened":
        h.wait_for(lambda: h.k("-n", NS, "rollout", "status", f"deploy/{CONTROLLER}", "--timeout=5s",
                               timeout=15).returncode == 0, 120, step=3, what="X1 controller")
    cleanup(h)
    pod = one_serving(h)
    ip = pod["status"]["podIP"]
    try:
        obs = run_baseline(h, ip, pod) if profile == "baseline" else run_hardened(h, ip, pod, directory)
    finally:
        cleanup(h)
        # back to one serving pod for the next profile
        for item in h.kj("-n", NS, "get", "pods", "-l", "app=x1-target").get("items", []):
            h.k("-n", NS, "delete", "pod", item["metadata"]["name"], "--wait=false", timeout=30)
    obs.update({"t0": t0, "profile": profile})
    (directory / f"x1-{profile}.json").write_text(json.dumps(obs, indent=2))
    obs["window"] = "isolation, premature release, evidence, release, controller kill, and the self-isolation refusal"
    return obs
