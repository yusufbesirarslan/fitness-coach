"""LP21 non-AI general first Training plan: generation, persistence, privacy.

Real HTTP surface and real persistence (SQLite here; the transaction and lock
contract is proven on PostgreSQL in ``test_lp21_general_training_plan_pg.py``).
Provider, consent, allowance and quota seams are replaced by explosives, so a
regression that reaches any of them fails the request instead of passing.
"""
import ast
import json
import pathlib
import socket
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from consent_projection_support import synthetic_authority

from app.blueprints import ai_consent as consent_routes
from app.extensions import db
from app.models import (
    AIConsentEvent,
    AIConsentState,
    AiUsageReservation,
    PumpCheck,
    TrainingPlan,
    TrainingPlanGenerationAllowance,
    TrainingPlanGenerationOperation,
    User,
    UserSession,
    WorkoutLog,
    WorkoutSession,
)
from app.services import mobile_auth, mobile_training
from app.services.exercise_catalog import (
    ExerciseContext,
    is_exercise_compatible,
    load_exercise_catalog,
)
from app.services.general_training_plan import (
    TEMPLATE_ID,
    is_general_receipt,
    parse_general_request,
)
from app.services.general_training_plan import template as general_template
from app.services.general_training_plan.service import canonical_document
from app.services.mobile_training_generation import InvalidPlanRequest
from app.services.mobile_training_generation import store
from app.services.training_generation.plan_schema import WEEKDAYS
from app.timeutil import APP_TZ, audit_clock


POST_PATH = "/api/v1/training/plans/general"
AI_POST_PATH = "/api/v1/training/plans"
CURRENT_PATH = "/api/v1/training/plans/current"
TODAY_PATH = "/api/v1/today"
SESSIONS_PATH = "/api/v1/training/workout-sessions"
BODY = {"template": TEMPLATE_ID, "gun_sayisi": 3, "ekipman": "ev"}
# A Monday: slot 0 is session A in both schedules.
MONDAY = datetime(2026, 7, 20, 15, 0, tzinfo=APP_TZ)
APP_ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"


@pytest.fixture
def owner(make_user):
    return SimpleNamespace(id=make_user("general-plan-owner").id)


@pytest.fixture
def stranger(make_user):
    return SimpleNamespace(id=make_user("general-plan-stranger").id)


@pytest.fixture
def as_mobile(monkeypatch):
    def _headers(user, key=None):
        owner_id = user.id

        def _authenticate(raw):
            row = db.session.get(User, owner_id)
            return mobile_auth.MobilePrincipal(
                row, SimpleNamespace(id=1), {"sub": row.cognito_sub})

        monkeypatch.setattr(mobile_auth, "authenticate_access", _authenticate)
        headers = {"Authorization": "Bearer opaque-general-access"}
        if key is not None:
            headers["Idempotency-Key"] = key
        return headers
    return _headers


@pytest.fixture
def no_ai(monkeypatch):
    """Every provider, consent, allowance and quota seam explodes if reached."""
    calls = []

    def explosive(name):
        def _boom(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"non-AI general plan reached {name}")
        return _boom

    targets = {
        "app.services.ai": (
            "_openai_chat", "_claude_chat", "_heavy_complete", "_heavy_chat",
            "_bedrock_image_message", "_bedrock_validate_image",
            "_bedrock_compare_images",
        ),
        "app.services.ai_provider_call": ("admit", "_before_physical_attempt"),
        "app.services.ai_consent": ("preflight", "dispatch", "mutate"),
        "app.services.premium": (
            "reserve_ai_quota_in_transaction", "refund_ai_quota_in_transaction"),
        "app.services.mobile_training_generation.allowance": (
            "preflight_consent", "bind_for_dispatch", "settle", "settle_pending"),
        "app.services.mobile_training_generation.store": (
            "claim", "authorize_dispatch", "retire_abandoned", "stage_candidate",
            "commit_plan", "record_failure", "abandon_claim"),
        "app.services.training_generation.service": (
            "generate_training_plan_candidate",),
        "app.services.vision_images": ("prepare_image_for_vision",),
    }
    for module, names in targets.items():
        for name in names:
            monkeypatch.setattr(f"{module}.{name}", explosive(f"{module}.{name}"))
    monkeypatch.setattr(socket.socket, "connect", explosive("socket.connect"))
    return calls


def _post(client, headers, body=BODY, when=MONDAY):
    with audit_clock(when):
        return client.post(POST_PATH, json=body, headers=headers)


def _program(document_json):
    return json.loads(document_json)["program"]


# -- deterministic generation -------------------------------------------------

@pytest.mark.parametrize("days", sorted(general_template.SUPPORTED_DAYS))
@pytest.mark.parametrize("equipment", sorted(general_template.SUPPORTED_EQUIPMENT))
@pytest.mark.parametrize("language", ["tr", "en"])
def test_every_supported_input_yields_a_canonical_catalog_valid_week(
        days, equipment, language):
    request = parse_general_request(
        {"template": TEMPLATE_ID, "gun_sayisi": days, "ekipman": equipment})
    document = json.loads(canonical_document(request, language))

    assert set(document) == {"program", "exercise_context"}  # no AI scores
    assert document["exercise_context"]["equipment_context"] == equipment
    program = document["program"]
    assert [day["gun"] for day in program] == WEEKDAYS
    training = [day for day in program if day["tip"] == "antrenman"]
    assert len(training) == days
    assert {day["tip"] for day in program} == {"antrenman", "dinlenme"}
    # No two consecutive training days for three sessions; never three in a row.
    slots = [index for index, day in enumerate(program) if day["tip"] == "antrenman"]
    if days == 3:
        assert all(b - a >= 2 for a, b in zip(slots, slots[1:]))
    catalog = load_exercise_catalog()
    context = ExerciseContext(equipment_context=equipment)
    for day in program:
        assert day["tahmini_kalori"] == 0  # never estimated without a body
        if day["tip"] == "dinlenme":
            assert day["egzersizler"] == [] and day["sure_dk"] == 0
            continue
        assert day["sure_dk"] == general_template.SESSION_MINUTES
        assert len(day["egzersizler"]) >= 4
        for exercise in day["egzersizler"]:
            entry = catalog.by_id[exercise["exercise_id"]]
            assert entry.active and is_exercise_compatible(entry, context)
            assert exercise["isim"] == entry.canonical_name
            assert 1 <= exercise["set"] <= 5 and exercise["tekrar"]
            assert exercise["not"] == ""


def test_identical_inputs_yield_byte_identical_documents():
    request = parse_general_request(BODY)
    first = canonical_document(request, "tr")
    assert canonical_document(parse_general_request(dict(BODY)), "tr") == first
    # Language only relabels days: identical structure, sets and exercises.
    english = _program(canonical_document(request, "en"))
    for tr_day, en_day in zip(_program(first), english):
        assert {**tr_day, "odak": None} == {**en_day, "odak": None}
    # An unknown account language falls back to the product default labels.
    assert canonical_document(request, "de") == first


