"""Resolve inventory-driven capture tiers into an explicit browser plan."""

from __future__ import annotations

from dataclasses import dataclass


REPRESENTATIVE_VIEWPORTS = ("mobile-390", "desktop-1440")


@dataclass(frozen=True, slots=True)
class CaptureSpec:
    route_id: str
    route: str
    scenario_id: str
    state: str
    viewport: str
    browser: str
    stress_profile: str | None = None

    @property
    def id(self) -> str:
        parts = [
            self.route_id,
            self.scenario_id,
            self.state,
            self.viewport,
            self.browser,
        ]
        if self.stress_profile:
            parts.append(self.stress_profile)
        return "--".join(parts)


def route_url(route: str) -> str:
    route = route.replace("<username>", "audit-friend").replace("<code>", "audit-code")
    if route == "/verify":
        return "/verify?u=audit-anonymous"
    if route == "/reset-password":
        return "/__audit__/reset-password"
    return route


def _base_spec(route: dict, scenario: str, viewport: str, browser: str, stress: str | None = None) -> CaptureSpec:
    return CaptureSpec(
        route_id=route["id"],
        route=route_url(route["route"]),
        scenario_id=scenario,
        state=route.get("states", ["default"])[0],
        viewport=viewport,
        browser=browser,
        stress_profile=stress,
    )


def _responsive_plan(inventory: dict) -> list[CaptureSpec]:
    captures = []
    for route in inventory["routes"]:
        coverage = route["coverage"]
        if coverage["tier"] == "excluded":
            continue
        primary, *secondary = coverage["scenarios"]
        for viewport in coverage["viewports"]:
            captures.append(_base_spec(route, primary, viewport, "chromium"))
        for scenario in secondary:
            for viewport in REPRESENTATIVE_VIEWPORTS:
                if viewport in coverage["viewports"]:
                    captures.append(_base_spec(route, scenario, viewport, "chromium"))
    return captures


def _stress_plan(inventory: dict) -> list[CaptureSpec]:
    captures = []
    for route in inventory["routes"]:
        coverage = route["coverage"]
        if coverage["tier"] == "excluded" or not coverage["stress_profiles"]:
            continue
        scenario = coverage["scenarios"][0]
        for stress in coverage["stress_profiles"]:
            captures.append(_base_spec(route, scenario, "mobile-390", "chromium", stress))
    return captures


def _cross_browser_plan(inventory: dict) -> list[CaptureSpec]:
    captures = []
    for route in inventory["routes"]:
        coverage = route["coverage"]
        if coverage["tier"] == "excluded":
            continue
        scenario = coverage["scenarios"][0]
        for browser in coverage["browsers"]:
            if browser == "chromium":
                continue
            for viewport in REPRESENTATIVE_VIEWPORTS:
                captures.append(_base_spec(route, scenario, viewport, browser))
    return captures


def _deduplicate(captures: list[CaptureSpec]) -> list[CaptureSpec]:
    return list({capture.id: capture for capture in captures}.values())


def resolve_capture_plan(inventory: dict, tier: str) -> list[CaptureSpec]:
    if tier == "smoke":
        by_id = {route["id"]: route for route in inventory["routes"]}
        return [
            _base_spec(by_id["landing"], "anonymous", "mobile-390", "chromium"),
            _base_spec(by_id["dashboard"], "active-workout", "mobile-390", "chromium"),
        ]
    if tier == "responsive":
        return _deduplicate(_responsive_plan(inventory))
    if tier == "stress":
        return _deduplicate(_stress_plan(inventory))
    if tier == "cross-browser":
        return _deduplicate(_cross_browser_plan(inventory))
    if tier == "full":
        return _deduplicate(
            _responsive_plan(inventory)
            + _stress_plan(inventory)
            + _cross_browser_plan(inventory)
        )
    raise ValueError(f"unsupported capture tier: {tier}")


def print_capture_plan(plan: list[CaptureSpec], viewports: dict) -> None:
    print("route | scenario | state | viewport | browser | stress")
    for capture in plan:
        dimensions = viewports[capture.viewport]
        size = f"{dimensions['width']}x{dimensions['height']}"
        print(
            f"{capture.route_id} | {capture.scenario_id} | {capture.state} | "
            f"{capture.viewport} ({size}) | {capture.browser} | {capture.stress_profile or '-'}"
        )
    print(f"resolved captures: {len(plan)}")
