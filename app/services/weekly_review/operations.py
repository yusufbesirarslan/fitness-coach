"""Durable worker claims live on the canonical row and share its erasure."""
from datetime import timedelta
import hashlib
import hmac
import json
import time

from flask import current_app
from rq.timeouts import JobTimeoutException

from app.extensions import db
from app.models import WeeklyCheckIn as CheckIn
from app.services import ai, ai_consent, ai_provider_call, ai_spend_guard, health_consent
from app.services.ai_allowances import execution
from app.services.weekly_checkin import queries
from app.timeutil import UTC, app_now

from . import contract, evidence, provider

CLAIM_WINDOW = timedelta(minutes=5)
BATCH_SIZE = 8
SCAN_BUDGET_SECONDS = 180


def _now():
    return app_now().astimezone(UTC).replace(tzinfo=None)


def _claim():
    """Lease pending work without permitting a provider call yet."""
    row = (db.session.query(CheckIn.user_id, CheckIn.id, CheckIn.review_context)
           .filter(CheckIn.review_status == "pending", queries.FULL_CHECKIN)
           .order_by(CheckIn.review_updated_at, CheckIn.id)
           .with_for_update(skip_locked=True).first())
    if row is None:
        db.session.rollback()
        return None
    owner, check_in_id, context = row
    claimed_at = _now()
    _processing_row(owner, check_in_id, status="pending").update(
        {"review_status": "processing", "review_updated_at": claimed_at},
        synchronize_session=False)
    db.session.commit()
    return owner, check_in_id, context, claimed_at


def _processing_row(owner, check_in_id, *, status="processing"):
    return CheckIn.query.filter_by(user_id=owner, id=check_in_id, review_status=status).filter(
        queries.FULL_CHECKIN)


def _unavailable(failure):
    return {"review_status": "unavailable", "review_context": None,
            "review_updated_at": _now(), "review_failure": failure}


def _recover_stale():
    for status, outcome in (
        ("processing", {"review_status": "pending", "review_updated_at": _now()}),
        ("dispatching", _unavailable("interrupted")),
    ):
        stale_ids = (db.session.query(CheckIn.id).filter(
            CheckIn.review_status == status,
            CheckIn.review_updated_at <= _now() - CLAIM_WINDOW,
        ).order_by(CheckIn.review_updated_at, CheckIn.id).limit(BATCH_SIZE)
            .with_for_update(skip_locked=True).scalar_subquery())
        CheckIn.query.filter(CheckIn.id.in_(stale_ids)).update(
            outcome, synchronize_session=False)
    db.session.commit()


def _begin_dispatch(owner, check_in_id, claimed_at):
    if not provider.enabled() or _now() >= claimed_at + CLAIM_WINDOW:
        return False
    updated = (_processing_row(owner, check_in_id)
               .filter(CheckIn.review_updated_at == claimed_at)
               .update({"review_status": "dispatching"}, synchronize_session=False))
    db.session.commit()
    return updated == 1


def _snapshot(owner, check_in_id):
    """Use one consistent PostgreSQL snapshot even if legacy weight is edited."""
    if db.engine.dialect.name == "postgresql":
        db.session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    try:
        row = queries.full_checkin_by_id(owner, check_in_id)
        return evidence.load(owner, check_in_id, row) if row is not None else None
    finally:
        db.session.rollback()


def _digest(owner, check_in_id, language, snapshot):
    encoded = json.dumps([contract.VERSION, owner, check_in_id, language, snapshot],
                         sort_keys=True, separators=(",", ":"), allow_nan=False)
    key = current_app.secret_key
    if isinstance(key, str):
        key = key.encode()
    return hmac.new(key, b"axisai/weekly-review/evidence/v1\0" + encoded.encode(),
                    hashlib.sha256).hexdigest()


