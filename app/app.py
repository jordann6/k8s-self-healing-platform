"""Every workload in the self-healing lab, selected by the ROLE env var.

One small stdlib-only image. The baseline and hardened runs use the same image,
the same code and the same fault toggles, so only manifests and policy differ
between them (ADR-0001).

Roles
  web      The service under test. GET / burns WORK_MS of CPU, checks its API
           key against the partner (cached for one second), and answers JSON.
           Port 8080 is HTTP, 8443 serves /healthz over TLS from /tls, and the
           certificate is reloaded when the mounted files change.
           Fault toggles (the injectors for rows A1 and A5):
             POST /fault/hang   every later request on this pod, probes
                                included, blocks forever (a deadlock).
             POST /fault/leak   a background thread allocates memory until the
                                container is killed or LEAK_CAP_MB is reached.
           The image built with RELEASE=v2 answers 30% of GET / with a 500: the
           bad release for row A2.
  partner  A third-party API that accepts the current key and, during a
           rotation grace window, the previous one. The rotation driver sets
           the keys through an admin listener bound to 127.0.0.1 (kubectl exec
           only). It starts from PARTNER_CURRENT / PARTNER_PREVIOUS so a restart
           keeps the rotation state.
  prober   The independent user. Calls web over HTTP (10/s) and HTTPS (5/s),
           open loop, and records every result. GET /stats returns the results
           for a time window. User impact is judged here, never from the app's
           own metrics: a hung request is never counted by the app that hangs.

Nothing here is real data. Keys are random per run.
"""
import hashlib
import json
import os
import random
import signal
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROLE = os.environ.get("ROLE", "web")
RELEASE = os.environ.get("RELEASE", "v1")
POD = os.environ.get("POD_NAME", socket.gethostname())
PORT = int(os.environ.get("HTTP_PORT", "8080"))


def log(**kw) -> None:
    print(json.dumps({"ts": round(time.time(), 3), "role": ROLE, "pod": POD, **kw}), flush=True)


# --------------------------------------------------------------------------- #
# metrics (Prometheus text format, no client library)
# --------------------------------------------------------------------------- #

BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


class Metrics:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: dict[tuple[str, str], int] = {}
        self.hist: dict[str, list[int]] = {}
        self.sums: dict[str, float] = {}

    def observe(self, path: str, code: int, seconds: float) -> None:
        with self.lock:
            key = (path, str(code))
            self.requests[key] = self.requests.get(key, 0) + 1
            h = self.hist.setdefault(path, [0] * (len(BUCKETS) + 1))
            for i, le in enumerate(BUCKETS):
                if seconds <= le:
                    h[i] += 1
            h[-1] += 1
            self.sums[path] = self.sums.get(path, 0.0) + seconds

    def render(self) -> str:
        out = ["# TYPE http_requests_total counter"]
        with self.lock:
            for (path, code), n in sorted(self.requests.items()):
                out.append(f'http_requests_total{{path="{path}",code="{code}"}} {n}')
            out.append("# TYPE http_request_duration_seconds histogram")
            for path, h in sorted(self.hist.items()):
                for i, le in enumerate(BUCKETS):
                    out.append(f'http_request_duration_seconds_bucket{{path="{path}",le="{le}"}} {h[i]}')
                out.append(f'http_request_duration_seconds_bucket{{path="{path}",le="+Inf"}} {h[-1]}')
                out.append(f'http_request_duration_seconds_count{{path="{path}"}} {h[-1]}')
                out.append(f'http_request_duration_seconds_sum{{path="{path}"}} {self.sums[path]:.6f}')
        out.append("# TYPE lab_leaked_bytes gauge")
        out.append(f"lab_leaked_bytes {sum(len(b) for b in LEAK)}")
        return "\n".join(out) + "\n"


METRICS = Metrics()

# --------------------------------------------------------------------------- #
# web
# --------------------------------------------------------------------------- #

HANG = threading.Event()
LEAK: list[bytes] = []
WORK_MS = float(os.environ.get("WORK_MS", "10"))
# Admission control: at most MAX_INFLIGHT requests to / do work at once; the
# rest get a fast 503. 0 = unlimited (the default, as most servers ship).
# Health probes are never shed, so a busy pod stays Ready and keeps reporting
# CPU to the HPA instead of dropping out of the Service.
MAX_INFLIGHT = int(os.environ.get("MAX_INFLIGHT", "0"))
INFLIGHT = threading.BoundedSemaphore(MAX_INFLIGHT) if MAX_INFLIGHT > 0 else None
LEAK_CAP_MB = int(os.environ.get("LEAK_CAP_MB", "320"))
LEAK_MB_PER_S = int(os.environ.get("LEAK_MB_PER_S", "16"))
PARTNER_URL = os.environ.get("PARTNER_URL", "http://partner.selfheal.svc.cluster.local:8080/v1/check")
API_KEY = os.environ.get("PARTNER_API_KEY", "")


