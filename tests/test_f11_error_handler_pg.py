"""F11 on real PostgreSQL: the 500 recovery path issues no SQL.

SQLite cannot reproduce PostgreSQL's aborted-transaction state ("current
transaction is aborted, commands ignored until end of transaction block") or a
server-side backend termination. Each case drives the full app stack through
Flask's real error path with an authenticated user whose ``current_user`` is
expired by an earlier commit — exactly what made the pre-F11 ``inject_rank``
re-query the user while rendering the error page.

Assertions are behavioural (status, page, statement count); no PostgreSQL
error text is matched.
"""
import os

import pytest
import sqlalchemy as sa


pytestmark = pytest.mark.pg_concurrency

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip(
        "set FITX_PG_CONCURRENCY_TEST=1 with a disposable PG_TEST_DATABASE_URL",
        allow_module_level=True,
    )


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

    monkeypatch.setenv("DATABASE_URL", url)
    from datetime import datetime

    from app import create_app
    from app.blueprints import auth as auth_bp
    from app.extensions import db, limiter
    from app.models import User
    from app.services import cognito_jwt, cognito_service, gamification

    app = create_app()
    app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
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

    state = {"sql": [], "mark": None}

    def record(conn, cursor, statement, params, context, executemany):
        state["sql"].append(" ".join(statement.split())[:120])

    def failing(op):
        try:
            op()
        except Exception:
            state["mark"] = len(state["sql"])
            raise
        raise AssertionError("the triggering operation was expected to fail")

    def unique_violation():
        db.session.commit()

        def op():
            db.session.add(User(username="f11pg-taken", email="dup@example.invalid",
                                cognito_sub="f11pg-dup"))
            db.session.commit()
        failing(op)

    def aborted_transaction():
        db.session.commit()

        def op():
            db.session.execute(sa.text("SELECT 1 / 0"))  # PG txn -> aborted state
        failing(op)

    def backend_terminated():
        db.session.commit()
        pid = db.session.execute(sa.text("SELECT pg_backend_pid()")).scalar()
        killer = sa.create_engine(url)
        try:
            with killer.connect() as connection:
                connection.execute(sa.text("SELECT pg_terminate_backend(:pid)"),
                                   {"pid": pid})
        finally:
            killer.dispose()

        def op():
            db.session.execute(sa.text("SELECT 1"))
        failing(op)

    def count_users():
        return {"users": db.session.query(User).count()}

    app.add_url_rule("/_f11pg/unique", "f11pg_unique", unique_violation)
    app.add_url_rule("/_f11pg/aborted", "f11pg_aborted", aborted_transaction)
    app.add_url_rule("/_f11pg/terminated", "f11pg_terminated", backend_terminated)
    app.add_url_rule("/_f11pg/count", "f11pg_count", count_users)

    with app.app_context():
        db.drop_all()
        db.create_all()
        for name in ("f11pg-taken", "f11pg-user"):
            db.session.add(User(username=name, email=f"{name}@example.invalid",
                                cognito_sub=f"sub-{name}"))
        db.session.commit()
        sa.event.listen(db.engine, "before_cursor_execute", record)
        try:
            yield app, state
        finally:
            sa.event.remove(db.engine, "before_cursor_execute", record)
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


@pytest.mark.parametrize("path", ["/_f11pg/unique", "/_f11pg/aborted", "/_f11pg/terminated"])
def test_pg_failure_renders_500_without_recovery_sql_and_next_request_is_clean(pg_app, path):
    app, state = pg_app
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "f11-pg-csrf"
    login = client.post("/login", json={"username": "f11pg-user", "password": "unused"},
                        headers={"Origin": "http://localhost", "X-CSRFToken": "f11-pg-csrf"})
    assert login.status_code == 200
    state["sql"].clear()
    state["mark"] = None

    response = client.get(path)

    assert response.status_code == 500
    html = response.get_data(as_text=True)
    assert "<h1>500</h1>" in html
    assert "Bir şeyler ters gitti" in html
    assert "Content-Security-Policy" in response.headers
    for marker in ("Error", "SELECT", "INSERT", "psycopg2", "f11pg-taken", "Traceback"):
        assert marker not in html, marker
    assert state["mark"] is not None
    assert state["sql"][state["mark"]:] == []

    follow_up = client.get("/_f11pg/count")
    assert follow_up.status_code == 200
    assert follow_up.get_json() == {"users": 2}
