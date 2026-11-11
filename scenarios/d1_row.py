"""D1 backup, restore, deletion resistance, and telemetry helpers."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any


ACTIONS = {
    "D1": "A retained logical PostgreSQL backup restores exact data into a fresh target and rejects deletion",
}
SOURCE_NS = "selfheal-d1-source"
STORE_NS = "selfheal-d1-store"
RESTORE_NS = "selfheal-d1-restore"
BUCKET = "d1-backups"
EXPECTED_ROWS = 100
# The tool image. Kind loads selfheal-d1-tool:v1; on the clouds run.py sets this from
# gitops/targets/<cloud>/generated/D1_IMAGE (ADR-0022).
IMAGE = "selfheal-d1-tool:v1"
# How each store phrases the refusals the row judges (ADR-0022 decision 8, live checks
# L9 and L10). The writer has no delete permission at all; the administrator has it
# and is refused by retention.
WRITER_DENIAL_MARKERS = ("accessdenied", "authorizationpermissionmismatch")
ADMIN_DENIAL_MARKERS = {
    "kind": ("worm", "retention", "object is locked", "object locked"),
    "eks": ("object lock", "objectlock", "retention"),
    "aks": ("blobimmutableduetopolicy", "immutable"),
}
MC_ALIAS = (
    "export MC_CONFIG_DIR=/work/.mc; "
    "mc alias set store http://minio.selfheal-d1-store.svc.cluster.local:9000 "
    "$S3_ACCESS_KEY $S3_SECRET_KEY >/dev/null"
)


def expected_records() -> list[tuple[int, str, str]]:
    records = []
    for seq in range(1, EXPECTED_ROWS + 1):
        payload = f"d1-record-{seq:03d}"
        digest = hashlib.sha256(payload.encode()).hexdigest()
        records.append((seq, payload, digest))
    return records


def canonical_text(records: list[tuple[int, str, str]]) -> str:
    return "".join(f"{seq}|{payload}|{digest}\n" for seq, payload, digest in records)


EXPECTED_DIGEST = hashlib.sha256(canonical_text(expected_records()).encode()).hexdigest()


def parse_backup_logs(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for line in text.splitlines():
        if line.startswith("D1_BACKUP_COMPLETE "):
            result.update(json.loads(line.removeprefix("D1_BACKUP_COMPLETE ")))
        elif line.startswith("D1_MANIFEST_SHA256 "):
            result["manifest_sha256"] = line.split(None, 1)[1].strip()
        elif line.startswith("D1_ARCHIVE_STAT "):
            stat = json.loads(line.removeprefix("D1_ARCHIVE_STAT "))
            result["object_version"] = stat.get("versionID") or stat.get("versionId")
            result["object_size"] = stat.get("size")
            result["retain_until"] = stat.get("retainUntilDate") or stat.get("retain-until-date")
        elif line.startswith("D1_RETENTION_INFO "):
            match = re.search(r"Object locking '([^']+)' is configured for ([0-9]+)DAYS", line)
            if match:
                result["retention_mode"] = match.group(1)
                result["retention_days"] = int(match.group(2))
    return result


def parse_restore_logs(text: str) -> dict[str, Any]:
    for line in text.splitlines():
        if line.startswith("D1_RESTORE_COMPLETE "):
            return json.loads(line.removeprefix("D1_RESTORE_COMPLETE "))
    return {}


def is_access_denied(text: str) -> bool:
    normalized = text.lower().replace(" ", "")
    return any(marker in normalized for marker in WRITER_DENIAL_MARKERS)


def is_retention_refusal(text: str, target: str) -> bool:
    """True when the store refused because of retention, in the words this target's store uses."""
    normalized = text.lower().replace(" ", "")
    markers = tuple(marker.replace(" ", "") for marker in ADMIN_DENIAL_MARKERS[target])
    return any(marker in normalized for marker in markers)


