"""Q2 scaling-failure experiment and pure evidence helpers."""
from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ACTIONS = {
    "Q2": "KEDA fallback preserves bounded capacity during metric failure; explicit autoscaler and scheduler evidence distinguishes metric, replica, and node ceilings",
}


def cpu_m(value: str | int | float | None) -> int:
    """Parse the CPU quantity forms used by this lab into millicores."""
    if value is None:
        return 0
    text = str(value).strip()
    if text.endswith("m"):
        return int(float(text[:-1]))
    if text.endswith("u"):
        return int(float(text[:-1]) / 1000)
    if text.endswith("n"):
        return int(float(text[:-1]) / 1_000_000)
    return int(float(text) * 1000)


def scheduled_cpu_m(pods: list[dict], node: str) -> int:
    """Sum effective scheduled CPU requests, including init containers."""
    total = 0
    for pod in pods:
        if pod.get("spec", {}).get("nodeName") != node or pod.get("status", {}).get("phase") in {"Succeeded", "Failed"}:
            continue
        spec = pod.get("spec", {})
        app = sum(cpu_m((c.get("resources", {}).get("requests") or {}).get("cpu"))
                  for c in spec.get("containers", []))
        init = max([cpu_m((c.get("resources", {}).get("requests") or {}).get("cpu"))
                    for c in spec.get("initContainers", [])] or [0])
        overhead = cpu_m((spec.get("overhead") or {}).get("cpu"))
        total += max(app, init) + overhead
    return total


def conditions(obj: dict) -> dict[str, dict[str, Any]]:
    """Index status conditions without discarding their reason or message."""
    return {c.get("type", "unknown"): {
        "status": c.get("status"), "reason": c.get("reason"),
        "message": c.get("message"), "lastTransitionTime": c.get("lastTransitionTime"),
    } for c in obj.get("status", {}).get("conditions", [])}


def autoscaler_error(snapshot: dict) -> bool:
    so = snapshot.get("scaledobject_conditions", {})
    hpa = snapshot.get("hpa_conditions", {})
    if so.get("Ready", {}).get("status") == "False":
        return True
    if hpa.get("ScalingActive", {}).get("status") == "False":
        return True
    text = json.dumps({"so": so, "hpa": hpa}).lower()
    return "failedget" in text or "error" in text


def stable_one(snapshot: dict) -> bool:
    """Require scale-down to finish before measuring remaining node capacity."""
    return all(snapshot.get(key) == 1 for key in (
        "desired_replicas", "actual_replicas", "ready_replicas", "available_replicas",
    )) and snapshot.get("target_pod_count") == 1 and snapshot.get("terminating_replicas") == 0 \
        and not snapshot.get("pending")


def request_stats(samples: list[dict], since: float, until: float) -> dict[str, Any]:
    mine = [s for s in samples if since <= float(s.get("started", 0)) < until]
    failures = sum(1 for s in mine if not s.get("ok"))
    durations = sorted(float(s["latency_ms"]) for s in mine if s.get("ok"))
    p95 = None
    if durations:
        index = max(0, min(len(durations) - 1, int((0.95 * len(durations) + 0.999999)) - 1))
        p95 = round(durations[index], 1)
    return {"samples": len(mine), "failures": failures,
            "failure_rate": None if not mine else failures / len(mine), "p95_ms": p95,
            "since": since, "until": until}


def snapshot(h) -> dict:
    deploy = h.kj("-n", h.NS, "get", "deploy", "q2-target")
    so = h.kj("-n", h.NS, "get", "scaledobject", "q2-target")
    hpa = h.kj("-n", h.NS, "get", "hpa", "q2-target")
    items = h.kj("-n", h.NS, "get", "pod", "-l", "app=q2-target").get("items", [])
    endpoints = h.kj("-n", h.NS, "get", "endpoints", "q2-target")
    service_ready_addresses = sum(len(subset.get("addresses", []))
                                  for subset in endpoints.get("subsets", []))
    pending = []
    for pod in items:
        if pod.get("status", {}).get("phase") != "Pending":
            continue
        scheduled = next((c for c in pod.get("status", {}).get("conditions", [])
                          if c.get("type") == "PodScheduled" and c.get("status") == "False"), {})
        pending.append({"pod": pod["metadata"]["name"], "reason": scheduled.get("reason"),
                        "message": scheduled.get("message")})
    return {
        "at": h.now(),
        "desired_replicas": deploy.get("spec", {}).get("replicas"),
        "actual_replicas": deploy.get("status", {}).get("replicas", 0),
        "ready_replicas": deploy.get("status", {}).get("readyReplicas", 0),
        "available_replicas": deploy.get("status", {}).get("availableReplicas", 0),
        "target_pod_count": sum(1 for pod in items
                                if pod.get("status", {}).get("phase") not in {"Succeeded", "Failed"}),
        "terminating_replicas": sum(1 for pod in items if pod.get("metadata", {}).get("deletionTimestamp")),
        "service_ready_addresses": service_ready_addresses,
        "pending": pending,
        "scaledobject_conditions": conditions(so),
        "hpa_conditions": conditions(hpa),
        "hpa_current_replicas": hpa.get("status", {}).get("currentReplicas"),
        "hpa_desired_replicas": hpa.get("status", {}).get("desiredReplicas"),
        "max_replicas": hpa.get("spec", {}).get("maxReplicas"),
        "current_metrics": hpa.get("status", {}).get("currentMetrics", []),
    }


