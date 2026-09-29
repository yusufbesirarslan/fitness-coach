"""NUTR-PR3 — Nutrition Today in a real browser.

Real rendered page and shipped script against the authenticated Flask client
(`training_page`). A failure is injected per read with a browser route; every
successful value is compared with what the server's own canonical read says,
never with a number copied into the test.

  UNKNOWN != ZERO · TARGET MISSING != TARGET UNAVAILABLE · EMPTY != FAILED ·
  PLANNED != LOGGED · one failed section never blanks a healthy sibling
"""
import json
import re
from collections import Counter
from pathlib import Path

import pytest
from playwright.sync_api import expect

from app.extensions import db
from app.models import MealLog, NutritionPlan, User, UserSession
from app.timeutil import app_today
from test_training_execution_boundary import training_page  # noqa: F401
from test_nutrition_vnext_pr2_navigation_browser import paths
from test_ux3_pr4_nutrition_placement_browser import stub_meal_macro_provider  # noqa: F401

UNKNOWN = '—'
CATALOG = {lang: json.loads((Path(__file__).resolve().parent.parent / 'locales' / f'{lang}.json')
                            .read_text(encoding='utf-8')) for lang in ('en', 'tr')}
PLAN = {
    'isim': 'Lean plan',
    'kahvalti': {'kalori': 450, 'protein': 30, 'karb': 50, 'yag': 12, 'yemekler': ['Oats']},
    'aksam': {'kalori': 650, 'protein': 45, 'karb': 60, 'yag': 20, 'yemekler': ['Salmon']},
}
MEALS = (('Kahvaltı', 'Oats and banana', 525), ('Öğle', 'Chicken and rice', 710))
COPY = {
    'en': {'absent': 'Target not set yet', 'unavailable': 'Target unavailable',
           'meals_unavailable': 'Meals unavailable', 'log_food': 'Log food',
           'planned': 'Planned', 'logged': 'Logged', 'retry': 'Try again',
           'plan_unavailable': "Your plan couldn't be loaded.",
           'stale': "Couldn't refresh. Showing the last loaded values."},
    'tr': {'absent': 'Hedef henüz belirlenmedi', 'unavailable': 'Hedef şu an alınamadı',
           'meals_unavailable': 'Öğünler şu an yüklenemedi', 'log_food': 'Yemek kaydet',
           'planned': 'Planlandı', 'logged': 'Kaydedildi', 'retry': 'Tekrar dene',
           'plan_unavailable': 'Planın şu an yüklenemedi.',
           'stale': 'Yenilenemedi. Son yüklenen değerler gösteriliyor.'},
}


def seed(app, user_id, language='en', target=2100, meals=MEALS, plan=True):
    with app.app_context():
        user = db.session.get(User, user_id)
        user.profile_complete = True
        user.language = language
        db.session.add(UserSession(user_id=user_id, target_calories=target, goal='kas kazanma'))
        for slot, food, kcal in meals:
            db.session.add(MealLog(user_id=user_id, ogun=slot, yemekler=food, kalori=kcal,
                                   protein=30, karb=60, yag=12, tarih=app_today().isoformat()))
        if plan:
            db.session.add(NutritionPlan(user_id=user_id, score=8,
                                         plan_data=json.dumps(PLAN, ensure_ascii=False)))
        db.session.commit()


def fail(route):
    route.fulfill(status=503, content_type='application/json', body='{}')


def open_today(page, width=390, height=844):
    page.set_viewport_size({'width': width, 'height': height})
    page.goto('http://localhost/nutrition')
    expect(page.locator('#nut-day')).not_to_have_attribute('data-intake-state', 'loading')


def assert_summary_matches_server(page, client):
    """The card equals the server's canonical read (rounded as displayed)."""
    today = client.get('/meal-log/today').get_json()
    eaten = round(today['totals']['kalori'])
    expect(page.locator('#nut-intake')).to_have_text(str(eaten))
    for key in ('protein', 'karb', 'yag'):
        expect(page.locator('#macro-' + key)).to_have_text(str(round(today['totals'][key])))
    expect(page.locator('#meal-timeline .meal-card')).to_have_count(len(today['meals']))
    target = today['targets']
    if target:
        expect(page.locator('#nut-target')).to_have_text(str(round(target['kalori'])))
        left = round(target['kalori']) - eaten
        catalog = CATALOG[page.evaluate('() => window.LOCALE')]
        key = 'nutrition.remaining_left' if left >= 0 else 'nutrition.remaining_over'
        expect(page.locator('#nut-remaining')).to_have_text(catalog[key].replace('{n}', str(abs(left))))
    return today


