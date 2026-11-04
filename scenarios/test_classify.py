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


# --------------------------------------------------------------------------- #
# EKS rows (ADR-0003 thresholds["eks"])
# --------------------------------------------------------------------------- #

E = THRESHOLDS["eks"]


def eks(row, profile, obs, baseline="impact"):
    return verdict(row, profile, obs, baseline if profile == "hardened" else None, target="eks")


def a4(**kw):
    o = {"samples": 700, "expected": 700, "terminated": True, "web_pods_on_node": 2, "instance_id": "i-0abc",
         "error_rate": 0.002, "recovered_s": 140.0, "replacement_node": "ip-10-30-1-2", "stopped_by_alarm": False}
    o.update(kw)
    return o


class A4NodeKill(unittest.TestCase):
    def test_clean_replacement_heals(self):
        self.assertEqual(eks("A4", "hardened", a4())["status"], "healed")

    def test_kill_that_hit_no_web_pod_is_error(self):
        v = eks("A4", "hardened", a4(web_pods_on_node=0))
        self.assertEqual(v["status"], "error")
        self.assertIn("ran no web pod", v["reason"])

    def test_experiment_that_never_terminated_is_error(self):
        self.assertEqual(eks("A4", "hardened", a4(terminated=False, experiment_state="stopped"))["status"], "error")

    def test_alarm_stop_is_a_miss_even_with_low_errors(self):
        self.assertEqual(eks("A4", "hardened", a4(stopped_by_alarm=True))["status"], "miss")

    def test_no_replacement_node_is_a_miss(self):
        self.assertEqual(eks("A4", "hardened", a4(replacement_node=None))["status"], "miss")

    def test_slow_recovery_is_a_miss(self):
        self.assertEqual(eks("A4", "hardened", a4(recovered_s=E["A4"]["recover_s"] + 1))["status"], "miss")


def s1b(**kw):
    o = {"samples": 600, "expected": 600, "rotations": 2, "original_credential_rejected": True,
         "failed": 0, "dropped": 0, "users_seen": ["selfheal_app", "selfheal_app_clone"]}
    o.update(kw)
    return o


class S1bDatabaseRotation(unittest.TestCase):
    def test_zero_failures_across_two_rotations_heals(self):
        self.assertEqual(eks("S1b", "hardened", s1b())["status"], "healed")

    def test_one_rotation_is_error_not_a_pass(self):
        # After one alternating rotation the original user still works, so a
        # client that never refreshes would also show 0 failures.
        v = eks("S1b", "hardened", s1b(rotations=1))
        self.assertEqual(v["status"], "error")
        self.assertIn("never changed", v["reason"])

    def test_original_credential_still_valid_is_error(self):
        v = eks("S1b", "hardened", s1b(original_credential_rejected=False))
        self.assertEqual(v["status"], "error")
        self.assertIn("invalidated nothing", v["reason"])

    def test_a_dropped_connection_is_a_miss(self):
        self.assertEqual(eks("S1b", "hardened", s1b(dropped=1))["status"], "miss")

    def test_baseline_failures_are_impact(self):
        self.assertEqual(eks("S1b", "baseline", s1b(failed=40))["status"], "impact")


def s4(**kw):
    o = {"injected": True, "noncompliant_s": 70.0, "reverted_s": 150.0, "reverted_by_automation": True,
         "remediation_state": "SUCCEEDED"}
    o.update(kw)
    return o


class S4FirewallRevert(unittest.TestCase):
    def test_automation_revert_in_time_heals(self):
        self.assertEqual(eks("S4", "hardened", s4())["status"], "healed")

    def test_revert_by_something_else_is_error(self):
        v = eks("S4", "hardened", s4(reverted_by_automation=False, remediation_state=None))
        self.assertEqual(v["status"], "error")
        self.assertIn("something other than the control", v["reason"])

    def test_never_reverted_is_impact_in_baseline(self):
        self.assertEqual(eks("S4", "baseline", s4(reverted_s=None, reverted_by_automation=False))["status"], "impact")

    def test_uninjected_is_error(self):
        self.assertEqual(eks("S4", "hardened", s4(injected=False))["status"], "error")

    def test_late_revert_is_a_miss(self):
        self.assertEqual(eks("S4", "hardened", s4(reverted_s=E["S4"]["revert_s"] + 1))["status"], "miss")


