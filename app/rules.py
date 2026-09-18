"""Rules: date extraction from file creation time + processed-registry (processed.json).

The registry lives next to the exe alongside config.json. Key = source path + size +
ctime; value = output name. Used to skip files already processed.
"""

from __future__ import annotations

import json
import os
import platform
from datetime import datetime
from pathlib import Path
from typing import Any


REGISTRY_FILENAME = "processed.json"

_WINDOWS_ILLEGAL = '<>:"/\\|?*'


def _creation_time(path: Path) -> float:
    """Best-effort file creation time across platforms.

    On Windows, st_ctime is creation time. On POSIX, st_ctime is metadata-change time,
    which is the closest stable proxy; st_mtime is used as a fallback for paths that
    cannot be statted.
    """
    st = path.stat()
    if platform.system() == "Windows":
        return st.st_ctime
    # POSIX: st_birthtime where available, else st_ctime
    birth = getattr(st, "st_birthtime", None)
    return birth if birth is not None else st.st_ctime


def extract_date(path: Path) -> str:
    """Return the file creation date as YYYY-MM-DD."""
    ts = _creation_time(Path(path))
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def sanitize_name(name: str) -> str:
    """Remove Windows-illegal characters and control chars; collapse whitespace."""
    cleaned = "".join("_" if c in _WINDOWS_ILLEGAL else c for c in name)
    cleaned = "".join(c for c in cleaned if c.isprintable())
    cleaned = cleaned.strip().rstrip(".")
    cleaned = "_".join(cleaned.split())
    cleaned = cleaned.strip("_")
    return cleaned or "file"


def registry_path() -> Path:
    """Path to processed.json next to the exe (or project root in dev)."""
    if getattr(__import__("sys"), "frozen", False):
        return Path(__import__("sys").executable).resolve().parent / REGISTRY_FILENAME
    return Path(__file__).resolve().parent.parent / REGISTRY_FILENAME


def registry_key(path: Path) -> str:
    """Stable key for a source file: absolute path + size + ctime."""
    st = Path(path).stat()
    return f"{Path(path).resolve()}|{st.st_size}|{int(st.st_ctime)}"


def load_registry() -> dict[str, Any]:
    """Load processed.json as a dict {key: output_name}. Empty dict if missing/corrupt."""
    p = registry_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_registry(reg: dict[str, Any]) -> None:
    """Write processed.json atomically."""
    p = registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def is_processed(path: Path) -> tuple[bool, str | None]:
    """Return (already_processed, output_name_or_None)."""
    reg = load_registry()
    key = registry_key(path)
    if key in reg:
        return True, str(reg[key])
    return False, None


def mark_processed(path: Path, output_name: str) -> None:
    """Record a processed file in the registry (atomic)."""
    reg = load_registry()
    reg[registry_key(path)] = output_name
    save_registry(reg)
