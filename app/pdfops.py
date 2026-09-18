"""PDF operations: embed ocr.md, build the target name, write to processed/ + md/ sidecar.

This module does NOT render pages (rendering lives in ocr.py). It takes the OCR
Markdown, attaches it to the source PDF as `ocr.md`, builds the target filename
`YYYY-MM-DD_filetype_oldname.pdf`, and writes the result atomically to a `processed/`
subfolder. A Markdown sidecar is written to `md/` next to the source when enabled.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from .rules import extract_date, sanitize_name


log = logging.getLogger("pdfops")

ATTACHMENT_NAME = "ocr.md"
PROCESSED_SUBDIR = "processed"
MD_SUBDIR = "md"
MAX_PATH_LEN = 250


def build_target_name(source: Path, filetype: str) -> str:
    """Build `YYYY-MM-DD_filetype_oldname.pdf` from the source file."""
    date = extract_date(source)
    ft = sanitize_name(filetype)
    stem = sanitize_name(source.stem)
    return f"{date}_{ft}_{stem}.pdf"


def _unique_path(directory: Path, filename: str) -> Path:
    """Return a non-existing target path in `directory`, adding _1, _2, ... suffixes if needed."""
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    i = 1
    while True:
        candidate = directory / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def _truncate_path(directory: Path, filename: str) -> Path:
    """Ensure the full target path stays under MAX_PATH_LEN by trimming the stem."""
    candidate = directory / filename
    if len(str(candidate)) <= MAX_PATH_LEN:
        return candidate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    over = len(str(candidate)) - MAX_PATH_LEN
    if over >= len(stem):
        stem = "file"
    else:
        stem = stem[: len(stem) - over]
    return directory / f"{stem}{suffix}"


def embed_and_write(
    source: Path,
    markdown: str,
    filetype: str,
    watch_folder: Path,
    keep_md_sidecar: bool = True,
) -> Path:
    """Embed ocr.md into the source PDF and write the renamed copy to processed/.

    Returns the path to the written PDF in `processed/`.
    """
    processed_dir = Path(watch_folder) / PROCESSED_SUBDIR
    processed_dir.mkdir(parents=True, exist_ok=True)

    target_name = build_target_name(source, filetype)
    target_name = sanitize_name(target_name)
    target = _unique_path(processed_dir, target_name)
    target = _truncate_path(target.parent, target.name)
    target = _unique_path(target.parent, target.name)

    reader = PdfReader(str(source))
    writer = PdfWriter(clone_from=reader)
    writer.add_attachment(ATTACHMENT_NAME, markdown.encode("utf-8"))

    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "wb") as fh:
        writer.write(fh)
    tmp.replace(target)
    log.info("Wrote processed PDF: %s", target)

    if keep_md_sidecar:
        md_dir = Path(watch_folder) / MD_SUBDIR
        md_dir.mkdir(parents=True, exist_ok=True)
        md_name = Path(target.name).with_suffix(".md")
        md_target = _unique_path(md_dir, md_name)
        md_tmp = md_target.with_suffix(".md.tmp")
        md_tmp.write_text(markdown, encoding="utf-8")
        md_tmp.replace(md_target)
        log.info("Wrote Markdown sidecar: %s", md_target)

    return target
