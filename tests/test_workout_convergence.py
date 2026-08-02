"""Sprint 7 PR4 consumer-convergence contract tests."""
import json
from contextlib import contextmanager
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import TrainingPlan
from app.services.training_generation.models import TrainingPreferences
from app.services.training_generation.response_validator import validate_generated_plan
MONDAY = date(2026, 7, 27)


def _program(exercise_count=1):
    days = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
    program = []
    for index, name in enumerate(days):
        workout = index == 0
        program.append({
            "gun": name,
            "tip": "antrenman" if workout else "dinlenme",
            "odak": "Sırt" if workout else "Toparlanma",
            "sure_dk": 45 if workout else 0,
            "tahmini_kalori": 380 if workout else 0,
            "egzersizler": [
                {"isim": f"Row {n}", "set": 3, "tekrar": "10-12",
                 "dinlenme": "75 sn", "not": "Kontrollü", "internal_id": n}
                for n in range(exercise_count)
            ] if workout else [],
        })
    return program


def test_today_plan_is_exact_bounded_projection():
    from app.services.workout_state.serialization import serialize_today_plan

    selected = serialize_today_plan({"program": _program()}, MONDAY)

    assert selected == {
        "gun": "Pazartesi", "tip": "antrenman", "odak": "Sırt",
        "sure_dk": 45, "tahmini_kalori": 380,
        "egzersizler": [{"isim": "Row 0", "set": 3, "tekrar": "10-12",
                          "dinlenme": "75 sn", "not": "Kontrollü"}],
    }


def test_today_plan_rejects_unbounded_exercise_list():
    from app.services.workout_state.serialization import (
        PlanSerializationError, serialize_today_plan)

    with pytest.raises(PlanSerializationError):
        serialize_today_plan({"program": _program(exercise_count=51)}, MONDAY)


def test_shared_state_payload_has_legacy_completed_mirror(monkeypatch):
    from app.services.workout_state.serialization import workout_state_payload

    class Snapshot:
        completed_today = True

        def to_dict(self):
            return {"contract_version": 2, "completed_today": True}

    assert workout_state_payload(Snapshot()) == {
        "completed": True,
        "state": {"contract_version": 2, "completed_today": True},
    }


def test_training_bootstrap_uses_one_plan_instance_and_shared_payload(
        client, auth_user, monkeypatch):
    from app.blueprints import training as training_bp
    from app.services import workout_state

    plan = TrainingPlan(user_id=auth_user.id,
                        plan_data=json.dumps({"program": _program()}, ensure_ascii=False),
                        score=8.5)
    db.session.add(plan)
    db.session.commit()
    seen = []
    events = []
    snapshot_open = False
    real_resolve = workout_state.resolve_workout_state
    real_get_active_plan = training_bp.get_active_plan
    real_serialize_plan = training_bp.serialize_plan
    real_serialize_today_plan = training_bp.serialize_today_plan
    real_workout_state_payload = training_bp.workout_state_payload

    class RouteUser:
        reads = 0

        @property
        def id(self):
            self.reads += 1
            events.append("capture:user_id")
            return auth_user.id

    route_user = RouteUser()

    @contextmanager
    def snapshot_boundary():
        nonlocal snapshot_open
        events.append("snapshot:enter")
        snapshot_open = True
        try:
            yield
        finally:
            snapshot_open = False
            events.append("snapshot:exit")

    def active_plan(user_id):
        assert snapshot_open
        events.append("read:plan")
        return real_get_active_plan(user_id)

    def capture(user_id, **kwargs):
        assert snapshot_open
        events.append("read:workout")
        seen.append(kwargs)
        return real_resolve(user_id, **kwargs)

    def serialize_plan(data):
        assert snapshot_open
        events.append("serialize:plan")
        return real_serialize_plan(data)

    def serialize_today_plan(data, today):
        assert snapshot_open
        events.append("serialize:today")
        return real_serialize_today_plan(data, today)

    def workout_payload(snapshot):
        assert snapshot_open
        events.append("serialize:workout")
        return real_workout_state_payload(snapshot)

    monkeypatch.setattr(training_bp, "current_user", route_user)
    monkeypatch.setattr(
        training_bp, "app_today",
        lambda: events.append("capture:today") or MONDAY)
    monkeypatch.setattr(
        training_bp, "_workout_sessions_enabled",
        lambda: events.append("capture:flag") or False)
    monkeypatch.setattr(training_bp, "coherent_read_snapshot", snapshot_boundary)
    monkeypatch.setattr(training_bp, "get_active_plan", active_plan)
    monkeypatch.setattr(training_bp, "resolve_workout_state", capture)
    monkeypatch.setattr(training_bp, "serialize_plan", serialize_plan)
    monkeypatch.setattr(training_bp, "serialize_today_plan", serialize_today_plan)
    monkeypatch.setattr(training_bp, "workout_state_payload", workout_payload)
    response = client.get("/training/bootstrap")

    assert response.status_code == 200
    body = response.get_json()
    assert set(body) == {"workout", "plan", "today_plan"}
    assert body["workout"]["completed"] is False
    assert body["plan"]["exists"] is True
    assert body["today_plan"]["gun"] == "Pazartesi"
    assert len(seen) == 1
    assert seen[0]["today"] == MONDAY
    assert seen[0]["plan"].id == plan.id
    assert seen[0]["strict_reads"] is True
    assert response.headers["Cache-Control"] == "private, no-store"
    assert route_user.reads == 1
    assert events == [
        "capture:user_id", "capture:today", "capture:flag",
        "snapshot:enter", "read:plan", "read:workout",
        "serialize:workout", "serialize:plan", "serialize:today",
        "snapshot:exit",
    ]


