"""Unit tests for ocr.classify / match_type (no API calls)."""

from __future__ import annotations

from app import ocr


TYPES = ["invoice", "letter", "receipt", "contract", "other"]


def test_match_type_exact():
    assert ocr.match_type("invoice", TYPES) == "invoice"


def test_match_type_case_insensitive():
    assert ocr.match_type("Invoice", TYPES) == "invoice"


def test_match_type_strips_punctuation():
    assert ocr.match_type("'Receipt.'", TYPES) == "receipt"
    assert ocr.match_type("(letter)", TYPES) == "letter"


def test_match_type_strips_whitespace():
    assert ocr.match_type("  contract  ", TYPES) == "contract"


def test_match_type_unknown_returns_none():
    assert ocr.match_type("magazine", TYPES) is None


def test_match_type_empty_returns_none():
    assert ocr.match_type("", TYPES) is None
    assert ocr.match_type(None, TYPES) is None  # type: ignore[arg-type]


def test_match_type_preserves_configured_casing():
    assert ocr.match_type("invoice", ["Invoice", "Letter"]) == "Invoice"


def test_classify_empty_types_returns_unknown(monkeypatch):
    # no types configured -> short-circuit, no client needed
    cfg = {"max_retries": 1}
    assert ocr.classify("some text", [], cfg) == "unknown"


from types import SimpleNamespace


def _resp(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_classify_call_failure_falls_back_unknown(monkeypatch):
    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**k):
                    raise RuntimeError("api down")

    monkeypatch.setattr(ocr, "_make_client", lambda cfg: FakeClient())
    cfg = {"max_retries": 1, "classify_model": "m"}
    assert ocr.classify("text", TYPES, cfg) == "unknown"


def test_classify_returns_matched_type(monkeypatch):
    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**k):
                    return _resp("invoice")

    monkeypatch.setattr(ocr, "_make_client", lambda cfg: FakeClient())
    cfg = {"max_retries": 1, "classify_model": "m"}
    assert ocr.classify("text", TYPES, cfg) == "invoice"


def test_classify_returns_unknown_for_invalid_answer(monkeypatch):
    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**k):
                    return _resp("magazine")

    monkeypatch.setattr(ocr, "_make_client", lambda cfg: FakeClient())
    cfg = {"max_retries": 1, "classify_model": "m"}
    assert ocr.classify("text", TYPES, cfg) == "unknown"
