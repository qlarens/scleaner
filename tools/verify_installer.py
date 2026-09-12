"""Install, reinstall and uninstall the release in a workspace fixture on Windows.

Uses the real installer and its HKCU uninstall registration, but no shortcuts.
Refuses to run when SCleaner is already installed. Never invokes app cleanup.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import time
import uuid
import winreg


ROOT = Path(__file__).resolve().parents[1]
METADATA = runpy.run_path(str(ROOT / "scleaner" / "__init__.py"))
VERSION = METADATA["__version__"]
PUBLISHER = METADATA["__copyright_holder__"]
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{05E50260-E263-49E8-AFE0-775A32C25C55}_is1"


def registrations():
    records = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, UNINSTALL_KEY, 0, winreg.KEY_READ | view) as key:
                    records.append({name: winreg.QueryValueEx(key, name)[0]
                                    for name in ("InstallLocation", "DisplayVersion", "Publisher")})
            except FileNotFoundError:
                pass
    return records


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    setup = ROOT / "dist" / f"SCleaner-{VERSION}-win64-Setup.exe"
    source = ROOT / "dist" / "SCleaner"
    require(setup.is_file(), "Build the installer first")
    require(not registrations(), "SCleaner is already registered. Use a clean Windows VM for this test.")
    fixtures = (ROOT / "artifacts" / "installer-tests").resolve()
    run = fixtures / uuid.uuid4().hex
    installed = run / "Installed SCleaner"
    data = run / "User Data" / "SCleaner"
    require(installed.resolve().is_relative_to(fixtures), "Installation must stay inside test fixtures")
    run.mkdir(parents=True)
    expected = {}
    for relative, contents in {
        "settings.json": '{"accent_color":"#54dfb1","age_hours":72}',
        "history/example.json": '{"status":"complete","removed":0}',
        "history/example.jsonl": '{"status":"skipped","path":"test"}\n',
        "backups/example/entry.reg": "Windows Registry Editor Version 5.00\n",
        "backups/example/entry.json": '{"test_backup":true}',
    }.items():
        path = data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        expected[path] = digest(path)

    flags = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
             "/NOCLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS"]

    def install(stage):
        subprocess.run([str(setup), *flags, "/NOICONS", "/TASKS=", "/LANG=russian",
                        f"/DIR={installed}", f"/LOG={run / (stage + '.log')}"],
                       check=True, timeout=120, cwd=run)
        records = registrations()
        require(bool(records), "Installer did not register an uninstaller")
        require(all(Path(record["InstallLocation"]).resolve() == installed.resolve() for record in records),
                "Unexpected registered installation directory")
        require(all(record["DisplayVersion"] == VERSION and record["Publisher"] == PUBLISHER for record in records),
                "Incorrect version or publisher in installed-app entry")
        for path in source.rglob("*"):
            if path.is_file():
                destination = installed / path.relative_to(source)
                require(destination.is_file() and digest(path) == digest(destination), f"Payload mismatch: {destination}")
        require(all(path.is_file() and digest(path) == value for path, value in expected.items()),
                "User data changed during installation")

    def render(stage):
        screenshot = run / f"{stage}.png"
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        environment.pop("PYTHONHOME", None)
        environment.pop("PYTHONPATH", None)
        subprocess.run([str(installed / "SCleaner.exe"), "--screenshot", str(screenshot), "--data-dir", str(data)],
                       env=environment, check=True, timeout=25, cwd=run)
        require(screenshot.is_file() and screenshot.stat().st_size > 10000, "Installed app did not render")

    def uninstall():
        # Re-check the full registered destination before invoking a destructive action.
        records = registrations()
        require(records and all(Path(record["InstallLocation"]).resolve() == installed.resolve() for record in records),
                "Refusing to uninstall an installation outside this fixture")
        require(installed.resolve().is_relative_to(fixtures), "Uninstall destination escaped test fixtures")
        subprocess.run([str(installed / "unins000.exe"), *flags, f"/LOG={run / 'uninstall.log'}"],
                       check=True, timeout=120, cwd=run)
        deadline = time.monotonic() + 20
        while (installed / "unins000.exe").exists() and time.monotonic() < deadline:
            time.sleep(0.1)

    try:
        install("install")
        render("installed")
        # A same-version update must replace stale payload without deleting user files.
        extra = installed / "user-note.txt"
        extra.write_text("Keep this user-created file", encoding="utf-8")
        expected[extra] = digest(extra)
        (installed / "README.md").write_text("stale installation", encoding="utf-8")
        install("reinstall")
        render("reinstalled")
    finally:
        if (installed / "unins000.exe").is_file():
            uninstall()

    require(not registrations(), "Uninstall registration was left behind")
    for path in source.rglob("*"):
        if path.is_file():
            require(not (installed / path.relative_to(source)).exists(), f"Installed file was not removed: {path.name}")
    require(all(path.is_file() and digest(path) == value for path, value in expected.items()),
            "User settings, history, backups or extra files were removed")
    (run / "result.json").write_text(json.dumps({"version": VERSION, "install": "passed", "reinstall": "passed",
        "uninstall": "passed", "user_data_preserved": True, "cleanup_called": False}, indent=2), encoding="utf-8")
    print(f"Installer verification passed. Logs and screenshots: {run}")


if __name__ == "__main__":
    main()
