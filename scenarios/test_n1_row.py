"""Pure N1 evidence helper tests."""
import unittest

from pathlib import Path

from n1_row import (SLOT_LABEL, capture_facts, capture_filter, exact_policy_drops, label_slot_nodes,
                    parse_physical_interfaces, plain_packets, request_summary, underlay_interfaces,
                    value_files, wireguard_facts)


class Capture(unittest.TestCase):
    def test_packet_types_and_marker(self):
        output = "node.40000 > peer.8472: VXLAN n1-marker\nnode.40001 > peer.51871: UDP"
        self.assertEqual(capture_facts(output, "n1-marker"), {
            "vxlan_packets": 1, "vxlan_packets_total": 1,
            "wireguard_packets": 1, "plain_packets": 0, "marker_visible": True,
            "bytes": len(output.encode())})

    def test_vxlan_is_attributed_to_the_inner_pod_pair(self):
        output = "\n".join([
            "IP worker-a.50000 > worker-b.8472: VXLAN",
            "IP 10.0.1.2.40000 > 10.0.2.3.8084: payload",
            "IP worker-a.50001 > worker-b.8472: VXLAN",
            "IP 10.0.9.2.40000 > 10.0.2.3.6379: background",
        ])
        facts = capture_facts(output, "marker", "10.0.1.2", "10.0.2.3")
        self.assertEqual(facts["vxlan_packets"], 1)
        self.assertEqual(facts["vxlan_packets_total"], 2)


class Requests(unittest.TestCase):
    def test_summary_counts_only_explicit_success(self):
        result = {"samples": [{"ok": True, "latency_ms": 2},
                              {"ok": False, "latency_ms": 5}]}
        self.assertEqual(request_summary(result)["failures"], 1)


class WireGuard(unittest.TestCase):
    def test_matching_peers_routes_and_handshakes(self):
        statuses = {
            "client": {"interfaces": [{"public-key": "a", "peers": [{
                "public-key": "b", "allowed-ips": ["10.1.2.3/32"],
                "last-handshake-time": "2026-11-08T20:00:00Z"}]}]},
            "server": {"interfaces": [{"public-key": "b", "peers": [{
                "public-key": "a", "allowed-ips": ["10.1.1.2/32"],
                "last-handshake-time": "2026-11-08T20:00:01Z"}]}]},
        }
        facts = wireguard_facts(statuses, "client", "server", "10.1.1.2", "10.1.2.3")
        self.assertTrue(all(facts.values()))

    def test_route_cidr_can_cover_remote_worker(self):
        statuses = {
            "client": {"interfaces": [{"public-key": "a", "peers": [{
                "public-key": "b", "allowed-ips": ["10.1.2.0/24"],
                "last-handshake-time": "2026-11-08T20:00:00Z"}]}]},
            "server": {"interfaces": [{"public-key": "b", "peers": [{
                "public-key": "a", "allowed-ips": ["10.1.1.0/24"],
                "last-handshake-time": "2026-11-08T20:00:01Z"}]}]},
        }
        facts = wireguard_facts(statuses, "client", "server", "10.1.1.2", "10.1.2.3")
        self.assertTrue(facts["client_routes_server"])
        self.assertTrue(facts["server_routes_client"])


class PolicyDrops(unittest.TestCase):
    def test_only_exact_policy_drop_counts(self):
        good = {"source": {"pod_name": "denied"}, "destination": {"pod_name": "server"},
                "drop_reason_desc": "POLICY_DENIED"}
        unrelated = {**good, "source": {"pod_name": "other"}}
        self.assertEqual(exact_policy_drops([good, unrelated], "denied", "server"), [good])


class BaselinePolicy(unittest.TestCase):
    def test_allow_all_baseline_policy_excludes_the_n1_pods(self):
        """Cilium policies are additive, so a baseline allow-all over the namespace overrides
        n1-server-ingress and the denied pod reaches the server (aks-n1 and four EKS runs)."""
        import yaml
        root = Path(__file__).resolve().parent.parent
        policy = yaml.safe_load((root / "gitops/profiles/baseline/policy.yaml").read_text())
        selector = policy["spec"]["endpointSelector"]
        excluded = {"n1-client", "n1-denied", "n1-server"}
        rules = selector.get("matchExpressions") or []
        self.assertTrue(any(r["key"] == "app" and r["operator"] == "NotIn"
                            and excluded <= set(r["values"]) for r in rules), selector)


