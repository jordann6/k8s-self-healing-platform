"""Pure D1 data and telemetry tests."""
import hashlib
import unittest

import run
import json
import tempfile
from pathlib import Path

import d1_row
from d1_row import (EXPECTED_DIGEST, EXPECTED_ROWS, MC_ALIAS, canonical_text,
                    cloud_admin_delete, expected_records, is_access_denied,
                    is_retention_refusal, parse_backup_logs, parse_restore_logs,
                    permission_proven, write_store_record, _cloud_job, _restore_job)
from judge import THRESHOLDS, verdict


class Dataset(unittest.TestCase):
    def test_dataset_is_exact_and_deterministic(self):
        records = expected_records()
        self.assertEqual(len(records), EXPECTED_ROWS)
        self.assertEqual([item[0] for item in records], list(range(1, 101)))
        self.assertEqual(hashlib.sha256(canonical_text(records).encode()).hexdigest(), EXPECTED_DIGEST)


class Telemetry(unittest.TestCase):
    def test_access_denial_accepts_minio_and_api_spellings(self):
        self.assertTrue(is_access_denied("Access Denied."))
        self.assertTrue(is_access_denied("AccessDenied"))
        self.assertFalse(is_access_denied("request failed"))

    def test_s3_checks_use_writable_client_configuration(self):
        self.assertIn("MC_CONFIG_DIR=/work/.mc", MC_ALIAS)

    def test_parses_every_required_backup_receipt(self):
        logs = "\n".join([
            'D1_BACKUP_COMPLETE {"backup_id":"b1","archive_key":"backups/b1.dump","archive_sha256":"abc","source_max_seq":100,"created_at":"2026-11-09T12:00:00Z"}',
            "D1_MANIFEST_SHA256 def",
            'D1_ARCHIVE_STAT {"versionID":"v1","size":123,"retentionMode":"COMPLIANCE","retainUntilDate":"2026-11-10T12:00:00Z"}',
            "D1_RETENTION_INFO Object locking 'COMPLIANCE' is configured for 1DAYS.",
        ])
        parsed = parse_backup_logs(logs)
        self.assertEqual(parsed["object_version"], "v1")
        self.assertEqual(parsed["source_max_seq"], 100)
        self.assertEqual(parsed["manifest_sha256"], "def")
        self.assertEqual(parsed["retention_mode"], "COMPLIANCE")
        self.assertEqual(parsed["retention_days"], 1)

    def test_missing_stat_stays_missing(self):
        parsed = parse_backup_logs(
            'D1_BACKUP_COMPLETE {"backup_id":"b1","archive_key":"x","archive_sha256":"abc","source_max_seq":100,"created_at":"2026-11-09T12:00:00Z"}'
        )
        self.assertNotIn("object_version", parsed)

    def test_restore_receipt_is_explicit(self):
        self.assertEqual(
            parse_restore_logs('D1_RESTORE_COMPLETE {"target_db":"d1","archive_sha256":"abc"}'),
            {"target_db": "d1", "archive_sha256": "abc"},
        )


def dataset():
    return {
        "available": True, "rows": 100, "min_seq": 1, "max_seq": 100,
        "contiguous": True, "exact": True, "digest": EXPECTED_DIGEST,
    }


def hardened():
    archive_sha = "a" * 64
    manifest_sha = "b" * 64
    return {
        "source": dataset(),
        "backup": {
            "job_name": "backup", "job_uid": "uid-backup", "completed": True,
            "completion_s": 10, "backup_id": "b1", "archive_key": "backups/b1.dump",
            "archive_sha256": archive_sha, "manifest_sha256": manifest_sha,
            "source_max_seq": 100, "created_at": "2026-11-09T12:00:00Z",
            "object_version": "v1", "object_size": 1234,
            "retention_mode": "COMPLIANCE", "retention_days": 1,
        },
        "post_backup_sentinel": {"seq": 101, "acknowledged": True},
        "loss": {"original_data_absent": True},
        "restore": {
            "job_name": "restore", "job_uid": "uid-restore", "completed": True,
            "completion_s": 12, "target_namespace": "selfheal-d1-restore",
            "target_database": "d1", "archive_sha256": archive_sha,
        },
        "restored": dataset(),
        "rpo_rows": 1, "rpo_seconds": 3, "rto_s": 30,
        "invalid_restore": {"job_uid": "uid-invalid", "failed": True, "application_tables": 0},
        "deletion_resistance": {
            "writer_delete_denied": True, "admin_version_delete_denied": True,
            "archive_readable_after": True, "manifest_readable_after": True,
        },
    }


