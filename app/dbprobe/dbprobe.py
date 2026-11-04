"""Row S1b's database client and its own independent measurement.

It behaves like an application with a connection pool:

  * one long-lived connection runs SELECT 1 every second. If a password
    change ever broke an open session, this is where it shows ("dropped").
  * one new connection per second (pool churn) logs in, runs SELECT 1 and
    closes. A stale credential shows here ("failed").

How it finds its credential is the experiment (CRED_MODE):

  env    DB_USER / DB_PASSWORD read once at start (the baseline wiring)
  files  /creds/current.json then /creds/previous.json, re-read on every new
         connection (the hardened wiring, both Secrets Manager versions)

GET /stats?since=&until=  counts in a window (epoch seconds, this pod's clock)
GET /now                  this pod's clock
GET /creds                which username each credential source holds now

python3 dbprobe.py --check-login   reads {"username","password"} on stdin and
    prints whether that credential still logs in. The runner uses it (through
    kubectl exec, never over the network) to prove the rotation invalidated
    the original password, which the judge requires.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg2

HOST = os.environ["DB_HOST"]
PORT = int(os.environ.get("DB_PORT", "5432"))
DBNAME = os.environ.get("DB_NAME", "selfheal")
MODE = os.environ.get("CRED_MODE", "env")
CA = os.environ.get("RDS_CA_BUNDLE", "/app/rds-ca-bundle.pem")
CREDS_DIR = os.environ.get("CREDS_DIR", "/creds")

EVENTS: list[tuple[float, str, bool, str, str]] = []  # (t, kind, ok, user, error)
LOCK = threading.Lock()
ENV_CRED = {"username": os.environ.get("DB_USER", ""), "password": os.environ.get("DB_PASSWORD", "")}


def record(kind: str, ok: bool, user: str = "", err: str = "") -> None:
    with LOCK:
        EVENTS.append((time.time(), kind, ok, user, err[:200]))
        if len(EVENTS) > 50_000:
            del EVENTS[:10_000]


def candidates() -> list[dict]:
    if MODE == "env":
        return [ENV_CRED]
    out = []
    for name in ("current.json", "previous.json"):
        try:
            with open(os.path.join(CREDS_DIR, name)) as f:
                d = json.load(f)
            out.append({"username": d["username"], "password": d["password"]})
        except (OSError, ValueError, KeyError):
            continue
    return out


def connect(cred: dict):
    return psycopg2.connect(host=HOST, port=PORT, dbname=DBNAME, user=cred["username"], password=cred["password"],
                            sslmode="verify-full", sslrootcert=CA, connect_timeout=5, application_name="dbprobe")


def is_auth_error(e: Exception) -> bool:
    return "password authentication failed" in str(e) or "no pg_hba.conf entry" in str(e)


def login() -> tuple[object | None, str, str]:
    """First credential that logs in. Falls through to the next only on an
    authentication failure; any other error is reported as is."""
    last = "no credential available"
    for cred in candidates():
        try:
            return connect(cred), cred["username"], ""
        except psycopg2.OperationalError as e:
            last = str(e).strip()
            if not is_auth_error(e):
                break
    return None, "", last


def churn() -> None:
    while True:
        t = time.time()
        conn, user, err = login()
        if conn is None:
            record("query", False, "", err)
        else:
            try:
                with conn.cursor() as c:
                    c.execute("SELECT 1")
                    c.fetchone()
                record("query", True, user)
            except psycopg2.Error as e:
                record("query", False, user, str(e))
            finally:
                conn.close()
        time.sleep(max(0.0, 1.0 - (time.time() - t)))


def long_lived() -> None:
    conn, user = None, ""
    while True:
        t = time.time()
        if conn is None:
            conn, user, err = login()
            if conn is None:
                record("session", False, "", err)
                time.sleep(1)
                continue
            conn.autocommit = True
        try:
            with conn.cursor() as c:
                c.execute("SELECT 1")
                c.fetchone()
            record("session", True, user)
        except psycopg2.Error as e:
            # An open session that breaks is a dropped connection.
            record("dropped", False, user, str(e))
            try:
                conn.close()
            except psycopg2.Error:
                pass
            conn = None
        time.sleep(max(0.0, 1.0 - (time.time() - t)))


def stats(since: float, until: float) -> dict:
    with LOCK:
        ev = [e for e in EVENTS if since <= e[0] <= until]
    q = [e for e in ev if e[1] == "query"]
    return {
        "samples": len(q),
        "failed": sum(1 for e in q if not e[2]),
        "dropped": sum(1 for e in ev if e[1] == "dropped"),
        "session_failed": sum(1 for e in ev if e[1] == "session" and not e[2]),
        "users_seen": sorted({e[3] for e in ev if e[2] and e[3]}),
        "first_error": next((e[4] for e in q if not e[2]), ""),
        "mode": MODE,
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/healthz":
            return self._send(200, {"ok": True})
        if u.path == "/now":
            return self._send(200, {"now": time.time()})
        if u.path == "/stats":
            return self._send(200, stats(float(q.get("since", 0)), float(q.get("until", time.time()))))
        if u.path == "/creds":
            return self._send(200, {"mode": MODE, "usernames": [c["username"] for c in candidates()]})
        self._send(404, {"error": "not found"})

    def log_message(self, *_args) -> None:
        pass


def check_login() -> int:
    cred = json.load(sys.stdin)
    try:
        connect(cred).close()
        print(json.dumps({"login": True}))
    except psycopg2.OperationalError as e:
        print(json.dumps({"login": False, "auth_error": is_auth_error(e), "error": str(e).strip()[:200]}))
    return 0


if __name__ == "__main__":
    if "--check-login" in sys.argv:
        sys.exit(check_login())
    for fn in (churn, long_lived):
        threading.Thread(target=fn, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
