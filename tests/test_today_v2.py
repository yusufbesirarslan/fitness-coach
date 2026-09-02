"""AxisAI UIUX Sprint 1 PR2 — Today experience v2 (flag-gated) tests.

Covers both OFF (legacy index.html) and ON (today.html) paths, the pure
presentation contract, the single authoritative primary action, the four
Navigation×Today flag combinations, the canonical endpoint sources
(/training-plan/active selector reused verbatim; /workout/status is owned by the
Sprint 7 canonical workout-state resolver, which Today delegates its completion
read to), the request-count dedup (V2 no longer client-fetches /workout/status),
and template safety. The Today flag is toggled
with app.config["UIUX_TODAY_V2_ENABLED"] (home() reads it at request time).
"""
import json
import re

from app.today_presenter import (
    STATE_ERROR,
    STATE_NO_PLAN,
    STATE_PLAN_READY,
    STATE_WORKOUT_DONE,
    TodayFacts,
    build_today_view,
)


# ── Helpers ──

def _seed_login(client, make_user, login, username="todayuser"):
    user = make_user(username, profile_complete=True)
    login(username)
    return user


def _seed_plan(user_id, plan_data="{}"):
    from app.extensions import db
    from app.models import TrainingPlan
    db.session.add(TrainingPlan(user_id=user_id, plan_data=plan_data))
    db.session.commit()


def _seed_pumpcheck_today(user_id):
    from app.extensions import db
    from app.models import PumpCheck
    db.session.add(PumpCheck(user_id=user_id))  # created_at defaults to now → today
    db.session.commit()


# ══════════════════════════════════════════════════════════════════════════
# A. PURE PRESENTER — contract (no app/DB context; correction #1)
# ══════════════════════════════════════════════════════════════════════════

def test_presenter_maps_every_state():
    cases = {
        STATE_NO_PLAN:      TodayFacts(read_ok=True,  has_active_plan=False, workout_completed_today=False),
        STATE_PLAN_READY:   TodayFacts(read_ok=True,  has_active_plan=True,  workout_completed_today=False),
        STATE_WORKOUT_DONE: TodayFacts(read_ok=True,  has_active_plan=True,  workout_completed_today=True),
        STATE_ERROR:        TodayFacts(read_ok=False, has_active_plan=False, workout_completed_today=False),
    }
    for expected, facts in cases.items():
        assert build_today_view(facts).state == expected


def test_presenter_error_is_not_no_plan():
    # A failed canonical read must be an honest error — never "no plan", never
    # a silent populated state.
    v = build_today_view(TodayFacts(read_ok=False, has_active_plan=False, workout_completed_today=False))
    assert v.state == STATE_ERROR
    assert v.state != STATE_NO_PLAN
    assert v.primary is None                      # no dominant CTA on error
    assert v.secondary and v.secondary[0].href == "/training"  # neutral "Open Plan"


def test_presenter_error_ignores_stale_booleans():
    # Even if booleans look "populated", read_ok=False wins → error.
    v = build_today_view(TodayFacts(read_ok=False, has_active_plan=True, workout_completed_today=False))
    assert v.state == STATE_ERROR


def test_presenter_completed_has_no_stale_start():
    v = build_today_view(TodayFacts(read_ok=True, has_active_plan=True, workout_completed_today=True))
    assert v.state == STATE_WORKOUT_DONE
    assert v.primary is None                      # acceptable: no dominant CTA
    labels = [a.label_key for a in v.secondary]
    assert "today.action.view_progress" in labels
    # No secondary claims a "start" action.
    assert not any("start" in lk for lk in labels)


def test_presenter_plan_ready_label_is_neutral():
    v = build_today_view(TodayFacts(read_ok=True, has_active_plan=True, workout_completed_today=False))
    assert v.state == STATE_PLAN_READY
    assert v.primary is not None
    # Neutral canonical label — NOT "today's workout / recommended". plan_ready
    # means only "an active plan exists and completion is false".
    assert v.primary.label_key == "today.action.view_plan"
    assert v.primary.href == "/training"
    assert "today" not in v.primary.label_key.replace("today.action", "")


def test_presenter_no_plan_points_at_canonical_plan_route():
    v = build_today_view(TodayFacts(read_ok=True, has_active_plan=False, workout_completed_today=False))
    assert v.state == STATE_NO_PLAN
    assert v.primary.label_key == "today.action.create_plan"
    assert v.primary.href == "/training"


def test_presenter_at_most_one_primary_action():
    # `primary` is a single Action or None — the type makes "exactly one dominant
    # action" structural, not a count we could get wrong.
    for facts in [
        TodayFacts(True, False, False),
        TodayFacts(True, True, False),
        TodayFacts(True, True, True),
        TodayFacts(False, False, False),
    ]:
        v = build_today_view(facts)
        assert v.primary is None or v.primary.primary is True


def test_presenter_state_ids_are_not_localized():
    # State ids are stable ASCII identifiers, never translated copy.
    for sid in (STATE_NO_PLAN, STATE_PLAN_READY, STATE_WORKOUT_DONE, STATE_ERROR):
        assert re.fullmatch(r"[a-z_]+", sid)


