from __future__ import annotations

import os
from pathlib import Path

from .models import Target
from .safety import absolute, has_link_ancestor, within


CATEGORIES = {
    "browsers": ("Браузеры", "Кэш страниц и графики. Пароли, история и сессии сохраняются.", "globe"),
    "apps": ("Приложения", "Кэши мессенджеров, редакторов и игровых клиентов.", "grid"),
    "temp": ("Временные файлы", "Temp, %TEMP% и Prefetch. Prefetch выбирается вручную.", "file"),
    "system": ("Диагностика и графика", "Отчёты об ошибках, дампы сбоев и кэш шейдеров.", "chip"),
    "empty": ("Пустые папки", "Пустые папки верхнего уровня в AppData. Требуют проверки.", "folder"),
    "registry": ("Следы в реестре", "Записи удаления и ключи приложений в HKLM\\SOFTWARE.", "layers"),
}
DEFAULT_CATEGORIES = ["browsers", "apps", "temp", "system"]


class Catalog:
    def __init__(self, env: dict[str, str] | None = None):
        self.env = {key.upper(): value for key, value in (os.environ if env is None else env).items()}
        self.local = self._env_path("LOCALAPPDATA")
        self.roaming = self._env_path("APPDATA")
        self.windows = self._env_path("SystemRoot")

    def _env_path(self, key: str) -> Path | None:
        value = self.env.get(key.upper())
        return absolute(value) if value and Path(value).is_absolute() else None

    def temp_root(self) -> Path | None:
        path = self._env_path("TEMP")
        if path is None or path == Path(path.anchor):
            return None
        # A misconfigured TEMP must not allow scanning an entire profile or Windows.
        protected = (self.local, self.roaming, self.windows, self._env_path("USERPROFILE"))
        if any(base is not None and within(base, path, allow_root=True) for base in protected):
            return None
        if self.windows is not None and within(path, self.windows) and not within(path, self.windows / "Temp", allow_root=True):
            return None
        return path

    @staticmethod
    def children(path: Path):
        try:
            if has_link_ancestor(path):
                return []
            return [p for p in path.iterdir() if p.is_dir() and not has_link_ancestor(p)]
        except OSError:
            return []

    def targets(self) -> list[Target]:
        targets: list[Target] = []

        def add(category, label, base, relative, processes=(), pattern="*", **options):
            if base is not None:
                targets.append(Target(category, label, base / relative, processes, pattern, **options))

        chromium = [
            ("Google Chrome", "Google/Chrome/User Data", "chrome.exe"),
            ("Chrome Beta", "Google/Chrome Beta/User Data", "chrome.exe"),
            ("Microsoft Edge", "Microsoft/Edge/User Data", "msedge.exe"),
            ("Edge Beta", "Microsoft/Edge Beta/User Data", "msedge.exe"),
            ("Brave", "BraveSoftware/Brave-Browser/User Data", "brave.exe"),
            ("Vivaldi", "Vivaldi/User Data", "vivaldi.exe"),
            ("Chromium", "Chromium/User Data", "chrome.exe"),
            ("Яндекс Браузер", "Yandex/YandexBrowser/User Data", "browser.exe"),
        ]
        for label, relative, process in chromium:
            if self.local is None:
                continue
            for profile in self.children(self.local / relative):
                if profile.name in ("Default", "Guest Profile") or profile.name.startswith("Profile "):
                    for cache in ("Cache", "Code Cache", "GPUCache", "DawnCache", "GrShaderCache"):
                        add("browsers", f"{label} · {profile.name}", profile, cache, (process,))
        for label, relative, process in [
            ("Opera", "Opera Software/Opera Stable", "opera.exe"),
            ("Opera GX", "Opera Software/Opera GX Stable", "opera.exe"),
        ]:
            for base in (self.local, self.roaming):
                if base is None:
                    continue
                root = base / relative
                for profile in [root, *[p for p in self.children(root) if p.name == "Default" or p.name.startswith("Profile ")]]:
                    for cache in ("Cache", "Code Cache", "GPUCache"):
                        add("browsers", label, profile, cache, (process,))
        for label, relative, process in [
            ("Firefox", "Mozilla/Firefox/Profiles", "firefox.exe"),
            ("Zen Browser", "zen/Profiles", "zen.exe"),
            ("Waterfox", "Waterfox/Profiles", "waterfox.exe"),
            ("LibreWolf", "librewolf/Profiles", "librewolf.exe"),
        ]:
            if self.local is not None:
                for profile in self.children(self.local / relative):
                    for cache in ("cache2", "startupCache", "shader-cache"):
                        add("browsers", f"{label} · {profile.name}", profile, cache, (process,))

        for label, relative, process in [
            ("Discord", "discord", "discord.exe"),
            ("Discord PTB", "discordptb", "discordptb.exe"),
            ("Discord Canary", "discordcanary", "discordcanary.exe"),
            ("Slack", "Slack", "slack.exe"),
            ("Visual Studio Code", "Code", "code.exe"),
            ("VS Code Insiders", "Code - Insiders", "code - insiders.exe"),
            ("Cursor", "Cursor", "cursor.exe"),
            ("Microsoft Teams (classic)", "Microsoft/Teams", "teams.exe"),
            ("Notion", "Notion", "notion.exe"),
            ("Obsidian", "obsidian", "obsidian.exe"),
        ]:
            for cache in ("Cache", "Code Cache", "GPUCache"):
                add("apps", label, self.roaming, f"{relative}/{cache}", (process,))
        add("apps", "Spotify · сетевой кэш", self.local, "Spotify/Browser/Cache", ("spotify.exe",))
        add("apps", "Steam · веб-кэш", self.local, "Steam/htmlcache", ("steam.exe", "steamwebhelper.exe"))
        for cache in ("webcache", "webcache_4147", "webcache_4430"):
            add("apps", "Epic Games Launcher", self.local, f"EpicGamesLauncher/Saved/{cache}", ("epicgameslauncher.exe",))
        add("apps", "pip · HTTP-кэш", self.local, "pip/Cache/http-v2", ("python.exe", "pip.exe"))
        add("apps", "npm · кэш пакетов", self.local, "npm-cache/_cacache", ("node.exe",))
        add("temp", "Временные файлы пользователя", self.local, "Temp")
        add("temp", "Временные файлы Windows", self.windows, "Temp")
        temp = self.temp_root()
        if temp is not None and all(target.root != temp for target in targets if target.category == "temp"):
            add("temp", "Временные файлы · %TEMP%", temp, "")
        add("temp", "Windows · Prefetch (по выбору)", self.windows, "Prefetch", pattern="*.pf", selected=False,
            details="Кэш запуска приложений · требуется выбор для очистки. После удаления первые запуски могут быть медленнее; Windows создаст кэш заново.")
        add("system", "Дампы сбоев приложений", self.local, "CrashDumps", pattern="*.dmp")
        add("system", "Отчёты об ошибках Windows", self.local, "Microsoft/Windows/WER/ReportArchive")
        add("system", "DirectX · кэш шейдеров", self.local, "D3DSCache")
        add("system", "NVIDIA · кэш DirectX", self.local, "NVIDIA/DXCache")
        add("system", "NVIDIA · кэш OpenGL", self.local, "NVIDIA/GLCache")
        add("system", "AMD · кэш DirectX", self.local, "AMD/DxCache")
        return targets

    def empty_roots(self) -> list[Path]:
        return [p for p in (self.local, self.roaming) if p is not None]


PROTECTED_FOLDERS = {
    "microsoft", "windows", "packages", "programs", "temp", "scleaner", "application data",
    "connecteddevicesplatform", "comms", "credentials", "crypto", "local", "locallow",
    "roaming", "virtualstore", "fonts", "publishers", "nvidia", "amd", "intel",
}
