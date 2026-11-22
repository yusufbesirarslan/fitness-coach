"""Private picker, canonical plan edits, and owner-scoped read convergence."""

import json

import pytest

from app.cli import _purge_user
from app.extensions import db
from app.models import PrivateExercise, TrainingPlan, WorkoutLog, WorkoutSession
from app.services import coach_privacy, health_erasure, mobile_today
from app.services.coach_plan_tools.plan_read import READ_PLAN_TOOL, read_current_training_plan
from app.services.exercise_catalog import ExerciseContext, ExerciseUnknown, compatible_exercises
from app.services.exercise_performance_history import build_exercise_history
from app.services.workout_session.prescription import capture
from app.services.workout_session.queries import planned_workout_for_slot
from tests.test_mobile_training_editor import (
    CATALOG, CURRENT, EDITOR, PLANK, RUN, SQUAT, as_owner, durable_state, exercise, mutate,
    program, revision, save_plan,
)


PRIVATE = "/api/v1/training/private-exercises"
BODY = {
    "display_name": "Zercher split squat", "equipment": ["barbell", "rack"],
    "movement": "lunge", "target_region": "hamstrings",
}


def register(client, headers, **overrides):
    with client.post(PRIVATE, headers=headers, json={**BODY, **overrides}) as response:
        return response.status_code, response.json


@pytest.mark.parametrize("method", ["get", "post"])
def test_private_routes_require_authentication_and_health_consent(
    raw_client, make_user, as_owner, method,
):
    call = getattr(raw_client, method)
    assert call(PRIVATE).status_code == 401
    headers = as_owner(make_user("private-no-consent", health_consent=False))
    assert call(PRIVATE, headers=headers).status_code == 403


@pytest.mark.parametrize("overrides", [
    {"display_name": "   "}, {"display_name": "---"}, {"display_name": "x" * 121},
    {"display_name": "bad\u202ename"}, {"display_name": "<script>"},
    {"equipment": []}, {"equipment": ["rack", "rack"]}, {"equipment": [{}]},
    {"equipment": ["unknown"]}, {"movement": []}, {"primary_region": "lower_body"},
    {"target_region": "lower_body"}, {"target_region": []}, {"target_region": "cardio"},
    {"target_region": "mobility"}, {"movement": "cardio"},
    {"movement": "cardio", "target_region": "cardio"}, {"user_id": 999},
    {"exercise_id": SQUAT},
])
def test_rejects_invalid_registration_without_writing(raw_client, make_user, as_owner, overrides):
    headers = as_owner(make_user("private-invalid"))
    status, payload = register(raw_client, headers, **overrides)
    assert status == 400
    assert payload["error"]["code"] == "TRAINING_PRIVATE_EXERCISE_INVALID"
    assert PrivateExercise.query.count() == 0


def test_duplicate_scope_private_search_and_closed_generation(raw_client, make_user, as_owner):
    first = as_owner(make_user("private-first"))
    other = as_owner(make_user("private-other"))
    status, original = register(raw_client, first)
    assert status == 201
    assert register(raw_client, first, display_name="  ZERCHER  split squat ",
                    equipment=["rack", "barbell"]) == (200, original)
    status, conflict = register(raw_client, first, equipment=["bodyweight"])
    assert status == 409
    assert conflict["error"]["code"] == "TRAINING_PRIVATE_EXERCISE_CONFLICT"
    assert register(raw_client, first, target_region="glutes")[0] == 409
    with raw_client.get(PRIVATE, headers=other) as response:
        assert response.json["exercises"] == []
    status, second = register(raw_client, other)
    assert status == 201
    item = original["exercise"]
    assert item["exercise_id"] != second["exercise"]["exercise_id"]
    with raw_client.get(PRIVATE, headers=first, query_string={"q": "SPLIT SQUAT"}) as response:
        assert response.json == {"contract_version": 2, "exercises": [item], "next_cursor": None}
        assert response.cache_control.no_store
    for query in ({"q": "%"}, {"q": "_"}, {"q": "other"}):
        with raw_client.get(PRIVATE, headers=first, query_string=query) as response:
            assert response.json["exercises"] == []
    assert raw_client.get(f"{PRIVATE}?q=a&q=b", headers=first).status_code == 400
    assert raw_client.get(f"{PRIVATE}?after={SQUAT}", headers=first).status_code == 400
    curated = raw_client.get(CATALOG, headers=first, query_string={"q": "Zercher"}).json["exercises"]
    assert [exercise["exercise_id"] for exercise in curated] == ["ex_barbell_zercher_squat"]
    assert item["exercise_id"] not in {
        choice.exercise_id for choice in compatible_exercises(ExerciseContext("spor_salonu"))
    }