def test_training_bootstrap_projects_full_week_onto_closed_public_schema(
        client, auth_user, monkeypatch):
    from app.blueprints import training as training_bp

    program = _program()
    for index, day in enumerate(program):
        day["raw_day_id"] = f"private-day-{index}"
        day["unknown_day_field"] = "private-day-value"
        if day["egzersizler"]:
            day["egzersizler"][0]["raw_exercise_id"] = "private-exercise-42"
            day["egzersizler"][0]["unknown_exercise_field"] = "private-exercise-value"
    persisted = {
        "program": program,
        "raw_plan_id": "private-plan-17",
        "unknown_plan_field": "private-plan-value",
    }
    db.session.add(TrainingPlan(
        user_id=auth_user.id,
        plan_data=json.dumps(persisted, ensure_ascii=False),
        score=8,
    ))
    db.session.commit()
    monkeypatch.setattr(training_bp, "app_today", lambda: MONDAY)

    response = client.get("/training/bootstrap")

    assert response.status_code == 200
    body = response.get_json()
    assert set(body["plan"]) == {"exists", "plan", "score", "created_at"}
    assert set(body["plan"]["plan"]) == {"program"}
    public_program = body["plan"]["plan"]["program"]
    assert len(public_program) == 7
    assert all(set(day) == {
        "gun", "tip", "odak", "sure_dk", "tahmini_kalori", "egzersizler",
    } for day in public_program)
    assert public_program[0]["egzersizler"] == [{
        "isim": "Row 0", "set": 3, "tekrar": "10-12",
        "dinlenme": "75 sn", "not": program[0]["egzersizler"][0]["not"],
    }]
    raw = response.get_data(as_text=True)
    for sentinel in (
            "private-plan", "private-day", "private-exercise",
            "raw_plan_id", "raw_day_id", "raw_exercise_id",
            "unknown_plan_field", "unknown_day_field",
            "unknown_exercise_field", "internal_id"):
        assert sentinel not in raw


@pytest.mark.parametrize(("raw_duration", "raw_sets", "duration", "sets"), [
    (-1, 0, 0, 1),
    (1441, 101, 1440, 100),
])
def test_training_bootstrap_serves_validator_bounded_generated_plan(
        client, auth_user, monkeypatch,
        raw_duration, raw_sets, duration, sets):
    program = _program()
    program[0]["sure_dk"] = raw_duration
    program[0]["egzersizler"][0]["set"] = raw_sets
    generated = {"program": program, "haftalik_ozet": {}}

    validated, _ = validate_generated_plan(
        generated, TrainingPreferences(gun_sayisi=1, sure=45))
    db.session.add(TrainingPlan(
        user_id=auth_user.id,
        plan_data=json.dumps(validated, ensure_ascii=False),
        score=8,
    ))
    db.session.commit()
    monkeypatch.setattr("app.blueprints.training.app_today", lambda: MONDAY)

    response = client.get("/training/bootstrap")

    assert response.status_code == 200
    assert response.get_json()["today_plan"]["sure_dk"] == duration
    assert response.get_json()["today_plan"]["egzersizler"][0]["set"] == sets

