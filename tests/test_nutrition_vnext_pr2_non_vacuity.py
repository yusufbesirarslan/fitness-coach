"""Controlled in-memory mutations of the real shipped template/script.

Each mutation must cause its matching normal PR2 assertion to fail. No product
file is changed; pytest's scoped patches and browser routes restore automatically.
"""
import pytest
from playwright.sync_api import expect
from test_training_execution_boundary import training_page  # noqa: F401
from test_nutrition_vnext_pr2_navigation_contract import (
    test_exactly_two_primary_modes as assert_modes,
    test_today_workflows_are_real_disclosures as assert_workflow,
)
from test_nutrition_vnext_pr2_navigation_browser import (
    test_initial_and_lazy_requests as assert_topology,
    test_no_plan_shortcut_retains_real_handoff as assert_handoff,
)


@pytest.mark.parametrize('mutation,tool', [
    ('M1', 'diary'), ('M2', 'history'), ('M3', 'water'), ('M4', 'diary'),
    ('M5', 'diary'), ('M6', 'history'), ('M7', 'water'),
])
def test_template_mutation_is_detected(app, client, make_user, login, monkeypatch, mutation, tool):
    loader = app.jinja_env.loader
    original = loader.get_source
    def mutated(environment, template):
        source, filename, uptodate = original(environment, template)
        if template == 'nutrition.html':
            if mutation in {'M1', 'M2', 'M3'}:
                source = source.replace('  <!-- Tab Bar -->',
                    '<button role="tab" class="tab-btn" data-tab-name="' + tool + '">' + tool + '</button>\n  <!-- Tab Bar -->')
            elif mutation == 'M4':
                source = source.replace('<summary id="nutrition-tab-diary"', '<summary role="tab" id="nutrition-tab-diary"')
            else:
                source = source.replace('<summary id="nutrition-tab-' + tool + '"', '<span id="nutrition-tab-' + tool + '"')
        return source, filename, uptodate
    monkeypatch.setattr(loader, 'get_source', mutated)
    app.jinja_env.cache.clear()
    with pytest.raises(AssertionError):
        if mutation in {'M1', 'M2', 'M3', 'M4'}:
            assert_modes(client, make_user, login, 'en')
        else:
            assert_workflow(client, make_user, login, tool)


@pytest.mark.parametrize('mutation', ['M8', 'M9'])
def test_script_mutation_is_detected(app, auth_user, client, training_page, mutation):
    page, _, _, _ = training_page
    original = client.get('/static/nutrition.js').get_data(as_text=True)
    if mutation == 'M8':
        mutated = original + '\nloadDiary();\n'
    else:
        mutated = original + "\nfxGoToPlanTab = function () { switchTab('today'); };\n"
    page.route('**/static/nutrition.js*', lambda route: route.fulfill(
        status=200, content_type='application/javascript', body=mutated))
    with pytest.raises(AssertionError):
        if mutation == 'M8':
            assert_topology(app, auth_user, training_page)
        else:
            assert_handoff(app, auth_user, training_page)
