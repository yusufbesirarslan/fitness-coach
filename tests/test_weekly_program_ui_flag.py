"""Configuration contract for WEEKLY_PROGRAM_UI_ENABLED (Sprint 6 PR6.1).

The flag is the rollout boundary for the Adaptive Weekly Program UI. It must be
default-OFF, server-controlled, parsed through the shared strict flag parser,
unreachable from request data, and structurally independent of the PR4 coach flag
`AI_ADAPTIVE_PLAN_CONTEXT`.

Env-default coverage runs in an isolated subprocess (the pattern
`tests/test_adaptive_plan_context.py` established) because `tests/conftest.py` pins
flag values at import time — an in-process assertion would only prove what conftest
set, not what an operator with no `.env` entry gets.

The probe used to load `app/config.py` and read a module-level constant. PR2 moved
rollout-flag resolution into `app/feature_flags.py` (stdlib-only, importable on its
own), so the probe now runs the real resolver against the real process environment
— the same chain production boots through, with one fewer indirection.

    python -m pytest tests/test_weekly_program_ui_flag.py -v
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "app" / "config.py"
CONFIG_SOURCE = CONFIG_PATH.read_text(encoding="utf-8")
FLAGS_PATH = REPO_ROOT / "app" / "feature_flags.py"
FLAG = "WEEKLY_PROGRAM_UI_ENABLED"
COACH_FLAG = "AI_ADAPTIVE_PLAN_CONTEXT"

_PROBE = (
    "import os, runpy, sys\n"
    "namespace = runpy.run_path(sys.argv[1])\n"
    "resolved = namespace['resolve_rollout_flags'](os.environ)\n"
    "expected = sys.argv[2] == 'True'\n"
    "actual = resolved[sys.argv[3]]\n"
    "assert actual is expected, '%s is %r, expected %r' % (sys.argv[3], actual, expected)\n"
)


def _resolved_value(tmp_path, env_value, expected, name=FLAG, extra_env=None):
    """Resolve the flag registry in a clean interpreter and assert the parsed value."""
    env = os.environ.copy()
    env.pop(FLAG, None)
    env.pop(COACH_FLAG, None)
    env.pop("PYTHONPATH", None)
    if env_value is not None:
        env[name] = env_value
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-I", "-c", _PROBE, str(FLAGS_PATH), str(expected), name],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )


# ── default OFF + parsing convention ────────────────────────────────────────────

@pytest.mark.parametrize("env_value", [None, "0", ""])
def test_flag_is_off_unless_explicitly_one(tmp_path, env_value):
    """Missing, empty and explicit `0` all mean OFF.

    `KEY=` (empty) is a normal way to write "not set" in a `.env`; keeping it
    permissive is what makes the stricter PR2 parser backward-compatible with
    every value a host accepts today.
    """
    completed = _resolved_value(tmp_path, env_value, expected=False)
    assert completed.returncode == 0, completed.stderr


def test_flag_is_on_only_for_the_exact_string_one(tmp_path):
    completed = _resolved_value(tmp_path, "1", expected=True)
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("env_value", ["true", "True", "yes", "on", "2", "1 "])
def test_a_typo_can_never_silently_ship_the_ui(tmp_path, env_value):
    """The original intent of this test, strengthened by PR2.

    Before PR2 a truthy-looking word read as OFF: the UI stayed dark, which was
    safe, but the operator got no signal that their edit did nothing. `"1 "` was
    the same silent OFF in the opposite direction. Now every one of them refuses
    to boot and names the key. The invariant that matters is unchanged and still
    asserted: none of these values turns the UI on.
    """
    completed = _resolved_value(tmp_path, env_value, expected=True)
    assert completed.returncode != 0, "malformed value must not resolve to ON"
    completed = _resolved_value(tmp_path, env_value, expected=False)
    assert completed.returncode != 0, "malformed value must be refused, not guessed"
    assert FLAG in completed.stderr, "the failure must name the offending key"


def test_flag_is_governed_by_the_shared_strict_parser():
    """PR2 convention: the flag is a registry record, not a module constant.

    A flag re-added the old way (`X = os.getenv("X", "0") == "1"`) would bypass
    the strict parser and read `true` as a silent OFF again.
    """
    from app import feature_flags

    record = feature_flags.FLAGS_BY_KEY[FLAG]
    assert record.default is False
    assert record.parsed_by == feature_flags.PARSED_BY_REGISTRY
    assert f'os.getenv("{FLAG}"' not in CONFIG_SOURCE


def test_flag_reaches_app_config(app):
    assert FLAG in app.config
    assert app.config[FLAG] is False  # hermetic test env leaves it unset → OFF


def test_app_config_mirrors_the_resolved_registry_value(app):
    """Closes the middle link of the rollout chain at runtime, not by string match:
    env -> registry resolver (subprocess tests above) -> app.config (here) -> template
    (tests/test_weekly_program_ui.py). A `configure_app` that forgot to apply the
    resolver would make the flag unsettable in production while every source-level
    assertion still passed."""
    from app.feature_flags import resolve_rollout_flags

    assert app.config[FLAG] is resolve_rollout_flags(os.environ)[FLAG]
    assert isinstance(app.config[FLAG], bool)


# ── not user-controlled ─────────────────────────────────────────────────────────

def test_flag_is_read_from_the_environment_only():
    """No request-derived source may feed the flag. Both the registry and
    `app/config.py` run once at boot and must not touch `request`, headers, args or
    cookies."""
    flags_source = FLAGS_PATH.read_text(encoding="utf-8")
    for forbidden in ("request.args", "request.headers", "request.cookies",
                      "request.values", "request.form"):
        assert forbidden not in CONFIG_SOURCE
        assert forbidden not in flags_source


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

def test_the_two_flags_are_distinct_env_names_and_config_keys(app):
    from app import feature_flags

    assert FLAG != COACH_FLAG
    assert {FLAG, COACH_FLAG} <= set(feature_flags.FEATURE_FLAG_KEYS)
    assert feature_flags.FLAGS_BY_KEY[FLAG] is not feature_flags.FLAGS_BY_KEY[COACH_FLAG]
    assert FLAG in app.config and COACH_FLAG in app.config


def test_ui_flag_is_not_derived_from_the_coach_flag():
    """Pins the record itself: the UI flag must not read, alias, or default from
    AI_ADAPTIVE_PLAN_CONTEXT — and the resolver must read one env key per flag."""
    from app import feature_flags
    from app.feature_flags import resolve_rollout_flags

    assert COACH_FLAG not in feature_flags.FLAGS_BY_KEY[FLAG].depends_on
    assert FLAG not in feature_flags.FLAGS_BY_KEY[COACH_FLAG].depends_on
    assert resolve_rollout_flags({FLAG: "1"})[FLAG] is True
    assert resolve_rollout_flags({COACH_FLAG: "1"})[FLAG] is False


def test_coach_flag_alone_stays_off_when_only_the_ui_flag_is_set(tmp_path):
    """Setting the UI flag must not switch the coach on."""
    completed = _resolved_value(tmp_path, None, expected=False, name=COACH_FLAG,
                                extra_env={FLAG: "1"})
    assert completed.returncode == 0, completed.stderr


def test_ui_flag_stays_off_when_only_the_coach_flag_is_set(tmp_path):
    """And the reverse: a coach rollout must not drag the UI shell along."""
    completed = _resolved_value(tmp_path, None, expected=False, name=FLAG,
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
