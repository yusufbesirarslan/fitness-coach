"""Regression tests for the 2026-10-01 triage fixes (TRIAGE_FINDINGS_2026-10-01.md).

#1 food-search overload is a 503 (tests/test_food_routes.py, tests/test_ai_gate.py);
#2 private JSON reads carry ``private, no-store``; #3/#4 "latest row" selectors
break ``created_at`` ties by ``id`` everywhere; #5 the referral cookie follows the
session cookie's Secure gate; #7 auth.js survives blocked storage.

    python -m pytest tests/test_triage_2026_10_01_fixes.py -v
"""
import json
import pathlib
import re
import shutil
import subprocess
from datetime import datetime

import pytest

from app.extensions import db
from app.models import TrainingPlan, UserSession
from test_training_execution_boundary import training_page  # noqa: F401

ROOT = pathlib.Path(__file__).resolve().parents[1]
PRIVATE = "private, no-store"


# ---------------------------------------------------------------------------
# #2 — private per-user JSON is never stored by a cache / bfcache
# ---------------------------------------------------------------------------

PRIVATE_GET_PATHS = [
    "/meal-log/today",
    "/api/diary/today",
    "/water",
    "/workout/status",
    "/checkin-history",
    "/api/activity/today",
    "/last-session",
    "/dashboard-nudges",
    "/api/progress/workout",
    "/api/progress/heatmap",
    "/api/progress/achievements",
]


@pytest.mark.parametrize("path", PRIVATE_GET_PATHS)
def test_private_json_read_is_no_store(client, auth_user, path):
    response = client.get(path)

    assert response.status_code == 200
    assert response.headers.get("Cache-Control") == PRIVATE


def test_water_set_answer_is_no_store(client, auth_user):
    response = client.post("/water", json={"count": 2})

    assert response.status_code == 200
    assert response.headers.get("Cache-Control") == PRIVATE


def test_unrelated_pages_keep_their_cache_behavior(client, auth_user):
    for path in ("/nutrition", "/progress-page", "/login"):
        assert client.get(path).headers.get("Cache-Control") != PRIVATE, path


# ---------------------------------------------------------------------------
# #3 — weight write lands in the canonical UserSession on a created_at tie
# ---------------------------------------------------------------------------

def test_weight_update_writes_canonical_session_on_created_at_tie(
        client, make_user, login):
    from app.services.account_profile import canonical_session

    user = make_user(
        "tieweight", height=180, age=30, gender="erkek",
        current_activity="orta", goal="kilo verme", weight=80)
    stamp = datetime(2026, 9, 1, 12, 0, 0)
    older = UserSession(user_id=user.id, name="a", created_at=stamp,
                        target_calories=1000)
    newer = UserSession(user_id=user.id, name="b", created_at=stamp,
                        target_calories=1000)
    db.session.add_all([older, newer])
    db.session.commit()
    assert newer.id > older.id
    login("tieweight")

    response = client.post("/update-weight", json={"weight": 75})

    assert response.status_code == 200
    canonical = canonical_session(user.id)
    assert canonical.id == newer.id
    assert canonical.weight == 75
    assert canonical.target_calories != 1000
    assert db.session.get(UserSession, older.id).target_calories == 1000


# ---------------------------------------------------------------------------
# #4 — the active plan is deterministic on a created_at tie
# ---------------------------------------------------------------------------

def test_active_plan_selectors_agree_on_created_at_tie(app, make_user):
    from app.services.today_facts import get_active_plan
    from app.services.workout_session import queries as session_queries

    user = make_user("tieplan")
    stamp = datetime(2026, 9, 1, 12, 0, 0)
    first = TrainingPlan(user_id=user.id, plan_data=json.dumps({"p": 1}),
                         created_at=stamp)
    second = TrainingPlan(user_id=user.id, plan_data=json.dumps({"p": 2}),
                          created_at=stamp)
    db.session.add_all([first, second])
    db.session.commit()
    assert second.id > first.id

    assert get_active_plan(user.id).id == second.id
    assert session_queries._newest_plan(user.id).id == second.id


