"""The AKS rows' own evidence rules, tested without Azure.

judge.py decides; these are the pieces of aks_rows.py that turn raw Azure
and Kubernetes answers into the facts the judge reads. Each one is a place a
row could otherwise claim more than it saw. Run: make test
"""
import unittest

import aks_rows as A

CLUSTER = "/subscriptions/x/resourcegroups/rg-selfheal-lab/providers/Microsoft.ContainerService/managedClusters/selfheal"
T0 = 1_000_000.0


def alert(**props):
    p = {"alertDisplayName": "Communication with suspicious domain", "timeGeneratedUtc": "1970-01-12T13:50:00Z",
         "resourceIdentifiers": [{"azureResourceId": CLUSTER, "type": "AzureResource"}],
         "entities": [{"type": "K8s-pod", "name": "web-7d9c-abcde"}]}
    p.update(props)
    return {"id": "/subscriptions/x/resourceGroups/rg/providers/Microsoft.Security/locations/eastus2/alerts/a1",
            "name": "a1", "properties": p}


class AlertFacts(unittest.TestCase):
    def test_names_cluster_and_pod_case_insensitively(self):
        f = A.alert_facts(alert(), CLUSTER.upper(), "WEB-7d9c-abcde", T0)
        self.assertTrue(f["names_cluster"])
        self.assertTrue(f["names_pod"])

    def test_alert_on_another_pod_does_not_name_ours(self):
        f = A.alert_facts(alert(entities=[{"type": "K8s-pod", "name": "web-other"}]), CLUSTER, "web-7d9c-abcde", T0)
        self.assertFalse(f["names_pod"])

    def test_alert_on_another_cluster_does_not_name_ours(self):
        f = A.alert_facts(alert(resourceIdentifiers=[{"azureResourceId": CLUSTER + "2x"}]), CLUSTER + "1", "web-7d9c-abcde", T0)
        self.assertFalse(f["names_cluster"])

    def test_sample_prefix_is_flagged(self):
        f = A.alert_facts(alert(alertDisplayName="[SAMPLE ALERT] Suspicious domain"), CLUSTER, "web-7d9c-abcde", T0)
        self.assertTrue(f["sample"])

    def test_generated_time_is_relative_to_t0(self):
        t0 = A.epoch("2026-11-05T10:00:00Z")
        f = A.alert_facts(alert(timeGeneratedUtc="2026-11-05T10:12:30.1234567Z"), CLUSTER, "web-7d9c-abcde", t0)
        self.assertAlmostEqual(f["generated_s"], 750.1, places=1)


