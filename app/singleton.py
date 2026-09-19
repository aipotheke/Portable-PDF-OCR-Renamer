"""M4: single-instance lockfile + rotating file log next to the exe."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

from . import config as _config

LOCK_FILENAME = "app.lock"
LOG_FILENAME = "app.log"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 2


def lock_path() -> Path:
    return _config.config_dir() / LOCK_FILENAME


def acquire_lock() -> bool:
    """Try to become the single instance. Returns False if another is running.

    The lock is held via an O_EXCL create; a stale lockfile (crash) is detected
    by trying to open the writing process's handle — a leftover file without a
    live writer is simply replaced after the grace check below.
    """
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        pid = _read_pid(path)
        if pid is not None and _pid_alive(pid):
            return False
        # stale lock (crashed instance): remove and retry once
        try:
            path.unlink()
        except OSError:
            return False
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    return True


def release_lock() -> None:
    try:
        lock_path().unlink()
    except OSError:
        pass


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
                return True
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def setup_logging(verbose: bool = False) -> Path:
    """Configure root logging: console + rotating file (1 MB, 2 backups)."""
    log_path = _config.config_dir() / LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers.clear()
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(fmt))
    root.addHandler(console)
    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(fmt))
    root.addHandler(file_handler)
    return log_path
