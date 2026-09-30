"""NUTR-PR4 — the "Log food" chooser in a real browser.

The real rendered page and shipped scripts run against the authenticated Flask
client (`training_page`). Only EXTERNAL providers are replaced: FatSecret
discovery answers come from browser routes, and the server-side serving
authority (`mobile_food_discovery.servings`) and the LLM macro estimator are
stubbed. Every write reaches the real routes and the real MealLog table, and
every count below is read back from that table.

  one front door · each method reaches its EXISTING workflow · opening the
  chooser is free · discovery / analysis / staging / navigation ≠ intake ·
  one confirmed action = exactly one canonical write · focus always lands
  somewhere real · one method failing never takes another down
"""
import base64
import json
import re
from collections import Counter

import pytest
from playwright.sync_api import expect

from app.extensions import db
from app.models import CustomMeal, MealLog
from test_training_execution_boundary import training_page  # noqa: F401
from test_ux3_pr4_nutrition_placement_browser import stub_meal_macro_provider  # noqa: F401
from test_nutrition_vnext_pr2_navigation_browser import paths
from test_nutrition_vnext_pr3_daily_browser import CATALOG, PLAN, fail, open_today, seed

OPEN = re.compile(r'\bopen\b')
PRIMARY = ['search', 'barcode', 'menu', 'quick-add', 'build-meal']
INITIAL = Counter({'/nutrition': 1, '/meal-log/today': 1, '/nutrition-plan/active': 1,
                   '/water': 1, '/notifications/unread-count': 1, '/coach/history': 1,
                   '/nutrition-day-view': 1})   # NUTR-PR6: one day-view read at load
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')

# One provider product per discovery method: what FatSecret would answer to
# the browser (discovery) and what the server's serving authority re-reads
# before it writes (the write never trusts the browser's numbers).
SEARCH_FOOD = {'name': 'Rolled oats', 'brand': '', 'food_id': '8801', 'serving': '100 g',
               'is_per_serving': False,
               'macros': {'calories': 389, 'protein': 17, 'carbs': 66, 'fat': 7},
               'per_100g': {'calories': 389, 'protein': 17, 'carbs': 66, 'fat': 7}}
SEARCH_SERVINGS = [{'serving_id': 's80', 'serving_description': '1 cup', 'metric_serving_amount': 80,
                    'calories': 311, 'protein': 13.6, 'carbs': 52.8, 'fat': 5.6, 'is_bulk': False}]
BARCODE_PRODUCT = {'food_id': '7701', 'name': 'Protein bar', 'brand': 'Acme',
                   'servings': [{'serving_id': 'b60', 'serving_description': '1 bar',
                                 'metric_serving_amount': 60, 'calories': 210, 'protein': 20,
                                 'carbs': 22, 'fat': 7, 'is_bulk': False}]}
AUTHORITY = {  # food_id → (serving_id, grams, kcal, protein, carbs, fat)
    '8801': ('s80', 80, 311, 13.6, 52.8, 5.6),
    '7701': ('b60', 60, 210, 20, 22, 7),
}
MENU_ANALYSIS = {'success': True, 'categories': {'Mains': [
    {'name': 'Grilled chicken', 'score': 60, 'reason': 'High protein',
     'macros': {'calories': 450, 'protein': 42, 'carbs': 12, 'fat': 20}}]},
    'coach_picks': [{'name': 'Grilled chicken', 'score': 60, 'reason': 'High protein',
                     'macros': {'calories': 450, 'protein': 42, 'carbs': 12, 'fat': 20}}]}

# Counts every listener and observer the page registers, so a chooser that
# re-binds on each open is visible as a number, not a guess.
INSTRUMENT = """
(() => {
  window.__listeners = 0; window.__observers = 0;
  const add = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function () { window.__listeners++; return add.apply(this, arguments); };
  const MO = window.MutationObserver;
  if (MO) window.MutationObserver = class extends MO { constructor(cb) { super(cb); window.__observers++; } };
  try { delete window.BarcodeDetector; } catch (e) {}
})();
"""


@pytest.fixture
def provider(monkeypatch):
    """The server-side serving authority the canonical write re-reads."""
    from app.services import mobile_food_discovery

    def servings(food_id):
        if food_id not in AUTHORITY:
            return None
        sid, grams, *values = AUTHORITY[food_id]
        keys = ('energy_kcal', 'protein_g', 'carbohydrate_g', 'fat_g')
        return {'provider': 'fatsecret', 'food_id': food_id, 'name': 'Provider food', 'brand': '',
                'servings': [{'serving_id': sid, 'description': 'serving',
                              'metric_mass': {'amount': grams, 'unit': 'g'},
                              'nutrition': dict(zip(keys, values)),
                              'nutrition_per_100g': dict(zip(keys, (v * 100 / grams for v in values)))}]}
    monkeypatch.setattr(mobile_food_discovery, 'servings', servings)


