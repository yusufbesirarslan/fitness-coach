"""Default-OFF logical execution binding over the canonical allowance ledger.

This module owns no balances. The reservation is the generic durable dispatch
marker; native Training retains its own operation/binding authority. Logical
callers reserve before work, signal domain success explicitly, and settle after
local execution terminates. The provider door independently requires a matching
binding and records every physical attempt after D1 and before SDK entry.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
import hashlib
import hmac
import json
import threading

from flask import current_app, g, has_app_context, has_request_context, request
import sqlalchemy as sa

from app.extensions import db
from app.services import ai_consent, ai_spend_guard
from . import service as ledger
from .errors import AllowanceError, CATALOG
from .policy import AllowancePolicy, SUPPORTED_FEATURES

FLAG = "AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED"
_ACTIVE = ContextVar("allowance_logical_action", default=None)
_NATIVE = ContextVar("allowance_native_training", default=None)
_EXEMPT = ContextVar("allowance_exempt_action", default=None)
# Physical safety ceilings, not additional visible units. Snapshot at dispatch.
# Each retry/fallback/tool/batch shares this finite ceiling and the spend guard.
ATTEMPT_LIMITS = {"coach": 30, "training_generation": 12,
                  "nutrition_generation": 8, "pump_check": 4, "qr_menu": 48}
PHYSICAL_FEATURES = {
    "coach": frozenset({"coach", "nutrition", "vision"}),
    "training_generation": frozenset({"training_plan"}),
    "nutrition_generation": frozenset({"nutrition_plan"}),
    "pump_check": frozenset({"vision"}),
    "qr_menu": frozenset({"menu_extract", "menu_ocr", "nutrition"}),
}
CONSENT_FEATURE = {"coach": "coach", "training_generation": "training_plan",
                   "nutrition_generation": "nutrition_plan", "pump_check": "vision",
                   "qr_menu": "menu_extract"}


class ExecutionRefused(AllowanceError, ai_spend_guard.AISpendLimitExceeded):
    """Existing allowance catalogue AND terminal provider refusal semantics."""
    def __init__(self, code, **details):
        self.code, self.details = code, details
        self.status, self.retryable = CATALOG[code]
        self.scope, self.provider_class = "allowance", "heavy"
        Exception.__init__(self, code)


def enabled():
    return has_app_context() and current_app.config.get(FLAG) is True


def _refuse(code):
    error = ExecutionRefused(code)
    action = _ACTIVE.get()
    if action is not None:
        action.refusal = error
    if has_request_context():
        g.allowance_refusal = error
    raise error


def require_ready():
    """Atomic launch: no state can enable a subset with Premium bypasses."""
    if not enabled():
        return
    if any(current_app.config.get(key) is not True for key in (
        "AI_ALLOWANCE_LEDGER_ENABLED", "AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED",
        "APPLE_SUBSCRIPTIONS_ENABLED",
    )):
        _refuse("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE")
    policy = current_app.config.get("AI_ALLOWANCE_POLICY")
    if (not isinstance(policy, AllowancePolicy) or policy.premium is None
            or set(policy.premium.allowances) != set(SUPPORTED_FEATURES)):
        _refuse("AI_ALLOWANCE_POLICY_UNCONFIGURED")


def fingerprint(owner, action, value):
    """Keyed semantic digest; no stored request, image, prompt or health facts."""
    def encode(item):
        if isinstance(item, bytes):
            # This inner digest is only ever input to the keyed outer digest.
            return {"bytes_sha256": hashlib.sha256(item).hexdigest()}
        raise TypeError("unsupported logical command fingerprint")
    body = json.dumps([owner, action, value], sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False, default=encode).encode()
    secret = current_app.config["SECRET_KEY"]
    if isinstance(secret, str):
        secret = secret.encode()
    return hmac.new(secret, b"axisai/allowance-command/v1\0" + body, hashlib.sha256).hexdigest()


def request_key():
    if not has_request_context():
        _refuse("AI_ALLOWANCE_INVALID_REQUEST")
    return request.headers.get("Idempotency-Key")


def _check_owner(owner):
    principal = ai_spend_guard.current_subject()
    if type(owner) is not int or (principal is not None and principal != owner):
        raise ai_consent.ConsentDenied()
    if has_request_context():
        # A worker's attribution scope cannot hide a changed request account.
        user = g.get("mobile_user")
        if user is None:
            from flask_login import current_user
            user = current_user if current_user.is_authenticated else None
        # Existing joined workers can push an app context on this same request
        # thread, where g has no auth object; their captured subject remains
        # authoritative. A present, different account must always refuse.
        if (user is not None and user.id != owner) or (user is None and principal is None):
            raise ai_consent.ConsentDenied()


def canonical_success():
    """After a canonical tool commit, textual/transport failure cannot free it."""
    action = _ACTIVE.get()
    if action is not None:
        action.success()


def failure(outcome):
    """Logical outcome only; never invents provider billing evidence."""
    if outcome not in ledger.FAILURE_OUTCOMES:
        raise ValueError("unsupported settlement outcome")
    action = _ACTIVE.get()
    if action is not None:
        action.failure_outcome = outcome


def canonical_within(owner):
    """Fence and mark a canonical write in the writer's SAME transaction.

    A lost commit acknowledgement must never make a canonical action free.
    Native Training continues to use its existing SUCCEEDED operation receipt.
    """
    action = _ACTIVE.get()
    if action is None:
        return
    _check_owner(owner)
    from app.models import AiUsageReservation as Hold
    ai_consent.fence(action.authority, owner)
    changed = db.session.execute(sa.update(Hold).where(
        Hold.user_id == owner, Hold.public_id == action.reference,
        Hold.state == ledger.RESERVED, Hold.expires_at > datetime.now(timezone.utc),
    ).values(canonical_committed=True).execution_options(synchronize_session=False)).rowcount
    if changed != 1:
        _refuse("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE")


def current_action():
    return _ACTIVE.get()


@dataclass
class Action:
    owner: int
    feature: str
    key: str
    digest: str
    authority: object = None
    reference: str | None = None
    successful: bool = False
    failure_outcome: str = "provider_error"
    refusal: object = None
    _lock: object = field(default_factory=threading.Lock)

    def bind_within(self):
        """Call while the durable lease/operation is locked, before its commit."""
        require_ready()
        try:
            hold = ledger.reserve_dispatch_within(
                self.owner, self.feature, self.key, self.digest,
                ATTEMPT_LIMITS[self.feature])
        except AllowanceError as error:
            self.refusal = ExecutionRefused(error.code, **error.details)
            if has_request_context():
                g.allowance_refusal = self.refusal
            raise self.refusal from None
        self.reference = hold.reference
        return hold

    def start(self):
        try:
            self.bind_within()
            db.session.commit()
        except BaseException:
            db.session.rollback()
            self.reference = None
            raise

    def success(self):
        _check_owner(self.owner)
        ai_consent.authorize_operation(self.authority)
        self.successful = True

    def finish(self):
        if self.reference is None:
            return
        # Domain caller has already committed its canonical outcome. Never
        # accidentally commit its failed/pending persistence with settlement.
        db.session.rollback()
        try:
            settled = ledger.finalize_dispatch(self.owner, self.reference,
                successful=self.successful, failure_outcome=self.failure_outcome)
            _metric("settled_success" if settled.outcome == "success" else "settled_failure", self.feature)
        except AllowanceError as error:
            # Hold remains durable and expires consumed. Never free ambiguity.
            current_app.logger.warning("ai_allowance event=settlement_deferred feature=%s code=%s",
                                       self.feature, error.code)


def _metric(event, feature):
    current_app.logger.info("ai_allowance event=%s feature=%s", event, feature)


def prepare(owner, feature, command, *, key=None):
    """After validation, before reserve, context, upload or provider work."""
    if not enabled():
        return None
    require_ready()
    _check_owner(owner)
    if feature not in SUPPORTED_FEATURES:
        proposed = {"training_replacement": "training_generation", "pump_comparison": "pump_check"}
        mappings = current_app.config.get("AI_ALLOWANCE_APPROVED_EXTENSIONS")
        if (type(mappings) is dict and feature in proposed
                and mappings.get(feature) == proposed[feature]):
            feature = proposed[feature]
        else:
        # Undecided classifications have no default mapping or commercial grant.
            _refuse("AI_FEATURE_NOT_IN_PLAN")
    authority = ai_consent.current_operation()
    if authority is None:
        from app.services import ai
        authority = ai_consent.begin_operation(owner, CONSENT_FEATURE[feature], *ai.heavy_primary_route())
    elif authority.owner != owner:
        raise ai_consent.ConsentDenied()
    ai_consent.authorize_operation(authority)
    action = Action(owner, feature, key if key is not None else request_key(),
                    fingerprint(owner, feature, command), authority)
    if has_request_context():
        actions = g.get("allowance_actions", [])
        actions.append(action)
        g.allowance_actions = actions
    _metric("requested", feature)
    return action


@contextmanager
def scope(action):
    if action is None:
        yield None
        return
    _check_owner(action.owner)
    token = _ACTIVE.set(action)
    try:
        with ai_spend_guard.subject_scope(action.owner), ai_consent.operation_scope(action.authority):
            yield action
    finally:
        _ACTIVE.reset(token)


@contextmanager
def logical_action(owner, feature, command, *, key=None):
    """Reserve once; retries/fallback/tools remain inside the same scope."""
    if not enabled():
        yield None
        return
    existing = _ACTIVE.get()
    if existing is not None:
        if existing.owner != owner or existing.feature != feature:
            _refuse("AI_ALLOWANCE_IDEMPOTENCY_CONFLICT")
        yield existing
        return
    action = prepare(owner, feature, command, key=key)
    action.start()
    _metric("authorized", feature)
    with scope(action):
        try:
            yield action
        except TimeoutError:
            failure("provider_timeout")
            raise
        except GeneratorExit:
            failure("cancelled_before_delivery")
            raise
        finally:
            action.finish()


def bind_worker(fn):
    """Copy the SAME action and original D1 authority into joined workers."""
    action, authority, exemption = _ACTIVE.get(), ai_consent.current_operation(), _EXEMPT.get()
    @wraps(fn)
    def run(*args, **kwargs):
        token, ex_token = _ACTIVE.set(action), _EXEMPT.set(exemption)
        try:
            if authority is None:
                return fn(*args, **kwargs)
            with ai_consent.operation_scope(authority):
                return fn(*args, **kwargs)
        finally:
            _EXEMPT.reset(ex_token)
            _ACTIVE.reset(token)
    return run


@contextmanager
def native_training(operation_id):
    token = _NATIVE.set(operation_id)
    try:
        yield
    finally:
        _NATIVE.reset(token)


@contextmanager
def exempt_operation(name):
    if name not in {"workout_photo_proof", "coach_summary"}:
        raise ValueError("undocumented exemption")
    token = _EXEMPT.set(name)
    try:
        yield
    finally:
        _EXEMPT.reset(token)


def check_provider(owner, feature):
    """Run OUTSIDE D1's separate-connection user lock. No provider authority."""
    if not enabled():
        if _ACTIVE.get() is not None:
            _refuse("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE")
        return
    _check_owner(owner)
    require_ready()
    if _exempt_feature(feature):
        return
    action = _ACTIVE.get()
    if action is not None:
        if action.owner != owner or feature not in PHYSICAL_FEATURES[action.feature] or not action.reference:
            _refuse("AI_ALLOWANCE_IDEMPOTENCY_CONFLICT")
        if action.refusal is not None:
            raise action.refusal
        return
    if _NATIVE.get() is not None and feature == "training_plan":
        from app.models import TrainingPlanGenerationOperation as Op, TrainingPlanGenerationAllowance as Bind
        from app.models import AiUsageReservation as Hold
        try:
            valid = db.session.execute(sa.select(Op.id).join(Bind, Bind.operation_id == Op.id)
                .join(Hold, Hold.public_id == Bind.reservation_ref).where(
                    Op.id == _NATIVE.get(), Op.user_id == owner, Bind.user_id == owner,
                    Hold.user_id == owner, Hold.feature == "training_generation",
                    Hold.state == ledger.RESERVED, Hold.expires_at > datetime.now(timezone.utc),
                    Op.status == "IN_PROGRESS", Op.allowance_metered.is_(True),
                    Op.provider_dispatch_possible.is_(True))).scalar_one_or_none()
            db.session.rollback()
        except Exception:
            db.session.rollback()
            _refuse("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE")
        if valid is None:
            _refuse("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE")
        return
    _refuse("AI_FEATURE_NOT_IN_PLAN")


