from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .models import Finding
from .safety import has_link_ancestor, within

UNINSTALL = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"


def executable(command: str) -> str | None:
    expanded = os.path.expandvars(command.strip())
    match = re.match(r'^"([^"\r\n]+\.exe)"(?:\s|$)', expanded, re.I)
    if not match:
        match = re.match(r'^([^"\r\n]+?\.exe)(?:\s|$)', expanded, re.I)
    return match.group(1) if match else None


def definitely_missing(value: str) -> bool:
    path = Path(os.path.expandvars(value.strip().strip('"')))
    if not path.is_absolute() or str(path).startswith(("\\\\", "//")) or "%" in str(path) or ".." in path.parts:
        return False
    try:
        if os.name == "nt":
            import ctypes
            # Only fixed, online local volumes. Never infer removal from offline media.
            if ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) != 3:
                return False
        if not Path(path.anchor).is_dir():
            return False
        # A missing target behind a broken junction is not proof of an uninstall.
        for ancestor in (path, *path.parents):
            try:
                info = ancestor.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                return False
        path.stat()
        return False
    except FileNotFoundError:
        return True
    except OSError:
        return False


def encode_data(data):
    return {"bytes": base64.b64encode(data).decode("ascii")} if isinstance(data, bytes) else data


def decode_data(data):
    return base64.b64decode(data["bytes"], validate=True) if isinstance(data, dict) else data


def digest(values: list) -> str:
    return hashlib.sha256(json.dumps(sorted(values, key=lambda x: x[0]), ensure_ascii=True).encode()).hexdigest()


def reg_text(hive: str, key: str, values: list) -> str:
    def quote(text):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    lines = ["Windows Registry Editor Version 5.00", "", f"[{hive}\\{key}]"]
    for name, encoded, kind in values:
        data = decode_data(encoded)
        prefix = quote(name) if name else "@"
        if kind == 1 and "\n" not in data and "\r" not in data:
            rendered = quote(data)
        elif kind == 4:
            rendered = f"dword:{data:08x}"
        else:
            if kind in (1, 2):
                blob = (data + "\0").encode("utf-16-le")
            elif kind == 7:
                blob = ("\0".join(data) + "\0\0").encode("utf-16-le")
            elif kind == 11:
                blob = data.to_bytes(8, "little")
            elif isinstance(data, bytes):
                blob = data
            else:
                raise ValueError("Неподдерживаемый тип значения реестра")
            tag = "hex" if kind == 3 else f"hex({kind:x})"
            rendered = tag + ":" + ",".join(f"{byte:02x}" for byte in blob)
        lines.append(prefix + "=" + rendered)
    return "\r\n".join(lines) + "\r\n"


