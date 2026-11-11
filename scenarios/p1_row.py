"""P1 signed-image admission experiment and telemetry helpers."""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any


ACTIONS = {
    "P1": "Kyverno verifies a digest-pinned Cosign signature against the injected local trust root and fails closed",
}

METRIC = "kyverno_image_validating_policy_results_total"
POLICY = "p1-trusted-signature"
NAMESPACE = "selfheal-p1"
TRUST_ROOT = "p1-cosign-trust-root"
KYVERNO_NS = "kyverno"
# ADR-0021: on the clouds the outage is an egress allow-list on the Kyverno
# admission controller. Applying it is the outage, deleting it the restoration.
OUTAGE_POLICY = "p1-verifier-outage"
ADMISSION_SELECTOR = {"app.kubernetes.io/component": "admission-controller",
                      "app.kubernetes.io/part-of": "kyverno"}
OUTAGE_SETTLE_S = 10


def state_dir(h: Any) -> Path:
    """Fixture files. Kind keeps `.p1/`, each cloud writes `.p1/<target>/` (ADR-0021)."""
    return h.ROOT / ".p1" if h.TARGET == "kind" else h.ROOT / ".p1" / h.TARGET


def outage_policy_manifest() -> dict[str, Any]:
    return {
        "apiVersion": "cilium.io/v2",
        "kind": "CiliumNetworkPolicy",
        "metadata": {"name": OUTAGE_POLICY, "namespace": KYVERNO_NS},
        "spec": {
            "endpointSelector": {"matchLabels": dict(ADMISSION_SELECTOR)},
            "egress": [
                {"toEntities": ["kube-apiserver"]},
                {"toEndpoints": [{"matchLabels": {"k8s:io.kubernetes.pod.namespace": "kube-system",
                                                  "k8s-app": "kube-dns"}}],
                 "toPorts": [{"ports": [{"port": "53", "protocol": "ANY"}]}]},
            ],
        },
    }


def summarize_drop(flow: dict[str, Any]) -> dict[str, Any]:
    l4 = flow.get("l4") or {}
    proto = next(iter(l4), None)
    return {"direction": flow.get("traffic_direction"),
            "destination": (flow.get("IP") or {}).get("destination"),
            "port": (l4.get(proto) or {}).get("destination_port") if proto else None,
            "reason": flow.get("drop_reason_desc")}


def admission_pods(h: Any) -> list[str]:
    selector = ",".join(f"{key}={value}" for key, value in ADMISSION_SELECTOR.items())
    items = h.kj("-n", KYVERNO_NS, "get", "pods", "-l", selector).get("items", [])
    return [item["metadata"]["name"] for item in items
            if not item.get("metadata", {}).get("deletionTimestamp")]


def cloud_outage(h: Any, directory: Path, profile: str, image: str) -> dict[str, Any]:
    """Apply the egress allow-list, request one signed image, always remove the policy.

    Returns the admission record, the Hubble drops that attribute the denial, and the
    restoration time for the recovery clock."""
    pods = admission_pods(h)
    if not pods:
        raise RuntimeError("P1 outage needs the Kyverno admission controller, none matched "
                           f"{ADMISSION_SELECTOR}")
    started = h.now()
    h.k("apply", "-f", "-", input=json.dumps(outage_policy_manifest()), check=True, timeout=30)
    try:
        time.sleep(OUTAGE_SETTLE_S)
        record = admit(h, f"p1-{profile}-recovery", image, False)
        drops: list[dict[str, Any]] = []
        for pod in pods:
            drops.extend(h.hubble_policy_drops(pod, started, KYVERNO_NS))
    finally:
        restored = h.now()
        h.k("-n", KYVERNO_NS, "delete", "ciliumnetworkpolicy", OUTAGE_POLICY,
            "--ignore-not-found", "--wait=true", check=True, timeout=60)
    # Only drops to TCP 443 attribute the outage: that is ECR, ACR and the token exchange. A drop to
    # anything else would not show the registry was cut off (ADR-0021 amendment).
    registry_drops = [f for f in drops if summarize_drop(f)["port"] == 443]
    return {"record": record, "restored": restored,
            "outage": {"mode": "cilium-egress-allow-list", "settle_s": OUTAGE_SETTLE_S,
                       "admission_pods": pods, "policy_drops": len(registry_drops),
                       "all_policy_drops": len(drops),
                       "drop_samples": [summarize_drop(f) for f in drops[:10]]}}


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
    fixtures = state_dir(h)
    image_state_path = fixtures / "images.json"
    signature_state_path = fixtures / "signatures.json"
    if not image_state_path.is_file() or not signature_state_path.is_file():
        raise RuntimeError(f"P1 image or signature preparation evidence is missing in {fixtures}; "
                           "run the P1 image step for this target")
    if not h.kj("-n", KYVERNO_NS, "get", "configmap", TRUST_ROOT).get("metadata"):
        raise RuntimeError(f"P1 trust root ConfigMap {KYVERNO_NS}/{TRUST_ROOT} is missing; "
                           "rerun the target bootstrap after the P1 image step")
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
    outage: dict[str, Any] | None = None
    if profile == "baseline":
        for case in ("trusted", "unsigned", "invalid", "untrusted"):
            observations[case] = admit(h, f"p1-{profile}-{case}", images[case], True)
        observations["tag_only"] = admit(h, f"p1-{profile}-tag", image_state["tag_only"], True)
    else:
        observations["trusted"] = admit(h, "p1-hardened-trusted", images["trusted"], True)
        for case in ("unsigned", "invalid", "untrusted"):
            observations[case] = admit(h, f"p1-hardened-{case}", images[case], False)
        observations["tag_only"] = admit(h, "p1-hardened-tag", image_state["tag_only"], False)

        if h.TARGET == "kind":
            h.sh(["docker", "stop", "selfheal-p1-registry"], timeout=30, check=True)
            try:
                observations["verifier_outage"] = admit(
                    h, "p1-hardened-recovery", images["recovery"], False)
            finally:
                restored = h.now()
                h.sh(["docker", "start", "selfheal-p1-registry"], timeout=30, check=True)
            h.wait_for(registry_ready, 60, step=0.5, what="P1 registry recovery")
        else:
            outage_run = cloud_outage(h, directory, profile, images["recovery"])
            observations["verifier_outage"] = outage_run["record"]
            outage = outage_run["outage"]
            restored = outage_run["restored"]
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
        "outage": outage,
        "window": "admission requests, verifier outage, registry restoration, and runtime digest check",
    }
