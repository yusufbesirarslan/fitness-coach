"""Disposable-PostgreSQL concurrency coverage for mobile refresh rotation.

Two requests may renew the provider token from the same phase-one snapshot, but
only one phase-two transaction may persist its provider material and child.
"""

import calendar
import os
import threading
from datetime import datetime, timedelta

import pytest
from flask import Flask
import sqlalchemy as sa

from app.extensions import db
from app.models import (
    MobileAccessCredential, MobileAuthSession, MobileRefreshCredential, User,
)
from app.services import cognito_jwt, cognito_service, mobile_auth, session_store


pytestmark = pytest.mark.pg_concurrency

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip(
        "set FITX_PG_CONCURRENCY_TEST=1 with a disposable PG_TEST_DATABASE_URL",
        allow_module_level=True,
    )


NOW = datetime(2026, 7, 29, 10)


@pytest.fixture
def pg_app(monkeypatch):
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.skip("PG_TEST_DATABASE_URL must name a disposable PostgreSQL database")
    probe = sa.create_engine(url)
    try:
        with probe.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except Exception:
        pytest.skip("disposable PostgreSQL database is not reachable")
    finally:
        probe.dispose()

    app = Flask("mobile-auth-pg-race")
    app.config.update(
        TESTING=True,
        SECRET_KEY="disposable-pg-mobile-auth-test",
        SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MOBILE_AUTH_ACCESS_TTL_SECONDS=900,
        MOBILE_AUTH_REFRESH_ABSOLUTE_DAYS=7,
        MOBILE_AUTH_REFRESH_RETRY_GRACE_SECONDS=10,
        MOBILE_AUTH_COGNITO_EXPIRY_LEEWAY_SECONDS=60,
        MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS=0,
        MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION="pg-v1",
        MOBILE_AUTH_DERIVATION_KEYRING={"pg-v1": b"p" * 32},
    )
    db.init_app(app)

    old_exp = NOW + timedelta(seconds=901)
    new_exp = NOW + timedelta(hours=3)
    counter = {
        "calls": 0,
        "provider_barrier": None,
        "thread_state": threading.local(),
        "winner_at_provider": None,
        "winner_finished": None,
    }
    counter_lock = threading.Lock()

    monkeypatch.setattr(cognito_service, "authenticate", lambda username, password: {
        "tokens": {
            "access_token": "pg-provider-old", "id_token": "pg-provider-id",
            "refresh_token": "pg-provider-refresh", "expires_in": 901,
        },
        "claims": {"sub": "pg-mobile-race-sub"},
    })

    def validate(token, expected_use, leeway_seconds=0):
        if expected_use == "id":
            return {
                "sub": "pg-mobile-race-sub", "email": "pg-race@example.invalid",
                "email_verified": True,
            }
        expiry = (
            new_exp if token.startswith("pg-provider-new-") else old_exp)
        return {
            "sub": "pg-mobile-race-sub",
            "exp": calendar.timegm(expiry.timetuple()),
        }

    def renew(_refresh_token, _username):
        contender = counter["thread_state"].contender
        with counter_lock:
            counter["calls"] += 1
        if contender == "winner":
            counter["winner_at_provider"].set()
        counter["provider_barrier"].wait(timeout=10)
        if contender == "stale":
            assert counter["winner_finished"].wait(timeout=10)
        return {
            "access_token": f"pg-provider-new-{contender}",
            "refresh_token": f"pg-provider-refresh-{contender}",
        }

    monkeypatch.setattr(cognito_jwt, "validate_token", validate)
    monkeypatch.setattr(cognito_service, "refresh_tokens", renew)

    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(User(
            username="pg-mobile-race", email="pg-race@example.invalid",
            cognito_sub="pg-mobile-race-sub"))
        db.session.commit()
    try:
        yield app, counter
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_same_parent_race_commits_one_child_and_keeps_winner_provider_tokens(
        pg_app):
    app, counter = pg_app
    with app.app_context():
        original = mobile_auth.login("pg-mobile-race", "correct", now=NOW)

    counter["provider_barrier"] = threading.Barrier(2)
    counter["winner_at_provider"] = threading.Event()
    counter["winner_finished"] = threading.Event()
    outcomes = {}

    def rotate(contender):
        with app.app_context():
            counter["thread_state"].contender = contender
            try:
                outcomes[contender] = (
                    "issued",
                    mobile_auth.refresh(
                        original.refresh_credential,
                        now=NOW + timedelta(seconds=850),
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
                    counter["winner_finished"].set()
                db.session.remove()

    threads = {
        contender: threading.Thread(
            target=rotate, args=(contender,), daemon=True)
        for contender in ("winner", "stale")
    }
    threads["winner"].start()
    assert counter["winner_at_provider"].wait(timeout=10), outcomes
    threads["stale"].start()
    for thread in threads.values():
        thread.join(timeout=30)

    assert not any(thread.is_alive() for thread in threads.values()), outcomes
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
    with app.app_context():
        family = MobileAuthSession.query.one()
        assert family.version == 2
        assert family.revoked_at is None
        assert MobileAccessCredential.query.filter_by(generation=1).count() == 1
        assert MobileRefreshCredential.query.filter_by(generation=1).count() == 1
        assert MobileRefreshCredential.query.filter_by(generation=2).count() == 0
        assert session_store.decrypt_token(
            family.cognito_access_token) == "pg-provider-new-winner"
        assert session_store.decrypt_token(
            family.cognito_refresh_token) == "pg-provider-refresh-winner"
    assert counter["calls"] == 2
