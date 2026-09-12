"""Only an in-memory registry is mutated by these tests."""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from scleaner.catalog import Catalog
from scleaner.engine import Engine
from scleaner.registry import RegistryCleaner, UNINSTALL
from scleaner.software_registry import SoftwareRegistry, RegistryRemovalError, name_matches, tree_digest
from scleaner.storage import Storage


class Key:
    def __init__(self, hive, path, view):
        self.hive, self.path, self.view = hive, path, view

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class MemoryRegistry:
    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE = 1, 2
    KEY_READ, KEY_WRITE = 1, 2
    KEY_WOW64_64KEY, KEY_WOW64_32KEY = 256, 512

    def __init__(self):
        self.keys, self.denied = {}, set()
        self.before_delete = None
        for hive in (1, 2):
            for view in (256, 512):
                self.add(hive, UNINSTALL, [], view)

    def add(self, hive, path, values, view=256):
        parts = path.split("\\")
        for i in range(1, len(parts)):
            self.keys.setdefault((hive, "\\".join(parts[:i]), view), [])
        self.keys[hive, path, view] = copy.deepcopy(values)

    def resolve(self, hive, path, access):
        view = access & (256 | 512) or (hive.view if isinstance(hive, Key) else 256)
        if isinstance(hive, Key):
            return hive.hive, hive.path + "\\" + path, view
        return hive, path, view

    def OpenKey(self, hive, path, reserved=0, access=1):
        target = self.resolve(hive, path, access)
        if target in self.denied:
            raise PermissionError("denied")
        if target not in self.keys:
            raise FileNotFoundError(path)
        return Key(*target)

    def children(self, key):
        prefix = key.path + "\\"
        return sorted(p[len(prefix):] for h, p, v in self.keys if h == key.hive and v == key.view and p.startswith(prefix) and "\\" not in p[len(prefix):])

    def QueryInfoKey(self, key):
        return len(self.children(key)), len(self.keys[key.hive, key.path, key.view]), 0

    def EnumKey(self, key, index):
        return self.children(key)[index]

    def EnumValue(self, key, index):
        return self.keys[key.hive, key.path, key.view][index]

    def DeleteKeyEx(self, hive, path, view, reserved):
        if self.before_delete:
            self.before_delete(path)
        key = self.OpenKey(hive, path, 0, view)
        if self.children(key):
            raise OSError("unexpected child")
        del self.keys[hive, path, view]

    def CreateKeyEx(self, hive, path, reserved=0, access=2):
        target = self.resolve(hive, path, access)
        self.keys.setdefault(target, [])
        return Key(*target)

    def SetValueEx(self, key, name, reserved, kind, data):
        self.keys[key.hive, key.path, key.view].append([name, data, kind])


