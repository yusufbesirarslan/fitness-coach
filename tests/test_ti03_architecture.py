"""TI-03 structural boundaries: read-only, deterministic, single-authority.

Behaviour tests prove today's output; these guards prove the NEXT change cannot
quietly add a write path, a provider, notes, Pump Check, legacy history, the
current plan, randomness or read-time dependence to Training Intelligence.
"""
import ast
from pathlib import Path

import pytest

from app.services.training_intelligence import projection
from app.services.training_intelligence.models import (
    KIND_INSUFFICIENT, MISSING_CODES, RESERVED_KINDS, Evidence, Selection,
)

PACKAGE = Path("app/services/training_intelligence")
MODULES = sorted(PACKAGE.glob("*.py"))
PURE = {"models.py", "facts.py", "comparability.py", "diagnostics.py", "policy.py",
        "projection.py"}
ROUTE = Path("app/blueprints/mobile_workout_sessions.py")


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports(path):
    found = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def _names(path):
    names = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def test_the_package_exists_with_the_frozen_layering():
    assert {path.name for path in MODULES} == PURE | {"__init__.py", "queries.py", "metrics.py"}


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_no_write_path_of_any_kind(path):
    """No add/flush/commit/delete/merge/update/execute and no row-locking: the
    package cannot persist anything -- diagnostics, sessions, plans, notes."""
    always = {"add_all", "commit", "flush", "merge", "execute", "bulk_save_objects",
              "bulk_insert_mappings", "begin_nested", "with_for_update", "rollback",
              "execution_options", "connection"}
    persistence = {"add", "update", "insert", "delete"}
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            receiver = ast.unparse(node.func.value)
            assert node.func.attr not in always, (path.name, node.func.attr, node.lineno)
            if node.func.attr in persistence:
                # set.add / dict.update on local values are fine; anything on a
                # session, query, model or table is a write.
                assert not any(word in receiver for word in ("db", "session", "query", "Workout", "__table__")), (
                    path.name, receiver, node.func.attr, node.lineno)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    # no attribute assignment at all: no ORM row can be mutated
                    raise AssertionError((path.name, ast.unparse(target), node.lineno))


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_no_forbidden_authority_is_reachable(path):
    """Plan writers, generators, providers, Coach, Pump Check, completion,
    legacy name-keyed history and estimated-strength code are unreachable."""
    for module in _imports(path):
        for prefix in ("app.services.plan_mutation", "app.services.plan_replacement",
                       "app.services.plan_confirmation", "app.services.mobile_training_generation",
                       "app.services.training_generation", "app.services.coach_plan_tools",
                       "app.services.ai", "app.prompts", "app.services.pump_check",
                       "app.services.workout_completion", "app.services.training_history",
                       "app.services.training_progression", "app.services.training_planning",
                       "app.services.weekly_program", "app.services.progress_",
                       "app.services.menu_extract", "app.services.mobile_training",
                       "app.services.exercise_notes", "app.blueprints",
                       "openai", "anthropic", "boto3", "botocore", "groq", "requests",
                       "random", "secrets", "uuid", "time", "flask", "flask_login"):
            assert not module.startswith(prefix), (path.name, module)
        for model in ("TrainingPlan", "WorkoutLog", "PumpCheck", "ExerciseNote",
                      "PlanMutationRecord", "WORKOUT_COMPLETION_MARKER"):
            assert not module.endswith("." + model), (path.name, module)


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_no_notes_no_e1rm_no_read_time_and_no_logging(path):
    names = {name.lower() for name in _names(path)}
    for forbidden in ("note", "notes", "exercise_note", "exercisenote", "e1rm", "estimated_1rm",
                      "epley", "brzycki", "one_rep_max", "utcnow", "now", "app_now", "app_today",
                      "today", "random", "logger", "print", "current_app", "getLogger"):
        assert forbidden.lower() not in names, (path.name, forbidden)


@pytest.mark.parametrize("path", sorted(PACKAGE / name for name in PURE), ids=lambda p: p.name)
def test_pure_layers_never_touch_the_database_or_flask(path):
    for module in _imports(path):
        assert not module.startswith(("app.extensions", "app.models", "sqlalchemy", "flask")), (
            path.name, module)


