import calendar
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import MobileAccessCredential, MobileAuthSession, MobileRefreshCredential
from app.services import cognito_jwt, cognito_service, mobile_credentials


NOW = datetime(2026, 7, 29, 10, 0, 0)


def test_sensitive_result_dataclasses_hide_credentials_from_repr():
    from app.services import mobile_auth

    issued = mobile_auth.IssuedSession(
        "raw-access", "raw-refresh", NOW, NOW + timedelta(days=7))
    logout = mobile_auth.LogoutResult("public-family", "provider-refresh")
    assert "raw-access" not in repr(issued)
    assert "raw-refresh" not in repr(issued)
    assert "provider-refresh" not in repr(logout)


@pytest.fixture
def mobile_user(app, make_user):
    return make_user("mobile-service", cognito_sub="sub-mobile")


@pytest.fixture
def provider(monkeypatch):
    state = {
        "access_exp": NOW + timedelta(hours=1),
        "renewed_exp": NOW + timedelta(hours=2),
        "refresh_calls": 0,
    }
    monkeypatch.setattr(cognito_service, "authenticate", lambda username, password: {
        "tokens": {
            "access_token": "provider-access", "id_token": "provider-id",
            "refresh_token": "provider-refresh", "expires_in": 3600,
        },
        "claims": {"sub": "sub-mobile"},
    })

    def validate(token, expected_use, leeway_seconds=0):
        if expected_use == "id":
            return {"sub": "sub-mobile", "email": "mobile@example.com",
                    "email_verified": True}
        expiry = state["renewed_exp"] if token == "renewed-access" else state["access_exp"]
        return {"sub": "sub-mobile", "exp": calendar.timegm(expiry.timetuple())}

    def refresh(refresh_token, username):
        state["refresh_calls"] += 1
        return {"access_token": "renewed-access", "id_token": "",
                "refresh_token": refresh_token, "expires_in": 3600}

    monkeypatch.setattr(cognito_jwt, "validate_token", validate)
    monkeypatch.setattr(cognito_service, "refresh_tokens", refresh)
    return state


def test_login_persists_only_hashes_and_encrypted_provider_tokens(
        app, mobile_user, provider):
    from app.services import mobile_auth

    issued = mobile_auth.login("mobile-service", "correct", now=NOW)
    family = MobileAuthSession.query.one()
    assert family.user_id == mobile_user.id
    assert family.cognito_access_token != "provider-access"
    assert family.cognito_refresh_token != "provider-refresh"
    assert MobileAccessCredential.query.one().credential_hash == (
        mobile_credentials.hash_credential(issued.access_credential))
    assert MobileRefreshCredential.query.one().credential_hash == (
        mobile_credentials.hash_credential(issued.refresh_credential))


def test_login_renews_for_provider_coverage(app, mobile_user, provider):
    from app.services import mobile_auth

    provider["access_exp"] = NOW + timedelta(minutes=5)
    issued = mobile_auth.login("mobile-service", "correct", now=NOW)
    assert provider["refresh_calls"] == 1
    assert issued.access_expires_at == NOW + timedelta(minutes=15)


def test_login_insufficient_renewed_coverage_rolls_back(app, mobile_user, provider):
    from app.services import mobile_auth

    provider["access_exp"] = NOW + timedelta(minutes=5)
    provider["renewed_exp"] = NOW + timedelta(minutes=10)
    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.login("mobile-service", "correct", now=NOW)
    assert exc.value.code == "AUTH_TEMPORARILY_UNAVAILABLE"
    assert MobileAuthSession.query.count() == 0
    assert MobileAccessCredential.query.count() == 0
    assert MobileRefreshCredential.query.count() == 0


def test_login_treats_cold_jwks_failure_as_temporary(
        app, mobile_user, monkeypatch):
    from app.services import mobile_auth

    monkeypatch.setattr(
        cognito_service, "authenticate",
        lambda *args: (_ for _ in ()).throw(cognito_service.CognitoServiceError(
            "safe temporary identity failure", "JWKSUnavailable")))
    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.login("mobile-service", "correct", now=NOW)
    assert exc.value.code == "AUTH_TEMPORARILY_UNAVAILABLE"
    assert exc.value.retryable is True