@pytest.mark.parametrize("body", [
    None, [], "general", True,
    {},
    {"template": TEMPLATE_ID, "gun_sayisi": 3},
    {**BODY, "injuries": "knee"},
    {**BODY, "goal": "lose_weight"},
    {**BODY, "plan": {"program": []}},
    {**BODY, "template": "general_full_body_v2"},
    {**BODY, "template": "AI"},
    {**BODY, "gun_sayisi": 2},
    {**BODY, "gun_sayisi": 5},
    {**BODY, "gun_sayisi": True},
    {**BODY, "gun_sayisi": 3.0},
    {**BODY, "gun_sayisi": "3"},
    {**BODY, "ekipman": "EV"},
    {**BODY, "ekipman": "outdoor"},
    {**BODY, "ekipman": None},
])
def test_unsupported_inputs_are_refused_never_coerced(body):
    with pytest.raises(InvalidPlanRequest):
        parse_general_request(body)


def test_fingerprint_binds_intent_and_is_domain_separated_from_ai():
    from app.services.mobile_training_generation import parse_native_request
    base = parse_general_request(BODY)
    assert parse_general_request(dict(BODY)).fingerprint == base.fingerprint
    assert parse_general_request({**BODY, "gun_sayisi": 4}).fingerprint != base.fingerprint
    assert parse_general_request({**BODY, "ekipman": "minimal"}).fingerprint != base.fingerprint
    ai = parse_native_request({
        "gun_sayisi": 3, "ekipman": "ev", "odak": "tum_vucut", "sure": 45,
        "kardiyo_tipi": "yok", "kardiyo_gun": 0, "kardiyo_sure": 20,
        "kardiyo_yogunluk": "orta", "antrenman_tarzi": "genel",
        "odak_hedef": "genel", "injuries": "",
    })
    assert ai.fingerprint != base.fingerprint


def test_plan_ignores_everything_known_about_the_person(
        client, make_user, as_mobile, no_ai):
    lean = make_user("general-lean", weight=55, height=160, age=22)
    heavy = make_user("general-heavy", weight=120, height=190, age=61,
                      user_metadata={"injuries": "left knee, lower back"},
                      profile_complete=True)
    db.session.add(UserSession(user_id=heavy.id, goal="kilo verme",
                               fitness_level="advanced", tdee=3100))
    db.session.commit()
    ids = [lean.id, heavy.id]
    for user_id in ids:
        response = _post(client, as_mobile(SimpleNamespace(id=user_id),
                                           f"general-person-{user_id:04d}"))
        assert response.status_code == 201, response.json
    documents = [TrainingPlan.query.filter_by(user_id=user_id).one().plan_data
                 for user_id in ids]
    assert documents[0] == documents[1]
    assert "knee" not in documents[1]
    assert no_ai == []


# -- persistence and idempotency ----------------------------------------------

def test_first_plan_is_a_real_canonical_plan_with_a_truthful_receipt(
        client, owner, as_mobile, no_ai):
    response = _post(client, as_mobile(owner, "general-key-0001"))

    assert response.status_code == 201, response.json
    assert response.headers["Idempotency-Replayed"] == "false"
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json
    assert set(body) == {"contract_version", "origin", "plan"}
    assert body["contract_version"] == 1
    assert body["origin"] == {
        "kind": "general_template", "template": TEMPLATE_ID, "personalized": False}
    projected = body["plan"]
    assert projected["score"] is None
    assert [day["kind"] for day in projected["days"]] == [
        "training", "rest", "training", "rest", "training", "rest", "rest"]
    assert projected["current_workout_ref"] == projected["days"][0]["workout_ref"]

    plan = TrainingPlan.query.filter_by(user_id=owner.id).one()
    assert plan.score is None and plan.mutation_version == 0
    assert projected["plan_lineage"] == plan.lineage_id
    assert plan.plan_data == canonical_document(parse_general_request(BODY), "tr")
    receipt = TrainingPlanGenerationOperation.query.filter_by(user_id=owner.id).one()
    assert is_general_receipt(receipt)
    assert (receipt.training_plan_id, receipt.plan_lineage_id) == (plan.id, plan.lineage_id)
    assert receipt.provider_dispatch_possible is False
    assert receipt.provider_attempt_possible is None
    assert receipt.allowance_metered is None
    assert receipt.quota_reserved is False and receipt.quota_week is None
    assert receipt.candidate_plan_data is None and receipt.completed_at is not None
    assert TrainingPlanGenerationAllowance.query.count() == 0
    assert AiUsageReservation.query.count() == 0
    # No fabricated execution: nothing was started, logged or completed.
    assert WorkoutSession.query.count() == 0
    assert WorkoutLog.query.count() == 0
    assert PumpCheck.query.count() == 0
    assert no_ai == []


def test_same_key_replays_the_same_plan_without_a_second_write(
        client, owner, as_mobile, no_ai):
    first = _post(client, as_mobile(owner, "general-key-0002"))
    replay = _post(client, as_mobile(owner, "general-key-0002"))

    assert replay.status_code == 201
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json == first.json
    assert TrainingPlan.query.filter_by(user_id=owner.id).count() == 1
    assert TrainingPlanGenerationOperation.query.filter_by(user_id=owner.id).count() == 1


def test_same_key_with_a_different_intent_conflicts(client, owner, as_mobile):
    assert _post(client, as_mobile(owner, "general-key-0003")).status_code == 201
    response = _post(client, as_mobile(owner, "general-key-0003"),
                     body={**BODY, "ekipman": "spor_salonu"})

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_PLAN_IDEMPOTENCY_CONFLICT"
    assert response.json["error"]["retryable"] is False


def test_an_ai_operation_key_is_never_replayed_as_a_general_plan(
        client, owner, as_mobile):
    db.session.add(TrainingPlanGenerationOperation(
        user_id=owner.id, idempotency_key="shared-key-0001",
        request_fingerprint="0" * 64, status="FAILED",
        provider_dispatch_possible=True, error_code="X", error_http_status=503))
    db.session.commit()

    response = _post(client, as_mobile(owner, "shared-key-0001"))

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_PLAN_IDEMPOTENCY_CONFLICT"
    assert TrainingPlan.query.count() == 0


def test_an_existing_plan_is_never_replaced(client, owner, as_mobile):
    existing = TrainingPlan(user_id=owner.id, plan_data='{"program": []}', score=8.0)
    db.session.add(existing)
    db.session.commit()
    before = (existing.id, existing.lineage_id, existing.plan_data)

    response = _post(client, as_mobile(owner, "general-key-0004"))

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_PLAN_REPLACEMENT_REFUSED"
    row = TrainingPlan.query.filter_by(user_id=owner.id).one()
    assert (row.id, row.lineage_id, row.plan_data) == before
    # The refusal consumed no key: nothing was recorded for it.
    assert TrainingPlanGenerationOperation.query.count() == 0


def test_a_second_key_after_success_is_refused_not_duplicated(
        client, owner, as_mobile):
    assert _post(client, as_mobile(owner, "general-key-0005")).status_code == 201
    response = _post(client, as_mobile(owner, "general-key-0006"))

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_PLAN_REPLACEMENT_REFUSED"
    assert TrainingPlan.query.filter_by(user_id=owner.id).count() == 1


