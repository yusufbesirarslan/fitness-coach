"""G4 (#530): independent cardio sessions on strength days, P3A preserved.

Founder rulings under test:

* D1 — a day with several planned sessions is complete only when every one
  reached a terminal state; completion, skipping and incomplete stay
  distinguishable; single-session days and rewards keep their meaning.
* D2 — strength and cardio have independent identities and states; cardio
  has an optional explicit answer, no Start Workout, timer or set execution.
* D3 — forward-only at source level; nothing historical is rewritten.
* P3A — one placement rule for generation, save, editor and Coach; cardio
  never enters a strength list; equipment/movement gates are not bypassed;
  a legacy strength day is not locked; legacy clients see the legacy shape.
"""
import json
from datetime import datetime
from itertools import product
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import (
    PlanMutationRecord, TrainingPlan, TrainingSessionOutcome, User,
    WorkoutCompletionDay,
)
from app.services import (
    general_plan_identity as identity,
    mobile_auth,
    mobile_training,
)
from app.services.exercise_catalog import ExerciseContext
from app.services.general_training_plan import template as general_template
from app.services.general_training_plan.contract import parse_general_request
from app.services.general_training_plan.service import canonical_document
from app.services.mobile_training_generation import InvalidPlanRequest
from app.services.mobile_training_generation.contract import parse_native_request
from app.services.training_generation import capability
from app.services.training_generation.exercise_resolution import (
    canonicalize_plan_exercises,
)
from app.services.training_generation.output_errors import (
    GenerationExerciseIncompatibleError,
    SchemaInvalidError,
    SemanticInvalidError,
)
from app.services.training_generation.preference_contract import PreferenceContractError
from app.services.training_generation.plan_schema import (
    CARDIO_SESSION_KEY,
    WEEKDAYS,
    day_exercise_lists,
)
from app.services.training_generation.response_validator import (
    validate_plan_structure,
)
from app.services.training_generation.semantic_validator import (
    validate_plan_semantics,
)
from app.timeutil import APP_TZ, audit_clock

SQUAT = "ex_bodyweight_squat"
PLANK = "ex_plank"
RUN = "ex_outdoor_run"
WALK = "ex_brisk_walk"
SWIM = "ex_swimming"
MONDAY = datetime(2026, 11, 23, 15, 0, tzinfo=APP_TZ)
TUESDAY = datetime(2026, 11, 24, 15, 0, tzinfo=APP_TZ)
CURRENT = "/api/v1/training/plans/current"
EDITOR = "/api/v1/training/editor"
GENERAL = "/api/v1/training/plans/general"
SESSIONS = "/api/v1/training/sessions"


# ── builders ──────────────────────────────────────────────────────────────

def entry(exercise_id=SQUAT, sets=3, reps="8-12"):
    return {"exercise_id": exercise_id, "isim": "Stored name", "set": sets,
            "tekrar": reps, "dinlenme": "60 sn", "not": ""}


def cardio(exercise_id=RUN, minutes=20):
    return entry(exercise_id, 1, f"{minutes} dk")


def document(*, equipment="ev", cardio_type="kosu", session=True):
    """Mon: strength + cardio session; Tue: dedicated cardio; rest of the week rest."""
    program = []
    for slot, weekday in enumerate(WEEKDAYS):
        if slot == 0:
            day = {"gun": weekday, "tip": "antrenman", "odak": "Full body",
                   "sure_dk": 40, "tahmini_kalori": 0,
                   "egzersizler": [entry(SQUAT), entry(PLANK, 3, "30 sn")]}
            if session:
                day[CARDIO_SESSION_KEY] = {"egzersizler": [cardio()]}
        elif slot == 1:
            day = {"gun": weekday, "tip": "kardiyo", "odak": "Run", "sure_dk": 20,
                   "tahmini_kalori": 0, "egzersizler": [cardio()]}
        else:
            day = {"gun": weekday, "tip": "dinlenme", "odak": "Rest", "sure_dk": 0,
                   "tahmini_kalori": 0, "egzersizler": []}
        program.append(day)
    return {"exercise_context": {"equipment_context": equipment, "cardio_type": cardio_type,
                                 "style": "general_fitness", "catalog_version": 1},
            "program": program}


def bare(doc):
    return {"program": doc["program"]}


def save(user_id, doc):
    row = TrainingPlan(user_id=user_id, plan_data=json.dumps(doc), score=7)
    db.session.add(row)
    db.session.commit()
    return row.id


@pytest.fixture
def as_owner(monkeypatch):
    owners = {}

    def authenticate(raw):
        owner = db.session.get(User, owners[raw])
        return mobile_auth.MobilePrincipal(owner, SimpleNamespace(id=1), {"sub": owner.cognito_sub})

    monkeypatch.setattr(mobile_auth, "authenticate_access", authenticate)

    def headers(user, key=None):
        token = f"g4-credential-{user.id}"
        owners[token] = user.id
        result = {"Authorization": f"Bearer {token}"}
        if key:
            result["Idempotency-Key"] = key
        return result

    return headers


