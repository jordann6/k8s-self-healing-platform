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


def proxy_service(h: Any) -> dict[str, Any]:
    services = h.kj("-n", PROXY_NS, "get", "svc", "-l", PROXY_SELECTOR).get("items", [])
    if len(services) != 1:
        raise RuntimeError(f"expected one Envoy proxy Service for {PLATFORM_NS}/{GATEWAY}, found {len(services)}")
    return services[0]


def load_balancer_address(service: dict[str, Any]) -> str | None:
    """The internal load balancer address once the cloud has provisioned it (hostname on AWS, IP on Azure)."""
    for ingress in ((service.get("status") or {}).get("loadBalancer") or {}).get("ingress") or []:
        if ingress.get("hostname") or ingress.get("ip"):
            return ingress.get("hostname") or ingress.get("ip")
    return None


def check_internal(service: dict[str, Any]) -> None:
    """ADR-0025 cloud addendum (a), no public address. A public AWS scheme or a missing internal annotation
    is an error before any request is sent."""
    annotations = (service.get("metadata") or {}).get("annotations") or {}
    aws = annotations.get("service.beta.kubernetes.io/aws-load-balancer-internal") == "true"
    azure = annotations.get("service.beta.kubernetes.io/azure-load-balancer-internal") == "true"
    if not (aws or azure):
        raise RuntimeError("the proxy Service is a LoadBalancer without an internal annotation, refusing to "
                           "send traffic through a possibly public address")


def proxy_address(h: Any) -> str:
    """Kind calls the ClusterIP. The clouds call the load balancer (a Service of any other type is an error)."""
    spec = proxy_service(h)["spec"]
    want = "ClusterIP" if h.TARGET == "kind" else "LoadBalancer"
    if spec.get("type") != want:
        raise RuntimeError(f"proxy Service is {spec.get('type')}, ADR-0025 requires {want} on {h.TARGET}")
    return spec["clusterIP"] if want == "ClusterIP" else wait_load_balancer(h)["address"]


def wait_load_balancer(h: Any, timeout_s: int = 300) -> dict[str, Any]:
    service = proxy_service(h)
    check_internal(service)
    started = h.now()
    service = h.wait_for(lambda: (lambda s: s if load_balancer_address(s) else None)(proxy_service(h)),
                         timeout_s, step=5, what="G1 load balancer address")
    return {"address": load_balancer_address(service), "address_s": round(h.now() - started, 3),
            "cluster_ip": service["spec"]["clusterIP"]}


TLS_PROBE = """
import json, socket, ssl, sys
addr, host, ca = sys.argv[1:4]
try:
    ctx = ssl.create_default_context(cadata=ca) if ca else ssl.create_default_context()
    with socket.create_connection((addr, 443), timeout=5) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
            tls.sendall(f"GET /shop HTTP/1.1\\r\\nHost: {host}\\r\\nConnection: close\\r\\n\\r\\n".encode())
            line = tls.recv(64).split(b"\\r\\n")[0].decode()
    print(json.dumps({"status_line": line, "error": None}))
except ssl.SSLCertVerificationError as e:
    print(json.dumps({"status_line": None, "error": "verify"}))
except OSError as e:
    print(json.dumps({"status_line": None, "error": type(e).__name__}))
"""


def tls_probe(h: Any, address: str, ca: str) -> dict[str, Any]:
    result = h.k("-n", PLATFORM_NS, "exec", CLIENT, "--", "python3", "-c", TLS_PROBE, address, HOST, ca, timeout=20)
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"status_line": None, "error": "unparseable"}


def tls_check(h: Any, address: str) -> dict[str, Any]:
    """G1f. The certificate from the lab CA is Ready within the bound, the Gateway serves HTTPS that
    verifies against that CA, and the same request without the lab CA fails verification."""
    started = h.now()
    try:
        h.wait_for(lambda: h.k("-n", PLATFORM_NS, "wait", "--for=condition=Ready", "certificate/g1-tls",
                               "--timeout=5s", timeout=15).returncode == 0, 150, step=3, what="G1 certificate")
    except TimeoutError:
        return {"error": "certificate g1-tls never became Ready"}
    cert_ready_s = round(h.now() - started, 3)
    ca = h.k("-n", "cert-manager", "get", "secret", "lab-ca", "-o", r"jsonpath={.data.ca\.crt}").stdout.strip()
    if not ca:
        return {"error": "lab CA certificate not found in cert-manager/lab-ca"}
    import base64
    pem = base64.b64decode(ca).decode()
    deadline = h.now() + 60
    good = tls_probe(h, address, pem)
    while "200" not in (good.get("status_line") or "") and h.now() < deadline:
        time.sleep(3)
        good = tls_probe(h, address, pem)
    bad = tls_probe(h, address, "")
    return {"cert_ready_s": cert_ready_s, "status_line": good.get("status_line"),
            "served_ok": "200" in (good.get("status_line") or ""),
            "without_lab_ca": bad.get("error"), "rejected_without_lab_ca": bad.get("error") == "verify"}


