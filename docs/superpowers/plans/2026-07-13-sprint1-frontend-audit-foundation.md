# Sprint 1 Frontend Audit & Design System Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Audit every AxisAI frontend surface at the required viewport matrix and fix only cross-cutting design-system, responsive, navigation, interaction-state, localization, and accessibility defects.

**Architecture:** Treat the screen inventory and evidence manifest as executable audit contracts. Make shared changes in the canonical token, component, navigation, auth-shell, and error-surface layers; retain existing public class names as compatibility APIs and defer page composition changes to later sprints.

**Tech Stack:** Flask/Jinja, vanilla JavaScript, CSS custom properties, pytest, the existing in-app browser/Playwright-compatible browser tooling, JSON/Markdown audit artifacts.

## Global Constraints

- Do not redesign an individual page.
- Do not change business logic, backend behavior, route contracts, models, or database schema.
- Do not introduce new product features.
- Preserve the established AxisAI identity: electric blue primary color, dark-first surfaces, light-theme support, Bebas Neue display typography, and Inter body typography.
- Prefer existing canonical tokens and components. Add or change a shared token only when it represents a genuine reusable semantic role.
- Do not add a page-specific override to conceal a shared defect.
- Keep CSP nonce, CSRF, localization, analytics, and authentication contracts intact.
- Preserve unrelated and pre-existing uncommitted work.
- Required acceptance viewports are 390 × 844, 768 × 1024, and 1280 × 900; 320 × 720 and 1440 × 900 are stress checks.
- Every production behavior change follows red-green-refactor and must be backed by a test observed failing for the intended reason.
- Baseline command `python -m pytest tests/test_design_system.py tests/test_app_shell.py tests/test_auth_phase6_ui.py tests/test_remaining_work.py -q` currently reports `41 passed`.

---

## File Structure

### New files

- `scripts/frontend_audit_server.py` — hermetic, local-only audit server with deterministic test identity; never registered by production `create_app`.
- `tests/test_frontend_audit_inventory.py` — executable coverage contract for screens, states, viewports, evidence, and reports.
- `docs/audits/frontend-audit-inventory.json` — canonical screen/state checklist.
- `docs/audits/evidence/2026-07-13/manifest.json` — viewport capture and interaction-check manifest.
- `docs/audits/2026-07-13-frontend-audit.md` — executive report, screen matrix, severity matrix, and ranked findings.
- `docs/audits/2026-07-13-design-system-audit.md` — tokens, components, duplicates, and Sprint 1 dispositions.
- `docs/audits/2026-07-13-responsive-audit.md` — desktop/tablet/mobile findings and overflow evidence.
- `docs/audits/2026-07-13-accessibility-audit.md` — contrast, focus, keyboard, semantics, touch, zoom, and reduced-motion findings.
- `static/nav.js` — shared, CSP-compatible accessible navigation-drawer controller.
- `templates/_auth_topbar.html` — one shared public/authentication header control group.
- `static/error.css` — shared 404/500 surface using canonical tokens.

### Modified files

- `tests/test_design_system.py` — canonical token, shared state, alias, dead-CSS, and error-surface contracts.
- `tests/test_app_shell.py` — navigation drawer semantics, external controller, focus management, and target-size contracts.
- `tests/test_auth_phase6_ui.py` — auth partial, language/theme state, localized password behavior, and shared form contracts.
- `static/tokens.css` — canonical input/layout/touch target tokens and dark/light mappings.
- `static/components.css` — shared interactive states, shrink safety, form states, and reusable target sizing.
- `static/theme.css` — canonical-token migration for shared app layout/feature rules.
- `static/auth.css` — canonical-token migration and auth-shell integration with shared states.
- `static/nav.css` — accessible target sizes, body scroll lock, canonical z-index, and drawer focus styling.
- `static/auth.js` — translated password controls/strength and synchronized theme state.
- `templates/_nav.html` — dialog semantics and external navigation controller.
- `templates/landing.html`, `login.html`, `register.html`, `forgot_password.html`, `reset_password.html`, `verify.html`, `setup.html` — replace repeated top bars with the shared partial only.
- `templates/404.html`, `templates/500.html` — consume `_head.html` and `error.css`; remove inline CSS.
- `locales/tr.json`, `locales/en.json` — password show/hide/strength strings with key parity.
- `docs/design-system.md` — implemented canonical contract and component inventory.
- `docs/handoff.md` — append Sprint 1 results without rewriting the current auth history.

### Removed file

- `static/style.css` — confirmed unreferenced legacy parallel stylesheet; removal is guarded by tests.

---

### Task 1: Executable screen inventory and hermetic audit server

**Files:**
- Create: `tests/test_frontend_audit_inventory.py`
- Create: `docs/audits/frontend-audit-inventory.json`
- Create: `scripts/frontend_audit_server.py`

**Interfaces:**
- Consumes: production `create_app()`, existing models, `session_store.create(user, tokens, cognito_username)`, and Flask route/template inventory.
- Produces: JSON schema `{version, acceptance_viewports, stress_viewports, surfaces[]}`; local-only `GET /__audit__/login`, `GET /__audit__/reset`, and `GET /__audit__/error`; deterministic users `axis_audit` and `audit_friend` with a valid local Cognito session and accepted friendship.

- [ ] **Step 1: Write the failing inventory tests**

Create `tests/test_frontend_audit_inventory.py`:

```python
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "docs" / "audits" / "frontend-audit-inventory.json"

REQUIRED_SURFACES = {
    "landing", "login", "register", "forgot-password", "reset-password",
    "verify", "setup", "dashboard", "ai-coach", "nutrition",
    "menu-scanner", "training", "progress", "weekly-checkin", "profile",
    "feed", "friends", "chat", "leaderboard", "quests", "pump-gallery",
    "supplements", "premium", "settings-hub", "error-404", "error-500",
}
REQUIRED_VIEWPORTS = {
    "mobile": [390, 844],
    "tablet": [768, 1024],
    "desktop": [1280, 900],
}


def load_inventory():
    return json.loads(INVENTORY.read_text(encoding="utf-8"))


def test_frontend_inventory_covers_required_surfaces_and_viewports():
    data = load_inventory()
    assert data["version"] == 1
    assert data["acceptance_viewports"] == REQUIRED_VIEWPORTS
    ids = {surface["id"] for surface in data["surfaces"]}
    assert REQUIRED_SURFACES <= ids
    assert len(ids) == len(data["surfaces"])


def test_every_rendered_html_template_is_in_inventory():
    rendered = set()
    for path in (ROOT / "app").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        rendered.update(re.findall(r'render_template\(\s*["\']([^"\']+\.html)', source))
    inventory_templates = {
        surface["template"] for surface in load_inventory()["surfaces"]
        if surface.get("template")
    }
    assert rendered <= inventory_templates


def test_audit_server_is_not_registered_in_production(app):
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    for route in ("/__audit__/login", "/__audit__/reset", "/__audit__/error"):
        assert route not in rules
    source = (ROOT / "scripts" / "frontend_audit_server.py").read_text(encoding="utf-8")
    assert 'app.route("/__audit__/login")' in source
    assert 'app.route("/__audit__/reset")' in source
    assert 'if __name__ == "__main__"' in source
```

