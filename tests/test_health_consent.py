"""HTTP/persistence qualification. SQLite fixture is not concurrency proof."""
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from app.extensions import db
from app.health_consent_models import HealthConsentEvent, HealthConsentState
from app.services import ai_consent, health_consent as consent, health_disclosure as disclosure
from test_mobile_account_profile_api import principal, BODY

PATH = "/api/v1/health-consent"
PROFILE = "/api/v1/account/profile"


def command(generation=0, action="affirmative_grant", request_id="health-grant-0001", locale="en"):
    return dict(purpose=disclosure.PURPOSE, action=action,
        version=disclosure.VERSION, locale=locale, text_digest=disclosure.DIGESTS[locale],
        expected_generation=generation, request_id=request_id)


def test_candidate_is_not_published_or_a_d1_policy():
    assert disclosure.APPROVED_VERSION is None
    assert disclosure.published() is None
    assert len(disclosure.TEXTS) == 2
    assert disclosure.PURPOSE not in {purpose for purpose, _ in ai_consent.BINDINGS.values()}
    assert dict(ai_consent.POLICIES) == {}


@pytest.mark.parametrize("locale", ["en", "tr"])
def test_affirmative_grant_persists_exact_evidence_and_allows_profile(
        app, client, make_user, principal, locale):
    owner = make_user("health-grant", health_consent=False)
    headers = principal(owner)
    before = client.get(PATH, headers=headers)
    assert before.json["state"] == "not_granted"
    assert before.json["disclosure"]["texts"][locale] == disclosure.TEXTS[locale]
    response = client.post(PATH, headers=headers, json=command(locale=locale))
    assert response.status_code == 200 and response.json["state"] == "granted"
    assert response.headers["Cache-Control"] == "no-store"
    db.session.remove()
    state = db.session.get(HealthConsentState, owner.id)
    assert state.record["purpose"] == disclosure.PURPOSE
    assert state.record["version"] == disclosure.VERSION
    assert state.record["text_digest"] == disclosure.DIGESTS[locale]
    assert state.record["locale"] == locale and state.record["granted_at"]
    event = HealthConsentEvent.query.one()
    assert event.owner_id == owner.id and event.action == "affirmative_grant"
    assert event.occurred_at and event.generation == 1
    assert client.get(PATH, headers=headers).json["state"] == "granted"
    assert client.put(PROFILE, headers=headers, json=BODY).status_code == 200


def test_collection_and_read_are_refused_before_body_or_history(
        client, make_user, principal, monkeypatch):
    owner = make_user("no-health-body", health_consent=False)
    headers = principal(owner)
    from app.blueprints import mobile_account_profile
    body = Mock(side_effect=AssertionError("must not consume health body"))
    history = Mock(side_effect=AssertionError("must not read health history"))
    monkeypatch.setattr(mobile_account_profile, "_json_object", body)
    monkeypatch.setattr(mobile_account_profile.account_profile, "current_profile", history)
    for response in [client.put(PROFILE, headers=headers, json=BODY),
                     client.get(PROFILE, headers=headers)]:
        assert response.status_code == 403
        assert response.json["error"]["code"] == "HEALTH_CONSENT_REQUIRED"
    body.assert_not_called()
    history.assert_not_called()


@pytest.mark.parametrize("method,path", [
    ("get", "/api/v1/nutrition/diary/today"),
    ("post", "/api/v1/nutrition/logs"),
    ("get", "/api/v1/today"),
    ("post", "/api/v1/coach/messages"),
    ("get", "/api/v1/training/plans/current"),
])
def test_api_aliases_cannot_bypass_health_permission(client, make_user, principal, method, path):
    owner = make_user("bypass-health", health_consent=False)
    response = getattr(client, method)(path, headers=principal(owner),
                                      json={"health_consent": True, "ai_consent": True})
    assert response.status_code == 403
    assert response.json["error"]["code"] == "HEALTH_CONSENT_REQUIRED"


def test_refusal_is_persisted_and_existing_account_is_not_grandfathered(
        client, make_user, principal):
    owner = make_user("existing-health", health_consent=False,
                      profile_complete=True, weight=81.5)
    headers = principal(owner)
    assert client.get(PROFILE, headers=headers).status_code == 403
    response = client.post(PATH, headers=headers, json=command(action="decline"))
    assert response.status_code == 200 and response.json["state"] == "declined"
    assert HealthConsentEvent.query.one().action == "decline"
    assert db.session.get(type(owner), owner.id).weight == 81.5
    neutral = client.get("/api/v1/account/me", headers=headers).json["user"]
    assert neutral["username"] == "existing-health"
    assert neutral["goal"] is None and neutral["profile_complete"] is False
    assert client.put(PROFILE, headers=headers, json=BODY).status_code == 403
    # Prospective decision does not rewrite historical records or timestamps.
    assert client.post(PATH, headers=headers, json=command(1, request_id="prospective-0002")).status_code == 200
    assert db.session.get(type(owner), owner.id).weight == 81.5


def test_withdrawal_blocks_reads_writes_and_old_grant_replay_without_deletion(
        client, make_user, principal):
    owner = make_user("withdraw-health", health_consent=False, weight=82.25)
    headers = principal(owner)
    original = command()
    assert client.post(PATH, headers=headers, json=original).status_code == 200
    withdrawn = client.post(PATH, headers=headers,
        json=command(1, "withdraw", "health-withdraw-0002"))
    assert withdrawn.json["state"] == "withdrawn" and withdrawn.json["withdrawn_at"]
    replay = client.post(PATH, headers=headers, json=original)
    assert replay.json["state"] == "withdrawn" and replay.json["generation"] == 2
    assert client.get(PROFILE, headers=headers).status_code == 403
    assert client.put(PROFILE, headers=headers, json=BODY).status_code == 403
    assert client.get("/api/v1/account/me", headers=headers).status_code == 200
    assert db.session.get(type(owner), owner.id).weight == 82.25
    assert HealthConsentEvent.query.count() == 2


