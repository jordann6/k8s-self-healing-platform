"""Cilium identity-delay probe (ADR-0026 addendum, Cilium investigation). Run on a kind cluster with Cilium and the
X1 baseline app (KUBECONFIG set, `make x1-images` loaded), as `scripts/cilium_identity_delay.py <label> <trials>`.
A manual label patch with no controller, so the controller's 1 s is out of the picture. Records when the pod
becomes unreachable and when Cilium's endpoint identity changes. The unreachable clock includes up to 3 s of the
probe's own timeout, the identity clock does not."""
import json, pathlib, subprocess, sys, threading, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scenarios"))
import x1_row

NS = "selfheal-x1"
def sh(*a, timeout=30, inp=None):
    return subprocess.run(a, capture_output=True, text=True, timeout=timeout, input=inp)
def kj(*a):
    r = sh("kubectl", *a, "-o", "json")
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {}

def ensure_client():
    if not kj("-n", NS, "get", "pod", "x1-client").get("metadata"):
        sh("kubectl", "create", "-f", "-", inp=json.dumps(x1_row.client_manifest()))
    sh("kubectl", "-n", NS, "wait", "--for=condition=Ready", "pod/x1-client", "--timeout=90s", timeout=100)

def target():
    pods = [p for p in kj("-n", NS, "get", "pods", "-l", "app=x1-target").get("items", [])
            if not p["metadata"].get("deletionTimestamp") and p["metadata"]["labels"].get("lab/pool") == "serving"]
    return pods[0] if pods else None

AGENT = None
def agent():
    global AGENT
    if not AGENT:
        AGENT = kj("-n", "kube-system", "get", "pods", "-l", "k8s-app=cilium")["items"][0]["metadata"]["name"]
    return AGENT

def endpoint(ip):
    r = sh("kubectl", "-n", "kube-system", "exec", agent(), "-c", "cilium-agent", "--", "cilium-dbg", "endpoint", "list", "-o", "json")
    try:
        eps = json.loads(r.stdout)
    except ValueError:
        return None
    for e in eps:
        if any(a.get("ipv4") == ip for a in e.get("status", {}).get("networking", {}).get("addressing", [])):
            return e
    return None

def reachable(ip):
    r = sh("kubectl", "-n", NS, "exec", "x1-client", "--", "python3", "-c", x1_row.PROBE, ip, timeout=20)
    return r.stdout.strip() == "200"

def wait_ready(ip, to=60):
    d = time.time() + to
    while time.time() < d:
        if reachable(ip): return True
        time.sleep(1)
    return False

def trial(label):
    pod = target()
    name, ip = pod["metadata"]["name"], pod["status"]["podIP"]
    assert wait_ready(ip), "not reachable before"
    ep0 = endpoint(ip)
    id0 = (ep0 or {}).get("status", {}).get("identity", {}).get("id")
    ev = {"blocked": None, "identity": None, "policy": None}
    stop = threading.Event()
    t0 = None
    def probe():
        while not stop.is_set():
            if t0 and ev["blocked"] is None and not reachable(ip):
                ev["blocked"] = time.time() - t0
            time.sleep(0.2)
    def watch():
        while not stop.is_set():
            e = endpoint(ip)
            if t0 and e:
                s = e.get("status", {})
                if ev["identity"] is None and s.get("identity", {}).get("id") != id0:
                    ev["identity"] = time.time() - t0
                pol = s.get("policy", {}).get("spec", {})
            time.sleep(0.2)
    th = [threading.Thread(target=probe), threading.Thread(target=watch)]
    [t.start() for t in th]
    time.sleep(1)
    t0 = time.time()
    sh("kubectl", "-n", NS, "label", "pod", name, "--overwrite", "selfheal.lab/quarantined=true", "lab/pool=quarantine")
    patched = time.time() - t0
    d = time.time() + 45
    while time.time() < d and ev["blocked"] is None:
        time.sleep(0.5)
    time.sleep(2)
    stop.set(); [t.join() for t in th]
    epn = endpoint(ip)
    log = [(l.get("timestamp"), l.get("state"), l.get("status")) for l in (epn or {}).get("status", {}).get("log", [])][:12] if epn else []
    # restore serving
    sh("kubectl", "-n", NS, "label", "pod", name, "--overwrite", "selfheal.lab/quarantined-", "lab/pool=serving")
    wait_ready(ip, 60)
    out = {"cond": label, "patch_s": round(patched, 2), "blocked_s": None if ev["blocked"] is None else round(ev["blocked"], 2),
           "identity_change_s": None if ev["identity"] is None else round(ev["identity"], 2),
           "identity_before": id0, "identity_after": (epn or {}).get("status", {}).get("identity", {}).get("id"),
           "endpoint_log_head": log}
    print(json.dumps(out), flush=True)
    time.sleep(5)
    return out

if __name__ == "__main__":
    label, n = sys.argv[1], int(sys.argv[2])
    ensure_client()
    for _ in range(n):
        trial(label)