def test_partial_failure_rolls_back_receipt_and_plan_together(
        client, owner, as_mobile, monkeypatch):
    original = store._insert_plan

    def failing_insert(**kwargs):
        original(**kwargs)
        db.session.flush()
        raise RuntimeError("injected after the plan and receipt were flushed")

    monkeypatch.setattr(store, "_insert_plan", failing_insert)
    failed = _post(client, as_mobile(owner, "general-key-0007"))

    assert failed.status_code == 503
    assert failed.json["error"]["code"] == "TRAINING_PLAN_PERSISTENCE_UNAVAILABLE"
    assert failed.json["error"]["retryable"] is True
    assert TrainingPlan.query.count() == 0
    assert TrainingPlanGenerationOperation.query.count() == 0

    monkeypatch.setattr(store, "_insert_plan", original)
    retried = _post(client, as_mobile(owner, "general-key-0007"))
    assert retried.status_code == 201
    assert retried.headers["Idempotency-Replayed"] == "false"


def test_replay_of_a_replaced_plan_is_superseded_not_recreated(
        client, owner, as_mobile):
    assert _post(client, as_mobile(owner, "general-key-0008")).status_code == 201
    plan = TrainingPlan.query.filter_by(user_id=owner.id).one()
    db.session.delete(plan)
    db.session.add(TrainingPlan(user_id=owner.id, plan_data=plan.plan_data))
    db.session.commit()

    response = _post(client, as_mobile(owner, "general-key-0008"))

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_PLAN_GENERAL_SUPERSEDED"
    assert response.json["error"]["retryable"] is False
    assert TrainingPlan.query.filter_by(user_id=owner.id).count() == 1


def test_a_live_ai_generation_is_not_overtaken(client, owner, as_mobile):
    db.session.add(TrainingPlanGenerationOperation(
        user_id=owner.id, idempotency_key="ai-key-live-0001",
        request_fingerprint="1" * 64, status="IN_PROGRESS",
        provider_dispatch_possible=True))
    db.session.commit()

    response = _post(client, as_mobile(owner, "general-key-0009"))

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_PLAN_GENERATION_IN_PROGRESS"
    assert response.json["error"]["retryable"] is True
    assert response.headers["Retry-After"] == "15"
    assert TrainingPlan.query.count() == 0
    assert TrainingPlanGenerationOperation.query.count() == 1


@pytest.mark.parametrize("status", ["IN_PROGRESS", "GENERATED"])
def test_abandoned_ai_work_does_not_strand_the_owner(
        client, owner, as_mobile, no_ai, status):
    stale = datetime.utcnow() - store.ABANDON_AFTER - timedelta(minutes=1)
    db.session.add(TrainingPlanGenerationOperation(
        user_id=owner.id, idempotency_key="ai-key-stale-0001",
        request_fingerprint="2" * 64, status=status,
        provider_dispatch_possible=True, created_at=stale, updated_at=stale))
    db.session.commit()

    response = _post(client, as_mobile(owner, "general-key-0010"))

    assert response.status_code == 201, response.json
    # The abandoned AI row is left exactly as it was: no retirement, no refund.
    ai_row = TrainingPlanGenerationOperation.query.filter_by(
        idempotency_key="ai-key-stale-0001").one()
    assert ai_row.status == status


def test_owners_are_isolated_even_under_the_same_key(
        client, owner, stranger, as_mobile):
    mine = _post(client, as_mobile(owner, "general-key-0011"))
    theirs = _post(client, as_mobile(stranger, "general-key-0011"),
                   body={**BODY, "ekipman": "spor_salonu"})

    assert mine.status_code == theirs.status_code == 201
    assert theirs.headers["Idempotency-Replayed"] == "false"
    assert mine.json["plan"]["plan_lineage"] != theirs.json["plan"]["plan_lineage"]
    assert TrainingPlan.query.filter_by(user_id=owner.id).count() == 1
    assert TrainingPlan.query.filter_by(user_id=stranger.id).count() == 1
    # Neither owner's reference can name the other's workout.
    ref = mine.json["plan"]["days"][0]["workout_ref"]
    with audit_clock(MONDAY):
        response = client.get(f"/api/v1/training/workouts/{ref}",
                              headers=as_mobile(stranger))
    assert response.status_code == 409


def test_request_shape_failures_are_typed_and_write_nothing(
        client, owner, as_mobile):
    missing_key = _post(client, as_mobile(owner))
    bad_key = _post(client, as_mobile(owner, "short"))
    bad_body = _post(client, as_mobile(owner, "general-key-0012"),
                     body={**BODY, "injuries": "knee"})

    assert missing_key.status_code == bad_key.status_code == 400
    assert missing_key.json["error"]["code"] == "TRAINING_PLAN_INVALID_IDEMPOTENCY_KEY"
    assert bad_body.status_code == 422
    assert bad_body.json["error"]["code"] == "TRAINING_PLAN_INVALID_REQUEST"
    assert TrainingPlan.query.count() == 0
    assert TrainingPlanGenerationOperation.query.count() == 0


def test_requires_a_bearer_and_a_browser_cookie_cannot_authorize(
        client, owner):
    with client.session_transaction() as session:
        session["_user_id"] = str(owner.id)
    response = _post(client, {"Idempotency-Key": "general-key-0013"})
    assert response.status_code == 401
    assert TrainingPlan.query.count() == 0


# -- canonical reads and execution ---------------------------------------------

def test_today_plan_and_workout_reads_see_the_canonical_plan(
        app, client, owner, as_mobile):
    created = _post(client, as_mobile(owner, "general-key-0014")).json["plan"]
    with audit_clock(MONDAY):
        current = client.get(CURRENT_PATH, headers=as_mobile(owner))
        today = client.get(TODAY_PATH, headers=as_mobile(owner))
        ref = created["current_workout_ref"]
        workout = client.get(f"/api/v1/training/workouts/{ref}",
                             headers=as_mobile(owner))

    assert current.status_code == 200
    assert current.json == {"plan": created}
    assert today.status_code == 200, today.json
    assert today.json["today"]["status"] == "scheduled_not_started"
    assert today.json["today"]["daily_context"]["plan_lineage"] == created["plan_lineage"]
    assert workout.status_code == 200
    exercises = workout.json["workout"]["exercises"]
    assert [item["exercise_id"] for item in exercises] == [
        exercise_id for exercise_id, *_ in general_template._SESSIONS["ev"]["A"]]
    assert exercises[0]["rest"] == {"display_text": "90 sn", "seconds": 90}


def test_a_general_workout_can_be_started_like_any_canonical_workout(
        app, client, owner, as_mobile):
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True
    created = _post(client, as_mobile(owner, "general-key-0015")).json["plan"]
    with audit_clock(MONDAY):
        started = client.post(SESSIONS_PATH, headers=as_mobile(owner),
                              json={"workout_ref": created["current_workout_ref"]})

    assert started.status_code == 201, started.json
    session = started.json["session"]
    assert session["plan_lineage"] == created["plan_lineage"]
    assert session["status"] == "active" and session["completed_at"] is None
    assert PumpCheck.query.count() == 0


