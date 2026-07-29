import calendar
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import MobileAccessCredential, MobileAuthSession, MobileRefreshCredential
from app.services import cognito_jwt, cognito_service, mobile_credentials


NOW = datetime(2026, 7, 29, 10, 0, 0)


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