- [ ] **Step 2: Run the tests and verify the intended failures**

Run:

```powershell
python -m pytest tests/test_frontend_audit_inventory.py -q
```

Expected: FAIL because `docs/audits/frontend-audit-inventory.json` and `scripts/frontend_audit_server.py` do not exist.

- [ ] **Step 3: Create the complete inventory**

Create `docs/audits/frontend-audit-inventory.json` with this schema and complete surface list:

```json
{
  "version": 1,
  "acceptance_viewports": {
    "mobile": [390, 844],
    "tablet": [768, 1024],
    "desktop": [1280, 900]
  },
  "stress_viewports": {
    "narrow": [320, 720],
    "large": [1440, 900]
  },
  "surfaces": [
    {"id":"landing","route":"/welcome","template":"landing.html","auth":false,"states":["default","light-theme","en"]},
    {"id":"login","route":"/login","template":"login.html","auth":false,"states":["default","error","loading","password-visible"]},
    {"id":"register","route":"/register","template":"register.html","auth":false,"states":["default","validation-error","password-strength","loading"]},
    {"id":"forgot-password","route":"/forgot-password","template":"forgot_password.html","auth":false,"states":["default","error","success","loading"]},
    {"id":"reset-password","route":"/__audit__/reset","template":"reset_password.html","auth":false,"audit_only":true,"states":["default","mismatch","password-strength","loading"]},
    {"id":"verify","route":"/verify?u=axis_audit","template":"verify.html","auth":false,"states":["default","error","resend-loading"]},
    {"id":"setup","route":"/setup?yeniden=1","template":"setup.html","auth":true,"states":["step-1","step-2","step-3","step-4","validation-error"]},
    {"id":"dashboard","route":"/","template":"index.html","auth":true,"states":["default","empty-metrics","quick-add","loading"]},
    {"id":"ai-coach","route":"/","template":"index.html","auth":true,"embedded":"coach-widget","states":["closed","open","loading","error"]},
    {"id":"nutrition","route":"/nutrition","template":"nutrition.html","auth":true,"states":["default","empty","meal-log","plan-loading","error"]},
    {"id":"menu-scanner","route":"/nutrition","template":"nutrition.html","auth":true,"embedded":"menu-scanner","states":["closed","open","loading","results","error"]},
    {"id":"training","route":"/training","template":"training.html","auth":true,"states":["default","preferences","plan-loading","active-plan","error"]},
    {"id":"progress","route":"/progress-page","template":"progress.html","auth":true,"states":["default","empty","charts-loading","error"]},
    {"id":"weekly-checkin","route":"/progress-page","template":"progress.html","auth":true,"embedded":"weekly-checkin","states":["closed","open","validation-error","success"]},
    {"id":"profile","route":"/edit-profile","template":"edit_profile.html","auth":true,"states":["default","edit","validation-error","success"]},
    {"id":"settings-hub","route":"/edit-profile","template":"edit_profile.html","auth":true,"embedded":"profile-hub","states":["default","language"]},
    {"id":"feed","route":"/feed","template":"feed.html","auth":true,"states":["loading","empty","populated","comments"]},
    {"id":"friends","route":"/friends","template":"friends.html","auth":true,"states":["default","invite-loading","empty","search-results","requests"]},
    {"id":"chat","route":"/chat/audit_friend","template":"chat.html","auth":true,"fixture":"audit_friend","states":["empty","messages","suggestion-modal","loading","error"]},
    {"id":"leaderboard","route":"/leaderboard","template":"leaderboard.html","auth":true,"states":["loading","empty","populated","reward"]},
    {"id":"quests","route":"/quests","template":"quests.html","auth":true,"states":["empty","active","completed","toast"]},
    {"id":"pump-gallery","route":"/pump-check-gallery","template":"pump_check_gallery.html","auth":true,"states":["empty","populated","detail-modal"]},
    {"id":"supplements","route":"/supplements","template":"manage_stack.html","auth":true,"states":["empty","form","populated","validation-error"]},
    {"id":"premium","route":"/premium","template":"premium.html","auth":true,"states":["default"]},
    {"id":"error-404","route":"/__audit__/missing","template":"404.html","auth":false,"states":["default"]},
    {"id":"error-500","route":"/__audit__/error","template":"500.html","auth":false,"audit_only":true,"states":["default"]}
  ]
}
```

- [ ] **Step 4: Create the hermetic audit server**

Create `scripts/frontend_audit_server.py`:

