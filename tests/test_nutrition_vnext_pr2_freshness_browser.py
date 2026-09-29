"""Real Diary POST, canonical MealLog reads and visible Nutrition projections.

Missing/stale Today reads must fail both card and aggregate assertions; removing
the visible History read or fetching hidden History must also fail this suite.
"""
import json
from collections import Counter

import pytest
from playwright.sync_api import expect

from app.extensions import db
from app.models import CustomMeal, CustomMealItem, MealLog
from app.timeutil import app_today
from test_training_execution_boundary import training_page  # noqa: F401
from test_nutrition_vnext_pr2_navigation_browser import ready, paths


def prepare(app, auth_user, client, training_page, history_open=False, language='en', width=390):
    ready(app, auth_user, language)
    with app.app_context():
        db.session.add(MealLog(user_id=auth_user.id, ogun='Öğle',
            yemekler='Existing canonical meal', kalori=200, protein=10, karb=20,
            yag=5, tarih=app_today().isoformat()))
        meal = CustomMeal(user_id=auth_user.id, meal_name='Kahvaltı',
                          date_key=app_today().isoformat())
        meal.items.append(CustomMealItem(food_name='Freshness oats', grams=100,
            calories=525, protein=30, carbs=60, fat=12))
        db.session.add(meal)
        db.session.commit()
        meal_id = meal.id
    page, traffic, _, _ = training_page
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto('http://localhost/nutrition')
    expect(page.locator('#meal-timeline .meal-card')).to_have_count(1)
    expect(page.locator('#nut-intake')).to_have_text('200')
    page.locator('#nutrition-tab-diary').click()
    expect(page.locator('[data-meal-name="Kahvaltı"]')).to_contain_text('Freshness oats')
    if history_open:
        page.locator('#nutrition-tab-history').click()
        expect(page.locator('#history-list .history-meal')).to_have_count(1)
    # Drain initial page auxiliaries before measuring mutation requests.
    page.wait_for_timeout(250)
    traffic.clear()
    return page, traffic, meal_id


def commit(page, meal_id):
    with page.expect_response(lambda r: r.url.endswith(f'/api/diary/meal/{meal_id}/log')) as response:
        page.locator('[data-meal-name="Kahvaltı"] .diary-log-btn').click()
    assert response.value.status == 200
    return response.value.json()


def assert_today(page):
    expect(page.locator('#meal-timeline .meal-card')).to_have_count(2, timeout=2500)
    expect(page.locator('#meal-timeline')).to_contain_text('Freshness oats (100g)')
    expect(page.locator('#nut-intake')).to_have_text('725')
    for key, value in [('protein', '40'), ('karb', '80'), ('yag', '17')]:
        expect(page.locator('#macro-' + key)).to_have_text(value)


def assert_history(page):
    expect(page.locator('#history-list .history-meal')).to_have_count(2, timeout=2500)
    expect(page.locator('#history-list')).to_contain_text('Freshness oats (100g)')
    expect(page.locator('#history-list .history-total').first).to_contain_text('725')


@pytest.mark.parametrize('history_open', [False, True])
@pytest.mark.parametrize('language,width', [('en', 390), ('tr', 1366)])
def test_diary_commit_refreshes_visible_canonical_projections(app, auth_user, client, training_page,
                                                            history_open, language, width):
    page, traffic, meal_id = prepare(app, auth_user, client, training_page, history_open, language, width)
    initial = client.get('/meal-log/today').json
    initial_history = page.locator('#history-list').inner_text()
    assert initial['totals'] == {'kalori': 200, 'protein': 10, 'karb': 20, 'yag': 5}
    mutation = commit(page, meal_id)
    expect(page.locator('[data-meal-name="Kahvaltı"] .diary-log-btn')).to_have_count(0)
    page.wait_for_load_state('networkidle')
    canonical = client.get('/meal-log/today').json
    history = client.get('/meal-log/history').json
    print(json.dumps({'initial': initial, 'mutation_request': f'POST /api/diary/meal/{meal_id}/log',
        'mutation_response': mutation, 'post_commit_requests': list(traffic), 'canonical_today': canonical,
        'visible_today': {'cards': page.locator('#meal-timeline .meal-card').count(),
                          'calories': page.locator('#nut-intake').inner_text()},
        'diary': page.locator('[data-meal-name="Kahvaltı"]').inner_text(),
        'canonical_history': history, 'history_open': history_open, 'initial_history': initial_history,
        'visible_history': page.locator('#history-list').inner_text()}))
    assert canonical['totals'] == {'kalori': 725, 'protein': 40, 'karb': 80, 'yag': 17}
    assert len(canonical['meals']) == 2
    with app.app_context():
        assert MealLog.query.filter_by(user_id=auth_user.id).count() == 2
        assert db.session.get(CustomMeal, meal_id).is_logged
    assert_today(page)
    expect(page.locator('#nutrition-tab-today')).to_have_attribute('aria-selected', 'true')
    expect(page.get_by_role('tab')).to_have_count(2)
    expect(page.locator('#nutrition-tool-diary')).to_have_attribute('open', '')
    expect(page.locator('#nutrition-tool-diary')).to_have_js_property('tagName', 'DETAILS')
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth + 1')
    expected = Counter({f'/api/diary/meal/{meal_id}/log': 1, '/api/diary/today': 1, '/meal-log/today': 1})
    if history_open:
        assert_history(page)
        expected['/meal-log/history'] = 1
    assert paths(traffic) == expected
    if not history_open:
        page.locator('#nutrition-tab-history').click()
        assert_history(page)
        assert paths(traffic)['/meal-log/history'] == 1


