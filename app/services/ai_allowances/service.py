"""Durable AI allowance ledger: Starter grant, reservations and the read view.

Lifecycle: AUTHORIZE (tier from the #436 entitlement, then policy) -> RESERVE
(one unit held under the owner lock) -> EXECUTE (caller's provider work, no
lock held) -> FINALIZE (commit = consume, release = return the unit).

Account writers lock ``user`` first, serializing ledger mutations per owner.
Replay/finalize/expiry lock reservations before updating their buckets; fresh
reserve updates a bucket before inserting its reservation. Retention deletes
terminal reservations before empty old Premium buckets. Bucket arithmetic is one conditional UPDATE each and
the CHECK constraints keep balances non-negative and within the grant even if
a caller skipped the owner lock. Nothing here logs, calls a provider or reads
``User.is_premium``. Every entry point refuses while the flag is OFF.
"""
import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from flask import current_app
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import db
from app.models import AiAllowanceBucket, AiStarterGrant, AiUsageReservation, User
from app.services import subscriptions
from app.services.ai_allowances import policy as P
from app.services.ai_allowances.errors import AllowanceError
from app.services.apple_subscriptions import SubscriptionError

CONTRACT_VERSION = 1
# Long enough for a plan generation with repair; an abandoned hold is settled
# (consumed) after this, so a crash can never mint unlimited free retries.
RESERVATION_TTL = timedelta(minutes=15)
TERMINAL_RETENTION = timedelta(days=90)

RESERVED, COMMITTED, RELEASED, EXPIRED = "RESERVED", "COMMITTED", "RELEASED", "EXPIRED"
OUTCOME_SUCCESS = "success"
OUTCOME_EXPIRED = "expired"
FAILURE_OUTCOMES = frozenset({
    "provider_error",             # provider/server failed; may be billed
    "provider_timeout",           # no result delivered; may be billed
    "invalid_output",             # validator rejected the result; billed
    "refused_before_provider",    # capacity/moderation/validation; never billed
    "cancelled_before_delivery",  # nothing delivered to the user
})
_NEVER_BILLED = frozenset({"refused_before_provider"})
# Billing evidence is separate from the logical outcome. UNKNOWN is stored as
# ``provider_billed = NULL``: never guessed into False (free) or True (billed).
BILLING_CONFIRMED_NOT_BILLED = "CONFIRMED_NOT_BILLED"
BILLING_CONFIRMED_BILLED = "CONFIRMED_BILLED"
BILLING_UNKNOWN = "UNKNOWN"
_BILLED = {BILLING_CONFIRMED_NOT_BILLED: False, BILLING_CONFIRMED_BILLED: True, BILLING_UNKNOWN: None}
_KEY = re.compile(r"[!-~]{8,128}")
_REFERENCE = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True)
class Reservation:
    reference: str
    feature: str
    scope: str
    state: str
    replayed: bool
    expires_at: datetime

    @property
    def may_execute(self):
        """Only a fresh hold authorizes provider work; a replay never does."""
        return self.state == RESERVED and not self.replayed


@dataclass(frozen=True)
class Settlement:
    reference: str
    state: str
    outcome: str
    consumed: bool
    replayed: bool


@dataclass(frozen=True)
class _Subscription:
    state: str               # AVAILABLE | DISABLED
    entitlement: dict | None  # the #436 entitlement view, verbatim

    @property
    def premium(self):
        return bool(self.entitlement) and self.entitlement.get("effective_premium") is True


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def _iso(value):
    return _utc(value).astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def _now(now):
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _require_enabled():
    if current_app.config.get("AI_ALLOWANCE_LEDGER_ENABLED") is not True:
        raise AllowanceError("AI_ALLOWANCE_DISABLED")


def _policy(required=True):
    value = current_app.config.get("AI_ALLOWANCE_POLICY")
    if isinstance(value, P.AllowancePolicy):
        return value
    if value is None and not required:
        return None
    raise AllowanceError("AI_ALLOWANCE_POLICY_UNCONFIGURED")


def _feature(name):
    try:
        return P.canonical_feature(name)
    except P.UnsupportedFeature:
        raise AllowanceError("AI_ALLOWANCE_FEATURE_UNSUPPORTED") from None


def _digest(key):
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise AllowanceError("AI_ALLOWANCE_INVALID_REQUEST")
    return hashlib.sha256(key.encode("ascii")).hexdigest()


