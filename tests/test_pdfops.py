"""Unit tests for pdfops: embed attachment, target name, no-overwrite, sidecar."""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from app import pdfops, rules


def _make_pdf(path: Path) -> Path:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def test_build_target_name_format(tmp_path: Path, monkeypatch):
    src = tmp_path / "Scan 0231.pdf"
    src.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(pdfops, "extract_date", lambda p: "2026-09-17")
    name = pdfops.build_target_name(src, "invoice")
    assert name == "2026-09-17_invoice_Scan_0231.pdf"


def test_build_target_name_with_sender(tmp_path: Path, monkeypatch):
    src = tmp_path / "Scan 0231.pdf"
    src.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(pdfops, "extract_date", lambda p: "2026-09-17")
    name = pdfops.build_target_name(src, "invoice", "Amazon EU")
    assert name == "2026-09-17_invoice_Amazon_EU_Scan_0231.pdf"


def test_build_target_name_sender_unknown_omitted(tmp_path: Path, monkeypatch):
    src = tmp_path / "Scan 0231.pdf"
    src.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(pdfops, "extract_date", lambda p: "2026-09-17")
    name = pdfops.build_target_name(src, "invoice", "unknown")
    assert name == "2026-09-17_invoice_Scan_0231.pdf"


def test_embed_and_write_creates_processed_and_attachment(tmp_path: Path, monkeypatch):
    src = _make_pdf(tmp_path / "scan.pdf")
    monkeypatch.setattr(pdfops, "extract_date", lambda p: "2026-09-17")
    out = pdfops.embed_and_write(src, "# Invoice\n\ntotal: 12.34", "invoice", tmp_path, keep_md_sidecar=True)

    assert out.exists()
    assert out.parent.name == pdfops.PROCESSED_SUBDIR
    assert out.name == "2026-09-17_invoice_scan.pdf"

    reader = PdfReader(str(out))
    names = reader.attachments
    assert pdfops.ATTACHMENT_NAME in names
    content = b"".join(names[pdfops.ATTACHMENT_NAME])
    assert content == b"# Invoice\n\ntotal: 12.34"


def test_sidecar_written(tmp_path: Path, monkeypatch):
    src = _make_pdf(tmp_path / "scan.pdf")
    monkeypatch.setattr(pdfops, "extract_date", lambda p: "2026-09-17")
    pdfops.embed_and_write(src, "hello md", "invoice", tmp_path, keep_md_sidecar=True)
    md = tmp_path / pdfops.MD_SUBDIR / "2026-09-17_invoice_scan.md"
    assert md.exists()
    assert md.read_text(encoding="utf-8") == "hello md"


def test_no_sidecar_when_disabled(tmp_path: Path, monkeypatch):
    src = _make_pdf(tmp_path / "scan.pdf")
    monkeypatch.setattr(pdfops, "extract_date", lambda p: "2026-09-17")
    pdfops.embed_and_write(src, "hello md", "invoice", tmp_path, keep_md_sidecar=False)
    assert not (tmp_path / pdfops.MD_SUBDIR).exists()


def test_no_overwrite_suffix(tmp_path: Path, monkeypatch):
    src = _make_pdf(tmp_path / "scan.pdf")
    monkeypatch.setattr(pdfops, "extract_date", lambda p: "2026-09-17")
    out1 = pdfops.embed_and_write(src, "first", "invoice", tmp_path, keep_md_sidecar=False)
    # second call with same content/name should get a _1 suffix, not overwrite out1
    out2 = pdfops.embed_and_write(src, "second", "invoice", tmp_path, keep_md_sidecar=False)
    assert out1 != out2
    assert out1.exists()
    assert out2.exists()
    assert out2.name == "2026-09-17_invoice_scan_1.pdf"