def test_access_authentication_requires_all_ownership_links(
        app, mobile_user, provider):
    from app.services import mobile_auth

    issued = mobile_auth.login("mobile-service", "correct", now=NOW)
    principal = mobile_auth.authenticate_access(issued.access_credential, now=NOW)
    assert principal.user.id == mobile_user.id
    family = MobileAuthSession.query.one()
    family.cognito_sub = "different-sub"
    db.session.commit()
    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.authenticate_access(issued.access_credential, now=NOW)
    assert exc.value.code == "AUTH_SESSION_EXPIRED"


def test_expired_access_rejects_without_revoking_refresh_family(
        app, mobile_user, provider):
    from app.services import mobile_auth

    issued = mobile_auth.login("mobile-service", "correct", now=NOW)
    row = MobileAccessCredential.query.one()
    row.expires_at = NOW
    db.session.commit()
    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.authenticate_access(
            issued.access_credential, now=NOW + timedelta(seconds=1))
    assert exc.value.code == "AUTH_SESSION_EXPIRED"
    family = MobileAuthSession.query.one()
    assert family.revoked_at is None
    assert family.cognito_access_token is not None
    assert family.cognito_refresh_token is not None


def test_absolute_expired_family_revokes_and_clears_ciphertext_on_access(
        app, mobile_user, provider):
    from app.services import mobile_auth

    issued = mobile_auth.login("mobile-service", "correct", now=NOW)
    family = MobileAuthSession.query.one()
    family.absolute_expires_at = NOW
    db.session.commit()
    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.authenticate_access(
            issued.access_credential, now=NOW + timedelta(seconds=1))
    assert exc.value.code == "AUTH_SESSION_EXPIRED"
    family = MobileAuthSession.query.one()
    assert family.revoked_at == NOW + timedelta(seconds=1)
    assert family.cognito_access_token is None
    assert family.cognito_refresh_token is None


def test_first_refresh_creates_one_child_and_revokes_old_access(
        app, mobile_user, provider):
    from app.services import mobile_auth

    original = mobile_auth.login("mobile-service", "correct", now=NOW)
    rotated = mobile_auth.refresh(
        original.refresh_credential, now=NOW + timedelta(seconds=1))
    family = MobileAuthSession.query.one()
    assert family.version == 2
    assert MobileAccessCredential.query.filter_by(generation=1).count() == 1
    assert MobileRefreshCredential.query.filter_by(generation=1).count() == 1
    assert MobileRefreshCredential.query.filter_by(parent_id=1).count() == 1
    assert MobileAccessCredential.query.filter_by(generation=0).one().revoked_at is not None
    assert rotated.refresh_expires_at == family.absolute_expires_at


def test_grace_replay_returns_identical_pair_without_writes(
        app, mobile_user, provider):
    from app.services import mobile_auth

    original = mobile_auth.login("mobile-service", "correct", now=NOW)
    first = mobile_auth.refresh(
        original.refresh_credential, now=NOW + timedelta(seconds=1))
    replay = mobile_auth.refresh(
        original.refresh_credential, now=NOW + timedelta(seconds=3))
    assert replay == first
    assert MobileAuthSession.query.one().version == 2
    assert MobileAccessCredential.query.count() == 2
    assert MobileRefreshCredential.query.count() == 2


def test_grace_replay_never_reissues_credentials_after_family_revocation(
        app, mobile_user, provider):
    from app.services import mobile_auth

    original = mobile_auth.login("mobile-service", "correct", now=NOW)
    mobile_auth.refresh(
        original.refresh_credential, now=NOW + timedelta(seconds=1))
    family = MobileAuthSession.query.one()
    family.revoked_at = NOW + timedelta(seconds=2)
    db.session.commit()
    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.refresh(
            original.refresh_credential, now=NOW + timedelta(seconds=3))
    assert exc.value.code == "AUTH_REFRESH_FAILED"


def test_grace_replay_after_absolute_family_expiry_revokes_instead_of_reissuing(
        app, mobile_user, provider):
    from app.services import mobile_auth

    original = mobile_auth.login("mobile-service", "correct", now=NOW)
    family = MobileAuthSession.query.one()
    family.absolute_expires_at = NOW + timedelta(seconds=2)
    db.session.commit()
    mobile_auth.refresh(
        original.refresh_credential, now=NOW + timedelta(seconds=1))
    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.refresh(
            original.refresh_credential, now=NOW + timedelta(seconds=3))
    assert exc.value.code == "AUTH_REFRESH_FAILED"
    family = MobileAuthSession.query.one()
    assert family.revoked_at is not None
    assert family.cognito_access_token is None
    assert family.cognito_refresh_token is None