# -- consent and provider invariants ------------------------------------------

def test_d1_still_closes_ai_generation_and_never_falls_back_to_general(
        client, owner, as_mobile):
    assert "mobile_api.create_training_plan" in consent_routes._CLOSED_ENDPOINTS
    assert "mobile_api.create_replacement_proposal" in consent_routes._CLOSED_ENDPOINTS
    assert "mobile_api.create_general_training_plan" not in consent_routes._CLOSED_ENDPOINTS

    response = client.post(AI_POST_PATH, json={}, headers=as_mobile(owner, "ai-key-0001"))

    assert response.status_code == 403
    assert response.json["error"]["code"] == "AI_CONSENT_DISCLOSURE_UNAVAILABLE"
    assert TrainingPlan.query.count() == 0
    assert TrainingPlanGenerationOperation.query.count() == 0


def test_consent_unavailable_or_declined_does_not_block_and_is_not_touched(
        client, make_user, as_mobile, no_ai):
    unavailable = make_user("general-consent-unavailable")
    declined = make_user("general-consent-declined")
    revoked = {"version": "v1", "state": "revoked", "revoked_at": "2026-07-01T00:00:00"}
    db.session.add(AIConsentState(
        owner_id=declined.id, generation=2,
        grants={"training_plan": revoked}, changed_at=datetime(2026, 7, 1)))
    db.session.commit()
    ids = [unavailable.id, declined.id]

    for user_id in ids:
        response = _post(client, as_mobile(SimpleNamespace(id=user_id), f"general-consent-{user_id:04d}"))
        assert response.status_code == 201, response.json

    states = {row.owner_id: (row.generation, row.grants) for row in AIConsentState.query.all()}
    assert states == {declined.id: (2, {"training_plan": revoked})}
    assert AIConsentEvent.query.count() == 0
    assert no_ai == []


def test_the_general_surface_imports_no_ai_consent_quota_or_provider_module():
    forbidden = {
        "app.services.ai", "app.services.ai_provider_call", "app.services.ai_consent",
        "app.services.ai_allowances", "app.services.ai_gate", "app.services.premium",
        "app.services.ai_spend_guard", "app.services.vision_images",
        "app.services.mobile_training_generation.allowance",
        "app.services.training_generation.prompt_builder",
        "openai", "anthropic", "boto3", "botocore",
    }
    sources = sorted((APP_ROOT / "services" / "general_training_plan").glob("*.py"))
    sources.append(APP_ROOT / "blueprints" / "mobile_training_general.py")
    assert len(sources) >= 6
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names.add(module)
                names.update(f"{module}.{alias.name}" for alias in node.names)
            elif isinstance(node, ast.Name):
                assert node.id not in {
                    "AI_RATELIMIT", "BEDROCK_RATELIMIT", "blocking_concurrency_slot",
                }, f"{path.name} takes an AI control"
        leaked = {name for name in names
                  for banned in forbidden
                  if name == banned or name.startswith(banned + ".")}
        assert not leaked, f"{path.name} imports {sorted(leaked)}"


def test_fingerprint_domain_is_pinned_and_distinct_from_the_ai_command():
    import hashlib
    from app.services.general_training_plan import contract as general_contract
    from app.services.mobile_training_generation import contract as ai_contract

    assert general_contract._FINGERPRINT_DOMAIN == b"axisai:training-plan-general:v1\0"
    assert general_contract._FINGERPRINT_DOMAIN != ai_contract._FINGERPRINT_DOMAIN
    canonical = json.dumps(BODY, sort_keys=True, separators=(",", ":")).encode()
    assert parse_general_request(BODY).fingerprint == hashlib.sha256(
        b"axisai:training-plan-general:v1\0" + canonical).hexdigest()


def test_replay_refuses_a_plan_row_whose_lineage_no_longer_matches(
        client, owner, as_mobile):
    assert _post(client, as_mobile(owner, "general-key-0016")).status_code == 201
    plan = TrainingPlan.query.filter_by(user_id=owner.id).one()
    plan.lineage_id = "rebound-lineage"
    db.session.commit()

    response = _post(client, as_mobile(owner, "general-key-0016"))

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_PLAN_GENERAL_SUPERSEDED"


# -- read-side origin (current plan + Today, per-request opt-in) -------------

GENERAL_ORIGIN = {
    "kind": "general_template", "template": TEMPLATE_ID, "personalized": False}
AI_ORIGIN = {"kind": "ai_generated", "template": None, "personalized": True}
UNKNOWN_ORIGIN = {"kind": "unknown", "template": None, "personalized": None}
OPT_IN = "?include_origin=1"
# The pre-LP21 contracts, exactly. Shipped mobile DTOs (exactKeys) reject any
# other key, so a request without the opt-in must never see one.
LEGACY_PLAN_KEYS = {"plan_lineage", "mutation_version", "created_at", "score",
                    "current_workout_ref", "days"}
LEGACY_TODAY_KEYS = {"date", "server_time", "status", "action", "workout",
                     "plan", "daily_context", "state"}
LEGACY_TODAY_PLAN_KEYS = {"exists", "created_at"}


def _reads(client, user, as_mobile, query="", when=MONDAY):
    with audit_clock(when):
        current = client.get(CURRENT_PATH + query, headers=as_mobile(user))
        today = client.get(TODAY_PATH + query, headers=as_mobile(user))
    return current, today


def _keys(value):
    """Every mapping key anywhere in a JSON document."""
    if isinstance(value, dict):
        found = set(value)
        for item in value.values():
            found |= _keys(item)
        return found
    if isinstance(value, list):
        found = set()
        for item in value:
            found |= _keys(item)
        return found
    return set()


def _assert_origin(client, user, as_mobile, expected):
    current, today = _reads(client, user, as_mobile, OPT_IN)
    assert current.status_code == 200, current.json
    assert today.status_code == 200, today.json
    assert current.json["plan"]["origin"] == expected
    assert today.json["today"]["plan"]["origin"] == expected
    assert today.json["today"]["plan"]["exists"] is True


def _legacy_plan(user_id):
    """A browser-style row: the SAME template content, but no ledger receipt."""
    plan = TrainingPlan(
        user_id=user_id,
        plan_data=canonical_document(parse_general_request(BODY), "tr"),
        score=None)
    db.session.add(plan)
    db.session.commit()
    return plan


def _receipt(user_id, plan, key, **overrides):
    fields = dict(
        user_id=user_id, idempotency_key=key, request_fingerprint="0" * 64,
        status="SUCCEEDED", provider_dispatch_possible=False,
        quota_reserved=False, training_plan_id=plan.id,
        plan_lineage_id=plan.lineage_id)
    fields.update(overrides)
    db.session.add(TrainingPlanGenerationOperation(**fields))
    db.session.commit()


def _ledger_statements(client, user, as_mobile, path):
    from sqlalchemy import event

    seen = []

    def _record(conn, cursor, statement, params, context, many):
        if "FROM training_plan_generation_operation" in statement:
            seen.append(statement)

    event.listen(db.engine, "before_cursor_execute", _record)
    try:
        with audit_clock(MONDAY):
            response = client.get(path, headers=as_mobile(user))
    finally:
        event.remove(db.engine, "before_cursor_execute", _record)
    assert response.status_code == 200, response.json
    return seen


