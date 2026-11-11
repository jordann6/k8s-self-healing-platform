"""Q3 VPA recommendation experiment and pure resource helpers."""
from __future__ import annotations

import json
import datetime as dt
import math
import time
from pathlib import Path
from typing import Any


ACTIONS = {
    "Q3": "VPA recommender compares measured CPU and memory use with recommendation-only targets while requests and the running pod remain unchanged",
}


def cpu_m(value: str | int | float | None) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    factors = {"n": 1 / 1_000_000, "u": 1 / 1000, "m": 1}
    if text[-1:] in factors:
        return float(text[:-1]) * factors[text[-1]]
    return float(text) * 1000


def memory_mib(value: str | int | float | None) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    binary = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024, "Ti": 1024 * 1024}
    decimal = {"K": 1000 / 1048576, "M": 1_000_000 / 1048576,
               "G": 1_000_000_000 / 1048576}
    for suffix, factor in {**binary, **decimal}.items():
        if text.endswith(suffix):
            return float(text[:-len(suffix)]) * factor
    return float(text) / 1048576


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[math.ceil(fraction * len(ordered)) - 1], 3)


def sample_spacing_s(samples: list[dict]) -> float | None:
    """Median seconds between distinct Metrics API timestamps (ADR-0020 live check L2)."""
    stamps = []
    for sample in samples:
        raw = sample.get("metrics_timestamp")
        if not raw:
            continue
        try:
            stamps.append(dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp())
        except ValueError:
            continue
    gaps = sorted(b - a for a, b in zip(stamps, stamps[1:]) if b > a)
    if not gaps:
        return None
    mid = len(gaps) // 2
    return round(gaps[mid] if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2, 1)


def collection_window(thresholds: dict, profile: str) -> int:
    """How long to collect distinct samples (ADR-0020). Hardened never stops before the recommendation clock."""
    if profile == "hardened":
        return max(thresholds["recommend_s"], thresholds["sample_window_s"])
    return thresholds["baseline_window_s"]


def metrics_server_info(h) -> dict[str, Any]:
    """Replica count and resolution flag of the metrics source, for the evidence (ADR-0020)."""
    deploy = h.kj("-n", "kube-system", "get", "deploy", "metrics-server")
    containers = deploy.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    args = [a for c in containers for a in (c.get("args") or []) if "metric-resolution" in a]
    return {"replicas": deploy.get("spec", {}).get("replicas"), "resolution_args": args}


def append_distinct(samples: list[dict], sample: dict) -> bool:
    """Append only a new Metrics API observation, not a repeated poll result."""
    key = (sample.get("metrics_timestamp"), sample.get("window"),
           sample.get("cpu"), sample.get("memory"))
    if samples:
        previous = samples[-1]
        old_key = (previous.get("metrics_timestamp"), previous.get("window"),
                   previous.get("cpu"), previous.get("memory"))
        if key == old_key:
            return False
    samples.append(sample)
    return True


def container_recommendation(vpa: dict, name: str) -> dict[str, Any] | None:
    items = vpa.get("status", {}).get("recommendation", {}).get("containerRecommendations", [])
    item = next((candidate for candidate in items if candidate.get("containerName") == name), None)
    if not item:
        return None
    result: dict[str, Any] = {"container": name}
    for key in ("lowerBound", "target", "uncappedTarget", "upperBound"):
        values = item.get(key) or {}
        result[key] = {
            "cpu": values.get("cpu"), "memory": values.get("memory"),
            "cpu_m": round(cpu_m(values.get("cpu")), 3),
            "memory_mib": round(memory_mib(values.get("memory")), 3),
        }
    return result


def pod_state(h) -> dict[str, Any]:
    pods = h.kj("-n", h.NS, "get", "pod", "-l", "app=q3-target").get("items", [])
    active = [pod for pod in pods if not pod.get("metadata", {}).get("deletionTimestamp")
              and pod.get("status", {}).get("phase") == "Running"]
    if len(active) != 1:
        return {"pod_count": len(active)}
    pod = active[0]
    container = next((c for c in pod.get("spec", {}).get("containers", [])
                      if c.get("name") == "target"), {})
    status = next((c for c in pod.get("status", {}).get("containerStatuses", [])
                   if c.get("name") == "target"), {})
    requests = container.get("resources", {}).get("requests", {})
    return {
        "pod_count": 1,
        "pod": pod.get("metadata", {}).get("name"),
        "node": pod.get("spec", {}).get("nodeName"),
        "uid": pod.get("metadata", {}).get("uid"),
        "restart_count": status.get("restartCount", 0),
        "ready": status.get("ready") is True,
        "requests": {"cpu": requests.get("cpu"), "memory": requests.get("memory"),
                     "cpu_m": cpu_m(requests.get("cpu")),
                     "memory_mib": memory_mib(requests.get("memory"))},
    }


