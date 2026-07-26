"""The one canonical confirmed-workout-completion mutation (Sprint 7 PR2).

Every production path that means "the user completed today's workout" — the
``POST /workout/complete`` route and the AI-coach gym-photo tool — funnels
through :func:`complete_workout`. This module owns the single atomic transaction
and the deterministic replay/conflict semantics; entry paths keep only transport,
validation, provider I/O and response shaping.

Guarantees:

* **Atomic** — PumpCheck, the completion marker, quest progress, XP and the
  activity row commit together or not at all. A failure in any *required* write
  rolls the whole thing back (no half-completed day).
* **Idempotent / replay-safe** — a read-only preflight short-circuits obvious
  replays; the ``uq_pump_check_day`` unique constraint is the durable,
  concurrency-safe claim. The race-loser and the sequential replay both return
  ``ALREADY_COMPLETED`` (a normal outcome, never an HTTP 500).
* **No network in the transaction** — remote work happens in the entry path
  before the command is built.

Side-effect classification (see docs/WORKOUT_STATE.md):
  - required & atomic: PumpCheck, marker WorkoutLog, quest, XP, activity, friend
    share-messages;
  - best-effort in-tx (savepoint-isolated, swallowed on failure): challenge
    progress + challenge-completion badge/notification/feed via ``record_event``;
  - response enrichment (not a DB mutation): presigned image URL, Redis
    leaderboard after_commit — owned by the entry path / existing helpers.
"""
import json
from datetime import datetime

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    Message,
    WORKOUT_COMPLETION_MARKER,
    WORKOUT_SESSION_ABANDONED,
    PumpCheck,
    WorkoutLog,
)
from app.observability import current_request_id
from app.services.gamification import (
    _claim_quest,
    award_xp,
    get_level,
    level_title,
    log_activity,
)

from .models import (
    CompleteWorkoutCommand,
    CompletionOutcome,
    CompletionResult,
    SessionCompletionConflict,
)
from .queries import (
    already_completed_today,
    is_pump_check_day_violation,
    lock_session_for_completion,
    mark_session_completed,
)

WORKOUT_LOGGED_QUEST = "workout_logged"


def complete_workout(command: CompleteWorkoutCommand) -> CompletionResult:
    """Perform the one canonical confirmed completion for ``command``.

    Returns a :class:`CompletionResult`; raises only on a genuine, unexpected
    persistence failure (rolled back) or a :class:`SessionCompletionConflict`
    (a session-scoped completion of an already-abandoned session, rolled back
    with no artifacts) — never for the expected duplicate/replay case. Callers
    map the result to their own response and translate a raised exception through
    the app's internal-error contract.

    Session linkage (Sprint 7 PR3, ``command.session_id``): the linked session is
    locked FIRST (fixed lock order), then — atomically in the same transaction as
    the PumpCheck/marker/XP — terminalized ACTIVE→COMPLETED. Every path that ends
    with the day completed also terminalizes the owned matching ACTIVE session
    (fresh create, preflight replay, and the ``uq_pump_check_day`` race-loser),
    so a matching session is never left permanently ACTIVE and COMPLETED is never
    written without PumpCheck authority. ``session_id is None`` is the unchanged
    legacy path.
    """
    now = datetime.utcnow()

    # FIXED lock order: lock the session row BEFORE any completion artifact, on
    # every path, so create and reconciliation can never deadlock. An explicitly
    # ABANDONED session cannot be completed — fail closed with no artifacts.
    session = None
    if command.session_id is not None:
        session = lock_session_for_completion(command.user_id, command.session_id)
        if session is not None and session.status == WORKOUT_SESSION_ABANDONED:
            db.session.rollback()
            _log(command, "session_abandoned_conflict")
            raise SessionCompletionConflict(
                "cannot complete an abandoned workout session"
            )

    # Preflight (optimization only): skip the whole mutation for an obvious
    # replay. The DB unique constraint below is what actually makes it safe.
    if already_completed_today(command.user_id, command.today):
        # Reconciliation: the day is already completed, but a matching ACTIVE
        # session must still be terminalized (never left ACTIVE) — with NO
        # duplicate PumpCheck/marker/XP/quest/activity. The session is already
        # locked in this transaction; terminalize + commit.
        if mark_session_completed(session, now):
            db.session.commit()
            _log(command, "already_completed_preflight_session_reconciled")
        else:
            # No session terminalization needed. Release the FOR UPDATE lock if we
            # took one (session already terminal); the pure legacy path (no
            # session) is left byte-identical — no write, no rollback.
            if session is not None:
                db.session.rollback()
            _log(command, "already_completed_preflight")
        return CompletionResult(outcome=CompletionOutcome.ALREADY_COMPLETED)

    pump_check = PumpCheck(
        user_id=command.user_id,
        image_key=command.image_key,
        location_type=command.location_type,
        description=command.description,
        workout_score=command.workout_score,
        valid=command.valid,
        fallback=command.fallback,
        date_key=command.today.isoformat(),
        visibility=command.visibility,
        shared_friend_ids=list(command.shared_friend_ids),
    )
    try:
        db.session.add(pump_check)
        # Flush now so a concurrent uq_pump_check_day conflict surfaces here (and
        # is handled identically to a commit-time conflict), and so pump_check.id
        # / created_at are available for the friend-message payload.
        db.session.flush()

        # Best-effort challenge funnel — savepoint-isolated + self-swallowing, so
        # a challenge failure can never block the completion (existing contract).
        _record_pump_check_created(command.user_id)
        _fan_out_friend_messages(command, pump_check)

        # Completion marker: paired with the PumpCheck in the same transaction so
        # a marker without a PumpCheck can never be produced by this writer.
        db.session.add(
            WorkoutLog(
                user_id=command.user_id,
                exercise_name=WORKOUT_COMPLETION_MARKER,
                sets=1,
                reps=1,
                weight_kg=0,
                volume=0,
            )
        )

        # Quest + XP + activity — all commit-free helpers (verified); this service
        # owns the single commit so every side effect is atomic with completion.
        quest_result = _claim_quest(command.user_id, WORKOUT_LOGGED_QUEST)
        total_grant = command.base_xp + command.photo_bonus
        new_total = award_xp(command.user_id, total_grant)
        log_activity(command.user_id, "workout_completed", command.activity_text)

        # Terminalize the linked session ACTIVE→COMPLETED in the SAME transaction,
        # before the single commit — one atomic unit with all completion artifacts.
        mark_session_completed(session, now)

        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        # Only a verified daily-completion unique violation is a replay; any other
        # integrity failure is a real error and must not masquerade as "done".
        if is_pump_check_day_violation(exc):
            # Race-loser reconciliation: the winner wrote the day's PumpCheck, so
            # the rollback also undid our session terminalization. Re-lock and
            # terminalize the owned matching ACTIVE session in a fresh transaction
            # (no duplicate artifacts) — it must never be left permanently ACTIVE.
            _reconcile_session_after_race(command, now)
            _log(command, "already_completed_race")
            return CompletionResult(outcome=CompletionOutcome.ALREADY_COMPLETED)
        _log(command, "integrity_error")
        raise
    except Exception:
        db.session.rollback()
        _log(command, "rollback")
        raise

    level = get_level(new_total)
    _log(command, "created")
    return CompletionResult(
        outcome=CompletionOutcome.CREATED,
        pump_check_id=pump_check.id,
        new_total=new_total,
        level=level,
        title=level_title(level),
        quest_result=quest_result,
        xp_awarded=total_grant + (quest_result["xp"] if quest_result else 0),
    )