class PartnerCheck:
    """The partner's verdict on this pod's key, cached for one second so the
    partner is never the bottleneck under load. A revoked key fails every
    request within a second of the revocation."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.ok = False
        self.at = 0.0

    def valid(self) -> bool:
        with self.lock:
            if time.monotonic() - self.at < 1.0:
                return self.ok
            try:
                req = urllib.request.Request(PARTNER_URL, headers={"X-Api-Key": API_KEY})
                with urllib.request.urlopen(req, timeout=1) as r:
                    self.ok = r.status == 200
            except urllib.error.HTTPError as e:
                self.ok = False
                log(event="partner_rejected", status=e.code)
            except Exception as e:  # noqa: BLE001 - an unreachable partner is a failed check
                self.ok = False
                log(event="partner_unreachable", error=str(e))
            self.at = time.monotonic()
            return self.ok


PARTNER = PartnerCheck()


def burn_cpu(ms: float) -> None:
    """Consume ms of this thread's CPU time (not wall time), so CPU throttling
    shows up as latency the way real work does."""
    end = time.thread_time() + ms / 1000.0
    x = b"selfheal"
    while time.thread_time() < end:
        x = hashlib.sha256(x).digest()


def leak() -> None:
    log(event="leak_started", mb_per_s=LEAK_MB_PER_S, cap_mb=LEAK_CAP_MB)
    chunk = 4 * 1024 * 1024
    while sum(len(b) for b in LEAK) < LEAK_CAP_MB * 1024 * 1024:
        LEAK.append(b"\x01" * chunk)  # written, so the pages are resident
        time.sleep(chunk / (LEAK_MB_PER_S * 1024 * 1024))
    log(event="leak_capped", mb=LEAK_CAP_MB)


class Base(BaseHTTPRequestHandler):
    server_version = "selfheal"
    protocol_version = "HTTP/1.0"  # one request per connection: new pods get traffic at once

    def log_message(self, *_a) -> None:
        pass

    def reply(self, code: int, body: dict | str, ctype: str = "application/json") -> None:
        data = body if isinstance(body, str) else json.dumps(body)
        raw = data.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Pod", POD)
        self.send_header("X-Release", RELEASE)
        self.end_headers()
        self.wfile.write(raw)


class WebHandler(Base):
    def do_GET(self) -> None:
        if HANG.is_set():
            while True:  # the deadlock: this thread never answers
                time.sleep(3600)
        start = time.monotonic()
        path = self.path.split("?")[0]
        if path == "/metrics":
            return self.reply(200, METRICS.render(), "text/plain; version=0.0.4")
        if path in ("/healthz", "/readyz"):
            code = 200
            self.reply(code, {"ok": True})
        elif path == "/" and INFLIGHT is not None and not INFLIGHT.acquire(blocking=False):
            code = 503
            self.reply(code, {"error": "overloaded, shedding", "pod": POD})
        elif path == "/":
            try:
                burn_cpu(WORK_MS)
            finally:
                if INFLIGHT is not None:
                    INFLIGHT.release()
            if not PARTNER.valid():
                code = 502
                self.reply(code, {"error": "partner rejected the API key", "pod": POD})
            elif RELEASE == "v2" and random.random() < 0.3:
                code = 500
                self.reply(code, {"error": "regression in v2", "pod": POD})
            else:
                code = 200
                self.reply(code, {"pod": POD, "release": RELEASE})
        else:
            code = 404
            self.reply(code, {"error": "not found"})
        METRICS.observe(path, code, time.monotonic() - start)

    def do_POST(self) -> None:
        if self.path == "/fault/hang":
            self.reply(200, {"fault": "hang", "pod": POD})
            log(event="fault_injected", fault="hang")
            HANG.set()
        elif self.path == "/fault/leak":
            threading.Thread(target=leak, daemon=True).start()
            self.reply(200, {"fault": "leak", "pod": POD})
        else:
            self.reply(404, {"error": "not found"})


class TLSHandler(Base):
    def do_GET(self) -> None:
        if HANG.is_set():
            while True:
                time.sleep(3600)
        self.reply(200, {"ok": True, "pod": POD})


class ReloadingTLSServer(ThreadingHTTPServer):
    """HTTPS that picks up a renewed certificate without a restart: the context
    is rebuilt whenever the mounted key pair changes. The same code runs in both
    profiles, so the baseline/hardened difference for row S2 is only who renews
    the certificate, never whether the app can load a new one."""

    daemon_threads = True

    def __init__(self, addr, handler, cert: str, key: str) -> None:
        self.cert, self.key = cert, key
        self.ctx, self.stamp = None, None
        self.lock = threading.Lock()
        super().__init__(addr, handler)

    def context(self) -> ssl.SSLContext:
        st = os.stat(self.cert)
        stamp = (st.st_mtime_ns, st.st_ino, st.st_size)
        with self.lock:
            if stamp != self.stamp:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(self.cert, self.key)
                self.ctx, self.stamp = ctx, stamp
                log(event="tls_loaded")
            return self.ctx

    def get_request(self):
        sock, addr = self.socket.accept()
        return self.context().wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr


# --------------------------------------------------------------------------- #
# partner
# --------------------------------------------------------------------------- #

KEYS = {"current": os.environ.get("PARTNER_CURRENT", ""), "previous": os.environ.get("PARTNER_PREVIOUS", "")}
KEYS_LOCK = threading.Lock()


class PartnerHandler(Base):
    def do_GET(self) -> None:
        if self.path == "/healthz":
            return self.reply(200, {"ok": True})
        if self.path == "/v1/check":
            key = self.headers.get("X-Api-Key", "")
            with KEYS_LOCK:
                accepted = {k for k in KEYS.values() if k}
            if key and key in accepted:
                return self.reply(200, {"ok": True})
            return self.reply(401, {"error": "invalid API key"})
        self.reply(404, {"error": "not found"})


class PartnerAdmin(Base):
    def do_POST(self) -> None:
        if self.path != "/admin/keys":
            return self.reply(404, {"error": "not found"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        with KEYS_LOCK:
            KEYS["current"] = body.get("current") or ""
            KEYS["previous"] = body.get("previous") or ""
        log(event="keys_set", previous_accepted=bool(KEYS["previous"]))
        self.reply(200, {"ok": True})


# --------------------------------------------------------------------------- #
# prober
# --------------------------------------------------------------------------- #

WEB_HTTP = os.environ.get("PROBE_HTTP_URL", "http://web.selfheal.svc.cluster.local:8080/")
WEB_TLS_HOST = os.environ.get("PROBE_TLS_HOST", "web.selfheal.svc.cluster.local")
CA_FILE = os.environ.get("PROBE_CA_FILE", "/etc/lab-ca/ca.crt")
HTTP_RPS = float(os.environ.get("PROBE_HTTP_RPS", "10"))
TLS_RPS = float(os.environ.get("PROBE_TLS_RPS", "5"))
TIMEOUT = float(os.environ.get("PROBE_TIMEOUT_S", "2"))
SAMPLES: deque = deque(maxlen=200_000)
SAMPLES_LOCK = threading.Lock()


def record(**s) -> None:
    with SAMPLES_LOCK:
        SAMPLES.append(s)


def probe_http(t0: float) -> None:
    try:
        with urllib.request.urlopen(WEB_HTTP, timeout=TIMEOUT) as r:
            r.read()
            record(t=t0, kind="http", ok=True, code=r.status, ms=(time.time() - t0) * 1000,
                   pod=r.headers.get("X-Pod", ""), release=r.headers.get("X-Release", ""))
    except urllib.error.HTTPError as e:
        record(t=t0, kind="http", ok=False, code=e.code, ms=(time.time() - t0) * 1000,
               pod=e.headers.get("X-Pod", "") if e.headers else "", err=f"http {e.code}")
    except Exception as e:  # noqa: BLE001 - every failure is a user-visible error
        record(t=t0, kind="http", ok=False, code=0, ms=(time.time() - t0) * 1000, err=type(e).__name__)


def probe_tls(t0: float) -> None:
    try:
        ctx = ssl.create_default_context(cafile=CA_FILE)
        with socket.create_connection((WEB_TLS_HOST, 8443), timeout=TIMEOUT) as raw:
            with ctx.wrap_socket(raw, server_hostname=WEB_TLS_HOST) as s:
                cert = s.getpeercert()
                s.sendall(f"GET /healthz HTTP/1.0\r\nHost: {WEB_TLS_HOST}\r\n\r\n".encode())
                resp = s.recv(256).decode(errors="replace")
        ok = resp.startswith("HTTP/1.0 200")
        record(t=t0, kind="tls", ok=ok, ms=(time.time() - t0) * 1000, serial=cert.get("serialNumber", ""),
               not_after=cert.get("notAfter", ""), err="" if ok else "bad status")
    except ssl.SSLCertVerificationError as e:
        record(t=t0, kind="tls", ok=False, ms=(time.time() - t0) * 1000, err=f"verify: {e.verify_message}")
    except Exception as e:  # noqa: BLE001
        record(t=t0, kind="tls", ok=False, ms=(time.time() - t0) * 1000, err=type(e).__name__)


def open_loop(fn, rps: float) -> None:
    """Fire on a fixed schedule whether or not earlier requests have returned,
    so a hung backend cannot slow the prober down and hide its own impact."""
    period, nxt = 1.0 / rps, time.time()
    while True:
        threading.Thread(target=fn, args=(time.time(),), daemon=True).start()
        nxt += period
        time.sleep(max(0.0, nxt - time.time()))


def p95(values: list[float]) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(0.95 * len(v)))], 1)


class ProberHandler(Base):
    def do_GET(self) -> None:
        path, _, qs = self.path.partition("?")
        if path == "/healthz":
            return self.reply(200, {"ok": True})
        if path == "/now":
            return self.reply(200, {"now": time.time()})
        if path == "/stats":
            q = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
            kind = q.get("kind", "http")
            since, until = float(q.get("since", "0")), float(q.get("until", str(time.time())))
            with SAMPLES_LOCK:
                rows = [s for s in SAMPLES if s["kind"] == kind and since <= s["t"] < until]
            errors = [s for s in rows if not s["ok"]]
            out = {
                "kind": kind, "since": since, "until": until, "n": len(rows), "errors": len(errors),
                "error_rate": round(len(errors) / len(rows), 4) if rows else None,
                "p95_ms": p95([s["ms"] for s in rows if s["ok"]]),
                "error_kinds": {},
                "first_error": min((s["t"] for s in errors), default=None),
                "last_error": max((s["t"] for s in errors), default=None),
            }
            for s in errors:
                out["error_kinds"][s.get("err", "?")] = out["error_kinds"].get(s.get("err", "?"), 0) + 1
            if kind == "http":
                out["releases"] = sorted({s.get("release", "") for s in rows if s["ok"]})
                out["pods"] = sorted({s.get("pod", "") for s in rows if s["ok"]})
            else:
                out["serials"] = sorted({s.get("serial", "") for s in rows if s["ok"]})
                last_ok = [s for s in rows if s["ok"]][-10:]
                out["recent_serials"] = sorted({s.get("serial", "") for s in last_ok})
            if q.get("samples") == "1":
                out["samples"] = rows
            return self.reply(200, out)
        self.reply(404, {"error": "not found"})


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def serve(server: ThreadingHTTPServer) -> threading.Thread:
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


def main() -> int:
    servers: list[ThreadingHTTPServer] = []
    if ROLE == "web":
        if not API_KEY:
            log(event="fatal", error="PARTNER_API_KEY is empty")
            return 1
        servers.append(ThreadingHTTPServer(("0.0.0.0", PORT), WebHandler))
        cert, key = "/tls/tls.crt", "/tls/tls.key"
        if os.path.exists(cert):
            servers.append(ReloadingTLSServer(("0.0.0.0", 8443), TLSHandler, cert, key))
    elif ROLE == "partner":
        servers.append(ThreadingHTTPServer(("0.0.0.0", PORT), PartnerHandler))
        servers.append(ThreadingHTTPServer(("127.0.0.1", 9000), PartnerAdmin))
    elif ROLE == "prober":
        servers.append(ThreadingHTTPServer(("0.0.0.0", PORT), ProberHandler))
        threading.Thread(target=open_loop, args=(probe_http, HTTP_RPS), daemon=True).start()
        if TLS_RPS > 0:
            threading.Thread(target=open_loop, args=(probe_tls, TLS_RPS), daemon=True).start()
    else:
        log(event="fatal", error=f"unknown ROLE {ROLE!r}")
        return 1
    for s in servers:
        s.daemon_threads = True
        serve(s)
    log(event="started", release=RELEASE, port=PORT)

    stop = threading.Event()

    def on_term(*_a) -> None:
        # Stop accepting, give in-flight requests a moment, exit. Same code in
        # both profiles; whether endpoints drain first is the manifest's job.
        log(event="sigterm")
        for s in servers:
            threading.Thread(target=s.shutdown, daemon=True).start()
        stop.set()

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    stop.wait()
    time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
