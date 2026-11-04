#!/usr/bin/env python3
"""Scenario runner: every row, baseline then hardened, with receipts.

One invocation runs the whole experiment against the lab cluster:

  1. preconditions  right cluster, every platform component ready, the prober,
                    Prometheus and Hubble reachable.
  2. baseline       switch the profile (one Git path change Argo CD syncs, plus
                    the Talon response rules), then run every row.
  3. hardened       switch the profile, run every row again.
  4. report         evidence/<run-id>/report.md + report.json, per-row prober
                    samples, and the R1 evidence bundle.

Per row the report records: the steady-state hypothesis, the event at T0, when
it was detected, the healing action, when it recovered, and the user impact
measured by the independent prober (failed requests, error rate, burn rate).

Verdicts and their honesty rules live in judge.py and are unit tested. The exit
code is non-zero unless every hardened row is "healed". A row is never
skipped: a row that cannot be measured is "error", and error fails the run.

Stdlib only. Needs kubectl, helm, cilium, hubble and openssl on PATH.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from judge import HYPOTHESES, THRESHOLDS, overall, verdict  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("KUBECONFIG", str(ROOT / ".kube" / "config"))
CONTEXT = "kind-selfheal"
TARGET = "kind"
NS = "selfheal"
ROWS = ["A1", "A2", "A3", "A5", "S1", "S2", "S3", "R1"]
TALON_VERSION = os.environ.get("TALON_VERSION", "0.5.0")
K6_IMAGE = "grafana/k6:2.3.0"
HTTP_RPS, TLS_RPS = 5, 2  # must match gitops/base/prober.yaml
# Abort line: if more than half the prober's requests in the last 10s failed,
# the injection is stopped. Baseline: that is the impact. Hardened: a miss.
ABORT_ERROR_RATE = 0.5
ABORT: dict = {}  # evidence for the abort line in the current row, set by watch()
ACTIONS = {
    "A1": "readiness removes the hung pod; liveness restarts it",
    "A2": "Argo Rollouts canary analysis aborts; stable keeps serving",
    "A3": "HPA scales the Rollout out",
    "A5": "memory limit OOMKills the leak; kubelet restarts it; OOM alert pages with a runbook",
    "S1": "ESO syncs the rotated key; Reloader restarts the pods",
    "S2": "cert-manager renews; the app hot-reloads the mounted pair",
    "S3": "Argo CD self-heal re-applies Git",
    "R1": "Falco -> Falcosidekick -> Talon labels the pod; quarantine CiliumNetworkPolicy isolates it",
}

# --------------------------------------------------------------------------- #
# plumbing
# --------------------------------------------------------------------------- #


def now() -> float:
    return time.time()


def iso(t: float | None) -> str:
    return "" if t is None else dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


def rfc3339(t: float) -> str:
    """Second-precision RFC 3339, the form Kubernetes date-time fields accept."""
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def sh(args: list[str], timeout: int = 60, check: bool = False, input: str | None = None) -> subprocess.CompletedProcess:
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, input=input)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:6])} failed: {(p.stderr or p.stdout).strip()[:400]}")
    return p


def k(*args: str, timeout: int = 120, check: bool = False, input: str | None = None) -> subprocess.CompletedProcess:
    return sh(["kubectl", *args], timeout=timeout, check=check, input=input)


def kj(*args: str, timeout: int = 120) -> dict:
    p = k(*args, "-o", "json", timeout=timeout)
    if p.returncode != 0:
        return {}
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {}


def apply(manifest: dict | str) -> None:
    k("apply", "-f", "-", input=manifest if isinstance(manifest, str) else json.dumps(manifest), check=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(fn, timeout: float, step: float = 2.0, what: str = "condition"):
    end = now() + timeout
    while now() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    raise TimeoutError(f"timed out after {timeout:.0f}s waiting for {what}")


class PortForward:
    def __init__(self, ns: str, target: str, remote: int) -> None:
        self.ns, self.target, self.remote = ns, target, remote
        self.port = free_port()
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "PortForward":
        # A busy single-node API server can take a while to open the stream;
        # respawn rather than fail the row.
        for _attempt in range(3):
            self.proc = subprocess.Popen(["kubectl", "-n", self.ns, "port-forward", self.target, f"{self.port}:{self.remote}"],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(80):
                try:
                    with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                        return self
                except OSError:
                    if self.proc.poll() is not None:
                        break
                    time.sleep(0.25)
            self.__exit__()
            self.port = free_port()
        raise RuntimeError(f"port-forward to {self.ns}/{self.target} never came up")

    def __exit__(self, *exc) -> None:
        if self.proc:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def get(self, path: str, timeout: float = 10) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=timeout) as r:
            return json.loads(r.read())


class HubbleRelay:
    def __enter__(self) -> "HubbleRelay":
        self.proc = subprocess.Popen(["cilium", "hubble", "port-forward"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            if sh(["hubble", "status"], timeout=10).returncode == 0:
                return self
            time.sleep(0.5)
        raise RuntimeError("hubble relay not reachable on localhost:4245")

    def __exit__(self, *exc) -> None:
        self.proc.send_signal(signal.SIGTERM)
        self.proc.wait(timeout=10)


# --------------------------------------------------------------------------- #
# the lab: prober, Prometheus, workload state
# --------------------------------------------------------------------------- #


class Lab:
    """Long-lived port-forwards and the prober clock offset. Prober and
    Prometheus timestamps are the cluster's clock; every runner timestamp is
    this machine's. All windows are translated through the measured offset."""

    def __init__(self) -> None:
        self.prober = PortForward(NS, "svc/prober", 8080)
        self.prom = PortForward("monitoring", "svc/kps-prometheus", 9090)
        self.offset = 0.0

    def __enter__(self) -> "Lab":
        self.prober.__enter__()
        self.prom.__enter__()
        t1 = now()
        remote = self.prober.get("/now")["now"]
        self.offset = remote - (t1 + now()) / 2
        return self

    def __exit__(self, *exc) -> None:
        self.prober.__exit__()
        self.prom.__exit__()

    def reconnect(self) -> None:
        """The prober Service can move to a new pod (Argo CD sync); re-open."""
        self.prober.__exit__()
        self.prober = PortForward(NS, "svc/prober", 8080)
        self.prober.__enter__()

    def stats(self, kind: str, since: float, until: float, samples: bool = False) -> dict:
        q = urllib.parse.urlencode({"kind": kind, "since": since + self.offset, "until": until + self.offset,
                                    **({"samples": "1"} if samples else {})})
        for attempt in range(3):
            try:
                return self.prober.get(f"/stats?{q}", timeout=20)
            except Exception:  # noqa: BLE001
                if attempt == 2:
                    raise
                self.reconnect()
        return {}

    def promq(self, query: str) -> list:
        q = urllib.parse.urlencode({"query": query})
        return self.prom.get(f"/api/v1/query?{q}")["data"]["result"]

    def alerts(self) -> list[dict]:
        return self.prom.get("/api/v1/alerts")["data"]["alerts"]

    def rules(self) -> set[str]:
        groups = self.prom.get("/api/v1/rules")["data"]["groups"]
        return {r["name"] for g in groups for r in g["rules"]}


