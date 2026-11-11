import json
import unittest
from unittest import mock

import app


class HandlerTests(unittest.TestCase):
    def test_alert_contract_requires_quarantine_kind(self):
        self.assertEqual(app.NS, "selfheal-o1")
        payload = {"kind": "quarantine", "target_uid": "uid-1"}
        self.assertEqual(json.loads(json.dumps(payload))["kind"], "quarantine")

    @mock.patch("builtins.open", mock.mock_open(read_data="token"))
    @mock.patch("urllib.request.urlopen")
    @mock.patch("ssl.create_default_context")
    def test_api_uses_merge_patch(self, _context, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok":true}'
        request_body = {"metadata": {"labels": {"x": None}}}
        result = app.api("PATCH", "/api/v1/namespaces/n/pods/p", request_body)
        request = urlopen.call_args.args[0]
        self.assertEqual(result, {"ok": True})
        self.assertEqual(request.get_method(), "PATCH")
        self.assertEqual(request.headers["Content-type"], "application/merge-patch+json")


if __name__ == "__main__":
    unittest.main()
