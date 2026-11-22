"""Native editor transport and canonical durable state."""

import json
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import PlanMutationRecord, TrainingPlan, WorkoutLog, WorkoutSession
from app.services import mobile_auth
from app.services.training_generation.plan_schema import WEEKDAYS


EDITOR = "/api/v1/training/editor"
CATALOG = "/api/v1/training/exercise-catalog"
CURRENT = "/api/v1/training/plans/current"
SQUAT = "ex_bodyweight_squat"
PLANK = "ex_plank"
FACE_PULL = "ex_cable_face_pull"
RUN = "ex_outdoor_run"


def exercise(exercise_id=SQUAT):
    return {
        "exercise_id": exercise_id, "isim": "Stored name", "set": 3,
        "tekrar": "8-12", "dinlenme": "60 sn", "not": "",
    }


def program(*, equipment="spor_salonu", mixed=False):
    return {
        "exercise_context": {
            "equipment_context": equipment, "cardio_type": "kosu",
            "style": "general_fitness", "catalog_version": 1,
        },
        "program": [{
            "gun": weekday, "tip": ("antrenman", "kardiyo")[slot] if slot < 2 else "dinlenme",
            "odak": "Test", "sure_dk": 30 if slot < 2 else 0, "tahmini_kalori": 0,
            "egzersizler": (
                [exercise(), exercise(RUN if mixed else SQUAT)] if slot == 0
                else [exercise(RUN)] if slot == 1 else []
            ),
        } for slot, weekday in enumerate(WEEKDAYS)],
    }


def save_plan(user_id, document):
    row = TrainingPlan(user_id=user_id, plan_data=json.dumps(document), score=7)
    db.session.add(row)
    db.session.commit()
    return row.id


@pytest.fixture
def as_owner(monkeypatch):
    owners = {}

    def authenticate(raw):
        owner = owners[raw]
        return mobile_auth.MobilePrincipal(owner, SimpleNamespace(id=1), {"sub": owner.cognito_sub})

    monkeypatch.setattr(mobile_auth, "authenticate_access", authenticate)

    def headers(user):
        token = f"editor-credential-{user.id}"
        owners[token] = SimpleNamespace(id=user.id, cognito_sub=user.cognito_sub)
        return {"Authorization": f"Bearer {token}"}

    return headers


def revision(client, headers):
    with client.get(EDITOR, headers=headers) as response:
        assert response.status_code == 200, response.json
        plan = response.json["plan"]
        return {key: plan[key] for key in ("plan_lineage", "mutation_version", "snapshot")}


def mutate(client, headers, operation, binding, key, **fields):
    return client.post(
        f"{EDITOR}/{operation}",
        headers={**headers, "Idempotency-Key": key},
        json={"day_slot": 0, **fields, **binding},
    )


def test_editor_requires_bearer_and_first_party_consent(raw_client, make_user, as_owner):
    for path in (EDITOR, CATALOG):
        assert raw_client.get(path).status_code == 401
    assert raw_client.post(f"{EDITOR}/add", json={}).status_code == 401
    headers = as_owner(make_user("editor-no-grant", health_consent=False))
    assert raw_client.get(EDITOR, headers=headers).status_code == 403
    assert raw_client.post(f"{EDITOR}/add", headers=headers, json={}).status_code == 403


@pytest.mark.parametrize(("query", "first"), [
    ("dambilla yana ac", "ex_lateral_raise"),
    ("ŞINAV", "ex_push_up"),
    ("db shoulder press", "ex_dumbbell_shoulder_press"),
    ("chest supported machine", "ex_machine_chest_supported_row"),
    ("yuzm", "ex_swimming"),
])
def test_versioned_picker_search(raw_client, make_user, as_owner, query, first):
    headers = as_owner(make_user("editor-catalog"))
    response = raw_client.get(CATALOG, headers=headers, query_string={"q": query})
    assert response.status_code == 200
    assert response.json["catalog_version"] == 3
    assert response.json["exercises"][0]["exercise_id"] == first


