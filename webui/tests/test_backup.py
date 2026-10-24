import errno
import io
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from .test_historical import AppTests


class BackupTests(AppTests):
    def run_backup(self, folder, external=False, chmod_error=None, replace_error=None):
        root = Path(folder)
        data = root / "data"
        dest = root / "backup"
        data.mkdir(exist_ok=True)
        dest.mkdir(exist_ok=True)
        with sqlite3.connect(data / "logs.db") as con:
            con.execute("CREATE TABLE IF NOT EXISTS fixture(value TEXT)")
            con.execute("INSERT INTO fixture VALUES ('test')")
        (data / "secret_key").write_text("fixture-secret")
        (data / "saml").mkdir(exist_ok=True)
        (data / "saml" / "key.pem").write_text("fixture-key")
        values = {"DB_PATH": data / "logs.db", "DATA_DIR": data, "TMP_DIR": root / "tmp"}
        with patch.dict(self.ns, values), \
                patch.dict(os.environ, LOGDB_BACKUP_DIR=str(dest),
                           LOGDB_BACKUP_EXTERNAL_PERMISSIONS="1" if external else "0"), \
                patch.object(os, "chmod", side_effect=chmod_error) as chmod, \
                patch.object(os, "replace", side_effect=replace_error, wraps=os.replace), \
                patch("sys.stderr", new_callable=io.StringIO) as warnings, \
                patch("sys.stdout", new_callable=io.StringIO):
            result = self.ns["backup"]()
        snapshots = sorted(p for p in dest.glob("backup-*") if (p / "logs.db").is_file())
        snapshot = snapshots[-1] if result == 0 else dest
        return result, warnings.getvalue(), chmod, snapshot

    def test_default_sets_private_permissions_and_copies_database_and_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            result, warnings, chmod, dest = self.run_backup(folder)
            self.assertEqual(result, 0)
            self.assertEqual(warnings, "")
            self.assertEqual(chmod.call_count, 3)
            self.assertTrue(all(call.args[1] == 0o600 for call in chmod.call_args_list))
            with sqlite3.connect(dest / "logs.db") as con:
                self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(con.execute("SELECT value FROM fixture").fetchone()[0], "test")
            self.assertEqual((dest / "secret_key").read_text(), "fixture-secret")
            self.assertEqual((dest / "saml/key.pem").read_text(), "fixture-key")

    def test_strict_failure_retains_previous_file_and_cleans_temporary_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "backup"
            dest.mkdir()
            (dest / "logs.db").write_bytes(b"previous")
            result, _, _, _ = self.run_backup(folder, chmod_error=PermissionError(errno.EACCES, "denied"))
            self.assertIn("LOGDB_BACKUP_EXTERNAL_PERMISSIONS=1", result)
            self.assertEqual((dest / "logs.db").read_bytes(), b"previous")
            self.assertFalse(list(dest.glob(".*.tmp")))
            self.assertFalse((Path(folder) / "tmp/backup.db").exists())

    def test_opt_in_warns_and_continues_for_permission_and_unsupported_errors(self):
        for code in (errno.EACCES, errno.EPERM, errno.ENOTSUP, errno.ENOSYS):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as folder:
                result, warnings, _, dest = self.run_backup(folder, True, OSError(code, "fixture"))
                self.assertEqual(result, 0)
                self.assertIn("externally managed", warnings)
                self.assertEqual(warnings.count("Cannot enforce chmod 600"), 3)
                self.assertTrue((dest / "saml/key.pem").exists())
                self.assertFalse(list(dest.rglob(".*.tmp")))

    def test_opt_in_does_not_hide_io_or_rename_errors(self):
        for stage in ("chmod", "replace"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as folder:
                error = OSError(errno.EIO, "fixture I/O failure")
                with self.assertRaisesRegex(OSError, "fixture I/O failure"):
                    self.run_backup(folder, True, error if stage == "chmod" else None,
                                    error if stage == "replace" else None)
                self.assertFalse(list((Path(folder) / "backup").rglob(".*.tmp")))
                self.assertFalse((Path(folder) / "tmp/backup.db").exists())

    def test_keeps_latest_14_complete_sets_and_preserves_unrelated_files(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "backup"
            dest.mkdir()
            (dest / "logs.db").write_bytes(b"legacy")
            unrelated = dest / "backup-not-a-snapshot"
            unrelated.mkdir()
            (unrelated / "notes").write_text("keep")
            for index in range(17):
                result, _, _, snapshot = self.run_backup(folder)
                self.assertEqual(result, 0)
                self.assertRegex(snapshot.name, r"^backup-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.\d{6}Z$")
                with sqlite3.connect(snapshot / "logs.db") as con:
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM fixture").fetchone()[0], index + 1)
            snapshots = sorted(p for p in dest.glob("backup-*") if p != unrelated)
            self.assertEqual(len(snapshots), 14)
            for index, snapshot in enumerate(snapshots, 4):
                with sqlite3.connect(snapshot / "logs.db") as con:
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM fixture").fetchone()[0], index)
                self.assertEqual((snapshot / "secret_key").read_text(), "fixture-secret")
                self.assertEqual((snapshot / "saml/key.pem").read_text(), "fixture-key")
            self.assertEqual((dest / "logs.db").read_bytes(), b"legacy")
            self.assertTrue((unrelated / "notes").exists())
            self.assertFalse(list(dest.glob(".backup-*")))
            self.assertFalse(list((Path(folder) / "tmp").iterdir()))

    def test_late_copy_or_publish_failure_keeps_previous_sets_without_pruning(self):
        for stage in ("copy", "publish"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as folder:
                _, _, _, previous = self.run_backup(folder)
                dest = previous.parent
                before = sorted(dest.iterdir())
                if stage == "copy":
                    with self.assertRaisesRegex(OSError, "late failure"):
                        self.run_backup(folder, chmod_error=[None, None, OSError(errno.EIO, "late failure")])
                else:
                    with patch.object(os, "rename", side_effect=OSError(errno.EIO, "late failure")), \
                            self.assertRaisesRegex(OSError, "late failure"):
                        self.run_backup(folder)
                self.assertEqual(sorted(dest.iterdir()), before)
                self.assertEqual((previous / "saml/key.pem").read_text(), "fixture-key")
                self.assertFalse(list((Path(folder) / "tmp").iterdir()))

    def test_installer_selects_newest_complete_set_or_legacy_backup(self):
        script = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
        selection = script.split('# ---------------------------------------------------------------- restore\n', 1)[1]
        selection = selection.split('if [ ! -f "$WEB_DATA/logs.db" ]', 1)[0]
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder)
            (dest / "logs.db").write_bytes(b"legacy")

            def selected():
                return subprocess.run(
                    ["bash", "-c", 'BACKUP_DIR="$1"\n' + selection + '\nprintf "%s" "$RESTORE_DIR"',
                     "fixture", str(dest)], check=True, capture_output=True, text=True).stdout

            self.assertEqual(selected(), str(dest))
            newer = dest / "backup-2026-10-24T12-45-00.123456Z"
            older = dest / "backup-2026-10-23T12-45-00.123456Z"
            for snapshot in (newer, older):
                snapshot.mkdir()
                (snapshot / "logs.db").write_bytes(b"fixture")
            (dest / "backup-2026-10-25T12-45-00.123456Z").mkdir()
            self.assertEqual(selected(), str(newer))