# Drift guard: a NEW "latest row" selector without the id tiebreak fails here,
# not in a rare production interleaving.
_SCANNED = [*sorted((ROOT / "app").rglob("*.py")),
            *sorted((ROOT / "fitx_mcp").rglob("*.py"))]


@pytest.mark.parametrize("model", ["UserSession", "TrainingPlan"])
def test_latest_row_selectors_break_ties_by_id(model):
    offenders = []
    for path in _SCANNED:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if f"{model}.created_at.desc()" in line and f"{model}.id.desc()" not in line:
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
    assert offenders == []


def test_raw_sql_latest_session_breaks_ties_by_id():
    offenders = []
    for path in _SCANNED:
        # Join implicit string concatenation so a split SQL literal reads whole.
        sql = re.sub(r'"\s*\n\s*"', "", path.read_text(encoding="utf-8"))
        for match in re.finditer(r"FROM user_session\b[^\"]*", sql):
            statement = match.group(0)
            if ("ORDER BY created_at DESC" in statement
                    and "ORDER BY created_at DESC, id DESC" not in statement):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


# ---------------------------------------------------------------------------
# #5 — referral cookie follows the session cookie's Secure gate
# ---------------------------------------------------------------------------

def _invite_cookie(app, client, make_user, secure):
    from app.services.referral import ensure_referral_code

    app.config["SESSION_COOKIE_SECURE"] = secure
    inviter = make_user(f"inviter{int(secure)}")
    code = ensure_referral_code(inviter)
    db.session.commit()
    response = client.get(f"/davet/{code}")
    return next(c for c in response.headers.getlist("Set-Cookie")
                if c.startswith("fitx_ref="))


def test_referral_cookie_is_secure_outside_dev(app, client, make_user):
    cookie = _invite_cookie(app, client, make_user, secure=True)

    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_referral_cookie_not_secure_in_dev(app, client, make_user):
    cookie = _invite_cookie(app, client, make_user, secure=False)

    assert "Secure" not in cookie


# ---------------------------------------------------------------------------
# #7 — auth.js theme init survives storage that throws
# ---------------------------------------------------------------------------

_AUTH_JS_HARNESS = r"""
const fs = require('fs');
const listeners = {};
const html = { dataset: {} };
global.window = global;
global.document = {
  documentElement: html,
  getElementById: () => null,
  addEventListener: (name, fn) => { listeners[name] = fn; },
};
global.localStorage = {
  getItem() { throw new Error('SecurityError'); },
  setItem() { throw new Error('SecurityError'); },
};
eval(fs.readFileSync(process.argv[1], 'utf8'));
listeners.DOMContentLoaded();
window.toggleTheme();
process.stdout.write(html.dataset.theme);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_auth_js_theme_survives_blocked_storage():
    result = subprocess.run(
        ["node", "-e", _AUTH_JS_HARNESS, str(ROOT / "static" / "auth.js")],
        capture_output=True, text=True, timeout=30, check=False)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "light"  # default dark applied, then toggled


# ---------------------------------------------------------------------------
# #1 (browser) — a refused search says "busy", never "no result", never a
# selectable item (the NUTR-PR4 "search unavailable" invariant still holds)
# ---------------------------------------------------------------------------

def test_search_refusal_shows_busy_status_not_no_result(app, auth_user, training_page):
    from playwright.sync_api import expect
    from test_nutrition_vnext_pr4_log_food_browser import canned, choose, start

    page, traffic, _ = start(app, auth_user, training_page)
    canned(page, '**/api/food/search?q=*', {'error': 'Busy, try again shortly'}, status=503)
    choose(page, 'search')
    page.locator('#food-search-input').fill('oats')

    status = page.locator('#food-autocomplete-dropdown .autocomplete-status')
    expect(status).to_have_text('Busy, try again shortly')
    expect(status).to_have_attribute('role', 'status')
    expect(page.locator('#food-autocomplete-dropdown .autocomplete-item')).to_have_count(0)