def _exempt_feature(feature):
    return ((_EXEMPT.get() == "workout_photo_proof" and feature == "vision")
            or (_EXEMPT.get() == "coach_summary" and feature == "summary"))


def before_attempt(owner, feature):
    """Inside D1: row-only committed evidence, never an account lock."""
    if not enabled():
        if _ACTIVE.get() is not None:
            _refuse("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE")
        return
    _check_owner(owner)
    if _exempt_feature(feature):
        return
    action = _ACTIVE.get()
    if action is None:
        return  # native Training's existing observer / explicit exemption
    if action.owner != owner or feature not in PHYSICAL_FEATURES[action.feature]:
        _refuse("AI_ALLOWANCE_IDEMPOTENCY_CONFLICT")
    # Serializes joined menu workers locally; PostgreSQL arbitrates processes.
    with action._lock:
        try:
            ledger.record_dispatch_attempt(owner, action.reference)
        except AllowanceError as error:
            action.refusal = ExecutionRefused(error.code, **error.details)
            raise action.refusal from None


def install(app):
    """Recover typed refusals even where legacy routes catch provider errors."""
    def response(error):
        from app.blueprints.mobile_api import mobile_error
        result = current_app.make_response(mobile_error(
            error.code, "AI allowance unavailable.", error.status,
            error.retryable, retry_after=30 if error.retryable else None))
        result.headers["Cache-Control"] = "no-store"
        return result
    app.register_error_handler(ExecutionRefused, response)
    @app.after_request
    def typed_refusal(result):
        error = g.get("allowance_refusal")
        if error is None:
            error = next((a.refusal for a in g.get("allowance_actions", []) if a.refusal), None)
        if error:
            result.close()
            return response(error)
        actions = g.get("allowance_actions", [])
        if actions and not result.is_streamed and result.status_code < 400:
            from app.consent_publication import protect_response
            try:
                with scope(actions[-1]):
                    result = protect_response(result)
            except ai_consent.ConsentDenied as error:
                from app.blueprints.ai_consent import consent_error_response
                result.close()
                return consent_error_response(error)
        return result