class RegistryCleaner:
    def __init__(self, storage):
        self.storage = storage
        self.api = None
        if os.name == "nt":
            import winreg
            self.api = winreg

    def _parts(self, meta):
        w = self.api
        hive_name, key, view = meta["hive"], meta["key"], meta["view"]
        if hive_name not in ("HKEY_CURRENT_USER", "HKEY_LOCAL_MACHINE") or view not in (32, 64):
            raise ValueError("Недопустимая ветка реестра")
        if meta.get("kind") == "software":
            from .software_registry import validate_key
            validate_key(meta)
        elif not key.startswith(UNINSTALL + "\\") or "\\" in key[len(UNINSTALL) + 1:] or not key[len(UNINSTALL) + 1:]:
            raise ValueError("Разрешены только отдельные записи удаления приложений")
        return getattr(w, hive_name), key, w.KEY_WOW64_64KEY if view == 64 else w.KEY_WOW64_32KEY

    def _read(self, key):
        w = self.api
        child_count, value_count, _ = w.QueryInfoKey(key)
        if child_count:
            raise ValueError("Запись содержит вложенные ключи")
        return [[name, encode_data(data), kind] for name, data, kind in (w.EnumValue(key, i) for i in range(value_count))]

    @staticmethod
    def candidate(values: list) -> bool:
        v = {name.casefold(): decode_data(data) for name, data, _ in values}
        if any(v.get(k) for k in ("systemcomponent", "windowsinstaller", "parentkeyname", "releaseType".casefold())):
            return False
        name, publisher = v.get("displayname", ""), v.get("publisher", "")
        if not isinstance(name, str) or not name or "microsoft" in str(publisher).casefold():
            return False
        location, command = v.get("installlocation", ""), v.get("uninstallstring", "")
        if not isinstance(location, str) or not location or not isinstance(command, str):
            return False
        exe = executable(command)
        return bool(exe and definitely_missing(location) and definitely_missing(exe))

    def scan(self, cancel, progress, *, include_review=False) -> tuple[list[Finding], list[str]]:
        if self.api is None:
            return [], ["Реестр доступен только в Windows."]
        w, findings, notes, seen = self.api, [], [], set()
        for hive_name in ("HKEY_CURRENT_USER", "HKEY_LOCAL_MACHINE"):
            for view, mask in ((64, w.KEY_WOW64_64KEY), (32, w.KEY_WOW64_32KEY)):
                try:
                    with w.OpenKey(getattr(w, hive_name), UNINSTALL, 0, w.KEY_READ | mask) as parent:
                        count = w.QueryInfoKey(parent)[0]
                        for i in range(count):
                            if cancel.is_set():
                                return findings, notes
                            try:
                                name = w.EnumKey(parent, i)
                                with w.OpenKey(parent, name, 0, w.KEY_READ | mask) as key:
                                    values = self._read(key)
                                signature = digest(values)
                                if (hive_name, view, name, signature) in seen or not self.candidate(values):
                                    continue
                                seen.add((hive_name, view, name, signature))
                                title = next(data for n, data, _ in values if n.casefold() == "displayname")
                                meta = {"hive": hive_name, "key": UNINSTALL + "\\" + name, "view": view, "digest": signature}
                                findings.append(Finding("registry", title, hive_name + "\\" + meta["key"], kind="registry", registry=meta, selected=False,
                                    details=f"{view} бит · папка установки и деинсталлятор отсутствуют. Проверьте вручную."))
                            except (OSError, ValueError, TypeError):
                                continue
                except FileNotFoundError:
                    pass
                except OSError:
                    notes.append(f"Нет доступа к {hive_name}, {view} бит.")
                progress(0, 0, f"Реестр · {hive_name} · {view} бит")
        if not cancel.is_set():
            from .software_registry import SoftwareRegistry
            software, software_notes = SoftwareRegistry(self).scan(cancel, progress, include_review=include_review)
            findings.extend(software)
            notes.extend(software_notes)
        return findings, notes

    def remove(self, item: Finding, session: str, *, allow_unverified=False) -> str:
        if self.api is None:
            raise ValueError("Реестр недоступен")
        if item.registry.get("kind") == "software":
            from .software_registry import SoftwareRegistry
            return SoftwareRegistry(self).remove(item, session, allow_unverified=allow_unverified)
        w = self.api
        hive, key_name, mask = self._parts(item.registry)
        with w.OpenKey(hive, key_name, 0, w.KEY_READ | mask) as key:
            values = self._read(key)
        if digest(values) != item.registry["digest"] or not self.candidate(values):
            raise ValueError("Запись изменилась после сканирования")
        base = self.storage.root / "backups" / session / item.id
        backup = {**item.registry, "values": values, "label": item.label}
        self.storage.write_json(base.with_suffix(".json"), backup)
        base.with_suffix(".reg").write_text(reg_text(item.registry["hive"], key_name, values), encoding="utf-16")
        # Re-read after backup; DeleteKeyEx never recursively removes unexpected children.
        with w.OpenKey(hive, key_name, 0, w.KEY_READ | mask) as key:
            if digest(self._read(key)) != item.registry["digest"]:
                raise ValueError("Запись изменилась во время резервного копирования")
        w.DeleteKeyEx(hive, key_name, mask, 0)
        return str(base.with_suffix(".json"))

    def restore(self, backup_path: str):
        if self.api is None:
            raise ValueError("Реестр недоступен")
        path = Path(backup_path)
        if not within(path, self.storage.root / "backups") or has_link_ancestor(path):
            raise ValueError("Резервная копия должна находиться в папке SCleaner")
        backup = json.loads(path.read_text(encoding="utf-8"))
        if backup.get("kind") == "software":
            from .software_registry import SoftwareRegistry
            return SoftwareRegistry(self).restore(backup)
        hive, key_name, mask = self._parts(backup)
        values = backup["values"]
        if digest(values) != backup["digest"]:
            raise ValueError("Резервная копия повреждена")
        w = self.api
        try:
            with w.OpenKey(hive, key_name, 0, w.KEY_READ | mask):
                raise ValueError("Запись уже существует. Восстановление не перезаписывает текущие данные.")
        except FileNotFoundError:
            pass
        with w.CreateKeyEx(hive, key_name, 0, w.KEY_WRITE | mask) as key:
            for name, data, kind in values:
                w.SetValueEx(key, name, 0, kind, decode_data(data))