class Epoch(unittest.TestCase):
    def test_seven_fractional_digits(self):
        self.assertAlmostEqual(A.epoch("2026-11-05T10:00:00.1234567Z") % 1, 0.123456, places=5)

    def test_seven_digits_with_an_offset_keep_the_offset(self):
        # The parser once swallowed the offset's digits into the fraction and
        # read the time as local: 6 hours off on a CST laptop.
        self.assertEqual(A.epoch("2026-11-05T10:00:00.1234567+00:00") // 1, A.epoch("2026-11-05T10:00:00Z"))

    def test_naive_time_is_utc_not_local(self):
        self.assertEqual(A.epoch("2026-11-05T10:00:00"), A.epoch("2026-11-05T10:00:00Z"))

    def test_offset_form(self):
        self.assertEqual(A.epoch("2026-11-05T10:00:00+00:00"), A.epoch("2026-11-05T10:00:00Z"))


class CaptureBytes(unittest.TestCase):
    def test_counts_run_command_messages(self):
        m = {"capture": {"result": [{"code": "ProvisioningState/succeeded", "message": "abc"},
                                    {"code": "x", "message": "de"}]}}
        self.assertEqual(A.capture_bytes(m), 5)

    def test_no_manifest_is_zero(self):
        self.assertEqual(A.capture_bytes({}), 0)

    def test_empty_messages_are_zero(self):
        self.assertEqual(A.capture_bytes({"capture": {"result": [{"code": "x", "message": ""}]}}), 0)


class OpenRule(unittest.TestCase):
    def test_allow_from_anywhere_is_open(self):
        self.assertTrue(A._open({"access": "Allow", "sourceAddressPrefix": "0.0.0.0/0"}))
        self.assertTrue(A._open({"access": "Allow", "sourceAddressPrefix": None, "sourceAddressPrefixes": ["Internet"]}))

    def test_deny_is_closed(self):
        self.assertFalse(A._open({"access": "Deny", "sourceAddressPrefix": "*"}))

    def test_deleted_is_closed(self):
        self.assertFalse(A._open(None))

    def test_allow_from_a_private_range_is_closed(self):
        self.assertFalse(A._open({"access": "Allow", "sourceAddressPrefix": "10.42.0.0/16"}))


class FakeH:
    def __init__(self, node):
        self.node = node

    def kj(self, *args):
        return self.node


def node(status, transition, provider=""):
    return {"status": {"conditions": [{"type": "Ready", "status": status, "lastTransitionTime": transition}]},
            "spec": {"providerID": provider}}


class NotReadySince(unittest.TestCase):
    def test_earlier_of_observation_and_transition(self):
        t0 = A.epoch("2026-11-05T10:00:00Z")
        h = FakeH(node("Unknown", "2026-11-05T10:00:48Z"))
        self.assertEqual(A.not_ready_since(h, "n", t0, t0 + 55), t0 + 48)

    def test_never_before_t0(self):
        # A transition stamped before T0 cannot open the judged window early.
        t0 = A.epoch("2026-11-05T10:00:00Z")
        h = FakeH(node("False", "2026-11-05T09:59:00Z"))
        self.assertEqual(A.not_ready_since(h, "n", t0, t0 + 50), t0)

    def test_ready_node_has_no_not_ready_time(self):
        h = FakeH(node("True", "2026-11-05T09:00:00Z"))
        self.assertIsNone(A.not_ready_since(h, "n", 0.0, 10.0))


class VmssOf(unittest.TestCase):
    def test_parses_uniform_scale_set_provider_id(self):
        pid = ("azure:///subscriptions/x/resourceGroups/rg-selfheal-lab-nodes/providers/Microsoft.Compute/"
               "virtualMachineScaleSets/aks-work-12345678-vmss/virtualMachines/3")
        self.assertEqual(A.vmss_of(FakeH(node("True", "", pid)), "n"), ("aks-work-12345678-vmss", "3"))

    def test_refuses_anything_else(self):
        with self.assertRaises(RuntimeError):
            A.vmss_of(FakeH(node("True", "", "aws:///us-east-1a/i-0abc")), "n")


class SteadyCordonedNodes(unittest.TestCase):
    """aks-run1: hardened A4 started its instance again after the autoscaler
    had already added a replacement, the autoscaler then removed the empty
    node, and steady took its cordon for an R2 leftover and asked AKS to
    delete a machine that was already gone. Every later row errored."""

    def run_steady(self, taints):
        calls = []

        class H:
            def log(self, msg):
                pass

            def wait_for(self, *a, **k):
                pass

        saved = A.outputs, A.az, A.workload_nodes, A.eradicate_node
        A.outputs = lambda: {"resource_group": "rg", "node_nsg": "nsg"}
        A.az = lambda *a, **k: None
        A.workload_nodes = lambda h: [{"name": "aks-work-0", "ready": True, "unschedulable": True,
                                       "annotations": {}, "taints": taints}]
        A.eradicate_node = lambda h, n: calls.append(n)
        try:
            A.steady(H())
        finally:
            A.outputs, A.az, A.workload_nodes, A.eradicate_node = saved
        return calls

    def test_autoscaler_scale_down_is_left_alone(self):
        self.assertEqual(self.run_steady([A.CA_DELETING_TAINT, "node.kubernetes.io/unschedulable"]), [])

    def test_r2_cordon_is_still_replaced(self):
        self.assertEqual(self.run_steady(["node.kubernetes.io/unschedulable"]), ["aks-work-0"])


if __name__ == "__main__":
    unittest.main()
