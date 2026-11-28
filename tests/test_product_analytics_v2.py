"""First-public privacy-first product analytics v2 (source only, default OFF).

Synthetic transport only. No test reaches a vendor; the package has no
network transport at all (architecture test below).
"""
import ast
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import (ProductAnalyticsConsent, ProductAnalyticsErasure,
                        ProductAnalyticsOutbox, User)
from app.services import mobile_auth
from app.services.product_analytics import (FLAG, catalog, consent, dispatch, emit,
                                            ingress, wire)
from app.services.product_analytics.transport import SyntheticTransport

ROOT = Path(__file__).resolve().parents[1]
NOTICE = catalog.NOTICE_VERSION


@pytest.fixture
def principal(monkeypatch):
    current = {}

    def authenticate(raw):
        user = db.session.get(User, current["id"])
        return mobile_auth.MobilePrincipal(user, SimpleNamespace(id=1), {"sub": user.cognito_sub})

    monkeypatch.setattr(mobile_auth, "authenticate_access", authenticate)

    def as_user(user):
        current["id"] = user.id
        return {"Authorization": "Bearer opaque-analytics-access"}
    return as_user


@pytest.fixture
def on(app):
    app.config[FLAG] = True
    yield app
    app.config[FLAG] = False


@pytest.fixture
def owner(make_user, principal):
    user = make_user("analytics-owner", email="owner@example.test")
    return SimpleNamespace(id=user.id, headers=lambda: principal(db.session.get(User, user.id)))


def _grant(client, owner):
    response = client.put("/api/v1/analytics/consent", headers=owner.headers(),
                          json={"decision": "grant", "notice_version": NOTICE})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _row(user_id):
    db.session.expire_all()
    return ProductAnalyticsConsent.query.filter_by(user_id=user_id).one_or_none()


def _outbox(user_id=None):
    db.session.expire_all()
    query = ProductAnalyticsOutbox.query
    if user_id is not None:
        query = query.filter_by(user_id=user_id)
    return query.order_by(ProductAnalyticsOutbox.id).all()


def _now_iso(delta=timedelta(0)):
    return (datetime.utcnow() + delta).isoformat(timespec="milliseconds") + "Z"


def _event(name="axis_v2_app_opened", properties=None, **overrides):
    item = {"event_id": str(uuid.uuid4()), "name": name, "occurred_at": _now_iso(),
            "properties": properties or {}}
    item.update(overrides)
    return item


def _post(client, owner, grant_id, events, **envelope):
    body = {"schema_version": 2, "consent_grant": grant_id, "events": events}
    body.update(envelope)
    return client.post("/api/v1/analytics/events", headers=owner.headers(),
                       data=json.dumps(body), content_type="application/json")


# ── OFF is inert ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [("get", "/api/v1/analytics/consent"),
                                         ("put", "/api/v1/analytics/consent"),
                                         ("post", "/api/v1/analytics/events")])
def test_off_routes_answer_404_before_authentication(client, method, path):
    assert getattr(client, method)(path).status_code == 404


def test_off_emitter_issues_no_sql(app, owner):
    statements = []
    listener = lambda *args: statements.append(args[2])  # noqa: E731
    event.listen(db.engine, "before_cursor_execute", listener)
    try:
        emit.emit_authoritative(owner.id, "axis_v2_workout_completed", "x")
    finally:
        event.remove(db.engine, "before_cursor_execute", listener)
    assert statements == [] and _outbox() == []


# ── Independent optional consent ──────────────────────────────────────────

def test_default_is_not_granted_and_view_never_exposes_the_pseudonym(on, client, owner):
    body = client.get("/api/v1/analytics/consent", headers=owner.headers()).get_json()
    assert body["status"] == "not_granted" and body["grant_id"] is None
    granted = _grant(client, owner)
    assert granted["status"] == "granted" and uuid.UUID(granted["grant_id"]).version == 4
    row = _row(owner.id)
    assert row.subject not in json.dumps(granted)
    assert set(granted) == {"contract_version", "status", "grant_id", "notice_version",
                            "granted_notice_version", "expires_at"}


