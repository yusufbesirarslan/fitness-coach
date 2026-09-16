import calendar
from datetime import datetime, timedelta, timezone
import threading

import pytest

from app.extensions import db
from app.models import (
    MobileAccessCredential, MobileAuthSession, MobileRefreshCredential, User,
)
from app.services import ai_gate, cognito_jwt, cognito_service, mobile_credentials


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


def test_login_hides_unconfirmed_account_and_logs_only_safe_event(
        app, monkeypatch, caplog):
    from app.services import mobile_auth

    monkeypatch.setattr(
        cognito_service, "authenticate",
        lambda *args: (_ for _ in ()).throw(cognito_service.CognitoServiceError(
            "raw cognito detail", "UserNotConfirmedException")))

    with pytest.raises(mobile_auth.MobileAuthFailure) as caught:
        mobile_auth.login(
            "unconfirmed@example.test", "do-not-log-password", now=NOW)

    assert (caught.value.code, caught.value.status, caught.value.retryable) == (
        "AUTH_INVALID_CREDENTIALS", 401, False)
    assert "do-not-log-password" not in caplog.text
    assert "unconfirmed@example.test" not in caplog.text
    assert "raw cognito detail" not in caplog.text
    assert "unconfirmed_account" in caplog.text


def test_login_rejects_saturation_before_any_provider_work(
        app, monkeypatch):
    from app.services import mobile_auth

    semaphore = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_ai_slots", semaphore)
    calls = {"authenticate": 0, "refresh": 0}

    def unexpected_authenticate(*args):
        calls["authenticate"] += 1
        raise AssertionError("authenticate must not run")

    def unexpected_refresh(*args):
        calls["refresh"] += 1
        raise AssertionError("refresh must not run")

    monkeypatch.setattr(cognito_service, "authenticate", unexpected_authenticate)
    monkeypatch.setattr(cognito_service, "refresh_tokens", unexpected_refresh)
    assert semaphore.acquire(blocking=False)
    try:
        with pytest.raises(mobile_auth.MobileAuthFailure) as caught:
            mobile_auth.login("mobile-service", "correct", now=NOW)
    finally:
        semaphore.release()

    assert calls == {"authenticate": 0, "refresh": 0}
    assert (
        caught.value.code,
        caught.value.status,
        caught.value.retryable,
        caught.value.retry_after,
    ) == ("AUTH_TEMPORARILY_UNAVAILABLE", 503, True, 15)


