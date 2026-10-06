"""TI-06: the workout lifecycle operational logs carry no owner identity.

TI-05 found ``[WORKOUT_SESSION]`` and ``[WORKOUT_COMPLETION]`` emitting
``user_id``. The repair removes it (never hashes or replaces it): request id is
the only correlation, every other field is a closed server vocabulary.

Two layers, both scoped to exactly these two log families:

* runtime — a real native lifecycle (start → checkpoint → complete, a refused
  re-start, start → resume → abandon) is captured and every emitted line must
  carry none of the owner's concrete identities AND match the exact safe shape;
* source — each ``_log`` helper takes only the bounded labels it emits, its
  format string is pinned, and every call site passes a literal vocabulary.
"""
import ast
import inspect
import logging
import re
import textwrap

from app.services.workout_completion import service as completion_service
from app.services.workout_session import service as session_service
from app.timeutil import audit_clock
from tests.test_ti03_api import (  # noqa: F401
    THURSDAY_ONE, THURSDAY_TWO, _CompletionClock, _plan, _workout, as_mobile,
    completion_proof, flags,
)

SESSIONS = "/api/v1/training/workout-sessions"
# Distinctive values so a leak of any of them is unambiguous in a log line.
OWNER_ID = 1987654321
OWNER_NAME = "ti06privacyowner"

SESSION_EVENTS = (
    "start_refused_completed_today", "start_conflict", "start_integrity_error",
    "started", "resume_stale", "resumed", "abandoned", "complete_conflict",
    "completed", "already_completed",
)
COMPLETION_OUTCOMES = (
    "session_abandoned_conflict", "session_revision_conflict",
    "already_completed_preflight_session_reconciled", "already_completed_preflight",
    "already_completed_race", "integrity_error", "rollback", "created",
)
ENTRY_PATHS = ("route", "ai_tool", "mobile_route", "session_service", "unknown")
RID = r"(?:[0-9a-f]{16}|-)"

SESSION_LINE = re.compile(
    rf"\[WORKOUT_SESSION\] rid={RID} event=(?P<event>{'|'.join(SESSION_EVENTS)})")
COMPLETION_LINE = re.compile(
    rf"\[WORKOUT_COMPLETION\] rid={RID} op=complete_workout "
    rf"entry=(?P<entry>{'|'.join(ENTRY_PATHS)}) "
    rf"outcome=(?P<outcome>{'|'.join(COMPLETION_OUTCOMES)})")


def _family_records(caplog):
    return [record for record in caplog.records
            if record.getMessage().startswith(("[WORKOUT_SESSION]", "[WORKOUT_COMPLETION]"))]


def test_real_lifecycle_logs_carry_no_owner_identity(
        client, app, make_user, flags, as_mobile, completion_proof, monkeypatch, caplog):
    owner = make_user(OWNER_NAME, id=OWNER_ID)
    identities = {str(owner.id), owner.username, owner.email, owner.cognito_sub}
    monkeypatch.setattr(completion_service, "datetime", _CompletionClock)
    _plan(owner.id)
    caplog.set_level(logging.INFO)
    caplog.clear()

    # Day one: full lifecycle, then a start on the already-completed day.
    _workout(client, app, owner, as_mobile, THURSDAY_ONE, [8, 8, 8], "ti06-privacy-key")
    headers = as_mobile(owner, **{"AxisAI-Workout-Contract": "2"})
    from app.services import mobile_training
    reference = mobile_training.workout_ref(app.config["SECRET_KEY"], owner.id,
                                            "ti03-e2e-lineage", 2, 3)
    with audit_clock(THURSDAY_ONE):
        refused = client.post(SESSIONS, headers=headers, json={"workout_ref": reference})
    assert refused.status_code != 201, refused.get_json()
    # Day two: start → resume → abandon.
    with audit_clock(THURSDAY_TWO):
        started = client.post(SESSIONS, headers=headers, json={"workout_ref": reference})
        assert started.status_code == 201, started.get_json()
        ref = started.get_json()["session"]["session_ref"]
        assert client.post(f"{SESSIONS}/{ref}/resume", headers=headers,
                           json={}).status_code == 200
        assert client.post(f"{SESSIONS}/{ref}/abandon", headers=headers,
                           json={"expected_revision": 0}).status_code == 200

    records = _family_records(caplog)
    messages = [record.getMessage() for record in records]
    # Identity first, so a leak under any field name fails on the identity itself.
    for record, message in zip(records, messages):
        assert "user_id" not in message, message
        rendered_args = repr(record.args)
        for identity in identities:
            assert identity not in message, (identity, message)
            assert identity not in rendered_args, (identity, rendered_args)

    # Shape: exactly the safe fields, every value from a closed vocabulary.
    session_lines = [m for m in messages if m.startswith("[WORKOUT_SESSION]")]
    completion_lines = [m for m in messages if m.startswith("[WORKOUT_COMPLETION]")]
    for line in session_lines:
        assert SESSION_LINE.fullmatch(line), line
    for line in completion_lines:
        assert COMPLETION_LINE.fullmatch(line), line
    events = [SESSION_LINE.fullmatch(line)["event"] for line in session_lines]
    outcomes = [COMPLETION_LINE.fullmatch(line)["outcome"] for line in completion_lines]
    # Non-vacuous: the real transitions were observed, not an empty capture.
    assert {"started", "completed", "start_refused_completed_today",
            "resumed", "abandoned"} <= set(events), events
    assert "created" in outcomes, outcomes
    # Request correlation is preserved: every line carries a real request id.
    assert all(" rid=-" not in line for line in messages), messages


