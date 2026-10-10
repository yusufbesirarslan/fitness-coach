"""TD-01 PR1 structural boundaries (H-A1-A6, H-A8-A10).

Behaviour tests prove today's output; these guards prove the NEXT change
cannot quietly add a write, a legacy history source, context enrichment, a
derived metric, a broad exception handler or a production caller. Every guard
is a function over source text, and each one is shown to fire on a
representative forbidden edit (non-vacuity) in the same test run.
"""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "app/services/exercise_performance_history"
PACKAGE_DOTTED = "app.services.exercise_performance_history"
MODULES = {path.name: path for path in sorted(PACKAGE.glob("*.py"))}
PURE = ("models.py", "selection.py")
# Canonical primitives whose code the history path executes; they must not
# reach a legacy source either (the "obvious transitive" boundary).
REUSED_PRIMITIVES = ("app/services/workout_session/checkpoint.py",
                     "app/services/workout_session/errors.py",
                     "app/services/exercise_catalog.py",
                     "app/timeutil.py")


def _source(name):
    return MODULES[name].read_text(encoding="utf-8")


def _imports(source):
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = ("." * node.level) + (node.module or "")
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def _identifiers(source):
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[-1])
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
    return names


def _strings(source):
    return [node.value for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)]


# ── Guards (each returns a list of violations) ──────────────────────────────
_ALWAYS_WRITE = {"add", "add_all", "flush", "commit", "merge", "execute", "delete", "update",
                 "insert", "with_for_update", "begin_nested", "rollback", "connection",
                 "bulk_save_objects", "bulk_insert_mappings", "execution_options"}


def write_violations(source):
    """H-A2: no write call of any kind and no attribute assignment."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _ALWAYS_WRITE:
                found.append(f"call {node.func.attr} line {node.lineno}")
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for sub in ast.walk(target):
                    if isinstance(sub, (ast.Attribute, ast.Subscript)) and isinstance(
                            getattr(sub, "ctx", None), ast.Store):
                        found.append(f"assign {ast.unparse(sub)} line {node.lineno}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in (
                "setattr", "delattr"):
            found.append(f"call {node.func.id} line {node.lineno}")
    return found


_FORBIDDEN_IMPORT_PREFIXES = tuple(f"app.services.{name}" for name in (
    "training_history", "training_progression", "training_planning", "weekly_program",
    "progress_", "adaptive_plan_context", "workout_state", "today_facts", "today_presenter",
    "mobile_today", "today_guidance_read_model", "analytics_engine", "context_builder",
    "coach_handoff", "ai", "ai_coach", "ai_pipeline", "coach_plan_tools", "coach_plan_policy",
    "plan_mutation", "plan_replacement", "plan_confirmation", "training_generation",
    "mobile_training", "mobile_training_generation", "training_intelligence",
    "workout_session.prior_performance", "workout_session.context",
    "workout_session.prescription", "workout_session.service", "workout_session.queries",
    "workout_session.execution", "workout_completion", "pump_check", "exercise_notes",
    "nutrition", "memory_manager",
)) + ("app.blueprints", "fitx_mcp", "app.prompts", "app.today_presenter", "app.coach_handoff",
      "openai", "anthropic", "boto3", "botocore", "groq", "requests", "httpx",
      "random", "secrets", "uuid", "time", "flask", "flask_login", "logging")


def import_violations(source):
    """H-A4: no import of a legacy history, derived output, Coach, Today,
    context projection, transport, provider, logger or randomness."""
    return sorted(module for module in _imports(source)
                  if module.startswith(_FORBIDDEN_IMPORT_PREFIXES))


_FORBIDDEN_SYMBOLS = ("WorkoutLog", "TrainingPlan", "PumpCheck", "ExerciseNote",
                      "PlanMutationRecord", "WORKOUT_COMPLETION_MARKER", "fetch_workout_entries",
                      "load_prior_performance", "project_context", "build_progression_report",
                      "build_training_history_summary")
_FORBIDDEN_LITERALS = ("workout_log", "exercise_note", "execution_context_data",
                       "prescription_data", "FITX_")


def symbol_violations(source):
    """H-A5: no legacy model/symbol name and no legacy table literal."""
    names = _identifiers(source)
    found = [name for name in _FORBIDDEN_SYMBOLS if name in names]
    lowered = [text.lower() for text in _strings(source)]
    found += [literal for literal in _FORBIDDEN_LITERALS
              if any(literal.lower() in text for text in lowered)]
    found += [name for name in ("execution_context_data", "prescription_data") if name in names]
    return found


_DERIVED_FRAGMENTS = ("e1rm", "epley", "brzycki", "one_rep_max", "onerepmax", "volume", "trend",
                      "progress", "recommend", "deload", "recovery", "score", "rir", "tempo",
                      "rest_seconds", "actual_rest")
_LOGGING_NAMES = ("logger", "getlogger", "print", "current_app", "suppress")
_CLOCK_NAMES = ("now", "utcnow", "app_now", "today", "app_today", "time", "monotonic")


def derived_violations(source, *, may_read_clock=False):
    """H-A6: no derived-metric, logging or (outside __init__) clock name."""
    names = {name.lower() for name in _identifiers(source)}
    found = sorted(f"derived:{name}" for name in names
                   if any(fragment in name for fragment in _DERIVED_FRAGMENTS))
    found += sorted(f"log:{name}" for name in names if name in _LOGGING_NAMES)
    clock = {name for name in names if name in _CLOCK_NAMES}
    if may_read_clock:
        clock -= {"app_today"}
    found += sorted(f"clock:{name}" for name in clock)
    return found


def pure_layer_violations(source):
    """H-A1: the pure layers never touch the database, ORM or Flask."""
    return sorted(module for module in _imports(source)
                  if module.startswith(("app.extensions", "app.models", "sqlalchemy", "flask")))


def selected_columns(source):
    """H-A3: the attribute names inside the ``_COLUMNS`` tuple of queries.py."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "_COLUMNS" for target in node.targets):
            return {element.attr for element in node.value.elts}
    return None


