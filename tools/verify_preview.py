"""Read-only real scan and UI render check. Never invokes cleanup or registry writes."""
from pathlib import Path
import json
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from scleaner.app import MainWindow
from scleaner.catalog import DEFAULT_CATEGORIES
from scleaner.engine import Engine
from scleaner.storage import Storage

root = Path(__file__).resolve().parents[1]
artifacts = root / "artifacts"
artifacts.mkdir(exist_ok=True)
storage = Storage(artifacts / "readonly-preview-data")
engine = Engine(storage=storage)
cancel = threading.Event()
timer = threading.Timer(45, cancel.set)
timer.start()
started = time.monotonic()
try:
    scan = engine.scan(DEFAULT_CATEGORIES, storage.read_settings(), cancel)
finally:
    timer.cancel()
summary = {"files": len(scan.findings), "bytes": scan.total_size, "notes": len(scan.notes), "cancelled": scan.cancelled, "seconds": round(time.monotonic() - started, 2), "cleanup_called": False}
(artifacts / "readonly-scan-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(json.dumps(summary))
app = QApplication([])
app.setStyle("Fusion")
for font in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
    QFontDatabase.addApplicationFont(str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts" / font))
window = MainWindow(storage, engine)
window.show()
window.scan_done("clean", scan)
for page, name in ((0, "overview-scanned"), (1, "cleaner"), (2, "leftovers"), (3, "large-files"), (4, "history"), (5, "settings")):
    window.select_page(page)
    QTest.qWait(30)
    if not window.grab().save(str(artifacts / f"{name}.png")):
        raise RuntimeError("Screenshot failed")
window.resize(1180, 780)
window.select_page(1)
QTest.qWait(30)
window.grab().save(str(artifacts / "cleaner-minimum.png"))
window.close()
