"""NUTR-PR5 non-vacuity: each controlled mutation must make its guard fail.

No product file is changed. The script is mutated through a per-page browser
route (and, where a structural guard exists, fed to it as a string); the
template through a scoped Jinja loader patch; the save route through a
monkeypatched call inside the real request. Browser mutations are checked to
be the code the page actually ran.

  P5-N1  active-read failure rendered as "no plan"        → read-failure test fails
  P5-N2  generated option drawn as the current plan       → proposal test fails
  P5-N3  success shown before the save answers            → refused-save test fails
  P5-N4  existing plan deleted before score/schema checks → save-invariant tests fail
  P5-N5  a refused save clears the current plan           → refused-save test fails
  P5-N6  opening Plan sends POST /nutrition-plan          → no-eager-generation tests fail
  P5-N7  double confirm sends two saves                   → single-flight tests fail
  P5-N8  option markup rendered unescaped                 → XSS/render test fails
  P5-N9  a planned meal labelled Logged without MealLog   → planned-meals test fails
  P5-N10 Supplements CRUD embedded in Plan                → child-link contract fails
  P5-N11 a Grocery Guide without a supported source       → grocery contract fails
  P5-N12 Today keeps the old plan after a confirmed save  → convergence test fails
"""
import json

import pytest

from test_training_execution_boundary import training_page  # noqa: F401
import test_nutrition_vnext_pr5_plan_browser as browser
import test_nutrition_vnext_pr5_plan_contract as contract
from test_nutrition_vnext_pr5_plan_browser import generator  # noqa: F401

SCRIPT_MUTATIONS = {
    'N1': [("    return renderCurrentPlanUnavailable();\n",
            "    return renderCurrentPlan({ exists: false });\n")],
    'N2': [("  renderPlans({ planlar: plans, overall_score: data.overall_score });\n",
            "  renderPlans({ planlar: plans, overall_score: data.overall_score });\n"
            "  renderCurrentPlan({ exists: true, plan: plans[0], score: data.overall_score, created_at: '' });\n")],
    'N3': [("  let res = null, body = null;\n",
            "  document.getElementById('plan-current-status').textContent = __t('nutrition.plan.saved');\n"
            "  let res = null, body = null;\n")],
    'N5': [("    builder.dataset.saveState = 'rejected';\n",
            "    builder.dataset.saveState = 'rejected';\n    renderCurrentPlan({ exists: false });\n")],
    'N6': [("  if (focusId) document.getElementById(focusId)?.focus();\n",
            "  if (next.mode === 'plan') fetch('/nutrition-plan', { method: 'POST', headers: "
            "{ 'Content-Type': 'application/json' }, body: JSON.stringify({ proteins: ['Yumurta'], "
            "carbs: ['Pirinç'], fats: ['Avokado'] }) });\n"
            "  if (focusId) document.getElementById(focusId)?.focus();\n")],
    'N7': [("  if (_planSaveInFlight || _planSaveLocked || !_planOptions || !_planOptions.plans[i]) return;\n",
            "  if (!_planOptions || !_planOptions.plans[i]) return;\n"),
           ("  if (i === null || _planSaveInFlight) return;\n", "  if (i === null) return;\n")],
    'N8': [("${esc(planName(plan, i))}", "${planName(plan, i)}")],
    'N9': [("<span class=\"apd-badge\">${esc(__t('nutrition.planned'))}</span>",
            "<span class=\"apd-badge\">${esc(__t('nutrition.logged_state'))}</span>")],
    'N12': [("  const reread = loadActivePlan();\n  loadQuickAddSection();\n",
             "  const reread = loadActivePlan();\n"),
            ("  if (refreshToday && next.mode === 'today') { loadTodayData(); loadQuickAddSection(); }\n",
             "  if (refreshToday && next.mode === 'today') { loadTodayData(); }\n")],
}

NAV = '  <nav class="nutrition-child-domain"'
TEMPLATE_MUTATIONS = {
    'N10': lambda s: s.replace(NAV, '    <form method="post" action="/supplement/add">'
                               '<input name="product_name"><button type="submit">Add supplement</button>'
                               '</form>\n' + NAV, 1),
    'N11': lambda s: s.replace(NAV, '    <section class="card plan-block" id="grocery-guide">'
                               '<h3 class="plan-block-title">Grocery Guide</h3>'
                               '<p class="plan-note">Coming soon</p></section>\n' + NAV, 1),
}


