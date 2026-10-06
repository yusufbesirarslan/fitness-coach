"""TI-07: the ``[WORKOUT_STATE] anomaly`` operational log carries no owner identity.

TI-05/TI-06 found the canonical workout-state service still emitting
``user_id`` on every anomaly line. The repair removes it (never hashes or
replaces it): request id is the only correlation, ``category`` is the closed
anomaly vocabulary and ``detail`` is an exception class name or ``-``.

``resolve_workout_state(user_id, ...)`` still needs the owner for its scoped
reads — only the log boundary is forbidden from receiving or emitting it.

Three layers:

* runtime — every anomaly category is induced through the real
  ``GET /workout/status`` read for an unmistakable synthetic owner, and each
  captured line must carry none of the owner's identities AND match the exact
  safe shape;
* integrated — one capture spanning a real native lifecycle plus a workout-state
  anomaly proves ``[WORKOUT_SESSION]``, ``[WORKOUT_COMPLETION]`` and
  ``[WORKOUT_STATE]`` are all identity-free together;
* source — ``_log_anomaly`` takes only the two labels it emits, its format
  string is pinned, and every call site passes a closed category.
"""
import ast
import inspect
import logging
import re
import textwrap
from datetime import datetime

import app.services.workout_session as session_package
from app.extensions import db
from app.models import PumpCheck
from app.services import workout_state as ws
from app.services.workout_completion import service as completion_service
from app.services.workout_state import models as m
from app.services.workout_state import queries as ws_queries
from app.services.workout_session import start_session
from app.timeutil import app_today, audit_clock
from tests.test_ti03_api import (  # noqa: F401
    THURSDAY_ONE, _CompletionClock, _plan, _workout, as_mobile, completion_proof,
    flags,
)
from tests.test_workout_session import _save_plan
from tests.test_workout_state import _add_pump, _add_workout

# Distinctive values so a leak of any of them is unambiguous in a log line.
OWNER_ID = 1987654307
OWNER_NAME = "ti07privacyowner"

CATEGORIES = (
    m.ANOMALY_SCHEDULE_UNPARSEABLE, m.ANOMALY_COMPLETION_MARKER_MISMATCH,
    m.ANOMALY_RESOLUTION_ERROR, m.ANOMALY_SESSION_INCONSISTENT,
    m.ANOMALY_SESSION_READ_ERROR,
)
RID = r"(?:[0-9a-f]{16}|-)"
# ``detail`` is ``type(exc).__name__`` or ``-``: a bare identifier, never text.
DETAIL = r"(?:-|[A-Za-z_][A-Za-z0-9_]*)"
STATE_LINE = re.compile(
    rf"\[WORKOUT_STATE\] anomaly rid=(?P<rid>{RID}) "
    rf"category=(?P<category>{'|'.join(CATEGORIES)}) detail=(?P<detail>{DETAIL})")
FAMILIES = ("[WORKOUT_STATE]", "[WORKOUT_SESSION]", "[WORKOUT_COMPLETION]")


class OwnerScopedReadError(RuntimeError):
    """A read failure whose *message* names the owner — only the class may log."""


def _identities(owner):
    identities = {str(owner.id), owner.username, owner.email, owner.cognito_sub}
    assert all(identities), identities
    return identities


def _leaky_failure(owner):
    message = f"read failed for {owner.email} sub={owner.cognito_sub} id={owner.id}"

    def _raise(*_args, **_kwargs):
        raise OwnerScopedReadError(message)
    return _raise


def _family_records(caplog, families=FAMILIES):
    return [record for record in caplog.records
            if record.getMessage().startswith(families)]


def _assert_identity_free(records, identities):
    # Identity first, so a leak under any field name — or none — fails on the
    # identity itself rather than on the literal ``user_id`` label.
    for record in records:
        message = record.getMessage()
        assert "user_id" not in message, message
        rendered_args = repr(record.args)
        for identity in identities:
            assert identity not in message, (identity, message)
            assert identity not in rendered_args, (identity, rendered_args)


def _status(client):
    response = client.get("/workout/status")
    assert response.status_code == 200, response.get_json()
    return response


