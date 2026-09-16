"""WEB-UX4-PR5 — what the active Plan must keep while its boxes go away.

F-06 removes visual containers; it must not remove content, ownership or
behaviour with them. These tests read the SERVER RENDER (scripts stripped:
`_head.html` ships the whole locale catalogue inside `window.I18N`, so a copy
assertion against raw HTML is vacuous). Geometry, focus and network are proven
in `test_ux4_pr5_plan_hierarchy_browser.py`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from app.extensions import db
from app.i18n import t
from app.models import TrainingPlan, User

ROOT = Path(__file__).resolve().parents[1]
PLAN_CSS = ROOT / "static" / "plan.css"
PLAN_HTML = ROOT / "templates" / "plan.html"


def _rendered_body(html):
    return re.sub(r"(?is)<script[^>]*>.*?</script>", "", html)


def _document(rest_days=4):
    from app.services.training_generation.response_validator import WEEKDAYS
    program = []
    for index, name in enumerate(WEEKDAYS):
        if index < 7 - rest_days:
            program.append({"gun": name, "tip": "antrenman", "odak": f"Focus {index}",
                            "sure_dk": 45, "tahmini_kalori": 300, "egzersizler": [
                                {"isim": f"Lift {index}-{n}", "set": 3, "tekrar": "8-10",
                                 "dinlenme": "90 sn", "not": f"cue {index}-{n}"}
                                for n in range(2)]})
        else:
            program.append({"gun": name, "tip": "dinlenme", "odak": "Recovery",
                            "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []})
    return program


def _render(app, client, user, *, program=None, raw=None, language="tr", weekly=True):
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    app.config["WEEKLY_PROGRAM_UI_ENABLED"] = weekly
    with app.app_context():
        row = db.session.get(User, user.id)
        row.profile_complete = True
        row.language = language
        data = raw if raw is not None else json.dumps(
            {"program": program if program is not None else _document()}, ensure_ascii=False)
        db.session.add(TrainingPlan(user_id=user.id, score=6.0, plan_data=data))
        db.session.commit()
    html = client.get("/training").get_data(as_text=True)
    return BeautifulSoup(_rendered_body(html), "html.parser"), html


@pytest.fixture(autouse=True)
def _flag_reset(app):
    yield
    app.config["UIUX_PLAN_V2_ENABLED"] = False
    app.config["WEEKLY_PROGRAM_UI_ENABLED"] = False


@pytest.mark.parametrize("language", ["tr", "en"])
def test_ownership_is_unchanged(app, client, auth_user, language):
    soup, html = _render(app, client, auth_user, language=language)
    main = soup.select_one('main[data-plan-state="active_plan"]')
    # The weekly-program consumer contract, exactly as rendered before PR5.
    raw = BeautifulSoup(html, "html.parser")
    mount = raw.select('section#weekly-program[data-weekly-program-mount][aria-hidden="true"]')
    assert len(mount) == 1
    copy = mount[0].select('script[type="application/json"][data-weekly-program-copy]')
    assert len(copy) == 1 and "weekly_program.loading" in json.loads(copy[0].string)
    assert html.count("/static/weekly_program.js") == 1
    assert main is not None
    assert len(main.select('[data-plan-domain="training"]')) == 1
    nutrition = main.select('[data-plan-domain="nutrition"]')
    assert len(nutrition) == 1
    supplements = main.select('[data-plan-domain="supplements"]')
    assert len(supplements) == 1
    assert supplements[0].find_parent(attrs={"data-plan-domain": "nutrition"}) is nutrition[0]
    # Training precedes Nutrition in document (and therefore reading) order.
    domains = [s["data-plan-domain"] for s in main.select("[data-plan-domain]")]
    assert domains == ["training", "nutrition", "supplements"]
    assert len(soup.select("[data-weekly-program-mount]")) == 1
    assert soup.select_one("[data-weekly-program-mount]").find_parent(
        attrs={"data-plan-domain": "training"}) is not None
    assert len(soup.select('[data-action="startWorkout"]')) <= 1
    assert soup.select_one("[data-plan-manage]") is not None
    assert soup.select_one('.coach-entry a[href="/coach"]') is not None
    assert soup.select_one("nav.plan-secondary") is not None
    assert main.select_one('a[href="/nutrition"]') and main.select_one('a[href="/supplements"]')


@pytest.mark.parametrize("rest_days", [0, 2, 4, 6])
def test_rest_rows_come_only_from_the_server_rest_marker(app, client, auth_user, rest_days):
    program = _document(rest_days)
    # An empty non-rest day is NOT a rest day and must stay a training disclosure.
    program[0]["egzersizler"] = [] if rest_days < 7 else program[0]["egzersizler"]
    soup, _ = _render(app, client, auth_user, program=program)
    days = soup.select(".plan-days .plan-day")
    assert len(days) == 7
    rest = [d for d in days if "plan-day--rest" in d.get("class", [])]
    assert len(rest) == rest_days
    assert all(d.name == "div" for d in rest)
    assert all(d.name == "details" for d in days if d not in rest)
    for row in rest:
        assert row.select("summary, a, button, [tabindex]") == []
    with app.test_request_context():
        rest_word = t("plan.day.rest", locale="tr")
        note = t("plan.day.rest_note", locale="tr")
    for row in rest:
        text = row.get_text(" ", strip=True)
        assert rest_word in text and note in text
    names = [d.select_one(".plan-day-name").get_text(strip=True) for d in days]
    assert len(set(names)) == 7


def test_every_training_day_keeps_all_of_its_content(app, client, auth_user):
    program = _document(4)
    soup, _ = _render(app, client, auth_user, program=program)
    details = soup.select(".plan-days details.plan-day")
    trained = [d for d in program if d["tip"] != "dinlenme"]
    assert len(details) == len(trained)
    for day, node in zip(trained, details):
        text = node.get_text(" ", strip=True)
        assert day["odak"] in text and "45" in text and "300 kcal" in text
        assert node.select_one("summary") is not None
        for exercise in day["egzersizler"]:
            for value in (exercise["isim"], exercise["tekrar"], exercise["dinlenme"], exercise["not"]):
                assert value in text, value
            assert "3 ×" in text


def test_stale_reason_is_rendered_verbatim_before_the_recovery_affordance():
    source = PLAN_HTML.read_text(encoding="utf-8")
    stale = source.index('data-plan-session-stale="{{ plan.workout_session_stale_reason }}"')
    recovery = source.index('data-action="recoverBlockedWorkout"')
    assert stale < recovery
    assert source.count("data-plan-session-stale=") == 1
    assert "t('plan.session_stale.' ~ plan.workout_session_stale_reason)" in source


def test_the_rest_branch_reads_only_day_is_rest():
    source = PLAN_HTML.read_text(encoding="utf-8")
    days = source[source.index('<section class="plan-days"'):source.index("</section>", source.index('<section class="plan-days"'))]
    assert "{% if day.is_rest %}" in days
    assert "egzersizler" not in days and "exercises|length == 0" not in days
    assert "not day.exercises" not in days


def test_partial_plan_keeps_its_note_and_no_action(app, client, auth_user):
    soup, _ = _render(app, client, auth_user, raw="{not json")
    assert soup.select_one('main[data-plan-state="partial_active_plan"]') is not None
    with app.test_request_context():
        note = t("plan.partial.note", locale="tr")
    assert note in soup.select_one(".plan-partial-note").get_text(strip=True)
    assert soup.select('[data-action="startWorkout"]') == []
    assert soup.select_one('[data-manage-state="regenerate"]') is not None


def test_schedule_heading_never_repeats_the_weekly_program_heading(app, client, auth_user):
    soup, html = _render(app, client, auth_user, weekly=True)
    with app.test_request_context():
        weekly = t("training.weekly_program", locale="tr")
        schedule = soup.select_one("#plan-days-label").get_text(strip=True)
    assert schedule.casefold() != weekly.casefold()


def test_without_the_weekly_program_the_schedule_keeps_its_label(app, client, auth_user):
    soup, _ = _render(app, client, auth_user, weekly=False)
    assert soup.select("[data-weekly-program-mount]") == []
    with app.test_request_context():
        assert soup.select_one("#plan-days-label").get_text(strip=True) == t("plan.days_label", locale="tr")


def _pr5_css():
    css = PLAN_CSS.read_text(encoding="utf-8")
    return css[css.index("/* ── UX4-PR5: active Plan surface hierarchy"):]


def test_pr5_css_mints_nothing_and_uses_no_fragile_selectors():
    block = _pr5_css()
    assert "!important" not in block
    assert "nth-child" not in block and "nth-of-type" not in block
    assert not re.search(r"^\s*--[\w-]+\s*:", block, re.M), "no new custom properties"
    assert "font-family" not in block and "@font-face" not in block
    assert "z-index" not in block
    assert "gradient" not in block and "backdrop-filter" not in block
    assert "position: fixed" not in block


def test_desktop_widening_is_state_scoped():
    block = _pr5_css()
    widening = [line for line in block.splitlines() if "max-width: 1120px" in line]
    assert widening
    scoped = block[:block.index("max-width: 1120px")]
    selector = scoped[scoped.rindex("\n  .plan-main"):]
    assert '[data-plan-state="active_plan"]' in selector
    assert '[data-plan-state="partial_active_plan"]' in selector
    rules = re.sub(r"(?s)/\*.*?\*/", "", block)
    assert "no_active_plan" not in rules and "read_error" not in rules
    # The shared base rule the PR4 first run relies on is untouched.
    assert ".plan-main { max-width: 720px;" in PLAN_CSS.read_text(encoding="utf-8")
