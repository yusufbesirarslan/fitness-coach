"""NUTR-PR4 — one "Log food" front door, several truthful methods (structural contract).

Rendered `/nutrition` plus the shipped script, asserted by meaning:

  * exactly ONE dominant Today `#log-food-btn`, opening the ONE chooser;
  * the chooser offers Search · Barcode · Menu · Quick add · Build meal, with
    Photo as a quieter secondary method, as real text-labelled buttons;
  * the voice placeholder (it could not log anything) is gone, not hidden;
  * PR4 is a UX convergence, not a backend one: no new route, no new endpoint
    in the script, no new writer — the chooser functions never fetch, and each
    canonical write still has exactly the caller it had before;
  * Nutrition keeps two primary modes and the shell keeps four destinations;
  * EN/TR parity and plain, intent-level copy.
"""
import html as html_lib
import json
import re
from pathlib import Path

import pytest

from test_nutrition_vnext_pr2_navigation_contract import Elements, render
from test_nutrition_vnext_pr3_daily_contract import KNOWN_ENDPOINTS

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (ROOT / 'static' / 'nutrition.js').read_text(encoding='utf-8').replace('\r\n', '\n')
TEMPLATE = (ROOT / 'templates' / 'nutrition.html').read_text(encoding='utf-8').replace('\r\n', '\n')

# (data-method, data-action, title key) in visual — and reading — order.
PRIMARY = [
    ('search', 'logManual', 'nutrition.log_manual'),
    ('barcode', 'logScanBarcode', 'nutrition.log_barcode'),
    ('menu', 'logMenuScan', 'nutrition.log_menu'),
    ('quick-add', 'logQuickAdd', 'nutrition.log_quick_add'),
    ('build-meal', 'logBuildMeal', 'nutrition.log_build_meal'),
]
SECONDARY = [('photo', 'logTakePhoto', 'nutrition.log_photo')]
METHODS = PRIMARY + SECONDARY

PR4_KEYS = [
    'nutrition.log_food', 'nutrition.log_food_prompt', 'nutrition.close',
    'nutrition.log_manual', 'nutrition.log_manual_sub', 'nutrition.log_barcode',
    'nutrition.log_barcode_sub', 'nutrition.log_menu', 'nutrition.log_menu_sub',
    'nutrition.log_quick_add', 'nutrition.log_quick_add_sub', 'nutrition.log_quick_add_none',
    'nutrition.log_quick_add_loading', 'nutrition.log_build_meal',
    'nutrition.log_build_meal_sub', 'nutrition.log_more_methods', 'nutrition.log_photo',
    'nutrition.log_photo_sub', 'nutrition.diary_builder', 'nutrition.diary_unavailable',
]
RETIRED_VOICE_KEYS = ['nutrition.log_voice', 'nutrition.log_voice_sub', 'nutrition.voice_title',
                      'nutrition.voice_body', 'nutrition.mobile_only']
# Words that name a subsystem rather than a user intent.
INTERNAL_WORDS = ('manual', 'diary', 'provider', 'custommeal', 'meallog', 'ai scan', 'fatsecret',
                  'endpoint', 'staging', 'enum', 'günlük oluşturucu', 'elle gir')

# Functions that only move the user between surfaces. None of them may reach
# the network: opening the chooser is local UI, and a method's own request
# starts only when the user acts inside that method.
NAVIGATION_ONLY = ('openLogSheet', 'closeLogSheet', 'dismissLogSheet', '_syncQuickAddOption',
                   '_returnFocus', '_focusFirstVisible', 'logManual', 'openManualSheet',
                   'closeManualSheet', 'logScanBarcode', 'logMenuScan', 'logQuickAdd',
                   'logBuildMeal', 'logTakePhoto')

# Every canonical write the Nutrition script can send, and who sends it. The
# chooser adds no caller to any of them.
WRITERS = {
    "fetch('/meal-log', {": ['submitPhotoMeal', 'postSelectedFood', 'submitMealLog',
                             'logProviderFoodToLedger'],
    "fetch('/api/quick-add-meal', {": ['quickAddMeal'],
    "fetch('/api/diary/meal/' + mealId + '/log', {": ['logDiaryMeal'],
}

# The POST/PUT/PATCH/DELETE surface that can create or remove consumed or
# staged food, exactly as it stood before PR4.
MUTATING_FOOD_ROUTES = {
    ('/meal-log', 'POST'), ('/meal-log/entry/<entry_token>', 'DELETE'),
    ('/meal-log/review', 'POST'), ('/api/quick-add-meal', 'POST'),
    ('/api/diary/meal', 'POST'), ('/api/diary/meal/<int:meal_id>/item', 'POST'),
    ('/api/diary/item/<int:item_id>', 'PATCH'), ('/api/diary/item/<int:item_id>', 'DELETE'),
    ('/api/diary/meal/<int:meal_id>/log', 'POST'), ('/api/food/barcode/add', 'POST'),
}