def resource_sample(h, pod_name: str) -> dict[str, Any] | None:
    response = h.k("get", "--raw=/apis/metrics.k8s.io/v1beta1/namespaces/selfheal/pods")
    if response.returncode != 0:
        return None
    try:
        raw = json.loads(response.stdout)
    except json.JSONDecodeError:
        return None
    pod = next((item for item in raw.get("items", [])
                if item.get("metadata", {}).get("name") == pod_name), None)
    if not pod:
        return None
    container = next((item for item in pod.get("containers", [])
                      if item.get("name") == "target"), None)
    if not container:
        return None
    usage = container.get("usage", {})
    return {
        "at": h.now(), "metrics_timestamp": pod.get("timestamp"), "window": pod.get("window"),
        "cpu": usage.get("cpu"), "memory": usage.get("memory"),
        "cpu_m": round(cpu_m(usage.get("cpu")), 3),
        "memory_mib": round(memory_mib(usage.get("memory")), 3),
    }


def hpa_targets_workload(h) -> bool:
    items = h.kj("-n", h.NS, "get", "hpa").get("items", [])
    return any(item.get("spec", {}).get("scaleTargetRef", {}).get("name") == "q3-target"
               for item in items)


def collect(h, profile: str, timeout: float, minimum: int) -> tuple[list[dict], dict | None, float | None]:
    state = pod_state(h)
    pod = state.get("pod")
    if not pod:
        raise RuntimeError(f"Q3 expected one running pod, found {state}")
    samples: list[dict] = []
    recommendation = None
    recommendation_at = None
    end = h.now() + timeout
    while h.now() < end:
        sample = resource_sample(h, pod)
        if sample:
            append_distinct(samples, sample)
        if profile == "hardened":
            recommendation = container_recommendation(
                h.kj("-n", h.NS, "get", "verticalpodautoscaler", "q3-target"), "target")
            if recommendation and recommendation_at is None:
                recommendation_at = h.now()
        if len(samples) >= minimum and (profile == "baseline" or recommendation):
            break
        time.sleep(10)
    return samples, recommendation, recommendation_at


def run(h, _lab, profile: str, directory: Path) -> dict:
    thresholds = h.THRESHOLDS[h.TARGET]["Q3"]
    initial = pod_state(h)
    if initial.get("pod_count") != 1 or not initial.get("ready"):
        raise RuntimeError(f"Q3 target was not ready: {initial}")
    first_metric = h.wait_for(lambda: resource_sample(h, initial["pod"]), 120, step=5,
                              what="Q3 resource metrics")
    t0 = h.now()
    vpa = h.kj("-n", h.NS, "get", "verticalpodautoscaler", "q3-target")
    vpa_present = bool(vpa.get("metadata"))
    update_mode = vpa.get("spec", {}).get("updatePolicy", {}).get("updateMode")
    # The recommendation clock (recommend_s) is judged on its own. The collection
    # window is derived from the metrics resolution (ADR-0020), so the hardened
    # loop may run past recommend_s to gather samples without excusing a late
    # recommendation.
    window = collection_window(thresholds, profile)
    samples, recommendation, recommendation_at = collect(h, profile, window, thresholds["min_samples"])
    final = pod_state(h)
    cpu_values = [sample["cpu_m"] for sample in samples]
    memory_values = [sample["memory_mib"] for sample in samples]
    summary = {
        "samples": len(samples),
        "cpu_p90_m": percentile(cpu_values, 0.90),
        "cpu_max_m": max(cpu_values, default=None),
        "memory_p90_mib": percentile(memory_values, 0.90),
        "memory_max_mib": max(memory_values, default=None),
        "sample_spacing_s": sample_spacing_s(samples),
    }
    (directory / f"q3-{profile}-timeline.json").write_text(json.dumps(samples, indent=2) + "\n")
    return {
        "t0": t0,
        "metrics_available": first_metric is not None,
        "vpa_present": vpa_present,
        "update_mode": update_mode,
        "hpa_present": hpa_targets_workload(h),
        "initial_state": initial,
        "final_state": final,
        "usage": summary,
        "collection_window_s": window,
        "metrics_server": metrics_server_info(h),
        "recommendation": recommendation,
        "recommendation_s": None if recommendation_at is None else round(recommendation_at - t0, 1),
        "requests_unchanged": initial.get("requests") == final.get("requests"),
        "pod_uid_unchanged": initial.get("uid") == final.get("uid"),
        "restart_delta": final.get("restart_count", 0) - initial.get("restart_count", 0),
        "window": "resource Metrics API samples and VPA status recommendation",
    }
