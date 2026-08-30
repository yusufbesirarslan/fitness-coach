"""Architecture and side-effect guards for native Training reads."""
import ast
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app import extensions
from app.extensions import db
from app.models import TrainingPlan
from app.services import mobile_auth
from app.timeutil import APP_TZ, audit_clock


SERVICE_PATH = Path("app/services/mobile_training.py")
ROUTE_PATH = Path("app/blueprints/mobile_training.py")
FIXED_NOW = datetime(2026, 7, 23, 15, 0, tzinfo=APP_TZ)
WEEKDAYS = [
    "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar",
]


def _source(path):
    return path.read_text(encoding="utf-8")


def _imports(path):
    modules = set()
    for node in ast.walk(ast.parse(_source(path))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _plan(user, exercise_count=1):
    exercise = {
        "exercise_id": "ex_barbell_back_squat",
        "isim": "Squat",
        "set": 3,
        "tekrar": "8",
        "dinlenme": "90 sn",
        "not": "",
    }
    days = []
    for index, weekday in enumerate(WEEKDAYS):
        workout = index == 3
        days.append(
            {
                "gun": weekday,
                "tip": "antrenman" if workout else "dinlenme",
                "odak": "Full body" if workout else "Recovery",
                "sure_dk": 45 if workout else 0,
                "tahmini_kalori": 300 if workout else 0,
                "egzersizler": [exercise.copy() for _ in range(exercise_count)]
                if workout
                else [],
            }
        )
    row = TrainingPlan(
        user_id=user.id,
        plan_data=json.dumps({"program": days}, ensure_ascii=False),
        score=7,
        lineage_id=f"lineage-{user.id}-{exercise_count}",
        mutation_version=2,
        created_at=datetime(2026, 7, 1, 8, 0),
    )
    db.session.add(row)
    db.session.commit()
    return row


def _headers(monkeypatch, user):
    principal = SimpleNamespace(id=user.id, cognito_sub=user.cognito_sub)
    monkeypatch.setattr(
        mobile_auth,
        "authenticate_access",
        lambda raw: mobile_auth.MobilePrincipal(
            principal, SimpleNamespace(id=1), {"sub": principal.cognito_sub}
        ),
    )
    return {"Authorization": "Bearer architecture-token"}


def test_training_projection_depends_only_on_canonical_non_http_authorities():
    modules = _imports(SERVICE_PATH) | _imports(ROUTE_PATH)

    assert "app.blueprints.training" not in modules
    assert "app.extensions" not in _imports(SERVICE_PATH)
    for module in modules:
        assert not module.startswith("app.services.ai")
        assert not module.startswith("app.prompts")
        assert module not in {"openai", "anthropic", "boto3", "groq"}


def test_training_read_modules_define_no_persistence_or_provider_operation():
    source = _source(SERVICE_PATH) + _source(ROUTE_PATH)
    for forbidden in (
        "session.add(",
        "session.delete(",
        "session.flush(",
        "session.commit(",
        "invoke_model(",
        "openai_client",
        "bedrock_client",
        "ai_gate",
        "ai_pipeline",
    ):
        assert forbidden not in source


def test_all_training_routes_use_the_shared_bearer_decorator(app):
    for endpoint in (
        "mobile_api.training_preferences",
        "mobile_api.current_training_plan",
        "mobile_api.training_workout",
    ):
        assert getattr(app.view_functions[endpoint], "_require_mobile_auth", False) is True


def test_every_training_read_succeeds_when_provider_clients_are_detonators(
    client, make_user, monkeypatch
):
    class _Detonator:
        def __getattr__(self, name):
            raise AssertionError(f"Training read reached provider method {name}")

    monkeypatch.setattr(extensions, "openai_client", _Detonator())
    monkeypatch.setattr(extensions, "bedrock_client", _Detonator())
    user = make_user("training-provider-guard")
    _plan(user)
    headers = _headers(monkeypatch, user)

    assert client.get("/api/v1/training/preferences", headers=headers).status_code == 200
    with audit_clock(FIXED_NOW):
        plan = client.get("/api/v1/training/plans/current", headers=headers)
    reference = plan.json["plan"]["current_workout_ref"]
    detail = client.get(
        f"/api/v1/training/workouts/{reference}", headers=headers
    )

    assert plan.status_code == 200
    assert detail.status_code == 200


def test_plan_and_workout_reads_never_flush_or_write(
    client, make_user, monkeypatch
):
    user = make_user("training-write-guard")
    _plan(user)
    headers = _headers(monkeypatch, user)
    session = db.session()
    flushes = []

    def _record_flush(*args):
        flushes.append("flush")

    event.listen(session, "before_flush", _record_flush)
    try:
        with audit_clock(FIXED_NOW):
            plan = client.get("/api/v1/training/plans/current", headers=headers)
        reference = plan.json["plan"]["current_workout_ref"]
        detail = client.get(
            f"/api/v1/training/workouts/{reference}", headers=headers
        )
    finally:
        event.remove(session, "before_flush", _record_flush)

    assert plan.status_code == 200
    assert detail.status_code == 200
    assert flushes == []


def _statements_for(client, path, headers, *, freeze=False):
    statements = []

    def _record(conn, cursor, statement, params, context, many):
        statements.append(" ".join(statement.split()))

    event.listen(db.engine, "before_cursor_execute", _record)
    try:
        if freeze:
            with audit_clock(FIXED_NOW):
                response = client.get(path, headers=headers)
        else:
            response = client.get(path, headers=headers)
    finally:
        event.remove(db.engine, "before_cursor_execute", _record)
    assert response.status_code == 200
    return response, statements


@pytest.mark.parametrize("exercise_count", [1, 20])
def test_training_reads_have_a_constant_bounded_query_cost(
    client, make_user, monkeypatch, exercise_count
):
    user = make_user(f"training-query-{exercise_count}")
    _plan(user, exercise_count=exercise_count)
    headers = _headers(monkeypatch, user)

    _, preference_statements = _statements_for(
        client, "/api/v1/training/preferences", headers
    )
    current, plan_statements = _statements_for(
        client, "/api/v1/training/plans/current", headers, freeze=True
    )
    reference = current.json["plan"]["current_workout_ref"]
    _, workout_statements = _statements_for(
        client, f"/api/v1/training/workouts/{reference}", headers
    )

    assert preference_statements == []
    assert len(plan_statements) <= 5, plan_statements
    assert len(workout_statements) <= 1, workout_statements
    assert sum("FROM training_plan" in item for item in plan_statements) == 1
    assert sum("FROM training_plan" in item for item in workout_statements) == 1
