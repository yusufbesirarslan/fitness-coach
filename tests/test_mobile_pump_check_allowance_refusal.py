"""P2-1: an allowance-refused Pump Check leaves no permanent "pending" row.

Before the fix a 402 committed the claimed `pending` row (no image, never
analysed) and history listed it as "Queued" forever. Fake consent and storage;
the allowance ledger is the real one.
"""
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from ai_allowance_support import enable_subscriptions, premium_row
from app.extensions import db
from app.models import PumpCheck, User
from app.services import ai_consent, mobile_auth
from app.services.ai_allowances import execution as E
from app.services.ai_allowances.policy import (
    AllowancePolicy, ScopePolicy, SUPPORTED_FEATURES)
from app.services.mobile_pump_checks import service


PATH = "/api/v1/pump-checks"
ANALYSIS = {
    "summary": "Visible definition can be assessed in this image.",
    "observations": ["Shoulder outline is visible."],
    "strengths": ["Consistent framing."],
    "focus_areas": ["Keep lighting even."],
    "limitations": ["One image cannot establish progress."],
    "next_check_guidance": "Repeat this framing next time.",
    "quality": "sufficient",
}


def _command():
    stream = BytesIO()
    Image.new("RGB", (12, 12), "blue").save(stream, format="JPEG")
    stream.seek(0)
    return {
        "image": (stream, "pump.jpg", "image/jpeg"),
        "body_region": "upper_body",
        "environment": "gym",
        "description": "progress",
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@pytest.fixture
def owner(app, make_user, monkeypatch):
    user = make_user("pump-allowance")
    app.config.update(
        AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED=True,
        AI_ALLOWANCE_LEDGER_ENABLED=True,
        AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED=True,
        AI_ALLOWANCE_POLICY=AllowancePolicy(
            "test-pump-once",
            starter=ScopePolicy("NEVER", {f: 1 for f in SUPPORTED_FEATURES}, 1),
            starter_eligible_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
            premium=ScopePolicy("WEEKLY", {f: 1 for f in SUPPORTED_FEATURES}, 1)))
    enable_subscriptions(app, monkeypatch)
    premium_row(user.id)
    monkeypatch.setattr(ai_consent, "begin_operation", lambda uid, f, *route:
                        ai_consent.OperationAuthority(uid, f, 1, "synthetic"))
    monkeypatch.setattr(ai_consent, "authorize_operation", lambda authority: authority)
    monkeypatch.setattr(ai_consent, "fence", lambda *args: None)
    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda raw: mobile_auth.MobilePrincipal(
            user, SimpleNamespace(id=1), {"sub": user.cognito_sub}))
    return user.id


@pytest.fixture
def calls(monkeypatch, owner):
    seen = {"upload": 0, "analysis": 0}

    def upload(*args, **kwargs):
        seen["upload"] += 1
        return f"pump-checks/{owner}/2026/11/private-{seen['upload']}.jpg"

    def analyze(*args, **kwargs):
        seen["analysis"] += 1
        return ANALYSIS

    monkeypatch.setattr(service.s3_helper, "is_enabled", lambda: True)
    monkeypatch.setattr(service.s3_helper, "upload_image", upload)
    monkeypatch.setattr(service.s3_helper, "generate_presigned_url",
                        lambda *a, **k: "https://media.example.test/temporary")
    monkeypatch.setattr(service, "analyze_image", analyze)
    return seen


def _fresh_request_globals():
    # The test `app` fixture keeps ONE app context pushed, so Flask reuses its
    # `g` across test-client requests; production gives every request its own.
    from flask import g
    for name in ("allowance_refusal", "allowance_actions"):
        g.pop(name, None)


def _authenticate(owner):
    from flask import g
    g.mobile_user = db.session.get(User, owner)


def _post(client, key):
    _fresh_request_globals()
    return client.post(PATH, data=_command(), headers={
        "Authorization": "Bearer opaque-access-credential", "Idempotency-Key": key})


