"""G1 Gateway API front door experiment and pure helpers (ADR-0025)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

ACTIONS = {
    "G1": "Envoy Gateway splits the entrance (platform owns the Gateway, the app team owns its route), so a "
          "stray hostname, a route from another namespace, or a route to a deleted backend never reaches "
          "traffic and never takes the legitimate route down",
}

PLATFORM_NS = "selfheal-g1-platform"
APP_NS = "selfheal-g1"
OTHER_NS = "selfheal-g1-other"
GATEWAY = "g1"
HOST = "app.g1.lab"
EVIL_HOST = "evil.g1.lab"
CLIENT = "g1-client"
CLIENT_IMAGE = "selfheal-g1:v1"
PROXY_NS = "envoy-gateway-system"
PROXY_SELECTOR = (f"gateway.envoyproxy.io/owning-gateway-name={GATEWAY},"
                  f"gateway.envoyproxy.io/owning-gateway-namespace={PLATFORM_NS}")
SETTLE_S = 5
KILL_WINDOW_S = 30

PROBE = """
import json, sys, urllib.request, urllib.error
host, path, ip = sys.argv[1:4]
req = urllib.request.Request(f"http://{ip}{path}", headers={"Host": host})
try:
    with urllib.request.urlopen(req, timeout=5) as r:
        status, raw = r.status, r.read()
except urllib.error.HTTPError as e:
    status, raw = e.code, e.read()
except OSError as e:
    print(json.dumps({"status": None, "backend": None, "error": type(e).__name__})); raise SystemExit(0)
try:
    backend = json.loads(raw).get("backend")
except ValueError:
    backend = None
