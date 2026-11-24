"""PD2 onboarding training start: method choice -> server preview -> Accept.

Synthetic providers only (no Bedrock). Covers the backend rows of the PD2
acceptance matrix: AI-1/AI-2 (preview, no regeneration), GEN-1 (zero AI),
MAN-1 (same deterministic plan, canonical editor afterwards), SKIP-1,
PREVIEW-1/2 (server-owned, invisible to active reads), ACCEPT-1/2 (exactly
once, idempotent, no double charge), CANCEL-1, STALE-1, CONSENT-1,
SECURITY-1 (owner isolation), G4-1 (opt-in sessions) and the flag-OFF
contract. PostgreSQL races live in tests/test_pd2_training_start_pg.py.
"""
import hashlib
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from ai_allowance_support import enable_ledger
from app.extensions import db
from app.models import (
    AiAllowanceBucket, TrainingPlan, TrainingPlanGenerationOperation,
    TrainingStartDecision, UserSession,
)
from app.services import nutrition_day_view
from app.services.mobile_training_generation import service as generation_service
from app.services.mobile_training_generation import store
from app.services.today_facts import get_active_plan
from tests.test_lp21_general_training_plan import (  # noqa: F401 - fixtures
    BODY as GENERAL_BODY, no_ai,
)
from tests.test_mobile_training_generation_api import (  # noqa: F401 - fixtures
    CANONICAL, _document, as_mobile, mobile_user,
)

FLAG = "FITX_TRAINING_START_PREVIEW_ENABLED"
AI_PATH = "/api/v1/training/plans"
AI_PREVIEW = "/api/v1/training/plans?preview=1"
ACCEPT = "/api/v1/training/plans/preview/accept"
DISCARD = "/api/v1/training/plans/preview/discard"
GENERAL = "/api/v1/training/plans/general"
GENERAL_PREVIEW = "/api/v1/training/plans/general/preview"
START = "/api/v1/training/start"
CURRENT = "/api/v1/training/plans/current"
EDITOR = "/api/v1/training/editor"


@pytest.fixture
def enabled(app):
    app.config[FLAG] = True
    return app


@pytest.fixture
def provider(monkeypatch):
    state = SimpleNamespace(calls=0, documents=[])

    def candidate(user, last_session, preferences, chat_fn, **kwargs):
        state.calls += 1
        from physical_budget_support import before_attempt
        before_attempt(user.id, "training_plan")
        document = _document(exercise_count=state.calls)  # each call differs
        state.documents.append(document)
        return SimpleNamespace(document=document, overall_score=8.5)

    monkeypatch.setattr(generation_service, "generate_training_plan_candidate", candidate)
    monkeypatch.setattr("app.blueprints.mobile_training._heavy_chat", lambda **kwargs: "unused")
    return state


def _headers(as_mobile, user, key=None, if_match=None):
    headers = as_mobile(user, key)
    if if_match is not None:
        headers["If-Match"] = f'"{if_match}"'
    return headers


def _preview(client, as_mobile, user, key, path=AI_PREVIEW):
    return client.post(path, json=CANONICAL, headers=_headers(as_mobile, user, key))


def _accept(client, as_mobile, user, key, fingerprint, query=""):
    return client.post(ACCEPT + query, headers=_headers(as_mobile, user, key, fingerprint))


def _operations(user_id):
    db.session.expire_all()
    return [(o.status, o.error_code) for o in TrainingPlanGenerationOperation.query
            .filter_by(user_id=user_id).order_by(TrainingPlanGenerationOperation.id)]


def _plans(user_id):
    db.session.expire_all()
    return TrainingPlan.query.filter_by(user_id=user_id).all()


def _set_target(user_id, kcal):
    session = (UserSession.query.filter_by(user_id=user_id)
               .order_by(UserSession.created_at.desc(), UserSession.id.desc()).first())
    session.target_calories = kcal
    db.session.commit()


