"""Hermetic LP17 qualification; real canonical persistence plus fault injection."""
import ast
import inspect
import json
from datetime import datetime

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import WaterLog, WeeklyCheckIn
from app.services import mobile_today, nutrition_day_view
from app.services import today_guidance_read_model as service
from app.services import today_guidance_projection as projection
from app.timeutil import APP_TZ, audit_clock
from test_mobile_today_api import save_plan, complete_workout, FIXED_NOW


@pytest.fixture
def owner(make_user):
    return make_user("lp17-owner")


def read(owner):
    owner_id = owner.id
    with audit_clock(FIXED_NOW):
        return service.build_today_guidance(owner_id)


@pytest.mark.parametrize("kind,expected", [
    ("none", "no_plan"), ("dinlenme", "rest_day"),
    ("antrenman", "scheduled_not_started"), ("broken", "needs_attention"),
    ("complete", "completed"),
])
def test_real_training_authority_parity(app, owner, kind, expected):
    if kind != "none":
        save_plan(owner, today_tip="dinlenme" if kind == "dinlenme" else "antrenman",
                  raw="broken" if kind == "broken" else None)
    if kind == "complete":
        complete_workout(owner)
    owner_id = owner.id
    with audit_clock(FIXED_NOW):
        canonical = mobile_today.build_today(owner_id)["today"]
        result = service.build_today_guidance(owner_id)
    assert result["training"]["status"] == expected == canonical["status"]
    assert result["training"]["workout"] == canonical["workout"]
    assert result["training"]["daily_context"] == canonical["daily_context"]


def test_missing_zero_and_canonical_nutrition_reuse(app, owner):
    owner_id = owner.id
    with audit_clock(FIXED_NOW):
        canonical = nutrition_day_view.nutrition_day_view_payload(
            nutrition_day_view.build_nutrition_day_view(owner_id))
        result = service.build_today_guidance(owner_id)
    assert result["hydration"] == canonical["hydration"] == {
        "state": "empty", "amount": 0, "unit": "glass"}
    assert result["nutrition"]["facts"]["intake"] == canonical["intake"]
    assert result["nutrition"]["facts"]["target"]["value"] is None
    assert result["checkin"]["state"] == "empty"
    assert result["recovery"] == {"state": "unsupported", "facts": None}


def test_real_in_progress_session_reuses_authority(app, owner):
    from app.services.workout_session import start_session
    owner_id = owner.id
    save_plan(owner)
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True
    with audit_clock(FIXED_NOW):
        start_session(owner_id)
        canonical = mobile_today.build_today(owner_id)["today"]
        result = service.build_today_guidance(owner_id)
    assert result["training"]["status"] == "in_progress"
    assert result["training"]["workout"] == canonical["workout"]
    assert result["training"]["guidance"]["kind"] == "resume_workout"


def test_secondary_total_failure_is_unknown(app, owner, monkeypatch):
    def fail(*args):
        raise RuntimeError("storage failed")
    monkeypatch.setattr(nutrition_day_view, "build_nutrition_day_view", fail)
    result = read(owner)
    assert result["nutrition"] == {"state": "unavailable", "facts": None}
    assert result["hydration"]["amount"] is None


def test_checkin_changes_and_account_isolation(app, owner, make_user):
    other = make_user("lp17-other")
    owner_id, other_id = owner.id, other.id
    db.session.add_all([
        WaterLog(user_id=other_id, date_key="2026-07-23", count=9),
        WeeklyCheckIn(user_id=other_id, weight=90, yogunluk=5, fatigue=5),
    ])
    db.session.commit()
    with audit_clock(FIXED_NOW):
        first = service.build_today_guidance(owner_id)
    assert first["hydration"]["amount"] == 0
    assert first["checkin"]["latest"] is None
    db.session.add(WeeklyCheckIn(user_id=owner_id, yogunluk=3, fatigue=2,
                                uyku_kalitesi=None, weight=80,
                                created_at=datetime(2026, 7, 23, 9)))
    db.session.commit()
    with audit_clock(FIXED_NOW):
        second = service.build_today_guidance(owner_id)
    assert second["checkin"]["current_week"]["submitted"] is True
    assert second["checkin"]["latest"]["fatigue"] == 2
    assert second["checkin"]["latest"]["sleep_quality"] is None
    assert second["freshness"]["revision"] is None
    assert second["action_priority"]["primary"] is None