def assert_log_food_works(page, language='en'):
    button = page.locator('#log-food-btn')
    expect(button).to_be_enabled()
    expect(button).to_have_text(COPY[language]['log_food'])
    button.click()
    expect(page.locator('#log-sheet')).to_have_class(re.compile(r'\bopen\b'))
    page.keyboard.press('Escape')
    expect(page.locator('#log-sheet')).not_to_have_class(re.compile(r'\bopen\b'))


# ── HIERARCHY ───────────────────────────────────────────────────────────


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_confirmed_day_renders_truthful_hierarchy(app, auth_user, client, training_page, language):
    seed(app, auth_user.id, language)
    page, _, _, _ = training_page
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    open_today(page)
    expect(page.locator('h1')).to_have_count(1)
    expect(page.locator('h1')).to_have_text({'en': 'Nutrition', 'tr': 'Beslenme'}[language])
    expect(page.get_by_role('tab')).to_have_count(2)
    expect(page.locator('#nutrition-tab-today')).to_have_attribute('aria-selected', 'true')

    hero = page.locator('#nut-day')
    expect(hero).to_have_attribute('data-intake-state', 'confirmed')
    expect(hero).to_have_attribute('data-target-state', 'known')
    today = assert_summary_matches_server(page, client)
    # Macro targets are the server's projection, drawn only because they exist.
    for key in ('protein', 'karb', 'yag'):
        expect(page.locator(f'#macro-{key}-target')).to_have_text(f"/ {round(today['targets'][key])}")
    expect(page.locator('.nut-macro .pbar-track')).to_have_count(3)
    for track in page.locator('.nut-macro .pbar-track').all():
        expect(track).to_be_visible()
    expect(page.locator('.nut-target-absent')).to_be_hidden()
    expect(page.locator('.nut-target-unavailable')).to_be_hidden()

    # One front door; logged meals below it; hydration compact; plan planned.
    expect(page.locator('[data-action="openLogSheet"]')).to_have_count(1)
    expect(page.locator('#log-fab')).to_have_count(0)
    expect(page.locator('#nut-meal-count')).to_have_text(
        {'en': '2 logged', 'tr': '2 kayıt'}[language])
    expect(page.locator('#qab-water-sub')).to_have_text(
        {'en': 'Today 0 / 8 cups', 'tr': 'Bugün 0 / 8 bardak'}[language])
    expect(page.locator('#qab-water')).to_be_enabled()
    planned = page.locator('#quick-add-cards .qab')
    expect(planned).to_have_count(2)
    expect(page.locator('#quick-add-section')).to_have_attribute('data-plan-state', 'available')
    for row in planned.all():
        expect(row.locator('.qab-badge')).to_have_text(COPY[language]['planned'])
        assert COPY[language]['logged'] not in row.inner_text()
    # Planned rows are not ledger rows.
    assert page.locator('#meal-timeline [data-planned]').count() == 0
    for tool in ('diary', 'history', 'water'):
        summary = page.locator('#nutrition-tab-' + tool)
        expect(summary).to_be_visible()
        assert summary.get_attribute('role') is None
    assert_log_food_works(page, language)
    assert errors == []


def test_intake_over_target_is_signed_and_neutral(app, auth_user, client, training_page):
    seed(app, auth_user.id, target=1000)
    page, _, _, _ = training_page
    open_today(page)
    assert_summary_matches_server(page, client)
    expect(page.locator('#nut-remaining')).to_have_text('235 kcal over target')
    expect(page.locator('#bar-kcal')).to_have_class(re.compile(r'\bis-over\b'))


# ── FAILURE / EMPTY / UNKNOWN ───────────────────────────────────────────


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_target_missing_is_not_zero(app, auth_user, client, training_page, language):
    seed(app, auth_user.id, language, target=None)
    page, _, _, _ = training_page
    open_today(page)
    hero = page.locator('#nut-day')
    expect(hero).to_have_attribute('data-target-state', 'absent')
    expect(page.locator('.nut-hero-target')).to_have_text(COPY[language]['absent'], use_inner_text=True)
    expect(page.locator('.nut-target-known')).to_be_hidden()
    expect(page.locator('#nut-remaining')).to_be_hidden()
    expect(page.locator('.nut-kcal-track')).to_be_hidden()
    for key in ('protein', 'karb', 'yag'):
        expect(page.locator(f'#macro-{key}-target')).to_be_hidden()
    for track in page.locator('.nut-macro .pbar-track').all():
        expect(track).to_be_hidden()
    assert re.findall(r'\d+', hero.inner_text().split(COPY[language]['log_food'])[0]) == \
        ['1235', '60', '120', '24'], hero.inner_text()   # what was eaten — never a target
    assert_summary_matches_server(page, client)
    assert_log_food_works(page, language)


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_target_unavailable_keeps_the_ledger(app, auth_user, client, training_page, language):
    """F1 — the target cannot be read (unusable projection) while the ledger is
    healthy. With one canonical read a server failure takes both down together
    (covered below); an unusable target in a healthy reply is the independent
    case, and it must not read as "not set"."""
    seed(app, auth_user.id, language)
    page, _, _, _ = training_page

    def broken_target(route):
        body = client.get('/meal-log/today').get_json()
        body['targets'] = {'kalori': 'n/a'}
        route.fulfill(status=200, content_type='application/json', body=json.dumps(body))
    page.route('**/meal-log/today', broken_target)
    open_today(page)
    hero = page.locator('#nut-day')
    expect(hero).to_have_attribute('data-target-state', 'unavailable')
    expect(hero).to_have_attribute('data-intake-state', 'confirmed')
    expect(page.locator('.nut-hero-target')).to_have_text(COPY[language]['unavailable'], use_inner_text=True)
    expect(page.locator('.nut-target-absent')).to_be_hidden()
    expect(page.locator('#nut-remaining')).to_be_hidden()
    expect(page.locator('#nut-intake')).to_have_text('1235')
    expect(page.locator('#meal-timeline .meal-card')).to_have_count(2)
    assert_log_food_works(page, language)