def test_grant_requires_the_exact_current_notice_and_closed_body(on, client, owner):
    stale = client.put("/api/v1/analytics/consent", headers=owner.headers(),
                       json={"decision": "grant", "notice_version": "old"})
    assert stale.status_code == 409
    assert stale.get_json()["error"]["code"] == "ANALYTICS_NOTICE_OUTDATED"
    for body in ({"decision": "grant"}, {"decision": "grant", "notice_version": NOTICE, "x": 1},
                 {"decision": "maybe", "notice_version": NOTICE}, ["grant"]):
        assert client.put("/api/v1/analytics/consent", headers=owner.headers(),
                          json=body).status_code == 400
    assert _row(owner.id) is None


def test_consent_endpoints_are_independent_of_health_consent():
    from app import health_boundary
    assert {"mobile_api.analytics_consent_read",
            "mobile_api.analytics_consent_write"} <= health_boundary.SAFE_ENDPOINTS
    assert "mobile_api.analytics_events" not in health_boundary.SAFE_ENDPOINTS


def test_regrant_after_withdrawal_mints_a_new_unlinkable_pseudonym(on, client, owner):
    first = _grant(client, owner)
    again = _grant(client, owner)
    assert again["grant_id"] == first["grant_id"]            # renewal keeps the choice
    old = _row(owner.id)
    old_subject, old_generation = old.subject, old.generation
    withdrawn = client.put("/api/v1/analytics/consent", headers=owner.headers(),
                           json={"decision": "withdraw"}).get_json()
    assert withdrawn["status"] == "withdrawn" and withdrawn["grant_id"] is None
    row = _row(owner.id)
    assert row.subject is None and row.generation == old_generation + 1
    second = _grant(client, owner)
    row = _row(owner.id)
    assert second["grant_id"] != first["grant_id"] and row.subject != old_subject
    enabled = [r for r in _outbox(owner.id) if r.name == "axis_v2_analytics_enabled"]
    assert [r.subject for r in enabled] == [row.subject]     # old one purged at withdrawal


def test_withdraw_is_idempotent_and_queues_one_vendor_erasure(on, client, owner):
    _grant(client, owner)
    subject = _row(owner.id).subject
    for _ in range(2):
        assert client.put("/api/v1/analytics/consent", headers=owner.headers(),
                          json={"decision": "withdraw"}).status_code == 200
    jobs = ProductAnalyticsErasure.query.all()
    # Never dispatched: nothing reached the vendor, settled without any vendor call.
    assert [(j.subject, j.reason, j.status) for j in jobs] == [(subject, "withdrawn", "not_required")]
    assert jobs[0].completed_at is not None and jobs[0].last_outcome == "never_exposed"
    assert _outbox(owner.id) == []


def test_withdrawal_after_exposure_queues_a_pending_vendor_erasure(on, client, owner):
    _grant(client, owner)
    subject = _row(owner.id).subject
    dispatch.dispatch_once(SyntheticTransport(fail=True))      # unacknowledged send = exposure
    assert _row(owner.id).vendor_exposed_at is not None
    client.put("/api/v1/analytics/consent", headers=owner.headers(), json={"decision": "withdraw"})
    jobs = ProductAnalyticsErasure.query.all()
    assert [(j.subject, j.status, j.completed_at) for j in jobs] == [(subject, "pending", None)]
    _grant(client, owner)
    assert _row(owner.id).vendor_exposed_at is None            # new subject starts unexposed


# ── Client ingress: strict validation, account fence, dedup ───────────────

