"""The one WeeklyCheckIn + body-weight persistence authority (LP16-A).

Extracted from ``app/blueprints/tracking.py`` without changing what the web
routes do. The service takes explicit owner/domain arguments: no request, no
``current_user``, no provider or AI call, no transport parsing.

Transaction ownership is explicit. Only three functions end a transaction:

* :func:`claim_submission` — ends it (rollback) when the key already answers
  (replay or conflict); a FRESH claim leaves the owner lock held for the
  caller's single commit.
* :func:`commit_full_checkin` — the ONE commit of a full check-in (row +
  ``User.weight`` + derived session + whatever the caller staged meanwhile)
  and, for a keyed row, the uniqueness-race arbitration.
* :func:`record_legacy_weight_update` — the legacy ``/update-weight`` write.

:func:`apply_body_weight` and :func:`stage_full_checkin` only stage; a failure
before the commit leaves nothing durable (no half-written row/user/session).

Never written here: ``User.target_weight``, plans, Today, Coach state.
"""
from __future__ import annotations

import json

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import WeeklyCheckIn
from app.services import account_profile
from app.services.calculations import calculate_bmr, calculate_target, calculate_tdee
from app.timeutil import app_today, utc_day_bounds

from . import queries
from .models import (BodyWeightUpdate, CheckInContext, FullCheckIn,
                     SubmissionOutcome, SubmissionResult)


def apply_body_weight(owner, weight, session):
    """Stage ``weight`` on the owner and, when eligible, the derived targets.

    The one shared weight primitive of ``/checkin`` and ``/update-weight``
    (BUG-1). Writes the canonical ``owner.weight``; when the profile is
    complete (height, age, gender, current_activity, goal) recalculates
    BMR/TDEE/target calories into ``session``. An incomplete profile would
    make ``calculate_tdee`` silently assume sedentary, so then only the weight
    moves and the derived targets stay as stored (L2). No commit.
    """
    if session is not None and session.user_id != owner.id:
        raise ValueError("session does not belong to owner")
    owner.weight = weight
    profile_ready = all([
        owner.height, owner.age, owner.gender,
        owner.current_activity, owner.goal,
    ])
    if profile_ready:
        bmr             = calculate_bmr(weight, owner.height, owner.age, owner.gender)
        tdee            = calculate_tdee(bmr, owner.current_activity)
        target_calories = calculate_target(tdee, owner.goal)
        if session:
            session.weight          = weight
            session.bmr             = bmr
            session.tdee            = tdee
            session.target_calories = target_calories
    else:
        if session:
            session.weight = weight
        bmr             = session.bmr if session else None
        tdee            = session.tdee if session else None
        target_calories = session.target_calories if session else None
    return BodyWeightUpdate(bmr, tdee, target_calories, profile_ready)


def claim_submission(owner_id, idempotency_key, request_fingerprint):
    """Lock the owner and resolve ``idempotency_key`` before any other work.

    FRESH: no row holds the key; the owner lock stays held until the caller's
    :func:`commit_full_checkin`. REPLAYED: same fingerprint, the stored
    response is returned. CONFLICT: a different fingerprint. Both of the
    latter end the transaction (releasing the lock) before returning.
    """
    queries.lock_owner(owner_id)
    existing = queries.find_by_idempotency_key(owner_id, idempotency_key)
    if existing is None:
        return SubmissionResult(SubmissionOutcome.FRESH)
    if existing.request_fingerprint != request_fingerprint:
        db.session.rollback()
        return SubmissionResult(SubmissionOutcome.CONFLICT)
    response = json.loads(existing.response_snapshot)
    db.session.rollback()
    return SubmissionResult(SubmissionOutcome.REPLAYED, response)


def reload_locked_owner(owner):
    """Re-read ``owner`` inside a FRESH claim, i.e. under the owner lock.

    ``owner`` was loaded before :func:`claim_submission` took the lock, so a
    concurrent check-in may have committed a different weight since. Staging
    against that stale snapshot is a lost update: when the new weight equals
    the stale value the ORM sees no change and emits no ``UPDATE``, and an
    earlier-serialized check-in's weight stays current (LP16-B PostgreSQL
    race). Provider-free callers reload here; the web ``/checkin`` path does
    not (its SQL is pinned byte-identical to 088d04d; recorded debt).
    """
    db.session.refresh(owner)
    return owner


