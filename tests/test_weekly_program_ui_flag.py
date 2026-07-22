"""Configuration contract for WEEKLY_PROGRAM_UI_ENABLED (Sprint 6 PR6.1).

The flag is the rollout boundary for the Adaptive Weekly Program UI. It must be
default-OFF, server-controlled, parsed like every other boolean in `app/config.py`,
unreachable from request data, and structurally independent of the PR4 coach flag
`AI_ADAPTIVE_PLAN_CONTEXT`.

Env-default coverage runs in an isolated subprocess (the pattern
`tests/test_adaptive_plan_context.py` established) because `tests/conftest.py` pins
flag values at import time — an in-process assertion would only prove what conftest
set, not what an operator with no `.env` entry gets.

    python -m pytest tests/test_weekly_program_ui_flag.py -v
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest


CONFIG_PATH = Path(__file__).resolve().parents[1] / "app" / "config.py"
CONFIG_SOURCE = CONFIG_PATH.read_text(encoding="utf-8")
FLAG = "WEEKLY_PROGRAM_UI_ENABLED"
COACH_FLAG = "AI_ADAPTIVE_PLAN_CONTEXT"

_PROBE = (
    "import runpy, sys, types\n"
    "dotenv = types.ModuleType('dotenv')\n"
    "dotenv.load_dotenv = lambda *args, **kwargs: None\n"
    "sys.modules['dotenv'] = dotenv\n"
    "namespace = runpy.run_path(sys.argv[1])\n"
    "expected = sys.argv[2] == 'True'\n"
    "actual = namespace[sys.argv[3]]\n"
    "assert actual is expected, '%s is %r, expected %r' % (sys.argv[3], actual, expected)\n"
)


def _config_value(tmp_path, env_value, expected, name=FLAG, extra_env=None):
    """Load app/config.py in a clean interpreter and assert the parsed flag value."""
    env = os.environ.copy()
    env.pop(FLAG, None)
    env.pop(COACH_FLAG, None)
    env.pop("PYTHONPATH", None)
    if env_value is not None:
        env[name] = env_value
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-I", "-c", _PROBE, str(CONFIG_PATH), str(expected), name],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )


# ── default OFF + parsing convention ────────────────────────────────────────────

@pytest.mark.parametrize("env_value", [None, "0", "", "true", "True", "yes", "on", "2"])
def test_flag_is_off_unless_explicitly_one(tmp_path, env_value):
    """Repository convention is a strict `os.getenv(...) == "1"`. Anything else — the
    var missing, empty, or a truthy-looking word — must read as OFF, so a typo can
    never silently ship the UI."""
    completed = _config_value(tmp_path, env_value, expected=False)
    assert completed.returncode == 0, completed.stderr


def test_flag_is_on_only_for_the_exact_string_one(tmp_path):
    completed = _config_value(tmp_path, "1", expected=True)
    assert completed.returncode == 0, completed.stderr


def test_flag_uses_the_repository_boolean_parsing_convention():
    assert 'WEEKLY_PROGRAM_UI_ENABLED = os.getenv("WEEKLY_PROGRAM_UI_ENABLED", "0") == "1"' \
        in CONFIG_SOURCE


def test_flag_reaches_app_config(app):
    assert FLAG in app.config
    assert app.config[FLAG] is False  # hermetic test env leaves it unset → OFF


def test_app_config_mirrors_the_module_constant(app):
    """Closes the middle link of the rollout chain at runtime, not by string match:
    env -> module constant (subprocess tests above) -> app.config (here) -> template
    (tests/test_weekly_program_ui.py). A missing mirror line would make the flag
    unsettable in production while every source-level assertion still passed."""
    import app.config as config_module

    assert app.config[FLAG] is config_module.WEEKLY_PROGRAM_UI_ENABLED
    assert isinstance(app.config[FLAG], bool)


# ── not user-controlled ─────────────────────────────────────────────────────────

def test_flag_is_read_from_the_environment_only():
    """No request-derived source may feed the flag. `app/config.py` is imported once at
    boot and must not touch `request`, headers, args, or cookies."""
    for forbidden in ("request.args", "request.headers", "request.cookies",
                      "request.values", "request.form"):
        assert forbidden not in CONFIG_SOURCE


@pytest.mark.parametrize("attempt", ["query", "header", "cookie"])
def test_request_data_cannot_turn_the_ui_on(client, make_user, login, attempt):
    make_user("flaguser_%s" % attempt, profile_complete=True)
    login("flaguser_%s" % attempt)

    if attempt == "query":
        response = client.get("/training?WEEKLY_PROGRAM_UI_ENABLED=1")
    elif attempt == "header":
        response = client.get("/training",
                              headers={"X-Weekly-Program-Ui-Enabled": "1"})
    else:
        client.set_cookie("WEEKLY_PROGRAM_UI_ENABLED", "1")
        response = client.get("/training")

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'id="weekly-program"' not in html
    assert "data-weekly-program-mount" not in html


# ── independence from the coach flag ────────────────────────────────────────────

def test_the_two_flags_are_distinct_env_names_and_config_keys():
    assert CONFIG_SOURCE.count('os.getenv("WEEKLY_PROGRAM_UI_ENABLED"') == 1
    assert CONFIG_SOURCE.count('os.getenv("AI_ADAPTIVE_PLAN_CONTEXT"') == 1
    assert 'app.config["WEEKLY_PROGRAM_UI_ENABLED"] = WEEKLY_PROGRAM_UI_ENABLED' in CONFIG_SOURCE
    assert 'app.config["AI_ADAPTIVE_PLAN_CONTEXT"] = AI_ADAPTIVE_PLAN_CONTEXT' in CONFIG_SOURCE


def test_ui_flag_is_not_derived_from_the_coach_flag():
    """Pins the definition line itself: the UI flag must not read, alias, or default
    from AI_ADAPTIVE_PLAN_CONTEXT."""
    definition = next(line for line in CONFIG_SOURCE.splitlines()
                      if line.startswith("WEEKLY_PROGRAM_UI_ENABLED ="))
    assert COACH_FLAG not in definition


def test_coach_flag_alone_stays_off_when_only_the_ui_flag_is_set(tmp_path):
    """Setting the UI flag must not switch the coach on."""
    completed = _config_value(tmp_path, None, expected=False, name=COACH_FLAG,
                              extra_env={FLAG: "1"})
    assert completed.returncode == 0, completed.stderr


def test_ui_flag_stays_off_when_only_the_coach_flag_is_set(tmp_path):
    """And the reverse: a coach rollout must not drag the UI shell along."""
    completed = _config_value(tmp_path, None, expected=False, name=FLAG,
                              extra_env={COACH_FLAG: "1"})
    assert completed.returncode == 0, completed.stderr


def test_ui_flag_does_not_appear_in_the_coach_read_path():
    """The coach reads its own flag in ai_coach/context_builder; the UI flag must be
    invisible there, so enabling the UI cannot alter a prompt."""
    root = Path(__file__).resolve().parents[1]
    for module in ("app/services/ai_coach.py", "app/services/context_builder.py",
                   "app/services/prompt_builder.py",
                   "app/services/adaptive_plan_context.py"):
        assert FLAG not in (root / module).read_text(encoding="utf-8"), module