class SoftwareRegistryTest(unittest.TestCase):
    def setUp(self):
        self.fixtures = Path(__file__).resolve().parents[1] / "artifacts" / "test-fixtures"
        self.fixtures.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.fixtures)
        self.storage = Storage(Path(self.temp.name) / "data")
        self.cleaner = RegistryCleaner(self.storage)
        self.api = MemoryRegistry()
        self.cleaner.api = self.api
        self.software = SoftwareRegistry(self.cleaner)
        self.path = r"SOFTWARE\OldStudio\OldApp"
        self.values = [["InstallDir", r"C:\Gone\OldApp", 1], ["ExecutablePath", r"C:\Gone\OldApp\app.exe", 1]]
        self.api.add(2, self.path, self.values)
        self.api.add(2, self.path + r"\Preferences", [["Theme", "dark", 1], ["Count", 42, 4], ["Raw", b"\x00\xff", 3], ["List", ["one", "two"], 7]])
        self.missing = patch("scleaner.software_registry.definitely_missing", return_value=True)
        self.missing.start()

    def tearDown(self):
        self.missing.stop()
        self.assertTrue(Path(self.temp.name).resolve().is_relative_to(self.fixtures.resolve()))
        self.temp.cleanup()

    def scan(self):
        return self.software.scan(threading.Event(), lambda *a: None)[0]

    def test_detects_vendor_product_subtree_without_selecting_it(self):
        found = self.scan()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].registry["key"], self.path)
        self.assertFalse(found[0].selected)
        self.assertIn("ключей: 2", found[0].details)

    def test_top_level_application_and_both_views_are_independent(self):
        for view in (256, 512):
            self.api.add(2, r"SOFTWARE\OtherApp", self.values, view)
        items = [f for f in self.scan() if f.label == "OtherApp"]
        self.assertEqual({f.registry["view"] for f in items}, {32, 64})
        for item in items:
            item.selected = True
        engine = Engine(Catalog({}), self.storage, lambda: set())
        engine.registry = self.cleaner
        result = engine.clean(items, {})
        self.assertEqual(result.removed, 2)

    def test_backup_precedes_every_delete_and_restores_exact_tree(self):
        item = self.scan()[0]
        original = copy.deepcopy(self.api.keys)
        def check(path):
            self.assertEqual(len(list(self.storage.root.rglob("*.reg"))), 1)
            backup = json.loads(next(self.storage.root.rglob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(set(backup["tree"]), {"", "Preferences"})
        self.api.before_delete = check
        backup = self.cleaner.remove(item, "test")
        self.assertIn((2, r"SOFTWARE\OldStudio", 256), self.api.keys)
        self.assertNotIn((2, self.path, 256), self.api.keys)
        self.cleaner.restore(backup)
        self.assertEqual(self.api.keys, original)
        with self.assertRaisesRegex(ValueError, "уже существует"):
            self.cleaner.restore(backup)

    def test_existing_or_inaccessible_path_blocks_candidate(self):
        with patch("scleaner.software_registry.definitely_missing", side_effect=lambda p: not p.endswith("app.exe")):
            self.assertEqual(self.scan(), [])

    def test_no_guessing_from_empty_key_name_or_uninstall_absence(self):
        self.api.keys[2, self.path, 256] = [["DisplayName", "Old App", 1]]
        self.assertEqual(self.scan(), [])

    def test_executable_must_belong_to_install_directory(self):
        self.api.keys[2, self.path, 256][1][1] = r"C:\Other\app.exe"
        self.assertEqual(self.scan(), [])

    def test_unrecognized_command_blocks_candidate(self):
        self.api.keys[2, self.path, 256].append(["UninstallString", "rundll32 something", 1])
        self.assertEqual(self.scan(), [])

    def test_live_path_in_child_blocks_whole_subtree(self):
        self.api.keys[2, self.path + r"\Preferences", 256].append(["Data", r"C:\Live\data", 1])
        with patch("scleaner.software_registry.definitely_missing", side_effect=lambda p: "live" not in p):
            self.assertEqual(self.scan(), [])

    def test_installed_product_in_either_hive_or_view_blocks_candidate(self):
        for hive in (1, 2):
            for view in (256, 512):
                with self.subTest(hive=hive, view=view):
                    self.api.add(hive, UNINSTALL + r"\Current", [["DisplayName", "Old App 2.0", 1]], view)
                    self.assertEqual(self.scan(), [])
                    del self.api.keys[hive, UNINSTALL + r"\Current", view]

    def test_installed_path_or_vendor_blocks_even_when_product_name_differs(self):
        for values in ([["InstallLocation", r"C:\Gone\OldApp", 1]], [["Publisher", "OldStudio", 1]]):
            self.api.add(1, UNINSTALL + r"\Current", values)
            self.assertEqual(self.scan(), [])

    def test_incomplete_inventory_fails_closed(self):
        self.api.denied.add((1, UNINSTALL, 512))
        found, notes = self.software.scan(threading.Event(), lambda *a: None)
        self.assertEqual(found, [])
        self.assertTrue(notes)

    def test_protected_roots_flags_links_and_inaccessible_children_are_skipped(self):
        self.api.add(2, r"SOFTWARE\Microsoft\OldApp", self.values)
        for extra in (["SystemComponent", 1, 4], ["Publisher", "Microsoft Corporation", 1], ["SymbolicLinkValue", b"target", 6]):
            self.api.keys[2, self.path, 256] = self.values + [extra]
            self.assertEqual(self.scan(), [])
        self.api.keys[2, self.path, 256] = self.values
        self.api.denied.add((2, self.path + r"\Preferences", 256))
        self.assertEqual(self.scan(), [])

    def test_tree_limits_and_cancel_prevent_partial_findings(self):
        with patch("scleaner.software_registry.MAX_KEYS", 1):
            self.assertEqual(self.scan(), [])
        cancel = threading.Event()
        cancel.set()
        self.assertEqual(self.software.scan(cancel, lambda *a: None)[0], [])

    def test_changed_subtree_is_not_deleted(self):
        item = self.scan()[0]
        self.api.add(2, self.path + r"\NewChild", [])
        with self.assertRaisesRegex(ValueError, "изменился"):
            self.cleaner.remove(item, "test")
        self.assertIn((2, self.path, 256), self.api.keys)

    def test_reinstalled_app_is_not_deleted(self):
        item = self.scan()[0]
        self.api.add(2, UNINSTALL + r"\Current", [["DisplayName", "OldApp", 1]])
        with self.assertRaises(ValueError):
            self.cleaner.remove(item, "test")

    def test_backup_write_failure_prevents_all_mutation(self):
        item, original = self.scan()[0], copy.deepcopy(self.api.keys)
        with patch("pathlib.Path.write_text", side_effect=PermissionError("disk full")):
            with self.assertRaises(PermissionError):
                self.cleaner.remove(item, "test")
        self.assertEqual(self.api.keys, original)

    def test_partial_delete_keeps_backup_in_history_and_can_restore(self):
        item, original = self.scan()[0], copy.deepcopy(self.api.keys)
        item.selected = True
        def fail_root(path):
            if path == self.path:
                raise PermissionError("root locked")
        self.api.before_delete = fail_root
        engine = Engine(Catalog({}), self.storage, lambda: set())
        engine.registry = self.cleaner
        result = engine.clean([item], {})
        self.assertEqual((result.removed, result.skipped), (0, 1))
        self.assertEqual(len(result.backups), 1)
        self.assertEqual(self.storage.history()[0]["backups"], result.backups)
        self.cleaner.restore(result.backups[0])
        self.assertEqual(self.api.keys, original)

    def test_change_after_backup_stops_deletion(self):
        item, write = self.scan()[0], self.storage.write_json
        def save_then_change(path, value):
            write(path, value)
            self.api.add(2, self.path + r"\Appeared", [])
        with patch.object(self.storage, "write_json", side_effect=save_then_change):
            with self.assertRaises(RegistryRemovalError):
                self.cleaner.remove(item, "test")
        self.assertIn((2, self.path + r"\Preferences", 256), self.api.keys)

    def test_32_bit_reg_export_and_restore_use_correct_view(self):
        self.api.add(2, r"SOFTWARE\OtherApp", self.values, 512)
        item = next(f for f in self.scan() if f.registry["view"] == 32)
        backup = self.cleaner.remove(item, "test")
        text = Path(backup).with_suffix(".reg").read_text(encoding="utf-16")
        self.assertIn(r"HKEY_LOCAL_MACHINE\SOFTWARE\Wow6432Node\OtherApp", text)
        self.cleaner.restore(backup)
        self.assertIn((2, r"SOFTWARE\OtherApp", 512), self.api.keys)
        self.assertNotIn((2, r"SOFTWARE\OtherApp", 256), self.api.keys)

    def test_restore_rejects_changed_existing_data_and_tampered_paths(self):
        item = self.scan()[0]
        backup = self.cleaner.remove(item, "test")
        self.api.add(2, self.path, [["Keep", "new data", 1]])
        with self.assertRaisesRegex(ValueError, "отличается"):
            self.cleaner.restore(backup)
        data = json.loads(Path(backup).read_text(encoding="utf-8"))
        data["tree"][r"..\Escape"] = []
        data["digest"] = tree_digest(data["tree"])
        self.storage.write_json(Path(backup), data)
        with self.assertRaisesRegex(ValueError, "путь"):
            self.cleaner.restore(backup)

    def test_scope_rejects_arbitrary_hives_and_protected_paths(self):
        meta = self.scan()[0].registry
        for key in ("SOFTWARE", r"SOFTWARE\Classes\App", r"SOFTWARE\Microsoft\App", "SOFTWARE\\" + "\\".join(["Deep"] * 10), r"SOFTWARE\A\..", "SOFTWARE\\A\nB"):
            with self.assertRaises(ValueError):
                self.cleaner._parts({**meta, "key": key})
        with self.assertRaises(ValueError):
            self.cleaner._parts({**meta, "hive": "HKEY_CURRENT_USER"})

    def add_nested_paths(self, view=256):
        self.api.add(2, r"SOFTWARE\Example Audio\Shared\Paths", [
            ["VST plugins", r"C:\Program Files\Common Files\VST2", 1],
            ["Music Editor", r"C:\Program Files\Example Audio\Music Editor 2025\Editor64.exe", 1],
            ["Processing engine", r"C:\Program Files\Example Audio\Music Editor 2025\AudioEngine.dll", 1],
            ["Company folder", "C:\\Program Files\\Example Audio\\", 1],
            ["Install path", r"C:\Program Files\Example Audio\Music Editor 2025", 1],
        ], view)

    def test_generic_nested_paths_detected_in_both_views_and_restored(self):
        for view in (256, 512):
            self.add_nested_paths(view)
        original = copy.deepcopy(self.api.keys)
        with patch.dict(os.environ, {"ProgramFiles": r"C:\Program Files"}), patch("scleaner.software_registry.definitely_missing", side_effect=lambda p: "vst2" not in p):
            items = [f for f in self.scan() if f.label == "Example Audio"]
            self.assertEqual(len(items), 2)
            for item in items:
                self.assertFalse(item.selected)
                backup = self.cleaner.remove(item, "test")
                self.cleaner.restore(backup)
        self.assertEqual(self.api.keys, original)

    def test_unrelated_product_or_license_data_blocks_vendor_deletion(self):
        self.add_nested_paths()
        original = copy.deepcopy(self.api.keys)
        for key, values in ((r"SOFTWARE\Example Audio\OtherProduct", [["License", "keep", 1]]), (r"SOFTWARE\Example Audio", [["License", "keep", 1]])):
            with self.subTest(key=key):
                self.api.keys = copy.deepcopy(original)
                self.api.add(2, key, values)
                self.assertFalse(any(f.label == "Example Audio" for f in self.scan()))

    def test_existing_library_or_registered_product_blocks_deletion(self):
        self.add_nested_paths()
        with patch("scleaner.software_registry.definitely_missing", side_effect=lambda p: not p.endswith(".dll")):
            self.assertFalse(any(f.label == "Example Audio" for f in self.scan()))
        self.api.add(1, UNINSTALL + r"\Current", [["DisplayName", "Music Editor 2026", 1]])
        self.assertFalse(any(f.path.startswith(r"HKEY_LOCAL_MACHINE\SOFTWARE\Example Audio") for f in self.scan()))

    def test_arbitrary_application_value_names_and_default_values(self):
        for name, directory_field, exe_field, binary in (("RenderTool", "Install Folder", "Custom launch target", "render.exe"),
                ("NoteMaker", "Home", "", "notes.exe"), ("Reader 3.5", "Unknown directory", "Reader", "view.exe")):
            path = "SOFTWARE\\" + name
            self.api.add(2, path, [[directory_field, "C:\\Apps\\" + name, 1], [exe_field, '"C:\\Apps\\' + name + '\\' + binary + '" /open', 1]])
            self.assertTrue(any(f.registry["key"] == path for f in self.scan()))

    def test_environment_variables_and_multistring_paths(self):
        self.api.add(2, r"SOFTWARE\ListApp", [["Files", [r"%TEST_APP_ROOT%\ListApp", r"%TEST_APP_ROOT%\ListApp\list.exe", r"%TEST_APP_ROOT%\ListApp\engine.dll"], 7]])
        with patch.dict(os.environ, {"TEST_APP_ROOT": r"C:\Gone"}):
            self.assertTrue(any(f.label == "ListApp" for f in self.scan()))
        with patch("scleaner.software_registry.definitely_missing", side_effect=lambda p: "engine.dll" not in p), patch.dict(os.environ, {"TEST_APP_ROOT": r"C:\Gone"}):
            self.assertFalse(any(f.label == "ListApp" for f in self.scan()))

    def test_expanded_install_path_and_icon_with_resource_index(self):
        self.api.add(2, r"SOFTWARE\IconApp", [["Install Path", r"%TEST_APP_ROOT%\IconApp", 2], ["DisplayIcon", r'"%TEST_APP_ROOT%\IconApp\icon.exe",0', 2]])
        with patch.dict(os.environ, {"TEST_APP_ROOT": r"C:\Gone"}):
            self.assertTrue(any(f.label == "IconApp" for f in self.scan()))

    def test_paths_in_different_nested_nodes_can_identify_same_application(self):
        self.api.add(2, r"SOFTWARE\NestedApp\Install", [["Install path", r"C:\Apps\NestedApp", 1]])
        self.api.add(2, r"SOFTWARE\NestedApp\Runtime", [["Executable", r"C:\Apps\NestedApp\run.exe", 1]])
        items = [f for f in self.scan() if f.registry["key"].startswith(r"SOFTWARE\NestedApp")]
        self.assertEqual([f.registry["key"] for f in items], [r"SOFTWARE\NestedApp"])

    def test_deeply_nested_application_is_found_removed_and_restored(self):
        path = r"SOFTWARE\Acme\Products\Desktop\Tools\OldEditor"
        self.api.add(2, path, self.values)
        item = next(f for f in self.scan() if f.registry["key"] == path)
        backup = self.cleaner.remove(item, "deep")
        self.assertNotIn((2, path, 256), self.api.keys)
        self.cleaner.restore(backup)
        self.assertEqual(self.api.keys[2, path, 256], self.values)

    def test_deleted_product_can_be_found_beside_installed_sibling(self):
        live_path = r"SOFTWARE\OldStudio\CurrentProduct"
        self.api.add(2, live_path, [["InstallDir", r"C:\Apps\CurrentProduct", 1], ["Executable", r"C:\Apps\CurrentProduct\current.exe", 1]])
        self.api.add(2, UNINSTALL + r"\Current", [["DisplayName", "CurrentProduct", 1], ["InstallLocation", r"C:\Apps\CurrentProduct", 1]])
        with patch("scleaner.software_registry.definitely_missing", side_effect=lambda p: "currentproduct" not in p):
            items = self.scan()
            self.assertEqual([f.registry["key"] for f in items], [self.path])
            backup = self.cleaner.remove(items[0], "test")
        self.assertIn((2, live_path, 256), self.api.keys)
        self.assertTrue(Path(backup).is_file())

    def test_parent_installation_and_protection_are_checked_for_nested_candidates(self):
        path = r"SOFTWARE\Container\Version\App"
        self.api.add(2, path, self.values)
        item = next(f for f in self.scan() if f.registry["key"] == path)
        for values in ([["SystemComponent", 1, 4]], [["Publisher", "InstalledVendor", 1]]):
            self.api.add(2, r"SOFTWARE\Container", values)
            self.api.add(2, UNINSTALL + r"\Live", [["Publisher", "InstalledVendor", 1]])
            self.assertFalse(any(f.registry["key"] == path for f in self.scan()))
            with self.assertRaises(ValueError):
                self.cleaner.remove(item, "blocked")

    def test_missing_document_paths_without_an_application_are_not_candidates(self):
        self.api.add(2, r"SOFTWARE\RecentDocuments", [["Folder", r"C:\Gone\Documents", 1], ["Recent file", r"C:\Gone\Documents\report.pdf", 1]])
        self.assertFalse(any(f.label == "RecentDocuments" for f in self.scan()))

    def test_unknown_or_relative_install_paths_fail_closed(self):
        for value in (r"%UNKNOWN_REGISTRY_TEST_VAR%\OldApp\run.exe", r"OldApp\run.exe"):
            self.api.add(2, r"SOFTWARE\UncertainApp", [["InstallDir", r"C:\Gone\OldApp", 1], ["Executable", value, 1]])
            with patch("scleaner.software_registry.definitely_missing", side_effect=lambda p: p.startswith("c:\\")):
                self.assertFalse(any(f.label == "UncertainApp" for f in self.scan()))

    def review_scan(self):
        return self.software.scan(threading.Event(), lambda *a: None, include_review=True)

    def test_names_match_whole_words_and_versions_without_substrings(self):
        for first, second in (("OldApp", "Old App 2.0"), ("Music Editor", "Music Editor 4 x64"), ("Studio Group", "Studio Group Ltd."), ("Mix-App", "Mix App")):
            with self.subTest(first=first, second=second):
                self.assertTrue(name_matches(first, second))
                self.assertTrue(name_matches(second, first))
        for first, second in (("DigitalWorks", "Git"), ("AcmeTools", "MeToo"), ("Media", "Multimedia"), ("", "App")):
            with self.subTest(first=first, second=second):
                self.assertFalse(name_matches(first, second))
                self.assertFalse(name_matches(second, first))

    def test_unrelated_installed_name_does_not_hide_review_or_automatic_keys(self):
        self.api.add(2, UNINSTALL + r"\Git", [["DisplayName", "Git", 1]])
        self.add_review_item(r"SOFTWARE\DigitalWorks", [])
        self.api.add(2, r"SOFTWARE\DigitalEditor", self.values)
        found, _ = self.review_scan()
        self.assertTrue(any(f.label == "DigitalWorks" and f.registry.get("review_required") for f in found))
        self.assertTrue(any(f.label == "DigitalEditor" and not f.registry.get("review_required") for f in found))

    def add_review_item(self, path=r"SOFTWARE\FormerApp", values=None):
        self.api.add(2, path, values if values is not None else [["Theme", "dark", 1]])
        return next(f for f in self.review_scan()[0] if f.registry["key"] == path)

    def test_empty_and_settings_only_keys_are_visible_as_unverified(self):
        for path, values in ((r"SOFTWARE\FormerAudio", []), (r"SOFTWARE\FormerMedia", []), (r"SOFTWARE\FormerUtility", [["Version", "1.0", 1]])):
            item = self.add_review_item(path, values)
            self.assertTrue(item.registry["review_required"])
            self.assertFalse(item.selected)
            self.assertIn("ручной проверки", item.details)
            self.assertFalse(any(f.registry["key"] == path for f in self.scan()))

    def test_single_install_path_is_available_for_review(self):
        item = self.add_review_item(values=[["InstallDir", r"C:\Gone\FormerApp", 1]])
        self.assertIn("исполняемому файлу", item.details)

    def test_unverified_key_requires_explicit_confirmation_and_restores(self):
        item = self.add_review_item()
        with self.assertRaisesRegex(ValueError, "подтверждение"):
            self.cleaner.remove(item, "test")
        self.assertIn((2, item.registry["key"], 256), self.api.keys)
        backup = self.cleaner.remove(item, "test", allow_unverified=True)
        self.assertTrue(json.loads(Path(backup).read_text(encoding="utf-8"))["user_confirmed_uninstall"])
        self.cleaner.restore(backup)
        self.assertEqual(self.api.keys[2, item.registry["key"], 256], [["Theme", "dark", 1]])

    def test_engine_confirmation_is_specific_to_selected_ids_and_not_settings(self):
        first = self.add_review_item(r"SOFTWARE\FormerOne")
        second = self.add_review_item(r"SOFTWARE\FormerTwo")
        first.selected = second.selected = True
        engine = Engine(Catalog({}), self.storage, lambda: set())
        engine.registry = self.cleaner
        result = engine.clean([first, second], {"allow_unverified": True, "confirmed_registry_ids": [second.id]}, confirmed_registry_ids=[first.id])
        self.assertEqual((result.removed, result.skipped), (1, 1))
        self.assertIn((2, second.registry["key"], 256), self.api.keys)
        log = Path(result.history_path).with_suffix(".jsonl").read_text(encoding="utf-8")
        self.assertTrue(json.loads(log.splitlines()[0])["user_confirmed_uninstall"])

    def test_confirmation_does_not_override_installed_app_or_new_paths(self):
        item = self.add_review_item()
        self.api.add(2, UNINSTALL + r"\FormerApp", [["DisplayName", "FormerApp", 1]])
        self.assertFalse(any(f.registry["key"] == item.registry["key"] for f in self.review_scan()[0]))
        with self.assertRaisesRegex(ValueError, "установленным"):
            self.cleaner.remove(item, "test", allow_unverified=True)
        del self.api.keys[2, UNINSTALL + r"\FormerApp", 256]
        self.api.keys[2, item.registry["key"], 256].append(["Executable", r"C:\Live\app.exe", 1])
        with self.assertRaisesRegex(ValueError, "изменился"):
            self.cleaner.remove(item, "test", allow_unverified=True)

    def test_live_unknown_protected_and_unreadable_paths_are_not_reviewable(self):
        self.api.add(2, r"SOFTWARE\CurrentApp", [["Path", r"C:\Live\App", 1]])
        self.api.add(2, r"SOFTWARE\UncertainApp", [["Path", r"%UNDEFINED_APP_VAR%\app", 1]])
        self.api.add(2, r"SOFTWARE\ComponentApp", [["SystemComponent", 1, 4]])
        self.api.add(2, r"SOFTWARE\LockedApp", [])
        self.api.denied.add((2, r"SOFTWARE\LockedApp", 256))
        with patch("scleaner.software_registry.definitely_missing", side_effect=lambda p: "live" not in p):
            found, notes = self.review_scan()
        names = {f.label for f in found}
        self.assertFalse(names & {"CurrentApp", "UncertainApp", "ComponentApp", "LockedApp"})
        self.assertTrue(any("CurrentApp" in n and "существует" in n for n in notes))
        self.assertTrue(any("LockedApp" in n and "denied" in n for n in notes))

    def test_empty_parent_of_installed_product_is_not_reviewable(self):
        self.api.add(2, r"SOFTWARE\VendorGroup\CurrentProduct", [])
        self.api.add(2, UNINSTALL + r"\CurrentProduct", [["DisplayName", "CurrentProduct", 1]])
        found, _ = self.review_scan()
        self.assertFalse(any(f.registry["key"].startswith(r"SOFTWARE\VendorGroup") for f in found))

    def test_review_and_automatic_findings_do_not_overlap(self):
        self.api.add(2, r"SOFTWARE\OldStudio\FormerSettings", [["Theme", "dark", 1]])
        found, _ = self.review_scan()
        paths = [f.registry["key"] for f in found]
        self.assertIn(self.path, paths)
        self.assertIn(r"SOFTWARE\OldStudio\FormerSettings", paths)
        self.assertTrue(all(not a.startswith(b + "\\") for a in paths for b in paths if a != b))

    def test_review_backup_failure_and_partial_delete_keep_recovery(self):
        self.api.add(2, r"SOFTWARE\FormerApp\Settings", [])
        item = self.add_review_item()
        original = copy.deepcopy(self.api.keys)
        with patch.object(self.storage, "write_json", side_effect=PermissionError("full")):
            with self.assertRaises(PermissionError):
                self.cleaner.remove(item, "test", allow_unverified=True)
        self.assertEqual(original, self.api.keys)
        def fail_root(path):
            if path == item.registry["key"]:
                raise PermissionError("locked")
        self.api.before_delete = fail_root
        with self.assertRaises(RegistryRemovalError) as caught:
            self.cleaner.remove(item, "test", allow_unverified=True)
        self.cleaner.restore(caught.exception.backup_path)
        self.assertEqual(original, self.api.keys)

    def test_slash_in_registry_key_name_can_be_backed_up_and_restored(self):
        path = r"SOFTWARE\PluginHost\@vendor/component"
        self.api.add(2, path, self.values)
        item = next(f for f in self.scan() if f.registry["key"] == path)
        backup = self.cleaner.remove(item, "test")
        self.cleaner.restore(backup)
        self.assertEqual(self.api.keys[2, path, 256], self.values)


if __name__ == "__main__":
    unittest.main()
