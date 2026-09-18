"""WEB-UX3-PR6B: retire UIUX_PLAN_V2_ENABLED from the active flag inventory.

GET /training always renders Plan. The Plan rollout flag itself is gone from
ROLLOUT_FLAGS, FEATURE_FLAG_KEYS, health, boot inventory, and .env.example.
A leftover production env key is ignored. WEEKLY_PROGRAM_UI_ENABLED no longer
depends on it. Today/Nav historical no-ops are out of scope.
"""
import ast
import os
import re
from pathlib import Path

import pytest

from app import feature_flags
from app.config import FEATURE_FLAG_KEYS, feature_flag_state
from app.feature_flags import ROLLOUT_FLAGS, resolve_rollout_flags


REPO_ROOT = Path(__file__).resolve().parents[1]
RETIRED_KEY = "UIUX_PLAN_V2_ENABLED"
WEEKLY_KEY = "WEEKLY_PROGRAM_UI_ENABLED"
TRAINING_BLUEPRINT = REPO_ROOT / "app" / "blueprints" / "training.py"


def _seed_login(client, make_user, login, username="planretire"):
    make_user(username, profile_complete=True)
    login(username)


def _create_app():
    from app import create_app

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    return flask_app


def _training_view_ast():
    tree = ast.parse(TRAINING_BLUEPRINT.read_text(encoding="utf-8"),
                     filename=str(TRAINING_BLUEPRINT))
    views = [node for node in ast.walk(tree)
             if isinstance(node, ast.FunctionDef) and node.name == "training"]
    assert len(views) == 1
    return views[0]


# ── C. Absent from active inventory ────────────────────────────────────────
def test_retired_plan_flag_is_absent_from_rollout_inventory():
    keys = tuple(flag.key for flag in ROLLOUT_FLAGS)
    assert RETIRED_KEY not in keys
    assert RETIRED_KEY not in feature_flags.FEATURE_FLAG_KEYS
    assert RETIRED_KEY not in feature_flags.FLAGS_BY_KEY
    assert RETIRED_KEY not in FEATURE_FLAG_KEYS
    assert RETIRED_KEY not in feature_flags.OPERATIONAL_BOOLEAN_KEYS


def test_today_and_nav_noops_were_not_retired_here():
    """PR6B retires exactly one flag. Today/Nav remaining is separate debt."""
    assert "UIUX_TODAY_V2_ENABLED" in feature_flags.FEATURE_FLAG_KEYS
    assert "UIUX_NAV_V2_ENABLED" in feature_flags.FEATURE_FLAG_KEYS
    assert WEEKLY_KEY in feature_flags.FEATURE_FLAG_KEYS


# ── D. Weekly program dependency ───────────────────────────────────────────
def test_weekly_program_flag_does_not_depend_on_retired_plan_flag():
    weekly = feature_flags.FLAGS_BY_KEY[WEEKLY_KEY]
    assert RETIRED_KEY not in weekly.depends_on
    assert weekly.default is False
    assert weekly.lifecycle == feature_flags.LIFECYCLE_SHIPPED_DARK
    assert weekly.decision == feature_flags.DECISION_ENABLE


# ── A + B. Unconditional Plan renderer ─────────────────────────────────────
@pytest.mark.parametrize("flag", [None, False, True, 0, 1, "0", "1"])
def test_training_always_renders_plan_regardless_of_retired_config(
        app, client, make_user, login, flag):
    if flag is None:
        app.config.pop(RETIRED_KEY, None)
    else:
        app.config[RETIRED_KEY] = flag
    _seed_login(client, make_user, login, username=f"planretire-{flag}")
    response = client.get("/training")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "data-plan-v2" in html
    assert 'data-plan-state=' in html
    assert "/static/training.js" not in html
    assert "/static/training.css" not in html
    assert 'id="setup-form"' not in html


def test_training_route_has_no_plan_flag_selector():
    source = ast.unparse(_training_view_ast())
    assert RETIRED_KEY not in source
    assert "training.html" not in source
    assert "plan.html" in source
    assert "WEEKLY_PROGRAM_UI_ENABLED" in source


def test_legacy_training_renderer_files_are_absent():
    assert not (REPO_ROOT / "templates" / "training.html").exists()
    assert not (REPO_ROOT / "static" / "training.css").exists()
    assert not (REPO_ROOT / "static" / "training.js").exists()


def test_active_python_does_not_reference_deleted_training_renderer():
    """A restored route selector that names training.html must fail here."""
    scanned = list((REPO_ROOT / "app").rglob("*.py"))
    scanned.append(REPO_ROOT / "starter.py")
    offenders = []
    for path in scanned:
        text = path.read_text(encoding="utf-8")
        if 'render_template("training.html"' in text or "render_template('training.html'" in text:
            offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert offenders == []