def test_valid_client_batch_is_accepted_and_deduplicated(on, client, owner):
    grant = _grant(client, owner)["grant_id"]
    item = _event("axis_v2_screen_viewed", {"screen": "coach"})
    first = _post(client, owner, grant, [item, _event("axis_v2_onboarding_step_completed",
                                                      {"milestone": "step2"})])
    assert first.status_code == 202 and first.get_json() == {"accepted": 2, "dropped": 0}
    retry = _post(client, owner, grant, [item])
    assert retry.get_json() == {"accepted": 0, "dropped": 1}
    names = [r.name for r in _outbox(owner.id) if r.authority == "client"]
    assert names == ["axis_v2_screen_viewed", "axis_v2_onboarding_step_completed"]


@pytest.mark.parametrize("bad", [
    _event("axis_v2_workout_completed"),                              # server authority
    _event("axis_v2_training_plan_created", {"path": "general"}),     # server authority
    _event("axis_v2_screen_viewed", {"screen": "coach/thread/42"}),   # not in enum
    _event("axis_v2_screen_viewed", {"screen": "coach", "extra": "x"}),
    _event("axis_v2_app_opened", {"email": "owner@example.test"}),
    _event("axis_v2_coach_message_sent", {"text": "my knee hurts"}),
    _event(event_id="00000000-0000-1000-8000-000000000000"),          # not v4
    _event(occurred_at="2020-01-01T00:00:00Z"),                       # outside window
    _event(occurred_at=_now_iso(timedelta(hours=1))),                 # future
    _event(occurred_at=datetime.utcnow().isoformat()),                # no zone
    {**_event(), "user_id": 1},
    _event("$identify"),
])
def test_out_of_catalog_events_are_dropped_not_stored(on, client, owner, bad):
    grant = _grant(client, owner)["grant_id"]
    response = _post(client, owner, grant, [bad])
    assert response.status_code == 202 and response.get_json() == {"accepted": 0, "dropped": 1}
    assert [r for r in _outbox(owner.id) if r.authority == "client"] == []


def test_envelope_is_exact(on, client, owner):
    grant = _grant(client, owner)["grant_id"]
    assert _post(client, owner, grant, [_event()], extra=1).status_code == 400
    assert _post(client, owner, grant, [_event()], schema_version=True).status_code == 400
    assert _post(client, owner, grant, [_event()], schema_version=1).status_code == 400
    assert _post(client, owner, grant, [_event()] * 21).status_code == 400
    assert _post(client, owner, grant, []).status_code == 400
    huge = [_event("axis_v2_app_opened", event_id=str(uuid.uuid4())) for _ in range(20)]
    padded = json.dumps({"schema_version": 2, "consent_grant": grant, "events": huge}) + " " * 17000
    assert client.post("/api/v1/analytics/events", headers=owner.headers(), data=padded,
                       content_type="application/json").status_code == 400


def test_account_fence_refuses_another_accounts_or_a_withdrawn_grant(on, client, owner, make_user,
                                                                     principal):
    grant = _grant(client, owner)["grant_id"]
    other = make_user("analytics-other")
    other_headers = principal(other)
    response = client.post("/api/v1/analytics/events", headers=other_headers,
                           data=json.dumps({"schema_version": 2, "consent_grant": grant,
                                            "events": [_event()]}),
                           content_type="application/json")
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "ANALYTICS_CONSENT_STALE"
    client.put("/api/v1/analytics/consent", headers=owner.headers(), json={"decision": "withdraw"})
    assert _post(client, owner, grant, [_event()]).status_code == 409
    assert ProductAnalyticsOutbox.query.count() == 0


def test_events_buffered_under_an_earlier_grant_are_refused_after_regrant(
        on, client, owner, make_user, principal):
    old_grant = _grant(client, owner)["grant_id"]
    client.put("/api/v1/analytics/consent", headers=owner.headers(), json={"decision": "withdraw"})
    new_grant = _grant(client, owner)["grant_id"]
    assert _post(client, owner, old_grant, [_event()]).status_code == 409
    other = make_user("analytics-other-granted")
    other_headers = principal(other)
    client.put("/api/v1/analytics/consent", headers=other_headers,
               json={"decision": "grant", "notice_version": NOTICE})
    crossed = client.post("/api/v1/analytics/events", headers=other_headers,
                          data=json.dumps({"schema_version": 2, "consent_grant": new_grant,
                                           "events": [_event()]}),
                          content_type="application/json")
    assert crossed.status_code == 409
    assert [r for r in _outbox() if r.authority == "client"] == []