@pytest.mark.parametrize("has_plan", [True, False])
def test_without_the_opt_in_both_reads_keep_the_legacy_contract_exactly(
        client, owner, as_mobile, has_plan):
    if has_plan:
        assert _post(client, as_mobile(owner, "general-key-0100")).status_code == 201
    current, today = _reads(client, owner, as_mobile)

    assert current.status_code == 200 and today.status_code == 200
    assert "origin" not in _keys(current.json)
    assert "origin" not in _keys(today.json)
    assert set(today.json["today"]) == LEGACY_TODAY_KEYS
    assert set(today.json["today"]["plan"]) == LEGACY_TODAY_PLAN_KEYS
    if has_plan:
        assert set(current.json["plan"]) == LEGACY_PLAN_KEYS
    else:
        assert current.json == {"plan": None}
    # Unrelated query values stay ignored, exactly as before LP21.
    with audit_clock(MONDAY):
        noisy = client.get(TODAY_PATH + "?user_id=1&date=2026-01-01",
                           headers=as_mobile(owner))
    assert noisy.status_code == 200 and "origin" not in _keys(noisy.json)


def test_legacy_requests_issue_no_origin_read_and_the_opt_in_exactly_one(
        client, owner, as_mobile):
    assert _post(client, as_mobile(owner, "general-key-0110")).status_code == 201
    for path in (CURRENT_PATH, TODAY_PATH):
        assert _ledger_statements(client, owner, as_mobile, path) == []
        assert len(_ledger_statements(client, owner, as_mobile, path + OPT_IN)) == 1


def test_the_general_create_201_keeps_its_own_contract(
        client, owner, as_mobile, no_ai):
    body = _post(client, as_mobile(owner, "general-key-0111")).json

    assert body["origin"] == GENERAL_ORIGIN
    # The 201's plan is the LEGACY current-plan plan; origin is top-level only.
    assert set(body["plan"]) == LEGACY_PLAN_KEYS
    current, _today = _reads(client, owner, as_mobile)
    assert current.json == {"plan": body["plan"]}
    assert no_ai == []


def test_opted_in_reads_carry_the_general_origin_the_201_announced(
        client, owner, as_mobile, no_ai):
    created = _post(client, as_mobile(owner, "general-key-0101")).json
    current, today = _reads(client, owner, as_mobile, OPT_IN)

    # Same plan object plus exactly one key; plan identity is unchanged.
    assert current.json["plan"] == {**created["plan"], "origin": GENERAL_ORIGIN}
    assert set(today.json["today"]["plan"]) == LEGACY_TODAY_PLAN_KEYS | {"origin"}
    assert today.json["today"]["plan"]["origin"] == created["origin"]
    assert (today.json["today"]["daily_context"]["plan_lineage"]
            == created["plan"]["plan_lineage"])
    for response in (current, today):
        assert response.headers["Cache-Control"] == "no-store"
    assert no_ai == []


def test_origin_names_the_lineage_so_journal_edits_and_undo_keep_it(
        client, owner, as_mobile):
    from app.services.plan_mutation import (
        MutationContext, apply_plan_mutation, undo_last_change)
    from app.services.plan_mutation.commands import (
        UpdateExercisePrescriptionCommand)

    created = _post(client, as_mobile(owner, "general-key-0102")).json["plan"]
    stored = TrainingPlan.query.filter_by(user_id=owner.id).one()
    day = _program(stored.plan_data)[0]
    edited = apply_plan_mutation(
        owner.id,
        UpdateExercisePrescriptionCommand(
            day=day["gun"], exercise=day["egzersizler"][0]["isim"], sets=4),
        MutationContext(idempotency_key="origin-edit-0001"))
    undone = undo_last_change(
        owner.id, MutationContext(idempotency_key="origin-undo-0001"))
    assert (edited.outcome, undone.outcome) == ("applied", "applied")

    current, today = _reads(client, owner, as_mobile, OPT_IN)
    assert current.json["plan"]["plan_lineage"] == created["plan_lineage"]
    assert current.json["plan"]["mutation_version"] == 2
    assert current.json["plan"]["origin"] == GENERAL_ORIGIN
    assert today.json["today"]["plan"]["origin"] == GENERAL_ORIGIN


def test_an_ai_first_plan_with_authoritative_provenance_is_ai_generated(
        client, owner, as_mobile):
    plan = _legacy_plan(owner.id)
    # The native AI command's terminal state: dispatch authorized (True) before
    # the candidate existed, then SUCCEEDED and bound to this plan row.
    _receipt(owner.id, plan, "origin-ai-0001", provider_dispatch_possible=True,
             allowance_metered=True, provider_attempt_possible=True)

    _assert_origin(client, owner, as_mobile, AI_ORIGIN)


def test_a_plan_without_a_receipt_is_unknown_never_ai(
        client, owner, as_mobile):
    # Absence of a general receipt proves nothing, and the content (here the
    # exact template document) is never used to guess.
    _legacy_plan(owner.id)
    _assert_origin(client, owner, as_mobile, UNKNOWN_ORIGIN)


@pytest.mark.parametrize("scenario", [
    "historical_unknown_dispatch", "metered_without_dispatch", "quota_reserved",
    "failed", "failed_after_dispatch", "names_another_row",
    "two_receipts_on_one_lineage", "general_and_ai_on_one_lineage",
])
def test_unestablished_or_contradictory_ledger_evidence_is_unknown(
        client, owner, as_mobile, scenario):
    plan = _legacy_plan(owner.id)
    if scenario == "historical_unknown_dispatch":
        _receipt(owner.id, plan, "origin-key-01", provider_dispatch_possible=None)
    elif scenario == "metered_without_dispatch":
        _receipt(owner.id, plan, "origin-key-01", allowance_metered=True)
    elif scenario == "quota_reserved":
        _receipt(owner.id, plan, "origin-key-01", quota_reserved=True)
    elif scenario == "failed":
        _receipt(owner.id, plan, "origin-key-01", status="FAILED")
    elif scenario == "failed_after_dispatch":
        _receipt(owner.id, plan, "origin-key-01", status="FAILED",
                 provider_dispatch_possible=True)
    elif scenario == "names_another_row":
        _receipt(owner.id, plan, "origin-key-01", training_plan_id=plan.id + 999)
    elif scenario == "two_receipts_on_one_lineage":
        _receipt(owner.id, plan, "origin-key-01")
        _receipt(owner.id, plan, "origin-key-02")
    else:
        _receipt(owner.id, plan, "origin-key-01")
        _receipt(owner.id, plan, "origin-key-02", provider_dispatch_possible=True)

    _assert_origin(client, owner, as_mobile, UNKNOWN_ORIGIN)


