from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import threading
import subprocess
import time
import unittest
from unittest.mock import patch

from scleaner.catalog import Catalog, DEFAULT_CATEGORIES
from scleaner.engine import Engine
from scleaner.models import Finding, size_text
from scleaner.safety import fingerprint, within
from scleaner.storage import Storage

WORKSPACE = Path(__file__).resolve().parents[1]
FIXTURES = WORKSPACE / "artifacts" / "test-fixtures"


class EngineTest(unittest.TestCase):
    def setUp(self):
        FIXTURES.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=FIXTURES)
        self.root = Path(self.temp.name)
        self.local = self.root / "Local"
        self.roaming = self.root / "Roaming"
        self.windows = self.root / "Windows"
        for directory in (self.local, self.roaming, self.windows):
            directory.mkdir()
        self.catalog = Catalog({"LOCALAPPDATA": str(self.local), "APPDATA": str(self.roaming), "SystemRoot": str(self.windows)})
        self.storage = Storage(self.root / "SCleaner")
        self.engine = Engine(self.catalog, self.storage, lambda: set())
        self.settings = {"age_hours": 24, "exclusions": []}

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(FIXTURES.resolve()))
        self.temp.cleanup()

    def file(self, relative, data=b"cache", old=True):
        path = self.local / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if old:
            stamp = time.time() - 3 * 86400
            os.utime(path, (stamp, stamp))
        return path

    def scan(self, categories=None):
        return self.engine.scan(categories or DEFAULT_CATEGORIES, self.settings)

    def test_temp_cleanup_and_audit_preserve_recent_and_excluded_files(self):
        old = self.file("Temp/old.tmp", b"12345678")
        recent = self.file("Temp/current.tmp", old=False)
        excluded = self.file("Temp/keep/data.tmp")
        self.settings["exclusions"] = [str(excluded.parent)]
        scan = self.scan()
        self.assertEqual([f.path for f in scan.findings], [str(old)])
        result = self.engine.clean(scan.findings, self.settings)
        self.assertEqual((result.removed, result.freed, result.skipped), (1, 8, 0))
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(excluded.exists())
        record = self.storage.history()[0]
        self.assertEqual(record["status"], "complete")
        log = Path(result.history_path).with_suffix(".jsonl").read_text(encoding="utf-8")
        self.assertEqual(json.loads(log)["path"], str(old))

    def test_windows_temp_and_relocated_environment_temp(self):
        user = self.file("Temp/user.tmp")
        custom = self.file("../Custom Temp/nested/old.tmp")
        system = self.file("../Windows/Temp/system.tmp")
        recent = self.file("../Custom Temp/recent.tmp", old=False)
        kept = self.file("../Custom Temp/keep/old.tmp")
        self.catalog = Catalog({**self.catalog.env, "temp": str(self.root / "Custom Temp")})
        self.engine.catalog = self.catalog
        self.settings["exclusions"] = [str(kept.parent)]
        result = self.scan(["temp"])
        self.assertEqual({Path(f.path).resolve() for f in result.findings}, {p.resolve() for p in (user, custom, system)})
        self.assertEqual(self.engine.clean(result.findings, self.settings).removed, 3)
        self.assertTrue(recent.exists())
        self.assertTrue(kept.exists())
        self.assertTrue(system.parent.is_dir())

    def test_environment_temp_aliases_are_not_counted_twice(self):
        old = self.file("Temp/nested/old.tmp")
        for temp in (self.local / "Temp", self.local / "TEMP", old.parent):
            with self.subTest(temp=temp):
                self.engine.catalog = Catalog({**self.catalog.env, "TEMP": str(temp)})
                result = self.scan(["temp"])
                self.assertEqual([f.path for f in result.findings], [str(old)])
                self.assertEqual(result.total_size, old.stat().st_size)

    def test_invalid_or_overbroad_environment_temp_is_ignored(self):
        old = self.file("Documents/keep.txt")
        for temp in ("", "relative", "%UNDEFINED%", str(self.local.anchor), str(self.local),
                     str(self.root), str(self.windows), str(self.windows / "Prefetch"), str(self.local / "Temp/..")):
            with self.subTest(temp=temp):
                self.engine.catalog = Catalog({**self.catalog.env, "TEMP": temp})
                self.assertEqual(self.scan(["temp"]).findings, [])
        self.assertTrue(old.exists())

    def test_prefetch_only_offers_old_pf_files_and_requires_selection(self):
        old = self.file("../Windows/Prefetch/APP-123.PF")
        recent = self.file("../Windows/Prefetch/CURRENT.pf", old=False)
        layout = self.file("../Windows/Prefetch/Layout.ini")
        database = self.file("../Windows/Prefetch/ReadyBoot/trace.fx")
        result = self.scan(["temp"])
        self.assertEqual([Path(f.path).resolve() for f in result.findings], [old.resolve()])
        self.assertFalse(result.findings[0].selected)
        self.assertIn("Prefetch", result.findings[0].label)
        self.assertEqual(self.engine.clean(result.findings, self.settings).removed, 0)
        result.findings[0].selected = True
        self.assertEqual(self.engine.clean(result.findings, self.settings).removed, 1)
        self.assertTrue(all(p.exists() for p in (recent, layout, database)))
        forged = Finding("temp", "Prefetch", str(layout.resolve()), root=str(layout.parent.resolve()), stamp=fingerprint(layout))
        self.assertEqual(self.engine.clean([forged], self.settings).skipped, 1)
        self.assertTrue(layout.exists())

    def test_prefetch_access_denied_does_not_stop_temp_scan(self):
        old = self.file("Temp/old.tmp")
        prefetch = self.file("../Windows/Prefetch/APP.pf").parent.resolve()
        scandir = os.scandir
        def check(path):
            if Path(path) == prefetch:
                raise PermissionError(13, "Access denied")
            return scandir(path)
        with patch("scleaner.engine.os.scandir", check):
            result = self.scan(["temp"])
        self.assertEqual([f.path for f in result.findings], [str(old)])
        self.assertTrue(any("Prefetch" in note for note in result.notes))

    def test_chromium_profiles_and_secrets(self):
        cache = self.file("Google/Chrome/User Data/Profile 2/Cache/Cache_Data/data_1")
        secrets = [self.file("Google/Chrome/User Data/Profile 2/" + name) for name in ("Login Data", "History", "Cookies", "Bookmarks", "Sessions/Session_1", "Service Worker/Database/entry")]
        result = self.scan(["browsers"])
        self.assertEqual({f.path for f in result.findings}, {str(cache)})
        self.engine.clean(result.findings, self.settings)
        self.assertTrue(all(p.exists() for p in secrets))

    def test_firefox_profile_cache_only(self):
        cache = self.file("Mozilla/Firefox/Profiles/a.default/cache2/entries/entry")
        password = self.file("Mozilla/Firefox/Profiles/a.default/logins.json")
        self.assertEqual([f.path for f in self.scan(["browsers"]).findings], [str(cache)])
        self.assertTrue(password.exists())

    def test_running_browser_and_failed_inventory_are_skipped(self):
        self.file("Google/Chrome/User Data/Default/Cache/item")
        for processes in ({"chrome.exe"}, None):
            self.engine.process_provider = lambda: processes
            result = self.scan(["browsers"])
            self.assertEqual(result.findings, [])
            self.assertTrue(result.notes)

    def test_app_started_after_scan_is_not_cleaned(self):
        cache = self.file("Google/Chrome/User Data/Default/Cache/item")
        scan = self.scan(["browsers"])
        self.engine.process_provider = lambda: {"chrome.exe"}
        result = self.engine.clean(scan.findings, self.settings)
        self.assertEqual(result.skipped, 1)
        self.assertTrue(cache.exists())

    def test_modified_and_replaced_files_are_not_cleaned(self):
        for replace_file in (False, True):
            file = self.file("Temp/modified.tmp")
            scan = self.scan(["temp"])
            if replace_file:
                file.unlink()
            file.write_bytes(b"new contents")
            result = self.engine.clean(scan.findings, self.settings)
            self.assertEqual(result.skipped, 1)
            self.assertTrue(file.exists())

    def test_new_exclusion_is_rechecked_before_clean(self):
        file = self.file("Temp/keep.tmp")
        result = self.scan(["temp"])
        self.settings["exclusions"] = [str(file)]
        clean = self.engine.clean(result.findings, self.settings)
        self.assertEqual(clean.removed, 0)
        self.assertTrue(file.exists())

    def test_forged_target_cannot_delete_documents(self):
        file = self.file("Documents/important.txt")
        finding = Finding("temp", "Forged", str(file), file.stat().st_size, root=str(file.parent), stamp=fingerprint(file))
        result = self.engine.clean([finding], self.settings)
        self.assertEqual(result.skipped, 1)
        self.assertTrue(file.exists())

    def test_path_escape_is_rejected(self):
        file = self.file("Temp2/file.txt")
        self.file("Temp/dummy")
        finding = Finding("temp", "Escape", str(file), root=str(self.local / "Temp"), stamp=fingerprint(file))
        self.assertFalse(within(file, self.local / "Temp"))
        self.assertEqual(self.engine.clean([finding], self.settings).skipped, 1)
        self.assertTrue(file.exists())

    def test_hardlinks_are_skipped(self):
        source = self.file("Documents/precious.txt")
        destination = self.local / "Temp" / "hardlink.tmp"
        destination.parent.mkdir()
        os.link(source, destination)
        self.assertEqual(self.scan(["temp"]).findings, [])
        self.assertTrue(source.exists())

    def test_symlink_is_not_traversed(self):
        source = self.file("Documents/precious.txt")
        link = self.local / "Temp" / "outside"
        link.parent.mkdir()
        try:
            link.symlink_to(source.parent, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation is not permitted on this Windows installation")
        self.assertEqual(self.scan(["temp"]).findings, [])
        self.assertTrue(source.exists())

    def test_reparse_point_attribute_is_detected(self):
        from scleaner.safety import is_link
        from types import SimpleNamespace
        with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=0o040755, st_file_attributes=0x400)):
            self.assertTrue(is_link(self.local))

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_windows_junction_is_not_traversed(self):
        source = self.file("Documents/precious.txt")
        junction = self.local / "Temp" / "outside"
        junction.parent.mkdir()
        result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(junction), str(source.parent)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        try:
            self.assertTrue(junction.absolute().is_relative_to(self.root.absolute()))
            self.assertEqual(self.scan(["temp"]).findings, [])
            self.assertTrue(source.exists())
        finally:
            # Remove the junction itself, never its target or contents.
            junction.rmdir()

    def test_empty_folders_are_manual_and_never_recursive(self):
        folder = self.local / "RemovedApp"
        folder.mkdir()
        protected = self.local / "Microsoft"
        protected.mkdir()
        for p in (folder, protected):
            stamp = time.time() - 3 * 86400
            os.utime(p, (stamp, stamp))
        result = self.scan(["empty"])
        self.assertEqual([f.path for f in result.findings], [str(folder)])
        self.assertFalse(result.findings[0].selected)
        self.assertEqual(self.engine.clean(result.findings, self.settings).removed, 0)
        result.findings[0].selected = True
        (folder / "new-file").write_text("do not delete")
        self.assertEqual(self.engine.clean(result.findings, self.settings).skipped, 1)
        self.assertTrue((folder / "new-file").exists())

    def test_confirmed_empty_folder_is_removed_without_touching_parent(self):
        folder = self.local / "RemovedApp"
        folder.mkdir()
        stamp = time.time() - 3 * 86400
        os.utime(folder, (stamp, stamp))
        result = self.scan(["empty"])
        result.findings[0].selected = True
        self.assertEqual(self.engine.clean(result.findings, self.settings).removed, 1)
        self.assertTrue(self.local.exists())

    def test_cancelled_scan_and_cleanup(self):
        file = self.file("Temp/old.tmp")
        event = threading.Event()
        event.set()
        result = self.engine.scan(["temp"], self.settings, event)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.findings, [])
        scan = self.scan(["temp"])
        result = self.engine.clean(scan.findings, self.settings, event)
        self.assertTrue(result.cancelled)
        self.assertTrue(file.exists())

    def test_cleanup_requires_writable_audit_log(self):
        file = self.file("Temp/old.tmp")
        scan = self.scan(["temp"])
        with patch.object(self.storage, "write_json", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                self.engine.clean(scan.findings, self.settings)
        self.assertTrue(file.exists())

    def test_unreadable_cache_root_does_not_abort_other_categories(self):
        self.file("Temp/old.tmp")
        original = Path.exists
        def check(path, *args, **kwargs):
            if str(path).endswith("discord\\Cache"):
                raise PermissionError("Access denied")
            return original(path, *args, **kwargs)
        with patch.object(Path, "exists", check):
            result = self.scan()
        self.assertEqual(len(result.findings), 1)
        self.assertTrue(any("discord" in note for note in result.notes))

    def test_locked_file_does_not_block_remaining_cleanup(self):
        locked = self.file("Temp/locked.tmp")
        good = self.file("Temp/good.tmp")
        unlink = Path.unlink
        def guard(path, *args, **kwargs):
            if path == locked:
                raise PermissionError("used by another process")
            return unlink(path, *args, **kwargs)
        with patch.object(Path, "unlink", guard):
            result = self.engine.clean(self.scan(["temp"]).findings, self.settings)
        self.assertEqual((result.removed, result.skipped), (1, 1))
        self.assertTrue(locked.exists())
        self.assertFalse(good.exists())

    def test_large_file_analysis_is_read_only(self):
        file = self.file("Documents/big.bin")
        with file.open("wb") as stream:
            stream.truncate(11 * 1024 * 1024)
        result = self.engine.large_files(file.parent, {"large_mb": 10})
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.findings[0].kind, "large")
        result.findings[0].selected = True
        self.assertEqual(self.engine.clean(result.findings, self.settings).removed, 0)
        self.assertTrue(file.exists())

    def test_own_data_is_excluded_even_under_temp(self):
        self.storage.root = self.local / "Temp" / "SCleaner"
        self.file("Temp/SCleaner/history/saved.json")
        self.assertEqual(self.scan(["temp"]).findings, [])

    def test_corrupt_settings_fall_back_to_defaults(self):
        self.storage.root.mkdir()
        path = self.storage.root / "settings.json"
        path.write_text("[1,2]", encoding="utf-8")
        self.assertEqual(self.storage.read_settings()["age_hours"], 24)
        path.write_text('{"age_hours": -12, "categories": ["registry", "temp"], "exclusions": [1, "relative"]}', encoding="utf-8")
        result = self.storage.read_settings()
        self.assertEqual(result["age_hours"], 1)
        self.assertEqual(result["categories"], ["temp"])
        self.assertEqual(result["exclusions"], [])


if __name__ == "__main__":
    unittest.main()
