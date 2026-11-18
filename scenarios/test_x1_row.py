"""Pure X1 helper and judge tests. No cluster."""
import unittest

import judge
import x1_row

T = judge.THRESHOLDS["kind"]["X1"]


def hardened():
    return {"profile": "hardened", "reachable_before": True,
            "x1a": {"phase": "Isolated", "isolate_s": 2.0, "blocked_s": 1.0, "same_uid": True,
                    "restarts_unchanged": True, "replacement_serving": True},
            "x1b": {"phase": "Isolated", "handled": "r1", "refused_reason": "NoEvidence", "blocked": True},
            "x1c": {"preserved": True, "released": True, "after_evidence": True, "reachable_s": 3.0,
                    "labels_restored": True},
            "x1d": {"resumed": True, "resume_s": 8.0, "label_actions": 1, "same_uid": True, "controller_pods": 1},
            "x1e": {"phase": "Failed", "reason": "Protected", "labels_unchanged": True, "can_delete_pods": False,
                    "can_get_secrets": False, "can_patch_pods": True}}


def baseline(**over):
    return {"profile": "baseline", "controller_present": False, "reachable_before": True, "blocked_s": 1.0,
            "released_without_evidence": True, "status_available": False, **over}


class Helpers(unittest.TestCase):
    def test_probe_is_true_only_for_an_explicit_200(self):
        self.assertTrue(x1_row.parse_probe("200\n"))
        for bad in ("", "blocked", "garbage", "500"):
            self.assertFalse(x1_row.parse_probe(bad))

    def test_label_actions_are_counted_per_quarantine(self):
        log = "\n".join(['X1_LABELS {"quarantine": "q-d", "labels": {}}', 'X1_LABELS {"quarantine": "q-a", "labels": {}}',
                         "X1_LABELS not json", "X1_STATUS {}"])
        self.assertEqual(x1_row.count_actions(log, "q-d"), 1)

    def test_condition_lookup(self):
        cr = {"status": {"conditions": [{"type": "ReleaseRefused", "reason": "NoEvidence"}]}}
        self.assertEqual(x1_row.condition(cr, "ReleaseRefused")["reason"], "NoEvidence")
        self.assertIsNone(x1_row.condition({}, "ReleaseRefused"))

    def test_the_client_never_mounts_a_service_account_token(self):
        self.assertFalse(x1_row.client_manifest()["spec"]["automountServiceAccountToken"])


class Judge(unittest.TestCase):
    def test_baseline_that_releases_without_evidence_is_impact(self):
        self.assertEqual(judge.judge_x1(baseline(), T)[:3:2], (True, True))

    def test_baseline_that_never_blocked_is_an_error_not_no_impact(self):
        self.assertFalse(judge.judge_x1(baseline(blocked_s=None), T)[0])

    def test_baseline_with_the_controller_still_present_is_an_error(self):
        self.assertFalse(judge.judge_x1(baseline(controller_present=True), T)[0])

    def test_hardened_pass_is_healed(self):
        self.assertEqual(judge.judge_x1(hardened(), T)[2:], (True, True))

    def test_a_restart_during_isolation_is_a_miss(self):
        o = hardened()
        o["x1a"]["restarts_unchanged"] = False
        self.assertEqual(judge.judge_x1(o, T)[2:], (True, False))

    def test_a_release_before_evidence_is_a_miss(self):
        o = hardened()
        o["x1b"]["phase"] = "Released"
        self.assertEqual(judge.judge_x1(o, T)[2:], (True, False))

    def test_a_duplicate_label_action_after_the_kill_is_a_miss(self):
        o = hardened()
        o["x1d"]["label_actions"] = 2
        self.assertEqual(judge.judge_x1(o, T)[2:], (True, False))

    def test_a_role_that_can_delete_pods_is_a_miss(self):
        o = hardened()
        o["x1e"]["can_delete_pods"] = True
        self.assertEqual(judge.judge_x1(o, T)[2:], (True, False))

    def test_missing_observation_is_an_error(self):
        o = hardened()
        del o["x1d"]
        self.assertFalse(judge.judge_x1(o, T)[0])


if __name__ == "__main__":
    unittest.main()