```python
"""Local-only deterministic browser-audit server; never imported by production."""
import os
import time

os.environ.update({
    "OPENAI_API_KEY": "audit-no-network",
    "SECRET_KEY": "axis-audit-secret",
    "FLASK_DEBUG": "1",
    "DATABASE_URL": "sqlite:///axis_frontend_audit.db",
    "FATSECRET_BASE_URL": "https://fatsecret.invalid",
    "FITX_SKIP_DB_INIT": "1",
    "REDIS_URL": "",
    "BEDROCK_ENABLED": "0",
    "S3_BUCKET_NAME": "",
    "COGNITO_USER_POOL_ID": "eu-central-1_axisAudit",
    "COGNITO_APP_CLIENT_ID": "axis-audit-client",
    "COGNITO_TOKEN_ENC_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
    "RESEND_API_KEY": "",
})

from flask import redirect, session  # noqa: E402
from flask_login import login_user  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db, limiter  # noqa: E402
from app.models import Friendship, User  # noqa: E402
from app.services import cognito_jwt, session_store  # noqa: E402

app = create_app()
app.config.update(
    TESTING=True,
    PROPAGATE_EXCEPTIONS=False,
    AI_PLAN_QUOTA_ENABLED=False,
)
limiter.enabled = False


def _claims(_token, _expected_use):
    return {"sub": "sub-axis-audit"}


cognito_jwt.validate_token = _claims

with app.app_context():
    db.create_all()
    user = User.query.filter_by(username="axis_audit").first()
    if user is None:
        user = User(
            username="axis_audit",
            email="axis_audit@example.com",
            cognito_sub="sub-axis-audit",
            profile_complete=True,
            weight=72.0,
            height=178.0,
            age=30,
        )
        db.session.add(user)
        db.session.commit()
    friend = User.query.filter_by(username="audit_friend").first()
    if friend is None:
        friend = User(
            username="audit_friend",
            email="audit_friend@example.com",
            cognito_sub="sub-audit-friend",
            profile_complete=True,
            weight=80.0,
            height=183.0,
            age=31,
        )
        db.session.add(friend)
        db.session.commit()
    friendship = Friendship.query.filter_by(
        sender_id=user.id,
        receiver_id=friend.id,
    ).first()
    if friendship is None:
        db.session.add(Friendship(
            sender_id=user.id,
            receiver_id=friend.id,
            status="accepted",
        ))
        db.session.commit()


@app.route("/__audit__/login")
def audit_login():
    user = User.query.filter_by(username="axis_audit").one()
    login_user(user)
    sid = session_store.create(user, {
        "access_token": "audit-access",
        "refresh_token": "audit-refresh",
        "expires_in": 86400,
    }, user.username)
    session["cognito_sid"] = sid
    return redirect("/")


@app.route("/__audit__/reset")
def audit_reset():
    session["password_reset_username"] = "axis_audit"
    session["password_reset_started_at"] = time.time()
    return redirect("/reset-password")


@app.route("/__audit__/error")
def audit_error():
    raise RuntimeError("intentional local audit error")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5055, debug=False, use_reloader=False)
```

- [ ] **Step 5: Run the inventory tests and commit**

Run:

```powershell
python -m pytest tests/test_frontend_audit_inventory.py -q
```

Expected: PASS.

Commit:

```powershell
git add tests/test_frontend_audit_inventory.py scripts/frontend_audit_server.py docs/audits/frontend-audit-inventory.json
git commit -m "test: inventory frontend surfaces"
```

---

### Task 2: Complete responsive, state, and accessibility evidence baseline

**Files:**
- Create: `docs/audits/evidence/2026-07-13/manifest.json`
- Create: `docs/audits/evidence/2026-07-13/*.png`
- Create: `docs/audits/2026-07-13-frontend-audit.md`
- Create: `docs/audits/2026-07-13-design-system-audit.md`
- Create: `docs/audits/2026-07-13-responsive-audit.md`
- Create: `docs/audits/2026-07-13-accessibility-audit.md`
- Modify: `tests/test_frontend_audit_inventory.py`

**Interfaces:**
- Consumes: Task 1 inventory and audit server.
- Produces: manifest keyed by `surface_id` and viewport; four reports using issue IDs `UI-*`, `DS-*`, `RESP-*`, and `A11Y-*`.

- [ ] **Step 1: Add the failing evidence/report completeness test**

Append to `tests/test_frontend_audit_inventory.py`:

```python
EVIDENCE = ROOT / "docs" / "audits" / "evidence" / "2026-07-13" / "manifest.json"
REPORTS = (
    "2026-07-13-frontend-audit.md",
    "2026-07-13-design-system-audit.md",
    "2026-07-13-responsive-audit.md",
    "2026-07-13-accessibility-audit.md",
)


def test_every_surface_has_acceptance_viewport_evidence_and_reports():
    manifest = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    inventory = load_inventory()
    by_id = manifest["surfaces"]
    for surface in inventory["surfaces"]:
        assert surface["id"] in by_id
        state_checks = by_id[surface["id"]]["states"]
        for state in surface["states"]:
            assert state in state_checks
            assert state_checks[state]["status"] in {"verified", "blocked-documented"}
            if state_checks[state]["status"] == "blocked-documented":
                assert state_checks[state]["reason"]
                assert state_checks[state]["recommended_manual_check"]
        for viewport, dimensions in REQUIRED_VIEWPORTS.items():
            capture = by_id[surface["id"]]["captures"][viewport]
            assert capture["dimensions"] == dimensions
            assert capture["status"] in {"verified", "blocked-documented"}
            if capture["status"] == "verified":
                assert (ROOT / capture["path"]).is_file()
    for report in REPORTS:
        text = (ROOT / "docs" / "audits" / report).read_text(encoding="utf-8")
        assert "## Executive Summary" in text
        assert "## Verification" in text
```

- [ ] **Step 2: Verify the completeness test fails**

Run:

```powershell
python -m pytest tests/test_frontend_audit_inventory.py::test_every_surface_has_acceptance_viewport_evidence_and_reports -q
```

Expected: FAIL because the manifest, screenshots, and reports do not exist.

- [ ] **Step 3: Start the audit server**

Run in a dedicated terminal:

```powershell
python scripts/frontend_audit_server.py
```

Expected: Flask listens on `http://127.0.0.1:5055`. Open `/__audit__/login` once before authenticated captures.

- [ ] **Step 4: Capture every acceptance viewport and both stress widths**

For every inventory item:

1. Navigate to its route and reach every listed state that is safe and deterministic.
2. Capture full-page PNGs at 390 × 844, 768 × 1024, and 1280 × 900.
3. Run overflow measurements in the page:

```javascript
({
  viewport: document.documentElement.clientWidth,
  scrollWidth: document.documentElement.scrollWidth,
  overflowing: [...document.querySelectorAll('body *')]
    .filter((el) => {
      const r = el.getBoundingClientRect();
      return r.right > document.documentElement.clientWidth + 1 || r.left < -1;
    })
    .map((el) => ({tag: el.tagName, id: el.id, className: el.className}))
})
```

4. At 320 × 720 and 1440 × 900, record overflow and layout observations; save a screenshot only when a defect differs from the acceptance captures.
5. Record keyboard traversal, focus visibility, 200% zoom, reduced motion, theme, and EN copy checks in the manifest under `checks`.
6. Record every inventory state under the surface's `states` map with `status`, `evidence`, and `notes`.
7. Use `blocked-documented` only when an external-service state cannot be reproduced; include `reason`, `static_evidence`, and `recommended_manual_check`.

