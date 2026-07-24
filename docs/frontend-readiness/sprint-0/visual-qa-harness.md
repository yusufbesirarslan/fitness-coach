# Visual QA Harness

The harness lives in `scripts/frontend_audit` and is isolated from production behavior. It creates only `instance/axis_frontend_audit.db`, rejects non-loopback servers and non-SQLite audit databases, disables external services, and installs audit-only login/error routes solely on the audit application factory.

Preflight verifies Python, OS support, Playwright package, version-coupled Chromium installation, process launch, loopback navigation, screenshot creation, and shutdown. Install browsers with:

```text
python -m playwright install chromium
```

Updating Playwright can change the required browser revision; reinstall browser binaries after changing the package version.

The application server, SQLite database, Playwright runner, and report output must run in the same supported Linux/WSL/container environment. Do not assume host and container `localhost` are shared. A bridge, if used, must be explicit and still satisfy the loopback/non-production guard.

Commands:

```text
python -m scripts.frontend_audit preflight --output artifacts/ui-audit/preflight
python -m scripts.frontend_audit seed
python -m scripts.frontend_audit capture --tier smoke --dry-run
python -m scripts.frontend_audit capture --tier full --output artifacts/ui-audit/full
python -m scripts.frontend_audit verify --manifest artifacts/ui-audit/full/manifest.json
```

The runner prints the resolved plan first, sets viewport and relevant stress emulation, installs a browser-side fixed Date, authenticates through audit-only routes, and records screenshot, console, request, overflow, placeholder, fixed-bottom, and active-navigation observations. Deterministic filenames prevent silent ambiguity.

Current result (2026-07-23, supported run): executed inside WSL Ubuntu-24.04 (Linux, `operating_system.supported: true`). The Chromium preflight passed every check — process launch, loopback navigation, screenshot, and clean shutdown. The full inventory-driven `capture --tier full` run captured all 277 mandatory Chromium captures with zero failures; the 48 optional WebKit/Firefox captures are recorded as `blocked` because those browser binaries are not installed in this environment and installing their system libraries needs privileged apt access. `verify --manifest` reports `audit artifacts valid`. Curated, schema-validated evidence for this run lives under `evidence/2026-07-23/` (manifest + 277 screenshots + per-capture result JSONs). The earlier Windows 10 host run remains diagnostic-only (unsupported target); it is no longer the basis for completion.

## Harness environment ordering (hermetic guarantee)

The hermetic audit environment must be configured **before importing any `app.*` module**. `app/config.py` calls `load_dotenv()` at import time and binds several settings into module-level constants; importing application modules first freezes repository-root production `.env` values into those constants, and the bake-in cannot be undone in-process. A validation harness must therefore pin external integrations — notably `FATSECRET_BASE_URL` — to hermetic, non-production values before the first `app.*` import (call `configure_audit_environment(...)` first; it also sets `FITX_SKIP_DB_INIT`, empties `REDIS_URL`, disables Bedrock, and points integration URLs at loopback/`*.invalid`). The harness must **never** contact production or externally hosted dependencies during UI validation. (Symptom of getting this wrong: a startup `RuntimeError: FATSECRET_BASE_URL must use https://` because the production HTTP URL was baked in before the hermetic override ran.)

## Navigation acceptance measurement (responsive shells)

A responsive shell can render the primary navigation twice — one variant per breakpoint (desktop header + mobile action bar) — so a raw DOM instance count legitimately includes **both** responsive shell variants. Acceptance must not treat that as a duplicate-nav defect. Instead it must verify **exactly one accessibility-exposed and interactive** primary navigation instance at the active breakpoint: the inactive variant is hidden with `display:none`, which removes it (and its `aria-current`) from the accessibility tree and the keyboard tab order. Record both the raw DOM instance count and the accessibility-exposed count; only the exposed count is asserted to equal one.
