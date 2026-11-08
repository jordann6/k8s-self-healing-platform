"""Q2 scaling-failure workload, metric source, and request generator.

The three roles share one small, dependency-free image:

* ``serve`` runs the bounded HTTP workload.
* ``signal`` exposes the metric KEDA reads and an experiment-only admin path.
* ``load`` generates timestamped requests for the scenario runner to judge.

The metric source can return a deliberate HTTP 503 while its admin path stays
available. That makes loss and restoration of the scaling signal explicit.
"""
from __future__ import annotations

import concurrent.futures
import json
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def percentile(values: list[float], p: float) -> float | None:
    """Return a nearest-rank percentile, or None when there is no data."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(p * len(ordered)) - 1))
    return round(ordered[index], 1)


def summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize request samples without treating no data as success."""
    durations = [float(s["latency_ms"]) for s in samples if s.get("ok")]
    failures = sum(1 for s in samples if not s.get("ok"))
    return {
        "samples": len(samples),
        "failures": failures,
        "failure_rate": None if not samples else failures / len(samples),
        "p95_ms": percentile(durations, 0.95),
    }


def validate_admin(body: Any) -> dict[str, Any]:
    """Validate a metric-source state update."""
    if not isinstance(body, dict):
        raise ValueError("admin body must be an object")
    out: dict[str, Any] = {}
    if "demand" in body:
        demand = body["demand"]
        if isinstance(demand, bool) or not isinstance(demand, (int, float)):
            raise ValueError("demand must be a number")
        if demand < 0 or demand > 100:
            raise ValueError("demand must be between 0 and 100")
        out["demand"] = float(demand)
    if "available" in body:
        if not isinstance(body["available"], bool):
            raise ValueError("available must be a boolean")
        out["available"] = body["available"]
    if not out:
        raise ValueError("admin update is empty")
    return out


class SignalState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._demand = 0.0
        self._available = True

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if "demand" in values:
                self._demand = values["demand"]
            if "available" in values:
                self._available = values["available"]
            return {"demand": self._demand, "available": self._available}

    def read(self) -> dict[str, Any]:
        with self._lock:
            return {"demand": self._demand, "available": self._available}


SIGNAL = SignalState()
WORK_SLOTS = threading.BoundedSemaphore(int(os.environ.get("WORK_SLOTS", "1")))


class JsonHandler(BaseHTTPRequestHandler):
    server_version = "selfheal-q2"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def send_json(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class WorkHandler(JsonHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/":
            self.send_json(404, {"error": "not found"})
            return
        timeout = int(os.environ.get("ACQUIRE_TIMEOUT_MS", "150")) / 1000
        if not WORK_SLOTS.acquire(timeout=timeout):
            self.send_json(503, {"error": "capacity"})
            return
        try:
            time.sleep(int(os.environ.get("WORK_MS", "60")) / 1000)
            self.send_json(200, {"ok": True})
        finally:
            WORK_SLOTS.release()


class SignalHandler(JsonHandler):
    def do_GET(self) -> None:  # noqa: N802
        state = SIGNAL.read()
        if self.path == "/health":
            self.send_json(200, state)
        elif self.path == "/metric":
            if state["available"]:
                self.send_json(200, {"demand": state["demand"]})
            else:
                self.send_json(503, {"error": "metric unavailable"})
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/admin":
            self.send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 1024:
                raise ValueError("invalid content length")
            update = validate_admin(json.loads(self.rfile.read(length)))
            self.send_json(200, SIGNAL.update(update))
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})


def one_request(url: str, timeout: float) -> dict[str, Any]:
    started = time.time()
    status = 0
    error = ""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            status = response.status
            response.read()
    except urllib.error.HTTPError as exc:
        status, error = exc.code, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        error = type(exc).__name__
    ended = time.time()
    return {
        "started": round(started, 6),
        "ended": round(ended, 6),
        "latency_ms": round((ended - started) * 1000, 1),
        "status": status,
        "ok": 200 <= status < 300,
        "error": error,
    }


def run_load() -> int:
    url = os.environ.get("TARGET_URL", "http://q2-target.selfheal.svc.cluster.local:8080/")
    rate = float(os.environ.get("RATE", "30"))
    duration = float(os.environ.get("DURATION", "60"))
    timeout = float(os.environ.get("REQUEST_TIMEOUT", "2"))
    started = time.monotonic()
    futures: list[concurrent.futures.Future[dict[str, Any]]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as pool:
        index = 0
        while True:
            due = index / rate
            remaining = due - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
            if time.monotonic() - started >= duration:
                break
            futures.append(pool.submit(one_request, url, timeout))
            index += 1
        samples = [future.result() for future in futures]
    for sample in samples:
        print("Q2_SAMPLE " + json.dumps(sample, sort_keys=True), flush=True)
    print("Q2_SUMMARY " + json.dumps(summarize(samples), sort_keys=True), flush=True)
    return 0 if samples else 2


def serve(handler: type[BaseHTTPRequestHandler], port: int) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)  # noqa: S104
    server.serve_forever()


def main() -> int:
    role = sys.argv[1] if len(sys.argv) > 1 else ""
    if role == "serve":
        serve(WorkHandler, 8080)
    elif role == "signal":
        serve(SignalHandler, 8081)
    elif role == "load":
        return run_load()
    elif role == "hold":
        while True:
            time.sleep(3600)
    else:
        print("usage: app.py serve|signal|load|hold", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