Use filenames `docs/audits/evidence/2026-07-13/<surface-id>-<viewport>.png`.

- [ ] **Step 5: Write the four evidence-backed reports**

Use this issue record for every finding; do not include empty headings or placeholder text:

```markdown
### A11Y-01 — Shared compact controls miss the 44px target

- Severity: High
- Locations: auth language/theme controls; header menu/avatar; drawer close
- Evidence: `<capture paths and measured bounds>`
- Description: Shared compact controls render below the minimum mobile target.
- Impact: Increases missed taps and creates a repeated motor-accessibility barrier.
- Root cause: Component dimensions predate a shared interactive-size token.
- Recommended solution: Add a canonical 44px target token and apply it to shared controls without enlarging decorative glyphs.
- Sprint 1 disposition: Fix in shared tokens/components/nav/auth layers.
```

At minimum, verify and disposition these already-confirmed static patterns in the reports:

- `DS-01`: shared `auth.css`/`theme.css` still consume legacy aliases;
- `DS-02`: auth buttons, fields, cards, and alerts duplicate component-library states;
- `DS-03`: unreferenced `static/style.css` preserves a parallel legacy system;
- `A11Y-01`: 28–40px shared language/theme/menu/avatar/close targets;
- `A11Y-02`: nav drawer lacks focus trap, initial focus, and focus return;
- `A11Y-03`: password toggle accessible name describes the field, not show/hide action;
- `UI-01`: auth topbar markup is repeated across seven templates and state semantics drift;
- `UI-02`: password strength has a hardcoded English success string;
- `UI-03`: 404/500 bypass `_head.html`, canonical fonts/tokens, responsive viewport, and shared focus states;
- `RESP-01`: global `overflow-x: clip` masks root causes unless child bounds are measured;
- `RESP-02`: shared grid/flex shells lack an explicit shrink-safety contract;
- `A11Y-04`: page-level field error association remains a later page-sprint concern unless a shared safe fix is found;
- `DS-04`: page-specific inline style attributes and legacy aliases remain documented debt, not blanket Sprint 1 rewrites.

- [ ] **Step 6: Run the completeness test and commit audit evidence**

Run:

```powershell
python -m pytest tests/test_frontend_audit_inventory.py -q
```

Expected: PASS.

Commit:

```powershell
git add tests/test_frontend_audit_inventory.py docs/audits
git commit -m "docs: audit frontend surfaces"
```

---

### Task 3: Canonical shared tokens and retire the dead stylesheet

**Files:**
- Modify: `tests/test_design_system.py`
- Modify: `static/tokens.css`
- Modify: `static/auth.css`
- Modify: `static/theme.css`
- Delete: `static/style.css`

**Interfaces:**
- Consumes: Task 2 findings `DS-01` and `DS-03`.
- Produces: `--color-input-bg`, `--control-target-min`, `--layout-gutter`, `--layout-gutter-wide`, `--content-reading-max`, `--z-navigation-modal`; shared CSS with no legacy aliases; one canonical stylesheet system.

- [ ] **Step 1: Add failing canonical-token and legacy-use tests**

Append to `tests/test_design_system.py`:

```python
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED_CSS = ("auth.css", "theme.css", "components.css", "nav.css")
LEGACY_USE = re.compile(
    r"var\(--(?:accent(?:2|-glow)?|bg(?:2|3)?|card|input-bg|text2|text3|"
    r"surface(?:-[23])?|border(?:-2)?|blue(?:-dim|-light)?|red(?:-dim)?|green(?:-dim)?|"
    r"orange(?:-dim)?|s(?:1|2|3|4|5|6|8|10)|r-(?:sm|md|lg|xl|full)|"
    r"shadow-(?:sm|md|lg|volt|blue)|t-(?:fast|base|spring))\)"
)


def test_shared_css_uses_only_canonical_tokens():
    for name in SHARED_CSS:
        css = (ROOT / "static" / name).read_text(encoding="utf-8")
        assert not LEGACY_USE.search(css), name


def test_foundation_tokens_cover_input_layout_and_touch():
    css = (ROOT / "static" / "tokens.css").read_text(encoding="utf-8")
    for token in (
        "--color-input-bg:", "--control-target-min:", "--layout-gutter:",
        "--layout-gutter-wide:", "--content-reading-max:",
        "--z-navigation-modal:",
    ):
        assert token in css
    assert css.count("--color-input-bg:") == 2


def test_legacy_parallel_style_css_is_retired():
    assert not (ROOT / "static" / "style.css").exists()
    for template in (ROOT / "templates").glob("*.html"):
        assert "/static/style.css" not in template.read_text(encoding="utf-8")
```

- [ ] **Step 2: Verify the tests fail for the expected legacy uses and missing tokens**

Run:

```powershell
python -m pytest tests/test_design_system.py -q
```

Expected: FAIL on missing foundation tokens, legacy aliases in `auth.css`/`theme.css`, and existing `static/style.css`.

- [ ] **Step 3: Add canonical foundation tokens**

Add to the appropriate `:root` sections in `static/tokens.css`:

```css
--color-input-bg: var(--color-surface-1);
--control-target-min: 44px;
--layout-gutter: var(--space-4);
--layout-gutter-wide: var(--space-6);
--content-reading-max: 720px;
--z-navigation-modal: 10000;
```

Add to `[data-theme="light"]`:

```css
--color-input-bg: var(--color-surface-2);
```

Keep the old alias definitions in `tokens.css` for page stylesheets that still consume them; Sprint 1 prohibits their use in the four shared CSS files but does not break later page consumers.

- [ ] **Step 4: Mechanically migrate shared CSS to canonical names**

Apply this exact mapping only in `static/auth.css` and `static/theme.css`:

| Legacy | Canonical |
|---|---|
| `--accent` | `--color-primary` |
| `--accent2` | `--color-primary-strong` |
| `--accent-glow` | `--color-primary-soft` |
| `--bg` | `--color-bg` |
| `--bg2` / `--surface` | `--color-surface-1` |
| `--bg3` / `--surface-3` | `--color-surface-3` |
| `--surface-2` / `--card` | `--color-surface-2` |
| `--input-bg` | `--color-input-bg` |
| `--text` / `--text2` / `--text3` | `--color-text-1` / `--color-text-2` / `--color-text-3` |
| `--border` / `--border-2` | `--color-border-1` / `--color-border-2` except auth solid borders use `--color-border-solid` |
| `--red` / `--green` / `--orange` | `--color-danger` / `--color-success` / `--color-warning` |
| `--r-*` | `--radius-*` |
| `--shadow-sm/md/lg` | `--elevation-1/2/3` |
| `--t-fast/base/spring` | explicit duration plus easing tokens |