def mutate(source, mutation):
    mutated = source.replace('\r\n', '\n')
    for old, new in SCRIPT_MUTATIONS[mutation]:
        assert mutated.count(old) == 1, 'script mutation target drifted: ' + old
        mutated = mutated.replace(old, new)
    return mutated


def serve_mutated_script(page, client, mutation):
    mutated = mutate(client.get('/static/nutrition.js').get_data(as_text=True), mutation)
    page.route('**/static/nutrition.js*', lambda route: route.fulfill(
        status=200, content_type='application/javascript', body=mutated))
    return mutated


def assert_served(page, functions, needle):
    """The browser really ran the mutated script, not the shipped one."""
    source = page.evaluate('names => names.map(n => window[n].toString()).join("\\n")', functions)
    assert needle in source, needle


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


# ── P5-N1 … P5-N3 ──────────────────────────────────────────────────────


def test_n1_read_failure_as_no_plan_is_detected(app, auth_user, client, training_page):
    with pytest.raises(AssertionError):
        contract.test_absent_is_not_a_read_failure(script=mutate(contract.SCRIPT, 'N1'))
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'N1')
    with pytest.raises(AssertionError):
        browser.test_active_plan_read_failure_is_not_no_plan(app, auth_user, training_page, 'en')
    assert_served(page, ['loadActivePlan'], 'return renderCurrentPlan({ exists: false })')


def test_n2_proposal_drawn_as_current_is_detected(app, auth_user, client, training_page, generator):
    with pytest.raises(AssertionError):
        contract.test_generation_is_a_proposal_not_a_save(script=mutate(contract.SCRIPT, 'N2'))
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'N2')
    with pytest.raises(AssertionError):
        browser.test_generated_options_are_proposals_and_the_current_plan_is_unchanged(
            app, auth_user, training_page, generator, 'en')
    assert_served(page, ['generatePlan'], 'renderCurrentPlan({ exists: true, plan: plans[0]')


def test_n3_success_before_the_answer_is_detected(app, auth_user, client, training_page, generator):
    with pytest.raises(AssertionError):
        contract.test_nothing_is_marked_current_before_the_save_and_the_reread(
            script=mutate(contract.SCRIPT, 'N3'))
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'N3')
    with pytest.raises(AssertionError):
        browser.test_refused_save_keeps_the_current_plan(app, auth_user, training_page, generator)
    assert_served(page, ['savePlanOption'], "textContent = __t('nutrition.plan.saved');\n  let res")


# ── P5-N4 · backend order ──────────────────────────────────────────────


# NUTR-PR7: the destructive step is the shared replacement boundary call.
DELETE = '    replace_nutrition_plan(current_user.id, plan, score, UNCONDITIONAL)\n'
SCORE = '    score = data.get("score")\n'


def test_n4_delete_before_validation_is_detected(app, client, auth_user, monkeypatch):
    source = contract.PLAN_ROUTES.replace('\r\n', '\n')
    assert source.count(DELETE) == 1 and source.count(SCORE) == 1
    reordered = source.replace(DELETE, '').replace(SCORE, SCORE + DELETE)
    with pytest.raises(AssertionError):
        contract.test_save_route_validates_before_it_deletes(source=reordered)

    # The same mutation executed inside the real request: destructive work
    # (delete + commit) before the score/schema checks.
    from flask_login import current_user
    from app.blueprints.nutrition import plan as plan_module
    from app.extensions import db
    from app.models import NutritionPlan
    original = plan_module.parse_plan_score

    def delete_first(value):
        NutritionPlan.query.filter_by(user_id=current_user.id).delete()
        db.session.commit()
        return original(value)
    monkeypatch.setattr(plan_module, 'parse_plan_score', delete_first)
    with pytest.raises(AssertionError):
        contract.test_invalid_save_never_destroys_the_current_plan(app, client, auth_user)


# ── P5-N5 … P5-N9 ──────────────────────────────────────────────────────