def post_json(port: int, path: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        return json.loads(response.read())


def metric_available(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metric", timeout=5) as response:  # noqa: S310
            return response.status == 200 and "demand" in json.loads(response.read())
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return False


def start_load(h, name: str, rate: int, duration: int) -> tuple[str, float]:
    h.k("-n", h.NS, "delete", "job", name, "--ignore-not-found", "--wait=true", timeout=60)
    manifest = {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": name, "namespace": h.NS, "labels": {"selfheal/row": "Q2"}},
        "spec": {"backoffLimit": 0, "ttlSecondsAfterFinished": 600, "template": {
            "metadata": {"labels": {"app": "q2-load", "selfheal/row": "Q2"}},
            "spec": {
                "restartPolicy": "Never", "automountServiceAccountToken": False,
                "securityContext": {"runAsNonRoot": True, "runAsUser": 10001,
                                    "seccompProfile": {"type": "RuntimeDefault"}},
                "containers": [{
                    "name": "load", "image": "selfheal-q2:v1", "imagePullPolicy": "IfNotPresent",
                    "args": ["load"],
                    "env": [{"name": "RATE", "value": str(rate)},
                            {"name": "DURATION", "value": str(duration)}],
                    "resources": {"requests": {"cpu": "100m", "memory": "32Mi"},
                                  "limits": {"cpu": "1", "memory": "128Mi"}},
                    "securityContext": {"allowPrivilegeEscalation": False,
                                        "readOnlyRootFilesystem": True,
                                        "capabilities": {"drop": ["ALL"]}},
                }],
            },
        }},
    }
    h.apply(manifest)
    h.wait_for(lambda: any(p.get("status", {}).get("phase") == "Running"
                           for p in h.kj("-n", h.NS, "get", "pod", "-l", f"job-name={name}").get("items", [])),
               90, what=f"Q2 load job {name} to start")
    return name, h.now()


def collect_load(h, name: str, path: Path, timeout: int = 180) -> tuple[list[dict], float]:
    h.k("-n", h.NS, "wait", "--for=condition=complete", f"job/{name}", f"--timeout={timeout}s",
        timeout=timeout + 10, check=True)
    logs = h.k("-n", h.NS, "logs", f"job/{name}", timeout=120, check=True).stdout
    samples = [json.loads(line.split(" ", 1)[1]) for line in logs.splitlines() if line.startswith("Q2_SAMPLE ")]
    with gzip.open(path, "wt") as handle:
        for sample in samples:
            handle.write(json.dumps(sample, sort_keys=True) + "\n")
    return samples, max((float(s["ended"]) for s in samples), default=h.now())


def wait_state(h, predicate, timeout: float, timeline: list[dict], what: str) -> tuple[dict, float]:
    end = h.now() + timeout
    while h.now() < end:
        state = snapshot(h)
        timeline.append(state)
        if predicate(state):
            return state, h.now()
        time.sleep(2)
    raise TimeoutError(f"timed out after {timeout:.0f}s waiting for {what}")


def set_signal(port: int, demand: float | None = None, available: bool | None = None) -> dict:
    body: dict[str, Any] = {}
    if demand is not None:
        body["demand"] = demand
    if available is not None:
        body["available"] = available
    return post_json(port, "/admin", body)


def add_capacity_holder(h) -> dict[str, Any]:
    nodes = h.kj("get", "nodes").get("items", [])
    if len(nodes) != 1:
        raise RuntimeError(f"Q2 kind proof requires exactly one node, found {len(nodes)}")
    node = nodes[0]["metadata"]["name"]
    allocatable = cpu_m(nodes[0]["status"]["allocatable"]["cpu"])
    pods = h.kj("get", "pods", "-A").get("items", [])
    requested = scheduled_cpu_m(pods, node)
    filler = allocatable - requested - 100
    if filler < 250:
        raise RuntimeError(f"not enough free requested CPU for a deterministic holder: {filler}m")
    h.apply({"apiVersion": "v1", "kind": "Pod",
             "metadata": {"name": "q2-capacity-holder", "namespace": h.NS,
                          "labels": {"app": "q2-capacity-holder", "selfheal/row": "Q2"}},
             "spec": {"nodeSelector": {"kubernetes.io/hostname": node},
                      "restartPolicy": "Never", "automountServiceAccountToken": False,
                      "securityContext": {"runAsNonRoot": True, "runAsUser": 10001,
                                          "seccompProfile": {"type": "RuntimeDefault"}},
                      "containers": [{"name": "holder", "image": "selfheal-q2:v1", "args": ["hold"],
                                      "resources": {"requests": {"cpu": f"{filler}m", "memory": "16Mi"},
                                                    "limits": {"memory": "32Mi"}},
                                      "securityContext": {"allowPrivilegeEscalation": False,
                                                          "readOnlyRootFilesystem": True,
                                                          "capabilities": {"drop": ["ALL"]}}}]}})
    h.k("-n", h.NS, "wait", "--for=condition=Ready", "pod/q2-capacity-holder", "--timeout=60s",
        timeout=70, check=True)
    return {"node": node, "node_allocatable_cpu_m": allocatable,
            "scheduled_requests_before_holder_m": requested, "holder_request_cpu_m": filler,
            "free_after_holder_m": 100, "workload_request_cpu_m": 250}


def run(h, _lab, profile: str, directory: Path) -> dict:
    thresholds = h.THRESHOLDS["kind"]["Q2"]
    timeline: list[dict] = []
    holder_created = False
    with h.PortForward(h.NS, "svc/q2-signal", 8081) as signal:
        try:
            set_signal(signal.port, demand=0, available=True)
            initial, _ = wait_state(h, stable_one,
                                    thresholds["scale_down_s"], timeline, "Q2 initial replica")
            precheck = metric_available(signal.port)

            set_signal(signal.port, demand=2)
            _, positive_at = wait_state(h, lambda s: s["ready_replicas"] >= 2,
                                        thresholds["fallback_s"], timeline, "Q2 positive scale")
            set_signal(signal.port, demand=0)
            wait_state(h, stable_one,
                       thresholds["scale_down_s"], timeline, "Q2 reset to one")

            load_name, load_started = start_load(h, f"q2-{profile}-outage", 30, 80)
            set_signal(signal.port, demand=1)
            time.sleep(3)
            outage_t0 = h.now()
            set_signal(signal.port, available=False)
            outage_observed = not metric_available(signal.port)
            error_state, detected_at = wait_state(h, autoscaler_error, thresholds["detect_s"], timeline,
                                                  "Q2 autoscaler metric error")
            fallback_at = None
            fallback_state: dict[str, Any] = {}
            if profile == "hardened":
                fallback_state, fallback_at = wait_state(
                    h, lambda s: s["ready_replicas"] >= 3 and s["service_ready_addresses"] >= 3
                    and s["scaledobject_conditions"].get("Fallback", {}).get("status") == "True",
                    thresholds["fallback_s"], timeline, "Q2 fallback replicas")
            restore_t0 = max(outage_t0 + 40,
                             (fallback_at + 20) if fallback_at is not None else h.now())
            while h.now() < restore_t0:
                timeline.append(snapshot(h))
                time.sleep(2)
            set_signal(signal.port, available=True)
            metric_state, metric_ready_at = wait_state(
                h, lambda s: metric_available(signal.port)
                and s["scaledobject_conditions"].get("Ready", {}).get("status") == "True",
                thresholds["metric_recover_s"], timeline, "Q2 metric recovery")
            outage_samples, outage_load_end = collect_load(
                h, load_name, directory / f"q2-{profile}-outage-requests.json.gz")
            judged_start = fallback_at if profile == "hardened" else detected_at
            outage_requests = request_stats(outage_samples, judged_start, restore_t0)
            metric_recovery_requests = request_stats(outage_samples, metric_ready_at, outage_load_end)

            set_signal(signal.port, demand=12)
            ceiling_state, ceiling_at = wait_state(
                h, lambda s: s["ready_replicas"] == 4
                and s["hpa_conditions"].get("ScalingLimited", {}).get("status") == "True",
                60, timeline, "Q2 maximum replica ceiling")
            ceiling_name, ceiling_load_start = start_load(h, f"q2-{profile}-ceiling", 50, 30)
            ceiling_samples, ceiling_load_end = collect_load(
                h, ceiling_name, directory / f"q2-{profile}-ceiling-requests.json.gz", timeout=90)
            ceiling_requests = request_stats(ceiling_samples, ceiling_load_start, ceiling_load_end)

            set_signal(signal.port, demand=0)
            wait_state(h, stable_one,
                       thresholds["scale_down_s"], timeline, "Q2 capacity setup at one replica")
            capacity = add_capacity_holder(h)
            holder_created = True
            capacity_name, capacity_load_start = start_load(h, f"q2-{profile}-capacity", 30, 100)
            capacity_t0 = h.now()
            set_signal(signal.port, demand=4)
            pending_state, pending_at = wait_state(
                h, lambda s: s["desired_replicas"] == 4 and any(
                    "Insufficient cpu" in (p.get("message") or "") for p in s["pending"]),
                60, timeline, "Q2 Pending pods with an Insufficient cpu reason")
            while h.now() < pending_at + 20:
                timeline.append(snapshot(h))
                time.sleep(2)
            h.k("-n", h.NS, "delete", "pod", "q2-capacity-holder", "--wait=true", timeout=60, check=True)
            holder_created = False
            capacity_restore_t0 = h.now()
            capacity_ready, capacity_ready_at = wait_state(
                h, lambda s: s["ready_replicas"] == 4 and not s["pending"],
                thresholds["capacity_recover_s"], timeline, "Q2 capacity recovery")
            capacity_samples, capacity_load_end = collect_load(
                h, capacity_name, directory / f"q2-{profile}-capacity-requests.json.gz", timeout=160)
            constrained_requests = request_stats(capacity_samples, capacity_t0, capacity_restore_t0)
            capacity_recovery_requests = request_stats(capacity_samples, capacity_ready_at, capacity_load_end)

            withdraw_t0 = h.now()
            set_signal(signal.port, demand=0)
            scale_down_state, scale_down_at = wait_state(
                h, stable_one,
                thresholds["scale_down_s"], timeline, "Q2 scale-down after demand withdrawal")

            (directory / f"q2-{profile}-timeline.json").write_text(json.dumps(timeline, indent=2))
            rel = lambda end, start: None if end is None else round(end - start, 1)  # noqa: E731
            return {
                "t0": outage_t0,
                "metric_source_available_before": precheck,
                "metric_outage_observed": outage_observed,
                "metric_failure_detected_s": rel(detected_at, outage_t0),
                "metric_error_state": error_state,
                "fallback_condition_observed": fallback_state.get("scaledobject_conditions", {}).get("Fallback", {}).get("status") == "True",
                "fallback_replicas_s": rel(fallback_at, outage_t0),
                "metric_recovered_s": rel(metric_ready_at, restore_t0),
                "metric_recovery_state": metric_state,
                "positive_scale_s": rel(positive_at, initial["at"]),
                "outage_requests": outage_requests,
                "metric_recovery_requests": metric_recovery_requests,
                "ceiling": {"demand": 12, "observed_s": rel(ceiling_at, restore_t0),
                            "state": ceiling_state, "requests": ceiling_requests},
                "capacity": {**capacity, "demand": 4, "pending_observed_s": rel(pending_at, capacity_t0),
                             "pending_state": pending_state,
                             "recovered_s": rel(capacity_ready_at, capacity_restore_t0),
                             "recovered_state": capacity_ready,
                             "constrained_requests": constrained_requests,
                             "recovery_requests": capacity_recovery_requests},
                "scale_down_s": rel(scale_down_at, withdraw_t0),
                "end_state": scale_down_state,
                "conditions_present": bool(initial["scaledobject_conditions"] and initial["hpa_conditions"]),
                "timeline_samples": len(timeline),
                "window": "metric outage, restored signal, configured replica ceiling, CPU scheduling ceiling, restored capacity, demand withdrawn",
            }
        finally:
            if holder_created:
                h.k("-n", h.NS, "delete", "pod", "q2-capacity-holder", "--ignore-not-found", "--wait=false")
            try:
                set_signal(signal.port, demand=0, available=True)
            except Exception:  # noqa: BLE001
                pass