def get(client, path, headers, when=MONDAY, **query):
    with audit_clock(when):
        return client.get(path, headers=headers, query_string=query)


def post(client, path, headers, when=MONDAY, **kwargs):
    with audit_clock(when):
        return client.post(path, headers=headers, **kwargs)


# ── structure, semantics, capability ─────────────────────────────────────

def test_session_key_exists_only_for_an_opted_in_reader_or_writer():
    doc = bare(document())
    with pytest.raises(SchemaInvalidError, match="unknown keys"):
        validate_plan_structure(doc, allow_exercise_id=True)
    validated = validate_plan_structure(doc, allow_exercise_id=True, allow_sessions=True)
    assert validated["program"][0][CARDIO_SESSION_KEY]["egzersizler"][0]["tekrar"] == "20 dk"
    # Days without a session are byte-identical to the pre-G4 projection.
    assert all(CARDIO_SESSION_KEY not in day for day in validated["program"][1:])


@pytest.mark.parametrize("mutate, message", [
    (lambda day: day.update(tip="kardiyo"), "yalnız antrenman"),
    (lambda day: day[CARDIO_SESSION_KEY].update(sure_dk=20), "unknown keys"),
    (lambda day: day[CARDIO_SESSION_KEY].update(egzersizler=[]), "en az bir"),
    (lambda day: day[CARDIO_SESSION_KEY]["egzersizler"][0].update(set=2), "tek set"),
    (lambda day: day[CARDIO_SESSION_KEY]["egzersizler"][0].update(tekrar="20"), "tek set"),
    (lambda day: day[CARDIO_SESSION_KEY]["egzersizler"][0].update(tekrar="020 dk"), "tek set"),
    (lambda day: day[CARDIO_SESSION_KEY].update(egzersizler=[cardio(minutes=400)] * 2),
     "sınırı"),
    (lambda day: day.update({CARDIO_SESSION_KEY: []}), "object"),
])
def test_malformed_sessions_are_refused(mutate, message):
    doc = bare(document())
    mutate(doc["program"][0])
    with pytest.raises(SchemaInvalidError, match=message):
        validate_plan_structure(doc, allow_exercise_id=True, allow_sessions=True)


def _prefs(strength, cardio_days, cardio_type="kosu"):
    return SimpleNamespace(gun_sayisi=strength, kardiyo_tipi=cardio_type,
                           kardiyo_gun=cardio_days, kardiyo_sure=20, sure=45,
                           antrenman_tarzi="genel")


def _week(strength, cardio_days):
    """A valid week: dedicated cardio on free days first, then sessions."""
    days = [{"gun": weekday, "tip": "dinlenme", "odak": "Rest", "sure_dk": 0,
             "tahmini_kalori": 0, "egzersizler": []} for weekday in WEEKDAYS]
    for slot in range(strength):
        days[slot].update(tip="antrenman", sure_dk=45, egzersizler=[entry()])
    free = [slot for slot in range(7) if days[slot]["tip"] == "dinlenme"]
    for count in range(cardio_days):
        if free:
            days[free.pop(0)].update(tip="kardiyo", sure_dk=20, egzersizler=[cardio()])
        else:
            days[count - (7 - strength)][CARDIO_SESSION_KEY] = {"egzersizler": [cardio()]}
    return {"program": days}


@pytest.mark.parametrize("strength, cardio_days", list(product(range(3, 7), range(0, 7))))
def test_independent_frequency_matrix_is_representable_only_with_sessions(strength, cardio_days):
    prefs = _prefs(strength, cardio_days, "kosu" if cardio_days else "yok")
    plan = validate_plan_structure(_week(strength, cardio_days), allow_exercise_id=True,
                                   allow_sessions=True)
    assert validate_plan_semantics(plan, prefs) is plan
    sessions = sum(CARDIO_SESSION_KEY in day for day in plan["program"])
    assert sessions == max(0, strength + cardio_days - 7)
    if sessions:
        with pytest.raises(SchemaInvalidError):
            validate_plan_structure(_week(strength, cardio_days), allow_exercise_id=True)


def test_semantics_count_sessions_as_cardio_and_check_their_minutes():
    plan = validate_plan_structure(_week(6, 3), allow_exercise_id=True, allow_sessions=True)
    with pytest.raises(SemanticInvalidError, match="kardiyo günü 2"):
        validate_plan_semantics(plan, _prefs(6, 2))
    plan["program"][0][CARDIO_SESSION_KEY]["egzersizler"][0]["tekrar"] = "90 dk"
    with pytest.raises(SemanticInvalidError, match="kardiyo süresi"):
        validate_plan_semantics(plan, _prefs(6, 3))