# ── Flag OFF: absent surface, legacy byte-identical ────────────────────────
@pytest.mark.usefixtures("synthetic_ai_layer")
def test_flag_off_new_routes_are_absent_before_auth_and_preview_never_commits(
        client, mobile_user, as_mobile, provider):
    for method, path in (("get", START), ("put", START), ("post", ACCEPT),
                         ("post", DISCARD), ("post", GENERAL_PREVIEW)):
        assert getattr(client, method)(path).status_code == 404, path
    response = _preview(client, as_mobile, mobile_user, "pd2-off-preview-1")
    # Refused, never silently ignored: ignoring it would COMMIT a plan.
    assert response.status_code == 404
    assert provider.calls == 0 and _operations(mobile_user.id) == [] and not _plans(mobile_user.id)
    general = client.post(GENERAL, json=GENERAL_BODY,
                          headers=_headers(as_mobile, mobile_user, "pd2-off-general", "a" * 64))
    assert general.status_code == 428
    assert general.json["error"]["code"] == "TRAINING_PLAN_PREVIEW_PRECONDITION_REQUIRED"
    assert not _plans(mobile_user.id)


# ── AI-1 / PREVIEW-1 / PREVIEW-2 ───────────────────────────────────────────
@pytest.mark.usefixtures("synthetic_ai_layer", "enabled")
def test_ai_preview_is_a_server_owned_pending_draft_invisible_to_active_reads(
        client, mobile_user, as_mobile, provider):
    _set_target(mobile_user.id, 2150)
    response = _preview(client, as_mobile, mobile_user, "pd2-ai-preview-1")

    assert response.status_code == 200
    assert response.headers["Idempotency-Replayed"] == "false"
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json
    assert set(body) == {"contract_version", "preview", "daily_calorie_target"}
    preview = body["preview"]
    assert preview["method"] == "ai"
    assert len(preview["candidate_fingerprint"]) == 64
    assert preview["expires_at"].endswith("Z")
    days = preview["plan"]["days"]
    assert [day["slot"] for day in days] == list(range(7))
    assert all(day["workout_ref"] is None for day in days)  # not startable
    assert "cardio_session" not in days[0]  # G4 is opt-in only
    assert days[0]["exercises"][0]["exercise_id"] == "ex_barbell_back_squat"
    assert "haftalik_ozet" not in json.dumps(body)  # no AI-written summary
    # The one canonical calorie read Nutrition shows; PD2 computes nothing.
    expected = nutrition_day_view.read_daily_calorie_target(mobile_user.id)
    assert body["daily_calorie_target"] == {"state": "available", "kcal": round(expected.value)}

    # The draft is the ledger's GENERATED row, never a plan row.
    assert _operations(mobile_user.id) == [("GENERATED", None)]
    assert not _plans(mobile_user.id) and get_active_plan(mobile_user.id) is None
    assert client.get(CURRENT, headers=as_mobile(mobile_user)).json == {"plan": None}
    start = client.get(START, headers=as_mobile(mobile_user)).json
    assert start["has_plan"] is False and start["decision"] is None
    # The fingerprint names the exact staged text.
    operation = TrainingPlanGenerationOperation.query.filter_by(user_id=mobile_user.id).one()
    _authority, staged = __import__("app.services.ai_consent", fromlist=["x"]).unbind_candidate(
        mobile_user.id, operation.candidate_plan_data)
    assert hashlib.sha256(staged.encode()).hexdigest() == preview["candidate_fingerprint"]


# ── AI-2: no regeneration on rebuild / restart ─────────────────────────────
@pytest.mark.usefixtures("synthetic_ai_layer", "enabled")
def test_same_key_preview_replays_the_same_candidate_without_a_provider_call(
        client, mobile_user, as_mobile, provider):
    first = _preview(client, as_mobile, mobile_user, "pd2-ai-replay-1")
    for _ in range(3):
        again = _preview(client, as_mobile, mobile_user, "pd2-ai-replay-1")
        assert again.status_code == 200
        assert again.headers["Idempotency-Replayed"] == "true"
        assert again.json == first.json
    assert provider.calls == 1
    # A legacy generate-and-persist under the preview key can never commit it.
    legacy = client.post(AI_PATH, json=CANONICAL,
                         headers=as_mobile(mobile_user, "pd2-ai-replay-1"))
    assert legacy.status_code == 409
    assert legacy.json["error"]["code"] == "TRAINING_PLAN_IDEMPOTENCY_CONFLICT"
    assert not _plans(mobile_user.id) and provider.calls == 1