@pytest.mark.parametrize(("target_region", "primary_region", "movement", "equipment"), [
    ("chest", "chest", "horizontal_push", "bodyweight"),
    ("back", "back", "hinge", "barbell"),
    ("shoulders", "shoulders", "vertical_push", "dumbbell"),
    ("biceps", "arms", "curl", "dumbbell"),
    ("triceps", "arms", "horizontal_push", "barbell"),
    ("quads", "lower_body", "squat", "barbell"),
    ("hamstrings", "lower_body", "hinge", "barbell"),
    ("glutes", "lower_body", "hinge", "barbell"),
    ("calves", "calves", "calf_raise", "bodyweight"),
    ("core", "core", "anti_rotation", "cable"),
    ("full_body", "full_body", "carry", "dumbbell"),
    ("cardio", "cardio", "cardio", "outdoor_walking"),
    ("mobility", "mobility", "mobility", "bodyweight"),
])
def test_registration_preserves_selected_region_and_derives_legacy_region(
    raw_client, make_user, as_owner, target_region, primary_region, movement, equipment,
):
    headers = as_owner(make_user("private-region"))
    status, payload = register(raw_client, headers, target_region=target_region,
                               movement=movement, equipment=[equipment])
    assert status == 201
    assert payload["contract_version"] == 2
    item = payload["exercise"]
    assert (item["target_region"], item["primary_region"]) == (target_region, primary_region)
    row = db.session.get(PrivateExercise, item["exercise_id"])
    assert (row.target_region, row.primary_region) == (target_region, primary_region)


def test_registration_recovers_after_response_loss_without_key_binding(
    raw_client, make_user, as_owner, monkeypatch,
):
    from sqlalchemy.exc import SQLAlchemyError

    headers = {**as_owner(make_user("private-uncertain")), "Idempotency-Key": "ignored"}
    commit = db.session.commit

    def commit_without_response():
        commit()
        raise SQLAlchemyError("synthetic loss after commit")

    with monkeypatch.context() as patch:
        patch.setattr(db.session, "commit", commit_without_response)
        assert register(raw_client, headers)[0] == 503

    stored, = raw_client.get(PRIVATE, headers=headers).json["exercises"]
    assert register(raw_client, headers) == (200, {"contract_version": 2, "exercise": stored})
    assert register(raw_client, headers, target_region="glutes")[0] == 409
    status, payload = register(raw_client, headers, display_name="A new intent")
    assert status == 201
    assert payload["exercise"]["exercise_id"] != stored["exercise_id"]


def test_picker_pages_repeatably_without_crossing_owners(raw_client, make_user, as_owner, monkeypatch):
    from app.services import private_exercises

    monkeypatch.setattr(private_exercises, "PAGE_SIZE", 2)
    headers = as_owner(make_user("private-pages"))
    other = as_owner(make_user("private-other-page"))
    identities = sorted(register(raw_client, headers, display_name=f"Zercher {i}")[1]
                        ["exercise"]["exercise_id"] for i in range(3))
    register(raw_client, other)
    with raw_client.get(PRIVATE, headers=headers) as first:
        cursor = first.json["next_cursor"]
        assert cursor == identities[1]
        assert [row["exercise_id"] for row in first.json["exercises"]] == identities[:2]
    with raw_client.get(PRIVATE, headers=headers, query_string={"after": cursor}) as second:
        assert second.json["next_cursor"] is None
        assert [row["exercise_id"] for row in second.json["exercises"]] == identities[2:]


