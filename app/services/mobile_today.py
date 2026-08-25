"""Canonical mobile Today read projection (Sprint 12 PR3).

This module is a **projection, not an authority**. It decides nothing about
fitness state: it orchestrates the same canonical reads the web Training surface
already performs, then serializes the result for the `/api/v1` client.

    canonical persisted state
      -> app/services/workout_state        (schedule / execution / completion)
      -> app/services/today_facts          (the active-plan selector)
      -> THIS projection
      -> JSON

Every decision it could have made is deliberately delegated:

* **which plan is active** - `today_facts.get_active_plan`, the exact selector
  `/training-plan/active` and Today V2 use. Not re-queried here, so mobile can
  never treat a superseded plan as active while the web does not.
* **workout vs rest vs no-plan vs unreadable** - `resolve_workout_state`. There is
  no rest-day inference in this file; "no plan" and "rest day" are different
  canonical `schedule_state` values and are never collapsed.
* **completion** - the same resolver's `completed_today` (today's PumpCheck),
  which is what `GET /workout/status` returns. Never inferred from the calendar,
  from missing data, or from recorded execution evidence.
* **today's date** - `app.timeutil.app_today()` (Istanbul). No client date, client
  timezone, request parameter or naive `date.today()` reaches this module; there
  is no second timezone conversion here.
* **today's plan content** - `workout_state.serialization.serialize_today_plan`,
  the bounded projection `/training/bootstrap` already publishes. This module only
  narrows it to a summary.

Read-only: no writes, no flush, no commit, no migration, no AI/provider call and
no HTTP call. `GET /api/v1/today` must cost exactly zero provider calls.

Failure semantics are strict, because a home screen that lies is worse than one
that is briefly unavailable. The composition runs with `strict_reads=True` inside
one coherent snapshot, so an infrastructure fault raises `TodayUnavailable` and
the route answers a typed 5xx. A read failure is NEVER downgraded into a
product-empty Today ("no plan") or a rest day.
"""
from __future__ import annotations

import json
from datetime import timezone

from flask import current_app

from app.services.today_facts import get_active_plan
from app.services.workout_state import resolve_workout_state
from app.services.workout_state.serialization import (
    PlanSerializationError, serialize_today_plan)
from app.services.workout_state.snapshot import coherent_read_snapshot
from app.timeutil import UTC, app_now, app_today

# The flag that selects the workout-state contract version. Read here for the
# same reason `/training/bootstrap` reads it: the value must be captured ONCE and
# passed in, so every read in one response describes one contract.
_SESSIONS_FLAG = "FITX_WORKOUT_SESSIONS_ENABLED"


class TodayUnavailable(RuntimeError):
    """A canonical Today read failed.

    Deliberately distinct from every product state: the route turns this into a
    retryable 5xx, never into an empty or resting Today.
    """


def build_today(user_id: int) -> dict:
    """Project the authenticated user's canonical Today state.

    `user_id` is the authenticated principal, supplied by the route from
    `g.mobile_user.id`. This function accepts no date, no timezone and no owner
    override - there is no parameter through which one user could read another's
    Today, and no parameter through which a client clock could move the day.

    Raises `TodayUnavailable` if a canonical authority cannot be read.
    """
    try:
        with coherent_read_snapshot():
            day = app_today()
            plan = get_active_plan(user_id)
            plan_data = _plan_content(plan)
            snapshot = resolve_workout_state(
                user_id,
                today=day,
                plan=plan,
                sessions_enabled=_sessions_enabled(),
                strict_reads=True,
            )
            return _project(day, plan, plan_data, snapshot)
    except Exception as error:  # noqa: BLE001 - every fault fails closed
        raise TodayUnavailable("canonical Today read failed") from error


def _plan_content(plan):
    """The plan row's parsed `plan_data`, or `None` when it cannot be read.

    An unparseable plan is a *domain* condition, not an infrastructure fault: the
    canonical resolver already classifies it as `schedule_unavailable` /
    `needs_attention`, and that classification must survive to the client. Raising
    here would replace an honest "your plan needs attention" with a 5xx.

    The caught set is exactly the one `workout_state.queries._load_schedule`
    catches, so the summary and the canonical schedule state can never disagree
    about whether the plan was readable.
    """
    if plan is None:
        return None
    try:
        return json.loads(plan.plan_data)
    except (ValueError, TypeError):
        return None