Do not change computed values, selectors, or page composition.

- [ ] **Step 5: Delete `static/style.css` and verify no references**

Delete the file with `apply_patch`. Then run:

```powershell
rg -n "/static/style.css" templates tests docs
```

Expected: no production template references; historical documentation references may be updated to say “retired in Sprint 1”.

- [ ] **Step 6: Run tests and commit**

Run:

```powershell
python -m pytest tests/test_design_system.py tests/test_auth_phase6_ui.py tests/test_app_shell.py -q
```

Expected: PASS.

Commit:

```powershell
git add tests/test_design_system.py static/tokens.css static/auth.css static/theme.css static/style.css
git commit -m "refactor: canonicalize shared styles"
```

---

### Task 4: Shared component states, shrink safety, and touch targets

**Files:**
- Modify: `tests/test_design_system.py`
- Modify: `static/components.css`
- Modify: `static/auth.css`
- Modify: `static/theme.css`

**Interfaces:**
- Consumes: canonical tokens from Task 3.
- Produces: shared disabled/loading/pressed/focus contract, minimum target sizing, and explicit shrink safety for shared shells/components.

- [ ] **Step 1: Add failing shared-state tests**

Append to `tests/test_design_system.py`:

```python
def test_shared_components_define_complete_interaction_states():
    css = (ROOT / "static" / "components.css").read_text(encoding="utf-8")
    for contract in (
        ":disabled", ".is-loading",
        ":active", ":focus-visible", "min-width: 0", "--control-target-min",
    ):
        assert contract in css


def test_shared_layout_css_avoids_viewport_width_overflow_patterns():
    for name in SHARED_CSS:
        css = (ROOT / "static" / name).read_text(encoding="utf-8")
        assert "100vw" not in css, name
```

- [ ] **Step 2: Verify red**

Run:

```powershell
python -m pytest tests/test_design_system.py::test_shared_components_define_complete_interaction_states -q
```

Expected: FAIL because the combined component contract is incomplete.

- [ ] **Step 3: Add the shared interaction and shrink-safety layer**

Add near the start of `static/components.css`, after global overflow safety:

```css
:where(
  .btn-volt, .btn-ghost, .btn-danger,
  .auth-btn, .setup-btn, .landing-cta,
  .lang-opt, .theme-toggle, .password-toggle,
  .header-menu-btn, .header-avatar, .nav-drawer-close
) {
  min-height: var(--control-target-min);
}

:where(
  .btn-volt, .btn-ghost, .btn-danger,
  .auth-btn, .setup-btn, .landing-cta
):active:not(:disabled) {
  transform: scale(0.98);
}

:where(
  .btn-volt, .btn-ghost, .btn-danger,
  .auth-btn, .setup-btn
):disabled,
:where(
  .btn-volt, .btn-ghost, .btn-danger,
  .auth-btn, .setup-btn
).is-loading {
  opacity: var(--opacity-disabled);
  cursor: not-allowed;
  box-shadow: none;
  transform: none;
}

:where(
  .main-content, .auth-main, .auth-card, .landing-hero,
  .landing-grid, .setup-main, .setup-card, .modal, .sheet
),
:where(
  .main-content, .auth-main, .landing-hero,
  .landing-grid, .setup-grid, .result-grid
) > * {
  min-width: 0;
}
```

Do not globally assign `min-height: 44px` to every button or link; that would alter dense page-specific controls and belongs to later owning-page migrations.

- [ ] **Step 4: Replace duplicated shared disabled/loading declarations**

Remove only declarations in `auth.css` and `theme.css` that are now byte-for-byte covered by the component layer. Keep page-specific color, typography, and geometry declarations.

- [ ] **Step 5: Run focused tests and commit**

Run:

```powershell
python -m pytest tests/test_design_system.py tests/test_auth_phase6_ui.py tests/test_app_shell.py -q
```

Expected: PASS.

Commit:

```powershell
git add tests/test_design_system.py static/components.css static/auth.css static/theme.css
git commit -m "feat: standardize shared component states"
```

---

### Task 5: Accessible shared navigation drawer

**Files:**
- Modify: `tests/test_app_shell.py`
- Create: `static/nav.js`
- Modify: `templates/_nav.html`
- Modify: `static/nav.css`

**Interfaces:**
- Consumes: `--control-target-min`, `--z-navigation-modal`, and shared focus styles.
- Produces: `window.AxisNav` with `open()` and `close()`; dialog semantics; initial focus, Tab trap, Escape close, breakpoint close, body scroll lock, and focus return.

- [ ] **Step 1: Replace stale drawer-removal assertions with failing accessible-drawer contracts**

Replace `test_drawer_and_nav_js_are_gone` in `tests/test_app_shell.py` with:

```python
def test_shared_nav_drawer_uses_external_accessible_controller():
    nav = (ROOT / "templates" / "_nav.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "nav.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "nav.css").read_text(encoding="utf-8")

    assert 'role="dialog"' in nav
    assert 'aria-modal="true"' in nav
    assert 'aria-labelledby="nav-drawer-title"' in nav
    assert 'data-nav-initial-focus' in nav
    assert 'class="nav-drawer-backdrop" data-nav-close tabindex="-1"' in nav
    assert '<script nonce=' not in nav
    assert '/static/nav.js?v={{ _v }}' in nav
    for contract in (
        "focusableElements", "event.key === \"Escape\"",
        "event.key !== \"Tab\"", "previousFocus.focus()",
        "matchMedia", "nav-open",
    ):
        assert contract in js
    assert "--control-target-min" in css
```

- [ ] **Step 2: Run and verify red**

Run:

```powershell
python -m pytest tests/test_app_shell.py::test_shared_nav_drawer_uses_external_accessible_controller -q
```

Expected: FAIL because `static/nav.js` does not exist and the partial uses an inline script without dialog semantics.

- [ ] **Step 3: Update drawer semantics in `_nav.html`**

Change the drawer opening element and title to:

