"""The runner's honesty rules, tested without a cluster.

Every case here is a way a naive harness would print a false "healed". The
judge must turn each of them into "error", "inconclusive" or "miss" instead.
Run: make test
"""
import unittest

from judge import THRESHOLDS, classify, overall, verdict

T = THRESHOLDS["kind"]


def a1(**kw):
    o = {"samples": 700, "expected": 700, "injected": True, "error_rate_after": 0.0,
         "recovered_s": 20.0, "aborted": False}
    o.update(kw)
    return o


class Classify(unittest.TestCase):
    def test_heal_after_baseline_impact_is_healed(self):
        self.assertEqual(classify("hardened", True, "", False, True, "impact")[0], "healed")

    def test_heal_without_baseline_impact_is_inconclusive(self):
        s, why = classify("hardened", True, "", False, True, "no-impact")
        self.assertEqual(s, "inconclusive")
        self.assertIn("impact observed in the same run", why)

    def test_heal_after_baseline_error_is_inconclusive(self):
        self.assertEqual(classify("hardened", True, "", False, True, "error")[0], "inconclusive")

    def test_missing_baseline_is_inconclusive(self):
        self.assertEqual(classify("hardened", True, "", False, True, None)[0], "inconclusive")

    def test_unmeasurable_is_error_even_if_it_looks_healed(self):
        self.assertEqual(classify("hardened", False, "thin", False, True, "impact")[0], "error")


class NoDataIsNeverAPass(unittest.TestCase):
    def test_thin_prober_window_is_error(self):
        v = verdict("A1", "hardened", a1(samples=100), "impact")
        self.assertEqual(v["status"], "error")
        self.assertFalse(v["pass"])

    def test_zero_errors_from_zero_samples_is_error(self):
        v = verdict("S1", "hardened", {"samples": 0, "expected": 900, "failed": 0, "revoked": True,
                                       "rolled_s": 30}, "impact")
        self.assertEqual(v["status"], "error")

    def test_bad_release_that_never_served_is_error(self):
        o = {"samples": 1200, "expected": 1200, "v2_seen": False, "error_rate_final": 0.0,
             "releases_final": ["v1"], "aborted_s": None}
        self.assertEqual(verdict("A2", "hardened", o, "impact")["status"], "error")

    def test_canary_aborted_on_no_data_is_error(self):
        o = {"samples": 300, "expected": 300, "v2_seen": True, "error_rate_final": 0.0, "releases_final": ["v1"],
             "aborted_s": 30, "analysis": {"values": ["[]", "[]"]}}
        v = verdict("A2", "hardened", o, "impact")
        self.assertEqual(v["status"], "error")
        self.assertIn("wrong reason", v["reason"])

    def test_canary_aborted_on_measured_errors_is_healed(self):
        o = {"samples": 300, "expected": 300, "v2_seen": True, "error_rate_final": 0.0, "releases_final": ["v1"],
             "aborted_s": 60, "analysis": {"values": ["[0.31]", "[0.24]"]}}
        self.assertEqual(verdict("A2", "hardened", o, "impact")["status"], "healed")

    def test_falco_silent_is_error_not_healed(self):
        o = {"samples": 600, "expected": 600, "detected_s": None, "reachable_before": True,
             "isolated_s": 5, "hubble_policy_drops": 3, "evidence_s": 20, "pod_kept_for_capture": True,
             "error_rate": 0.0}
        self.assertEqual(verdict("R1", "hardened", o, "impact")["status"], "error")

    def test_isolation_without_hubble_policy_drop_is_error(self):
        o = {"samples": 600, "expected": 600, "detected_s": 2, "reachable_before": True,
             "isolated_s": 5, "hubble_policy_drops": 0, "evidence_s": 20, "pod_kept_for_capture": True,
             "error_rate": 0.0}
        v = verdict("R1", "hardened", o, "impact")
        self.assertEqual(v["status"], "error")
        self.assertIn("Hubble", v["reason"])

    def test_target_unreachable_before_attack_is_error(self):
        o = {"samples": 600, "expected": 600, "detected_s": 2, "reachable_before": False,
             "isolated_s": 0, "hubble_policy_drops": 3, "evidence_s": 20, "pod_kept_for_capture": True,
             "error_rate": 0.0}
        self.assertEqual(verdict("R1", "hardened", o, "impact")["status"], "error")

    def test_runner_exception_is_error(self):
        self.assertEqual(verdict("A1", "hardened", {"exception": "boom"}, "impact")["status"], "error")