def test_expired_grant_collects_nothing(on, client, owner):
    grant = _grant(client, owner)["grant_id"]
    row = _row(owner.id)
    row.expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.session.commit()
    assert _post(client, owner, grant, [_event()]).status_code == 409
    emit.emit_authoritative(owner.id, "axis_v2_workout_completed", "day:1")
    assert [r.name for r in _outbox(owner.id)] == ["axis_v2_analytics_enabled"]
    assert client.get("/api/v1/analytics/consent", headers=owner.headers()).get_json()["status"] == "expired"


# ── Server authority and canonical identity ───────────────────────────────

def test_authoritative_event_identity_is_canonical_keyed_and_replay_safe(on, client, owner):
    _grant(client, owner)
    for _ in range(3):
        emit.emit_authoritative(owner.id, "axis_v2_workout_completed", "workout_completion_day:7:2026-11-27")
    rows = [r for r in _outbox(owner.id) if r.name == "axis_v2_workout_completed"]
    assert len(rows) == 1
    identity = uuid.UUID(rows[0].event_id)
    assert identity.version == 8 and "2026" not in rows[0].event_id
    emit.emit_authoritative(owner.id, "axis_v2_workout_completed", "workout_completion_day:7:2026-11-28")
    assert len([r for r in _outbox(owner.id) if r.name == "axis_v2_workout_completed"]) == 2


def test_no_grant_means_no_authoritative_event(on, owner):
    emit.emit_authoritative(owner.id, "axis_v2_onboarding_completed", f"onboarding:{owner.id}")
    assert _outbox() == []


def test_emitter_never_raises_into_the_writer(on, client, owner, monkeypatch):
    _grant(client, owner)

    def boom(*args, **kwargs):
        raise RuntimeError("storage down")
    monkeypatch.setattr(emit, "stage", boom)
    emit.emit_authoritative(owner.id, "axis_v2_workout_completed", "x")   # no exception


def test_real_writer_general_plan_emits_once_after_commit(on, client, owner):
    from app.services.mobile_training_generation import store
    _grant(client, owner)
    plan = store.commit_general_plan(owner.id, "general-key-1", "fp-1", '{"program": []}')
    rows = [r for r in _outbox(owner.id) if r.name == "axis_v2_training_plan_created"]
    assert len(rows) == 1 and json.loads(rows[0].properties) == {"path": "general"}
    with pytest.raises(Exception):
        store.commit_general_plan(owner.id, "general-key-1", "fp-1", '{"program": []}')
    assert len([r for r in _outbox(owner.id) if r.name == "axis_v2_training_plan_created"]) == 1
    assert plan.id is not None


def test_every_server_event_has_exactly_the_reviewed_emitters():
    """Each catalog server event is emitted by the reviewed writer(s) only, and
    no emitter names an event outside the catalog."""
    expected = {
        "axis_v2_onboarding_completed": {"app/services/account_profile.py"},
        "axis_v2_training_plan_proposed": {"app/services/mobile_training_generation/store.py"},
        "axis_v2_training_plan_created": {"app/services/mobile_training_generation/store.py"},
        "axis_v2_feature_failed": {"app/services/mobile_training_generation/store.py"},
        "axis_v2_workout_completed": {"app/services/workout_completion/service.py"},
        "axis_v2_coach_mutation_applied": {"app/services/plan_mutation/service.py"},
        "axis_v2_coach_reply_completed": {"app/services/memory_manager.py"},
        "axis_v2_weekly_checkin_submitted": {"app/services/mobile_weekly_checkin/submission.py"},
        "axis_v2_analytics_enabled": {"app/services/product_analytics/consent.py"},
    }
    found = {}
    for path in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)
                    and node.args[1].value.startswith("axis_v2_")):
                found.setdefault(node.args[1].value, set()).add(str(path.relative_to(ROOT)))
    assert found == expected
    assert set(expected) == catalog.SERVER_EVENTS


