"""AxisAI UX-2 PR4 — Today / Home structural convergence.

PR2 shipped a *second* Today behind `UIUX_TODAY_V2_ENABLED`; PR4 converges Home
on it, deletes the legacy dashboard and retires the selector. This module locks
the model that convergence produces:

* **One production Home.** `/` renders `today.html` at every value of the
  retired flag. There is no second Home tree to drift.
* **One state vocabulary.** Today re-exports the canonical `primary_state` from
  the workout-state contract instead of maintaining a fourth Today dialect, so
  the page, `GET /workout/status` and `GET /api/v1/today` describe one user with
  one word.
* **One dominant action, decided on the server.** The browser computes no next
  action, no time-of-day rule and no completion inference.
* **Honest absence.** A failed read is an error state, never "no plan"; a missing
  value is "—", never 0; an insight nobody published is not rendered at all.
* **Nothing was deleted that the user still needs.** The legacy dashboard's
  modules were removed from Home; their capabilities stay at their canonical
  destinations, and this module asserts each one there.

The PR2 assertions for the flag-selected legacy branch, the four-state Today
dialect and the removed dashboard modules were obsolete layout assertions and
are gone with the layout. Every guard that carried product meaning is kept below.
"""
import json
import re
from pathlib import Path

import pytest

from app.today_presenter import (
    INSIGHT_WATCH,
    INSIGHT_WORKING,
    STATE_COMPLETED,
    STATE_ERROR,
    STATE_IN_PROGRESS,
    STATE_NEEDS_ATTENTION,
    STATE_NO_PLAN,
    STATE_REST_DAY,
    STATE_SCHEDULED,
    STATE_UNSCHEDULED_COMPLETED,
    TODAY_STATES,
    Action,
    TodayFacts,
    TodayPlanSummary,
    build_today_view,
)

ROOT = Path(__file__).resolve().parents[1]


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


def _week(today_kind, today_focus="İtiş", exercises=2):
    """A valid seven-day program whose *today* carries `today_kind`."""
    from app.services.training_generation.response_validator import WEEKDAYS
    from app.timeutil import app_today
    weekday = WEEKDAYS[app_today().weekday()]
    program = []
    for day in WEEKDAYS:
        if day == weekday:
            program.append({
                "gun": day, "tip": today_kind, "odak": today_focus,
                "sure_dk": 45, "tahmini_kalori": 320,
                "egzersizler": [
                    {"isim": "Bench Press %d" % i, "set": 3, "tekrar": "8-12"}
                    for i in range(exercises)
                ],
            })
        else:
            program.append({
                "gun": day, "tip": "dinlenme", "odak": "", "sure_dk": 0,
                "tahmini_kalori": 0, "egzersizler": [],
            })
    return json.dumps(program, ensure_ascii=False)


def _facts(**kw):
    base = dict(read_ok=True, has_active_plan=True, workout_completed_today=False,
                primary_state=STATE_SCHEDULED, action="start")
    base.update(kw)
    return TodayFacts(**base)


def _html(client, route="/"):
    resp = client.get(route)
    assert resp.status_code == 200, (route, resp.status_code)
    return resp.get_data(as_text=True)


# ══════════════════════════════════════════════════════════════════════════
# A. ONE STATE VOCABULARY — the canonical contract, not a Today dialect
# ══════════════════════════════════════════════════════════════════════════