@pytest.mark.parametrize(("saved_reps", "coach_reps"), [
    ("8–12", "8-12"), ("30 sn", "30 sn"), ("stop when it burns", None),
])
def test_add_replace_undo_and_resolve_in_fresh_plan_today_workout_and_coach(
    app, raw_client, make_user, as_owner, monkeypatch, saved_reps, coach_reps,
):
    from datetime import date

    owner = make_user("private-plan")
    owner_id = owner.id
    other = make_user("private-plan-foreign")
    other_id = other.id
    headers, other_headers = as_owner(owner), as_owner(other)
    document = program()
    document["program"][0]["egzersizler"][1] = exercise(PLANK)
    plan_id = save_plan(owner_id, document)
    save_plan(other_id, program())
    identity = register(raw_client, headers)[1]["exercise"]["exercise_id"]
    assert app.config.get("AI_COACH_PLAN_MUTATION_TOOLS_ENABLED", False) is False
    foreign_binding = revision(raw_client, other_headers)
    foreign_fields = {"exercise_id": identity, "sets": 3, "reps": saved_reps}
    foreign = mutate(raw_client, other_headers, "add", foreign_binding, "private-foreign", **foreign_fields)
    unknown = mutate(raw_client, other_headers, "add", foreign_binding, "private-unknown",
                     **{**foreign_fields, "exercise_id": "ex_private_" + "0" * 32})
    assert foreign.status_code == unknown.status_code == 400
    assert foreign.json["error"]["code"] == unknown.json["error"]["code"]
    base = revision(raw_client, headers)
    assert mutate(raw_client, headers, "add", base, "private-add", **foreign_fields).status_code == 200
    newer = revision(raw_client, headers)
    assert mutate(raw_client, headers, "add", newer, "private-duplicate", **foreign_fields).json[
        "outcome"] == "no_op"
    assert mutate(raw_client, headers, "prescription", base, "private-stale", position=2,
                  exercise_id=identity, sets=4).json["error"]["code"] == "TRAINING_EDITOR_STALE_PLAN"

    fresh = app.test_client()
    monkeypatch.setattr(mobile_today, "app_today", lambda: date(2026, 11, 23))
    with fresh.get("/api/v1/today", headers=headers) as today:
        assert today.status_code == 200, today.json
        assert today.json["today"]["workout"]["summary"]["exercise_count"] == 3
    with fresh.get(CURRENT, headers=headers) as current:
        day = current.json["plan"]["days"][0]
        assert day["exercises"][2]["exercise_id"] == identity
        assert day["exercises"][2]["display_name"] == BODY["display_name"]
        assert day["exercises"][2]["sets"] == 3
        assert day["exercises"][2]["reps"] == saved_reps
        assert "target_region" not in day["exercises"][2]
    with fresh.get(f"/api/v1/training/workouts/{day['workout_ref']}", headers=headers) as workout:
        assert workout.json["workout"]["exercises"][2] == day["exercises"][2]
    row = db.session.get(TrainingPlan, plan_id)
    stored = row.plan_data
    entries = json.loads(stored)["program"][0]["egzersizler"]
    assert planned_workout_for_slot(owner_id, "Pazartesi").exercise_ids == (SQUAT, PLANK, identity)
    assert json.loads(capture(row, entries))["exercises"][2]["exercise_id"] == identity

    def coach(owner):
        result = read_current_training_plan(owner_id)
        projected = coach_privacy.tool_result(READ_PLAN_TOOL, json.dumps(result), user_id=owner)
        assert identity not in projected
        return json.loads(projected)["plan"]["days"][0]["exercises"][2]

    assert read_current_training_plan(owner_id)["plan"]["days"][0]["exercises"][2]["reps"] == saved_reps
    assert coach(owner_id) == {
        "name": BODY["display_name"], "equipment": BODY["equipment"], "movement": BODY["movement"],
        "primary_region": "lower_body", "target_region": BODY["target_region"], "sets": 3, "reps": coach_reps,
        "position": 2, "user_defined": True,
    }
    assert coach(other_id) == {"name": None, "sets": 3, "reps": coach_reps}
    assert coach(None)["name"] is None
    edited = mutate(fresh, headers, "prescription", newer, "private-prescription", position=2,
                    exercise_id=identity, sets=4)
    assert edited.status_code == 200, edited.json
    assert coach(owner_id)["sets"] == 4

    before_replace = db.session.get(TrainingPlan, plan_id).plan_data
    replaced = mutate(fresh, headers, "replace", revision(fresh, headers), "private-replace",
                       position=2, exercise_id=identity, replacement_id="ex_cable_face_pull")
    assert replaced.status_code == 200, replaced.json
    with fresh.post(f"{EDITOR}/undo", headers={**headers, "Idempotency-Key": "private-undo"},
                    json={**revision(fresh, headers), "mutation_id": replaced.json["mutation_id"]}) as undo:
        assert undo.status_code == 200, undo.json
    assert db.session.get(TrainingPlan, plan_id).plan_data == before_replace
    assert coach(owner_id)["sets"] == 4
    assert coach(owner_id)["target_region"] == BODY["target_region"]
    assert build_exercise_history(owner_id, identity).exercise_id == identity
    with pytest.raises(ExerciseUnknown):
        build_exercise_history(other_id, identity)
    assert WorkoutLog.query.count() == WorkoutSession.query.count() == 0

    from app.timeutil import app_today
    from tests.exercise_history_support import candidate, persist, straight

    removed = mutate(fresh, headers, "remove", revision(fresh, headers), "private-history-remove",
                     position=2, exercise_id=identity)
    assert removed.status_code == 200
    persist(owner_id, candidate("h" * 24, on=app_today(), exercises=[(identity, straight(30, 6))]))
    assert build_exercise_history(owner_id, identity).occurrences


