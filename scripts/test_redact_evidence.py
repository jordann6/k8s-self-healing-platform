"""The evidence redactor, tested without a cloud: nothing that identifies the
subscription, the tenant or an identity, and no credential, may survive into
a committed run. Run: make test
"""
import base64
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("redact", Path(__file__).with_name("redact-evidence.py"))
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)

SUB = "11111111-2222-3333-4444-555555555555"
TEN = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SUBS = {SUB: "<azure-subscription>", TEN: "<azure-tenant>"}


class Azure(unittest.TestCase):
    def test_subscription_any_case(self):
        t = f"/subscriptions/{SUB.upper()}/resourceGroups/rg and /subscriptions/{SUB}/x"
        out = R.redact(t, SUBS, azure=True)
        self.assertEqual(out.count("<azure-subscription>"), 2)
        self.assertFalse(R.leftover(out, SUBS, azure=True))

    def test_tenant_then_other_guids(self):
        out = R.redact(f'{{"tenantId": "{TEN}", "principalId": "0f0e0d0c-0b0a-0908-0706-050403020100"}}', SUBS, azure=True)
        self.assertIn("<azure-tenant>", out)
        self.assertIn("<azure-guid>", out)
        self.assertFalse(R.leftover(out, SUBS, azure=True))

    def test_any_guid_left_fails_an_azure_run(self):
        self.assertTrue(R.leftover("client 0f0e0d0c-0b0a-0908-0706-050403020100", SUBS, azure=True))

    def test_guids_are_not_touched_in_an_aws_run(self):
        # EKS evidence keeps Kubernetes UIDs; only its account IDs are secret.
        uid = "0f0e0d0c-0b0a-0908-0706-050403020100"
        self.assertIn(uid, R.redact(uid, {"123456789012": "<lab-account>"}))


class Credentials(unittest.TestCase):
    def test_sas_signature(self):
        url = "https://st.blob.core.windows.net/c/b?sv=2023-11-03&sp=r&sig=abc%2Bdef%3D"
        out = R.redact(url, {})
        self.assertIn("sig=<sas-signature>", out)
        self.assertFalse(R.leftover(out, {}))

    def test_a_real_signature_next_to_a_redacted_one_still_fails(self):
        self.assertTrue(R.leftover("a?sig=<sas-signature> b?sig=abcdef", {}))

    def test_jwt(self):
        # Built at run time: a JWT-shaped literal would (rightly) trip the
        # repository's own secret scanner.
        b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
        tok = "Bearer " + ".".join([b64(b'{"alg":"none"}'), b64(b'{"sub":"fixture-only"}'), b64(b"not-a-signature")])
        out = R.redact(tok, {})
        self.assertIn("<jwt>", out)
        self.assertFalse(R.leftover(out, {}))


if __name__ == "__main__":
    unittest.main()
