"""P1 local registry helper tests."""
import unittest

from p1_registry import MARKER, patch_corefile


class CoreDNS(unittest.TestCase):
    def test_inserts_and_updates_one_host_block(self):
        original = ".:53 {\n    ready\n    kubernetes cluster.local in-addr.arpa ip6.arpa {\n    }\n}\n"
        first = patch_corefile(original, "172.18.0.9")
        second = patch_corefile(first, "172.18.0.10")
        self.assertIn("172.18.0.10 selfheal-p1-registry", second)
        self.assertNotIn("172.18.0.9 selfheal-p1-registry", second)
        self.assertEqual(second.count(MARKER), 1)

    def test_missing_anchor_is_an_error(self):
        with self.assertRaises(ValueError):
            patch_corefile(".:53 {\n}\n", "172.18.0.9")


if __name__ == "__main__":
    unittest.main()
