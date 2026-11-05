"""R2 worker on AKS: the in-cluster half of the Defender response (ADR-0007 D6).

The responder Logic App (infra/azure/responder/workflow.json.tftpl) saves a
Defender for Containers alert to the evidence container and puts a small
incident on the "incidents" queue. This worker, running on a system node
with workload identity, takes the incident and does the Kubernetes steps the
Logic App cannot (it has no route to the API server and cannot trust the
cluster CA):

  1. resolve the alert to exactly one web pod in the lab namespace, on a
     workload node of this cluster; refuse (never guess) otherwise
  2. cordon the node FIRST and confirm it, so the pod's replacement can never
     be scheduled onto the node being quarantined (the EKS lesson), and keep
     the cluster autoscaler from removing it
  3. relabel the pod out of its Service and ReplicaSet and into the
     quarantine CiliumNetworkPolicy (the same labels as R1)
  4. write r2/<run_id>/isolated.json; the Logic App then snapshots the OS
     disk and runs the capture on that scale set instance

There is no per-node network quarantine on AKS (no NIC-level NSG is
supported): the node's isolation is the cordon plus the pod's policy, a gap
the README lists (ADR-0007 D4). Nothing here deletes or drains anything.

Only the standard library, so the image needs no build step beyond a copy.
"""
from __future__ import annotations

import base64
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

CLUSTER_NAME = os.environ.get("CLUSTER_NAME", "selfheal")
CLUSTER_ID = os.environ.get("CLUSTER_ID", "").lower()
NAMESPACE = os.environ.get("LAB_NAMESPACE", "selfheal")
APP_LABEL = os.environ.get("APP_LABEL", "web")
NODE_RESOURCE_GROUP = os.environ.get("NODE_RESOURCE_GROUP", "").lower()
WORKLOAD_NODE_LABEL = ("selfheal.lab/nodes", "workload")
STORAGE_ACCOUNT = os.environ.get("STORAGE_ACCOUNT", "")
QUEUE = os.environ.get("QUEUE", "incidents")
CONTAINER = os.environ.get("CONTAINER", "evidence")
MAX_INCIDENT_AGE = int(os.environ.get("MAX_INCIDENT_AGE_SECONDS", "900"))
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "3"))
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "2"))

STORAGE_API = "2023-11-03"
QUARANTINE_LABELS = {"quarantine": "true", "lab/pool": "quarantine"}

RUN_ID = re.compile(r"^[A-Za-z0-9]{1,80}$")
DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
VMSS_NAME = re.compile(r"^[A-Za-z0-9][-A-Za-z0-9_]{0,63}$")
INSTANCE = re.compile(r"^[0-9]{1,6}$")
PROVIDER_ID = re.compile(
    r"^azure:///subscriptions/[^/]+/resourceGroups/(?P<rg>[^/]+)/providers/Microsoft\.Compute/"
    r"virtualMachineScaleSets/(?P<vmss>[^/]+)/virtualMachines/(?P<instance>[^/]+)$",
    re.IGNORECASE,
)