def test_training_bootstrap_fails_closed_without_partial_payload(
        client, auth_user, monkeypatch):
    from app.blueprints import training as training_bp

    class RouteUser:
        reads = 0

        @property
        def id(self):
            self.reads += 1
            if self.reads > 1:
                raise AssertionError("current_user read after snapshot reset")
            return auth_user.id

    route_user = RouteUser()
    remove_calls = []
    real_remove = db.session.remove

    def record_remove():
        remove_calls.append("remove")
        return real_remove()

    def fail_resolve(*_args, **_kwargs):
        assert remove_calls == ["remove"]
        raise RuntimeError("db")

    monkeypatch.setattr(training_bp, "current_user", route_user)
    monkeypatch.setattr(db.session, "remove", record_remove)
    monkeypatch.setattr(
        training_bp, "resolve_workout_state", fail_resolve)
    response = client.get("/training/bootstrap")

    assert response.status_code == 500
    assert response.get_json()["code"] == "bootstrap_unavailable"
    assert set(response.get_json()) == {"error", "code"}
    assert response.headers["Cache-Control"] == "private, no-store"
    assert route_user.reads == 1
    assert len(remove_calls) >= 2


def test_bootstrap_supports_legacy_top_level_program_saved_by_training_ui(
        client, auth_user, monkeypatch):
    from app.blueprints import training as training_bp

    monkeypatch.setattr(training_bp, "app_today", lambda: MONDAY)
    saved = client.post("/training-plan/save", json={"plan": _program(), "score": 7})
    assert saved.status_code == 200

    body = client.get("/training/bootstrap").get_json()
    assert body["today_plan"]["gun"] == "Pazartesi"
    assert body["workout"]["state"]["schedule_state"] == "scheduled"


def test_bootstrap_malformed_schedule_is_blocked_not_rest(client, auth_user):
    db.session.add(TrainingPlan(
        user_id=auth_user.id, plan_data=json.dumps({"program": []}), score=1))
    db.session.commit()

    response = client.get("/training/bootstrap")
    body = response.get_json()
    assert response.status_code == 200
    assert body["today_plan"] is None
    assert body["workout"]["state"]["schedule_state"] == "schedule_unavailable"
    assert body["workout"]["state"]["action"] == "blocked"


def test_bootstrap_loads_training_plan_once(client, auth_user, monkeypatch):
    from sqlalchemy import event
    from app.extensions import db as extension_db

    db.session.add(TrainingPlan(
        user_id=auth_user.id,
        plan_data=json.dumps({"program": _program()}, ensure_ascii=False), score=8))
    db.session.commit()
    statements = []

    def record(_conn, _cursor, statement, _parameters, _context, _many):
        if "from training_plan" in statement.lower():
            statements.append(statement)

    event.listen(extension_db.engine, "before_cursor_execute", record)
    try:
        assert client.get("/training/bootstrap").status_code == 200
    finally:
        event.remove(extension_db.engine, "before_cursor_execute", record)

    assert len(statements) == 1

    from app.services.workout_state import snapshot as snapshot_module

    for dialect_name, expected_connection_call in (
        ("postgresql", {
            "execution_options": {"isolation_level": "REPEATABLE READ"}}),
        ("sqlite", {}),
    ):
        calls = []

        class FakeSession:
            def remove(self):
                calls.append(("remove", {}))

            def connection(self, **kwargs):
                calls.append(("connection", kwargs))

        fake_db = SimpleNamespace(
            engine=SimpleNamespace(
                dialect=SimpleNamespace(name=dialect_name)),
            session=FakeSession(),
        )
        monkeypatch.setattr(snapshot_module, "db", fake_db)

        with snapshot_module.coherent_read_snapshot():
            calls.append(("body", {}))

        assert calls == [
            ("remove", {}),
            ("connection", expected_connection_call),
            ("body", {}),
            ("remove", {}),
        ]


def test_flag_on_legacy_program_creates_resumable_persisted_session(
        app, client, auth_user):
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True
    program = _program()
    for day in program:
        day["tip"] = "antrenman"
        day["egzersizler"] = [{"isim": "Row", "set": 3, "tekrar": "10",
                                "dinlenme": "60 sn", "not": ""}]
    assert client.post(
        "/training-plan/save", json={"plan": program, "score": 7}).status_code == 200
    assert client.post("/workout/session/start").status_code == 201

    state = client.get("/training/bootstrap").get_json()["workout"]["state"]
    assert state["contract_version"] == 2
    assert state["session_state"] == "active_resumable"
    assert state["action"] == "resume"


def test_progress_current_equals_status_payload(client, auth_user):
    status = client.get("/workout/status").get_json()
    progress = client.get("/api/progress/workout?range=week").get_json()

    assert progress["current"] == status


