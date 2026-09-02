"""Architecture and side-effect guards for native Training reads."""
import ast
import json
from itertools import product
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app import extensions
from app.extensions import db
from app.models import TrainingPlan
from app.services import mobile_auth
from app.services.mobile_training import preference_contract
from app.services.training_generation.capability import (
    STATUS_SUPPORTED,
    evaluate_capability,
)
from app.services.training_generation.models import TrainingPreferences
from app.services.training_generation.preference_contract import (
    CARDIO_DAYS,
    CARDIO_TYPES,
    DAY_COUNTS,
    EQUIPMENT_VALUES,
    STYLE_RULE_KEYS,
)
from app.timeutil import APP_TZ, audit_clock


SERVICE_PATH = Path("app/services/mobile_training.py")
ROUTE_PATH = Path("app/blueprints/mobile_training.py")
COMMAND_PATH = Path("app/services/mobile_training_generation/service.py")
CONTRACT_PATH = Path("app/services/mobile_training_generation/contract.py")
STORE_PATH = Path("app/services/mobile_training_generation/store.py")
MODEL_PATH = Path("app/models.py")
MIGRATION_PATH = Path(
    "migrations/versions/d3e4f5a6b7c8_add_training_plan_generation_operations.py")
CI_PATH = Path(".github/workflows/ci.yml")
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


def test_public_capability_constraints_match_canonical_evaluator_exhaustively():
    constraints = preference_contract()["capability_constraints"]

    def matches(constraint, preferences):
        return any(
            all(getattr(preferences, field) in values
                for field, values in alternative.items())
            for alternative in constraint["when_any"]
        )

    combinations = product(
        sorted(DAY_COUNTS), sorted(EQUIPMENT_VALUES), sorted(CARDIO_TYPES),
        sorted(CARDIO_DAYS), sorted(STYLE_RULE_KEYS),
    )
    for days, equipment, cardio_type, cardio_days, style in combinations:
        preferences = TrainingPreferences(
            gun_sayisi=days,
            ekipman=equipment,
            kardiyo_tipi=cardio_type,
            kardiyo_gun=cardio_days,
            antrenman_tarzi=style,
        )
        canonical = evaluate_capability(preferences)
        matched = next(
            (item for item in constraints if matches(item, preferences)), None
        )
        if canonical.status == STATUS_SUPPORTED:
            assert matched is None
        else:
            assert matched is not None
            assert (matched["status"], matched["reason"]) == (
                canonical.status, canonical.reason,
            )


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
    modules = _imports(SERVICE_PATH)

    assert "app.blueprints.training" not in modules
    assert "app.extensions" not in modules
    for module in modules:
        assert not module.startswith("app.services.ai")
        assert not module.startswith("app.prompts")
        assert module not in {"openai", "anthropic", "boto3", "groq"}


def test_training_read_modules_define_no_persistence_or_provider_operation():
    source = _source(SERVICE_PATH)
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
        "mobile_api.create_training_plan",
        "mobile_api.current_training_plan",
        "mobile_api.training_workout",
    ):
        assert getattr(app.view_functions[endpoint], "_require_mobile_auth", False) is True


def test_generation_route_uses_the_command_and_shared_row_projector():
    source = _source(ROUTE_PATH)

    assert "generate_and_persist(" in source
    assert "mobile_training.project_current_plan(" in source
    assert "app.blueprints.training" not in _imports(ROUTE_PATH)


def test_generation_command_uses_canonical_authorities_and_durable_ledger():
    contract = _source(CONTRACT_PATH)
    command = _source(COMMAND_PATH)
    store = _source(STORE_PATH)
    model = _source(MODEL_PATH)
    migration = _source(MIGRATION_PATH)

    assert "parse_canonical_preferences" in contract
    assert "require_supported" in contract
    assert "generate_training_plan_candidate" in command
    assert "get_active_plan" in command
    assert "provider_guard" in command
    assert "TrainingPlanGenerationOperation" in store
    assert "TrainingPlanGenerationOperation" in model
    assert "uq_training_plan_generation_user_key" in model
    assert "uq_training_plan_generation_active_owner" in migration
    assert "postgresql_where" in migration
    assert "TrainingPlan.query.delete" not in command + store
    assert "session.delete(plan" not in command + store


def test_provider_limits_wrap_only_candidate_generation():
    route = _source(ROUTE_PATH)
    command = _source(COMMAND_PATH)

    assert "def _native_generation_provider_guard" in route
    assert route.count("limiter.limit(") == 2
    assert "blocking_concurrency_slot()" in route
    assert "with provider_guard():" in command
    assert command.index("with provider_guard():") < command.index(
        "generate_training_plan_candidate(")
    assert command.index("_inspect_durable(") < command.index(
        "with provider_guard():")


def test_ci_runs_native_generation_in_postgresql_concurrency_job():
    ci = _source(CI_PATH)
    job = ci[ci.index("mobile-pg-concurrency:"):]

    assert "tests/test_mobile_training_generation_pg.py" in job


def test_generation_sources_do_not_log_or_return_sensitive_command_material():
    route = _source(ROUTE_PATH)
    command = _source(COMMAND_PATH)
    store = _source(STORE_PATH)

    for source in (route, command, store):
        assert "logger.info(" not in source
        assert "logger.warning(" not in source
        assert "logger.error(" not in source
    assert "request_fingerprint" not in route
    assert "candidate_plan_data" not in route
    assert "training_plan_id" not in route


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
    writes = []

    def _record_sql(conn, cursor, statement, params, context, many):
        verb = statement.lstrip().split(None, 1)[0].upper()
        if verb in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    event.listen(db.engine, "before_cursor_execute", _record_sql)
    try:
        preferences = client.get(
            "/api/v1/training/preferences", headers=headers
        )
        with audit_clock(FIXED_NOW):
            plan = client.get("/api/v1/training/plans/current", headers=headers)
        reference = plan.json["plan"]["current_workout_ref"]
        detail = client.get(
            f"/api/v1/training/workouts/{reference}", headers=headers
        )
    finally:
        event.remove(db.engine, "before_cursor_execute", _record_sql)

    assert preferences.status_code == 200
    assert plan.status_code == 200
    assert detail.status_code == 200
    assert writes == []


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
