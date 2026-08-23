"""Sprint 12 PR1 - characterization tests for the Daily Coach discovery.

These are DISCOVERY tests. They do not assert what AxisAI should become; they
pin down what it *is* today, so the architecture report's claims are executable
rather than prose, and so a later PR that changes one of these facts has to say
so out loud.

Every assertion below corresponds to a numbered finding in
``docs/superpowers/specs/2026-08-23-sprint12-pr1-daily-coach-convergence-discovery.md``.
Nothing here is a snapshot of copy, layout or payload shape - each test proves a
structural fact about ownership, composition order, or reachability.
"""
import ast
import json
import os
from datetime import date
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


def _source(relative):
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


# -- F1: the mobile API has no Daily-Coach domain ---------------------------
# The whole convergence question turns on this. `/api/v1` publishes auth,
# nutrition and Pump Check - and nothing about training, workout state,
# progress, check-ins or the Coach. A mobile Today surface therefore cannot be
# assembled from existing mobile endpoints at all: it is not a composition
# problem, it is a missing-contract problem.

@pytest.fixture(scope="module")
def mobile_enabled_app():
    """An app with the `/api/v1` blueprint registered.

    Built once: `create_app()` is expensive, and every test in this group only
    reads the URL map.
    """
    previous = {
        key: os.environ.get(key)
        for key in (
            "MOBILE_AUTH_ENABLED",
            "MOBILE_AUTH_DERIVATION_KEYRING",
            "MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION",
        )
    }
    os.environ["MOBILE_AUTH_ENABLED"] = "1"
    os.environ["MOBILE_AUTH_DERIVATION_KEYRING"] = json.dumps(
        {"1": "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s"})
    os.environ["MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION"] = "1"
    try:
        from app import create_app

        flask_app = create_app()
        flask_app.config["TESTING"] = True
        yield flask_app
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _mobile_paths(flask_app):
    return {
        rule.rule for rule in flask_app.url_map.iter_rules()
        if rule.endpoint.startswith("mobile_api.")
    }


def test_mobile_api_publishes_only_auth_nutrition_and_pump_check(
        mobile_enabled_app):
    """F1 - the entire mobile contract, enumerated.

    Kept as an exact set on purpose: the point of the finding is the *absence*
    of whole domains, and a subset assertion would not notice one arriving.
    """
    assert _mobile_paths(mobile_enabled_app) == {
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/account/me",
        "/api/v1/nutrition/diary/today",
        "/api/v1/nutrition/foods/search",
        "/api/v1/nutrition/foods/fatsecret/<food_id>/servings",
        "/api/v1/nutrition/foods/barcode",
        "/api/v1/nutrition/logs",
        "/api/v1/nutrition/logs/<entry_token>",
        "/api/v1/pump-checks",
        "/api/v1/pump-checks/<pump_check_token>",
        "/api/v1/pump-check-comparisons",
        "/api/v1/pump-check-comparisons/<comparison_id>",
    }


@pytest.mark.parametrize(
    "domain",
    ["training", "workout", "plan", "progress", "coach", "checkin", "today"],
)
def test_no_mobile_endpoint_serves_a_daily_coach_domain(
        mobile_enabled_app, domain):
    """F1 - no mobile route mentions any Today signal domain.

    `plan` is checked too: the only `/api/v1` path that could contain it would
    be a training plan, since nutrition plans are not published to mobile
    either. `today` is checked to prove no aggregate exists yet - the one
    `/nutrition/diary/today` path is the single, domain-scoped exception.
    """
    offenders = {
        path for path in _mobile_paths(mobile_enabled_app)
        if domain in path and path != "/api/v1/nutrition/diary/today"
    }
    assert offenders == set(), (
        "a mobile endpoint now serves '%s'; the discovery report's "
        "missing-contract finding is out of date: %s"
        % (domain, sorted(offenders)))


# -- F2: Today already has a canonical composition on web -------------------
# `app/services/today_facts.py` composes two existing authorities and owns no
# rules of its own. It must keep delegating rather than growing a third query,
# and the presenter above it must stay pure.

def test_today_facts_delegates_completion_and_owns_no_query_of_its_own():
    """F2 - the Today read layer composes; it does not re-derive."""
    source = _source("app/services/today_facts.py")
    assert "from app.services.workout_state import resolve_workout_state" in source
    # The day-bounded completion query lives in app/services/workout_state.
    # A PumpCheck query here would be that query's second home. The docstring
    # mentions the model by name, so look for the query, not the noun.
    assert "PumpCheck.query" not in source