class Refused(Exception):
    """The incident must not be acted on. Final: recorded, never retried."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def log(**kw) -> None:
    print(json.dumps({"at": now(), **kw}, sort_keys=True), flush=True)


# --------------------------------------------------------------------------- #
# pure parts (unit tested in test_worker.py)
# --------------------------------------------------------------------------- #

def parse_incident(text: str, now_ts: float | None = None) -> dict:
    """The queue message the Logic App wrote. Raises Refused for anything
    malformed or stale (an incident from an earlier run must never act)."""
    try:
        msg = json.loads(base64.b64decode(text, validate=True))
    except (ValueError, TypeError) as e:
        raise Refused(f"queue message is not base64 JSON: {e}") from e
    if not isinstance(msg, dict):
        raise Refused("queue message is not a JSON object")
    run_id = msg.get("run_id")
    if not isinstance(run_id, str) or not RUN_ID.match(run_id):
        raise Refused(f"bad run_id {run_id!r}")
    if msg.get("alert_blob") != f"r2/{run_id}/alert.json":
        raise Refused(f"alert_blob {msg.get('alert_blob')!r} is not r2/{run_id}/alert.json")
    received = msg.get("received_at")
    try:
        ts = datetime.fromisoformat(str(received).replace("Z", "+00:00")).timestamp()
    except ValueError as e:
        raise Refused(f"bad received_at {received!r}") from e
    age = (now_ts if now_ts is not None else time.time()) - ts
    if age > MAX_INCIDENT_AGE:
        raise Refused(f"incident is {int(age)}s old (limit {MAX_INCIDENT_AGE}s); stale incidents never act")
    return {"run_id": run_id, "alert_blob": msg["alert_blob"], "received_at": received}


def _lower_keys(o):
    if isinstance(o, dict):
        return {str(k).lower(): _lower_keys(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_lower_keys(v) for v in o]
    return o


def _entities(alert: dict) -> list[dict]:
    """Alert entities with $ref links resolved. The ARM form nests them under
    properties; the workflow automation form may not (live check L2), and
    key casing differs between the two, so keys are lowercased first."""
    a = _lower_keys(alert)
    props = a.get("properties") if isinstance(a.get("properties"), dict) else a
    ents = [e for e in props.get("entities") or [] if isinstance(e, dict)]
    by_id = {e.get("$id"): e for e in ents if e.get("$id") is not None}

    def deref(v, depth=0):
        if isinstance(v, dict) and set(v) == {"$ref"} and depth < 5:
            return deref(by_id.get(v["$ref"], {}), depth + 1)
        return v

    out = []
    for e in ents:
        out.append({k: deref(v) for k, v in e.items()})
    return out


def alert_summary(alert: dict) -> dict:
    a = _lower_keys(alert)
    props = a.get("properties") if isinstance(a.get("properties"), dict) else a
    return {
        "alert_id": a.get("id") or props.get("systemalertid") or props.get("alertid"),
        "alert_type": props.get("alerttype"),
        "display_name": props.get("alertdisplayname") or props.get("displayname"),
        "time_generated": props.get("timegeneratedutc") or props.get("timegenerated"),
        "start_time": props.get("starttimeutc") or props.get("starttime"),
    }


def alert_target(alert: dict) -> dict:
    """The one pod the alert names. Raises Refused unless the alert is a real
    (not sample) alert for this cluster naming exactly one pod in the lab
    namespace."""
    if CLUSTER_ID and CLUSTER_ID not in json.dumps(alert).lower():
        raise Refused(f"alert does not name the lab cluster {CLUSTER_NAME!r}")
    summary = alert_summary(alert)
    name = str(summary.get("display_name") or "")
    if "sample" in name.lower() or "sample" in str(summary.get("alert_type") or "").lower():
        raise Refused(f"sample alert {name!r}; R2 measures the real detector")

    ents = _entities(alert)
    for e in ents:
        if str(e.get("type", "")).lower() == "k8s-cluster" and e.get("name") not in (None, CLUSTER_NAME):
            raise Refused(f"alert names cluster {e.get('name')!r}, not {CLUSTER_NAME!r}")

    pods = set()
    for e in ents:
        if str(e.get("type", "")).lower() != "k8s-pod":
            continue
        ns = e.get("namespace")
        ns_name = ns.get("name") if isinstance(ns, dict) else ns
        pods.add((ns_name, e.get("name")))
    if not pods:
        raise Refused("alert names no pod")
    if len(pods) > 1:
        raise Refused(f"alert names {len(pods)} pods {sorted(map(str, pods))}; refusing to guess")
    ns_name, pod_name = pods.pop()
    if ns_name != NAMESPACE:
        raise Refused(f"alert is for namespace {ns_name!r}, not {NAMESPACE!r}")
    if not isinstance(pod_name, str) or not DNS_LABEL.match(pod_name):
        raise Refused(f"bad pod name {pod_name!r}")
    return {"pod": pod_name, **summary}


def check_pod(pod: dict) -> str:
    """The pod must still be a live web pod of the lab. Returns its node."""
    md = pod.get("metadata", {})
    if md.get("namespace") not in (None, NAMESPACE):
        raise Refused(f"pod is in {md.get('namespace')!r}")
    if md.get("labels", {}).get("app") != APP_LABEL:
        raise Refused(f"pod {md.get('name')} is not a {APP_LABEL} pod")
    if md.get("deletionTimestamp"):
        raise Refused(f"pod {md.get('name')} is terminating")
    node = pod.get("spec", {}).get("nodeName")
    if not node or not DNS_LABEL.match(node):
        raise Refused(f"pod {md.get('name')} has no usable node ({node!r})")
    return node


def node_target(node: dict) -> dict:
    """Scale set and instance of a workload node in this cluster's node
    resource group. A system node is never quarantined."""
    md = node.get("metadata", {})
    name = md.get("name")
    k, v = WORKLOAD_NODE_LABEL
    if md.get("labels", {}).get(k) != v:
        raise Refused(f"node {name} is not a workload node ({k}={md.get('labels', {}).get(k)!r})")
    m = PROVIDER_ID.match(node.get("spec", {}).get("providerID", ""))
    if not m:
        raise Refused(f"node {name} has no scale set providerID")
    if NODE_RESOURCE_GROUP and m["rg"].lower() != NODE_RESOURCE_GROUP:
        raise Refused(f"node {name} is in {m['rg']!r}, not the lab node group")
    if not VMSS_NAME.match(m["vmss"]) or not INSTANCE.match(m["instance"]):
        raise Refused(f"node {name} providerID has an unexpected scale set or instance")
    return {"vmss": m["vmss"], "instance_id": m["instance"]}


# --------------------------------------------------------------------------- #
# the response, against an API object (real or fake)
# --------------------------------------------------------------------------- #

def respond(incident: dict, api) -> dict:
    """Resolve, cordon, confirm, relabel. Returns the isolated.json body.
    Refused propagates before any patch is made."""
    alert = api.get_blob(incident["alert_blob"])
    target = alert_target(alert)
    pod = api.get_pod(target["pod"])
    node_name = check_pod(pod)
    node = api.get_node(node_name)
    where = node_target(node)
    alert_ref = str(target.get("alert_id") or incident["run_id"])[-250:]

    api.patch_node(node_name, {
        "spec": {"unschedulable": True},
        "metadata": {
            "labels": {"selfheal.lab/quarantined": "true"},
            "annotations": {
                "cluster-autoscaler.kubernetes.io/scale-down-disabled": "true",
                "selfheal.lab/alert": alert_ref,
            },
        },
    })
    if not api.get_node(node_name).get("spec", {}).get("unschedulable"):
        raise RuntimeError(f"node {node_name} did not report unschedulable after the cordon")
    cordoned_at = now()

    api.patch_pod(target["pod"], {
        "metadata": {
            "labels": QUARANTINE_LABELS,
            "annotations": {"selfheal.lab/quarantined-by": "r2-worker", "selfheal.lab/alert": alert_ref},
        },
    })
    isolated_at = now()
    return {
        "status": "isolated",
        "run_id": incident["run_id"],
        "received_at": incident["received_at"],
        "alert": {k: target.get(k) for k in ("alert_id", "alert_type", "display_name", "time_generated", "start_time")},
        "pod": target["pod"],
        "pod_uid": pod.get("metadata", {}).get("uid"),
        "pod_labels_before": pod.get("metadata", {}).get("labels", {}),
        "node": node_name,
        "vmss": where["vmss"],
        "instance_id": where["instance_id"],
        "cordoned_at": cordoned_at,
        "isolated_at": isolated_at,
    }


def handle_message(text: str, dequeue_count: int, api) -> tuple[dict | None, bool]:
    """One queue message. Returns (isolated.json body or None, delete?).
    Refusals and successes are final; a transient error is retried by the
    queue's visibility timeout until MAX_ATTEMPTS, then recorded as refused."""
    try:
        incident = parse_incident(text)
    except Refused as e:
        log(event="refused", reason=str(e))
        return None, True
    try:
        body = respond(incident, api)
        log(event="isolated", run_id=incident["run_id"], node=body["node"], pod=body["pod"])
        return body, True
    except Refused as e:
        log(event="refused", run_id=incident["run_id"], reason=str(e))
        return {"status": "refused", "run_id": incident["run_id"], "reason": str(e), "at": now()}, True
    except Exception as e:  # noqa: BLE001 - every failure is recorded, none swallowed
        log(event="error", run_id=incident["run_id"], attempt=dequeue_count, error=str(e)[:500])
        if dequeue_count >= MAX_ATTEMPTS:
            reason = f"gave up after {dequeue_count} attempts: {str(e)[:300]}"
            return {"status": "refused", "run_id": incident["run_id"], "reason": reason, "at": now()}, True
        return None, False