class Verdict(unittest.TestCase):
    def test_unprotected_baseline_is_impact(self):
        obs = {
            "source": dataset(), "backup": None, "cronjob_present": False,
            "loss": {"original_data_absent": True}, "restore_available": False,
        }
        self.assertEqual(verdict("D1", "baseline", obs, None)["status"], "impact")

    def test_complete_hardened_proof_heals(self):
        self.assertEqual(verdict("D1", "hardened", hardened(), "impact")["status"], "healed")

    def test_missing_backup_version_is_error(self):
        obs = hardened()
        obs["backup"]["object_version"] = None
        self.assertEqual(verdict("D1", "hardened", obs, "impact")["status"], "error")

    def test_missing_restore_completion_is_error(self):
        obs = hardened()
        obs["restore"]["completion_s"] = None
        self.assertEqual(verdict("D1", "hardened", obs, "impact")["status"], "error")

    def test_missing_retention_telemetry_is_error(self):
        obs = hardened()
        obs["backup"]["retention_days"] = None
        self.assertEqual(verdict("D1", "hardened", obs, "impact")["status"], "error")

    def test_invalid_archive_that_restores_is_a_miss(self):
        obs = hardened()
        obs["invalid_restore"]["failed"] = False
        self.assertEqual(verdict("D1", "hardened", obs, "impact")["status"], "miss")

    def test_deletion_that_succeeds_is_a_miss(self):
        obs = hardened()
        obs["deletion_resistance"]["writer_delete_denied"] = False
        self.assertEqual(verdict("D1", "hardened", obs, "impact")["status"], "miss")

    def test_thresholds_were_fixed_in_the_kind_contract(self):
        self.assertEqual(THRESHOLDS["kind"]["D1"], {
            "backup_s": 120, "restore_s": 120, "rto_s": 180,
            "rpo_rows": 1, "rows": 100, "retention_days": 1,
        })


class FocusedPreflight(unittest.TestCase):
    def test_d1_report_names_its_healing_control(self):
        old = run.TARGET
        try:
            run.TARGET = "kind"
            self.assertEqual(run.action("D1"), run.d1_row.ACTIONS["D1"])
        finally:
            run.TARGET = old

    def test_d1_only_uses_database_store_and_gitops(self):
        old = run.TARGET
        try:
            run.TARGET = "kind"
            resources = set(run.precondition_resources(["D1"]))
        finally:
            run.TARGET = old
        self.assertIn(("selfheal-d1-store", "statefulset", "d1-minio"), resources)
        self.assertIn(("selfheal-d1-source", "statefulset", "d1-source"), resources)
        self.assertIn(("argocd", "statefulset", "argocd-application-controller"), resources)
        self.assertNotIn(("falco", "daemonset", "falco"), resources)
        self.assertNotIn(("monitoring", "deploy", "kps-operator"), resources)


class _Run:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def cloud_hardened(target, **resistance):
    obs = hardened()
    obs["backup"]["retention_mode"] = "Locked" if target == "aks" else "COMPLIANCE"
    obs["deletion_resistance"] = {
        "writer_delete_denied": True, "admin_version_delete_denied": True,
        "archive_readable_after": True, "manifest_readable_after": True,
        "admin_permission_checked": True, "admin_deleted": False, "admin_refusal_recognised": True,
        **resistance,
    }
    return obs