# ── ACCEPT-1 / ACCEPT-2 ────────────────────────────────────────────────────
@pytest.mark.usefixtures("synthetic_ai_layer", "enabled")
def test_accept_activates_exactly_the_previewed_candidate_once(
        client, mobile_user, as_mobile, provider):
    preview = _preview(client, as_mobile, mobile_user, "pd2-ai-accept-1").json["preview"]
    fingerprint = preview["candidate_fingerprint"]

    accepted = _accept(client, as_mobile, mobile_user, "pd2-ai-accept-1", fingerprint)

    assert accepted.status_code == 201
    assert accepted.headers["Idempotency-Replayed"] == "false"
    assert accepted.json["origin"] == {"kind": "ai_generated", "template": None,
                                       "personalized": True}
    plans = _plans(mobile_user.id)
    assert len(plans) == 1
    assert hashlib.sha256(plans[0].plan_data.encode()).hexdigest() == fingerprint
    current = client.get(CURRENT, headers=as_mobile(mobile_user)).json
    assert accepted.json["plan"] == current["plan"]
    # The accepted plan is the reviewed plan, day by day.
    reviewed = [{k: v for k, v in day.items() if k != "workout_ref"} for day in preview["plan"]["days"]]
    active = [{k: v for k, v in day.items() if k != "workout_ref"} for day in current["plan"]["days"]]
    assert reviewed == active
    assert _operations(mobile_user.id) == [("SUCCEEDED", None)]

    for _ in range(2):
        again = _accept(client, as_mobile, mobile_user, "pd2-ai-accept-1", fingerprint)
        assert again.status_code == 201
        assert again.headers["Idempotency-Replayed"] == "true"
        assert again.json == accepted.json
    assert len(_plans(mobile_user.id)) == 1 and provider.calls == 1
    # Accepted previews are no longer discardable, and the plan survives.
    discard = client.post(DISCARD, headers=as_mobile(mobile_user, "pd2-ai-accept-1"))
    assert discard.status_code == 409
    assert discard.json["error"]["code"] == "TRAINING_PLAN_PREVIEW_ALREADY_ACCEPTED"
    assert len(_plans(mobile_user.id)) == 1


@pytest.mark.usefixtures("synthetic_ai_layer", "enabled")
def test_accept_requires_if_match_and_a_stale_fingerprint_writes_nothing(
        client, mobile_user, as_mobile, provider):
    fingerprint = _preview(client, as_mobile, mobile_user, "pd2-ai-stale-1").json[
        "preview"]["candidate_fingerprint"]

    missing = client.post(ACCEPT, headers=as_mobile(mobile_user, "pd2-ai-stale-1"))
    assert missing.status_code == 428
    malformed = client.post(ACCEPT, headers={**as_mobile(mobile_user, "pd2-ai-stale-1"),
                                             "If-Match": "W/\"nope\""})
    assert malformed.status_code == 428
    stale = _accept(client, as_mobile, mobile_user, "pd2-ai-stale-1", "0" * 64)
    assert stale.status_code == 412
    assert stale.json["error"]["code"] == "TRAINING_PLAN_PREVIEW_STALE"
    assert not _plans(mobile_user.id)
    assert _operations(mobile_user.id) == [("GENERATED", None)]  # still reviewable
    ok = _accept(client, as_mobile, mobile_user, "pd2-ai-stale-1", fingerprint)
    assert ok.status_code == 201 and len(_plans(mobile_user.id)) == 1


