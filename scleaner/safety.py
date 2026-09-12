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