# ── Source guard: these two helpers only ─────────────────────────────────────
def _function(module, name):
    tree = ast.parse(textwrap.dedent(inspect.getsource(module)))
    return tree, next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef) and node.name == name)


def _logger_call(function):
    calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "info"]
    assert len(calls) == 1
    return calls[0]


def _log_sites(tree):
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == "_log"]


def test_workout_session_log_helper_cannot_reach_identity():
    tree, function = _function(session_service, "_log")
    assert [arg.arg for arg in function.args.args] == ["event"]
    call = _logger_call(function)
    assert call.args[0].value == "[WORKOUT_SESSION] rid=%s event=%s"
    assert ast.unparse(call.args[1]) == "current_request_id()"
    assert [ast.unparse(arg) for arg in call.args[2:]] == ["event"]
    assert not call.keywords

    sites = _log_sites(tree)
    assert sites
    for site in sites:
        assert len(site.args) == 1 and not site.keywords, ast.unparse(site)
        (arg,) = site.args
        literals = ([arg.body, arg.orelse] if isinstance(arg, ast.IfExp) else [arg])
        for literal in literals:
            assert isinstance(literal, ast.Constant), ast.unparse(site)
            assert literal.value in SESSION_EVENTS, literal.value


def test_workout_completion_log_helper_cannot_reach_identity():
    tree, function = _function(completion_service, "_log")
    # Narrowed from the whole command (which carries the owner) to two labels.
    assert [arg.arg for arg in function.args.args] == ["entry_path", "outcome"]
    call = _logger_call(function)
    assert call.args[0].value == (
        "[WORKOUT_COMPLETION] rid=%s op=complete_workout entry=%s outcome=%s")
    assert ast.unparse(call.args[1]) == "current_request_id()"
    assert [ast.unparse(arg) for arg in call.args[2:]] == ["entry_path", "outcome"]
    assert not call.keywords

    sites = _log_sites(tree)
    assert sites
    for site in sites:
        assert not site.keywords, ast.unparse(site)
        entry, outcome = site.args
        assert ast.unparse(entry) == "command.entry_path", ast.unparse(site)
        assert isinstance(outcome, ast.Constant), ast.unparse(site)
        assert outcome.value in COMPLETION_OUTCOMES, outcome.value


def _entry_path_values(tree):
    """Every way the repository supplies ``entry_path``: keyword, dict key,
    ``setdefault`` and the command's own default."""
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "entry_path":
            yield node.value
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "entry_path":
                    yield value
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == "setdefault" and node.args
              and isinstance(node.args[0], ast.Constant)
              and node.args[0].value == "entry_path"):
            yield node.args[1]
        elif (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
              and node.target.id == "entry_path" and node.value is not None):
            yield node.value


def test_completion_entry_paths_are_the_closed_labels():
    import pathlib
    root = pathlib.Path(completion_service.__file__).resolve().parents[3]
    found = set()
    for path in root.joinpath("app").rglob("*.py"):
        for value in _entry_path_values(ast.parse(path.read_text(encoding="utf-8"))):
            assert isinstance(value, ast.Constant), (path, ast.unparse(value))
            found.add(value.value)
    assert found == set(ENTRY_PATHS), found


def test_logging_failure_never_breaks_a_transition(app, monkeypatch):
    def _explode(*_args, **_kwargs):
        raise RuntimeError("log sink down")

    with app.test_request_context():
        monkeypatch.setattr(app.logger, "info", _explode)
        session_service._log("started")
        completion_service._log("route", "created")