@pytest.mark.usefixtures("synthetic_ai_layer", "enabled")
def test_unknown_or_foreign_preview_keys_are_not_found(
        client, mobile_user, as_mobile, provider, make_user):
    fingerprint = _preview(client, as_mobile, mobile_user, "pd2-ai-owner-1").json[
        "preview"]["candidate_fingerprint"]
    other = make_user("pd2-other-owner", profile_complete=True)

    for response in (
            _accept(client, as_mobile, other, "pd2-ai-owner-1", fingerprint),
            client.post(DISCARD, headers=as_mobile(other, "pd2-ai-owner-1")),
            _accept(client, as_mobile, mobile_user, "pd2-ai-never-1", fingerprint)):
        assert response.status_code == 404
        assert response.json["error"]["code"] == "TRAINING_PLAN_PREVIEW_NOT_FOUND"
    assert _operations(mobile_user.id) == [("GENERATED", None)]
    assert not _plans(mobile_user.id) and not _plans(other.id)


@pytest.mark.usefixtures("synthetic_ai_layer", "enabled")
def test_legacy_generation_keys_are_never_accepted_as_previews(
        client, mobile_user, as_mobile, provider):
    created = client.post(AI_PATH, json=CANONICAL,
                          headers=as_mobile(mobile_user, "pd2-legacy-key-1"))
    assert created.status_code == 201
    response = _accept(client, as_mobile, mobile_user, "pd2-legacy-key-1", "a" * 64)
    assert response.status_code == 404


# ── CANCEL-1 / choose another / regenerate ─────────────────────────────────
@pytest.mark.usefixtures("synthetic_ai_layer", "enabled")
def test_discard_is_idempotent_and_leaves_nothing_activatable(
        client, mobile_user, as_mobile, provider):
    fingerprint = _preview(client, as_mobile, mobile_user, "pd2-ai-cancel-1").json[
        "preview"]["candidate_fingerprint"]

    for _ in range(2):
        response = client.post(DISCARD, headers=as_mobile(mobile_user, "pd2-ai-cancel-1"))
        assert response.status_code == 204
    assert _operations(mobile_user.id) == [("FAILED", "TRAINING_PLAN_CANDIDATE_DISCARDED")]
    late = _accept(client, as_mobile, mobile_user, "pd2-ai-cancel-1", fingerprint)
    assert late.status_code == 409
    assert late.json["error"]["code"] == "TRAINING_PLAN_PREVIEW_DISCARDED"
    replay = _preview(client, as_mobile, mobile_user, "pd2-ai-cancel-1")
    assert replay.status_code == 409  # the key is consumed; a new attempt needs a new key
    assert not _plans(mobile_user.id) and provider.calls == 1
    # Cancel records no decision: the owner is back at the method choice.
    assert client.get(START, headers=as_mobile(mobile_user)).json["decision"] is None


@pytest.mark.usefixtures("synthetic_ai_layer", "enabled")
def test_a_new_preview_supersedes_the_unaccepted_one(
        client, mobile_user, as_mobile, provider):
    first = _preview(client, as_mobile, mobile_user, "pd2-ai-regen-1").json["preview"]
    second = _preview(client, as_mobile, mobile_user, "pd2-ai-regen-2").json["preview"]

    assert first["candidate_fingerprint"] != second["candidate_fingerprint"]
    assert _operations(mobile_user.id) == [
        ("FAILED", "TRAINING_PLAN_CANDIDATE_DISCARDED"), ("GENERATED", None)]
    old = _accept(client, as_mobile, mobile_user, "pd2-ai-regen-1", first["candidate_fingerprint"])
    assert old.status_code == 409 and not _plans(mobile_user.id)
    new = _accept(client, as_mobile, mobile_user, "pd2-ai-regen-2", second["candidate_fingerprint"])
    assert new.status_code == 201 and len(_plans(mobile_user.id)) == 1


