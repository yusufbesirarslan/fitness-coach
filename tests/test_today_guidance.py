"""Pure decision-table tests for UX-2 PR5 Today guidance."""

import ast
import inspect
from pathlib import Path

import pytest

from app.services.workout_state.models import (
    ACTION_BLOCKED,
    ACTION_NONE,
    ACTION_RESUME,
    ACTION_START,
    PRIMARY_COMPLETED,
    PRIMARY_EXECUTION_RECORDED,
    PRIMARY_IN_PROGRESS,
    PRIMARY_NEEDS_ATTENTION,
    PRIMARY_NO_PLAN,
    PRIMARY_REST_DAY,
    PRIMARY_SCHEDULED_NOT_STARTED,
    PRIMARY_UNSCHEDULED_COMPLETED,
    PRIMARY_UNSCHEDULED_EXECUTION,
    CANONICAL_PRIMARY_STATES,
)
from app.today_guidance import (
    CANDIDATE_CREATE_PLAN,
    CANDIDATE_RESUME_WORKOUT,
    CANDIDATE_START_WORKOUT,
    PRIORITY_CREATE_PLAN,
    PRIORITY_RESUME_WORKOUT,
    PRIORITY_START_WORKOUT,
    SUPPORTED_STATE_ACTIONS,
    STATE_ERROR,
    Candidate,
    decide_today_guidance,
    rank_candidates,
)

ROOT = Path(__file__).resolve().parents[1]


def test_resume_outranks_start_and_create_plan():
    """An active canonical session must win every lower-value collision."""
    candidates = (
        Candidate(CANDIDATE_CREATE_PLAN, PRIORITY_CREATE_PLAN,
                  "canonical_no_plan"),
        Candidate(CANDIDATE_START_WORKOUT, PRIORITY_START_WORKOUT,
                  "canonical_start"),
        Candidate(CANDIDATE_RESUME_WORKOUT, PRIORITY_RESUME_WORKOUT,
                  "canonical_resume"),
    )

    assert rank_candidates(candidates).kind == CANDIDATE_RESUME_WORKOUT


def test_start_outranks_create_plan():
    """A scheduled canonical workout must beat plan setup if candidates collide."""
    candidates = (
        Candidate(CANDIDATE_CREATE_PLAN, PRIORITY_CREATE_PLAN,
                  "canonical_no_plan"),
        Candidate(CANDIDATE_START_WORKOUT, PRIORITY_START_WORKOUT,
                  "canonical_start"),
    )

    assert rank_candidates(candidates).kind == CANDIDATE_START_WORKOUT


@pytest.mark.parametrize(("state", "action", "primary_kind", "reason"), [
    (PRIMARY_IN_PROGRESS, ACTION_RESUME,
     CANDIDATE_RESUME_WORKOUT, "canonical_resume"),
    (PRIMARY_SCHEDULED_NOT_STARTED, ACTION_START,
     CANDIDATE_START_WORKOUT, "canonical_start"),
    (PRIMARY_NO_PLAN, ACTION_NONE,
     CANDIDATE_CREATE_PLAN, "canonical_no_plan"),
    (PRIMARY_REST_DAY, ACTION_NONE, None, "no_primary_action"),
    (PRIMARY_EXECUTION_RECORDED, ACTION_NONE, None, "no_primary_action"),
    (PRIMARY_UNSCHEDULED_EXECUTION, ACTION_NONE, None, "no_primary_action"),
    (PRIMARY_COMPLETED, ACTION_NONE, None, "no_primary_action"),
    (PRIMARY_UNSCHEDULED_COMPLETED, ACTION_NONE, None, "no_primary_action"),
    (PRIMARY_NEEDS_ATTENTION, ACTION_BLOCKED, None, "no_primary_action"),
])
def test_canonical_state_action_matrix(state, action, primary_kind, reason):
    """Changing any compatibility branch must change an observable decision."""
    decision = decide_today_guidance(
        read_ok=True, primary_state=state, action=action)

    assert decision.state == state
    assert decision.primary_kind == primary_kind
    assert decision.emphasis == state
    assert decision.decision_reason == reason