def permission_proven(text: str, returncode: int, target: str) -> bool:
    """An authorization check that does not depend on the delete being refused.

    Both stores authorize before they look at the object, so deleting an object that was
    never written either succeeds (S3 adds a delete marker) or fails as not found (Blob).
    An authorization failure is the only outcome that means the permission is missing."""
    if returncode == 0:
        return True
    normalized = text.lower().replace(" ", "")
    if target == "aks":
        return "blobnotfound" in normalized and "authorization" not in normalized
    return False


def cloud_admin_delete(h: Any, store: dict[str, str], target: str, key: str,
                       version: str, backup_id: str) -> dict[str, Any]:
    """The administrator attempt, run by the operator identity from the workstation.

    Proves the operator may delete (a never-written key), then tries to delete the retained
    version. The first call makes a refusal of the second attributable to retention."""
    probe_key = f"backups/permcheck-{backup_id}"
    if target == "eks":
        profile = os.environ.get("LAB_PROFILE") or os.environ.get("AWS_PROFILE", "")
        base = ["env", f"AWS_PROFILE={profile}", f"AWS_REGION={store.get('AWS_REGION', '')}",
                "aws", "s3api", "delete-object", "--bucket", store["S3_BUCKET"]]
        permission = h.sh([*base, "--key", probe_key], timeout=60)
        attempt = h.sh([*base, "--key", key, "--version-id", version], timeout=60)
    else:
        base = ["az", "storage", "blob", "delete", "--only-show-errors",
                "--account-name", store["AZ_ACCOUNT_URL"].split("//", 1)[1].split(".", 1)[0],
                "--container-name", store["D1_CONTAINER"], "--auth-mode", "login"]
        permission = h.sh([*base, "--name", probe_key], timeout=90)
        # A blob's backup version is its current version, and Azure refuses a delete by
        # version id on the current version (OperationNotAllowedOnRootBlob, live 2026-11-11).
        # The administrator attempt is a plain delete of the blob, which retention refuses.
        attempt = h.sh([*base, "--name", key], timeout=90)
    permission_text = (permission.stderr or permission.stdout)
    attempt_text = (attempt.stderr or attempt.stdout)
    return {
        "admin_permission_checked": permission_proven(permission_text, permission.returncode, target),
        "admin_permission_reason": permission_text.strip()[-500:],
        "admin_deleted": attempt.returncode == 0,
        "admin_refusal_recognised": attempt.returncode != 0 and is_retention_refusal(attempt_text, target),
        "admin_reason": attempt_text.strip()[-1000:],
    }


def _psql(h: Any, namespace: str, pod: str, database: str, sql: str,
          check: bool = True) -> Any:
    return h.k("-n", namespace, "exec", "-i", pod, "--", "psql", "-U", "d1",
               "-d", database, "-v", "ON_ERROR_STOP=1", "-At", input=sql,
               timeout=60, check=check)


def seed_source(h: Any) -> None:
    values = ",\n".join(
        "(%d,'%s','%s',clock_timestamp())" % record for record in expected_records()
    )
    sql = f"""
DROP TABLE IF EXISTS records;
CREATE TABLE records (
  seq integer PRIMARY KEY,
  payload text NOT NULL,
  payload_sha256 text NOT NULL,
  written_at timestamptz NOT NULL
);
INSERT INTO records (seq, payload, payload_sha256, written_at) VALUES
{values};
"""
    _psql(h, SOURCE_NS, "d1-source-0", "d1", sql)


def inspect_dataset(h: Any, namespace: str, pod: str, database: str) -> dict[str, Any]:
    result = _psql(
        h, namespace, pod, database,
        "SELECT seq || '|' || payload || '|' || payload_sha256 FROM records ORDER BY seq;\n",
        check=False,
    )
    if result.returncode != 0:
        return {"available": False, "reason": (result.stderr or result.stdout).strip()[:500]}
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    parsed: list[tuple[int, str, str]] = []
    try:
        for line in lines:
            seq, payload, digest = line.split("|", 2)
            parsed.append((int(seq), payload, digest))
    except (ValueError, TypeError):
        return {"available": True, "parse_error": True, "rows": len(lines)}
    canonical = canonical_text(parsed)
    seqs = [item[0] for item in parsed]
    return {
        "available": True,
        "rows": len(parsed),
        "min_seq": min(seqs) if seqs else None,
        "max_seq": max(seqs) if seqs else None,
        "contiguous": seqs == list(range(1, len(seqs) + 1)),
        "digest": hashlib.sha256(canonical.encode()).hexdigest(),
        "exact": parsed == expected_records(),
    }


