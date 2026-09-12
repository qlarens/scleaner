"""Conservative, bounded cleanup of application-owned HKLM SOFTWARE subtrees.

An absent uninstall entry alone is never evidence of an orphan. Installation
directories and executables are discovered by their contents throughout the
subtree, independently of application names. Keys with uncertain ownership can
be reviewed separately and require explicit confirmation that the app was removed.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import json
import ntpath
import os
import re

from .models import Finding
from .registry import UNINSTALL, decode_data, definitely_missing, digest, encode_data, executable, reg_text

SOFTWARE = "SOFTWARE"
OPEN_LINK = 8  # RegOpenKeyEx: inspect a symbolic link rather than follow it.
MAX_KEYS = 128
MAX_DEPTH = 8
MAX_BYTES = 1024 * 1024
PROTECTED = frozenset(name.casefold() for name in (
    "Microsoft", "Classes", "Policies", "Wow6432Node", "WOW6432", "Clients",
    "RegisteredApplications", "Windows", "ODBC", "WOW", "Setup", "Drivers",
    "Intel", "Intel Corporation", "AMD", "ATI", "ATI Technologies", "NVIDIA Corporation",
    "Realtek", "ASIO", "Khronos", "OpenGL", "Vulkan", "Waves", "Dolby", "Synaptics",
    "DefaultUserEnvironment", "OEM", "Partner", "SyncIntegrationClients", "MSI", "AGEIA Technologies",
))
DIRECTORIES = {"installlocation", "installdir", "installpath", "installationdirectory", "installationpath", "installdirectory", "installfolder", "applicationdirectory", "applicationfolder", "appdir", "programdir", "programdirectory"}
EXECUTABLES = {"executable", "executablepath", "applicationpath", "apppath", "exe", "exepath", "uninstallstring", "quietuninstallstring", "launchcommand", "commandline"}
FLAGS = {"systemcomponent", "windowsinstaller", "parentkeyname", "releasetype", "shared", "sharedcomponent"}
DESCRIPTORS = {"displayname", "productname", "applicationname", "publisher", "version", "displayversion", "productversion", "language"}
GENERIC_KEY_NAMES = {"data", "settings", "preferences", "configuration", "config", "shared", "paths", "path", "install", "installation", "runtime", "capabilities", "products", "versions", "version", "update", "updater", "shell", "commands"}


def valid_segment(part):
    return isinstance(part, str) and bool(part.strip()) and part not in (".", "..") and not any(c in part for c in "\\\r\n\0[]")


def validate_key(meta):
    parts = meta["key"].split("\\")
    if (meta["hive"] != "HKEY_LOCAL_MACHINE" or meta.get("kind") != "software"
            or not 2 <= len(parts) <= MAX_DEPTH + 2 or parts[0] != SOFTWARE
            or any(not valid_segment(p) or p.casefold() in PROTECTED for p in parts[1:])):
        raise ValueError("Разрешены только отдельные ключи приложений в HKLM\\SOFTWARE")


def tree_digest(tree):
    return digest([[path, sorted(values, key=lambda v: v[0])] for path, values in tree.items()])


def normalized_path(value):
    return ntpath.normcase(ntpath.normpath(os.path.expandvars(value.strip().strip('"'))))


def child_path(path, directory):
    try:
        return path != directory and ntpath.commonpath((path, directory)) == directory
    except ValueError:
        return False


def identity(value):
    return "".join(c for c in str(value).casefold() if c.isalnum())


def name_matches(first, second):
    """Match complete names/tokens and version suffixes, never arbitrary substrings."""
    a, b = identity(first), identity(second)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    if len(short) < 3:
        return False
    if long.startswith(short) and long[len(short):len(short) + 1].isdigit():
        return True

    def tokens(value):
        value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value))
        value = re.sub(r"([A-Za-z])([0-9])", r"\1 \2", value)
        return re.findall(r"[^\W_]+", value.casefold())

    small, big = sorted((tokens(first), tokens(second)), key=len)
    return bool(small) and any(big[i:i + len(small)] == small for i in range(len(big) - len(small) + 1))


def protected_values(values):
    return any((identity(name) in FLAGS and decode_data(data))
               or (identity(name) == "publisher" and "microsoft" in str(decode_data(data)).casefold())
               for name, data, _ in values)


def shared_directory(name, path):
    """Only known common directory references; never files or vendor subfolders."""
    if not any(word in name for word in ("shared", "common", "plugin", "vst")):
        return False
    roots = {normalized_path(value) for key, value in os.environ.items()
             if key.casefold() in ("programfiles", "programfiles(x86)", "programw6432")}
    shared = {ntpath.join(root, child) for root in roots
              for child in ("common files", "vstplugins", r"common files\vst2", r"common files\vst3")}
    return path in shared


def path_references(tree):
    """Return (relative key, normalized value name, path, role) tuples.

    Contents identify .exe paths regardless of value names; directory aliases
    add support for dotted installation folders. Lists and expanded strings use
    the same rules. Unparseable explicit installation fields veto the subtree.
    """
    references = []
    for relative, values in tree.items():
        if protected_values(values):
            raise ValueError("Защищённый или общий компонент")
        for name, data, kind in values:
            name = identity(name)
            data = decode_data(data)
            if kind not in (1, 2, 7):
                if name in DIRECTORIES | EXECUTABLES:
                    raise ValueError("Неизвестный формат пути установки")
                continue
            entries = data if kind == 7 else [data]
            for value in entries:
                if not isinstance(value, str):
                    raise ValueError("Неизвестный формат пути")
                expanded = os.path.expandvars(value.strip())
                exe = executable(expanded)
                if not exe and "icon" in name:
                    icon_path = re.sub(r",\s*-?\d+$", "", expanded)
                    exe = executable(icon_path)
                if exe:
                    path, role = normalized_path(exe), "exe"
                elif ntpath.isabs(expanded.strip('"')):
                    path = normalized_path(expanded)
                    role = "directory" if name in DIRECTORIES or not ntpath.splitext(ntpath.basename(path))[1] or expanded.endswith(("\\", "/")) else "file"
                elif name in DIRECTORIES | EXECUTABLES:
                    raise ValueError("Путь установки не является абсолютным")
                else:
                    # Unknown environment variables cannot prove that a file is gone.
                    if re.search(r"%[^%]+%[\\/]", expanded):
                        raise ValueError("Неизвестная переменная в пути")
                    continue
                if name in EXECUTABLES and role != "exe":
                    raise ValueError("Не удалось определить исполняемый файл")
                if role == "directory" and shared_directory(name, path):
                    continue
                references.append((relative, name, path, role))
    return references


def subtree(tree, relative):
    if not relative:
        return tree
    return {p[len(relative) + 1:] if p != relative else "": v for p, v in tree.items()
            if p == relative or p.startswith(relative + "\\")}


def common_key(paths):
    parts = [path.split("\\") if path else [] for path in paths]
    common = []
    for group in zip(*parts):
        if len(set(group)) != 1:
            break
        common.append(group[0])
    return "\\".join(common)


class RegistryRemovalError(ValueError):
    def __init__(self, error, backup):
        super().__init__(f"Удаление остановлено: {error}. Полная копия: {backup}. Восстановление доступно в истории.")
        self.backup_path = backup


class SoftwareRegistry:
    def __init__(self, cleaner):
        self.cleaner, self.w, self.storage = cleaner, cleaner.api, cleaner.storage

    def values(self, key):
        count = self.w.QueryInfoKey(key)[1]
        if count > 4096:
            raise ValueError("Слишком много значений реестра")
        values = []
        for index in range(count):
            name, data, kind = self.w.EnumValue(key, index)
            if (kind not in (0, 1, 2, 3, 4, 7, 11) or name.casefold() == "symboliclinkvalue"
                    or any(c in name for c in "\r\n\0")):
                raise ValueError("Ссылка или неподдерживаемое значение реестра")
            values.append([name, encode_data(data), kind])
        return values

    @contextmanager
    def open(self, hive, path, mask, access=None):
        # Check every component, including ancestors of the selected subtree.
        with ExitStack() as stack:
            current = hive
            for part in path.split("\\"):
                current = stack.enter_context(self.w.OpenKey(current, part, OPEN_LINK, (access or self.w.KEY_READ) | mask))
                self.values(current)
            yield current

    def snapshot(self, hive, path, mask, cancel=None):
        tree, total = {}, 0

        def visit(key, relative, depth):
            nonlocal total
            if cancel is not None and cancel.is_set():
                raise InterruptedError("Сканирование отменено")
            if len(tree) >= MAX_KEYS or depth > MAX_DEPTH:
                raise ValueError("Превышен лимит размера ветки реестра")
            before = self.w.QueryInfoKey(key)
            values = self.values(key)
            total += len(json.dumps(values, ensure_ascii=True))
            if total > MAX_BYTES:
                raise ValueError("Превышен лимит размера копии реестра")
            tree[relative] = values
            names = [self.w.EnumKey(key, i) for i in range(before[0])]
            for name in names:
                if not valid_segment(name):
                    raise ValueError("Недопустимое имя подраздела")
                with self.w.OpenKey(key, name, OPEN_LINK, self.w.KEY_READ | mask) as child:
                    visit(child, relative + "\\" + name if relative else name, depth + 1)
            if self.w.QueryInfoKey(key) != before:
                raise ValueError("Ключ изменился во время чтения")

        with self.open(hive, path, mask) as root:
            visit(root, "", 0)
        return tree

    def inventory(self, cancel=None):
        """Any unreadable uninstall record blocks SOFTWARE cleanup entirely."""
        records = []
        w = self.w
        for hive in (w.HKEY_LOCAL_MACHINE, w.HKEY_CURRENT_USER):
            for mask in (w.KEY_WOW64_64KEY, w.KEY_WOW64_32KEY):
                try:
                    with self.open(hive, UNINSTALL, mask) as parent:
                        before = w.QueryInfoKey(parent)
                        count = before[0]
                        for index in range(count):
                            if cancel is not None and cancel.is_set():
                                return None
                            name = w.EnumKey(parent, index)
                            with w.OpenKey(parent, name, OPEN_LINK, w.KEY_READ | mask) as key:
                                record_stamp = w.QueryInfoKey(key)
                                values = self.values(key)
                                if w.QueryInfoKey(key) != record_stamp:
                                    return None
                            # A positively stale uninstall record is not evidence of installation.
                            if self.cleaner.candidate(values):
                                continue
                            record = {n.casefold(): decode_data(d) for n, d, _ in values}
                            record["keyname"] = name
                            records.append(record)
                        if w.QueryInfoKey(parent) != before:
                            return None
                except FileNotFoundError:
                    # A disappearing child is ambiguous; only a missing whole branch is OK.
                    try:
                        with self.open(hive, UNINSTALL, mask):
                            return None
                    except FileNotFoundError:
                        continue
                except (OSError, ValueError, TypeError):
                    return None
        return records

    def evidence(self, key_name, tree, inventory):
        if inventory is None:
            return None
        if any(part.casefold() in PROTECTED for relative in tree for part in relative.split("\\")):
            return None
        try:
            references = path_references(tree)
        except (ValueError, TypeError):
            return None
        exes = sorted({p for _, _, p, role in references if role == "exe"})
        if not exes:
            return None
        # Paths with arbitrary names can identify the installation directory too.
        locations = sorted({p for _, _, p, role in references
                            if role != "exe" and all(child_path(exe, p) for exe in exes)}, key=lambda p: (-len(p), p))
        if not locations:
            return None
        if not all(definitely_missing(path) for _, _, path, _ in references):
            return None

        evidence_nodes = {relative for relative, _, path, role in references if role == "exe" or path in locations}
        anchor = common_key(evidence_nodes)
        if "" not in evidence_nodes:
            # Promote a nested path container to its application only when an
            # explicit directory ties it to that application. A vendor name
            # alone never grants permission to delete siblings of a product.
            own_name = identity(key_name.rsplit("\\", 1)[-1])
            if own_name not in {identity(ntpath.basename(p)) for p in locations}:
                return None
        for relative, values in tree.items():
            inside = not anchor or relative == anchor or relative.startswith(anchor + "\\")
            if not inside and any(identity(name) not in DESCRIPTORS for name, _, _ in values):
                return None

        identities = {p for p in key_name.split("\\")[1:] if identity(p) not in GENERIC_KEY_NAMES}
        identities.update(str(decode_data(data)) for values in tree.values() for name, data, _ in values
                          if identity(name) in ("displayname", "productname", "applicationname", "publisher"))
        identities.update(name for _, name, _, role in references if role == "exe"
                          and name not in EXECUTABLES | {"displayicon", "icon", "launcher", "binary", "client", "program", "main"})
        identities.discard("")
        for record in inventory:
            record = {identity(name): value for name, value in record.items()}
            names = {str(record.get(n, "")) for n in ("displayname", "publisher", "keyname")}
            if any(name_matches(a, b) for a in identities for b in names):
                return None
            for value in record.values():
                if not isinstance(value, str) or not value:
                    continue
                path = executable(value) or (value if ntpath.isabs(os.path.expandvars(value.strip('"'))) else None)
                if path:
                    path = normalized_path(path)
                    if any(path == loc or child_path(path, loc) or child_path(loc, path) for loc in locations):
                        return None
        return locations[0], exes[0]

    def ancestor_allows(self, values, inventory):
        if protected_values(values):
            return False
        names = {str(decode_data(data)) for name, data, _ in values
                 if identity(name) in ("displayname", "productname", "publisher")}
        for record in inventory:
            installed = {str(value) for name, value in record.items()
                         if identity(name) in ("displayname", "publisher", "keyname")}
            if any(name_matches(a, b) for a in names for b in installed):
                return False
        try:
            refs = path_references({"": values})
        except (TypeError, ValueError):
            return False
        return all(definitely_missing(path) for _, name, path, role in refs if name in DIRECTORIES | EXECUTABLES or role == "exe")

    def review_status(self, key_name, tree, inventory):
        """Explain an unverified key. Only uncertain ownership is overridable.

        A missing uninstall entry never labels settings-only keys as orphans.
        Explicit user confirmation is required, and cannot override known live
        paths, installed software, protected components or incomplete reads.
        """
        if inventory is None:
            return False, "Список установленных приложений прочитан не полностью."
        if any(p.casefold() in PROTECTED for relative in tree for p in relative.split("\\")):
            return False, "Ветка содержит системный или защищённый подраздел."
        if re.fullmatch(r"\{?[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\}?", key_name.split("\\")[1]):
            return False, "Назначение служебного ключа с идентификатором не определено."
        try:
            refs = path_references(tree)
        except (TypeError, ValueError) as error:
            return False, str(error)
        names = {p for p in key_name.split("\\")[1:] if identity(p) not in GENERIC_KEY_NAMES}
        names.update(p.rsplit("\\", 1)[-1] for p in tree if p and identity(p.rsplit("\\", 1)[-1]) not in GENERIC_KEY_NAMES)
        names.update(str(decode_data(data)) for values in tree.values() for name, data, _ in values
                     if identity(name) in ("displayname", "productname", "applicationname", "publisher"))
        names.update(name for _, name, _, role in refs if role == "exe" and name not in EXECUTABLES | {"displayicon", "icon", "launcher", "binary", "client", "program", "main"})
        for record in inventory:
            installed = {str(value) for name, value in record.items() if identity(name) in ("displayname", "publisher", "keyname")}
            if any(name_matches(a, b) for a in names for b in installed):
                title = record.get("displayname") or record.get("publisher") or record.get("keyname", "")
                return False, f"Совпадение с установленным приложением или издателем: {title}."
            for value in record.values():
                if not isinstance(value, str):
                    continue
                path = executable(value) or (value if ntpath.isabs(os.path.expandvars(value.strip('"'))) else None)
                if path:
                    path = normalized_path(path)
                    if any(path == p or child_path(path, p) or child_path(p, path) for _, _, p, _ in refs):
                        return False, "Путь связан с зарегистрированным установленным приложением."
        for _, _, path, _ in refs:
            if not definitely_missing(path):
                return False, f"Путь существует или его отсутствие нельзя подтвердить: {path}."
        if not any(tree.values()):
            return True, "Ключ и его подразделы пусты. Это не доказывает, что приложение удалено."
        if not refs:
            return True, "Сохранились настройки без путей установки. Автоматически определить наличие приложения нельзя."
        if not any(role == "exe" for _, _, _, role in refs):
            return True, "Указанные пути отсутствуют, но путь к исполняемому файлу не сохранён."
        return True, "Указанные пути отсутствуют, но данных недостаточно для подтверждения удаления всего приложения."

    def review_candidates(self, path, tree, inventory, cancel, automatic):
        covered = [p for p, _, _ in automatic]
        for relative in sorted(tree, key=lambda p: (p.count("\\") + bool(p), p)):
            if cancel.is_set():
                return
            key_name = path + ("\\" + relative if relative else "")
            if any(key_name == p or key_name.startswith(p + "\\") or p.startswith(key_name + "\\") for p in covered):
                continue
            try:
                validate_key({"hive": "HKEY_LOCAL_MACHINE", "key": key_name, "kind": "software"})
            except ValueError:
                continue
            ancestors = [""] + ["\\".join(relative.split("\\")[:i]) for i in range(1, len(relative.split("\\")))]
            if any(not self.ancestor_allows(tree[p], inventory) for p in ancestors if p != relative):
                continue
            branch = subtree(tree, relative)
            allowed, reason = self.review_status(key_name, branch, inventory)
            if allowed:
                covered.append(key_name)
                yield key_name, branch, reason

    def candidates(self, path, tree, inventory, cancel):
        covered = []
        for relative in sorted(tree, key=lambda p: (p.count("\\") + bool(p), p)):
            if cancel.is_set():
                return
            if any(not p or relative == p or relative.startswith(p + "\\") for p in covered):
                continue
            key_name = path + ("\\" + relative if relative else "")
            try:
                validate_key({"hive": "HKEY_LOCAL_MACHINE", "key": key_name, "kind": "software"})
            except ValueError:
                continue
            ancestors = [""] + ["\\".join(relative.split("\\")[:i]) for i in range(1, len(relative.split("\\")))]
            if any(not self.ancestor_allows(tree[p], inventory) for p in ancestors if p != relative):
                continue
            branch = subtree(tree, relative)
            proof = self.evidence(key_name, branch, inventory)
            if proof:
                covered.append(relative)
                yield key_name, branch, proof

    def scan(self, cancel, progress, *, include_review=False):
        findings, notes = [], []
        inventory = self.inventory(cancel)
        if inventory is None:
            return [], ["HKLM\\SOFTWARE: проверка пропущена — список установленных приложений прочитан не полностью или проверка отменена."]
        w = self.w
        for view, mask in ((64, w.KEY_WOW64_64KEY), (32, w.KEY_WOW64_32KEY)):
            try:
                with self.open(w.HKEY_LOCAL_MACHINE, SOFTWARE, mask) as parent:
                    names = [w.EnumKey(parent, i) for i in range(w.QueryInfoKey(parent)[0])]
                for name in names:
                    if cancel.is_set():
                        return findings, notes
                    if not valid_segment(name) or name.casefold() in PROTECTED:
                        notes.append(f"HKLM\\SOFTWARE\\{name}, {view} бит: системная или защищённая ветка.")
                        continue
                    path = SOFTWARE + "\\" + name
                    progress(0, 0, f"HKLM\\{path} · {view} бит")
                    try:
                        tree = self.snapshot(w.HKEY_LOCAL_MACHINE, path, mask, cancel)
                        automatic = list(self.candidates(path, tree, inventory, cancel))
                        for key_name, snapshot, proof in automatic:
                            meta = {"hive": "HKEY_LOCAL_MACHINE", "key": key_name, "view": view, "kind": "software", "digest": tree_digest(snapshot), "key_count": len(snapshot)}
                            title = key_name.rsplit("\\", 1)[-1]
                            findings.append(Finding("registry", title, "HKEY_LOCAL_MACHINE\\" + key_name, kind="registry", registry=meta, selected=False,
                                details=f"HKLM SOFTWARE · {view} бит · ключей: {len(snapshot)}. Отсутствуют папка {proof[0]} и файл {proof[1]}. Совпадений с установленными приложениями нет. Проверьте вручную."))
                        manual = list(self.review_candidates(path, tree, inventory, cancel, automatic)) if include_review else []
                        for key_name, snapshot, reason in manual:
                            meta = {"hive": "HKEY_LOCAL_MACHINE", "key": key_name, "view": view, "kind": "software", "digest": tree_digest(snapshot), "key_count": len(snapshot), "review_required": True}
                            title = key_name.rsplit("\\", 1)[-1]
                            findings.append(Finding("registry", title, "HKEY_LOCAL_MACHINE\\" + key_name, kind="registry", registry=meta, selected=False,
                                details=f"Требует ручной проверки · {view} бит · ключей: {len(snapshot)}. {reason} Выбирайте запись только если знаете, что приложение удалено. Массовый выбор эту строку не отмечает."))
                        if not any(p == path for p, _, _ in [*automatic, *manual]):
                            reviewable, reason = self.review_status(path, tree, inventory)
                            suffix = " Включите «Ручная проверка», чтобы увидеть запись." if reviewable and not include_review else ""
                            notes.append(f"HKLM\\{path}, {view} бит: {reason}{suffix}")
                    except (OSError, ValueError, TypeError) as error:
                        notes.append(f"HKLM\\{path}, {view} бит: ветка пропущена — {error}.")
            except FileNotFoundError:
                pass
            except (OSError, ValueError):
                notes.append(f"Нет доступа к HKLM\\SOFTWARE, {view} бит.")
        return findings, notes

    def remove(self, item, session, *, allow_unverified=False):
        hive, path, mask = self.cleaner._parts(item.registry)
        inventory = self.inventory()
        self.check_ancestors(hive, path, mask, inventory)
        tree = self.snapshot(hive, path, mask)
        if tree_digest(tree) != item.registry["digest"]:
            raise ValueError("Ключ изменился или больше не соответствует условиям очистки")
        self.check_removal(item, tree, inventory, allow_unverified)
        base = self.storage.root / "backups" / session / item.id
        backup = {**item.registry, "tree": tree, "label": item.label, "schema": 2}
        if item.registry.get("review_required"):
            backup["user_confirmed_uninstall"] = True
        text = "Windows Registry Editor Version 5.00\r\n\r\n"
        # Export 32-bit SOFTWARE into its physical location for safe manual .reg import.
        export_path = path if item.registry["view"] == 64 else path.replace("SOFTWARE\\", "SOFTWARE\\Wow6432Node\\", 1)
        for relative, values in tree.items():
            name = export_path + ("\\" + relative if relative else "")
            text += reg_text("HKEY_LOCAL_MACHINE", name, values).split("\r\n", 2)[2] + "\r\n"
        self.storage.write_json(base.with_suffix(".json"), backup)
        base.with_suffix(".reg").write_text(text, encoding="utf-16")
        backup_path = str(base.with_suffix(".json"))
        try:
            inventory = self.inventory()
            self.check_removal(item, tree, inventory, allow_unverified)
            remaining = dict(tree)
            for relative in sorted(tree, key=lambda p: (p.count("\\") + bool(p), p), reverse=True):
                self.check_ancestors(hive, path, mask, inventory)
                current = self.snapshot(hive, path, mask)
                if tree_digest(current) != tree_digest(remaining):
                    raise ValueError("Ключ изменился после резервного копирования")
                full = path + ("\\" + relative if relative else "")
                # Only the exact backed-up leaf; DeleteKeyEx refuses unexpected children.
                self.w.DeleteKeyEx(hive, full, mask, 0)
                del remaining[relative]
        except (OSError, ValueError, TypeError) as error:
            raise RegistryRemovalError(error, backup_path) from error
        return backup_path

    def check_removal(self, item, tree, inventory, allow_unverified):
        if item.registry.get("review_required"):
            if not allow_unverified:
                raise ValueError("Требуется явное подтверждение, что приложение удалено")
            allowed, reason = self.review_status(item.registry["key"], tree, inventory)
            if not allowed:
                raise ValueError(reason)
        elif not self.evidence(item.registry["key"], tree, inventory):
            raise ValueError("Ключ изменился или больше не соответствует условиям очистки")

    def check_ancestors(self, hive, path, mask, inventory):
        if inventory is None:
            raise ValueError("Список установленных приложений прочитан не полностью")
        parts = path.split("\\")
        for i in range(2, len(parts)):
            with self.open(hive, "\\".join(parts[:i]), mask) as parent:
                if not self.ancestor_allows(self.values(parent), inventory):
                    raise ValueError("Родительская ветка относится к установленному или защищённому компоненту")

    def restore(self, backup):
        hive, path, mask = self.cleaner._parts(backup)
        tree = backup["tree"]
        if not isinstance(tree, dict) or "" not in tree or len(tree) > MAX_KEYS or tree_digest(tree) != backup["digest"]:
            raise ValueError("Резервная копия повреждена")
        for relative, values in tree.items():
            parts = relative.split("\\") if relative else []
            if len(parts) > MAX_DEPTH or any(not valid_segment(p) for p in parts):
                raise ValueError("Недопустимый путь в резервной копии")
            if relative and relative.rpartition("\\")[0] not in tree:
                raise ValueError("Неполная структура резервной копии")
            reg_text(backup["hive"], path, values)  # Validate all encodings before writing.
            if any(kind not in (0, 1, 2, 3, 4, 7, 11) or n.casefold() == "symboliclinkvalue" for n, _, kind in values):
                raise ValueError("Недопустимое значение в резервной копии")
        try:
            current = self.snapshot(hive, path, mask)
        except FileNotFoundError:
            current = {}
        if current and tree_digest(current) == tree_digest(tree):
            raise ValueError("Запись уже существует. Восстановление не перезаписывает текущие данные.")
        if any(p not in tree or digest(v) != digest(tree[p]) for p, v in current.items()):
            raise ValueError("Запись уже существует и отличается от копии. Восстановление не перезаписывает текущие данные.")
        for relative in sorted(tree, key=lambda p: (p.count("\\") + bool(p), p)):
            if relative in current:
                continue
            full = path + ("\\" + relative if relative else "")
            parent_path, _, leaf = full.rpartition("\\")
            with self.open(hive, parent_path, mask, self.w.KEY_READ | self.w.KEY_WRITE) as parent:
                # Recheck before creation; never merge values into an existing key.
                try:
                    with self.w.OpenKey(parent, leaf, OPEN_LINK, self.w.KEY_READ | mask):
                        raise ValueError("Ключ появился во время восстановления")
                except FileNotFoundError:
                    pass
                with self.w.CreateKeyEx(parent, leaf, 0, self.w.KEY_WRITE | mask) as key:
                    for name, data, kind in tree[relative]:
                        self.w.SetValueEx(key, name, 0, kind, decode_data(data))