class _Run:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class NativeDatapath(unittest.TestCase):
    """ADR-0023: EKS has no VXLAN, so plaintext pod traffic on the physical NICs is the evidence."""

    OUTPUT = "\n".join([
        "### interface ens5",
        "IP 10.0.1.5.43210 > 10.0.2.7.8084: Flags [P.], length 90",
        "GET /n1-marker HTTP/1.1",
        "IP 10.0.2.7.8084 > 10.0.1.5.43210: Flags [P.], length 60",
        "IP 10.0.1.5.43211 > 10.0.2.7.8084: Flags [S], length 0",
        "IP 10.0.9.9.43210 > 10.0.2.7.8084: Flags [S], length 0",
        "IP 10.0.1.5.43210 > 10.0.2.70.8084: Flags [S], length 0",
    ])

    def test_plaintext_is_counted_in_both_directions_and_only_for_the_pod_pair(self):
        self.assertEqual(plain_packets(self.OUTPUT, "10.0.1.5", "10.0.2.7"), 3)

    def test_a_similar_address_is_not_the_pod_pair(self):
        self.assertEqual(plain_packets(self.OUTPUT, "10.0.1.5", "10.0.2.70"), 1)

    def test_no_addresses_means_nothing_to_attribute(self):
        self.assertEqual(plain_packets(self.OUTPUT, None, None), 0)

    def test_capture_facts_reports_plaintext_and_the_marker(self):
        facts = capture_facts(self.OUTPUT, "n1-marker", "10.0.1.5", "10.0.2.7")
        self.assertEqual(facts["plain_packets"], 3)
        self.assertTrue(facts["marker_visible"])
        self.assertEqual(facts["wireguard_packets"], 0)

    def test_hardened_output_with_only_wireguard_has_no_plaintext(self):
        output = "### interface ens5\nIP 10.0.0.4.51871 > 10.0.0.9.51871: UDP, length 148\n" * 6
        facts = capture_facts(output, "n1-marker", "10.0.1.5", "10.0.2.7")
        # The existing counter matches both ports of a line, so a packet can count twice. The row
        # needs a floor, not an exact count.
        self.assertGreaterEqual(facts["wireguard_packets"], 6)
        self.assertEqual((facts["plain_packets"], facts["marker_visible"]), (0, False))

    def test_native_filter_watches_wireguard_between_nodes_and_tcp_between_pods(self):
        expression = " ".join(capture_filter("native", "10.0.1.5", "10.0.2.7", "10.0.0.4", "10.0.0.9"))
        self.assertIn("udp port 51871", expression)
        self.assertIn("host 10.0.0.4 and host 10.0.0.9", expression)
        self.assertIn("tcp and host 10.0.1.5 and host 10.0.2.7", expression)
        self.assertNotIn("8472", expression)

    def test_tunnel_filter_is_the_kind_filter(self):
        expression = " ".join(capture_filter("tunnel", "10.0.1.5", "10.0.2.7", "10.0.0.4", "10.0.0.9"))
        self.assertIn("udp port 8472", expression)
        self.assertIn("udp port 51871", expression)
        self.assertNotIn("10.0.1.5", expression)

    def test_route_check_takes_the_pod_addresses_on_a_native_datapath(self):
        statuses = {
            "client": {"interfaces": [{"public-key": "a", "peers": [{
                "public-key": "b", "allowed-ips": ["10.0.2.7/32"],
                "last-handshake-time": "2026-11-10T20:00:00Z"}]}]},
            "server": {"interfaces": [{"public-key": "b", "peers": [{
                "public-key": "a", "allowed-ips": ["10.0.1.5/32"],
                "last-handshake-time": "2026-11-10T20:00:01Z"}]}]},
        }
        pods = wireguard_facts(statuses, "client", "server", "10.0.1.5", "10.0.2.7")
        self.assertTrue(pods["client_routes_server"] and pods["server_routes_client"])
        nodes = wireguard_facts(statuses, "client", "server", "10.0.0.4", "10.0.0.9")
        self.assertFalse(nodes["client_routes_server"])


