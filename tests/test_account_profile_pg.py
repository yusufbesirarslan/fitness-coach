"""PostgreSQL proof: concurrent first onboarding creates ONE canonical session.

SQLite admits one writer at a time, so it cannot show the race the owner-row
lock closes: two first submissions both reading "no session" and both
inserting one. Here the first submission is held after its canonical-session
read, inside its transaction, while the second is observed reaching the
owner-row `FOR UPDATE` and not getting past it. Released, the second one sees
the committed session and updates it.

    FITX_PG_CONCURRENCY_TEST=1 PG_TEST_DATABASE_URL=postgresql://… \
        python -m pytest -m pg_concurrency tests/test_account_profile_pg.py
"""
import os
import threading
from datetime import datetime

import pytest
import sqlalchemy as sa


pytestmark = pytest.mark.pg_concurrency

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip("requires disposable PG_TEST_DATABASE_URL", allow_module_level=True)


@pytest.fixture
def pg_app(monkeypatch):
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.skip("PG_TEST_DATABASE_URL must name disposable PostgreSQL")
    probe = sa.create_engine(url)
    try:
        with probe.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
    except Exception:
        pytest.skip("disposable PostgreSQL is unreachable")
    finally:
        probe.dispose()

    monkeypatch.setenv("DATABASE_URL", url)
    from app import create_app
    from app.extensions import db, limiter
    from app.services import gamification

    app = create_app()
    app.config["TESTING"] = True
    limiter.enabled = False
    gamification._last_rollover_check[0] = datetime.utcnow()
    with app.app_context():
        from app.models import User
        assert db.engine.dialect.name == "postgresql"
        db.drop_all()
        db.create_all()
        db.session.add(User(username="pgonboard", email="pgonboard@example.invalid",
                            cognito_sub="sub-pgonboard"))
        db.session.commit()
    try:
        yield app
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
            db.drop_all()


def test_concurrent_first_onboarding_serializes_on_the_owner_row(pg_app, monkeypatch):
    from app.extensions import db
    from app.models import User, UserSession
    from app.services import account_profile

    profile = account_profile.OnboardingProfile(
        weight=80, height=180, age=30, gender="male", goal="lose_weight",
        fitness_level="beginner", current_activity="active")
    first_read = threading.Event()
    release_first = threading.Event()
    second_at_lock = threading.Event()
    second_read = threading.Event()
    thread_role = {}
    real_canonical_session = account_profile.canonical_session

    def canonical_session(user_id):
        row = real_canonical_session(user_id)
        role = thread_role.get(threading.get_ident())
        if role == "first":
            first_read.set()
            assert release_first.wait(10), "first submission was never released"
        elif role == "second":
            second_read.set()
        return row

    monkeypatch.setattr(account_profile, "canonical_session", canonical_session)

    with pg_app.app_context():
        engine = db.engine

    def observe(_conn, _cursor, statement, _params, _context, _many):
        if (thread_role.get(threading.get_ident()) == "second"
                and "FOR UPDATE" in statement.upper()
                and '"user"' in statement.lower()):
            second_at_lock.set()

    sa.event.listen(engine, "before_cursor_execute", observe)
    results, errors = {}, []

    def submit(role):
        thread_role[threading.get_ident()] = role
        try:
            with pg_app.app_context():
                user = User.query.filter_by(username="pgonboard").one()
                results[role] = account_profile.complete_onboarding(user, profile)
                db.session.remove()
        except Exception as error:  # surfaced below
            errors.append(error)

    first = threading.Thread(target=submit, args=("first",))
    second = threading.Thread(target=submit, args=("second",))
    try:
        first.start()
        assert first_read.wait(10), "first submission never read the session"
        second.start()
        assert second_at_lock.wait(10), "second submission never reached the lock"
        # Blocked at the owner row: it must not get to its own session read
        # while the first transaction is still open.
        assert not second_read.wait(1.0), "second submission passed the lock"
        release_first.set()
        first.join(10)
        second.join(10)
    finally:
        release_first.set()
        sa.event.remove(engine, "before_cursor_execute", observe)

    assert errors == []
    assert results["first"].session_created is True
    assert results["second"].session_created is False
    with pg_app.app_context():
        user = User.query.filter_by(username="pgonboard").one()
        assert UserSession.query.filter_by(user_id=user.id).count() == 1
        assert account_profile.onboarding_state(user).complete is True