def _reconcile_session_after_race(command: CompleteWorkoutCommand, now: datetime) -> None:
    """After a ``uq_pump_check_day`` race loss (transaction rolled back), the day
    is completed by the winner but our linked session is still ACTIVE. Re-lock it
    (fixed lock order preserved: session row first) and terminalize ACTIVE→
    COMPLETED, committing a fresh, artifact-free transaction. A session that a
    concurrent request already made terminal (abandoned/completed) is left as-is —
    ``mark_session_completed`` is conditional on ACTIVE, so no state is clobbered
    and no duplicate work is done."""
    if command.session_id is None:
        return
    session = lock_session_for_completion(command.user_id, command.session_id)
    if mark_session_completed(session, now):
        db.session.commit()
    elif session is not None:
        db.session.rollback()


def _record_pump_check_created(user_id: int) -> None:
    """Feed the ``pump_check_created`` challenge metric (best-effort, in-tx).

    ``record_event`` is savepoint-isolated and swallows its own failures, so this
    never breaks the completion. Imported lazily to avoid an import cycle.
    """
    from app.services.challenges import record_event

    record_event(user_id, "pump_check_created")


def _fan_out_friend_messages(command: CompleteWorkoutCommand, pump_check: PumpCheck) -> None:
    """Share the Pump Check with explicitly selected friends (``visibility=friends``).

    Part of the atomic completion unit — identical to the pre-PR2 route behavior.
    """
    if command.visibility != "friends" or not command.shared_friend_ids:
        return
    payload = json.dumps(
        {
            "pump_check_id": pump_check.id,
            "image_key": command.image_key,
            "environment": command.location_type,
            "description": command.description,
            "created_at": pump_check.created_at.isoformat() if pump_check.created_at else None,
        },
        ensure_ascii=False,
    )
    for friend_id in command.shared_friend_ids:
        db.session.add(
            Message(
                sender_id=command.user_id,
                receiver_id=friend_id,
                body=payload,
                message_type="pump_check",
            )
        )


def _log(command: CompleteWorkoutCommand, outcome: str) -> None:
    """Safe, bounded operational log — no PII, no workout content. Best-effort."""
    try:
        current_app.logger.info(
            "[WORKOUT_COMPLETION] rid=%s op=complete_workout entry=%s user_id=%s outcome=%s",
            current_request_id(),
            command.entry_path,
            command.user_id,
            outcome,
        )
    except Exception:  # noqa: BLE001 — logging must never break the mutation
        pass