def test_verified_attacker_email_cannot_bind_same_named_legacy_local_account(
        app, make_user, monkeypatch):
    from app.services import mobile_auth

    victim = make_user("legacy-victim", cognito_sub=None)
    victim.email = "victim@example.com"
    db.session.commit()
    monkeypatch.setattr(cognito_service, "authenticate", lambda username, password: {
        "tokens": {
            "access_token": "attacker-access", "id_token": "attacker-id",
            "refresh_token": "attacker-refresh", "expires_in": 3600,
        }, "claims": {"sub": "attacker-sub"},
    })

    def validate(token, expected_use, leeway_seconds=0):
        if expected_use == "id":
            return {
                "sub": "attacker-sub", "email": "attacker@example.com",
                "email_verified": True,
            }
        return {
            "sub": "attacker-sub",
            "exp": calendar.timegm((NOW + timedelta(hours=1)).timetuple()),
        }

    monkeypatch.setattr(cognito_jwt, "validate_token", validate)
    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.login("legacy-victim", "correct", now=NOW)
    assert exc.value.code == "AUTH_INVALID_CREDENTIALS"
    db.session.refresh(victim)
    assert victim.cognito_sub is None
    assert MobileAuthSession.query.count() == 0


def test_post_grace_reuse_revokes_only_affected_family(
        app, mobile_user, provider):
    from app.services import mobile_auth

    first_family = mobile_auth.login("mobile-service", "correct", now=NOW)
    mobile_auth.login("mobile-service", "correct", now=NOW)
    mobile_auth.refresh(
        first_family.refresh_credential, now=NOW + timedelta(seconds=1))
    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.refresh(
            first_family.refresh_credential, now=NOW + timedelta(seconds=12))
    assert exc.value.code == "AUTH_REFRESH_FAILED"
    families = MobileAuthSession.query.order_by(MobileAuthSession.id).all()
    assert families[0].revoked_at is not None
    assert families[0].cognito_access_token is None
    assert families[1].revoked_at is None