def test_capability_keeps_the_seven_day_conflict_for_legacy_requests():
    prefs = SimpleNamespace(antrenman_tarzi="genel", ekipman="ev", gun_sayisi=5,
                            kardiyo_tipi="kosu", kardiyo_gun=4)
    assert capability.evaluate_capability(prefs).reason == "WEEK_ALLOCATION_EXCEEDS_SEVEN_DAYS"
    assert capability.evaluate_capability(prefs, sessions=True).ok
    legacy = capability.public_capability_constraints()
    opted = capability.public_capability_constraints(sessions=True)
    assert legacy[:-1] == opted
    assert legacy[-1]["reason"] == "WEEK_ALLOCATION_EXCEEDS_SEVEN_DAYS"


def test_native_request_identity_is_unchanged_without_the_opt_in():
    body = {"gun_sayisi": 5, "ekipman": "ev", "odak": "tum_vucut", "sure": 45,
            "kardiyo_tipi": "kosu", "kardiyo_gun": 2, "kardiyo_sure": 20,
            "kardiyo_yogunluk": "orta", "antrenman_tarzi": "genel",
            "odak_hedef": "genel", "injuries": ""}
    legacy = parse_native_request(body)
    opted = parse_native_request(body, sessions=True)
    assert legacy.sessions is False and opted.sessions is True
    assert legacy.normalized == opted.normalized
    assert legacy.fingerprint != opted.fingerprint
    with pytest.raises(PreferenceContractError):
        parse_native_request({**body, "kardiyo_gun": 4})
    assert parse_native_request({**body, "kardiyo_gun": 4}, sessions=True).sessions


# ── placement (P3A) ───────────────────────────────────────────────────────

def _context(cardio_type="kosu", equipment="ev"):
    return ExerciseContext(equipment_context=equipment, cardio_type=cardio_type,
                           style="general_fitness")


def _canonical(doc, **context):
    plan = validate_plan_structure(bare(doc), allow_exercise_id=True, allow_sessions=True)
    return canonicalize_plan_exercises(plan, _context(**context))


def test_session_holds_cardio_only_and_strength_list_never_holds_cardio():
    canonical = _canonical(document())
    assert canonical["program"][0][CARDIO_SESSION_KEY]["egzersizler"][0]["exercise_id"] == RUN
    assert canonical["program"][0][CARDIO_SESSION_KEY]["egzersizler"][0]["isim"] != "Stored name"

    strength_in_session = document()
    strength_in_session["program"][0][CARDIO_SESSION_KEY]["egzersizler"] = [entry(PLANK, 1, "5 dk")]
    with pytest.raises(GenerationExerciseIncompatibleError) as refused:
        _canonical(strength_in_session)
    assert refused.value.resolution_category == "cardio_placement"

    cardio_in_strength = document(session=False)
    cardio_in_strength["program"][0]["egzersizler"].append(cardio())
    with pytest.raises(GenerationExerciseIncompatibleError) as refused:
        _canonical(cardio_in_strength)
    assert refused.value.resolution_category == "cardio_placement"


def test_session_placement_does_not_open_an_equipment_or_cardio_type_bypass():
    # An "ev" plan whose cardio type is running cannot prescribe swimming,
    # neither in the strength list (placement) nor in a session (cardio type).
    swim_session = document()
    swim_session["program"][0][CARDIO_SESSION_KEY]["egzersizler"] = [cardio(SWIM)]
    with pytest.raises(GenerationExerciseIncompatibleError) as refused:
        _canonical(swim_session)
    assert refused.value.resolution_category == "equipment"
    swim_strength = document(session=False)
    swim_strength["program"][0]["egzersizler"].append(cardio(SWIM))
    with pytest.raises(GenerationExerciseIncompatibleError):
        _canonical(swim_strength, cardio_type="yuzme")
    # The declared cardio type is exactly what a session may hold.
    swim_session["program"][1]["egzersizler"] = [cardio(SWIM)]
    assert _canonical(swim_session, cardio_type="yuzme")["program"][0][CARDIO_SESSION_KEY]


def test_every_walker_sees_session_identities():
    lists = day_exercise_lists(document()["program"][0])
    assert [item["exercise_id"] for items in lists for item in items] == [SQUAT, PLANK, RUN]
    from app.services.private_exercises import program_exercise_ids
    assert list(program_exercise_ids(document()["program"]))[:3] == [SQUAT, PLANK, RUN]


# ── prompt (opt-in only) ──────────────────────────────────────────────────

