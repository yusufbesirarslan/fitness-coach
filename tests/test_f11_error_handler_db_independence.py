"""F11: the 500 recovery path must not depend on the database.

On the pre-F11 handler (``render_template("500.html")``) every global context
processor ran while the error page rendered. ``inject_rank`` reads
``current_user.rank_points``; once the request's session had committed (every
commit expires ``current_user``) or failed a flush, that read either issued a
fresh ``SELECT`` against a possibly dead database or raised
``PendingRollbackError`` out of the error handler itself, so the user got the
WSGI server's bare 500 instead of ours (no CSP header, no request log line).

These tests drive the real Flask error path (``PROPAGATE_EXCEPTIONS=False``)
through the full app stack and record every SQL statement the engine is asked
to execute. The invariant: once the original failure has happened, producing
the 500 response issues zero statements.
"""
import re
import sqlite3

import pytest
from flask import got_request_exception
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.extensions import db
from app.models import User

_LEAK_MARKERS = (
    b"boom-secret-detail", b"RuntimeError", b"IntegrityError", b"OperationalError",
    b"PendingRollbackError", b"INSERT", b"SELECT", b"UNIQUE", b"user.username",
    b"Traceback", b"sqlite", b"simulated outage",
)
_TR_BODY = "Bir şeyler ters gitti. Kısa süre içinde tekrar dene."
_EN_BODY = "Something went wrong. Please try again shortly."


@pytest.fixture
def f11(app):
    """Test routes + an SQL recorder + a switchable DB outage + a processor spy.

    Must be requested BEFORE any fixture that issues a request (routes cannot
    be added after the app's first request)."""
    app.config["PROPAGATE_EXCEPTIONS"] = False
    state = {"sql": [], "mark": None, "outage": False, "procs": [],
             "commits_after_mark": 0}

    def record(conn, cursor, statement, params, context, executemany):
        state["sql"].append(" ".join(statement.split())[:120])

    def maybe_fail(cursor, statement, params, context):
        if state["outage"]:
            raise sqlite3.OperationalError("simulated outage")
        return False

    def count_commit(session):
        if state["mark"] is not None:
            state["commits_after_mark"] += 1

    engine = db.engine
    event.listen(engine, "before_cursor_execute", record)
    event.listen(engine.dialect, "do_execute", maybe_fail)
    event.listen(Session, "after_commit", count_commit)

    processors = app.template_context_processors[None]
    for index, fn in enumerate(list(processors)):
        def spy(fn=fn):
            state["procs"].append(fn.__name__)
            return fn()
        processors[index] = spy

    def failing(op):
        """Run the triggering operation; mark the moment it has failed and let
        the exception reach Flask unrepaired."""
        try:
            op()
        except Exception:
            state["mark"] = len(state["sql"])
            raise
        raise AssertionError("the triggering operation was expected to fail")

    def plain():
        def op():
            raise RuntimeError("boom-secret-detail")
        failing(op)

    def commit_then_raise():
        db.session.commit()  # expires current_user, like any real write route

        def op():
            raise RuntimeError("boom-secret-detail")
        failing(op)

    def poisoned(prior_commit):
        def view():
            if prior_commit:
                db.session.commit()

            def op():
                db.session.add(User(username="f11-taken", email="dup@example.com",
                                    cognito_sub="f11-dup"))
                db.session.commit()  # unique violation during flush
            failing(op)
        view.__name__ = f"f11_poisoned_{int(prior_commit)}"
        return view

    def outage():
        db.session.commit()

        def op():
            state["outage"] = True
            db.session.query(User).count()
        failing(op)

    def count_users():
        return {"users": db.session.query(User).count()}

    app.add_url_rule("/_f11/plain", "f11_plain", plain)
    app.add_url_rule("/_f11/commit-raise", "f11_commit_raise", commit_then_raise)
    app.add_url_rule("/_f11/poisoned", "f11_poisoned", poisoned(False))
    app.add_url_rule("/_f11/poisoned-after-commit", "f11_poisoned_after_commit",
                     poisoned(True))
    app.add_url_rule("/_f11/outage", "f11_outage", outage)
    app.add_url_rule("/_f11/count", "f11_count", count_users)

    def reset():
        state["sql"].clear()
        state["procs"].clear()
        state["mark"] = None
        state["outage"] = False
        state["commits_after_mark"] = 0

    state["reset"] = reset
    yield state
    state["outage"] = False
    event.remove(engine, "before_cursor_execute", record)
    event.remove(engine.dialect, "do_execute", maybe_fail)
    event.remove(Session, "after_commit", count_commit)