def test_only_the_query_layer_reads_and_only_the_session_table():
    readers = [path.name for path in MODULES if "app.extensions" in _imports(path)]
    assert readers == ["queries.py"]
    imported_models = {module for module in _imports(PACKAGE / "queries.py")
                       if module.startswith("app.models.")}
    assert imported_models == {"app.models.WorkoutSession", "app.models.WORKOUT_SESSION_COMPLETED"}


def test_the_route_is_a_thin_read_transport():
    tree = _tree(ROUTE)
    view = next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == "read_training_insight")
    body = ast.Module(body=view.body, type_ignores=[])
    calls = {node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
             for node in ast.walk(body) if isinstance(node, ast.Call)}
    assert calls <= {"_insights_enabled", "_disabled", "build_training_insight", "_failure",
                     "record_insight_event", "_unavailable", "jsonify"}
    assert "request" not in {node.id for node in ast.walk(body) if isinstance(node, ast.Name)}


def test_exercise_notes_are_not_a_reachable_authority():
    """TI-01B (merged) is a separate private authority: no TI-03 module imports
    the note model or service, and the history query selects no note column."""
    from app.models import ExerciseNote
    assert ExerciseNote.__tablename__ == "exercise_note"
    for path in MODULES:
        assert "exercise_note" not in path.read_text(encoding="utf-8").lower(), path.name
    from app.services.training_intelligence import queries
    selected = {column.key for column in queries._COLUMNS}
    assert selected == {"public_id", "status", "workout_date", "completed_at", "weekday_slot",
                        "checkpoint_revision", "checkpoint_data", "execution_context_data",
                        "prescription_data"}


# ── Fail-closed projection ──────────────────────────────────────────────────
def _selection(**overrides):
    base = dict(state="available", kind="performance_improved", exercise_id="ex_barbell_back_squat",
                evidence=(Evidence("reps", 24, 25, 3, "prev"),), missing=(), recommended_action=None)
    base.update(overrides)
    return Selection(**base)


@pytest.mark.parametrize("overrides", [
    {"state": "maybe"},
    {"kind": "short_rest_confound"},
    {"kind": "you_are_fatigued"},
    {"state": "insufficient_data"},
    {"state": "not_comparable"},
    {"exercise_id": None},
    {"missing": ("tired",)},
    {"missing": ("missing_rir", "missing_rir")},
    {"evidence": (Evidence("load_volume", 1, 2, 1, None),)},
    {"evidence": (Evidence("reps", 24, 20_001, 3, None),)},
    {"evidence": (Evidence("reps", 24, None, 3, None),)},
    {"evidence": (Evidence("weight_kg", 60.0, float("inf"), 3, None),)},
    {"evidence": (Evidence("rir", "2", 2, 3, None),)},
    {"evidence": (Evidence("rir", "2", "4", 3, None),)},
    {"evidence": (Evidence("tempo", "slow", "faster", 3, None),)},
    {"evidence": (Evidence("reps", 1, 2, 21, None),)},
    {"evidence": (Evidence("reps", 1, 2, 1, None),) * 2},
    {"evidence": tuple(Evidence("reps", 1, 2, 1, None) for _ in range(9))},
    {"recommended_action": {"lever": "load", "action": "add_weight", "observe": "next_comparable_session"}},
    {"recommended_action": {"lever": "rest", "action": "restore_prescribed_rest",
                            "observe": "next_comparable_session"}},
    {"recommended_action": {"lever": "current_approach", "action": "hold_current_approach",
                            "observe": "next_comparable_session", "why": "fatigue"}},
])
def test_projection_refuses_anything_outside_the_contract(overrides):
    with pytest.raises(projection.ProjectionInvariantError):
        projection.insight_payload("ref", 1, _selection(**overrides))


def test_projection_refuses_an_action_outside_an_available_insight():
    with pytest.raises(projection.ProjectionInvariantError):
        projection.insight_payload("ref", 1, Selection(
            "insufficient_data", KIND_INSUFFICIENT, None, (), (),
            {"lever": "current_approach", "action": "hold_current_approach",
             "observe": "next_comparable_session"}))


def test_vocabularies_are_closed_and_frozen():
    assert len(MISSING_CODES) == 12 and len(set(MISSING_CODES)) == 12
    assert RESERVED_KINDS == {"short_rest_confound"}