def test_prompt_mentions_sessions_only_when_opted_in():
    from app.services.training_generation import prompt_builder
    from app.services.training_generation.exercise_choices import compatible_exercise_choices
    from app.services.training_generation.models import TrainingPreferences
    prefs = TrainingPreferences(gun_sayisi=4, ekipman="ev", odak="tum_vucut", sure=45,
                                kardiyo_tipi="kosu", kardiyo_gun=4, kardiyo_sure=20,
                                kardiyo_yogunluk="orta", antrenman_tarzi="genel",
                                odak_hedef="genel", injuries="")
    args = (SimpleNamespace(goal=None, self_reported_level=None, current_activity=None,
                            age=30, weight=70),
            prefs,
            SimpleNamespace(level="beginner", confidence=1, constraints_applied=(),
                            risk_flags=()),
            SimpleNamespace(recovery_capacity_factor=1, volume_guideline="v",
                            intensity_guideline="i", progression_guideline="p",
                            deload_guideline="d", style_directive="s",
                            movement_coverage=("squat",)))
    choices = compatible_exercise_choices(_context())
    legacy = prompt_builder.build_training_prompt(*args, exercise_choices=choices)
    opted = prompt_builder.build_training_prompt(*args, exercise_choices=choices, sessions=True)
    assert CARDIO_SESSION_KEY not in legacy
    assert 'SADECE tip="kardiyo" günlerine yazılsın' in legacy
    assert "4 antrenman günü + 4 kardiyo günü + -1 dinlenme" in legacy  # pre-G4 text, unchanged
    assert CARDIO_SESSION_KEY in opted and 'tekrar="20 dk"' in opted
    assert "ASLA bir antrenman gününün" in opted


# ── general template ─────────────────────────────────────────────────────

def test_general_pre_g4_combinations_keep_their_exact_content():
    for days, equipment, cardio_type, minutes, intensity, language in product(
            sorted(identity.STRENGTH_DAYS), sorted(identity.EQUIPMENT),
            sorted(identity.CARDIO_TYPES), sorted(identity.CARDIO_DURATIONS),
            sorted(identity.CARDIO_INTENSITIES), ("tr", "en")):
        for cardio_days in identity.cardio_day_choices(days):
            args = (days, equipment, cardio_type, cardio_days, minutes, intensity, language)
            assert (general_template.build_cardio_program(*args, sessions=True)
                    == general_template.build_cardio_program(*args))


@pytest.mark.parametrize("days", sorted(identity.STRENGTH_DAYS))
@pytest.mark.parametrize("cardio_days", range(1, 7))
def test_general_widened_counts_use_separate_sessions(days, cardio_days):
    strength = general_template.build_program(days, "ev", "tr")
    program = general_template.build_cardio_program(
        days, "ev", "kosu", cardio_days, 20, "orta", "tr", sessions=True)
    sessions = [day for day in program if CARDIO_SESSION_KEY in day]
    dedicated = [day for day in program if day["tip"] == "kardiyo"]
    assert len(sessions) + len(dedicated) == cardio_days
    assert sum(day["tip"] == "antrenman" for day in program) == days
    for before, after in zip(strength, program):
        if after["tip"] == "antrenman":
            # The strength session is exactly the strength template's.
            assert {k: v for k, v in after.items() if k != CARDIO_SESSION_KEY} == before
    request = parse_general_request(
        {"template": identity.TEMPLATE_CARDIO_V1, "gun_sayisi": days, "ekipman": "ev",
         "kardiyo_tipi": "kosu", "kardiyo_gun": cardio_days, "kardiyo_sure": 20,
         "kardiyo_yogunluk": "orta"}, sessions=True)
    stored = json.loads(canonical_document(request, "tr"))
    assert [CARDIO_SESSION_KEY in day for day in stored["program"]] == \
        [CARDIO_SESSION_KEY in day for day in program]
    if sessions:
        with pytest.raises(InvalidPlanRequest):
            parse_general_request(
                {"template": identity.TEMPLATE_CARDIO_V1, "gun_sayisi": days,
                 "ekipman": "ev", "kardiyo_tipi": "kosu", "kardiyo_gun": cardio_days,
                 "kardiyo_sure": 20, "kardiyo_yogunluk": "orta"})


# ── mobile projection: legacy vs opt-in ──────────────────────────────────

LEGACY_DAY_KEYS = {"slot", "weekday", "kind", "focus", "duration_minutes",
                   "estimated_calories", "workout_ref", "exercises"}


def test_legacy_shape_is_the_strength_session_only(raw_client, make_user, as_owner):
    owner = make_user("g4-legacy")
    save(owner.id, document())
    headers = as_owner(owner)
    plan = get(raw_client, CURRENT, headers).json["plan"]
    assert "today_progress" not in plan
    assert all(set(day) == LEGACY_DAY_KEYS for day in plan["days"])
    monday = plan["days"][0]
    assert [item["exercise_id"] for item in monday["exercises"]] == [SQUAT, PLANK]
    assert monday["kind"] == "training" and monday["duration_minutes"] == 40
    # The legacy Today summary agrees: strength only.
    today = get(raw_client, "/api/v1/today", headers).json
    assert json.dumps(today).count(RUN) == 0