def start(app, auth_user, training_page, language='en', plan=True, size=(390, 844)):
    seed(app, auth_user.id, language, plan=plan)
    page, traffic, _, _ = training_page
    page.add_init_script(INSTRUMENT)
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    open_today(page, *size)
    expect(page.locator('#quick-add-section')).not_to_have_attribute('data-plan-state', 'loading')
    page.wait_for_timeout(200)
    return page, traffic, errors


def canned(page, pattern, body, status=200, calls=None):
    def answer(route):
        if calls is not None:
            calls.append(route.request.url)
        route.fulfill(status=status, content_type='application/json', body=json.dumps(body))
    page.route(pattern, answer)


def meal_logs(app, user_id):
    with app.app_context():
        return MealLog.query.filter_by(user_id=user_id).count()


def writes(traffic):
    """Every canonical consumption write the page sent (real server answers)."""
    return [(p, s) for p, _, s in traffic
            if p in ('/meal-log', '/api/quick-add-meal') or re.fullmatch(r'/api/diary/meal/\d+/log', p)]


def settle(page):
    """Measure the sheet where it rests, not mid slide-up."""
    # Two frames first: under load the slide-up may not exist yet when asked.
    page.evaluate('''() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))
        .then(() => Promise.all(document.getAnimations().map(a => a.finished)))''')


def open_chooser(page):
    page.locator('#log-food-btn').click()
    expect(page.locator('#log-sheet')).to_have_class(OPEN)


def choose(page, method):
    open_chooser(page)
    option = page.locator(f'#log-sheet [data-method="{method}"]')
    expect(option).to_be_visible()                  # a missing method fails, not times out
    option.click()
    expect(page.locator('#log-sheet')).not_to_have_class(OPEN)


def assert_front_door_closed(page):
    expect(page.locator('#log-sheet')).not_to_have_class(OPEN)
    expect(page.locator('#log-food-btn')).to_have_attribute('aria-expanded', 'false')


def search_and_pick(page, calls=None):
    canned(page, '**/api/food/search?q=*', {'results': [SEARCH_FOOD]}, calls=calls)
    canned(page, '**/api/food/8801/servings', {'servings': SEARCH_SERVINGS}, calls=calls)
    page.locator('#food-search-input').fill('oats')
    page.locator('#food-autocomplete-dropdown .autocomplete-item').first.click()
    expect(page.locator('#serving-modal')).to_have_class(OPEN)
    expect(page.locator('#sm-serving-row')).to_be_visible()
    page.locator('#sm-confirm-btn').click()
    expect(page.locator('#selected-foods-items .selected-food-item')).to_have_count(1)


def add_photo(page):
    with page.expect_file_chooser() as chooser:
        choose(page, 'photo')
    chooser.value.set_files(files=[{'name': 'meal.png', 'mimeType': 'image/png', 'buffer': PNG}])
    expect(page.locator('#photo-modal')).to_have_class(OPEN)


# ── THE CHOOSER ITSELF ──────────────────────────────────────────────────


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_chooser_is_one_titled_dialog_of_labelled_methods(app, auth_user, training_page, language):
    page, _, errors = start(app, auth_user, training_page, language)
    catalog = CATALOG[language]
    expect(page.locator('#log-sheet')).to_be_hidden()           # closed state takes no layout
    open_chooser(page)
    dialog = page.get_by_role('dialog', name=catalog['nutrition.log_food'])
    expect(dialog).to_be_visible()
    expect(page.locator('#log-sheet-lead')).to_have_text(catalog['nutrition.log_food_prompt'])
    methods = page.locator('#log-sheet .log-sheet-opt')
    expect(methods).to_have_count(6)
    assert [m.get_attribute('data-method') for m in methods.all()] == PRIMARY + ['photo']
    titles = {'search': 'log_manual', 'barcode': 'log_barcode', 'menu': 'log_menu',
              'quick-add': 'log_quick_add', 'build-meal': 'log_build_meal', 'photo': 'log_photo'}
    for method, key in titles.items():
        button = dialog.get_by_role('button', name=re.compile(re.escape(catalog['nutrition.' + key])))
        expect(button).to_have_attribute('data-method', method)
    expect(dialog.get_by_role('button', name=catalog['nutrition.close'])).to_be_visible()
    # Reading order is visual order.
    tops = [m.bounding_box()['y'] for m in methods.all()]
    assert tops == sorted(tops), tops
    assert 'nutrition.' not in page.locator('#log-sheet').inner_text()
    assert errors == []


