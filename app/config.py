"""Configuration: load/save config.json next to the exe with defaults."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


DEFAULTS: dict[str, Any] = {
    "ionos_api_key": "",
    "ionos_base_url": "https://openai.inference.de-txl.ionos.com/v1",
    "ocr_model": "lightonai/LightOnOCR-2-1B",
    "classify_model": "mistralai/Mistral-Small-24B-Instruct",
    "watch_folder": "",
    "doc_types": ["invoice", "letter", "receipt", "contract", "other"],
    "render_scale": 2.0,
    "ocr_max_tokens": 4096,
    "ocr_temperature": 0.2,
    "request_timeout": 120,
    "max_retries": 4,
    "keep_md_sidecar": True,
    "sender_in_filename": True,
    "stability_seconds": 3.0,
    "stability_max_wait": 120.0,
}

CONFIG_FILENAME = "config.json"


def config_dir() -> Path:
    """Directory that holds config.json: next to the frozen exe, else the project root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # dev: repo root (parent of app/)
    return Path(__file__).resolve().parent.parent


def config_path() -> Path:
    return config_dir() / CONFIG_FILENAME


def load_config() -> dict[str, Any]:
    """Load config.json, filling missing keys with defaults. Creates the file on first run."""
    path = config_path()
    cfg = dict(DEFAULTS)
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                cfg.update(stored)
        except (json.JSONDecodeError, OSError):
            # corrupt or unreadable: fall back to defaults, do not overwrite silently
            pass
    else:
        save_config(cfg)
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    """Write config.json atomically next to the exe."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = {**DEFAULTS, **cfg}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def get_api_key(cfg: dict[str, Any]) -> str:
    """Return the configured API key, falling back to the IONOS_API_TOKEN env var."""
    import os

    key = cfg.get("ionos_api_key", "") or ""
    return key or os.environ.get("IONOS_API_TOKEN", "")