def pods(selector: str = "app=web,lab/pool=serving") -> list[dict]:
    out = []
    for p in kj("-n", NS, "get", "pod", "-l", selector).get("items", []):
        if p["metadata"].get("deletionTimestamp"):
            continue
        cs = next((c for c in p["status"].get("containerStatuses", []) if c["name"] == "web"), {})
        ready = any(c["type"] == "Ready" and c["status"] == "True" for c in p["status"].get("conditions", []))
        out.append({
            "name": p["metadata"]["name"], "ip": p["status"].get("podIP", ""), "ready": ready,
            "restarts": cs.get("restartCount", 0), "last_reason": ((cs.get("lastState") or {}).get("terminated") or {}).get("reason"),
            "running": "running" in (cs.get("state") or {}), "labels": p["metadata"].get("labels", {}),
            "created": dt.datetime.strptime(p["metadata"]["creationTimestamp"], "%Y-%m-%dT%H:%M:%SZ")
                         .replace(tzinfo=dt.timezone.utc).timestamp(),
            "image": next((c["image"] for c in p["spec"]["containers"] if c["name"] == "web"), ""),
        })
    return sorted(out, key=lambda x: x["name"])


def pod(name: str) -> dict | None:
    return next((p for p in pods("app=web") if p["name"] == name), None)


def rollout() -> dict:
    return kj("-n", NS, "get", "rollout", "web")


def rollout_healthy(image_tag: str = "v1") -> bool:
    r = rollout()
    st = r.get("status", {})
    if not r or str(st.get("observedGeneration")) != str(r["metadata"]["generation"]):
        return False
    image = r["spec"]["template"]["spec"]["containers"][0]["image"]
    want = r["spec"].get("replicas", 3)
    serving = [p for p in pods() if p["ready"]]
    return (st.get("phase") == "Healthy" and image.endswith(f":{image_tag}")
            and len(serving) >= want and all(p["image"].endswith(f":{image_tag}") for p in serving))


def exec_curl(pod_name: str, *args: str, timeout: int = 15) -> subprocess.CompletedProcess:
    # curl, never a shell: only the R1 attack runs a shell in a web pod.
    return k("-n", NS, "exec", pod_name, "-c", "web", "--", "curl", "-s", "-m", "5", *args, timeout=timeout)


def backend_key() -> str:
    import base64
    d = kj("-n", "secret-backend", "get", "secret", "partner-api-key")
    return base64.b64decode(d["data"]["api-key"]).decode()


def synced_key() -> str:
    import base64
    d = kj("-n", NS, "get", "secret", "partner-api-key")
    return base64.b64decode(d.get("data", {}).get("api-key", "")).decode() if d else ""


def pod_key(pod_name: str) -> str:
    """The key a web pod is actually using (printenv, not a shell)."""
    return k("-n", NS, "exec", pod_name, "-c", "web", "--", "printenv", "PARTNER_API_KEY", timeout=20).stdout.strip()


def partner_set(current: str, previous: str = "") -> None:
    """The partner's accepted keys: its admin call plus the Secret it restarts
    from, kept in step."""
    apply({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "partner-keys", "namespace": NS},
           "stringData": {"current": current, "previous": previous}})
    body = json.dumps({"current": current, "previous": previous})

    def live_partner() -> str:
        # A pod being replaced (after a hand edit or its revert) lingers as
        # Succeeded or terminating; only a Running, Ready one takes the call.
        for i in kj("-n", NS, "get", "pod", "-l", "app=partner").get("items", []):
            ready = any(c["type"] == "Ready" and c["status"] == "True" for c in i["status"].get("conditions", []))
            if i["status"].get("phase") == "Running" and ready and not i["metadata"].get("deletionTimestamp"):
                return i["metadata"]["name"]
        return ""

    for attempt in range(3):
        pod_name = wait_for(live_partner, 90, what="a Running partner pod")
        p = k("-n", NS, "exec", pod_name, "--", "curl", "-s", "-m", "5", "-X", "POST", "-H",
              "Content-Type: application/json", "--data", body, "http://127.0.0.1:9000/admin/keys")
        if p.returncode == 0:
            return
        time.sleep(3)
    raise RuntimeError(f"could not set partner keys: {p.stderr.strip()[:300]}")


def restart_web() -> None:
    k("-n", NS, "patch", "rollout", "web", "--type", "merge",
      "-p", json.dumps({"spec": {"restartAt": rfc3339(now() + 2)}}), check=True)


# --------------------------------------------------------------------------- #
# Git + Argo CD
# --------------------------------------------------------------------------- #


def rollout_image_tag() -> str:
    img = (rollout().get("spec", {}).get("template", {}).get("spec", {}).get("containers") or [{}])[0].get("image", "")
    return img.rsplit(":", 1)[-1] if ":" in img else ""


def git_set_tag(profile: str, tag: str) -> float:
    """Commit the image tag and wait until Argo CD has applied it to the
    Rollout spec. Retried; raises if the release never lands, so a row can
    never silently run against the wrong version. Returns the push time."""
    last: Exception | None = None
    for _attempt in range(3):
        try:
            t = _git_push_tag(profile, tag)
            wait_for(lambda: (refresh("selfheal-workload") or True) and rollout_image_tag() == tag, 120, step=5,
                     what=f"Argo CD to apply image {tag}")
            return t
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"  git release {tag} retry after: {e}")
            time.sleep(5)
    raise RuntimeError(f"could not ship image {tag} through Git: {last}")


def _git_push_tag(profile: str, tag: str) -> float:
    work = Path(tempfile.mkdtemp())
    try:
        with PortForward("gitops", "svc/gitserver", 9418) as pf:
            url = f"git://127.0.0.1:{pf.port}/lab.git"
            sh(["git", "clone", "-q", url, str(work / "r")], check=True)
            kz = work / "r" / "profiles" / profile / "kustomization.yaml"
            text = kz.read_text()
            new = "\n".join(f"    newTag: {tag}" if ln.strip().startswith("newTag:") else ln for ln in text.splitlines()) + "\n"
            if new == text:
                return now()
            kz.write_text(new)
            g = ["git", "-C", str(work / "r"), "-c", "user.name=selfheal-lab", "-c", "user.email=lab@selfheal.invalid"]
            sh([*g, "commit", "-q", "-am", f"{profile}: web image {tag}"], check=True)
            sh([*g, "push", "-q", "origin", "main"], check=True)
            t = now()
        refresh("selfheal-workload")
        return t
    finally:
        shutil.rmtree(work, ignore_errors=True)


