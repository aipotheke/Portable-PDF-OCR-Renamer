"""Unit tests for rules.py (date extraction, sanitize, processed registry)."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from app import rules


# ---- sanitize_name -----------------------------------------------------------

def test_sanitize_name_removes_windows_illegal_chars():
    assert rules.sanitize_name("scan:0231") == "scan_0231"
    assert rules.sanitize_name("a<b>c?d*e") == "a_b_c_d_e"
    assert rules.sanitize_name('quote"pipe|q') == "quote_pipe_q"


def test_sanitize_name_collapses_whitespace_and_strips_dot():
    assert rules.sanitize_name("  my   file .  ") == "my_file"


def test_sanitize_name_empty_fallback():
    assert rules.sanitize_name("///") == "file"
    assert rules.sanitize_name("") == "file"


def test_sanitize_name_strips_control_and_non_printable():
    assert rules.sanitize_name("a\x00b\x07c") == "abc"


# ---- extract_date ------------------------------------------------------------

def test_extract_date_format(tmp_path: Path):
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4")
    date = rules.extract_date(f)
    assert len(date) == 10
    datetime.strptime(date, "%Y-%m-%d")


def test_extract_date_matches_ctime(tmp_path: Path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4")
    os.utime(f, (1_700_000_000, 1_700_000_000))
    # On Windows st_ctime is creation; on POSIX it is metadata-change, which we set now.
    expected = datetime.fromtimestamp(rules._creation_time(f)).strftime("%Y-%m-%d")
    assert rules.extract_date(f) == expected


# ---- registry ----------------------------------------------------------------

def test_registry_roundtrip(tmp_path: Path, monkeypatch):
    reg_file = tmp_path / rules.REGISTRY_FILENAME
    monkeypatch.setattr(rules, "registry_path", lambda: reg_file)

    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4")
    assert rules.is_processed(f)[0] is False

    rules.mark_processed(f, "2026-09-17_invoice_scan.pdf")
    done, name = rules.is_processed(f)
    assert done is True
    assert name == "2026-09-17_invoice_scan.pdf"

    data = json.loads(reg_file.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert rules.registry_key(f) in data


def test_registry_corrupt_returns_empty(tmp_path: Path, monkeypatch):
    reg_file = tmp_path / rules.REGISTRY_FILENAME
    reg_file.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(rules, "registry_path", lambda: reg_file)
    assert rules.load_registry() == {}


def test_registry_key_changes_with_size(tmp_path: Path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"abcd")
    k1 = rules.registry_key(f)
    f.write_bytes(b"abcdef")
    k2 = rules.registry_key(f)
    assert k1 != k2
