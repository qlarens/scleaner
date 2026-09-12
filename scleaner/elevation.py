"""Request Windows administrator rights before creating the interactive window."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys

from .safety import is_admin
from .storage import data_directory


class ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
    ]


def _runas(executable: str, arguments: list[str], directory: str):
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    execute = shell.ShellExecuteExW
    execute.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    execute.restype = wintypes.BOOL
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    com_result = ole.CoInitializeEx(None, 0x2 | 0x4)  # STA, disable OLE1 DDE.
    try:
        info = ShellExecuteInfo()
        info.cbSize = ctypes.sizeof(info)
        # Complete Shell activation before this launcher exits; report errors ourselves.
        info.fMask = 0x100 | 0x400  # SEE_MASK_NOASYNC | SEE_MASK_FLAG_NO_UI
        info.lpVerb = "runas"
        info.lpFile = executable
        info.lpParameters = subprocess.list2cmdline(arguments)
        info.lpDirectory = directory
        info.nShow = 1
        if not execute(ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        if com_result >= 0:
            ole.CoUninitialize()


def ensure_admin(data_dir: Path | None = None, *, screenshot: bool = False, elevated: bool = False,
                 license_test_config: Path | None = None) -> bool:
    """Return True to start here, False after relaunch; raise on rejection/failure."""
    if screenshot or os.name != "nt" or is_admin():
        return True
    if elevated:
        raise OSError("Windows не предоставила права администратора. Запуск SCleaner остановлен.")
    # Preserve an explicit/relative data path, and the original user's default data.
    directory = str(Path(__file__).resolve().parents[1])
    arguments = ["--data-dir", str((data_dir or data_directory()).resolve()), "--elevated"]
    if license_test_config is not None:
        arguments.extend(["--license-test-config", str(license_test_config.resolve())])
    if getattr(sys, "frozen", False):
        directory = str(Path(sys.executable).resolve().parent)
    else:
        arguments = ["-m", "scleaner", *arguments]
    _runas(sys.executable, arguments, directory)
    return False