def _sessions_enabled() -> bool:
    try:
        return bool(current_app.config.get(_SESSIONS_FLAG, False))
    except Exception:  # noqa: BLE001 - no app context / missing key => OFF
        return False


def _project(day, plan, plan_data, snapshot) -> dict:
    """Serialize already-resolved canonical facts. Pure: no reads, no decisions."""
    state = snapshot.to_dict()
    return {
        "today": {
            # The server's canonical Istanbul day - the same day the snapshot was
            # resolved for, so the two can never describe different days.
            "date": day.isoformat(),
            "server_time": _utc_now_iso(),
            # The dominant canonical state. Re-exported verbatim from the
            # workout-state contract rather than remapped, so no fourth Today
            # vocabulary is introduced (docs/WORKOUT_STATE.md owns the enum).
            "status": snapshot.primary_state,
            "action": snapshot.action,
            "workout": {
                "schedule_state": snapshot.schedule_state,
                "is_rest_day": snapshot.is_rest_day,
                "completed": snapshot.completed_today,
                "summary": _summary(plan_data, day),
                # Present only once the persisted session lifecycle is the active
                # contract; `None` otherwise. Never an invented identity.
                "session": state.get("session"),
            },
            "plan": _plan(plan),
            "daily_context": _daily_context(plan, day),
            # The canonical workout-state contract exactly as `GET /workout/status`
            # publishes it. Carried verbatim so a mobile client and the web client
            # read one contract; `state.contract_version` announces its shape.
            "state": state,
        }
    }


def _summary(plan_data, day):
    """A bounded summary of today's canonical plan day, or `None`.

    Narrowed from `serialize_today_plan` - the same bounded projection
    `/training/bootstrap` publishes - so the exercise payload itself is not
    duplicated onto a second surface. `None` means the canonical projection has no
    publishable day for today (no plan, an unreadable schedule, or content outside
    the public bounds). It never means "rest day": that is `schedule_state`'s job.

    Deliberately carries no weekday name and no `tip` token. Both are localized
    web copy, and both would hand a client a second way to answer "is today a rest
    day?" - one that is not the canonical authority and would drift from it. The
    canonical answers are `date` and `schedule_state`. `focus` is plan *content*
    (free text an author wrote, like an exercise name), not an enum: display it,
    never branch on it.
    """
    if plan_data is None:
        return None
    try:
        today_plan = serialize_today_plan(plan_data, day)
    except PlanSerializationError:
        return None
    if today_plan is None:
        return None
    return {
        "focus": today_plan["odak"],
        "duration_min": today_plan["sure_dk"],
        "estimated_calories": today_plan["tahmini_kalori"],
        "exercise_count": len(today_plan["egzersizler"]),
    }


def _plan(plan):
    """Plan availability. `exists` is the canonical active-plan selector's answer."""
    if plan is None:
        return {"exists": False, "created_at": None}
    return {"exists": True, "created_at": _iso(plan.created_at)}


def _daily_context(plan, day):
    """The approved daily workout-context identity.

    `(plan_lineage, mutation_version, canonical_local_date)` - the tuple PR4 uses
    to decide whether its cached Today is still current. These are server-owned
    tokens read straight off the canonical plan row; nothing is hashed, minted or
    defaulted. With no plan, lineage and version are `None` - a fabricated value
    here would make a client believe in a plan the server does not have.
    """
    return {
        "plan_lineage": plan.lineage_id if plan is not None else None,
        "mutation_version": plan.mutation_version if plan is not None else None,
        "canonical_local_date": day.isoformat(),
    }


def _utc_now_iso() -> str:
    """The server's canonical instant, in UTC.

    Derived from `app_now()` - the one clock authority - rather than a second
    `datetime.now()`, so this timestamp and `date` can never come from different
    clocks, and a frozen test clock freezes both.
    """
    return app_now().astimezone(UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z")


def _iso(value):
    """Naive-UTC persisted timestamp -> the `...Z` form the mobile API uses."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
