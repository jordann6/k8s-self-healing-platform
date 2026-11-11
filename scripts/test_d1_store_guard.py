"""The second destroy must never run before the retention has ended."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from d1_store_guard import decide, parse_time

NOW = dt.datetime(2026, 11, 11, 20, 0, tzinfo=dt.timezone.utc)


def record(directory, **fields):
    path = Path(directory) / "eks-store.json"
    path.write_text(json.dumps(fields))
    return path


class Guard(unittest.TestCase):
    def test_refuses_while_retention_is_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, why = decide(record(tmp, retain_until="2026-11-11T21:00:00Z"), NOW)
        self.assertFalse(ok)
        self.assertIn("remain", why)

    def test_refuses_inside_the_margin_after_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, _ = decide(record(tmp, retain_until="2026-11-11T19:55:00Z"), NOW, 600)
        self.assertFalse(ok)

    def test_allows_after_expiry_plus_margin(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, _ = decide(record(tmp, retain_until="2026-11-11T19:40:00Z"), NOW, 600)
        self.assertTrue(ok)

    def test_never_trusts_a_missing_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, why = decide(Path(tmp) / "absent.json", NOW)
        self.assertFalse(ok)
        self.assertIn("no store record", why)

    def test_never_trusts_a_corrupt_or_incomplete_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "x.json"
            bad.write_text("{not json")
            self.assertFalse(decide(bad, NOW)[0])
            self.assertFalse(decide(record(tmp), NOW)[0])
            self.assertFalse(decide(record(tmp, retain_until=""), NOW)[0])
            self.assertFalse(decide(record(tmp, retain_until="soon"), NOW)[0])

    def test_naive_timestamps_are_treated_as_utc(self):
        self.assertEqual(parse_time("2026-11-11T19:00:00").tzinfo, dt.timezone.utc)


if __name__ == "__main__":
    unittest.main()
