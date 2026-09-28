"""Controlled in-memory mutations of the shipped hydration script/template.

Each mutation reintroduces one defect the hydration contract forbids and must
make its matching normal assertion fail. No product file is changed: the served
`nutrition.js` is replaced per page via a browser route, the template through a
scoped loader patch; both restore automatically.
"""
import pytest

from test_training_execution_boundary import training_page  # noqa: F401
from test_nutrition_vnext_pr2_navigation_contract import test_exactly_two_primary_modes as assert_modes
import test_nutrition_hydration_reliability_browser as hydration

SCRIPT_MUTATIONS = {
    # A failed read becomes a confirmed zero.
    'failed_read_is_zero': (
        [("  else waterState = 'unavailable';\n", "  else confirmWater(0);\n")],
        hydration.test_failed_read_without_confirmed_state_is_not_zero),
    # Optimistic success: the requested total is drawn before the server answers.
    'optimistic_success': (
        [("  const seq = ++waterSeq;\n  waterState = 'saving';\n",
          "  const seq = ++waterSeq;\n  waterConfirmed = next;\n  waterState = 'saving';\n")],
        lambda *a: hydration.test_no_success_before_canonical_confirmation(*a, '#water-btn')),
    # Confirmation removed: an unanswered write is assumed to have landed.
    'confirmation_removed': (
        [("  const check = await requestWater();\n", "  const check = { kind: 'ok', count: next };\n")],
        hydration.test_confirmation_failure_does_not_fabricate_certainty),
    # Stale-response fencing removed.
    'fencing_removed_read': (
        [("if (seq !== waterSeq) return", "if (false) return")],
        hydration.test_older_read_cannot_overwrite_newer_read),
    'fencing_removed_write': (
        [("if (seq !== waterSeq) return", "if (false) return")],
        hydration.test_pre_write_read_cannot_overwrite_post_write_confirmation),
    # Extra eager read at initial load.
    'extra_initial_read': (
        [("function initWaterButton() {", "setTimeout(() => loadWater(), 0);\nfunction initWaterButton() {")],
        hydration.test_request_topology),
    # Opening Water starts issuing its own read.
    'read_on_open': (
        [("function initWaterButton() {\n  buildWaterGlasses();\n",
          "function initWaterButton() {\n  buildWaterGlasses();\n"
          "  document.getElementById('nutrition-tool-water').addEventListener('toggle', () => loadWater());\n")],
        hydration.test_request_topology),
}


def mutate(original, replacements):
    # The working tree may be CRLF (core.autocrlf); compare on LF.
    original = original.replace('\r\n', '\n')
    mutated = original
    for old, new in replacements:
        assert old in mutated, 'mutation target drifted: ' + old
        mutated = mutated.replace(old, new)
    assert mutated != original
    return mutated


@pytest.mark.parametrize('name', sorted(SCRIPT_MUTATIONS))
def test_script_mutation_is_detected(app, auth_user, client, training_page, name):
    replacements, assertion = SCRIPT_MUTATIONS[name]
    page = training_page[0]
    mutated = mutate(client.get('/static/nutrition.js').get_data(as_text=True), replacements)
    page.route('**/static/nutrition.js*', lambda route: route.fulfill(
        status=200, content_type='application/javascript', body=mutated))
    with pytest.raises(AssertionError):
        assertion(app, auth_user, client, training_page)


def test_water_as_primary_tab_is_detected(app, client, make_user, login, monkeypatch):
    loader = app.jinja_env.loader
    original = loader.get_source

    def mutated(environment, template):
        source, filename, uptodate = original(environment, template)
        if template == 'nutrition.html':
            marker = '  <!-- Tab Bar -->'
            assert marker in source
            source = source.replace(marker, '<button role="tab" class="tab-btn" data-tab-name="water">water</button>\n' + marker)
        return source, filename, uptodate
    monkeypatch.setattr(loader, 'get_source', mutated)
    app.jinja_env.cache.clear()
    with pytest.raises(AssertionError):
        assert_modes(client, make_user, login, 'en')
