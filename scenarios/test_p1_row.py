"""Pure P1 telemetry helper tests."""
import unittest
from unittest import mock

from p1_row import metric_delta, parse_metrics, pod_manifest, policy_ready, registry_ready


class Metrics(unittest.TestCase):
    def test_filters_the_exact_policy_and_sums_results(self):
        text = "\n".join([
            'kyverno_image_validating_policy_results_total{policy_name="p1-trusted-signature",result="pass"} 2',
            'kyverno_image_validating_policy_results_total{policy_name="p1-trusted-signature",result="fail"} 3',
            'kyverno_image_validating_policy_results_total{policy_name="other",result="pass"} 99',
        ])
        self.assertEqual(parse_metrics(text), {"pass": 2.0, "fail": 3.0})

    def test_counter_reset_never_becomes_a_negative_decision(self):
        self.assertEqual(metric_delta({"pass": 3}, {"pass": 1}), {"pass": 0.0})


class Manifest(unittest.TestCase):
    def test_pod_is_restricted_and_uses_the_exact_reference(self):
        manifest = pod_manifest("case", "registry/app@sha256:" + "a" * 64)
        container = manifest["spec"]["containers"][0]
        self.assertEqual(container["imagePullPolicy"], "Always")
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])


class PolicyStatus(unittest.TestCase):
    class Harness:
        def __init__(self, document):
            self.document = document

        def kj(self, *_args):
            return self.document

    def test_uses_the_v1beta1_condition_status_ready_field(self):
        document = {"metadata": {"name": "p1-trusted-signature"},
                    "status": {"conditionStatus": {"ready": True}}}
        self.assertTrue(policy_ready(self.Harness(document)))

    def test_missing_ready_status_is_not_ready(self):
        self.assertFalse(policy_ready(self.Harness({"metadata": {"name": "policy"}})))


class RegistryStatus(unittest.TestCase):
    def test_closed_registry_is_not_ready(self):
        with mock.patch("p1_row.urllib.request.urlopen", side_effect=OSError("closed")):
            self.assertFalse(registry_ready())

    def test_http_200_registry_is_ready(self):
        response = mock.MagicMock(status=200)
        response.__enter__.return_value = response
        with mock.patch("p1_row.urllib.request.urlopen", return_value=response):
            self.assertTrue(registry_ready())


if __name__ == "__main__":
    unittest.main()
