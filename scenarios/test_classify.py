"""The runner's honesty rules, tested without a cluster.

Every case here is a way a naive harness would print a false "healed". The
judge must turn each of them into "error", "inconclusive" or "miss" instead.
Run: make test
"""
import unittest

from eks_rows import rotation_finished
from judge import THRESHOLDS, classify, overall, verdict
import run

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


def q1(**kw):
    o = {"published": 240, "n_target": 240, "distinct_done": 240, "expected_done": 240,
         "unexpected_done": 0, "duplicates": 7,
         "profile_start_replicas": 0, "max_replicas": 8, "end_replicas": 0, "scaled_up_s": 6.0,
         "drained_s": 40.0, "scaled_to_zero_s": 70.0, "killed_pod": "qworker-abc",
         "unacked_at_kill": 1, "workers_at_kill": 1, "metrics_source_ok": True}
    o.update(kw)
    return o


class Q1BusyWorkerAttribution(unittest.TestCase):
    """The kill victim is the pod the broker says holds an unacked message."""

    PODS = [{"metadata": {"name": f"qworker-{i}"}, "status": {"podIP": f"10.0.0.{i}"}} for i in (1, 2, 3)]

    def run_with(self, chans, conns, rc=0, split=True):
        import json
        import subprocess
        out = json.dumps(chans) + ("\n@@SPLIT@@\n" if split else "") + json.dumps(conns)
        orig = run.k
        run.k = lambda *a, **kw: subprocess.CompletedProcess(a, rc, out, "")
        try:
            return run.q1_busy_workers(self.PODS)
        finally:
            run.k = orig

    def test_picks_the_pod_that_holds_an_unacked_message_among_many(self):
        conns = [{"pid": f"<c{i}>", "peer_host": f"10.0.0.{i}"} for i in (1, 2, 3)]
        chans = [{"connection": "<c1>", "messages_unacknowledged": 0},
                 {"connection": "<c2>", "messages_unacknowledged": 1},
                 {"connection": "<c3>", "messages_unacknowledged": 0}]
        self.assertEqual(self.run_with(chans, conns), {"qworker-2": 1})

    def test_real_rabbitmq_shape_decodes_ipv4_mapped_peer_words(self):
        # Captured from RabbitMQ 3.13 on EKS: pids are "#PID<...>" strings and
        # peer_host is eight 16-bit words (eks-20261110-123247 crashed on this).
        pods = [{"metadata": {"name": "qworker-a"}, "status": {"podIP": "10.30.19.80"}}]
        conns = [{"pid": "#PID<13322.1083.0>", "peer_host": [0, 0, 0, 0, 0, 65535, 2590, 4944]},
                 {"pid": "#PID<13322.1217.0>", "peer_host": [0, 0, 0, 0, 0, 65535, 2590, 4105]}]
        chans = [{"connection": "#PID<13322.1083.0>", "messages_unacknowledged": 1},
                 {"connection": "#PID<13322.1217.0>", "messages_unacknowledged": 1}]
        import json
        import subprocess
        orig = run.k
        run.k = lambda *a, **kw: subprocess.CompletedProcess(
            a, 0, json.dumps(chans) + "\n@@SPLIT@@\n" + json.dumps(conns), "")
        try:
            self.assertEqual(run.q1_busy_workers(pods), {"qworker-a": 1})
        finally:
            run.k = orig

    def test_peer_ip_rejects_unrecognised_shapes(self):
        self.assertEqual(run.q1_peer_ip([0, 0, 0, 0, 0, 65535, 2590, 4944]), "10.30.19.80")
        self.assertEqual(run.q1_peer_ip("10.0.0.7"), "10.0.0.7")
        self.assertIsNone(run.q1_peer_ip([1, 2, 3]))
        self.assertIsNone(run.q1_peer_ip(None))

    def test_unknown_peer_such_as_the_producer_is_never_a_victim(self):
        conns = [{"pid": "<p>", "peer_host": "10.9.9.9"}]
        self.assertEqual(self.run_with([{"connection": "<p>", "messages_unacknowledged": 1}], conns), {})

    def test_telemetry_gap_is_empty_never_a_guess(self):
        self.assertEqual(self.run_with([], [], rc=1), {})
        self.assertEqual(self.run_with([], [], split=False), {})


