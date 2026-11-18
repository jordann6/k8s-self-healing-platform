import io
import json
import unittest
from unittest import mock

import app


def call(path):
    handler = app.Handler.__new__(app.Handler)
    handler.path = path
    handler.wfile = io.BytesIO()
    handler.send_response = mock.Mock()
    handler.send_header = mock.Mock()
    handler.end_headers = mock.Mock()
    handler.do_GET()
    return handler.send_response.call_args.args[0], json.loads(handler.wfile.getvalue())


class HandlerTests(unittest.TestCase):
    def test_health_is_ok(self):
        self.assertEqual(call("/healthz"), (200, {"ok": True}))

    def test_every_other_path_names_the_backend(self):
        with mock.patch.object(app, "NAME", "web"):
            self.assertEqual(call("/shop"), (200, {"backend": "web", "path": "/shop"}))


if __name__ == "__main__":
    unittest.main()