def _subscription(user_id):
    """Reuse the #436 resolution as is; called before any ledger lock.

    Disabled verification means no Premium source exists, which is not the same
    as "unknown": the tier is decided. Any other failure is undecided and fails
    closed instead of guessing FREE or PREMIUM.
    """
    try:
        return _Subscription("AVAILABLE", subscriptions.entitlement(user_id))
    except SubscriptionError as error:
        if error.code == "SUBSCRIPTIONS_DISABLED":
            return _Subscription("DISABLED", None)
        if error.code == "SUBSCRIPTION_ACCOUNT_UNAVAILABLE":
            raise AllowanceError("AI_ALLOWANCE_ACCOUNT_UNAVAILABLE") from None
        raise AllowanceError("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE") from None


def _lock_owner(user_id, *, nowait=False):
    owner = db.session.execute(
        sa.select(User).where(User.id == user_id).with_for_update(nowait=nowait)
        .execution_options(populate_existing=True)).scalar_one_or_none()
    if owner is None:
        raise AllowanceError("AI_ALLOWANCE_ACCOUNT_UNAVAILABLE")
    return owner


def _move(bucket_id, now, *, reserve=0, use=0, billed=0):
    """One conditional UPDATE; ``False`` means the guard refused the change."""
    bucket = AiAllowanceBucket
    statement = sa.update(bucket).where(bucket.id == bucket_id)
    if reserve > 0:
        statement = statement.where(bucket.used + bucket.reserved < bucket.granted)
    elif reserve < 0:
        statement = statement.where(bucket.reserved >= -reserve)
    statement = statement.values(
        reserved=bucket.reserved + reserve, used=bucket.used + use,
        billed_releases=bucket.billed_releases + billed, updated_at=now)
    return db.session.execute(statement.execution_options(synchronize_session=False)).rowcount == 1


def _settle(row, state, outcome, *, consume, now, billed=False):
    if not _move(row.bucket_id, now, reserve=-1, use=1 if consume else 0, billed=1 if billed else 0):
        raise AllowanceError("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE")
    row.state, row.outcome, row.finalized_at = state, outcome, now


def _expire_owner(user_id, now):
    rows = db.session.execute(
        sa.select(AiUsageReservation).where(
            AiUsageReservation.user_id == user_id, AiUsageReservation.state == RESERVED,
            AiUsageReservation.expires_at <= now)
        .with_for_update().execution_options(populate_existing=True)).scalars().all()
    for row in rows:
        _settle(row, EXPIRED, OUTCOME_EXPIRED, consume=True, now=now)
    return len(rows)


def _bucket(user_id, scope, feature, key):
    return db.session.execute(
        sa.select(AiAllowanceBucket).where(
            AiAllowanceBucket.user_id == user_id, AiAllowanceBucket.scope == scope,
            AiAllowanceBucket.feature == feature, AiAllowanceBucket.period_key == key)
        .execution_options(populate_existing=True)).scalar_one_or_none()


def _new_bucket(user_id, scope, feature, key, units, version, now):
    return AiAllowanceBucket(user_id=user_id, scope=scope, feature=feature, period_key=key,
                             policy_version=version, granted=units, used=0, reserved=0,
                             billed_releases=0, created_at=now, updated_at=now)


def _starter_locked(owner, policy, now):
    """Exactly-once grant under the owner lock; the PK arbitrates any race.

    Quantities are snapshotted into STARTER buckets, so a later policy change
    never rewrites a balance that was already granted.
    """
    grant = db.session.get(AiStarterGrant, owner.id, populate_existing=True)
    if grant is not None:
        return grant, False
    if not P.starter_eligible(policy, owner.created_at):
        return None, False
    try:
        with db.session.begin_nested():
            grant = AiStarterGrant(user_id=owner.id, policy_version=policy.version, granted_at=now)
            db.session.add(grant)
            db.session.flush()
            db.session.add_all([
                _new_bucket(owner.id, P.SCOPE_STARTER, feature, P.period_key(P.PERIOD_NEVER, now),
                            units, policy.version, now)
                for feature, units in policy.starter.allowances.items()])
            db.session.flush()
    except IntegrityError:
        grant = db.session.get(AiStarterGrant, owner.id, populate_existing=True)
        if grant is None:
            raise AllowanceError("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE") from None
        return grant, False
    return grant, True