class Thresholds(unittest.TestCase):
    def test_slow_recovery_is_miss(self):
        self.assertEqual(verdict("A1", "hardened", a1(recovered_s=61.0), "impact")["status"], "miss")

    def test_aborted_hardened_row_is_miss(self):
        self.assertEqual(verdict("A1", "hardened", a1(aborted=True), "impact")["status"], "miss")

    def test_aborted_baseline_row_is_impact(self):
        o = {"samples": 900, "expected": 900, "failed": 400, "revoked": True, "rolled_s": None, "aborted": True}
        self.assertEqual(verdict("S1", "baseline", o, None)["status"], "impact")

    def test_one_failed_request_fails_rotation(self):
        o = {"samples": 900, "expected": 900, "failed": 1, "revoked": True, "rolled_s": 30, "synced_s": 8}
        self.assertEqual(verdict("S1", "hardened", o, "impact")["status"], "miss")

    def test_rotation_finished_after_grace_is_miss(self):
        o = {"samples": 900, "expected": 900, "failed": 0, "revoked": True, "rolled_s": 61, "synced_s": 8}
        self.assertEqual(verdict("S1", "hardened", o, "impact")["status"], "miss")

    def test_spike_without_scale_out_is_miss(self):
        o = {"samples": 600, "expected": 600, "load_ran": True, "p95_ms": 120, "error_rate": 0.0,
             "min_replicas": 3, "max_replicas": 3}
        self.assertEqual(verdict("A3", "hardened", o, "impact")["status"], "miss")

    def test_spike_with_no_successes_has_no_p95_and_is_impact(self):
        o = {"samples": 600, "expected": 600, "load_ran": True, "p95_ms": None, "error_rate": 0.9,
             "min_replicas": 3, "max_replicas": 3}
        self.assertEqual(verdict("A3", "baseline", o, None)["status"], "impact")

    def test_oom_without_runbook_is_miss(self):
        o = {"samples": 900, "expected": 900, "injected": True, "oom_s": 8, "recovered_s": 15, "alert_s": 40,
             "alert_runbook": "", "other_restarts": 0, "error_rate": 0.0}
        self.assertEqual(verdict("A5", "hardened", o, "impact")["status"], "miss")

    def test_oom_that_restarts_a_neighbour_is_miss(self):
        o = {"samples": 900, "expected": 900, "injected": True, "oom_s": 8, "recovered_s": 15, "alert_s": 40,
             "alert_runbook": "x", "other_restarts": 1, "error_rate": 0.0}
        self.assertEqual(verdict("A5", "hardened", o, "impact")["status"], "miss")

    def test_quarantine_by_killing_the_pod_is_miss(self):
        o = {"samples": 600, "expected": 600, "detected_s": 2, "reachable_before": True, "isolated_s": 5,
             "hubble_policy_drops": 3, "evidence_s": 20, "pod_kept_for_capture": False, "error_rate": 0.0}
        self.assertEqual(verdict("R1", "hardened", o, "impact")["status"], "miss")

    def test_half_reverted_drift_is_miss(self):
        o = {"injected": True, "policy_restored_s": 5, "edit_reverted_s": None}
        self.assertEqual(verdict("S3", "hardened", o, "impact")["status"], "miss")


class AbortLine(unittest.TestCase):
    AB = {"n": 40, "errors": 30, "error_rate": 0.75, "at_s": 14, "kind": "http"}

    def test_abort_in_baseline_is_impact_even_with_a_short_window(self):
        o = {"samples": 20, "expected": 300, "v2_seen": True, "abort_evidence": self.AB}
        self.assertEqual(verdict("A2", "baseline", o, None)["status"], "impact")

    def test_abort_in_hardened_is_miss(self):
        o = {"samples": 20, "expected": 300, "abort_evidence": self.AB}
        self.assertEqual(verdict("A3", "hardened", o, "impact")["status"], "miss")

    def test_abort_with_too_few_samples_is_not_evidence(self):
        o = {"samples": 5, "expected": 300, "v2_seen": True, "abort_evidence": {**self.AB, "n": 6}}
        self.assertEqual(verdict("A2", "baseline", o, None)["status"], "error")


class Overall(unittest.TestCase):
    def test_all_healed_passes(self):
        self.assertTrue(overall({"A1": {"baseline": {"status": "impact"}, "hardened": {"status": "healed"}}}))

    def test_one_inconclusive_fails(self):
        self.assertFalse(overall({
            "A1": {"baseline": {"status": "impact"}, "hardened": {"status": "healed"}},
            "A2": {"baseline": {"status": "no-impact"}, "hardened": {"status": "inconclusive"}}}))

    def test_row_without_hardened_verdict_fails(self):
        self.assertFalse(overall({"A1": {"baseline": {"status": "impact"}}}))

    def test_empty_run_fails(self):
        self.assertFalse(overall({}))


if __name__ == "__main__":
    unittest.main()