class Q1QueueScaling(unittest.TestCase):
    def test_scale_from_zero_drain_and_return_heals(self):
        self.assertEqual(verdict("Q1", "hardened", q1(), "impact")["status"], "healed")

    def test_one_lost_message_is_a_miss(self):
        self.assertEqual(verdict("Q1", "hardened", q1(distinct_done=239), "impact")["status"], "miss")

    def test_wrong_ids_cannot_hide_missing_work(self):
        self.assertEqual(verdict("Q1", "hardened", q1(expected_done=239, unexpected_done=1), "impact")["status"], "miss")

    def test_short_backlog_is_error(self):
        v = verdict("Q1", "hardened", q1(published=200), "impact")
        self.assertEqual(v["status"], "error")
        self.assertIn("backlog under test was not established", v["reason"])

    def test_unreadable_done_queue_is_error(self):
        self.assertEqual(verdict("Q1", "hardened", q1(distinct_done=None), "impact")["status"], "error")

    def test_keda_metric_source_down_is_error_not_a_pass(self):
        v = verdict("Q1", "hardened", q1(metrics_source_ok=False), "impact")
        self.assertEqual(v["status"], "error")
        self.assertIn("cannot be attributed", v["reason"])

    def test_no_scale_out_is_a_miss(self):
        self.assertEqual(verdict("Q1", "hardened", q1(max_replicas=0, scaled_up_s=None), "impact")["status"], "miss")

    def test_kill_without_an_in_flight_message_is_a_miss(self):
        self.assertEqual(verdict("Q1", "hardened", q1(unacked_at_kill=0), "impact")["status"], "miss")
        self.assertEqual(verdict("Q1", "hardened", q1(killed_pod=None), "impact")["status"], "miss")

    def test_kill_of_a_broker_attributed_busy_worker_among_many_heals(self):
        self.assertEqual(verdict("Q1", "hardened", q1(workers_at_kill=3), "impact")["status"], "healed")

    def test_slow_drain_is_a_miss(self):
        self.assertEqual(verdict("Q1", "hardened", q1(drained_s=T["Q1"]["drain_s"] + 1), "impact")["status"], "miss")

    def test_never_back_to_zero_is_a_miss(self):
        self.assertEqual(verdict("Q1", "hardened", q1(end_replicas=2, scaled_to_zero_s=None), "impact")["status"], "miss")

    def test_zero_window_starts_after_drain(self):
        self.assertEqual(verdict("Q1", "hardened", q1(drained_s=110, scaled_to_zero_s=140), "impact")["status"], "healed")
        self.assertEqual(verdict("Q1", "hardened", q1(drained_s=10, scaled_to_zero_s=131), "impact")["status"], "miss")

    def test_baseline_never_idles_to_zero_is_impact(self):
        self.assertEqual(verdict("Q1", "baseline", q1(end_replicas=2, scaled_to_zero_s=None), None)["status"], "impact")


class Q1FocusedPreflight(unittest.TestCase):
    def test_q1_only_uses_queue_dependencies(self):
        old = run.TARGET
        try:
            run.TARGET = "kind"
            resources = set(run.precondition_resources(["Q1"]))
        finally:
            run.TARGET = old
        self.assertIn(("kube-system", "daemonset", "cilium"), resources)
        self.assertIn(("keda", "deploy", "keda-operator"), resources)
        self.assertIn(("argocd", "statefulset", "argocd-application-controller"), resources)
        self.assertIn(("gitops", "deploy", "gitserver"), resources)
        self.assertNotIn(("falco", "daemonset", "falco"), resources)
        self.assertNotIn(("monitoring", "deploy", "kps-operator"), resources)

    def test_eks_q1_uses_upstream_keda_namespace(self):
        old = run.TARGET
        try:
            run.TARGET = "eks"
            resources = set(run.precondition_resources(["Q1"]))
        finally:
            run.TARGET = old
        self.assertIn(("keda", "deploy", "keda-operator"), resources)
        self.assertNotIn(("kube-system", "deploy", "keda-operator"), resources)

    def test_aks_q1_uses_managed_keda_namespace(self):
        old = run.TARGET
        try:
            run.TARGET = "aks"
            resources = set(run.precondition_resources(["Q1"]))
        finally:
            run.TARGET = old
        self.assertIn(("kube-system", "deploy", "keda-operator"), resources)
        self.assertIn(("kube-system", "deploy", "keda-admission-webhooks"), resources)
        self.assertNotIn(("keda", "deploy", "keda-operator"), resources)

    def test_mixed_kind_run_keeps_full_preflight(self):
        old = run.TARGET
        try:
            run.TARGET = "kind"
            resources = set(run.precondition_resources(["A1", "Q1"]))
        finally:
            run.TARGET = old
        self.assertIn(("falco", "daemonset", "falco"), resources)
        self.assertIn(("monitoring", "deploy", "kps-operator"), resources)


class Q2FocusedPreflight(unittest.TestCase):
    def test_q2_only_uses_scaling_dependencies(self):
        old = run.TARGET
        try:
            run.TARGET = "kind"
            resources = set(run.precondition_resources(["Q2"]))
        finally:
            run.TARGET = old
        self.assertIn(("kube-system", "daemonset", "cilium"), resources)
        self.assertIn(("keda", "deploy", "keda-operator"), resources)
        self.assertIn(("argocd", "statefulset", "argocd-application-controller"), resources)
        self.assertIn(("gitops", "deploy", "gitserver"), resources)
        self.assertNotIn(("falco", "daemonset", "falco"), resources)
        self.assertNotIn(("monitoring", "deploy", "kps-operator"), resources)


class Q2CloudPreflight(unittest.TestCase):
    def resources(self, target):
        old = run.TARGET
        try:
            run.TARGET = target
            return set(run.precondition_resources(["Q2"]))
        finally:
            run.TARGET = old

    def test_eks_uses_upstream_keda_namespace(self):
        self.assertIn(("keda", "deploy", "keda-operator"), self.resources("eks"))

    def test_aks_uses_managed_keda_namespace(self):
        resources = self.resources("aks")
        self.assertIn(("kube-system", "deploy", "keda-operator"), resources)
        self.assertNotIn(("keda", "deploy", "keda-operator"), resources)

    def test_q2_is_focused_on_every_target(self):
        old = run.TARGET
        try:
            for target in ("kind", "eks", "aks"):
                run.TARGET = target
                self.assertTrue(run.q2_only(["Q2"]))
        finally:
            run.TARGET = old

    def test_both_clouds_list_q2(self):
        self.assertIn("Q2", run.ROWS_BY_TARGET["eks"])
        self.assertIn("Q2", run.ROWS_BY_TARGET["aks"])