def test_partial_failure_unknown_never_zero(app, owner, monkeypatch):
    def fail(*args):
        raise RuntimeError("private failure")
    monkeypatch.setattr(nutrition_day_view, "_read_hydration", fail)
    monkeypatch.setattr(service.history, "build_history", fail)
    result = read(owner)
    assert result["training"]["status"] == "no_plan"
    assert result["hydration"]["state"] == "unavailable"
    assert result["hydration"]["amount"] is None
    assert result["checkin"]["state"] == "unavailable"
    assert "private failure" not in json.dumps(result)


def test_strict_training_failure(app, owner, monkeypatch):
    def fail(*args):
        raise mobile_today.TodayUnavailable("private")
    monkeypatch.setattr(mobile_today, "build_today", fail)
    with pytest.raises(service.GuidanceUnavailable):
        read(owner)


def test_istanbul_boundary(app, owner):
    owner_id = owner.id
    with audit_clock(datetime.fromisoformat("2026-07-22T21:00:00+00:00")):
        result = service.build_today_guidance(owner_id)
    assert result["day"] == "2026-07-23"
    assert result["training"]["daily_context"]["canonical_local_date"] == result["day"]


def test_midnight_between_sources_requires_reread(app, owner, monkeypatch):
    monkeypatch.setattr(service, "app_today", lambda: datetime(2026, 7, 24).date())
    with pytest.raises(service.GuidanceUnavailable, match="day changed"):
        read(owner)


def test_no_writes_or_flushes(app, owner):
    owner_id = owner.id
    statements = []
    flushes = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().split()[0].upper())
    def fail_flush(*args):
        flushes.append(True)
        raise AssertionError("flush attempted")
    engine = db.engine
    session_type = type(db.session())
    event.listen(engine, "before_cursor_execute", capture)
    event.listen(session_type, "before_flush", fail_flush)
    try:
        with audit_clock(FIXED_NOW):
            service.build_today_guidance(owner_id)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        event.remove(session_type, "before_flush", fail_flush)
    assert "SELECT" in statements
    assert flushes == []
    assert not {"INSERT", "UPDATE", "DELETE"}.intersection(statements)


def test_pending_writes_rejected_without_discard(app, owner):
    owner_id = owner.id
    owner.weight = 81
    with pytest.raises(service.GuidanceUnavailable, match="clean read boundary"):
        service.build_today_guidance(owner_id)
    assert owner in db.session.dirty


def test_zero_provider_calls(app, owner, monkeypatch):
    from app import extensions
    calls = []
    class Detonator:
        def __getattr__(self, name):
            calls.append(name)
            raise AssertionError("provider accessed")
    monkeypatch.setattr(extensions, "openai_client", Detonator())
    monkeypatch.setattr(extensions, "bedrock_client", Detonator())
    result = read(owner)
    assert calls == []
    assert result["nutrition"]["state"] == "available"
    assert result["checkin"]["state"] == "empty"


def test_bounded_output_and_no_new_ranking(app, owner):
    result = read(owner)
    assert len(json.dumps(result).encode()) <= projection.MAX_PAYLOAD_BYTES
    training = {"date": result["day"], "status": "in_progress", "action": "resume",
                "workout": result["training"]["workout"],
                "daily_context": result["training"]["daily_context"]}
    assert projection.project_guidance(training, None, None)["training"]["guidance"] == {
        "state": "in_progress", "kind": "resume_workout"}
    training["workout"] = {"summary": "x" * projection.MAX_PAYLOAD_BYTES}
    with pytest.raises(ValueError, match="bound"):
        projection.project_guidance(training, None, None)


def test_architecture_narrow_imports_and_pure_projection():
    allowed = {
        "app.extensions", "app.services", "app.services.mobile_weekly_checkin",
        "app.services.workout_state.snapshot", "app.timeutil",
        "app.services.today_guidance_projection"}
    tree = ast.parse(inspect.getsource(service))
    assert {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} <= allowed
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
    pure = ast.parse(inspect.getsource(projection))
    assert {node.module for node in ast.walk(pure) if isinstance(node, ast.ImportFrom)} == {
        "app.today_guidance"}
    assert {name.name for node in ast.walk(pure) if isinstance(node, ast.Import)
            for name in node.names} == {"json"}
    for module in (service, projection):
        source = inspect.getsource(module)
        assert not any(token in source for token in (
            ".commit(", ".flush(", ".add(", ".query", "requests.", "httpx."))