def test_opt_in_shape_carries_the_separate_session(raw_client, make_user, as_owner):
    owner = make_user("g4-opt-in")
    save(owner.id, document())
    headers = as_owner(owner)
    plan = get(raw_client, CURRENT, headers, include_sessions="1").json["plan"]
    assert all(set(day) == LEGACY_DAY_KEYS | {"cardio_session"} for day in plan["days"])
    monday, tuesday = plan["days"][0], plan["days"][1]
    block = monday["cardio_session"]
    assert set(block) == {"workout_ref", "duration_minutes", "exercises"}
    assert block["duration_minutes"] == 20
    assert [item["exercise_id"] for item in block["exercises"]] == [RUN]
    assert [item["exercise_id"] for item in monday["exercises"]] == [SQUAT, PLANK]
    refs = {day["workout_ref"] for day in plan["days"] if day["workout_ref"]}
    assert block["workout_ref"] not in refs
    assert tuesday["cardio_session"] is None and tuesday["kind"] == "cardio"
    assert plan["today_progress"] == {
        "date": "2026-11-23", "status": "not_started", "sessions": [
            {"session": "day", "kind": "training", "workout_ref": monday["workout_ref"],
             "state": "incomplete"},
            {"session": "cardio_session", "kind": "cardio",
             "workout_ref": block["workout_ref"], "state": "incomplete"}]}
    # A session reference is not a workout: it can never start a strength
    # workout session or claim the day.
    detail = get(raw_client, f"/api/v1/training/workouts/{block['workout_ref']}", headers)
    assert detail.status_code == 409


@pytest.mark.parametrize("value", ["0", "true", "", "2"])
def test_malformed_opt_in_is_refused_not_coerced(raw_client, make_user, as_owner, value):
    owner = make_user("g4-bad-opt-in")
    save(owner.id, document())
    headers = as_owner(owner)
    for path in (CURRENT, EDITOR, "/api/v1/training/preferences"):
        response = get(raw_client, path, headers, include_sessions=value)
        assert response.status_code == 400, (path, response.json)
    with audit_clock(MONDAY):
        repeated = raw_client.get(f"{CURRENT}?include_sessions=1&include_sessions=1",
                                  headers=headers)
    assert repeated.status_code == 400


def test_preferences_contract_drops_the_seven_day_conflict_only_when_opted_in(
        raw_client, make_user, as_owner):
    headers = as_owner(make_user("g4-preferences"))
    legacy = get(raw_client, "/api/v1/training/preferences", headers).json
    opted = get(raw_client, "/api/v1/training/preferences", headers, include_sessions="1").json
    reasons = [item["reason"] for item in legacy["capability_constraints"]]
    assert "WEEK_ALLOCATION_EXCEEDS_SEVEN_DAYS" in reasons
    assert "WEEK_ALLOCATION_EXCEEDS_SEVEN_DAYS" not in [
        item["reason"] for item in opted["capability_constraints"]]
    assert {k: v for k, v in legacy.items() if k != "capability_constraints"} == \
        {k: v for k, v in opted.items() if k != "capability_constraints"}


def test_general_endpoint_generates_sessions_only_for_an_opted_in_client(
        raw_client, make_user, as_owner):
    body = {"template": identity.TEMPLATE_CARDIO_V1, "gun_sayisi": 4, "ekipman": "ev",
            "kardiyo_tipi": "kosu", "kardiyo_gun": 5, "kardiyo_sure": 20,
            "kardiyo_yogunluk": "orta"}
    owner = make_user("g4-general")
    refused = post(raw_client, GENERAL, as_owner(owner, "g4-general-legacy-1"), json=body)
    assert refused.status_code == 422, refused.json
    assert TrainingPlan.query.filter_by(user_id=owner.id).count() == 0
    created = post(raw_client, f"{GENERAL}?include_sessions=1",
                   as_owner(owner, "g4-general-opted-1"), json=body)
    assert created.status_code == 201, created.json
    days = created.json["plan"]["days"]
    assert sum(day["cardio_session"] is not None for day in days) == 2
    assert sum(day["kind"] == "cardio" for day in days) == 3
    stored = json.loads(TrainingPlan.query.filter_by(user_id=owner.id).one().plan_data)
    assert all(not any(item["exercise_id"] == RUN for item in day["egzersizler"])
               for day in stored["program"] if day["tip"] == "antrenman")


# ── D1 / D2: session outcomes ────────────────────────────────────────────

def _session_ref(client, headers, when=MONDAY, slot=0):
    plan = get(client, CURRENT, headers, when, include_sessions="1").json["plan"]
    return plan["days"][slot]["cardio_session"]["workout_ref"]


def _progress(client, headers, when=MONDAY):
    return get(client, CURRENT, headers, when, include_sessions="1").json["plan"]["today_progress"]


