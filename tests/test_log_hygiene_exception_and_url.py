"""Server logs carry an exception TYPE and a URL's origin -- never their paths or payloads.

Triage 2026-09-30 #7. The repo convention is `type(e).__name__` only; a handful of
paths logged the exception object itself, and an S3/provider/requests exception
string can carry a bucket, an object key, an internal URL or a credential-bearing
query string. Menu scraping also logged caller-supplied URLs in full, query string
included.
"""
import json
import logging
import re
from pathlib import Path

import pytest

from app.blueprints import food as food_bp
from app.services import ai_coach, fatsecret, menu_fetch

SECRET = "SECRET-bucket-fitx-prod/meals/42/deadbeef.jpg?X-Amz-Signature=abc123"


def _assert_clean(caplog):
    assert SECRET not in caplog.text
    assert "X-Amz-Signature" not in caplog.text
    assert "abc123" not in caplog.text


# ---------------------------------------------------------------------------
# loggable_url
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("https://menu.example.com/lunch?token=s3cr3t&utm=1#frag",
     "https://menu.example.com/<path-redacted>"),
    ("https://user:hunter2@menu.example.com:443/a/b?sig=abc",
     "https://menu.example.com/<path-redacted>"),
    ("http://menu.example.com", "http://menu.example.com/<path-redacted>"),
    ("https://drive.google.com/file/d/ID/view?usp=sharing",
     "https://drive.google.com/<path-redacted>"),
])
def test_loggable_url_keeps_origin_and_drops_path_and_credentials(raw, expected):
    assert menu_fetch.loggable_url(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "not a url", "://nohost", 12345])
def test_loggable_url_never_raises_and_never_echoes_garbage(raw):
    out = menu_fetch.loggable_url(raw)
    assert out == "<unparsable-url>"


def test_menu_blueprint_never_interpolates_a_raw_url_into_a_log_line():
    source = Path("app/blueprints/menu.py").read_text(encoding="utf-8")
    offenders = [
        line.strip() for line in source.splitlines()
        if re.search(r"logger\.[a-z]+\(", line)
        and re.search(r"\{(sub_url|url)\}", line)
    ]
    assert offenders == []


# ---------------------------------------------------------------------------
# Exception objects
# ---------------------------------------------------------------------------

def test_barcode_token_failure_logs_the_type_only(app, monkeypatch, caplog):
    def boom():
        raise RuntimeError(SECRET)
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", boom)

    with app.app_context(), caplog.at_level(logging.INFO):
        assert fatsecret._food_find_by_barcode("5449000000996") is None

    assert "token failed: RuntimeError" in caplog.text
    _assert_clean(caplog)


def test_barcode_name_lookup_soft_failure_logs_the_type_only(app, monkeypatch, caplog):
    monkeypatch.setattr(fatsecret, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(fatsecret, "_food_get_servings", lambda fid: [])
    calls = {"n": 0}

    class _Resp:
        def json(self):
            return {"food_id": {"value": "123"}}

    def fake_get(url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp()                       # food.find_id_for_barcode
        raise RuntimeError(SECRET)               # the name lookup blows up
    monkeypatch.setattr(fatsecret, "_fs_get", fake_get)

    with app.app_context(), caplog.at_level(logging.INFO):
        result = fatsecret._food_find_by_barcode("5449000000996")

    assert result is not None and result["food_id"] == "123"   # still fail-soft
    assert "name lookup soft-fail: RuntimeError" in caplog.text
    _assert_clean(caplog)


def test_analyze_photo_fetch_failure_logs_the_type_only(app, monkeypatch, caplog):
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    monkeypatch.setattr(ai_coach.s3_helper, "is_enabled", lambda: True)

    def boom(*args, **kwargs):
        raise RuntimeError(SECRET)
    monkeypatch.setattr(ai_coach.s3_helper, "get_object_bytes", boom)

    with app.app_context(), caplog.at_level(logging.INFO):
        out = json.loads(ai_coach._tool_analyze_gym_photo(1, "meals/1/x.jpg"))

    assert out["status"] == "error"
    assert "görsel alınamadı: RuntimeError" in caplog.text
    _assert_clean(caplog)
    assert SECRET not in json.dumps(out)


def test_servings_by_name_failure_logs_the_type_only(client, auth_user, monkeypatch, caplog):
    monkeypatch.setattr(food_bp, "_get_fatsecret_token", lambda: "tok")

    def boom(*args, **kwargs):
        raise RuntimeError(SECRET)
    monkeypatch.setattr(food_bp, "_fs_relevant_candidates", boom)
    monkeypatch.setattr(food_bp, "_cached_food_id", lambda name: None, raising=False)

    with caplog.at_level(logging.INFO):
        response = client.get("/api/food/servings-by-name?name=mercimek")

    assert response.status_code == 200
    assert response.get_json() == {"servings": [], "food_id": ""}
    assert "servings-by-name failed for 'mercimek': RuntimeError" in caplog.text
    _assert_clean(caplog)