def test_opening_and_dismissing_is_local_and_returns_focus(app, auth_user, training_page):
    page, traffic, errors = start(app, auth_user, training_page)
    button = page.locator('#log-food-btn')
    before = page.evaluate('() => [window.__listeners, window.__observers]')
    traffic.clear()
    dismissals = {
        'escape': lambda: page.keyboard.press('Escape'),
        'backdrop': lambda: page.locator('#log-sheet').click(position={'x': 5, 'y': 5}),
        'close': lambda: page.locator('#log-sheet .log-sheet-close').click(),
    }
    for _ in range(3):
        for name, dismiss in dismissals.items():
            button.focus()
            page.keyboard.press('Enter')
            expect(page.locator('#log-sheet')).to_have_class(OPEN)
            expect(button).to_have_attribute('aria-expanded', 'true')
            # Focus moved INTO the chooser, onto its first method.
            expect(page.locator('#log-sheet [data-method="search"]')).to_be_focused()
            dismiss()
            assert_front_door_closed(page)
            expect(button).to_be_focused(), name
    page.wait_for_timeout(300)
    assert paths(traffic) == Counter(), paths(traffic)            # 0 requests, any dismissal
    assert page.evaluate('() => [window.__listeners, window.__observers]') == before
    assert errors == []


def test_tab_stays_inside_the_open_chooser(app, auth_user, training_page):
    page, _, _ = start(app, auth_user, training_page)
    open_chooser(page)
    first = page.locator('#log-sheet .log-sheet-close')
    last = page.locator('#log-sheet [data-method="photo"]')
    last.focus()
    page.keyboard.press('Tab')
    expect(first).to_be_focused()
    page.keyboard.press('Shift+Tab')
    expect(last).to_be_focused()
    for _ in range(12):
        page.keyboard.press('Tab')
        assert page.evaluate("() => document.getElementById('log-sheet').contains(document.activeElement)")


# ── EACH METHOD REACHES ITS EXISTING WORKFLOW, AND ONLY THAT ─────────────


def test_search_method_opens_the_search_sheet_without_a_request(app, auth_user, training_page):
    page, traffic, _ = start(app, auth_user, training_page)
    traffic.clear()
    choose(page, 'search')
    expect(page.locator('#manual-sheet')).to_have_class(OPEN)
    expect(page.get_by_role('dialog', name=CATALOG['en']['nutrition.log_manual'])).to_be_visible()
    expect(page.locator('#food-search-input')).to_be_focused()
    assert_front_door_closed(page)
    page.wait_for_timeout(300)
    assert paths(traffic) == Counter()                         # no provider call before a query
    page.keyboard.press('Escape')
    expect(page.locator('#manual-sheet')).not_to_have_class(OPEN)
    expect(page.locator('#log-food-btn')).to_be_focused()


def test_barcode_method_opens_the_scanner_without_a_lookup(app, auth_user, training_page):
    page, traffic, _ = start(app, auth_user, training_page)
    traffic.clear()
    choose(page, 'barcode')
    expect(page.locator('#scan-overlay')).to_have_class(OPEN)
    expect(page.locator('#barcode-manual-input')).to_be_focused()
    page.wait_for_timeout(300)
    assert paths(traffic) == Counter()                         # no lookup until a code exists
    page.locator('#scan-overlay .scan-close').click()
    expect(page.locator('#scan-overlay')).not_to_have_class(OPEN)
    expect(page.locator('#log-food-btn')).to_be_focused()


def test_menu_method_opens_the_existing_scanner_without_analysis(app, auth_user, training_page):
    page, traffic, _ = start(app, auth_user, training_page)
    traffic.clear()
    choose(page, 'menu')
    expect(page.locator('#cw-scan')).to_have_class(re.compile(r'\bcw-open\b'))
    page.wait_for_timeout(300)
    assert not any(p.startswith(('/api/menu', '/api/proxy', '/ask')) for p in paths(traffic)), \
        paths(traffic)
    assert paths(traffic) == Counter()


def test_quick_add_routes_to_the_planned_rows(app, auth_user, training_page):
    page, traffic, _ = start(app, auth_user, training_page)
    open_chooser(page)
    option = page.locator('#log-sheet [data-method="quick-add"]')
    expect(option).to_have_attribute('data-plan-state', 'available')
    expect(option.locator('.lso-sub')).to_have_text(CATALOG['en']['nutrition.log_quick_add_sub'])
    page.keyboard.press('Escape')
    traffic.clear()
    choose(page, 'quick-add')
    row = page.locator('#qab-kahvalti')
    expect(row).to_be_focused()                                # the first planned meal
    expect(row.locator('.qab-badge')).to_have_text(CATALOG['en']['nutrition.planned'])
    page.wait_for_timeout(300)
    assert paths(traffic) == Counter()                         # routing reuses loaded state


