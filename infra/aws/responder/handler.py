"""R2 responder: the Kubernetes and bookkeeping steps of the state machine.

The state machine (responder.tf) runs the AWS steps itself through SDK
integrations: termination protection, the quarantine security group,
snapshots, Run Command. This function does what those cannot:

  resolve      turn a GuardDuty Runtime Monitoring finding into one pod, one
               node, one instance. Refuses (never guesses) when the finding is
               not for this cluster and namespace, or matches no pod or several.
  isolate_pod  relabel the pod out of its Service and ReplicaSet and into the
               quarantine CiliumNetworkPolicy (the same labels as row R1).
  cordon       stop scheduling on the node and keep Karpenter's hands off it
               (do-not-disrupt, do-not-repair). No drain: draining would kill
               the compromised process and the evidence with it.
  revoke       deny every session issued before now for any Pod Identity role
               the pod's service account carries (aws:TokenIssueTime). Node
               role sessions are not revoked: every node shares that role
               (ADR-0006).
  collect      write the evidence manifest: finding, isolation, Run Command
               output, snapshot ids, the audit log window, step timestamps.
  custody      record the snapshot copies made under the evidence key.

Isolate, preserve, investigate, eradicate: nothing here deletes anything.
Only the standard library and boto3 (bundled with the Lambda runtime), so the
package needs no build step.
"""
from __future__ import annotations

import base64
import json
import os
import re
import ssl
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import boto3

CLUSTER = os.environ["CLUSTER_NAME"]
ENDPOINT = os.environ["CLUSTER_ENDPOINT"]
CA_DATA = os.environ["CLUSTER_CA"]
NAMESPACE = os.environ.get("LAB_NAMESPACE", "selfheal")
APP_LABEL = os.environ.get("APP_LABEL", "web")
EVIDENCE_BUCKET = os.environ.get("EVIDENCE_BUCKET", "")
EVIDENCE_KMS_ARN = os.environ.get("EVIDENCE_KMS_ARN", "")
AUDIT_LOG_GROUP = os.environ.get("AUDIT_LOG_GROUP", f"/aws/eks/{CLUSTER}/cluster")
REVOCABLE_ROLE_PATH = os.environ.get("REVOCABLE_ROLE_PATH", "/selfheal-workload/")