# --------------------------------------------------------------------------- #
# real clients: Entra workload identity, Storage REST, Kubernetes REST
# --------------------------------------------------------------------------- #

class Azure:
    """Workload identity token exchange (the federated token the AKS webhook
    projects, traded for an Entra token), then Storage REST calls."""

    def __init__(self) -> None:
        self._tok: tuple[str, float] | None = None
        self.blob = f"https://{STORAGE_ACCOUNT}.blob.core.windows.net/{CONTAINER}"
        self.queue = f"https://{STORAGE_ACCOUNT}.queue.core.windows.net/{QUEUE}"

    def token(self) -> str:
        if self._tok and self._tok[1] > time.time() + 300:
            return self._tok[0]
        authority = os.environ.get("AZURE_AUTHORITY_HOST", "https://login.microsoftonline.com/").rstrip("/")
        with open(os.environ["AZURE_FEDERATED_TOKEN_FILE"]) as f:
            assertion = f.read().strip()
        form = urllib.parse.urlencode({
            "client_id": os.environ["AZURE_CLIENT_ID"],
            "grant_type": "client_credentials",
            "scope": "https://storage.azure.com/.default",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": assertion,
        }).encode()
        req = urllib.request.Request(f"{authority}/{os.environ['AZURE_TENANT_ID']}/oauth2/v2.0/token", data=form)
        with urllib.request.urlopen(req, timeout=15) as r:
            out = json.loads(r.read())
        self._tok = (out["access_token"], time.time() + int(out.get("expires_in", 3600)))
        return self._tok[0]

    def _call(self, method: str, url: str, data: bytes | None = None, headers: dict | None = None) -> bytes:
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token()}")
        req.add_header("x-ms-version", STORAGE_API)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {url.split('?')[0]}: HTTP {e.code} {e.read()[:300]!r}") from e

    def receive(self) -> list[dict]:
        xml = self._call("GET", f"{self.queue}/messages?numofmessages=1&visibilitytimeout=120")
        out = []
        for m in ET.fromstring(xml or b"<QueueMessagesList/>").findall("QueueMessage"):
            out.append({k: (m.findtext(k) or "") for k in ("MessageId", "PopReceipt", "DequeueCount", "MessageText")})
        return out

    def delete(self, message_id: str, pop_receipt: str) -> None:
        q = urllib.parse.quote(pop_receipt, safe="")
        self._call("DELETE", f"{self.queue}/messages/{message_id}?popreceipt={q}")

    def get_blob(self, name: str) -> dict:
        return json.loads(self._call("GET", f"{self.blob}/{name}"))

    def put_blob(self, name: str, body: dict) -> None:
        self._call("PUT", f"{self.blob}/{name}", json.dumps(body, sort_keys=True).encode(),
                   {"x-ms-blob-type": "BlockBlob", "Content-Type": "application/json"})