def resolver_violations(source):
    """H-A9: the active-only resolver is never used by the package."""
    return ["resolve_exercise"] if "resolve_exercise" in _identifiers(source) else []


def exception_violations(source):
    """H-A10 (C-1): only the two approved, single-statement handlers exist."""
    found = []
    handlers = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Try) or type(node).__name__ == "TryStar":
            if node.finalbody or node.orelse:
                found.append(f"try with else/finally line {node.lineno}")
            if len(node.body) != 1:
                found.append(f"try body with {len(node.body)} statements line {node.lineno}")
            for handler in node.handlers:
                if handler.type is None:
                    found.append(f"bare except line {handler.lineno}")
                elif not isinstance(handler.type, ast.Name):
                    found.append(f"handler {ast.unparse(handler.type)} line {handler.lineno}")
                else:
                    handlers.append((handler.type.id, ast.unparse(node.body[0])))
    for name, statement in handlers:
        if name == "InvalidSessionRequest":
            if "parse_stored_exercises(load_snapshot(" not in statement:
                found.append(f"InvalidSessionRequest guards {statement!r}")
        elif name == "ValueError":
            if "date.fromisoformat(" not in statement:
                found.append(f"ValueError guards {statement!r}")
        else:
            found.append(f"handler {name}")
    return found