def refresh(app: str) -> None:
    k("-n", "argocd", "annotate", "application", app, "argocd.argoproj.io/refresh=hard", "--overwrite")


def app_state(app: str) -> dict:
    a = kj("-n", "argocd", "get", "application", app)
    st = a.get("status", {})
    return {"path": a.get("spec", {}).get("source", {}).get("path"), "sync": st.get("sync", {}).get("status"),
            "health": st.get("health", {}).get("status"), "revision": st.get("sync", {}).get("revision")}


def argo_sync(app: str) -> None:
    k("-n", "argocd", "patch", "application", app, "--type", "merge",
      "-p", json.dumps({"operation": {"initiatedBy": {"username": "selfheal-runner"}, "sync": {"prune": True}}}))


# --------------------------------------------------------------------------- #
# profile switch and steady state
# --------------------------------------------------------------------------- #


def apply_profile(lab: Lab, profile: str) -> None:
    log(f"profile -> {profile}")
    sh(["helm", "upgrade", "falco-talon", "falcosecurity/falco-talon", "--version", TALON_VERSION, "-n", "falco",
        "--reuse-values", "--set-file", f"config.rulesOverride={ROOT / 'platform' / 'talon' / (profile + '.yaml')}",
        "--wait", "--timeout", "3m"], timeout=240, check=True)
    k("-n", "falco", "rollout", "status", "deploy/falco-talon", "--timeout=120s", timeout=150, check=True)
    k("-n", "argocd", "patch", "application", "selfheal-root", "--type", "merge",
      "-p", json.dumps({"spec": {"source": {"path": f"apps/{profile}"}}}), check=True)
    refresh("selfheal-root")
    wait_for(lambda: app_state("selfheal-workload")["path"] == f"profiles/{profile}", 120, what="workload app path")
    refresh("selfheal-workload")
    want_hpa = profile == "hardened"
    wait_for(lambda: (k("-n", NS, "get", "hpa", "web").returncode == 0) == want_hpa, 180, what="profile objects")
    wait_for(lambda: app_state("selfheal-workload")["sync"] == "Synced", 300, what="workload Synced")
    rule_present = lambda: ("WebContainerOOMKilled" in lab.rules()) == want_hpa  # noqa: E731
    wait_for(rule_present, 180, step=5, what="Prometheus rules for the profile")
    steady(lab)


def steady(lab: Lab) -> None:
    """Return the lab to its steady state before a row: v1 serving on the
    backend's key, three Ready pods, no faults, no leftovers, and a clean prober."""
    for kind in ("podchaos", "job"):
        k("-n", NS, "delete", kind, "-l", "selfheal/row", "--ignore-not-found", "--wait=false")
    k("-n", NS, "delete", "pod", "-l", "quarantine=true", "--ignore-not-found", "--wait=false")
    if rollout_image_tag() != "v1":
        log(f"  steady: Git left the Rollout on {rollout_image_tag()!r}, restoring v1")
        git_set_tag(app_state("selfheal-workload")["path"].split("/")[-1], "v1")
    key = backend_key()
    if synced_key() != key:
        k("-n", NS, "annotate", "externalsecret", "partner-api-key", f"force-sync={int(now())}", "--overwrite")
        wait_for(lambda: synced_key() == key, 90, what="ESO to sync the backend key")
    partner_set(key)
    # Any web pod that is not Ready, or was started with a different key, goes.
    for p in pods("app=web"):
        if not p["ready"] or not p["running"]:
            k("-n", NS, "delete", "pod", p["name"], "--wait=false")
    wait_for(lambda: rollout_healthy("v1"), 300, step=3, what="web Rollout Healthy on v1")
    def settled() -> bool:
        want = rollout().get("spec", {}).get("replicas", 3)
        hpa = kj("-n", NS, "get", "hpa", "web").get("status", {})
        hpa_ok = not hpa or hpa.get("desiredReplicas") == hpa.get("currentReplicas") == want
        return hpa_ok and want <= 4 and len([p for p in pods() if p["ready"]]) == want

    wait_for(settled, 300, step=5, what="web settled at its minimum replica count")
    lab.reconnect()

    # Pods still on a stale key (after an unhealed S1) are restarted, but only
    # on proof: the key in the pod differs from the backend's. A 502 alone is
    # not proof; a slow partner answers 502 too.
    stale = [p["name"] for p in pods() if p["ready"] and pod_key(p["name"]) not in ("", key)]
    if stale:
        log(f"  steady: {len(stale)} pod(s) on a stale key, restarting web")
        restart_web()
        wait_for(lambda: rollout_healthy("v1") and not [p for p in pods() if p["ready"] and pod_key(p["name"]) not in ("", key)],
                 300, step=5, what="web on the current key")

    def clean() -> bool:
        st = lab.stats("http", now() - 10, now())
        return st["n"] >= 0.8 * 10 * HTTP_RPS and st["errors"] == 0

    wait_for(clean, 240, step=10, what="10s of clean prober traffic")


def watch(lab: Lab, until: float, poll, done=None, kind: str = "http", step: float = 2.0,
          since: float | None = None) -> float | None:
    """Poll until `until` or `done()`. Returns the time the abort line was hit,
    or None. The caller stops the injection either way. The abort line only
    looks at samples from `since` on, so it never judges data from before the
    window the row is judged on."""
    while now() < until:
        poll(now())
        if done and done():
            return None
        st = lab.stats(kind, max(now() - 10, since or 0), now())
        if st["n"] >= 10 and st["error_rate"] is not None and st["error_rate"] > ABORT_ERROR_RATE:
            log(f"  abort line: {st['error_rate']:.0%} {kind} errors in the last 10s")
            ABORT.update({"t": now(), "kind": kind, "n": st["n"], "errors": st["errors"],
                          "error_rate": st["error_rate"], "error_kinds": st.get("error_kinds")})
            return now()
        time.sleep(step)
    return None


def window(lab: Lab, kind: str, since: float, until: float, row_dir: Path, tag: str) -> dict:
    st = lab.stats(kind, since, until, samples=True)
    with gzip.open(row_dir / f"prober-{tag}-{kind}.json.gz", "wt") as f:
        json.dump(st.pop("samples", []), f)
    st["expected"] = (until - since) * (HTTP_RPS if kind == "http" else TLS_RPS)
    return st


# --------------------------------------------------------------------------- #
# rows. Each returns its observations; judge.py decides.
# --------------------------------------------------------------------------- #