class Q3FocusedPreflight(unittest.TestCase):
    def test_q3_only_uses_metrics_and_recommender_dependencies(self):
        old = run.TARGET
        try:
            run.TARGET = "kind"
            resources = set(run.precondition_resources(["Q3"]))
        finally:
            run.TARGET = old
        self.assertIn(("kube-system", "daemonset", "cilium"), resources)
        self.assertIn(("kube-system", "deploy", "metrics-server"), resources)
        self.assertIn(("vpa", "deploy", "vpa-vertical-pod-autoscaler-recommender"), resources)
        self.assertIn(("argocd", "statefulset", "argocd-application-controller"), resources)
        self.assertNotIn(("keda", "deploy", "keda-operator"), resources)
        self.assertNotIn(("falco", "daemonset", "falco"), resources)


class N1FocusedPreflight(unittest.TestCase):
    def test_n1_only_uses_cilium_hubble_and_gitops(self):
        old = run.TARGET
        try:
            run.TARGET = "kind"
            resources = set(run.precondition_resources(["N1"]))
        finally:
            run.TARGET = old
        self.assertIn(("kube-system", "daemonset", "cilium"), resources)
        self.assertIn(("kube-system", "deploy", "hubble-relay"), resources)
        self.assertIn(("argocd", "statefulset", "argocd-application-controller"), resources)
        self.assertNotIn(("keda", "deploy", "keda-operator"), resources)
        self.assertNotIn(("kube-system", "deploy", "metrics-server"), resources)


def q2(**kw):
    conditions = {
        "scaledobject_conditions": {"Ready": {"status": "False", "reason": "ScalerError"}},
        "hpa_conditions": {"ScalingActive": {"status": "False", "reason": "FailedGetExternalMetric"}},
    }
    limited = {
        "actual_replicas": 4, "ready_replicas": 4, "max_replicas": 4,
        "hpa_conditions": {"ScalingLimited": {"status": "True", "reason": "TooManyReplicas"}},
    }
    pending = {"desired_replicas": 4, "actual_replicas": 1, "pending": [
        {"pod": "q2-target-x", "reason": "Unschedulable",
         "message": "0/1 nodes are available: 1 Insufficient cpu."}]}
    o = {
        "metric_source_available_before": True,
        "metric_outage_observed": True,
        "conditions_present": True,
        "metric_error_state": conditions,
        "metric_failure_detected_s": 8.0,
        "fallback_condition_observed": True,
        "fallback_replicas_s": 18.0,
        "metric_recovered_s": 7.0,
        "outage_requests": {"samples": 300, "failures": 0, "failure_rate": 0.0, "p95_ms": 120.0},
        "ceiling": {"demand": 12, "state": limited},
        "capacity": {"pending_state": pending, "recovered_s": 12.0,
                     "recovery_requests": {"samples": 300, "failures": 0,
                                           "failure_rate": 0.0, "p95_ms": 110.0}},
        "scale_down_s": 35.0,
        "end_state": {"ready_replicas": 1},
    }
    o.update(kw)
    return o


class Q2CloudVerdict(unittest.TestCase):
    def stable(self, **kw):
        inv = {"nodes": ["a", "b", "c"], "nodeclaims": []}
        o = q2()
        o["capacity"]["node_stability"] = {"before": inv, "during": inv, "after": inv, "unchanged": True}
        o["capacity"].update(kw)
        return o

    def test_cloud_pass_with_unchanged_nodes(self):
        v = verdict("Q2", "hardened", self.stable(), "impact", target="eks")
        self.assertEqual(v["status"], "healed")

    def test_new_node_during_pending_window_is_an_error(self):
        o = self.stable()
        o["capacity"]["node_stability"]["unchanged"] = False
        self.assertEqual(verdict("Q2", "hardened", o, "impact", target="aks")["status"], "error")

    def test_missing_inventory_is_an_error_on_cloud(self):
        self.assertEqual(verdict("Q2", "hardened", q2(), "impact", target="eks")["status"], "error")

    def test_kind_needs_no_inventory(self):
        self.assertEqual(verdict("Q2", "hardened", q2(), "impact")["status"], "healed")

    def test_cloud_fallback_allows_sixty_seconds_but_not_more(self):
        self.assertEqual(verdict("Q2", "hardened", self.stable_with(fallback_replicas_s=59), "impact",
                                 target="eks")["status"], "healed")
        self.assertEqual(verdict("Q2", "hardened", self.stable_with(fallback_replicas_s=61), "impact",
                                 target="eks")["status"], "miss")

    def stable_with(self, **kw):
        o = self.stable()
        o.update(kw)
        return o


