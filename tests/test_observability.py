"""Hata izleme + yapısal istek logu (app/observability.py).

Sentry DSN yoksa kurulum no-op (hermetik test ağ'a çıkmaz). İstek logu logfmt
biçiminde üretilir; /health gürültüsü atlanır.

    python -m pytest tests/test_observability.py -v
"""
import logging
import re

import pytest


def test_init_sentry_noop_without_dsn(app, monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    from app.observability import init_sentry
    # DSN yoksa sessizce döner (sentry_sdk import bile edilmez); hata fırlatmamalı.
    init_sentry(app)


def test_request_is_logged_logfmt(client, caplog):
    with caplog.at_level(logging.INFO):
        client.get("/login")
    line = next((r.getMessage() for r in caplog.records
                 if "method=GET path=/login" in r.getMessage()), None)
    assert line is not None
    assert "status=200" in line
    assert "dur_ms=" in line
    assert "user=" in line
    assert "id=" in line  # WS6: izleme kimliği log satırında


def test_authenticated_web_request_keeps_structured_user_diagnostic(
        client, auth_user, caplog):
    caplog.clear()
    with caplog.at_level(logging.INFO):
        response = client.get("/login")

    line = next((record.getMessage() for record in caplog.records
                 if "method=GET path=/login" in record.getMessage()), None)
    assert response.status_code == 200
    assert line is not None
    assert f"user={auth_user.id}" in line
    assert "status=200" in line
    assert "dur_ms=" in line
    assert re.search(r"\bid=[0-9a-f]{16}\b", line)


@pytest.mark.parametrize(("path", "expected_status"), [
    ("/api/v1/nutrition/logs/opaque-sensitive-diary-item", 405),
    ("/api/v1/not-a-route/private-barcode-0123456789012", 404),
])
def test_unmatched_request_logs_placeholder_not_literal_path(
        client, auth_user, caplog, path, expected_status):
    caplog.clear()
    with caplog.at_level(logging.INFO):
        response = client.get(path)

    line = next((record.getMessage() for record in caplog.records
                 if "method=GET path=<unmatched>" in record.getMessage()), None)
    assert response.status_code == expected_status
    assert line is not None
    assert path not in caplog.text
    assert "user=-" in line
    assert f"user={auth_user.id}" not in line
    assert f"status={expected_status}" in line
    assert "dur_ms=" in line
    assert re.search(r"\bid=[0-9a-f]{16}\b", line)


def test_health_requests_not_logged(client, caplog):
    with caplog.at_level(logging.INFO):
        client.get("/health")
    assert not any("path=/health" in r.getMessage() for r in caplog.records)


# ── WS6: request_id izleme kimliği ─────────────────────────────────────────

def test_request_id_assigned_per_request(app):
    from app.observability import assign_request_id, current_request_id
    with app.test_request_context("/"):
        assert current_request_id() == "-"  # atanmadan önce
        assign_request_id()
        rid = current_request_id()
        assert rid and rid != "-" and len(rid) == 16


def test_request_id_unique_across_requests(app):
    from app.observability import assign_request_id, current_request_id
    ids = set()
    for _ in range(5):
        with app.test_request_context("/"):
            assign_request_id()
            ids.add(current_request_id())
    assert len(ids) == 5  # her istek benzersiz kimlik alır


def test_request_id_in_log_line(client, caplog):
    import re
    with caplog.at_level(logging.INFO):
        client.get("/login")
    line = next((r.getMessage() for r in caplog.records
                 if "method=GET path=/login" in r.getMessage()), None)
    assert line is not None
    m = re.search(r"\bid=([0-9a-f]{16})\b", line)
    assert m is not None  # 16-haneli hex izleme kimliği
