"""Start the packaged app from a different folder and require a rendered window."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
artifacts = root / "artifacts"
artifacts.mkdir(exist_ok=True)
native = "--native" in sys.argv
screenshot = artifacts / ("release-native.png" if native else "release-overview.png")
screenshot.unlink(missing_ok=True)
environment = dict(os.environ)
environment["QT_QPA_PLATFORM"] = "offscreen"
if native:
    environment.pop("QT_QPA_PLATFORM", None)
environment.pop("PYTHONPATH", None)
environment.pop("PYTHONHOME", None)
exe = root / "dist" / "SCleaner" / "SCleaner.exe"
subprocess.run([str(exe), "--screenshot", str(screenshot), "--data-dir", str(artifacts / "release-preview-data")],
    cwd=artifacts, env=environment, check=True, timeout=20)
if not screenshot.is_file() or screenshot.stat().st_size < 10000:
    raise RuntimeError("The packaged app did not render its window")
print(f"Packaged executable started and rendered: {screenshot}")
