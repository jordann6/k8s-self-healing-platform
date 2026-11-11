#!/usr/bin/env python3
"""Small O1 target, alert receiver, and evidence-gated release controller."""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


NS = os.environ.get("O1_NAMESPACE", "selfheal-o1")
API = "https://kubernetes.default.svc"
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        self._reply(200, {"ok": True, "mode": os.environ.get("O1_MODE")})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path != "/alerts" or payload.get("kind") != "quarantine":
            self._reply(400, {"accepted": False})
            return
        print("O1_ALERT_DELIVERED " + json.dumps(payload, sort_keys=True), flush=True)
        self._reply(202, {"accepted": True})

    def log_message(self, _format: str, *_args: object) -> None:
        return


def api(method: str, path: str, body: dict | None = None) -> dict:
    token = open(TOKEN_PATH, encoding="utf-8").read().strip()
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(API + path, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        request.add_header("Content-Type", "application/merge-patch+json")
    context = ssl.create_default_context(cafile=CA_PATH)
    with urllib.request.urlopen(request, context=context, timeout=5) as response:
        return json.loads(response.read() or b"{}")


def controller() -> None:
    evidence_sent = False
    while True:
        try:
            pod = api("GET", f"/api/v1/namespaces/{NS}/pods/o1-target")
            evidence = api("GET", f"/api/v1/namespaces/{NS}/configmaps/o1-evidence")
            lease = api("GET", f"/apis/coordination.k8s.io/v1/namespaces/{NS}/leases/o1-release")
            annotations = pod.get("metadata", {}).get("annotations", {})
            data = evidence.get("data", {})
            if annotations.get("o1.security/incident") == "true" and not data.get("target_uid"):
                captured = {
                    "target_uid": pod["metadata"]["uid"],
                    "target_resource_version": pod["metadata"]["resourceVersion"],
                    "target_image": pod["spec"]["containers"][0]["image"],
                    "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                evidence = api(
                    "PATCH", f"/api/v1/namespaces/{NS}/configmaps/o1-evidence",
                    {"data": captured},
                )
                data = evidence.get("data", captured)
                print("O1_EVIDENCE_CAPTURED " + json.dumps(data, sort_keys=True), flush=True)
            if data.get("target_uid") and not evidence_sent:
                alert = {
                    "kind": "quarantine",
                    "target_uid": data["target_uid"],
                    "evidence_resource_version": evidence["metadata"]["resourceVersion"],
                    "captured_at": data["captured_at"],
                }
                request = urllib.request.Request(
                    "http://o1-alert-receiver:8080/alerts",
                    data=json.dumps(alert).encode(), method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    if response.status != 202:
                        raise RuntimeError(f"alert receiver returned {response.status}")
                print("O1_ALERT_SENT " + json.dumps(alert, sort_keys=True), flush=True)
                evidence_sent = True
            requested = lease.get("metadata", {}).get("annotations", {}).get("o1.security/release") == "true"
            if requested and data.get("target_uid") == pod.get("metadata", {}).get("uid") \
                    and annotations.get("o1.security/incident") == "true":
                released = api(
                    "PATCH", f"/apis/networking.k8s.io/v1/namespaces/{NS}/networkpolicies/quarantine-ingress",
                    {"spec": {"ingress": [{}]}},
                )
                print("O1_RELEASED " + json.dumps({
                    "target_uid": pod["metadata"]["uid"],
                    "policy_resource_version": released["metadata"]["resourceVersion"],
                    "released_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }, sort_keys=True), flush=True)
        except (KeyError, RuntimeError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            print(f"O1_CONTROLLER_RETRY {type(exc).__name__}: {exc}", flush=True)
        time.sleep(1)


def main() -> int:
    mode = os.environ.get("O1_MODE", "target")
    if mode == "controller":
        controller()
        return 0
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