def test_empty_day_is_a_proven_zero(app, auth_user, client, training_page):
    seed(app, auth_user.id, meals=())
    page, _, _, _ = training_page
    open_today(page)
    expect(page.locator('#meal-timeline')).to_have_attribute('data-ledger-state', 'empty')
    expect(page.locator('#meal-timeline .nut-ledger-note')).to_have_text('No meals logged today yet')
    expect(page.locator('#nut-meal-count')).to_have_text('0 logged')
    expect(page.locator('#nut-intake')).to_have_text('0')
    expect(page.locator('#nut-remaining')).to_have_text('2100 kcal left')
    # Empty slots are one line each and keep their contextual add action.
    expect(page.locator('.meal-slot.is-empty .slot-empty')).to_have_count(4)
    assert_summary_matches_server(page, client)


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_meal_read_failure_is_unknown_not_empty(app, auth_user, client, training_page, language):
    """F2 — the ledger read fails; Water, Plan shortcuts and Log food stay usable."""
    seed(app, auth_user.id, language)
    page, traffic, _, _ = training_page
    page.route('**/meal-log/today', fail)
    open_today(page)
    hero = page.locator('#nut-day')
    expect(hero).to_have_attribute('data-intake-state', 'unavailable')
    expect(hero).to_have_attribute('data-target-state', 'unavailable')
    expect(page.locator('#nut-intake')).to_have_text(UNKNOWN)
    for key in ('protein', 'karb', 'yag'):
        expect(page.locator('#macro-' + key)).to_have_text(UNKNOWN)
    expect(page.locator('#nut-remaining')).to_be_hidden()
    expect(page.locator('#nut-intake-status-text')).to_have_text(COPY[language]['meals_unavailable'])
    expect(page.locator('#nut-intake-retry')).to_be_visible()
    expect(page.locator('#meal-timeline')).to_have_attribute('data-ledger-state', 'unavailable')
    expect(page.locator('#nut-meal-count')).to_be_hidden()
    summary = hero.inner_text()
    assert not re.search(r'\d', summary), summary       # no "0 kcal", no "0 meals"
    assert not re.search(r'\d', page.locator('#meal-timeline').inner_text())
    # Healthy siblings.
    expect(page.locator('#qab-water')).to_be_enabled()
    expect(page.locator('#quick-add-cards .qab')).to_have_count(2)
    assert_log_food_works(page, language)
    # Retry reads the ledger again and recovers.
    page.unroute('**/meal-log/today', fail)
    traffic.clear()
    page.locator('#nut-intake-retry').click()
    expect(hero).to_have_attribute('data-intake-state', 'confirmed')
    assert_summary_matches_server(page, client)
    expect(page.locator('#nut-intake-retry')).to_be_hidden()
    assert paths(traffic) == Counter({'/meal-log/today': 1})


def test_refresh_failure_after_confirmed_read_is_labelled(app, auth_user, client, training_page):
    """A confirmed read stays visible after a failed refresh — but says so."""
    seed(app, auth_user.id)
    page, _, _, _ = training_page
    open_today(page)
    expect(page.locator('#nut-intake')).to_have_text('1235')
    page.route('**/meal-log/today', fail)
    page.evaluate('() => { loadTodayData(); }')
    hero = page.locator('#nut-day')
    expect(hero).to_have_attribute('data-intake-state', 'stale')
    expect(page.locator('#nut-intake-status-text')).to_have_text(COPY['en']['stale'])
    expect(page.locator('#nut-intake')).to_have_text('1235')
    expect(page.locator('#meal-timeline .meal-card')).to_have_count(2)
    expect(page.locator('#nut-intake-retry')).to_be_visible()


