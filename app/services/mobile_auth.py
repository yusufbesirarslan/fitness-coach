"""Transactional opaque mobile-session lifecycle."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hmac

from flask import current_app

from app.extensions import db
from app.observability import current_request_id
from app.models import (
    MobileAccessCredential, MobileAuthSession, MobileRefreshCredential, User,
)
from app.services import cognito_jwt, cognito_service, mobile_credentials, session_store
from app.services.ai_gate import (
    BlockingConcurrencyLimit, blocking_concurrency_slot,
)
from app.services.cognito_identity import reconcilable_local_user


@dataclass(frozen=True)
class IssuedSession:
    access_credential: str = field(repr=False)
    refresh_credential: str = field(repr=False)
    access_expires_at: datetime
    refresh_expires_at: datetime


@dataclass(frozen=True)
class MobilePrincipal:
    user: User
    family: MobileAuthSession
    claims: dict


@dataclass(frozen=True)
class LogoutResult:
    family_id: str | None = None
    provider_refresh_token: str | None = field(default=None, repr=False)


class MobileAuthFailure(Exception):
    def __init__(self, code, status, retryable, reason, retry_after=None):
        super().__init__(reason)
        self.code = code
        self.status = status
        self.retryable = retryable
        self.reason = reason
        self.retry_after = retry_after


class _DefinitiveRefreshFailure(Exception):
    pass


def _failure(code, status, retryable, reason, retry_after=None):
    return MobileAuthFailure(
        code, status, retryable, reason, retry_after=retry_after)


def _security_event(event, family_id="-", category="auth", key_version="-"):
    current_app.logger.warning(
        "mobile_auth event=%s family=%s category=%s key_version=%s request_id=%s",
        event, family_id, category, key_version, current_request_id())


def calculate_access_expiry(now, family_absolute_expires_at):
    configured = now + timedelta(
        seconds=current_app.config["MOBILE_AUTH_ACCESS_TTL_SECONDS"])
    return min(configured, family_absolute_expires_at)


def _coverage(now, absolute_expires_at):
    access_exp = calculate_access_expiry(now, absolute_expires_at)
    deadline = access_exp + timedelta(seconds=current_app.config[
        "MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS"])
    trigger = max(
        now + timedelta(seconds=current_app.config[
            "MOBILE_AUTH_COGNITO_EXPIRY_LEEWAY_SECONDS"]),
        deadline,
    )
    return access_exp, deadline, trigger


def _claim_expiry(claims):
    try:
        return datetime.utcfromtimestamp(int(claims["exp"]))
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise _failure(
            "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
            "provider_expiry_unavailable") from exc


def _resolve_user(claims, username):
    sub = (claims.get("sub") or "").strip()
    email = (claims.get("email") or "").strip().lower()
    if not sub:
        return None
    user = User.query.filter_by(cognito_sub=sub).first()
    if user is not None:
        return user
    verified = claims.get("email_verified")
    if not email or (verified is not True and str(verified).lower() != "true"):
        return None
    user, denial = reconcilable_local_user(username, email, sub)
    if denial is not None:
        return None
    if user is not None:
        user.cognito_sub = sub
        return user
    user = User(
        username=username, email=email, cognito_sub=sub,
        full_name=claims.get("name") or username, language="tr")
    db.session.add(user)
    db.session.flush()
    return user


def _validate_provider(token, expected_use):
    try:
        return cognito_jwt.validate_token(
            token, expected_use,
            leeway_seconds=current_app.config[
                "MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS"])
    except cognito_jwt.TokenValidationError as exc:
        if exc.reason == "jwks_unavailable":
            _security_event("jwks_unavailable", category="provider")
            raise _failure(
                "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
                "jwks_unavailable") from exc
        raise _failure(
            "AUTH_INVALID_CREDENTIALS", 401, False,
            "provider_token_invalid") from exc


def _validate_refreshed_provider(token, expected_sub):
    """Validate renewed access material using refresh failure semantics."""
    try:
        claims = cognito_jwt.validate_token(
            token, "access",
            leeway_seconds=current_app.config[
                "MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS"])
    except cognito_jwt.TokenValidationError as exc:
        if exc.reason == "jwks_unavailable":
            _security_event("jwks_unavailable", category="provider")
            raise _failure(
                "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
                "jwks_unavailable") from exc
        raise _DefinitiveRefreshFailure("provider_token_invalid") from exc
    if not claims.get("sub") or claims.get("sub") != expected_sub:
        raise _DefinitiveRefreshFailure("provider_subject_mismatch")
    try:
        provider_exp = _claim_expiry(claims)
    except MobileAuthFailure as exc:
        raise _DefinitiveRefreshFailure(
            "provider_token_invalid") from exc
    return claims, provider_exp


def login(username, password, now=None):
    now = now or datetime.utcnow()
    try:
        with blocking_concurrency_slot():
            try:
                result = cognito_service.authenticate(username, password)
                tokens = dict(result["tokens"])
            except cognito_service.CognitoServiceError as exc:
                if exc.code == "UserNotConfirmedException":
                    _security_event("unconfirmed_account")
                    raise _failure(
                        "AUTH_INVALID_CREDENTIALS", 401, False,
                        "invalid_credentials") from exc
                if exc.code in {
                        "TooManyRequestsException", "InternalErrorException",
                        "LimitExceededException", "ServiceUnavailableException",
                        "JWKSUnavailable", ""}:
                    raise _failure(
                        "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
                        "provider_unavailable") from exc
                raise _failure(
                    "AUTH_INVALID_CREDENTIALS", 401, False,
                    "invalid_credentials") from exc
            except Exception as exc:
                raise _failure(
                    "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
                    "provider_response_invalid") from exc

            id_claims = _validate_provider(tokens.get("id_token", ""), "id")
            access_claims = _validate_provider(
                tokens.get("access_token", ""), "access")
            if (not id_claims.get("sub")
                    or id_claims.get("sub") != access_claims.get("sub")):
                raise _failure(
                    "AUTH_INVALID_CREDENTIALS", 401, False,
                    "provider_subject_mismatch")
            absolute_exp = now + timedelta(
                days=current_app.config["MOBILE_AUTH_REFRESH_ABSOLUTE_DAYS"])
            access_exp, deadline, trigger = _coverage(now, absolute_exp)
            provider_exp = _claim_expiry(access_claims)
            if provider_exp <= trigger:
                tokens = cognito_service.refresh_tokens(
                    tokens.get("refresh_token", ""), username)
                access_claims = _validate_provider(
                    tokens.get("access_token", ""), "access")
                if access_claims.get("sub") != id_claims.get("sub"):
                    raise _failure(
                        "AUTH_INVALID_CREDENTIALS", 401, False,
                        "provider_subject_mismatch")
                provider_exp = _claim_expiry(access_claims)
            if provider_exp <= deadline:
                _security_event(
                    "provider_coverage_insufficient", category="provider")
                raise _failure(
                    "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
                    "provider_coverage_insufficient")
    except BlockingConcurrencyLimit as exc:
        raise _failure(
            "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
            "blocking_capacity_exhausted", retry_after=15) from exc
    except MobileAuthFailure:
        db.session.rollback()
        raise
    except cognito_service.CognitoServiceError as exc:
        db.session.rollback()
        raise _failure(
            "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
            "provider_renewal_failed") from exc
    except Exception as exc:
        db.session.rollback()
        raise _failure(
            "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
            "session_commit_failed") from exc

    try:
        user = _resolve_user(id_claims, username)
        if user is None:
            raise _failure(
                "AUTH_INVALID_CREDENTIALS", 401, False,
                "local_identity_unavailable")
        access_raw = mobile_credentials.generate_credential()
        refresh_raw = mobile_credentials.generate_credential()
        family = MobileAuthSession(
            family_id=mobile_credentials.generate_credential(),
            user_id=user.id,
            cognito_username=username,
            cognito_sub=id_claims["sub"],
            cognito_access_token=session_store.encrypt_token(tokens["access_token"]),
            cognito_refresh_token=session_store.encrypt_token(tokens["refresh_token"]),
            cognito_access_expires_at=provider_exp,
            absolute_expires_at=absolute_exp,
            version=1, created_at=now, last_used_at=now, updated_at=now,
        )
        db.session.add(family)
        db.session.flush()
        db.session.add_all([
            MobileAccessCredential(
                session_id=family.id,
                credential_hash=mobile_credentials.hash_credential(access_raw),
                generation=0, issued_at=now, expires_at=access_exp),
            MobileRefreshCredential(
                session_id=family.id,
                credential_hash=mobile_credentials.hash_credential(refresh_raw),
                generation=0, issued_at=now, expires_at=absolute_exp),
        ])
        db.session.commit()
        return IssuedSession(access_raw, refresh_raw, access_exp, absolute_exp)
    except MobileAuthFailure:
        db.session.rollback()
        raise
    except cognito_service.CognitoServiceError as exc:
        db.session.rollback()
        raise _failure(
            "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
            "provider_renewal_failed") from exc
    except Exception as exc:
        db.session.rollback()
        raise _failure(
            "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
            "session_commit_failed") from exc


def _revoke_family(family, reason, now):
    family.revoked_at = family.revoked_at or now
    family.revoked_reason = family.revoked_reason or reason
    family.cognito_access_token = None
    family.cognito_refresh_token = None
    MobileAccessCredential.query.filter_by(session_id=family.id).update(
        {MobileAccessCredential.revoked_at: now}, synchronize_session=False)
    MobileRefreshCredential.query.filter_by(session_id=family.id).update(
        {MobileRefreshCredential.revoked_at: now}, synchronize_session=False)


def _revoke_family_and_commit(family, reason, now):
    """Persist irreversible local revocation or raise a normalized retry."""
    try:
        _revoke_family(family, reason, now)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        raise _failure(
            "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
            "storage_unavailable") from exc


def authenticate_access(raw_access, now=None):
    now = now or datetime.utcnow()
    try:
        digest = mobile_credentials.hash_credential(raw_access)
    except mobile_credentials.InvalidMobileCredential as exc:
        raise _failure(
            "AUTH_SESSION_EXPIRED", 401, False, "access_invalid") from exc
    try:
        row = MobileAccessCredential.query.filter_by(credential_hash=digest).first()
        family = (db.session.get(MobileAuthSession, row.session_id)
                  if row is not None else None)
    except Exception as exc:
        db.session.rollback()
        raise _failure(
            "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
            "storage_unavailable") from exc
    if row is None or not hmac.compare_digest(row.credential_hash, digest):
        raise _failure("AUTH_SESSION_EXPIRED", 401, False, "access_invalid")
    if (family is None or row.revoked_at is not None
            or family.revoked_at is not None):
        raise _failure("AUTH_SESSION_EXPIRED", 401, False, "access_expired")
    if family.absolute_expires_at <= now:
        _revoke_family_and_commit(family, "absolute_expired", now)
        raise _failure("AUTH_SESSION_EXPIRED", 401, False, "access_expired")
    if row.expires_at <= now:
        raise _failure("AUTH_SESSION_EXPIRED", 401, False, "access_expired")
    try:
        provider_access = session_store.decrypt_token(family.cognito_access_token)
    except Exception as exc:
        _revoke_family_and_commit(
            family, "provider_ciphertext_invalid", now)
        raise _failure(
            "AUTH_SESSION_EXPIRED", 401, False,
            "provider_ciphertext_invalid") from exc
    try:
        claims = _validate_provider(provider_access, "access")
    except MobileAuthFailure as exc:
        if exc.code == "AUTH_TEMPORARILY_UNAVAILABLE":
            raise
        _revoke_family_and_commit(
            family, "provider_validation_failed", now)
        raise _failure(
            "AUTH_SESSION_EXPIRED", 401, False,
            "provider_validation_failed") from exc
    try:
        user = db.session.get(User, family.user_id)
    except Exception as exc:
        db.session.rollback()
        raise _failure(
            "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
            "storage_unavailable") from exc
    sub = claims.get("sub")
    if (user is None or not family.cognito_sub or sub != family.cognito_sub
            or user.cognito_sub != family.cognito_sub):
        _revoke_family_and_commit(family, "ownership_mismatch", now)
        raise _failure(
            "AUTH_SESSION_EXPIRED", 401, False, "ownership_mismatch")
    return MobilePrincipal(user, family, claims)


def _refresh_failed(reason):
    return _failure("AUTH_REFRESH_FAILED", 401, False, reason)


def _replay_consumed_parent(parent, family, raw_refresh, now):
    if parent.grace_expires_at is None or now > parent.grace_expires_at:
        _security_event("refresh_reuse", family.family_id, "credential")
        _revoke_family_and_commit(family, "refresh_reuse", now)
        raise _refresh_failed("refresh_reuse")
    try:
        pair = mobile_credentials.derive_replacement_pair(
            raw_refresh, family.family_id, parent.generation,
            parent.replacement_generation, parent.replacement_key_version,
            current_app.config["MOBILE_AUTH_DERIVATION_KEYRING"])
        access = db.session.get(
            MobileAccessCredential, parent.replacement_access_id)
        child = db.session.get(
            MobileRefreshCredential, parent.replacement_refresh_id)
        if (access is None or child is None
                or not hmac.compare_digest(
                    access.credential_hash,
                    mobile_credentials.hash_credential(pair.access))
                or not hmac.compare_digest(
                    child.credential_hash,
                    mobile_credentials.hash_credential(pair.refresh))):
            raise ValueError("replacement hash mismatch")
        issued = IssuedSession(
            pair.access, pair.refresh,
            parent.replacement_access_expires_at,
            parent.replacement_refresh_expires_at)
    except Exception as exc:
        db.session.rollback()
        raise _failure(
            "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
            "refresh_replay_unavailable") from exc
    db.session.rollback()
    return issued


def refresh(raw_refresh, now=None):
    """Rotate one refresh generation or replay its one committed child."""
    now = now or datetime.utcnow()
    try:
        digest = mobile_credentials.hash_credential(raw_refresh)
    except mobile_credentials.InvalidMobileCredential as exc:
        raise _refresh_failed("refresh_invalid") from exc

    try:
        located = db.session.query(
            MobileRefreshCredential.id,
            MobileRefreshCredential.session_id,
        ).filter_by(credential_hash=digest).first()
        if located is None:
            raise _refresh_failed("refresh_invalid")
        parent_id, family_id = located
        family = (MobileAuthSession.query.filter_by(id=family_id)
                  .with_for_update().one_or_none())
        parent = (MobileRefreshCredential.query.filter_by(
            id=parent_id, session_id=family_id).with_for_update().one_or_none())
    except MobileAuthFailure:
        db.session.rollback()
        raise
    except Exception as exc:
        db.session.rollback()
        raise _failure(
            "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
            "storage_unavailable") from exc
    if family is None or parent is None:
        db.session.rollback()
        raise _refresh_failed("refresh_invalid")
    if not hmac.compare_digest(parent.credential_hash, digest):
        db.session.rollback()
        raise _refresh_failed("refresh_invalid")
    if family.revoked_at is not None or parent.revoked_at is not None:
        db.session.rollback()
        raise _refresh_failed("refresh_revoked")
    if family.absolute_expires_at <= now or parent.expires_at <= now:
        _revoke_family_and_commit(family, "refresh_expired", now)
        raise _refresh_failed("refresh_expired")
    if parent.consumed_at is not None:
        return _replay_consumed_parent(parent, family, raw_refresh, now)

    expected_version = family.version
    child_generation = parent.generation + 1
    access_exp, deadline, trigger = _coverage(now, family.absolute_expires_at)
    try:
        provider_exp = family.cognito_access_expires_at
        if not isinstance(provider_exp, datetime):
            raise _failure(
                "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
                "provider_expiry_unavailable")
        if provider_exp <= trigger:
            provider_refresh = session_store.decrypt_token(
                family.cognito_refresh_token)
            tokens = cognito_service.refresh_tokens(
                provider_refresh, family.cognito_username)
            access_claims, provider_exp = _validate_refreshed_provider(
                tokens["access_token"], family.cognito_sub)
            if provider_exp <= deadline:
                _security_event(
                    "provider_coverage_insufficient", family.family_id,
                    "provider")
                raise _failure(
                    "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
                    "provider_coverage_insufficient")
            family.cognito_access_token = session_store.encrypt_token(
                tokens["access_token"])
            family.cognito_refresh_token = session_store.encrypt_token(
                tokens["refresh_token"])
            family.cognito_access_expires_at = provider_exp

        key_version = current_app.config[
            "MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION"]
        pair = mobile_credentials.derive_replacement_pair(
            raw_refresh, family.family_id, parent.generation,
            child_generation, key_version,
            current_app.config["MOBILE_AUTH_DERIVATION_KEYRING"])
        access = MobileAccessCredential(
            session_id=family.id,
            credential_hash=mobile_credentials.hash_credential(pair.access),
            generation=child_generation, issued_at=now, expires_at=access_exp)
        child = MobileRefreshCredential(
            session_id=family.id,
            credential_hash=mobile_credentials.hash_credential(pair.refresh),
            generation=child_generation, parent_id=parent.id,
            issued_at=now, expires_at=family.absolute_expires_at)
        db.session.add_all([access, child])
        db.session.flush()
        parent.consumed_at = now
        parent.grace_expires_at = now + timedelta(seconds=current_app.config[
            "MOBILE_AUTH_REFRESH_RETRY_GRACE_SECONDS"])
        parent.replacement_key_version = key_version
        parent.replacement_generation = child_generation
        parent.replacement_access_id = access.id
        parent.replacement_refresh_id = child.id
        parent.replacement_issued_at = now
        parent.replacement_access_expires_at = access_exp
        parent.replacement_refresh_expires_at = family.absolute_expires_at
        (MobileAccessCredential.query.filter(
            MobileAccessCredential.session_id == family.id,
            MobileAccessCredential.generation < child_generation,
            MobileAccessCredential.revoked_at.is_(None)).update(
                {MobileAccessCredential.revoked_at: now},
                synchronize_session=False))
        updated = (MobileAuthSession.query.filter_by(
            id=family.id, version=expected_version, revoked_at=None).update({
                MobileAuthSession.version: expected_version + 1,
                MobileAuthSession.last_used_at: now,
                MobileAuthSession.updated_at: now,
            }, synchronize_session=False))
        if updated != 1:
            raise _failure(
                "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
                "refresh_conflict")
        db.session.commit()
        return IssuedSession(
            pair.access, pair.refresh, access_exp, family.absolute_expires_at)
    except _DefinitiveRefreshFailure as exc:
        db.session.rollback()
        try:
            family = db.session.get(MobileAuthSession, family_id)
        except Exception as storage_exc:
            db.session.rollback()
            raise _failure(
                "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
                "storage_unavailable") from storage_exc
        _revoke_family_and_commit(
            family, "provider_refresh_rejected", now)
        _security_event(
            "provider_refresh_rejected", family.family_id, "provider")
        raise _refresh_failed(exc.args[0]) from exc
    except MobileAuthFailure:
        db.session.rollback()
        raise
    except cognito_service.CognitoServiceError as exc:
        db.session.rollback()
        if exc.code in {"NotAuthorizedException", "UserNotFoundException",
                        "RefreshFailed"}:
            try:
                family = db.session.get(MobileAuthSession, family_id)
            except Exception as storage_exc:
                db.session.rollback()
                raise _failure(
                    "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
                    "storage_unavailable") from storage_exc
            _revoke_family_and_commit(
                family, "provider_refresh_rejected", now)
            _security_event(
                "provider_refresh_rejected", family.family_id, "provider")
            raise _refresh_failed("provider_refresh_rejected") from exc
        _security_event("provider_renewal_failed", category="provider")
        raise _failure(
            "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
            "provider_renewal_failed") from exc
    except Exception as exc:
        db.session.rollback()
        raise _failure(
            "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
            "refresh_transaction_failed") from exc


def _family_id_for_credential(access_credential=None, refresh_credential=None):
    for raw, model in (
            (access_credential, MobileAccessCredential),
            (refresh_credential, MobileRefreshCredential)):
        if not raw:
            continue
        try:
            digest = mobile_credentials.hash_credential(raw)
        except mobile_credentials.InvalidMobileCredential:
            continue
        row = model.query.filter_by(credential_hash=digest).first()
        if row is not None and hmac.compare_digest(row.credential_hash, digest):
            return row.session_id
    return None


def prepare_logout(access_credential=None, refresh_credential=None, now=None):
    """Commit irreversible local revocation and return provider work in memory."""
    now = now or datetime.utcnow()
    try:
        family_id = _family_id_for_credential(
            access_credential, refresh_credential)
        if family_id is None:
            db.session.rollback()
            return LogoutResult()
        family = (MobileAuthSession.query.filter_by(id=family_id)
                  .with_for_update().one_or_none())
        if family is None or family.revoked_at is not None:
            db.session.rollback()
            return LogoutResult()
        provider_refresh = None
        if family.cognito_refresh_token:
            try:
                provider_refresh = session_store.decrypt_token(
                    family.cognito_refresh_token)
            except Exception:
                provider_refresh = None
        public_family_id = family.family_id
        _revoke_family_and_commit(family, "logout", now)
        return LogoutResult(public_family_id, provider_refresh)
    except MobileAuthFailure:
        raise
    except Exception as exc:
        db.session.rollback()
        raise _failure(
            "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
            "storage_unavailable") from exc


def best_effort_provider_revoke(result):
    if not result.provider_refresh_token:
        return
    try:
        cognito_service.revoke_token(result.provider_refresh_token)
    except Exception:
        _security_event(
            "provider_revoke_failed", result.family_id or "-", "provider")


def validate_derivation_key_readiness(now=None):
    """Fail closed while a replayable parent references a removed key version."""
    now = now or datetime.utcnow()
    cutoff = now - timedelta(seconds=current_app.config[
        "MOBILE_AUTH_DERIVATION_KEY_RETENTION_BUFFER_SECONDS"])
    referenced = {
        row[0] for row in db.session.query(
            MobileRefreshCredential.replacement_key_version).join(
                MobileAuthSession,
                MobileAuthSession.id == MobileRefreshCredential.session_id,
            ).filter(
                MobileAuthSession.revoked_at.is_(None),
                MobileRefreshCredential.consumed_at.is_not(None),
                MobileRefreshCredential.grace_expires_at >= cutoff,
                MobileRefreshCredential.replacement_key_version.is_not(None),
            ).distinct().all()
    }
    missing = referenced.difference(current_app.config[
        "MOBILE_AUTH_DERIVATION_KEYRING"])
    if missing:
        raise mobile_credentials.CredentialConfigurationError(
            "missing retained mobile derivation key version: "
            + ",".join(sorted(missing)))


def purge_expired(now=None):
    """Revoke due families without deleting refresh-hash tombstones."""
    now = now or datetime.utcnow()
    removed = 0
    while True:
        due_ids = [row[0] for row in db.session.query(MobileAuthSession.id).filter(
            MobileAuthSession.absolute_expires_at <= now,
            MobileAuthSession.revoked_at.is_(None)).limit(500).all()]
        if not due_ids:
            break
        for family_id in due_ids:
            family = (MobileAuthSession.query.filter_by(id=family_id)
                      .with_for_update().one_or_none())
            if family is None or family.revoked_at is not None:
                db.session.rollback()
                continue
            _revoke_family_and_commit(family, "absolute_expired", now)
            removed += 1
    return removed