@pytest.fixture
def authed(f11, make_user, login, client):
    """A real logged-in session. The warm-up request consumes the day's
    first-request streak commit so each test starts from a quiet request.
    Returns the user's id (read now, before any test poisons the session)."""
    make_user("f11-taken")
    user_id = make_user("f11user").id
    assert login("f11user").status_code == 200
    client.get("/_f11/plain")
    f11["reset"]()
    return user_id


def _recovery_sql(state):
    assert state["mark"] is not None, "the triggering operation never failed"
    return state["sql"][state["mark"]:]


def _assert_generic_500(response, body_text=_TR_BODY):
    assert response.status_code == 500
    assert response.mimetype == "text/html"
    html = response.get_data(as_text=True)
    assert "<h1>500</h1>" in html
    assert body_text in html
    assert '<a href="/">' in html
    for marker in _LEAK_MARKERS:
        assert marker not in response.data, marker
    assert "Content-Security-Policy" in response.headers


_FAILURES = ["/_f11/plain", "/_f11/commit-raise", "/_f11/poisoned",
             "/_f11/poisoned-after-commit", "/_f11/outage"]


@pytest.mark.parametrize("path", _FAILURES)
def test_authenticated_failure_renders_500_with_zero_recovery_sql(client, f11, authed, path):
    response = client.get(path)

    _assert_generic_500(response)
    assert _recovery_sql(f11) == []
    assert f11["commits_after_mark"] == 0


@pytest.mark.parametrize("path", ["/_f11/plain", "/_f11/poisoned", "/_f11/outage"])
def test_anonymous_failure_renders_500_with_zero_recovery_sql(client, f11, make_user, path):
    make_user("f11-taken")
    f11["reset"]()

    response = client.get(path)

    _assert_generic_500(response)
    assert _recovery_sql(f11) == []


def test_poisoned_session_does_not_raise_pending_rollback_during_rendering(
        client, f11, authed, caplog):
    response = client.get("/_f11/poisoned-after-commit")

    _assert_generic_500(response)
    rendered_messages = [r.getMessage() for r in caplog.records]
    assert not any("PendingRollbackError" in m for m in rendered_messages)
    secondary = [r for r in caplog.records
                 if r.exc_info and r.exc_info[0].__name__ == "PendingRollbackError"]
    assert secondary == []


def test_poisoned_session_is_reset_before_the_emergency_page_renders(
        client, f11, authed, monkeypatch):
    import app.hooks as hooks
    seen = []
    original = hooks._render_emergency_500

    def observe():
        seen.append(db.session.is_active)
        return original()

    monkeypatch.setattr(hooks, "_render_emergency_500", observe)

    response = client.get("/_f11/poisoned")

    _assert_generic_500(response)
    assert seen == [True]


def test_emergency_render_bypasses_every_global_context_processor(client, f11, authed):
    response = client.get("/_f11/plain")

    _assert_generic_500(response)
    assert f11["procs"] == []


def test_normal_page_still_runs_the_normal_processors_and_rank(client, f11, authed):
    response = client.get("/notifications")

    assert response.status_code == 200
    for name in ("inject_csp_nonce", "inject_csrf_token", "inject_i18n",
                 "inject_nav", "inject_rank"):
        assert name in f11["procs"]


def test_404_keeps_the_normal_render_path(client, f11, authed):
    response = client.get("/_f11/does-not-exist")

    assert response.status_code == 404
    assert "inject_rank" in f11["procs"]


@pytest.mark.parametrize("path", ["/_f11/plain", "/_f11/poisoned"])
def test_500_nonce_matches_the_csp_header(client, f11, authed, path):
    response = client.get(path)

    _assert_generic_500(response)
    page_nonce = re.search(r'<style nonce="([^"]+)">', response.get_data(as_text=True))
    header = response.headers["Content-Security-Policy"]
    header_nonce = re.search(r"style-src-elem 'self' 'nonce-([^']+)'", header)
    assert page_nonce and header_nonce
    assert page_nonce.group(1) == header_nonce.group(1)
    assert len(page_nonce.group(1)) >= 16
    assert "'unsafe-inline'" not in header.split("style-src-elem", 1)[1].split(";", 1)[0]