class CloudContract(unittest.TestCase):
    """ADR-0022: the cloud port of the kind row."""

    def test_thresholds_differ_from_kind_only_where_the_adr_says(self):
        kind = THRESHOLDS["kind"]["D1"]
        for target, extra in (("eks", {"admin_attribution": True}),
                              ("aks", {"admin_attribution": True, "retention_mode": "Locked"})):
            cloud = THRESHOLDS[target]["D1"]
            self.assertEqual({k: v for k, v in cloud.items() if kind.get(k) != v}, extra)

    def test_complete_cloud_proof_heals_on_both_clouds(self):
        for target in ("eks", "aks"):
            self.assertEqual(verdict("D1", "hardened", cloud_hardened(target), "impact", target)["status"],
                             "healed", target)

    def test_azure_must_report_azures_own_retention_mode(self):
        obs = cloud_hardened("aks")
        obs["backup"]["retention_mode"] = "COMPLIANCE"
        self.assertEqual(verdict("D1", "hardened", obs, "impact", "aks")["status"], "miss")

    def test_unproven_administrator_permission_is_error_not_a_pass(self):
        for target in ("eks", "aks"):
            obs = cloud_hardened(target, admin_permission_checked=False)
            self.assertEqual(verdict("D1", "hardened", obs, "impact", target)["status"], "error")
            obs = cloud_hardened(target, admin_permission_checked=None)
            self.assertEqual(verdict("D1", "hardened", obs, "impact", target)["status"], "error")

    def test_refusal_for_an_unrecognised_reason_is_error(self):
        obs = cloud_hardened("eks", admin_version_delete_denied=False, admin_refusal_recognised=False,
                             admin_reason="AccessDenied: not authorized to perform s3:DeleteObjectVersion")
        self.assertEqual(verdict("D1", "hardened", obs, "impact", "eks")["status"], "error")

    def test_an_administrator_delete_that_succeeded_is_a_miss(self):
        obs = cloud_hardened("aks", admin_version_delete_denied=False, admin_deleted=True,
                             admin_refusal_recognised=False)
        self.assertEqual(verdict("D1", "hardened", obs, "impact", "aks")["status"], "miss")

    def test_kind_needs_none_of_the_cloud_fields(self):
        self.assertEqual(verdict("D1", "hardened", hardened(), "impact", "kind")["status"], "healed")

    def test_each_store_is_recognised_in_its_own_words_and_not_in_the_others_permission_error(self):
        self.assertTrue(is_access_denied("AccessDenied: Access Denied"))
        self.assertTrue(is_access_denied("AuthorizationPermissionMismatch: This request is not authorized"))
        self.assertTrue(is_retention_refusal(
            "An error occurred (AccessDenied) when calling the DeleteObject operation: Access Denied "
            "because object protected by object lock.", "eks"))
        self.assertTrue(is_retention_refusal("BlobImmutableDueToPolicy: The blob is immutable", "aks"))
        self.assertFalse(is_retention_refusal("AccessDenied: User is not authorized", "eks"))
        self.assertFalse(is_retention_refusal("AuthorizationPermissionMismatch: not authorized", "aks"))

    def test_permission_is_proven_by_authorization_not_by_the_refusal(self):
        self.assertTrue(permission_proven("", 0, "eks"))
        self.assertFalse(permission_proven("AccessDenied", 1, "eks"))
        self.assertTrue(permission_proven("ErrorCode:BlobNotFound The specified blob does not exist", 1, "aks"))
        self.assertFalse(permission_proven("AuthorizationPermissionMismatch", 1, "aks"))

    def test_cloud_restore_job_has_no_static_keys_and_uses_the_workload_identity(self):
        old = d1_row.IMAGE
        d1_row.IMAGE = "reg/selfheal/d1:abc"
        try:
            for target in ("eks", "aks"):
                job = _restore_job("r", "backups/x.dump", "a" * 64, "d1", target=target)
                pod = job["spec"]["template"]["spec"]
                text = json.dumps(job)
                self.assertEqual(pod["serviceAccountName"], "d1-restore")
                self.assertEqual(pod["containers"][0]["image"], "reg/selfheal/d1:abc")
                self.assertEqual(pod["containers"][0]["envFrom"], [{"configMapRef": {"name": "d1-store"}}])
                for forbidden in ("S3_ACCESS_KEY", "S3_SECRET_KEY", "d1-backup-store", "minio"):
                    self.assertNotIn(forbidden, text)
                labels = job["spec"]["template"]["metadata"]["labels"]
                self.assertEqual("azure.workload.identity/use" in labels, target == "aks")
        finally:
            d1_row.IMAGE = old

    def test_kind_restore_job_is_unchanged(self):
        job = _restore_job("r", "backups/x.dump", "a" * 64, "d1")
        text = json.dumps(job)
        self.assertIn("S3_ACCESS_KEY", text)
        self.assertNotIn("serviceAccountName", text)
        self.assertEqual(job["spec"]["template"]["spec"]["containers"][0]["image"], "selfheal-d1-tool:v1")

    def test_check_job_runs_as_the_given_identity(self):
        job = _cloud_job("w", "selfheal-d1-source", "d1store.py delete k", "d1-backup", "aks")
        self.assertEqual(job["spec"]["template"]["spec"]["serviceAccountName"], "d1-backup")
        self.assertEqual(job["spec"]["template"]["metadata"]["labels"]["azure.workload.identity/use"], "true")

    def test_admin_attempt_proves_permission_then_targets_the_retained_object(self):
        for target, store, refusal in (
            ("eks", {"S3_BUCKET": "b", "AWS_REGION": "us-east-1"},
             "An error occurred (AccessDenied) ... because object protected by object lock."),
            ("aks", {"AZ_ACCOUNT_URL": "https://acct.blob.core.windows.net", "D1_CONTAINER": "d1-backups"},
             "BlobImmutableDueToPolicy: The blob is immutable due to policy"),
        ):
            calls = []

            class H:
                @staticmethod
                def sh(args, timeout=60):
                    calls.append(args)
                    if len(calls) == 1:
                        return _Run(0) if target == "eks" else _Run(1, "", "ErrorCode:BlobNotFound")
                    return _Run(1, "", refusal)

            result = cloud_admin_delete(H, store, target, "backups/b1.dump", "v-1", "b1")
            self.assertEqual(len(calls), 2)
            self.assertIn("permcheck-b1", " ".join(calls[0]))
            self.assertNotIn("v-1", " ".join(calls[0]))
            self.assertIn("backups/b1.dump", " ".join(calls[1]))
            if target == "eks":
                self.assertIn("v-1", " ".join(calls[1]))
            else:
                # az has no version flag and Azure refuses a delete by id on the current version
                self.assertNotIn("--version-id", " ".join(calls[1]))
                self.assertNotIn("v-1", " ".join(calls[1]))
            self.assertTrue(result["admin_permission_checked"], target)
            self.assertFalse(result["admin_deleted"], target)
            self.assertTrue(result["admin_refusal_recognised"], target)

    def test_store_record_names_when_the_second_destroy_becomes_possible(self):
        with tempfile.TemporaryDirectory() as tmp:
            class H:
                ROOT = Path(tmp)
                TARGET = "eks"

                @staticmethod
                def now():
                    return 1_000_000.0

                @staticmethod
                def iso(value):
                    return "2026-11-10T20:00:00Z"

            write_store_record(H, {"S3_BUCKET": "b", "AWS_REGION": "us-east-1", "ignored": "x"},
                               {"backup_id": "b1", "object_version": "v1", "retain_until": "2026-11-11T20:00:00Z"})
            record = json.loads((Path(tmp) / ".d1" / "eks-store.json").read_text())
        self.assertEqual(record["retain_until"], "2026-11-11T20:00:00Z")
        self.assertEqual(record["store"], {"S3_BUCKET": "b", "AWS_REGION": "us-east-1"})


if __name__ == "__main__":
    unittest.main()
