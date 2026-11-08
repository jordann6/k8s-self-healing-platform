"""Unit tests for Q2 input validation and request summaries."""
import unittest

from app import percentile, summarize, validate_admin


class AdminValidation(unittest.TestCase):
    def test_accepts_demand_and_availability(self):
        self.assertEqual(validate_admin({"demand": 4, "available": False}),
                         {"demand": 4.0, "available": False})

    def test_rejects_bad_or_empty_updates(self):
        for value in ({}, {"demand": -1}, {"demand": 101}, {"demand": True}, {"available": "no"}, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_admin(value)


class RequestSummary(unittest.TestCase):
    def test_positive_samples_have_latency_and_no_failures(self):
        samples = [{"ok": True, "latency_ms": 10}, {"ok": True, "latency_ms": 30}]
        self.assertEqual(summarize(samples), {"samples": 2, "failures": 0,
                                              "failure_rate": 0.0, "p95_ms": 30.0})

    def test_failures_do_not_create_fake_latency(self):
        result = summarize([{"ok": False, "latency_ms": 100}, {"ok": True, "latency_ms": 20}])
        self.assertEqual(result["failure_rate"], 0.5)
        self.assertEqual(result["p95_ms"], 20.0)

    def test_no_data_is_not_success(self):
        self.assertEqual(summarize([]), {"samples": 0, "failures": 0,
                                         "failure_rate": None, "p95_ms": None})

    def test_percentile_uses_nearest_rank(self):
        self.assertEqual(percentile([1, 2, 3, 4, 5], 0.8), 4)


if __name__ == "__main__":
    unittest.main()