def s5(**kw):
    o = {"samples": 1500, "expected": 1500, "drifted": True, "failed": 0, "replaced_s": 700.0,
         "max_disrupted": 1, "nodes_before": 2, "ami_before": "ami-1", "ami_after": "ami-2"}
    o.update(kw)
    return o


class S5NodeReplacement(unittest.TestCase):
    def test_one_at_a_time_with_no_failures_heals(self):
        self.assertEqual(eks("S5", "hardened", s5())["status"], "healed")

    def test_no_drift_is_error(self):
        v = eks("S5", "hardened", s5(drifted=False))
        self.assertEqual(v["status"], "error")
        self.assertIn("drifted", v["reason"])

    def test_two_nodes_out_at_once_is_a_miss_even_with_no_failures(self):
        self.assertEqual(eks("S5", "hardened", s5(max_disrupted=2))["status"], "miss")

    def test_unknown_concurrency_is_not_a_pass(self):
        self.assertEqual(eks("S5", "hardened", s5(max_disrupted=None))["status"], "miss")


def r2(**kw):
    o = {"samples": 700, "expected": 700, "reachable_before": True, "reachable_after": False,
         "finding": {"source": "aws.guardduty", "type": "Backdoor:Runtime/C&CActivity.B!DNS", "sample": False},
         "finding_s": 240.0, "isolated_s": 300.0, "evidence_s": 420.0, "pod_kept": True,
         "termination_protected": True, "snapshots": 1, "capture_bytes": 9000, "error_rate": 0.001}
    o.update(kw)
    return o


class R2NativeResponse(unittest.TestCase):
    def test_full_response_heals(self):
        self.assertEqual(eks("R2", "hardened", r2())["status"], "healed")

    def test_synthetic_event_is_error(self):
        v = eks("R2", "hardened", r2(finding={"source": "selfheal.demo", "type": "x"}))
        self.assertEqual(v["status"], "error")
        self.assertIn("no real GuardDuty finding", v["reason"])

    def test_sample_finding_is_error(self):
        f = {"source": "aws.guardduty", "type": "x", "sample": True}
        self.assertEqual(eks("R2", "hardened", r2(finding=f))["status"], "error")

    def test_no_finding_is_error_in_both_profiles(self):
        self.assertEqual(eks("R2", "baseline", r2(finding=None, isolated_s=None))["status"], "error")
        self.assertEqual(eks("R2", "hardened", r2(finding=None))["status"], "error")

    def test_isolated_but_still_reachable_is_error(self):
        v = eks("R2", "hardened", r2(reachable_after=True))
        self.assertEqual(v["status"], "error")
        self.assertIn("still answered", v["reason"])

    def test_killed_pod_is_a_miss(self):
        self.assertEqual(eks("R2", "hardened", r2(pod_kept=False))["status"], "miss")

    def test_no_volatile_capture_is_a_miss(self):
        self.assertEqual(eks("R2", "hardened", r2(capture_bytes=0))["status"], "miss")

    def test_unisolated_baseline_is_impact(self):
        self.assertEqual(eks("R2", "baseline", r2(isolated_s=None, evidence_s=None))["status"], "impact")


class EksThresholds(unittest.TestCase):
    def test_every_row_has_a_judge_threshold_and_hypothesis(self):
        from judge import HYPOTHESES, JUDGES
        self.assertEqual(set(THRESHOLDS["eks"]), set(JUDGES))
        self.assertEqual(set(HYPOTHESES), set(JUDGES))

    def test_a3_latency_bar_is_not_relaxed_on_eks(self):
        self.assertEqual(THRESHOLDS["eks"]["A3"]["p95_ms"], THRESHOLDS["kind"]["A3"]["p95_ms"])


if __name__ == "__main__":
    unittest.main()