def test_build_meal_opens_the_builder_and_reads_it_once(app, auth_user, training_page):
    page, traffic, _ = start(app, auth_user, training_page)
    traffic.clear()
    choose(page, 'build-meal')
    expect(page.locator('#nutrition-tool-diary')).to_have_attribute('open', '')
    expect(page.locator('#diary-builder-title')).to_be_focused()
    expect(page.locator('#diary-meals')).to_have_attribute('data-diary-state', 'available')
    expect(page.locator('#panel-diary .diary-meal-card')).to_have_count(4)
    page.wait_for_timeout(300)
    assert paths(traffic) == Counter({'/api/diary/today': 1}), paths(traffic)
    traffic.clear()
    choose(page, 'build-meal')                                 # again: already loaded
    expect(page.locator('#diary-builder-title')).to_be_focused()
    page.wait_for_timeout(300)
    assert paths(traffic) == Counter(), paths(traffic)
    expect(page.locator('#nutrition-tab-today')).to_have_attribute('aria-selected', 'true')


def test_photo_method_is_retained_and_logs_only_on_confirm(
        app, auth_user, training_page, stub_meal_macro_provider):
    page, traffic, _ = start(app, auth_user, training_page)
    before = meal_logs(app, auth_user.id)
    traffic.clear()
    add_photo(page)
    page.wait_for_timeout(300)
    assert paths(traffic) == Counter() and meal_logs(app, auth_user.id) == before
    page.keyboard.press('Escape')                              # cancel → nothing written
    expect(page.locator('#photo-modal')).not_to_have_class(OPEN)
    expect(page.locator('#log-food-btn')).to_be_focused()
    assert writes(traffic) == [] and meal_logs(app, auth_user.id) == before
    add_photo(page)
    page.locator('#photo-note-input').fill('Salad bowl')
    page.locator('#photo-confirm-btn').click()
    expect(page.locator('#photo-modal')).not_to_have_class(OPEN)
    expect(page.locator('#meal-timeline')).to_contain_text('Salad bowl')
    assert writes(traffic) == [('/meal-log', 200)]
    assert meal_logs(app, auth_user.id) == before + 1


# ── WRITE BOUNDARIES: discovery / analysis / staging ≠ intake ───────────


def test_search_writes_once_and_only_on_the_explicit_log(app, auth_user, client, training_page,
                                                          provider):
    page, traffic, _ = start(app, auth_user, training_page)
    before = meal_logs(app, auth_user.id)
    intake = page.locator('#nut-intake').inner_text()
    traffic.clear()
    choose(page, 'search')
    search_and_pick(page)
    page.wait_for_timeout(200)
    # Found, served, picked: still nothing consumed.
    assert writes(traffic) == [] and meal_logs(app, auth_user.id) == before
    expect(page.locator('#nut-intake')).to_have_text(intake)
    page.locator('#manual-sheet [data-action="logMeal"]').click()
    expect(page.locator('#manual-sheet')).not_to_have_class(OPEN)
    expect(page.locator('#meal-timeline')).to_contain_text('Provider food')
    assert writes(traffic) == [('/meal-log', 200)]
    assert meal_logs(app, auth_user.id) == before + 1
    body = json.loads(next(d for p, d, _ in traffic if p == '/meal-log'))
    assert body['provider_food']['food_id'] == '8801' and body['provider_food']['serving_id'] == 's80'
    assert 'override_macros' not in body                       # the server scales, not the page
    today = client.get('/meal-log/today').get_json()
    expect(page.locator('#nut-intake')).to_have_text(str(round(today['totals']['kalori'])))


def test_barcode_resolution_is_discovery_not_a_log(app, auth_user, client, training_page, provider):
    page, traffic, _ = start(app, auth_user, training_page)
    before = meal_logs(app, auth_user.id)
    lookups = []
    canned(page, '**/api/food/barcode?code=*', BARCODE_PRODUCT, calls=lookups)
    traffic.clear()
    choose(page, 'barcode')
    page.locator('#barcode-manual-input').fill('8690000000001')
    page.locator('#scan-overlay [data-action="onBarcodeManual"]').click()
    expect(page.locator('#serving-modal')).to_have_class(OPEN)
    expect(page.locator('#sm-food-name')).to_have_text('Protein bar')
    page.wait_for_timeout(300)
    assert len(lookups) == 1
    assert writes(traffic) == [] and meal_logs(app, auth_user.id) == before   # resolved ≠ logged
    page.locator('#sm-confirm-btn').click()
    expect(page.locator('#serving-modal')).not_to_have_class(OPEN)
    expect(page.locator('#meal-timeline')).to_contain_text('Provider food')
    assert writes(traffic) == [('/meal-log', 200)]
    assert meal_logs(app, auth_user.id) == before + 1
    body = json.loads(next(d for p, d, _ in traffic if p == '/meal-log'))
    assert body['provider_food']['discovery_source'] == 'barcode'
    assert paths(traffic)['/meal-log/today'] == 1               # one canonical refresh