def test_catalog_browse_and_missing_name(raw_client, make_user, as_owner):
    headers = as_owner(make_user("editor-catalog"))
    exercises = raw_client.get(CATALOG, headers=headers).json["exercises"]
    assert len({exercise["exercise_id"] for exercise in exercises}) == len(exercises) == 205
    assert raw_client.get(CATALOG, headers=headers, query_string={"q": "Belt squat"}).json == {
        "contract_version": 1, "catalog_version": 3, "exercises": [],
    }
    assert raw_client.get(f"{CATALOG}?q=run&q=walk", headers=headers).status_code == 400


def test_exact_duplicate_edits_and_durable_retry_preserve_performed_history(
    app, raw_client, make_user, as_owner,
):
    from datetime import datetime

    owner = make_user("editor-persistence")
    owner_id = owner.id
    headers = as_owner(owner)
    plan_id = save_plan(owner_id, program())
    session = WorkoutSession(
        user_id=owner_id, status="completed", workout_date="2026-10-01",
        completed_at=datetime(2026, 10, 1), planned_training_plan_id=plan_id,
        prescription_data='{"recorded":"prescription"}', checkpoint_data='{"recorded":"checkpoint"}',
    )
    log = WorkoutLog(user_id=owner_id, exercise_name="Original log", sets=3, reps=8)
    db.session.add_all([session, log])
    db.session.commit()
    session_id, log_id = session.id, log.id
    app.config["AI_COACH_PLAN_MUTATION_TOOLS_ENABLED"] = False

    base = revision(raw_client, headers)
    target = {"position": 1, "exercise_id": SQUAT}
    edited = mutate(raw_client, headers, "prescription", base, "editor-edit", sets=5, **target)
    assert edited.status_code == 200, edited.json
    newer = revision(raw_client, headers)
    replaced = mutate(raw_client, headers, "replace", newer, "editor-replace",
                      replacement_id=FACE_PULL, **target)
    assert replaced.status_code == 200, replaced.json
    newer = revision(raw_client, headers)
    added = mutate(raw_client, headers, "add", newer, "editor-add",
                   exercise_id=PLANK, sets=2, reps="30 sn")
    assert added.status_code == 200, added.json
    newer = revision(raw_client, headers)
    removed = mutate(raw_client, headers, "remove", newer, "editor-remove", position=0, exercise_id=SQUAT)
    assert removed.status_code == 200, removed.json

    fresh_client = app.test_client()
    current = fresh_client.get(CURRENT, headers=headers)
    day = current.json["plan"]["days"][0]
    assert [(item["exercise_id"], item["sets"], item["reps"]) for item in day["exercises"]] == [
        (FACE_PULL, 5, "8-12"), (PLANK, 2, "30 sn"),
    ]
    assert day["exercises"][1]["rest"]["seconds"] == 0
    replay = mutate(fresh_client, headers, "prescription", base, "editor-edit", sets=5, **target)
    assert replay.json == edited.json
    assert replay.headers["Idempotency-Replayed"] == "true"
    conflict = mutate(fresh_client, headers, "prescription", base, "editor-edit",
                      sets=5, position=0, exercise_id=SQUAT)
    assert conflict.json["error"]["code"] == "TRAINING_EDITOR_IDEMPOTENCY_CONFLICT"

    db.session.remove()
    stored = json.loads(db.session.get(TrainingPlan, plan_id).plan_data)
    assert stored["program"][0]["egzersizler"][0]["isim"] == "Cable Face Pull"
    assert stored["exercise_context"]["catalog_version"] == 1
    history = PlanMutationRecord.query.order_by(PlanMutationRecord.id).all()
    assert [(row.actor, row.before_version, row.after_version) for row in history] == [
        ("user", version, version + 1) for version in range(4)
    ]
    completed = db.session.get(WorkoutSession, session_id)
    assert completed.status == "completed"
    assert completed.prescription_data == '{"recorded":"prescription"}'
    assert completed.checkpoint_data == '{"recorded":"checkpoint"}'
    assert WorkoutSession.query.count() == 1
    assert WorkoutLog.query.count() == 1
    assert db.session.get(WorkoutLog, log_id).exercise_name == "Original log"


