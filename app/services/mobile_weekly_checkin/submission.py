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
   LP16-R4 (D3): when the client named the week it froze the intent in, a
   FRESH write whose own ``checked_in_at`` falls in another Istanbul week is
   refused (``WeekChanged``) before anything is staged. The check reads the
   very instant that dates the row, so there is no window between "checked"
   and "written": an old unresolved intent can never become a new week's
   check-in. REPLAYED returns before this point — a committed intent keeps
   its original week whenever it is replayed.
4. ``stage_full_checkin`` — row + body-weight effect, ``coach_feedback`` and
   ``note`` empty (no AI, no note in the launch contract).
5. ``commit_full_checkin`` — the ONE commit, the replay snapshot included;
   a uniqueness race loser is arbitrated to REPLAYED/CONFLICT there.

Never written: ``target_weight``, TrainingPlan, NutritionPlan, Coach, Today.
"""
from __future__ import annotations

from app.services import weekly_checkin
from app.services.nutrition_native.plan import ABSENT, ACTIVE, saved_plan_state
from app.timeutil import UTC, app_date_of, app_now

from app.services.weekly_checkin.models import NOT_APPLICABLE

from .contract import (OVERLOAD_WIRE_TO_STORED,
                       TERNARY_CONTRACT_VERSION, IdempotencyConflict,
                       WeekChanged, checkin_payload, fingerprint, week_start)

_Outcome = weekly_checkin.SubmissionOutcome


class NutritionApplicabilityChanged(Exception):
    """The frozen answer does not match the current canonical plan state."""


class NutritionPlanUnavailable(Exception):
    """The saved plan could not establish applicability."""


def server_checked_in_at():
    """The server-owned check-in instant as naive UTC (the column convention).

    ``app_now`` so a test's ``audit_clock`` pins it like every other
    application "now"; never a client value.
    """
    return app_now().astimezone(UTC).replace(tzinfo=None)


def _full_checkin(command):
    ternary = command.contract_version == TERNARY_CONTRACT_VERSION
    return weekly_checkin.FullCheckIn(
        weight=command.weight_kg,
        intensity=command.training_intensity,
        fatigue=command.fatigue,
        progressive_overload=OVERLOAD_WIRE_TO_STORED[command.progressive_overload],
        sleep_quality=command.sleep_quality,
        nutrition_adherence=None if ternary else command.nutrition_adherence,
        nutrition_adherence_v2=command.nutrition_adherence if ternary else None,
        note=None,
    )


def submit(owner, idempotency_key, command, expected_week_start=None, *, review_intent=None):
    """Write (or replay) one native check-in. Returns ``(body, created)``.

    ``created`` is true only for the call whose commit made the row durable;
    every replay — sequential or a race loser — answers the winner's stored
    body with ``created`` false. Raises ``IdempotencyConflict``, or
    ``WeekChanged`` when ``expected_week_start`` (a Monday) is not the week
    of this FRESH write's server instant; any other failure propagates with
    nothing durable (the caller rolls back).
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
    if expected_week_start is not None and \
            week_start(app_date_of(checked_in_at)) != expected_week_start:
        raise WeekChanged()
    ternary = command.contract_version == TERNARY_CONTRACT_VERSION
    if ternary:
        state = saved_plan_state(owner_id)
        if state not in (ACTIVE, ABSENT):
            raise NutritionPlanUnavailable()
        answered = command.nutrition_adherence != NOT_APPLICABLE
        if answered != (state == ACTIVE):
            raise NutritionApplicabilityChanged()
    entry = weekly_checkin.stage_full_checkin(
        owner, _full_checkin(command), context,
        coach_feedback=None,
        idempotency_key=idempotency_key,
        request_fingerprint=digest,
        checked_in_at=checked_in_at,
    )
    if ternary and review_intent is not None:
        # The intent becomes visible atomically with the row and replay body.
        # The worker is the first consumer and reads in a separate transaction.
        entry.review_updated_at = checked_in_at
        if review_intent["authority"] is None or type(review_intent["health_generation"]) is not int:
            entry.review_status = "unavailable"
            entry.review_failure = "authorization"
        else:
            entry.review_status = "pending"
            language = "tr" if owner.language == "tr" else "en"
            entry.review_context = dict(review_intent, language=language)

    def versioned_response(checkin_id):
        return checkin_payload(checked_in_at, command, checkin_id=checkin_id)

    response = (versioned_response if ternary
                else checkin_payload(checked_in_at, command))
    result = weekly_checkin.commit_full_checkin(entry, response=response)
    if result.outcome is _Outcome.CONFLICT:
        raise IdempotencyConflict()
    if result.outcome is _Outcome.REPLAYED:
        return result.response, False
    # Product analytics v2: a NEW committed check-in only (no answers).
    from app.services.product_analytics.emit import emit_authoritative
    emit_authoritative(owner_id, "axis_v2_weekly_checkin_submitted",
                       f"weekly_checkin:{owner_id}:{idempotency_key}")
    return result.response, True