def test_diary_rejected_mutation_does_not_advance_today(app, auth_user, client, training_page):
    page, traffic, meal_id = prepare(app, auth_user, client, training_page)
    # Real route rejects a changed staging meal, rather than a mocked success.
    with app.app_context():
        meal = db.session.get(CustomMeal, meal_id)
        meal.items.clear()
        db.session.commit()
    with page.expect_response(lambda r: r.url.endswith(f'/api/diary/meal/{meal_id}/log')) as response:
        page.locator('[data-meal-name="Kahvaltı"] .diary-log-btn').click()
    assert response.value.status == 400
    expect(page.locator('.toast-error')).to_be_visible()
    expect(page.locator('#nut-intake')).to_have_text('200')
    expect(page.locator('#meal-timeline .meal-card')).to_have_count(1)
    assert paths(traffic) == Counter({f'/api/diary/meal/{meal_id}/log': 1})
    assert len(client.get('/meal-log/today').json['meals']) == 1


def test_hidden_open_history_stays_lazy_and_refreshes_on_return(app, auth_user, client, training_page):
    """A delayed commit must invalidate History even if Plan hides its disclosure."""
    page, traffic, meal_id = prepare(app, auth_user, client, training_page, history_open=True)
    pending = []
    endpoint = f'/api/diary/meal/{meal_id}/log'
    page.route('**' + endpoint, lambda route: pending.append(route))
    page.locator('[data-meal-name="Kahvaltı"] .diary-log-btn').click()
    assert len(pending) == 1
    page.locator('#nutrition-tab-plan').click()
    response = client.post(endpoint)
    assert response.status_code == 200
    traffic.append((endpoint, None, response.status_code))
    pending[0].fulfill(status=response.status_code, content_type='application/json',
                       body=response.get_data())
    expect(page.locator('[data-meal-name="Kahvaltı"] .diary-log-btn')).to_have_count(0)
    page.wait_for_timeout(200)
    assert paths(traffic)['/meal-log/history'] == 0
    expect(page.locator('#nutrition-tab-plan')).to_have_attribute('aria-selected', 'true')
    page.locator('#nutrition-tab-today').click()
    assert_history(page)
    assert_today(page)
    assert paths(traffic)['/meal-log/history'] == 1


@pytest.mark.parametrize('projection', ['today', 'history'])
def test_older_canonical_read_cannot_overwrite_post_commit_state(app, auth_user, client,
                                                              training_page, projection):
    """Delay a real pre-commit response until after the post-commit read renders."""
    page, traffic, meal_id = prepare(app, auth_user, client, training_page, history_open=True)
    endpoint = '/meal-log/' + projection
    pending = []
    def deliver_or_hold(route):
        response = client.get(endpoint)
        traffic.append((endpoint, None, response.status_code))
        if not pending:
            pending.append((route, response.get_data()))
        else:
            route.fulfill(status=response.status_code, content_type='application/json',
                          body=response.get_data())
    page.route('**' + endpoint, deliver_or_hold)
    function = 'loadTodayData' if projection == 'today' else 'loadMealHistory'
    page.evaluate('() => { ' + function + '(); }')
    page.wait_for_timeout(100)
    assert len(pending) == 1
    commit(page, meal_id)
    assert_today(page)
    assert_history(page)
    pending[0][0].fulfill(status=200, content_type='application/json', body=pending[0][1])
    page.wait_for_timeout(200)
    assert_today(page)
    assert_history(page)
    assert paths(traffic)[endpoint] == 2


