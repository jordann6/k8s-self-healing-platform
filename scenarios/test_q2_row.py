"""Pure Q2 evidence helpers and failure detection."""
import unittest

from q2_row import autoscaler_error, cpu_m, request_stats, scheduled_cpu_m, stable_one


class CpuQuantities(unittest.TestCase):
    def test_common_cpu_forms(self):
        self.assertEqual(cpu_m("250m"), 250)
        self.assertEqual(cpu_m("2"), 2000)
        self.assertEqual(cpu_m("500000u"), 500)
        self.assertEqual(cpu_m("500000000n"), 500)

    def test_scheduled_requests_use_larger_init_request(self):
        pod = {"spec": {"nodeName": "n1", "containers": [
            {"resources": {"requests": {"cpu": "100m"}}},
            {"resources": {"requests": {"cpu": "200m"}}}],
            "initContainers": [{"resources": {"requests": {"cpu": "500m"}}}]},
            "status": {"phase": "Running"}}
        self.assertEqual(scheduled_cpu_m([pod], "n1"), 500)


class ConditionEvidence(unittest.TestCase):
    def test_scaledobject_ready_false_is_an_error(self):
        self.assertTrue(autoscaler_error({"scaledobject_conditions": {"Ready": {"status": "False"}},
                                         "hpa_conditions": {}}))

    def test_hpa_failed_metric_is_an_error(self):
        self.assertTrue(autoscaler_error({"scaledobject_conditions": {"Ready": {"status": "True"}},
                                         "hpa_conditions": {"ScalingActive": {
                                             "status": "False", "reason": "FailedGetExternalMetric"}}}))

    def test_healthy_conditions_are_not_an_error(self):
        self.assertFalse(autoscaler_error({"scaledobject_conditions": {"Ready": {"status": "True"}},
                                          "hpa_conditions": {"ScalingActive": {"status": "True"}}}))

    def test_stable_one_rejects_terminating_replica(self):
        state = {"desired_replicas": 1, "actual_replicas": 1, "ready_replicas": 1,
                 "available_replicas": 1, "target_pod_count": 2, "terminating_replicas": 1,
                 "pending": []}
        self.assertFalse(stable_one(state))
        state.update(target_pod_count=1, terminating_replicas=0)
        self.assertTrue(stable_one(state))


class RequestWindows(unittest.TestCase):
    def test_window_excludes_outside_samples(self):
        samples = [{"started": 1, "ok": True, "latency_ms": 10},
                   {"started": 2, "ok": False, "latency_ms": 20},
                   {"started": 3, "ok": True, "latency_ms": 30}]
        self.assertEqual(request_stats(samples, 1.5, 2.5), {
            "samples": 1, "failures": 1, "failure_rate": 1.0, "p95_ms": None,
            "since": 1.5, "until": 2.5})

    def test_empty_window_is_not_zero_failure(self):
        result = request_stats([], 1, 2)
        self.assertIsNone(result["failure_rate"])
        self.assertIsNone(result["p95_ms"])


if __name__ == "__main__":
    unittest.main()