def test_barcode_context_uses_canonical_completion_once(auth_user, monkeypatch):
    from app.services import barcode

    calls = []

    class Snapshot:
        completed_today = True

    def resolve(user_id):
        calls.append(user_id)
        return Snapshot()

    monkeypatch.setattr(barcode, "resolve_workout_state", resolve)
    context = barcode.get_user_barcode_context(auth_user.id)

    assert context["workout_completed_today"] is True
    assert calls == [auth_user.id]


def test_coach_context_includes_one_canonical_current_snapshot(
        auth_user, monkeypatch):
    from app.services import context_builder

    calls = []

    class Snapshot:
        def to_dict(self):
            return {"contract_version": 2, "primary_state": "in_progress",
                    "action": "resume", "completed_today": False}

    def resolve(user_id):
        calls.append(user_id)
        return Snapshot()

    monkeypatch.setattr(context_builder, "resolve_workout_state", resolve)
    context = context_builder.fetch_coach_context(auth_user.id)

    assert "[GÜNCEL ANTRENMAN DURUMU]" in context
    assert '"primary_state":"in_progress"' in context
    assert calls == [auth_user.id]


def test_today_plan_accepts_boundary_values_drops_internal_fields_and_orders_keys():
    from app.services.workout_state.serialization import (
        MAX_TODAY_EXERCISES, serialize_today_plan)

    program = _program(exercise_count=MAX_TODAY_EXERCISES)
    selected = program[0]
    selected.update({"odak": "O" * 120, "sure_dk": 1440,
                     "tahmini_kalori": 900, "private": "discard"})
    selected["egzersizler"][0].update({
        "isim": "I" * 120, "set": 100, "tekrar": "T" * 40,
        "dinlenme": "D" * 40, "not": "N" * 240, "internal_id": "discard",
    })

    payload = serialize_today_plan({"program": program}, MONDAY)

    assert list(payload) == ["gun", "tip", "odak", "sure_dk",
                             "tahmini_kalori", "egzersizler"]
    assert len(payload["egzersizler"]) == MAX_TODAY_EXERCISES
    assert list(payload["egzersizler"][0]) == ["isim", "set", "tekrar",
                                                "dinlenme", "not"]
    assert "private" not in payload
    assert "internal_id" not in payload["egzersizler"][0]


def test_today_plan_rejects_out_of_bound_text_and_integer_fields():
    from app.services.workout_state.serialization import (
        PlanSerializationError, serialize_today_plan)

    invalid_values = [
        ("odak", "x" * 121), ("sure_dk", 1441),
        ("tahmini_kalori", 901), ("egzersizler.0.isim", "x" * 121),
        ("egzersizler.0.set", 101), ("egzersizler.0.tekrar", "x" * 41),
        ("egzersizler.0.dinlenme", "x" * 41), ("egzersizler.0.not", "x" * 241),
    ]
    for path, value in invalid_values:
        program = _program()
        selected = program[0]
        if path.startswith("egzersizler."):
            selected["egzersizler"][0][path.rsplit(".", 1)[1]] = value
        else:
            selected[path] = value
        with pytest.raises(PlanSerializationError):
            serialize_today_plan({"program": program}, MONDAY)


def test_training_bootstrap_rejects_unauthenticated_client(client):
    response = client.get("/training/bootstrap")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_training_bootstrap_no_plan_is_an_honest_no_plan_state(
        client, auth_user, monkeypatch):
    from app.blueprints import training as training_bp

    monkeypatch.setattr(training_bp, "app_today", lambda: MONDAY)
    response = client.get("/training/bootstrap")

    assert response.status_code == 200
    body = response.get_json()
    assert body["plan"] == {"exists": False}
    assert body["today_plan"] is None
    assert body["workout"]["state"]["schedule_state"] == "no_plan"
    assert body["workout"]["state"]["primary_state"] == "no_plan"


def test_training_bootstrap_scopes_plan_and_today_plan_to_authenticated_user(
        client, auth_user, make_user, login, monkeypatch):
    from app.blueprints import training as training_bp
    from app.services import workout_state

    db.session.add(TrainingPlan(
        user_id=auth_user.id,
        plan_data=json.dumps({"program": _program()}, ensure_ascii=False), score=8))
    db.session.commit()
    second_user = make_user("second_user")
    assert login("second_user").status_code == 200
    monkeypatch.setattr(training_bp, "app_today", lambda: MONDAY)
    resolved_user_ids = []
    real_resolve = workout_state.resolve_workout_state

    def capture(user_id, **kwargs):
        resolved_user_ids.append(user_id)
        return real_resolve(user_id, **kwargs)

    monkeypatch.setattr(training_bp, "resolve_workout_state", capture)
    response = client.get("/training/bootstrap")

    assert response.status_code == 200
    body = response.get_json()
    assert body["plan"] == {"exists": False}
    assert body["today_plan"] is None
    assert "Row 0" not in response.get_data(as_text=True)
    assert resolved_user_ids == [second_user.id]


