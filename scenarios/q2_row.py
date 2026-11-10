"""Q2 scaling-failure experiment and pure evidence helpers."""
from __future__ import annotations

import gzip
import json
import re
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
                    "name": "load", "image": h.Q2_IMAGE, "imagePullPolicy": "IfNotPresent",
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


HOLDER_LABEL = "app=q2-capacity-holder"
Q2_NODE_LABEL = "selfheal.lab/q2"
# ADR-0017: fallback on a Value trigger needs KEDA 2.18.0 or later.
MIN_KEDA = (2, 18)


def parse_keda_version(image: str | None) -> tuple[int, int, int] | None:
    """Read X.Y.Z from a KEDA image reference such as ghcr.io/kedacore/keda:2.21.0 or the AKS 2.19.0-6."""
    match = re.search(r":v?(\d+)\.(\d+)\.(\d+)", image or "")
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def keda_version(h) -> tuple[str | None, tuple[int, int, int] | None]:
    namespace = "kube-system" if h.TARGET == "aks" else "keda"
    deploy = h.kj("-n", namespace, "get", "deploy", "keda-operator")
    image = ((deploy.get("spec", {}).get("template", {}).get("spec", {}).get("containers") or [{}])[0]).get("image")
    return image, parse_keda_version(image)


def q2_nodes(nodes: list[dict], target: str) -> list[dict]:
    """The nodes Q2 may use. Kind has one unlabelled node. The clouds use the labelled workload nodes."""
    if target == "kind":
        return nodes
    return [n for n in nodes if (n.get("metadata", {}).get("labels") or {}).get(Q2_NODE_LABEL) == "true"]


def label_workload_nodes(h) -> list[str]:
    """Label every current workload node before the Q2 app syncs (ADR-0017). Idempotent."""
    nodes = h.kj("get", "nodes", "-l", "selfheal.lab/nodes=workload").get("items", [])
    names = [n["metadata"]["name"] for n in nodes]
    if not names:
        raise RuntimeError("no workload nodes found to label for Q2")
    h.k("label", "node", *names, f"{Q2_NODE_LABEL}=true", "--overwrite", check=True)
    return names


def free_cpu_by_node(nodes: list[dict], pods: list[dict]) -> dict[str, dict[str, int]]:
    out = {}
    for node in nodes:
        name = node["metadata"]["name"]
        allocatable = cpu_m(node["status"]["allocatable"]["cpu"])
        requested = scheduled_cpu_m(pods, name)
        out[name] = {"allocatable_m": allocatable, "requested_m": requested, "free_m": allocatable - requested}
    return out


