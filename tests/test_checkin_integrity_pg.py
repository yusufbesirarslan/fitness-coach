"""PostgreSQL proof that concurrent same-key POSTs call feedback once."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
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
    from app.blueprints import auth as auth_bp
    from app.extensions import db, limiter
    from app.services import cognito_jwt, cognito_service, gamification

    app = create_app()
    app.config["TESTING"] = True
    limiter.enabled = False
    gamification._last_rollover_check[0] = datetime.utcnow()
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", True)
    monkeypatch.setattr(cognito_service, "authenticate", lambda username, _pw: {
        "tokens": {"access_token": f"acc-{username}", "id_token": f"id-{username}",
                   "refresh_token": f"ref-{username}", "expires_in": 3600},
        "claims": {"sub": f"sub-{username}"},
    })
    monkeypatch.setattr(cognito_jwt, "validate_token", lambda token, use, **kw: {
        "sub": f"sub-{token.removeprefix('id-').removeprefix('acc-')}"})
    with app.app_context():
        from app.models import User
        db.drop_all()
        db.create_all()
        db.session.add(User(username="pgcheckin", email="pgcheckin@example.invalid",
                            cognito_sub="sub-pgcheckin"))
        db.session.commit()
    try:
        yield app
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
            db.drop_all()


def _client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "pg-checkin-csrf"
    headers = {"Origin": "http://localhost", "X-CSRFToken": "pg-checkin-csrf"}
    assert client.post("/login", json={"username": "pgcheckin", "password": "unused"},
                       headers=headers).status_code == 200
    return client, headers


def test_two_concurrent_same_token_requests_share_one_committed_result(pg_app, monkeypatch):
    from app.blueprints import tracking
    from app.extensions import db
    from app.models import WeeklyCheckIn

    entered = threading.Event()
    release = threading.Event()
    second_at_lock = threading.Event()
    second_thread = [None]
    calls = []

    def feedback(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(10), "first feedback was never released"
        return "one feedback"

    monkeypatch.setattr(tracking, "generate_checkin_feedback", feedback)
    first_client, headers = _client(pg_app)
    second_client, _ = _client(pg_app)
    headers["Idempotency-Key"] = "pg-checkin-attempt-0001"

    def observe_lock(_conn, _cursor, statement, _params, _context, _many):
        if (threading.get_ident() == second_thread[0] and
                "FOR UPDATE" in statement.upper() and
                "user" in statement.lower()):
            second_at_lock.set()

    with pg_app.app_context():
        engine = db.engine
    sa.event.listen(engine, "before_cursor_execute", observe_lock)

    def submit(client):
        return client.post("/checkin", json={"weight": 79}, headers=headers)

    def second_submit():
        second_thread[0] = threading.get_ident()
        return submit(second_client)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(submit, first_client)
            assert entered.wait(10), "first request did not reach feedback"
            second = pool.submit(second_submit)
            try:
                assert second_at_lock.wait(10), "second request did not reach owner lock"
                assert not second.done(), "duplicate crossed the held owner lock"
            finally:
                release.set()
            responses = [first.result(timeout=15), second.result(timeout=15)]
    finally:
        sa.event.remove(engine, "before_cursor_execute", observe_lock)

    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].get_json() == responses[1].get_json()
    assert len(calls) == 1
    with pg_app.app_context():
        assert WeeklyCheckIn.query.count() == 1
        assert WeeklyCheckIn.query.one().coach_feedback == "one feedback"