def load_context(owner_id):
    """Read what a full check-in is persisted against (latest full, session)."""
    previous = queries.latest_full_checkin(owner_id)
    session = account_profile.canonical_session(owner_id)
    return CheckInContext(owner_id=owner_id, previous=previous, session=session)


def stage_full_checkin(owner, checkin, context, *, coach_feedback=None,
                       idempotency_key=None, request_fingerprint=None,
                       checked_in_at=None):
    """Stage one full check-in row plus its body-weight effect. No commit.

    ``coach_feedback`` is stored as given (empty for provider-free callers);
    the service never produces it. ``checked_in_at`` (naive UTC, a server
    clock reading — never client input) lets a caller that must answer with
    the timestamp before the commit, i.e. put it in the replay snapshot, own
    it explicitly; ``None`` keeps the column default (the web path).
    """
    if not isinstance(checkin, FullCheckIn):
        raise TypeError("checkin must be a FullCheckIn")
    if checkin.intensity is None:
        raise ValueError("a full check-in requires intensity")
    if not isinstance(context, CheckInContext) or context.owner_id != owner.id:
        raise ValueError("context does not belong to owner")
    if (idempotency_key is None) != (request_fingerprint is None):
        raise ValueError("idempotency_key and request_fingerprint go together")

    entry = WeeklyCheckIn(
        user_id=owner.id,
        weight=checkin.weight,
        yogunluk=checkin.intensity,
        fatigue=checkin.fatigue,
        progressive_overload=checkin.progressive_overload,
        uyku_kalitesi=checkin.sleep_quality,
        beslenme_uyumu=checkin.nutrition_adherence,
        note=checkin.note,
        coach_feedback=coach_feedback,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
    )
    if checked_in_at is not None:
        if checked_in_at.tzinfo is not None:
            raise ValueError("checked_in_at must be naive UTC")
        entry.created_at = checked_in_at
    db.session.add(entry)
    apply_body_weight(owner, checkin.weight, context.session)
    return entry


def commit_full_checkin(entry, *, response=None):
    """Commit the staged full check-in: the one commit of the operation.

    Unkeyed: plain commit → COMMITTED. Keyed: ``response`` becomes the replay
    snapshot of the same commit; a concurrent winner of the same key
    (``uq_weekly_checkin_user_key``) is arbitrated by fingerprint → REPLAYED
    with the winner's snapshot, or CONFLICT. Any other failure propagates.
    """
    if entry.idempotency_key is None:
        if response is not None:
            raise ValueError("an unkeyed check-in stores no replay response")
        db.session.commit()
        return SubmissionResult(SubmissionOutcome.COMMITTED)
    if response is None:
        raise ValueError("a keyed check-in requires its replay response")

    owner_id = entry.user_id
    idempotency_key = entry.idempotency_key
    request_fingerprint = entry.request_fingerprint
    entry.response_snapshot = json.dumps(response, ensure_ascii=False)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        winner = queries.find_by_idempotency_key(owner_id, idempotency_key)
        if winner is None:
            raise
        if winner.request_fingerprint != request_fingerprint:
            return SubmissionResult(SubmissionOutcome.CONFLICT)
        return SubmissionResult(SubmissionOutcome.REPLAYED,
                                json.loads(winner.response_snapshot))
    return SubmissionResult(SubmissionOutcome.COMMITTED, response)


def record_legacy_weight_update(owner, weight):
    """Legacy web ``/update-weight`` write, preserved exactly (LP16-A).

    Updates ANY row created in the current Istanbul day — including a full
    check-in, whose weight is then overwritten — or inserts a weight-only row;
    takes no owner lock. Both are known defects kept on purpose; new callers
    (the native check-in API) must not use this path.
    """
    session = account_profile.canonical_session(owner.id)
    update = apply_body_weight(owner, weight, session)

    # Istanbul day (CLAUDE.md): a UTC midnight would put an Istanbul
    # 00:00–03:00 entry into the previous day (F1).
    today_start, today_end = utc_day_bounds(app_today())
    today_checkin = queries.same_app_day_checkin(owner.id, today_start, today_end)
    if today_checkin:
        today_checkin.weight = weight
    else:
        db.session.add(WeeklyCheckIn(user_id=owner.id, weight=weight))

    db.session.commit()
    return update