class Interfaces(unittest.TestCase):
    SYSFS = "\n".join([
        "ens5 /sys/devices/pci0000:00/0000:00:05.0/net/ens5",
        "ens6 /sys/devices/pci0000:00/0000:00:06.0/net/ens6",
        "lo /sys/devices/virtual/net/lo",
        "cilium_host /sys/devices/virtual/net/cilium_host",
        "cilium_wg0 /sys/devices/virtual/net/cilium_wg0",
        "lxc1a2b3c /sys/devices/virtual/net/lxc1a2b3c",
        "eth0 /sys/devices/LNXSYSTM:00/LNXSYBUS:00/VMBUS:01/net/eth0",
    ])

    def test_virtual_devices_are_excluded_because_they_carry_decrypted_traffic(self):
        self.assertEqual(parse_physical_interfaces(self.SYSFS), ["ens5", "ens6", "eth0"])

    def test_garbage_lines_are_ignored(self):
        self.assertEqual(parse_physical_interfaces("\nnot a pair here at all\nens5 /sys/x/net/ens5\n"), ["ens5"])

    def test_tunnel_datapaths_capture_eth0_without_asking_the_node(self):
        class H:
            @staticmethod
            def k(*_a, **_k):
                raise AssertionError("must not exec")
        self.assertEqual(underlay_interfaces(H, "ns", "pod", "tunnel"), ["eth0"])

    def test_native_discovery_runs_in_the_capture_pod_and_returns_the_physical_nics(self):
        calls = []

        class H:
            @staticmethod
            def k(*args, **_kwargs):
                calls.append(args)
                return _Run(0, Interfaces.SYSFS)
        self.assertEqual(underlay_interfaces(H, "ns", "pod", "native"), ["ens5", "ens6", "eth0"])
        self.assertIn("pod", calls[0])

    def test_no_physical_interface_is_an_error(self):
        class H:
            @staticmethod
            def k(*_a, **_k):
                return _Run(0, "lo /sys/devices/virtual/net/lo\n")
        with self.assertRaises(RuntimeError):
            underlay_interfaces(H, "ns", "pod", "native")

    def test_a_failed_listing_is_an_error(self):
        class H:
            @staticmethod
            def k(*_a, **_k):
                return _Run(1, "", "boom")
        with self.assertRaises(RuntimeError):
            underlay_interfaces(H, "ns", "pod", "native")


class HelmValues(unittest.TestCase):
    def test_kind_uses_the_base_values_then_the_profile(self):
        files = [f.name for f in value_files(Path("/repo"), "kind", "hardened")]
        self.assertEqual(files, ["values.yaml", "n1-hardened.yaml"])

    def test_clouds_keep_their_own_values_between_the_base_and_the_profile(self):
        for target in ("eks", "aks"):
            files = [f.name for f in value_files(Path("/repo"), target, "baseline")]
            self.assertEqual(files, ["values.yaml", f"values-{target}.yaml", "n1-baseline.yaml"])

    def test_the_cloud_value_files_exist(self):
        root = Path(__file__).resolve().parents[1]
        for target in ("eks", "aks"):
            for profile in ("baseline", "hardened"):
                for path in value_files(root, target, profile):
                    self.assertTrue(path.is_file(), path)


class SlotNodes(unittest.TestCase):
    def harness(self, labelled=None, system=("sys-b", "sys-a", "sys-c"), ready=True):
        labelled = labelled or {}
        calls = []

        class H:
            @staticmethod
            def kj(*args):
                selector = args[args.index("-l") + 1]
                if selector.startswith(SLOT_LABEL):
                    slot = selector.split("=")[1]
                    return {"items": [{"metadata": {"name": n}} for n in labelled.get(slot, [])]}
                status = {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}]}
                return {"items": [{"metadata": {"name": n}, "status": status} for n in system]}

            @staticmethod
            def k(*args, **_kwargs):
                calls.append(args)
                return _Run(0)
        return H, calls

    def test_existing_distinct_pins_are_reused_and_never_moved(self):
        h, calls = self.harness({"client": ["a"], "server": ["b"]})
        self.assertEqual(label_slot_nodes(h), {"client": "a", "server": "b"})
        self.assertEqual(calls, [])

    def test_two_distinct_system_nodes_are_labelled_deterministically(self):
        h, calls = self.harness()
        self.assertEqual(label_slot_nodes(h), {"client": "sys-a", "server": "sys-b"})
        self.assertIn(("label", "node", "sys-a", f"{SLOT_LABEL}=client", "--overwrite"), calls)
        self.assertIn(("label", "node", "sys-b", f"{SLOT_LABEL}=server", "--overwrite"), calls)

    def test_one_node_is_an_error(self):
        h, _ = self.harness(system=("only",))
        with self.assertRaises(RuntimeError):
            label_slot_nodes(h)

    def test_not_ready_nodes_are_not_chosen(self):
        h, _ = self.harness(ready=False)
        with self.assertRaises(RuntimeError):
            label_slot_nodes(h)

    def test_both_slots_on_the_same_node_are_repaired(self):
        h, calls = self.harness({"client": ["a"], "server": ["a"]})
        self.assertEqual(label_slot_nodes(h), {"client": "sys-a", "server": "sys-b"})
        self.assertIn(("label", "node", "a", f"{SLOT_LABEL}-"), calls)


if __name__ == "__main__":
    unittest.main()