@pytest.mark.parametrize('failure', ['http', 'network'])
def test_post_commit_today_failure_preserves_confirmed_values_and_navigation(app, auth_user, client,
                                                                           training_page, failure):
    page, traffic, meal_id = prepare(app, auth_user, client, training_page)
    def fail(route):
        if failure == 'http':
            route.fulfill(status=503, content_type='application/json', body='{}')
        else:
            route.abort()
    page.route('**/meal-log/today', fail)
    commit(page, meal_id)
    expect(page.locator('[data-meal-name="Kahvaltı"] .diary-log-btn')).to_have_count(0)
    expect(page.locator('.toast-error')).to_be_visible(timeout=2500)
    expect(page.locator('#nut-intake')).to_have_text('200')
    expect(page.locator('#meal-timeline .meal-card')).to_have_count(1)
    assert client.get('/meal-log/today').json['totals']['kalori'] == 725
    page.locator('#nutrition-tab-plan').click()
    expect(page.locator('#nutrition-tab-plan')).to_have_attribute('aria-selected', 'true')
    page.unroute('**/meal-log/today', fail)
    page.locator('#nutrition-tab-today').click()
    assert_today(page)


@pytest.mark.parametrize('projection', ['today', 'history'])
def test_superseding_failed_read_keeps_post_commit_failure_visible(app, auth_user, client,
                                                                 training_page, projection):
    """An ordinary read cannot silence an unconfirmed post-commit refresh."""
    page, traffic, meal_id = prepare(app, auth_user, client, training_page, history_open=True)
    pending = []
    endpoint = '/meal-log/' + projection
    def hold_then_fail(route):
        if not pending:
            response = client.get(endpoint)
            pending.append((route, response.get_data()))
        else:
            route.fulfill(status=503, content_type='application/json', body='{}')
    page.route('**' + endpoint, hold_then_fail)
    commit(page, meal_id)
    expect(page.locator('[data-meal-name="Kahvaltı"] .diary-log-btn')).to_have_count(0)
    assert len(pending) == 1
    function = 'loadTodayData' if projection == 'today' else 'loadMealHistory'
    with page.expect_response(lambda r: r.url.endswith(endpoint) and r.status == 503):
        page.evaluate('() => { ' + function + '(); }')
    pending[0][0].fulfill(status=200, content_type='application/json', body=pending[0][1])
    page.wait_for_timeout(200)
    if projection == 'today':
        expect(page.locator('#nut-intake')).to_have_text('200')
        expect(page.locator('#meal-timeline .meal-card')).to_have_count(1)
    else:
        expect(page.locator('#history-list .history-meal')).to_have_count(1)
    expect(page.locator('.toast-error')).to_be_visible(timeout=2500)
    page.locator('#nutrition-tab-plan').click()
    expect(page.locator('#nutrition-tab-plan')).to_have_attribute('aria-selected', 'true')


def test_closed_history_read_failure_reports_unconfirmed_freshness_on_open(app, auth_user, client,
                                                                         training_page):
    page, traffic, meal_id = prepare(app, auth_user, client, training_page, history_open=True)
    pending = []
    def hold_then_fail(route):
        if not pending:
            response = client.get('/meal-log/history')
            traffic.append(('/meal-log/history', None, response.status_code))
            pending.append((route, response.get_data()))
        else:
            traffic.append(('/meal-log/history', None, 503))
            route.fulfill(status=503, content_type='application/json', body='{}')
    page.route('**/meal-log/history', hold_then_fail)
    page.evaluate('() => { loadMealHistory(); }')
    page.wait_for_timeout(100)
    assert len(pending) == 1
    page.locator('#nutrition-tab-history').click()
    traffic.clear()
    commit(page, meal_id)
    assert_today(page)
    # A pre-commit reply arriving while hidden must not clear freshness debt.
    pending[0][0].fulfill(status=200, content_type='application/json', body=pending[0][1])
    page.wait_for_load_state('networkidle')
    assert paths(traffic)['/meal-log/history'] == 0
    page.locator('#nutrition-tab-history').click()
    expect(page.locator('.toast-error')).to_be_visible(timeout=2500)
    expect(page.locator('#history-list .history-meal')).to_have_count(1)
