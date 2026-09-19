"""Unit tests for M4: single-instance lock, rotating log, tray fallback, no-key idle."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest

from app import config as cfgmod
from app import singleton, tray as traymod, watcher as w


# ---- singleton lock ----------------------------------------------------------

def test_lock_acquire_and_release(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cfgmod, "config_dir", lambda: tmp_path)
    assert singleton.acquire_lock() is True
    assert (tmp_path / "app.lock").exists()
    singleton.release_lock()
    assert not (tmp_path / "app.lock").exists()


def test_lock_blocks_second_instance(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cfgmod, "config_dir", lambda: tmp_path)
    assert singleton.acquire_lock() is True
    assert singleton.acquire_lock() is False
    singleton.release_lock()
    assert singleton.acquire_lock() is True


def test_lock_stale_file_removed(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cfgmod, "config_dir", lambda: tmp_path)
    # PID that certainly does not exist -> stale lock gets replaced
    (tmp_path / "app.lock").write_text("999999999", encoding="utf-8")
    assert singleton.acquire_lock() is True
    singleton.release_lock()


def test_lock_corrupt_file_treated_as_stale(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cfgmod, "config_dir", lambda: tmp_path)
    (tmp_path / "app.lock").write_text("not-a-pid", encoding="utf-8")
    assert singleton.acquire_lock() is True
    singleton.release_lock()


# ---- logging ------------------------------------------------------------------

def test_setup_logging_writes_file(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cfgmod, "config_dir", lambda: tmp_path)
    log_path = singleton.setup_logging()
    assert log_path == tmp_path / "app.log"
    logging.getLogger("m4test").info("hello-log")
    for h in logging.getLogger().handlers:
        h.flush()
    content = log_path.read_text(encoding="utf-8")
    assert "hello-log" in content


# ---- tray ------------------------------------------------------------------------

def test_tray_start_fails_gracefully_headless(tmp_path: Path, monkeypatch):
    from app.webui.server import WebUI

    cfg = dict(cfgmod.DEFAULTS)
    cfg["watch_folder"] = str(tmp_path)
    app = WebUI(cfg, watcher=w.FolderWatcher(cfg), port=0)
    import threading

    quit_event = threading.Event()
    t = traymod.Tray(app, quit_event)
    started = t.start()
    if started:
        t.stop()
    else:
        assert t.icon is None
    assert quit_event.is_set() is False


def test_tray_toggle_pause(tmp_path: Path):
    from app.webui.server import WebUI

    cfg = dict(cfgmod.DEFAULTS)
    cfg["watch_folder"] = str(tmp_path)
    app = WebUI(cfg, watcher=w.FolderWatcher(cfg), port=0)
    import threading

    t = traymod.Tray(app, threading.Event())
    t.toggle_pause()
    assert app.watcher.paused is True
    t.toggle_pause()
    assert app.watcher.paused is False


def test_icon_path_missing_returns_none(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cfgmod, "config_dir", lambda: tmp_path)
    assert traymod.icon_path() is None


# ---- watcher idles without API key ------------------------------------------------

def _wait_until(cond, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_worker_idles_until_api_key_set(tmp_path: Path, monkeypatch):
    cfg = {
        "watch_folder": str(tmp_path),
        "stability_seconds": 0.2,
        "stability_max_wait": 5,
        "ionos_api_key": "",
    }
    monkeypatch.delenv("IONOS_API_TOKEN", raising=False)
    f = tmp_path / "needskey.pdf"
    f.write_bytes(b"%PDF-1.4")

    processed: list[Path] = []

    def process(path: Path):
        processed.append(Path(path))
        return path

    fw = w.FolderWatcher(cfg, process=process)
    fw.start()
    try:
        fw.enqueue(f)
        assert _wait_until(lambda: any(j["stage"] == "waiting_for_key" for j in fw.job_list()))
        # still idle after a moment
        time.sleep(0.5)
        assert processed == []
        # setting a key lets the job continue
        fw.cfg["ionos_api_key"] = "k"
        assert _wait_until(lambda: any(j["stage"] == "done" for j in fw.job_list()))
        assert len(processed) == 1
    finally:
        fw.stop()