def hubble_drop_check(h: Any) -> dict[str, Any]:
    """G1g, informational. A stranger (the client pod, not an Envoy proxy pod) calls a backend pod directly.
    The backend's ingress policy should drop it, and Hubble attributes the drop to the destination pod."""
    pods = h.kj("-n", APP_NS, "get", "pods", "-l", "app=web").get("items", [])
    if len(pods) != 1:
        return {"available": False, "error": f"expected one web pod, found {len(pods)}"}
    pod, ip = pods[0]["metadata"]["name"], pods[0]["status"]["podIP"]
    since = h.now()
    direct = probe(h, f"{ip}:8080", HOST, "/healthz")
    time.sleep(3)
    try:
        flows = h.hubble_policy_drops(pod, since, APP_NS)
    except Exception as exc:  # noqa: BLE001 - recorded, informational
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"[:200], "direct_call": direct}
    seen = [f for f in flows if f.get("source", {}).get("pod_name") == CLIENT
            and f.get("destination", {}).get("pod_name") == pod]
    return {"available": True, "direct_call": direct, "blocked": direct.get("status") is None,
            "seen": bool(seen), "drops": len(seen),
            "verdict_side": "destination ingress policy" if seen else None}


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


def parse_ts(value: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def lb_provisioning_s(h: Any) -> float | None:
    """Seconds from the proxy Service being created to the Gateway reporting Programmed, which needs the
    load balancer address. None when either timestamp is missing, and the judge then errors."""
    created = proxy_service(h).get("metadata", {}).get("creationTimestamp")
    gateway = h.kj("-n", PLATFORM_NS, "get", "gateway", GATEWAY)
    programmed = [c.get("lastTransitionTime") for c in (gateway.get("status") or {}).get("conditions") or []
                  if c.get("type") == "Programmed" and c.get("status") == "True"]
    if not created or not programmed or not programmed[0]:
        return None
    return round(parse_ts(programmed[0]) - parse_ts(created), 3)


def listener_has_https(h: Any) -> bool:
    gateway = h.kj("-n", PLATFORM_NS, "get", "gateway", GATEWAY)
    return any(item.get("protocol") == "HTTPS" for item in (gateway.get("spec") or {}).get("listeners") or [])


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


def kill_proxy(h: Any, ip: str, cluster_ip: str | None = None) -> dict[str, Any]:
    pods = [p["metadata"]["name"] for p in h.kj("-n", PROXY_NS, "get", "pods", "-l", PROXY_SELECTOR).get("items", [])
            if not p["metadata"].get("deletionTimestamp")]
    if len(pods) < 2:
        return {"error": f"need two proxy pods, found {len(pods)}"}
    started = h.now()
    h.k("-n", PROXY_NS, "delete", "pod", pods[0], "--wait=false", timeout=30)
    results, direct = [], []
    while h.now() - started < KILL_WINDOW_S:
        results.append(probe(h, ip, HOST, "/shop")["status"])
        if cluster_ip:
            # The cloud also records the ClusterIP path, so load balancer deregistration time is a
            # separate number from the proxy replacement time (ADR-0025 cloud addendum, f).
            direct.append(probe(h, cluster_ip, HOST, "/shop")["status"])
        time.sleep(1)
    failures = sum(1 for s in results if s != 200)
    out = {"killed": pods[0], "requests": len(results), "failures": failures}
    if cluster_ip:
        out["cluster_ip_path"] = {"requests": len(direct), "failures": sum(1 for s in direct if s != 200)}
    return out


def run(h: Any, _lab: Any, profile: str, directory: Path) -> dict[str, Any]:
    t0 = h.now()
    h.wait_for(lambda: gateway_ready(h), 120, step=2, what="G1 Gateway Programmed")
    ensure_client(h)
    cloud = h.TARGET != "kind"
    ip = proxy_address(h)
    cluster_ip = proxy_service(h)["spec"]["clusterIP"] if cloud else None
    shape = listener_shape(h)
    first_ok_wait = h.THRESHOLDS[h.TARGET]["G1"].get("first_ok_s", 120)
    ok_started = h.now()
    h.wait_for(lambda: probe(h, ip, HOST, "/shop")["status"] == 200, first_ok_wait, step=3, what="G1 legitimate route")
    obs: dict[str, Any] = {"t0": t0, "listener": shape, "proxy_service": "LoadBalancer" if cloud else "ClusterIP",
                           "legit_before": probe(h, ip, HOST, "/shop")}
    if cloud:
        # The address clock comes from Kubernetes timestamps (proxy Service created to Gateway Programmed), so
        # it holds even when the Gateway was provisioned before this row started. The first-200 clock is the
        # wait from the start of this row, so it is conservative (never shorter than the wait after the address).
        obs["lb_address_s"] = lb_provisioning_s(h)
        obs["first_ok_s"] = round(h.now() - ok_started, 3)
        obs["lb_address"] = ip
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
        if cloud:
            obs["hubble_drop"] = hubble_drop_check(h)
            if listener_has_https(h):
                obs["tls"] = tls_check(h, ip)
        obs["proxy_kill"] = kill_proxy(h, ip, cluster_ip)
        h.wait_for(lambda: probe(h, ip, HOST, "/shop")["status"] == 200, 60, step=2, what="G1 recovery")
        obs["proxy_recovery_s"] = round(h.now() - killed, 3)
        obs["route_status_s"] = max(obs[name]["status_s"] for name in FAULTS)
        obs["legit_after"] = probe(h, ip, HOST, "/shop")
    finally:
        remove_faults(h)
    (directory / f"g1-{profile}.json").write_text(json.dumps(obs, indent=2))
    obs["window"] = "faulted routes, unrouted requests, and one proxy pod kill (informational)"
    return obs