def test_barcode_cancel_writes_nothing_and_returns_focus(app, auth_user, training_page, provider):
    page, traffic, _ = start(app, auth_user, training_page)
    before = meal_logs(app, auth_user.id)
    canned(page, '**/api/food/barcode?code=*', BARCODE_PRODUCT)
    choose(page, 'barcode')
    page.locator('#barcode-manual-input').fill('8690000000001')
    page.keyboard.press('Tab')
    page.locator('#scan-overlay [data-action="onBarcodeManual"]').click()
    expect(page.locator('#serving-modal')).to_have_class(OPEN)
    page.keyboard.press('Escape')
    expect(page.locator('#serving-modal')).not_to_have_class(OPEN)
    expect(page.locator('#log-food-btn')).to_be_focused()
    page.wait_for_timeout(300)
    assert writes(traffic) == [] and meal_logs(app, auth_user.id) == before


def test_barcode_not_found_is_unavailable_and_search_still_works(app, auth_user, training_page):
    page, traffic, errors = start(app, auth_user, training_page)
    before = meal_logs(app, auth_user.id)
    intake = page.locator('#nut-intake').inner_text()
    canned(page, '**/api/food/barcode?code=*', {'error': 'not_found'}, status=404)
    choose(page, 'barcode')
    page.locator('#barcode-manual-input').fill('0000000000000')
    page.locator('#scan-overlay [data-action="onBarcodeManual"]').click()
    expect(page.locator('#toast-wrap .toast-error')).to_contain_text(
        CATALOG['en']['nutrition.barcode_not_found'])
    expect(page.locator('#serving-modal')).not_to_have_class(OPEN)
    expect(page.locator('#nut-intake')).to_have_text(intake)          # never 0, never "logged"
    assert writes(traffic) == [] and meal_logs(app, auth_user.id) == before
    expect(page.locator('#log-food-btn')).to_be_focused()
    choose(page, 'search')                                              # another method still works
    expect(page.locator('#manual-sheet')).to_have_class(OPEN)
    assert errors == []


def test_search_unavailable_leaves_barcode_usable(app, auth_user, training_page, provider):
    page, traffic, _ = start(app, auth_user, training_page)
    page.route('**/api/food/search?q=*', fail)
    choose(page, 'search')
    page.locator('#food-search-input').fill('oats')
    page.wait_for_timeout(800)
    expect(page.locator('#food-autocomplete-dropdown .autocomplete-item')).to_have_count(0)
    page.keyboard.press('Escape')
    canned(page, '**/api/food/barcode?code=*', BARCODE_PRODUCT)
    choose(page, 'barcode')
    page.locator('#barcode-manual-input').fill('8690000000001')
    page.locator('#scan-overlay [data-action="onBarcodeManual"]').click()
    expect(page.locator('#serving-modal')).to_have_class(OPEN)
    assert writes(traffic) == []


def test_menu_analysis_is_not_a_log(app, auth_user, training_page):
    page, traffic, _ = start(app, auth_user, training_page)
    before = meal_logs(app, auth_user.id)
    intake = page.locator('#nut-intake').inner_text()
    analysed = []
    canned(page, '**/api/proxy/scan-menu', {'title': 'Bistro', 'headings': ['Mains'],
                                            'body_text': 'Grilled chicken with greens, 18 TL'},
           calls=analysed)
    canned(page, '**/api/menu/analyze', MENU_ANALYSIS, calls=analysed)
    choose(page, 'menu')
    expect(page.locator('#cw-scan')).to_have_class(re.compile(r'\bcw-open\b'))
    # The scanner decodes a menu QR into a URL; hand it that URL as the scan would.
    page.evaluate("() => { CW.stopScan(); CW.processMenuUrl('https://bistro.example/menu'); }")
    expect(page.locator('.cw-dish').first).to_contain_text('Grilled chicken')
    page.wait_for_timeout(300)
    assert len(analysed) == 2                                   # analysis really ran
    assert writes(traffic) == [] and meal_logs(app, auth_user.id) == before
    expect(page.locator('#nut-intake')).to_have_text(intake)
    expect(page.locator('#toast-wrap .toast-success')).to_have_count(0)
    assert CATALOG['en']['nutrition.logged_state'] not in page.locator('#meal-timeline').inner_text()


def test_menu_scanner_unavailable_leaves_the_chooser_usable(app, auth_user, training_page):
    page, traffic, errors = start(app, auth_user, training_page)
    page.evaluate('() => { window.CW = undefined; }')
    choose(page, 'menu')
    expect(page.locator('#toast-wrap .toast-error')).to_contain_text(
        CATALOG['en']['nutrition.menu_unavailable'])
    expect(page.locator('#log-food-btn')).to_be_focused()
    choose(page, 'search')
    expect(page.locator('#manual-sheet')).to_have_class(OPEN)
    assert writes(traffic) == [] and errors == []


