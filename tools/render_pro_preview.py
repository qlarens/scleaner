"""Render the Free/Pro preview without scanning, deleting, or opening a browser."""
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from scleaner.app import MainWindow
from scleaner.licensing import LicenseConfig
from scleaner.storage import Storage
from scleaner_server.signing import issue_license
from tools.license_sandbox import ROOT, initialize


def main():
    output = ROOT / "artifacts" / "pro-preview"
    key, config_path = initialize(output)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    for font in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts" / font))
    # Each render gets a fresh data directory so Free always renders first.
    import uuid
    window = MainWindow(Storage(output / ("data-" + uuid.uuid4().hex)), license_config=LicenseConfig.test_file(config_path))
    window.resize(1180, 780)
    window.show()
    window.open_pro_dialog()
    dialog = window.pro_dialog
    QTest.qWait(150)
    if not dialog.grab().save(str(output / "free-pro.png")):
        raise RuntimeError("Cannot save Free preview")
    window.licenses.install(json.dumps(issue_license(key, "local-test", window.licenses.device_id())).encode())
    dialog.refresh()
    window.refresh_license()
    QTest.qWait(50)
    if not dialog.grab().save(str(output / "pro-activated.png")):
        raise RuntimeError("Cannot save Pro preview")
    dialog.reject()
    QTest.qWait(50)
    if not window.grab().save(str(output / "overview.png")):
        raise RuntimeError("Cannot save overview")
    window.close()
    print(f"Previews: {output}")


if __name__ == "__main__":
    main()
