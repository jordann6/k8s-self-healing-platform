import unittest

import o1_row
from judge import judge_o1


THRESHOLDS = {"controller_recover_s": 60, "evidence_s": 30, "alert_s": 30, "release_s": 30}


def hardened_observation():
    return {
        "quota_present": True,
        "limit_range_present": True,
        "default_resources": {"requests": {"cpu": "50m", "memory": "32Mi"},
                              "limits": {"cpu": "200m", "memory": "128Mi"}},
        "oversized_admitted": False,
        "rbac": {"get_secret": False, "delete_target": False, "patch_quarantine": False,
                 "patch_release": True},
        "direct_release_allowed": False,
        "release_request_accepted": True,
        "gated_before_evidence": True,
        "controller_old_uid": "old",
        "controller_new_uid": "new",
        "controller_recovered_s": 4.0,
        "quarantine_blocked_before": True,
        "evidence": {"target_uid": "target", "target_resource_version": "10",
                     "target_image": "selfheal-o1:v1", "captured_at": "2026-11-09T20:00:00Z"},
        "evidence_s": 1.0,
        "alert": {"kind": "quarantine", "target_uid": "target",
                  "evidence_resource_version": "12"},
        "alert_s": 1.0,
        "release_s": 2.0,
        "release_after_evidence": True,
        "connectivity_after_release": True,
    }


class O1Tests(unittest.TestCase):
    def test_marker_parser(self):
        logs = 'prefix O1_ALERT_DELIVERED {"kind":"quarantine","target_uid":"u1"}\n'
        self.assertEqual(o1_row.parse_marker(logs, "O1_ALERT_DELIVERED ")["target_uid"], "u1")

    def test_named_rbac_target_uses_resource_slash_name(self):
        class Harness:
            @staticmethod
            def k(*args, **_kwargs):
                class Result:
                    returncode = 0
                    stdout = "yes\n"
                Harness.args = args
                return Result()

        self.assertTrue(o1_row._can(Harness, "patch", "pod", "o1-target"))
        self.assertIn("pod/o1-target", Harness.args)

    def test_omitted_empty_ingress_is_quarantined(self):
        class Harness:
            @staticmethod
            def kj(*_args):
                return {"metadata": {"name": "quarantine-ingress"}, "spec": {"policyTypes": ["Ingress"]}}

        self.assertTrue(o1_row._quarantined(Harness))

    def test_hardened_positive(self):
        ok, _, impact, healed = judge_o1(hardened_observation(), THRESHOLDS)
        self.assertTrue(ok)
        self.assertTrue(impact)
        self.assertTrue(healed)

    def test_negative_direct_release_is_miss(self):
        obs = hardened_observation()
        obs["direct_release_allowed"] = True
        ok, _, _, healed = judge_o1(obs, THRESHOLDS)
        self.assertTrue(ok)
        self.assertFalse(healed)

    def test_missing_alert_is_telemetry_error(self):
        obs = hardened_observation()
        obs["alert"] = {}
        ok, reason, _, healed = judge_o1(obs, THRESHOLDS)
        self.assertFalse(ok)
        self.assertIn("alert", reason)
        self.assertFalse(healed)


if __name__ == "__main__":
    unittest.main()
