"""Pure Q3 resource quantity and recommendation tests."""
import unittest

from judge import THRESHOLDS, judge_q3
from q3_row import (append_distinct, collection_window, container_recommendation, cpu_m,
                    memory_mib, metrics_server_info, percentile, sample_spacing_s)


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


class CloudContract(unittest.TestCase):
    """ADR-0020: the cloud port of the kind row."""

    def test_observation_thresholds_are_identical_on_every_target(self):
        keep = ("recommend_s", "min_samples", "cpu_pressure_m", "memory_pressure_mib",
                "cpu_ratio_min", "cpu_ratio_max", "memory_ratio_min", "memory_ratio_max", "restarts")
        for target in ("eks", "aks"):
            for key in keep:
                self.assertEqual(THRESHOLDS[target]["Q3"][key], THRESHOLDS["kind"]["Q3"][key], key)

    def test_windows_derive_from_the_metrics_resolution(self):
        kind = THRESHOLDS["kind"]["Q3"]
        aks = THRESHOLDS["aks"]["Q3"]
        self.assertEqual((collection_window(kind, "hardened"), collection_window(kind, "baseline")), (240, 120))
        self.assertEqual(THRESHOLDS["eks"]["Q3"]["sample_window_s"], 240)
        # (6 samples + 1) x 60s resolution
        self.assertEqual(aks["sample_window_s"], (aks["min_samples"] + 1) * 60)
        self.assertEqual((collection_window(aks, "hardened"), collection_window(aks, "baseline")), (420, 420))

    def test_hardened_window_never_ends_before_the_recommendation_clock(self):
        self.assertEqual(collection_window({"recommend_s": 240, "sample_window_s": 90, "baseline_window_s": 60},
                                           "hardened"), 240)

    def test_sample_spacing_is_the_median_gap_of_distinct_timestamps(self):
        stamps = ["2026-11-10T20:00:00Z", "2026-11-10T20:01:00Z", "2026-11-10T20:02:00Z",
                  "2026-11-10T20:03:10Z"]
        self.assertEqual(sample_spacing_s([{"metrics_timestamp": t} for t in stamps]), 60.0)
        self.assertIsNone(sample_spacing_s([{"metrics_timestamp": stamps[0]}]))
        self.assertIsNone(sample_spacing_s([{"metrics_timestamp": "garbage"}, {}]))

    def test_metrics_server_info_reads_replicas_and_resolution(self):
        class Harness:
            @staticmethod
            def kj(*_args):
                return {"spec": {"replicas": 2, "template": {"spec": {"containers": [
                    {"args": ["--kubelet-insecure-tls", "--metric-resolution=15s"]}]}}}}

        self.assertEqual(metrics_server_info(Harness),
                         {"replicas": 2, "resolution_args": ["--metric-resolution=15s"]})

    def hardened(self, recommendation_s, samples=6):
        return {
            "metrics_available": True, "vpa_present": True, "update_mode": "Off", "hpa_present": False,
            "initial_state": {"pod_count": 1, "requests": {"cpu_m": 50, "memory_mib": 32}},
            "final_state": {"pod_count": 1},
            "usage": {"samples": samples, "cpu_p90_m": 200, "memory_p90_mib": 96},
            "recommendation": {
                "lowerBound": {"cpu_m": 100, "memory_mib": 64},
                "target": {"cpu_m": 230, "memory_mib": 110, "cpu": "230m", "memory": "110Mi"},
                "upperBound": {"cpu_m": 600, "memory_mib": 256}},
            "recommendation_s": recommendation_s,
            "requests_unchanged": True, "pod_uid_unchanged": True, "restart_delta": 0,
        }

    def test_late_recommendation_is_a_miss_even_inside_the_aks_window(self):
        aks = THRESHOLDS["aks"]["Q3"]
        ok, _, _, healed = judge_q3(self.hardened(300.0), aks)
        self.assertTrue(ok)
        self.assertFalse(healed)

    def test_on_time_recommendation_heals_on_aks(self):
        ok, _, _, healed = judge_q3(self.hardened(130.0), THRESHOLDS["aks"]["Q3"])
        self.assertTrue(ok)
        self.assertTrue(healed)

    def test_too_few_samples_inside_the_window_is_an_error_not_a_pass(self):
        ok, why, _, healed = judge_q3(self.hardened(130.0, samples=4), THRESHOLDS["aks"]["Q3"])
        self.assertFalse(ok)
        self.assertFalse(healed)
        self.assertIn("too thin", why)
