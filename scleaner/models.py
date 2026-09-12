from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import uuid


def size_text(size: int | float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if abs(size) < 1024 or unit == "ТБ":
            return f"{size:.1f} {unit}" if unit != "Б" else f"{int(size)} Б"
        size /= 1024
    return "0 Б"


@dataclass(frozen=True)
class Target:
    category: str
    label: str
    root: Path
    processes: tuple[str, ...] = ()
    pattern: str = "*"
    selected: bool = True
    details: str = "Кэш или временный файл · без восстановления"


@dataclass
class Finding:
    category: str
    label: str
    path: str
    size: int = 0
    kind: str = "file"
    root: str = ""
    stamp: tuple[int, int, int, int] | None = None
    processes: tuple[str, ...] = ()
    details: str = ""
    registry: dict = field(default_factory=dict)
    selected: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class ScanResult:
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    cancelled: bool = False
    started: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def total_size(self) -> int:
        return sum(item.size for item in self.findings)


@dataclass
class CleanResult:
    removed: int = 0
    freed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    backups: list[str] = field(default_factory=list)
    cancelled: bool = False
    history_path: str = ""
