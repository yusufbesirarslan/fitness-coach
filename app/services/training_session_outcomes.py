"""Explicit outcomes of independent cardio sessions (G4, #530; D1/D2).

A strength day may carry its own cardio session (``kardiyo_seansi``). That
session has its own identity (an opaque reference minted by
``mobile_training.cardio_session_ref``) and its own terminal state, recorded
here and nowhere else:

* ``completed`` or ``skipped`` — one explicit, owner-initiated answer per
  Istanbul day and PLAN (lineage). A replay of the same answer is idempotent;
  a different answer is a conflict, never an overwrite. A new plan starts
  unanswered: an answer is never carried over to, or shown for, another plan.
* no row — the session is still ``incomplete``.

What this deliberately does NOT do:

* It never writes, reads or changes the canonical day completion claim
  (``workout_completion_day``), XP, streaks, quests, Pump Check or the
  completion fence. Rewards and progress metrics keep their existing meaning
  until a contract-level decision changes them (D1).
* It needs no started workout, timer or set-by-set execution (D2): answering
  is a single explicit action on today's session.
* It never fabricates or reassigns a session. Only a session the owner's
  CURRENT plan holds for TODAY can be answered.
"""
from __future__ import annotations

import hmac

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import TrainingSessionOutcome
from app.services import mobile_training
from app.services.today_facts import get_active_plan
from app.timeutil import app_today

KIND_CARDIO = "cardio"
OUTCOMES = (mobile_training.STATE_COMPLETED, mobile_training.STATE_SKIPPED)


class SessionOutcomeRefused(Exception):
    """A typed refusal. ``reason`` is one of: ``not_found`` (malformed
    reference), ``stale`` (not a session of the current plan revision),
    ``not_today`` (a session of another weekday) or ``conflict`` (a different
    outcome is already recorded for today)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def outcomes_for_day(user_id: int, day, plan_lineage: str) -> dict:
    """``{session_kind: outcome}`` answered on ``day`` for THIS plan lineage."""
    rows = db.session.query(
        TrainingSessionOutcome.session_kind, TrainingSessionOutcome.outcome,
    ).filter(
        TrainingSessionOutcome.user_id == user_id,
        TrainingSessionOutcome.date_key == day.isoformat(),
        TrainingSessionOutcome.plan_lineage_id == plan_lineage,
    ).all()
    return {kind: outcome for kind, outcome in rows}


def _existing(user_id, date_key, plan_lineage):
    return TrainingSessionOutcome.query.filter_by(
        user_id=user_id, date_key=date_key, session_kind=KIND_CARDIO,
        plan_lineage_id=plan_lineage).one_or_none()


def record_cardio_outcome(user_id: int, secret, reference, outcome: str):
    """Record today's cardio-session outcome. Returns ``(outcome, replayed)``."""
    if outcome not in OUTCOMES:
        raise ValueError("unknown session outcome")
    if (not isinstance(reference, str)
            or not mobile_training.WORKOUT_REF_RE.fullmatch(reference)):
        raise SessionOutcomeRefused("not_found")
    today = app_today()
    plan = get_active_plan(user_id, lock_for_share=True)
    if plan is None:
        raise SessionOutcomeRefused("stale")
    projected = mobile_training._project_plan(
        plan, user_id, secret, include_sessions=True)
    matched = None
    for day in projected["days"]:
        block = day["cardio_session"]
        if block is not None and hmac.compare_digest(block["workout_ref"], reference):
            matched = day
    if matched is None:
        raise SessionOutcomeRefused("stale")
    if matched["slot"] != today.weekday():
        raise SessionOutcomeRefused("not_today")

    date_key = today.isoformat()
    lineage = projected["plan_lineage"]
    existing = _existing(user_id, date_key, lineage)
    if existing is None:
        db.session.add(TrainingSessionOutcome(
            user_id=user_id, date_key=date_key, session_kind=KIND_CARDIO,
            outcome=outcome, plan_lineage_id=lineage,
            mutation_version=projected["mutation_version"]))
        try:
            db.session.commit()
            return outcome, False
        except IntegrityError:
            # A concurrent answer won the unique claim; re-read it below.
            db.session.rollback()
            existing = _existing(user_id, date_key, lineage)
            if existing is None:
                raise
    recorded = existing.outcome
    db.session.rollback()
    if recorded != outcome:
        raise SessionOutcomeRefused("conflict")
    return outcome, True
