"""Transactional opaque mobile-session lifecycle."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import hmac

from flask import current_app

from app.extensions import db
from app.models import (
    MobileAccessCredential, MobileAuthSession, MobileRefreshCredential, User,
)
from app.services import cognito_jwt, cognito_service, mobile_credentials, session_store


@dataclass(frozen=True)
class IssuedSession:
    access_credential: str
    refresh_credential: str
    access_expires_at: datetime
    refresh_expires_at: datetime


@dataclass(frozen=True)
class MobilePrincipal:
    user: User
    family: MobileAuthSession
    claims: dict


class MobileAuthFailure(Exception):
    def __init__(self, code, status, retryable, reason):
        super().__init__(reason)
        self.code = code
        self.status = status
        self.retryable = retryable
        self.reason = reason


def _failure(code, status, retryable, reason):
    return MobileAuthFailure(code, status, retryable, reason)


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
    user = (User.query.filter_by(username=username).first()
            or User.query.filter(db.func.lower(User.email) == email).first())
    if user is not None:
        if user.cognito_sub and user.cognito_sub != sub:
            return None
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
            raise _failure(
                "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
                "jwks_unavailable") from exc
        raise _failure(
            "AUTH_INVALID_CREDENTIALS", 401, False,
            "provider_token_invalid") from exc


def login(username, password, now=None):
    now = now or datetime.utcnow()
    try:
        result = cognito_service.authenticate(username, password)
        tokens = dict(result["tokens"])
    except cognito_service.CognitoServiceError as exc:
        if exc.code == "UserNotConfirmedException":
            raise _failure(
                "AUTH_VERIFICATION_REQUIRED", 403, False,
                "verification_required") from exc
        if exc.code in {"TooManyRequestsException", "InternalErrorException",
                        "LimitExceededException", "ServiceUnavailableException", ""}:
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

    try:
        id_claims = _validate_provider(tokens.get("id_token", ""), "id")
        access_claims = _validate_provider(tokens.get("access_token", ""), "access")
        if not id_claims.get("sub") or id_claims.get("sub") != access_claims.get("sub"):
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
            access_claims = _validate_provider(tokens.get("access_token", ""), "access")
            if access_claims.get("sub") != id_claims.get("sub"):
                raise _failure(
                    "AUTH_INVALID_CREDENTIALS", 401, False,
                    "provider_subject_mismatch")
            provider_exp = _claim_expiry(access_claims)
        if provider_exp <= deadline:
            raise _failure(
                "AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
                "provider_coverage_insufficient")

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


def authenticate_access(raw_access, now=None):
    now = now or datetime.utcnow()
    try:
        digest = mobile_credentials.hash_credential(raw_access)
    except mobile_credentials.InvalidMobileCredential as exc:
        raise _failure(
            "AUTH_SESSION_EXPIRED", 401, False, "access_invalid") from exc
    row = MobileAccessCredential.query.filter_by(credential_hash=digest).first()
    if row is None or not hmac.compare_digest(row.credential_hash, digest):
        raise _failure("AUTH_SESSION_EXPIRED", 401, False, "access_invalid")
    family = db.session.get(MobileAuthSession, row.session_id)
    if (family is None or row.revoked_at is not None or family.revoked_at is not None
            or row.expires_at <= now or family.absolute_expires_at <= now):
        raise _failure("AUTH_SESSION_EXPIRED", 401, False, "access_expired")
    user = db.session.get(User, family.user_id)
    try:
        claims = _validate_provider(
            session_store.decrypt_token(family.cognito_access_token), "access")
    except MobileAuthFailure as exc:
        if exc.code == "AUTH_TEMPORARILY_UNAVAILABLE":
            raise
        _revoke_family(family, "provider_validation_failed", now)
        db.session.commit()
        raise _failure(
            "AUTH_SESSION_EXPIRED", 401, False,
            "provider_validation_failed") from exc
    sub = claims.get("sub")
    if (user is None or not family.cognito_sub or sub != family.cognito_sub
            or user.cognito_sub != family.cognito_sub):
        _revoke_family(family, "ownership_mismatch", now)
        db.session.commit()
        raise _failure(
            "AUTH_SESSION_EXPIRED", 401, False, "ownership_mismatch")
    return MobilePrincipal(user, family, claims)