class Q2ScalingFailure(unittest.TestCase):
    def test_hardened_fallback_ceilings_and_recovery_heal(self):
        self.assertEqual(verdict("Q2", "hardened", q2(), "impact")["status"], "healed")

    def test_baseline_user_impact_is_measured(self):
        obs = q2(fallback_condition_observed=False, fallback_replicas_s=None,
                 outage_requests={"samples": 300, "failures": 90,
                                  "failure_rate": 0.3, "p95_ms": 700.0})
        self.assertEqual(verdict("Q2", "baseline", obs, None)["status"], "impact")

    def test_missing_metric_outage_is_an_error(self):
        self.assertEqual(verdict("Q2", "hardened", q2(metric_outage_observed=False), "impact")["status"], "error")

    def test_missing_autoscaler_conditions_is_an_error(self):
        self.assertEqual(verdict("Q2", "hardened", q2(metric_error_state={}), "impact")["status"], "error")

    def test_replica_max_without_scaling_limited_is_a_miss(self):
        obs = q2()
        obs["ceiling"] = {"demand": 12, "state": {"actual_replicas": 4, "max_replicas": 4,
                                                   "hpa_conditions": {"ScalingLimited": {"status": "False"}}}}
        self.assertEqual(verdict("Q2", "hardened", obs, "impact")["status"], "miss")

    def test_pending_without_scheduler_reason_is_an_error(self):
        obs = q2()
        obs["capacity"]["pending_state"]["pending"][0]["message"] = None
        self.assertEqual(verdict("Q2", "hardened", obs, "impact")["status"], "error")

    def test_no_pending_pod_is_an_error(self):
        obs = q2()
        obs["capacity"]["pending_state"]["pending"] = []
        self.assertEqual(verdict("Q2", "hardened", obs, "impact")["status"], "error")

    def test_thin_request_telemetry_is_an_error(self):
        obs = q2(outage_requests={"samples": 0, "failures": 0, "failure_rate": None, "p95_ms": None})
        self.assertEqual(verdict("Q2", "hardened", obs, "impact")["status"], "error")

    def test_slow_fallback_is_a_miss(self):
        self.assertEqual(verdict("Q2", "hardened", q2(fallback_replicas_s=31), "impact")["status"], "miss")

    def test_slow_capacity_recovery_is_a_miss(self):
        obs = q2()
        obs["capacity"]["recovered_s"] = 61
        self.assertEqual(verdict("Q2", "hardened", obs, "impact")["status"], "miss")

    def test_never_scales_down_is_a_miss(self):
        self.assertEqual(verdict("Q2", "hardened", q2(scale_down_s=None), "impact")["status"], "miss")


def q3(**kw):
    state = {"pod_count": 1, "pod": "q3-target-x", "uid": "uid-1", "restart_count": 0,
             "ready": True, "requests": {"cpu": "50m", "memory": "32Mi",
                                            "cpu_m": 50, "memory_mib": 32}}
    recommendation = {
        "container": "target",
        "lowerBound": {"cpu": "100m", "memory": "80Mi", "cpu_m": 100, "memory_mib": 80},
        "target": {"cpu": "190m", "memory": "100Mi", "cpu_m": 190, "memory_mib": 100},
        "uncappedTarget": {"cpu": "190m", "memory": "100Mi", "cpu_m": 190, "memory_mib": 100},
        "upperBound": {"cpu": "400m", "memory": "200Mi", "cpu_m": 400, "memory_mib": 200},
    }
    o = {"metrics_available": True, "vpa_present": True, "update_mode": "Off",
         "hpa_present": False, "initial_state": state, "final_state": dict(state),
         "usage": {"samples": 8, "cpu_p90_m": 200, "memory_p90_mib": 96,
                   "cpu_max_m": 210, "memory_max_mib": 97},
         "recommendation": recommendation, "recommendation_s": 90,
         "requests_unchanged": True, "pod_uid_unchanged": True, "restart_delta": 0}
    o.update(kw)
    return o


class Q3VerticalSizing(unittest.TestCase):
    def test_baseline_pressure_without_recommendation_is_impact(self):
        obs = q3(vpa_present=False, update_mode=None, recommendation=None, recommendation_s=None)
        self.assertEqual(verdict("Q3", "baseline", obs, None)["status"], "impact")

    def test_hardened_bounded_recommendation_heals_without_mutation(self):
        self.assertEqual(verdict("Q3", "hardened", q3(), "impact")["status"], "healed")

    def test_missing_metrics_is_an_error(self):
        self.assertEqual(verdict("Q3", "hardened", q3(metrics_available=False), "impact")["status"], "error")

    def test_thin_metrics_is_an_error(self):
        obs = q3()
        obs["usage"] = {**obs["usage"], "samples": 5}
        self.assertEqual(verdict("Q3", "hardened", obs, "impact")["status"], "error")

    def test_missing_recommendation_is_an_error(self):
        self.assertEqual(verdict("Q3", "hardened", q3(recommendation=None), "impact")["status"], "error")

    def test_hpa_confounds_the_result(self):
        self.assertEqual(verdict("Q3", "hardened", q3(hpa_present=True), "impact")["status"], "error")

    def test_workload_mutation_is_a_miss(self):
        self.assertEqual(verdict("Q3", "hardened", q3(pod_uid_unchanged=False), "impact")["status"], "miss")
        self.assertEqual(verdict("Q3", "hardened", q3(restart_delta=1), "impact")["status"], "miss")

    def test_unrelated_recommendation_is_a_miss(self):
        obs = q3()
        obs["recommendation"]["target"] = {"cpu": "500m", "memory": "220Mi",
                                               "cpu_m": 500, "memory_mib": 220}
        self.assertEqual(verdict("Q3", "hardened", obs, "impact")["status"], "miss")

    def test_slow_recommendation_is_a_miss(self):
        self.assertEqual(verdict("Q3", "hardened", q3(recommendation_s=241), "impact")["status"], "miss")