def test_training_bootstrap_v2_without_persisted_session_never_fabricates_resume(
        app, client, auth_user, monkeypatch):
    from app.blueprints import training as training_bp

    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True
    db.session.add(TrainingPlan(
        user_id=auth_user.id,
        plan_data=json.dumps({"program": _program()}, ensure_ascii=False), score=8))
    db.session.commit()
    monkeypatch.setattr(training_bp, "app_today", lambda: MONDAY)

    response = client.get("/training/bootstrap")

    assert response.status_code == 200
    state = response.get_json()["workout"]["state"]
    assert state["contract_version"] == 2
    assert state["session_state"] == "none"
    assert state["session"] is None
    assert state["action"] != "resume"


def test_training_bootstrap_mirrors_canonical_completed_pump_check(
        client, auth_user, monkeypatch):
    from app.blueprints import training as training_bp
    from app.models import PumpCheck

    db.session.add(TrainingPlan(
        user_id=auth_user.id,
        plan_data=json.dumps({"program": _program()}, ensure_ascii=False), score=8))
    db.session.add(PumpCheck(
        user_id=auth_user.id, valid=True, date_key=MONDAY.isoformat(),
        created_at=datetime(2026, 7, 27, 12, 0, 0)))
    db.session.commit()
    monkeypatch.setattr(training_bp, "app_today", lambda: MONDAY)

    response = client.get("/training/bootstrap")

    assert response.status_code == 200
    workout = response.get_json()["workout"]
    assert workout["completed"] is True
    assert workout["state"]["completed_today"] is True
    assert workout["state"]["execution_state"] == "completed"
    assert workout["state"]["action"] == "none"


def _assert_bootstrap_unavailable(response, forbidden):
    assert response.status_code == 500
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.get_json()["code"] == "bootstrap_unavailable"
    assert set(response.get_json()) == {"error", "code"}
    raw = response.get_data(as_text=True)
    for value in forbidden + ("workout", "plan", "today_plan"):
        assert value not in raw


def test_training_bootstrap_fails_closed_when_active_plan_read_fails(
        client, auth_user, monkeypatch):
    from app.blueprints import training as training_bp

    monkeypatch.setattr(
        training_bp, "get_active_plan",
        lambda _user_id: (_ for _ in ()).throw(
            RuntimeError("postgresql://db-user:password@db/private-plan")),
    )

    _assert_bootstrap_unavailable(
        client.get("/training/bootstrap"),
        ("testuser", "postgresql", "password", "private-plan"),
    )


def test_training_bootstrap_fails_closed_for_malformed_persisted_plan_json(
        client, auth_user):
    db.session.add(TrainingPlan(
        user_id=auth_user.id, plan_data='{"program": "private-plan-token"', score=8))
    db.session.commit()

    _assert_bootstrap_unavailable(
        client.get("/training/bootstrap"),
        ("testuser", "private-plan-token", "program"),
    )


def test_training_bootstrap_fails_closed_when_strict_session_read_fails(
        app, client, auth_user, monkeypatch):
    from app.blueprints import training as training_bp
    from app.services import workout_state

    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True
    db.session.add(TrainingPlan(
        user_id=auth_user.id,
        plan_data=json.dumps({"program": _program()}, ensure_ascii=False), score=8))
    db.session.commit()
    monkeypatch.setattr(training_bp, "app_today", lambda: MONDAY)
    monkeypatch.setattr(
        workout_state, "load_session_facts",
        lambda *_args: (_ for _ in ()).throw(
            RuntimeError("sqlite:///private-session.db user=secret")),
    )

    _assert_bootstrap_unavailable(
        client.get("/training/bootstrap"),
        ("testuser", "sqlite", "private-session", "secret"),
    )


def test_training_bootstrap_fails_closed_when_today_plan_serialization_is_unbounded(
        client, auth_user, monkeypatch):
    from app.blueprints import training as training_bp

    program = _program()
    program[1]["tip"] = "private-invalid-future-tip"
    db.session.add(TrainingPlan(
        user_id=auth_user.id,
        plan_data=json.dumps({"program": program}, ensure_ascii=False), score=8))
    db.session.commit()
    monkeypatch.setattr(training_bp, "app_today", lambda: MONDAY)

    _assert_bootstrap_unavailable(
        client.get("/training/bootstrap"),
        ("testuser", "private-invalid-future-tip", "Row 0"),
    )
