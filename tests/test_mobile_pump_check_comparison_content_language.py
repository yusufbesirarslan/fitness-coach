"""Current account authority across mixed historical sources and pair reuse."""
import json
from datetime import timedelta

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import PumpCheckComparison
from app.services import ai
from app.services.mobile_pump_check_comparisons import service
from tests.test_mobile_pump_check_comparison_api import (  # noqa: F401
    PATH, _analysis, _command, _make_check,
    auth_headers, dependencies, mobile_user, pair,
)


TR_TEXT = "Çerçeve aynı; ışık düzgün, İki görüntü benzer görünüyor."


@pytest.fixture
def provider_prompts(monkeypatch):
    calls = []

    def provider(a, a_media, b, b_media, prompt, **kwargs):
        calls.append(prompt)
        document = dict(_analysis(), comparability="comparable")
        if "prose in Turkish." in prompt:
            document["summary"] = TR_TEXT
        return json.dumps(document, ensure_ascii=False)

    monkeypatch.setattr(ai, "_bedrock_compare_images", provider)
    return calls


@pytest.mark.parametrize("language,name", [("en", "English"), ("tr", "Turkish"), (None, "Turkish")])
def test_mixed_historical_sources_preserved_and_current_account_controls_output(
        client, mobile_user, auth_headers, pair, dependencies, provider_prompts,
        monkeypatch, language, name):
    from app.services.mobile_pump_check_comparisons.analysis import analyze_images
    monkeypatch.setattr(service, "analyze_images", analyze_images)
    baseline, current = pair
    current.analysis = dict(current.analysis, summary="Işık düzgün, görüntü açık.")
    mobile_user.language = language
    db.session.commit()
    snapshots = [json.dumps(row.analysis, ensure_ascii=False) for row in pair]
    db.session.refresh(mobile_user)
    queries = []
    real = service.create_or_replay

    def record_sql(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    def enter(*args, **kwargs):
        event.remove(db.engine, "before_cursor_execute", record_sql)
        assert queries == []
        return real(*args, **kwargs)

    monkeypatch.setattr(service, "create_or_replay", enter)
    event.listen(db.engine, "before_cursor_execute", record_sql)
    try:
        response = client.post(PATH + "?language=de", json=_command(),
                               headers=dict(auth_headers, **{"Accept-Language": "de"}))
    finally:
        if event.contains(db.engine, "before_cursor_execute", record_sql):
            event.remove(db.engine, "before_cursor_execute", record_sql)
    assert response.status_code == 201, response.json
    assert provider_prompts[0].startswith(f"Return all natural-language analysis prose in {name}.")
    # Production compares images: historical prose is validated for eligibility,
    # never inserted into the provider prompt or translated on the way through.
    assert baseline.analysis["summary"] not in provider_prompts[0]
    assert current.analysis["summary"] not in provider_prompts[0]
    assert [json.dumps(row.analysis, ensure_ascii=False) for row in pair] == snapshots
    stored = PumpCheckComparison.query.one()
    body = response.json["pump_check_comparison"]
    assert body["analysis"] == stored.analysis
    assert body["comparability"] == "comparable"
    assert stored.analysis["summary"] == (TR_TEXT if name == "Turkish" else _analysis()["summary"])
    assert client.get(PATH + "/" + body["id"], headers=auth_headers).json == response.json


def test_language_switch_preserves_completed_pair_and_new_pair_uses_current_language(
        client, mobile_user, auth_headers, pair, dependencies, provider_prompts, monkeypatch):
    from app.services.mobile_pump_check_comparisons.analysis import analyze_images
    monkeypatch.setattr(service, "analyze_images", analyze_images)
    mobile_user.language = "en"
    db.session.commit()
    first = client.post(PATH, json=_command(), headers=auth_headers)
    assert first.status_code == 201
    historical = json.dumps(PumpCheckComparison.query.one().analysis, ensure_ascii=False)
    assert client.put("/api/v1/account/language", json={"language": "tr"},
                      headers=auth_headers).status_code == 200
    replay = client.post(PATH, json=_command(), headers=auth_headers)
    assert replay.status_code == 200 and replay.json == first.json
    # Different keys for the same immutable pair also converge by contract.
    new_headers = dict(auth_headers, **{"Idempotency-Key": "comparison-language-new-0002"})
    reused = client.post(PATH, json=_command(), headers=new_headers)
    assert reused.status_code == 200 and reused.json == first.json
    assert len(provider_prompts) == 1
    baseline, current = pair
    third = _make_check(mobile_user, "C" * 24, current.captured_at + timedelta(days=1))
    fresh = client.post(PATH, json=_command(baseline.public_id, third.public_id),
                        headers=dict(auth_headers, **{"Idempotency-Key": "comparison-language-new-0003"}))
    assert fresh.status_code == 201
    assert "prose in English." in provider_prompts[0]
    assert "prose in Turkish." in provider_prompts[1]
    assert fresh.json["pump_check_comparison"]["analysis"]["summary"] == TR_TEXT
    original = PumpCheckComparison.query.filter_by(public_id=first.json["pump_check_comparison"]["id"]).one()
    assert json.dumps(original.analysis, ensure_ascii=False) == historical


@pytest.mark.parametrize("language", ["en", "tr"])
def test_provider_timeout_keeps_typed_comparison_error(
        client, mobile_user, auth_headers, pair, dependencies, monkeypatch, language):
    from app.services.mobile_pump_check_comparisons.analysis import analyze_images
    mobile_user.language = language
    db.session.commit()
    monkeypatch.setattr(service, "analyze_images", analyze_images)

    def fail(*a, **k):
        raise TimeoutError("provider timeout")

    monkeypatch.setattr(ai, "_bedrock_compare_images", fail)
    response = client.post(PATH, json=_command(), headers=auth_headers)
    assert response.status_code == 503
    assert response.json["error"]["code"] == "PUMP_CHECK_COMPARISON_UNAVAILABLE"
    assert response.json["error"]["retryable"] is True
    assert PumpCheckComparison.query.one().status == "failed"
