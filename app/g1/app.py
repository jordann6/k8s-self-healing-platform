"""G1 backend. Answers every path with its own name so the runner can tell
which backend the Gateway reached, and serves /healthz for probes."""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NAME = os.environ.get("G1_BACKEND", "unnamed")


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._reply(200, {"ok": True})
        else:
            self._reply(200, {"backend": NAME, "path": self.path})

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> int:
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