# ══════════════════════════════════════════════════════════════════════════
# B. FLAG BEHAVIOUR — OFF preserves legacy, ON renders V2
# ══════════════════════════════════════════════════════════════════════════

def test_flag_default_off_renders_legacy_today(client, make_user, login):
    _seed_login(client, make_user, login)
    html = client.get("/").get_data(as_text=True)
    assert "data-today-v2" not in html            # V2 mark absent
    assert 'class="dash-grid"' in html            # legacy dashboard grid present


def test_flag_on_renders_today_v2(app, client, make_user, login):
    app.config["UIUX_TODAY_V2_ENABLED"] = True
    _seed_login(client, make_user, login)
    html = client.get("/").get_data(as_text=True)
    assert "data-today-v2" in html
    assert 'class="dash-grid"' not in html        # legacy tree not rendered
    assert 'data-today-state=' in html


def test_missing_flag_fails_safe_to_legacy(app, client, make_user, login):
    app.config.pop("UIUX_TODAY_V2_ENABLED", None)
    _seed_login(client, make_user, login)
    html = client.get("/").get_data(as_text=True)
    assert "data-today-v2" not in html            # safe default: legacy


def test_exactly_one_today_tree_per_flag_state(app, client, make_user, login):
    _seed_login(client, make_user, login)
    off = client.get("/").get_data(as_text=True)
    assert "data-today-v2" not in off and 'class="dash-grid"' in off

    app.config["UIUX_TODAY_V2_ENABLED"] = True
    on = client.get("/").get_data(as_text=True)
    assert "data-today-v2" in on and 'class="dash-grid"' not in on   # never both


# ══════════════════════════════════════════════════════════════════════════
# C. PRIMARY ACTION + STATE SEMANTICS (route level)
# ══════════════════════════════════════════════════════════════════════════

def test_v2_no_plan_state_and_primary(app, client, make_user, login):
    app.config["UIUX_TODAY_V2_ENABLED"] = True
    _seed_login(client, make_user, login)               # no plan seeded
    html = client.get("/").get_data(as_text=True)
    assert 'data-today-state="no_plan"' in html
    assert html.count("data-today-primary") == 1        # exactly one dominant CTA
    assert 'href="/training"' in html


def test_v2_plan_ready_single_primary(app, client, make_user, login):
    app.config["UIUX_TODAY_V2_ENABLED"] = True
    user = _seed_login(client, make_user, login)
    _seed_plan(user.id)
    html = client.get("/").get_data(as_text=True)
    assert 'data-today-state="plan_ready"' in html
    assert html.count("data-today-primary") == 1


def test_v2_workout_done_no_stale_start(app, client, make_user, login):
    app.config["UIUX_TODAY_V2_ENABLED"] = True
    user = _seed_login(client, make_user, login)
    _seed_plan(user.id)
    _seed_pumpcheck_today(user.id)
    html = client.get("/").get_data(as_text=True)
    assert 'data-today-state="workout_done"' in html
    assert "data-today-primary" not in html             # no dominant CTA when done
    assert 'href="/progress-page"' in html              # review kept as secondary


def test_v2_has_exactly_one_h1(app, client, make_user, login):
    app.config["UIUX_TODAY_V2_ENABLED"] = True
    _seed_login(client, make_user, login)
    html = client.get("/").get_data(as_text=True)
    assert len(re.findall(r"<h1\b", html)) == 1


def test_v2_no_raw_localization_keys_leak(app, client, make_user, login):
    app.config["UIUX_TODAY_V2_ENABLED"] = True
    _seed_login(client, make_user, login)
    html = client.get("/").get_data(as_text=True)
    # Translated copy is present, raw dotted keys are not shown as text.
    assert ">today.title<" not in html
    assert ">today.status.no_plan<" not in html


# ══════════════════════════════════════════════════════════════════════════
# D. FLAG MATRIX — 4 Navigation×Today combinations
# ══════════════════════════════════════════════════════════════════════════

def test_four_flag_combinations_render_safely(app, client, make_user, login):
    _seed_login(client, make_user, login)
    for nav_on in (False, True):
        for today_on in (False, True):
            app.config["UIUX_NAV_V2_ENABLED"] = nav_on
            app.config["UIUX_TODAY_V2_ENABLED"] = today_on
            html = client.get("/").get_data(as_text=True)
            # Exactly one Today tree.
            v2 = "data-today-v2" in html
            legacy = 'class="dash-grid"' in html
            assert v2 != legacy, f"nav={nav_on} today={today_on}: XOR Today tree"
            assert v2 is today_on
            # Production chrome is the four-destination shell regardless of
            # UIUX_NAV_V2_ENABLED; Today remains independently flag-gated.
            assert 'data-nav-id="today"' in html


