"""Disposable-PostgreSQL concurrency coverage for mobile refresh rotation."""

import calendar
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from flask import Flask
import sqlalchemy as sa
from sqlalchemy import event

from app.extensions import db
from app.models import (
    MobileAccessCredential, MobileAuthSession, MobileRefreshCredential, User,
)
from app.services import cognito_jwt, cognito_service, mobile_auth


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
    counter = {"calls": 0}
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
        expiry = new_exp if token == "pg-provider-new" else old_exp
        return {
            "sub": "pg-mobile-race-sub",
            "exp": calendar.timegm(expiry.timetuple()),
        }

    def renew(refresh_token, username):
        with counter_lock:
            counter["calls"] += 1
        return {
            "access_token": "pg-provider-new",
            "refresh_token": refresh_token,
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

def test_same_parent_race_commits_exactly_one_child_and_one_provider_renewal(
        pg_app):
    app, counter = pg_app
    with app.app_context():
        original = mobile_auth.login("pg-mobile-race", "correct", now=NOW)
        engine = db.engine

    barrier = threading.Barrier(2)
    pre_family_lock = threading.Barrier(2)

    def force_both_scalar_lookups_before_family_lock(
            conn, cursor, statement, parameters, context, executemany):
        normalized = " ".join(statement.lower().split())
        if ("from mobile_auth_session" in normalized
                and "for update" in normalized):
            pre_family_lock.wait(timeout=10)

    event.listen(
        engine, "before_cursor_execute",
        force_both_scalar_lookups_before_family_lock)

    def rotate():
        with app.app_context():
            barrier.wait(timeout=10)
            return mobile_auth.refresh(
                original.refresh_credential,
                now=NOW + timedelta(seconds=850))

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(rotate) for _ in range(2)]
            results = [future.result(timeout=30) for future in futures]
    finally:
        event.remove(
            engine, "before_cursor_execute",
            force_both_scalar_lookups_before_family_lock)

    assert results[0] == results[1]
    with app.app_context():
        family = MobileAuthSession.query.one()
        assert family.version == 2
        assert MobileAccessCredential.query.filter_by(generation=1).count() == 1
        assert MobileRefreshCredential.query.filter_by(generation=1).count() == 1
        assert MobileRefreshCredential.query.filter_by(generation=2).count() == 0
    assert counter["calls"] == 1
