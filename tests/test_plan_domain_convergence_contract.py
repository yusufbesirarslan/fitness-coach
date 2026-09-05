"""UX-3 PR1 characterization guards for the Plan convergence handoff.

These facts are expected to change only through an explicit architecture PR.
They exercise Flask's registered URL map, the production navigation resolver,
and the request-time template selector rather than decorating source strings.
"""

from app import nav


def _seed_login(make_user, login):
    make_user("plancontract", profile_complete=True)
    login("plancontract")


def test_plan_and_child_domain_routes_remain_registered_to_current_owners(app):
    adapter = app.url_map.bind("")

    assert adapter.match("/training", method="GET")[0] == "training.training"
    assert adapter.match("/nutrition", method="GET")[0] == "nutrition.nutrition"
    assert (
        adapter.match("/supplements", method="GET")[0]
        == "supplements.supplements_page"
    )


def test_plan_primary_route_and_child_active_ownership_are_one_contract():
    primary = {item["id"]: item for item in nav.primary_destinations()}

    assert primary["plan"]["path"] == "/training"
    assert primary["plan"]["active_when"] == (
        "plan",
        "training",
        "nutrition",
        "supplements",
    )
    for child in ("training", "nutrition", "supplements"):
        assert nav.resolve_active(child) == "plan"


def test_plan_flag_is_an_atomic_request_time_template_selector(
    app, client, make_user, login
):
    _seed_login(make_user, login)

    app.config["UIUX_PLAN_V2_ENABLED"] = False
    legacy = client.get("/training")
    assert legacy.status_code == 200
    legacy_html = legacy.get_data(as_text=True)
    assert "data-plan-v2" not in legacy_html
    assert "/static/training.js" in legacy_html

    app.config["UIUX_PLAN_V2_ENABLED"] = True
    plan = client.get("/training")
    assert plan.status_code == 200
    plan_html = plan.get_data(as_text=True)
    assert "data-plan-v2" in plan_html
    assert "/static/training.js" not in plan_html
