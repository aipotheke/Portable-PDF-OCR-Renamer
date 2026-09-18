"""M2 watcher: watchdog observer + stability check + single-worker job queue.

New/modified PDFs in the watch folder (non-recursive) are enqueued, the worker
waits until each file stops changing (scanner/copy-in safety), skips files already
in the processed registry, and processes them sequentially (API-friendly).
Per-file errors are caught and recorded; the worker never crashes.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from . import rules
from .config import config_dir


log = logging.getLogger("watcher")

MISSING_POLLS_LIMIT = 20


def file_fingerprint(path: Path) -> tuple[int, int] | None:
    """Return (size, mtime) of a file, or None if it cannot be statted."""
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return st.st_size, int(st.st_mtime)


def wait_for_stability(
    path: Path,
    stability_seconds: float = 3.0,
    max_wait: float = 120.0,
    poll_interval: float = 0.5,
    stop: threading.Event | None = None,
) -> bool:
    """Wait until a file stops changing.

    True once size+mtime stayed unchanged for `stability_seconds`. False if the
    file never stabilizes within `max_wait`, disappears, or `stop` is set.
    """
    path = Path(path)
    deadline = time.monotonic() + max_wait
    last: tuple[int, int] | None = None
    stable_since = 0.0
    missing = 0
    while time.monotonic() < deadline:
        if stop is not None and stop.is_set():
            return False
        fp = file_fingerprint(path)
        now = time.monotonic()
        if fp is None:
            missing += 1
            if missing >= MISSING_POLLS_LIMIT:
                return False
            last = None
        else:
            missing = 0
            if fp == last and now - stable_since >= stability_seconds:
                return True
            if fp != last:
                last = fp
                stable_since = now
        remaining = deadline - now
        if remaining <= 0:
            break
        time.sleep(min(poll_interval, remaining))
    return False


class _PDFEventHandler(FileSystemEventHandler):
    """Enqueues PDF events from the watch folder (non-recursive)."""

    def __init__(self, watcher: FolderWatcher):
        self.watcher = watcher

    def on_created(self, event: FileSystemEvent) -> None:
        self._maybe_enqueue(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._maybe_enqueue(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        if getattr(event, "is_directory", False):
            return
        dest = Path(getattr(event, "dest_path", ""))
        if dest.suffix.lower() == ".pdf":
            self.watcher.enqueue(dest)

    def _maybe_enqueue(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        p = Path(event.src_path)
        if p.suffix.lower() == ".pdf":
            self.watcher.enqueue(p)


class FolderWatcher:
    """Watches a folder for PDFs and processes them through a single worker."""

    def __init__(
        self,
        cfg: dict[str, Any],
        process: Callable[[Path], Path | None] | None = None,
    ):
        self.cfg = cfg
        self._process = process
        self.queue: queue.Queue[Path | None] = queue.Queue()
        self._enqueued: set[str] = set()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.observer: Observer | None = None
        self.worker: threading.Thread | None = None
        self.stability_seconds = float(cfg.get("stability_seconds", 3.0))
        self.stability_max_wait = float(cfg.get("stability_max_wait", 120.0))

    # ---- status -------------------------------------------------------------

    def _record(self, path: Path, stage: str, **fields: Any) -> None:
        with self._lock:
            key = str(Path(path).resolve())
            job = self._jobs.setdefault(key, {"name": Path(path).name})
            job["stage"] = stage
            job["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
            job.update(fields)

    def job_list(self) -> list[dict[str, Any]]:
        """Snapshot of job states (for the web UI / tests)."""
        with self._lock:
            return [dict(j) for j in self._jobs.values()]

    def _job_stage(self, path: Path) -> str | None:
        key = str(Path(path).resolve())
        with self._lock:
            job = self._jobs.get(key)
            return job["stage"] if job else None

    # ---- folder / queue -----------------------------------------------------

    def folder(self) -> Path:
        configured = self.cfg.get("watch_folder") or ""
        return Path(configured) if configured else config_dir()

    def enqueue(self, path: Path) -> bool:
        """Add a PDF to the queue; returns False for non-PDFs/missing/duplicates."""
        p = Path(path)
        if not p.is_file() or p.suffix.lower() != ".pdf":
            return False
        key = str(p.resolve())
        with self._lock:
            if key in self._enqueued:
                return False
            self._enqueued.add(key)
            self._jobs.setdefault(key, {"name": p.name, "stage": "queued"})
        self.queue.put(p)
        log.info("Queued: %s", p.name)
        return True

    def scan_existing(self) -> int:
        """Enqueue all PDFs currently in the watch folder (for manual scans)."""
        count = 0
        folder = self.folder()
        if folder.is_dir():
            for p in sorted(f for f in folder.iterdir() if f.is_file() and f.suffix.lower() == ".pdf"):
                if self.enqueue(p):
                    count += 1
        return count

    # ---- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        handler = _PDFEventHandler(self)
        self.observer = Observer()
        self.observer.schedule(handler, str(self.folder()), recursive=False)
        self.observer.start()
        self.worker = threading.Thread(target=self._worker, name="ocr-worker", daemon=True)
        self.worker.start()
        log.info("Watching folder: %s", self.folder())
        print(f"Watching folder: {self.folder()}")

    def stop(self) -> None:
        self._stop.set()
        if self.observer is not None:
            self.observer.stop()
            self.observer.join(timeout=5)
            self.observer = None
        self.queue.put(None)
        if self.worker is not None:
            self.worker.join(timeout=10)
            self.worker = None
        log.info("Watcher stopped")

    # ---- worker ------------------------------------------------------------

    def _default_process(self, path: Path) -> Path | None:
        from .main import process_one

        return process_one(path, self.cfg)

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                item = self.queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:
                break
            path = Path(item)
            try:
                self._handle(path)
            except Exception:
                log.exception("Unexpected worker error for %s", path)
                self._record(path, "error", error="unexpected worker error")

    def _handle(self, path: Path) -> None:
        path = Path(path)
        try:
            self._handle_inner(path)
        finally:
            with self._lock:
                self._enqueued.discard(str(path.resolve()))

    def _handle_inner(self, path: Path) -> None:
        path = Path(path)
        log.info("New PDF: %s", path.name)
        print(f"New PDF: {path.name}")
        self._record(path, "waiting_stable")

        if not wait_for_stability(
            path,
            self.stability_seconds,
            self.stability_max_wait,
            stop=self._stop,
        ):
            if self._stop.is_set():
                return
            msg = "file never stabilized (still being written or disappeared)"
            log.error("Skipping %s: %s", path.name, msg)
            self._record(path, "error", error=msg)
            return

        already, prev = rules.is_processed(path)
        if already:
            log.info("Skipping already-processed file: %s (→ %s)", path.name, prev)
            self._record(path, "skipped", result=prev)
            return

        self._record(path, "processing")
        process = self._process or self._default_process
        try:
            out = process(path)
        except Exception as exc:
            log.exception("Processing failed: %s", path.name)
            self._record(path, "error", error=str(exc))
            return
        self._record(path, "done", result=str(out) if out else None)
        log.info("Job done: %s → %s", path.name, out)
        print(f"Job done: {path.name} → {out}")
