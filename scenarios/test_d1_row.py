"""Pure D1 data and telemetry tests."""
import hashlib
import unittest

import run
from d1_row import (EXPECTED_DIGEST, EXPECTED_ROWS, MC_ALIAS, canonical_text,
                    expected_records, is_access_denied, parse_backup_logs,
                    parse_restore_logs)
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


if __name__ == "__main__":
    unittest.main()
