"""N1 cross-node WireGuard experiment and pure evidence helpers."""
from __future__ import annotations

import ipaddress
import json
import math
import re
import secrets
import subprocess
import time
from pathlib import Path
from typing import Any, Callable


ACTIONS = {
    "N1": "Cilium WireGuard replaces visible cross-node VXLAN traffic while legitimate requests and policy denial remain observable",
}


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[math.ceil(len(ordered) * fraction) - 1], 3)


def request_summary(result: dict) -> dict[str, Any]:
    samples = result.get("samples") or []
    successes = [sample for sample in samples if sample.get("ok") is True]
    latencies = [float(sample["latency_ms"]) for sample in successes]
    return {
        "samples": len(samples),
        "successes": len(successes),
        "failures": len(samples) - len(successes),
        "p95_ms": percentile(latencies, 0.95),
    }


def capture_facts(output: str, marker: str, client_ip: str | None = None,
                  server_ip: str | None = None) -> dict[str, Any]:
    lines = output.splitlines()
    total_vxlan = len(re.findall(r"\.8472(?::| >)", output))
    if client_ip and server_ip:
        vxlan = sum(
            1 for index, line in enumerate(lines[:-1])
            if re.search(r"\.8472(?::| >)", line)
            and client_ip in lines[index + 1]
            and server_ip in lines[index + 1]
        )
    else:
        vxlan = total_vxlan
    return {
        "vxlan_packets": vxlan,
        "vxlan_packets_total": total_vxlan,
        "wireguard_packets": len(re.findall(r"\.51871(?::| >)", output)),
        "marker_visible": marker in output,
        "bytes": len(output.encode()),
    }


def wireguard_facts(status_by_node: dict[str, dict], client_node: str, server_node: str,
                    client_underlay_ip: str, server_underlay_ip: str) -> dict[str, Any]:
    def interface(node: str) -> dict:
        interfaces = (status_by_node.get(node, {}).get("interfaces") or [])
        return interfaces[0] if len(interfaces) == 1 else {}

    client = interface(client_node)
    server = interface(server_node)
    client_peers = client.get("peers") or []
    server_peers = server.get("peers") or []
    client_peer = next((peer for peer in client_peers
                        if peer.get("public-key") == server.get("public-key")), None)
    server_peer = next((peer for peer in server_peers
                        if peer.get("public-key") == client.get("public-key")), None)

    def routes(peer: dict | None, ip: str) -> bool:
        try:
            address = ipaddress.ip_address(ip)
            return any(address in ipaddress.ip_network(value, strict=False)
                       for value in (peer or {}).get("allowed-ips", []))
        except ValueError:
            return False

    def handshake(peer: dict | None) -> bool:
        value = str((peer or {}).get("last-handshake-time") or "")
        return bool(value and not value.startswith("0001-"))

    return {
        "client_interfaces": len(status_by_node.get(client_node, {}).get("interfaces") or []),
        "server_interfaces": len(status_by_node.get(server_node, {}).get("interfaces") or []),
        "client_peer_count": len(client_peers),
        "server_peer_count": len(server_peers),
        "peer_keys_match": client_peer is not None and server_peer is not None,
        "client_routes_server": routes(client_peer, server_underlay_ip),
        "server_routes_client": routes(server_peer, client_underlay_ip),
        "client_handshake": handshake(client_peer),
        "server_handshake": handshake(server_peer),
    }


def pod(h, app: str, namespace: str | None = None) -> dict[str, Any]:
    namespace = namespace or h.NS
    items = h.kj("-n", namespace, "get", "pod", "-l", f"app={app}").get("items", [])
    active = [item for item in items if not item.get("metadata", {}).get("deletionTimestamp")
              and item.get("status", {}).get("phase") == "Running"]
    if len(active) != 1:
        return {"count": len(active)}
    item = active[0]
    return {"count": 1, "name": item["metadata"]["name"],
            "uid": item["metadata"].get("uid"), "node": item["spec"].get("nodeName"),
            "ip": item["status"].get("podIP")}


def node_internal_ip(h, name: str) -> str | None:
    addresses = h.kj("get", "node", name).get("status", {}).get("addresses", [])
    return next((item.get("address") for item in addresses
                 if item.get("type") == "InternalIP"), None)