def _catalog(language):
    return json.loads((ROOT / 'locales' / f'{language}.json').read_text(encoding='utf-8'))


def _body(html):
    return re.sub(r'<script\b[^>]*>.*?</script>', '', html, flags=re.S)


def _chooser(html):
    body = _body(html)
    start = body.index('id="log-sheet"')
    return body[start:body.index('<!-- ── MANUAL ENTRY SHEET')]


def _text(markup):
    return re.sub(r'\s+', ' ', html_lib.unescape(re.sub(r'<[^>]+>', ' ', markup))).strip()


def function_body(name):
    """Source of one top-level `function name(` in the shipped script."""
    match = re.search(r'^(?:async )?function ' + re.escape(name) + r'\(', SCRIPT, re.M)
    assert match, name
    end = SCRIPT.find('\n}\n', match.start())
    return SCRIPT[match.start():end + 2]


def function_containing(index):
    starts = [m for m in re.finditer(r'^(?:async )?function (\w+)\(', SCRIPT, re.M)
              if m.start() < index]
    return starts[-1].group(1)


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_one_front_door_opens_the_one_chooser(client, make_user, login, language):
    html = render(client, make_user, login, language)
    rows = Elements(html).rows
    openers = [(tag, a, anc) for tag, a, anc in rows if a.get('data-action') == 'openLogSheet']
    assert len(openers) == 1, openers
    tag, attrs, ancestors = openers[0]
    assert (tag, attrs['id']) == ('button', 'log-food-btn')
    assert 'btn-volt' in attrs['class'].split() and 'nut-day' in ancestors
    assert attrs['aria-haspopup'] == 'dialog' and attrs['aria-controls'] == 'log-sheet'
    assert attrs['aria-expanded'] == 'false'
    sheets = [a for _, a, _ in rows if a.get('id') == 'log-sheet']
    assert len(sheets) == 1
    # The chooser is a titled, described, closable modal dialog.
    dialog = next(a for _, a, anc in rows if a.get('role') == 'dialog' and 'log-sheet' in anc)
    assert dialog['aria-modal'] == 'true'
    assert dialog['aria-labelledby'] == 'log-sheet-title'
    assert dialog['aria-describedby'] == 'log-sheet-lead'
    title = next((t, a) for t, a, _ in rows if a.get('id') == 'log-sheet-title')
    assert title[0] == 'h2'
    close = [a for t, a, anc in rows if 'log-sheet' in anc and a.get('data-action') == 'dismissLogSheet']
    assert len(close) == 1 and close[0]['aria-label'] == _catalog(language)['nutrition.close']
    assert close[0]['type'] == 'button'


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_chooser_offers_every_method_in_order(client, make_user, login, language):
    html = render(client, make_user, login, language)
    rows = Elements(html).rows
    catalog = _catalog(language)
    options = [(t, a, anc) for t, a, anc in rows if 'log-sheet-opt' in a.get('class', '').split()]
    assert [(a['data-method'], a['data-action']) for _, a, _ in options] == \
        [(m, action) for m, action, _ in METHODS]
    for tag, attrs, ancestors in options:
        assert tag == 'button' and attrs['type'] == 'button', attrs
        assert 'log-sheet' in ancestors
        assert attrs.get('role') is None                       # never a tab
    chooser = _chooser(html)
    # Each method is a text-labelled button: title + one line of meaning.
    for method, action, title_key in METHODS:
        button = re.search(r'<button[^>]*data-method="' + method + r'"[^>]*>(.*?)</button>',
                           chooser, re.S).group(1)
        assert '<button' not in button and '<a ' not in button    # nothing nested inside
        assert catalog[title_key] in _text(button), (method, _text(button))
    # Photo is visibly secondary: its own labelled group after the five.
    assert chooser.index('data-method="build-meal"') < chooser.index('id="log-sheet-more"') \
        < chooser.index('data-method="photo"')
    assert 'role="tab' not in chooser and 'role="tablist"' not in chooser
    # Every method is reachable ONLY from inside the chooser.
    for _, action, _ in METHODS:
        placed = [anc for _, a, anc in rows if a.get('data-action') == action]
        assert len(placed) == 1 and 'log-sheet' in placed[0], action


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_chooser_copy_is_plain_and_localized(client, make_user, login, language):
    catalog = _catalog(language)
    chooser = _text(_chooser(render(client, make_user, login, language)))
    assert catalog['nutrition.log_food_prompt'] in chooser
    assert 'nutrition.' not in chooser                          # no raw key
    for word in INTERNAL_WORDS:
        assert word not in chooser.lower(), word
    for key in PR4_KEYS:
        assert catalog[key].strip(), key
    # Truthful per method: menu analysis is not logging, photo is not analysis.
    menu = catalog['nutrition.log_menu_sub'].lower()
    assert not re.search(r'\blog|kaydet', menu), menu
    photo = catalog['nutrition.log_photo_sub'].lower()
    assert 'auto' not in photo and 'otomatik' not in photo and 'ai' not in photo.split()