@pytest.mark.parametrize('failure', ['unavailable', 'unconfirmed'])
def test_water_failure_leaves_the_day_usable(app, auth_user, client, training_page, failure):
    """F3 — Water fails; summary, ledger and Log food are untouched, and Water
    never shows a confirmed-looking 0."""
    seed(app, auth_user.id)
    page, _, _, _ = training_page
    script = []

    def water(route):
        action = script.pop(0) if script else 'forward'
        if action == 'fail':
            fail(route)
            return
        request = route.request
        response = client.open('/water', method=request.method, data=request.post_data,
                               headers={k: v for k, v in request.headers.items()
                                        if k.lower() in {'content-type', 'origin', 'x-csrftoken'}})
        if action == 'commit-then-500':
            route.fulfill(status=500, content_type='application/json', body='{}')
        else:
            route.fulfill(status=response.status_code, content_type='application/json',
                          body=response.get_data())
    page.route('**/water', water)
    if failure == 'unavailable':
        script.append('fail')
    open_today(page)
    if failure == 'unconfirmed':
        expect(page.locator('#qab-water')).to_be_enabled()
        script.extend(['commit-then-500', 'fail'])
        page.locator('#qab-water').click()
    expect(page.locator('#qab-water')).to_be_disabled()
    expect(page.locator('#qab-water-sub')).to_have_text('Water status unavailable')
    assert '/ 8' not in page.locator('#qab-water-sub').inner_text()
    expect(page.locator('.water-card')).to_have_attribute('data-water-state', failure)
    assert_summary_matches_server(page, client)
    expect(page.locator('#nut-day')).to_have_attribute('data-intake-state', 'confirmed')
    assert_log_food_works(page)


def test_plan_shortcut_failure_is_not_no_plan(app, auth_user, client, training_page):
    """F4 — the plan read fails; the consumed ledger is healthy and nothing says
    "no plan"."""
    seed(app, auth_user.id)
    page, traffic, _, _ = training_page
    page.route('**/nutrition-plan/active', fail)
    open_today(page)
    section = page.locator('#quick-add-section')
    expect(section).to_have_attribute('data-plan-state', 'unavailable')
    expect(section.locator('.nut-planned-note')).to_have_text(COPY['en']['plan_unavailable'])
    expect(page.locator('.qab-no-plan')).to_have_count(0)
    assert_summary_matches_server(page, client)
    page.unroute('**/nutrition-plan/active', fail)
    traffic.clear()
    section.get_by_role('button', name=COPY['en']['retry']).click()
    expect(section).to_have_attribute('data-plan-state', 'available')
    expect(page.locator('#quick-add-cards .qab')).to_have_count(2)
    assert paths(traffic) == Counter({'/nutrition-plan/active': 1})


def test_no_plan_is_its_own_state(app, auth_user, client, training_page):
    seed(app, auth_user.id, plan=False)
    page, _, _, _ = training_page
    open_today(page)
    expect(page.locator('#quick-add-section')).to_have_attribute('data-plan-state', 'none')
    expect(page.locator('.qab-no-plan')).to_be_visible()


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_multiple_failures_never_blank_the_page(app, auth_user, client, training_page, language):
    """F5 — ledger, Water and Plan all fail at once: each says so on its own,
    Log food and the mode switch still work."""
    seed(app, auth_user.id, language)
    page, _, _, _ = training_page
    for pattern in ('**/meal-log/today', '**/water', '**/nutrition-plan/active'):
        page.route(pattern, fail)
    open_today(page)
    expect(page.locator('#nut-day')).to_have_attribute('data-intake-state', 'unavailable')
    expect(page.locator('#qab-water')).to_be_disabled()
    expect(page.locator('#quick-add-section')).to_have_attribute('data-plan-state', 'unavailable')
    expect(page.locator('#nut-intake-status-text')).to_have_text(COPY[language]['meals_unavailable'])
    assert_log_food_works(page, language)
    page.locator('#nutrition-tab-plan').click()
    expect(page.locator('#panel-plan')).to_be_visible()
    page.locator('#nutrition-tab-today').click()
    expect(page.locator('#log-food-btn')).to_be_visible()


# ── FRESHNESS (real writes, canonical re-read) ─────────────────────────


