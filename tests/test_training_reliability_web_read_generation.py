"""Production reliability regressions for the WEB Training read + generation paths.

Two independent production failures are pinned here.

CASE A/B — an existing persisted TrainingPlan stopped rendering. The canonical
plan-mutation writer (``plan_mutation.document._apply_add``) legitimately
appends an exercise carrying only ``isim``/``set``/``tekrar``; the bootstrap's
public projection (``workout_state.serialization._serialize_day``) demanded a
``str`` for ``dinlenme``/``not`` and raised on the resulting ``None``. One
``PlanSerializationError`` inside ``/training/bootstrap``'s fail-closed wrapper
turned into HTTP 500 ``bootstrap_unavailable``, which the client renders as
"Workout state unavailable" *instead of* the plan the user still owns.

CASE C/D — ``POST /training-plan`` with the plainest supported preferences
(3 days / gym / general / full body / 45 min / no cardio / no injury) failed
with ``TRAINING_PLAN_GENERATION_SCHEMA_INVALID``. Verified against the live
provider: 4/4 candidates placed exercises on a ``tip="dinlenme"`` day, which
``_validate_day`` rejects, while the prompt asked for a
"dinlenme/aktif toparlanma" day and never stated that a rest day must carry an
empty ``egzersizler``. ``SchemaInvalidError`` was additionally not repair
eligible, so the second already-budgeted completion was never spent.

CASE E — a failed save must never cost the user their current plan.
"""
import json

import pytest

from app.blueprints import training as training_bp
from app.extensions import db
from app.models import TrainingPlan
from app.services.training_generation.plan_schema import MAX_PROVIDER_COMPLETIONS
from app.services.workout_state.serialization import (
    PlanSerializationError,
    serialize_plan,
    serialize_today_plan,
)
from tests.test_training_routes import (  # canonical fixtures/helpers, one definition
    PLAN_JSON,
    _seven_day_program,
    plan_save_token,  # noqa: F401 - re-exported pytest fixture
    with_session,  # noqa: F401 - re-exported pytest fixture
)

WEEKDAYS = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]


def _mutated_program():
    """The EXACT structural class that reproduced production.

    Built by running the real canonical mutation writer, not by hand-writing
    what we think it emits — if that writer ever starts emitting the full
    exercise shape, this regression must follow it rather than pin a fiction.
    """
    from app.services.plan_mutation.commands import AddExerciseCommand
    from app.services.plan_mutation.document import apply_command

    document = {"program": _seven_day_program()}
    mutated, changed = apply_command(
        document, AddExerciseCommand(
            day="Pazartesi", exercise="Push-up", sets=3, reps="12"))
    assert changed, "the fixture must exercise a real applied mutation"
    return mutated


def _persist(user_id, document, score=8.0):
    plan = TrainingPlan(
        user_id=user_id,
        plan_data=json.dumps(document, ensure_ascii=False),
        score=score,
    )
    db.session.add(plan)
    db.session.commit()
    return plan


def _reread(plan_id):
    """Re-read the row from a fresh identity map.

    ``/training/bootstrap`` resets Flask-SQLAlchemy's scoped session inside
    ``coherent_read_snapshot()``, which detaches any instance held across the
    request — so proving the row is unchanged has to re-query it by id.
    """
    db.session.expire_all()
    return TrainingPlan.query.filter_by(id=plan_id).one().plan_data


# ---------------------------------------------------------------------------
# CASE A — an existing valid persisted plan stays renderable
# ---------------------------------------------------------------------------

def test_mutation_writer_still_omits_the_optional_exercise_text():
    """Pins the producer half of the incompatibility.

    If this ever fails the writer changed; the reader fix below is then still
    correct for already-persisted rows, but this test documents why it exists.
    """
    added = _mutated_program()["program"][0]["egzersizler"][-1]

    assert "dinlenme" not in added
    assert "not" not in added


def test_serialize_plan_reads_a_legitimately_mutated_plan():
    projected = serialize_plan(_mutated_program())

    added = projected["program"][0]["egzersizler"][-1]
    assert added["dinlenme"] == ""
    assert added["not"] == ""
    # The narrow compatibility boundary must not become a general escape hatch.
    assert added["set"] == 3
    assert added["tekrar"] == "12"


