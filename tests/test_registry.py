import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scleaner.models import Finding
from scleaner.registry import RegistryCleaner, UNINSTALL, digest, executable, reg_text
from scleaner.storage import Storage


class Key:
    def __init__(self, api, name):
        self.api, self.name = api, name
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


class FakeRegistry:
    HKEY_CURRENT_USER = 1
    HKEY_LOCAL_MACHINE = 2
    KEY_WOW64_64KEY = 256
    KEY_WOW64_32KEY = 512
    KEY_READ = 1
    KEY_WRITE = 2

    def __init__(self, values):
        self.keys = {UNINSTALL + "\\OldApp": values}
        self.before_delete = None

    def OpenKey(self, hive, name, *args):
        if name not in self.keys:
            raise FileNotFoundError(name)
        return Key(self, name)

    def QueryInfoKey(self, key):
        return 0, len(self.keys[key.name]), 0

    def EnumValue(self, key, index):
        return self.keys[key.name][index]

    def DeleteKeyEx(self, hive, name, *args):
        if self.before_delete:
            self.before_delete()
        del self.keys[name]

    def CreateKeyEx(self, hive, name, *args):
        self.keys[name] = []
        return Key(self, name)

    def SetValueEx(self, key, name, reserved, kind, data):
        self.keys[key.name].append([name, data, kind])


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.fixtures = Path(__file__).resolve().parents[1] / "artifacts" / "test-fixtures"
        self.fixtures.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.fixtures)
        self.root = Path(self.temp.name)
        self.storage = Storage(self.root / "SCleaner")
        self.values = [["DisplayName", "Old App", 1], ["InstallLocation", "C:\\Gone", 1], ["UninstallString", '"C:\\Gone\\uninstall.exe" /S', 1]]
        self.registry = RegistryCleaner(self.storage)
        self.registry.api = FakeRegistry(self.values.copy())
        self.item = Finding("registry", "Old App", "registry path", kind="registry", registry={"hive": "HKEY_CURRENT_USER", "key": UNINSTALL + "\\OldApp", "view": 64, "digest": digest(self.values)})

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(self.fixtures.resolve()))
        self.temp.cleanup()

    def test_backup_is_written_before_delete_and_restores_exact_values(self):
        def check_backup():
            copies = list(self.storage.root.rglob("*.reg"))
            self.assertEqual(len(copies), 1)
            self.assertIn("Windows Registry Editor Version 5.00", copies[0].read_text(encoding="utf-16"))
        self.registry.api.before_delete = check_backup
        with patch("scleaner.registry.definitely_missing", return_value=True):
            backup = self.registry.remove(self.item, "test-session")
        self.assertNotIn(self.item.registry["key"], self.registry.api.keys)
        self.registry.restore(backup)
        self.assertEqual(self.registry.api.keys[self.item.registry["key"]], self.values)
        with self.assertRaisesRegex(ValueError, "уже существует"):
            self.registry.restore(backup)

    def test_changed_record_is_not_deleted(self):
        self.registry.api.keys[self.item.registry["key"]].append(["New", "value", 1])
        with patch("scleaner.registry.definitely_missing", return_value=True):
            with self.assertRaisesRegex(ValueError, "изменилась"):
                self.registry.remove(self.item, "test")
        self.assertIn(self.item.registry["key"], self.registry.api.keys)

    def test_backup_failure_prevents_registry_mutation(self):
        with patch.object(self.storage, "write_json", side_effect=PermissionError("locked")), patch("scleaner.registry.definitely_missing", return_value=True):
            with self.assertRaises(PermissionError):
                self.registry.remove(self.item, "test")
        self.assertIn(self.item.registry["key"], self.registry.api.keys)

    def test_protected_components_are_not_candidates(self):
        with patch("scleaner.registry.definitely_missing", return_value=True):
            self.assertTrue(self.registry.candidate(self.values))
            for name, value, kind in (("WindowsInstaller", 1, 4), ("SystemComponent", 1, 4), ("Publisher", "Microsoft Corporation", 1), ("ParentKeyName", "Parent", 1)):
                self.assertFalse(self.registry.candidate(self.values + [[name, value, kind]]))

    def test_non_uninstall_keys_and_external_backups_are_rejected(self):
        for key in ("SOFTWARE\\Important", UNINSTALL, UNINSTALL + "\\App\\Child"):
            with self.assertRaises(ValueError):
                self.registry._parts({**self.item.registry, "key": key})
        with self.assertRaises(ValueError):
            self.registry.restore(str(self.root / "outside.json"))

    def test_command_parser_does_not_guess_relative_paths(self):
        self.assertEqual(executable('"C:\\Program Files\\App\\uninstall.exe" /silent'), "C:\\Program Files\\App\\uninstall.exe")
        self.assertIsNone(executable("rundll32 something"))
        self.assertIsNone(executable('"C:\\App\\uninstall.exe"junk'))

    def test_reg_file_encodes_value_types(self):
        result = reg_text("HKEY_CURRENT_USER", UNINSTALL + "\\Test", [["Text", 'a"b\\c', 1], ["Number", 255, 4], ["List", ["one", "two"], 7], ["Binary", {"bytes": base64.b64encode(b"\x00\xff").decode()}, 3], ["Big", 2**40, 11], ["Expanded", "%LOCALAPPDATA%", 2]])
        self.assertIn('"Number"=dword:000000ff', result)
        self.assertIn('"Binary"=hex:00,ff', result)
        self.assertIn('"List"=hex(7):', result)
        self.assertIn('"Big"=hex(b):', result)


if __name__ == "__main__":
    unittest.main()
