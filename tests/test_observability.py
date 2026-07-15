"""Hata izleme + yapısal istek logu (app/observability.py).

Sentry DSN yoksa kurulum no-op (hermetik test ağ'a çıkmaz). İstek logu logfmt
biçiminde üretilir; /health gürültüsü atlanır.

    python -m pytest tests/test_observability.py -v
"""
import logging


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
