import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import restore
import restore_catalog


class RestoreCatalogTests(unittest.TestCase):
    def test_record_and_list_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            restore_catalog, "RESTORE_INDEX_FILE", Path(temp_dir) / "index.json"
        ):
            restore_catalog.record_uploaded_file(
                chat_id="-100",
                message_id=12,
                file_id="FILE123",
                path=r"C:\\backup\\a.txt",
                relative_path="a.txt",
                size=42,
                thread_id=7,
            )
            entries = restore_catalog.list_restore_candidates()

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["file_id"], "FILE123")
        self.assertEqual(entries[0]["message_id"], 12)
        self.assertEqual(entries[0]["relative_path"], "a.txt")
        self.assertEqual(entries[0]["version"], 1)

    def test_corrupt_catalog_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            restore_catalog, "RESTORE_INDEX_FILE", Path(temp_dir) / "index.json"
        ):
            restore_catalog.RESTORE_INDEX_FILE.write_text("not-json", encoding="utf-8")
            self.assertEqual(restore_catalog.load_restore_index(), [])


class RestoreFileTests(unittest.TestCase):
    def test_restore_downloads_file_atomically(self):
        entry = {"file_id": "FILE123", "relative_path": "folder/a.txt"}
        telegram_file = {"file_path": "documents/file.bin", "file_size": 4}
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.headers = {"Content-Length": "4"}
        response.iter_content.return_value = [b"test"]

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            restore, "telegram_request", return_value=telegram_file
        ) as request, patch.object(restore.requests, "get", return_value=response):
            output = restore.restore_file("TOKEN", entry, temp_dir)
            self.assertEqual(output.read_bytes(), b"test")
            request.assert_called_once_with("TOKEN", "getFile", data={"file_id": "FILE123"})

    def test_restore_rejects_path_traversal(self):
        entry = {"file_id": "FILE123", "relative_path": "../escape.txt"}
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                restore.restore_file("TOKEN", entry, temp_dir)

    def test_restore_does_not_overwrite_without_flag(self):
        entry = {"file_id": "FILE123", "relative_path": "a.txt"}
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "a.txt"
            target.write_text("old", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                restore.restore_file("TOKEN", entry, temp_dir)


if __name__ == "__main__":
    unittest.main()