```jinja
<div class="nav-drawer" id="nav-drawer" role="dialog" aria-modal="true"
     aria-labelledby="nav-drawer-title" hidden>
    <button type="button" class="nav-drawer-backdrop" data-nav-close tabindex="-1"
            aria-label="{{ t('feed.comments_close') }}"></button>
    <nav class="nav-drawer-panel" aria-label="{{ t('nav.menu_nav') }}">
        <div class="nav-drawer-head">
            <span class="nav-drawer-title" id="nav-drawer-title">{{ t('nav.menu_nav') }}</span>
            <button type="button" class="nav-drawer-close" data-nav-close
                    data-nav-initial-focus
                    aria-label="{{ t('feed.comments_close') }}">&times;</button>
        </div>
        {# existing links stay byte-for-byte unchanged #}
    </nav>
</div>
<script src="/static/nav.js?v={{ _v }}"></script>
```

Delete the inline nonce script from the partial.

- [ ] **Step 4: Create `static/nav.js`**

```javascript
(function () {
  "use strict";

  var button = document.getElementById("header-menu-btn");
  var drawer = document.getElementById("nav-drawer");
  var previousFocus = null;
  if (!button || !drawer) return;

  function focusableElements() {
    return Array.prototype.slice.call(drawer.querySelectorAll(
      'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )).filter(function (element) {
      return !element.hidden && element.getAttribute("aria-hidden") !== "true";
    });
  }

  function open() {
    previousFocus = document.activeElement;
    drawer.hidden = false;
    button.setAttribute("aria-expanded", "true");
    document.body.classList.add("nav-open");
    var initialFocus = drawer.querySelector("[data-nav-initial-focus]");
    if (initialFocus) initialFocus.focus();
  }

  function close(options) {
    if (drawer.hidden) return;
    drawer.hidden = true;
    button.setAttribute("aria-expanded", "false");
    document.body.classList.remove("nav-open");
    if ((!options || options.restoreFocus !== false) && previousFocus &&
        document.contains(previousFocus)) {
      previousFocus.focus();
    } else if (drawer.contains(document.activeElement)) {
      var fallback = document.querySelector(".header-brand");
      if (fallback) fallback.focus();
    }
  }

  function trapFocus(event) {
    if (drawer.hidden || event.key !== "Tab") return;
    var items = focusableElements();
    if (!items.length) return;
    var first = items[0];
    var last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  button.addEventListener("click", open);
  drawer.querySelectorAll("[data-nav-close]").forEach(function (element) {
    element.addEventListener("click", function () { close(); });
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !drawer.hidden) close();
    trapFocus(event);
  });
  window.matchMedia("(min-width: 1024px)").addEventListener("change", function (event) {
    if (event.matches) close({restoreFocus: false});
  });

  window.AxisNav = {open: open, close: close};
})();
```

- [ ] **Step 5: Update nav target sizing and scroll lock**

In `static/nav.css`, replace the existing single-line `.nav-drawer` rule with the expanded canonical-token rule below and retain the existing `.nav-drawer[hidden]` and desktop media-query rules:

```css
body.nav-open { overflow: hidden; }

.nav-drawer {
  position: fixed;
  inset: 0;
  z-index: var(--z-navigation-modal);
  display: flex;
}

.header-menu-btn,
.nav-drawer-close {
  width: var(--control-target-min);
  height: var(--control-target-min);
}

.header-avatar {
  width: var(--control-target-min);
  height: var(--control-target-min);
}
```

Use the canonical `--z-navigation-modal` token because the drawer must remain above the coach widget while preserving the existing stacking order.

- [ ] **Step 6: Run tests, browser-check keyboard behavior, and commit**

Run:

```powershell
python -m pytest tests/test_app_shell.py tests/test_design_system.py -q
```

Expected: PASS.

At 390px verify: open focuses first drawer control; Tab wraps; Shift+Tab wraps; Escape closes; focus returns to the menu button; body scroll is restored.

Commit:

```powershell
git add tests/test_app_shell.py static/nav.js static/nav.css templates/_nav.html
git commit -m "feat: make navigation drawer accessible"
```

---

### Task 6: Consolidated auth topbar and localized shared form behavior

**Files:**
- Modify: `tests/test_auth_phase6_ui.py`
- Create: `templates/_auth_topbar.html`
- Modify: `templates/landing.html`
- Modify: `templates/login.html`
- Modify: `templates/register.html`
- Modify: `templates/forgot_password.html`
- Modify: `templates/reset_password.html`
- Modify: `templates/verify.html`
- Modify: `templates/setup.html`
- Modify: `static/auth.css`
- Modify: `static/auth.js`
- Modify: `locales/tr.json`
- Modify: `locales/en.json`

**Interfaces:**
- Consumes: shared target token/component states from Tasks 3–4.
- Produces: `_auth_topbar.html` accepting `auth_topbar_landing` and `auth_topbar_show_login`; short visible translation keys `form.show` and `form.hide`; accessible action keys `form.show_password` and `form.hide_password`; and `form.password_strong`.

- [ ] **Step 1: Add failing auth consolidation and localization tests**

Append to `tests/test_auth_phase6_ui.py`:

```python
def test_public_auth_pages_use_one_topbar_partial():
    pages = (
        "landing.html", "login.html", "register.html", "forgot_password.html",
        "reset_password.html", "verify.html", "setup.html",
    )
    for name in pages:
        source = Path("templates", name).read_text(encoding="utf-8")
        assert '{% include "_auth_topbar.html" %}' in source, name
        assert source.count('class="auth-controls"') == 0, name


def test_auth_topbar_exposes_language_and_theme_state(client):
    html = _html(client, "/register")
    assert 'aria-pressed="true"' in html
    assert 'class="theme-toggle"' in html
    assert 'aria-pressed="false"' in html


def test_password_controls_are_localized_and_action_named(client):
    js = client.get("/static/auth.js").get_data(as_text=True)
    assert 'tr("form.show_password")' in js
    assert 'tr("form.hide_password")' in js
    assert 'tr("form.password_strong")' in js
    assert 'tr(showing ? "form.hide" : "form.show")' in js
    assert '"Strong password"' not in js
```

- [ ] **Step 2: Run and verify red**

Run:

```powershell
python -m pytest tests/test_auth_phase6_ui.py -q
```

Expected: FAIL because the shared partial and translation-based JS behavior do not exist.

- [ ] **Step 3: Create `_auth_topbar.html`**

