"""P1 signed-image admission experiment and telemetry helpers."""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any


ACTIONS = {
    "P1": "Kyverno verifies a digest-pinned Cosign signature against the injected local trust root and fails closed",
}

METRIC = "kyverno_image_validating_policy_results_total"
POLICY = "p1-trusted-signature"
NAMESPACE = "selfheal-p1"


def parse_metrics(text: str, policy: str = POLICY) -> dict[str, float]:
    totals: dict[str, float] = {}
    for line in text.splitlines():
        if not line.startswith(METRIC + "{"):
            continue
        labels_text, value_text = line.split("}", 1)
        labels = dict(re.findall(r'(\w+)="((?:[^"\\]|\\.)*)"', labels_text))
        if labels.get("policy_name") != policy:
            continue
        result = labels.get("result")
        if result:
            totals[result] = totals.get(result, 0.0) + float(value_text.strip().split()[0])
    return totals


def metric_delta(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
    return {key: max(0.0, after.get(key, 0.0) - before.get(key, 0.0))
            for key in set(before) | set(after)}


def pod_manifest(name: str, image: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": NAMESPACE,
                     "labels": {"app": "p1-artifact", "selfheal.lab/case": name}},
        "spec": {
            "restartPolicy": "Never",
            "securityContext": {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [{
                "name": "artifact",
                "image": image,
                "imagePullPolicy": "Always",
                "ports": [{"containerPort": 8085}],
                "readinessProbe": {"httpGet": {"path": "/health", "port": 8085},
                                   "periodSeconds": 1, "failureThreshold": 30},
                "securityContext": {"allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]}},
                "resources": {"requests": {"cpu": "10m", "memory": "16Mi"},
                              "limits": {"cpu": "100m", "memory": "64Mi"}},
            }],
        },
    }


def policy_ready(h: Any) -> bool:
    policy = h.kj("get", "imagevalidatingpolicy", POLICY)
    if not policy.get("metadata"):
        return False
    return policy.get("status", {}).get("conditionStatus", {}).get("ready") is True


def policy_report_pass(h: Any, pod_name: str) -> bool:
    reports = h.kj("-n", NAMESPACE, "get", "policyreport").get("items", [])
    for report in reports:
        scope = report.get("scope") or {}
        if scope.get("name") != pod_name:
            continue
        for result in report.get("results") or []:
            if result.get("policy") == POLICY and result.get("result") == "pass":
                return True
    return False


def metrics(h: Any) -> tuple[dict[str, float], str]:
    with h.PortForward("kyverno", "deploy/kyverno-admission-controller", 8000) as forward:
        with urllib.request.urlopen(f"http://127.0.0.1:{forward.port}/metrics", timeout=10) as response:
            raw = response.read().decode()
    return parse_metrics(raw), raw


def registry_ready() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:5001/v2/", timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def admit(h: Any, name: str, image: str, expect_ready: bool) -> dict[str, Any]:
    h.k("-n", NAMESPACE, "delete", "pod", name, "--ignore-not-found", "--wait=true", timeout=60)
    started = h.now()
    result = h.k("create", "-f", "-", input=json.dumps(pod_manifest(name, image)), timeout=30)
    elapsed = round(h.now() - started, 3)
    record: dict[str, Any] = {
        "returncode": result.returncode,
        "elapsed_s": elapsed,
        "reason": (result.stderr or result.stdout).strip()[:2000],
        "pod_created": False,
        "ready_s": None,
        "image_id": None,
    }
    if result.returncode != 0:
        record["pod_created"] = bool(h.kj("-n", NAMESPACE, "get", "pod", name).get("metadata"))
        return record
    record["pod_created"] = True
    if not expect_ready:
        return record

    def ready() -> dict[str, Any] | None:
        pod = h.kj("-n", NAMESPACE, "get", "pod", name)
        conditions = pod.get("status", {}).get("conditions") or []
        if any(item.get("type") == "Ready" and item.get("status") == "True" for item in conditions):
            return pod
        return None

    pod = h.wait_for(ready, 120, step=1, what=f"P1 pod {name} Ready")
    record["ready_s"] = round(h.now() - started, 3)
    statuses = pod.get("status", {}).get("containerStatuses") or []
    record["image_id"] = statuses[0].get("imageID") if len(statuses) == 1 else None
    return record


def run(h: Any, _lab: Any, profile: str, directory: Path) -> dict[str, Any]:
    t0 = h.now()
    image_state_path = h.ROOT / ".p1" / "images.json"
    signature_state_path = h.ROOT / ".p1" / "signatures.json"
    if not image_state_path.is_file() or not signature_state_path.is_file():
        raise RuntimeError("P1 image or signature preparation evidence is missing")
    image_state = json.loads(image_state_path.read_text())
    preparation = json.loads(signature_state_path.read_text())
    digest = image_state.get("digest")
    images = image_state.get("images") or {}
    policy_present = bool(h.kj("get", "imagevalidatingpolicy", POLICY).get("metadata"))
    if profile == "baseline" and policy_present:
        raise RuntimeError("P1 baseline still has the image policy")
    if profile == "hardened" and not policy_ready(h):
        raise RuntimeError("P1 hardened image policy is not Ready")

    before, before_raw = metrics(h)
    (directory / f"p1-{profile}-metrics-before.txt").write_text(before_raw)
    observations: dict[str, Any] = {}
    if profile == "baseline":
        for case in ("trusted", "unsigned", "invalid", "untrusted"):
            observations[case] = admit(h, f"p1-{profile}-{case}", images[case], True)
        observations["tag_only"] = admit(h, f"p1-{profile}-tag", image_state["tag_only"], True)
    else:
        observations["trusted"] = admit(h, "p1-hardened-trusted", images["trusted"], True)
        for case in ("unsigned", "invalid", "untrusted"):
            observations[case] = admit(h, f"p1-hardened-{case}", images[case], False)
        observations["tag_only"] = admit(h, "p1-hardened-tag", image_state["tag_only"], False)

        h.sh(["docker", "stop", "selfheal-p1-registry"], timeout=30, check=True)
        try:
            observations["verifier_outage"] = admit(
                h, "p1-hardened-recovery", images["recovery"], False)
        finally:
            restored = h.now()
            h.sh(["docker", "start", "selfheal-p1-registry"], timeout=30, check=True)
        h.wait_for(registry_ready, 60, step=0.5, what="P1 registry recovery")
        observations["recovery"] = admit(
            h, "p1-hardened-recovery", images["recovery"], True)
        observations["recovery_s"] = round(h.now() - restored, 3)

    after, after_raw = metrics(h)
    (directory / f"p1-{profile}-metrics-after.txt").write_text(after_raw)
    delta = metric_delta(before, after)
    trusted_name = f"p1-{profile}-trusted"
    report_seen = False
    if profile == "hardened":
        try:
            report_seen = bool(h.wait_for(lambda: policy_report_pass(h, trusted_name), 60, step=2,
                                          what="P1 trusted PolicyReport"))
        except TimeoutError:
            report_seen = False
    cleanup_names = [f"p1-{profile}-{case}" for case in ("trusted", "unsigned", "invalid", "untrusted")]
    cleanup_names.extend((f"p1-{profile}-tag", "p1-hardened-recovery"))
    for name in cleanup_names:
        h.k("-n", NAMESPACE, "delete", "pod", name,
            "--ignore-not-found", "--wait=false", timeout=30)
    return {
        "t0": t0,
        "policy_present": policy_present,
        "policy_ready": policy_ready(h) if policy_present else False,
        "digest": digest,
        "preparation": preparation,
        "admissions": observations,
        "metrics_available": bool(before_raw and after_raw),
        "metric_delta": delta,
        "policy_report_pass": report_seen,
        "trusted_admission_s": (observations.get("trusted") or {}).get("elapsed_s"),
        "recovery_s": observations.get("recovery_s"),
        "window": "admission requests, verifier outage, registry restoration, and runtime digest check",
    }