def test_today_presenter_is_pure():
    """F2 - no I/O, no ORM, no clock in the presentation layer.

    Enforced by parsing imports rather than substring matching, so a name that
    merely appears inside a docstring cannot fail the test.
    """
    tree = ast.parse(_source("app/today_presenter.py"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {"flask", "app.extensions", "app.models", "app.timeutil",
                 "datetime", "app.services.workout_state"}
    assert imported & forbidden == set(), (
        "the Today presenter gained an impure dependency: %s"
        % sorted(imported & forbidden))


# -- F3: three Today state vocabularies exist -------------------------------
# The web presenter, the canonical workout-state resolver and the mobile draft
# fixture each name "today" differently. Sprint 12 must converge on one; this
# test records that they are, right now, not one.

def test_web_today_states_and_workout_state_vocabulary_do_not_match():
    """F3 - the web Today state ids are not the canonical workout-state ids."""
    from app import today_presenter
    from app.services.workout_state import models as ws

    web_states = {
        today_presenter.STATE_NO_PLAN,
        today_presenter.STATE_PLAN_READY,
        today_presenter.STATE_WORKOUT_DONE,
        today_presenter.STATE_ERROR,
    }
    canonical_states = {
        ws.PRIMARY_REST_DAY,
        ws.PRIMARY_SCHEDULED_NOT_STARTED,
        ws.PRIMARY_EXECUTION_RECORDED,
        ws.PRIMARY_COMPLETED,
        ws.PRIMARY_UNSCHEDULED_EXECUTION,
        ws.PRIMARY_UNSCHEDULED_COMPLETED,
        ws.PRIMARY_NO_PLAN,
        ws.PRIMARY_NEEDS_ATTENTION,
        ws.PRIMARY_IN_PROGRESS,
    }
    # `no_plan` is the single shared token; everything else diverges. The web
    # presenter cannot express rest_day at all, which is why with the flag on
    # it renders "View plan" as the dominant CTA on a rest day.
    assert web_states & canonical_states == {ws.PRIMARY_NO_PLAN}
    assert ws.PRIMARY_REST_DAY not in web_states


# -- F4: today's plan projection carries no canonical exercise identity -----
# Sprint 11 PR4 made exercise identity server-owned, but the authority reaches
# the plan document only. The wire projection for *today's* workout - the one a
# Today surface would render - still publishes a name.

def test_today_plan_projection_publishes_names_not_canonical_ids():
    """F4 - `serialize_today_plan` drops `exercise_id` even when present."""
    from app.services.training_generation.plan_schema import EXERCISE_ID_KEY
    from app.services.workout_state.serialization import serialize_today_plan

    monday = date(2026, 8, 24)  # a Monday -> "Pazartesi"
    weekdays = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma",
                "Cumartesi", "Pazar")
    plan_data = {"program": [
        {
            "gun": gun,
            "tip": "antrenman" if gun == "Pazartesi" else "dinlenme",
            "odak": "full body",
            "sure_dk": 45,
            "tahmini_kalori": 300,
            "egzersizler": ([{
                "isim": "Bench Press",
                "set": 3,
                "tekrar": "8",
                "dinlenme": "90s",
                "not": "",
                EXERCISE_ID_KEY: "ex_barbell_bench_press",
            }] if gun == "Pazartesi" else []),
        }
        for gun in weekdays
    ]}

    projected = serialize_today_plan(plan_data, monday)

    assert projected is not None
    exercise = projected["egzersizler"][0]
    assert exercise["isim"] == "Bench Press"
    assert EXERCISE_ID_KEY not in exercise, (
        "today's workout now publishes canonical exercise identity; the "
        "discovery report's F4 finding is resolved and must be updated")


# -- F5: no read path exposes a pending plan confirmation -------------------
# A pending Adaptive Coaching proposal is only observable from inside an AI
# Coach turn. No HTTP route, and no non-Coach service, can answer "is a plan
# change waiting for me?" - so Today cannot surface one today.

def test_pending_plan_confirmation_is_reachable_only_from_coach_plan_tools():
    """F5 - `get_pending` has exactly one consumer outside its own package."""
    consumers = set()
    for path in (REPO_ROOT / "app").rglob("*.py"):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative.startswith("app/services/plan_confirmation/"):
            continue
        if "get_pending" in path.read_text(encoding="utf-8"):
            consumers.add(relative)
    assert consumers == {"app/services/coach_plan_tools/executor.py"}, (
        "pending-proposal readers changed: %s" % sorted(consumers))


def test_no_blueprint_reads_the_plan_mutation_journal():
    """F5 - "why did my plan change?" has no HTTP surface either."""
    offenders = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "app" / "blueprints").rglob("*.py")
        if "plan_mutation" in path.read_text(encoding="utf-8")
    }
    assert offenders == set(), (
        "a blueprint now reads plan mutation history: %s" % sorted(offenders))


# -- F6: P2-16 - injury annotation runs before canonical resolution ---------
# `annotate_injuries` matches on the provider's raw exercise string; catalog
# resolution happens afterwards. Two aliases of one canonical exercise can
# therefore be annotated differently, and the note persists into `plan_data`.