def test_cardio_completion_is_partial_progress_until_every_session_is_terminal(
        raw_client, make_user, as_owner):
    owner = make_user("g4-progress")
    owner_id = owner.id
    save(owner_id, document())
    headers = as_owner(owner)
    xp_before = (db.session.get(User, owner_id).weekly_xp,
                 db.session.get(User, owner_id).rank_points)
    ref = _session_ref(raw_client, headers)

    done = post(raw_client, f"{SESSIONS}/{ref}/complete", headers)
    assert done.status_code == 200, done.json
    assert done.json == {"contract_version": 1, "session": "cardio_session",
                         "workout_ref": ref, "state": "completed", "replayed": False}
    progress = _progress(raw_client, headers)
    assert progress["status"] == "partial"
    assert [item["state"] for item in progress["sessions"]] == ["incomplete", "completed"]
    # No day claim, no XP: rewards keep their meaning (contract-level only).
    assert WorkoutCompletionDay.query.filter_by(user_id=owner_id).count() == 0
    db.session.expire_all()
    assert (db.session.get(User, owner_id).weekly_xp,
            db.session.get(User, owner_id).rank_points) == xp_before

    db.session.add(WorkoutCompletionDay(user_id=owner_id, date_key="2026-11-23", proof="none"))
    db.session.commit()
    assert _progress(raw_client, headers)["status"] == "complete"


def test_skip_is_terminal_but_distinguishable(raw_client, make_user, as_owner):
    owner = make_user("g4-skip")
    owner_id = owner.id
    save(owner_id, document())
    headers = as_owner(owner)
    ref = _session_ref(raw_client, headers)
    assert post(raw_client, f"{SESSIONS}/{ref}/skip", headers).json["state"] == "skipped"
    assert _progress(raw_client, headers)["status"] == "partial"
    db.session.add(WorkoutCompletionDay(user_id=owner_id, date_key="2026-11-23", proof="none"))
    db.session.commit()
    progress = _progress(raw_client, headers)
    assert progress["status"] == "complete_with_skips"
    assert [item["state"] for item in progress["sessions"]] == ["completed", "skipped"]


def test_outcomes_are_idempotent_and_never_overwritten(raw_client, make_user, as_owner):
    owner = make_user("g4-idempotent")
    owner_id = owner.id
    save(owner_id, document())
    headers = as_owner(owner)
    ref = _session_ref(raw_client, headers)
    assert post(raw_client, f"{SESSIONS}/{ref}/complete", headers).json["replayed"] is False
    assert post(raw_client, f"{SESSIONS}/{ref}/complete", headers).json["replayed"] is True
    conflict = post(raw_client, f"{SESSIONS}/{ref}/skip", headers)
    assert conflict.status_code == 409
    assert conflict.json["error"]["code"] == "TRAINING_SESSION_OUTCOME_CONFLICT"
    row = TrainingSessionOutcome.query.filter_by(user_id=owner_id).one()
    assert (row.outcome, row.date_key, row.session_kind) == ("completed", "2026-11-23", "cardio")


def test_only_todays_session_of_the_current_plan_can_be_answered(
        raw_client, make_user, as_owner):
    owner = make_user("g4-refusals")
    other = SimpleNamespace(id=make_user("g4-other").id)
    plan_id = save(owner.id, document())
    save(other.id, document())
    headers = as_owner(owner)
    ref = _session_ref(raw_client, headers)
    day_ref = get(raw_client, CURRENT, headers).json["plan"]["days"][0]["workout_ref"]

    malformed = post(raw_client, f"{SESSIONS}/not-a-ref/complete", headers)
    assert malformed.json["error"]["code"] == "TRAINING_SESSION_NOT_FOUND"
    for stale_ref in (day_ref, _session_ref(raw_client, as_owner(other))):
        stale = post(raw_client, f"{SESSIONS}/{stale_ref}/complete", headers)
        assert stale.json["error"]["code"] == "TRAINING_SESSION_STALE", stale_ref
    tuesday = post(raw_client, f"{SESSIONS}/{ref}/complete", headers, when=TUESDAY)
    assert tuesday.json["error"]["code"] == "TRAINING_SESSION_NOT_TODAY"
    body = post(raw_client, f"{SESSIONS}/{ref}/complete", headers, json={"minutes": 20})
    assert body.json["error"]["code"] == "TRAINING_SESSION_INVALID_REQUEST"
    # A plan edit mints new references; the old one is stale, never rebound.
    plan = db.session.get(TrainingPlan, plan_id)
    plan.mutation_version += 1
    db.session.commit()
    edited = post(raw_client, f"{SESSIONS}/{ref}/complete", headers)
    assert edited.json["error"]["code"] == "TRAINING_SESSION_STALE"
    assert TrainingSessionOutcome.query.count() == 0


def test_session_answers_need_health_consent(raw_client, make_user, as_owner):
    owner = make_user("g4-no-consent", health_consent=False)
    save(owner.id, document())
    response = post(raw_client, f"{SESSIONS}/AAAAAAAAAAAAAAAAAAAAAAAA/complete", as_owner(owner))
    assert response.status_code == 403