def test_login_gate_covers_provider_work_but_not_local_identity(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    provider["access_exp"] = NOW + timedelta(minutes=5)
    semaphore = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_ai_slots", semaphore)
    original_authenticate = cognito_service.authenticate
    original_validate = cognito_jwt.validate_token
    original_refresh = cognito_service.refresh_tokens
    original_resolve_user = mobile_auth._resolve_user

    def assert_gate_held():
        acquired = semaphore.acquire(blocking=False)
        if acquired:
            semaphore.release()
        assert acquired is False

    def authenticate(*args):
        assert_gate_held()
        return original_authenticate(*args)

    def validate(*args, **kwargs):
        assert_gate_held()
        return original_validate(*args, **kwargs)

    def refresh(*args):
        assert_gate_held()
        return original_refresh(*args)

    def resolve_user(*args):
        assert semaphore.acquire(blocking=False)
        semaphore.release()
        return original_resolve_user(*args)

    monkeypatch.setattr(cognito_service, "authenticate", authenticate)
    monkeypatch.setattr(cognito_jwt, "validate_token", validate)
    monkeypatch.setattr(cognito_service, "refresh_tokens", refresh)
    monkeypatch.setattr(mobile_auth, "_resolve_user", resolve_user)

    mobile_auth.login("mobile-service", "correct", now=NOW)


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


def test_refresh_provider_renewal_runs_without_database_transaction(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    original = mobile_auth.login("mobile-service", "correct", now=NOW)
    provider_refresh = cognito_service.refresh_tokens

    def renew_without_database_transaction(*args):
        assert db.session().in_transaction() is False
        return provider_refresh(*args)

    monkeypatch.setattr(
        cognito_service, "refresh_tokens", renew_without_database_transaction)

    rotated = mobile_auth.refresh(
        original.refresh_credential, now=NOW + timedelta(minutes=50))

    assert rotated.access_expires_at == NOW + timedelta(minutes=65)
    assert MobileAuthSession.query.one().version == 2


def test_refresh_provider_saturation_is_retryable_without_provider_work(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    original = mobile_auth.login("mobile-service", "correct", now=NOW)
    semaphore = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_ai_slots", semaphore)
    calls = {"refresh": 0}

    def unexpected_refresh(*args):
        calls["refresh"] += 1
        raise AssertionError("refresh must not run")

    monkeypatch.setattr(cognito_service, "refresh_tokens", unexpected_refresh)
    assert semaphore.acquire(blocking=False)
    try:
        with pytest.raises(mobile_auth.MobileAuthFailure) as caught:
            mobile_auth.refresh(
                original.refresh_credential,
                now=NOW + timedelta(minutes=50),
            )
    finally:
        semaphore.release()

    assert calls == {"refresh": 0}
    assert (
        caught.value.code,
        caught.value.status,
        caught.value.retryable,
        caught.value.retry_after,
    ) == ("AUTH_TEMPORARILY_UNAVAILABLE", 503, True, 15)
    family = MobileAuthSession.query.one()
    assert family.version == 1
    assert family.revoked_at is None
    assert MobileRefreshCredential.query.one().consumed_at is None


def _run_same_version_refresh_race(
        app, raw_refresh, monkeypatch, *, reject_stale_provider_token=False,
        phase_times=None):
    from app.services import mobile_auth

    monkeypatch.setattr(
        ai_gate, "_ai_slots", threading.BoundedSemaphore(2))
    provider_barrier = threading.Barrier(2)
    winner_at_provider = threading.Event()
    winner_finished = threading.Event()
    thread_state = threading.local()
    provider_calls = []
    previous_validate = cognito_jwt.validate_token

    if phase_times is not None:
        real_datetime = datetime

        class RaceDatetimeMeta(type):
            def __instancecheck__(cls, instance):
                return isinstance(instance, real_datetime)

        class RaceDatetime(metaclass=RaceDatetimeMeta):
            @classmethod
            def utcnow(cls):
                return next(thread_state.phase_times)

            @classmethod
            def utcfromtimestamp(cls, value):
                return real_datetime.fromtimestamp(
                    value, timezone.utc).replace(tzinfo=None)

        monkeypatch.setattr(mobile_auth, "datetime", RaceDatetime)

    def renew(_provider_refresh, _username):
        contender = thread_state.contender
        provider_calls.append(contender)
        if contender == "winner":
            winner_at_provider.set()
        provider_barrier.wait(timeout=10)
        if contender == "stale":
            assert winner_finished.wait(timeout=10)
        return {
            "access_token": f"race-provider-access-{contender}",
            "id_token": "",
            "refresh_token": f"race-provider-refresh-{contender}",
            "expires_in": 7200,
        }

    def validate(token, expected_use, leeway_seconds=0):
        if token.startswith("race-provider-access-"):
            contender = token.rsplit("-", 1)[-1]
            subject = (
                "different-sub"
                if reject_stale_provider_token and contender == "stale"
                else "sub-mobile"
            )
            return {
                "sub": subject,
                "exp": calendar.timegm(
                    (NOW + timedelta(hours=3)).timetuple()),
            }
        return previous_validate(token, expected_use, leeway_seconds)

    monkeypatch.setattr(cognito_service, "refresh_tokens", renew)
    monkeypatch.setattr(cognito_jwt, "validate_token", validate)
    outcomes = {}

    def rotate(contender):
        with app.app_context():
            thread_state.contender = contender
            if phase_times is not None:
                thread_state.phase_times = iter(phase_times[contender])
            try:
                outcomes[contender] = (
                    "issued",
                    mobile_auth.refresh(
                        raw_refresh,
                        now=(
                            None if phase_times is not None
                            else NOW + timedelta(minutes=50)),
                    ),
                )
            except mobile_auth.MobileAuthFailure as exc:
                outcomes[contender] = (
                    "failed", exc.code, exc.reason, exc.retryable)
            except Exception as exc:  # pragma: no cover - surfaced by assertions
                outcomes[contender] = (
                    "unexpected", type(exc).__name__, str(exc))
            finally:
                if contender == "winner":
                    winner_finished.set()
                db.session.remove()

    db.session.remove()
    threads = {
        contender: threading.Thread(
            target=rotate, args=(contender,), daemon=True)
        for contender in ("winner", "stale")
    }
    threads["winner"].start()
    assert winner_at_provider.wait(timeout=10), outcomes
    threads["stale"].start()
    for thread in threads.values():
        thread.join(timeout=15)

    assert not any(thread.is_alive() for thread in threads.values()), outcomes
    assert sorted(provider_calls) == ["stale", "winner"], outcomes
    return outcomes


def test_same_version_refresh_race_keeps_winner_tokens_and_one_child(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth, session_store

    original = mobile_auth.login("mobile-service", "correct", now=NOW)

    outcomes = _run_same_version_refresh_race(
        app, original.refresh_credential, monkeypatch)

    assert outcomes["winner"][0] == "issued", outcomes
    if outcomes["stale"][0] == "issued":
        assert outcomes["stale"][1] == outcomes["winner"][1]
    else:
        assert outcomes["stale"] == (
            "failed",
            "AUTH_TEMPORARILY_UNAVAILABLE",
            "refresh_conflict",
            True,
        )

    family = MobileAuthSession.query.one()
    access_rows = MobileAccessCredential.query.order_by(
        MobileAccessCredential.generation).all()
    refresh_rows = MobileRefreshCredential.query.order_by(
        MobileRefreshCredential.generation).all()
    parent, child = refresh_rows
    assert family.version == 2
    assert family.revoked_at is None
    assert [row.generation for row in access_rows] == [0, 1]
    assert [row.generation for row in refresh_rows] == [0, 1]
    assert child.parent_id == parent.id
    assert parent.replacement_access_id == access_rows[1].id
    assert parent.replacement_refresh_id == child.id
    assert session_store.decrypt_token(
        family.cognito_access_token) == "race-provider-access-winner"
    assert session_store.decrypt_token(
        family.cognito_refresh_token) == "race-provider-refresh-winner"


def test_stale_provider_rejection_does_not_revoke_refresh_race_winner(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth, session_store

    original = mobile_auth.login("mobile-service", "correct", now=NOW)

    outcomes = _run_same_version_refresh_race(
        app,
        original.refresh_credential,
        monkeypatch,
        reject_stale_provider_token=True,
    )

    assert outcomes["winner"][0] == "issued", outcomes
    assert outcomes["stale"] == outcomes["winner"]
    family = MobileAuthSession.query.one()
    assert family.version == 2
    assert family.revoked_at is None
    assert family.revoked_reason is None
    assert MobileAccessCredential.query.filter_by(generation=1).count() == 1
    assert MobileRefreshCredential.query.filter_by(generation=1).count() == 1
    assert session_store.decrypt_token(
        family.cognito_access_token) == "race-provider-access-winner"
    assert session_store.decrypt_token(
        family.cognito_refresh_token) == "race-provider-refresh-winner"


def test_post_grace_stale_success_conflicts_without_revoking_race_winner(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth, session_store

    original = mobile_auth.login("mobile-service", "correct", now=NOW)
    winner_phase_one = NOW + timedelta(minutes=50)
    stale_phase_one = winner_phase_one + timedelta(seconds=11)
    winner_phase_two = winner_phase_one + timedelta(seconds=12)
    stale_phase_two = winner_phase_one + timedelta(seconds=23)

    outcomes = _run_same_version_refresh_race(
        app,
        original.refresh_credential,
        monkeypatch,
        phase_times={
            "winner": (winner_phase_one, winner_phase_two),
            "stale": (stale_phase_one, stale_phase_two),
        },
    )

    family = MobileAuthSession.query.one()
    parent = MobileRefreshCredential.query.filter_by(generation=0).one()
    assert outcomes["winner"][0] == "issued", outcomes
    assert outcomes["stale"] == (
        "failed",
        "AUTH_TEMPORARILY_UNAVAILABLE",
        "refresh_conflict",
        True,
    )
    assert parent.consumed_at == winner_phase_two
    assert parent.grace_expires_at == winner_phase_two + timedelta(seconds=10)
    assert family.version == 2
    assert family.revoked_at is None
    assert MobileAccessCredential.query.filter_by(generation=1).count() == 1
    assert MobileRefreshCredential.query.filter_by(generation=1).count() == 1
    assert session_store.decrypt_token(
        family.cognito_access_token) == "race-provider-access-winner"
    assert session_store.decrypt_token(
        family.cognito_refresh_token) == "race-provider-refresh-winner"


def test_post_grace_stale_provider_rejection_does_not_revoke_race_winner(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth, session_store

    original = mobile_auth.login("mobile-service", "correct", now=NOW)
    winner_phase_one = NOW + timedelta(minutes=50)
    stale_phase_one = winner_phase_one + timedelta(seconds=11)
    winner_phase_two = winner_phase_one + timedelta(seconds=12)
    stale_phase_two = winner_phase_one + timedelta(seconds=23)

    outcomes = _run_same_version_refresh_race(
        app,
        original.refresh_credential,
        monkeypatch,
        reject_stale_provider_token=True,
        phase_times={
            "winner": (winner_phase_one, winner_phase_two),
            "stale": (stale_phase_one, stale_phase_two),
        },
    )

    family = MobileAuthSession.query.one()
    parent = MobileRefreshCredential.query.filter_by(generation=0).one()
    assert outcomes["winner"][0] == "issued", outcomes
    assert outcomes["stale"] == (
        "failed",
        "AUTH_TEMPORARILY_UNAVAILABLE",
        "refresh_conflict",
        True,
    )
    assert parent.consumed_at == winner_phase_two
    assert parent.grace_expires_at == winner_phase_two + timedelta(seconds=10)
    assert family.version == 2
    assert family.revoked_at is None
    assert family.revoked_reason is None
    assert MobileAccessCredential.query.filter_by(generation=1).count() == 1
    assert MobileRefreshCredential.query.filter_by(generation=1).count() == 1
    assert session_store.decrypt_token(
        family.cognito_access_token) == "race-provider-access-winner"
    assert session_store.decrypt_token(
        family.cognito_refresh_token) == "race-provider-refresh-winner"


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


@pytest.mark.parametrize("reason", [
    "invalid_signature",
    "malformed",
    "wrong_issuer",
    "wrong_audience",
    "wrong_use",
    "expired",
])
def test_definitive_refreshed_token_validation_failure_revokes_family(
        app, mobile_user, provider, monkeypatch, reason):
    from app.services import mobile_auth

    original = mobile_auth.login("mobile-service", "correct", now=NOW)
    previous_validate = cognito_jwt.validate_token

    def reject_refreshed_access(token, expected_use, leeway_seconds=0):
        if token == "renewed-access":
            raise cognito_jwt.TokenValidationError(reason)
        return previous_validate(token, expected_use, leeway_seconds)

    monkeypatch.setattr(cognito_jwt, "validate_token", reject_refreshed_access)
    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.refresh(
            original.refresh_credential, now=NOW + timedelta(minutes=50))

    assert exc.value.code == "AUTH_REFRESH_FAILED"
    assert exc.value.retryable is False
    family = MobileAuthSession.query.one()
    assert family.revoked_at is not None
    assert family.cognito_access_token is None
    assert family.cognito_refresh_token is None
    assert all(row.revoked_at is not None for row in MobileAccessCredential.query.all())
    assert all(row.revoked_at is not None for row in MobileRefreshCredential.query.all())


def test_refreshed_token_missing_subject_revokes_family(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    original = mobile_auth.login("mobile-service", "correct", now=NOW)
    previous_validate = cognito_jwt.validate_token

    def omit_refreshed_subject(token, expected_use, leeway_seconds=0):
        if token == "renewed-access":
            return {"exp": calendar.timegm((NOW + timedelta(hours=2)).timetuple())}
        return previous_validate(token, expected_use, leeway_seconds)

    monkeypatch.setattr(cognito_jwt, "validate_token", omit_refreshed_subject)
    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.refresh(
            original.refresh_credential, now=NOW + timedelta(minutes=50))

    assert exc.value.code == "AUTH_REFRESH_FAILED"
    assert MobileAuthSession.query.one().revoked_at is not None


def test_refreshed_token_subject_mismatch_revokes_family(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    original = mobile_auth.login("mobile-service", "correct", now=NOW)
    previous_validate = cognito_jwt.validate_token

    def mismatch_refreshed_subject(token, expected_use, leeway_seconds=0):
        claims = previous_validate(token, expected_use, leeway_seconds)
        if token == "renewed-access":
            claims["sub"] = "different-sub"
        return claims

    monkeypatch.setattr(cognito_jwt, "validate_token", mismatch_refreshed_subject)
    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.refresh(
            original.refresh_credential, now=NOW + timedelta(minutes=50))

    assert exc.value.code == "AUTH_REFRESH_FAILED"
    assert MobileAuthSession.query.one().revoked_at is not None


def test_refreshed_jwks_unavailability_preserves_family(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    original = mobile_auth.login("mobile-service", "correct", now=NOW)
    previous_validate = cognito_jwt.validate_token

    def reject_refreshed_jwks(token, expected_use, leeway_seconds=0):
        if token == "renewed-access":
            raise cognito_jwt.TokenValidationError("jwks_unavailable")
        return previous_validate(token, expected_use, leeway_seconds)

    monkeypatch.setattr(cognito_jwt, "validate_token", reject_refreshed_jwks)
    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.refresh(
            original.refresh_credential, now=NOW + timedelta(minutes=50))

    assert exc.value.code == "AUTH_TEMPORARILY_UNAVAILABLE"
    assert exc.value.retryable is True
    family = MobileAuthSession.query.one()
    assert family.revoked_at is None
    assert family.cognito_access_token is not None
    assert family.cognito_refresh_token is not None
    assert MobileAccessCredential.query.one().revoked_at is None
    assert MobileRefreshCredential.query.one().revoked_at is None


def test_definitive_refreshed_token_revoke_commit_failure_is_retryable(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    original = mobile_auth.login("mobile-service", "correct", now=NOW)
    previous_validate = cognito_jwt.validate_token

    def reject_refreshed_access(token, expected_use, leeway_seconds=0):
        if token == "renewed-access":
            raise cognito_jwt.TokenValidationError("invalid_signature")
        return previous_validate(token, expected_use, leeway_seconds)

    monkeypatch.setattr(cognito_jwt, "validate_token", reject_refreshed_access)
    monkeypatch.setattr(
        db.session, "commit",
        lambda: (_ for _ in ()).throw(RuntimeError("database unavailable")))

    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.refresh(
            original.refresh_credential, now=NOW + timedelta(minutes=50))

    assert exc.value.code == "AUTH_TEMPORARILY_UNAVAILABLE"
    assert exc.value.retryable is True


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


def _fail_revocation_commit(monkeypatch):
    monkeypatch.setattr(
        db.session, "commit",
        lambda: (_ for _ in ()).throw(RuntimeError("database unavailable")))


def _fail_commit_from_call(monkeypatch, first_failing_call):
    """Break commit only from the Nth call on.

    `revoke_all_for_user` commits the credential fence before it touches a
    single family, so a globally broken commit never reaches the family sweep
    at all. Counting keeps "the sweep could not be persisted" tests about the
    sweep.
    """
    real_commit = db.session.commit
    calls = {"n": 0}

    def commit():
        calls["n"] += 1
        if calls["n"] >= first_failing_call:
            raise RuntimeError("database unavailable")
        return real_commit()

    monkeypatch.setattr(db.session, "commit", commit)
    return calls


def _assert_retryable_storage_failure(exc):
    assert exc.value.code == "AUTH_TEMPORARILY_UNAVAILABLE"
    assert exc.value.status == 503
    assert exc.value.retryable is True


def test_access_provider_validation_revoke_commit_failure_is_typed_retryable(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    issued = mobile_auth.login("mobile-service", "correct", now=NOW)
    monkeypatch.setattr(
        cognito_jwt, "validate_token",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            cognito_jwt.TokenValidationError("invalid_signature")))
    _fail_revocation_commit(monkeypatch)

    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.authenticate_access(issued.access_credential, now=NOW)

    _assert_retryable_storage_failure(exc)


def test_ownership_mismatch_revoke_commit_failure_is_typed_retryable(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    issued = mobile_auth.login("mobile-service", "correct", now=NOW)
    family = MobileAuthSession.query.one()
    family.cognito_sub = "different-sub"
    db.session.commit()
    _fail_revocation_commit(monkeypatch)

    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.authenticate_access(issued.access_credential, now=NOW)

    _assert_retryable_storage_failure(exc)


def test_refresh_expiry_revoke_commit_failure_is_typed_retryable(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    issued = mobile_auth.login("mobile-service", "correct", now=NOW)
    family = MobileAuthSession.query.one()
    family.absolute_expires_at = NOW
    db.session.commit()
    _fail_revocation_commit(monkeypatch)

    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.refresh(issued.refresh_credential, now=NOW)

    _assert_retryable_storage_failure(exc)


def test_post_grace_reuse_revoke_commit_failure_is_typed_retryable(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    original = mobile_auth.login("mobile-service", "correct", now=NOW)
    mobile_auth.refresh(
        original.refresh_credential, now=NOW + timedelta(seconds=1))
    _fail_revocation_commit(monkeypatch)

    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.refresh(
            original.refresh_credential, now=NOW + timedelta(seconds=12))

    _assert_retryable_storage_failure(exc)


def test_definitive_cognito_rejection_revoke_commit_failure_is_typed_retryable(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    original = mobile_auth.login("mobile-service", "correct", now=NOW)
    monkeypatch.setattr(
        cognito_service, "refresh_tokens",
        lambda *args: (_ for _ in ()).throw(cognito_service.CognitoServiceError(
            "raw provider rejection", "NotAuthorizedException")))
    _fail_revocation_commit(monkeypatch)

    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.refresh(
            original.refresh_credential, now=NOW + timedelta(minutes=50))

    _assert_retryable_storage_failure(exc)


def test_definitive_cognito_rejection_reload_failure_is_typed_retryable(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    original = mobile_auth.login("mobile-service", "correct", now=NOW)
    monkeypatch.setattr(
        cognito_service, "refresh_tokens",
        lambda *args: (_ for _ in ()).throw(cognito_service.CognitoServiceError(
            "raw provider rejection", "NotAuthorizedException")))
    monkeypatch.setattr(
        db.session, "get",
        lambda *args: (_ for _ in ()).throw(RuntimeError("database unavailable")))

    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.refresh(
            original.refresh_credential, now=NOW + timedelta(minutes=50))

    _assert_retryable_storage_failure(exc)


def test_logout_revoke_commit_failure_is_typed_retryable(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    issued = mobile_auth.login("mobile-service", "correct", now=NOW)
    _fail_revocation_commit(monkeypatch)

    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.prepare_logout(access_credential=issued.access_credential, now=NOW)

    _assert_retryable_storage_failure(exc)


def test_cleanup_revoke_commit_failure_is_typed_retryable(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    mobile_auth.login("mobile-service", "correct", now=NOW)
    family = MobileAuthSession.query.one()
    family.absolute_expires_at = NOW
    db.session.commit()
    _fail_revocation_commit(monkeypatch)

    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.purge_expired(now=NOW + timedelta(seconds=1))

    _assert_retryable_storage_failure(exc)


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


def _other_user_family(user, *, family_id="other-family", revoked=False):
    """A bare family row for a DIFFERENT user — enough to prove isolation."""
    from app.services import session_store

    family = MobileAuthSession(
        family_id=family_id,
        user_id=user.id,
        cognito_username=user.username,
        cognito_sub=user.cognito_sub,
        cognito_access_token=session_store.encrypt_token("other-access"),
        cognito_refresh_token=session_store.encrypt_token("other-refresh"),
        cognito_access_expires_at=NOW + timedelta(hours=1),
        absolute_expires_at=NOW + timedelta(days=7),
        revoked_at=NOW if revoked else None,
        revoked_reason="logout" if revoked else None,
        version=1, created_at=NOW, last_used_at=NOW, updated_at=NOW,
    )
    db.session.add(family)
    db.session.commit()
    return family


def test_credential_change_revokes_every_family_of_only_that_user(
        app, mobile_user, provider, make_user):
    from app.services import mobile_auth

    first = mobile_auth.login("mobile-service", "correct", now=NOW)
    second = mobile_auth.login(
        "mobile-service", "correct", now=NOW + timedelta(minutes=1))
    bystander = make_user("bystander", cognito_sub="sub-bystander")
    untouched = _other_user_family(bystander)

    results = mobile_auth.revoke_all_for_user(
        mobile_user.id, now=NOW + timedelta(minutes=2))

    assert len(results) == 2
    assert {r.provider_refresh_token for r in results} == {"provider-refresh"}
    mine = MobileAuthSession.query.filter_by(user_id=mobile_user.id).all()
    assert len(mine) == 2
    for family in mine:
        assert family.revoked_at == NOW + timedelta(minutes=2)
        assert family.revoked_reason == "credential_change"
        assert family.cognito_access_token is None
        assert family.cognito_refresh_token is None
    assert {r.family_id for r in results} == {f.family_id for f in mine}
    # Every credential row of both families is revoked, not just the family.
    assert MobileAccessCredential.query.filter(
        MobileAccessCredential.revoked_at.is_(None)).count() == 0
    assert MobileRefreshCredential.query.filter(
        MobileRefreshCredential.revoked_at.is_(None)).count() == 0
    db.session.refresh(untouched)
    assert untouched.revoked_at is None
    assert untouched.cognito_refresh_token is not None
    assert first.access_credential != second.access_credential


def test_credential_change_makes_existing_mobile_credentials_unusable(
        app, mobile_user, provider):
    from app.services import mobile_auth

    issued = mobile_auth.login("mobile-service", "correct", now=NOW)
    later = NOW + timedelta(minutes=2)
    # Precondition: both credentials work before the credential change.
    assert mobile_auth.authenticate_access(
        issued.access_credential, now=later).user.id == mobile_user.id

    mobile_auth.revoke_all_for_user(mobile_user.id, now=later)

    with pytest.raises(mobile_auth.MobileAuthFailure) as access_exc:
        mobile_auth.authenticate_access(issued.access_credential, now=later)
    assert access_exc.value.code == "AUTH_SESSION_EXPIRED"
    assert access_exc.value.status == 401
    assert access_exc.value.retryable is False

    with pytest.raises(mobile_auth.MobileAuthFailure) as refresh_exc:
        mobile_auth.refresh(issued.refresh_credential, now=later)
    assert refresh_exc.value.code == "AUTH_REFRESH_FAILED"
    assert refresh_exc.value.reason == "refresh_revoked"


def test_credential_change_revocation_is_idempotent_and_keeps_first_reason(
        app, mobile_user, provider):
    from app.services import mobile_auth

    mobile_auth.login("mobile-service", "correct", now=NOW)
    first = mobile_auth.revoke_all_for_user(
        mobile_user.id, now=NOW + timedelta(minutes=2))
    second = mobile_auth.revoke_all_for_user(
        mobile_user.id, now=NOW + timedelta(minutes=3))

    assert len(first) == 1
    assert second == []
    family = MobileAuthSession.query.one()
    assert family.revoked_at == NOW + timedelta(minutes=2)
    assert family.revoked_reason == "credential_change"


def test_credential_change_skips_unreadable_provider_ciphertext(
        app, mobile_user, provider):
    from app.services import mobile_auth

    mobile_auth.login("mobile-service", "correct", now=NOW)
    family = MobileAuthSession.query.one()
    family.cognito_refresh_token = "not-a-fernet-token"
    db.session.commit()

    results = mobile_auth.revoke_all_for_user(
        mobile_user.id, now=NOW + timedelta(minutes=2))

    assert len(results) == 1
    assert results[0].provider_refresh_token is None
    assert MobileAuthSession.query.one().revoked_at is not None


def test_credential_change_revoke_commit_failure_is_typed_retryable(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    mobile_auth.login("mobile-service", "correct", now=NOW)
    _fail_commit_from_call(monkeypatch, 2)

    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.revoke_all_for_user(
            mobile_user.id, now=NOW + timedelta(minutes=2))

    _assert_retryable_storage_failure(exc)


def test_credential_change_lookup_failure_is_typed_retryable(
        app, mobile_user, provider, monkeypatch):
    """The FAMILY lookup, specifically: the epoch fence queries before it."""
    from app.services import mobile_auth

    mobile_auth.login("mobile-service", "correct", now=NOW)
    real_query = db.session.query
    calls = {"n": 0}

    def query(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("database unavailable")
        return real_query(*args, **kwargs)

    monkeypatch.setattr(db.session, "query", query)

    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.revoke_all_for_user(
            mobile_user.id, now=NOW + timedelta(minutes=2))

    _assert_retryable_storage_failure(exc)


def test_credential_change_reason_fits_the_persisted_column(app):
    from app.services import mobile_auth

    column = MobileAuthSession.__table__.c.revoked_reason
    assert len(mobile_auth.CREDENTIAL_CHANGE_REASON) <= column.type.length


def _credential_change_lands_during_provider_call(monkeypatch, user_id, now):
    """Make the password change commit while `login` is inside Cognito.

    The window is real and it is wide: `cognito_service.authenticate` is a
    network round-trip (5s connect, 10s read, two attempts) and the family row
    is written only after it returns. Driving the whole credential change from
    inside the stub reproduces the interleaving exactly, in one thread, with no
    sleep and no barrier - the reset commits strictly between this login's fence
    read and its INSERT, which is the only ordering that ever mattered.
    """
    from app.services import mobile_auth

    authenticated = {"with_old_password": False}
    real_authenticate = cognito_service.authenticate

    def authenticate(username, password):
        result = real_authenticate(username, password)
        authenticated["with_old_password"] = True
        mobile_auth.revoke_all_for_user(user_id, now=now)
        return result

    monkeypatch.setattr(cognito_service, "authenticate", authenticate)
    return authenticated


def _assert_refused_as_stale_credential(exc):
    assert exc.value.code == "AUTH_INVALID_CREDENTIALS"
    assert exc.value.status == 401
    assert exc.value.retryable is False
    assert exc.value.reason == "credential_changed_during_login"


def test_login_authenticated_before_a_reset_cannot_create_a_session_after_it(
        app, mobile_user, provider, monkeypatch):
    """The in-flight login race, with nothing for the family sweep to find.

    This is the case a candidate snapshot can never cover: at the moment the
    credential change collects the families to revoke, this login's family does
    not exist yet. Only the epoch fence can refuse it.
    """
    from app.services import mobile_auth

    user_id = mobile_user.id
    authenticated = _credential_change_lands_during_provider_call(
        monkeypatch, user_id, NOW + timedelta(minutes=1))

    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.login("mobile-service", "correct", now=NOW)

    assert authenticated["with_old_password"] is True
    _assert_refused_as_stale_credential(exc)
    # Refused means never born: no family, no credential, nothing to expire.
    assert MobileAuthSession.query.filter_by(user_id=user_id).count() == 0
    assert MobileAccessCredential.query.count() == 0
    assert MobileRefreshCredential.query.count() == 0


def test_in_flight_login_is_refused_while_the_existing_family_is_revoked(
        app, mobile_user, provider, monkeypatch):
    """Both halves of the invariant at once: the old session dies and the new
    one is never issued."""
    from app.services import mobile_auth

    user_id = mobile_user.id
    established = mobile_auth.login("mobile-service", "correct", now=NOW)
    _credential_change_lands_during_provider_call(
        monkeypatch, user_id, NOW + timedelta(minutes=1))

    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.login(
            "mobile-service", "correct", now=NOW + timedelta(seconds=1))

    _assert_refused_as_stale_credential(exc)
    family = MobileAuthSession.query.filter_by(user_id=user_id).one()
    assert family.revoked_reason == mobile_auth.CREDENTIAL_CHANGE_REASON
    with pytest.raises(mobile_auth.MobileAuthFailure) as reused:
        mobile_auth.authenticate_access(
            established.access_credential, now=NOW + timedelta(minutes=2))
    assert reused.value.code == "AUTH_SESSION_EXPIRED"


def test_login_authenticated_after_the_reset_is_still_issued_a_session(
        app, mobile_user, provider):
    """The fence must not cost the user the login they are about to make.

    A legitimate login reads the already-bumped epoch as its own fence, so the
    two reads agree and the session is issued exactly as before.
    """
    from app.services import mobile_auth

    mobile_auth.revoke_all_for_user(mobile_user.id, now=NOW)
    issued = mobile_auth.login(
        "mobile-service", "new-password", now=NOW + timedelta(minutes=1))

    principal = mobile_auth.authenticate_access(
        issued.access_credential, now=NOW + timedelta(minutes=2))
    assert principal.user.id == mobile_user.id
    assert principal.family.revoked_at is None
    assert MobileAuthSession.query.filter(
        MobileAuthSession.revoked_at.is_(None)).count() == 1


def test_repeated_logins_are_unaffected_while_the_credential_stands(
        app, mobile_user, provider):
    """The fence is not a one-shot token: it moves only when the password does."""
    from app.services import mobile_auth

    first = mobile_auth.login("mobile-service", "correct", now=NOW)
    second = mobile_auth.login(
        "mobile-service", "correct", now=NOW + timedelta(minutes=1))

    assert first.access_credential != second.access_credential
    assert MobileAuthSession.query.filter(
        MobileAuthSession.revoked_at.is_(None)).count() == 2
    assert mobile_user.credential_epoch == 0


def test_credential_fence_is_scoped_to_the_account_that_changed(
        app, mobile_user, make_user, provider, monkeypatch):
    """A reset for somebody else must not refuse this user's login."""
    from app.services import mobile_auth

    bystander = make_user("mobile-bystander", cognito_sub="sub-bystander")
    bystander_id = bystander.id
    _credential_change_lands_during_provider_call(
        monkeypatch, bystander_id, NOW + timedelta(minutes=1))

    issued = mobile_auth.login("mobile-service", "correct", now=NOW)

    assert mobile_auth.authenticate_access(
        issued.access_credential,
        now=NOW + timedelta(minutes=2)).user.id == mobile_user.id
    assert db.session.query(User.credential_epoch).filter_by(
        id=bystander_id).scalar() == 1
    assert db.session.query(User.credential_epoch).filter_by(
        id=mobile_user.id).scalar() == 0


def test_every_credential_change_moves_the_fence_and_never_resets_it(
        app, mobile_user, provider):
    from app.services import mobile_auth

    for expected in (1, 2, 3):
        mobile_auth.revoke_all_for_user(mobile_user.id, now=NOW)
        assert db.session.query(User.credential_epoch).filter_by(
            id=mobile_user.id).scalar() == expected


def test_the_fence_survives_a_family_sweep_that_cannot_be_persisted(
        app, mobile_user, provider, monkeypatch):
    """A 503 must not leave the account unfenced.

    The route answers "I could not close your sessions" and the user resets
    again; in between, a login that authenticated with the old password must
    still be refused. That only holds because the epoch is committed before the
    sweep can fail.
    """
    from app.services import mobile_auth

    mobile_auth.login("mobile-service", "correct", now=NOW)
    _fail_commit_from_call(monkeypatch, 2)

    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.revoke_all_for_user(
            mobile_user.id, now=NOW + timedelta(minutes=2))

    _assert_retryable_storage_failure(exc)
    monkeypatch.undo()
    assert MobileAuthSession.query.one().revoked_at is None
    assert db.session.query(User.credential_epoch).filter_by(
        id=mobile_user.id).scalar() == 1


def test_fence_storage_failure_is_typed_retryable_and_revokes_nothing(
        app, mobile_user, provider, monkeypatch):
    from app.services import mobile_auth

    mobile_auth.login("mobile-service", "correct", now=NOW)
    monkeypatch.setattr(
        db.session, "query",
        lambda *args: (_ for _ in ()).throw(RuntimeError("database unavailable")))

    with pytest.raises(mobile_auth.MobileAuthFailure) as exc:
        mobile_auth.revoke_all_for_user(
            mobile_user.id, now=NOW + timedelta(minutes=2))

    _assert_retryable_storage_failure(exc)
    monkeypatch.undo()
    assert MobileAuthSession.query.one().revoked_at is None


def test_fencing_an_absent_account_is_a_no_op_rather_than_a_failure(app):
    from app.services import mobile_auth

    assert mobile_auth.revoke_all_for_user(999_999, now=NOW) == []
