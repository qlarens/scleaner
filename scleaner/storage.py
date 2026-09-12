from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import datetime
import uuid

from .catalog import DEFAULT_CATEGORIES
from .appearance import DEFAULT_ACCENT, normalize_accent


def data_directory() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "SCleaner" if base else Path.home() / ".scleaner"


class Storage:
    def __init__(self, root: Path | None = None):
        self.root = root or data_directory()

    def read_settings(self) -> dict:
        defaults = {"age_hours": 24, "exclusions": [], "categories": DEFAULT_CATEGORIES, "large_mb": 500, "accent_color": DEFAULT_ACCENT}
        try:
            raw = json.loads((self.root / "settings.json").read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return defaults
            defaults["accent_color"] = normalize_accent(raw.get("accent_color"))
            defaults["age_hours"] = max(1, min(720, int(raw.get("age_hours", 24))))
            defaults["large_mb"] = max(10, min(100000, int(raw.get("large_mb", 500))))
            exclusions = raw.get("exclusions", [])
            if isinstance(exclusions, list):
                defaults["exclusions"] = [p for p in exclusions if isinstance(p, str) and Path(p).is_absolute()]
            categories = raw.get("categories", DEFAULT_CATEGORIES)
            if isinstance(categories, list):
                defaults["categories"] = [x for x in categories if x in DEFAULT_CATEGORIES]
        except (OSError, ValueError, TypeError):
            pass
        return defaults

    def write_json(self, path: Path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

    def save_settings(self, settings):
        self.write_json(self.root / "settings.json", settings)

    def history(self) -> list[dict]:
        records = []
        for path in sorted((self.root / "history").glob("*.json"), reverse=True):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    records.append(value)
            except (OSError, ValueError):
                continue
        return records

    def new_session(self) -> tuple[str, Path]:
        identifier = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        return identifier, self.root / "history" / f"{identifier}.json"
