"""Pure P1 telemetry helper tests."""
import unittest
from unittest import mock

import json
from pathlib import Path

import p1_row
from p1_row import (OUTAGE_POLICY, cloud_outage, metric_delta, outage_policy_manifest, parse_metrics,
                    pod_manifest, policy_ready, registry_ready, state_dir, summarize_drop)


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


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class CloudOutage(unittest.TestCase):
    """ADR-0021: the outage is a network policy, always removed, and attributed through Hubble."""

    class Harness:
        TARGET = "eks"
        ROOT = Path("/repo")

        def __init__(self, pods=("kyverno-admission-controller-abc",), drops=None, create_raises=False):
            self.pods, self.drops, self.create_raises = pods, drops or [], create_raises
            self.calls = []
            self.clock = 100.0

        def now(self):
            self.clock += 1
            return self.clock

        def kj(self, *args):
            self.calls.append(("kj", args))
            return {"items": [{"metadata": {"name": name}} for name in self.pods]}

        def k(self, *args, input=None, **_kwargs):
            self.calls.append(("k", args))
            if args and args[0] == "create":
                if self.create_raises:
                    raise RuntimeError("api went away")
                return _Result(1, "", 'denied by policy p1-trusted-signature: image signature verification failed')
            return _Result()

        def hubble_policy_drops(self, pod, since, namespace=None):
            self.calls.append(("hubble", (pod, namespace)))
            return self.drops

    def deleted(self, harness):
        return any(kind == "k" and "ciliumnetworkpolicy" in args and "delete" in args
                   for kind, args in harness.calls)

    def test_manifest_selects_only_the_admission_controller_and_allows_api_and_dns(self):
        manifest = outage_policy_manifest()
        self.assertEqual(manifest["metadata"]["name"], OUTAGE_POLICY)
        self.assertEqual(manifest["metadata"]["namespace"], "kyverno")
        self.assertEqual(manifest["spec"]["endpointSelector"]["matchLabels"]["app.kubernetes.io/component"],
                         "admission-controller")
        egress = manifest["spec"]["egress"]
        self.assertEqual(egress[0], {"toEntities": ["kube-apiserver"]})
        self.assertEqual(egress[1]["toPorts"][0]["ports"], [{"port": "53", "protocol": "ANY"}])
        self.assertEqual(len(egress), 2)
        self.assertNotIn("toFQDNs", json.dumps(manifest))

    def test_denied_outage_returns_drops_and_removes_the_policy(self):
        flow = {"traffic_direction": "EGRESS", "IP": {"destination": "10.0.1.9"},
                "l4": {"TCP": {"destination_port": 443}}, "drop_reason_desc": "POLICY_DENIED"}
        harness = self.Harness(drops=[flow])
        with mock.patch.object(p1_row.time, "sleep"):
            result = cloud_outage(harness, Path("."), "hardened", "reg/p1-recovery@sha256:" + "a" * 64)
        self.assertNotEqual(result["record"]["returncode"], 0)
        self.assertFalse(result["record"]["pod_created"])
        self.assertEqual(result["outage"]["policy_drops"], 1)
        self.assertEqual(result["outage"]["drop_samples"][0],
                         {"direction": "EGRESS", "destination": "10.0.1.9", "port": 443, "reason": "POLICY_DENIED"})
        self.assertEqual(result["outage"]["settle_s"], p1_row.OUTAGE_SETTLE_S)
        self.assertTrue(self.deleted(harness))
        self.assertGreaterEqual(result["restored"], 100.0)

    def test_policy_is_removed_even_when_the_request_blows_up(self):
        harness = self.Harness(create_raises=True)
        with mock.patch.object(p1_row.time, "sleep"):
            with self.assertRaises(RuntimeError):
                cloud_outage(harness, Path("."), "hardened", "reg/img@sha256:" + "a" * 64)
        self.assertTrue(self.deleted(harness))

    def test_no_admission_controller_is_an_error_before_any_policy_is_applied(self):
        harness = self.Harness(pods=())
        with self.assertRaises(RuntimeError):
            cloud_outage(harness, Path("."), "hardened", "img")
        self.assertFalse(any(kind == "k" and args[:1] == ("apply",) for kind, args in harness.calls))

    def test_hubble_is_queried_in_the_kyverno_namespace(self):
        harness = self.Harness()
        with mock.patch.object(p1_row.time, "sleep"):
            cloud_outage(harness, Path("."), "hardened", "reg/img@sha256:" + "a" * 64)
        self.assertIn(("hubble", ("kyverno-admission-controller-abc", "kyverno")), harness.calls)

    def test_fixture_paths_are_per_target(self):
        for target, expected in (("kind", "/repo/.p1"), ("eks", "/repo/.p1/eks"), ("aks", "/repo/.p1/aks")):
            harness = self.Harness()
            harness.TARGET = target
            self.assertEqual(str(state_dir(harness)), expected)

    def test_summarize_drop_tolerates_a_flow_with_no_l4(self):
        self.assertEqual(summarize_drop({"drop_reason_desc": "POLICY_DENIED"}),
                         {"direction": None, "destination": None, "port": None, "reason": "POLICY_DENIED"})


if __name__ == "__main__":
    unittest.main()