def test_another_owners_receipt_cannot_label_a_plan(owner, stranger):
    from app.services.training_plan_origin import resolve_plan_origin

    plan = _legacy_plan(owner.id)
    _receipt(stranger.id, plan, "origin-key-03")

    assert resolve_plan_origin(plan, owner.id) == UNKNOWN_ORIGIN
    # Even asked on the receipt owner's behalf, the plan row is not theirs.
    assert resolve_plan_origin(plan, stranger.id) == UNKNOWN_ORIGIN


@pytest.mark.usefixtures("synthetic_operation_authority")
def test_replacing_a_general_plan_drops_the_general_origin(
        client, owner, as_mobile):
    from app.services.plan_replacement import PlanExpectation, replace_training_plan

    created = _post(client, as_mobile(owner, "general-key-0104")).json["plan"]
    replace_training_plan(
        owner.id,
        PlanExpectation(False, created["plan_lineage"], created["mutation_version"]),
        plan_data=canonical_document(parse_general_request(BODY), "tr"), score=None,
        authority=synthetic_authority(owner.id))

    receipt = TrainingPlanGenerationOperation.query.filter_by(user_id=owner.id).one()
    assert is_general_receipt(receipt)  # history is kept; it names the old lineage
    current, _today = _reads(client, owner, as_mobile, OPT_IN)
    assert current.json["plan"]["plan_lineage"] != created["plan_lineage"]
    _assert_origin(client, owner, as_mobile, UNKNOWN_ORIGIN)


def test_opted_in_without_a_plan_has_a_null_origin_and_no_ledger_read(
        client, owner, as_mobile, monkeypatch):
    from app.services import mobile_today

    def _never(*args, **kwargs):
        raise AssertionError("origin resolved without a plan")

    monkeypatch.setattr(mobile_today, "resolve_plan_origin", _never)
    monkeypatch.setattr(mobile_training, "resolve_plan_origin", _never)
    current, today = _reads(client, owner, as_mobile, OPT_IN)

    assert current.status_code == 200 and current.json == {"plan": None}
    assert today.status_code == 200
    assert today.json["today"]["plan"] == {
        "exists": False, "created_at": None, "origin": None}


@pytest.mark.parametrize("query", [
    "?include_origin=0", "?include_origin=true", "?include_origin=",
    "?include_origin=1&include_origin=1", "?include_origin=yes",
])
def test_a_malformed_opt_in_is_refused_never_coerced(
        client, owner, as_mobile, query):
    assert _post(client, as_mobile(owner, "general-key-0106")).status_code == 201
    current, today = _reads(client, owner, as_mobile, query)

    assert current.status_code == 400
    assert current.json["error"]["code"] == "TRAINING_READ_INVALID_REQUEST"
    assert current.json["error"]["retryable"] is False
    assert today.status_code == 400
    assert today.json["error"]["code"] == "TODAY_INVALID_REQUEST"
    assert today.json["error"]["retryable"] is False


def test_the_opt_in_still_requires_a_bearer(raw_client):
    for path in (CURRENT_PATH + OPT_IN, TODAY_PATH + OPT_IN):
        response = raw_client.get(path)
        assert response.status_code == 401
        assert "origin" not in _keys(response.json)