def test_log_food_front_door_write_refreshes_canonically(
        app, auth_user, client, training_page, stub_meal_macro_provider):
    seed(app, auth_user.id)
    page, traffic, _, _ = training_page
    open_today(page)
    before = assert_summary_matches_server(page, client)
    page.locator('#log-food-btn').click()
    page.locator('[data-action="logManual"]').click()
    page.locator('#meal-input').fill('PR3 freshness meal')
    traffic.clear()
    with page.expect_response(lambda r: r.url.endswith('/meal-log') and r.request.method == 'POST') as w:
        page.locator('[data-action="logMeal"]').click()
    assert w.value.ok
    expect(page.locator('#meal-timeline')).to_contain_text('PR3 freshness meal')
    after = assert_summary_matches_server(page, client)
    assert after['totals']['kalori'] == before['totals']['kalori'] + 320
    assert paths(traffic)['/meal-log/today'] == 1          # re-read, not client arithmetic


def test_planned_shortcut_is_planned_until_the_write_confirms(app, auth_user, client, training_page):
    seed(app, auth_user.id)
    page, traffic, _, _ = training_page
    open_today(page)
    before = assert_summary_matches_server(page, client)
    held = []
    page.route('**/api/quick-add-meal', lambda route: held.append(route))
    row = page.locator('#qab-kahvalti')
    row.click()
    page.wait_for_timeout(100)
    assert len(held) == 1
    # In flight: still Planned, locked against a second tap, no total moved.
    expect(row).to_be_disabled()
    expect(row.locator('.qab-badge')).to_have_text('Planned')
    row.click(force=True)
    page.wait_for_timeout(100)
    assert len(held) == 1
    expect(page.locator('#nut-intake')).to_have_text(str(round(before['totals']['kalori'])))
    request = held[0].request
    response = client.open('/api/quick-add-meal', method='POST', data=request.post_data,
                           headers={k: v for k, v in request.headers.items()
                                    if k.lower() in {'content-type', 'origin', 'x-csrftoken', 'idempotency-key'}})
    traffic.clear()
    held[0].fulfill(status=response.status_code, content_type='application/json', body=response.get_data())
    expect(row.locator('.qab-action')).to_have_text('Logged')
    after = assert_summary_matches_server(page, client)
    assert after['totals']['kalori'] == before['totals']['kalori'] + PLAN['kahvalti']['kalori']
    assert paths(traffic)['/meal-log/today'] == 1


AMBIGUOUS = {
    '5xx': fail,
    'network': lambda route: route.abort('failed'),
    'unreadable': lambda route: route.fulfill(status=200, content_type='application/json', body='<html>'),
}


@pytest.mark.parametrize('outcome', list(AMBIGUOUS))
def test_ambiguous_planned_write_is_not_retried_or_claimed(app, auth_user, client, training_page,
                                                           outcome):
    seed(app, auth_user.id)
    page, traffic, _, _ = training_page
    open_today(page)
    copy = CATALOG['en']
    attempts = []

    def ambiguous(route):
        attempts.append(route.request.headers.get('idempotency-key'))
        AMBIGUOUS[outcome](route)
    page.route('**/api/quick-add-meal', ambiguous)
    traffic.clear()
    row = page.locator('#qab-kahvalti')
    row.click()
    expect(page.locator('#toast-wrap .toast-warning')).to_be_visible()
    # Persistent on the row, not only in the toast: neither Logged nor failed.
    expect(row.locator('.qab-badge')).to_have_text(copy['nutrition.unconfirmed_state'])
    expect(row.locator('.qab-action')).to_have_text(copy['nutrition.check_logged_meals'])
    for part in ('.qab-badge', '.qab-action'):
        expect(row.locator(part)).not_to_have_text(copy['nutrition.logged_state'])
        expect(row.locator(part)).not_to_have_text(copy['nutrition.log_planned'])
    expect(row).not_to_have_class(re.compile(r'\bqab-done\b'))
    expect(row).to_be_disabled()
    # The one canonical ledger re-read still refreshes the day.
    page.wait_for_timeout(300)
    assert paths(traffic)['/meal-log/today'] == 1
    assert_summary_matches_server(page, client)
    # No second write: not by itself, not by a tap on the element, not by
    # keyboard, not with `disabled` stripped (the page-life lock alone must
    # hold), not after the toast is gone, not after the section re-renders.
    # Activation targets the element, never screen coordinates: a forced
    # coordinate click lands on whatever overlays the row at that viewport.
    url = page.url
    row.evaluate('el => el.click()')
    row.dispatch_event('click')
    row.focus()
    page.keyboard.press('Enter')
    row.evaluate('el => { el.disabled = false; el.click(); el.disabled = true; }')
    page.evaluate('() => loadQuickAddSection(true)')
    expect(page.locator('#quick-add-section')).to_have_attribute('data-plan-state', 'available')
    expect(row).to_be_disabled()
    expect(row.locator('.qab-badge')).to_have_text(copy['nutrition.unconfirmed_state'])
    row.evaluate('el => { el.disabled = false; el.click(); el.disabled = true; }')
    page.wait_for_timeout(4000)
    assert page.url == url
    expect(row).to_be_disabled()
    expect(row.locator('.qab-badge')).to_have_text(copy['nutrition.unconfirmed_state'])
    assert len(attempts) == 1 and attempts[0]           # sent once, never re-sent
    assert paths(traffic)['/meal-log/today'] == 1       # exactly one ledger re-read
    # Recovery boundary: a reload rebuilds the row from the server.
    page.reload()
    expect(page.locator('#quick-add-section')).to_have_attribute('data-plan-state', 'available')
    expect(row).to_be_enabled()
    expect(row.locator('.qab-badge')).to_have_text(copy['nutrition.planned'])
    assert len(attempts) == 1