def _rows(owner):
    db.session.rollback()
    return sorted((r.analysis_status, r.image_key is not None)
                  for r in PumpCheck.query.filter_by(user_id=owner))


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_allowance_refusal_leaves_no_pending_row_or_history_entry(
        client, owner, calls):
    first = _post(client, "pump-allowance-op-0001")
    assert first.status_code == 201
    assert first.get_json()["pump_check"]["analysis_status"] == "completed"

    refused = _post(client, "pump-allowance-op-0002")
    assert refused.status_code == 402
    assert refused.get_json()["error"]["code"] == "AI_ALLOWANCE_EXHAUSTED"
    assert calls == {"upload": 1, "analysis": 1}
    assert _rows(owner) == [("completed", True)]

    again = _post(client, "pump-allowance-op-0002")  # same key retried
    assert again.status_code == 402
    assert _rows(owner) == [("completed", True)]

    _fresh_request_globals()
    history = client.get(PATH, headers={"Authorization": "Bearer opaque-access-credential"})
    assert history.status_code == 200
    statuses = [item["analysis_status"] for item in history.get_json()["pump_checks"]]
    assert statuses == ["completed"]


def test_consent_refusal_after_claim_also_leaves_no_row(app, owner, calls, monkeypatch):
    # Real consent refusals happen before the route (admit_endpoint); this
    # pins the service: a refusal raised by prepare() after the claim.
    denied = []

    def deny(authority):
        denied.append(authority)
        raise ai_consent.ConsentDenied()
    monkeypatch.setattr(ai_consent, "authorize_operation", deny)
    command = service.create_command(
        b"img", "image/jpeg", "arms", "gym", "",
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    with app.test_request_context():
        _authenticate(owner)
        with pytest.raises(ai_consent.ConsentDenied):
            service.create_or_replay(owner, "pump-allowance-op-0003", command)
    assert len(denied) == 1  # the refusal came from prepare(), after the claim
    assert calls == {"upload": 0, "analysis": 0}
    assert _rows(owner) == []


def test_discard_never_touches_a_claimed_uploaded_or_finished_row(app, owner):
    def row(**fields):
        check = PumpCheck(
            user_id=owner, captured_at=datetime(2026, 11, 1), body_region="arms",
            location_type="gym", description="", visibility="private", valid=True,
            fallback=False, idempotency_key=f"discard-{len(fields)}-{fields}",
            idempotency_fingerprint="f" * 64,
            public_id=service.new_pump_check_id(app.config["SECRET_KEY"], owner),
            date_key=None, **fields)
        db.session.add(check)
        db.session.commit()
        return check.id

    kept = [
        row(analysis_status="analyzing", analysis_attempt=1),
        row(analysis_status="pending", analysis_attempt=1),
        row(analysis_status="pending", image_key="pump-checks/x.jpg"),
        row(analysis_status="completed", analysis_attempt=1),
        row(analysis_status="failed", analysis_attempt=1),
    ]
    unclaimed = row(analysis_status="pending")
    for row_id in kept + [unclaimed]:
        service._discard_unclaimed(row_id, owner)
    service._discard_unclaimed(kept[0], owner + 1)  # foreign owner: no-op
    db.session.rollback()
    remaining = {r.id for r in PumpCheck.query.filter_by(user_id=owner)}
    assert remaining == set(kept)


def test_discard_failure_never_masks_the_refusal(app, owner, monkeypatch):
    class Boom:
        def filter(self, *a):
            raise RuntimeError("database unavailable")
    monkeypatch.setattr(PumpCheck, "query", Boom())
    service._discard_unclaimed(1, owner)  # logs and returns


def test_replay_that_loses_its_row_is_retryable_not_a_crash(app, owner, monkeypatch):
    monkeypatch.setattr(service, "_claim_analysis", lambda row_id, user_id: None)
    with pytest.raises(service.PumpCheckPersistenceUnavailable):
        service._run_pump_analysis(987654, owner, None, False, "en")


def test_refusal_is_reraised_unchanged(app, owner, monkeypatch):
    discarded = []
    monkeypatch.setattr(service, "_discard_unclaimed",
                        lambda row_id, user_id: discarded.append(row_id))
    refusal = E.ExecutionRefused("AI_ALLOWANCE_EXHAUSTED")

    def refuse(*a, **k):
        raise refusal
    monkeypatch.setattr(service, "_run_pump_analysis", refuse)
    command = service.create_command(
        b"img", "image/jpeg", "arms", "gym", "",
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    with app.test_request_context():
        _authenticate(owner)
        with pytest.raises(E.ExecutionRefused) as raised:
            service.create_or_replay(owner, "pump-allowance-op-0004", command)
        assert raised.value is refusal
        assert len(discarded) == 1
        with pytest.raises(E.ExecutionRefused):  # replay of a kept row: no discard
            monkeypatch.setattr(service, "_claim_row",
                                lambda u, k, c: (SimpleNamespace(id=5, analysis_status="pending"), False))
            service.create_or_replay(owner, "pump-allowance-op-0004", command)
        assert len(discarded) == 1