def test_nav_and_today_flags_are_independent(app, client, make_user, login):
    # Today ON does not change production chrome; chrome does not force Today v2.
    _seed_login(client, make_user, login)
    app.config["UIUX_NAV_V2_ENABLED"] = False
    app.config["UIUX_TODAY_V2_ENABLED"] = True
    html = client.get("/").get_data(as_text=True)
    assert "data-today-v2" in html
    assert 'data-nav-id="today"' in html


# ══════════════════════════════════════════════════════════════════════════
# E. CANONICAL ENDPOINT SOURCES (correction #2) — shared, undistorted signals
# ══════════════════════════════════════════════════════════════════════════

def test_workout_status_completion_signal(app, client, make_user, login):
    # /workout/status is owned by the Sprint 7 canonical resolver, which returns
    # an additive `state` object alongside `completed`. Today V2 does NOT modify
    # this route — it delegates its own completion read to the same resolver. So
    # assert the `completed` signal Today relies on still tracks today's PumpCheck
    # (without pinning the additive contract this endpoint is free to grow).
    user = _seed_login(client, make_user, login)
    before = client.get("/workout/status").get_json()
    assert before["completed"] is False
    assert "state" in before  # Sprint 7 additive contract preserved after rebase
    _seed_pumpcheck_today(user.id)
    after = client.get("/workout/status").get_json()
    assert after["completed"] is True


def test_today_completion_matches_workout_status(app, client, make_user, login):
    # The Today read layer must agree with /workout/status because both now read
    # the one canonical owner (resolve_workout_state) — no second query to drift.
    from app.services.today_facts import workout_completed_today
    user = _seed_login(client, make_user, login)
    assert workout_completed_today(user.id) is False
    assert client.get("/workout/status").get_json()["completed"] is False
    _seed_pumpcheck_today(user.id)
    assert workout_completed_today(user.id) is True
    assert client.get("/workout/status").get_json()["completed"] is True


def test_training_plan_active_response_unchanged(app, client, make_user, login):
    user = _seed_login(client, make_user, login)
    assert client.get("/training-plan/active").get_json() == {"exists": False}
    _seed_plan(user.id, plan_data=json.dumps({"days": []}))
    data = client.get("/training-plan/active").get_json()
    assert data["exists"] is True
    assert data["plan"] == {"days": []}
    assert "created_at" in data and "score" in data


def test_today_facts_resolution_error_is_honest_error(app, make_user, monkeypatch):
    # The canonical resolver owns completion; when IT hits an unexpected read
    # failure Today must surface an honest error — never a fabricated state.
    from app.services import today_facts as tf
    from app.services.workout_state import _safe_snapshot
    from app.timeutil import app_today
    make_user("erruser", profile_complete=True)

    # (a) resolver raises unexpectedly → caught by gather_today_facts → error
    def _raise(uid, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(tf, "resolve_workout_state", _raise)
    facts = tf.gather_today_facts(1)
    assert facts.read_ok is False
    assert build_today_view(facts).state == STATE_ERROR

    # (b) resolver fails safe with a resolution_error snapshot → still honest error
    #     (completed_today=False from a failure must NOT read as a real "not done")
    def _failsafe(uid, **kw):
        return _safe_snapshot(app_today())
    monkeypatch.setattr(tf, "resolve_workout_state", _failsafe)
    facts = tf.gather_today_facts(1)
    assert facts.read_ok is False


def test_today_facts_domain_anomaly_is_not_error(app, client, make_user, login):
    # A DOMAIN anomaly (unparseable schedule from an empty plan) is an honest
    # classification, not a read failure: completion is still trustworthy, so
    # Today stays plan_ready — it must NOT collapse to the error state.
    user = _seed_login(client, make_user, login)
    _seed_plan(user.id, plan_data="{}")  # valid row, no 7-day program → schedule_unparseable
    from app.services.today_facts import gather_today_facts
    facts = gather_today_facts(user.id)
    assert facts.read_ok is True
    assert facts.has_active_plan is True
    assert build_today_view(facts).state == STATE_PLAN_READY


# ══════════════════════════════════════════════════════════════════════════
# F. REQUEST-COUNT DEDUP (correction #6) + template safety
# ══════════════════════════════════════════════════════════════════════════

def test_v2_does_not_client_fetch_workout_status(app, client, make_user, login):
    # Completion is read server-side for the primary action, so V2 must NOT
    # re-fetch /workout/status on hydration (no duplicate authoritative read).
    app.config["UIUX_TODAY_V2_ENABLED"] = True
    _seed_login(client, make_user, login)
    html = client.get("/").get_data(as_text=True)
    assert "/workout/status" not in html


def test_legacy_off_path_still_fetches_workout_status(client, make_user, login):
    # The legacy OFF path is unchanged (still hydrates completion client-side).
    _seed_login(client, make_user, login)
    html = client.get("/").get_data(as_text=True)
    assert "/workout/status" in html


def test_new_today_copy_is_axisai_only():
    # Correction #7: no "FitX" in the new product-facing Today copy.
    from app.i18n import _CATALOG
    for loc in ("tr", "en"):
        for key, val in _CATALOG[loc].items():
            if key.startswith("today."):
                assert "fitx" not in val.lower(), f"{loc}:{key}"
