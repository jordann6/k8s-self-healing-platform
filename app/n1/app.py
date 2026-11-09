"""N1 cross-node HTTP endpoint and bounded request probe."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class Handler(BaseHTTPRequestHandler):
    server_version = "selfheal-n1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps({"path": self.path, "ok": True}, sort_keys=True).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def probe(url: str, count: int, timeout: float) -> dict[str, Any]:
    samples = []
    for _ in range(count):
        started = time.perf_counter()
        status = None
        error = None
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                response.read()
                status = response.status
        except (OSError, urllib.error.URLError) as exc:
            error = type(exc).__name__
        samples.append({"ok": status == 200, "status": status, "error": error,
                        "latency_ms": round((time.perf_counter() - started) * 1000, 3)})
    return {"url": url, "count": count, "samples": samples}


def main(argv: list[str]) -> int:
    if len(argv) == 1 or argv[1] == "serve":
        ThreadingHTTPServer(("0.0.0.0", 8084), Handler).serve_forever()  # noqa: S104
        return 0
    if argv[1] == "hold":
        while True:
            time.sleep(3600)
    if argv[1] == "probe" and len(argv) == 5:
        print(json.dumps(probe(argv[2], int(argv[3]), float(argv[4])), sort_keys=True))
        return 0
    raise SystemExit("usage: app.py serve|hold|probe URL COUNT TIMEOUT")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
