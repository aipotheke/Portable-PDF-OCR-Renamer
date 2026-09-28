"""Unit tests for ocr.classify / parse_answer / match_type (no API calls)."""

from __future__ import annotations

from types import SimpleNamespace

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
    assert ocr.classify("some text", [], cfg) == ("unknown", "")


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
    assert ocr.classify("text", TYPES, cfg) == ("unknown", "")


def test_classify_returns_matched_type_and_sender(monkeypatch):
    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**k):
                    return _resp("type: invoice\nsender: Amazon EU S.à r.l.")

    monkeypatch.setattr(ocr, "_make_client", lambda cfg: FakeClient())
    cfg = {"max_retries": 1, "classify_model": "m"}
    assert ocr.classify("text", TYPES, cfg) == ("invoice", "Amazon EU S.à r.l.")


def test_classify_returns_unknown_for_invalid_answer(monkeypatch):
    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**k):
                    return _resp("magazine")

    monkeypatch.setattr(ocr, "_make_client", lambda cfg: FakeClient())
    cfg = {"max_retries": 1, "classify_model": "m"}
    assert ocr.classify("text", TYPES, cfg) == ("unknown", "")


def test_parse_answer_extracts_type_and_sender():
    assert ocr.parse_answer("type: letter\nsender: Stadtwerke München", TYPES) == (
        "letter",
        "Stadtwerke München",
    )


def test_parse_answer_sender_unknown_becomes_empty():
    assert ocr.parse_answer("type: invoice\nsender: unknown", TYPES) == ("invoice", "")


def test_parse_answer_case_insensitive_keys():
    assert ocr.parse_answer("Type: receipt\nSender: Telekom", TYPES) == ("receipt", "Telekom")


def test_parse_answer_no_sender_line():
    assert ocr.parse_answer("type: receipt", TYPES) == ("receipt", "")


def test_parse_answer_garbage_returns_none_type():
    assert ocr.parse_answer("cannot read this document", TYPES) == (None, "")


def test_parse_answer_empty_returns_none_type():
    assert ocr.parse_answer("", TYPES) == (None, "")
