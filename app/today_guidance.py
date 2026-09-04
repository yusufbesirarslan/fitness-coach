"""Pure, deterministic Today guidance decisions.

This module ranks semantic action candidates. It performs no reads and owns no
fitness facts, copy, routes, or rendering.
"""
from __future__ import annotations

from dataclasses import dataclass

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
)

CANDIDATE_RESUME_WORKOUT = "resume_workout"
CANDIDATE_START_WORKOUT = "start_workout"
CANDIDATE_CREATE_PLAN = "create_plan"

PRIORITY_RESUME_WORKOUT = 10
PRIORITY_START_WORKOUT = 20
PRIORITY_CREATE_PLAN = 30

# This is not a product state. It means a trustworthy decision was unavailable.
STATE_ERROR = "error"

SUPPORTED_STATE_ACTIONS = (
    (PRIMARY_IN_PROGRESS, ACTION_RESUME),
    (PRIMARY_SCHEDULED_NOT_STARTED, ACTION_START),
    (PRIMARY_NO_PLAN, ACTION_NONE),
    (PRIMARY_REST_DAY, ACTION_NONE),
    (PRIMARY_EXECUTION_RECORDED, ACTION_NONE),
    (PRIMARY_UNSCHEDULED_EXECUTION, ACTION_NONE),
    (PRIMARY_COMPLETED, ACTION_NONE),
    (PRIMARY_UNSCHEDULED_COMPLETED, ACTION_NONE),
    (PRIMARY_NEEDS_ATTENTION, ACTION_BLOCKED),
)

_EXPECTED_ACTION_BY_STATE = dict(SUPPORTED_STATE_ACTIONS)


@dataclass(frozen=True)
class Candidate:
    """One eligible semantic action and its explicit precedence."""

    kind: str
    priority: int
    reason: str


@dataclass(frozen=True)
class TodayDecision:
    """One validated semantic decision for the server-rendered Today view."""

    state: str
    primary_kind: str | None
    emphasis: str
    decision_reason: str


def rank_candidates(candidates: tuple[Candidate, ...]) -> Candidate | None:
    """Return the highest-priority eligible candidate, or no primary action."""

    return min(candidates, key=lambda item: item.priority, default=None)


def _eligible_candidates(primary_state: str, action: str) -> tuple[Candidate, ...]:
    """Build candidates only from compatible canonical workout dimensions."""

    if primary_state == PRIMARY_IN_PROGRESS and action == ACTION_RESUME:
        return (Candidate(
            CANDIDATE_RESUME_WORKOUT,
            PRIORITY_RESUME_WORKOUT,
            "canonical_resume",
        ),)
    if (primary_state == PRIMARY_SCHEDULED_NOT_STARTED
            and action == ACTION_START):
        return (Candidate(
            CANDIDATE_START_WORKOUT,
            PRIORITY_START_WORKOUT,
            "canonical_start",
        ),)
    if primary_state == PRIMARY_NO_PLAN and action == ACTION_NONE:
        return (Candidate(
            CANDIDATE_CREATE_PLAN,
            PRIORITY_CREATE_PLAN,
            "canonical_no_plan",
        ),)
    return ()


def _error(reason: str) -> TodayDecision:
    return TodayDecision(
        state=STATE_ERROR,
        primary_kind=None,
        emphasis=STATE_ERROR,
        decision_reason=reason,
    )


def decide_today_guidance(
    *, read_ok: bool, primary_state: str, action: str
) -> TodayDecision:
    """Validate canonical facts, rank eligible actions, and fail closed.

    The function accepts no raw plan, session, Nutrition, check-in, insight, or
    clock input. Those domains cannot silently become decision authorities.
    """

    if not read_ok:
        return _error("workout_read_unavailable")

    expected_action = _EXPECTED_ACTION_BY_STATE.get(primary_state)
    if expected_action is None:
        return _error("unsupported_primary_state")
    if action != expected_action:
        return _error("incompatible_state_action")

    winner = rank_candidates(_eligible_candidates(primary_state, action))
    return TodayDecision(
        state=primary_state,
        primary_kind=winner.kind if winner else None,
        emphasis=primary_state,
        decision_reason=winner.reason if winner else "no_primary_action",
    )
