"""Pure Q3 resource quantity and recommendation tests."""
import unittest

from q3_row import append_distinct, container_recommendation, cpu_m, memory_mib, percentile


class Quantities(unittest.TestCase):
    def test_cpu_units(self):
        self.assertEqual(cpu_m("250m"), 250)
        self.assertEqual(cpu_m("500000u"), 500)
        self.assertEqual(cpu_m("500000000n"), 500)
        self.assertEqual(cpu_m("1"), 1000)

    def test_memory_units(self):
        self.assertEqual(memory_mib("64Mi"), 64)
        self.assertEqual(memory_mib("1Gi"), 1024)
        self.assertAlmostEqual(memory_mib("1048576"), 1)

    def test_nearest_rank_percentile(self):
        self.assertEqual(percentile([1, 2, 3, 4, 5], 0.9), 5)

    def test_repeated_metrics_snapshot_is_not_a_new_sample(self):
        samples = []
        first = {"metrics_timestamp": "t1", "window": "15s", "cpu": "200m", "memory": "96Mi"}
        self.assertTrue(append_distinct(samples, first))
        self.assertFalse(append_distinct(samples, {**first, "at": 2}))
        self.assertTrue(append_distinct(samples, {**first, "metrics_timestamp": "t2"}))
        self.assertEqual(len(samples), 2)


class Recommendations(unittest.TestCase):
    def test_extracts_container_and_normalizes_resources(self):
        vpa = {"status": {"recommendation": {"containerRecommendations": [{
            "containerName": "target",
            "lowerBound": {"cpu": "100m", "memory": "64Mi"},
            "target": {"cpu": "200m", "memory": "128Mi"},
            "uncappedTarget": {"cpu": "200m", "memory": "128Mi"},
            "upperBound": {"cpu": "400m", "memory": "256Mi"},
        }]}}}
        result = container_recommendation(vpa, "target")
        self.assertEqual(result["target"]["cpu_m"], 200)
        self.assertEqual(result["target"]["memory_mib"], 128)

    def test_missing_container_is_none(self):
        self.assertIsNone(container_recommendation({}, "target"))


if __name__ == "__main__":
    unittest.main()
