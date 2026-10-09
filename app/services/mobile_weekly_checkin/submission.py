"""Provider-free native check-in write over the LP16-A primitives (LP16-B).

Composition only — the canonical ``weekly_checkin`` service owns the lock,
the row, ``User.weight``, the derived session and the single commit:

1. ``claim_submission`` — owner ``FOR UPDATE`` + key lookup. REPLAYED returns
   the stored native body (no second row, no second weight/session write);
   CONFLICT → ``IdempotencyConflict``.
2. ``reload_locked_owner`` — the owner as committed, re-read under the lock
   (staging against the pre-lock snapshot loses a concurrent update), then
   ``load_context`` — the canonical session the weight recalculates.
3. the server clock is read **after** the owner lock, so per owner the commit
   order and the ``checked_in_at`` order agree: the latest check-in by
   ``created_at, id`` is always the one whose weight ``User.weight`` holds.
4. ``stage_full_checkin`` — row + body-weight effect, ``coach_feedback`` and
   ``note`` empty (no AI, no note in the launch contract).
5. ``commit_full_checkin`` — the ONE commit, the replay snapshot included;
   a uniqueness race loser is arbitrated to REPLAYED/CONFLICT there.

Never written: ``target_weight``, TrainingPlan, NutritionPlan, Coach, Today.
"""
from __future__ import annotations

from app.services import weekly_checkin
from app.timeutil import UTC, app_now

from .contract import (OVERLOAD_WIRE_TO_STORED, IdempotencyConflict,
                       checkin_payload, fingerprint)

_Outcome = weekly_checkin.SubmissionOutcome


def server_checked_in_at():
    """The server-owned check-in instant as naive UTC (the column convention).

    ``app_now`` so a test's ``audit_clock`` pins it like every other
    application "now"; never a client value.
    """
    return app_now().astimezone(UTC).replace(tzinfo=None)


def _full_checkin(command):
    return weekly_checkin.FullCheckIn(
        weight=command.weight_kg,
        intensity=command.training_intensity,
        fatigue=command.fatigue,
        progressive_overload=OVERLOAD_WIRE_TO_STORED[command.progressive_overload],
        sleep_quality=command.sleep_quality,
        nutrition_adherence=command.nutrition_adherence,
        note=None,
    )


def submit(owner, idempotency_key, command):
    """Write (or replay) one native check-in. Returns ``(body, created)``.

    ``created`` is true only for the call whose commit made the row durable;
    every replay — sequential or a race loser — answers the winner's stored
    body with ``created`` false. Raises ``IdempotencyConflict``; any other
    failure propagates with nothing durable (the caller rolls back).
    """
    owner_id = owner.id
    digest = fingerprint(command)
    claim = weekly_checkin.claim_submission(owner_id, idempotency_key, digest)
    if claim.outcome is _Outcome.CONFLICT:
        raise IdempotencyConflict()
    if claim.outcome is _Outcome.REPLAYED:
        return claim.response, False

    weekly_checkin.reload_locked_owner(owner)
    context = weekly_checkin.load_context(owner_id)
    checked_in_at = server_checked_in_at()
    entry = weekly_checkin.stage_full_checkin(
        owner, _full_checkin(command), context,
        coach_feedback=None,
        idempotency_key=idempotency_key,
        request_fingerprint=digest,
        checked_in_at=checked_in_at,
    )
    body = checkin_payload(checked_in_at, command)
    result = weekly_checkin.commit_full_checkin(entry, response=body)
    if result.outcome is _Outcome.CONFLICT:
        raise IdempotencyConflict()
    if result.outcome is _Outcome.REPLAYED:
        return result.response, False
    return body, True
