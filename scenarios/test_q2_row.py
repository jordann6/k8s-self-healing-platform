"""Pure Q2 evidence helpers and failure detection."""
import unittest

from q2_row import (autoscaler_error, cpu_m, free_cpu_by_node, node_inventory, nodes_unchanged,
                    parse_keda_version, q2_nodes, replicas_fit, request_stats, scheduled_cpu_m, stable_one)


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


class KedaVersion(unittest.TestCase):
    def test_reads_chart_and_addon_tags(self):
        self.assertEqual(parse_keda_version("ghcr.io/kedacore/keda:2.21.0"), (2, 21, 0))
        self.assertEqual(parse_keda_version("mcr.microsoft.com/aks/keda:2.19.0-6"), (2, 19, 0))
        self.assertEqual(parse_keda_version("registry/keda:v2.17.2"), (2, 17, 2))

    def test_unreadable_image_is_none(self):
        self.assertIsNone(parse_keda_version(None))
        self.assertIsNone(parse_keda_version("keda@sha256:abc"))


def node(name, cpu="1900m", labels=None):
    return {"metadata": {"name": name, "labels": labels or {}}, "status": {"allocatable": {"cpu": cpu}}}


def pod(node_name, cpu):
    return {"spec": {"nodeName": node_name, "containers": [{"resources": {"requests": {"cpu": cpu}}}]},
            "status": {"phase": "Running"}}


class CloudNodes(unittest.TestCase):
    def test_cloud_uses_only_labelled_nodes(self):
        nodes = [node("a", labels={"selfheal.lab/q2": "true"}), node("sys")]
        self.assertEqual([n["metadata"]["name"] for n in q2_nodes(nodes, "eks")], ["a"])

    def test_kind_uses_every_node(self):
        self.assertEqual(len(q2_nodes([node("kind")], "kind")), 1)

    def test_free_cpu_is_allocatable_minus_requests(self):
        free = free_cpu_by_node([node("a")], [pod("a", "820m"), pod("b", "500m")])
        self.assertEqual(free["a"], {"allocatable_m": 1900, "requested_m": 820, "free_m": 1080})

    def test_three_more_replicas_fit_across_three_nodes(self):
        free = {n: {"free_m": 300} for n in "abc"}
        self.assertTrue(replicas_fit(free, 3, 250))

    def test_three_more_replicas_do_not_fit_when_nodes_are_full(self):
        free = {"a": {"free_m": 400}, "b": {"free_m": 200}, "c": {"free_m": 100}}
        self.assertFalse(replicas_fit(free, 3, 250))

    def test_one_node_can_host_several(self):
        self.assertTrue(replicas_fit({"a": {"free_m": 800}}, 3, 250))

    def test_inventory_comparison(self):
        a = {"nodes": ["x", "y"], "nodeclaims": []}
        self.assertTrue(nodes_unchanged(a, dict(a), dict(a)))
        self.assertFalse(nodes_unchanged(a, a, {"nodes": ["x", "y", "z"], "nodeclaims": []}))
        self.assertFalse(nodes_unchanged(a, {"nodes": ["x", "y"], "nodeclaims": ["nc-1"]}))


if __name__ == "__main__":
    unittest.main()
