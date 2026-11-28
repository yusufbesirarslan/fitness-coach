"""Durable worker claims live on the canonical row and share its erasure."""
from contextvars import ContextVar
from datetime import timedelta
import hashlib
import hmac
import json
import time

from flask import current_app
from rq.timeouts import JobTimeoutException

from app.extensions import db
from app.models import User, WeeklyCheckIn as CheckIn
from app.services import ai, ai_consent, ai_spend_guard, health_consent
from app.services.mobile_weekly_checkin.contract import week_start
from app.services.weekly_checkin import queries
from app.timeutil import UTC, app_date_of, app_now, utc_day_bounds

from . import allowance, contract, evidence, provider

CLAIM_WINDOW = timedelta(minutes=5)
BATCH_SIZE = 8
SCAN_BUDGET_SECONDS = 180
_PHYSICAL_BINDING = ContextVar("weekly_review_physical_binding", default=None)


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


def _locked_claim(owner, check_in_id, claimed_at, *, status):
    return (_processing_row(owner, check_in_id, status=status)
            .filter(CheckIn.review_updated_at == claimed_at)
            .populate_existing().with_for_update().one_or_none())


def _terminalize(row, failure):
    admission = allowance.admission_for(row)
    if admission is not None and admission.state == allowance.RESERVED:
        admission.state = allowance.RESTORED
    for key, value in _unavailable(failure).items():
        setattr(row, key, value)


def _settlement_owner(owner):
    # Settlement can release an obligation after revoke. It takes the same
    # owner row before the operation without granting processing authority.
    return (db.session.query(User.id).filter_by(id=owner)
            .with_for_update(skip_locked=True).scalar()) is not None


def _recover_stale():
    candidates = db.session.query(CheckIn.user_id, CheckIn.id, CheckIn.review_status,
                                  CheckIn.review_updated_at).filter(
        CheckIn.review_status.in_(("processing", "dispatching")),
        CheckIn.review_updated_at <= _now() - CLAIM_WINDOW,
    ).order_by(CheckIn.review_updated_at, CheckIn.id).limit(BATCH_SIZE).all()
    db.session.rollback()
    for owner, check_in_id, status, claimed_at in candidates:
        if _settlement_owner(owner):
            row = _locked_claim(owner, check_in_id, claimed_at, status=status)
            if row is not None:
                if status == "dispatching":
                    _terminalize(row, "interrupted")
                else:
                    row.review_status = "pending"
                    row.review_updated_at = _now()
        db.session.commit()


def _fresh_week(saved_at, now):
    if saved_at is None or now < saved_at:
        return None
    week = week_start(app_date_of(saved_at))
    next_monday, _ = utc_day_bounds(week + timedelta(days=7))
    return week if now < min(saved_at + timedelta(hours=24), next_monday) else None


def _begin_dispatch(owner, check_in_id, claimed_at, authority):
    ai_consent.fence(authority, owner, feature=provider.FEATURE)
    row = _locked_claim(owner, check_in_id, claimed_at, status="processing")
    now = _now()
    if row is None or not provider.enabled() or now >= claimed_at + CLAIM_WINDOW:
        db.session.rollback()
        return False
    week = _fresh_week(row.created_at, now)
    if (week is None or row.nutrition_adherence_v2 is None
            or row.idempotency_key is None or row.response_snapshot is None):
        _terminalize(row, "checkin_ineligible")
        db.session.commit()
        return False
    admission, failure = allowance.reserve(owner, week, allowance.window_for(now))
    if admission is None:
        _terminalize(row, failure)
        db.session.commit()
        return False
    row.review_admission_week = week
    row.review_status = "dispatching"
    db.session.commit()
    return True


