"""Entrypoint for the PDF OCR Renamer.

M1: `python -m app.main --once FILE` processes a single PDF end-to-end.
M2: `python -m app.main --watch` starts the folder watcher with its single-worker
queue (Ctrl+C to stop).
M3: `python -m app.main --serve` starts the watcher plus the web UI at
http://127.0.0.1:8765 (Ctrl+C to stop).
M4: `python -m app.main` (no args) is the full app — single instance, web UI,
tray icon (Open UI / Pause-Resume / Quit), rotating app.log next to the exe.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

from . import ocr, pdfops, rules
from .config import load_config


log = logging.getLogger("main")


def process_one(pdf_path: Path, cfg: dict | None = None) -> Path | None:
    """Process a single PDF: skip if already processed, else OCR → classify → rename + embed.

    Returns the path to the written PDF in processed/, or None if skipped.
    """
    cfg = cfg or load_config()
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)

    already, prev_name = rules.is_processed(pdf_path)
    if already:
        log.info("Skipping already-processed file: %s (→ %s)", pdf_path.name, prev_name)
        print(f"Skipping already-processed file: {pdf_path.name} (→ {prev_name})")
        return None

    watch_folder = Path(cfg.get("watch_folder") or pdf_path.parent).resolve()
    keep_sidecar = bool(cfg.get("keep_md_sidecar", True))

    log.info("OCR: %s", pdf_path.name)
    print(f"OCR: {pdf_path.name}")
    markdown = ocr.ocr_pdf(pdf_path, cfg)

    filetype = ocr.classify(markdown, list(cfg.get("doc_types", [])), cfg)
    log.info("Classified as: %s", filetype)
    print(f"Classified as: {filetype}")

    out = pdfops.embed_and_write(pdf_path, markdown, filetype, watch_folder, keep_sidecar)
    rules.mark_processed(pdf_path, out.name)
    log.info("Done: %s → %s", pdf_path.name, out)
    print(f"Done: {pdf_path.name} → {out}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.main", description="Portable PDF OCR Renamer")
    parser.add_argument("--once", metavar="FILE", help="process a single PDF and exit")
    parser.add_argument("--watch", action="store_true", help="watch the configured folder for new PDFs (Ctrl+C to stop)")
    parser.add_argument("--serve", action="store_true", help="watcher + web UI at http://127.0.0.1:8765 (Ctrl+C to stop)")
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.once:
        cfg = load_config()
        try:
            process_one(Path(args.once), cfg)
        except FileNotFoundError as exc:
            print(f"File not found: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            log.exception("Processing failed")
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.watch:
        from .watcher import FolderWatcher

        cfg = load_config()
        watcher = FolderWatcher(cfg)
        watcher.start()
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            watcher.stop()
        return 0

    if args.serve:
        from .webui.server import WebUI

        cfg = load_config()
        ui = WebUI(cfg)
        ui.start()
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            ui.stop()
        return 0

    # default: full app (single instance, web UI, tray)
    from .singleton import acquire_lock, release_lock, setup_logging
    from .tray import start_tray
    from .webui.server import WebUI

    if not acquire_lock():
        print("Another instance is already running (app.lock exists) — opening its UI.")
        try:
            import webbrowser

            webbrowser.open("http://127.0.0.1:8765")
        except Exception:
            pass
        return 0

    setup_logging(args.verbose)
    quit_event = threading.Event()
    cfg = load_config()
    ui = WebUI(cfg)
    ui.start()
    tray = start_tray(ui, quit_event)
    try:
        while not quit_event.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        tray.stop()
        ui.stop()
        release_lock()
        log.info("Bye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
