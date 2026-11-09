import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent


class Scripts(unittest.TestCase):
    def test_backup_uses_custom_archive_and_emits_required_telemetry(self):
        text = (ROOT / "backup.sh").read_text()
        self.assertIn("--format custom", text)
        for marker in ("D1_BACKUP_COMPLETE", "D1_MANIFEST_SHA256", "D1_ARCHIVE_STAT",
                       "D1_RETENTION_INFO"):
            self.assertIn(marker, text)
        self.assertIn('MC_CONFIG_DIR="$work/.mc"', text)

    def test_restore_is_atomic_and_exits_on_error(self):
        text = (ROOT / "restore.sh").read_text()
        self.assertIn("--single-transaction", text)
        self.assertIn("--exit-on-error", text)
        self.assertIn("archive checksum mismatch", text)
        self.assertIn('MC_CONFIG_DIR="$work/.mc"', text)


if __name__ == "__main__":
    unittest.main()