def test_definitive_provider_refresh_rejection_revokes_family(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    original = mobile_auth.login("mobile-service", "correct", now=NOW)
    monkeypatch.setattr(
        cognito_service, "refresh_tokens",
        lambda *args: (_ for _ in ()).throw(cognito_service.CognitoServiceError(
            "raw provider rejection", "NotAuthorizedException")))
    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.refresh(
            original.refresh_credential, now=NOW + timedelta(minutes=50))
    assert exc.value.code == "AUTH_REFRESH_FAILED"
    family = MobileAuthSession.query.one()
    assert family.revoked_at is not None
    assert family.cognito_access_token is None
    assert family.cognito_refresh_token is None


def test_temporary_provider_refresh_failure_rolls_back_without_revocation(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    original = mobile_auth.login("mobile-service", "correct", now=NOW)
    monkeypatch.setattr(
        cognito_service, "refresh_tokens",
        lambda *args: (_ for _ in ()).throw(cognito_service.CognitoServiceError(
            "raw temporary provider failure", "TooManyRequestsException")))
    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.refresh(
            original.refresh_credential, now=NOW + timedelta(minutes=50))
    assert exc.value.code == "AUTH_TEMPORARILY_UNAVAILABLE"
    family = MobileAuthSession.query.one()
    assert family.revoked_at is None
    assert family.version == 1
    assert MobileRefreshCredential.query.one().consumed_at is None
    assert MobileRefreshCredential.query.count() == 1


def test_too_short_renewed_provider_token_rolls_back_every_mutation(
        app, mobile_user, provider):
    from app.services import mobile_auth

    original = mobile_auth.login("mobile-service", "correct", now=NOW)
    provider["renewed_exp"] = NOW + timedelta(minutes=60)
    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.refresh(
            original.refresh_credential, now=NOW + timedelta(minutes=50))
    assert exc.value.code == "AUTH_TEMPORARILY_UNAVAILABLE"
    family = MobileAuthSession.query.one()
    assert family.revoked_at is None
    assert family.version == 1
    assert MobileAccessCredential.query.count() == 1
    parent = MobileRefreshCredential.query.one()
    assert parent.consumed_at is None
    assert MobileRefreshCredential.query.count() == 1


def test_refresh_after_stored_provider_access_expiry_renews_instead_of_relogin(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    original = mobile_auth.login("mobile-service", "correct", now=NOW)
    provider["renewed_exp"] = NOW + timedelta(hours=4)
    previous_validate = cognito_jwt.validate_token

    def reject_expired_stored_access(token, expected_use, leeway_seconds=0):
        if token == "provider-access":
            raise cognito_jwt.TokenValidationError("expired")
        return previous_validate(token, expected_use, leeway_seconds)

    monkeypatch.setattr(
        cognito_jwt, "validate_token", reject_expired_stored_access)
    issued = mobile_auth.refresh(
        original.refresh_credential, now=NOW + timedelta(hours=2))
    assert provider["refresh_calls"] == 1
    assert issued.access_expires_at == NOW + timedelta(hours=2, minutes=15)
    assert MobileAuthSession.query.one().version == 2


def test_refresh_storage_failure_is_typed_retryable(app, monkeypatch):
    from app.services import mobile_auth

    raw = mobile_credentials.generate_credential()
    monkeypatch.setattr(
        db.session, "query",
        lambda *args: (_ for _ in ()).throw(RuntimeError("database unavailable")))
    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.refresh(raw, now=NOW)
    assert exc.value.code == "AUTH_TEMPORARILY_UNAVAILABLE"
    assert exc.value.retryable is True


def test_derivation_key_readiness_rejects_missing_replay_version_until_buffer(
        app, mobile_user, provider):
    from app.services import mobile_auth

    original = mobile_auth.login("mobile-service", "correct", now=NOW)
    mobile_auth.refresh(
        original.refresh_credential, now=NOW + timedelta(seconds=1))
    app.config["MOBILE_AUTH_DERIVATION_KEYRING"] = {"next-v1": b"n" * 32}
    app.config["MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION"] = "next-v1"
    with pytest.raises(mobile_credentials.CredentialConfigurationError):
        mobile_auth.validate_derivation_key_readiness(
            now=NOW + timedelta(seconds=3))
    mobile_auth.validate_derivation_key_readiness(
        now=NOW + timedelta(seconds=312))


def test_access_corrupt_provider_ciphertext_revokes_with_typed_error(
        app, mobile_user, provider):
    from app.services import mobile_auth

    issued = mobile_auth.login("mobile-service", "correct", now=NOW)
    family = MobileAuthSession.query.one()
    family.cognito_access_token = "not-fernet-ciphertext"
    db.session.commit()
    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.authenticate_access(issued.access_credential, now=NOW)
    assert exc.value.code == "AUTH_SESSION_EXPIRED"
    assert MobileAuthSession.query.one().revoked_at is not None


def test_access_storage_failure_is_typed_retryable(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    issued = mobile_auth.login("mobile-service", "correct", now=NOW)
    monkeypatch.setattr(
        db.session, "get",
        lambda *args: (_ for _ in ()).throw(RuntimeError("database unavailable")))
    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.authenticate_access(issued.access_credential, now=NOW)
    assert exc.value.code == "AUTH_TEMPORARILY_UNAVAILABLE"
    assert exc.value.retryable is True


def test_absolute_expiry_revoke_commit_failure_is_typed_retryable(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    issued = mobile_auth.login("mobile-service", "correct", now=NOW)
    family = MobileAuthSession.query.one()
    family.absolute_expires_at = NOW
    db.session.commit()
    monkeypatch.setattr(
        db.session, "commit",
        lambda: (_ for _ in ()).throw(RuntimeError("database unavailable")))

    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.authenticate_access(issued.access_credential, now=NOW)
    assert exc.value.code == "AUTH_TEMPORARILY_UNAVAILABLE"
    assert exc.value.retryable is True


def test_expiry_cleanup_is_idempotent_and_clears_ciphertext(
        app, mobile_user, provider):
    from app.services import mobile_auth

    mobile_auth.login("mobile-service", "correct", now=NOW)
    family = MobileAuthSession.query.one()
    family.absolute_expires_at = NOW
    db.session.commit()
    assert mobile_auth.purge_expired(now=NOW + timedelta(seconds=1)) == 1
    assert mobile_auth.purge_expired(now=NOW + timedelta(seconds=2)) == 0
    family = MobileAuthSession.query.one()
    assert family.revoked_at is not None
    assert family.cognito_access_token is None
    assert family.cognito_refresh_token is None