def _authorize(owner, policy, subscription, feature, now):
    """Effective Premium uses only Premium allowances: no unlimited, no fallback."""
    if subscription.premium:
        premium = policy.premium
        if premium is None or feature not in premium.allowances:
            raise AllowanceError("AI_FEATURE_NOT_IN_PLAN", feature=feature, scope=P.SCOPE_PREMIUM)
        key = P.period_key(premium.period, now)
        bucket = _bucket(owner.id, P.SCOPE_PREMIUM, feature, key)
        if bucket is None:
            try:
                with db.session.begin_nested():
                    bucket = _new_bucket(owner.id, P.SCOPE_PREMIUM, feature, key,
                                         premium.allowances[feature], policy.version, now)
                    db.session.add(bucket)
                    db.session.flush()
            except IntegrityError:
                bucket = _bucket(owner.id, P.SCOPE_PREMIUM, feature, key)
                if bucket is None:
                    raise AllowanceError("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE") from None
        return bucket, P.SCOPE_PREMIUM
    if policy.starter is not None:
        grant, _ = _starter_locked(owner, policy, now)
    else:
        grant = db.session.get(AiStarterGrant, owner.id, populate_existing=True)
    bucket = grant and _bucket(owner.id, P.SCOPE_STARTER, feature, P.period_key(P.PERIOD_NEVER, now))
    if bucket is None:
        raise AllowanceError("AI_FEATURE_NOT_IN_PLAN", feature=feature, scope=P.SCOPE_STARTER)
    return bucket, P.SCOPE_STARTER


def _exhausted(feature, scope, policy, now):
    period = P.PERIOD_NEVER
    if scope == P.SCOPE_PREMIUM and policy.premium is not None:
        period = policy.premium.period
    return AllowanceError("AI_ALLOWANCE_EXHAUSTED", feature=feature, scope=scope, reset=period,
                          resets_at=_iso(P.resets_at(period, now)))


def _scope_of(row):
    return db.session.get(AiAllowanceBucket, row.bucket_id).scope


def grant_starter(user_id, *, now=None):
    """Explicit grant entry point. Returns GRANTED, ALREADY_GRANTED or NOT_ELIGIBLE."""
    _require_enabled()
    policy = _policy()
    if policy.starter is None:
        raise AllowanceError("AI_FEATURE_NOT_IN_PLAN", scope=P.SCOPE_STARTER)
    now = _now(now)
    try:
        owner = _lock_owner(user_id)
        grant, created = _starter_locked(owner, policy, now)
        db.session.commit()
    except AllowanceError:
        db.session.rollback()
        raise
    except SQLAlchemyError:
        db.session.rollback()
        raise AllowanceError("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE") from None
    if grant is None:
        return "NOT_ELIGIBLE"
    return "GRANTED" if created else "ALREADY_GRANTED"