def test_remove_one_of_two_identical_rows(raw_client, make_user, as_owner):
    owner = make_user("editor-exact-remove")
    save_plan(owner.id, program())
    headers = as_owner(owner)
    binding = revision(raw_client, headers)
    response = mutate(raw_client, headers, "remove", binding, "editor-dupe-remove",
                      position=1, exercise_id=SQUAT)
    assert response.status_code == 200, response.json
    assert len(raw_client.get(CURRENT, headers=headers).json["plan"]["days"][0]["exercises"]) == 1


def test_editor_refuses_stale_foreign_mismatched_and_invalid_writes(raw_client, make_user, as_owner):
    owner = make_user("editor-owner")
    other = make_user("editor-other")
    plan_id = save_plan(owner.id, program())
    other_id = other.id
    save_plan(other_id, program())
    headers = as_owner(owner)
    other_headers = as_owner(other)
    binding = revision(raw_client, headers)
    payload = {"exercise_id": PLANK, "sets": 2, "reps": "12"}
    foreign = mutate(raw_client, other_headers, "add", binding, "editor-foreign", **payload)
    assert foreign.json["error"]["code"] == "TRAINING_EDITOR_STALE_PLAN"

    for invalid in ({"exercise_id": "Zercher squat"}, {"exercise_id": "ex_zercher_squat"},
                    {"sets": True}, {"reps": None}, {"reps": chr(0xD800)},
                    {"user_id": other_id}, {"day_slot": 2}):
        response = mutate(raw_client, headers, "add", binding, "editor-invalid",
                          **{**payload, **invalid})
        assert response.status_code == 400, response.json
    assert mutate(raw_client, headers, "add", binding, "bad", **payload).status_code == 400
    wrong = mutate(raw_client, headers, "remove", binding, "editor-target",
                   position=1, exercise_id=PLANK)
    assert wrong.json["error"]["code"] == "TRAINING_EDITOR_TARGET_NOT_FOUND"

    plan = db.session.get(TrainingPlan, plan_id)
    plan.plan_data += " "
    db.session.commit()
    stale = mutate(raw_client, headers, "add", binding, "editor-byte-stale", **payload)
    assert stale.json["error"]["code"] == "TRAINING_EDITOR_STALE_PLAN"
    assert PlanMutationRecord.query.count() == 0


def test_editor_preserves_equipment_cardio_and_in_flight_session(raw_client, make_user, as_owner):
    owner = make_user("editor-cardio")
    owner_id = owner.id
    save_plan(owner_id, program(equipment="ev", mixed=True))
    headers = as_owner(owner)
    binding = revision(raw_client, headers)
    for operation, fields in (
        ("add", {"exercise_id": FACE_PULL, "sets": 2, "reps": "10"}),
        ("add", {"exercise_id": RUN, "sets": 1, "reps": "10 dk"}),
        ("add", {"exercise_id": PLANK, "sets": 1, "reps": "20 sn", "day_slot": 1}),
        ("remove", {"exercise_id": RUN, "position": 0, "day_slot": 1}),
        ("remove", {"exercise_id": SQUAT, "position": 0}),
    ):
        assert mutate(raw_client, headers, operation, binding, "editor-refuse", **fields).status_code == 400

    assert mutate(raw_client, headers, "prescription", binding, "editor-cardio-edit",
                  exercise_id=RUN, position=1, reps="25 dk").status_code == 200
    binding = revision(raw_client, headers)
    db.session.add(WorkoutSession(user_id=owner_id, workout_date="2026-11-21"))
    db.session.commit()
    active = mutate(raw_client, headers, "remove", binding, "editor-active", exercise_id=RUN, position=1)
    assert active.status_code == 409
    assert active.json["error"]["code"] == "TRAINING_EDITOR_ACTIVE_WORKOUT"
    assert PlanMutationRecord.query.count() == 1