def test_today_states_are_the_canonical_primary_states():
    """The point of the PR4 presenter rewrite: Today stopped inventing
    `plan_ready` / `workout_done` and re-exports the workout-state contract, so
    a state added upstream cannot silently become an unhandled Today state."""
    from app.services.workout_state.models import (
        PRIMARY_COMPLETED, PRIMARY_EXECUTION_RECORDED, PRIMARY_IN_PROGRESS,
        PRIMARY_NEEDS_ATTENTION, PRIMARY_NO_PLAN, PRIMARY_REST_DAY,
        PRIMARY_SCHEDULED_NOT_STARTED, PRIMARY_UNSCHEDULED_COMPLETED,
        PRIMARY_UNSCHEDULED_EXECUTION)

    canonical = {
        PRIMARY_REST_DAY, PRIMARY_SCHEDULED_NOT_STARTED,
        PRIMARY_EXECUTION_RECORDED, PRIMARY_COMPLETED,
        PRIMARY_UNSCHEDULED_EXECUTION, PRIMARY_UNSCHEDULED_COMPLETED,
        PRIMARY_NO_PLAN, PRIMARY_NEEDS_ATTENTION, PRIMARY_IN_PROGRESS,
    }
    # `error` is deliberately outside the canonical enum: it is not a statement
    # about the user, it is the absence of one.
    assert set(TODAY_STATES) == canonical | {STATE_ERROR}
    assert STATE_ERROR not in canonical


def test_state_ids_are_identifiers_not_localized_copy():
    for sid in TODAY_STATES:
        assert re.fullmatch(r"[a-z_]+", sid), sid


def test_an_unknown_canonical_state_fails_to_error_not_to_a_guess():
    """Contract drift must surface as unavailability. Mapping an unrecognised
    state onto the nearest familiar one would render a confident screen built on
    a state this build does not understand."""
    view = build_today_view(_facts(primary_state="some_future_state"))
    assert view.state == STATE_ERROR
    assert view.primary is None


# ══════════════════════════════════════════════════════════════════════════
# B. ONE DOMINANT ACTION — driven by the canonical action dimension
# ══════════════════════════════════════════════════════════════════════════

def test_primary_action_follows_the_canonical_action_dimension():
    start = build_today_view(_facts(action="start")).primary
    assert start.label_key == "today.action.start_workout"
    assert start.href == "/training" and start.primary is True

    resume = build_today_view(
        _facts(primary_state=STATE_IN_PROGRESS, action="resume")).primary
    assert resume.label_key == "today.action.resume_workout"


def test_presenter_maps_the_ranked_semantic_action_to_copy_and_route():
    """Dropping the decision-to-presentation mapping must break visible output."""
    from app.services.workout_state.models import ACTION_RESUME

    view = build_today_view(_facts(
        primary_state=STATE_IN_PROGRESS, action=ACTION_RESUME))

    assert view.primary == Action(
        "today.action.resume_workout", "/training", primary=True)
    assert view.brief_key == "today.brief.in_progress"


def test_presenter_fails_closed_on_an_incompatible_canonical_pair():
    """A terminal state carrying Start must never leak a start CTA to Home."""
    from app.services.workout_state.models import ACTION_START

    view = build_today_view(_facts(
        primary_state=STATE_COMPLETED, action=ACTION_START))

    assert view.state == STATE_ERROR
    assert view.primary is None
    assert view.brief_key == "today.brief.error"
    assert view.secondary


def test_no_plan_offers_the_one_honest_next_step():
    view = build_today_view(_facts(primary_state=STATE_NO_PLAN, action="none",
                                   has_active_plan=False))
    assert view.state == STATE_NO_PLAN
    assert view.primary.label_key == "today.action.create_plan"
    assert view.primary.href == "/training"


@pytest.mark.parametrize("state", [
    STATE_REST_DAY, STATE_COMPLETED, STATE_UNSCHEDULED_COMPLETED,
    STATE_NEEDS_ATTENTION,
])
def test_settled_and_blocked_states_render_no_dominant_cta(state):
    """A completed day must never carry a "Start" and a rest day must never be
    handed a workout — the canonical `action` says `none`/`blocked` and the
    presenter adds nothing on top of it."""
    view = build_today_view(_facts(primary_state=state, action="none"))
    assert view.primary is None
    assert not any("start" in a.label_key or "resume" in a.label_key
                   for a in view.secondary)


def test_at_most_one_primary_action_is_structural():
    """`primary` is a single Action or None, so "exactly one dominant action" is
    a type guarantee rather than a count the template could get wrong."""
    for state in TODAY_STATES:
        for action in ("start", "resume", "none", "blocked"):
            view = build_today_view(_facts(primary_state=state, action=action))
            assert view.primary is None or isinstance(view.primary, Action)
            assert view.primary is None or view.primary.primary is True