def row_a1(lab: Lab, profile: str, d: Path) -> dict:
    serving = [p for p in pods() if p["ready"]]
    hung, other = serving[0], serving[1]
    t0 = now()
    injected = exec_curl(hung["name"], "-X", "POST", "http://127.0.0.1:8080/fault/hang").returncode == 0
    ev = {"detected": None, "recovered": None, "killed": None}

    def poll(t: float) -> None:
        p = pod(hung["name"])
        if p and ev["detected"] is None and not p["ready"]:
            ev["detected"] = t
        if p and ev["recovered"] is None and p["restarts"] > hung["restarts"] and p["ready"]:
            ev["recovered"] = t
        if ev["killed"] is None and t - t0 >= 30:
            # Blast radius grows: Chaos Mesh kills a second pod (graceful, like
            # a node drain would).
            apply({"apiVersion": "chaos-mesh.org/v1alpha1", "kind": "PodChaos",
                   "metadata": {"name": "a1-pod-kill", "namespace": NS, "labels": {"selfheal/row": "A1"}},
                   "spec": {"action": "pod-kill", "mode": "one", "gracePeriod": 30,
                            "selector": {"pods": {NS: [other["name"]]}}}})
            ev["killed"] = t

    aborted = watch(lab, t0 + 90, poll)
    end = now()
    st = window(lab, "http", t0 + 20, end, d, profile)
    k("-n", NS, "delete", "podchaos", "a1-pod-kill", "--ignore-not-found")
    if not (pod(hung["name"]) or {}).get("restarts", 0) > hung["restarts"]:
        k("-n", NS, "delete", "pod", hung["name"], "--wait=false")
    rel = lambda t: None if t is None else round(t - t0, 1)  # noqa: E731
    return {"t0": t0, "hung_pod": hung["name"], "killed_pod": other["name"], "injected": injected,
            "detected_s": rel(ev["detected"]), "recovered_s": rel(ev["recovered"]), "chaos_at_s": rel(ev["killed"]),
            "aborted": aborted is not None, "error_rate_after": st["error_rate"] or 0.0, "samples": st["n"],
            "expected": st["expected"], "window": "T0+20s to end", "impact": st}


def row_a2(lab: Lab, profile: str, d: Path) -> dict:
    try:
        return _row_a2(lab, profile, d)
    finally:
        # Whatever happened, the next row starts from v1 in Git.
        git_set_tag(profile, "v1")
        wait_for(lambda: rollout_healthy("v1"), 300, step=3, what="rollback to v1")


def _row_a2(lab: Lab, profile: str, d: Path) -> dict:
    t0 = git_set_tag(profile, "v2")
    ev = {"aborted": None, "full": None, "v2_seen": False}

    def poll(t: float) -> None:
        r = rollout().get("status", {})
        if ev["aborted"] is None and r.get("abort"):
            ev["aborted"] = t
        st = lab.stats("http", t - 6, t)
        if "v2" in st.get("releases", []):
            ev["v2_seen"] = True
        if ev["full"] is None and st.get("releases") == ["v2"] and r.get("phase") == "Healthy":
            ev["full"] = t

    def done() -> bool:
        decided = ev["aborted"] or ev["full"]
        return decided is not None and now() - decided >= 40

    aborted_line = watch(lab, t0 + 180, poll, done)
    end = now()
    full = window(lab, "http", t0, end, d, profile)
    final = lab.stats("http", end - 30, end)
    runs = kj("-n", NS, "get", "analysisrun").get("items", [])
    mine = [r for r in runs if r["metadata"]["creationTimestamp"] >= rfc3339(t0 - 5)]
    last = sorted(mine, key=lambda r: r["metadata"]["creationTimestamp"])[-1] if mine else {}
    values = [x.get("value") for m in last.get("status", {}).get("metricResults", []) for x in m.get("measurements", [])]
    rel = lambda t: None if t is None else round(t - t0, 1)  # noqa: E731
    return {"t0": t0, "aborted_s": rel(ev["aborted"]), "fully_rolled_s": rel(ev["full"]), "v2_seen": ev["v2_seen"],
            "abort_line": aborted_line is not None, "error_rate_final": final["error_rate"] or 0.0,
            "releases_final": final.get("releases", []), "samples": final["n"], "expected": 30 * HTTP_RPS,
            "analysis": {"phase": last.get("status", {}).get("phase"), "message": last.get("status", {}).get("message"),
                         "values": values},
            "window": "last 30s of the observation", "impact": full}


K6_SCRIPT = """
import http from 'k6/http';
const RATE = parseInt(__ENV.RATE);
export const options = {
  discardResponseBodies: true,
  scenarios: {
    ramp: {executor: 'ramping-arrival-rate', startRate: 10, timeUnit: '1s', preAllocatedVUs: 20, maxVUs: 120,
           stages: [{target: RATE, duration: '40s'}]},
    sustain: {executor: 'constant-arrival-rate', rate: RATE, timeUnit: '1s', duration: '120s', startTime: '40s',
              preAllocatedVUs: 20, maxVUs: 120},
  },
  thresholds: {'http_req_duration{scenario:sustain}': ['p(95)<300'], 'http_req_failed{scenario:sustain}': ['rate<0.01']},
};
export default function () { http.get('http://web.selfheal.svc.cluster.local:8080/', {timeout: '2s'}); }
export function handleSummary(data) { return {stdout: 'K6SUMMARY ' + JSON.stringify(data.metrics) + '\\n'}; }
"""


