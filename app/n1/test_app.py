"""N1 application unit tests."""
import unittest
from unittest.mock import patch

from app import probe


class Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return b"ok"


class Probe(unittest.TestCase):
    @patch("app.urllib.request.urlopen", return_value=Response())
    def test_success_samples_are_explicit(self, _urlopen):
        result = probe("http://server", 3, 1)
        self.assertEqual(result["count"], 3)
        self.assertTrue(all(sample["ok"] for sample in result["samples"]))

    @patch("app.urllib.request.urlopen", side_effect=OSError("blocked"))
    def test_failures_are_explicit(self, _urlopen):
        result = probe("http://server", 2, 1)
        self.assertTrue(all(not sample["ok"] for sample in result["samples"]))
        self.assertTrue(all(sample["error"] == "OSError" for sample in result["samples"]))


if __name__ == "__main__":
    unittest.main()
