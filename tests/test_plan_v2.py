"""AxisAI UIUX Sprint 1 PR3 — Plan experience v2 (flag-gated) tests.

Covers the pure presentation contract (page state vs. weekly-section state kept
INDEPENDENT), the strict fallback semantics (parse failure → partial, never
no-plan; empty exercise list is NOT a rest day; malformed ≠ no plan; canonical
order preserved), both flag paths (OFF = legacy training.html + training.js; ON =
plan.html), the weekly-program-disabled combination, and the guarantee that the V2
surface carries NONE of legacy training.js's client-side authority. The Plan flag
is toggled with app.config["UIUX_PLAN_V2_ENABLED"] (training() reads it at request
time); the weekly section is gated independently by WEEKLY_PROGRAM_UI_ENABLED.
"""
import json
from pathlib import Path
import re

import pytest

from app.plan_presenter import (
    STATE_ACTIVE_PLAN,
    STATE_NO_ACTIVE_PLAN,
    STATE_PARTIAL_ACTIVE_PLAN,
    STATE_READ_ERROR,
    WEEKLY_DISABLED,
    WEEKLY_LOADING,
    PlanDay,
    PlanExercise,
    PlanFacts,
    build_plan_view,
)


# ── Helpers ──

def _seed_login(client, make_user, login, username="planuser"):
    user = make_user(username, profile_complete=True)
    login(username)
    return user


def _seed_plan(user_id, plan_data):
    from app.extensions import db
    from app.models import TrainingPlan
    db.session.add(TrainingPlan(user_id=user_id, plan_data=plan_data))
    db.session.commit()