def row_a3(lab: Lab, profile: str, d: Path) -> dict:
    rate = int(os.environ.get("A3_RATE", "40"))
    apply({"apiVersion": "v1", "kind": "ConfigMap",
           "metadata": {"name": "a3-k6", "namespace": NS, "labels": {"selfheal/row": "A3"}},
           "data": {"spike.js": K6_SCRIPT}})
    sc = {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}
    apply({"apiVersion": "batch/v1", "kind": "Job",
           "metadata": {"name": "a3-spike", "namespace": NS, "labels": {"selfheal/row": "A3"}},
           "spec": {"backoffLimit": 0, "ttlSecondsAfterFinished": 600, "template": {
               "metadata": {"labels": {"app": "loadgen", "selfheal/row": "A3"}},
               "spec": {"restartPolicy": "Never", "automountServiceAccountToken": False,
                        "securityContext": {"runAsNonRoot": True, "runAsUser": 12345, "seccompProfile": {"type": "RuntimeDefault"}},
                        "containers": [{"name": "k6", "image": K6_IMAGE, "imagePullPolicy": "IfNotPresent",
                                        "args": ["run", "-q", "--no-color", "/scripts/spike.js"],
                                        "env": [{"name": "RATE", "value": str(rate)}],
                                        "resources": {"requests": {"cpu": "200m", "memory": "64Mi"},
                                                      "limits": {"cpu": "1", "memory": "256Mi"}},
                                        "securityContext": sc,
                                        "volumeMounts": [{"name": "s", "mountPath": "/scripts"},
                                                         {"name": "tmp", "mountPath": "/tmp"}]}],
                        "volumes": [{"name": "s", "configMap": {"name": "a3-k6"}}, {"name": "tmp", "emptyDir": {}}]}}}})
    wait_for(lambda: any(i["status"].get("phase") == "Running"
                         for i in kj("-n", NS, "get", "pod", "-l", "job-name=a3-spike").get("items", [])),
             120, what="k6 to start")
    t0 = now()
    ev = {"max": 0, "scaled": None}
    base_replicas = rollout()["spec"].get("replicas", 3)

    def poll(t: float) -> None:
        n = len([p for p in pods() if p["ready"]])
        ev["max"] = max(ev["max"], n)
        if ev["scaled"] is None and n > base_replicas:
            ev["scaled"] = t

    # The ramp is allowed to hurt: the HPA needs a scrape and a sync to react.
    # The hypothesis, and the abort line, apply to the sustained spike.
    while now() < t0 + 100:
        poll(now())
        time.sleep(3)
    ramp = lab.stats("http", t0, t0 + 100)
    aborted = watch(lab, t0 + 162, poll, step=3, since=t0 + 100)
    end = now()
    sustain = window(lab, "http", t0 + 100, max(end, t0 + 101), d, profile)
    k("-n", NS, "wait", "--for=condition=complete", "job/a3-spike", "--timeout=60s", timeout=70)
    logs = k("-n", NS, "logs", "job/a3-spike", timeout=120).stdout
    summary = next((json.loads(ln.split(" ", 1)[1]) for ln in logs.splitlines() if ln.startswith("K6SUMMARY ")), {})
    k("-n", NS, "delete", "job", "a3-spike", "--ignore-not-found", "--wait=false")
    k("-n", NS, "delete", "configmap", "a3-k6", "--ignore-not-found")

    def k6v(metric: str, key: str):
        return ((summary.get(metric) or {}).get("values") or {}).get(key)

    return {"t0": t0, "rate_rps": rate, "load_ran": bool(summary) or aborted is not None,
            "p95_ms": sustain.get("p95_ms"), "error_rate": sustain["error_rate"] if sustain["error_rate"] is not None else 1.0,
            "min_replicas": base_replicas, "max_replicas": ev["max"],
            "scaled_s": None if ev["scaled"] is None else round(ev["scaled"] - t0, 1), "aborted": aborted is not None,
            "samples": sustain["n"], "expected": sustain["expected"], "window": "T0+100s to T0+160s (sustain)",
            "ramp": {"error_rate": ramp.get("error_rate"), "p95_ms": ramp.get("p95_ms"), "n": ramp.get("n")},
            "k6": {"sustain_p95_ms": k6v("http_req_duration{scenario:sustain}", "p(95)"),
                   "sustain_failed_rate": k6v("http_req_failed{scenario:sustain}", "rate"),
                   "dropped_iterations": k6v("dropped_iterations", "count")},
            "impact": sustain}


def cgroup_mb(pod_name: str) -> float | None:
    p = k("-n", NS, "exec", pod_name, "-c", "web", "--", "cat", "/sys/fs/cgroup/memory.current", timeout=10)
    try:
        return round(int(p.stdout.strip()) / 2**20, 1)
    except ValueError:
        return None


def row_a5(lab: Lab, profile: str, d: Path) -> dict:
    serving = [p for p in pods() if p["ready"]]
    target, others = serving[0], {p["name"]: p["restarts"] for p in serving[1:]}
    t0 = now()
    injected = exec_curl(target["name"], "-X", "POST", "http://127.0.0.1:8080/fault/leak").returncode == 0
    ev = {"oom": None, "recovered": None, "alert": None, "runbook": "", "mem": None}

    def poll(t: float) -> None:
        p = pod(target["name"])
        if p and ev["oom"] is None and p["restarts"] > target["restarts"] and p["last_reason"] == "OOMKilled":
            ev["oom"] = t
        if p and ev["oom"] and ev["recovered"] is None and p["ready"] and p["running"]:
            ev["recovered"] = t
        if ev["alert"] is None:
            for a in lab.alerts():
                if (a["labels"].get("alertname") == "WebContainerOOMKilled" and a["labels"].get("pod") == target["name"]
                        and a["state"] == "firing"):
                    ev["alert"], ev["runbook"] = t, a["annotations"].get("runbook_url", "")

    def done() -> bool:
        return ev["recovered"] is not None and ev["alert"] is not None

    aborted = watch(lab, t0 + 100, poll, done)
    if ev["oom"] is None:
        ev["mem"] = cgroup_mb(target["name"])
    end = now()
    st = window(lab, "http", t0, end, d, profile)
    other_restarts = sum((pod(n) or {"restarts": r})["restarts"] - r for n, r in others.items())
    if ev["oom"] is None:
        k("-n", NS, "delete", "pod", target["name"], "--wait=false")
    rel = lambda t: None if t is None else round(t - t0, 1)  # noqa: E731
    return {"t0": t0, "target_pod": target["name"], "injected": injected, "oom_s": rel(ev["oom"]),
            "recovered_s": rel(ev["recovered"]), "alert_s": rel(ev["alert"]), "alert_runbook": ev["runbook"],
            "memory_end_mb": ev["mem"], "other_restarts": other_restarts, "aborted": aborted is not None,
            "error_rate": st["error_rate"] or 0.0, "samples": st["n"], "expected": st["expected"],
            "window": "T0 to end", "impact": st}


def row_s1(lab: Lab, profile: str, d: Path) -> dict:
    grace = THRESHOLDS[TARGET]["S1"]["grace_s"]
    old, new = backend_key(), secrets.token_hex(24)
    partner_set(new, old)  # the provider issues the new key; the old one stays valid for the grace window
    t0 = now()
    k("-n", "secret-backend", "patch", "secret", "partner-api-key", "--type", "merge",
      "-p", json.dumps({"stringData": {"api-key": new}}), check=True)
    ev = {"synced": None, "rolled": None, "revoked": None}

    def poll(t: float) -> None:
        if ev["synced"] is None and synced_key() == new:
            ev["synced"] = t
        if ev["synced"] and ev["rolled"] is None:
            ps = [p for p in pods() if p["ready"]]
            if len(ps) >= 3 and all(p["created"] >= ev["synced"] - 2 for p in ps):
                ev["rolled"] = t
        if ev["revoked"] is None and t - t0 >= grace:
            partner_set(new, "")  # grace window over: the old key stops working
            ev["revoked"] = t

    aborted = watch(lab, t0 + grace + 30, poll)
    if ev["revoked"] is None:
        partner_set(new, "")
        ev["revoked"] = now()
    end = now()
    st = window(lab, "http", t0, end, d, profile)
    rel = lambda t: None if t is None else round(t - t0, 1)  # noqa: E731
    return {"t0": t0, "synced_s": rel(ev["synced"]), "rolled_s": rel(ev["rolled"]), "revoked_s": rel(ev["revoked"]),
            "revoked": True, "aborted": aborted is not None, "failed": st["errors"], "samples": st["n"],
            "expected": st["expected"], "window": "T0 (backend rotation) to revoke + 30s", "impact": st}