QUARANTINE_LABELS = {"quarantine": "true", "lab/pool": "quarantine"}
CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")
INSTANCE_ID = re.compile(r"^i-[0-9a-f]{8,17}$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# Kubernetes API with an EKS token, no client library
# --------------------------------------------------------------------------- #

_ctx: ssl.SSLContext | None = None
_token: tuple[str, float] | None = None


def _ssl() -> ssl.SSLContext:
    global _ctx
    if _ctx is None:
        with tempfile.NamedTemporaryFile("wb", suffix=".crt", delete=False) as f:
            f.write(base64.b64decode(CA_DATA))
        _ctx = ssl.create_default_context(cafile=f.name)
    return _ctx


def _eks_token() -> str:
    """The token aws-iam-authenticator accepts: a presigned STS
    GetCallerIdentity URL that names the cluster in a signed header."""
    global _token
    if _token and _token[1] > time.time() + 60:
        return _token[0]
    sts = boto3.client("sts", region_name=os.environ.get("AWS_REGION", "us-east-1"))

    def lift(params, context, **_):
        if "x-k8s-aws-id" in params:
            context["x-k8s-aws-id"] = params.pop("x-k8s-aws-id")

    def inject(request, **_):
        if "x-k8s-aws-id" in request.context:
            request.headers["x-k8s-aws-id"] = request.context["x-k8s-aws-id"]

    sts.meta.events.register("provide-client-params.sts.GetCallerIdentity", lift)
    sts.meta.events.register("before-sign.sts.GetCallerIdentity", inject)
    url = sts.generate_presigned_url("get_caller_identity", Params={"x-k8s-aws-id": CLUSTER},
                                     ExpiresIn=60, HttpMethod="GET")
    tok = "k8s-aws-v1." + base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    _token = (tok, time.time() + 840)
    return tok


def k8s(method: str, path: str, body: dict | None = None, patch: str = "merge") -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(ENDPOINT.rstrip("/") + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {_eks_token()}")
    req.add_header("Accept", "application/json")
    if method == "PATCH":
        req.add_header("Content-Type", f"application/{patch}-patch+json")
    try:
        with urllib.request.urlopen(req, context=_ssl(), timeout=15) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path}: HTTP {e.code} {e.read()[:300]!r}") from e


# --------------------------------------------------------------------------- #
# resolve: finding -> pod, node, instance (pure parts are unit tested)
# --------------------------------------------------------------------------- #

def finding_target(detail: dict) -> dict:
    """What the finding says was hit. Raises ValueError for findings the
    responder must not act on, so the state machine records why."""
    res = detail.get("resource") or {}
    if res.get("resourceType") != "EKSCluster":
        raise ValueError(f"not an EKS finding (resourceType {res.get('resourceType')!r})")
    cluster = (res.get("eksClusterDetails") or {}).get("name")
    if cluster != CLUSTER:
        raise ValueError(f"finding is for cluster {cluster!r}, not {CLUSTER!r}")
    wl = (res.get("kubernetesDetails") or {}).get("kubernetesWorkloadDetails") or {}
    if wl.get("namespace") != NAMESPACE:
        raise ValueError(f"finding is for namespace {wl.get('namespace')!r}, not {NAMESPACE!r}")
    svc = detail.get("service") or {}
    return {
        "finding_id": detail.get("id"),
        "finding_type": detail.get("type"),
        "severity": detail.get("severity"),
        "first_seen": svc.get("eventFirstSeen"),
        "workload": {k: wl.get(k) for k in ("name", "type", "uid", "namespace")},
        "containers": [c.get("name") for c in wl.get("containers") or [] if c.get("name")],
        "instance_id_hint": (res.get("instanceDetails") or {}).get("instanceId"),
    }


def match_pods(target: dict, pods: list[dict]) -> list[dict]:
    """Pods the finding can only mean. A workload uid may be the pod's own
    uid or an owner's (ReplicaSet); a name may be the pod's. Only lab app pods
    are candidates. Containers named in the finding narrow further."""
    wl = target["workload"]
    out = []
    for p in pods:
        md = p.get("metadata", {})
        if md.get("labels", {}).get("app") != APP_LABEL or md.get("deletionTimestamp"):
            continue
        owners = {o.get("uid") for o in md.get("ownerReferences") or []}
        if wl.get("uid") and (md.get("uid") == wl["uid"] or wl["uid"] in owners):
            out.append(p)
        elif not wl.get("uid") and wl.get("name") and md.get("name") == wl["name"]:
            out.append(p)
    if target["containers"] and len(out) > 1:
        want = set(target["containers"])
        out = [p for p in out if want & {c["name"] for c in p["spec"]["containers"]}]
    hint = target.get("instance_id_hint")
    if len(out) > 1 and hint:
        out = [p for p in out if p.get("_instance_id") == hint]
    return out


def instance_of(node: dict) -> str:
    pid = node.get("spec", {}).get("providerID", "")  # aws:///us-east-1a/i-0abc...
    iid = pid.rsplit("/", 1)[-1]
    if not INSTANCE_ID.match(iid):
        raise ValueError(f"node {node['metadata']['name']} has no EC2 instance id ({pid!r})")
    return iid


def capture_commands(container_ids: list[str]) -> list[str]:
    """Volatile state, read-only. Container ids are validated hex, the only
    values that reach the shell."""
    ids = [c for c in container_ids if CONTAINER_ID.match(c)]
    cmds = [
        "set +e",
        "echo '== host'; date -u; uptime; uname -a",
        "echo '== processes'; ps auxww --forest",
        "echo '== sockets'; ss -tanup",
        "echo '== interfaces'; ip -brief addr; ip route",
        "echo '== containers'; crictl ps -a",
    ]
    for cid in ids:
        cmds += [
            f"echo '== container {cid}'",
            f"crictl inspect --output go-template --template '{{{{.info.pid}}}}' {cid} > /tmp/selfheal.pid 2>/dev/null",
            "P=$(cat /tmp/selfheal.pid); echo pid=$P",
            "[ -n \"$P\" ] && ls -l /proc/$P/exe /proc/$P/cwd; [ -n \"$P\" ] && tr '\\0' ' ' < /proc/$P/cmdline; echo",
            "[ -n \"$P\" ] && ls -l /proc/$P/fd | head -50",
            "[ -n \"$P\" ] && nsenter -t $P -n ss -tanup",
        ]
    cmds.append("rm -f /tmp/selfheal.pid")
    return cmds


def resolve(event: dict) -> dict:
    detail = event.get("detail") or {}
    if event.get("source") != "aws.guardduty":
        # Synthetic events are not acted on: R2 measures the real detector.
        return {"act": False, "reason": f"source {event.get('source')!r} is not aws.guardduty", "at": now()}
    try:
        target = finding_target(detail)
    except ValueError as e:
        return {"act": False, "reason": str(e), "at": now()}
    pods = k8s("GET", f"/api/v1/namespaces/{NAMESPACE}/pods").get("items", [])
    matched = match_pods(target, pods)
    if len(matched) != 1:
        names = [p["metadata"]["name"] for p in matched]
        raise RuntimeError(f"finding {target['finding_id']} matched {len(matched)} pods {names}; refusing to guess")
    pod = matched[0]
    node_name = pod["spec"].get("nodeName")
    node = k8s("GET", f"/api/v1/nodes/{node_name}")
    cids = [s.get("containerID", "").split("://", 1)[-1] for s in pod.get("status", {}).get("containerStatuses", [])]
    return {
        "act": True,
        "at": now(),
        "finding": target,
        "pod": pod["metadata"]["name"],
        "pod_uid": pod["metadata"]["uid"],
        "service_account": pod["spec"].get("serviceAccountName", "default"),
        "pod_labels_before": pod["metadata"].get("labels", {}),
        "node": node_name,
        "instance_id": instance_of(node),
        "container_ids": [c for c in cids if CONTAINER_ID.match(c)],
        "capture_commands": capture_commands(cids),
    }


# --------------------------------------------------------------------------- #
# actions
# --------------------------------------------------------------------------- #

def isolate_pod(ctx: dict) -> dict:
    k8s("PATCH", f"/api/v1/namespaces/{NAMESPACE}/pods/{ctx['pod']}",
        {"metadata": {"labels": QUARANTINE_LABELS,
                      "annotations": {"selfheal.lab/quarantined-by": "r2-responder",
                                      "selfheal.lab/finding": str(ctx["finding"]["finding_id"])}}})
    return {"at": now(), "labels": QUARANTINE_LABELS}


def cordon(ctx: dict) -> dict:
    k8s("PATCH", f"/api/v1/nodes/{ctx['node']}",
        {"spec": {"unschedulable": True},
         "metadata": {"labels": {"selfheal.lab/quarantined": "true"},
                      "annotations": {"karpenter.sh/do-not-disrupt": "true",
                                      "karpenter.sh/do-not-repair": "true",
                                      "selfheal.lab/finding": str(ctx["finding"]["finding_id"])}}})
    return {"at": now()}


def revoke(ctx: dict) -> dict:
    eks = boto3.client("eks")
    iam = boto3.client("iam")
    assocs = eks.list_pod_identity_associations(
        clusterName=CLUSTER, namespace=NAMESPACE, serviceAccount=ctx["service_account"]).get("associations", [])
    if not assocs:
        return {"at": now(), "revoked": [], "reason": (
            f"service account {ctx['service_account']} has no Pod Identity association, and IMDS is "
            f"unreachable from pods (hop limit 1): no AWS credentials were exposed to revoke")}
    cutoff = now().split(".")[0] + "Z"
    policy = {"Version": "2012-10-17", "Statement": [{
        "Sid": "RevokeSessionsBeforeIncident", "Effect": "Deny", "Action": "*", "Resource": "*",
        "Condition": {"DateLessThan": {"aws:TokenIssueTime": cutoff}}}]}
    revoked, skipped = [], []
    for a in assocs:
        d = eks.describe_pod_identity_association(clusterName=CLUSTER, associationId=a["associationId"])
        role_name = d["association"]["roleArn"].rsplit("/", 1)[-1]
        role = iam.get_role(RoleName=role_name)["Role"]
        if role["Path"] != REVOCABLE_ROLE_PATH:
            skipped.append({"role": role_name, "reason": f"path {role['Path']} outside {REVOCABLE_ROLE_PATH}"})
            continue
        iam.put_role_policy(RoleName=role_name, PolicyName="SelfHealRevokeOldSessions",
                            PolicyDocument=json.dumps(policy))
        revoked.append(role_name)
    return {"at": now(), "revoked": revoked, "skipped": skipped, "cutoff": cutoff}


def _audit_window(ctx: dict, minutes: int = 30) -> list[dict]:
    logs = boto3.client("logs")
    end = int(time.time() * 1000)
    events, token = [], None
    while len(events) < 500:
        kw = {"logGroupName": AUDIT_LOG_GROUP, "logStreamNamePrefix": "kube-apiserver-audit",
              "startTime": end - minutes * 60_000, "endTime": end,
              "filterPattern": f'"{ctx["pod"]}"'}
        if token:
            kw["nextToken"] = token
        r = logs.filter_log_events(**kw)
        events += [{"t": e["timestamp"], "message": e["message"]} for e in r.get("events", [])]
        token = r.get("nextToken")
        if not token:
            break
    return events[:500]


def _put(key: str, body: bytes, ctype: str) -> None:
    boto3.client("s3").put_object(Bucket=EVIDENCE_BUCKET, Key=key, Body=body, ContentType=ctype,
                                  ServerSideEncryption="aws:kms", SSEKMSKeyId=EVIDENCE_KMS_ARN)


def collect(state: dict) -> dict:
    ctx, steps = state["ctx"], state.get("steps", {})
    prefix = f"evidence/{ctx['node']}/{ctx['pod']}/{ctx['finding']['finding_id']}"
    cap = steps.get("capture") or {}
    output = ""
    if cap.get("command_id"):
        inv = boto3.client("ssm").get_command_invocation(CommandId=cap["command_id"], InstanceId=ctx["instance_id"])
        output = inv.get("StandardOutputContent", "") + "\n== stderr\n" + inv.get("StandardErrorContent", "")
        cap = {**cap, "status": inv.get("Status"), "truncated": len(inv.get("StandardOutputContent", "")) >= 24000}
    _put(f"{prefix}/capture.txt", output.encode(), "text/plain")
    try:
        audit, audit_error = _audit_window(ctx), None
    except Exception as e:  # noqa: BLE001 - the manifest records a missing audit window
        audit, audit_error = [], str(e)
    _put(f"{prefix}/audit.jsonl", "\n".join(json.dumps(e) for e in audit).encode(), "application/x-ndjson")
    manifest = {
        "schema": "k8s-self-healing-platform/evidence/v1",
        "written_at": now(),
        "order": "isolate -> preserve -> investigate (eradicate is a human decision)",
        "finding": ctx["finding"],
        "target": {k: ctx[k] for k in ("pod", "pod_uid", "service_account", "node", "instance_id", "container_ids")},
        "steps": steps,
        "evidence": {
            "capture": {**cap, "key": f"{prefix}/capture.txt", "bytes": len(output)},
            "snapshots": (steps.get("snapshot") or {}).get("snapshot_ids", []),
            "audit": {"key": f"{prefix}/audit.jsonl", "events": len(audit), "error": audit_error},
        },
    }
    _put(f"{prefix}/manifest.json", json.dumps(manifest, indent=2, default=str).encode(), "application/json")
    return {"at": manifest["written_at"], "bucket": EVIDENCE_BUCKET, "manifest_key": f"{prefix}/manifest.json",
            "capture_bytes": len(output), "audit_events": len(audit)}


def custody(state: dict) -> dict:
    ctx = state["ctx"]
    prefix = f"evidence/{ctx['node']}/{ctx['pod']}/{ctx['finding']['finding_id']}"
    body = {"written_at": now(), "kms_key": EVIDENCE_KMS_ARN, "copies": state.get("copies", [])}
    _put(f"{prefix}/custody.json", json.dumps(body, indent=2, default=str).encode(), "application/json")
    return {"at": body["written_at"], "key": f"{prefix}/custody.json", "copies": len(body["copies"])}


ACTIONS = {"isolate_pod": isolate_pod, "cordon": cordon, "revoke": revoke}


def handler(event, _context):
    action = event.get("action")
    if action == "resolve":
        return resolve(event["event"])
    if action in ACTIONS:
        return ACTIONS[action](event["ctx"])
    if action == "collect":
        return collect(event["state"])
    if action == "custody":
        return custody(event["state"])
    raise ValueError(f"unknown action {action!r}")
