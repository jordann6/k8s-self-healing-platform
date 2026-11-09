"""Pure N1 evidence helper tests."""
import unittest

from n1_row import capture_facts, exact_policy_drops, request_summary, wireguard_facts


class Capture(unittest.TestCase):
    def test_packet_types_and_marker(self):
        output = "node.40000 > peer.8472: VXLAN n1-marker\nnode.40001 > peer.51871: UDP"
        self.assertEqual(capture_facts(output, "n1-marker"), {
            "vxlan_packets": 1, "vxlan_packets_total": 1,
            "wireguard_packets": 1, "marker_visible": True,
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


if __name__ == "__main__":
    unittest.main()