def test_serialize_today_plan_reads_a_legitimately_mutated_plan():
    from datetime import date

    monday = date(2026, 7, 27)
    assert monday.weekday() == 0
    today = serialize_today_plan(_mutated_program(), monday)

    assert today["gun"] == "Pazartesi"
    assert today["egzersizler"][-1]["dinlenme"] == ""


def test_bootstrap_serves_the_plan_after_a_legitimate_mutation(client, auth_user):
    plan = _persist(auth_user.id, _mutated_program())
    plan_id, before = plan.id, plan.plan_data

    response = client.get("/training/bootstrap")

    assert response.status_code == 200
    body = response.get_json()
    assert body.get("code") != "bootstrap_unavailable"
    assert body["plan"]["exists"] is True
    assert len(body["plan"]["plan"]["program"]) == 7
    assert [day["gun"] for day in body["plan"]["plan"]["program"]] == WEEKDAYS
    # The read is a read: the row is byte-identical afterwards.
    assert _reread(plan_id) == before


def test_bootstrap_read_performs_no_write(client, auth_user):
    """The plan disappearing must never be 'fixed' by repairing user data."""
    _persist(auth_user.id, _mutated_program())
    writes = []

    from sqlalchemy import event

    def _record(session, flush_context, instances):
        # Scoped to the plan: unrelated request hooks (streak/last-seen) write
        # User/UserSession on every authenticated request and are not this
        # read path.
        writes.extend(
            obj for bucket in (session.new, session.dirty, session.deleted)
            for obj in bucket if isinstance(obj, TrainingPlan)
        )

    event.listen(db.session, "before_flush", _record)
    try:
        assert client.get("/training/bootstrap").status_code == 200
    finally:
        event.remove(db.session, "before_flush", _record)

    assert writes == []


def test_training_page_renders_the_existing_plan(client, auth_user):
    _persist(auth_user.id, _mutated_program())

    response = client.get("/training")

    assert response.status_code == 200


def test_genuinely_unreadable_schedule_still_blocks_the_action(client, auth_user):
    """Fail-closed action semantics are preserved.

    An unrecognized ``tip`` is genuinely unrepresentable content, and Sprint 7
    PR4 deliberately fails the whole snapshot closed rather than leak a partial
    or misleading plan (see test_workout_convergence.py). This fix must NOT
    relax that: only an ABSENT optional field becomes readable.
    """
    broken = {"program": [dict(day, tip="brunch") for day in _seven_day_program()]}
    _persist(auth_user.id, broken)

    response = client.get("/training/bootstrap")

    assert response.status_code == 500
    assert response.get_json()["code"] == "bootstrap_unavailable"
    assert "brunch" not in response.get_data(as_text=True)


def test_corrupt_plan_is_not_fabricated_into_content(client, auth_user):
    """A genuinely invalid plan keeps the safe unavailable state; the reader
    never invents days, never invents a rest day, never rewrites the row."""
    plan = _persist(auth_user.id, {"program": [{"gun": "Pazartesi"}]})
    plan_id, before = plan.id, plan.plan_data

    body = client.get("/training/bootstrap").get_json()

    # A wrong-length program is a normal unavailable domain state, not content.
    assert body["plan"]["plan"] == {"program": []}
    assert body["today_plan"] is None
    assert _reread(plan_id) == before


def test_exercise_text_type_and_bounds_are_still_enforced():
    """Only ABSENT optional text is normalized. A present wrong-typed or
    over-long value is still a refusal — the projection is not weakened."""
    document = {"program": _seven_day_program()}
    document["program"][0]["egzersizler"][0]["dinlenme"] = 90
    with pytest.raises(PlanSerializationError):
        serialize_plan(document)

    document = {"program": _seven_day_program()}
    document["program"][0]["egzersizler"][0]["not"] = "x" * 241
    with pytest.raises(PlanSerializationError):
        serialize_plan(document)

    document = {"program": _seven_day_program()}
    del document["program"][0]["egzersizler"][0]["isim"]
    with pytest.raises(PlanSerializationError):
        serialize_plan(document)

    document = {"program": _seven_day_program()}
    del document["program"][0]["odak"]
    with pytest.raises(PlanSerializationError):
        serialize_plan(document)


# ---------------------------------------------------------------------------
# CASE B — a freshly canonical plan does not regress
# ---------------------------------------------------------------------------