def _physical_row(owner, *, lock):
    binding = _PHYSICAL_BINDING.get()
    if binding is None or binding[0] != owner or not provider.enabled():
        raise ai_consent.ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
    _, check_in_id, claimed_at, deadline = binding
    if _now() >= claimed_at + CLAIM_WINDOW or (
        deadline is not None and time.monotonic() >= deadline
    ):
        raise ai_consent.ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
    query = (_processing_row(owner, check_in_id, status="dispatching")
             .filter(CheckIn.review_updated_at == claimed_at))
    row = (query.with_for_update() if lock else query).one_or_none()
    admission = allowance.admission_for(row) if row is not None else None
    if admission is None or admission.state != allowance.RESERVED or admission.physical_attempt:
        raise ai_consent.ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
    return admission


def check_physical_binding(owner):
    try:
        _physical_row(owner, lock=False)
    finally:
        db.session.rollback()


def mark_physical_attempt(owner):
    """The shared SDK door calls this while its independent D1 fence is held."""
    try:
        admission = _physical_row(owner, lock=True)
        admission.physical_attempt = True
        db.session.commit()
    except BaseException:
        db.session.rollback()
        raise


def _publish(owner, check_in_id, claimed_at, authority, result, digest):
    ai_consent.fence(authority, owner, feature=provider.FEATURE)
    row = _locked_claim(owner, check_in_id, claimed_at, status="dispatching")
    if row is None or _now() >= claimed_at + CLAIM_WINDOW:
        raise ai_consent.ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
    admission = allowance.admission_for(row)
    if admission is None or admission.state != allowance.RESERVED or not admission.physical_attempt:
        raise ai_consent.ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
    admission.state = allowance.CHARGED
    row.review_status = "completed"
    row.review_context = None
    row.review_updated_at = _now()
    row.review_result = {"version": contract.VERSION, "value": result}
    row.review_evidence_digest = digest
    db.session.commit()


def _finish_failure(owner, check_in_id, claimed_at, failure):
    if not _settlement_owner(owner):
        db.session.rollback()
        return
    row = _locked_claim(owner, check_in_id, claimed_at, status="dispatching")
    if row is None:
        row = _locked_claim(owner, check_in_id, claimed_at, status="processing")
    if row is not None:
        admission = allowance.admission_for(row)
        if (failure != "processing_failed" or admission is None
                or not admission.physical_attempt):
            _terminalize(row, failure)
    db.session.commit()


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
            authority = provider.authorize(owner, context)
            snapshot = _snapshot(owner, check_in_id)
            if snapshot is None:
                return "evidence_unavailable"
            language = context["language"]
            digest = _digest(owner, check_in_id, language, snapshot)
            if not _begin_dispatch(owner, check_in_id, claimed_at, authority):
                return "interrupted"
            token = _PHYSICAL_BINDING.set((owner, check_in_id, claimed_at, scan_deadline))
            try:
                result = provider.review(owner, language, snapshot, authority)
            finally:
                _PHYSICAL_BINDING.reset(token)
            _publish(owner, check_in_id, claimed_at, authority, result, digest)
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
        original = error.__cause__ or error.__context__ or error
        if getattr(original, "status_code", None) in (400, 401, 403, 404, 413, 422, 429):
            return "provider_rejected"
        return "processing_failed"
    return None


def drain():
    """RQ wakeups may repeat or disappear. PostgreSQL is the source of work."""
    _recover_stale()
    if not provider.enabled():
        return
    deadline = time.monotonic() + SCAN_BUDGET_SECONDS
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
            _finish_failure(owner, check_in_id, claimed_at, failure)


def read(owner, check_in_id, version=1):
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
    payload = {"contract_version": version, "check_in_id": check_in_id,
               "status": status, "evaluation": result}
    if version == 2:
        admission = allowance.admission_for(row)
        state = admission.state if admission is not None else "none"
        if state == allowance.RESERVED and row.review_status == "dispatching":
            payload["status"] = "processing"
        window = (allowance.window_for(_now())
                  if provider.enabled() and row.review_failure != "authorization" else None)
        payload["allowance"] = {
            "state": state,
            "remaining": allowance.remaining(owner, window) if window else None,
            "window_end": (window + timedelta(days=allowance.WINDOW_DAYS)).isoformat()
            if window else None,
        }
    return payload
