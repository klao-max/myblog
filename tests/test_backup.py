import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.data_backup import backup_data, restore_data, verify_backup


class BackupTests(unittest.TestCase):
    def test_backup_verify_and_restore_reconstruct_database_and_uploads(self):
        with tempfile.TemporaryDirectory(prefix="signal-backup-test-") as root:
            root_path = Path(root)
            source = root_path / "source"
            backup = root_path / "backup"
            restored = root_path / "restored"
            uploads = source / "uploads" / "gallery"
            uploads.mkdir(parents=True)
            connection = sqlite3.connect(source / "blog.db")
            connection.execute("CREATE TABLE notes (body TEXT NOT NULL)")
            connection.execute("INSERT INTO notes(body) VALUES ('rehearsal')")
            connection.commit()
            connection.close()
            (uploads / "hero.jpg").write_bytes(b"image-data")

            backup_data(source, backup)
            verify_backup(backup)
            restore_data(backup, restored)

            restored_connection = sqlite3.connect(restored / "blog.db")
            self.assertEqual(restored_connection.execute("SELECT body FROM notes").fetchone()[0], "rehearsal")
            restored_connection.close()
            self.assertEqual((restored / "uploads" / "gallery" / "hero.jpg").read_bytes(), b"image-data")

    def test_verify_backup_rejects_tampered_manifest(self):
        with tempfile.TemporaryDirectory(prefix="signal-backup-test-") as root:
            root_path = Path(root)
            source = root_path / "source"
            backup = root_path / "backup"
            source.mkdir()
            sqlite3.connect(source / "blog.db").close()
            backup_data(source, backup)

            manifest_path = backup / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["database_sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest))

            with self.assertRaises(ValueError):
                verify_backup(backup)


if __name__ == "__main__":
    unittest.main()