def test_refused_planned_write_can_be_tried_again(app, auth_user, client, training_page):
    seed(app, auth_user.id)
    page, _, _, _ = training_page
    open_today(page)
    attempts = []

    def refused(route):
        attempts.append(route.request.headers.get('idempotency-key'))
        route.fulfill(status=422, content_type='application/json', body='{"error": "refused"}')
    page.route('**/api/quick-add-meal', refused)
    row = page.locator('#qab-kahvalti')
    row.click()
    expect(page.locator('#toast-wrap .toast-error')).to_be_visible()
    expect(row).to_be_enabled()
    expect(row.locator('.qab-badge')).to_have_text('Planned')
    row.click()
    page.wait_for_timeout(300)
    assert len(attempts) == 2 and attempts[0] != attempts[1]


# ── CONFIRMED PLANNED WRITE: page-life Logged lock ───────────────────────
# Each is a normal redraw that rebuilds #quick-add-cards from scratch.

def _plan_then_today(page):
    page.locator('#nutrition-tab-plan').click()
    expect(page.locator('#panel-plan')).to_be_visible()
    page.locator('#nutrition-tab-today').click()


def _plan_back_today(page):
    page.locator('#nutrition-tab-plan').click()
    expect(page.locator('#panel-plan')).to_be_visible()
    page.go_back()


REDRAWS = {
    'plan_to_today': _plan_then_today,
    'reselect_today': lambda page: page.locator('#nutrition-tab-today').click(),
    'back_to_today': _plan_back_today,
}


def meal_log_count(app, user_id):
    with app.app_context():
        return MealLog.query.filter_by(user_id=user_id).count()


def planned_posts(traffic):
    return [status for path, _, status in traffic if path == '/api/quick-add-meal']


def assert_logged_and_inert(page, row):
    """Logged, locked, and no activation path reaches the server — including
    one with `disabled` and `qab-done` stripped, so the page-life lock map
    alone must hold."""
    logged = CATALOG['en']['nutrition.logged_state']
    expect(row).to_be_disabled()
    expect(row).to_have_attribute('data-write-state', 'logged')
    expect(row).to_have_class(re.compile(r'\bqab-done\b'))
    expect(row.locator('.qab-badge')).to_have_text(logged)
    expect(row.locator('.qab-action')).to_have_text(logged)
    row.evaluate('el => el.click()')
    row.dispatch_event('click')
    row.focus()
    page.keyboard.press('Enter')
    row.evaluate("""el => { el.disabled = false; el.classList.remove('qab-done'); el.click();
                            el.disabled = true; el.classList.add('qab-done'); }""")
    page.wait_for_timeout(300)


def confirm_planned_breakfast(app, user_id, page, traffic):
    row = page.locator('#qab-kahvalti')
    expect(page.locator('#quick-add-section')).to_have_attribute('data-plan-state', 'available')
    expect(row).to_be_enabled()
    expect(row.locator('.qab-badge')).to_have_text(CATALOG['en']['nutrition.planned'])
    before = meal_log_count(app, user_id)
    traffic.clear()
    row.click()                                         # the real write, real server
    expect(row.locator('.qab-action')).to_have_text(CATALOG['en']['nutrition.logged_state'])
    expect(page.locator('#toast-wrap .toast-success')).to_be_visible()
    page.wait_for_timeout(300)
    assert planned_posts(traffic) == [200]
    assert paths(traffic)['/meal-log/today'] == 1       # canonical refresh still runs
    assert meal_log_count(app, user_id) == before + 1
    return row, before