def test_plan_frontend_audit_treats_retired_flag_values_as_inert():
    """The browser matrix must expect Plan for both stale config values."""
    from scripts.frontend_audit import plan_coach_pr3_matrix as audit

    cell = {
        "flags": {"plan": False, "weekly": False},
        "expect_state": "active_plan",
    }
    measurement = {
        "doc_horizontal_overflow": False,
        "h1_count": 1,
        "coach_root_count": 1,
        "raw_key_leak": [],
        "plan_present": True,
        "legacy_training_js": 0,
        "plan_mount_overflow": False,
        "status_text_present": True,
        "self_link_to_training": False,
        "plan_state": "active_plan",
        "create_form_present": 0,
        "retry_present": 0,
        "weekly_mount_count": 0,
        "weekly_js_count": 0,
    }

    verdict, reasons = audit._evaluate_plan(cell, measurement, weekly_reqs=0)

    assert verdict == "pass", reasons


def test_workout_frontend_audit_reads_only_canonical_shared_assets():
    """The runnable audit must not require the deleted legacy bundle."""
    from scripts.frontend_audit import workout_pr4_matrix as audit

    reader = getattr(audit, "_read_workout_authority_sources", None)
    assert reader is not None, "audit has no canonical authority-source reader"
    source = reader()

    assert "WorkoutStateClient" in source
    assert "fitx_workout_completed_" not in source


# ── E. .env.example ────────────────────────────────────────────────────────
def test_env_example_has_no_active_plan_flag_entry():
    source = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    pattern = re.compile(r"^\s*#?\s*(?:export\s+)?UIUX_PLAN_V2_ENABLED=")
    offending = [line for line in source.splitlines() if pattern.match(line)]
    assert offending == []


# ── F. Current operator docs ───────────────────────────────────────────────
def test_current_rollout_docs_do_not_instruct_flipping_plan_v2():
    rollout = (REPO_ROOT / "docs" / "ROLLOUT.md").read_text(encoding="utf-8")
    flags_doc = (REPO_ROOT / "docs" / "FEATURE_FLAGS.md").read_text(encoding="utf-8")

    assert re.search(r"^\| \d+ \| `UIUX_PLAN_V2_ENABLED`", flags_doc, re.M) is None
    assert re.search(r"^\| \d+ \| `UIUX_PLAN_V2_ENABLED`", rollout, re.M) is None
    assert "set UIUX_PLAN_V2_ENABLED=0" not in rollout
    assert "set UIUX_PLAN_V2_ENABLED=0" not in flags_doc
    assert "UIUX_PLAN_V2_ENABLED|" not in rollout
    grep_lines = [line for line in rollout.splitlines()
                  if "grep -E" in line and "WEEKLY_PROGRAM_UI_ENABLED" in line]
    assert grep_lines, "expected the live-flags host grep in ROLLOUT.md"
    assert all(RETIRED_KEY not in line for line in grep_lines)


def test_current_docs_describe_unconditional_plan_and_git_revert_rollback():
    rollout = (REPO_ROOT / "docs" / "ROLLOUT.md").read_text(encoding="utf-8")
    flags_doc = (REPO_ROOT / "docs" / "FEATURE_FLAGS.md").read_text(encoding="utf-8")
    plan_doc = (REPO_ROOT / "docs" / "PLAN_DOMAIN_CONVERGENCE.md").read_text(
        encoding="utf-8")
    for source in (rollout, flags_doc, plan_doc):
        assert "git revert" in source
        assert "always" in source.lower() or "unconditional" in source.lower()


# ── C + G. Health / boot residue ───────────────────────────────────────────
def test_deep_health_omits_retired_plan_flag(client):
    body = client.get(
        "/health?deep=1",
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    ).get_json()
    assert RETIRED_KEY not in body["flags"]
    assert set(body["flags"]) == set(FEATURE_FLAG_KEYS)


def test_boot_ignores_stale_production_plan_flag_env(monkeypatch):
    monkeypatch.setenv(RETIRED_KEY, "1")
    flask_app = _create_app()
    state = feature_flag_state(flask_app)
    assert RETIRED_KEY not in state
    assert RETIRED_KEY not in resolve_rollout_flags(os.environ)
    assert WEEKLY_KEY in state
    assert state[WEEKLY_KEY] is False


def test_resolver_does_not_parse_retired_plan_env_even_when_malformed():
    """Unknown leftover keys must not fail boot, even if they look malformed."""
    resolved = resolve_rollout_flags({RETIRED_KEY: "true", WEEKLY_KEY: "1"})
    assert RETIRED_KEY not in resolved
    assert resolved[WEEKLY_KEY] is True