```jinja
{% set _landing = auth_topbar_landing|default(false) %}
<header class="{{ 'landing-topbar' if _landing else 'auth-topbar' }}">
    <a class="{{ 'landing-brand' if _landing else 'auth-brand' }}"
       href="/welcome" aria-label="AxisAI">
        <img src="/static/icon-holo.png" alt="">AxisAI
    </a>
    <div class="auth-controls">
        {% if auth_topbar_show_login|default(false) %}
        <a class="auth-link" href="/login">{{ t('landing.nav_login') }}</a>
        {% endif %}
        <div class="lang-switch" role="group" aria-label="{{ t('lang.aria') }}">
            <button type="button" class="lang-opt{% if locale == 'tr' %} on{% endif %}"
                    aria-pressed="{{ 'true' if locale == 'tr' else 'false' }}"
                    data-action="setLang" data-args='["tr"]'>TR</button>
            <button type="button" class="lang-opt{% if locale == 'en' %} on{% endif %}"
                    aria-pressed="{{ 'true' if locale == 'en' else 'false' }}"
                    data-action="setLang" data-args='["en"]'>EN</button>
        </div>
        <button type="button" class="theme-toggle" data-action="toggleTheme"
                aria-label="{{ t('theme.aria') }}" aria-pressed="false"></button>
    </div>
</header>
```

In `landing.html`, include it with:

```jinja
{% set auth_topbar_landing = true %}
{% set auth_topbar_show_login = true %}
{% include "_auth_topbar.html" %}
```

In the other six templates use only:

```jinja
{% include "_auth_topbar.html" %}
```

- [ ] **Step 4: Add localized password strings with catalog parity**

Add to `locales/tr.json`:

```json
"form.show": "Göster",
"form.hide": "Gizle",
"form.show_password": "Şifreyi göster",
"form.hide_password": "Şifreyi gizle",
"form.password_strong": "Güçlü şifre"
```

Add to `locales/en.json`:

```json
"form.show": "Show",
"form.hide": "Hide",
"form.show_password": "Show password",
"form.hide_password": "Hide password",
"form.password_strong": "Strong password"
```

- [ ] **Step 5: Synchronize theme and password control semantics in `auth.js`**

Replace the theme setter and password toggle behavior with:

```javascript
function setTheme(theme) {
  var selected = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = selected;
  localStorage.setItem("theme", selected);
  document.querySelectorAll(".theme-toggle").forEach(function (toggle) {
    toggle.setAttribute("aria-pressed", selected === "light" ? "true" : "false");
  });
}

function updatePasswordToggle(button, showing) {
  var key = showing ? "form.hide_password" : "form.show_password";
  button.textContent = tr(showing ? "form.hide" : "form.show");
  button.setAttribute("aria-label", tr(key));
  button.setAttribute("aria-pressed", showing ? "true" : "false");
}

window.togglePassword = function (el) {
  var target = qs(el.getAttribute("data-target"));
  if (!target) return;
  var show = target.type === "password";
  target.type = show ? "text" : "password";
  updatePasswordToggle(el, show);
};
```

Use `tr("form.password_strong")` in the strength function. During `DOMContentLoaded`, call `initTheme()` and initialize each `.password-toggle` with `updatePasswordToggle(button, false)`.

- [ ] **Step 6: Enforce shared target dimensions without changing the visual glyph size**

In `static/auth.css`, keep the switch thumb and visible track sizes, but expand hit areas:

```css
.lang-switch { min-height: var(--control-target-min); }
.lang-opt { min-width: var(--control-target-min); min-height: var(--control-target-min); }
.theme-toggle {
  min-width: 46px;
  min-height: var(--control-target-min);
  border: 0;
  background: transparent;
}
.theme-toggle::before {
  content: "";
  position: absolute;
  inset: 8px 0;
  border: 1px solid var(--color-border-solid);
  border-radius: var(--radius-md);
  background: var(--color-input-bg);
}
.theme-toggle::after { top: 12px; }
.password-toggle { min-width: 64px; min-height: var(--control-target-min); }
.auth-input-wrap input { padding-right: 80px; }
```

Keep the existing thumb transform at `18px` so the 46 × 28 visual track remains inside the 46 × 44 hit area. Do not change brand colors or card composition.

- [ ] **Step 7: Run focused tests and commit**

Run:

```powershell
python -m pytest tests/test_auth_phase6_ui.py tests/test_i18n.py tests/test_design_system.py -q
```

Expected: PASS.

Commit:

```powershell
git add tests/test_auth_phase6_ui.py templates/_auth_topbar.html templates/landing.html templates/login.html templates/register.html templates/forgot_password.html templates/reset_password.html templates/verify.html templates/setup.html static/auth.css static/auth.js locales/tr.json locales/en.json
git commit -m "feat: unify auth controls"
```

---

### Task 7: Canonical responsive error surfaces

**Files:**
- Modify: `tests/test_design_system.py`
- Create: `static/error.css`
- Modify: `templates/404.html`
- Modify: `templates/500.html`

**Interfaces:**
- Consumes: `_head.html`, canonical tokens, shared focus states.
- Produces: `.error-body`, `.error-card`, `.error-code`, `.error-message`, `.error-link` shared by 404 and 500.

- [ ] **Step 1: Add failing error-surface tests**

Append to `tests/test_design_system.py`:

```python
def test_error_pages_use_shared_head_and_canonical_styles():
    for name in ("404.html", "500.html"):
        source = (ROOT / "templates" / name).read_text(encoding="utf-8")
        assert '{% include "_head.html" %}' in source
        assert "/static/error.css" in source
        assert "<style" not in source
        assert 'class="error-body"' in source
    css = (ROOT / "static" / "error.css").read_text(encoding="utf-8")
    assert "var(--color-primary)" in css
    assert "clamp(" in css
    assert "100vw" not in css
```

- [ ] **Step 2: Verify red**

Run:

```powershell
python -m pytest tests/test_design_system.py::test_error_pages_use_shared_head_and_canonical_styles -q
```

Expected: FAIL because both templates contain inline raw-color CSS and do not include `_head.html`.

- [ ] **Step 3: Create `static/error.css`**

