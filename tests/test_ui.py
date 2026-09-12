import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QListWidget, QPushButton

from scleaner.app import MainWindow
from scleaner.appearance import DEFAULT_ACCENT
from scleaner.theme import stylesheet
from scleaner.catalog import Catalog
from scleaner.engine import Engine
from scleaner.models import CleanResult, Finding, ScanResult
from scleaner.storage import Storage

APP = QApplication.instance() or QApplication([])
APP.setStyle("Fusion")
for font in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
    QFontDatabase.addApplicationFont(str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts" / font))


class UITest(unittest.TestCase):
    def setUp(self):
        self.fixtures = Path(__file__).resolve().parents[1] / "artifacts" / "test-fixtures"
        self.fixtures.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.fixtures)
        self.root = Path(self.temp.name)
        self.local = self.root / "Local"
        cache = self.local / "Temp"
        cache.mkdir(parents=True)
        self.files = [cache / "old-cache-a.tmp", cache / "old-cache-b.tmp"]
        for file in self.files:
            file.write_bytes(b"temporary test contents")
            stamp = time.time() - 86400 * 3
            os.utime(file, (stamp, stamp))
        storage = Storage(self.root / "data")
        engine = Engine(Catalog({"LOCALAPPDATA": str(self.local)}), storage, lambda: set())
        self.window = MainWindow(storage, engine)
        self.messages = []
        self.window.message = lambda *args, **kwargs: self.messages.append(args)
        self.window.show()
        QTest.qWait(20)

    def tearDown(self):
        if self.window.busy:
            self.window.cancel_operation()
            self.wait_worker()
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        self.assertTrue(self.root.resolve().is_relative_to(self.fixtures.resolve()))
        self.temp.cleanup()

    def wait_worker(self):
        deadline = time.monotonic() + 10
        while self.window.busy and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(self.window.busy, "Background operation timed out")

    def test_navigation_and_minimum_window_layout(self):
        for index, nav in enumerate(self.window.nav):
            nav.click()
            APP.processEvents()
            self.assertEqual(self.window.stack.currentIndex(), index)
            self.assertTrue(nav.isChecked())
        self.window.resize(1180, 780)
        self.window.select_page(1)
        APP.processEvents()
        self.assertGreater(self.window.clean_table.table.height(), 200)

    def test_category_controls_stay_in_sync(self):
        self.window.overview_checks["browsers"].click()
        self.assertFalse(self.window.clean_checks["browsers"].isChecked())
        self.window.clean_checks["browsers"].click()
        self.assertTrue(self.window.overview_checks["browsers"].isChecked())

    def test_real_scan_select_confirm_and_cleanup_flow(self):
        self.assertFalse(self.window.clean_button.isEnabled())
        self.window.hero_scan.click()
        self.wait_worker()
        self.assertEqual(len(self.window.clean_table.model.items), 2)
        self.assertTrue(self.window.clean_button.isEnabled())
        self.window.clean_table.search.setText("old-cache-a")
        self.assertEqual(self.window.clean_table.proxy.rowCount(), 1)
        self.window.clean_table.select_visible(False)
        self.assertEqual(sum(f.selected for f in self.window.results["clean"].findings), 1)
        # Rejecting the confirmation must not perform a deletion.
        self.window.confirm = lambda *args: False
        self.window.clean_button.click()
        self.assertTrue(all(f.exists() for f in self.files))
        self.window.confirm = lambda *args: True
        self.window.clean_button.click()
        self.wait_worker()
        self.assertTrue(self.files[0].exists())
        self.assertFalse(self.files[1].exists())
        self.assertEqual(self.window.storage.history()[0]["removed"], 1)
        self.assertFalse(self.window.clean_button.isEnabled())
        self.assertTrue(self.messages)

    def test_table_checkbox_and_filter_do_not_change_hidden_selection(self):
        self.window.start_scan()
        self.wait_worker()
        table = self.window.clean_table
        index = table.model.index(0, 0)
        self.assertTrue(table.model.setData(index, Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole))
        self.assertFalse(table.model.items[0].selected)
        table.search.setText("no match")
        table.select_visible(False)
        self.assertTrue(table.model.items[1].selected)

    def test_settings_are_saved(self):
        self.window.age.setValue(72)
        self.window.exclusions.addItem(str(self.local))
        self.window.save_settings()
        stored = self.window.storage.read_settings()
        self.assertEqual(stored["age_hours"], 72)
        self.assertEqual(stored["exclusions"], [str(self.local)])

    def review_item(self, name="FormerApp"):
        return Finding("registry", name, "HKEY_LOCAL_MACHINE\\SOFTWARE\\" + name, kind="registry", selected=False,
                       registry={"kind": "software", "view": 64, "key_count": 1, "review_required": True})

    def test_review_items_require_individual_selection(self):
        manual = self.review_item()
        regular = Finding("registry", "MissingApp", "test", kind="registry", selected=False)
        self.window.scan_done("leftovers", ScanResult(findings=[manual, regular]))
        table = self.window.left_table
        self.assertEqual(table.model.data(table.model.index(0, 2)), "Ручная проверка")
        table.select_visible(True)
        self.assertFalse(manual.selected)
        self.assertTrue(regular.selected)
        table.model.setData(table.model.index(0, 0), Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        self.assertTrue(manual.selected)
        table.search.setText("MissingApp")
        table.select_visible(False)
        self.assertTrue(manual.selected)
        self.assertFalse(regular.selected)
        table.search.clear()
        table.select_visible(False)
        self.assertFalse(manual.selected)

    def test_review_checkbox_controls_the_scan(self):
        self.assertTrue(self.window.registry_review.isChecked())
        with patch.object(self.window.engine, "scan", return_value=ScanResult()) as scan:
            for enabled in (True, False):
                self.window.registry_review.setChecked(enabled)
                self.window.start_leftover_scan()
                self.wait_worker()
                self.assertEqual(scan.call_args.args[1]["registry_review"], enabled)

    def test_manual_confirmation_gates_action_and_lists_exact_keys(self):
        item = self.review_item()
        states = []
        def interact():
            dialog = APP.activeModalWidget()
            try:
                checkbox = dialog.findChild(QCheckBox, "registryAcknowledgement")
                action = next(b for b in dialog.findChildren(QPushButton) if b.text() == "Начать очистку")
                listing = dialog.findChild(QListWidget)
                states.append((checkbox.isChecked(), action.isEnabled(), listing.item(0).text()))
                checkbox.click()
                states.append(action.isEnabled())
                checkbox.click()
                states.append(action.isEnabled())
                checkbox.click()
                action.click()
            except Exception as error:
                states.append(error)
                if dialog:
                    dialog.reject()
        QTimer.singleShot(20, interact)
        accepted = self.window.confirm("Очистка", "Проверьте выбранные записи.", "Начать очистку", [item])
        self.assertTrue(accepted, states)
        self.assertEqual(states, [(False, False, f"FormerApp · 64 бит\n{item.path}"), True, False])

    def test_confirmation_is_only_forwarded_for_chosen_review_items(self):
        selected, unselected = self.review_item(), self.review_item("AnotherApp")
        selected.selected = True
        self.window.scan_done("leftovers", ScanResult(findings=[selected, unselected]))
        with patch.object(self.window, "confirm", return_value=False) as confirm, patch.object(self.window.engine, "clean", return_value=CleanResult()) as clean:
            self.window.start_clean("leftovers")
            clean.assert_not_called()
            self.assertEqual(confirm.call_args.args[3], [selected])
            confirm.return_value = True
            self.window.start_clean("leftovers")
            self.wait_worker()
            self.assertEqual(clean.call_args.args[0], [selected])
            self.assertEqual(clean.call_args.kwargs["confirmed_registry_ids"], frozenset([selected.id]))

    def test_accent_preview_cancel_save_and_reset(self):
        self.window.accent_button.click()
        dialog = self.window.accent_dialog
        dialog.swatches[6][1].click()
        self.assertEqual(self.window.disk_ring.accent, "#54dfb1")
        dialog.cancel_button.click()
        self.assertEqual(self.window.styleSheet(), stylesheet(DEFAULT_ACCENT))
        self.assertEqual(self.window.storage.read_settings()["accent_color"], DEFAULT_ACCENT)
        self.window.open_accent_dialog()
        self.window.accent_dialog.set_color("#123456")
        self.window.accent_dialog.save_button.click()
        self.assertEqual(self.window.storage.read_settings()["accent_color"], "#123456")
        reloaded = MainWindow(self.window.storage)
        self.assertEqual(reloaded.styleSheet(), stylesheet("#123456"))
        reloaded.close()
        reloaded.deleteLater()
        self.window.open_accent_dialog()
        self.window.accent_dialog.reset_button.click()
        self.window.accent_dialog.save_button.click()
        self.assertEqual(self.window.storage.read_settings()["accent_color"], DEFAULT_ACCENT)

    def test_accent_rejects_invalid_input_and_rolls_back_failed_save(self):
        self.window.open_accent_dialog()
        dialog = self.window.accent_dialog
        dialog.hex_input.selectAll()
        QTest.keyClicks(dialog.hex_input, "#ff")
        self.assertFalse(dialog.save_button.isEnabled())
        dialog.set_color("#ef8a74")
        with patch.object(self.window.storage, "save_settings", side_effect=PermissionError("locked")):
            dialog.save_button.click()
        self.assertEqual(self.window.settings["accent_color"], DEFAULT_ACCENT)
        self.assertEqual(self.window.styleSheet(), stylesheet(DEFAULT_ACCENT))
        self.assertTrue(self.messages)

    def test_corrupt_accent_setting_uses_original_color(self):
        self.window.storage.save_settings({"accent_color": "red; color: transparent"})
        self.assertEqual(self.window.storage.read_settings()["accent_color"], DEFAULT_ACCENT)


if __name__ == "__main__":
    unittest.main()
