from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from scleaner import elevation


class ElevationTest(unittest.TestCase):
    def setUp(self):
        self.admin = patch.object(elevation, "is_admin", return_value=False).start()
        self.runas = patch.object(elevation, "_runas").start()
        self.frozen = patch.object(elevation.sys, "frozen", False, create=True).start()
        self.addCleanup(patch.stopall)

    def test_source_launch_preserves_relative_data_and_uses_importable_directory(self):
        data = Path("artifacts") / "Данные с пробелами"
        self.assertFalse(elevation.ensure_admin(data))
        executable, arguments, directory = self.runas.call_args.args
        self.assertEqual(executable, elevation.sys.executable)
        self.assertEqual(arguments, ["-m", "scleaner", "--data-dir", str(data.resolve()), "--elevated"])
        self.assertTrue((Path(directory) / "scleaner" / "__main__.py").is_file())

    def test_frozen_launch_uses_executable_without_python_arguments(self):
        exe = str(Path.cwd() / "Папка приложения" / "SCleaner.exe")
        with patch.object(elevation.sys, "frozen", True), patch.object(elevation.sys, "executable", exe):
            self.assertFalse(elevation.ensure_admin(Path("artifacts/data")))
        executable, arguments, directory = self.runas.call_args.args
        self.assertEqual(executable, exe)
        self.assertEqual(arguments, ["--data-dir", str(Path("artifacts/data").resolve()), "--elevated"])
        self.assertEqual(directory, str(Path(exe).parent))

    def test_default_data_stays_with_original_user(self):
        original_data = Path.cwd() / "artifacts" / "Original User" / "SCleaner"
        with patch.object(elevation, "data_directory", return_value=original_data):
            self.assertFalse(elevation.ensure_admin())
        self.assertIn(str(original_data), self.runas.call_args.args[1])

    def test_test_license_configuration_survives_uac_with_absolute_path(self):
        config = Path("artifacts") / "Тест Pro" / "client-test.json"
        self.assertFalse(elevation.ensure_admin(Path("artifacts/pro-data"), license_test_config=config))
        arguments = self.runas.call_args.args[1]
        self.assertEqual(arguments[-2:], ["--license-test-config", str(config.resolve())])

    def test_already_admin_starts_without_relaunch(self):
        self.admin.return_value = True
        self.assertTrue(elevation.ensure_admin(elevated=True))
        self.runas.assert_not_called()

    def test_screenshot_does_not_request_admin(self):
        self.assertTrue(elevation.ensure_admin(screenshot=True))
        self.runas.assert_not_called()

    def test_child_without_admin_stops_instead_of_looping(self):
        with self.assertRaisesRegex(OSError, "не предоставила права"):
            elevation.ensure_admin(elevated=True)
        self.runas.assert_not_called()

    def test_relaunch_errors_are_propagated(self):
        error = OSError("launch failed")
        self.runas.side_effect = error
        with self.assertRaises(OSError) as raised:
            elevation.ensure_admin()
        self.assertIs(raised.exception, error)


@unittest.skipUnless(os.name == "nt", "Windows Shell API")
class ShellLaunchTest(unittest.TestCase):
    def test_unicode_quoting_uac_verb_and_com_cleanup(self):
        shell, ole = MagicMock(), MagicMock()
        ole.CoInitializeEx.return_value = 0
        arguments = ["--data-dir", 'C:\\Данные с пробелами\\', '--elevated']
        captured = []

        def execute(pointer):
            info = ctypes.cast(pointer, ctypes.POINTER(elevation.ShellExecuteInfo)).contents
            captured.append((info.lpVerb, info.lpFile, info.lpParameters, info.lpDirectory, info.fMask))
            return True

        shell.ShellExecuteExW.side_effect = execute
        with patch.object(elevation.ctypes, "WinDLL", side_effect=[shell, ole]):
            elevation._runas("C:\\Program Files\\SCleaner.exe", arguments, "C:\\Program Files")
        self.assertEqual(captured, [("runas", "C:\\Program Files\\SCleaner.exe", subprocess.list2cmdline(arguments),
                                    "C:\\Program Files", 0x500)])
        ole.CoUninitialize.assert_called_once_with()

    def test_uac_cancellation_preserves_error_code_and_cleans_up_com(self):
        shell, ole = MagicMock(), MagicMock()
        shell.ShellExecuteExW.return_value = False
        ole.CoInitializeEx.return_value = 0
        with patch.object(elevation.ctypes, "WinDLL", side_effect=[shell, ole]), \
             patch.object(elevation.ctypes, "get_last_error", return_value=1223):
            with self.assertRaises(OSError) as raised:
                elevation._runas("SCleaner.exe", [], str(Path.cwd()))
        self.assertEqual(raised.exception.winerror, 1223)
        ole.CoUninitialize.assert_called_once_with()


class StartupTest(unittest.TestCase):
    def test_parent_exits_before_window_or_lock_creation(self):
        from scleaner import app
        with patch.object(app.sys, "argv", ["SCleaner.exe"]), \
             patch.object(app, "ensure_admin", return_value=False), \
             patch.object(app, "QApplication") as qt, patch.object(app, "QLockFile") as lock, \
             patch.object(app, "MainWindow") as window:
            self.assertEqual(app.main(), 0)
        qt.assert_not_called()
        lock.assert_not_called()
        window.assert_not_called()

    def test_cancelled_uac_exits_without_opening_a_window(self):
        from scleaner import app
        error = OSError("cancelled")
        error.winerror = 1223
        with patch.object(app.sys, "argv", ["SCleaner.exe"]), \
             patch.object(app, "ensure_admin", side_effect=error), \
             patch.object(app, "QApplication") as qt, patch.object(app, "MainWindow") as window:
            self.assertEqual(app.main(), 1)
        qt.assert_not_called()
        window.assert_not_called()

    def test_launch_failure_shows_error_without_creating_main_window(self):
        from scleaner import app
        with patch.object(app.sys, "argv", ["SCleaner.exe"]), \
             patch.object(app, "ensure_admin", side_effect=OSError("Cannot launch")), \
             patch.object(app, "QApplication"), patch.object(app.QMessageBox, "critical") as message, \
             patch.object(app, "MainWindow") as window:
            self.assertEqual(app.main(), 1)
        self.assertIn("Cannot launch", message.call_args.args[2])
        window.assert_not_called()


if __name__ == "__main__":
    unittest.main()