def static_cert(seconds: int) -> str:
    p = sh([str(ROOT / "scripts" / "static-cert.sh"), str(seconds)], timeout=60, check=True)
    serial = next(ln.split("=", 1)[1] for ln in p.stdout.splitlines() if ln.startswith("serial="))
    return serial.upper()


def recent_serial(lab: Lab) -> list[str]:
    return [s.upper() for s in lab.stats("tls", now() - 4, now()).get("recent_serials", [])]


def row_s2(lab: Lab, profile: str, d: Path) -> dict:
    if profile == "baseline":
        # The hand-made certificate, re-issued with 90s left, then nobody acts.
        serial = static_cert(90)
        written = now()
        wait_for(lambda: recent_serial(lab) == [serial], 150, step=3, what="pods serving the hand-made cert")
        t0 = written + 90 - 30  # its last 30 seconds: the renewal window
        time.sleep(max(0.0, t0 - now()))
        ev = {"renewed": None, "served": None}
        aborted = watch(lab, written + 90 + 45, lambda t: None, kind="tls")
        end = now()
        st = window(lab, "tls", t0, end, d, profile)
        static_cert(3600)
        wait_for(lambda: len(recent_serial(lab)) == 1 and not lab.stats("tls", now() - 4, now())["errors"],
                 150, step=3, what="baseline cert restored")
        return {"t0": t0, "renewed_s": None, "served_s": None, "aborted": aborted is not None,
                "tls_errors": st["errors"], "samples": st["n"], "expected": st["expected"],
                "error_kinds": st.get("error_kinds"), "window": "last 30s of validity to expiry + 45s", "impact": st}

    before = recent_serial(lab)
    cert = kj("-n", NS, "get", "certificate", "web-tls")
    rev0 = cert.get("status", {}).get("revision", 0)
    t0 = now()
    # The renewal window arrives: set Issuing exactly as cert-manager's trigger
    # controller does at renewalTime (and as `cmctl renew` does).
    k("-n", NS, "patch", "certificate", "web-tls", "--subresource=status", "--type=json", "-p", json.dumps([
        {"op": "add", "path": "/status/conditions/-", "value": {
            "type": "Issuing", "status": "True", "reason": "ManuallyTriggered",
            "message": "selfheal runner: renewal window reached", "lastTransitionTime": rfc3339(t0)}}]),
      check=True)
    ev = {"renewed": None, "served": None}

    def poll(t: float) -> None:
        c = kj("-n", NS, "get", "certificate", "web-tls").get("status", {})
        ready = any(x["type"] == "Ready" and x["status"] == "True" for x in c.get("conditions", []))
        if ev["renewed"] is None and c.get("revision", 0) > rev0 and ready:
            ev["renewed"] = t
        cur = recent_serial(lab)
        if ev["renewed"] and ev["served"] is None and cur and cur != before and len(cur) == 1:
            ev["served"] = t

    aborted = watch(lab, t0 + 170, poll, lambda: ev["served"] is not None and now() - ev["served"] > 15, kind="tls")
    end = now()
    st = window(lab, "tls", t0, end, d, profile)
    rel = lambda t: None if t is None else round(t - t0, 1)  # noqa: E731
    return {"t0": t0, "renewed_s": rel(ev["renewed"]), "served_s": rel(ev["served"]), "aborted": aborted is not None,
            "serial_before": before, "serial_after": recent_serial(lab), "tls_errors": st["errors"],
            "samples": st["n"], "expected": st["expected"], "error_kinds": st.get("error_kinds"),
            "window": "T0 (renewal window) to new cert served + 15s", "impact": st}


def row_s3(lab: Lab, profile: str, d: Path) -> dict:
    uid0 = kj("-n", NS, "get", "cnp", "selfheal-policy").get("metadata", {}).get("uid")
    t0 = now()
    deleted = k("-n", NS, "delete", "cnp", "selfheal-policy").returncode == 0
    # Someone raises a limit by hand: a field Argo CD owns, so both client- and
    # server-side diff see it.
    edited = k("-n", NS, "patch", "deploy", "partner", "--type=json", "-p", json.dumps([
        {"op": "replace", "path": "/spec/template/spec/containers/0/resources/limits/memory", "value": "1Gi"}])).returncode == 0
    ev = {"policy": None, "edit": None}

    def poll(t: float) -> None:
        if ev["policy"] is None:
            uid = kj("-n", NS, "get", "cnp", "selfheal-policy").get("metadata", {}).get("uid")
            if uid and uid != uid0:
                ev["policy"] = t
        if ev["edit"] is None:
            dep = kj("-n", NS, "get", "deploy", "partner")
            lim = dep.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [{}])[0].get("resources", {}).get("limits", {})
            if lim.get("memory") == "128Mi":
                ev["edit"] = t

    watch(lab, t0 + 120, poll, lambda: ev["policy"] is not None and ev["edit"] is not None, step=1)
    if ev["policy"] is None or ev["edit"] is None:
        argo_sync("selfheal-workload")
        wait_for(lambda: k("-n", NS, "get", "cnp", "selfheal-policy").returncode == 0, 120, what="manual sync")
    k("-n", NS, "rollout", "status", "deploy/partner", "--timeout=120s", timeout=130)
    partner_set(backend_key())
    rel = lambda t: None if t is None else round(t - t0, 1)  # noqa: E731
    return {"t0": t0, "injected": deleted and edited, "policy_restored_s": rel(ev["policy"]),
            "edit_reverted_s": rel(ev["edit"]), "window": "T0 to T0+120s"}


def reach(src_pod: str, src_container: str, url: str) -> bool:
    p = k("-n", NS, "exec", src_pod, "-c", src_container, "--", "curl", "-s", "-o", "/dev/null", "-m", "3",
          "-w", "%{http_code}", url, timeout=15)
    return p.stdout.strip().startswith("2")


