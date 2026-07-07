"""Render/regression tests for the Phase 5 workout redesign (Task 9):
canonical markup presence, external asset wiring, no legacy --volt token
leakage. Mirrors the fixture pattern used across the suite
(`app, client, make_user, login` — see tests/conftest.py / tests/test_i18n.py);
there is no `auth_client` fixture in this project."""


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
