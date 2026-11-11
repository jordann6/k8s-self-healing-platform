"""Small deterministic HTTP workload used by the P1 admission proof."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class Handler(BaseHTTPRequestHandler):
    server_version = "selfheal-p1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self.send_error(404)
            return
        body = json.dumps({"artifact": "p1", "status": "ok"}, sort_keys=True).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    ThreadingHTTPServer(("0.0.0.0", 8085), Handler).serve_forever()  # noqa: S104
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