def probe(h, pod_name: str, url: str, count: int, timeout: float) -> dict:
    result = h.k("-n", h.NS, "exec", pod_name, "--", "python3", "/app/app.py",
                 "probe", url, str(count), str(timeout), timeout=max(30, int(count * timeout + 20)))
    if result.returncode != 0:
        raise RuntimeError(f"N1 probe failed: {(result.stderr or result.stdout).strip()[:300]}")
    try:
        return json.loads(result.stdout.splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise RuntimeError(f"N1 probe returned invalid evidence: {result.stdout[-300:]}") from exc


def capture(h, capture_namespace: str, capture_pod: str, marker: str,
            client_pod_ip: str, server_pod_ip: str,
            client_node_ip: str, server_node_ip: str,
            action: Callable[[], dict]) -> tuple[dict, str]:
    command = ["kubectl", "-n", capture_namespace, "exec", capture_pod, "--",
               "timeout", "8", "tcpdump", "-l", "-nn", "-s", "0",
               "-A", "-i", "eth0", "(", "udp", "port", "8472", "or", "udp", "port",
               "51871", ")", "and", "host", client_node_ip, "and", "host", server_node_ip]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1)
    result = action()
    stdout, stderr = process.communicate(timeout=15)
    if process.returncode not in (0, 124):
        raise RuntimeError(f"N1 tcpdump failed in {capture_pod}: {(stderr or stdout).strip()[:300]}")
    output = stdout + "\n" + stderr
    facts = capture_facts(output, marker, client_pod_ip, server_pod_ip)
    facts["probe"] = request_summary(result)
    return facts, output


def encryption_status(h, node: str) -> dict:
    pods = h.kj("-n", "kube-system", "get", "pod", "-l", "k8s-app=cilium").get("items", [])
    item = next((candidate for candidate in pods if candidate.get("spec", {}).get("nodeName") == node), None)
    if not item:
        return {}
    result = h.k("-n", "kube-system", "exec", item["metadata"]["name"], "-c", "cilium-agent",
                 "--", "cilium-dbg", "debuginfo", "--output", "json", timeout=60)
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout).get("encryption", {}).get("wireguard", {})
    except json.JSONDecodeError:
        return {}


def exact_policy_drops(flows: list[dict], denied: str, server: str) -> list[dict]:
    return [flow for flow in flows
            if flow.get("source", {}).get("pod_name") == denied
            and flow.get("destination", {}).get("pod_name") == server
            and "POLICY" in str(flow.get("drop_reason_desc") or "")]


def run(h, _lab, profile: str, directory: Path) -> dict:
    thresholds = h.THRESHOLDS["kind"]["N1"]
    client, denied, server = pod(h, "n1-client"), pod(h, "n1-denied"), pod(h, "n1-server")
    capture_namespace = "selfheal-n1-capture"
    capture_pod = pod(h, "n1-capture", capture_namespace)
    if any(item.get("count") != 1 or not item.get("ip")
           for item in (client, denied, server, capture_pod)):
        raise RuntimeError(
            f"N1 workload placement was incomplete: {client}, {denied}, {server}, {capture_pod}")
    if capture_pod["node"] != server["node"]:
        raise RuntimeError(f"N1 capture pod is not on the server node: {capture_pod}, {server}")
    node_ips = {node: node_internal_ip(h, node) for node in (client["node"], server["node"])}
    if not all(node_ips.values()):
        raise RuntimeError(f"N1 worker InternalIP evidence was incomplete: {node_ips}")
    marker = f"n1-{profile}-{secrets.token_hex(8)}"
    url = f"http://{server['ip']}:8084/{marker}"
    t0 = h.now()
    capture_result, capture_text = capture(
        h, capture_namespace, capture_pod["name"], marker, client["ip"], server["ip"],
        node_ips[client["node"]], node_ips[server["node"]],
        lambda: probe(h, client["name"], url, thresholds["min_requests"], 2))
    denied_result = probe(h, denied["name"], url, thresholds["min_denied"], 1)
    drops = exact_policy_drops(h.hubble_policy_drops(server["name"], t0), denied["name"], server["name"])
    status = {node: encryption_status(h, node) for node in (client["node"], server["node"])}
    config = h.kj("-n", "kube-system", "get", "configmap", "cilium-config").get("data", {})
    wg = wireguard_facts(status, client["node"], server["node"],
                         node_ips[client["node"]], node_ips[server["node"]])
    (directory / f"n1-{profile}-capture.txt").write_text(capture_text)
    (directory / f"n1-{profile}-encryption.json").write_text(json.dumps(status, indent=2) + "\n")
    (directory / f"n1-{profile}-policy-drops.json").write_text(json.dumps(drops, indent=2) + "\n")
    return {
        "t0": t0,
        "placement": {"client": client, "denied": denied, "server": server,
                      "capture": capture_pod,
                      "cross_node": client["node"] != server["node"]},
        "marker": marker,
        "capture": capture_result,
        "denied": request_summary(denied_result),
        "hubble_available": True,
        "policy_drops": len(drops),
        # The chart omits the key at its documented false default.
        "wireguard_config": config.get("enable-wireguard", "false"),
        "wireguard": wg,
        "node_internal_ips": node_ips,
        "window": "cross-node requests under worker eth0 capture, followed by policy-denied attempts",
    }