@pytest.mark.parametrize('redraw', list(REDRAWS))
def test_confirmed_planned_write_stays_logged_through_redraw(app, auth_user, client, training_page,
                                                             redraw):
    seed(app, auth_user.id)
    page, traffic, _, _ = training_page
    open_today(page)
    row, before = confirm_planned_breakfast(app, auth_user.id, page, traffic)
    assert_logged_and_inert(page, row)
    assert len(planned_posts(traffic)) == 1
    row.evaluate('el => { el.dataset.redrawProbe = "1"; }')
    REDRAWS[redraw](page)
    expect(page.locator('#nutrition-tab-today')).to_have_attribute('aria-selected', 'true')
    # The row really was rebuilt (the probe is gone), and it is still Logged.
    expect(page.locator('#qab-kahvalti[data-redraw-probe]')).to_have_count(0)
    assert_logged_and_inert(page, row)
    assert len(planned_posts(traffic)) == 1, redraw     # no second POST, by any path
    assert meal_log_count(app, auth_user.id) == before + 1
    # The lock is per meal: the same plan's other shortcut stays actionable.
    expect(page.locator('#qab-aksam')).to_be_enabled()


def test_confirmed_logged_lock_ends_at_a_full_reload(app, auth_user, client, training_page):
    """PR3 boundary, stated rather than hidden: the Logged lock lives for one
    page life. A reload rebuilds from the server and the current plan, and the
    shortcut is offered again — backend exactly-once remains deferred."""
    seed(app, auth_user.id)
    page, traffic, _, _ = training_page
    open_today(page)
    row, before = confirm_planned_breakfast(app, auth_user.id, page, traffic)
    page.reload()
    expect(page.locator('#quick-add-section')).to_have_attribute('data-plan-state', 'available')
    expect(row).to_be_enabled()
    expect(row.locator('.qab-badge')).to_have_text(CATALOG['en']['nutrition.planned'])
    expect(row).not_to_have_attribute('data-write-state', 'logged')
    assert meal_log_count(app, auth_user.id) == before + 1   # the reload wrote nothing


def test_new_active_plan_does_not_inherit_an_old_logged_lock(app, auth_user, client, training_page):
    """The active plan can be replaced in the same page life (Plan → select →
    `invalidateActivePlan()` → Today). The old plan's Logged breakfast must not
    disable the new plan's breakfast."""
    seed(app, auth_user.id)
    page, traffic, _, _ = training_page
    open_today(page)
    confirm_planned_breakfast(app, auth_user.id, page, traffic)
    replacement = {**PLAN, 'isim': 'Fresh plan',
                   'kahvalti': {**PLAN['kahvalti'], 'kalori': 520, 'yemekler': ['Eggs']}}
    with app.app_context():
        NutritionPlan.query.filter_by(user_id=auth_user.id).delete()
        db.session.add(NutritionPlan(user_id=auth_user.id, score=9,
                                     plan_data=json.dumps(replacement, ensure_ascii=False)))
        db.session.commit()
    page.evaluate('() => invalidateActivePlan()')         # what a confirmed plan save does
    traffic.clear()
    _plan_then_today(page)
    row = page.locator('#qab-kahvalti')
    expect(row.locator('.qab-title')).to_contain_text('Fresh plan')
    expect(row).to_be_enabled()
    expect(row.locator('.qab-badge')).to_have_text(CATALOG['en']['nutrition.planned'])
    expect(row).not_to_have_class(re.compile(r'\bqab-done\b'))
    assert paths(traffic)['/nutrition-plan/active'] == 1


def test_delete_refreshes_canonically(app, auth_user, client, training_page):
    seed(app, auth_user.id)
    page, traffic, _, _ = training_page
    open_today(page)
    before = assert_summary_matches_server(page, client)
    page.on('dialog', lambda dialog: dialog.accept())
    traffic.clear()
    with page.expect_response(lambda r: '/meal-log/entry/' in r.url) as deleted:
        page.locator('.mc-del').first.click()
    assert deleted.value.status == 204
    expect(page.locator('#meal-timeline .meal-card')).to_have_count(1)
    after = assert_summary_matches_server(page, client)
    assert after['totals']['kalori'] == before['totals']['kalori'] - 525
    assert paths(traffic)['/meal-log/today'] == 1


# ── REQUEST TOPOLOGY ────────────────────────────────────────────────────


def test_initial_request_topology_is_unchanged(app, auth_user, client, training_page):
    seed(app, auth_user.id)
    page, traffic, _, _ = training_page
    open_today(page)
    expect(page.locator('#quick-add-section')).to_have_attribute('data-plan-state', 'available')
    page.wait_for_timeout(300)
    # Same set as the PR2-recorded baseline; `/coach/history` is the Coach
    # widget's own pre-existing hydration (it hosts the menu scanner).
    assert paths(traffic) == Counter({'/nutrition': 1, '/meal-log/today': 1,
        '/nutrition-plan/active': 1, '/water': 1, '/notifications/unread-count': 1,
        '/coach/history': 1}), paths(traffic)
    for lazy in ('/meal-log/history', '/api/diary/today', '/api/food', '/api/menu',
                 '/supplements', '/meal-log/review', '/nutrition-plan/save'):
        assert not any(p.startswith(lazy) for p in paths(traffic)), lazy
    traffic.clear()
    page.locator('#nutrition-tab-history').click()
    expect(page.locator('#history-list .history-meal')).to_have_count(2)
    assert paths(traffic) == Counter({'/meal-log/history': 1})