def test_500_is_localized_from_the_resolved_locale_tr_and_en(client, f11, make_user, login):
    make_user("f11-taken")
    make_user("f11en", language="en")
    anonymous_tr = client.get("/_f11/plain")
    _assert_generic_500(anonymous_tr, _TR_BODY)
    assert '<html lang="tr">' in anonymous_tr.get_data(as_text=True)

    with client.session_transaction() as sess:
        sess["lang"] = "en"
    anonymous_en = client.get("/_f11/plain")
    _assert_generic_500(anonymous_en, _EN_BODY)
    assert "<title>Server Error</title>" in anonymous_en.get_data(as_text=True)

    with client.session_transaction() as sess:
        sess.pop("lang")
    assert login("f11en").status_code == 200
    f11["reset"]()
    authenticated_en = client.get("/_f11/poisoned-after-commit")
    _assert_generic_500(authenticated_en, _EN_BODY)
    assert '<html lang="en">' in authenticated_en.get_data(as_text=True)
    assert _recovery_sql(f11) == []


def test_500_does_not_mint_a_csrf_session_token(client, f11):
    response = client.get("/_f11/plain")

    _assert_generic_500(response)
    with client.session_transaction() as sess:
        assert "_csrf_token" not in sess


def test_rollback_failure_still_serves_the_500_page(client, f11, authed, monkeypatch, caplog):
    def broken_rollback():
        raise sqlite3.OperationalError("rollback-secret")

    monkeypatch.setattr(db.session, "rollback", broken_rollback)

    response = client.get("/_f11/poisoned")

    _assert_generic_500(response)
    warnings = [r.getMessage() for r in caplog.records if "[F11]" in r.getMessage()]
    assert warnings == ["[F11] 500 recovery: session rollback failed (OperationalError)"]
    assert "rollback-secret" not in " ".join(warnings)


def test_template_failure_falls_back_to_the_static_body(client, f11, authed, app,
                                                        monkeypatch, caplog):
    from jinja2 import TemplateNotFound

    def missing(name, *args, **kwargs):
        raise TemplateNotFound(name)

    monkeypatch.setattr(app.jinja_env, "get_template", missing)

    response = client.get("/_f11/poisoned")

    assert response.status_code == 500
    assert response.mimetype == "text/html"
    assert b"<h1>500</h1>" in response.data
    assert b"Internal Server Error" in response.data
    for marker in _LEAK_MARKERS:
        assert marker not in response.data, marker
    assert "Content-Security-Policy" in response.headers
    assert _recovery_sql(f11) == []
    assert any("static fallback served" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("path", ["/_f11/poisoned", "/_f11/outage"])
def test_next_request_is_not_contaminated_by_the_failed_session(client, f11, authed, path):
    assert client.get(path).status_code == 500
    f11["outage"] = False

    follow_up = client.get("/_f11/count")

    assert follow_up.status_code == 200
    assert follow_up.get_json() == {"users": 2}


def test_original_exception_reaches_observability_once_and_the_log_line_survives(
        client, f11, authed, app, caplog):
    received = []

    def on_exception(sender, exception, **extra):
        received.append(type(exception).__name__)

    got_request_exception.connect(on_exception, app)
    try:
        response = client.get("/_f11/poisoned-after-commit")
    finally:
        got_request_exception.disconnect(on_exception, app)

    _assert_generic_500(response)
    assert received == ["IntegrityError"]
    flask_logged = [r for r in caplog.records
                    if r.getMessage().startswith("Exception on /_f11/poisoned-after-commit")]
    assert len(flask_logged) == 1
    assert flask_logged[0].exc_info[0].__name__ == "IntegrityError"
    request_lines = [r.getMessage() for r in caplog.records
                     if r.getMessage().startswith("request id=")
                     and "path=/_f11/poisoned-after-commit" in r.getMessage()]
    assert len(request_lines) == 1
    assert "status=500" in request_lines[0]
    assert f"user={authed} " in request_lines[0]
