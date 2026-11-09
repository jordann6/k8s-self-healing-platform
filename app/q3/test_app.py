"""Unit tests for the deterministic Q3 resource workload."""
import unittest

from app import allocate_memory, settings


class Settings(unittest.TestCase):
    def test_defaults_and_explicit_values(self):
        self.assertEqual(settings({}), {"cpu_m": 200, "memory_mib": 96})
        self.assertEqual(settings({"CPU_M": "250", "MEMORY_MIB": "64"}),
                         {"cpu_m": 250, "memory_mib": 64})

    def test_out_of_range_values_are_refused(self):
        for env in ({"CPU_M": "901"}, {"CPU_M": "-1"},
                    {"MEMORY_MIB": "193"}, {"MEMORY_MIB": "-1"}):
            with self.subTest(env=env), self.assertRaises(ValueError):
                settings(env)

    def test_memory_is_allocated_and_touched(self):
        block = allocate_memory(1)
        self.assertEqual(len(block), 1024 * 1024)
        self.assertEqual(block[0], 1)
        self.assertEqual(block[4096], 1)


if __name__ == "__main__":
    unittest.main()