# ── FIRST VIEWPORT / RESPONSIVE / A11Y ──────────────────────────────────

GEOMETRY = """() => {
  const box = s => { const e = document.querySelector(s); if (!e) return null;
    const b = e.getBoundingClientRect(); return {top: b.top, bottom: b.bottom, h: b.height, w: b.width}; };
  const bar = document.querySelector('.action-bar');
  const barTop = bar && getComputedStyle(bar).display !== 'none'
    ? bar.getBoundingClientRect().top : innerHeight;
  const clipped = [...document.querySelectorAll('#nut-day button, #nut-day .nut-macro, #qab-water, #quick-add-cards .qab, .nutrition-tool-toggle')]
    .filter(e => e.offsetParent && e.scrollWidth > e.clientWidth + 1).map(e => e.id || e.className);
  return {vh: innerHeight, barTop, day: box('#nut-day'), log: box('#log-food-btn'),
          ledger: box('#nut-ledger-title'), hydration: box('#qab-water'),
          planned: box('#quick-add-section'), secondary: box('.nutrition-secondary'),
          tabs: box('.tab-bar'), clipped,
          overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1};
}"""


@pytest.mark.parametrize('language', ['en', 'tr'])
@pytest.mark.parametrize('size', [(320, 640), (390, 844), (430, 844)])
def test_first_viewport_holds_the_daily_job(app, auth_user, training_page, language, size):
    seed(app, auth_user.id, language)
    page, _, _, _ = training_page
    open_today(page, *size)
    expect(page.locator('#quick-add-section')).to_have_attribute('data-plan-state', 'available')
    g = page.evaluate(GEOMETRY)
    assert not g['overflow'], g
    assert g['clipped'] == [], g
    assert g['tabs']['bottom'] <= g['day']['top'], g           # Today | Plan above the state
    assert g['day']['top'] < g['barTop'], g                    # summary begins in view
    assert g['log']['bottom'] <= g['barTop'], g                # Log food fully visible
    assert g['log']['h'] >= 44 and g['log']['w'] >= 44, g
    assert g['ledger']['top'] < g['barTop'], g                 # meal state begins in view
    for later in ('hydration', 'planned', 'secondary'):        # secondary never above core
        assert g[later]['top'] >= g['log']['bottom'], (later, g)


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_responsive_widths_keep_today_usable(app, auth_user, training_page, language):
    seed(app, auth_user.id, language)
    page, _, _, _ = training_page
    open_today(page)
    for width in (320, 390, 430, 768, 1024, 1366):
        page.set_viewport_size({'width': width, 'height': 900})
        g = page.evaluate(GEOMETRY)
        assert not g['overflow'], (width, g)
        assert g['clipped'] == [], (width, g)
        assert page.locator('.tab-bar').evaluate('e => e.scrollWidth <= e.clientWidth + 1'), width
        for control in ('#log-food-btn', '#qab-water', '#nutrition-tab-today', '#nutrition-tab-plan',
                        '.mc-edit', '.mc-del', '.slot-empty', '#nutrition-tab-history'):
            box = page.locator(control).first.bounding_box()
            assert box and box['height'] >= 43.5 and box['width'] >= 43.5, (width, control, box)


def test_keyboard_and_focus(app, auth_user, training_page):
    seed(app, auth_user.id)
    page, _, _, _ = training_page
    open_today(page)
    today = page.locator('#nutrition-tab-today')
    today.focus()
    page.keyboard.press('ArrowRight')
    expect(page.locator('#nutrition-tab-plan')).to_be_focused()
    page.keyboard.press('Home')
    expect(today).to_be_focused()
    button = page.locator('#log-food-btn')
    button.focus()
    assert page.evaluate("() => getComputedStyle(document.activeElement).outlineStyle") != 'none'
    page.keyboard.press('Enter')
    expect(page.locator('#log-sheet')).to_have_class(re.compile(r'\bopen\b'))
    expect(button).to_have_attribute('aria-expanded', 'true')
    assert page.evaluate("() => document.getElementById('log-sheet').contains(document.activeElement)")
    page.keyboard.press('Escape')
    expect(button).to_be_focused()                     # focus returns to the front door
    expect(button).to_have_attribute('aria-expanded', 'false')
    # A secondary disclosure is a button-like summary, not a tab.
    history = page.locator('#nutrition-tab-history')
    history.focus()
    page.keyboard.press('Enter')
    expect(page.locator('#panel-history')).to_be_visible()
    expect(page.locator('#nutrition-tab-today')).to_have_attribute('aria-selected', 'true')