def n1(**kw):
    o = {
        "placement": {"cross_node": True},
        "capture": {"bytes": 5000, "vxlan_packets": 0, "wireguard_packets": 20,
                    "marker_visible": False,
                    "probe": {"samples": 20, "successes": 20, "failures": 0, "p95_ms": 20}},
        "denied": {"samples": 5, "successes": 0, "failures": 5, "p95_ms": None},
        "hubble_available": True,
        "policy_drops": 5,
        "wireguard_config": "true",
        "wireguard": {"client_interfaces": 1, "server_interfaces": 1,
                      "client_peer_count": 2, "server_peer_count": 2,
                      "peer_keys_match": True, "client_routes_server": True,
                      "server_routes_client": True, "client_handshake": True,
                      "server_handshake": True},
    }
    o.update(kw)
    return o


class N1WireGuard(unittest.TestCase):
    def test_visible_baseline_is_impact(self):
        obs = n1(wireguard_config="false", wireguard={},
                 capture={"bytes": 5000, "vxlan_packets": 20, "wireguard_packets": 0,
                          "marker_visible": True,
                          "probe": {"samples": 20, "successes": 20, "failures": 0, "p95_ms": 20}})
        self.assertEqual(verdict("N1", "baseline", obs, None)["status"], "impact")

    def test_encrypted_cross_node_path_heals(self):
        self.assertEqual(verdict("N1", "hardened", n1(), "impact")["status"], "healed")

    def test_missing_capture_is_error(self):
        obs = n1(capture={})
        self.assertEqual(verdict("N1", "hardened", obs, "impact")["status"], "error")

    def test_missing_hubble_is_error(self):
        self.assertEqual(verdict("N1", "hardened", n1(hubble_available=False), "impact")["status"], "error")

    def test_missing_config_telemetry_is_error(self):
        self.assertEqual(verdict("N1", "hardened", n1(wireguard_config=None), "impact")["status"], "error")

    def test_plaintext_marker_in_hardened_capture_is_miss(self):
        obs = n1()
        obs["capture"] = {**obs["capture"], "marker_visible": True}
        self.assertEqual(verdict("N1", "hardened", obs, "impact")["status"], "miss")

    def test_missing_peer_route_or_handshake_is_miss(self):
        for field in ("peer_keys_match", "client_routes_server", "server_routes_client",
                      "client_handshake", "server_handshake"):
            obs = n1()
            obs["wireguard"] = {**obs["wireguard"], field: False}
            with self.subTest(field=field):
                self.assertEqual(verdict("N1", "hardened", obs, "impact")["status"], "miss")

    def test_denial_without_policy_drop_is_miss(self):
        self.assertEqual(verdict("N1", "hardened", n1(policy_drops=0), "impact")["status"], "miss")


def p1_admitted(digest):
    return {"returncode": 0, "pod_created": True, "elapsed_s": 1.0, "ready_s": 4.0,
            "image_id": f"docker-pullable://selfheal-p1-registry:5000/p1/artifact@{digest}"}


def p1_rejected(reason="p1-trusted-signature image signature verification failed"):
    return {"returncode": 1, "pod_created": False, "elapsed_s": 1.0, "ready_s": None,
            "image_id": None, "reason": reason}


def p1(policy_present=True, **kw):
    digest = "sha256:" + "a" * 64
    admissions = {"trusted": p1_admitted(digest), "recovery": p1_admitted(digest),
                  "recovery_s": 5.0}
    admissions.update({case: p1_rejected() for case in
                       ("unsigned", "invalid", "untrusted", "tag_only", "verifier_outage")})
    o = {
        "digest": digest,
        "preparation": {name: True for name in (
            "one_digest", "trusted_valid", "unsigned_absent", "invalid_claim_rejected",
            "untrusted_rejected_by_trust_root", "untrusted_valid_with_own_key")},
        "policy_present": policy_present,
        "policy_ready": policy_present,
        "metrics_available": True,
        "policy_report_pass": True,
        "metric_delta": {"pass": 2, "fail": 5},
        "admissions": admissions,
    }
    o.update(kw)
    return o