# ── QUICK ADD: routed into the ONE planned-row state machine ────────────


def test_quick_add_writes_only_through_the_planned_row(app, auth_user, training_page):
    page, traffic, _ = start(app, auth_user, training_page)
    before = meal_logs(app, auth_user.id)
    traffic.clear()
    choose(page, 'quick-add')
    row = page.locator('#qab-kahvalti')
    expect(row).to_be_focused()
    page.wait_for_timeout(200)
    assert writes(traffic) == [] and meal_logs(app, auth_user.id) == before   # routing alone
    page.keyboard.press('Enter')                                # the user acts on the row
    expect(row.locator('.qab-action')).to_have_text(CATALOG['en']['nutrition.logged_state'])
    expect(row).to_have_attribute('data-write-state', 'logged')
    page.wait_for_timeout(300)
    assert writes(traffic) == [('/api/quick-add-meal', 200)]
    assert meal_logs(app, auth_user.id) == before + 1
    # Quick add again: the logged row stays locked (PR3 M10) and focus moves
    # to the next plannable meal; nothing is sent by routing.
    choose(page, 'quick-add')
    expect(page.locator('#qab-aksam')).to_be_focused()
    expect(row).to_be_disabled()
    page.wait_for_timeout(300)
    assert writes(traffic) == [('/api/quick-add-meal', 200)]
    assert meal_logs(app, auth_user.id) == before + 1


def test_quick_add_ambiguous_row_stays_locked_when_routed_again(app, auth_user, training_page):
    page, _, _ = start(app, auth_user, training_page)
    attempts = []

    def ambiguous(route):
        attempts.append(1)
        fail(route)
    page.route('**/api/quick-add-meal', ambiguous)
    choose(page, 'quick-add')
    page.keyboard.press('Enter')
    row = page.locator('#qab-kahvalti')
    expect(row).to_have_attribute('data-write-state', 'unconfirmed')
    choose(page, 'quick-add')                                   # PR3 M9 lock holds
    expect(page.locator('#qab-aksam')).to_be_focused()
    row.evaluate('el => { el.disabled = false; el.click(); el.disabled = true; }')
    page.wait_for_timeout(300)
    assert attempts == [1]


def test_quick_add_says_no_plan_only_when_there_is_no_plan(app, auth_user, training_page):
    page, traffic, _ = start(app, auth_user, training_page, plan=False)
    open_chooser(page)
    option = page.locator('#log-sheet [data-method="quick-add"]')
    expect(option).to_have_attribute('data-plan-state', 'none')
    expect(option.locator('.lso-sub')).to_have_text(CATALOG['en']['nutrition.log_quick_add_none'])
    page.keyboard.press('Escape')
    choose(page, 'quick-add')
    expect(page.locator('#quick-add-section .qab-no-plan')).to_be_focused()
    choose(page, 'search')                                      # other methods stay usable
    expect(page.locator('#manual-sheet')).to_have_class(OPEN)
    assert writes(traffic) == []


def test_quick_add_plan_failure_is_not_no_plan(app, auth_user, training_page):
    seed(app, auth_user.id)
    page, traffic, _, _ = training_page
    page.route('**/nutrition-plan/active', fail)
    open_today(page)
    expect(page.locator('#quick-add-section')).to_have_attribute('data-plan-state', 'unavailable')
    open_chooser(page)
    option = page.locator('#log-sheet [data-method="quick-add"]')
    expect(option).to_have_attribute('data-plan-state', 'unavailable')
    expect(option.locator('.lso-sub')).to_have_text(CATALOG['en']['nutrition.plan_unavailable'])
    expect(option.locator('.lso-sub')).not_to_have_text(CATALOG['en']['nutrition.log_quick_add_none'])
    page.keyboard.press('Escape')
    choose(page, 'quick-add')
    expect(page.locator('#quick-add-section [data-action="retryPlanShortcuts"]')).to_be_focused()
    choose(page, 'search')
    expect(page.locator('#manual-sheet')).to_have_class(OPEN)
    assert writes(traffic) == []


# ── BUILD MEAL: staging is not intake; only its commit is ───────────────


def stage_one_item(page):
    canned(page, '**/api/food/search?q=*', {'results': [{**SEARCH_FOOD, 'food_id': None}]})
    canned(page, '**/api/food/servings-by-name?name=*', {'servings': []})
    card = page.locator('#panel-diary .diary-meal-card[data-meal-name="Öğle"]')
    card.locator('.diary-food-search').fill('oats')
    card.locator('.diary-ac .autocomplete-item').first.click()
    expect(page.locator('#serving-modal')).to_have_class(OPEN)
    page.locator('#sm-confirm-btn').click()
    expect(card.locator('.diary-food-row')).to_have_count(1)
    return card