def caller_violations(source):
    """H-A8: does this (non-package, non-test) module import or call the service?"""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names if alias.name.startswith(PACKAGE_DOTTED)]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(PACKAGE_DOTTED):
                found.append(module)
            elif module == "app.services" and any(
                    alias.name == "exercise_performance_history" for alias in node.names):
                found.append(PACKAGE_DOTTED)
        elif isinstance(node, (ast.Name, ast.Attribute)):
            name = node.id if isinstance(node, ast.Name) else node.attr
            if name in ("build_exercise_history", "exercise_performance_history"):
                found.append(name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "exercise_performance_history" in node.value:
                found.append(f"string {node.value[:60]!r}")
    return found


# ── H-A1 ────────────────────────────────────────────────────────────────────
def test_h_a1_frozen_pr1_module_set():
    assert set(MODULES) == {"__init__.py", "models.py", "queries.py", "selection.py"}


@pytest.mark.parametrize("name", PURE)
def test_h_a1_pure_layers_never_touch_the_database_or_flask(name):
    assert pure_layer_violations(_source(name)) == []


# ── H-A2 ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", sorted(MODULES))
def test_h_a2_no_write_path(name):
    assert write_violations(_source(name)) == []


# ── H-A3 ────────────────────────────────────────────────────────────────────
def test_h_a3_only_queries_reads_and_only_five_session_columns():
    readers = sorted(name for name in MODULES if any(
        module.startswith("app.extensions") for module in _imports(_source(name))))
    assert readers == ["queries.py"]
    model_imports = {module for name in MODULES for module in _imports(_source(name))
                     if module.startswith("app.models.")}
    assert model_imports == {"app.models.WorkoutSession", "app.models.WORKOUT_SESSION_COMPLETED"}
    expected = {"public_id", "workout_date", "completed_at", "checkpoint_revision", "checkpoint_data"}
    assert selected_columns(_source("queries.py")) == expected
    from app.services.exercise_performance_history import queries
    assert {column.key for column in queries._COLUMNS} == expected


# ── H-A4 / H-A5 / H-A6 / H-A9 ───────────────────────────────────────────────
@pytest.mark.parametrize("name", sorted(MODULES))
def test_h_a4_no_forbidden_authority_is_imported(name):
    assert import_violations(_source(name)) == []


@pytest.mark.parametrize("name", sorted(MODULES))
def test_h_a5_no_legacy_model_symbol_or_literal(name):
    assert symbol_violations(_source(name)) == []


@pytest.mark.parametrize("name", sorted(MODULES))
def test_h_a6_no_derived_metric_logging_or_read_time_clock(name):
    assert derived_violations(_source(name), may_read_clock=(name == "__init__.py")) == []


def test_h_a6_clock_is_read_once_and_only_in_the_orchestrator():
    calls = [node for node in ast.walk(ast.parse(_source("__init__.py")))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id == "app_today"]
    assert len(calls) == 1


@pytest.mark.parametrize("name", sorted(MODULES))
def test_h_a9_active_only_resolver_is_not_used(name):
    assert resolver_violations(_source(name)) == []


def test_h_a9_historical_resolver_is_used():
    assert "resolve_historical_exercise" in _identifiers(_source("__init__.py"))


def test_reused_primitives_do_not_reach_a_legacy_source():
    for relative in REUSED_PRIMITIVES:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert import_violations(source) == [], relative
        names = _identifiers(source)
        assert not {"WorkoutLog", "fetch_workout_entries", "load_prior_performance",
                    "project_context"} & names, relative


# ── H-A8 ────────────────────────────────────────────────────────────────────
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "tests", ".pytest_cache"}


def _production_modules():
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT)
        if set(relative.parts[:-1]) & _SKIP_DIRS or relative.parts[0].startswith("."):
            continue
        if path.is_relative_to(PACKAGE):
            continue
        yield path


def test_h_a8_zero_production_callers():
    modules = list(_production_modules())
    assert len(modules) > 200 and any(path.name == "mobile_api.py" for path in modules)
    callers = {}
    for path in modules:
        found = caller_violations(path.read_text(encoding="utf-8"))
        if found:
            callers[str(path.relative_to(ROOT))] = found
    assert callers == {}, ("PR1 ships with ZERO production callers; a later approved "
                           "slice must change this guard explicitly", callers)


# ── H-A10 ───────────────────────────────────────────────────────────────────
def test_h_a10_exactly_two_approved_handlers():
    handlers = []
    for name in MODULES:
        assert exception_violations(_source(name)) == [], name
        for node in ast.walk(ast.parse(_source(name))):
            if isinstance(node, ast.ExceptHandler):
                handlers.append((name, ast.unparse(node.type)))
    assert sorted(handlers) == [("selection.py", "InvalidSessionRequest"),
                                ("selection.py", "ValueError")]


# ── Non-vacuity: every guard fires on a representative forbidden edit ───────
def _mutate(name, before, after):
    source = _source(name)
    assert source.count(before) == 1, (name, before)
    return source.replace(before, after)


_SELECTION_TRY = "        return parse_stored_exercises(load_snapshot(checkpoint_data))\n    except InvalidSessionRequest:"


