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
    CATALOG, CURRENT, EDITOR, PLANK, SQUAT, as_owner, exercise, mutate, program, revision,
    save_plan,
)


PRIVATE = "/api/v1/training/private-exercises"
BODY = {
    "display_name": "Zercher split squat", "equipment": ["barbell", "rack"],
    "movement": "lunge", "primary_region": "lower_body",
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
    {"equipment": ["unknown"]}, {"movement": []}, {"primary_region": "bad"},
    {"movement": "cardio", "primary_region": "cardio"}, {"user_id": 999},
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
    with raw_client.get(PRIVATE, headers=other) as response:
        assert response.json["exercises"] == []
    status, second = register(raw_client, other)
    assert status == 201
    item = original["exercise"]
    assert item["exercise_id"] != second["exercise"]["exercise_id"]
    with raw_client.get(PRIVATE, headers=first, query_string={"q": "SPLIT SQUAT"}) as response:
        assert response.json == {"contract_version": 1, "exercises": [item], "next_cursor": None}
        assert response.cache_control.no_store
    for query in ({"q": "%"}, {"q": "_"}, {"q": "other"}):
        with raw_client.get(PRIVATE, headers=first, query_string=query) as response:
            assert response.json["exercises"] == []
    assert raw_client.get(f"{PRIVATE}?q=a&q=b", headers=first).status_code == 400
    assert raw_client.get(f"{PRIVATE}?after={SQUAT}", headers=first).status_code == 400
    assert raw_client.get(CATALOG, headers=first, query_string={"q": "Zercher"}).json["exercises"] == []
    assert item["exercise_id"] not in {
        choice.exercise_id for choice in compatible_exercises(ExerciseContext("spor_salonu"))
    }


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


def test_add_replace_undo_and_resolve_in_fresh_plan_today_workout_and_coach(
    app, raw_client, make_user, as_owner, monkeypatch,
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
    foreign_fields = {"exercise_id": identity, "sets": 2, "reps": "8-12"}
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
        assert day["exercises"][2]["reps"] == "8-12"
    with fresh.get(f"/api/v1/training/workouts/{day['workout_ref']}", headers=headers) as workout:
        assert workout.json["workout"]["exercises"][2] == day["exercises"][2]
    row = db.session.get(TrainingPlan, plan_id)
    stored = row.plan_data
    entries = json.loads(stored)["program"][0]["egzersizler"]
    assert planned_workout_for_slot(owner_id, "Pazartesi").exercise_ids == (SQUAT, PLANK, identity)
    assert json.loads(capture(row, entries))["exercises"][2]["exercise_id"] == identity

    def coach(owner):
        raw = json.dumps(read_current_training_plan(owner_id))
        projected = coach_privacy.tool_result(READ_PLAN_TOOL, raw, user_id=owner)
        assert identity not in projected
        return json.loads(projected)["plan"]["days"][0]["exercises"][2]

    assert coach(owner_id) == {
        "name": BODY["display_name"], "equipment": BODY["equipment"], "movement": BODY["movement"],
        "primary_region": BODY["primary_region"], "sets": 2, "reps": "8-12",
        "position": 2, "user_defined": True,
    }
    assert coach(other_id) == {"name": None, "sets": 2, "reps": "8-12"}
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