def test_every_workout_state_anomaly_is_logged_without_owner_identity(
        app, client, make_user, login, monkeypatch, caplog):
    owner = make_user(OWNER_NAME, id=OWNER_ID)
    assert owner.id == OWNER_ID
    identities = _identities(owner)
    login(OWNER_NAME)
    now = datetime.utcnow()
    caplog.set_level(logging.INFO)
    caplog.clear()

    # 1. schedule_unparseable — a real malformed plan row.
    malformed = _save_plan(owner.id, raw="{not json")
    _status(client)
    db.session.delete(malformed)
    db.session.commit()

    # 2. completion_marker_mismatch — a marker row today with no PumpCheck.
    _save_plan(owner.id)
    _add_workout(owner.id, when=now, marker=True)
    _status(client)

    # 3. resolution_error — the owner-scoped read raises; its message names the
    #    owner, so only the exception class may reach the log.
    with monkeypatch.context() as patch:
        patch.setattr(ws_queries, "fetch_workout_entries", _leaky_failure(owner))
        state = _status(client).get_json()["state"]
        assert state["primary_state"] == m.PRIMARY_NEEDS_ATTENTION

    # 4. session_read_error — sessions enabled, the session read raises.
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True
    try:
        with monkeypatch.context() as patch:
            patch.setattr(session_package, "read_session_for_state",
                          _leaky_failure(owner))
            assert _status(client).get_json()["state"]["contract_version"] == 2

        # 5. session_lifecycle_inconsistent — an ACTIVE session on a day that
        #    already holds a confirmed PumpCheck completion (marker kept, so the
        #    base snapshot itself is consistent).
        start_session(owner.id)
        _add_pump(owner.id, when=now)
        _status(client)
    finally:
        app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = False

    state_records = _family_records(caplog, ("[WORKOUT_STATE]",))
    _assert_identity_free(_family_records(caplog), identities)

    # Shape: exactly the safe fields, every value from a closed vocabulary.
    matches = []
    for record in state_records:
        match = STATE_LINE.fullmatch(record.getMessage())
        assert match, record.getMessage()
        matches.append(match)
    seen = {match["category"] for match in matches}
    # Non-vacuous: every category was really induced, not an empty capture.
    assert seen == set(CATEGORIES), seen
    details = {match["category"]: set() for match in matches}
    for match in matches:
        details[match["category"]].add(match["detail"])
    assert details[m.ANOMALY_RESOLUTION_ERROR] == {"OwnerScopedReadError"}
    assert details[m.ANOMALY_SESSION_READ_ERROR] == {"OwnerScopedReadError"}
    for category in (m.ANOMALY_SCHEDULE_UNPARSEABLE,
                     m.ANOMALY_COMPLETION_MARKER_MISMATCH,
                     m.ANOMALY_SESSION_INCONSISTENT):
        assert details[category] == {"-"}, (category, details[category])
    # Request correlation is preserved: every line carries a real request id.
    assert all(match["rid"] != "-" for match in matches), [m_.string for m_ in matches]
    # The owner still scopes the reads: today's state really is this owner's.
    assert PumpCheck.query.filter_by(user_id=owner.id).count() == 1


def test_lifecycle_and_state_logs_are_identity_free_together(
        client, app, make_user, flags, as_mobile, completion_proof, monkeypatch, caplog):
    """The integrated TI-05 privacy contract: all three workout log families in
    one capture, none carrying the owner."""
    owner = make_user(OWNER_NAME, id=OWNER_ID)
    identities = _identities(owner)
    monkeypatch.setattr(completion_service, "datetime", _CompletionClock)
    _plan(owner.id)
    caplog.set_level(logging.INFO)
    caplog.clear()

    _workout(client, app, owner, as_mobile, THURSDAY_ONE, [8, 8, 8], "ti07-privacy-key")
    # A strict canonical read on the native Today surface: the anomaly is logged
    # before the read fails closed.
    with monkeypatch.context() as patch, audit_clock(THURSDAY_ONE):
        patch.setattr(ws_queries, "fetch_workout_entries", _leaky_failure(owner))
        response = client.get("/api/v1/today", headers=as_mobile(owner))
    assert response.status_code == 503, response.get_json()

    records = _family_records(caplog)
    _assert_identity_free(records, identities)
    prefixes = {record.getMessage().split(" ", 1)[0] for record in records}
    assert prefixes == set(FAMILIES), prefixes
    state_lines = [record.getMessage() for record in records
                   if record.getMessage().startswith("[WORKOUT_STATE]")]
    assert state_lines and all(STATE_LINE.fullmatch(line) for line in state_lines), \
        state_lines


