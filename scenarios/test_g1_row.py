"""Pure G1 helper and judge tests. No cluster."""
import json
import unittest

import g1_row
import judge

T = judge.THRESHOLDS["kind"]["G1"]


def cond(status, reason="x"):
    return {"status": status, "reason": reason}


def fault(accepted, resolved, request, status_s=3.0, legit=200):
    return {"accepted": cond(accepted), "resolved_refs": cond(resolved), "request": {"status": request, "backend": None},
            "legit_during": {"status": legit}, "status_s": status_s}


def baseline_obs():
    return {"listener": {"from": "All", "hostname": None}, "legit_before": {"status": 200},
            "stray_host": fault("True", "True", 200), "cross_namespace": fault("True", "True", 200),
            "deleted_backend": fault("True", "False", 500), "unrouted_path": {"status": 404},
            "unknown_host": {"status": 404}, "legit_after": {"status": 200}}


def hardened_obs():
    return {"listener": {"from": "Selector", "hostname": "app.g1.lab"}, "legit_before": {"status": 200},
            "stray_host": fault("False", "True", 404), "cross_namespace": fault("False", "True", 404),
            "deleted_backend": fault("True", "False", 500), "unrouted_path": {"status": 404},
            "unknown_host": {"status": 404}, "legit_after": {"status": 200},
            "proxy_kill": {"requests": 30, "failures": 2}}


class Helpers(unittest.TestCase):
    def test_route_targets_the_platform_gateway_and_is_labelled_for_cleanup(self):
        route = g1_row.route_manifest("r", "ns", "h.lab", "/p", "svc")
        self.assertEqual(route["spec"]["parentRefs"], [{"name": "g1", "namespace": g1_row.PLATFORM_NS}])
        self.assertEqual(route["metadata"]["labels"], {"selfheal.lab/g1-fault": "true"})

    def test_parse_probe_never_invents_a_status(self):
        self.assertEqual(g1_row.parse_probe("garbage"), {"status": None, "backend": None, "error": "unparseable"})
        self.assertEqual(g1_row.parse_probe(json.dumps({"status": 200, "backend": "web"})),
                         {"status": 200, "backend": "web"})

    def test_condition_is_read_only_for_this_gateway(self):
        route = {"status": {"parents": [
            {"parentRef": {"name": "other"}, "conditions": [{"type": "Accepted", "status": "True", "reason": "a"}]},
            {"parentRef": {"name": "g1"}, "conditions": [{"type": "Accepted", "status": "False", "reason": "b"}]}]}}
        self.assertEqual(g1_row.route_condition(route, "Accepted"), {"status": "False", "reason": "b"})
        self.assertIsNone(g1_row.route_condition({}, "Accepted"))

    def test_probe_fixture_is_a_cluster_ip_client(self):
        pod = g1_row.client_manifest()
        self.assertFalse(pod["spec"]["automountServiceAccountToken"])
        self.assertEqual(pod["metadata"]["namespace"], g1_row.PLATFORM_NS)


class Judge(unittest.TestCase):
    def test_baseline_that_leaks_is_impact(self):
        ok, _why, impact, healed = judge.judge_g1(baseline_obs(), T)
        self.assertEqual((ok, impact, healed), (True, True, False))

    def test_baseline_where_the_probe_could_not_reach_is_an_error_not_no_impact(self):
        obs = baseline_obs()
        obs["stray_host"]["request"]["status"] = 404
        self.assertFalse(judge.judge_g1(obs, T)[0])

    def test_hardened_that_rejects_everything_is_healed(self):
        ok, _why, impact, healed = judge.judge_g1(hardened_obs(), T)
        self.assertEqual((ok, impact, healed), (True, True, True))

    def test_hardened_that_still_serves_the_stray_host_is_a_miss(self):
        obs = hardened_obs()
        obs["stray_host"] = fault("True", "True", 200)
        self.assertEqual(judge.judge_g1(obs, T)[2:], (True, False))

    def test_hardened_that_loses_the_legitimate_route_is_a_miss(self):
        obs = hardened_obs()
        obs["deleted_backend"]["legit_during"] = {"status": 503}
        self.assertEqual(judge.judge_g1(obs, T)[2:], (True, False))

    def test_missing_route_status_is_an_error_never_a_pass(self):
        obs = hardened_obs()
        obs["cross_namespace"]["accepted"] = None
        self.assertFalse(judge.judge_g1(obs, T)[0])

    def test_proxy_kill_is_informational_only(self):
        obs = hardened_obs()
        obs["proxy_kill"] = {"requests": 30, "failures": 30}
        self.assertTrue(judge.judge_g1(obs, T)[3])

    def test_missing_observation_is_an_error(self):
        obs = hardened_obs()
        del obs["unknown_host"]
        self.assertFalse(judge.judge_g1(obs, T)[0])


if __name__ == "__main__":
    unittest.main()
