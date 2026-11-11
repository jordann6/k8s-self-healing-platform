"""P1 workload unit tests."""
import unittest

from app import Handler


class Workload(unittest.TestCase):
    def test_server_identity_is_fixed(self):
        self.assertEqual(Handler.server_version, "selfheal-p1")


if __name__ == "__main__":
    unittest.main()
