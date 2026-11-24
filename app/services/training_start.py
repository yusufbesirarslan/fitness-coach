"""PD2 onboarding training start: the owner's start state, and Skip.

The training state of an owner is decided by exactly two canonical facts:

* an active ``TrainingPlan`` (``today_facts.get_active_plan``) -- always wins;
* otherwise an explicit ``training_start_decision`` row (Skip), or nothing.

Nothing here writes a plan, reads a pending draft, calls a provider or
computes calories. The daily calorie target is the canonical Nutrition read
(``nutrition_day_view.daily_calorie_target_payload``); PD1 owns its value.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import TrainingStartDecision
from app.services.account_profile import onboarding_state
from app.services.nutrition_day_view import daily_calorie_target_payload
from app.services.plan_owner_lock import lock_plan_owner
from app.services.today_facts import get_active_plan

CONTRACT_VERSION = 1
DECISION_SKIP = "skip"
DECISIONS = (DECISION_SKIP,)


class TrainingStartError(RuntimeError):
    public_code = "TRAINING_START_FAILED"
    http_status = 500
    retryable = False


class TrainingStartInvalid(TrainingStartError):
    public_code = "TRAINING_START_INVALID_REQUEST"
    http_status = 422


class TrainingStartPrerequisiteMissing(TrainingStartError):
    public_code = "TRAINING_PLAN_PREREQUISITE_MISSING"
    http_status = 422


class TrainingStartPlanExists(TrainingStartError):
    """Skip is a choice for an owner WITHOUT a plan; an active plan is kept."""

    public_code = "TRAINING_START_PLAN_EXISTS"
    http_status = 409


class TrainingStartUnavailable(TrainingStartError):
    public_code = "TRAINING_START_UNAVAILABLE"
    http_status = 503
    retryable = True


def parse_decision(body) -> str:
    if (not isinstance(body, dict) or set(body) != {"decision"}
            or body["decision"] not in DECISIONS):
        raise TrainingStartInvalid()
    return body["decision"]


def read_start(user_id: int) -> dict:
    """The owner's start state; never raises for the calorie section."""
    try:
        has_plan = get_active_plan(user_id) is not None
        row = (db.session.query(TrainingStartDecision.decision)
               .filter_by(user_id=user_id).one_or_none())
        db.session.rollback()
    except Exception as error:
        db.session.rollback()
        raise TrainingStartUnavailable() from error
    return {
        "contract_version": CONTRACT_VERSION,
        "has_plan": has_plan,
        # An active plan is the training state: a stale Skip is never reported
        # over it, so a client cannot hide a plan behind "skipped".
        "decision": None if has_plan or row is None else row[0],
        "daily_calorie_target": daily_calorie_target_payload(user_id),
    }


def record_skip(user) -> dict:
    """Record Skip for an owner without a plan; idempotent; never a plan write."""
    if not onboarding_state(user).complete:
        raise TrainingStartPrerequisiteMissing()
    user_id = user.id
    try:
        # Same owner serialization every plan writer takes, so Skip and a
        # concurrent first-plan Accept are ordered: a plan committed first
        # refuses Skip; a Skip committed first is simply outranked later.
        lock_plan_owner(user_id)
        if get_active_plan(user_id) is not None:
            raise TrainingStartPlanExists()
        row = (TrainingStartDecision.query.filter_by(user_id=user_id)
               .populate_existing().with_for_update().one_or_none())
        now = datetime.utcnow()
        if row is None:
            db.session.add(TrainingStartDecision(
                user_id=user_id, decision=DECISION_SKIP, created_at=now, updated_at=now))
        elif row.decision != DECISION_SKIP:
            row.decision = DECISION_SKIP
            row.updated_at = now
        db.session.commit()
    except TrainingStartPlanExists:
        db.session.rollback()
        raise
    except IntegrityError:
        # A concurrent identical Skip won the unique row: same outcome.
        db.session.rollback()
    except Exception as error:
        db.session.rollback()
        raise TrainingStartUnavailable() from error
    return read_start(user_id)


METHOD_AI = "ai"
METHOD_GENERAL = "general"


def _iso_z(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat() + "Z"


def preview_payload(user_id: int, *, method: str, document_json: str,
                    candidate_fingerprint: str, expires_at=None,
                    include_sessions: bool = False) -> dict:
    """The one preview wire shape for every method (AI and General/Manual).

    ``plan`` is projected from the exact candidate text Accept would write
    (``mobile_training.project_preview_document``), never from a summary.
    """
    from app.services import mobile_training

    if method not in (METHOD_AI, METHOD_GENERAL):
        raise ValueError("unknown preview method")
    return {
        "contract_version": CONTRACT_VERSION,
        "preview": {
            "method": method,
            "candidate_fingerprint": candidate_fingerprint,
            "expires_at": None if expires_at is None else _iso_z(expires_at),
            "plan": mobile_training.project_preview_document(
                document_json, user_id, include_sessions=include_sessions),
        },
        "daily_calorie_target": daily_calorie_target_payload(user_id),
    }


def parse_if_match(raw) -> str | None:
    """``If-Match: "<64 lowercase hex>"`` (quotes optional) or ``None``."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        return ""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        return ""
    return value