def test_voice_placeholder_is_retired():
    """Voice could only show a "use the mobile app" sheet: it never logged
    food, so it is no longer presented as a way to log food."""
    assert 'logVoice' not in TEMPLATE and 'voice-sheet' not in TEMPLATE
    assert 'logVoice' not in SCRIPT and 'voice-sheet' not in SCRIPT
    for language in ('en', 'tr'):
        catalog = _catalog(language)
        for key in RETIRED_VOICE_KEYS:
            assert key not in catalog, key
    css = (ROOT / 'static' / 'nutrition.css').read_text(encoding='utf-8')
    assert '.voice-' not in css


def test_photo_capability_is_retained_with_its_confirmed_write():
    """Photo is a working method (photo + note → POST /meal-log only on the
    modal's explicit confirm), so PR4 keeps it — as a secondary method."""
    for anchor in ('function logTakePhoto()', 'async function onPhotoPicked(el)',
                   'function openPhotoConfirm(dataUrl)', 'async function submitPhotoMeal()'):
        assert anchor in SCRIPT, anchor
    assert 'image: _photoDataUrl' in function_body('submitPhotoMeal')
    assert 'fetch(' not in function_body('onPhotoPicked')
    assert 'fetch(' not in function_body('openPhotoConfirm')
    assert 'id="photo-input"' in TEMPLATE and 'data-action="submitPhotoMeal"' in TEMPLATE


def test_chooser_functions_never_touch_the_network():
    for name in NAVIGATION_ONLY:
        body = function_body(name)
        for forbidden in ('fetch(', 'XMLHttpRequest', 'sendBeacon', 'loadDiary(', 'loadMealHistory(',
                          'getActivePlan(', 'loadQuickAddSection(', 'loadTodayData(',
                          'quickAddMeal(', 'searchFood(', 'resolveBarcode(', 'processMenuUrl('):
            assert forbidden not in body, (name, forbidden)


def test_no_second_writer_and_no_new_endpoint():
    literals = set(re.findall(r"fetch\(\s*('[^']*'|url)", SCRIPT))
    assert literals <= KNOWN_ENDPOINTS, literals - KNOWN_ENDPOINTS
    for call, owners in WRITERS.items():
        found = [function_containing(m.start()) for m in re.finditer(re.escape(call), SCRIPT)]
        assert found == owners, (call, found)
    # Quick add reuses the planned-row machine: its one writer, its locks.
    assert SCRIPT.count("'/api/quick-add-meal'") == 1
    quick = function_body('logQuickAdd')
    assert 'quick-add-section' in quick and '_plannedWriteLocks' not in quick
    # Build meal reuses the builder through the normal navigation path.
    assert "switchTab('diary')" in function_body('logBuildMeal')
    # Search reuses the search sheet; barcode reuses the scanner.
    assert function_body('logManual').count('openManualSheet()') == 1
    assert 'openScanOverlay()' in function_body('logScanBarcode')
    assert 'window.CW.startScan()' in function_body('logMenuScan')


def test_no_new_route_mode_or_destination(app):
    rules = list(app.url_map.iter_rules())
    mutating = {(r.rule, m) for r in rules for m in (r.methods or ())
                if m in {'POST', 'PUT', 'PATCH', 'DELETE'} and
                (r.rule.startswith(('/meal-log', '/api/diary', '/api/quick-add', '/api/food',
                                    '/nutrition')))}
    assert mutating - {('/nutrition-plan', 'POST'), ('/nutrition-plan/save', 'POST')} \
        == MUTATING_FOOD_ROUTES, mutating
    assert not any('log-food' in r.rule or 'log_food' in r.rule for r in rules
                   if not r.rule.startswith('/api/v1/'))
    assert not any(r.rule.startswith('/nutrition/') for r in rules)
    from app.nav import primary_destinations
    assert [d['id'] for d in primary_destinations()] == ['today', 'plan', 'coach', 'progress']


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_nutrition_keeps_two_modes(client, make_user, login, language):
    rows = Elements(render(client, make_user, login, language)).rows
    tabs = [a['data-tab-name'] for _, a, _ in rows if a.get('role') == 'tab']
    assert tabs == ['today', 'plan']


def test_locale_parity():
    en, tr = _catalog('en'), _catalog('tr')
    assert set(en) == set(tr)
    for key in PR4_KEYS:
        assert en[key] != tr[key] or key == 'nutrition.log_food', key   # translated, not copied
    # TR reads as Turkish, not a transliteration of the English label.
    assert tr['nutrition.log_manual'] == 'Yemek ara'
    assert tr['nutrition.log_build_meal'] == 'Öğün oluştur'
