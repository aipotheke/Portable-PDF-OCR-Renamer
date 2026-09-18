# Portable PDF OCR Renamer

A single-file Windows tool (`.exe` via PyInstaller) that watches a folder, OCRs new PDFs with the IONOS AI Model Hub (`lightonai/LightOnOCR-2-1B`), renames files to `date_filetype_oldname.pdf`, embeds the Markdown output as a PDF attachment, and shows progress in a browser UI plus a system tray icon. **No Tesseract / OCRmyPDF.**

> Status: **M2 — Watcher + queue**. Core pipeline (M1) plus folder watching with a single-worker queue. Web UI, tray icon and PyInstaller build are planned in later milestones.

## M1 scope

- `ocr.py` — render PDF pages with pypdfium2, base64-encode, call the IONOS OCR endpoint via the `openai` client (one request per page), join page outputs; classify the document type via a text model.
- `rules.py` — date extraction from file creation time (`st_ctime` on Windows); processed-registry (`processed.json`) read/write to skip already-handled files.
- `pdfops.py` — write the renamed PDF to a `processed/` subfolder, embed `ocr.md` as a PDF attachment, write a sidecar `.md` to an `md/` subfolder, atomic writes, Windows-safe filename sanitization, never overwrite existing targets.
- `config.py` — load/save `config.json` next to the exe with sensible defaults.
- CLI: `python -m app.main --once FILE` processes a single file end-to-end.

## M2 scope

- `watcher.py` — `watchdog` observer on the configured folder (non-recursive), new/modified/moved PDFs are enqueued (deduplicated while a job is in flight).
- Stability check — a job starts only after a file's size and mtime stayed unchanged for `stability_seconds` (default 3 s, max wait `stability_max_wait`); slow scanner writes and copy-ins never get partially OCR'd.
- Single worker thread — sequential OCR, API-friendly; per-file errors are caught, logged and recorded, and never crash the worker.
- Job status — every file has a stage (`queued` → `waiting_stable` → `processing` → `done`/`skipped`/`error`) with a timestamp; `job_list()` snapshots all jobs (ready for the M3 web UI).
- CLI: `python -m app.main --watch` runs the watcher (Ctrl+C to stop).

The web UI and tray icon are **not** implemented yet.

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

## Usage (M1)

```bash
# install deps (editable)
pip install -e ".[test]"

# process a single file
python -m app.main --once path/to/scan.pdf

# watch the configured folder for new PDFs (Ctrl+C to stop)
python -m app.main --watch

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
- A signed exe may trigger SmartScreen warnings; see M5 in the build plan.