# ── Source guard: the log boundary only ──────────────────────────────────────
def _function(name):
    tree = ast.parse(textwrap.dedent(inspect.getsource(ws)))
    return tree, next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef) and node.name == name)


def test_workout_state_log_helper_cannot_reach_identity():
    tree, function = _function("_log_anomaly")
    # Narrowed from (user_id, category, detail): the helper receives only what
    # it emits. The orchestrator keeps user_id for its owner-scoped reads.
    assert [arg.arg for arg in function.args.args] == ["category", "detail"]
    assert not (function.args.posonlyargs or function.args.kwonlyargs
                or function.args.vararg or function.args.kwarg)
    calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "warning"]
    assert len(calls) == 1
    (call,) = calls
    assert call.args[0].value == (
        "[WORKOUT_STATE] anomaly rid=%s category=%s detail=%s")
    assert [ast.unparse(arg) for arg in call.args[1:]] == [
        "current_request_id()", "category", "detail or '-'"]
    assert not call.keywords
    # Nothing else in the helper can pull an owner in from ambient state.
    names = {node.id for node in ast.walk(function) if isinstance(node, ast.Name)}
    assert names <= {"current_app", "current_request_id", "category", "detail",
                     "Exception", "Optional", "str", "None"}, names

    category_names = {name for name in vars(m) if name.startswith("ANOMALY_")}
    assert {getattr(m, name) for name in category_names} == set(CATEGORIES)
    sites = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "_log_anomaly"]
    assert len(sites) == 4
    for site in sites:
        assert len(site.args) == 2 and not site.keywords, ast.unparse(site)
        category, detail = site.args
        # A closed constant, or the resolver's own snapshot anomaly field
        # (itself only ever one of the closed constants).
        assert (
            (isinstance(category, ast.Name) and category.id in category_names)
            or ast.unparse(category) in {"snapshot.anomaly", "enriched.anomaly"}
        ), ast.unparse(site)
        assert ast.unparse(detail) in {"None", "type(exc).__name__"}, ast.unparse(site)


def test_resolver_anomalies_are_the_closed_categories():
    # snapshot.anomaly / enriched.anomaly are assigned only from the constants.
    from app.services.workout_state import resolver
    tree = ast.parse(inspect.getsource(resolver))
    returned = {node.value.id for node in ast.walk(tree)
                if isinstance(node, ast.Return) and isinstance(node.value, ast.Name)
                and node.value.id.startswith("ANOMALY_")}
    assigned = {node.value.id for node in ast.walk(tree)
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name)
                and node.value.id.startswith("ANOMALY_")}
    used = returned | assigned
    assert used and {getattr(m, name) for name in used} <= set(CATEGORIES), used


def test_logging_failure_never_breaks_a_workout_state_read(app, make_user, monkeypatch):
    def _explode(*_args, **_kwargs):
        raise RuntimeError("log sink down")

    owner = make_user(OWNER_NAME, id=OWNER_ID)
    _save_plan(owner.id, raw="{not json")
    with app.test_request_context():
        monkeypatch.setattr(app.logger, "warning", _explode)
        ws._log_anomaly(m.ANOMALY_RESOLUTION_ERROR, "OperationalError")
        snapshot = ws.resolve_workout_state(owner.id, today=app_today())
        monkeypatch.setattr(ws_queries, "fetch_workout_entries", _leaky_failure(owner))
        failed = ws.resolve_workout_state(owner.id, today=app_today(),
                                          sessions_enabled=True)
    assert snapshot.anomaly == m.ANOMALY_SCHEDULE_UNPARSEABLE
    assert failed.anomaly == m.ANOMALY_RESOLUTION_ERROR
    assert failed.primary_state == m.PRIMARY_NEEDS_ATTENTION