def test_only_the_posthog_transport_may_reach_the_network_and_no_vendor_sdk_exists():
    banned = {"requests", "urllib", "urllib3", "httpx", "http", "socket", "posthog", "boto3", "aiohttp"}
    network = {}
    for path in (ROOT / "app/services/product_analytics").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for name in names:
                if name.split(".")[0] in banned:
                    network.setdefault(path.name, set()).add(name)
    assert network == {"posthog_transport.py": {"requests", "requests.adapters"}}


# ── Wire, fenced dispatch, retries, erasure ───────────────────────────────

def test_wire_is_the_exact_allowlist_and_carries_no_account_identity(on, client, owner):
    grant = _grant(client, owner)["grant_id"]
    _post(client, owner, grant, [_event("axis_v2_screen_viewed", {"screen": "today"})])
    emit.emit_authoritative(owner.id, "axis_v2_training_plan_created", "training_plan:1",
                            {"path": "ai_preview_accepted"})
    transport = SyntheticTransport()
    stats = dispatch.dispatch_once(transport)
    assert stats["sent"] == 3 and len(transport.sent) == 1 and _outbox() == []
    body = json.loads(transport.sent[0])
    assert set(body) == {"api_key", "batch"}
    subject = _row(owner.id).subject
    user = db.session.get(User, owner.id)
    for item in body["batch"]:
        assert set(item) == {"event", "distinct_id", "uuid", "timestamp", "properties"}
        assert item["distinct_id"] == subject
        spec = catalog.EVENTS[item["event"]]["properties"]
        assert set(item["properties"]) == set(spec) | {"$process_person_profile", "$geoip_disable"}
        assert item["properties"]["$process_person_profile"] is False
        assert item["properties"]["$geoip_disable"] is True
    raw = transport.sent[0].decode()
    for secret in (user.username, user.email, str(user.cognito_sub), f'"{owner.id}"'):
        assert secret not in raw


def test_withdrawal_purges_staged_events_before_dispatch(on, client, owner):
    grant = _grant(client, owner)["grant_id"]
    _post(client, owner, grant, [_event()])
    client.put("/api/v1/analytics/consent", headers=owner.headers(), json={"decision": "withdraw"})
    transport = SyntheticTransport()
    assert dispatch.dispatch_once(transport)["sent"] == 0 and transport.sent == []


def test_failed_send_never_rebinds_an_old_event_to_a_new_grant(on, client, owner):
    """Regression for #541 A6: old event retried after withdraw+regrant."""
    grant = _grant(client, owner)["grant_id"]
    _post(client, owner, grant, [_event("axis_v2_coach_message_sent")])
    failing = SyntheticTransport(fail=True)
    assert dispatch.dispatch_once(failing)["retry"] == 2
    stale = [(r.id, r.subject, r.generation) for r in _outbox(owner.id)]
    # A withdrawal racing an in-flight send could leave a row behind; model it.
    client.put("/api/v1/analytics/consent", headers=owner.headers(), json={"decision": "withdraw"})
    _grant(client, owner)
    for _, subject, generation in stale:
        db.session.add(ProductAnalyticsOutbox(
            user_id=owner.id, generation=generation, subject=subject,
            event_id=str(uuid.uuid4()), name="axis_v2_coach_message_sent", authority="client",
            occurred_at=datetime.utcnow(), received_at=datetime.utcnow(), properties="{}",
            attempts=1, next_attempt_at=datetime.utcnow()))
    db.session.commit()
    transport = SyntheticTransport()
    stats = dispatch.dispatch_once(transport, now=datetime.utcnow() + timedelta(minutes=5))
    sent = [item for body in transport.sent for item in json.loads(body)["batch"]]
    new_subject = _row(owner.id).subject
    assert stats["fenced"] == len(stale)
    assert all(item["distinct_id"] == new_subject for item in sent)
    assert [item["event"] for item in sent] == ["axis_v2_analytics_enabled"]


