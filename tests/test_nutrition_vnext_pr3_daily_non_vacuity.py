"""NUTR-PR3 non-vacuity: each controlled mutation must make its guard fail.

No product file is changed. The template is mutated through a scoped Jinja
loader patch and the script through a per-page browser route; both restore
automatically when the test ends.

  M1 History promoted to a primary tab        → IA contract fails
  M2 a second dominant food-log entry         → primary-action contract fails
  M3 a missing target drawn as 0              → target-missing test fails
  M4 a failed meal read drawn as "0 meals"    → meal-failure test fails
  M5 the post-log canonical refresh removed   → freshness test fails
  M6 History eager-loaded                     → request-topology test fails
  M7 Water unavailable drawn as 0 cups        → hydration failure test fails
  M8 secondary content placed before the core → first-viewport test fails
"""
import pytest

from test_training_execution_boundary import training_page  # noqa: F401
from test_ux3_pr4_nutrition_placement_browser import stub_meal_macro_provider  # noqa: F401
import test_nutrition_vnext_pr3_daily_browser as browser
import test_nutrition_vnext_pr3_daily_contract as contract

HERO = '    {#- ── NUTR-PR3 — Today is one daily job ──'
SECONDARY_START = '    {#- TIER 2 — compact hydration.'
SECONDARY_END = '  </div><!-- /nutrition-secondary -->\n'

TEMPLATE_MUTATIONS = {
    'M1': lambda s: s.replace(
        '    <button id="nutrition-tab-plan"',
        '    <button id="nutrition-tab-history-mode" class="tab-btn" data-tab-name="history" '
        'type="button" role="tab" aria-selected="false" aria-controls="panel-history">History</button>\n'
        '    <button id="nutrition-tab-plan"'),
    'M2': lambda s: s.replace(
        '<input type="file" id="photo-input"',
        '<button class="btn-volt log-fab" id="log-fab" data-action="openLogSheet" '
        'aria-label="Log food">+</button>\n<input type="file" id="photo-input"'),
    'M8': lambda s: (lambda a, b: s[:s.index(HERO)] + s[a:b] + s[s.index(HERO):a] + s[b:])(
        s.index(SECONDARY_START), s.index(SECONDARY_END) + len(SECONDARY_END)),
}

SCRIPT_MUTATIONS = {
    'M3': [("  if (d.targets === null) return { state: 'absent' };\n",
            "  if (d.targets === null) return { state: 'known', kalori: 0, "
            "macros: { protein: null, karb: null, yag: null } };\n")],
    'M4': [("    renderDaySummaryFailure();\n    renderTimelineFailure();\n",
            "    renderDaySummary({ kalori: 0, protein: 0, karb: 0, yag: 0 }, { state: 'absent' });\n"
            "    renderTimeline([]);\n")],
    'M5': [("    closeManualSheet();\n    loadTodayData();\n  } catch (e) {\n",
            "    closeManualSheet();\n  } catch (e) {\n")],
    'M6': [("initWaterButton();\n", "initWaterButton();\nloadMealHistory();\n")],
    'M7': [("  else waterState = 'unavailable';\n", "  else confirmWater(0);\n")],
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


@pytest.mark.parametrize('mutation', ['M1', 'M2'])
def test_template_contract_mutation_is_detected(app, client, make_user, login, monkeypatch, mutation):
    patch_template(app, monkeypatch, mutation)
    with pytest.raises(AssertionError):
        if mutation == 'M1':
            contract.test_exactly_today_and_plan_are_primary(client, make_user, login, 'en')
        else:
            contract.test_one_dominant_log_food_action(client, make_user, login, 'en')


def test_m8_secondary_before_core_is_detected(app, auth_user, training_page, monkeypatch):
    patch_template(app, monkeypatch, 'M8')
    with pytest.raises(AssertionError):
        browser.test_first_viewport_holds_the_daily_job(app, auth_user, training_page, 'en', (320, 640))


def test_m3_missing_target_as_zero_is_detected(app, auth_user, client, training_page):
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'M3')
    with pytest.raises(AssertionError):
        browser.test_target_missing_is_not_zero(app, auth_user, client, training_page, 'en')


def test_m4_meal_failure_as_zero_is_detected(app, auth_user, client, training_page):
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'M4')
    with pytest.raises(AssertionError):
        browser.test_meal_read_failure_is_unknown_not_empty(app, auth_user, client, training_page, 'en')


def test_m5_stale_totals_after_log_are_detected(app, auth_user, client, training_page,
                                               stub_meal_macro_provider):
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'M5')
    with pytest.raises(AssertionError):
        browser.test_log_food_front_door_write_refreshes_canonically(
            app, auth_user, client, training_page, stub_meal_macro_provider)


def test_m6_eager_history_is_detected(app, auth_user, client, training_page):
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'M6')
    with pytest.raises(AssertionError):
        browser.test_initial_request_topology_is_unchanged(app, auth_user, client, training_page)


def test_m7_water_unavailable_as_zero_is_detected(app, auth_user, client, training_page):
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'M7')
    with pytest.raises(AssertionError):
        browser.test_water_failure_leaves_the_day_usable(app, auth_user, client, training_page,
                                                         'unavailable')