def test_single_session_days_keep_the_existing_meaning(raw_client, make_user, as_owner):
    owner = make_user("g4-single")
    owner_id = owner.id
    save(owner_id, document(session=False))
    headers = as_owner(owner)
    progress = _progress(raw_client, headers)
    assert progress["status"] == "not_started" and len(progress["sessions"]) == 1
    db.session.add(WorkoutCompletionDay(user_id=owner_id, date_key="2026-11-23", proof="none"))
    db.session.commit()
    assert _progress(raw_client, headers)["status"] == "complete"
    assert _progress(raw_client, headers, when=datetime(2026, 11, 25, 12, tzinfo=APP_TZ)) is None


def test_erasure_and_purge_remove_session_outcomes(raw_client, make_user, as_owner):
    from app.cli import _purge_user
    from app.services import health_erasure
    owner = make_user("g4-erase")
    owner_id = owner.id
    save(owner_id, document())
    headers = as_owner(owner)
    ref = _session_ref(raw_client, headers)
    assert post(raw_client, f"{SESSIONS}/{ref}/complete", headers).status_code == 200
    health_erasure._erase_rows(db.session.get(User, owner_id), {"workouts"})
    db.session.commit()
    assert TrainingSessionOutcome.query.count() == 1  # held category is kept
    health_erasure._erase_rows(db.session.get(User, owner_id), set())
    db.session.commit()
    assert TrainingSessionOutcome.query.count() == 0
    save(owner_id, document())  # erasure removed the plan too
    ref = _session_ref(raw_client, headers)
    assert post(raw_client, f"{SESSIONS}/{ref}/skip", headers).status_code == 200
    _purge_user(db.session.get(User, owner_id))
    db.session.commit()
    assert TrainingSessionOutcome.query.count() == 0


# ── editor: separate addressing, P3A, no day lock ────────────────────────

def _binding(client, headers):
    plan = get(client, EDITOR, headers, include_sessions="1").json["plan"]
    return {key: plan[key] for key in ("plan_lineage", "mutation_version", "snapshot")}, plan


def _edit(client, headers, operation, binding, key, **fields):
    return post(client, f"{EDITOR}/{operation}", {**headers, "Idempotency-Key": key},
                json={"day_slot": 0, **fields, **binding})


def test_editor_addresses_the_cardio_session_separately(raw_client, make_user, as_owner):
    owner = make_user("g4-editor")
    plan_id = save(owner.id, document(cardio_type="karisik"))
    headers = as_owner(owner)

    binding, plan = _binding(raw_client, headers)
    assert plan["days"][0]["cardio_session"]["exercises"][0]["exercise_id"] == RUN
    updated = _edit(raw_client, headers, "prescription", binding, "g4-session-reps",
                    session="cardio", position=0, exercise_id=RUN, reps="30 dk")
    assert updated.status_code == 200, updated.json
    binding, _ = _binding(raw_client, headers)
    added = _edit(raw_client, headers, "add", binding, "g4-session-add",
                  session="cardio", exercise_id=WALK, sets=1, reps="10 dk")
    assert added.status_code == 200, added.json
    binding, plan = _binding(raw_client, headers)
    block = plan["days"][0]["cardio_session"]
    assert [(e["exercise_id"], e["reps"]) for e in block["exercises"]] == \
        [(RUN, "30 dk"), (WALK, "10 dk")]
    assert block["duration_minutes"] == 40
    # The strength list is untouched by session edits.
    assert [e["exercise_id"] for e in plan["days"][0]["exercises"]] == [SQUAT, PLANK]

    for key, operation, fields in (
        ("plank-into-session", "add", {"exercise_id": PLANK, "sets": 1, "reps": "5 dk"}),
        ("session-not-minutes", "prescription", {"position": 0, "exercise_id": RUN,
                                                 "reps": "12"}),
        ("session-two-rounds", "prescription", {"position": 0, "exercise_id": RUN, "sets": 2}),
        ("session-on-cardio-day", "add", {"day_slot": 1, "exercise_id": WALK, "sets": 1,
                                          "reps": "10 dk"}),
        ("session-on-rest-day", "add", {"day_slot": 3, "exercise_id": WALK, "sets": 1,
                                        "reps": "10 dk"}),
        ("unknown-session", "add", {"exercise_id": WALK, "sets": 1, "reps": "10 dk",
                                    "session": "yoga"}),
    ):
        fields.setdefault("session", "cardio")
        response = _edit(raw_client, headers, operation, binding, f"g4-refuse-{key}", **fields)
        assert response.status_code == 400, (key, response.json)

    for position, exercise_id, key in ((1, WALK, "walk"), (0, RUN, "run")):
        removed = _edit(raw_client, headers, "remove", binding, f"g4-session-remove-{key}",
                        session="cardio", position=position, exercise_id=exercise_id)
        assert removed.status_code == 200, removed.json
        binding, plan = _binding(raw_client, headers)
    assert plan["days"][0]["cardio_session"] is None
    stored = json.loads(db.session.get(TrainingPlan, plan_id).plan_data)
    assert CARDIO_SESSION_KEY not in stored["program"][0]
    assert PlanMutationRecord.query.count() == 4


