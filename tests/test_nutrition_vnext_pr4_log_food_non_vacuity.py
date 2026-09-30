"""NUTR-PR4 non-vacuity: each controlled mutation must make its guard fail.

No product file is changed. The template is mutated through a scoped Jinja
loader patch and the script through a per-page browser route; both restore
automatically when the test ends.

  N1  Search removed from the chooser             → chooser contract fails
  N2  barcode resolution logs by itself           → barcode write-boundary test fails
  N3  menu analysis claims and writes "logged"    → menu-is-not-a-log test fails
  N4  builder staging commits itself (= intake)   → staging-is-not-intake test fails
  N5  Quick add writes past the planned-row lock  → quick-add routing test fails
  N6  opening the chooser calls the provider      → request-topology test fails
  N7  a duplicated write listener per open        → exactly-one-write test fails
  N8  dismissing the chooser drops focus          → focus-return test fails
  N9  a failed plan read shown as "no plan"       → plan-state semantics test fails
  N10 the retained Photo method made unreachable  → photo capability test fails

Voice is intentionally retired, so no mutation requires it to exist.
"""
import re

import pytest

from test_training_execution_boundary import training_page  # noqa: F401
from test_ux3_pr4_nutrition_placement_browser import stub_meal_macro_provider  # noqa: F401
import test_nutrition_vnext_pr4_log_food_browser as browser
import test_nutrition_vnext_pr4_log_food_contract as contract
from test_nutrition_vnext_pr4_log_food_browser import provider  # noqa: F401


def _drop_button(method):
    pattern = re.compile(r'      <button class="log-sheet-opt[^"]*" type="button" data-method="'
                         + method + r'".*?</button>\n', re.S)
    return lambda s: pattern.sub('', s, count=1)


TEMPLATE_MUTATIONS = {
    'N1': _drop_button('search'),
    'N10': _drop_button('photo'),
}

SCRIPT_MUTATIONS = {
    'N2': [("      _suggestOgunByHour()\n    );\n",
            "      _suggestOgunByHour()\n    );\n"
            "    await logProviderFoodToLedger(_smFood, _smLogOgun);\n")],
    'N3': [("    window.CW.startScan();               // #cw-scan overlay'ini kendisi açar\n",
            "    window.CW.startScan();               // #cw-scan overlay'ini kendisi açar\n"
            "    fetch('/meal-log', { method: 'POST', headers: mealWriteHeaders(), body: JSON.stringify("
            "{ ogun: 'Öğle', yemekler: 'Menu pick', override_macros: "
            "{ kalori: 450, protein: 42, karb: 12, yag: 20 } }) });\n"
            "    showToast(__t('nutrition.meal_saved'), 'success');\n")],
    'N4': [("    closeServingModal();\n    loadDiary();\n",
            "    closeServingModal();\n    loadDiary();\n"
            "    await fetch('/api/diary/meal/' + mealId + '/log', { method: 'POST' });\n"
            "    loadTodayData();\n")],
    'N5': [("  closeLogSheet();\n  var section = document.getElementById('quick-add-section');\n",
            "  closeLogSheet();\n"
            "  fetch('/api/quick-add-meal', { method: 'POST', headers: mealWriteHeaders(),"
            " body: JSON.stringify({ meal_key: 'kahvalti' }) });\n"
            "  var section = document.getElementById('quick-add-section');\n")],
    'N6': [("  _syncQuickAddOption();\n  s.classList.add('open');\n",
            "  _syncQuickAddOption();\n  fetch('/api/food/search?q=a');\n  s.classList.add('open');\n")],
    'N7': [("  _syncQuickAddOption();\n  s.classList.add('open');\n",
            "  _syncQuickAddOption();\n"
            "  document.querySelector('#manual-sheet [data-action=\"logMeal\"]')"
            ".addEventListener('click', () => submitMealLog());\n"
            "  s.classList.add('open');\n")],
    'N8': [("  if (wasOpen && back) back.focus();\n", "")],
    'N9': [("  unavailable: 'nutrition.plan_unavailable',\n",
            "  unavailable: 'nutrition.log_quick_add_none',\n")],
}


def patch_template(app, monkeypatch, mutation):
    loader = app.jinja_env.loader
    original = loader.get_source

    def mutated(environment, template):
        source, filename, uptodate = original(environment, template)
        if template == 'nutrition.html':
            source = source.replace('\r\n', '\n')
            changed = TEMPLATE_MUTATIONS[mutation](source)
            assert changed != source, 'template mutation target drifted: ' + mutation
            source = changed
        return source, filename, uptodate
    monkeypatch.setattr(loader, 'get_source', mutated)
    app.jinja_env.cache.clear()