def test_n5_refused_save_clearing_the_plan_is_detected(app, auth_user, client, training_page, generator):
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'N5')
    with pytest.raises(AssertionError):
        browser.test_refused_save_keeps_the_current_plan(app, auth_user, training_page, generator)
    assert_served(page, ['savePlanOption'], "'rejected';\n    renderCurrentPlan({ exists: false });")


def test_n6_generation_on_plan_open_is_detected(app, auth_user, client, training_page, generator):
    with pytest.raises(AssertionError), pytest.MonkeyPatch.context() as scoped:
        contract.test_no_eager_generation_or_provider_call(
            app, client, auth_user, scoped, script=mutate(contract.SCRIPT, 'N6'))
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'N6')
    with pytest.raises(AssertionError):
        browser.test_opening_plan_calls_no_generation_or_provider(app, auth_user, training_page, generator)
    assert generator['calls'] > 0                                    # the provider really ran
    assert_served(page, ['applyNutritionNavigation'], "if (next.mode === 'plan') fetch('/nutrition-plan'")


def test_n7_double_save_is_detected(app, auth_user, client, training_page, generator):
    with pytest.raises(AssertionError):
        contract.test_replacement_is_confirmed_and_single_flight(script=mutate(contract.SCRIPT, 'N7'))
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'N7')
    with pytest.raises(AssertionError):
        browser.test_generate_and_save_are_single_flight(app, auth_user, training_page, generator)
    assert_served(page, ['savePlanOption', 'confirmPlanReplace'], 'if (i === null) return;')


def test_n8_unescaped_option_markup_is_detected(app, auth_user, client, training_page, generator):
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'N8')
    with pytest.raises(AssertionError):
        browser.test_generated_and_persisted_markup_renders_as_text(app, auth_user, training_page, generator)
    assert_served(page, ['renderPlans'], '>${planName(plan, i)}<')


def test_n9_planned_as_logged_is_detected(app, auth_user, client, training_page):
    with pytest.raises(AssertionError):
        contract.test_planned_is_never_logged_on_plan(script=mutate(contract.SCRIPT, 'N9'))
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'N9')
    with pytest.raises(AssertionError):
        browser.test_active_plan_is_current_with_planned_meals(app, auth_user, client, training_page, 'en')
    assert_served(page, ['renderActivePlanDetail'], "__t('nutrition.logged_state')")


# ── P5-N10 · P5-N11 · template ─────────────────────────────────────────


def test_n10_inline_supplement_crud_is_detected(app, client, make_user, login, monkeypatch):
    patch_template(app, monkeypatch, 'N10')
    html = contract.render(client, make_user, login, 'en')
    assert 'action="/supplement/add"' in html                        # the mutated page was served
    with pytest.raises(AssertionError):
        contract.test_supplements_is_only_a_child_link(client, make_user, login, html=html)


def test_n11_unsupported_grocery_guide_is_detected(app, client, make_user, login, monkeypatch):
    patch_template(app, monkeypatch, 'N11')
    html = contract.render(client, make_user, login, 'en')
    assert 'id="grocery-guide"' in html
    with pytest.raises(AssertionError):
        contract.test_grocery_guide_is_absent(client, make_user, login, html=html)


# ── P5-N12 · Today convergence ─────────────────────────────────────────


def test_n12_today_keeping_the_old_plan_is_detected(app, auth_user, client, training_page, generator):
    page, _, _, _ = training_page
    serve_mutated_script(page, client, 'N12')
    with pytest.raises(AssertionError):
        browser.test_confirmed_replacement_saves_once_and_plan_and_today_converge(
            app, auth_user, training_page, generator, 'en')
    assert_served(page, ['applyNutritionNavigation'], '{ loadTodayData(); }')
    source = page.evaluate('() => savePlanOption.toString()')
    assert 'loadQuickAddSection' not in source


def test_every_script_mutation_targets_shipped_code():
    for name in SCRIPT_MUTATIONS:
        assert mutate(contract.SCRIPT, name) != contract.SCRIPT, name
    assert json.dumps(sorted(SCRIPT_MUTATIONS) + sorted(TEMPLATE_MUTATIONS)) == json.dumps(
        ['N1', 'N12', 'N2', 'N3', 'N5', 'N6', 'N7', 'N8', 'N9', 'N10', 'N11'])
