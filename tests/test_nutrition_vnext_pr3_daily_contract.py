"""NUTR-PR3 — Nutrition Today as one daily execution surface (structural contract).

Rendered `/nutrition` plus the shipped script, asserted by meaning rather than
pixels:

  * Nutrition keeps EXACTLY two primary modes (Today / Plan);
  * Today has ONE dominant "Log food" front door into the unchanged chooser;
  * the factual daily summary precedes every secondary capability;
  * Diary / History / Water stay secondary disclosures, never tabs;
  * the shipped hydration reliability contract is untouched;
  * no NUTR-PR4 method redesign, no new endpoint, no new global destination;
  * `/nutrition` stays the one canonical route; locale parity is exact.
"""
import html as html_lib
import json
import re
from pathlib import Path

import pytest

from test_nutrition_vnext_pr2_navigation_contract import Elements, render

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (ROOT / 'static' / 'nutrition.js').read_text(encoding='utf-8').replace('\r\n', '\n')

# Every entry into food logging the chooser offered before PR3, in order. PR3
# converges the front door only; the rooms behind it are NUTR-PR4's.
CHOOSER_METHODS = ['logTakePhoto', 'logScanBarcode', 'logMenuScan', 'logVoice', 'logManual']

# The network surface Nutrition's script is allowed to reach — exactly the
# pre-PR3 set. A new endpoint here is a backend/API change PR3 does not own.
KNOWN_ENDPOINTS = {
    "'/meal-log/today'", "'/meal-log/entry/'", "'/meal-log'", "'/meal-log/review'",
    "'/meal-log/history'", "'/nutrition-plan'", "'/nutrition-plan/save'",
    "'/nutrition-plan/active'", "'/api/quick-add-meal'", "'/water'",
    "'/api/food/barcode?code='", "'/api/food/search?q='", "'/api/diary/today'",
    "'/api/diary/meal'", "'/api/diary/meal/'", "'/api/diary/item/'",
    'url',   # fetchServings: '/api/food/<id>/servings' | '/api/food/servings-by-name?name='
}

PR3_KEYS = [
    'nutrition.todays_intake', 'nutrition.target_unavailable', 'nutrition.remaining_left',
    'nutrition.remaining_over', 'nutrition.meals_unavailable', 'nutrition.intake_stale',
    'nutrition.try_again', 'nutrition.log_food', 'nutrition.logged_meals',
    'nutrition.meal_count', 'nutrition.from_plan', 'nutrition.planned',
    'nutrition.log_planned', 'nutrition.logged_state', 'nutrition.plan_unavailable',
    'nutrition.quick_add_uncertain',
]

ARCHITECTURE_WORDS = ('MealLog', 'NutritionPlan', 'WaterLog', 'CustomMeal', 'canonical',
                      'projection', 'authority', 'invalid', 'enum')
MORALIZING_WORDS = ('bad', 'overate', 'off track', 'behind', 'ahead', 'cheat', 'score')


def _catalog(language):
    return json.loads((ROOT / 'locales' / f'{language}.json').read_text(encoding='utf-8'))


def _body(html):
    """Rendered markup without <script> blocks: `_head.html` inlines the whole
    locale catalog into window.I18N, so copy must be checked outside it."""
    return re.sub(r'<script\b[^>]*>.*?</script>', '', html, flags=re.S)


def _today(html):
    body = _body(html)
    start = body.index('id="panel-today"')
    return body[start:body.index('id="panel-plan"')]


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_exactly_today_and_plan_are_primary(client, make_user, login, language):
    rows = Elements(render(client, make_user, login, language)).rows
    tabs = [a for _, a, _ in rows if a.get('role') == 'tab']
    assert [a['data-tab-name'] for a in tabs] == ['today', 'plan']
    assert [a['aria-controls'] for a in tabs] == ['panel-today', 'panel-plan']
    for tool in ('diary', 'history', 'water'):
        tag, attrs, ancestors = next(r for r in rows if r[1].get('id') == 'nutrition-tab-' + tool)
        assert tag == 'summary' and 'role' not in attrs, tool
        assert 'panel-today' in ancestors, tool


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_one_dominant_log_food_action(client, make_user, login, language):
    html = render(client, make_user, login, language)
    body = _body(html)
    rows = Elements(html).rows
    openers = [(tag, a, anc) for tag, a, anc in rows if a.get('data-action') == 'openLogSheet']
    assert len(openers) == 1, openers
    tag, attrs, ancestors = openers[0]
    assert tag == 'button' and attrs['id'] == 'log-food-btn'
    assert 'btn-volt' in attrs['class'].split()
    assert attrs.get('aria-haspopup') == 'dialog' and attrs.get('aria-controls') == 'log-sheet'
    assert 'nut-day' in ancestors            # it lives inside the daily summary
    label = re.search(r'id="log-food-btn"[^>]*>(.*?)</button>', body, re.S).group(1)
    assert html_lib.unescape(re.sub(r'<[^>]+>', '', label)).strip() == _catalog(language)['nutrition.log_food']
    # No floating duplicate, and no method is exposed outside the chooser.
    assert 'log-fab' not in body and 'has-fab-rail' not in body
    for method in CHOOSER_METHODS:
        placed = [anc for _, a, anc in rows if a.get('data-action') == method]
        assert len(placed) == 1 and 'log-sheet' in placed[0], method
    # Tier 1 is the only filled primary on the server-rendered Today surface.
    today = _today(html)
    assert len(re.findall(r'class="[^"]*\bbtn-volt\b', today)) == 1