@pytest.mark.usefixtures("synthetic_ai_layer", "enabled")
def test_an_expired_preview_is_ended_not_activated(
        client, mobile_user, as_mobile, provider):
    fingerprint = _preview(client, as_mobile, mobile_user, "pd2-ai-expired-1").json[
        "preview"]["candidate_fingerprint"]
    operation = TrainingPlanGenerationOperation.query.filter_by(user_id=mobile_user.id).one()
    operation.updated_at = datetime.utcnow() - generation_service.PREVIEW_TTL - timedelta(seconds=1)
    db.session.commit()

    response = _accept(client, as_mobile, mobile_user, "pd2-ai-expired-1", fingerprint)

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_PLAN_PREVIEW_EXPIRED"
    assert _operations(mobile_user.id) == [("FAILED", "TRAINING_PLAN_CANDIDATE_DISCARDED")]
    assert not _plans(mobile_user.id)


# ── Existing plan protected / returning user ───────────────────────────────
@pytest.mark.usefixtures("synthetic_ai_layer", "enabled")
def test_an_active_plan_is_never_replaced_by_any_start_path(
        client, mobile_user, as_mobile, provider):
    fingerprint = _preview(client, as_mobile, mobile_user, "pd2-ai-protect-1").json[
        "preview"]["candidate_fingerprint"]
    general = client.post(GENERAL, json=GENERAL_BODY,
                          headers=as_mobile(mobile_user, "pd2-gen-protect-1"))
    # A live AI draft blocks the General commit until it is discarded.
    assert general.status_code == 409
    assert client.post(DISCARD, headers=as_mobile(mobile_user, "pd2-ai-protect-1")).status_code == 204
    general = client.post(GENERAL, json=GENERAL_BODY,
                          headers=as_mobile(mobile_user, "pd2-gen-protect-1"))
    assert general.status_code == 201
    plan_id = _plans(mobile_user.id)[0].id

    again = _preview(client, as_mobile, mobile_user, "pd2-ai-protect-2")
    assert again.status_code == 409
    assert again.json["error"]["code"] == "TRAINING_PLAN_REPLACEMENT_REFUSED"
    assert provider.calls == 1  # refused before any provider work
    skip = client.put(START, json={"decision": "skip"}, headers=as_mobile(mobile_user))
    assert skip.status_code == 409
    assert skip.json["error"]["code"] == "TRAINING_START_PLAN_EXISTS"
    late = _accept(client, as_mobile, mobile_user, "pd2-ai-protect-1", fingerprint)
    assert late.status_code == 409
    assert [plan.id for plan in _plans(mobile_user.id)] == [plan_id]
    start = client.get(START, headers=as_mobile(mobile_user)).json
    assert start["has_plan"] is True and start["decision"] is None


# ── GEN-1 / MAN-1: deterministic, zero AI ──────────────────────────────────
@pytest.mark.usefixtures("enabled")
def test_general_preview_is_pure_and_accept_persists_exactly_that_document(
        client, mobile_user, as_mobile, no_ai):
    first = client.post(GENERAL_PREVIEW, json=GENERAL_BODY, headers=as_mobile(mobile_user))
    second = client.post(GENERAL_PREVIEW, json=GENERAL_BODY, headers=as_mobile(mobile_user))

    assert first.status_code == second.status_code == 200
    assert first.json == second.json  # deterministic
    preview = first.json["preview"]
    assert preview["method"] == "general" and preview["expires_at"] is None
    assert preview["origin"]["kind"] == "general_template"
    assert preview["origin"]["personalized"] is False
    assert _operations(mobile_user.id) == [] and not _plans(mobile_user.id)

    stale = client.post(GENERAL, json=GENERAL_BODY, headers=_headers(
        as_mobile, mobile_user, "pd2-gen-accept-1", "f" * 64))
    assert stale.status_code == 412 and not _plans(mobile_user.id)
    assert _operations(mobile_user.id) == []  # a refused accept consumes no key

    fingerprint = preview["candidate_fingerprint"]
    accepted = client.post(GENERAL, json=GENERAL_BODY, headers=_headers(
        as_mobile, mobile_user, "pd2-gen-accept-1", fingerprint))
    assert accepted.status_code == 201
    assert accepted.json["origin"]["kind"] == "general_template"  # never relabelled AI
    plan = _plans(mobile_user.id)[0]
    assert hashlib.sha256(plan.plan_data.encode()).hexdigest() == fingerprint
    replay = client.post(GENERAL, json=GENERAL_BODY, headers=_headers(
        as_mobile, mobile_user, "pd2-gen-accept-1", fingerprint))
    assert replay.status_code == 201 and replay.headers["Idempotency-Replayed"] == "true"
    assert len(_plans(mobile_user.id)) == 1
    assert no_ai == []  # no provider, consent, allowance or quota seam reached

    # MAN-1: Manual continues in the ONE canonical editor over this plan.
    editor = client.get(EDITOR, headers=as_mobile(mobile_user))
    assert editor.status_code == 200


