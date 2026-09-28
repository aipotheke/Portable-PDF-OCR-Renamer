# Portable PDF OCR Renamer

A single-file Windows tool (`.exe` via PyInstaller) that watches a folder, OCRs new PDFs with the IONOS AI Model Hub (`lightonai/LightOnOCR-2-1B`), renames files to `date_filetype[_company]_oldname.pdf` (the LLM classifier also extracts the sender company name from the letterhead), embeds the Markdown output as a PDF attachment, and shows progress in a browser UI plus a system tray icon. **No Tesseract / OCRmyPDF.**

> Status: **M5 — PyInstaller build & portability**. All milestones implemented; final Windows portability test on real hardware pending.

## M1 scope

- `ocr.py` — render PDF pages with pypdfium2, base64-encode, call the IONOS OCR endpoint via the `openai` client (one request per page), join page outputs; classify the document type and extract the sender company name via a text model.
- `rules.py` — date extraction from file creation time (`st_ctime` on Windows); processed-registry (`processed.json`) read/write to skip already-handled files.
- `pdfops.py` — write the renamed PDF to a `processed/` subfolder, embed `ocr.md` as a PDF attachment, write a sidecar `.md` to an `md/` subfolder, atomic writes, Windows-safe filename sanitization, never overwrite existing targets.
- `config.py` — load/save `config.json` next to the exe with sensible defaults.
- CLI: `python -m app.main --once FILE` processes a single file end-to-end.

## M2 scope

- `watcher.py` — `watchdog` observer on the configured folder (non-recursive), new/modified/moved PDFs are enqueued (deduplicated while a job is in flight).
- Stability check — a job starts only after a file's size and mtime stayed unchanged for `stability_seconds` (default 3 s, max wait `stability_max_wait`); slow scanner writes and copy-ins never get partially OCR'd.
- Single worker thread — sequential OCR, API-friendly; per-file errors are caught, logged and recorded, and never crash the worker.
- Job status — every file has a stage (`queued` → `waiting_stable` → `processing` → `done`/`skipped`/`error`) with a timestamp; `job_list()` snapshots all jobs (ready for the M3 web UI).
- Pause/resume — the worker idles while paused; events keep being enqueued. Changing `watch_folder` restarts the observer.
- CLI: `python -m app.main --watch` runs the watcher (Ctrl+C to stop).

## M3 scope

- `webui/server.py` — local HTTP server (`http.server`) bound to **127.0.0.1:8765 only**.
  - `GET /` — single-page UI (`webui/index.html`, vanilla JS, polls `/api/status` every 2 s).
  - `GET /api/status` — watch folder, paused flag, API-key presence, doc types, job list.
  - `GET /api/config` — current config with the API key masked (last 4 chars only).
  - `POST /api/config` — validated updates (folder exists, key non-empty, ≥1 doc type, numeric fields), persisted atomically to `config.json`; live-applied (stability settings, watch folder).
  - `POST /api/scan` — enqueue all existing PDFs in the watch folder on demand.
- UI: setup banner when no API key is set, job table with stage badges, settings form (API key, watch folder, doc types).
- CLI: `python -m app.main --serve` starts watcher + web UI (Ctrl+C to stop).

## M4 scope

- `python -m app.main` (no args) runs the **full app**: single-instance check, web UI, tray icon.
- `tray.py` — pystray menu: **Open UI** (opens the browser), **Pause/Resume**, **Quit**. Icon dims while paused. Falls back to running without a tray when no backend is available (headless/dev). Icon from `assets/tray.png` when bundled, else a generated placeholder.
- `singleton.py` — single-instance lockfile (`app.lock`, PID-checked, stale locks cleaned up after crashes) and a rotating `app.log` (1 MB, 2 backups) next to the exe.
- No API key set → the watcher idles: new PDFs wait in `waiting_for_key` stage until a key is saved (UI shows a setup banner); no API calls are attempted.
- A second instance refuses to start and just opens the first instance's UI.

## Configuration

`config.json` is created next to the exe (or the project root in dev) on first run. Defaults:

```json
{
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
  "keep_md_sidecar": true,
  "stability_seconds": 3.0,
  "stability_max_wait": 120.0
}
```

The API key is read from the env var `IONOS_API_TOKEN` if `ionos_api_key` in config is empty.

## Usage

```bash
# install deps (editable)
pip install -e ".[test]"

# process a single file
python -m app.main --once path/to/scan.pdf

# watch the configured folder for new PDFs (Ctrl+C to stop)
python -m app.main --watch

# watcher + web UI at http://127.0.0.1:8765 (Ctrl+C to stop)
python -m app.main --serve

# full app: single instance, web UI, tray icon (this is what the exe runs)
python -m app.main

# run unit tests
pytest
```

The IONOS API token must be available via the `IONOS_API_TOKEN` environment variable (or set in `config.json`).

## Target filename

`YYYY-MM-DD_filetype_oldname.pdf` — e.g. `2026-09-17_invoice_scan0231.pdf`

- `date` = file creation date (`st_ctime` on Windows).
- `filetype` = one of the configured doc types, chosen by the classification model; `unknown` on failure.
- `oldname` = sanitized original stem (Windows-illegal chars removed).

Renamed PDFs and `ocr.md` attachments go to `processed/`; Markdown sidecars go to `md/` next to the source.

## Notes

- Classification uses `mistralai/Mistral-Small-24B-Instruct` (configurable). Reasoning-class models like gpt-oss-120b work but are more expensive for a labelling task.
- OCR is one API request per page, sequential. No batching (by design).
- The `processed.json` registry lives next to the exe alongside `config.json`.

## Build & portability (M5)

- `build.spec` — PyInstaller **onefile + windowed** (no console). Bundles `webui/index.html` (served from `_MEIPASS` when frozen) and `assets/tray.png`. Entry via `run.py` (package-safe import of `app.main`).
- Verified on Linux sandbox: the frozen binary runs the full app — `config.json`, `app.lock`, `app.log` are created **next to the exe**, watch folder defaults to the exe folder, web UI + config API work, a second launch is refused by the single-instance lock, and the tray degrades gracefully without a display.

### Build

```bash
pip install -e .
pip install pyinstaller
pyinstaller build.spec --noconfirm --clean
# -> dist/PDF-OCR-Renamer(.exe on Windows)
```

### Windows notes

- Build **on Windows** for a Windows exe (PyInstaller does not cross-compile); the spec is platform-agnostic.
- Copy the single exe anywhere (USB stick, scanner folder) and run it — no Python needed. `config.json`, `app.log`, `app.lock`, `processed.json`, `processed/` and `md/` are created next to the exe on demand.
- **SmartScreen/AV:** the exe is unsigned, so Windows may warn on first run ("More info → Run anyway"), and some antivirus tools flag PyInstaller onefile binaries. If that is a problem, sign the exe or fall back to `--onedir` (a folder with an exe is still "no install").
- The Windows tray icon requires `pystray`'s Win32 backend — included automatically.
