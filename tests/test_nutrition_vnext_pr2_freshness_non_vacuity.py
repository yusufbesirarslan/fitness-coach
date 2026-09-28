"""Scoped mutations prove visible-state and request-budget assertions are live.

Browser routes mutate only delivered bytes; production files stay untouched.
The existing PR2 IA non-vacuity suite covers M6 (Diary as a primary tab).
"""
import json

import pytest

from test_training_execution_boundary import training_page  # noqa: F401
from test_nutrition_vnext_pr2_freshness_browser import (
    test_diary_commit_refreshes_visible_canonical_projections as assert_freshness,
    test_hidden_open_history_stays_lazy_and_refreshes_on_return as assert_hidden_history,
    test_older_canonical_read_cannot_overwrite_post_commit_state as assert_read_order,
    test_superseding_failed_read_keeps_post_commit_failure_visible as assert_failure_order,
    test_closed_history_read_failure_reports_unconfirmed_freshness_on_open as assert_closed_failure,
)


@pytest.mark.parametrize('mutation,history_open,match', [
    ('M1', False, 'count'),
    ('M2', False, 'count'),
    ('M3', False, '725'),
    ('M4', True, 'count'),
    ('M5', False, 'meal-log/history'),
])
def test_freshness_mutation_is_detected(app, auth_user, client, training_page,
                                      mutation, history_open, match):
    page, traffic, _, _ = training_page
    original = client.get('/static/nutrition.js').get_data(as_text=True).replace('\r\n', '\n')
    if mutation == 'M1':
        mutated = original.replace('    loadTodayData(true);', '    /* missing parent refresh */')
    elif mutation == 'M2':
        mutated = original
        initial = []
        def stale_today(route):
            response = client.get('/meal-log/today')
            if not initial:
                initial.append(response.json)
            traffic.append(('/meal-log/today', None, response.status_code))
            route.fulfill(status=200, content_type='application/json', body=json.dumps(initial[0]))
        page.route('**/meal-log/today', stale_today)
    elif mutation == 'M3':
        # Cards become current, aggregates stay stale: the calorie assertion
        # must catch this despite a real canonical read and correct card count.
        mutated = original.replace('    loadTodayData(true);',
            "    fetch('/meal-log/today').then(r => r.json()).then(d => renderTimeline(d.meals));")
    elif mutation == 'M4':
        mutated = original.replace('      loadMealHistory(true);', '      /* missing visible History refresh */')
    else:
        mutated = original.replace("    if (_nutritionNavigation.mode === 'today' &&\n"
            "        document.getElementById('nutrition-tool-history').open) {", '    if (true) {')
    assert mutated != original or mutation == 'M2', 'mutation failed to apply'
    page.route('**/static/nutrition.js*', lambda route: route.fulfill(
        status=200, content_type='application/javascript', body=mutated))
    with pytest.raises(AssertionError, match=match):
        assert_freshness(app, auth_user, client, training_page, history_open, 'en', 390)


@pytest.mark.parametrize('mutation', ['hidden-history', 'today-order', 'history-order'])
def test_delayed_response_mutation_is_detected(app, auth_user, client, training_page, mutation):
    page, _, _, _ = training_page
    original = client.get('/static/nutrition.js').get_data(as_text=True).replace('\r\n', '\n')
    if mutation == 'hidden-history':
        mutated = original.replace("      _nutritionLoadedTools.delete('history');", '')
    else:
        name = 'today' if mutation == 'today-order' else 'history'
        mutated = original.replace(f'    if (generation !== _{name}ReadGeneration) return;', '')
    assert mutated != original, 'mutation failed to apply'
    page.route('**/static/nutrition.js*', lambda route: route.fulfill(
        status=200, content_type='application/javascript', body=mutated))
    with pytest.raises(AssertionError, match='count'):
        if mutation == 'hidden-history':
            assert_hidden_history(app, auth_user, client, training_page)
        else:
            assert_read_order(app, auth_user, client, training_page, name)


@pytest.mark.parametrize('mutation', ['today-warning', 'history-warning', 'closed-warning', 'hidden-generation'])
def test_failure_order_mutation_is_detected(app, auth_user, client, training_page, mutation):
    page, _, _, _ = training_page
    original = client.get('/static/nutrition.js').get_data(as_text=True).replace('\r\n', '\n')
    if mutation == 'hidden-generation':
        mutated = original.replace('      ++_historyReadGeneration;', '')
    else:
        name = 'today' if mutation == 'today-warning' else 'history'
        mutated = original.replace(f'_{name}RefreshPending = true;', f'_{name}RefreshPending = false;')
    assert mutated != original, 'mutation failed to apply'
    page.route('**/static/nutrition.js*', lambda route: route.fulfill(
        status=200, content_type='application/javascript', body=mutated))
    with pytest.raises(AssertionError, match='visible'):
        if mutation in {'closed-warning', 'hidden-generation'}:
            assert_closed_failure(app, auth_user, client, training_page)
        else:
            assert_failure_order(app, auth_user, client, training_page, name)
