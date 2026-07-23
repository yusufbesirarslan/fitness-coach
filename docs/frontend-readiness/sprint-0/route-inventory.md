# Route Inventory

`inventory.json` version `1.0.0` is canonical. It declares 25 user-facing or audit-only HTML routes, 11 deterministic scenarios, eight viewport definitions, browser requirements, and relevant stress profiles.

Every production HTML route receives at least Chromium coverage at `390x844` and `1440x900`. Full responsive coverage is assigned to authentication, onboarding, Dashboard/Today, Nutrition, Training, Progress, profile/settings, all fixed-navigation application pages, charts, grids, sheets, and complex forms. All eight required widths appear application-wide without imposing a route-by-viewport Cartesian product. Audit-only 404 and 500 routes are explicitly marked.

Coverage tiers are inventory-derived:

- `smoke`: one minimal Chromium path for development health.
- `responsive`: declared Chromium route/scenario/viewport coverage.
- `stress`: only declared state-risk profiles such as keyboard, 200% text, landscape, reduced motion, and dark mode.
- `cross-browser`: declared WebKit/Firefox samples; these do not replace mandatory Chromium.
- `full`: the union required for Sprint 0 completion.

The CLI prints its resolved route, scenario, state, viewport, browser, and stress-profile plan before launching a browser. Scenario clocks come from `scenario-clocks.json`; host date and timezone do not select workout or history state.

Run `python -m scripts.frontend_audit inventory` to print the inventory and `python -m scripts.frontend_audit capture --tier full --dry-run` to inspect the completion plan.
