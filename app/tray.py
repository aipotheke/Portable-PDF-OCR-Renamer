"""M4: system tray icon (pystray) — Open UI, Pause/Resume, Quit.

Degrades gracefully: if no tray backend is available (headless/dev), the app
keeps running without a tray. The icon comes from `assets/tray.png` when bundled
(next to the exe or in _MEIPASS), else a generated placeholder image is used;
paused state swaps to a dimmed variant.
"""

from __future__ import annotations

import logging
import threading
import webbrowser
from pathlib import Path
from typing import Any, Callable

from . import config as _config
from .webui.server import WebUI


log = logging.getLogger("tray")

ICON_RELPATH = Path("assets") / "tray.png"
UI_URL = "http://127.0.0.1:8765"


def icon_path() -> Path | None:
    """Find the bundled tray icon: exe dir, _MEIPASS, or repo assets."""
    import sys

    candidates = [_config.config_dir() / ICON_RELPATH]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / ICON_RELPATH)
    for c in candidates:
        if c.is_file():
            return c
    return None


def _placeholder_image(paused: bool):
    from PIL import Image, ImageDraw

    color = (200, 130, 30) if paused else (40, 120, 90)
    img = Image.new("RGB", (64, 64), color)
    draw = ImageDraw.Draw(img)
    draw.rectangle((18, 14, 46, 50), fill="white")
    draw.line((18, 24, 46, 24), fill="black", width=2)
    draw.line((18, 32, 46, 32), fill="black", width=2)
    draw.line((18, 40, 38, 40), fill="black", width=2)
    return img


def _load_image(paused: bool):
    from PIL import Image

    path = icon_path()
    if path is None:
        return _placeholder_image(paused)
    img = Image.open(path)
    if paused:
        from PIL import ImageEnhance

        img = ImageEnhance.Brightness(img.convert("RGB")).enhance(0.55)
    return img


class Tray:
    """Owns the pystray icon; no-ops when no tray backend is available."""

    def __init__(self, app: WebUI, quit_event: threading.Event):
        self.app = app
        self.quit_event = quit_event
        self.icon: Any | None = None
        self._lock = threading.Lock()

    # ---- callbacks ------------------------------------------------------

    def open_ui(self) -> None:
        try:
            webbrowser.open(UI_URL)
        except Exception as exc:
            log.warning("Could not open browser: %s", exc)

    def toggle_pause(self) -> None:
        watcher = self.app.watcher
        if watcher.paused:
            watcher.resume()
        else:
            watcher.pause()
        self._update_icon()

    def quit(self) -> None:
        log.info("Quit requested from tray")
        self.quit_event.set()

    # ---- icon -----------------------------------------------------------

    def _update_icon(self) -> None:
        if self.icon is None:
            return
        with self._lock:
            try:
                self.icon.icon = _load_image(self.app.watcher.paused)
            except Exception as exc:
                log.warning("Could not update tray icon: %s", exc)

    def _menu(self):
        import pystray

        return pystray.Menu(
            pystray.MenuItem("Open UI", lambda *_: self.open_ui(), default=True),
            pystray.MenuItem("Pause/Resume", lambda *_: self.toggle_pause()),
            pystray.MenuItem("Quit", lambda *_: self.quit()),
        )

    # ---- lifecycle ------------------------------------------------------

    def start(self) -> bool:
        """Start the tray icon; returns False if no tray backend is available."""
        try:
            import pystray

            self.icon = pystray.Icon(
                "pdf_ocr_renamer",
                icon=_load_image(False),
                title="PDF OCR Renamer",
                menu=self._menu(),
            )
            self.icon.run_detached()
            log.info("Tray icon started")
            return True
        except Exception as exc:
            log.warning("Tray not available (%s) — running without tray icon", exc)
            print(f"Tray not available ({exc}) — running without tray icon")
            self.icon = None
            return False

    def stop(self) -> None:
        icon = self.icon
        self.icon = None
        if icon is None:
            return
        try:
            icon.stop()
        except Exception as exc:
            log.warning("Error stopping tray icon: %s", exc)
        setup_thread = getattr(icon, "_setup_thread", None)
        if setup_thread is not None and setup_thread is not threading.current_thread() and setup_thread.is_alive():
            setup_thread.join(timeout=10)


def start_tray(app: WebUI, quit_event: threading.Event) -> Tray:
    tray = Tray(app, quit_event)
    tray.start()
    return tray
