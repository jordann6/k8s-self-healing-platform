"""P1 local registry helper tests."""
import unittest

import base64
import json

from p1_registry import MARKER, SIGNATURE_ANNOTATION, patch_corefile, tamper_manifest


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


class Tamper(unittest.TestCase):
    def manifest(self, signature=b"\x01\x02\x03"):
        return json.dumps({"schemaVersion": 2, "layers": [{
            "digest": "sha256:" + "a" * 64,
            "annotations": {SIGNATURE_ANNOTATION: base64.b64encode(signature).decode(), "other": "kept"}}]})

    def test_flips_exactly_the_last_signature_byte_and_nothing_else(self):
        result = json.loads(tamper_manifest(self.manifest()))
        layer = result["layers"][0]
        self.assertEqual(base64.b64decode(layer["annotations"][SIGNATURE_ANNOTATION]), b"\x01\x02\x02")
        self.assertEqual(layer["annotations"]["other"], "kept")
        self.assertEqual(layer["digest"], "sha256:" + "a" * 64)

    def test_tampering_twice_restores_the_original(self):
        once = tamper_manifest(self.manifest())
        twice = json.loads(tamper_manifest(once))
        self.assertEqual(base64.b64decode(twice["layers"][0]["annotations"][SIGNATURE_ANNOTATION]), b"\x01\x02\x03")

    def test_not_a_signature_manifest_is_an_error(self):
        with self.assertRaises(ValueError):
            tamper_manifest(json.dumps({"layers": []}))
        with self.assertRaises(ValueError):
            tamper_manifest(json.dumps({"layers": [{"annotations": {}}]}))


if __name__ == "__main__":
    unittest.main()
