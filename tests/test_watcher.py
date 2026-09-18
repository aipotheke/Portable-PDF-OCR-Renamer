"""Unit tests for the M2 watcher (stability check, queue, worker; no API calls)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from app import watcher as w


# ---- wait_for_stability -------------------------------------------------------

def test_stability_returns_true_for_settled_file(tmp_path: Path):
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4")
    assert w.wait_for_stability(f, stability_seconds=0.2, max_wait=5) is True


def test_stability_returns_false_for_missing_file(tmp_path: Path):
    f = tmp_path / "missing.pdf"
    assert w.wait_for_stability(f, stability_seconds=0.2, max_wait=1) is False


def test_stability_aborts_on_stop_event(tmp_path: Path):
    f = tmp_path / "growing.pdf"
    f.write_bytes(b"%PDF-1.4")
    stop = threading.Event()
    stop.set()
    assert w.wait_for_stability(f, stability_seconds=0.2, max_wait=5, stop=stop) is False


def test_stability_waits_until_writer_finishes(tmp_path: Path):
    f = tmp_path / "slowcopy.pdf"
    f.write_bytes(b"%PDF-1.4 start")

    def slow_writer():
        for i in range(4):
            time.sleep(0.3)
            with open(f, "ab") as fh:
                fh.write(b"x" * 1024)

    t = threading.Thread(target=slow_writer)
    t.start()
    start = time.monotonic()
    ok = w.wait_for_stability(f, stability_seconds=0.4, max_wait=10)
    elapsed = time.monotonic() - start
    t.join()
    assert ok is True
    # returned only after writes stopped for the stability window
    assert elapsed >= 0.4


def test_stability_returns_false_for_never_stabilizing_file(tmp_path: Path):
    f = tmp_path / "endless.pdf"
    f.write_bytes(b"%PDF-1.4")
    stop = threading.Event()

    def endless():
        while not stop.is_set():
            with open(f, "ab") as fh:
                fh.write(b"x")
            time.sleep(0.05)

    t = threading.Thread(target=endless)
    t.start()
    ok = w.wait_for_stability(f, stability_seconds=2.0, max_wait=1.5, poll_interval=0.1)
    stop.set()
    t.join()
    assert ok is False


# ---- enqueue ------------------------------------------------------------------

def _cfg(folder: Path) -> dict:
    return {"watch_folder": str(folder), "stability_seconds": 0.2, "stability_max_wait": 5}


def test_enqueue_rejects_non_pdf_and_missing(tmp_path: Path):
    fw = w.FolderWatcher(_cfg(tmp_path))
    assert fw.enqueue(tmp_path / "nope.pdf") is False
    txt = tmp_path / "note.txt"
    txt.write_text("x")
    assert fw.enqueue(txt) is False


def test_enqueue_deduplicates(tmp_path: Path):
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4")
    fw = w.FolderWatcher(_cfg(tmp_path))
    assert fw.enqueue(f) is True
    assert fw.enqueue(f) is False
    assert fw.queue.qsize() == 1


def test_enqueue_requeues_after_job_finished(tmp_path: Path):
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4")
    fw = w.FolderWatcher(_cfg(tmp_path))
    fw.enqueue(f)
    fw._handle(f)
    assert fw.enqueue(f) is True


def test_scan_existing_enqueues_only_pdfs(tmp_path: Path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "b.PDF").write_bytes(b"%PDF-1.4")
    (tmp_path / "c.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "d.pdf").write_bytes(b"%PDF-1.4")
    fw = w.FolderWatcher(_cfg(tmp_path))
    n = fw.scan_existing()
    assert n == 2
    names = {p.name for p in list(fw.queue.queue)}
    assert names == {"a.pdf", "b.PDF"}


def test_folder_defaults_to_config_dir_when_unset(tmp_path: Path):
    fw = w.FolderWatcher({"watch_folder": ""})
    assert fw.folder() == w.config_dir()


# ---- worker ---------------------------------------------------------------

def _wait_until(cond, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


def _jobs(fw, name):
    return [j for j in fw.job_list() if j["name"] == name]


def test_worker_processes_file_end_to_end(tmp_path: Path):
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4")
    processed: list[Path] = []

    def process(path: Path):
        processed.append(Path(path))
        return tmp_path / "processed" / "out.pdf"

    fw = w.FolderWatcher(_cfg(tmp_path), process=process)
    fw.start()
    try:
        fw.enqueue(f)
        assert _wait_until(lambda: any(j["stage"] == "done" for j in _jobs(fw, "scan.pdf")))
        assert [p.name for p in processed] == ["scan.pdf"]
        job = _jobs(fw, "scan.pdf")[0]
        assert job["result"] == str(tmp_path / "processed" / "out.pdf")
        assert "updated" in job
    finally:
        fw.stop()


def test_worker_records_error_and_survives(tmp_path: Path):
    f = tmp_path / "bad.pdf"
    f.write_bytes(b"%PDF-1.4")

    def process(path: Path):
        raise RuntimeError("boom")

    fw = w.FolderWatcher(_cfg(tmp_path), process=process)
    fw.start()
    try:
        fw.enqueue(f)
        assert _wait_until(lambda: any(j["stage"] == "error" for j in _jobs(fw, "bad.pdf")))
        # worker still alive and can process another file
        f2 = tmp_path / "good.pdf"
        f2.write_bytes(b"%PDF-1.4")
        fw.enqueue(f2)
        assert _wait_until(lambda: any(j["stage"] == "error" for j in _jobs(fw, "good.pdf")))
        assert fw.worker is not None and fw.worker.is_alive()
    finally:
        fw.stop()


def test_worker_skips_already_processed(tmp_path: Path, monkeypatch):
    from app import rules

    f = tmp_path / "dup.pdf"
    f.write_bytes(b"%PDF-1.4")
    reg_file = tmp_path / rules.REGISTRY_FILENAME
    monkeypatch.setattr(rules, "registry_path", lambda: reg_file)
    rules.mark_processed(f, "2026-09-17_invoice_dup.pdf")

    processed: list[Path] = []

    def process(path: Path):
        processed.append(Path(path))
        return path

    fw = w.FolderWatcher(_cfg(tmp_path), process=process)
    fw.start()
    try:
        fw.enqueue(f)
        assert _wait_until(lambda: any(j["stage"] == "skipped" for j in _jobs(fw, "dup.pdf")))
        assert processed == []
        job = _jobs(fw, "dup.pdf")[0]
        assert job["result"] == "2026-09-17_invoice_dup.pdf"
    finally:
        fw.stop()


def test_worker_marks_error_on_unstable_file(tmp_path: Path):
    f = tmp_path / "ghost.pdf"
    f.write_bytes(b"%PDF-1.4")
    fw = w.FolderWatcher(
        {
            "watch_folder": str(tmp_path),
            "stability_seconds": 1.0,
            "stability_max_wait": 0.8,
        }
    )
    fw.start()
    try:
        fw.enqueue(f)
        # delete while the worker waits for stability -> stability check fails
        assert _wait_until(lambda: any(j["stage"] == "waiting_stable" for j in _jobs(fw, "ghost.pdf")))
        f.unlink()
        assert _wait_until(lambda: any(j["stage"] == "error" for j in _jobs(fw, "ghost.pdf")))
    finally:
        fw.stop()