def test_an_origin_read_failure_fails_closed_never_unknown(
        client, owner, as_mobile, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from app.services import training_plan_origin

    assert _post(client, as_mobile(owner, "general-key-0105")).status_code == 201

    class _Unreadable:
        class query:  # noqa: N801 - stands in for the model's query attribute
            @staticmethod
            def filter_by(**kwargs):
                raise OperationalError("SELECT", {}, Exception("ledger down"))

    monkeypatch.setattr(
        training_plan_origin, "TrainingPlanGenerationOperation", _Unreadable)
    current, today = _reads(client, owner, as_mobile, OPT_IN)

    assert current.status_code == 503
    assert current.json["error"]["code"] == "TRAINING_READ_UNAVAILABLE"
    assert current.json["error"]["retryable"] is True
    assert today.status_code == 503
    assert today.json["error"]["code"] == "TODAY_TEMPORARILY_UNAVAILABLE"
    # The legacy shape never touches the ledger, so it is unaffected.
    legacy_current, legacy_today = _reads(client, owner, as_mobile)
    assert legacy_current.status_code == 200 and legacy_today.status_code == 200


def test_origin_module_is_a_neutral_read_only_projection():
    from app.services import general_training_plan, training_plan_origin

    path = APP_ROOT / "services" / "training_plan_origin.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)}
    imported |= {alias.name for node in ast.walk(tree)
                 if isinstance(node, ast.Import) for alias in node.names}
    # Only the model and the neutral request identities: no Flask, AI ledger
    # store, provider or general-plan package.
    assert imported == {
        "__future__", "app.models", "app.services.general_plan_identity"}
    identity_path = APP_ROOT / "services" / "general_plan_identity.py"
    identity_tree = ast.parse(identity_path.read_text(encoding="utf-8"))
    identity_imports = {node.module for node in ast.walk(identity_tree)
                        if isinstance(node, ast.ImportFrom)}
    identity_imports |= {alias.name for node in ast.walk(identity_tree)
                         if isinstance(node, ast.Import) for alias in node.names}
    # Standard library only.
    assert identity_imports == {
        "__future__", "hashlib", "json", "functools", "itertools"}
    source = path.read_text(encoding="utf-8")
    for write in ("session.add", "session.flush", "session.commit", ".update(",
                  ".delete("):
        assert write not in source
    # A failed read must propagate to the callers' fail-closed handlers.
    assert not any(isinstance(node, ast.ExceptHandler) for node in ast.walk(tree))
    # Every template the command accepts is one the projection can name.
    assert training_plan_origin.GENERAL_TEMPLATE_IDS == (
        TEMPLATE_ID, general_template.CARDIO_TEMPLATE_ID)
    assert general_template.TEMPLATE_ID == TEMPLATE_ID
    assert general_training_plan.is_general_receipt is training_plan_origin.is_general_receipt


# -- LP21 cardio: general_full_body_cardio_v1 ---------------------------------

from app.services import general_plan_identity as identity  # noqa: E402
from app.services.general_training_plan import CARDIO_TEMPLATE_ID  # noqa: E402

CARDIO_BODY = {
    "template": CARDIO_TEMPLATE_ID, "gun_sayisi": 3, "ekipman": "ev",
    "kardiyo_tipi": "kosu", "kardiyo_gun": 2, "kardiyo_sure": 30,
    "kardiyo_yogunluk": "orta",
}
CARDIO_ORIGIN = {
    "kind": "general_template", "template": CARDIO_TEMPLATE_ID,
    "personalized": False}
SATURDAY = datetime(2026, 7, 25, 15, 0, tzinfo=APP_TZ)
SUNDAY = datetime(2026, 7, 26, 15, 0, tzinfo=APP_TZ)
DAY_KIND = "?include_day_kind=1"


def _cardio(**overrides):
    return {**CARDIO_BODY, **overrides}


@pytest.mark.parametrize("days", sorted(identity.STRENGTH_DAYS))
@pytest.mark.parametrize("equipment", sorted(identity.EQUIPMENT))
@pytest.mark.parametrize("cardio_type", sorted(identity.CARDIO_TYPES))
@pytest.mark.parametrize("language", ["tr", "en"])
def test_every_cardio_input_keeps_the_v1_strength_week_and_adds_real_cardio_days(
        days, equipment, cardio_type, language):
    v1 = _program(canonical_document(parse_general_request(
        {"template": TEMPLATE_ID, "gun_sayisi": days, "ekipman": equipment}),
        language))
    catalog = load_exercise_catalog()
    for cardio_days in identity.cardio_day_choices(days):
        request = parse_general_request(_cardio(
            gun_sayisi=days, ekipman=equipment, kardiyo_tipi=cardio_type,
            kardiyo_gun=cardio_days, kardiyo_sure=20, kardiyo_yogunluk="dusuk"))
        document = json.loads(canonical_document(request, language))

        assert set(document) == {"program", "exercise_context"}
        assert document["exercise_context"]["equipment_context"] == equipment
        assert document["exercise_context"]["cardio_type"] == cardio_type
        program = document["program"]
        assert [day["gun"] for day in program] == WEEKDAYS
        tips = [day["tip"] for day in program]
        assert tips.count("antrenman") == days
        assert tips.count("kardiyo") == cardio_days
        # Never a full week: Sunday stays a rest day.
        assert tips.count("dinlenme") == 7 - days - cardio_days >= 1
        assert program[6]["tip"] == "dinlenme"
        context = ExerciseContext(equipment_context=equipment,
                                  cardio_type=cardio_type)
        for strength, original in zip(program, v1):
            if original["tip"] == "antrenman":
                # The strength week is v1's, byte for byte.
                assert strength == original
                continue
            assert strength["tahmini_kalori"] == 0
            if strength["tip"] == "dinlenme":
                assert strength == original
                continue
            assert strength["sure_dk"] == 20
            [exercise] = strength["egzersizler"]
            entry = catalog.by_id[exercise["exercise_id"]]
            assert entry.active and entry.movement == "cardio"
            assert is_exercise_compatible(entry, context)
            assert exercise["isim"] == entry.canonical_name
            assert (exercise["set"], exercise["tekrar"], exercise["dinlenme"],
                    exercise["not"]) == (1, "20 dk", "0 sn", "")
            assert entry.exercise_id != "ex_swimming" or cardio_type == "yuzme"


def test_cardio_day_placement_and_labels_are_fixed():
    program = _program(canonical_document(parse_general_request(_cardio(
        gun_sayisi=3, kardiyo_gun=3, kardiyo_tipi="karisik")), "en"))
    assert [day["tip"] for day in program] == [
        "antrenman", "kardiyo", "antrenman", "kardiyo", "antrenman", "kardiyo",
        "dinlenme"]
    assert [day["odak"] for day in program if day["tip"] == "kardiyo"] == [
        "Cardio · Running · Moderate intensity",
        "Cardio · Cycling · Moderate intensity",
        "Cardio · Brisk walking · Moderate intensity"]
    four = _program(canonical_document(parse_general_request(_cardio(
        gun_sayisi=4, kardiyo_gun=2, kardiyo_tipi="yuzme",
        kardiyo_yogunluk="yuksek")), "tr"))
    assert [day["tip"] for day in four] == [
        "antrenman", "antrenman", "kardiyo", "antrenman", "antrenman",
        "kardiyo", "dinlenme"]
    assert four[2]["odak"] == "Kardiyo · Yüzme · Yüksek yoğunluk"
    one = _program(canonical_document(parse_general_request(_cardio(
        kardiyo_gun=1)), "tr"))
    assert [day["tip"] for day in one].index("kardiyo") == 5  # Saturday


def test_cardio_documents_are_deterministic_and_language_only_relabels():
    request = parse_general_request(CARDIO_BODY)
    first = canonical_document(request, "tr")
    assert canonical_document(parse_general_request(dict(CARDIO_BODY)), "tr") == first
    for tr_day, en_day in zip(_program(first),
                              _program(canonical_document(request, "en"))):
        assert {**tr_day, "odak": None} == {**en_day, "odak": None}
    assert canonical_document(request, "de") == first


@pytest.mark.parametrize("body", [
    {key: value for key, value in CARDIO_BODY.items() if key != "kardiyo_sure"},
    _cardio(injuries="knee"),
    _cardio(kardiyo_tipi="yok"),
    _cardio(kardiyo_tipi="crossfit"),
    _cardio(kardiyo_tipi="KOSU"),
    _cardio(kardiyo_gun=0),
    _cardio(gun_sayisi=3, kardiyo_gun=4),  # 3 + 4 fills the week
    _cardio(gun_sayisi=4, kardiyo_gun=3),  # 4 + 3 fills the week
    _cardio(kardiyo_gun=True),
    _cardio(kardiyo_gun="2"),
    _cardio(kardiyo_sure=25),
    _cardio(kardiyo_sure=30.0),
    _cardio(kardiyo_yogunluk="extreme"),
    _cardio(kardiyo_yogunluk=None),
    _cardio(gun_sayisi=5),
    # A v1 body never carries cardio, and a cardio body never omits it.
    {**BODY, "kardiyo_tipi": "kosu"},
    {**BODY, "kardiyo_tipi": "kosu", "kardiyo_gun": 2, "kardiyo_sure": 30,
     "kardiyo_yogunluk": "orta"},
    _cardio(template="general_full_body_cardio_v2"),
])
def test_unsupported_cardio_inputs_are_refused_never_coerced(body):
    with pytest.raises(InvalidPlanRequest):
        parse_general_request(body)


def test_cardio_fingerprint_binds_every_field_and_keeps_v1_identity():
    import hashlib
    base = parse_general_request(CARDIO_BODY)
    assert base.fingerprint != parse_general_request(BODY).fingerprint
    for field, value in (("kardiyo_tipi", "bisiklet"), ("kardiyo_gun", 1),
                         ("kardiyo_sure", 45), ("kardiyo_yogunluk", "dusuk"),
                         ("gun_sayisi", 4), ("ekipman", "minimal")):
        assert parse_general_request(_cardio(**{field: value})).fingerprint \
            != base.fingerprint
    canonical = json.dumps(CARDIO_BODY, sort_keys=True,
                           separators=(",", ":")).encode()
    assert base.fingerprint == hashlib.sha256(
        b"axisai:training-plan-general:v1\0" + canonical).hexdigest()


def test_every_accepted_request_names_its_template_and_nothing_else_does():
    space = list(identity.request_space())
    # 6 strength-only + (3 + 2 cardio-day counts) * 3 equipment * 6 types
    # * 4 durations * 4 intensities.
    assert len(space) == 6 + 5 * 3 * 6 * 4 * 4
    for body in space:
        request = parse_general_request(body)
        assert identity.template_for_fingerprint(request.fingerprint) \
            == body["template"]
    assert identity.template_for_fingerprint("0" * 64) is None
    assert identity.template_for_fingerprint(None) is None


def test_identity_vocabularies_match_the_canonical_preference_contract():
    from app.services.training_generation import preference_contract as prefs
    assert identity.CARDIO_TYPES == prefs.CARDIO_TYPES - {"yok"}
    assert identity.CARDIO_DURATIONS == prefs.CARDIO_DURATIONS
    assert identity.CARDIO_INTENSITIES == prefs.CARDIO_INTENSITIES
    assert identity.STRENGTH_DAYS <= prefs.DAY_COUNTS
    assert identity.EQUIPMENT == prefs.EQUIPMENT_VALUES
    for days in identity.STRENGTH_DAYS:
        assert set(identity.cardio_day_choices(days)) <= prefs.CARDIO_DAYS
        assert days + max(identity.cardio_day_choices(days)) == 6
    # The cardio slots hold exactly the largest allowed cardio week.
    for days, slots in general_template._CARDIO_SLOTS.items():
        assert len(slots) == max(identity.cardio_day_choices(days))
        assert 6 not in slots
    assert set(general_template._CARDIO_EXERCISES) == identity.CARDIO_TYPES


def test_a_cardio_plan_is_persisted_with_a_truthful_receipt_and_origin(
        client, owner, as_mobile, no_ai):
    response = _post(client, as_mobile(owner, "cardio-key-0001"), CARDIO_BODY)

    assert response.status_code == 201, response.json
    body = response.json
    assert body["origin"] == CARDIO_ORIGIN
    assert [day["kind"] for day in body["plan"]["days"]] == [
        "training", "cardio", "training", "rest", "training", "cardio", "rest"]
    cardio_day = body["plan"]["days"][1]
    assert cardio_day["workout_ref"] is not None
    plan = TrainingPlan.query.filter_by(user_id=owner.id).one()
    assert plan.score is None
    assert plan.plan_data == canonical_document(
        parse_general_request(CARDIO_BODY), "tr")
    receipt = TrainingPlanGenerationOperation.query.filter_by(user_id=owner.id).one()
    assert is_general_receipt(receipt)
    assert receipt.request_fingerprint == parse_general_request(CARDIO_BODY).fingerprint

    current, today = _reads(client, owner, as_mobile, OPT_IN)
    assert current.json["plan"]["origin"] == CARDIO_ORIGIN
    assert today.json["today"]["plan"]["origin"] == CARDIO_ORIGIN
    assert no_ai == []


def test_cardio_idempotency_replays_and_conflicts_like_v1(
        client, owner, as_mobile, no_ai):
    first = _post(client, as_mobile(owner, "cardio-key-0002"), CARDIO_BODY)
    replay = _post(client, as_mobile(owner, "cardio-key-0002"), CARDIO_BODY)
    assert replay.status_code == 201
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json == first.json

    # Same key, another cardio intent, or the strength-only intent: conflict.
    for body in (_cardio(kardiyo_gun=1), BODY):
        conflict = _post(client, as_mobile(owner, "cardio-key-0002"), body)
        assert conflict.status_code == 409
        assert conflict.json["error"]["code"] == "TRAINING_PLAN_IDEMPOTENCY_CONFLICT"
    # A new key never adds a second plan.
    second = _post(client, as_mobile(owner, "cardio-key-0003"), CARDIO_BODY)
    assert second.status_code == 409
    assert second.json["error"]["code"] == "TRAINING_PLAN_REPLACEMENT_REFUSED"
    assert TrainingPlan.query.filter_by(user_id=owner.id).count() == 1
    assert no_ai == []


def test_cardio_owners_are_isolated_under_the_same_key(
        client, owner, stranger, as_mobile):
    mine = _post(client, as_mobile(owner, "cardio-key-0004"), CARDIO_BODY)
    theirs = _post(client, as_mobile(stranger, "cardio-key-0004"), BODY)
    assert mine.status_code == theirs.status_code == 201
    assert mine.json["origin"] == CARDIO_ORIGIN
    assert theirs.json["origin"] == GENERAL_ORIGIN
    assert mine.json["plan"]["plan_lineage"] != theirs.json["plan"]["plan_lineage"]


def test_a_general_receipt_with_an_unrecognized_fingerprint_is_unknown(
        client, owner, as_mobile):
    plan = _legacy_plan(owner.id)
    _receipt(owner.id, plan, "origin-key-09")  # general state, fingerprint 0*64

    _assert_origin(client, owner, as_mobile, UNKNOWN_ORIGIN)


@pytest.mark.parametrize("when,kind", [
    (MONDAY, "training"), (SATURDAY, "cardio"), (SUNDAY, "rest")])
def test_today_names_a_cardio_session_only_when_opted_in(
        client, owner, as_mobile, when, kind):
    assert _post(client, as_mobile(owner, "cardio-key-0005"),
                 CARDIO_BODY).status_code == 201

    with audit_clock(when):
        legacy = client.get(TODAY_PATH, headers=as_mobile(owner))
        origin_only = client.get(TODAY_PATH + OPT_IN, headers=as_mobile(owner))
        opted = client.get(TODAY_PATH + DAY_KIND, headers=as_mobile(owner))
        both = client.get(TODAY_PATH + OPT_IN + "&include_day_kind=1",
                          headers=as_mobile(owner))
    for response in (legacy, origin_only, opted, both):
        assert response.status_code == 200, response.json
    # Installed builds send include_origin=1 and read the summary exactly.
    assert "kind" not in legacy.json["today"]["workout"]["summary"]
    assert "kind" not in origin_only.json["today"]["workout"]["summary"]
    summary = opted.json["today"]["workout"]["summary"]
    assert summary["kind"] == kind
    assert {**summary, "kind": None} == {
        **legacy.json["today"]["workout"]["summary"], "kind": None}
    assert both.json["today"]["workout"]["summary"]["kind"] == kind
    assert both.json["today"]["plan"]["origin"] == CARDIO_ORIGIN
    # The rest authority is unchanged and agrees.
    assert opted.json["today"]["workout"]["is_rest_day"] is (kind == "rest")
    assert {key: value for key, value in opted.json["today"].items()
            if key not in {"workout", "server_time"}} == {
        key: value for key, value in legacy.json["today"].items()
        if key not in {"workout", "server_time"}}


def test_today_day_kind_without_a_plan_keeps_a_null_summary(
        client, owner, as_mobile):
    with audit_clock(MONDAY):
        response = client.get(TODAY_PATH + DAY_KIND, headers=as_mobile(owner))
    assert response.status_code == 200
    assert response.json["today"]["workout"]["summary"] is None


@pytest.mark.parametrize("query", [
    "?include_day_kind=0", "?include_day_kind=true", "?include_day_kind=",
    "?include_day_kind=1&include_day_kind=1"])
def test_a_malformed_day_kind_opt_in_is_refused_never_coerced(
        client, owner, as_mobile, query):
    with audit_clock(MONDAY):
        response = client.get(TODAY_PATH + query, headers=as_mobile(owner))
    assert response.status_code == 400
    assert response.json["error"]["code"] == "TODAY_INVALID_REQUEST"
    assert response.json["error"]["retryable"] is False


def test_the_day_kind_opt_in_costs_no_extra_ledger_read(
        client, owner, as_mobile):
    assert _post(client, as_mobile(owner, "cardio-key-0006"),
                 CARDIO_BODY).status_code == 201
    assert _ledger_statements(client, owner, as_mobile,
                              TODAY_PATH + DAY_KIND) == []