def test_undo_restores_exact_bytes_and_refuses_coach_mutations(raw_client, make_user, as_owner):
    from app.services.plan_mutation import (
        ACTOR_AI_COACH, AddExerciseCommand, MutationContext, apply_plan_mutation,
    )

    owner = make_user("editor-undo")
    owner_id = owner.id
    plan_id = save_plan(owner_id, program())
    headers = as_owner(owner)
    original = db.session.get(TrainingPlan, plan_id).plan_data
    added = mutate(raw_client, headers, "add", revision(raw_client, headers), "editor-undo-add",
                   exercise_id=PLANK, sets=3, reps="20 sn")
    assert added.status_code == 200, added.json
    body = {**revision(raw_client, headers), "mutation_id": added.json["mutation_id"]}
    undo_headers = {**headers, "Idempotency-Key": "editor-undo-write"}
    undone = raw_client.post(f"{EDITOR}/undo", headers=undo_headers, json=body)
    assert undone.status_code == 200, undone.json
    assert undone.json["mutation_version"] == 2
    assert db.session.get(TrainingPlan, plan_id).plan_data == original
    replay = raw_client.post(f"{EDITOR}/undo", headers=undo_headers, json=body)
    assert replay.json == undone.json
    assert replay.headers["Idempotency-Replayed"] == "true"

    coach = apply_plan_mutation(
        owner_id, AddExerciseCommand(day=WEEKDAYS[0], exercise="Plank", sets=2, reps="30 sn"),
        MutationContext("coach-test-edit", actor=ACTOR_AI_COACH),
    )
    blocked = raw_client.post(f"{EDITOR}/undo", headers={**headers, "Idempotency-Key": "editor-undo-coach"},
                              json={**revision(raw_client, headers), "mutation_id": coach.mutation_id})
    assert blocked.status_code == 409
    assert blocked.json["error"]["code"] == "TRAINING_EDITOR_UNDO_UNAVAILABLE"
    assert db.session.get(TrainingPlan, plan_id).mutation_version == 3


def test_old_canonical_add_without_rest_is_readable_and_editable(raw_client, make_user, as_owner):
    owner = make_user("editor-legacy-add")
    document = program()
    stored = document["program"][0]["egzersizler"][0]
    del stored["dinlenme"], stored["not"]
    plan_id = save_plan(owner.id, document)
    headers = as_owner(owner)
    response = mutate(raw_client, headers, "prescription", revision(raw_client, headers),
                      "editor-v1-add-edit", position=0, exercise_id=SQUAT, sets=5)
    assert response.status_code == 200, response.json
    current = raw_client.get(CURRENT, headers=headers).json["plan"]["days"][0]["exercises"][0]
    assert current["sets"] == 5
    assert current["rest"]["seconds"] == 0
    assert "dinlenme" not in json.loads(db.session.get(TrainingPlan, plan_id).plan_data)["program"][0]["egzersizler"][0]


def test_editor_rejects_oversize_and_deeply_nested_json(raw_client, make_user, as_owner):
    headers = {**as_owner(make_user("editor-body-limit")), "Idempotency-Key": "editor-malformed"}
    path = f"{EDITOR}/add"
    for depth, expected_status in ((1500, 400), (3000, 413)):
        response = raw_client.post(path, data="[" * depth + "]" * depth,
                                   content_type="application/json", headers=headers)
        assert response.status_code == expected_status
        assert response.json["error"]["code"] == "TRAINING_EDITOR_INVALID_REQUEST"
