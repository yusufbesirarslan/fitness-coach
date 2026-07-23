# AxisAI Frontend Readiness Sprint 0 Design

## Objective

Verify the supplied frontend-readiness audit against the current Flask/Jinja
application, establish a complete inventory, and add a reproducible visual-QA
harness without redesigning production UI.

## Architecture

The audit is a local-only Python package under `scripts/frontend_audit`. It wraps
the production factory with hermetic SQLite data, synthetic users, deterministic
clocks, and audit-only routes never registered in production. A versioned
inventory declares route/scenario/state/browser/viewport coverage, and a Python
Playwright runner emits schema-validated evidence.

Every HTML route receives representative mobile and desktop coverage. Routes
with beta or responsive risk receive the full Chromium matrix; stress and
cross-browser checks are inventory-selected. Chromium must run in a supported
environment for Sprint 0 completion.

## Evidence and safety

The unchanged external audit is imported with SHA-256 provenance and converted
into deduplicated material findings. Audit startup refuses unsafe hosts and
non-SQLite databases. External services are disabled or locally substituted.
Production behavior remains unchanged except for a test/audit clock hook whose
default is real Istanbul time.