def test_freshly_saved_canonical_plan_stays_reader_compatible(
        client, auth_user, plan_save_token):  # noqa: F811
    token = plan_save_token(auth_user.id)
    saved = client.post("/training-plan/save", json={
        "plan": _seven_day_program(), "score": 7.0,
        "exercise_context_token": token,
    })
    assert saved.status_code == 200

    body = client.get("/training/bootstrap").get_json()

    assert body["plan"]["exists"] is True
    program = body["plan"]["plan"]["program"]
    assert [day["gun"] for day in program] == WEEKDAYS
    for day in program:
        for exercise in day["egzersizler"]:
            assert isinstance(exercise["dinlenme"], str) and exercise["dinlenme"]
            assert isinstance(exercise["not"], str)


# ---------------------------------------------------------------------------
# CASE C — the basic supported generation works
# ---------------------------------------------------------------------------

def _rest_day_with_exercises(program):
    """The exact production provider defect: content on a ``dinlenme`` day."""
    broken = json.loads(json.dumps(program))
    for day in broken:
        if day["tip"] == "dinlenme":
            day["egzersizler"] = [{
                "isim": "Yürüyüş", "set": 1, "tekrar": "20 dk",
                "dinlenme": "—", "not": "aktif toparlanma",
            }]
            break
    return broken


def test_basic_supported_preferences_generate_a_valid_plan(
        client, with_session, monkeypatch):  # noqa: F811
    monkeypatch.setattr(
        training_bp, "_heavy_chat",
        lambda **kwargs: json.dumps(PLAN_JSON, ensure_ascii=False))

    response = client.post("/training-plan", json={})

    assert response.status_code == 200
    body = response.get_json()
    assert [day["gun"] for day in body["program"]] == WEEKDAYS
    assert len(body["program"]) == 7
    assert body["exercise_context_token"]
    for day in body["program"]:
        for exercise in day["egzersizler"]:
            assert exercise["exercise_id"]  # resolved through catalog authority


def test_generation_does_not_touch_the_existing_plan(
        client, with_session, monkeypatch):  # noqa: F811
    plan = _persist(with_session.id, {"program": _seven_day_program()})
    plan_id, before = plan.id, plan.plan_data
    monkeypatch.setattr(
        training_bp, "_heavy_chat",
        lambda **kwargs: json.dumps(PLAN_JSON, ensure_ascii=False))

    assert client.post("/training-plan", json={}).status_code == 200

    assert TrainingPlan.query.filter_by(user_id=with_session.id).count() == 1
    assert _reread(plan_id) == before


