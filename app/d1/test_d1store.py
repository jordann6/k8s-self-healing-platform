"""Pure d1store tests: formats, the error contract, and the command dispatch with a fake store."""
import datetime as dt
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import d1store


class Formats(unittest.TestCase):
    def test_retention_line_matches_what_the_runner_parses(self):
        import re
        line = d1store.retention_line("COMPLIANCE", 1)
        match = re.search(r"Object locking '([^']+)' is configured for ([0-9]+)DAYS", line)
        self.assertEqual((match.group(1), int(match.group(2))), ("COMPLIANCE", 1))

    def test_days_between_rounds_to_whole_days(self):
        start = dt.datetime(2026, 11, 10, 12, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(d1store.days_between(start, start + dt.timedelta(days=1, seconds=2)), 1)
        self.assertEqual(d1store.days_between(start, start - dt.timedelta(days=3)), 0)

    def test_iso_normalises_to_utc_and_tolerates_none(self):
        zone = dt.timezone(dt.timedelta(hours=-5))
        self.assertEqual(d1store.iso(dt.datetime(2026, 11, 10, 7, 0, tzinfo=zone)), "2026-11-10T12:00:00Z")
        self.assertIsNone(d1store.iso(None))


class ErrorContract(unittest.TestCase):
    def test_s3_style_client_error_names_its_code(self):
        error = Exception("An error occurred (AccessDenied) when calling DeleteObject")
        error.response = {"Error": {"Code": "AccessDenied"}}
        self.assertTrue(d1store.describe(error).startswith("AccessDenied: "))

    def test_azure_style_error_names_its_error_code(self):
        error = Exception("This operation is not permitted")
        error.error_code = "AuthorizationPermissionMismatch"
        self.assertTrue(d1store.describe(error).startswith("AuthorizationPermissionMismatch: "))

    def test_unknown_error_falls_back_to_the_type_name(self):
        self.assertTrue(d1store.describe(ValueError("boom")).startswith("ValueError: "))


class Dispatch(unittest.TestCase):
    class Fake:
        def __init__(self, refuse=False):
            self.refuse, self.calls = refuse, []

        def put(self, path, key):
            self.calls.append(("put", path, key))
            return {"versionID": "v1", "size": 3}

        def stat(self, key):
            return {"versionID": "v1", "size": 3, "mode": "COMPLIANCE", "retainUntilDate": "2026-11-11T00:00:00Z"}

        def retention(self, key):
            return d1store.retention_line("COMPLIANCE", 1)

        def get(self, key, path):
            self.calls.append(("get", key, path))

        def delete(self, key, version):
            self.calls.append(("delete", key, version))
            if self.refuse:
                error = Exception("Access Denied")
                error.response = {"Error": {"Code": "AccessDenied"}}
                raise error

    def run_cli(self, fake, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(d1store, "backend", return_value=fake), redirect_stdout(out), redirect_stderr(err):
            code = d1store.main(["d1store.py", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_stat_prints_one_json_object(self):
        code, out, _ = self.run_cli(self.Fake(), "stat", "backups/x.dump")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["mode"], "COMPLIANCE")

    def test_a_refused_delete_exits_1_with_the_code_on_stderr_and_nothing_on_stdout(self):
        code, out, err = self.run_cli(self.Fake(refuse=True), "delete", "backups/x.dump")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("AccessDenied", err)

    def test_delete_passes_the_version_through(self):
        fake = self.Fake()
        self.run_cli(fake, "delete", "backups/x.dump", "v1")
        self.assertIn(("delete", "backups/x.dump", "v1"), fake.calls)

    def test_wrong_usage_is_exit_2(self):
        self.assertEqual(self.run_cli(self.Fake(), "put", "only-one-arg")[0], 2)

    def test_an_unknown_backend_is_refused(self):
        with mock.patch.dict("os.environ", {"D1_STORE": "mc"}):
            with self.assertRaises(SystemExit):
                d1store.backend()


class S3Put(unittest.TestCase):
    def test_put_sends_the_encryption_header_the_org_scp_requires(self):
        import os
        import tempfile
        with tempfile.NamedTemporaryFile() as handle:
            handle.write(b"x")
            handle.flush()
            store = d1store.S3.__new__(d1store.S3)
            store.client = mock.Mock()
            store.client.put_object.return_value = {"VersionId": "v1"}
            store.bucket = "b"
            result = store.put(handle.name, "k")
        self.assertEqual(store.client.put_object.call_args.kwargs["ServerSideEncryption"], "AES256")
        self.assertEqual(result, {"versionID": "v1", "size": 1})


if __name__ == "__main__":
    unittest.main()