def test_build_meal_staging_is_not_intake(app, auth_user, client, training_page):
    page, traffic, _ = start(app, auth_user, training_page)
    before = meal_logs(app, auth_user.id)
    intake = page.locator('#nut-intake').inner_text()
    traffic.clear()
    choose(page, 'build-meal')
    card = stage_one_item(page)
    page.wait_for_timeout(300)
    staged = [p for p, _, s in traffic if p.startswith('/api/diary/meal')]
    assert staged and all(not p.endswith('/log') for p in staged), staged
    assert writes(traffic) == [] and meal_logs(app, auth_user.id) == before
    expect(page.locator('#nut-intake')).to_have_text(intake)    # staged ≠ consumed
    with app.app_context():
        assert CustomMeal.query.filter_by(user_id=auth_user.id, is_logged=False).count() == 1
    # Leaving and re-entering the builder discards nothing staged.
    page.locator('#nutrition-tab-plan').click()
    page.locator('#nutrition-tab-today').click()
    choose(page, 'build-meal')
    expect(card.locator('.diary-food-row')).to_have_count(1)
    card.locator('[data-action="logDiaryMeal"]').click()        # the explicit commit
    expect(card.locator('.diary-logged')).to_be_visible()
    page.wait_for_timeout(300)
    assert [p for p, _ in writes(traffic)] and all(p.endswith('/log') for p, _ in writes(traffic))
    assert len(writes(traffic)) == 1
    assert meal_logs(app, auth_user.id) == before + 1
    today = client.get('/meal-log/today').get_json()
    expect(page.locator('#nut-intake')).to_have_text(str(round(today['totals']['kalori'])))


def test_build_meal_read_failure_is_unavailable_not_empty(app, auth_user, training_page):
    page, traffic, errors = start(app, auth_user, training_page)
    page.route('**/api/diary/today', fail)
    choose(page, 'build-meal')
    box = page.locator('#diary-meals')
    expect(box).to_have_attribute('data-diary-state', 'unavailable')
    expect(page.locator('#panel-diary .diary-meal-card')).to_have_count(0)
    expect(box).to_contain_text(CATALOG['en']['nutrition.diary_unavailable'])
    expect(page.locator('#diary-grand-total')).to_be_hidden()
    choose(page, 'search')                                      # Search still usable
    expect(page.locator('#manual-sheet')).to_have_class(OPEN)
    page.keyboard.press('Escape')
    page.unroute('**/api/diary/today', fail)
    box.locator('[data-action="loadDiary"]').click()            # retry recovers the real state
    expect(box).to_have_attribute('data-diary-state', 'available')
    expect(page.locator('#panel-diary .diary-meal-card')).to_have_count(4)
    assert writes(traffic) == [] and errors == []


# ── EXACTLY ONE WRITE PER CONFIRMED ACTION ─────────────────────────────


def _open_close_open(page):
    open_chooser(page)
    page.keyboard.press('Escape')
    open_chooser(page)
    page.keyboard.press('Escape')


def _method_cancel(page):
    choose(page, 'search')
    page.keyboard.press('Escape')
    choose(page, 'barcode')
    page.locator('#scan-overlay .scan-close').click()


def _today_reselect(page):
    page.locator('#nutrition-tab-plan').click()
    page.locator('#nutrition-tab-today').click()
    page.locator('#nutrition-tab-today').click()


def _backdrop(page):
    open_chooser(page)
    page.locator('#log-sheet').click(position={'x': 5, 'y': 5})


PRELUDES = {'open_close_open': _open_close_open, 'method_cancel': _method_cancel,
            'today_reselect': _today_reselect, 'chooser_dismissal': _backdrop}


@pytest.mark.parametrize('prelude', list(PRELUDES))
def test_one_confirmed_log_is_exactly_one_write(app, auth_user, training_page, provider, prelude):
    page, traffic, _ = start(app, auth_user, training_page)
    before = meal_logs(app, auth_user.id)
    for _ in range(2):
        PRELUDES[prelude](page)
        assert_front_door_closed(page)
    traffic.clear()
    choose(page, 'search')
    search_and_pick(page)
    page.locator('#manual-sheet [data-action="logMeal"]').click()
    expect(page.locator('#manual-sheet')).not_to_have_class(OPEN)
    page.wait_for_timeout(500)
    assert writes(traffic) == [('/meal-log', 200)], prelude
    assert meal_logs(app, auth_user.id) == before + 1