def replicas_fit(free: dict[str, dict[str, int]], replicas: int, request_m: int) -> bool:
    """True when `replicas` pods of request_m can all be placed on the labelled nodes."""
    return sum(max(0, node["free_m"]) // request_m for node in free.values()) >= replicas


def node_inventory(h) -> dict[str, Any]:
    nodes = sorted(n["metadata"]["name"] for n in h.kj("get", "nodes").get("items", []))
    try:
        claims = h.kj("get", "nodeclaims.karpenter.sh").get("items", [])
    except Exception:  # noqa: BLE001
        claims = []
    return {"nodes": nodes, "nodeclaims": sorted(c["metadata"]["name"] for c in claims)}


def nodes_unchanged(*inventories: dict) -> bool:
    return all(i == inventories[0] for i in inventories[1:])


def add_capacity_holders(h) -> dict[str, Any]:
    """One holder per Q2 node leaves under the workload request free on every node."""
    nodes = q2_nodes(h.kj("get", "nodes").get("items", []), h.TARGET)
    if h.TARGET == "kind" and len(nodes) != 1:
        raise RuntimeError(f"Q2 kind proof requires exactly one node, found {len(nodes)}")
    if not nodes:
        raise RuntimeError("no labelled Q2 workload node for the capacity holders")
    pods = h.kj("get", "pods", "-A").get("items", [])
    free = free_cpu_by_node(nodes, pods)
    holders = []
    for index, (node, cpu) in enumerate(sorted(free.items())):
        filler = cpu["allocatable_m"] - cpu["requested_m"] - 100
        if filler < 1:
            holders.append({"node": node, **cpu, "holder": None, "holder_request_cpu_m": 0,
                            "note": "already below 100m free, no holder needed"})
            continue
        if h.TARGET == "kind" and filler < 250:
            raise RuntimeError(f"not enough free requested CPU for a deterministic holder: {filler}m")
        name = f"q2-capacity-holder-{index}"
        spec = {"nodeSelector": {"kubernetes.io/hostname": node},
                "restartPolicy": "Never", "automountServiceAccountToken": False,
                "securityContext": {"runAsNonRoot": True, "runAsUser": 10001,
                                    "seccompProfile": {"type": "RuntimeDefault"}},
                "containers": [{"name": "holder", "image": h.Q2_IMAGE, "args": ["hold"],
                                "imagePullPolicy": "IfNotPresent",
                                "resources": {"requests": {"cpu": f"{filler}m", "memory": "16Mi"},
                                              "limits": {"memory": "32Mi"}},
                                "securityContext": {"allowPrivilegeEscalation": False,
                                                    "readOnlyRootFilesystem": True,
                                                    "capabilities": {"drop": ["ALL"]}}}]}
        if h.TARGET != "kind":
            spec["tolerations"] = [{"key": "selfheal.lab/workload", "operator": "Exists", "effect": "NoSchedule"}]
        h.apply({"apiVersion": "v1", "kind": "Pod",
                 "metadata": {"name": name, "namespace": h.NS,
                              "labels": {"app": "q2-capacity-holder", "selfheal/row": "Q2"}},
                 "spec": spec})
        holders.append({"node": node, **cpu, "holder": name, "holder_request_cpu_m": filler,
                        "free_after_holder_m": 100})
    created = [x["holder"] for x in holders if x["holder"]]
    for name in created:
        h.k("-n", h.NS, "wait", "--for=condition=Ready", f"pod/{name}", "--timeout=120s",
            timeout=130, check=True)
    return {"holders": holders, "workload_request_cpu_m": 250,
            "node": holders[0]["node"], "node_allocatable_cpu_m": holders[0]["allocatable_m"],
            "scheduled_requests_before_holder_m": holders[0]["requested_m"],
            "holder_request_cpu_m": holders[0]["holder_request_cpu_m"], "free_after_holder_m": 100}


def run(h, _lab, profile: str, directory: Path) -> dict:
    thresholds = h.THRESHOLDS[h.TARGET]["Q2"]
    cloud = h.TARGET != "kind"
    # Non-verdict wait bounds. They only stop a stuck run and are never judged.
    ceiling_wait, pending_wait = (120, 90) if cloud else (60, 60)
    outage_load_s, capacity_load_s, capacity_collect_s = (140, 260, 320) if cloud else (80, 100, 160)
    if cloud:
        # Nodes replaced earlier in a mixed run lose the label (ADR-0017). Idempotent.
        label_workload_nodes(h)
    keda_image, keda_ver = keda_version(h)
    if keda_ver is None or keda_ver[:2] < MIN_KEDA:
        raise RuntimeError(f"Q2 needs KEDA {MIN_KEDA[0]}.{MIN_KEDA[1]} or later for fallback on a Value "
                           f"trigger (ADR-0017), found {keda_image}")
    timeline: list[dict] = []
    holder_created = False
    with h.PortForward(h.NS, "svc/q2-signal", 8081) as signal:
        try:
            set_signal(signal.port, demand=0, available=True)
            initial, _ = wait_state(h, stable_one,
                                    thresholds["scale_down_s"], timeline, "Q2 initial replica")
            precheck = metric_available(signal.port)
            q2_node_objs = q2_nodes(h.kj("get", "nodes").get("items", []), h.TARGET)
            free_before = free_cpu_by_node(q2_node_objs, h.kj("get", "pods", "-A").get("items", []))
            if not replicas_fit(free_before, 3, 250):
                raise RuntimeError(f"the four-replica ceiling does not fit on the Q2 nodes: {free_before}")

            set_signal(signal.port, demand=2)
            _, positive_at = wait_state(h, lambda s: s["ready_replicas"] >= 2,
                                        thresholds["fallback_s"], timeline, "Q2 positive scale")
            set_signal(signal.port, demand=0)
            wait_state(h, stable_one,
                       thresholds["scale_down_s"], timeline, "Q2 reset to one")

            load_name, load_started = start_load(h, f"q2-{profile}-outage", 30, outage_load_s)
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
                ceiling_wait, timeline, "Q2 maximum replica ceiling")
            ceiling_name, ceiling_load_start = start_load(h, f"q2-{profile}-ceiling", 50, 30)
            ceiling_samples, ceiling_load_end = collect_load(
                h, ceiling_name, directory / f"q2-{profile}-ceiling-requests.json.gz", timeout=90)
            ceiling_requests = request_stats(ceiling_samples, ceiling_load_start, ceiling_load_end)

            set_signal(signal.port, demand=0)
            wait_state(h, stable_one,
                       thresholds["scale_down_s"], timeline, "Q2 capacity setup at one replica")
            nodes_before = node_inventory(h)
            capacity = add_capacity_holders(h)
            holder_created = True
            capacity_name, capacity_load_start = start_load(h, f"q2-{profile}-capacity", 30, capacity_load_s)
            capacity_t0 = h.now()
            set_signal(signal.port, demand=4)
            pending_state, pending_at = wait_state(
                h, lambda s: s["desired_replicas"] == 4 and any(
                    "Insufficient cpu" in (p.get("message") or "") for p in s["pending"]),
                pending_wait, timeline, "Q2 Pending pods with an Insufficient cpu reason")
            while h.now() < pending_at + 20:
                timeline.append(snapshot(h))
                time.sleep(2)
            nodes_during = node_inventory(h)
            h.k("-n", h.NS, "delete", "pod", "-l", HOLDER_LABEL, "--wait=true", timeout=120, check=True)
            holder_created = False
            capacity_restore_t0 = h.now()
            capacity_ready, capacity_ready_at = wait_state(
                h, lambda s: s["ready_replicas"] == 4 and not s["pending"],
                thresholds["capacity_recover_s"], timeline, "Q2 capacity recovery")
            capacity_samples, capacity_load_end = collect_load(
                h, capacity_name, directory / f"q2-{profile}-capacity-requests.json.gz", timeout=capacity_collect_s)
            constrained_requests = request_stats(capacity_samples, capacity_t0, capacity_restore_t0)
            capacity_recovery_requests = request_stats(capacity_samples, capacity_ready_at, capacity_load_end)

            nodes_after = node_inventory(h)
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
                "keda_image": keda_image,
                "keda_version": ".".join(map(str, keda_ver)),
                "q2_node_cpu_before": free_before,
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
                             "recovery_requests": capacity_recovery_requests,
                             "node_stability": {"before": nodes_before, "during": nodes_during,
                                                "after": nodes_after,
                                                "unchanged": nodes_unchanged(nodes_before, nodes_during, nodes_after)}},
                "scale_down_s": rel(scale_down_at, withdraw_t0),
                "end_state": scale_down_state,
                "conditions_present": bool(initial["scaledobject_conditions"] and initial["hpa_conditions"]),
                "timeline_samples": len(timeline),
                "window": "metric outage, restored signal, configured replica ceiling, CPU scheduling ceiling, restored capacity, demand withdrawn",
            }
        finally:
            if holder_created:
                h.k("-n", h.NS, "delete", "pod", "-l", HOLDER_LABEL, "--ignore-not-found", "--wait=false")
            try:
                set_signal(signal.port, demand=0, available=True)
            except Exception:  # noqa: BLE001
                pass
