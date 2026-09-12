import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from scleaner.app import MainWindow
from scleaner.licensing import LicenseConfig
from scleaner.storage import Storage
from scleaner_server.server import SandboxHTTPServer, SandboxService
from scleaner_server.signing import issue_license

APP = QApplication.instance() or QApplication([])


class ProUITest(unittest.TestCase):
    def setUp(self):
        fixtures = Path(__file__).resolve().parents[1] / "artifacts" / "test-fixtures"
        fixtures.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=fixtures)
        self.root = Path(self.temp.name)
        self.private = Ed25519PrivateKey.generate()
        self.service = SandboxService(self.root / "orders.sqlite3", self.private)
        self.server = SandboxHTTPServer(("127.0.0.1", 0), self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.config = LicenseConfig("test", {"local-test": self.private.public_key().public_bytes_raw()}, self.server.origin)
        self.window = MainWindow(Storage(self.root / "app"), license_config=self.config)
        self.window.show()

    def tearDown(self):
        if self.window.pro_dialog and self.window.pro_dialog.is_busy:
            self.wait_task()
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def wait_task(self):
        deadline = time.monotonic() + 15
        while self.window.pro_dialog.is_busy and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(self.window.pro_dialog.is_busy)

    def test_import_updates_window_and_bad_import_preserves_activation(self):
        self.window.pro_button.click()
        dialog = self.window.pro_dialog
        self.assertEqual(dialog.badge.text(), "Free")
        path = self.root / "test.sclicense"
        document = issue_license(self.private, "local-test", self.window.licenses.device_id())
        path.write_text(json.dumps(document))
        dialog.install_file(path)
        self.assertEqual(dialog.badge.text(), "Pro · тест")
        self.assertEqual(self.window.license_status.text(), "Pro · тест")
        path.write_text('{"claims": {}, "signature": "invalid"}')
        dialog.install_file(path)
        self.assertEqual(dialog.badge.text(), "Pro · тест")
        self.assertIn("неизвестным", dialog.notice.text())
        self.assertTrue(self.window.hero_scan.isEnabled())

    def test_browser_checkout_confirmation_updates_pro_without_restart(self):
        self.window.open_pro_dialog()
        dialog = self.window.pro_dialog
        with patch("scleaner.pro_dialog.QDesktopServices.openUrl", return_value=True) as open_url:
            dialog.buy_button.click()
            self.wait_task()
            token = open_url.call_args.args[0].toString().rsplit("/", 1)[-1]
        dialog.check_button.click()
        self.wait_task()
        self.assertIn("пока не подтверждена", dialog.notice.text())
        self.assertEqual(dialog.badge.text(), "Free")
        self.service.fulfill(token)
        dialog.check_button.click()
        self.wait_task()
        self.assertEqual(dialog.badge.text(), "Pro · тест")
        self.assertTrue(dialog.key_input.text().startswith("SCT-"))
        self.assertEqual(self.window.license_status.text(), "Pro · тест")

    def test_window_cannot_destroy_a_running_activation_thread(self):
        self.window.open_pro_dialog()
        dialog = self.window.pro_dialog
        completed = threading.Event()
        dialog.run_task(lambda: completed.wait(3), lambda _: None, "Проверяем…")
        try:
            dialog.reject()
            self.window.close()
            self.assertTrue(dialog.isVisible())
            self.assertTrue(self.window.isVisible())
            self.assertFalse(dialog.activate_button.isEnabled())
        finally:
            completed.set()
            self.wait_task()
        self.assertTrue(dialog.close_button.isEnabled())

    def test_normal_mode_has_no_purchase_endpoint_and_no_test_activation(self):
        normal = MainWindow(Storage(self.root / "normal"))
        try:
            normal.open_pro_dialog()
            dialog = normal.pro_dialog
            self.assertFalse(dialog.buy_button.isEnabled())
            self.assertFalse(dialog.activate_button.isEnabled())
            self.assertFalse(dialog.import_button.isEnabled())
            self.assertEqual(normal.license_status.text(), "Free")
        finally:
            normal.close()
            normal.deleteLater()


if __name__ == "__main__":
    unittest.main()