class P1SignedImageAdmission(unittest.TestCase):
    def test_unprotected_baseline_is_impact(self):
        obs = p1(policy_present=False)
        obs["admissions"] = {case: p1_admitted(obs["digest"]) for case in
                             ("trusted", "unsigned", "invalid", "untrusted", "tag_only")}
        self.assertEqual(verdict("P1", "baseline", obs, None)["status"], "impact")

    def test_complete_hardened_proof_heals(self):
        self.assertEqual(verdict("P1", "hardened", p1(), "impact")["status"], "healed")

    def test_missing_metrics_is_error(self):
        self.assertEqual(verdict("P1", "hardened", p1(metrics_available=False), "impact")["status"], "error")

    def test_missing_policy_report_is_error(self):
        self.assertEqual(verdict("P1", "hardened", p1(policy_report_pass=False), "impact")["status"], "error")

    def test_malformed_digest_is_error(self):
        self.assertEqual(verdict("P1", "hardened", p1(digest="sha256:bad"), "impact")["status"], "error")

    def test_false_signature_preflight_is_error(self):
        obs = p1()
        obs["preparation"] = {**obs["preparation"], "invalid_claim_rejected": False}
        self.assertEqual(verdict("P1", "hardened", obs, "impact")["status"], "error")

    def test_admitted_unsigned_image_is_a_miss(self):
        obs = p1()
        obs["admissions"] = {**obs["admissions"], "unsigned": p1_admitted(obs["digest"])}
        self.assertEqual(verdict("P1", "hardened", obs, "impact")["status"], "miss")

    def test_wrong_runtime_digest_is_a_miss(self):
        obs = p1()
        obs["admissions"] = {**obs["admissions"], "trusted": p1_admitted("sha256:" + "b" * 64)}
        self.assertEqual(verdict("P1", "hardened", obs, "impact")["status"], "miss")


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
         "failed": 0, "dropped": 0, "users_seen": ["selfheal_app", "selfheal_app_clone"],
         "pre_t0": {"samples": 30, "failed": 0, "seconds": 30}}
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

    def test_probe_failing_before_t0_is_error_not_impact(self):
        # eks-run2 baseline: every login had failed for 30 minutes before T0.
        v = eks("S1b", "baseline", s1b(failed=239, pre_t0={"samples": 30, "failed": 30, "seconds": 30}))
        self.assertEqual(v["status"], "error")
        self.assertIn("not clean before T0", v["reason"])

    def test_no_pre_t0_measurement_is_error(self):
        v = eks("S1b", "hardened", s1b(pre_t0=None))
        self.assertEqual(v["status"], "error")


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
    def test_every_judge_has_a_hypothesis(self):
        from judge import HYPOTHESES, JUDGES
        self.assertEqual(set(HYPOTHESES), set(JUDGES))

    def test_every_eks_threshold_row_has_a_judge(self):
        # EKS runs the cloud superset. Later Phase 4 rows still remain kind only,
        # so JUDGES is a
        # superset of the eks rows, not equal to it.
        from judge import JUDGES
        self.assertTrue(set(THRESHOLDS["eks"]) <= set(JUDGES))

    def test_q1_has_cloud_parity_without_relaxed_thresholds(self):
        for target in ("kind", "eks", "aks"):
            self.assertEqual(THRESHOLDS[target]["Q1"], THRESHOLDS["kind"]["Q1"])

    def test_q3_is_kind_only(self):
        self.assertIn("Q3", THRESHOLDS["kind"])
        self.assertNotIn("Q3", THRESHOLDS["eks"])
        self.assertNotIn("Q3", THRESHOLDS["aks"])

    def test_q2_cloud_thresholds_differ_only_where_adr_0017_says(self):
        kind = THRESHOLDS["kind"]["Q2"]
        for target in ("eks", "aks"):
            cloud = THRESHOLDS[target]["Q2"]
            changed = {k for k in set(kind) | set(cloud) if kind.get(k) != cloud.get(k)}
            self.assertEqual(changed, {"fallback_s", "capacity_recover_s", "scale_down_s", "node_autoscaler"})
            self.assertEqual((cloud["fallback_s"], cloud["capacity_recover_s"], cloud["scale_down_s"]),
                             (60, 90, 120))
            for key in ("detect_s", "metric_recover_s", "p95_ms", "error_rate"):
                self.assertEqual(cloud[key], kind[key])

    def test_n1_is_kind_only_in_phase_three(self):
        self.assertIn("N1", THRESHOLDS["kind"])
        self.assertNotIn("N1", THRESHOLDS["eks"])
        self.assertNotIn("N1", THRESHOLDS["aks"])

    def test_a3_latency_bar_is_not_relaxed_on_eks(self):
        self.assertEqual(THRESHOLDS["eks"]["A3"]["p95_ms"], THRESHOLDS["kind"]["A3"]["p95_ms"])


class RotationFinished(unittest.TestCase):
    """S1b waits for each rotation with these labels (eks-run1 shape)."""

    def test_pending_left_on_the_new_current_counts_as_finished(self):
        st = {"713e": ["AWSPREVIOUS"], "ca4c": ["AWSCURRENT", "AWSPENDING"]}
        self.assertTrue(rotation_finished(st, before="713e"))

    def test_pending_on_another_version_is_still_rotating(self):
        st = {"713e": ["AWSCURRENT"], "ca4c": ["AWSPENDING"]}
        self.assertFalse(rotation_finished(st, before="713e"))

    def test_current_not_moved_is_not_finished(self):
        st = {"420f": ["AWSPREVIOUS"], "713e": ["AWSCURRENT"]}
        self.assertFalse(rotation_finished(st, before="713e"))

    def test_clean_finish(self):
        st = {"713e": ["AWSPREVIOUS"], "ca4c": ["AWSCURRENT"]}
        self.assertTrue(rotation_finished(st, before="713e"))


# --------------------------------------------------------------------------- #
# AKS (ADR-0003, "AKS thresholds"): one test per honesty rule
# --------------------------------------------------------------------------- #

def aks(row, profile, obs, baseline="impact"):
    return verdict(row, profile, obs, baseline if profile == "hardened" else None, target="aks")


def a4z(**kw):
    o = {"samples": 2700, "expected": 2750, "stopped": True, "power_state": "PowerState/stopped",
         "web_pods_on_node": 1, "not_ready_s": 48.0, "recovered_s": 330.0, "replacement_node": "aks-work-000004",
         "error_rate": 0.002, "gap_error_rate": 0.31, "repair_events": []}
    o.update(kw)
    return o


