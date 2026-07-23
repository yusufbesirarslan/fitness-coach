import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "docs" / "frontend-readiness" / "sprint-0" / "inventory.json"


def load_inventory():
    return json.loads(INVENTORY.read_text(encoding="utf-8"))


def test_capture_tiers_resolve_without_duplicate_ids():
    from scripts.frontend_audit.plan import resolve_capture_plan

    document = load_inventory()
    plans = {tier: resolve_capture_plan(document, tier) for tier in (
        "smoke", "responsive", "stress", "cross-browser", "full"
    )}
    for tier, plan in plans.items():
        ids = [capture.id for capture in plan]
        assert len(ids) == len(set(ids)), tier
    assert {capture.id for capture in plans["responsive"]} <= {capture.id for capture in plans["full"]}
    assert {capture.id for capture in plans["stress"]} <= {capture.id for capture in plans["full"]}
    assert {capture.id for capture in plans["cross-browser"]} <= {capture.id for capture in plans["full"]}


def test_full_plan_applies_primary_and_secondary_scenario_coverage():
    from scripts.frontend_audit.plan import resolve_capture_plan

    plan = resolve_capture_plan(load_inventory(), "full")
    nutrition = [capture for capture in plan if capture.route_id == "nutrition" and not capture.stress_profile and capture.browser == "chromium"]
    primary = {capture.viewport for capture in nutrition if capture.scenario_id == "new-empty"}
    secondary = {capture.viewport for capture in nutrition if capture.scenario_id == "active-workout"}
    assert primary == {
        "mobile-320", "mobile-360", "mobile-375", "mobile-390",
        "mobile-430", "tablet-768", "wide-1024", "desktop-1440",
    }
    assert secondary == {"mobile-390", "desktop-1440"}


def test_stress_and_cross_browser_are_inventory_selected():
    from scripts.frontend_audit.plan import resolve_capture_plan

    stress = resolve_capture_plan(load_inventory(), "stress")
    assert stress
    assert all(capture.stress_profile for capture in stress)
    assert {capture.stress_profile for capture in stress} >= {"keyboard", "text-200", "dark-mode"}

    cross = resolve_capture_plan(load_inventory(), "cross-browser")
    assert cross
    assert {capture.browser for capture in cross} == {"webkit", "firefox"}
    assert {capture.viewport for capture in cross} <= {"mobile-390", "desktop-1440"}


def test_route_url_substitutes_inventory_parameters():
    from scripts.frontend_audit.plan import route_url

    assert route_url("/chat/<username>") == "/chat/audit-friend"
    assert route_url("/davet/<code>") == "/davet/audit-code"
    assert route_url("/verify") == "/verify?u=audit-anonymous"
    assert route_url("/reset-password") == "/__audit__/reset-password"


def test_plan_printer_lists_resolved_dimensions(capsys):
    from scripts.frontend_audit.plan import print_capture_plan, resolve_capture_plan

    plan = resolve_capture_plan(load_inventory(), "smoke")
    print_capture_plan(plan, load_inventory()["viewports"])
    output = capsys.readouterr().out
    assert "route" in output
    assert "scenario" in output
    assert "390x844" in output
    assert f"resolved captures: {len(plan)}" in output


def test_windows_10_is_diagnostic_not_supported():
    from scripts.frontend_audit.preflight import classify_environment

    result = classify_environment(system="Windows", release="10", version="10.0.19045")
    assert result["supported"] is False
    assert result["reason"] == "Playwright documents native Windows 11+ support"
    linux = classify_environment(system="Linux", release="6.8", version="Ubuntu 24.04")
    assert linux["supported"] is True
