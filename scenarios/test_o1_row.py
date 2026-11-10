import json
import unittest

import o1_row
from judge import THRESHOLDS as ALL_THRESHOLDS, judge_o1


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


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class O1CloudTests(unittest.TestCase):
    """ADR-0019: the cloud port of the kind row."""

    def test_cloud_thresholds_equal_kind(self):
        for target in ("eks", "aks"):
            self.assertEqual(ALL_THRESHOLDS[target]["O1"], ALL_THRESHOLDS["kind"]["O1"])

    def test_dry_run_uses_the_target_image(self):
        seen = {}

        class Harness:
            @staticmethod
            def k(*_args, input=None, **_kwargs):
                seen["manifest"] = json.loads(input)
                return _Result()

        old = o1_row.IMAGE
        o1_row.IMAGE = "123.dkr.ecr.us-east-2.amazonaws.com/selfheal/o1:abc1234"
        try:
            o1_row._server_dry_run(Harness, "probe")
        finally:
            o1_row.IMAGE = old
        self.assertEqual(seen["manifest"]["spec"]["containers"][0]["image"],
                         "123.dkr.ecr.us-east-2.amazonaws.com/selfheal/o1:abc1234")

    def test_forbidden_impersonation_is_an_error_not_a_pass(self):
        class Harness:
            @staticmethod
            def k(*_args, **_kwargs):
                return _Result(1, "", 'Error from server (Forbidden): users "system:serviceaccount:..." cannot impersonate')

        with self.assertRaises(RuntimeError) as ctx:
            o1_row.check_impersonation(Harness)
        self.assertIn("not a hardening verdict", str(ctx.exception))

    def test_impersonation_answered_no_is_also_an_error(self):
        class Harness:
            @staticmethod
            def k(*_args, **_kwargs):
                return _Result(1, "no\n", "")

        with self.assertRaises(RuntimeError):
            o1_row.check_impersonation(Harness)

    def test_impersonation_yes_passes(self):
        class Harness:
            @staticmethod
            def k(*_args, **_kwargs):
                return _Result(0, "yes\n", "")

        o1_row.check_impersonation(Harness)

    def test_label_system_node_reuses_an_existing_pin(self):
        calls = []

        class Harness:
            @staticmethod
            def kj(*args):
                calls.append(args)
                return {"items": [{"metadata": {"name": "pinned"}}]}

            @staticmethod
            def k(*args, **_kwargs):
                raise AssertionError("must not relabel")

        self.assertEqual(o1_row.label_system_node(Harness), "pinned")

    def test_label_system_node_labels_first_system_node(self):
        labelled = []

        class Harness:
            @staticmethod
            def kj(*args):
                if f"{o1_row.NODE_LABEL}=true" in args:
                    return {"items": []}
                return {"items": [{"metadata": {"name": "sys-a"}}, {"metadata": {"name": "sys-b"}}]}

            @staticmethod
            def k(*args, **_kwargs):
                labelled.append(args)
                return _Result()

        self.assertEqual(o1_row.label_system_node(Harness), "sys-a")
        self.assertIn("sys-a", labelled[0])

    def test_label_system_node_errors_without_a_system_node(self):
        class Harness:
            @staticmethod
            def kj(*_args):
                return {"items": []}

        with self.assertRaises(RuntimeError):
            o1_row.label_system_node(Harness)


if __name__ == "__main__":
    unittest.main()