def test_injury_annotation_precedes_canonical_exercise_resolution():
    """P2-16 - proven by call order in the generation service, not by comment."""
    tree = ast.parse(_source("app/services/training_generation/service.py"))

    target = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "generate_training_plan_payload"
    )
    # Compare source LINES, not `ast.walk` order: walk is breadth-first, so its
    # sequence says nothing about which call runs first.
    lines = {"_parse_and_validate": [], "canonicalize_plan_exercises": []}
    for node in ast.walk(target):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in lines):
            lines[node.func.id].append(node.lineno)

    assert lines["_parse_and_validate"], "validation call site disappeared"
    assert lines["canonicalize_plan_exercises"], "canonicalization call site disappeared"
    # Every parse/validate call - and `_parse_and_validate` is what runs
    # `annotate_injuries` - precedes every canonicalization call.
    assert (max(lines["_parse_and_validate"])
            < min(lines["canonicalize_plan_exercises"])), (
        "canonicalization now runs before validation/annotation; re-assess "
        "P2-16 in the discovery report")

    # The annotation really is inside the earlier of the two.
    validate_source = _source(
        "app/services/training_generation/response_validator.py")
    assert "warnings = annotate_injuries(structured, injuries)" in validate_source

    # ...and the annotation really does match on the raw provider name.
    validator = _source(
        "app/services/training_generation/response_validator.py")
    assert 'find_contraindicated(ex["isim"], injuries)' in validator, (
        "the injury overlay no longer matches on the raw provider name; "
        "P2-16 may be resolved")


def test_injury_warning_text_persists_into_the_stored_plan():
    """P2-16 severity - the inconsistent annotation is durable, not transient."""
    from app.services.training_generation.plan_schema import EXERCISE_KEYS

    # `annotate_injuries` writes the warning into `ex["not"]`, and `not` is a
    # persisted plan-schema key, so the annotation survives into
    # TrainingPlan.plan_data and into today's projection.
    assert "not" in EXERCISE_KEYS
    assert 'ex["not"] = f"{warn}. {note}"' in _source(
        "app/services/training_generation/response_validator.py")


# -- F7: "today" is a server-owned Istanbul day, everywhere ------------------
# Every Today-relevant module derives the day from app/timeutil. No client
# input and no server-local date reaches the boundary.

@pytest.mark.parametrize("module", [
    "app/services/today_facts.py",
    "app/services/workout_state/queries.py",
    "app/services/mobile_nutrition/__init__.py",
    "app/services/workout_state/__init__.py",
])
def test_today_signals_take_their_day_from_the_istanbul_authority(module):
    """F7 - no `date.today()` / `datetime.now()` in a Today-signal module."""
    source = _source(module)
    assert "date.today()" not in source
    assert "datetime.now()" not in source
    # today_facts delegates its day entirely; the others read it from timeutil.
    if module != "app/services/today_facts.py":
        assert "from app.timeutil import" in source


def test_mobile_nutrition_publishes_the_zone_that_resolved_the_day():
    """F7 - the mobile contract states its day boundary rather than implying it."""
    from app.services.mobile_nutrition.serialization import diary_day_payload

    payload = diary_day_payload(
        date(2026, 8, 23), [], None,
        lambda entry_id: "id", lambda entry: "rev")
    assert payload["day"] == {
        "date": "2026-08-23", "timezone": "Europe/Istanbul"}


# -- F8: completion is a Pump Check, and it is expensive ---------------------
# "Mark today's workout done" is not a cheap toggle: the only completion write
# runs a Bedrock vision call behind the AI concurrency gate. Any Today CTA that
# implies one-tap completion would be misrepresenting the system.

def test_workout_completion_is_gated_behind_the_ai_concurrency_gate():
    """F8 - /workout/complete is an AI-bound write, not a state toggle."""
    source = _source("app/blueprints/training.py")
    marker = '@bp.route("/workout/complete", methods=["POST"])'
    assert marker in source
    decorators = source[source.index(marker):source.index(
        "def complete_workout()")]
    assert "@ai_concurrency_gate" in decorators
    assert "BEDROCK_RATELIMIT" in decorators


# -- F9: no canonical recovery / readiness state exists ---------------------
# The brief warns against assuming one. Nothing in the application publishes a
# recovery or readiness classification; the closest signals are a nudge
# heuristic over the last check-in and the planner's weekly `deload` focus.

def test_no_module_publishes_a_recovery_or_readiness_score():
    """F9 - the concept does not exist as a canonical field anywhere."""
    offenders = set()
    for path in (REPO_ROOT / "app").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for token in ("readiness_score", "recovery_score", "readiness_state"):
            if token in source:
                offenders.add(path.relative_to(REPO_ROOT).as_posix())
    assert offenders == set(), (
        "a readiness/recovery score appeared: %s" % sorted(offenders))


def test_check_in_has_no_due_read_model():
    """F9 - "is a check-in due?" is not answerable from any published surface.

    `/checkin` writes, `/checkin-history` lists. Neither computes a cadence, a
    due date, or a "due" flag - the only elapsed-days arithmetic happens inside
    the POST, as context for the AI feedback string.
    """
    source = _source("app/blueprints/tracking.py")
    for token in ("checkin_due", "check_in_due", "next_checkin", "is_due"):
        assert token not in source