def test_log_food_opens_the_unchanged_chooser():
    template = (ROOT / 'templates' / 'nutrition.html').read_text(encoding='utf-8')
    sheet = template[template.index('id="log-sheet"'):template.index('<!-- ── MANUAL ENTRY SHEET')]
    assert re.findall(r'data-action="(log\w+)"', sheet) == CHOOSER_METHODS
    assert 'function openLogSheet()' in SCRIPT


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_daily_state_precedes_secondary_capabilities(client, make_user, login, language):
    today = _today(render(client, make_user, login, language))
    order = ['id="nut-day"', 'id="nut-intake"', 'id="nut-target"', 'id="nut-remaining"',
             'id="macro-protein"', 'id="log-food-btn"', 'id="meal-timeline"', 'id="qab-water"',
             'id="quick-add-section"', 'id="nutrition-tool-history"', 'id="nutrition-tool-water"',
             'id="nutrition-tool-diary"', 'id="review-btn"', 'class="coach-entry"']
    positions = [today.index(marker) for marker in order]
    assert positions == sorted(positions), dict(zip(order, positions))
    # The first heading on Today names the daily state, not a workflow.
    first_h2 = re.search(r'<h2[^>]*>(.*?)</h2>', today, re.S).group(1)
    assert html_lib.unescape(first_h2).strip() == _catalog(language)['nutrition.todays_intake']


def test_initial_markup_claims_no_number_before_a_read(client, make_user, login):
    """Server-rendered placeholders are UNKNOWN, never a zero the reads have
    not proven."""
    today = _today(render(client, make_user, login, 'en'))
    summary = today[:today.index('id="log-food-btn"')]
    for marker in ('id="nut-intake">', 'id="nut-target">', 'id="macro-protein">',
                   'id="macro-karb">', 'id="macro-yag">'):
        value = summary[summary.index(marker) + len(marker):].split('<', 1)[0]
        assert value == '—', (marker, value)
    assert 'data-intake-state="loading"' in summary and 'data-target-state="pending"' in summary


def test_hydration_reliability_contract_is_unchanged():
    for state in ("'loading'", "'confirmed'", "'saving'", "'unavailable'", "'unconfirmed'"):
        assert state in SCRIPT
    assert 'fc_water' not in SCRIPT and 'localStorage' not in SCRIPT
    # Water keeps exactly one eager read and no reader of its own on open.
    assert SCRIPT.count('initWaterButton();') == 1
    assert "addEventListener('toggle', () => loadWater" not in SCRIPT


def test_no_new_endpoint_and_no_method_redesign():
    literals = set(re.findall(r"fetch\(\s*('[^']*'|url)", SCRIPT))
    assert literals <= KNOWN_ENDPOINTS, literals - KNOWN_ENDPOINTS
    # The existing method handlers and their write boundaries are intact.
    for anchor in ('async function submitPhotoMeal()', 'async function resolveBarcode(code)',
                   'window.CW.startScan()', 'async function logMeal()',
                   'async function logProviderFoodToLedger(food, ogun)',
                   'async function logDiaryMeal(mealName)', 'async function deleteMeal('):
        assert anchor in SCRIPT, anchor
    assert 'provider_food' in SCRIPT and 'override_macros' in SCRIPT


def test_no_new_route_or_destination(app):
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert '/nutrition' in rules and '/supplements' in rules
    assert not any(r.startswith('/nutrition/') for r in rules), sorted(
        r for r in rules if r.startswith('/nutrition/'))
    from app.nav import primary_destinations
    assert [d['id'] for d in primary_destinations()] == ['today', 'plan', 'coach', 'progress']
    # Nutrition stays a child of Plan in the shell, never a fifth destination.
    assert 'nutrition' in next(d for d in primary_destinations() if d['id'] == 'plan')['active_when']


def test_today_read_contract_is_the_one_pr3_consumes(client, make_user, login):
    """The surface composes the existing read; its shape is unchanged."""
    make_user('pr3-read', profile_complete=True)
    login('pr3-read')
    payload = client.get('/meal-log/today').get_json()
    assert set(payload) == {'meals', 'totals', 'tarih', 'targets', 'remaining'}
    assert payload['targets'] is None and payload['meals'] == []


def test_locale_parity_and_plain_copy():
    en, tr = _catalog('en'), _catalog('tr')
    assert set(en) == set(tr)
    for key in PR3_KEYS:
        for catalog in (en, tr):
            copy = catalog[key]
            assert copy.strip(), key
            for word in ARCHITECTURE_WORDS:
                assert word.lower() not in copy.lower(), (key, copy)
    for key in PR3_KEYS:
        for word in MORALIZING_WORDS:
            assert not re.search(r'\b' + re.escape(word) + r'\b', en[key].lower()), (key, en[key])
    for catalog in (en, tr):
        for key in ('nutrition.target_absent', 'nutrition.target_unavailable'):
            assert not re.search(r'\d', catalog[key]), key
    for key in ('nutrition.remaining_left', 'nutrition.remaining_over', 'nutrition.meal_count'):
        assert '{n}' in en[key] and '{n}' in tr[key], key


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_rendered_today_speaks_plain_language(client, make_user, login, language):
    today = re.sub(r'<[^>]+>', ' ', _today(render(client, make_user, login, language)))
    for word in ARCHITECTURE_WORDS:
        assert word not in today, word
