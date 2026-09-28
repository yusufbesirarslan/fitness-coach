"""Real rendered page, native disclosures, keyboard and request topology."""
import json
from collections import Counter
from pathlib import Path

import pytest
from playwright.sync_api import expect
from app.extensions import db
from app.models import User, MealLog, NutritionPlan
from app.timeutil import app_today
from test_training_execution_boundary import training_page  # noqa: F401


def ready(app, auth_user, language='en'):
    with app.app_context():
        user = db.session.get(User, auth_user.id)
        user.profile_complete = True
        user.language = language
        db.session.commit()


def paths(traffic):
    return Counter(p for p, _, _ in traffic if not p.startswith('/static/'))


def test_initial_and_lazy_requests(app, auth_user, training_page):
    ready(app, auth_user)
    page, traffic, _, _ = training_page
    page.goto('http://localhost/nutrition')
    page.wait_for_timeout(300)
    assert paths(traffic) == Counter({'/nutrition': 1, '/meal-log/today': 1,
        '/nutrition-plan/active': 1, '/water': 1, '/notifications/unread-count': 1, '/coach/history': 1}), paths(traffic)
    evidence = Path('docs/evidence/nutrition-pr2')
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / 'after-requests.json').write_text(json.dumps(dict(paths(traffic)), indent=2), encoding='utf-8')
    traffic.clear()
    page.locator('#nutrition-tab-diary').click()
    expect(page.locator('#panel-diary .diary-meal-card')).to_have_count(4)
    assert paths(traffic) == Counter({'/api/diary/today': 1})
    traffic.clear()
    page.locator('#nutrition-tab-diary').click()
    page.locator('#nutrition-tab-history').click()
    page.wait_for_timeout(200)
    assert paths(traffic) == Counter({'/meal-log/history': 1})
    traffic.clear()
    page.locator('#nutrition-tab-water').click()
    expect(page.locator('#water-btn')).to_be_visible()
    page.locator('#nutrition-tab-plan').click()
    expect(page.locator('#plan-btn')).to_be_visible()
    expect(page.locator('.nutrition-child-domain')).to_be_visible()
    assert paths(traffic) == Counter()


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_hierarchy_geometry_keyboard_and_back(app, auth_user, training_page, language):
    ready(app, auth_user, language)
    page, traffic, _, _ = training_page
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto('http://localhost/nutrition')
    labels = ['Today', 'Plan'] if language == 'en' else ['Bugün', 'Plan']
    expect(page.get_by_role('tab')).to_have_text(labels)
    assert 'Create Plan tab' not in page.locator('main').inner_text()
    assert 'Plan Oluştur sekmesine' not in page.locator('main').inner_text()
    for width in (320, 390, 430, 768, 1024, 1366):
        page.set_viewport_size({'width': width, 'height': 900})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        assert page.locator('.tab-bar').evaluate('e => e.scrollWidth <= e.clientWidth + 1')
        for tab in page.get_by_role('tab').all():
            expect(tab).to_be_visible()
            box = tab.bounding_box()
            assert box['width'] >= 44 and box['height'] >= 44
        if width in (320, 1366):
            evidence = Path('docs/evidence/nutrition-pr2')
            evidence.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(evidence / f'today-{language}-{width}.png'), full_page=True)
        for name in ('diary', 'history', 'water'):
            control = page.locator('#nutrition-tab-' + name)
            expect(control).to_be_visible()
            assert control.bounding_box()['height'] >= 44
    today = page.locator('#nutrition-tab-today')
    plan = page.locator('#nutrition-tab-plan')
    today.focus()
    page.keyboard.press('ArrowRight')
    expect(plan).to_be_focused()
    expect(plan).to_have_attribute('aria-selected', 'true')
    expect(page.locator('#panel-today')).to_be_hidden()
    page.keyboard.press('Home')
    expect(today).to_be_focused()
    expect(today).to_have_attribute('aria-selected', 'true')
    history_length = page.evaluate('history.length')
    page.keyboard.press('Home')
    assert page.evaluate('history.length') == history_length
    diary = page.locator('#nutrition-tab-diary')
    diary.focus()
    page.keyboard.press('Enter')
    expect(page.locator('#panel-diary')).to_be_visible()
    expect(diary).to_be_focused()
    # Native details opens before its queued toggle records the history entry.
    page.wait_for_function(
        "history.state?.nutritionNavigation?.tools?.diary === true")
    page.evaluate('history.back()')
    expect(page.locator('#panel-diary')).to_be_hidden()
    expect(diary).to_be_focused()
    page.evaluate('history.forward()')
    expect(page.locator('#panel-diary')).to_be_visible()
    page.reload()
    expect(page.locator('#panel-diary')).to_be_visible()
    page.locator('#nutrition-tab-plan').click()
    page.reload()
    expect(plan).to_have_attribute('aria-selected', 'true')
    expect(page.locator('.nutrition-child-domain')).to_be_visible()
    assert page.get_by_role('heading', level=1).count() == 1
    assert errors == []


