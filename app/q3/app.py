"""Deterministic resource workload for Q3 VPA recommendations."""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def settings(env: dict[str, str] | None = None) -> dict[str, int]:
    values = env or os.environ
    cpu_m = int(values.get("CPU_M", "200"))
    memory_mib = int(values.get("MEMORY_MIB", "96"))
    if not 0 <= cpu_m <= 900:
        raise ValueError("CPU_M must be between 0 and 900")
    if not 0 <= memory_mib <= 192:
        raise ValueError("MEMORY_MIB must be between 0 and 192")
    return {"cpu_m": cpu_m, "memory_mib": memory_mib}


def allocate_memory(memory_mib: int) -> bytearray:
    """Allocate and touch each page so working-set telemetry observes it."""
    block = bytearray(memory_mib * 1024 * 1024)
    for offset in range(0, len(block), 4096):
        block[offset] = 1
    return block


def burn_cpu(cpu_m: int, stop: threading.Event) -> None:
    """Use a 100 ms duty cycle to consume a stable fraction of one core."""
    period = 0.1
    busy = period * cpu_m / 1000
    value = 1
    while not stop.is_set():
        started = time.perf_counter()
        while time.perf_counter() - started < busy:
            value = (value * 1103515245 + 12345) & 0x7FFFFFFF
        remaining = period - (time.perf_counter() - started)
        if remaining > 0:
            stop.wait(remaining)
    if value == -1:  # pragma: no cover
        raise RuntimeError("unreachable")


class Handler(BaseHTTPRequestHandler):
    server_version = "selfheal-q3"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self.send_error(404)
            return
        body = json.dumps(self.server.settings, sort_keys=True).encode()  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    configured = settings()
    retained = allocate_memory(configured["memory_mib"])
    stop = threading.Event()
    worker = threading.Thread(target=burn_cpu, args=(configured["cpu_m"], stop), daemon=True)
    worker.start()
    server = ThreadingHTTPServer(("0.0.0.0", 8082), Handler)  # noqa: S104
    server.settings = configured  # type: ignore[attr-defined]
    try:
        server.serve_forever()
    finally:
        stop.set()
        worker.join(timeout=2)
        if retained:
            retained[0] = 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