def _wait_job(h: Any, namespace: str, name: str, timeout: int,
              expect_success: bool) -> tuple[dict[str, Any], str, float]:
    started = h.now()

    def finished() -> dict[str, Any] | None:
        job = h.kj("-n", namespace, "get", "job", name)
        status = job.get("status", {})
        if status.get("succeeded") or status.get("failed"):
            return job
        return None

    job = h.wait_for(finished, timeout, step=1, what=f"D1 Job {name}")
    elapsed = round(h.now() - started, 3)
    logs = h.k("-n", namespace, "logs", f"job/{name}", timeout=30).stdout
    succeeded = bool(job.get("status", {}).get("succeeded"))
    if succeeded != expect_success:
        state = "succeeded" if succeeded else "failed"
        raise RuntimeError(f"D1 Job {name} unexpectedly {state}: {logs[-500:]}")
    return job, logs, elapsed


def _workload_identity_labels(target: str) -> dict[str, str]:
    return {"azure.workload.identity/use": "true"} if target == "aks" else {}


def _restore_job(name: str, object_key: str, archive_sha256: str,
                 target_db: str, truncate_bytes: int | None = None,
                 target: str = "kind") -> dict[str, Any]:
    env: list[dict[str, Any]] = [
        {"name": "POSTGRES_HOST", "value": "d1-restore.selfheal-d1-restore.svc.cluster.local"},
        {"name": "POSTGRES_USER", "value": "d1"},
        {"name": "POSTGRES_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "d1-postgres", "key": "password"}}},
        {"name": "TARGET_DB", "value": target_db},
        {"name": "OBJECT_KEY", "value": object_key},
        {"name": "EXPECTED_SHA256", "value": archive_sha256},
    ]
    if target == "kind":
        env.extend([
            {"name": "S3_ENDPOINT", "value": "http://minio.selfheal-d1-store.svc.cluster.local:9000"},
            {"name": "S3_BUCKET", "value": BUCKET},
            {"name": "S3_ACCESS_KEY", "valueFrom": {"secretKeyRef": {"name": "d1-backup-store", "key": "access-key"}}},
            {"name": "S3_SECRET_KEY", "valueFrom": {"secretKeyRef": {"name": "d1-backup-store", "key": "secret-key"}}},
        ])
    if truncate_bytes is not None:
        env.append({"name": "TRUNCATE_BYTES", "value": str(truncate_bytes)})
    cloud_spec: dict[str, Any] = {}
    if target != "kind":
        # Cloud identity, no static keys. The store settings come from the ConfigMap.
        cloud_spec = {"serviceAccountName": "d1-restore"}
    return {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": name, "namespace": RESTORE_NS},
        "spec": {
            "backoffLimit": 0, "activeDeadlineSeconds": 120,
            "template": {"metadata": {"labels": {"app": "d1-restore-job",
                                                 **_workload_identity_labels(target)}}, "spec": {
                **cloud_spec,
                "restartPolicy": "Never", "automountServiceAccountToken": False,
                "enableServiceLinks": False,
                "securityContext": {"runAsNonRoot": True, "runAsUser": 70,
                                    "runAsGroup": 70, "fsGroup": 70,
                                    "seccompProfile": {"type": "RuntimeDefault"}},
                "containers": [{
                    "name": "restore", "image": IMAGE,
                    "imagePullPolicy": "IfNotPresent", "command": ["/usr/local/bin/restore.sh"],
                    "env": env,
                    **({"envFrom": [{"configMapRef": {"name": "d1-store"}}]} if target != "kind" else {}),
                    "securityContext": {"allowPrivilegeEscalation": False,
                                        "readOnlyRootFilesystem": True,
                                        "capabilities": {"drop": ["ALL"]}},
                    "resources": {"requests": {"cpu": "25m", "memory": "64Mi"},
                                  "limits": {"cpu": "500m", "memory": "256Mi"}},
                    "volumeMounts": [{"name": "work", "mountPath": "/work"}],
                }],
                "volumes": [{"name": "work", "emptyDir": {"sizeLimit": "256Mi"}}],
            }},
        },
    }


def _s3_job(name: str, namespace: str, script: str, secret: str) -> dict[str, Any]:
    return {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 60, "template": {
            "metadata": {"labels": {"app": "d1-s3-check"}},
            "spec": {
                "restartPolicy": "Never", "automountServiceAccountToken": False,
                "enableServiceLinks": False,
                "securityContext": {"runAsNonRoot": True, "runAsUser": 70,
                                    "runAsGroup": 70,
                                    "seccompProfile": {"type": "RuntimeDefault"}},
                "containers": [{
                    "name": "check", "image": "selfheal-d1-tool:v1",
                    "imagePullPolicy": "IfNotPresent", "command": ["/bin/sh", "-c", script],
                    "env": [
                        {"name": "S3_ACCESS_KEY", "valueFrom": {"secretKeyRef": {"name": secret, "key": "access-key"}}},
                        {"name": "S3_SECRET_KEY", "valueFrom": {"secretKeyRef": {"name": secret, "key": "secret-key"}}},
                    ],
                    "securityContext": {"allowPrivilegeEscalation": False,
                                        "readOnlyRootFilesystem": True,
                                        "capabilities": {"drop": ["ALL"]}},
                    "resources": {"requests": {"cpu": "10m", "memory": "32Mi"},
                                  "limits": {"cpu": "100m", "memory": "64Mi"}},
                    "volumeMounts": [{"name": "work", "mountPath": "/work"}],
                }],
                "volumes": [{"name": "work", "emptyDir": {"sizeLimit": "256Mi"}}],
            },
        }},
    }