def falco_event(pod_name: str, since: float) -> dict | None:
    logs = k("-n", "falco", "logs", "-l", "app.kubernetes.io/name=falco", "-c", "falco", "--since=10m",
             "--tail=-1", timeout=30).stdout
    for ln in logs.splitlines():
        if not ln.startswith("{"):
            continue
        try:
            e = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if e.get("rule") != "Shell spawned in selfheal web pod":
            continue
        if (e.get("output_fields") or {}).get("k8s.pod.name") != pod_name:
            continue
        t = dt.datetime.strptime(e["time"][:26], "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=dt.timezone.utc).timestamp()
        if t >= since - 5:
            e["_t"] = t
            return e
    return None


def hubble_policy_drops(pod_name: str, since: float) -> list[dict]:
    p = sh(["hubble", "observe", "--pod", f"{NS}/{pod_name}", "--verdict", "DROPPED", "--since",
            dt.datetime.fromtimestamp(since - 2, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "-o", "json"], timeout=60)
    out = []
    for ln in p.stdout.splitlines():
        try:
            f = json.loads(ln).get("flow")
        except json.JSONDecodeError:
            continue
        if f and "POLICY" in f.get("drop_reason_desc", ""):
            out.append(f)
    return out


def row_r1(lab: Lab, profile: str, d: Path) -> dict:
    serving = [p for p in pods() if p["ready"]]
    target = serving[0]
    prober = kj("-n", NS, "get", "pod", "-l", "app=prober")["items"][0]["metadata"]["name"]
    url_in = f"http://{target['ip']}:8080/healthz"
    url_out = "http://partner.selfheal.svc.cluster.local:8080/healthz"
    reachable_before = reach(prober, "prober", url_in) and reach(target["name"], "web", url_out)
    t0 = now()
    # The attack: a shell in the pod reads the token path and leaves a process
    # behind (the implant the responder must preserve, not kill).
    k("-n", NS, "exec", target["name"], "-c", "web", "--", "sh", "-c",
      "id; cat /var/run/secrets/kubernetes.io/serviceaccount/token 2>/dev/null || echo no-token; "
      "nohup sleep 3600 >/dev/null 2>&1 &", timeout=20)
    ev: dict = {"detected": None, "labelled": None, "isolated": None, "falco": None}

    def poll(t: float) -> None:
        if ev["detected"] is None:
            e = falco_event(target["name"], t0)
            if e:
                ev["detected"], ev["falco"] = e["_t"], e
        p = pod(target["name"])
        if p and ev["labelled"] is None and p["labels"].get("quarantine") == "true":
            ev["labelled"] = t
        if ev["isolated"] is None and t - t0 > 1:
            if not reach(prober, "prober", url_in) and not reach(target["name"], "web", url_out):
                ev["isolated"] = t

    watch(lab, t0 + 60, poll, lambda: ev["isolated"] is not None and now() - ev["isolated"] > 10)
    evidence_s, kept = None, None
    rdir = d / f"r1-{profile}"
    rdir.mkdir(exist_ok=True)
    drops = hubble_policy_drops(target["name"], t0)
    if ev["isolated"] is not None:
        # Preserve, then investigate: capture while the pod is still alive.
        p = pod(target["name"])
        kept = p is not None and p["running"] and p["restarts"] == target["restarts"]
        (rdir / "pod.json").write_text(json.dumps(kj("-n", NS, "get", "pod", target["name"]), indent=2))
        (rdir / "logs.txt").write_text(k("-n", NS, "logs", target["name"], "-c", "web", timeout=30).stdout)
        (rdir / "processes.txt").write_text(k("-n", NS, "exec", target["name"], "-c", "web", "--", "ps", "-o",
                                              "pid,ppid,user,args", timeout=15).stdout)
        (rdir / "net-tcp.txt").write_text(k("-n", NS, "exec", target["name"], "-c", "web", "--", "cat",
                                            "/proc/net/tcp", "/proc/net/tcp6", timeout=15).stdout)
        (rdir / "falco-event.json").write_text(json.dumps(ev["falco"], indent=2))
        (rdir / "events.txt").write_text(k("-n", NS, "get", "events", "--field-selector",
                                           f"involvedObject.name={target['name']}", timeout=20).stdout)
        with gzip.open(rdir / "hubble-drops.jsonl.gz", "wt") as f:
            for fl in drops:
                f.write(json.dumps(fl) + "\n")
        evidence_s = round(now() - t0, 1)
    end = now()
    st = window(lab, "http", t0, end, d, profile)
    # Eradicate: only now is the pod deleted.
    k("-n", NS, "delete", "pod", target["name"], "--wait=false")
    rel = lambda t: None if t is None else round(t - t0, 1)  # noqa: E731
    return {"t0": t0, "target_pod": target["name"], "reachable_before": reachable_before,
            "detected_s": rel(ev["detected"]), "labelled_s": rel(ev["labelled"]), "isolated_s": rel(ev["isolated"]),
            "hubble_policy_drops": len(drops), "evidence_s": evidence_s, "pod_kept_for_capture": kept,
            "error_rate": st["error_rate"] or 0.0, "samples": st["n"], "expected": st["expected"],
            "window": "T0 to end", "impact": st}


RUNNERS = {"A1": row_a1, "A2": row_a2, "A3": row_a3, "A5": row_a5, "S1": row_s1, "S2": row_s2, "S3": row_s3, "R1": row_r1}
DETECTED = {"A1": "detected_s", "A2": "aborted_s", "A3": "scaled_s", "A5": "oom_s", "S1": "synced_s",
            "S2": "renewed_s", "S3": "policy_restored_s", "R1": "detected_s"}
RECOVERED = {"A1": "recovered_s", "A2": "aborted_s", "A3": "scaled_s", "A5": "recovered_s", "S1": "rolled_s",
             "S2": "served_s", "S3": "edit_reverted_s", "R1": "isolated_s"}

# --------------------------------------------------------------------------- #
# preconditions, report, main
# --------------------------------------------------------------------------- #


def preconditions() -> dict:
    ctx = k("config", "current-context").stdout.strip()
    if ctx != CONTEXT:
        raise SystemExit(f"refusing to run: kube context is {ctx!r}, expected {CONTEXT!r} (KUBECONFIG={os.environ['KUBECONFIG']})")
    checks = {"context": ctx}
    for ns, kind, name in [("kube-system", "deploy", "metrics-server"), ("monitoring", "deploy", "kps-operator"),
                           ("cert-manager", "deploy", "cert-manager"), ("external-secrets", "deploy", "external-secrets"),
                           ("reloader", "deploy", "reloader-reloader"), ("argo-rollouts", "deploy", "argo-rollouts"),
                           ("argocd", "statefulset", "argocd-application-controller"), ("chaos-mesh", "deploy", "chaos-controller-manager"),
                           ("falco", "daemonset", "falco"), ("falco", "deploy", "falco-falcosidekick"),
                           ("falco", "deploy", "falco-talon"), ("gitops", "deploy", "gitserver")]:
        p = k("-n", ns, "rollout", "status", f"{kind}/{name}", "--timeout=120s", timeout=130)
        checks[f"{ns}/{name}"] = p.returncode == 0
        if p.returncode != 0:
            raise SystemExit(f"precondition failed: {ns}/{kind}/{name} not ready: {p.stderr.strip()}")
    # Right after `make up`, Argo CD may not have synced the lab yet.
    wait_for(lambda: app_state("selfheal-workload").get("health") == "Healthy"
             and app_state("selfheal-workload").get("sync") == "Synced", 600, step=5, what="workload app Synced and Healthy")
    p = k("-n", NS, "rollout", "status", "deploy/prober", "--timeout=300s", timeout=320)
    checks["workload_synced"] = p.returncode == 0
    if p.returncode != 0:
        raise SystemExit(f"precondition failed: prober not ready: {p.stderr.strip()}")
    gate = sh([str(ROOT / "cilium" / "readiness-gate.sh")], timeout=600)
    checks["cilium_gate"] = gate.returncode == 0
    if gate.returncode != 0:
        raise SystemExit(f"Cilium readiness gate failed: {gate.stderr.strip()}")
    return checks


def versions() -> dict:
    out = {}
    for ns, kind, name in [("kube-system", "daemonset", "cilium"), ("falco", "daemonset", "falco"),
                           ("falco", "deploy", "falco-talon"), ("argo-rollouts", "deploy", "argo-rollouts"),
                           ("external-secrets", "deploy", "external-secrets"), ("cert-manager", "deploy", "cert-manager"),
                           ("chaos-mesh", "deploy", "chaos-controller-manager"), ("reloader", "deploy", "reloader-reloader")]:
        d = kj("-n", ns, "get", kind, name)
        out[name] = (d.get("spec", {}).get("template", {}).get("spec", {}).get("containers") or [{}])[0].get("image")
    out["kubernetes"] = json.loads(k("version", "-o", "json").stdout or "{}").get("serverVersion", {}).get("gitVersion")
    return out


def fmt_s(v) -> str:
    return "" if v is None else f"{v:.0f}s"


def write_report(outdir: Path, doc: dict) -> None:
    (outdir / "report.json").write_text(json.dumps(doc, indent=2, default=str))
    L = [f"# Self-healing run `{doc['run_id']}` ({doc['target']})\n",
         f"- Started {doc['started']}, finished {doc['finished']}",
         f"- Rows: {', '.join(doc['rows'])}{' (partial run)' if doc['partial'] else ''}",
         f"- Overall: **{'PASS' if doc['pass'] else 'FAIL'}** (every hardened row must be healed; nothing is skipped)\n",
         "## Verdicts\n",
         "| Row | Baseline | Hardened | Hardened detail |", "|---|---|---|---|"]
    for row in doc["rows"]:
        r = doc["results"].get(row, {})
        b, h = r.get("baseline", {}), r.get("hardened", {})
        L.append(f"| {row} | {b.get('status', '-')} | **{h.get('status', '-')}** | {h.get('reason', '')} |")
    L.append("\n## Per row\n")
    for row in doc["rows"]:
        L.append(f"### {row}\n\n**Steady-state hypothesis:** {HYPOTHESES[row]}\n")
        L.append(f"Healing control: {ACTIONS[row]}. Thresholds ({doc['target']}): `{json.dumps(THRESHOLDS[doc['target']][row])}`\n")
        L.append("| Profile | Event at T0 | Detected | Recovered | Requests | Failed | Error rate | Burn rate | Alerts fired | Verdict |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for prof in ("baseline", "hardened"):
            r = doc["results"].get(row, {}).get(prof)
            o = doc["observations"].get(row, {}).get(prof, {})
            if not r:
                continue
            imp = o.get("impact") or {}
            er = imp.get("error_rate")
            L.append(f"| {prof} | {iso(o.get('t0'))[11:19]} | {fmt_s(o.get(DETECTED[row]))} | {fmt_s(o.get(RECOVERED[row]))} | "
                     f"{imp.get('n', '')} | {imp.get('errors', '')} | {'' if er is None else f'{er:.2%}'} | "
                     f"{'' if er is None else f'{er / 0.005:.1f}x'} | {', '.join(o.get('alerts_fired', [])) or '-'} | "
                     f"{r['status']}: {r['reason']} |")
        L.append("")
    L.append("## Environment\n")
    L.append("```json\n" + json.dumps({"preconditions": doc["preconditions"], "versions": doc["versions"]}, indent=2) + "\n```")
    L.append("\nBurn rate is the row window's error rate over the 99.5% SLO's budget (0.5%). Raw prober samples for "
             "every row window are in `<row>/prober-*.json.gz`; the R1 evidence bundle is in `<row>/r1-hardened/`.")
    (outdir / "report.md").write_text("\n".join(L) + "\n")


def alerts_fired(lab: Lab, since: float) -> list[str]:
    try:
        return sorted({a["labels"]["alertname"] for a in lab.alerts()
                       if a["state"] == "firing" and a.get("activeAt")
                       and dt.datetime.fromisoformat(a["activeAt"].replace("Z", "+00:00")).timestamp() >= since + lab.offset - 5})
    except Exception:  # noqa: BLE001
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=dt.datetime.now().strftime("kind-%Y%m%d-%H%M%S"))
    ap.add_argument("--rows", default=",".join(ROWS))
    ap.add_argument("--only-profile", choices=["baseline", "hardened"])
    args = ap.parse_args()
    rows = [r.strip().upper() for r in args.rows.split(",") if r.strip()]
    bad = [r for r in rows if r not in RUNNERS]
    if bad:
        raise SystemExit(f"unknown rows: {bad}")

    checks = preconditions()
    if args.only_profile:
        with Lab() as lab:
            apply_profile(lab, args.only_profile)
        return 0

    outdir = ROOT / "evidence" / args.run_id
    outdir.mkdir(parents=True, exist_ok=True)
    doc = {"run_id": args.run_id, "target": TARGET, "started": iso(now()), "rows": rows,
           "partial": rows != ROWS, "preconditions": checks, "versions": versions(),
           "results": {}, "observations": {}}
    with Lab() as lab, HubbleRelay():
        for profile in ("baseline", "hardened"):
            apply_profile(lab, profile)
            for row in rows:
                d = outdir / row
                d.mkdir(exist_ok=True)
                log(f"{profile} {row}: {HYPOTHESES[row]}")
                try:
                    steady(lab)
                    ABORT.clear()
                    obs = RUNNERS[row](lab, profile, d)
                    if ABORT:
                        obs["abort_evidence"] = {**ABORT, "at_s": round(ABORT["t"] - obs.get("t0", ABORT["t"]), 1)}
                    obs["alerts_fired"] = alerts_fired(lab, obs.get("t0", now()))
                except Exception as e:  # noqa: BLE001 - recorded as error, never skipped
                    obs = {"exception": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}
                    log(f"  exception: {obs['exception']}")
                base = doc["results"].get(row, {}).get("baseline", {}).get("status")
                v = verdict(row, profile, obs, base, TARGET)
                doc["observations"].setdefault(row, {})[profile] = obs
                doc["results"].setdefault(row, {})[profile] = v
                log(f"  {profile} {row}: {v['status']}: {v['reason']}")
                write_report(outdir, {**doc, "finished": "(running)", "pass": False})
    doc["finished"] = iso(now())
    doc["pass"] = overall(doc["results"])
    write_report(outdir, doc)
    log(f"report: {outdir / 'report.md'}  overall={'PASS' if doc['pass'] else 'FAIL'}")
    return 0 if doc["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