def _evaluate(owner, check_in_id, context, claimed_at, *, scan_deadline=None):
    if not provider.enabled() or _now() >= claimed_at + CLAIM_WINDOW:
        return "interrupted"
    try:
        generation = context["health_generation"]
        if type(generation) is not int:
            return "authorization"
        with health_consent.processing(owner, expected_generation=generation):
            authority, family = provider.authorize(owner, context)
            snapshot = _snapshot(owner, check_in_id)
            if snapshot is None:
                return "evidence_unavailable"
            language = context["language"]
            digest = _digest(owner, check_in_id, language, snapshot)

            if not _begin_dispatch(owner, check_in_id, claimed_at):
                return "interrupted"

            def publish(original_authority, result):
                # Owner lock precedes row update, matching erasure/deletion and D1.
                ai_consent.fence(original_authority, owner, feature=provider.FEATURE)
                if not provider.enabled() or _now() >= claimed_at + CLAIM_WINDOW:
                    raise ai_consent.ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
                execution.canonical_within(owner)
                updated = _processing_row(owner, check_in_id, status="dispatching").update({
                    "review_status": "completed", "review_context": None,
                    "review_updated_at": _now(),
                    "review_result": {"version": contract.VERSION, "value": result},
                    "review_evidence_digest": digest,
                }, synchronize_session=False)
                if updated != 1:
                    raise ai_consent.ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
                db.session.commit()

            def before_attempt():
                # The shared provider door invokes this inside each D1 dispatch
                # fence, including the router's authorized retries or fallback.
                try:
                    active = (_processing_row(owner, check_in_id, status="dispatching")
                              .filter(CheckIn.review_updated_at == claimed_at)
                              .with_entities(CheckIn.id).scalar())
                    if (active is None or not provider.enabled()
                            or _now() >= claimed_at + CLAIM_WINDOW
                            or (scan_deadline is not None and time.monotonic() >= scan_deadline)):
                        raise ai_consent.ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
                finally:
                    db.session.rollback()

            with ai_provider_call.attempt_observer(before_attempt):
                provider.review(owner, check_in_id, language, snapshot, authority, family, publish)
    except JobTimeoutException:
        raise
    except (ai_consent.ConsentDenied, health_consent.HealthConsentDenied):
        return "authorization"
    except ai_spend_guard.AISpendLimitExceeded:
        return "capacity"
    except ai.ModelRefusal:
        return "provider_refused"
    except contract.InvalidReview:
        return "invalid_output"
    except Exception as error:
        current_app.logger.warning("weekly_review event=processing_failed type=%s",
                                   type(error).__name__)
        return "processing_failed"
    return None


def drain():
    """RQ wakeups may repeat or disappear. PostgreSQL is the source of work."""
    if not provider.enabled():
        return
    deadline = time.monotonic() + SCAN_BUDGET_SECONDS
    _recover_stale()
    for _ in range(BATCH_SIZE):
        if time.monotonic() >= deadline:
            return
        claim = _claim()
        if claim is None:
            return
        owner, check_in_id, _, claimed_at = claim
        failure = _evaluate(*claim, scan_deadline=deadline)
        if failure == "interrupted":
            # No dispatch marker was committed by this claimant. Let the lease recover.
            db.session.rollback()
            continue
        if failure is not None:
            db.session.rollback()
            (CheckIn.query.filter_by(user_id=owner, id=check_in_id, review_updated_at=claimed_at)
             .filter(CheckIn.review_status.in_(("processing", "dispatching")), queries.FULL_CHECKIN)
             .update(_unavailable(failure), synchronize_session=False))
            db.session.commit()


def read(owner, check_in_id):
    row = queries.full_checkin_by_id(owner, check_in_id)
    if row is None or row.review_status is None:
        return None
    status = row.review_status
    if status in ("pending", "processing", "dispatching") and not provider.enabled():
        status = "unavailable"
    elif status == "dispatching":
        status = ("unavailable" if row.review_updated_at <= _now() - CLAIM_WINDOW
                  else "processing")
    elif status == "processing" and row.review_updated_at <= _now() - CLAIM_WINDOW:
        status = "pending"
    result = None
    if status == "completed":
        if row.review_result["version"] == contract.VERSION:
            result = row.review_result["value"]
        else:
            status = "unavailable"
    return {"contract_version": contract.VERSION, "check_in_id": check_in_id,
            "status": status, "evaluation": result}
