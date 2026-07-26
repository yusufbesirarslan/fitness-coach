"""Render/regression tests for the Phase 5 workout redesign (Task 9):
canonical markup presence, external asset wiring, no legacy --volt token
leakage. Mirrors the fixture pattern used across the suite
(`app, client, make_user, login` — see tests/conftest.py / tests/test_i18n.py);
there is no `auth_client` fixture in this project."""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"


def test_training_renders_hero_and_session(app, client, make_user, login):
    make_user("wkuiuser", profile_complete=True)
    login("wkuiuser")
    r = client.get("/training")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # new canonical markup present
    assert 'id="workout-hero"' in html
    assert 'id="session-view"' in html
    assert 'id="celebration"' in html
    assert 'id="rest-timer"' in html
    assert 'data-action="startWorkout"' in html
    # no legacy volt styling leaked into the page
    assert '--volt' not in html


def test_training_loads_external_assets(app, client, make_user, login):
    make_user("wkuiuser2", profile_complete=True)
    login("wkuiuser2")
    html = client.get("/training").get_data(as_text=True)
    assert '/static/training.js' in html
    assert '/static/training.css' in html


def test_wstats_collapses_to_fewer_columns_on_narrow_screens():
    """Regression (UIUX Sprint 1 PR3 legacy compatibility fix): the weekly-stats
    row `.wstats` is a fixed 3-column grid whose stat cards cannot shrink below
    their (locale-dependent) text min-content, so at 320px the row overflowed the
    viewport by 24px (EN) / 36px (TR) — a defect pre-existing on base 9400641 (see
    docs/frontend-readiness/sprint-1-pr3/legacy-overflow/). A narrow-width media
    query (<=389px, so the passing 390px+ layout is untouched) must collapse
    `.wstats` to two shrinkable columns so the row fits. Legacy-only: training.css
    is loaded solely by templates/training.html and `.wstats` appears nowhere
    else, so this cannot affect Plan V2 (plan.html/plan.css) or Nav/Today."""
    css = (STATIC / "training.css").read_text(encoding="utf-8")
    # Base row is still the fixed 3-column grid at >=390px (unchanged).
    assert re.search(r"\.wstats\s*\{[^}]*grid-template-columns:\s*repeat\(3", css), \
        "base .wstats should remain a 3-column grid"
    # A <=389px media query reduces .wstats to two columns.
    blocks = re.findall(
        r"@media\s*\(max-width:\s*(\d+)px\)\s*(\{(?:[^{}]|\{[^{}]*\})*\})", css)
    narrow = [
        int(px) for px, body in blocks
        if int(px) <= 389
        and re.search(r"\.wstats\s*\{[^}]*grid-template-columns:\s*repeat\(2", body)
    ]
    assert narrow, "expected a <=389px media query collapsing .wstats to 2 columns"
