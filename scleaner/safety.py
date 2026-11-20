from __future__ import annotations

import os
from pathlib import Path
import stat


def absolute(path: Path | str) -> Path:
    return Path(os.path.abspath(path))


def is_link(path: Path) -> bool:
    try:
        info = path.lstat()
        return stat.S_ISLNK(info.st_mode) or bool(
            getattr(info, "st_file_attributes", 0) & 0x400
        )
    except OSError:
        return True


def has_link_ancestor(path: Path) -> bool:
    # Junctions, mount points and symlinks are never traversed, even above a target.
    return any(is_link(parent) for parent in (path, *path.parents))


def within(path: Path, root: Path, *, allow_root: bool = False) -> bool:
    path, root = absolute(path), absolute(root)
    try:
        relative = path.relative_to(root)
        return allow_root or relative != Path(".")
    except ValueError:
        return False


def allowed(path: Path, root: Path) -> bool:
    return within(path, root) and not has_link_ancestor(path)


def fingerprint(path: Path) -> tuple[int, int, int, int]:
    info = path.stat(follow_symlinks=False)
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def delete_file(path: Path, root: Path, stamp) -> None:
    """Delete one scanned regular file through a single handle.

    Path checks alone race with a junction swapped into a parent folder
    (users can write to Windows\\Temp), so identity and the real location are
    verified on the same handle that performs the deletion.
    """
    if os.name != "nt":
        raise ValueError("Удаление поддерживается только в Windows")
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    open_reparse_point, backup_semantics = 0x00200000, 0x02000000

    def open_handle(target, access, share, flags):
        handle = kernel.CreateFileW(str(target), access, share, None, 3, flags, None)  # OPEN_EXISTING
        if handle is None or handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def final_path(handle):
        buffer = ctypes.create_unicode_buffer(32768)
        if not kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0):
            raise ctypes.WinError(ctypes.get_last_error())
        text = buffer.value
        return Path("\\\\" + text[8:] if text.startswith("\\\\?\\UNC\\") else text.removeprefix("\\\\?\\"))

    # No FILE_SHARE_DELETE: the root cannot be renamed or replaced while it is held.
    root_handle = open_handle(root, 0x80, 0x1 | 0x2, open_reparse_point | backup_semantics)
    try:
        real_root = final_path(root_handle)
        # DELETE | FILE_READ_ATTRIBUTES; never follow a reparse point at the file itself.
        handle = open_handle(path, 0x10000 | 0x80, 0x1 | 0x2 | 0x4, open_reparse_point)
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
        except OSError:
            kernel.CloseHandle(handle)
            raise
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or getattr(info, "st_file_attributes", 0) & (0x400 | 0x10)):
                raise ValueError("Это не обычный файл")
            if stamp is None or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != tuple(stamp):
                raise ValueError("Объект изменился после сканирования")
            if not within(final_path(handle), real_root):
                raise ValueError("Файл находится вне разрешённой папки")
            delete = ctypes.c_ubyte(1)  # FILE_DISPOSITION_INFO.DeleteFile
            if not kernel.SetFileInformationByHandle(handle, 4, ctypes.byref(delete), 1):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            os.close(descriptor)  # Closing the handle completes the deletion.
    finally:
        kernel.CloseHandle(root_handle)


def windows_directory() -> Path:
    """The real Windows folder; %SystemRoot% can be redefined by the user."""
    import ctypes
    buffer = ctypes.create_unicode_buffer(260)
    if not ctypes.windll.kernel32.GetSystemWindowsDirectoryW(buffer, len(buffer)):
        raise ctypes.WinError()
    return Path(buffer.value)


def excluded(path: Path, exclusions: list[str]) -> bool:
    return any(within(path, Path(entry), allow_root=True) for entry in exclusions)


def running_processes() -> set[str] | None:
    try:
        import psutil
        names = set()
        for process in psutil.process_iter(["name"]):
            name = process.info.get("name")
            if name:
                names.add(name.casefold())
        return names
    except Exception:
        # A failed process inventory must not turn into permission to clean live caches.
        return None


def is_admin() -> bool:
    if os.name != "nt":
        return False
    import ctypes
    return bool(ctypes.windll.shell32.IsUserAnAdmin())
