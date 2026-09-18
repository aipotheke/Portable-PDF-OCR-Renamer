"""Unit tests for the M3 web UI server (endpoints, validation, masking; no API calls)."""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

import pytest

def _wait_until(cond, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False

from app import config as cfgmod
from app import watcher as w
from app.webui import server as webui


def _base(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _req(url: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


@pytest.fixture
def ui(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cfgmod, "config_path", lambda: tmp_path / "config.json")
    cfg = dict(cfgmod.DEFAULTS)
    cfg["watch_folder"] = str(tmp_path)
    cfg["stability_seconds"] = 0.2
    app = webui.WebUI(cfg, watcher=w.FolderWatcher(cfg), port=8971)
    app.start()
    yield app
    app.stop()


def test_index_served(ui):
    with urllib.request.urlopen(_base(ui.port) + "/", timeout=5) as resp:
        assert resp.status == 200
        assert b"PDF OCR Renamer" in resp.read()


def test_status_endpoint(ui):
    status, body = _req(_base(ui.port) + "/api/status")
    assert status == 200
    assert Path(body["watch_folder"]) == ui.watcher.folder()
    assert body["paused"] is False
    assert "jobs" in body
    assert "has_api_key" in body


def test_config_get_masks_api_key(ui):
    ui.cfg["ionos_api_key"] = "secret-token-1234"
    status, body = _req(_base(ui.port) + "/api/config")
    assert status == 200
    assert body["ionos_api_key"].endswith("1234")
    assert "secret-token-1234" not in body["ionos_api_key"]
    assert "*" in body["ionos_api_key"]


def test_config_post_persists(ui, tmp_path: Path):
    status, body = _req(_base(ui.port) + "/api/config", "POST", {
        "ionos_api_key": "abcd1234efgh",
        "doc_types": ["invoice", "receipt"],
    })
    assert status == 200
    assert body["ok"] is True
    assert ui.cfg["doc_types"] == ["invoice", "receipt"]
    stored = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert stored["ionos_api_key"] == "abcd1234efgh"
    assert stored["doc_types"] == ["invoice", "receipt"]


def test_config_post_rejects_bad_watch_folder(ui):
    status, body = _req(_base(ui.port) + "/api/config", "POST", {
        "watch_folder": "/no/such/dir/anywhere",
    })
    assert status == 400
    assert "does not exist" in body["error"]


def test_config_post_rejects_empty_doc_types(ui):
    status, body = _req(_base(ui.port) + "/api/config", "POST", {"doc_types": []})
    assert status == 400
    assert "document type" in body["error"]


def test_config_post_rejects_empty_api_key(ui):
    status, body = _req(_base(ui.port) + "/api/config", "POST", {"ionos_api_key": ""})
    assert status == 400
    assert "empty" in body["error"]


def test_config_post_rejects_unknown_key(ui):
    status, body = _req(_base(ui.port) + "/api/config", "POST", {"nope": 1})
    assert status == 400
    assert "Unknown setting" in body["error"]


def test_config_post_rejects_non_number(ui):
    status, body = _req(_base(ui.port) + "/api/config", "POST", {"render_scale": "fast"})
    assert status == 400
    assert "render_scale" in body["error"]


def test_config_post_invalid_json(ui):
    req = urllib.request.Request(_base(ui.port) + "/api/config", data=b"{bad", method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    assert status == 400


def test_config_post_changes_watch_folder(ui, tmp_path: Path):
    other = tmp_path / "other"
    other.mkdir()
    status, body = _req(_base(ui.port) + "/api/config", "POST", {"watch_folder": str(other)})
    assert status == 200
    assert ui.watcher.folder() == other
    # observer restarted and still watching the new folder
    assert ui.watcher.observer is not None


def test_scan_enqueues_existing_pdfs(ui, tmp_path: Path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "b.pdf").write_bytes(b"%PDF-1.4")
    # let the watchdog-triggered first pass finish (errors out: no API key)
    _wait_until(lambda: len([j for j in ui.watcher.job_list() if j["stage"] in ("error", "done", "skipped")]) == 2)
    # second pass via the manual scan button re-enqueues both files
    status, body = _req(_base(ui.port) + "/api/scan", "POST", {})
    assert status == 200
    assert body["queued"] == 2


def test_unknown_route_404(ui):
    status, _ = _req(_base(ui.port) + "/api/nope")
    assert status == 404


# ---- pure functions -------------------------------------------------------

def test_validate_accepts_good_update(tmp_path: Path):
    update = {"watch_folder": str(tmp_path), "doc_types": ["a", " b ", "c"]}
    assert webui.validate_config_update({}, update) is None
    assert update["doc_types"] == ["a", "b", "c"]


def test_masked_config_empty_key():
    assert webui.masked_config({"ionos_api_key": ""})["ionos_api_key"] == ""


def test_status_shows_setup_banner_flag(ui):
    ui.cfg["ionos_api_key"] = ""
    status, body = _req(_base(ui.port) + "/api/status")
    assert body["has_api_key"] is False