class A4AksNodeStop(unittest.TestCase):
    def test_full_recovery_heals(self):
        self.assertEqual(aks("A4", "hardened", a4z())["status"], "healed")

    def test_errors_before_notready_are_reported_not_judged(self):
        # D3: a third of requests failing before NotReady does not fail the row...
        v = aks("A4", "hardened", a4z(gap_error_rate=0.33))
        self.assertEqual(v["status"], "healed")
        self.assertIn("reported not judged: 33.00%", v["reason"])

    def test_errors_after_notready_are_judged(self):
        # ...but the same rate after NotReady is a miss.
        self.assertEqual(aks("A4", "hardened", a4z(error_rate=0.02))["status"], "miss")

    def test_never_notready_is_error(self):
        v = aks("A4", "hardened", a4z(not_ready_s=None))
        self.assertEqual(v["status"], "error")
        self.assertIn("no start", v["reason"])

    def test_unconfirmed_stop_is_error(self):
        v = aks("A4", "hardened", a4z(stopped=False, power_state="PowerState/running"))
        self.assertEqual(v["status"], "error")

    def test_stopping_a_node_without_web_pods_is_error(self):
        self.assertEqual(aks("A4", "hardened", a4z(web_pods_on_node=0))["status"], "error")

    def test_auto_repair_in_the_window_voids_the_row(self):
        v = aks("A4", "hardened", a4z(repair_events=["NodeRebootStart"]))
        self.assertEqual(v["status"], "error")
        self.assertIn("auto-repair", v["reason"])

    def test_auto_repair_voids_even_past_the_abort_line(self):
        ab = {"n": 20, "errors": 20, "error_rate": 1.0, "at_s": 60}
        v = aks("A4", "baseline", a4z(repair_events=["NodeRebootStart"], abort_evidence=ab))
        self.assertEqual(v["status"], "error")

    def test_no_new_node_is_a_miss(self):
        self.assertEqual(aks("A4", "hardened", a4z(replacement_node=None))["status"], "miss")

    def test_late_recovery_is_a_miss(self):
        self.assertEqual(aks("A4", "hardened", a4z(recovered_s=640.0))["status"], "miss")

    def test_colocated_baseline_outage_is_impact(self):
        self.assertEqual(aks("A4", "baseline", a4z(web_pods_on_node=3, error_rate=0.9, recovered_s=None,
                                                    replacement_node=None))["status"], "impact")

    def test_eks_keeps_judging_from_t0(self):
        # The D3 rule is AKS-only: on EKS the instance is terminated.
        self.assertEqual(THRESHOLDS["eks"]["A4"]["recover_s"], 300)


def s4z(**kw):
    o = {"injected": True, "reverted_s": 140.0, "access_after": "Deny",
         "description_after": "Closed by policy selfheal-close-open-ingress (row S4).",
         "deployment": {"name": "PolicyDeployment_1", "state": "Succeeded", "after_t0": True, "targets_rule": True},
         "noncompliant_s": None}
    o.update(kw)
    return o


class S4AksPolicy(unittest.TestCase):
    def test_policy_deployment_close_heals(self):
        self.assertEqual(aks("S4", "hardened", s4z())["status"], "healed")

    def test_rule_deleted_by_someone_else_is_error(self):
        v = aks("S4", "hardened", s4z(access_after=None, description_after=None, deployment=None))
        self.assertEqual(v["status"], "error")
        self.assertIn("not by the policy deployment", v["reason"])

    def test_closed_without_the_policy_marker_is_error(self):
        v = aks("S4", "hardened", s4z(description_after="selfheal S4 drift"))
        self.assertEqual(v["status"], "error")

    def test_deployment_from_before_t0_is_error(self):
        dep = {"name": "PolicyDeployment_0", "state": "Succeeded", "after_t0": False, "targets_rule": True}
        self.assertEqual(aks("S4", "hardened", s4z(deployment=dep))["status"], "error")

    def test_failed_deployment_is_error(self):
        dep = {"name": "PolicyDeployment_1", "state": "Failed", "after_t0": True, "targets_rule": True}
        self.assertEqual(aks("S4", "hardened", s4z(deployment=dep))["status"], "error")

    def test_deployment_for_another_rule_is_error(self):
        dep = {"name": "PolicyDeployment_1", "state": "Succeeded", "after_t0": True, "targets_rule": False}
        self.assertEqual(aks("S4", "hardened", s4z(deployment=dep))["status"], "error")

    def test_late_close_is_a_miss(self):
        self.assertEqual(aks("S4", "hardened", s4z(reverted_s=1300.0))["status"], "miss")

    def test_never_closed_is_impact_in_baseline(self):
        self.assertEqual(aks("S4", "baseline", s4z(reverted_s=None, deployment=None))["status"], "impact")

    def test_uninjected_is_error(self):
        self.assertEqual(aks("S4", "hardened", s4z(injected=False))["status"], "error")


def r2z(**kw):
    o = {"samples": 2000, "expected": 2000, "reachable_before": True, "reachable_after": False,
         "alert": {"display_name": "Communication with suspicious domain", "generated_s": 600.0,
                   "names_cluster": True, "names_pod": True, "sample": False},
         "isolated_s": 690.0, "evidence_s": 980.0, "pod_kept": True, "scale_down_disabled": True,
         "snapshot_state": "Succeeded", "capture_bytes": 4000, "replacement_on_quarantined_node": False,
         "error_rate": 0.001}
    o.update(kw)
    return o