def test_retries_are_bounded(on, client, owner):
    grant = _grant(client, owner)["grant_id"]
    _post(client, owner, grant, [_event()])
    failing = SyntheticTransport(fail=True)
    now = datetime.utcnow()
    for step in range(dispatch.MAX_ATTEMPTS):
        dispatch.dispatch_once(failing, now=now + timedelta(hours=step))
    assert _outbox() == []


def test_vendor_erasure_jobs_for_withdrawal_and_account_deletion(on, client, owner, make_user,
                                                                principal):
    transport = SyntheticTransport()
    _grant(client, owner)
    first = _row(owner.id).subject
    assert dispatch.dispatch_once(transport)["sent"] == 1
    client.put("/api/v1/analytics/consent", headers=owner.headers(), json={"decision": "withdraw"})
    _grant(client, owner)
    second = _row(owner.id).subject
    assert dispatch.dispatch_once(transport)["sent"] == 1
    from app.cli import _purge_user
    _purge_user(db.session.get(User, owner.id))
    db.session.commit()
    assert ProductAnalyticsConsent.query.count() == 0 and ProductAnalyticsOutbox.query.count() == 0
    jobs = {(j.subject, j.reason) for j in ProductAnalyticsErasure.query.all()}
    assert jobs == {(first, "withdrawn"), (second, "account_deleted")}
    assert not hasattr(ProductAnalyticsErasure, "user_id")
    now = datetime.utcnow()
    assert dispatch.process_erasures_once(transport, now=now)["materialized"] == 2
    markers = [item for item in json.loads(transport.sent[-1])["batch"]]
    assert {m["distinct_id"] for m in markers} == {first, second}
    assert all(m["event"] == "$set" and m["properties"] == {
        "$set": {"axis_v2_erasure": "requested"}, "$geoip_disable": True} for m in markers)
    assert dispatch.process_erasures_once(transport, now=now)["requested"] == 0   # settling
    later = now + dispatch.ERASURE_SETTLE
    assert dispatch.process_erasures_once(transport, now=later)["requested"] == 2
    assert sorted(transport.deleted[0]) == sorted([first, second])
    statuses = {j.status for j in ProductAnalyticsErasure.query.all()}
    assert statuses == {"requested"}                       # never claimed before verification
    verify_at = later + dispatch.ERASURE_VERIFY_INTERVAL
    assert dispatch.process_erasures_once(transport, now=verify_at)["waiting"] == 2
    transport.verify_all()
    verify_at += dispatch.ERASURE_VERIFY_INTERVAL
    assert dispatch.process_erasures_once(transport, now=verify_at)["verified"] == 2
    db.session.expire_all()
    assert {j.status for j in ProductAnalyticsErasure.query.all()} == {"verified"}


def test_ingress_parser_rejects_non_json_and_oversize_without_echo():
    for raw in (b"", b"not json", b"[]", b"x" * (ingress.MAX_BODY + 1)):
        with pytest.raises(ingress.InvalidEnvelope):
            ingress.parse(raw)


def test_wire_timestamp_is_utc_milliseconds():
    row = SimpleNamespace(name="axis_v2_app_opened", subject=str(uuid.uuid4()),
                          event_id=str(uuid.uuid4()), properties="{}",
                          occurred_at=datetime(2026, 11, 27, 18, 44, 26, 222776))
    assert wire.wire_event(row)["timestamp"] == "2026-11-27T18:44:26.222Z"
