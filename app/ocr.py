"""IONOS AI Model Hub OCR + document-type classification.

OCR uses the OpenAI-compatible endpoint with `lightonai/LightOnOCR-2-1B`: one request
per page, base64 PNG image, Markdown output. Classification uses a text model
(default Mistral Small 24B Instruct) with a strict prompt to pick exactly one of the
configured document types.
"""

from __future__ import annotations

import base64
import io
import logging
import time
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from openai import OpenAI

from .config import get_api_key


log = logging.getLogger("ocr")


def _make_client(cfg: dict[str, Any]) -> OpenAI:
    api_key = get_api_key(cfg)
    if not api_key:
        raise RuntimeError(
            "No IONOS API key: set ionos_api_key in config.json or the IONOS_API_TOKEN env var."
        )
    return OpenAI(
        api_key=api_key,
        base_url=cfg.get("ionos_base_url"),
        timeout=cfg.get("request_timeout", 120),
    )


def _page_to_data_uri(pdf_path: Path, page_index: int, scale: float) -> str:
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        pil = doc[page_index].render(scale=scale).to_pil()
    finally:
        doc.close()
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _retry_call(fn, max_retries: int):
    """Call fn() with exponential backoff on rate-limit (429) and 5xx errors."""
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            retriable = _is_retriable(exc)
            if not retriable or attempt == max_retries:
                raise
            wait = min(2 ** attempt, 30)
            log.warning("OCR request failed (attempt %d/%d): %s — retrying in %ds", attempt, max_retries, exc, wait)
            time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def _is_retriable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status is not None:
        return status == 429 or 500 <= int(status) < 600
    return False


def ocr_page(client: OpenAI, model: str, data_uri: str, cfg: dict[str, Any]) -> str:
    """OCR a single page (data URI) and return Markdown text."""
    max_tokens = int(cfg.get("ocr_max_tokens", 4096))
    temperature = float(cfg.get("ocr_temperature", 0.2))
    max_retries = int(cfg.get("max_retries", 4))

    def call() -> str:
        resp = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": data_uri}}],
                }
            ],
        )
        return resp.choices[0].message.content or ""

    return _retry_call(call, max_retries)


def ocr_pdf(pdf_path: Path, cfg: dict[str, Any]) -> str:
    """Render and OCR every page of a PDF; join with page markers. One API call per page."""
    client = _make_client(cfg)
    model = cfg.get("ocr_model", "lightonai/LightOnOCR-2-1B")
    scale = float(cfg.get("render_scale", 2.0))

    doc = pdfium.PdfDocument(str(pdf_path))
    n_pages = len(doc)
    doc.close()

    pages: list[str] = []
    for i in range(n_pages):
        data_uri = _page_to_data_uri(pdf_path, i, scale)
        md = ocr_page(client, model, data_uri, cfg)
        pages.append(f"<!-- page {i + 1} -->\n\n{md}")
    return "\n\n".join(pages)


def classify(markdown: str, doc_types: list[str], cfg: dict[str, Any]) -> str:
    """Classify the document into exactly one of doc_types via an LLM. Fallback 'unknown'."""
    types = [t for t in doc_types if t and t.strip()]
    if not types:
        return "unknown"

    client = _make_client(cfg)
    model = cfg.get("classify_model", "mistralai/Mistral-Small-24B-Instruct-2501")
    max_retries = int(cfg.get("max_retries", 4))
    allowed = ", ".join(types)
    prompt = (
        "You are a document classifier. Read the OCR text and reply with exactly ONE "
        "document type from this list (lowercase, no punctuation, no explanation):\n"
        f"{allowed}\n\nOCR text:\n{markdown[:8000]}"
    )

    def call() -> str:
        resp = client.chat.completions.create(
            model=model,
            temperature=0.0,
            max_tokens=32,
            messages=[
                {"role": "system", "content": "Reply with a single word: one of the allowed types."},
                {"role": "user", "content": prompt},
            ],
        )
        return (resp.choices[0].message.content or "").strip()

    try:
        answer = _retry_call(call, max_retries)
    except Exception as exc:
        log.warning("Classification call failed: %s — falling back to 'unknown'", exc)
        return "unknown"

    matched = match_type(answer, types)
    if matched is None:
        log.warning("Classification answer '%s' not in allowed types — fallback 'unknown'", answer)
        return "unknown"
    return matched


def match_type(answer: str, doc_types: list[str]) -> str | None:
    """Normalize an LLM answer and return the matching configured type, or None."""
    normalized = (answer or "").lower().strip().strip(".,;:!?\"'()[]")
    for t in doc_types:
        if t.lower() == normalized:
            return t
    return None