def test_a_second_activation_during_the_write_sends_nothing(app, auth_user, training_page,
                                                            stub_meal_macro_provider):
    """The overlay blocks a second click, not a second Enter on the focused
    button — each call would mint a new idempotency key (a second meal)."""
    page, traffic, _ = start(app, auth_user, training_page)
    before = meal_logs(app, auth_user.id)
    held = []
    page.route('**/meal-log', lambda route: held.append(route)
               if route.request.method == 'POST' else route.fallback())
    choose(page, 'search')
    page.locator('#meal-input').fill('Toast and eggs')
    button = page.locator('#manual-sheet [data-action="logMeal"]')
    button.focus()
    page.keyboard.press('Enter')
    page.keyboard.press('Enter')
    button.evaluate('el => el.click()')
    page.wait_for_timeout(300)
    assert len(held) == 1
    page.unroute('**/meal-log')
    held[0].fallback()
    expect(page.locator('#manual-sheet')).not_to_have_class(OPEN)
    page.wait_for_timeout(300)
    assert writes(traffic) == [('/meal-log', 200)]
    assert meal_logs(app, auth_user.id) == before + 1


# ── REQUEST TOPOLOGY ────────────────────────────────────────────────────


def test_request_topology_per_method_launch(app, auth_user, training_page):
    page, traffic, _ = start(app, auth_user, training_page)
    assert paths(traffic) == INITIAL, paths(traffic)            # A: same as PR3
    measured = {}

    def measure(name, act):
        traffic.clear()
        act()
        page.wait_for_timeout(300)
        measured[name] = paths(traffic)

    measure('open', lambda: open_chooser(page))
    measure('close', lambda: page.keyboard.press('Escape'))
    for method, close in (('search', lambda: page.keyboard.press('Escape')),
                          ('barcode', lambda: page.locator('#scan-overlay .scan-close').click()),
                          ('quick-add', lambda: None),
                          ('build-meal', lambda: None),
                          ('menu', lambda: None)):
        measure(method, lambda: choose(page, method))
        close()
    assert measured == {'open': Counter(), 'close': Counter(), 'search': Counter(),
                        'barcode': Counter(), 'quick-add': Counter(),
                        'build-meal': Counter({'/api/diary/today': 1}), 'menu': Counter()}, measured


# ── RESPONSIVE / ACCESSIBILITY ──────────────────────────────────────────

CHOOSER_GEOMETRY = """() => {
  const sheet = document.querySelector('#log-sheet .sheet').getBoundingClientRect();
  const opts = [...document.querySelectorAll('#log-sheet .log-sheet-opt')].map(e => {
    const b = e.getBoundingClientRect(); return {m: e.dataset.method, top: b.top, bottom: b.bottom, w: b.width, h: b.height}; });
  const close = document.querySelector('#log-sheet .log-sheet-close').getBoundingClientRect();
  const clipped = [...document.querySelectorAll('#log-sheet .lso-title, #log-sheet .lso-sub, #log-sheet-title, #log-sheet-lead')]
    .filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.textContent);
  return {vw: innerWidth, vh: innerHeight, sheet: {l: sheet.left, r: sheet.right, top: sheet.top, bottom: sheet.bottom},
          opts, close: {l: close.left, r: close.right, top: close.top, bottom: close.bottom, w: close.width, h: close.height},
          clipped, overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1};
}"""


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_chooser_fits_every_width(app, auth_user, training_page, language):
    page, _, _ = start(app, auth_user, training_page, language)
    for width, height in ((320, 640), (390, 844), (430, 844), (768, 900), (1024, 900), (1366, 900)):
        page.set_viewport_size({'width': width, 'height': height})
        open_chooser(page)
        settle(page)
        g = page.evaluate(CHOOSER_GEOMETRY)
        assert not g['overflow'], (width, g)
        assert g['clipped'] == [], (width, g)
        assert g['sheet']['l'] >= 0 and g['sheet']['r'] <= g['vw'] + 0.5, (width, g)
        assert 0 <= g['close']['top'] and g['close']['bottom'] <= g['vh'], (width, g)   # close in view
        assert g['close']['w'] >= 43.5 and g['close']['h'] >= 43.5, (width, g)
        for o in g['opts']:
            assert o['h'] >= 43.5 and o['w'] >= 43.5, (width, o)
        # Every primary method is in view without scrolling the sheet.
        for o in g['opts'][:5]:
            assert o['bottom'] <= g['vh'], (width, language, o, g['vh'])
        page.keyboard.press('Escape')
        assert_front_door_closed(page)


def test_focus_is_visible_on_methods(app, auth_user, training_page):
    page, _, _ = start(app, auth_user, training_page)
    page.locator('#log-food-btn').focus()
    page.keyboard.press('Enter')
    for _ in range(3):
        style = page.evaluate("() => getComputedStyle(document.activeElement).outlineStyle")
        assert style != 'none', style
        page.keyboard.press('Tab')