@pytest.mark.usefixtures("enabled")
def test_general_preview_and_skip_stay_available_when_ai_consent_is_refused(
        client, mobile_user, as_mobile, no_ai):
    # CONSENT-1: no synthetic consent layer -> the D1-closed AI route refuses.
    refused = client.post(AI_PREVIEW, json=CANONICAL,
                          headers=as_mobile(mobile_user, "pd2-consent-1"))
    assert refused.status_code in (403, 451)
    assert _operations(mobile_user.id) == []
    general = client.post(GENERAL_PREVIEW, json=GENERAL_BODY, headers=as_mobile(mobile_user))
    assert general.status_code == 200
    skip = client.put(START, json={"decision": "skip"}, headers=as_mobile(mobile_user))
    assert skip.status_code == 200
    assert no_ai == []


# ── SKIP-1 ─────────────────────────────────────────────────────────────────
@pytest.mark.usefixtures("enabled")
def test_skip_records_a_decision_and_creates_no_plan(
        client, mobile_user, as_mobile, no_ai):
    before = client.get(START, headers=as_mobile(mobile_user))
    assert before.status_code == 200
    assert before.json == {"contract_version": 1, "has_plan": False, "decision": None,
                           "daily_calorie_target": {"state": "empty", "kcal": None}}

    for _ in range(2):
        skip = client.put(START, json={"decision": "skip"}, headers=as_mobile(mobile_user))
        assert skip.status_code == 200
        assert skip.json["decision"] == "skip" and skip.json["has_plan"] is False
    assert TrainingStartDecision.query.filter_by(user_id=mobile_user.id).count() == 1
    assert not _plans(mobile_user.id) and _operations(mobile_user.id) == []
    assert client.get(START, headers=as_mobile(mobile_user)).json["decision"] == "skip"

    for body in ({}, {"decision": "cancel"}, {"decision": "skip", "x": 1}, None):
        bad = client.put(START, json=body, headers=as_mobile(mobile_user))
        assert bad.status_code == 422

    # A later plan outranks the old Skip.
    created = client.post(GENERAL, json=GENERAL_BODY, headers=as_mobile(mobile_user, "pd2-skip-then-1"))
    assert created.status_code == 201
    after = client.get(START, headers=as_mobile(mobile_user)).json
    assert after["has_plan"] is True and after["decision"] is None
    assert no_ai == []


@pytest.mark.usefixtures("enabled")
def test_skip_requires_completed_onboarding(client, make_user, as_mobile):
    user = make_user("pd2-not-onboarded")
    response = client.put(START, json={"decision": "skip"}, headers=as_mobile(user))
    assert response.status_code == 422
    assert response.json["error"]["code"] == "TRAINING_PLAN_PREREQUISITE_MISSING"
    assert TrainingStartDecision.query.count() == 0