@pytest.mark.parametrize("guard, name, before, after", [
    (write_violations, "queries.py", "    candidates = tuple(", "    db.session.commit()\n    candidates = tuple("),
    (write_violations, "queries.py", "        .limit(MAX_SCAN_ROWS)\n",
     "        .limit(MAX_SCAN_ROWS).with_for_update()\n"),
    (write_violations, "selection.py", "    occurrences = []\n",
     "    occurrences = []\n    rows[0].completed_at = None\n"),
    (import_violations, "selection.py", "from app.timeutil import app_date_of\n",
     "from app.timeutil import app_date_of\n"
     "from app.services.workout_session.prior_performance import load_prior_performance\n"),
    (import_violations, "selection.py", "from app.timeutil import app_date_of\n",
     "from app.timeutil import app_date_of\nfrom app.services.workout_session.context import project_context\n"),
    (import_violations, "queries.py", "from app.extensions import db\n",
     "from app.extensions import db\nfrom app.services.training_history import fetch_workout_entries\n"),
    (import_violations, "selection.py", "import math\n", "import math\nimport logging\n"),
    (symbol_violations, "queries.py", "from app.models import WORKOUT_SESSION_COMPLETED, WorkoutSession\n",
     "from app.models import WORKOUT_SESSION_COMPLETED, WorkoutLog, WorkoutSession\n"),
    (symbol_violations, "queries.py", "    WorkoutSession.checkpoint_data,\n)",
     "    WorkoutSession.checkpoint_data,\n    WorkoutSession.execution_context_data,\n)"),
    (symbol_violations, "selection.py", "_MAX_REPS = 1_000\n", '_MAX_REPS = 1_000\n_T = "workout_log"\n'),
    (derived_violations, "selection.py", "_MAX_REPS = 1_000\n", "_MAX_REPS = 1_000\ndef e1rm(x):\n    return x\n"),
    (derived_violations, "selection.py", "    occurrences = []\n",
     "    occurrences = []\n    total_volume = 0\n"),
    (derived_violations, "selection.py", "    occurrences = []\n", "    occurrences = []\n    print(rows)\n"),
    (derived_violations, "selection.py", "    occurrences = []\n",
     "    occurrences = []\n    anchor = app_today()\n"),
    (pure_layer_violations, "selection.py", "import math\n", "import math\nfrom app.extensions import db\n"),
    (resolver_violations, "__init__.py", "    resolve_historical_exercise(exercise_id)\n",
     "    resolve_historical_exercise(exercise_id)\n    resolve_exercise(exercise_id)\n"),
    (exception_violations, "selection.py", _SELECTION_TRY,
     _SELECTION_TRY.replace("except InvalidSessionRequest:", "except Exception:")),
    (exception_violations, "selection.py", _SELECTION_TRY,
     _SELECTION_TRY.replace("except InvalidSessionRequest:", "except (InvalidSessionRequest, RecursionError):")),
    (exception_violations, "selection.py", _SELECTION_TRY,
     _SELECTION_TRY.replace("except InvalidSessionRequest:", "except RecursionError:")),
    (exception_violations, "selection.py", _SELECTION_TRY,
     _SELECTION_TRY.replace("except InvalidSessionRequest:", "except:")),
    (exception_violations, "selection.py", _SELECTION_TRY,
     "        snapshot = load_snapshot(checkpoint_data)\n" + _SELECTION_TRY),
    (exception_violations, "queries.py", "        .all()\n    )\n",
     "        .all()\n    )\n    try:\n        pass\n    except Exception:\n        rows = []\n"),
    (exception_violations, "selection.py", "        return date.fromisoformat(raw)\n    except ValueError:",
     "        return date.fromisoformat(raw)\n    except (ValueError, TypeError):"),
], ids=lambda value: getattr(value, "__name__", None))
def test_guards_are_non_vacuous(guard, name, before, after):
    assert guard(_source(name)) == []
    assert guard(_mutate(name, before, after)) != []


def test_column_pin_is_non_vacuous():
    widened = _mutate("queries.py", "    WorkoutSession.checkpoint_data,\n)",
                      "    WorkoutSession.checkpoint_data,\n    WorkoutSession.prescription_data,\n)")
    assert "prescription_data" in selected_columns(widened)


@pytest.mark.parametrize("snippet", [
    "from app.services.exercise_performance_history import build_exercise_history\n",
    "import app.services.exercise_performance_history as history\n",
    "from app.services import exercise_performance_history\n",
    "from app.services.exercise_performance_history.queries import load_candidates\n",
    "import importlib\nmodule = importlib.import_module('app.services.exercise_performance_history')\n",
    "def view(user):\n    return services.exercise_performance_history.build_exercise_history(user.id, 'x')\n",
])
def test_caller_guard_is_non_vacuous(snippet):
    assert caller_violations("import os\n") == []
    assert caller_violations(snippet) != []