@pytest.mark.parametrize(("state", "action"), [
    (PRIMARY_SCHEDULED_NOT_STARTED, ACTION_RESUME),
    (PRIMARY_COMPLETED, ACTION_START),
    (PRIMARY_REST_DAY, ACTION_START),
    (PRIMARY_NO_PLAN, ACTION_BLOCKED),
    (PRIMARY_IN_PROGRESS, ACTION_START),
])
def test_incompatible_state_action_pair_fails_closed(state, action):
    """A drifted action may never create a CTA for a different product state."""
    decision = decide_today_guidance(
        read_ok=True, primary_state=state, action=action)

    assert decision.state == STATE_ERROR
    assert decision.primary_kind is None
    assert decision.emphasis == STATE_ERROR
    assert decision.decision_reason == "incompatible_state_action"


def test_read_failure_ignores_populated_values_and_fails_closed():
    decision = decide_today_guidance(
        read_ok=False,
        primary_state=PRIMARY_IN_PROGRESS,
        action=ACTION_RESUME,
    )

    assert decision.state == STATE_ERROR
    assert decision.primary_kind is None
    assert decision.decision_reason == "workout_read_unavailable"


def test_unknown_canonical_state_fails_closed():
    decision = decide_today_guidance(
        read_ok=True,
        primary_state="future_state",
        action=ACTION_NONE,
    )

    assert decision.state == STATE_ERROR
    assert decision.primary_kind is None
    assert decision.decision_reason == "unsupported_primary_state"


def test_supported_pairs_cover_the_canonical_primary_vocabulary_exactly():
    """Adding a canonical state without a fail-closed compatibility row is a bug."""
    expected = {
        (PRIMARY_IN_PROGRESS, ACTION_RESUME),
        (PRIMARY_SCHEDULED_NOT_STARTED, ACTION_START),
        (PRIMARY_NO_PLAN, ACTION_NONE),
        (PRIMARY_REST_DAY, ACTION_NONE),
        (PRIMARY_EXECUTION_RECORDED, ACTION_NONE),
        (PRIMARY_UNSCHEDULED_EXECUTION, ACTION_NONE),
        (PRIMARY_COMPLETED, ACTION_NONE),
        (PRIMARY_UNSCHEDULED_COMPLETED, ACTION_NONE),
        (PRIMARY_NEEDS_ATTENTION, ACTION_BLOCKED),
    }

    assert set(SUPPORTED_STATE_ACTIONS) == expected
    assert {state for state, _action in SUPPORTED_STATE_ACTIONS} == set(
        CANONICAL_PRIMARY_STATES)


def test_decision_api_cannot_accept_lower_authority_domain_inputs():
    """Nutrition, check-ins, clock data, and raw rows cannot enter ranking."""
    assert tuple(inspect.signature(decide_today_guidance).parameters) == (
        "read_ok", "primary_state", "action")


def test_guidance_module_has_no_io_or_secondary_domain_imports():
    """A new ORM/domain import would turn the decision layer into an authority."""
    source = (ROOT / "app" / "today_guidance.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert imported <= {
        "__future__",
        "dataclasses",
        "app.services.workout_state.models",
    }


def test_client_and_template_cannot_choose_the_guidance_action():
    """Moving ranking into JavaScript/Jinja would create a second decision path."""
    script = (ROOT / "static" / "today.js").read_text(encoding="utf-8")
    template = (ROOT / "templates" / "today.html").read_text(encoding="utf-8")

    for token in (
        CANDIDATE_RESUME_WORKOUT,
        CANDIDATE_START_WORKOUT,
        CANDIDATE_CREATE_PLAN,
        "data-today-state']",
        'data-today-state"]',
    ):
        assert token not in script
    assert template.count("{% if today.primary %}") == 1
    assert "today.primary_state" not in template
    assert "today.state ==" not in template
    assert "today.state in" not in template


def test_full_decision_matrix_runs_without_a_flask_application_context():
    """The orchestration step itself must add zero reads or request dependencies."""
    decisions = [
        decide_today_guidance(
            read_ok=True, primary_state=state, action=action)
        for state, action in SUPPORTED_STATE_ACTIONS
    ]

    assert len(decisions) == 9
    assert [item.primary_kind for item in decisions].count(
        CANDIDATE_RESUME_WORKOUT) == 1