@pytest.mark.parametrize(("saved", "projected"), [
    ("8-12", "8-12"), ("8‐12", "8-12"), ("8‑12", "8-12"), ("8‒12", "8-12"),
    ("8–12", "8-12"), ("8—12", "8-12"), ("8―12", "8-12"), ("8 − 12", "8-12"),
    ("30 sn", "30 sn"), ("045 sec", "45 sec"), ("20–30 sn", "20-30 sn"),
    ("stop when it burns", None), ("8-12 reps", None), ("12–8", None),
    ("8--12", None), ("8-", None), ("0–8", None), ("501", None),
    ("8" + " " * 15 + "s", None), ("", None), (None, None),
])
def test_coach_prescription_uses_closed_projection_grammar(saved, projected):
    raw = json.dumps({"status": "ok", "plan": {"days": [{
        "day": "Pazartesi", "kind": "antrenman",
        "exercises": [{"name": "Plank", "sets": 3, "reps": saved}],
    }]}})
    exercise = json.loads(coach_privacy.tool_result(READ_PLAN_TOOL, raw))["plan"]["days"][0]["exercises"][0]
    assert (exercise["sets"], exercise["reps"]) == (3, projected)


def test_private_replacement_and_mixed_day_use_canonical_placement_rules(
    raw_client, make_user, as_owner,
):
    owner = make_user("private-placement")
    headers = as_owner(owner)
    doc = program(equipment="ev", mixed=True)
    save_plan(owner.id, doc)
    incompatible = register(raw_client, headers)[1]["exercise"]["exercise_id"]
    accessible = register(raw_client, headers, display_name="Private split squat",
                          equipment=["bodyweight"])[1]["exercise"]["exercise_id"]
    binding = revision(raw_client, headers)
    fields = {"position": 0, "exercise_id": SQUAT}
    refused = mutate(raw_client, headers, "replace", binding, "private-equipment",
                     replacement_id=incompatible, **fields)
    assert refused.status_code == 400
    changed = mutate(raw_client, headers, "replace", binding, "private-mixed-replace",
                     replacement_id=accessible, **fields)
    assert changed.status_code == 200, changed.json
    removed = mutate(raw_client, headers, "remove", revision(raw_client, headers), "private-mixed-remove",
                     position=0, exercise_id=accessible)
    assert removed.status_code == 400


MOBILITY = "ex_hip_mobility_flow"