def _cloud_job(name: str, namespace: str, script: str, service_account: str,
               target: str) -> dict[str, Any]:
    """A one-shot Job that runs the store client as one of the two D1 identities."""
    return {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 60, "template": {
            "metadata": {"labels": {"app": "d1-store-check", **_workload_identity_labels(target)}},
            "spec": {
                "serviceAccountName": service_account,
                "restartPolicy": "Never", "automountServiceAccountToken": False,
                "enableServiceLinks": False,
                "securityContext": {"runAsNonRoot": True, "runAsUser": 70, "runAsGroup": 70,
                                    "seccompProfile": {"type": "RuntimeDefault"}},
                "containers": [{
                    "name": "check", "image": IMAGE,
                    "imagePullPolicy": "IfNotPresent", "command": ["/bin/sh", "-c", script],
                    "envFrom": [{"configMapRef": {"name": "d1-store"}}],
                    "securityContext": {"allowPrivilegeEscalation": False,
                                        "readOnlyRootFilesystem": True,
                                        "capabilities": {"drop": ["ALL"]}},
                    "resources": {"requests": {"cpu": "10m", "memory": "64Mi"},
                                  "limits": {"cpu": "200m", "memory": "192Mi"}},
                    "volumeMounts": [{"name": "work", "mountPath": "/work"}],
                }],
                "volumes": [{"name": "work", "emptyDir": {"sizeLimit": "256Mi"}}],
            },
        }},
    }