class Kube:
    """The Kubernetes API with the pod's own service account token."""

    SA = "/var/run/secrets/kubernetes.io/serviceaccount"

    def __init__(self) -> None:
        host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        self.base = f"https://{host}:{port}"
        self.ctx = ssl.create_default_context(cafile=f"{self.SA}/ca.crt")

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        with open(f"{self.SA}/token") as f:  # re-read: the projected token rotates
            tok = f.read().strip()
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {tok}")
        req.add_header("Accept", "application/json")
        if method == "PATCH":
            req.add_header("Content-Type", "application/merge-patch+json")
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=15) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path}: HTTP {e.code} {e.read()[:300]!r}") from e


class Api:
    """What respond() needs, backed by the real clients."""

    def __init__(self, azure: Azure, kube: Kube) -> None:
        self.azure, self.kube = azure, kube

    def get_blob(self, name: str) -> dict:
        return self.azure.get_blob(name)

    def get_pod(self, name: str) -> dict:
        return self.kube._call("GET", f"/api/v1/namespaces/{NAMESPACE}/pods/{name}")

    def get_node(self, name: str) -> dict:
        return self.kube._call("GET", f"/api/v1/nodes/{name}")

    def patch_node(self, name: str, body: dict) -> dict:
        return self.kube._call("PATCH", f"/api/v1/nodes/{name}", body)

    def patch_pod(self, name: str, body: dict) -> dict:
        return self.kube._call("PATCH", f"/api/v1/namespaces/{NAMESPACE}/pods/{name}", body)


def main() -> int:
    missing = [k for k in ("STORAGE_ACCOUNT", "CLUSTER_ID", "NODE_RESOURCE_GROUP") if not os.environ.get(k)]
    if missing:
        log(event="config_error", missing=missing)
        return 2
    azure = Azure()
    api = Api(azure, Kube())
    log(event="started", cluster=CLUSTER_NAME, queue=QUEUE, namespace=NAMESPACE)
    while True:
        try:
            for m in azure.receive():
                body, delete = handle_message(m["MessageText"], int(m["DequeueCount"] or "1"), api)
                if body is not None:
                    azure.put_blob(f"r2/{body['run_id']}/isolated.json", body)
                if delete:
                    azure.delete(m["MessageId"], m["PopReceipt"])
        except Exception as e:  # noqa: BLE001 - keep polling; the error is logged
            log(event="poll_error", error=str(e)[:500])
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