# ── G4-1: sessions stay opt-in ─────────────────────────────────────────────
@pytest.mark.usefixtures("synthetic_ai_layer", "enabled")
def test_preview_projection_follows_the_g4_opt_in(
        client, mobile_user, as_mobile, provider):
    plain = client.post(GENERAL_PREVIEW, json=GENERAL_BODY, headers=as_mobile(mobile_user))
    opted = client.post(GENERAL_PREVIEW + "?include_sessions=1", json=GENERAL_BODY,
                        headers=as_mobile(mobile_user))
    assert all("cardio_session" not in day for day in plain.json["preview"]["plan"]["days"])
    assert all("cardio_session" in day for day in opted.json["preview"]["plan"]["days"])
    ai = client.post(AI_PREVIEW + "&include_sessions=1", json=CANONICAL,
                     headers=as_mobile(mobile_user, "pd2-ai-g4-1"))
    assert ai.status_code == 200
    assert all("cardio_session" in day for day in ai.json["preview"]["plan"]["days"])
    bad = client.post("/api/v1/training/plans?preview=2", json=CANONICAL,
                      headers=as_mobile(mobile_user, "pd2-ai-g4-2"))
    assert bad.status_code == 422


# ── Allowance: no double charge, no free preview/discard loop ──────────────
@pytest.mark.usefixtures("synthetic_ai_layer", "enabled")
def test_metered_preview_accept_consumes_one_unit_and_discard_is_not_refunded(
        app, client, mobile_user, as_mobile, provider):
    enable_ledger(app)  # Starter: one training_generation unit
    app.config["AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED"] = True

    def bucket():
        db.session.expire_all()
        row = AiAllowanceBucket.query.filter_by(
            user_id=mobile_user.id, feature="training_generation").one()
        return row.used, row.reserved

    preview = _preview(client, as_mobile, mobile_user, "pd2-metered-1").json["preview"]
    assert bucket() == (0, 1)  # held, not yet settled
    assert client.post(DISCARD, headers=as_mobile(mobile_user, "pd2-metered-1")).status_code == 204
    assert bucket() == (1, 0)  # the candidate existed: consumed, never refunded
    exhausted = _preview(client, as_mobile, mobile_user, "pd2-metered-2")
    assert exhausted.status_code == 402
    assert exhausted.json["error"]["code"] == "AI_ALLOWANCE_EXHAUSTED"
    assert provider.calls == 1
    late = _accept(client, as_mobile, mobile_user, "pd2-metered-1", preview["candidate_fingerprint"])
    assert late.status_code == 409 and not _plans(mobile_user.id)


@pytest.mark.usefixtures("synthetic_ai_layer", "enabled")
def test_metered_accept_and_duplicate_accept_charge_exactly_once(
        app, client, mobile_user, as_mobile, provider):
    enable_ledger(app)
    app.config["AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED"] = True
    fingerprint = _preview(client, as_mobile, mobile_user, "pd2-metered-accept").json[
        "preview"]["candidate_fingerprint"]
    for _ in range(3):
        assert _accept(client, as_mobile, mobile_user, "pd2-metered-accept",
                       fingerprint).status_code == 201
    db.session.expire_all()
    row = AiAllowanceBucket.query.filter_by(
        user_id=mobile_user.id, feature="training_generation").one()
    assert (row.used, row.reserved) == (1, 0)
    assert len(_plans(mobile_user.id)) == 1 and provider.calls == 1


# ── Store-level: the writer refuses a mismatched candidate ─────────────────
@pytest.mark.usefixtures("synthetic_ai_layer", "enabled")
def test_commit_plan_pins_the_candidate_text(client, mobile_user, as_mobile, provider):
    _preview(client, as_mobile, mobile_user, "pd2-store-pin-1")
    operation = TrainingPlanGenerationOperation.query.filter_by(user_id=mobile_user.id).one()
    with pytest.raises(store.CandidateMismatch):
        store.commit_plan(operation.id, mobile_user.id, expected_candidate_sha="1" * 64)
    assert _operations(mobile_user.id) == [("GENERATED", None)] and not _plans(mobile_user.id)
