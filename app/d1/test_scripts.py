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


class CloudStore(unittest.TestCase):
    """ADR-0022: the cloud store paths branch on D1_STORE and leave the kind path alone."""

    def test_both_scripts_default_to_the_kind_minio_path(self):
        self.assertIn('store="${D1_STORE:-mc}"', (ROOT / "backup.sh").read_text())
        self.assertIn('"${D1_STORE:-mc}" = mc', (ROOT / "restore.sh").read_text())

    def test_cloud_branches_call_the_store_client_and_keep_the_markers(self):
        text = (ROOT / "backup.sh").read_text()
        for call in ("d1store.py put", "d1store.py stat", "d1store.py retention"):
            self.assertIn(call, text)
        self.assertIn("d1store.py get", (ROOT / "restore.sh").read_text())

    def test_the_cloud_image_installs_the_client_and_does_not_replace_the_kind_one(self):
        cloud = (ROOT / "cloud.Dockerfile").read_text()
        self.assertIn("boto3==", cloud)
        self.assertIn("azure-storage-blob==", cloud)
        self.assertIn("azure-identity==", cloud)
        self.assertNotIn("minio/mc", cloud)
        self.assertIn("mc.linux", (ROOT / "Dockerfile").read_text())


if __name__ == "__main__":
    unittest.main()
