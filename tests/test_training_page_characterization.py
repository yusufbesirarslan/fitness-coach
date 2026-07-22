"""Characterization coverage for the `/training` page (Sprint 6 PR6.1).

Written and green BEFORE the Adaptive Weekly Program UI mount shell was added, so
the OFF path of that rollout is provably the pre-PR6.1 page. `tests/test_training_ui.py`
already pins a handful of Phase-5 markers; this module pins the whole meaningful
contract: route/auth behavior, the major DOM regions, every declarative control, the
navigation block, and the asset/script wiring.

Byte identity is deliberately NOT asserted here. `/training` embeds three values that
change per boot or per request — the `_v` cache-buster (`app/hooks.py`, boot
timestamp), the per-request CSP nonce and the CSRF token — so a committed golden would
be noise, and the repository has no stable-snapshot convention to reuse. A one-time
normalized before/after diff of the OFF-path HTML was run during implementation
instead (recorded in the PR6.1 handoff); what lives here is the DOM contract.

Fixture pattern is the suite-wide `app, client, make_user, login` (tests/conftest.py).

    python -m pytest tests/test_training_page_characterization.py -v
"""
import pytest


@pytest.fixture
def training_html(client, make_user, login):
    """The authenticated `/training` document, once per test."""
    make_user("charuser", profile_complete=True)
    login("charuser")
    response = client.get("/training")
    assert response.status_code == 200
    return response.get_data(as_text=True)


# ── route / auth ────────────────────────────────────────────────────────────────

def test_training_requires_authentication(client):
    response = client.get("/training")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_training_renders_for_an_authenticated_user(client, make_user, login):
    make_user("charauth", profile_complete=True)
    login("charauth")
    response = client.get("/training")
    assert response.status_code == 200
    assert response.mimetype == "text/html"


def test_training_renders_the_training_template(app, client, make_user, login):
    from flask import template_rendered

    rendered = []

    def record(sender, template, **extra):
        rendered.append(template.name)

    # blinker holds receivers weakly — `record` must stay referenced for the request.
    with template_rendered.connected_to(record, app):
        make_user("chartpl", profile_complete=True)
        login("chartpl")
        client.get("/training")
    assert "training.html" in rendered


# ── major DOM regions ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("marker", [
    'id="active-plan-view"',   # plan varsa gösterilen görünüm (training.js açar)
    'id="setup-form"',         # plan yoksa gösterilen kurulum akışı
    'id="workout-hero"',
    'id="week-strip"',
    'id="wstats"',
    'class="apv-meta-row"',
    'id="info-banner"',
    'id="results"',
    'id="weekly-grid"',
    'id="session-view"',
    'id="celebration"',
    'id="rest-timer"',
    'id="pump-check-modal"',
    'id="pump-crop"',
    'id="day-preview"',
    'id="toast-wrap"',
    'id="loading"',
])
def test_existing_dom_regions_are_present(training_html, marker):
    assert marker in training_html


@pytest.mark.parametrize("grid_id", [
    "gun-grid", "tarzi-grid", "hedef-grid", "ekipman-grid",
    "odak-grid", "sure-grid", "kardiyo-tip-grid", "injury-grid",
])
def test_setup_form_option_grids_are_present(training_html, grid_id):
    assert 'id="%s"' % grid_id in training_html


# ── controls ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("action", [
    "startWorkout", "generatePlan", "savePlan", "resetPlan",
    "submitPumpCheck", "finishSession", "closeSession",
    "closeCelebration", "addRest", "skipRest",
])
def test_existing_declarative_controls_are_present(training_html, action):
    assert 'data-action="%s"' % action in training_html


@pytest.mark.parametrize("control_id", [
    "submit-btn", "save-btn", "pump-submit", "pump-cancel", "pump-close",
    "pump-file-input", "pump-location", "pump-desc", "injury-other",
])
def test_existing_form_controls_are_present(training_html, control_id):
    assert 'id="%s"' % control_id in training_html


def test_pump_share_selector_is_present(training_html):
    assert 'data-share="feed"' in training_html
    assert 'data-share="friends"' in training_html
    assert 'id="pump-friend-picker"' in training_html


# ── navigation ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("href", [
    'href="/"', 'href="/nutrition"', 'href="/training"',
    'href="/progress-page"', 'href="/edit-profile"', 'href="/notifications"',
])
def test_existing_navigation_entries_are_present(training_html, href):
    assert href in training_html


def test_training_is_the_active_navigation_entry(training_html):
    assert 'href="/training" class="hn-link active" aria-current="page"' in training_html


# ── assets / scripts ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("asset", [
    "/static/training.css", "/static/theme.css", "/static/nav.css",
    "/static/training.js", "/static/coach_widget.js", "/static/actions.js",
])
def test_existing_assets_are_wired(training_html, asset):
    assert asset in training_html


def test_inline_training_bootstrap_carries_a_csp_nonce(training_html):
    assert "window.__TRAINING" in training_html
    marker = training_html.index("window.__TRAINING")
    assert "nonce=" in training_html[marker - 120:marker]


def test_csrf_meta_tag_is_present(training_html):
    assert 'name="csrf-token"' in training_html


def test_no_legacy_volt_token_leaks_into_the_page(training_html):
    assert "--volt" not in training_html


# ── default state: the weekly-program UI is absent ──────────────────────────────

@pytest.mark.parametrize("marker", [
    'id="weekly-program"',
    "data-weekly-program-mount",
    "/static/weekly_program.js",
    "/api/training/weekly-program",
])
def test_weekly_program_ui_is_absent_by_default(training_html, marker):
    """The PR6.1 rollout gate defaults OFF, so a stock `/training` carries no shell,
    no mount attribute, no script tag and no endpoint string."""
    assert marker not in training_html


def test_bare_weekly_program_substring_is_pre_existing_catalog_noise(training_html):
    """Guards the guard above: the literal `weekly_program` ALREADY appears on every
    page, because `_head.html` injects the whole locale catalog into `window.I18N`
    and `locales/{tr,en}.json` carries an unused `training.weekly_program` key.

    So weekly-program presence/absence must always be asserted with a precise marker
    (`id=`, `data-`, an asset path), never the bare substring. Recorded here so a
    later PR does not "fix" the precise markers into a false-failing broad one."""
    assert "training.weekly_program" in training_html
    assert 'id="weekly-program"' not in training_html