print(json.dumps({"status": status, "backend": backend}))
"""


def client_manifest() -> dict[str, Any]:
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": CLIENT, "namespace": PLATFORM_NS, "labels": {"app": CLIENT}},
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


def route_manifest(name: str, namespace: str, hostname: str, path: str, backend: str) -> dict[str, Any]:
    return {
        "apiVersion": "gateway.networking.k8s.io/v1", "kind": "HTTPRoute",
        "metadata": {"name": name, "namespace": namespace, "labels": {"selfheal.lab/g1-fault": "true"}},
        "spec": {
            "parentRefs": [{"name": GATEWAY, "namespace": PLATFORM_NS}],
            "hostnames": [hostname],
            "rules": [{"matches": [{"path": {"type": "PathPrefix", "value": path}}],
                       "backendRefs": [{"name": backend, "port": 80}]}],
        },
    }


FAULTS = {
    # name: (namespace, hostname, path, backend)
    "stray_host": (APP_NS, EVIL_HOST, "/", "web"),
    "cross_namespace": (OTHER_NS, HOST, "/other", "other"),
    "deleted_backend": (APP_NS, HOST, "/ghost", "deleted-backend"),
}


def parse_probe(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"status": None, "backend": None, "error": "unparseable"}
    return {"status": data.get("status"), "backend": data.get("backend"),
            **({"error": data["error"]} if data.get("error") else {})}


def route_condition(route: dict[str, Any], kind: str) -> dict[str, Any] | None:
    """The condition of `kind` reported by the Gateway for this route, or None before it reports."""
    for parent in (route.get("status") or {}).get("parents") or []:
        if (parent.get("parentRef") or {}).get("name") != GATEWAY:
            continue
        for cond in parent.get("conditions") or []:
            if cond.get("type") == kind:
                return {"status": cond.get("status"), "reason": cond.get("reason")}
    return None


def proxy_address(h: Any) -> str:
    services = h.kj("-n", PROXY_NS, "get", "svc", "-l", PROXY_SELECTOR).get("items", [])
    if len(services) != 1:
        raise RuntimeError(f"expected one Envoy proxy Service for {PLATFORM_NS}/{GATEWAY}, found {len(services)}")
    spec = services[0]["spec"]
    if spec.get("type") != "ClusterIP":
        raise RuntimeError(f"proxy Service is {spec.get('type')}, ADR-0025 requires ClusterIP")
    return spec["clusterIP"]


def probe(h: Any, ip: str, host: str, path: str) -> dict[str, Any]:
    result = h.k("-n", PLATFORM_NS, "exec", CLIENT, "--", "python3", "-c", PROBE, host, path, ip, timeout=20)
    return parse_probe(result.stdout)


def ensure_client(h: Any) -> None:
    if not h.kj("-n", PLATFORM_NS, "get", "pod", CLIENT).get("metadata"):
        h.k("create", "-f", "-", input=json.dumps(client_manifest()), check=True, timeout=30)
    h.wait_for(lambda: h.k("-n", PLATFORM_NS, "wait", "--for=condition=Ready", f"pod/{CLIENT}",
                           "--timeout=5s", timeout=15).returncode == 0, 90, step=2, what="G1 client pod")


def gateway_ready(h: Any) -> bool:
    gateway = h.kj("-n", PLATFORM_NS, "get", "gateway", GATEWAY)
    conds = {c["type"]: c["status"] for c in (gateway.get("status") or {}).get("conditions") or []}
    return conds.get("Programmed") == "True"


def listener_shape(h: Any) -> dict[str, Any]:
    gateway = h.kj("-n", PLATFORM_NS, "get", "gateway", GATEWAY)
    listener = ((gateway.get("spec") or {}).get("listeners") or [{}])[0]
    return {"hostname": listener.get("hostname"),
            "from": ((listener.get("allowedRoutes") or {}).get("namespaces") or {}).get("from")}


def apply_fault(h: Any, name: str, timeout_s: int) -> dict[str, Any]:
    namespace, hostname, path, backend = FAULTS[name]
    manifest = route_manifest(f"g1-{name.replace('_', '-')}", namespace, hostname, path, backend)
    h.k("apply", "-f", "-", input=json.dumps(manifest), check=True, timeout=30)
    route = manifest["metadata"]["name"]

    def reported() -> dict[str, Any] | None:
        item = h.kj("-n", namespace, "get", "httproute", route)
        if route_condition(item, "Accepted") and route_condition(item, "ResolvedRefs"):
            return item
        return None

    started = h.now()
    try:
        item = h.wait_for(reported, timeout_s, step=2, what=f"G1 {name} route status")
    except TimeoutError:
        item = h.kj("-n", namespace, "get", "httproute", route)
    time.sleep(SETTLE_S)
    return {"namespace": namespace, "route": route, "hostname": hostname, "path": path,
            "accepted": route_condition(item, "Accepted"), "resolved_refs": route_condition(item, "ResolvedRefs"),
            "status_s": round(h.now() - started - SETTLE_S, 3)}


def remove_faults(h: Any) -> None:
    for namespace in (APP_NS, OTHER_NS):
        h.k("-n", namespace, "delete", "httproute", "-l", "selfheal.lab/g1-fault=true",
            "--ignore-not-found", "--wait=true", timeout=60)


def kill_proxy(h: Any, ip: str) -> dict[str, Any]:
    pods = [p["metadata"]["name"] for p in h.kj("-n", PROXY_NS, "get", "pods", "-l", PROXY_SELECTOR).get("items", [])
            if not p["metadata"].get("deletionTimestamp")]
    if len(pods) < 2:
        return {"error": f"need two proxy pods, found {len(pods)}"}
    started = h.now()
    h.k("-n", PROXY_NS, "delete", "pod", pods[0], "--wait=false", timeout=30)
    results = []
    while h.now() - started < KILL_WINDOW_S:
        results.append(probe(h, ip, HOST, "/shop")["status"])
        time.sleep(1)
    failures = sum(1 for s in results if s != 200)
    return {"killed": pods[0], "requests": len(results), "failures": failures}


def run(h: Any, _lab: Any, profile: str, directory: Path) -> dict[str, Any]:
    t0 = h.now()
    h.wait_for(lambda: gateway_ready(h), 120, step=2, what="G1 Gateway Programmed")
    ensure_client(h)
    ip = proxy_address(h)
    shape = listener_shape(h)
    h.wait_for(lambda: probe(h, ip, HOST, "/shop")["status"] == 200, 120, step=3, what="G1 legitimate route")
    obs: dict[str, Any] = {"t0": t0, "listener": shape, "proxy_service": "ClusterIP",
                           "legit_before": probe(h, ip, HOST, "/shop")}
    try:
        for name in FAULTS:
            fault = apply_fault(h, name, 30)
            host, path = fault["hostname"], fault["path"]
            fault["request"] = probe(h, ip, host, path)
            fault["legit_during"] = probe(h, ip, HOST, "/shop")
            obs[name] = fault
            remove_faults(h)
        obs["unrouted_path"] = probe(h, ip, HOST, "/nothing")
        obs["unknown_host"] = probe(h, ip, "nobody.g1.lab", "/shop")
        killed = h.now()
        obs["proxy_kill"] = kill_proxy(h, ip)
        h.wait_for(lambda: probe(h, ip, HOST, "/shop")["status"] == 200, 60, step=2, what="G1 recovery")
        obs["proxy_recovery_s"] = round(h.now() - killed, 3)
        obs["route_status_s"] = max(obs[name]["status_s"] for name in FAULTS)
        obs["legit_after"] = probe(h, ip, HOST, "/shop")
    finally:
        remove_faults(h)
    (directory / f"g1-{profile}.json").write_text(json.dumps(obs, indent=2))
    obs["window"] = "faulted routes, unrouted requests, and one proxy pod kill (informational)"
    return obs