def test_withdrawal_survives_retired_text_and_authority_errors_fail_closed(
        app, client, make_user, principal, monkeypatch):
    owner = make_user("retired-health")
    headers = principal(owner)
    monkeypatch.setattr(disclosure, "APPROVED_VERSION", None)
    assert client.get(PROFILE, headers=headers).status_code == 403
    assert client.post(PATH, headers=headers,
        json=command(1, "withdraw", "retired-withdraw")).json["state"] == "withdrawn"
    refused = client.post(PATH, headers=headers, json=command(2, request_id="retired-grant"))
    assert refused.json["error"]["code"] == "HEALTH_CONSENT_DISCLOSURE_UNAVAILABLE"


@pytest.mark.parametrize("change", [
    {"owner_id": 2}, {"health_consent": True}, {"purpose": "training.plan"},
    {"text_digest": "a" * 64}, {"version": "old"}, {"expected_generation": True},
    {"action": "accept_terms"}, {"locale": "xx"},
])
def test_spoofed_or_unreviewed_grants_are_refused(client, make_user, principal, change):
    owner = make_user("bad-health", health_consent=False)
    response = client.post(PATH, headers=principal(owner), json={**command(), **change})
    assert response.status_code in {400, 403}
    assert HealthConsentState.query.count() == 0


def test_owner_is_bearer_and_other_account_is_not_authorised(client, make_user, principal):
    a = make_user("health-owner-a", health_consent=False)
    b = make_user("health-owner-b", health_consent=False)
    client.post(PATH, headers=principal(a), json=command())
    assert client.get(PATH + "?owner_id=" + str(a.id), headers=principal(b)).json["state"] == "not_granted"
    assert client.put(PROFILE, headers=principal(b), json=BODY).status_code == 403


def test_health_grant_does_not_publish_or_grant_any_d1_purpose(client, make_user, principal):
    from app.models import AIConsentEvent, AIConsentState
    owner = make_user("health-not-ai", health_consent=False)
    headers = principal(owner)
    client.post(PATH, headers=headers, json=command())
    assert AIConsentState.query.count() == AIConsentEvent.query.count() == 0
    assert dict(ai_consent.POLICIES) == {}
    assert {purpose for purpose, _ in ai_consent.BINDINGS.values()} == {
        "coach.conversation", "training.plan", "nutrition.plan", "pump_check.analysis", "qr_menu.analysis"}
    response = client.post("/api/v1/coach/messages", headers=headers, json={"message": "synthetic"})
    assert response.json["error"]["code"] == "AI_CONSENT_DISCLOSURE_UNAVAILABLE"


def test_browser_collection_uses_same_boundary_and_csrf(
        client, raw_client, make_user, login):
    make_user("web-health", health_consent=False)
    login("web-health")
    assert client.get("/setup").json["error"]["code"] == "HEALTH_CONSENT_REQUIRED"
    assert raw_client.post("/api/health-consent", json=command()).status_code == 403
    assert client.post("/api/health-consent", json=command()).status_code == 200
    assert client.get("/setup").status_code == 200


def test_deletion_explicitly_purges_minimal_evidence(app, make_user):
    from app.cli import _purge_user
    owner = make_user("purge-health")
    _purge_user(owner)
    db.session.commit()
    assert HealthConsentState.query.count() == HealthConsentEvent.query.count() == 0


def _migration():
    path = Path(__file__).parents[1] / "migrations/versions/f01c0a1e0001_first_party_health_consent.py"
    spec = importlib.util.spec_from_file_location("health_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_empty_expand_migration_never_infers_consent_or_changes_old_data(tmp_path):
    engine = sa.create_engine("sqlite:///" + str(tmp_path / "migration.db"))
    migration = _migration()
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE user (id INTEGER PRIMARY KEY, weight FLOAT)'))
        conn.execute(sa.text('INSERT INTO user VALUES (1, 81.5)'))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            migration.upgrade()
        assert conn.execute(sa.text('SELECT count(*) FROM health_consent_state')).scalar() == 0
        assert conn.execute(sa.text('SELECT weight FROM user')).scalar() == 81.5
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()


def test_migration_refuses_partial_authority_schema(tmp_path):
    engine = sa.create_engine("sqlite:///" + str(tmp_path / "partial.db"))
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE user (id INTEGER PRIMARY KEY)'))
        conn.execute(sa.text('CREATE TABLE health_consent_state (owner_id INTEGER PRIMARY KEY)'))
        with Operations.context(MigrationContext.configure(conn)), pytest.raises(RuntimeError):
            _migration().upgrade()


@pytest.mark.parametrize("consume", ["all", "close", "error"])
def test_wsgi_scope_is_released_exactly_once(consume):
    from app.health_boundary import HealthProcessingBoundary, MARKER
    class Scope:
        exits = 0
        def __exit__(self, *args):
            self.exits += 1
    scope = Scope()
    def app(environ, start_response):
        environ[MARKER] = scope
        def values():
            yield b"synthetic"
            if consume == "error":
                raise RuntimeError("synthetic transport failure")
        return values()
    result = HealthProcessingBoundary(app)({}, lambda *args: None)
    if consume == "all":
        assert list(result) == [b"synthetic"]
    elif consume == "error":
        with pytest.raises(RuntimeError):
            list(result)
    result.close()
    result.close()
    assert scope.exits == 1
