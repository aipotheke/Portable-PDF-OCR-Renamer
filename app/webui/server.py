"""M3 web UI: local HTTP server on 127.0.0.1:8765 (stdlib http.server).

Endpoints:
- GET  /            single-file UI (index.html)
- GET  /api/status  job list + watch state (polled every 2 s by the UI)
- GET  /api/config  current config (API key masked)
- POST /api/config  update + persist config.json (validated)
- POST /api/scan    enqueue all existing PDFs in the watch folder
"""

from __future__ import annotations

import json
import logging
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..config import get_api_key, load_config, save_config
from ..watcher import FolderWatcher


log = logging.getLogger("webui")

DEFAULT_PORT = 8765

EDITABLE_KEYS = (
    "ionos_api_key",
    "watch_folder",
    "doc_types",
    "keep_md_sidecar",
    "sender_in_filename",
    "render_scale",
    "ocr_max_tokens",
    "ocr_temperature",
    "request_timeout",
    "max_retries",
    "stability_seconds",
    "stability_max_wait",
)


def index_html_path() -> Path:
    """Location of the bundled index.html (PyInstaller _MEIPASS when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "webui" / "index.html"
    return Path(__file__).resolve().parent / "index.html"


def validate_config_update(cfg: dict[str, Any], update: dict[str, Any]) -> str | None:
    """Validate a config update; return an error message or None if valid."""
    if "watch_folder" in update:
        folder = str(update["watch_folder"] or "").strip()
        if folder and not Path(folder).is_dir():
            return f"Watch folder does not exist: {folder}"

    if "doc_types" in update:
        types = update["doc_types"]
        if not isinstance(types, list):
            return "doc_types must be a list"
        cleaned = [str(t).strip() for t in types if str(t).strip()]
        if not cleaned:
            return "At least one document type is required"
        update["doc_types"] = cleaned

    if "ionos_api_key" in update and not str(update["ionos_api_key"] or "").strip():
        return "API key must not be empty"

    for key in ("render_scale", "ocr_max_tokens", "ocr_temperature", "request_timeout",
                "max_retries", "stability_seconds", "stability_max_wait"):
        if key in update:
            try:
                float(update[key])
            except (TypeError, ValueError):
                return f"{key} must be a number"

    unknown = [k for k in update if k not in EDITABLE_KEYS]
    if unknown:
        return f"Unknown setting(s): {', '.join(unknown)}"
    return None


def masked_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Config safe to show in the UI: key masked to its last 4 characters."""
    out: dict[str, Any] = {}
    for key in EDITABLE_KEYS:
        if key in cfg:
            out[key] = cfg[key]
    key = str(out.get("ionos_api_key") or "")
    out["ionos_api_key"] = ("*" * max(0, len(key) - 4) + key[-4:]) if key else ""
    return out


def apply_config_update(cfg: dict[str, Any], update: dict[str, Any], watcher: FolderWatcher | None) -> None:
    """Merge a validated update into the live config and persist it."""
    old_folder = str(cfg.get("watch_folder") or "")
    for key in EDITABLE_KEYS:
        if key in update:
            cfg[key] = update[key]
    save_config(cfg)
    if watcher is not None:
        watcher.stability_seconds = float(cfg.get("stability_seconds", 3.0))
        watcher.stability_max_wait = float(cfg.get("stability_max_wait", 120.0))
        if str(cfg.get("watch_folder") or "") != old_folder and watcher.observer is not None:
            watcher.restart_observer()


class _Handler(BaseHTTPRequestHandler):
    server: "WebUIServer"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("%s " + fmt, self.address_string(), *args)

    # ---- helpers ----------------------------------------------------------

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise ValueError("empty request body")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # ---- routes ----------------------------------------------------------

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/index.html"):
            self._serve_index()
        elif self.path == "/api/status":
            self._send_json(self.server.app.status())
        elif self.path == "/api/config":
            self._send_json(masked_config(self.server.app.cfg))
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if self.path == "/api/config":
            self._post_config()
        elif self.path == "/api/scan":
            self._post_scan()
        else:
            self._send_json({"error": "not found"}, 404)

    def _serve_index(self) -> None:
        try:
            body = index_html_path().read_bytes()
        except OSError:
            self._send_json({"error": "index.html not found"}, 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _post_config(self) -> None:
        try:
            update = self._read_json()
        except (ValueError, json.JSONDecodeError):
            self._send_json({"error": "invalid JSON body"}, 400)
            return
        if not isinstance(update, dict):
            self._send_json({"error": "body must be a JSON object"}, 400)
            return
        error = validate_config_update(self.server.app.cfg, update)
        if error:
            self._send_json({"error": error}, 400)
            return
        apply_config_update(self.server.app.cfg, update, self.server.app.watcher)
        log.info("Config updated via web UI: %s", ", ".join(update))
        self._send_json({"ok": True, "config": masked_config(self.server.app.cfg)})

    def _post_scan(self) -> None:
        count = self.server.app.watcher.scan_existing()
        self._send_json({"ok": True, "queued": count})


class WebUIServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, app: "WebUI", address: tuple[str, int]):
        super().__init__(address, _Handler)
        self.app = app


class WebUI:
    """Owns the server plus the watcher; serves the single-page UI."""

    def __init__(self, cfg: dict[str, Any] | None = None, watcher: FolderWatcher | None = None,
                 port: int = DEFAULT_PORT):
        self.cfg = cfg if cfg is not None else load_config()
        self.watcher = watcher if watcher is not None else FolderWatcher(self.cfg)
        self.port = port
        self.server: WebUIServer | None = None

    def status(self) -> dict[str, Any]:
        return {
            "watch_folder": str(self.watcher.folder()),
            "paused": self.watcher.paused,
            "has_api_key": bool(get_api_key(self.cfg)),
            "doc_types": list(self.cfg.get("doc_types", [])),
            "jobs": self.watcher.job_list(),
        }

    def start(self) -> None:
        """Start the watcher and the HTTP server; returns once both are up."""
        self.watcher.start()
        self.server = WebUIServer(self, ("127.0.0.1", self.port))
        log.info("Web UI: http://127.0.0.1:%d", self.port)
        print(f"Web UI: http://127.0.0.1:{self.port}")
        import threading

        threading.Thread(target=self.server.serve_forever, name="webui", daemon=True).start()

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        self.watcher.stop()