@pytest.mark.parametrize('name', ['today', 'plan', 'diary', 'history', 'water'])
def test_existing_programmatic_entries(app, auth_user, training_page, name):
    ready(app, auth_user)
    page, _, _, _ = training_page
    page.goto('http://localhost/nutrition')
    page.evaluate('(name) => switchTab(name, document.querySelector(`[data-tab-name="${name}"]`))', name)
    owner = 'plan' if name == 'plan' else 'today'
    expect(page.locator('#nutrition-tab-' + owner)).to_have_attribute('aria-selected', 'true')
    expect(page.locator('#panel-' + name)).to_be_visible()


def test_no_plan_shortcut_retains_real_handoff(app, auth_user, training_page):
    ready(app, auth_user)
    page, _, _, _ = training_page
    page.goto('http://localhost/nutrition')
    shortcut = page.locator('[data-action="fxGoToPlanTab"]')
    expect(shortcut).to_have_role('button')
    shortcut.focus()
    page.keyboard.press('Enter')
    expect(page.locator('#nutrition-tab-plan')).to_have_attribute('aria-selected', 'true')
    expect(page.locator('#plan-btn')).to_be_visible()


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_open_workflows_and_plan_have_no_overflow(app, auth_user, training_page, language):
    ready(app, auth_user, language)
    with app.app_context():
        db.session.add(MealLog(user_id=auth_user.id, ogun='Kahvaltı', yemekler='PR2 canonical meal',
                              kalori=525, protein=30, karb=60, yag=12, tarih=app_today().isoformat()))
        db.session.add(NutritionPlan(user_id=auth_user.id, plan_data=json.dumps({
            'isim': 'PR2 canonical plan', 'toplam_kalori': 2100,
            'kahvalti': {'yemekler': ['Oats'], 'kalori': 525, 'protein': 30, 'karb': 60, 'yag': 12}})))
        db.session.commit()
    page, traffic, _, _ = training_page
    page.goto('http://localhost/nutrition')
    for name in ('diary', 'history', 'water'):
        page.locator('#nutrition-tab-' + name).click()
        expect(page.locator('#panel-' + name)).to_be_visible()
    expect(page.locator('#panel-history')).to_contain_text('PR2 canonical meal')
    for width in (320, 390, 430, 768, 1024, 1366):
        page.set_viewport_size({'width': width, 'height': 900})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        page.locator('#nutrition-tab-plan').click()
        expect(page.locator('#active-plan-detail')).to_contain_text('PR2 canonical plan')
        expect(page.locator('.nutrition-child-domain')).to_be_visible()
        if width in (320, 1366):
            evidence = Path('docs/evidence/nutrition-pr2')
            evidence.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(evidence / f'plan-{language}-{width}.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        page.locator('#nutrition-tab-today').click()
    for name in ('diary', 'history', 'water'):
        expect(page.locator('#panel-' + name)).to_be_visible()


@pytest.mark.parametrize('endpoint,tool', [('/api/diary/today', 'diary'), ('/meal-log/history', 'history'),
                                        ('/nutrition-plan/active', 'plan'), ('/water', 'water')])
def test_child_failure_preserves_navigation(app, auth_user, training_page, endpoint, tool):
    ready(app, auth_user)
    page, _, _, _ = training_page
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.route('**' + endpoint, lambda route: route.fulfill(status=503, content_type='application/json', body='{}'))
    page.goto('http://localhost/nutrition')
    page.locator('#nutrition-tab-' + tool).click()
    expect(page.locator('#panel-' + tool)).to_be_visible()
    page.locator('#nutrition-tab-plan').click()
    expect(page.locator('#nutrition-tab-plan')).to_have_attribute('aria-selected', 'true')
    page.locator('#nutrition-tab-today').click()
    expect(page.locator('#nutrition-tab-today')).to_have_attribute('aria-selected', 'true')
    assert errors == []


def test_plan_reload_defers_open_today_workflows_until_return(app, auth_user, training_page):
    ready(app, auth_user)
    page, traffic, _, _ = training_page
    page.goto('http://localhost/nutrition')
    for name in ('diary', 'history'):
        page.locator('#nutrition-tab-' + name).click()
    expect(page.locator('#panel-diary .diary-meal-card')).to_have_count(4)
    page.locator('#nutrition-tab-plan').click()
    traffic.clear()
    page.reload()
    page.wait_for_timeout(200)
    assert '/api/diary/today' not in paths(traffic)
    assert '/meal-log/history' not in paths(traffic)
    traffic.clear()
    page.locator('#nutrition-tab-today').click()
    expect(page.locator('#panel-diary .diary-meal-card')).to_have_count(4)
    expect(page.locator('#panel-history')).to_be_visible()
    page.wait_for_timeout(200)
    assert paths(traffic)['/api/diary/today'] == 1
    assert paths(traffic)['/meal-log/history'] == 1