def serve_mutated_script(page, client, mutation):
    original = client.get('/static/nutrition.js').get_data(as_text=True).replace('\r\n', '\n')
    mutated = original
    for old, new in SCRIPT_MUTATIONS[mutation]:
        assert mutated.count(old) == 1, 'script mutation target drifted: ' + old
        mutated = mutated.replace(old, new)
    page.route('**/static/nutrition.js*', lambda route: route.fulfill(
        status=200, content_type='application/javascript', body=mutated))
    return mutated


def assert_served(page, needle):
    """The browser really ran the mutated script, not the shipped one."""
    assert page.evaluate('() => document.documentElement.outerHTML') is not None
    assert needle in page.evaluate("""() => [openLogSheet, dismissLogSheet, logMenuScan, logQuickAdd,
        resolveBarcode, confirmServingModal].map(f => f.toString()).join('\\n')
        + JSON.stringify(_QUICK_ADD_SUB)""")


def test_n1_search_removed_is_detected(app, client, make_user, login, monkeypatch):
    patch_template(app, monkeypatch, 'N1')
    with pytest.raises(AssertionError):
        contract.test_chooser_offers_every_method_in_order(client, make_user, login, 'en')


def test_n2_barcode_auto_log_is_detected(app, auth_user, client, training_page, provider):
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'N2')
    with pytest.raises(AssertionError):
        browser.test_barcode_resolution_is_discovery_not_a_log(
            app, auth_user, client, training_page, provider)
    assert_served(page, 'await logProviderFoodToLedger(_smFood, _smLogOgun)')


def test_n3_menu_analysis_claiming_a_log_is_detected(app, auth_user, client, training_page):
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'N3')
    with pytest.raises(AssertionError):
        browser.test_menu_analysis_is_not_a_log(app, auth_user, training_page)
    assert_served(page, "yemekler: 'Menu pick'")


def test_n4_staging_counted_as_intake_is_detected(app, auth_user, client, training_page):
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'N4')
    with pytest.raises(AssertionError):
        browser.test_build_meal_staging_is_not_intake(app, auth_user, client, training_page)
    assert_served(page, "'/log', { method: 'POST' });\n    loadTodayData();")


def test_n5_quick_add_bypassing_the_row_is_detected(app, auth_user, client, training_page):
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'N5')
    with pytest.raises(AssertionError):
        browser.test_quick_add_writes_only_through_the_planned_row(app, auth_user, training_page)
    assert_served(page, "meal_key: 'kahvalti'")


def test_n6_eager_provider_on_open_is_detected(app, auth_user, client, training_page):
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'N6')
    with pytest.raises(AssertionError):
        browser.test_request_topology_per_method_launch(app, auth_user, training_page)
    assert_served(page, "fetch('/api/food/search?q=a')")


def test_n7_duplicate_write_listener_is_detected(app, auth_user, client, training_page, provider):
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'N7')
    with pytest.raises(AssertionError):
        browser.test_one_confirmed_log_is_exactly_one_write(
            app, auth_user, training_page, provider, 'open_close_open')
    assert_served(page, "addEventListener('click', () => submitMealLog())")


def test_n8_focus_not_returned_is_detected(app, auth_user, client, training_page):
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'N8')
    with pytest.raises(AssertionError):
        browser.test_opening_and_dismissing_is_local_and_returns_focus(app, auth_user, training_page)
    served = page.evaluate('() => dismissLogSheet.toString()')
    assert 'back.focus()' not in served


def test_n9_plan_failure_as_no_plan_is_detected(app, auth_user, client, training_page):
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'N9')
    with pytest.raises(AssertionError):
        browser.test_quick_add_plan_failure_is_not_no_plan(app, auth_user, training_page)
    assert_served(page, '"unavailable":"nutrition.log_quick_add_none"')


def test_n10_retained_photo_unreachable_is_detected(app, auth_user, training_page, monkeypatch,
                                                    stub_meal_macro_provider):
    patch_template(app, monkeypatch, 'N10')
    with pytest.raises(AssertionError):
        browser.test_photo_method_is_retained_and_logs_only_on_confirm(
            app, auth_user, training_page, stub_meal_macro_provider)