def test_no_state_is_a_dead_end_and_none_competes_with_the_primary():
    """Two structural rules, checked over the whole matrix rather than a
    hand-maintained per-state list: a view with a dominant CTA carries no
    subordinate links (nothing competes with it), and a view without one always
    offers at least the neutral fallback (no screen is a dead end). The blocked
    scheduled/in-progress combinations only a flag-on v2 snapshot can produce are
    included, because those are exactly the ones a per-state table forgets."""
    for state in TODAY_STATES:
        for action in ("start", "resume", "none", "blocked", ""):
            view = build_today_view(_facts(primary_state=state, action=action))
            assert view.brief_key
            if view.primary is not None:
                assert view.secondary == (), (state, action)
            else:
                assert view.secondary, (state, action)


def test_every_action_points_at_an_existing_canonical_route():
    routes = {"/training", "/progress-page"}
    for state in TODAY_STATES:
        for action in ("start", "resume", "none"):
            view = build_today_view(_facts(primary_state=state, action=action))
            items = list(view.secondary) + ([view.primary] if view.primary else [])
            for item in items:
                assert item.href in routes, (state, item.href)


# ══════════════════════════════════════════════════════════════════════════
# C. HONEST FAILURE — an unreadable Today is not an empty one
# ══════════════════════════════════════════════════════════════════════════

def test_read_failure_is_an_error_state_not_no_plan():
    view = build_today_view(TodayFacts(
        read_ok=False, has_active_plan=False, workout_completed_today=False))
    assert view.state == STATE_ERROR
    assert view.state != STATE_NO_PLAN
    assert view.primary is None
    assert view.secondary and view.secondary[0].href == "/training"


def test_read_failure_ignores_otherwise_populated_facts():
    view = build_today_view(TodayFacts(
        read_ok=False, has_active_plan=True, workout_completed_today=True,
        primary_state=STATE_COMPLETED, action="none"))
    assert view.state == STATE_ERROR


def test_read_failure_publishes_no_plan_summary():
    """A stale plan card under an error banner is exactly the "unknown rendered
    as known" this surface must not do."""
    view = build_today_view(TodayFacts(
        read_ok=False, has_active_plan=True, workout_completed_today=False,
        plan=TodayPlanSummary(focus="İtiş", duration_min=45, exercise_count=4)))
    assert view.plan is None