def write_store_record(h: Any, store: dict[str, str], backup: dict[str, Any]) -> None:
    """Remember when the second destroy becomes possible (ADR-0022 decision 3).

    The destroy script reads this and refuses until the retention has passed."""
    directory = h.ROOT / ".d1"
    directory.mkdir(exist_ok=True)
    record = {
        "target": h.TARGET,
        "store": {k: v for k, v in store.items() if k in ("S3_BUCKET", "D1_CONTAINER", "AZ_ACCOUNT_URL", "AWS_REGION")},
        "backup_id": backup.get("backup_id"),
        "object_version": backup.get("object_version"),
        "retain_until": backup.get("retain_until"),
        "recorded_at": h.iso(h.now()),
    }
    (directory / f"{h.TARGET}-store.json").write_text(json.dumps(record, indent=2) + "\n")


def _replace_source_data(h: Any) -> dict[str, Any]:
    started = h.now()
    h.k("-n", "argocd", "patch", "application", "selfheal-d1", "--type", "merge",
        "-p", json.dumps({"spec": {"syncPolicy": {"automated": None}}}), check=True)
    try:
        h.k("-n", SOURCE_NS, "delete", "statefulset", "d1-source",
            "--cascade=foreground", "--wait=true", timeout=90, check=True)
        h.k("-n", SOURCE_NS, "delete", "pvc", "data-d1-source-0",
            "--wait=true", timeout=90, check=True)
        storage_released = h.now()
    finally:
        h.k("-n", "argocd", "patch", "application", "selfheal-d1", "--type", "merge",
            "-p", json.dumps({"spec": {"syncPolicy": {"automated": {
                "prune": True, "selfHeal": True,
            }}}}), check=True)
    h.refresh("selfheal-d1")
    h.wait_for(lambda: h.k("-n", SOURCE_NS, "rollout", "status", "statefulset/d1-source",
                           "--timeout=10s", timeout=15).returncode == 0,
               120, step=2, what="empty D1 source PostgreSQL")
    after = inspect_dataset(h, SOURCE_NS, "d1-source-0", "d1")
    return {"started_at": h.iso(started), "elapsed_s": round(h.now() - started, 3),
            # How long the replacement volume and PostgreSQL took, recorded beside the total
            # so a missed recovery clock on a cloud is attributable (ADR-0022 decision 9).
            "source_reprovision_s": round(h.now() - storage_released, 3),
            "original_data_absent": after.get("available") is False, "after": after}


def _integrity_ok(data: dict[str, Any]) -> bool:
    return (data.get("available") is True and data.get("rows") == EXPECTED_ROWS
            and data.get("min_seq") == 1 and data.get("max_seq") == EXPECTED_ROWS
            and data.get("contiguous") is True and data.get("exact") is True
            and data.get("digest") == EXPECTED_DIGEST)