_VALID_PLAN = json.dumps([
    {"gun": "Pazartesi", "tip": "guc", "odak": "İtiş günü", "sure_dk": 45,
     "tahmini_kalori": 320, "egzersizler": [
         {"isim": "Bench Press", "set": "3", "tekrar": "8-12",
          "dinlenme": "90 sn", "not": "kontrollü in"}]},
    {"gun": "Salı", "tip": "dinlenme", "egzersizler": []},
    {"gun": "Çarşamba", "tip": "guc", "odak": "Çekiş günü", "sure_dk": 50,
     "tahmini_kalori": 340, "egzersizler": [
         {"isim": "Barbell Row", "set": "4", "tekrar": "6-10", "dinlenme": "2 dk"}]},
], ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════════
# A. PURE PRESENTER — page-state contract, no dominant CTA, weekly independence
# ══════════════════════════════════════════════════════════════════════════

def _facts(read_ok=True, has=True, parse_ok=True, days=(PlanDay(label="Pazartesi"),)):
    return PlanFacts(read_ok=read_ok, has_active_plan=has, parse_ok=parse_ok, days=days)


def test_presenter_maps_every_page_state():
    cases = {
        STATE_READ_ERROR:          _facts(read_ok=False, has=False, parse_ok=False, days=()),
        STATE_NO_ACTIVE_PLAN:      _facts(has=False, parse_ok=False, days=()),
        STATE_ACTIVE_PLAN:         _facts(),
        STATE_PARTIAL_ACTIVE_PLAN: _facts(parse_ok=False, days=()),
    }
    for expected, facts in cases.items():
        assert build_plan_view(facts).state == expected


def test_presenter_has_no_dominant_cta_in_any_state():
    # The Plan surface never renders a dominant navigation CTA: the plan content,
    # the in-page generator, and the safe retry ARE the interactions (answer.txt §2).
    for facts in (
        _facts(read_ok=False, has=False, parse_ok=False, days=()),
        _facts(has=False, parse_ok=False, days=()),
        _facts(),
        _facts(parse_ok=False, days=()),
    ):
        assert build_plan_view(facts).primary is None


def test_presenter_populated_has_no_self_link_and_no_start():
    v = build_plan_view(_facts())
    assert v.state == STATE_ACTIVE_PLAN
    assert v.primary is None                         # plan IS the destination
    hrefs = [a.href for a in v.secondary]
    labels = [a.label_key for a in v.secondary]
    assert "/training" not in hrefs                  # never a self-link
    assert not any("start" in lk for lk in labels)   # no invented "start today"
    assert "/coach" in hrefs and "/progress-page" in hrefs


def test_presenter_no_active_plan_is_not_error_and_no_secondary():
    v = build_plan_view(_facts(has=False, parse_ok=False, days=()))
    assert v.state == STATE_NO_ACTIVE_PLAN
    assert v.state != STATE_READ_ERROR               # not-an-error (answer.txt §2)
    assert v.primary is None                         # the generator's submit is the action
    assert v.secondary == ()


def test_presenter_read_error_offers_no_open_plan_self_link():
    v = build_plan_view(_facts(read_ok=False, has=False, parse_ok=False, days=()))
    assert v.state == STATE_READ_ERROR
    assert v.primary is None
    # A safe retry is a page reload (handled in the template), NOT an "Open Plan"
    # navigation — the user is already on Plan (answer.txt §2).
    assert all(a.href != "/training" for a in v.secondary)


def test_presenter_partial_keeps_plan_visible():
    v = build_plan_view(_facts(parse_ok=False, days=()))
    assert v.state == STATE_PARTIAL_ACTIVE_PLAN
    assert v.partial is True                          # honest partial, plan stays


def test_weekly_section_state_is_independent_of_page_state():
    # missing_baseline/error/etc. are the weekly SECTION's client states; the page
    # only ever emits loading|disabled and never lets the weekly section decide the
    # page identity (answer.txt §3).
    # Disabled whenever the flag is off, regardless of a healthy active plan:
    assert build_plan_view(_facts(), weekly_enabled=False).weekly_section_state == WEEKLY_DISABLED
    # Loading when the flag is on AND there is a plan context:
    assert build_plan_view(_facts(), weekly_enabled=True).weekly_section_state == WEEKLY_LOADING
    assert build_plan_view(_facts(parse_ok=False, days=()), weekly_enabled=True).weekly_section_state == WEEKLY_LOADING
    # No plan context → disabled even with the flag on (nothing to mount for):
    assert build_plan_view(_facts(has=False, parse_ok=False, days=()), weekly_enabled=True).weekly_section_state == WEEKLY_DISABLED
    assert build_plan_view(_facts(read_ok=False, has=False, parse_ok=False, days=()), weekly_enabled=True).weekly_section_state == WEEKLY_DISABLED


def test_presenter_state_ids_are_not_localized():
    for sid in (STATE_READ_ERROR, STATE_NO_ACTIVE_PLAN, STATE_ACTIVE_PLAN, STATE_PARTIAL_ACTIVE_PLAN):
        assert re.fullmatch(r"[a-z_]+", sid)


# ══════════════════════════════════════════════════════════════════════════
# B. READ LAYER — safe parse + strict fallback semantics (answer.txt §8)
# ══════════════════════════════════════════════════════════════════════════

def test_facts_no_plan_is_honest_absence(app, make_user):
    from app.services.plan_facts import gather_plan_facts
    user = make_user("noplan", profile_complete=True)
    facts = gather_plan_facts(user.id)
    assert facts.read_ok is True and facts.has_active_plan is False
    assert build_plan_view(facts).state == STATE_NO_ACTIVE_PLAN


def test_facts_valid_plan_parses_in_canonical_order(app, make_user):
    from app.services.plan_facts import gather_plan_facts
    user = make_user("valid", profile_complete=True)
    _seed_plan(user.id, _VALID_PLAN)
    facts = gather_plan_facts(user.id)
    assert facts.read_ok and facts.has_active_plan and facts.parse_ok
    assert [d.label for d in facts.days] == ["Pazartesi", "Salı", "Çarşamba"]  # order preserved
    assert facts.days[0].is_rest is False and facts.days[1].is_rest is True
    assert facts.days[0].exercises[0].name == "Bench Press"


def test_facts_accepts_program_wrapper(app, make_user):
    from app.services.plan_facts import gather_plan_facts
    user = make_user("wrapped_valid", profile_complete=True)
    _seed_plan(user.id, json.dumps({"program": json.loads(_VALID_PLAN)}))
    facts = gather_plan_facts(user.id)
    assert facts.has_active_plan is True and facts.parse_ok is True


@pytest.mark.parametrize("payload", [{}, {"program": {}}, {"program": []}])
def test_bad_wrapper_remains_partial(app, make_user, payload):
    from app.services.plan_facts import gather_plan_facts
    user = make_user("wrapped_partial")
    _seed_plan(user.id, json.dumps(payload))
    facts = gather_plan_facts(user.id)
    assert facts.has_active_plan is True and facts.parse_ok is False


def test_facts_empty_exercise_list_is_not_rest(app, make_user):
    from app.services.plan_facts import gather_plan_facts
    user = make_user("emptyex", profile_complete=True)
    _seed_plan(user.id, json.dumps([{"gun": "Pazartesi", "tip": "guc", "egzersizler": []}]))
    facts = gather_plan_facts(user.id)
    assert facts.parse_ok is True
    assert facts.days[0].is_rest is False            # empty list ≠ rest (answer.txt §8)


def test_facts_malformed_is_partial_not_no_plan(app, make_user):
    from app.services.plan_facts import gather_plan_facts
    user = make_user("malformed", profile_complete=True)
    _seed_plan(user.id, "not valid json{{")
    facts = gather_plan_facts(user.id)
    assert facts.has_active_plan is True             # a row exists → NOT no-plan
    assert facts.parse_ok is False
    assert build_plan_view(facts).state == STATE_PARTIAL_ACTIVE_PLAN


def test_facts_non_list_and_empty_list_are_partial(app, make_user):
    from app.services.plan_facts import gather_plan_facts
    for i, bad in enumerate([json.dumps({"program": "not a list"}), json.dumps([])]):
        user = make_user(f"bad{i}", profile_complete=True)
        _seed_plan(user.id, bad)
        facts = gather_plan_facts(user.id)
        assert facts.has_active_plan is True and facts.parse_ok is False
        assert build_plan_view(facts).state == STATE_PARTIAL_ACTIVE_PLAN


def test_facts_read_failure_is_read_error(app, make_user, monkeypatch):
    from app.services import plan_facts as pf
    make_user("readerr", profile_complete=True)

    def _raise(uid):
        raise RuntimeError("db down")
    monkeypatch.setattr(pf, "get_active_plan", _raise)
    facts = pf.gather_plan_facts(1)
    assert facts.read_ok is False
    assert build_plan_view(facts).state == STATE_READ_ERROR


# ══════════════════════════════════════════════════════════════════════════
# C. FLAG BEHAVIOUR — OFF preserves legacy, ON renders Plan v2
# ══════════════════════════════════════════════════════════════════════════

def test_flag_default_off_renders_legacy_training(client, make_user, login):
    _seed_login(client, make_user, login)
    html = client.get("/training").get_data(as_text=True)
    assert "data-plan-v2" not in html                    # V2 mark absent
    assert "/static/training.js" in html                 # legacy renderer present
    assert 'id="setup-form"' in html


def test_flag_on_renders_plan_v2(app, client, make_user, login):
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    _seed_login(client, make_user, login)
    html = client.get("/training").get_data(as_text=True)
    assert "data-plan-v2" in html
    assert 'data-plan-state=' in html
    assert "/static/training.js" not in html             # legacy JS never loaded on V2


def test_missing_flag_fails_safe_to_legacy(app, client, make_user, login):
    app.config.pop("UIUX_PLAN_V2_ENABLED", None)
    _seed_login(client, make_user, login)
    html = client.get("/training").get_data(as_text=True)
    assert "data-plan-v2" not in html                    # safe default: legacy


def test_exactly_one_plan_tree_per_flag_state(app, client, make_user, login):
    _seed_login(client, make_user, login)
    off = client.get("/training").get_data(as_text=True)
    assert "data-plan-v2" not in off and "/static/training.js" in off
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    on = client.get("/training").get_data(as_text=True)
    assert "data-plan-v2" in on and "/static/training.js" not in on   # never both


# ══════════════════════════════════════════════════════════════════════════
# D. PLAN V2 STATE RENDERING (route level)
# ══════════════════════════════════════════════════════════════════════════

def test_v2_no_active_plan_shows_inline_generator(app, client, make_user, login):
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    _seed_login(client, make_user, login)               # no plan seeded
    html = client.get("/training").get_data(as_text=True)
    assert 'data-plan-state="no_active_plan"' in html
    assert "data-plan-create" in html                    # in-page generator present
    assert "/static/plan_create.js" in html              # creation-only script loaded
    assert "/static/training.js" not in html             # NOT the legacy generator


def test_v2_active_plan_renders_days_no_dominant_cta(app, client, make_user, login):
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    user = _seed_login(client, make_user, login)
    _seed_plan(user.id, _VALID_PLAN)
    html = client.get("/training").get_data(as_text=True)
    assert 'data-plan-state="active_plan"' in html
    assert "Bench Press" in html                          # exercise rendered server-side
    assert "data-plan-create" not in html                # no generator when populated
    assert "/static/plan_create.js" not in html
    assert "/static/training.js" not in html


def test_v2_partial_plan_keeps_plan_visible(app, client, make_user, login):
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    user = _seed_login(client, make_user, login)
    _seed_plan(user.id, "not json{{")
    html = client.get("/training").get_data(as_text=True)
    assert 'data-plan-state="partial_active_plan"' in html
    assert "plan-partial-note" in html                    # honest partial note
    assert "plan-secondary" in html                       # actions still available


def test_v2_has_exactly_one_h1(app, client, make_user, login):
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    _seed_login(client, make_user, login)
    html = client.get("/training").get_data(as_text=True)
    assert len(re.findall(r"<h1\b", html)) == 1


def test_v2_no_raw_localization_keys_leak(app, client, make_user, login):
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    _seed_login(client, make_user, login)
    html = client.get("/training").get_data(as_text=True)
    assert ">plan.title<" not in html
    assert ">plan.create.title<" not in html


def test_v2_has_no_client_side_authority_markers(app, client, make_user, login):
    # The V2 server surface must carry NONE of legacy training.js's forbidden
    # client authority: no clock-based today, no rest inference, no localStorage.
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    user = _seed_login(client, make_user, login)
    _seed_plan(user.id, _VALID_PLAN)
    html = client.get("/training").get_data(as_text=True)
    for forbidden in ("getTodayTurkish", "localStorage", "/static/training.js"):
        assert forbidden not in html


# ══════════════════════════════════════════════════════════════════════════
# E. WEEKLY-PROGRAM SECTION — independent flag, disabled combination (§3, §6)
# ══════════════════════════════════════════════════════════════════════════

def test_plan_on_weekly_disabled_omits_section_keeps_plan(app, client, make_user, login):
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    app.config["WEEKLY_PROGRAM_UI_ENABLED"] = False
    user = _seed_login(client, make_user, login)
    _seed_plan(user.id, _VALID_PLAN)
    html = client.get("/training").get_data(as_text=True)
    assert "data-weekly-program-mount" not in html       # no mount
    assert "/static/weekly_program.js" not in html       # not loaded → no indefinite loading
    assert 'data-plan-state="active_plan"' in html        # active plan stays visible
    assert "Bench Press" in html


def test_plan_on_weekly_enabled_mounts_section(app, client, make_user, login):
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    app.config["WEEKLY_PROGRAM_UI_ENABLED"] = True
    user = _seed_login(client, make_user, login)
    _seed_plan(user.id, _VALID_PLAN)
    html = client.get("/training").get_data(as_text=True)
    assert "data-weekly-program-mount" in html
    assert "/static/weekly_program.js" in html
    assert "data-weekly-program-copy" in html            # consumer copy contract present


def test_weekly_never_mounts_without_active_plan(app, client, make_user, login):
    # Even with the weekly flag on, no plan context → no mount (nothing to show for).
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    app.config["WEEKLY_PROGRAM_UI_ENABLED"] = True
    _seed_login(client, make_user, login)               # no plan
    html = client.get("/training").get_data(as_text=True)
    assert 'data-plan-state="no_active_plan"' in html
    assert "data-weekly-program-mount" not in html
    assert "/static/weekly_program.js" not in html


# ══════════════════════════════════════════════════════════════════════════
# F. FLAG INDEPENDENCE + copy hygiene
# ══════════════════════════════════════════════════════════════════════════

def test_plan_flag_independent_of_today_and_nav(app, client, make_user, login):
    # Plan ON must not force Today v2 or Nav v2 on the Plan page.
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    app.config["UIUX_TODAY_V2_ENABLED"] = False
    app.config["UIUX_NAV_V2_ENABLED"] = False
    _seed_login(client, make_user, login)
    html = client.get("/training").get_data(as_text=True)
    assert "data-plan-v2" in html
    assert "data-today-v2" not in html
    assert 'data-nav-id="today"' not in html             # legacy nav preserved


def test_new_plan_copy_is_axisai_only():
    from app.i18n import _CATALOG
    for loc in ("tr", "en"):
        for key, val in _CATALOG[loc].items():
            if key.startswith("plan."):
                assert "fitx" not in val.lower(), f"{loc}:{key}"


# ══════════════════════════════════════════════════════════════════════════
# G. SIGNED EXERCISE-CONTEXT TOKEN FORWARDING (Sprint 11 PR4 Task 4)
# ══════════════════════════════════════════════════════════════════════════

def test_plan_create_forwards_the_context_token_from_generate_into_save():
    """plan_create.js is a submitter, not a second authority — and that holds
    for the signed equipment context too: it forwards the token from the
    generate response straight into the save body without parsing it,
    rendering it, editing it, or persisting it anywhere."""
    source = (Path(__file__).resolve().parents[1]
              / "static" / "plan_create.js").read_text(encoding="utf-8")

    assert "exercise_context_token: data.exercise_context_token" in source
    token_lines = [
        line for line in source.splitlines() if "exercise_context_token" in line
    ]
    assert len(token_lines) == 1
    for forbidden in ("localStorage", "sessionStorage", "textContent",
                      "innerHTML", "location", "atob", "JSON.parse",
                      "searchParams", "split"):
        assert forbidden not in token_lines[0], forbidden
    # The client never invents an equipment context of its own.
    assert "exercise_context" not in source.replace("exercise_context_token", "")
