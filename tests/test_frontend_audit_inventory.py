import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "docs" / "frontend-readiness" / "sprint-0" / "inventory.json"
FULL_VIEWPORTS = {
    "mobile-320", "mobile-360", "mobile-375", "mobile-390",
    "mobile-430", "tablet-768", "wide-1024", "desktop-1440",
}


def load_inventory():
    return json.loads(INVENTORY.read_text(encoding="utf-8"))


def test_inventory_validates_against_live_production_routes(app):
    from scripts.frontend_audit.validation import validate_inventory

    known_routes = {rule.rule for rule in app.url_map.iter_rules()}
    document = load_inventory()
    validate_inventory(document, known_routes=known_routes)


def test_inventory_covers_every_rendered_template():
    rendered = set()
    pattern = re.compile(r'render_template\(\s*["\']([^"\']+\.html)')
    for path in (ROOT / "app").rglob("*.py"):
        rendered.update(pattern.findall(path.read_text(encoding="utf-8")))
    inventoried = {route["template"] for route in load_inventory()["routes"]}
    assert rendered <= inventoried


def test_all_routes_have_representative_or_explicit_excluded_coverage():
    document = load_inventory()
    for route in document["routes"]:
        coverage = route["coverage"]
        if coverage["tier"] == "excluded":
            assert coverage["exclusion_reason"]
            continue
        assert {"mobile-390", "desktop-1440"} <= set(coverage["viewports"])
        assert "chromium" in coverage["browsers"]


def test_beta_and_responsive_risk_routes_have_full_matrix():
    document = load_inventory()
    required_full = {
        "login", "register", "forgot-password", "reset-password", "verify",
        "setup", "dashboard", "nutrition", "training", "progress",
    }
    by_id = {route["id"]: route for route in document["routes"]}
    assert required_full <= set(by_id)
    for route_id in required_full:
        coverage = by_id[route_id]["coverage"]
        assert coverage["tier"] == "full"
        assert FULL_VIEWPORTS <= set(coverage["viewports"])


def test_inventory_collectively_covers_every_required_viewport():
    covered = {
        viewport
        for route in load_inventory()["routes"]
        for viewport in route["coverage"]["viewports"]
    }
    assert FULL_VIEWPORTS <= covered