def test_mobility_never_counts_as_the_strength_that_keeps_cardio_on_a_day(
    raw_client, make_user, as_owner,
):
    owner = make_user("private-mobility-last-strength")
    headers = as_owner(owner)
    document = program(equipment="ev", mixed=True)
    document["program"][2].update(tip="antrenman", sure_dk=30)
    document["program"][2]["egzersizler"] = [exercise(MOBILITY), exercise(RUN)]
    plan_id = save_plan(owner.id, document)
    status, payload = register(raw_client, headers, display_name="Private hip flow",
                               equipment=["bodyweight"], movement="mobility",
                               target_region="mobility")
    assert status == 201, payload
    private_mobility = payload["exercise"]["exercise_id"]
    before = durable_state(raw_client, headers, plan_id)
    binding = before[-1]
    last_strength = {"position": 0, "exercise_id": SQUAT}
    for key, operation, fields in (
        ("private", "replace", {**last_strength, "replacement_id": private_mobility}),
        ("curated", "replace", {**last_strength, "replacement_id": MOBILITY}),
        ("private-prescribed", "replace",
         {**last_strength, "replacement_id": private_mobility, "sets": 2}),
        ("same-id-cardio", "replace",
         {"day_slot": 2, "position": 1, "exercise_id": RUN, "replacement_id": RUN,
          "reps": "25 dk"}),
        ("remove-mobility-day-strength", "remove", {"day_slot": 2, "position": 0,
                                                   "exercise_id": MOBILITY}),
        ("prescription-cardio", "prescription",
         {"day_slot": 2, "position": 1, "exercise_id": RUN, "reps": "30 dk"}),
        ("prescription-mobility", "prescription",
         {"day_slot": 2, "position": 0, "exercise_id": MOBILITY, "sets": 2}),
    ):
        response = mutate(raw_client, headers, operation, binding, f"private-mobility-{key}",
                          **fields)
        assert response.status_code == 400, (key, response.json)
        assert response.json["error"]["code"] == "TRAINING_EDITOR_INVALID_REQUEST"
        assert durable_state(raw_client, headers, plan_id) == before, key

    added = mutate(raw_client, headers, "add", binding, "private-mobility-add",
                   exercise_id=private_mobility, sets=1, reps="60 sn")
    assert added.status_code == 200, added.json
    after_add = durable_state(raw_client, headers, plan_id)
    removed = mutate(raw_client, headers, "remove", after_add[-1], "private-mobility-remove",
                     **last_strength)
    assert removed.status_code == 400, removed.json
    assert durable_state(raw_client, headers, plan_id) == after_add

    strength = mutate(raw_client, headers, "replace", after_add[-1], "private-mobility-plank",
                      **last_strength, replacement_id=PLANK)
    assert strength.status_code == 200, strength.json
    day = json.loads(raw_client.get(CURRENT, headers=headers).data)["plan"]["days"][0]
    assert [item["exercise_id"] for item in day["exercises"]] == [PLANK, RUN, private_mobility]


def test_retention_holds_and_account_purge_erase_only_owned_rows(raw_client, make_user, as_owner):
    from app.models import User

    owner = make_user("private-erase")
    other = make_user("private-retained")
    owner_id, other_id = owner.id, other.id
    register(raw_client, as_owner(owner))
    register(raw_client, as_owner(other))
    health_erasure._erase_rows(db.session.get(User, owner_id), {"workouts"})
    db.session.commit()
    assert PrivateExercise.query.filter_by(user_id=owner_id).count() == 1
    health_erasure._erase_rows(db.session.get(User, owner_id), set())
    db.session.commit()
    assert PrivateExercise.query.filter_by(user_id=owner_id).count() == 0
    assert PrivateExercise.query.filter_by(user_id=other_id).count() == 1
    register(raw_client, as_owner(db.session.get(User, owner_id)))
    assert _purge_user(db.session.get(User, owner_id))["private_exercise"] == 1
    db.session.commit()
    assert PrivateExercise.query.filter_by(user_id=owner_id).count() == 0
    assert PrivateExercise.query.filter_by(user_id=other_id).count() == 1



def test_coach_resolves_private_exercise_after_curated_projection_limit(raw_client, make_user, as_owner):
    owner = make_user("private-coach-position")
    owner_id = owner.id
    headers = as_owner(owner)
    identity = register(raw_client, headers)[1]["exercise"]["exercise_id"]
    document = program()
    document["program"][0]["egzersizler"] = [exercise() for _ in range(31)] + [exercise(identity)]
    save_plan(owner_id, document)
    result = read_current_training_plan(owner_id)
    raw = coach_privacy.tool_result(READ_PLAN_TOOL, json.dumps(result), user_id=owner_id)
    exercises = json.loads(raw)["plan"]["days"][0]["exercises"]
    assert len(exercises) == 32
    assert exercises[-1]["name"] == BODY["display_name"]
    assert exercises[-1]["position"] == 31


def test_migration_verifies_precreated_table_and_refuses_losing_registered_ids(
    raw_client, make_user, as_owner,
):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory

    migration = ScriptDirectory("migrations").get_revision("e5b27aa4101c").module
    headers = as_owner(make_user("private-precreated"))
    assert register(raw_client, headers)[0] == 201
    with db.engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            with pytest.raises(RuntimeError, match="would lose durable identities"):
                migration.downgrade()
    assert PrivateExercise.query.count() == 1