def run(h: Any, _lab: Any, profile: str, directory: Path) -> dict[str, Any]:
    t0 = h.now()
    cloud = h.TARGET != "kind"
    store: dict[str, str] = {}
    if cloud:
        store = h.kj("-n", SOURCE_NS, "get", "configmap", "d1-store").get("data") or {}
        if not store.get("D1_STORE"):
            raise RuntimeError("D1 store settings (ConfigMap d1-store) are missing; apply the WORM store "
                               "root, then rerun the target bootstrap")
    seed_source(h)
    source = inspect_dataset(h, SOURCE_NS, "d1-source-0", "d1")
    if not _integrity_ok(source):
        raise RuntimeError(f"D1 deterministic source data is invalid: {source}")

    if profile == "baseline":
        cronjob_present = bool(h.kj("-n", SOURCE_NS, "get", "cronjob", "d1-backup").get("metadata"))
        loss = _replace_source_data(h)
        return {
            "t0": t0, "source": source, "cronjob_present": cronjob_present,
            "backup": None, "loss": loss, "restore_available": False,
            "window": "deterministic source creation through unprotected source PVC replacement",
        }

    if not h.kj("-n", SOURCE_NS, "get", "cronjob", "d1-backup").get("metadata"):
        raise RuntimeError("D1 hardened backup CronJob is missing")
    backup_name = f"d1-backup-{int(time.time())}"
    started = h.now()
    h.k("-n", SOURCE_NS, "create", "job", backup_name,
        "--from=cronjob/d1-backup", check=True)
    backup_job, backup_logs, backup_s = _wait_job(h, SOURCE_NS, backup_name, 125, True)
    (directory / "backup.log").write_text(backup_logs)
    backup = parse_backup_logs(backup_logs)
    backup.update({
        "job_name": backup_name,
        "job_uid": backup_job.get("metadata", {}).get("uid"),
        "completed": bool(backup_job.get("status", {}).get("succeeded")),
        "completion_s": backup_s,
        "started_at": h.iso(started),
    })

    sentinel_at = h.now()
    payload = "d1-record-101-post-backup"
    sentinel_sha = hashlib.sha256(payload.encode()).hexdigest()
    _psql(h, SOURCE_NS, "d1-source-0", "d1",
          "INSERT INTO records VALUES (101, '%s', '%s', clock_timestamp());\n" % (payload, sentinel_sha))
    loss_started = h.now()
    loss = _replace_source_data(h)

    restore_name = f"d1-restore-{int(time.time())}"
    h.apply(_restore_job(restore_name, backup.get("archive_key", ""),
                         backup.get("archive_sha256", ""), "d1", target=h.TARGET))
    restore_job, restore_logs, restore_s = _wait_job(h, RESTORE_NS, restore_name, 125, True)
    (directory / "restore.log").write_text(restore_logs)
    restored = inspect_dataset(h, RESTORE_NS, "d1-restore-0", "d1")
    rto_s = round(h.now() - loss_started, 3)
    restore = parse_restore_logs(restore_logs)
    restore.update({
        "job_name": restore_name,
        "job_uid": restore_job.get("metadata", {}).get("uid"),
        "completed": bool(restore_job.get("status", {}).get("succeeded")),
        "completion_s": restore_s,
        "target_namespace": RESTORE_NS,
        "target_database": "d1",
    })

    invalid_name = f"d1-invalid-{int(time.time())}"
    h.apply(_restore_job(invalid_name, backup.get("archive_key", ""),
                         backup.get("archive_sha256", ""), "d1_invalid", truncate_bytes=128,
                         target=h.TARGET))
    invalid_job, invalid_logs, invalid_s = _wait_job(h, RESTORE_NS, invalid_name, 125, False)
    (directory / "invalid-restore.log").write_text(invalid_logs)
    table_count_result = _psql(
        h, RESTORE_NS, "d1-restore-0", "d1_invalid",
        "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename='records';\n",
        check=False,
    )
    invalid_restore = {
        "job_name": invalid_name,
        "job_uid": invalid_job.get("metadata", {}).get("uid"),
        "failed": bool(invalid_job.get("status", {}).get("failed")),
        "completion_s": invalid_s,
        "reason": invalid_logs.strip()[-1000:],
        "application_tables": int(table_count_result.stdout.strip() or -1)
        if table_count_result.returncode == 0 else None,
    }

    object_key = backup.get("archive_key", "")
    version = backup.get("object_version", "")
    alias = MC_ALIAS
    manifest_key = object_key.removesuffix(".dump") + ".json"
    admin_extra: dict[str, Any] = {}
    writer_delete_name = f"d1-writer-delete-{int(time.time())}"
    if cloud:
        # The backup identity, with no delete permission, tries to delete the archive.
        h.apply(_cloud_job(writer_delete_name, SOURCE_NS, f'd1store.py delete "{object_key}"',
                           "d1-backup", h.TARGET))
    else:
        h.apply(_s3_job(writer_delete_name, SOURCE_NS,
                        f"{alias}; mc rm store/{BUCKET}/{object_key}", "d1-backup-store"))
    _, writer_delete_logs, _ = _wait_job(h, SOURCE_NS, writer_delete_name, 65, False)

    if cloud:
        # The operator, who may delete, tries to delete the retained version directly.
        admin = cloud_admin_delete(h, store, h.TARGET, object_key, version, str(backup.get("backup_id")))
        admin_delete_logs = admin["admin_reason"]
        admin_extra = {k: v for k, v in admin.items() if k != "admin_reason"}
        admin_retention_refused = admin["admin_refusal_recognised"]
    else:
        admin_delete_name = f"d1-admin-delete-{int(time.time())}"
        h.apply(_s3_job(admin_delete_name, STORE_NS,
                        f"{alias}; mc rm --version-id '{version}' store/{BUCKET}/{object_key}",
                        "d1-minio-root"))
        _, admin_delete_logs, _ = _wait_job(h, STORE_NS, admin_delete_name, 65, False)
        admin_retention_refused = is_retention_refusal(admin_delete_logs, "kind")

    verify_name = f"d1-object-verify-{int(time.time())}"
    if cloud:
        verify_script = (
            f'd1store.py get "{object_key}" /work/archive && d1store.py get "{manifest_key}" /work/manifest && '
            "echo D1_ARCHIVE_SHA256 $(sha256sum /work/archive | awk '{print $1}') && "
            "echo D1_MANIFEST_SHA256 $(sha256sum /work/manifest | awk '{print $1}')"
        )
        h.apply(_cloud_job(verify_name, RESTORE_NS, verify_script, "d1-restore", h.TARGET))
    else:
        verify_script = (
            f"{alias}; mc cp store/{BUCKET}/{object_key} /work/archive >/dev/null; "
            f"mc cp store/{BUCKET}/{manifest_key} /work/manifest >/dev/null; "
            "echo D1_ARCHIVE_SHA256 $(sha256sum /work/archive | awk '{print $1}'); "
            "echo D1_MANIFEST_SHA256 $(sha256sum /work/manifest | awk '{print $1}')"
        )
        h.apply(_s3_job(verify_name, RESTORE_NS, verify_script, "d1-backup-store"))
    _, verify_logs, _ = _wait_job(h, RESTORE_NS, verify_name, 65, True)
    checksums = {}
    for line in verify_logs.splitlines():
        if line.startswith("D1_ARCHIVE_SHA256 "):
            checksums["archive_sha256"] = line.split(None, 1)[1].strip()
        elif line.startswith("D1_MANIFEST_SHA256 "):
            checksums["manifest_sha256"] = line.split(None, 1)[1].strip()
    if cloud:
        write_store_record(h, store, backup)

    created_at = backup.get("created_at")
    rpo_seconds = None
    if created_at:
        backup_time = dt.datetime.fromisoformat(str(created_at).replace("Z", "+00:00")).timestamp()
        rpo_seconds = round(max(0.0, sentinel_at - backup_time), 3)
    deletion_resistance = {
        "writer_delete_denied": is_access_denied(writer_delete_logs),
        "writer_reason": writer_delete_logs.strip()[-1000:],
        "admin_version_delete_denied": admin_retention_refused and not admin_extra.get("admin_deleted", False),
        "admin_reason": admin_delete_logs.strip()[-1000:],
        **admin_extra,
        "archive_readable_after": checksums.get("archive_sha256") == backup.get("archive_sha256"),
        "manifest_readable_after": checksums.get("manifest_sha256") == backup.get("manifest_sha256"),
        "verified_checksums": checksums,
    }
    return {
        "t0": t0,
        "source": source,
        "backup": backup,
        "backup_s": backup_s,
        "post_backup_sentinel": {"seq": 101, "acknowledged": True, "at": h.iso(sentinel_at)},
        "loss": loss,
        "restore": restore,
        "restored": restored,
        "rpo_rows": 101 - int(restored.get("max_seq") or 0),
        "rpo_seconds": rpo_seconds,
        "rto_s": rto_s,
        "invalid_restore": invalid_restore,
        "deletion_resistance": deletion_resistance,
        "window": "backup completion through source PVC replacement, fresh-target restore, integrity, invalid restore, and delete denial",
    }