def test_a_legacy_client_keeps_editing_the_strength_session(raw_client, make_user, as_owner):
    """No session selector = the day's own list. The session is preserved,
    never addressable by a legacy position, and never lost."""
    owner = make_user("g4-legacy-editor")
    plan_id = save(owner.id, document(cardio_type="karisik"))
    headers = as_owner(owner)
    legacy_plan = get(raw_client, EDITOR, headers).json["plan"]
    binding = {key: legacy_plan[key] for key in ("plan_lineage", "mutation_version", "snapshot")}
    assert "cardio_session" not in legacy_plan["days"][0]
    edited = _edit(raw_client, headers, "prescription", binding, "g4-legacy-strength",
                   position=1, exercise_id=PLANK, sets=4)
    assert edited.status_code == 200, edited.json
    stored = json.loads(db.session.get(TrainingPlan, plan_id).plan_data)
    assert stored["program"][0][CARDIO_SESSION_KEY] == document()["program"][0][CARDIO_SESSION_KEY]
    binding = {key: value for key, value in _binding(raw_client, headers)[0].items()}
    beyond = _edit(raw_client, headers, "remove", binding, "g4-legacy-beyond",
                   position=2, exercise_id=RUN)
    assert beyond.status_code == 404
    moved = _edit(raw_client, headers, "add", binding, "g4-legacy-run-into-strength",
                  exercise_id=RUN, sets=1, reps="20 dk")
    assert moved.status_code == 400


def test_session_commands_need_a_canonical_native_plan():
    from app.services.plan_mutation import AddExerciseCommand, InvalidMutation
    from app.services.plan_mutation.document import apply_command
    from app.services.plan_mutation.fingerprint import semantic_fingerprint
    legacy = bare(document())
    with pytest.raises(InvalidMutation):
        apply_command(legacy, AddExerciseCommand(day="Pazartesi", exercise="Koşu", sets=1,
                                                 reps="10 dk", session="cardio"))
    strength = AddExerciseCommand(day="Pazartesi", exercise="Plank", sets=1, reps="5")
    assert semantic_fingerprint(strength) == semantic_fingerprint(
        AddExerciseCommand(day="Pazartesi", exercise="Plank", sets=1, reps="5",
                           session="strength"))
    assert semantic_fingerprint(strength) != semantic_fingerprint(
        AddExerciseCommand(day="Pazartesi", exercise="Plank", sets=1, reps="5",
                           session="cardio"))


# ── Coach read ───────────────────────────────────────────────────────────

def test_coach_reads_the_session_as_its_own_block(make_user):
    from app.services import coach_privacy
    from app.services.coach_plan_tools.plan_read import (
        READ_PLAN_TOOL, read_current_training_plan,
    )
    owner = make_user("g4-coach")
    save(owner.id, document())
    result = read_current_training_plan(owner.id)
    monday = result["plan"]["days"][0]
    assert [e["exercise_id"] for e in monday["exercises"]] == [SQUAT, PLANK]
    assert monday["cardio_session"]["exercises"][0]["exercise_id"] == RUN
    projected = json.loads(coach_privacy.tool_result(
        READ_PLAN_TOOL, json.dumps(result), user_id=owner.id))
    session = projected["plan"]["days"][0]["cardio_session"]
    assert session["exercises"][0]["reps"] == "20 dk"
    assert "cardio_session" not in projected["plan"]["days"][1]


# ── migration ─────────────────────────────────────────────────────────────

def test_outcome_migration_is_expand_only_and_never_drops_recorded_outcomes():
    from pathlib import Path

    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    script = ScriptDirectory(str(Path(__file__).resolve().parents[1] / "migrations"))
    (head,) = script.get_heads()
    module = script.get_revision(head).module
    assert module.down_revision == "19cb998acd2f" and module.expand_contract == "expand"
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE "user" (id INTEGER PRIMARY KEY)'))
        conn.execute(sa.text('INSERT INTO "user" (id) VALUES (1)'))
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
            module.upgrade()  # verify-or-create: a model-created table is verified
            conn.execute(sa.text(
                "INSERT INTO training_session_outcome (user_id, date_key, session_kind, "
                "outcome, plan_lineage_id, mutation_version, created_at) VALUES "
                "(1, '2026-11-23', 'cardio', 'completed', 'lineage', 0, '2026-11-23')"))
            with pytest.raises(sa.exc.IntegrityError):
                with conn.begin_nested():
                    conn.execute(sa.text(
                        "INSERT INTO training_session_outcome (user_id, date_key, "
                        "session_kind, outcome, plan_lineage_id, mutation_version, "
                        "created_at) VALUES (1, '2026-11-23', 'cardio', 'skipped', "
                        "'lineage', 0, '2026-11-23')"))
            with pytest.raises(RuntimeError, match="lose recorded outcomes"):
                module.downgrade()
            conn.execute(sa.text("DELETE FROM training_session_outcome"))
            module.downgrade()
        assert not sa.inspect(conn).has_table("training_session_outcome")
    engine.dispose()