def test_prompt_states_the_rest_day_rule_it_is_judged_by(
        client, with_session, monkeypatch):  # noqa: F811
    """The provider was being asked for 'aktif toparlanma' days and then
    refused for putting activity on them. The rule must be in the prompt."""
    captured = {}

    def fake_chat(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return json.dumps(PLAN_JSON, ensure_ascii=False)

    monkeypatch.setattr(training_bp, "_heavy_chat", fake_chat)
    client.post("/training-plan", json={})

    prompt = captured["prompt"]
    assert '"egzersizler": []' in prompt or '"egzersizler":[]' in prompt
    assert "dinlenme" in prompt


# ---------------------------------------------------------------------------
# CASE D — the exact provider defect is recovered, or fails closed
# ---------------------------------------------------------------------------

def test_rest_day_exercise_defect_is_repaired_within_budget(
        client, with_session, monkeypatch):  # noqa: F811
    """First completion reproduces production; the bounded repair recovers."""
    broken = dict(PLAN_JSON, program=_rest_day_with_exercises(PLAN_JSON["program"]))
    calls = []

    def fake_chat(**kwargs):
        calls.append(kwargs["messages"][0]["content"])
        if len(calls) == 1:
            return json.dumps(broken, ensure_ascii=False)
        return json.dumps(PLAN_JSON, ensure_ascii=False)

    monkeypatch.setattr(training_bp, "_heavy_chat", fake_chat)

    response = client.post("/training-plan", json={})

    assert response.status_code == 200
    body = response.get_json()
    assert len(calls) == 2 <= MAX_PROVIDER_COMPLETIONS
    # Full canonical validation re-ran after repair.
    assert [day["gun"] for day in body["program"]] == WEEKDAYS
    assert all(day["egzersizler"] == []
               for day in body["program"] if day["tip"] == "dinlenme")
    assert body["exercise_context_token"]


def test_repair_prompt_never_invents_user_preferences(
        client, with_session, monkeypatch):  # noqa: F811
    broken = dict(PLAN_JSON, program=_rest_day_with_exercises(PLAN_JSON["program"]))
    calls = []

    def fake_chat(**kwargs):
        calls.append(kwargs["messages"][0]["content"])
        if len(calls) == 1:
            return json.dumps(broken, ensure_ascii=False)
        return json.dumps(PLAN_JSON, ensure_ascii=False)

    monkeypatch.setattr(training_bp, "_heavy_chat", fake_chat)
    client.post("/training-plan", json={})

    # The repair turn is the original accepted command plus a schema
    # instruction — never a different, invented request.
    assert calls[1].startswith(calls[0])


def test_unrecoverable_schema_defect_fails_closed_and_keeps_the_plan(
        client, with_session, monkeypatch):  # noqa: F811
    plan = _persist(with_session.id, {"program": _seven_day_program()})
    plan_id, before = plan.id, plan.plan_data
    broken = dict(PLAN_JSON, program=_rest_day_with_exercises(PLAN_JSON["program"]))
    calls = []

    def fake_chat(**kwargs):
        calls.append(1)
        return json.dumps(broken, ensure_ascii=False)

    monkeypatch.setattr(training_bp, "_heavy_chat", fake_chat)

    response = client.post("/training-plan", json={})

    assert response.status_code == 500
    assert response.get_json()["code"] == "TRAINING_PLAN_GENERATION_SCHEMA_INVALID"
    # Bounded: exactly the existing budget, no open-ended retry loop.
    assert len(calls) == MAX_PROVIDER_COMPLETIONS == 2
    assert TrainingPlan.query.filter_by(user_id=with_session.id).count() == 1
    assert _reread(plan_id) == before


def test_semantic_violation_is_still_terminal(
        client, with_session, monkeypatch):  # noqa: F811
    """Repair covers provider FORMATTING only. A candidate that contradicts the
    accepted command is a different answer and must not be re-rolled."""
    wrong_day_count = json.loads(json.dumps(PLAN_JSON))
    for day in wrong_day_count["program"]:
        if day["tip"] == "dinlenme":
            day["tip"] = "antrenman"
            day["odak"] = "Full Body"
            day["sure_dk"] = 45
            day["tahmini_kalori"] = 300
            day["egzersizler"] = [{
                "isim": "Goblet Squat", "set": 3, "tekrar": "8-12",
                "dinlenme": "90 sn", "not": "RPE 7"}]
    calls = []

    def fake_chat(**kwargs):
        calls.append(1)
        return json.dumps(wrong_day_count, ensure_ascii=False)

    monkeypatch.setattr(training_bp, "_heavy_chat", fake_chat)

    response = client.post("/training-plan", json={})

    assert response.status_code == 500
    assert response.get_json()["code"] == "TRAINING_PLAN_GENERATION_SEMANTICALLY_INVALID"
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# CASE E — save safety
# ---------------------------------------------------------------------------

def test_failed_save_validation_keeps_the_existing_plan(
        client, auth_user, plan_save_token):  # noqa: F811
    plan = _persist(auth_user.id, {"program": _seven_day_program()})
    plan_id, before = plan.id, plan.plan_data
    token = plan_save_token(auth_user.id)
    invalid = _seven_day_program()[:6]  # six days is not a weekly program

    response = client.post("/training-plan/save", json={
        "plan": invalid, "score": 7.0, "exercise_context_token": token,
    })

    assert response.status_code == 422
    db.session.expire_all()
    rows = TrainingPlan.query.filter_by(user_id=auth_user.id).all()
    assert len(rows) == 1
    assert rows[0].id == plan_id
    assert rows[0].plan_data == before


def test_failed_save_context_check_keeps_the_existing_plan(client, auth_user):
    plan = _persist(auth_user.id, {"program": _seven_day_program()})
    before = plan.plan_data
    assert plan.id

    response = client.post("/training-plan/save", json={
        "plan": _seven_day_program(), "score": 7.0,
        "exercise_context_token": "forged.token.value",
    })

    assert response.status_code == 422
    db.session.expire_all()
    rows = TrainingPlan.query.filter_by(user_id=auth_user.id).all()
    assert len(rows) == 1
    assert rows[0].plan_data == before