def _by_digest(user_id, digest):
    return db.session.execute(
        sa.select(AiUsageReservation).where(
            AiUsageReservation.user_id == user_id, AiUsageReservation.key_digest == digest)
        .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()


def _fresh_hold(owner, policy, subscription, feature, digest, now, *, bound):
    """A new hold under the owner lock (lazy expiry, grant, one conditional UPDATE)."""
    _expire_owner(owner.id, now)
    bucket, scope = _authorize(owner, policy, subscription, feature, now)
    if not _move(bucket.id, now, reserve=1):
        raise _exhausted(feature, scope, policy, now)
    row = AiUsageReservation(
        public_id=secrets.token_hex(16), user_id=owner.id, bucket_id=bucket.id,
        feature=feature, key_digest=digest, state=RESERVED, outcome=None,
        # sa.null(): a plain None would let the ORM apply the column default.
        provider_billed=sa.null() if bound else False,
        billing_evidence=BILLING_UNKNOWN if bound else None,
        created_at=now, expires_at=now + RESERVATION_TTL)
    db.session.add(row)
    db.session.flush()
    return Reservation(row.public_id, feature, scope, RESERVED, False, now + RESERVATION_TTL)


def reserve(user_id, feature, idempotency_key, *, now=None):
    """Hold one unit for one logical action, or replay the key's earlier hold.

    ``idempotency_key`` is owner-scoped and stored only as a SHA-256 digest.
    Only ``Reservation.may_execute`` authorizes provider work.
    """
    _require_enabled()
    feature = _feature(feature)
    digest = _digest(idempotency_key)
    policy = _policy()
    now = _now(now)
    subscription = _subscription(user_id)
    try:
        owner = _lock_owner(user_id)
        existing = _by_digest(owner.id, digest)
        if existing is not None:
            if existing.feature != feature:
                raise AllowanceError("AI_ALLOWANCE_IDEMPOTENCY_CONFLICT")
            if existing.state == RESERVED and _utc(existing.expires_at) <= now:
                _settle(existing, EXPIRED, OUTCOME_EXPIRED, consume=True, now=now)
            if existing.state == RESERVED:
                raise AllowanceError("AI_ALLOWANCE_IN_PROGRESS")
            result = Reservation(existing.public_id, existing.feature, _scope_of(existing),
                                 existing.state, True, _utc(existing.expires_at))
            db.session.commit()
            return result
        try:
            result = _fresh_hold(owner, policy, subscription, feature, digest, now, bound=False)
        except AllowanceError as error:
            if error.code == "AI_ALLOWANCE_EXHAUSTED":
                db.session.commit()  # keep a fresh grant and any settled expiries
            raise
        db.session.commit()
        return result
    except AllowanceError:
        db.session.rollback()
        raise
    except SQLAlchemyError:
        db.session.rollback()
        raise AllowanceError("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE") from None


def reserve_within(user_id, feature, idempotency_key, *, now=None, strict_entitlement=False):
    """``reserve`` for a caller that authorizes provider dispatch in the SAME commit.

    The caller owns the transaction: nothing here commits or rolls back, so the
    hold and the caller's durable dispatch authorization become visible together
    or not at all. The caller may hold only rows that precede ``user`` in the
    repository lock order (its own operation row); this then takes the owner
    lock, reservations and the bucket, exactly like ``reserve``. The hold starts
    with billing evidence UNKNOWN because dispatch is possible from its commit.
    A dispatch-bound hold is never a replay: an existing key is refused.
    """
    _require_enabled()
    feature = _feature(feature)
    digest = _digest(idempotency_key)
    policy = _policy()
    now = _now(now)
    subscription = None if strict_entitlement else _subscription(user_id)
    try:
        owner = _lock_owner(user_id)
        if strict_entitlement:
            subscription = _dispatch_subscription(owner.id)
        if _by_digest(owner.id, digest) is not None:
            raise AllowanceError("AI_ALLOWANCE_IDEMPOTENCY_CONFLICT")
        return _fresh_hold(owner, policy, subscription, feature, digest, now, bound=True)
    except SQLAlchemyError:
        raise AllowanceError("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE") from None


def _dispatch_subscription(user_id):
    """Resolve after the owner lock, without expiring the caller's dirty lease."""
    from app.models import AppleSubscription
    for identity in list(db.session.identity_map.values()):
        if isinstance(identity, AppleSubscription):
            db.session.expire(identity)
    subscription = _subscription(user_id)
    if subscription.state != "AVAILABLE" or subscription.entitlement.get("verification_pending"):
        raise AllowanceError("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE")
    return subscription


def reserve_dispatch_within(user_id, feature, idempotency_key, fingerprint,
                            attempt_limit, *, now=None):
    """Bind one generic logical dispatch to the ledger in the caller's commit.

    A RESERVED row with non-NULL dispatch evidence IS the durable dispatch
    marker. Only this fresh return authorizes execution; even RELEASED keys
    cannot execute again. Durable callers update their own lease in this same
    transaction. No provider I/O occurs here.
    """
    _require_enabled()
    feature, digest = _feature(feature), _digest(idempotency_key)
    if (not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
            or type(attempt_limit) is not int or not 1 <= attempt_limit <= 100):
        raise AllowanceError("AI_ALLOWANCE_INVALID_REQUEST")
    policy, now = _policy(), _now(now)
    try:
        owner = _lock_owner(user_id)
        # Subscription writers serialize on this same owner row.
        subscription = _dispatch_subscription(owner.id)
        existing = _by_digest(user_id, digest)
        if existing is not None:
            if existing.feature != feature or existing.request_fingerprint != fingerprint:
                raise AllowanceError("AI_ALLOWANCE_IDEMPOTENCY_CONFLICT")
            if existing.state == RESERVED and _utc(existing.expires_at) > now:
                raise AllowanceError("AI_ALLOWANCE_IN_PROGRESS")
            # A retained replay is never a second dispatch, including after
            # hold expiry. Sweep owns expiry accounting; no hidden commit.
            raise AllowanceError("AI_ALLOWANCE_RESERVATION_CONFLICT")
        hold = _fresh_hold(owner, policy, subscription, feature, digest, now, bound=True)
        row = _by_digest(user_id, digest)
        row.request_fingerprint = fingerprint
        row.physical_attempts, row.physical_attempt_limit = 0, attempt_limit
        row.canonical_committed = False
        from . import physical
        physical.initialize(row)
        db.session.flush()
        return hold
    except SQLAlchemyError:
        raise AllowanceError("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE") from None


def record_dispatch_attempt(user_id, reference, *, now=None):
    """Committed evidence BEFORE each SDK entry, inside the existing D1 fence.

    Intentionally no owner/entitlement/bucket lock: D1 holds User on a separate
    connection. This one conditional reservation update cannot invert that
    lock order. NULL/historical, terminal, expired and exhausted work refuse.
    """
    _require_enabled()
    now = _now(now)
    row = AiUsageReservation
    try:
        changed = db.session.execute(sa.update(row).where(
            row.user_id == user_id, row.public_id == reference,
            row.state == RESERVED, row.expires_at > now,
            row.request_fingerprint.is_not(None), row.physical_attempts >= 0,
            row.physical_attempts < row.physical_attempt_limit,
        ).values(physical_attempts=row.physical_attempts + 1)
          .execution_options(synchronize_session=False)).rowcount
        if changed != 1:
            raise AllowanceError("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE")
        db.session.commit()
    except (AllowanceError, SQLAlchemyError) as error:
        db.session.rollback()
        if isinstance(error, AllowanceError):
            raise
        raise AllowanceError("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE") from None


def finalize_dispatch(user_id, reference, *, successful=False, failure_outcome="provider_error"):
    """Seal attempts and settle under ONE owner/reservation transaction.

    D1 serializes physical entry on this same owner row. Once locked, no
    producer can enter or append evidence until finalize commits a terminal
    state. Its conditional pre-attempt update then refuses. Zero is therefore
    immutable proof even when a disconnected streaming producer outlives its
    consumer. A crash before this transaction leaves expiry to consume.
    """
    _require_enabled()
    if failure_outcome not in FAILURE_OUTCOMES:
        raise AllowanceError("AI_ALLOWANCE_INVALID_REQUEST")
    try:
        _lock_owner(user_id)
        row = db.session.execute(sa.select(AiUsageReservation).where(
            AiUsageReservation.user_id == user_id,
            AiUsageReservation.public_id == reference)
            .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        if row is None:
            raise AllowanceError("AI_ALLOWANCE_RESERVATION_NOT_FOUND")
        if row.request_fingerprint is None:
            raise AllowanceError("AI_ALLOWANCE_RESERVATION_CONFLICT")
        if row.state != RESERVED:
            return finalize(user_id, reference, row.outcome if row.state != EXPIRED else "provider_timeout",
                            billing_evidence=row.billing_evidence or BILLING_UNKNOWN)
        evidence = BILLING_CONFIRMED_NOT_BILLED if row.physical_attempts == 0 else BILLING_UNKNOWN
        outcome = "success" if successful or row.canonical_committed is True else (
            "refused_before_provider" if evidence == BILLING_CONFIRMED_NOT_BILLED else (
                failure_outcome if failure_outcome != "refused_before_provider" else "provider_error"))
        return finalize(user_id, reference, outcome, billing_evidence=evidence)
    except (AllowanceError, SQLAlchemyError) as error:
        db.session.rollback()
        if isinstance(error, AllowanceError):
            raise
        raise AllowanceError("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE") from None


def finalize(user_id, reference, outcome, *, provider_billed=False, billing_evidence=None,
             now=None):
    """Settle a hold exactly once: success consumes, a qualifying failure releases.

    A provider-billed failure is recorded on the row (FinOps evidence) and is
    released at most ``max_billed_releases`` times per bucket; beyond that it
    consumes. Repeating the same settlement replays; a different one conflicts.

    ``billing_evidence`` (instead of ``provider_billed``) records the tri-state.
    UNKNOWN stores ``provider_billed = NULL`` and, because it is not proven
    unbilled, a failure is subject to the same ``max_billed_releases`` cap as a
    billed one. A dispatch-bound hold (``reserve_within``) must be settled with
    explicit evidence so a Boolean default can never overwrite UNKNOWN.
    """
    _require_enabled()
    if billing_evidence is not None:
        if billing_evidence not in _BILLED or provider_billed is not False:
            raise AllowanceError("AI_ALLOWANCE_INVALID_REQUEST")
        provider_billed = _BILLED[billing_evidence]
    if (not isinstance(reference, str) or not _REFERENCE.fullmatch(reference)
            or not isinstance(outcome, str)
            or (outcome != OUTCOME_SUCCESS and outcome not in FAILURE_OUTCOMES)
            or (type(provider_billed) is not bool and billing_evidence != BILLING_UNKNOWN)
            or (outcome in _NEVER_BILLED and provider_billed is not False)):
        raise AllowanceError("AI_ALLOWANCE_INVALID_REQUEST")
    policy = _policy(required=False)
    now = _now(now)
    try:
        _lock_owner(user_id)
        row = db.session.execute(
            sa.select(AiUsageReservation).where(
                AiUsageReservation.public_id == reference, AiUsageReservation.user_id == user_id)
            .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        if row is None:
            raise AllowanceError("AI_ALLOWANCE_RESERVATION_NOT_FOUND")
        if row.state == RESERVED and _utc(row.expires_at) <= now:
            _settle(row, EXPIRED, OUTCOME_EXPIRED, consume=True, now=now)
        if row.state != RESERVED:
            if row.state != EXPIRED and row.outcome != outcome:
                raise AllowanceError("AI_ALLOWANCE_RESERVATION_CONFLICT")
            result = Settlement(row.public_id, row.state, row.outcome, row.state != RELEASED, True)
            db.session.commit()
            return result
        if row.billing_evidence is not None and billing_evidence is None:
            raise AllowanceError("AI_ALLOWANCE_INVALID_REQUEST")
        row.provider_billed = provider_billed
        row.billing_evidence = billing_evidence
        if outcome == OUTCOME_SUCCESS:
            _settle(row, COMMITTED, outcome, consume=True, now=now)
        else:
            possibly_billed = provider_billed is not False
            bucket = db.session.get(AiAllowanceBucket, row.bucket_id, populate_existing=True)
            scope_policy = policy.scope(bucket.scope) if policy is not None else None
            cap = scope_policy.max_billed_releases if scope_policy is not None else 0
            if possibly_billed and bucket.billed_releases >= cap:
                _settle(row, COMMITTED, outcome, consume=True, now=now)
            else:
                _settle(row, RELEASED, outcome, consume=False, billed=possibly_billed, now=now)
        result = Settlement(row.public_id, row.state, row.outcome, row.state == COMMITTED, False)
        db.session.commit()
        return result
    except AllowanceError:
        db.session.rollback()
        raise
    except SQLAlchemyError:
        db.session.rollback()
        raise AllowanceError("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE") from None


def expire_abandoned(*, now=None, limit=500):
    """Settle holds past their TTL as consumed, one owner lock at a time."""
    _require_enabled()
    now = _now(now)
    try:
        owners = db.session.execute(
            sa.select(AiUsageReservation.user_id).where(
                AiUsageReservation.state == RESERVED, AiUsageReservation.expires_at <= now)
            .distinct().order_by(AiUsageReservation.user_id).limit(limit)).scalars().all()
        db.session.rollback()
        expired = 0
        for user_id in owners:
            try:
                _lock_owner(user_id)
            except AllowanceError:
                db.session.rollback()  # deleted meanwhile; its rows cascaded away
                continue
            expired += _expire_owner(user_id, now)
            db.session.commit()
        return expired
    except SQLAlchemyError:
        db.session.rollback()
        raise AllowanceError("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE") from None


def _claim_purge_candidates(cutoff, audit_cutoff, limit):
    """Commit traversal progress BEFORE taking any financial authority locks.

    A finite high-water cycle revisits every deferred ID. A crash after claim
    delays its batch until the next cycle; it never marks evidence completed.
    Concurrent workers serialize only this short claim, not owner processing.
    """
    from app.models import AiAllowancePurgeProgress, TrainingAllowanceCompensation
    progress = AiAllowancePurgeProgress
    if db.engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
        db.session.execute(sa.text("SET LOCAL lock_timeout = '2000ms'"))
    else:
        from sqlalchemy.dialects.sqlite import insert
    db.session.execute(insert(progress).values(id=1, after_id=0, through_id=0)
                       .on_conflict_do_nothing(index_elements=["id"]))
    cursor = db.session.execute(sa.select(progress).where(progress.id == 1)
        .with_for_update(nowait=True).execution_options(populate_existing=True)).scalar_one()
    reservation = AiUsageReservation
    def page():
        return db.session.execute(sa.select(reservation.id, reservation.user_id).where(
            reservation.id > cursor.after_id, reservation.id <= cursor.through_id,
            reservation.state != RESERVED, reservation.finalized_at < cutoff,
            ~sa.exists().where(
                TrainingAllowanceCompensation.reservation_id == reservation.id,
                TrainingAllowanceCompensation.applied_at >= audit_cutoff))
            .order_by(reservation.id).limit(limit)).all()
    candidates = page() if cursor.after_id < cursor.through_id else []
    if not candidates:
        cursor.after_id = 0
        cursor.through_id = db.session.execute(sa.select(sa.func.coalesce(
            sa.func.max(reservation.id), 0))).scalar_one()
        candidates = page()
    cursor.after_id = candidates[-1].id if candidates else cursor.through_id
    db.session.commit()
    return candidates


def purge_retained(*, now=None, retention=TERMINAL_RETENTION, limit=500):
    """Bounded retained-evidence cleanup, with pre-purge correction arbitration.

    At most 500 reservations per call. A newly applied receipt retains its
    original reservation for a full audit window from correction. Busy owners
    or operation locks defer deletion; absent/contradictory proof never refunds.
    Durable finite keyset cycles revisit deferred candidates after restart.
    At most 500 empty Premium buckets are removed; no scheduler is installed.
    """
    _require_enabled()
    from app.models import TrainingAllowanceCompensation, TrainingPlanGenerationAllowance
    from . import training_compensation as compensation
    now = _now(now)
    if not isinstance(retention, timedelta) or retention < timedelta(0):
        raise ValueError("retention must be a nonnegative timedelta")
    # Caller cleanup horizons cannot shorten original proof eligibility or the
    # independent audit lifetime that starts when a correction is committed.
    cutoff = now - max(retention, TERMINAL_RETENTION)
    audit_cutoff = min(now - TERMINAL_RETENTION, now - retention)
    reservation = AiUsageReservation
    try:
        batch_limit = min(max(limit, 0), 500)
        candidates = (_claim_purge_candidates(cutoff, audit_cutoff, batch_limit)
                      if batch_limit else [])
        settled = 0
        for reservation_id, user_id in candidates:
            try:
                if db.engine.dialect.name == "postgresql":
                    db.session.execute(sa.text("SET LOCAL lock_timeout = '2000ms'"))
                _lock_owner(user_id, nowait=True)
                # Discover only; correct_within takes operation NOWAIT before
                # binding/reservation. Never invert those locks during purge.
                operation_id = db.session.execute(sa.select(
                    TrainingPlanGenerationAllowance.operation_id).join(reservation,
                    TrainingPlanGenerationAllowance.reservation_ref == reservation.public_id)
                    .where(reservation.id == reservation_id)).scalar_one_or_none()
                if operation_id is not None and compensation.enabled():
                    compensation.correct_within(user_id, operation_id, now)
                row = db.session.execute(sa.select(reservation).where(
                    reservation.id == reservation_id, reservation.user_id == user_id)
                    .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
                receipt = db.session.get(TrainingAllowanceCompensation, reservation_id,
                                         populate_existing=True)
                if (row is not None and row.state != RESERVED and _utc(row.finalized_at) < cutoff
                        and (receipt is None or _utc(receipt.applied_at) < audit_cutoff)):
                    db.session.delete(row)
                    settled += 1
                db.session.commit()
            except (AllowanceError, SQLAlchemyError):
                db.session.rollback()  # retry evidence later, never erase a failed correction
        if db.engine.dialect.name == "postgresql":
            db.session.execute(sa.text("SET LOCAL lock_timeout = '2000ms'"))
        old_buckets = sa.select(AiAllowanceBucket.id).where(
            AiAllowanceBucket.scope == P.SCOPE_PREMIUM, AiAllowanceBucket.updated_at < cutoff,
            ~sa.exists().where(reservation.bucket_id == AiAllowanceBucket.id)
        ).order_by(AiAllowanceBucket.id).limit(batch_limit)
        periods = db.session.execute(sa.delete(AiAllowanceBucket).where(
            AiAllowanceBucket.id.in_(old_buckets))
            .execution_options(synchronize_session=False)).rowcount
        db.session.commit()
        return settled, periods
    except SQLAlchemyError:
        db.session.rollback()
        raise AllowanceError("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE") from None


def _feature_view(feature, access, policy, starter_state, buckets, now):
    view = {"feature": feature, "scope": None, "state": "NOT_IN_PLAN", "limit": None,
            "used": None, "reserved": None, "remaining": None, "reset": None, "resets_at": None}
    if access == "UNCONFIGURED":
        return {**view, "state": "NOT_CONFIGURED"}
    if access == "NONE":
        return view
    if access == "PREMIUM":
        scope_policy, scope = policy.premium, P.SCOPE_PREMIUM
    else:
        scope_policy, scope = policy.starter, P.SCOPE_STARTER
    view["scope"] = scope
    period = P.PERIOD_NEVER if scope == P.SCOPE_STARTER else scope_policy and scope_policy.period
    bucket = buckets.get((scope, feature, P.period_key(period, now))) if period else None
    if bucket is not None:
        limit, used, held = bucket.granted, bucket.used, bucket.reserved
    elif ((scope == P.SCOPE_PREMIUM or starter_state == "ELIGIBLE")
          and scope_policy is not None and feature in scope_policy.allowances):
        limit, used, held = scope_policy.allowances[feature], 0, 0
    else:
        return view
    remaining = max(limit - used - held, 0)
    return {**view, "state": "AVAILABLE" if remaining else "EXHAUSTED", "limit": limit,
            "used": used, "reserved": held, "remaining": remaining, "reset": period,
            "resets_at": _iso(P.resets_at(period, now))}


def allowance_view(user_id, *, now=None):
    """Read-only, server-authoritative allowance state (contract_version 1).

    Never grants, reserves or settles. Missing policy is ``NOT_CONFIGURED`` and a
    missing scope or feature is ``NOT_IN_PLAN`` - never unlimited, never zero.
    """
    _require_enabled()
    policy = _policy(required=False)
    now = _now(now)
    subscription = _subscription(user_id)
    try:
        owner = db.session.get(User, user_id)
        if owner is None:
            raise AllowanceError("AI_ALLOWANCE_ACCOUNT_UNAVAILABLE")
        grant = db.session.get(AiStarterGrant, user_id)
        keys = {P.period_key(P.PERIOD_NEVER, now)}
        if policy is not None and policy.premium is not None:
            keys.add(P.period_key(policy.premium.period, now))
        buckets = {(b.scope, b.feature, b.period_key): b for b in db.session.execute(
            sa.select(AiAllowanceBucket).where(AiAllowanceBucket.user_id == user_id,
                                               AiAllowanceBucket.period_key.in_(keys))).scalars()}
    except AllowanceError:
        db.session.rollback()
        raise
    except SQLAlchemyError:
        db.session.rollback()
        raise AllowanceError("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE") from None
    if grant is not None:
        starter_state = "GRANTED"
    elif policy is None or policy.starter is None:
        starter_state = "UNCONFIGURED"
    else:
        starter_state = "ELIGIBLE" if P.starter_eligible(policy, owner.created_at) else "NOT_ELIGIBLE"
    if policy is None:
        access = "UNCONFIGURED"
    elif subscription.premium:
        access = "PREMIUM"
    elif starter_state in ("GRANTED", "ELIGIBLE"):
        access = "STARTER"
    else:
        access = "NONE"
    return {
        "contract_version": CONTRACT_VERSION,
        "policy_state": "UNCONFIGURED" if policy is None else "CONFIGURED",
        "access": access,
        "subscription": {"state": subscription.state, "entitlement": subscription.entitlement},
        "starter": {"state": starter_state, "granted_at": _iso(grant.granted_at) if grant else None},
        "features": [_feature_view(f, access, policy, starter_state, buckets, now)
                     for f in P.SUPPORTED_FEATURES],
    }
