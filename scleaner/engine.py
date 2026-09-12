from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import fnmatch
import json
import os
from pathlib import Path
import stat
import threading
import time

from .catalog import Catalog, PROTECTED_FOLDERS
from .models import CleanResult, Finding, ScanResult
from .registry import RegistryCleaner
from .safety import absolute, allowed, excluded, fingerprint, has_link_ancestor, is_link, running_processes, within
from .storage import Storage

MAX_FINDINGS = 250_000


class Engine:
    def __init__(self, catalog: Catalog | None = None, storage: Storage | None = None, process_provider=None):
        self.catalog = catalog or Catalog()
        self.storage = storage or Storage()
        self.process_provider = process_provider or running_processes
        self.registry = RegistryCleaner(self.storage)

    def _walk(self, root: Path, cancel, exclusions, notes):
        try:
            if not root.exists() or has_link_ancestor(root) or excluded(root, exclusions):
                return
        except OSError as error:
            if len(notes) < 200:
                notes.append(f"Недоступно: {root} ({error.strerror})")
            return
        stack = [root]
        while stack and not cancel.is_set():
            folder = stack.pop()
            if has_link_ancestor(folder):
                continue
            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        if cancel.is_set():
                            return
                        path = Path(entry.path)
                        if excluded(path, exclusions) or is_link(path):
                            continue
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(path)
                            elif entry.is_file(follow_symlinks=False):
                                # Windows DirEntry.stat can omit inode/device/link counts.
                                # Use a fresh filesystem stat for the deletion snapshot.
                                yield path, path.stat(follow_symlinks=False)
                        except OSError:
                            continue
            except OSError as error:
                if len(notes) < 200:
                    notes.append(f"Недоступно: {folder} ({error.strerror})")

    def scan(self, categories, settings, cancel=None, progress=None) -> ScanResult:
        cancel, progress = cancel or threading.Event(), progress or (lambda *args: None)
        result, seen = ScanResult(), set()
        cutoff = time.time() - max(1, settings.get("age_hours", 24)) * 3600
        exclusions = [*settings.get("exclusions", []), str(self.storage.root)]
        processes = self.process_provider()
        targets = [t for t in self.catalog.targets() if t.category in categories]
        for index, target in enumerate(targets):
            if cancel.is_set():
                break
            if target.processes and (processes is None or processes.intersection(target.processes)):
                try:
                    exists = target.root.exists()
                except OSError:
                    exists = True
                if exists:
                    result.notes.append(f"Пропущено: {target.label} — приложение работает или список процессов недоступен.")
                continue
            progress(index, len(targets), target.label)
            for path, info in self._walk(target.root, cancel, exclusions, result.notes):
                identity = os.path.normcase(str(absolute(path)))
                if identity in seen or info.st_mtime > cutoff or not fnmatch.fnmatch(path.name, target.pattern):
                    continue
                # Hard links may not release space and can refer to data outside the cache.
                if info.st_nlink > 1:
                    continue
                seen.add(identity)
                result.findings.append(Finding(target.category, target.label, str(path), info.st_size,
                    root=str(target.root), stamp=(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns),
                    processes=target.processes, selected=target.selected, details=target.details))
                if len(result.findings) >= MAX_FINDINGS:
                    result.notes.append("Достигнут лимит 250 000 объектов. После очистки повторите сканирование.")
                    result.cancelled = cancel.is_set()
                    return result
                if len(result.findings) % 1000 == 0:
                    progress(index, len(targets), f"{target.label} · найдено {len(result.findings):,}")
        if "empty" in categories and not cancel.is_set():
            for root in self.catalog.empty_roots():
                for path in self.catalog.children(root):
                    if cancel.is_set():
                        break
                    if path.name.casefold() in PROTECTED_FOLDERS or excluded(path, exclusions):
                        continue
                    try:
                        if path.stat().st_mtime <= cutoff and not any(path.iterdir()):
                            result.findings.append(Finding("empty", "Пустая папка приложения", str(path), kind="directory",
                                root=str(root), stamp=fingerprint(path), selected=False,
                                details="Папка пуста; это не доказывает удаление приложения. Проверьте название."))
                    except OSError:
                        pass
        if "registry" in categories and not cancel.is_set():
            items, notes = self.registry.scan(cancel, progress, include_review=bool(settings.get("registry_review", False)))
            result.findings.extend(items)
            result.notes.extend(notes)
        result.notes = list(dict.fromkeys(result.notes))
        result.cancelled = cancel.is_set()
        progress(len(targets), len(targets), "Сканирование завершено")
        return result

    def clean(self, findings, settings, cancel=None, progress=None, *, confirmed_registry_ids=()) -> CleanResult:
        cancel, progress = cancel or threading.Event(), progress or (lambda *args: None)
        chosen = [f for f in findings if f.selected]
        confirmed_registry_ids = frozenset(confirmed_registry_ids)
        result = CleanResult()
        if not chosen:
            return result
        session, history_path = self.storage.new_session()
        result.history_path = str(history_path)
        record = {"id": session, "date": datetime.now(timezone.utc).isoformat(), "status": "running", "requested": len(chosen)}
        # Require a writable audit trail before the first mutation.
        self.storage.write_json(history_path, record)
        targets = {(os.path.normcase(str(absolute(t.root))), t.category): t for t in self.catalog.targets()}
        empty_roots = {absolute(p) for p in self.catalog.empty_roots()}
        exclusions = [*settings.get("exclusions", []), str(self.storage.root)]
        cutoff = time.time() - max(1, settings.get("age_hours", 24)) * 3600
        processes, process_time = self.process_provider(), time.monotonic()
        completed = set()
        finished_normally = False
        try:
            with history_path.with_suffix(".jsonl").open("a", encoding="utf-8") as audit:
                for index, item in enumerate(chosen):
                    if cancel.is_set():
                        result.cancelled = True
                        break
                    progress(index, len(chosen), item.label)
                    try:
                        identity = (item.kind, item.path, item.registry.get("view") if item.kind == "registry" else None)
                        if identity in completed:
                            raise ValueError("Повторяющийся объект")
                        completed.add(identity)
                        if item.kind == "registry":
                            result.backups.append(self.registry.remove(item, session, allow_unverified=item.id in confirmed_registry_ids))
                        else:
                            path, root = absolute(item.path), absolute(item.root)
                            if not allowed(path, root) or excluded(path, exclusions):
                                raise ValueError("Путь защищён, исключён или содержит ссылку")
                            if item.stamp is None or fingerprint(path) != tuple(item.stamp):
                                raise ValueError("Объект изменился после сканирования")
                            if path.stat().st_mtime > cutoff:
                                raise ValueError("Объект используется недавно")
                            if item.kind == "file":
                                target = targets.get((os.path.normcase(str(root)), item.category))
                                if target is None or not fnmatch.fnmatch(path.name, target.pattern):
                                    raise ValueError("Путь не входит в список разрешённых кэшей")
                                if time.monotonic() - process_time > 1:
                                    processes, process_time = self.process_provider(), time.monotonic()
                                if target.processes and (processes is None or processes.intersection(target.processes)):
                                    raise ValueError("Приложение запущено; закройте его и повторите сканирование")
                                info = path.lstat()
                                if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                                    raise ValueError("Это не обычный файл")
                                path.unlink()
                                result.freed += item.size
                            elif item.kind == "directory":
                                if root not in empty_roots or path.parent != root or path.name.casefold() in PROTECTED_FOLDERS:
                                    raise ValueError("Папка защищена")
                                path.rmdir()  # Atomic empty-directory removal; never recursive.
                            else:
                                raise ValueError("Неизвестный тип объекта")
                        result.removed += 1
                        audit.write(json.dumps({"path": item.path, "kind": item.kind, "bytes": item.size, "status": "removed",
                            "registry_view": item.registry.get("view"), "user_confirmed_uninstall": item.id in confirmed_registry_ids}, ensure_ascii=False) + "\n")
                    except (OSError, ValueError, KeyError, TypeError) as error:
                        if getattr(error, "backup_path", None):
                            result.backups.append(error.backup_path)
                        result.skipped += 1
                        if len(result.errors) < 1000:
                            result.errors.append(f"{item.path}: {error}")
                        audit.write(json.dumps({"path": item.path, "status": "skipped", "reason": str(error)}, ensure_ascii=False) + "\n")
                    audit.flush()
            finished_normally = True
        finally:
            record.update(asdict(result))
            record["status"] = "cancelled" if result.cancelled else "complete" if finished_normally else "failed"
            self.storage.write_json(history_path, record)
        progress(len(chosen), len(chosen), "Очистка завершена")
        return result

    def large_files(self, root, settings, cancel=None, progress=None) -> ScanResult:
        cancel, progress = cancel or threading.Event(), progress or (lambda *args: None)
        root, result = absolute(root), ScanResult()
        if not root.is_dir() or has_link_ancestor(root):
            raise ValueError("Выберите доступную папку без символических ссылок")
        threshold = max(10, settings.get("large_mb", 500)) * 1024 * 1024
        for index, (path, info) in enumerate(self._walk(root, cancel, settings.get("exclusions", []), result.notes)):
            if info.st_size >= threshold:
                result.findings.append(Finding("large", path.name, str(path), info.st_size, kind="large", selected=False, details="Обзор места на диске · откройте расположение файла для управления"))
            if index % 500 == 0:
                progress(0, 0, f"Проверено файлов: {index:,}")
            if len(result.findings) >= MAX_FINDINGS:
                result.notes.append("Достигнут лимит результатов. Выберите папку меньшего размера.")
                break
        result.findings.sort(key=lambda f: f.size, reverse=True)
        result.cancelled = cancel.is_set()
        return result