```css
.error-body {
  min-height: 100dvh;
  margin: 0;
  display: grid;
  place-items: center;
  padding: var(--layout-gutter);
  background: var(--color-bg);
  color: var(--color-text-1);
  font-family: var(--font-body);
  text-align: center;
}

.error-card {
  width: min(100%, var(--content-reading-max));
  min-width: 0;
}

.error-code {
  margin: 0;
  color: var(--color-primary);
  font-family: var(--font-display);
  font-size: clamp(72px, 24vw, 120px);
  line-height: var(--leading-none);
  letter-spacing: var(--tracking-wider);
}

.error-message {
  margin: var(--space-3) 0 var(--space-6);
  color: var(--color-text-2);
  font-size: var(--text-xl);
  line-height: var(--leading-normal);
}

.error-link {
  display: inline-flex;
  min-height: var(--control-target-min);
  align-items: center;
  color: var(--color-primary);
  font-weight: var(--weight-semibold);
  text-decoration-thickness: var(--border-w-1);
  text-underline-offset: var(--space-1);
}
```

- [ ] **Step 4: Replace both error templates with the shared structure**

Use this exact structure, substituting `404`/`500` and the existing translation keys:

```jinja
<!DOCTYPE html>
<html lang="{{ locale }}" data-theme="dark">
<head>
    {% include "_head.html" %}
    <title>{{ t('error.404_title') }} - AxisAI</title>
    <link rel="stylesheet" href="/static/error.css?v={{ _v }}">
</head>
<body class="error-body">
    <main class="error-card">
        <h1 class="error-code">404</h1>
        <p class="error-message">{{ t('error.404_body') }}</p>
        <a class="error-link" href="/">{{ t('error.back_home') }}</a>
    </main>
</body>
</html>
```

- [ ] **Step 5: Run tests, recapture both pages, and commit**

Run:

```powershell
python -m pytest tests/test_design_system.py tests/test_hooks.py -q
```

Expected: PASS.

Commit:

```powershell
git add tests/test_design_system.py static/error.css templates/404.html templates/500.html
git commit -m "feat: standardize error surfaces"
```

---

### Task 8: Final evidence, design-system documentation, and regression gate

**Files:**
- Modify: `docs/audits/evidence/2026-07-13/manifest.json`
- Modify: all four `docs/audits/2026-07-13-*.md` reports
- Modify: `docs/design-system.md`
- Modify: `docs/handoff.md`

**Interfaces:**
- Consumes: all prior tasks and before captures.
- Produces: before/after dispositions, final severity counts, updated component/token contract, exact verification record, and Sprint 2 deferral list.

- [ ] **Step 1: Re-capture every surface affected by a shared change**

At 390 × 844, 768 × 1024, and 1280 × 900 recapture:

- all seven public/auth/setup surfaces;
- one representative core screen for global component/layout impact (`dashboard`);
- one dense core screen (`nutrition` or `training`);
- one secondary/social screen with the shared drawer (`friends`);
- `error-404` and `error-500`.

Re-run overflow measurements. If any shared change alters another audited surface, recapture that surface too. Preserve before captures by suffixing new files `-after.png` and add `after_path` to the manifest.

- [ ] **Step 2: Finalize issue dispositions and severity matrix**

Each issue in the four reports must end in exactly one state:

```markdown
- Disposition: Fixed in Sprint 1
- Verification: `<test name>` + `<before/after evidence paths>`
```

or:

```markdown
- Disposition: Deferred to Sprint 2 page owner
- Reason: Page-specific composition/copy/state; no shared root-cause change is safe.
- Verification needed: `<exact viewport/state/manual check>`
```

Do not mark automated contrast, screen-reader, or external-service checks as manually verified when they were not exercised.

- [ ] **Step 3: Update the canonical design-system document**

In `docs/design-system.md` document:

- the six new canonical foundation tokens;
- the shared interaction-state contract;
- shrink-safety and overflow policy;
- accessible nav drawer behavior and `static/nav.js`;
- `_auth_topbar.html` parameters;
- error-surface component classes;
- retirement of `static/style.css`; and
- remaining page-level legacy alias/inline-style debt explicitly assigned to later sprints.

- [ ] **Step 4: Run static guards**

Run:

```powershell
rg -n "var\(--(?:accent|bg2|bg3|text2|text3|s[0-9]|r-|t-)" static/auth.css static/theme.css static/components.css static/nav.css
```

Expected: no matches.

Run:

```powershell
rg -n "/static/style.css" templates static tests
```

Expected: no matches.

Run:

```powershell
git diff --check
```

Expected: no whitespace errors.

- [ ] **Step 5: Run focused frontend tests**

Run:

```powershell
python -m pytest tests/test_frontend_audit_inventory.py tests/test_design_system.py tests/test_app_shell.py tests/test_auth_phase6_ui.py tests/test_i18n.py tests/test_hooks.py tests/test_remaining_work.py -q
```

Expected: PASS with only pre-existing `datetime.utcnow()` warnings documented separately.

- [ ] **Step 6: Run the full regression suite**

Run with at least a 15-minute timeout:

```powershell
python -m pytest -q
```

Expected: all tests pass. Record the exact pass count, runtime, and warning count in every report’s `## Verification` section and in the new Sprint 1 handoff entry.

- [ ] **Step 7: Append the Sprint 1 handoff and commit**

Append to `docs/handoff.md` rather than replacing the authentication history. Include:

- audited surface count and capture matrix count;
- Critical/High/Medium/Low counts;
- shared files changed;
- fixed issue IDs;
- deferred page-specific issue IDs grouped by future sprint;
- focused/full test results; and
- manual/device checks still required.

Commit:

```powershell
git add docs/audits docs/design-system.md docs/handoff.md
git commit -m "docs: complete frontend foundation audit"
```

---

## Plan Self-Review Checklist

- Spec coverage: Tasks 1–2 cover every screen, responsive matrix, reports, severity, states, localization, and accessibility evidence; Tasks 3–7 implement only confirmed shared-root fixes; Task 8 revalidates and hands off.
- Page-redesign boundary: no task changes page composition; repeated topbar/error markup changes are shared component migrations.
- Backend boundary: the only new route lives in a standalone local audit script and is asserted absent from production.
- TDD: every production CSS/JS/template behavior change has a preceding focused failing test and explicit red/green commands.
- Dirty worktree: commits list only Sprint 1 files; execution must re-run `git status --short` before each commit and omit unrelated files.
- Naming consistency: `--control-target-min`, `--color-input-bg`, `--layout-gutter`, `--layout-gutter-wide`, `--content-reading-max`, `--z-navigation-modal`, `form.show`, `form.hide`, `form.show_password`, `form.hide_password`, `window.AxisNav`, and `_auth_topbar.html` are used consistently across all tasks.
- No placeholders: discovery outputs use an explicit issue schema and disposition rules; no incomplete sections or unspecified implementation steps remain.
