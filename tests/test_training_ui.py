"""Canonical Plan page render/regression tests after WEB-UX3-PR6B."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
PLAN_MANAGEMENT = STATIC / "training_plan_management.js"


def test_training_renders_canonical_plan(app, client, make_user, login):
    make_user("wkuiuser", profile_complete=True)
    login("wkuiuser")
    r = client.get("/training")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "data-plan-v2" in html
    assert 'id="plan-page"' in html
    assert 'data-manage-state="create"' in html
    assert "--volt" not in html


def test_training_loads_external_assets(app, client, make_user, login):
    make_user("wkuiuser2", profile_complete=True)
    login("wkuiuser2")
    html = client.get("/training").get_data(as_text=True)
    assert "/static/plan.css" in html
    assert "/static/training_plan_management.js" in html
    assert "/static/plan_training_manage.js" in html
    assert "/static/training.js" not in html
    assert "/static/training.css" not in html


def test_legacy_training_client_is_gone():
    assert not (STATIC / "training.js").exists()
    assert not (STATIC / "training.css").exists()
    assert not (STATIC.parent / "templates" / "training.html").exists()


def test_shared_management_contract_carries_the_context_token():
    """The token is a carrier, not client state. After WEB-UX3-PR6B the
    generate→save carry lives only in the shared Training-management contract."""
    shared = PLAN_MANAGEMENT.read_text(encoding="utf-8")
    assert "exercise_context_token: body.exercise_context_token" in shared
    assert "exercise_context_token: proposal.exercise_context_token" in shared
    for forbidden in (
        "atob(",
        "localStorage.setItem('exercise_context_token'",
    ):
        assert forbidden not in shared, forbidden
    token_lines = [
        line for line in shared.splitlines() if "exercise_context_token" in line
    ]
    for line in token_lines:
        for word in ("localStorage", "sessionStorage", "innerHTML", "searchParams"):
            assert word not in line, line
    assert re.search(r"exercise_context_token", shared)