class R2AksDefender(unittest.TestCase):
    def test_full_response_heals(self):
        self.assertEqual(aks("R2", "hardened", r2z())["status"], "healed")

    def test_clocks_run_from_the_alert_not_t0(self):
        # Isolated 90s after an alert at 3000s heals, though it is 3090s after T0.
        a = {**r2z()["alert"], "generated_s": 3000.0}
        self.assertEqual(aks("R2", "hardened", r2z(alert=a, isolated_s=3090.0, evidence_s=3400.0))["status"], "healed")

    def test_isolation_late_after_the_alert_is_a_miss(self):
        self.assertEqual(aks("R2", "hardened", r2z(isolated_s=950.0))["status"], "miss")

    def test_no_alert_is_error_in_both_profiles(self):
        self.assertEqual(aks("R2", "baseline", r2z(alert=None, isolated_s=None, evidence_s=None))["status"], "error")
        v = aks("R2", "hardened", r2z(alert=None))
        self.assertEqual(v["status"], "error")
        self.assertIn("never fired", v["reason"])

    def test_sample_alert_is_error(self):
        a = {**r2z()["alert"], "display_name": "[SAMPLE ALERT] Suspicious domain"}
        self.assertEqual(aks("R2", "hardened", r2z(alert=a))["status"], "error")

    def test_alert_not_naming_the_pod_is_error(self):
        a = {**r2z()["alert"], "names_pod": False}
        v = aks("R2", "hardened", r2z(alert=a))
        self.assertEqual(v["status"], "error")
        self.assertIn("attacked pod", v["reason"])

    def test_alert_from_before_the_attack_is_error(self):
        a = {**r2z()["alert"], "generated_s": -30.0}
        self.assertEqual(aks("R2", "hardened", r2z(alert=a))["status"], "error")

    def test_alert_past_the_hour_is_error(self):
        a = {**r2z()["alert"], "generated_s": 3700.0}
        self.assertEqual(aks("R2", "hardened", r2z(alert=a, isolated_s=3750.0, evidence_s=3900.0))["status"], "error")

    def test_isolated_but_still_reachable_is_error(self):
        v = aks("R2", "hardened", r2z(reachable_after=True))
        self.assertEqual(v["status"], "error")
        self.assertIn("still answered", v["reason"])

    def test_response_before_the_alert_is_clock_skew_error(self):
        v = aks("R2", "hardened", r2z(isolated_s=590.0))
        self.assertEqual(v["status"], "error")
        self.assertIn("clock skew", v["reason"])

    def test_replacement_on_the_quarantined_node_is_a_miss(self):
        v = aks("R2", "hardened", r2z(replacement_on_quarantined_node=True))
        self.assertEqual(v["status"], "miss")
        self.assertIn("quarantined node", v["reason"])

    def test_unknown_replacement_placement_is_not_a_pass(self):
        self.assertEqual(aks("R2", "hardened", r2z(replacement_on_quarantined_node=None))["status"], "miss")

    def test_failed_snapshot_is_a_miss(self):
        self.assertEqual(aks("R2", "hardened", r2z(snapshot_state=None))["status"], "miss")

    def test_no_capture_is_a_miss(self):
        self.assertEqual(aks("R2", "hardened", r2z(capture_bytes=0))["status"], "miss")

    def test_node_not_protected_from_scale_down_is_a_miss(self):
        self.assertEqual(aks("R2", "hardened", r2z(scale_down_disabled=False))["status"], "miss")

    def test_unisolated_baseline_is_impact(self):
        self.assertEqual(aks("R2", "baseline", r2z(isolated_s=None, evidence_s=None))["status"], "impact")


class Void(unittest.TestCase):
    def test_void_reason_is_error_before_the_abort_line(self):
        ab = {"n": 20, "errors": 20, "error_rate": 1.0, "at_s": 5}
        v = verdict("A1", "baseline", a1(void="the fault hit the wrong pod", abort_evidence=ab), None)
        self.assertEqual(v["status"], "error")
        self.assertIn("wrong pod", v["reason"])


class AksThresholds(unittest.TestCase):
    def test_every_aks_row_has_a_threshold_judge_and_hypothesis(self):
        from judge import JUDGES, hypothesis
        rows = {"A1", "A2", "A3", "A4", "A5", "S1", "S2", "S3", "S4", "R1", "Q1", "Q2", "O1", "R2"}
        self.assertEqual(set(THRESHOLDS["aks"]), rows)
        self.assertTrue(rows <= set(JUDGES))
        for r in rows:
            self.assertTrue(hypothesis(r, "aks"))

    def test_shared_rows_keep_their_numbers(self):
        for r in ("A1", "A2", "A3", "A5", "S1", "S2", "S3", "R1", "Q1", "O1"):
            self.assertEqual(THRESHOLDS["aks"][r], THRESHOLDS["kind"][r], r)

    def test_aks_hypotheses_name_the_aks_numbers(self):
        from judge import hypothesis
        self.assertIn("600s", hypothesis("A4", "aks"))
        self.assertIn("NotReady", hypothesis("A4", "aks"))
        self.assertIn("1200s", hypothesis("S4", "aks"))
        self.assertIn("300s of the alert", hypothesis("R2", "aks"))
        self.assertIn("300s", hypothesis("A4", "eks"))

    def test_q2_hypothesis_reads_the_target_thresholds(self):
        from judge import hypothesis
        kind = hypothesis("Q2", "kind")
        self.assertIn("fallback reaches three replicas within 30s", kind)
        self.assertIn("recovers within 60s", kind)
        self.assertIn("back to one within 90s", kind)
        for target in ("eks", "aks"):
            text = hypothesis("Q2", target)
            self.assertNotIn("{", text)
            self.assertIn("fallback reaches three replicas within 60s", text)
            self.assertIn("recovers within 90s", text)
            self.assertIn("back to one within 120s", text)


if __name__ == "__main__":
    unittest.main()