def test_today_facts_resolution_error_is_honest_error(app, make_user, monkeypatch):
    from app.services import today_facts as tf
    from app.services.workout_state import _safe_snapshot
    from app.timeutil import app_today
    make_user("erruser", profile_complete=True)

    def _raise(uid, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(tf, "resolve_workout_state", _raise)
    facts = tf.gather_today_facts(1)
    assert facts.read_ok is False
    assert build_today_view(facts).state == STATE_ERROR

    # A fail-safe snapshot carries completed_today=False from a FAILURE; it must
    # not read as a trustworthy "not done".
    def _failsafe(uid, **kw):
        return _safe_snapshot(app_today())
    monkeypatch.setattr(tf, "resolve_workout_state", _failsafe)
    assert tf.gather_today_facts(1).read_ok is False


def test_domain_anomaly_is_a_product_state_not_a_failure(app, client, make_user,
                                                         login):
    """An unparseable plan is something true about the user's data, and the
    canonical resolver already classifies it. Reporting "unavailable" instead of
    "your plan needs attention" would hide an actionable condition."""
    user = _seed_login(client, make_user, login)
    _seed_plan(user.id, plan_data="{}")   # valid row, no seven-day program
    from app.services.today_facts import gather_today_facts
    facts = gather_today_facts(user.id)
    assert facts.read_ok is True
    assert facts.has_active_plan is True
    assert build_today_view(facts).state == STATE_NEEDS_ATTENTION


# ══════════════════════════════════════════════════════════════════════════
# D. THE CANONICAL INSIGHT — re-published, never re-decided
# ══════════════════════════════════════════════════════════════════════════

def test_insight_label_keys_cover_the_canonical_vocabulary_exactly():
    """Drift gate. A code added to Axis Insights without a Today mapping would
    render nothing at all, which reads as "we found nothing" — the one wrong
    answer. A mapping for a code the server can no longer emit is dead copy."""
    from app.services.progress_insights import WATCH_CODES, WORKING_CODES
    from app.today_presenter import _INSIGHT_LABEL_KEYS

    assert set(_INSIGHT_LABEL_KEYS) == (
        {(INSIGHT_WATCH, code) for code in WATCH_CODES}
        | {(INSIGHT_WORKING, code) for code in WORKING_CODES}
    )


def test_insight_reuses_progress_copy_verbatim():
    """Today and Progress must say the SAME sentence for the same canonical
    signal. A second phrasing would be a second claim."""
    from app.i18n import _CATALOG
    from app.today_presenter import _INSIGHT_LABEL_KEYS

    for key in _INSIGHT_LABEL_KEYS.values():
        assert key.startswith("progress.axis_"), key
        for loc in ("tr", "en"):
            assert _CATALOG[loc].get(key), (loc, key)


def test_a_watch_signal_outranks_a_working_signal():
    view = build_today_view(_facts(insight_kind=INSIGHT_WATCH,
                                   insight_code="deload_due"))
    assert view.insight.kind == INSIGHT_WATCH
    assert view.insight.label_key == "progress.axis_watch_deload_due"
    assert view.insight.href == "/progress-page"


def test_an_unmapped_or_absent_insight_renders_as_absence():
    for kind, code in ((None, None), (INSIGHT_WATCH, None),
                       (INSIGHT_WATCH, "a_code_from_the_future")):
        view = build_today_view(_facts(insight_kind=kind, insight_code=code))
        assert view.insight is None


def test_a_broken_insight_read_never_takes_down_today(app, make_user, monkeypatch):
    """The insight is a secondary observation. If it fails, Today still answers
    the questions it exists to answer."""
    from app.services import today_facts as tf
    user = make_user("insightfail", profile_complete=True)

    def _boom(uid, **kw):
        raise RuntimeError("progression read failed")
    monkeypatch.setattr(tf, "build_progress_insights", _boom)

    facts = tf.gather_today_facts(user.id)
    assert facts.read_ok is True
    assert facts.insight_kind is None and facts.insight_code is None
    assert build_today_view(facts).insight is None


# ══════════════════════════════════════════════════════════════════════════
# E. COPY COMPLETENESS — nothing renders a raw key
# ══════════════════════════════════════════════════════════════════════════

def test_every_state_has_a_brief_and_a_training_stat_label():
    """Both tables must be TOTAL over the state vocabulary: a state without an
    entry renders the dotted key itself to the user."""
    from app.i18n import _CATALOG
    from app.today_presenter import _TRAINING_STAT_KEYS

    assert set(_TRAINING_STAT_KEYS) == set(TODAY_STATES)
    for loc in ("tr", "en"):
        for state in TODAY_STATES:
            assert _CATALOG[loc].get("today.brief." + state), (loc, state)
            assert _CATALOG[loc].get(_TRAINING_STAT_KEYS[state]), (loc, state)


def test_every_action_label_key_resolves_in_every_locale():
    from app.i18n import _CATALOG
    from app.today_presenter import _HREF_BY_LABEL

    keys = set(_HREF_BY_LABEL) | {
        "today.action.start_workout", "today.action.resume_workout",
        "today.action.create_plan",
    }
    for loc in ("tr", "en"):
        for key in keys:
            assert _CATALOG[loc].get(key), (loc, key)


def test_today_copy_is_axisai_only():
    from app.i18n import _CATALOG
    for loc in ("tr", "en"):
        for key, val in _CATALOG[loc].items():
            if key.startswith("today."):
                assert "fitx" not in val.lower(), f"{loc}:{key}"


def test_the_retired_legacy_home_copy_is_gone():
    """`index.*` existed for `templates/index.html` alone. Leaving it behind
    would make the catalog claim a surface the build no longer has."""
    from app.i18n import _CATALOG
    for loc in ("tr", "en"):
        assert not [k for k in _CATALOG[loc] if k.startswith("index.")], loc


# ══════════════════════════════════════════════════════════════════════════
# F. THE RENDERED PAGE — one Home, decided on the server
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("flag", [None, False, True])
def test_the_retired_flag_no_longer_selects_a_home(app, client, make_user, login,
                                                   flag):
    """Mirrors the `UIUX_NAV_V2_ENABLED` precedent: the key stays registered so
    `/health` and the `[FLAGS]` boot line do not drift, but flipping it is not a
    rollback path. Rolling this PR back is a git revert."""
    if flag is None:
        app.config.pop("UIUX_TODAY_V2_ENABLED", None)
    else:
        app.config["UIUX_TODAY_V2_ENABLED"] = flag
    _seed_login(client, make_user, login)
    html = _html(client)
    assert "data-today" in html
    assert 'data-today-state="' in html
    assert 'class="dash-grid"' not in html      # the legacy tree cannot return


def test_the_legacy_dashboard_template_is_gone():
    assert not (ROOT / "templates" / "index.html").exists()
    assert not (ROOT / "static" / "dashboard.css").exists()


def test_no_plan_renders_exactly_one_dominant_cta(app, client, make_user, login):
    _seed_login(client, make_user, login)               # no plan seeded
    html = _html(client)
    assert 'data-today-state="no_plan"' in html
    assert html.count("data-today-primary") == 1
    assert 'href="/training"' in html


def test_a_scheduled_day_shows_the_plan_content_it_actually_has(
        app, client, make_user, login):
    user = _seed_login(client, make_user, login)
    _seed_plan(user.id, plan_data=_week("antrenman", today_focus="Zirve İtiş",
                                        exercises=3))
    html = _html(client)
    assert 'data-today-state="scheduled_not_started"' in html
    assert html.count("data-today-primary") == 1
    # Plan content is the author's, projected through the canonical serializer.
    assert "Zirve İtiş" in html


def test_a_rest_day_is_not_a_missing_plan(app, client, make_user, login):
    user = _seed_login(client, make_user, login)
    _seed_plan(user.id, plan_data=_week("dinlenme"))
    html = _html(client)
    assert 'data-today-state="rest_day"' in html
    assert 'data-today-state="no_plan"' not in html
    assert "data-today-primary" not in html     # nothing is owed today


def test_a_completed_day_carries_no_stale_start(app, client, make_user, login):
    user = _seed_login(client, make_user, login)
    _seed_plan(user.id, plan_data=_week("antrenman"))
    _seed_pumpcheck_today(user.id)
    html = _html(client)
    assert 'data-today-state="completed"' in html
    assert "data-today-primary" not in html
    assert 'href="/progress-page"' in html      # review stays available


def test_home_has_exactly_one_h1(app, client, make_user, login):
    _seed_login(client, make_user, login)
    assert len(re.findall(r"<h1\b", _html(client))) == 1


def test_no_raw_localization_keys_leak(app, client, make_user, login):
    _seed_login(client, make_user, login)
    html = _html(client)
    for key in ("today.title", "today.brief.no_plan", "today.status_label",
                "today.stat_training.no_plan", "today.greeting_named"):
        assert ">%s<" % key not in html


def test_the_day_is_the_servers_and_the_browser_only_formats_it(
        app, client, make_user, login):
    """A browser-picked day would let a traveller's clock disagree with the
    canonical Istanbul day the state was resolved for."""
    from app.timeutil import app_today
    _seed_login(client, make_user, login)
    assert 'data-today-date="%s"' % app_today().isoformat() in _html(client)


def test_home_does_not_re_fetch_the_server_decided_state(
        app, client, make_user, login):
    """Completion, the schedule and the next action are already in the HTML.
    Re-fetching `/workout/status` would add a second authoritative read that can
    disagree with the one that rendered the page."""
    _seed_login(client, make_user, login)
    assert "/workout/status" not in _html(client)
    js = (ROOT / "static" / "today.js").read_text(encoding="utf-8")
    assert "/workout/status" not in js
    assert "/training-plan/active" not in js


# ══════════════════════════════════════════════════════════════════════════
# G. WHAT HOME LOST — and where each capability went
# ══════════════════════════════════════════════════════════════════════════

def test_the_removed_dashboard_modules_are_not_on_home(app, client, make_user,
                                                       login):
    user = _seed_login(client, make_user, login)
    _seed_plan(user.id, plan_data=_week("antrenman"))
    html = _html(client)
    for gone in ('class="dash-grid"', 'id="weight-input"',
                 'data-action="doUpdateWeight"', "wt-bmr", "wt-tdee",
                 'class="qa-grid"', 'class="tip-card"',
                 'data-action="nextTip"', 'class="ach-card"', "chart.js"):
        assert gone not in html, gone


def test_home_promotes_no_unavailable_feature(app, client, make_user, login):
    """The legacy quick-action grid shipped a disabled "Soon" barcode tile.
    Advertising a capability the build does not have is a trust cost with no
    product benefit."""
    from app.i18n import _CATALOG
    _seed_login(client, make_user, login)
    assert "qa-soon" not in _html(client)
    for loc in ("tr", "en"):
        assert "index.qa_soon" not in _CATALOG[loc]


def test_the_capabilities_the_dashboard_hosted_are_still_reachable(
        app, client, make_user, login):
    """Removal is a hierarchy decision, not a feature deletion. Each module Home
    lost is asserted here at the destination that canonically owns it."""
    _seed_login(client, make_user, login)

    # Weight entry + history → Progress (the check-in sheet).
    assert 'id="ci-weight"' in _html(client, "/progress-page")

    # Meal logging and the menu scanner → Nutrition.
    nutrition = _html(client, "/nutrition")
    assert 'id="log-fab"' in nutrition
    assert 'data-action="logMenuScan"' in nutrition

    # Level / XP / quests → Account.
    assert 'href="/quests"' in _html(client, "/edit-profile")


def test_home_renders_no_emoji_as_an_interface_icon():
    emoji = re.compile(
        r"[\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF"
        r"\U0001F900-\U0001FAFF\U00002600-\U000026FF]"
    )
    found = sorted(set(emoji.findall(
        (ROOT / "templates" / "today.html").read_text(encoding="utf-8"))))
    assert not found, found


# ══════════════════════════════════════════════════════════════════════════
# H. CANONICAL SOURCES — Today cannot drift from the endpoints
# ══════════════════════════════════════════════════════════════════════════

def test_today_completion_matches_workout_status(app, client, make_user, login):
    from app.services.today_facts import workout_completed_today
    user = _seed_login(client, make_user, login)
    assert workout_completed_today(user.id) is False
    assert client.get("/workout/status").get_json()["completed"] is False
    _seed_pumpcheck_today(user.id)
    assert workout_completed_today(user.id) is True
    assert client.get("/workout/status").get_json()["completed"] is True


def test_today_state_matches_the_workout_status_contract(app, client, make_user,
                                                         login):
    """The page's `data-today-state` is the same string `/workout/status`
    publishes as `state.primary_state`."""
    user = _seed_login(client, make_user, login)
    _seed_plan(user.id, plan_data=_week("antrenman"))
    published = client.get("/workout/status").get_json()["state"]["primary_state"]
    assert 'data-today-state="%s"' % published in _html(client)


def test_training_plan_active_response_unchanged(app, client, make_user, login):
    user = _seed_login(client, make_user, login)
    assert client.get("/training-plan/active").get_json() == {"exists": False}
    _seed_plan(user.id, plan_data=json.dumps({"days": []}))
    data = client.get("/training-plan/active").get_json()
    assert data["exists"] is True
    assert data["plan"] == {"days": []}
    assert "created_at" in data and "score" in data
