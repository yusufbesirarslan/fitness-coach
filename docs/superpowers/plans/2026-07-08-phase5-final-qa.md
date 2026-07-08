# Phase 5 · Surface 4 — Global Polish + Final QA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring all 13 redesigned core surfaces onto the canonical AxisAI design system *exclusively* — retire the legacy `--volt*` aliases, tokenize residual raw colors, extract every remaining inline `<style>` block, and fix the High/Med visual/interaction inconsistencies — frontend-only, backend untouched.

**Architecture:** Hybrid. Stage 0 audits (static greps + a Playwright screenshot harness driving all 13 routes) into a ranked report. Stage 1 does the cross-cutting global refactor (token migration) once, up front. Stage 2 polishes surface-by-surface (extract inline CSS + apply that surface's High/Med findings). Stage 3 writes the report + handoff. Each task ends with `pytest` green + a browser spot-check; one branch → one PR.

**Tech Stack:** Flask/Jinja2 templates, canonical CSS design system (`static/tokens.css` + `static/components.css` + `static/nav.css`), per-page `static/*.css`, Playwright (Node, `channel:'chrome'`) for screenshots, pytest (`tests/test_design_system.py`, `tests/test_i18n.py`).

## Global Constraints

- **No new features. No backend/business-logic/route/model/schema/migration changes.** Frontend files only (`templates/*.html`, `static/*.css`, and — only if a page JS bug is found — `static/*.js`).
- **Preserve functionality** — every interaction behaves identically; selectors relocated during extraction stay byte-identical.
- **Single source of truth by the end:** `grep -rE "var\(\s*--volt" static/ templates/` returns **0**; the `--volt*` alias block is deleted from `tokens.css`; no raw hex/`rgba()`/`hsl()` in in-scope surface CSS **except** a genuinely new shared token defined in `tokens.css` and referenced elsewhere.
- **CSP:** page CSS via `<link>`; any remaining inline `<style>` keeps `nonce="{{ csp_nonce }}"`; never inject `<style>` from JS (see the `csp-no-js-injected-style` constraint).
- **Severity policy:** fix **High + Med** findings; log **Low** as "remaining tech debt" in the Stage-3 report.
- **Commits:** stage only the files named in each task's commit step — **never `git add -A`** (repo root has untracked scratch: `.superpowers/sdd/*`, `AGENTS.md`).
- **Branch:** all work on `feat/phase5-final-qa` (already created off `main` @ `4b7a6c5`).
- **In-scope surfaces (13):** `index` (dashboard, `/`), `nutrition` (`/nutrition`), `training` (`/training`), `progress` (`/progress-page`), `edit_profile` (`/edit-profile`), `friends` (`/friends`), `feed` (`/feed`), `leaderboard` (`/leaderboard`), `quests` (`/quests`), `manage_stack` (`/supplements`), `premium` (`/premium`), `chat` (`/chat`), `pump_check_gallery` (`/pump-check-gallery`) — plus app-wide `coach_widget.css` and shared `tokens.css`/`components.css`/`theme.css`.
- **Out of scope:** the 5 legacy auth/onboarding pages (`login`, `register`, `setup`, `verify`, `landing`).

## File Structure

- `scratchpad/qa_env.py`, `qa_seed_rich.py`, `qa_serve.py`, `qa_audit.mjs` — **Create** (session scratchpad): local-QA env, rich seed, no-reloader server, multi-route screenshot harness (Task 1). Never committed.
- `docs/superpowers/specs/2026-07-08-phase5-final-qa-audit.md` — **Create**: the ranked findings report (Task 1). Committed.
- `static/tokens.css` — **Modify**: delete `--volt*` aliases (Task 2); add new shared tokens as needed (Tasks 3–4).
- `static/coach_widget.css` — **Modify**: `--volt`→canonical + tokenize 79 raw colors (Tasks 2–3).
- `static/theme.css`, `static/components.css`, `static/nutrition.css` — **Modify**: `--volt`→canonical + tokenize residual raw colors (Tasks 2, 4).
- `templates/{chat,feed,friends,leaderboard,manage_stack,premium,quests}.html` — **Modify**: `--volt`→canonical inside inline `<style>` (Task 2), then inline-`<style>`→`static/*.css` extraction (Stage 2 tasks).
- `templates/{index,pump_check_gallery}.html` — **Modify**: inline-`<style>`→`static/*.css` extraction (Stage 2 tasks).
- `static/{friends,feed,leaderboard,quests,manage_stack,premium,chat,pump_check_gallery,index}.css` — **Create**: per-page CSS extracted from the inline blocks (Stage 2 tasks). (`index.css` only if `index.html`'s block isn't already served by `dashboard.css` — verify first.)
- `docs/handoff.md` — **Rewrite** (Task 15); Profile handoff archived.

---

### Task 1: Stage 0 — build the QA harness, run the audit, write the ranked report

**Files:**
- Create (scratchpad, uncommitted): `qa_env.py`, `qa_seed_rich.py`, `qa_serve.py`, `qa_audit.mjs`
- Create (committed): `docs/superpowers/specs/2026-07-08-phase5-final-qa-audit.md`

**Interfaces:**
- Produces: `2026-07-08-phase5-final-qa-audit.md` with, per surface, a **High/Med/Low**-ranked findings list across the 8 dimensions (spacing, typography, color-token compliance, responsive, a11y, component behavior, interaction/loading, empty states) + a global section (token/`--volt`/raw-color inventory). Baseline screenshots `qa-<surface>-{mobile,desktop}.png` in scratchpad. **Every Stage-2 task consumes its surface's section.**

- [ ] **Step 1: Write `scratchpad/qa_env.py`** (env before app import — file-SQLite, dev cookies)

```python
import os
_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qa_fq.db").replace("\\", "/")
os.environ["OPENAI_API_KEY"] = "test-key-not-used"
os.environ["SECRET_KEY"] = "local-qa-secret-key-stable"
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["FATSECRET_BASE_URL"] = "https://fatsecret.invalid"
os.environ["REDIS_URL"] = ""
os.environ["BEDROCK_ENABLED"] = "0"
os.environ["S3_BUCKET_NAME"] = ""
os.environ["COGNITO_USER_POOL_ID"] = ""
os.environ["COGNITO_APP_CLIENT_ID"] = ""
os.environ["FLASK_DEBUG"] = "1"
os.environ["FITX_SKIP_DB_INIT"] = "1"
DB_PATH = _DB
```

- [ ] **Step 2: Write `scratchpad/qa_seed_rich.py`** (one user with enough data that lists render populated; unknown/optional model fields are set defensively with `getattr`/`try` so a missing column never aborts the seed)

```python
import os, qa_env  # noqa: F401
from datetime import date
from app import create_app
from app.extensions import db
from app.models import User
app = create_app()
with app.app_context():
    if os.path.exists(qa_env.DB_PATH): os.remove(qa_env.DB_PATH)
    db.create_all()
    u = User(username="qauser", email="qauser@example.com")
    u.set_password("Sifre123")
    for k, v in {"full_name": "QA Kullanıcı", "goal": "kilo verme",
                 "target_weight": 75, "streak_count": 12, "rank_points": 350,
                 "is_premium": False}.items():
        if hasattr(u, k): setattr(u, k, v)
    db.session.add(u); db.session.commit()
    # Best-effort related rows so friends/feed/leaderboard/quests/supplements/nutrition aren't all empty.
    try:
        from app.models import Supplement
        for nm, br, cat in [("Kreatin", "MyBrand", "Performance"), ("Omega-3", "Nordic", "Health")]:
            s = Supplement(user_id=u.id)
            for k, v in {"product_name": nm, "brand": br, "category": cat, "status": "Active"}.items():
                if hasattr(s, k): setattr(s, k, v)
            db.session.add(s)
        db.session.commit()
    except Exception as e: print("supp seed skipped:", e)
    try:
        from app.models import MealLog
        m = MealLog(user_id=u.id)
        for k, v in {"tarih": date.today().isoformat(), "ogun": "Kahvaltı",
                     "yemek": "Yulaf", "kalori": 350, "protein": 12, "karbonhidrat": 55, "yag": 8}.items():
            if hasattr(m, k): setattr(m, k, v)
        db.session.add(m); db.session.commit()
    except Exception as e: print("meal seed skipped:", e)
    print("SEED OK", u.username, "id", u.id)
```

- [ ] **Step 3: Write `scratchpad/qa_serve.py`** (no-reloader server on :5001)

```python
import qa_env  # noqa: F401
from app import create_app
app = create_app()
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=False, use_reloader=False)
```

- [ ] **Step 4: Write `scratchpad/qa_audit.mjs`** (login `qauser`, drive all 13 routes at 390 + 1280, screenshot, dump per-route console errors)

```javascript
import { chromium } from 'playwright';
const BASE = 'http://127.0.0.1:5001';
const OUT = process.env.OUT_DIR;
const ROUTES = [
  ['index','/'],['nutrition','/nutrition'],['training','/training'],['progress','/progress-page'],
  ['edit_profile','/edit-profile'],['friends','/friends'],['feed','/feed'],['leaderboard','/leaderboard'],
  ['quests','/quests'],['manage_stack','/supplements'],['premium','/premium'],['chat','/chat'],
  ['pump_check_gallery','/pump-check-gallery'],
];
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await ctx.newPage();
await page.goto(BASE + '/login', { waitUntil: 'networkidle' });
await page.fill('#username', 'qauser'); await page.fill('#password', 'Sifre123');
await Promise.all([ page.waitForResponse(r => r.url().endsWith('/login') && r.request().method()==='POST'),
                    page.click('[data-action="login"]') ]);
for (const [name, path] of ROUTES) {
  const errs = [];
  page.on('console', m => { if (m.type()==='error' && !m.text().includes('google.com/g/collect')) errs.push(m.text()); });
  await page.goto(BASE + path, { waitUntil: 'networkidle' });
  await page.setViewportSize({ width: 1280, height: 900 }); await page.waitForTimeout(200);
  await page.screenshot({ path: `${OUT}/qa-${name}-desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 }); await page.waitForTimeout(200);
  await page.screenshot({ path: `${OUT}/qa-${name}-mobile.png`, fullPage: true });
  console.log(`${name} ${path} status-ok, errors: ${errs.length ? JSON.stringify(errs) : 'none'}`);
  page.removeAllListeners('console');
}
await browser.close(); console.log('AUDIT DONE');
```

- [ ] **Step 5: Run the static inventory + the harness**

```bash
SP="<scratchpad>"; ROOT="C:/Users/yusuf/python_temellerii/flask"
PYTHONPATH="$ROOT" python "$SP/qa_seed_rich.py"
PYTHONPATH="$ROOT" python "$SP/qa_serve.py" &   # background; wait for :5001/login=200
OUT_DIR="$SP" node "$SP/qa_audit.mjs"
# static inventory:
grep -rcE 'var\(\s*--volt' static/*.css templates/*.html | grep -v ':0$'
for f in tokens components nav theme dashboard nutrition training progress profile coach_widget; do
  printf "%s " "$f"; grep -oiE '#[0-9a-f]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)' "static/$f.css" | grep -viE 'var\(' | wc -l; done
```
Expected: 13 screenshots × 2 widths captured; per-route "errors: none" (GA noise filtered); inventory numbers recorded.

- [ ] **Step 6: Write the ranked audit report**

Review every screenshot (desktop + mobile) against the 8 dimensions. Create `docs/superpowers/specs/2026-07-08-phase5-final-qa-audit.md` with: a **Global** section (the `--volt` usage map + raw-color counts + inline-`<style>` inventory) and a **per-surface** section for each of the 13, each finding tagged `[High]`/`[Med]`/`[Low]` with the file+symptom+fix. This report is the fix-spec that Stage-2 tasks read.

- [ ] **Step 7: Commit** (report only; scratchpad stays uncommitted)

```bash
git add docs/superpowers/specs/2026-07-08-phase5-final-qa-audit.md
git commit -m "Phase 5 final-QA Stage 0: ranked audit report + baseline screenshots"
```

---

### Task 2: Stage 1a — retire the `--volt*` aliases

**Files:**
- Modify: `static/coach_widget.css`, `static/theme.css`, `static/tokens.css`, and the inline `<style>` blocks of `templates/{chat,feed,friends,leaderboard,manage_stack,premium,quests}.html`

**Interfaces:**
- Consumes: nothing (deterministic).
- Produces: an app with **zero `var(--volt*)` usages** and no `--volt*` alias definitions. Canonical mapping: `--volt`→`--color-primary`, `--volt-dim`→`--color-primary-soft`, `--volt-glow`→`--color-primary-glow`, `--volt-dark`→`--color-primary-strong` (exact aliases per `tokens.css:208-211`).

- [ ] **Step 1: Confirm the alias mapping** — `sed -n '205,212p' static/tokens.css` shows `--volt: var(--color-primary)`, `--volt-dim: var(--color-primary-soft)`, `--volt-glow: var(--color-primary-glow)`, `--volt-dark: var(--color-primary-strong)`. These four are the whole mapping.

- [ ] **Step 2: Replace usages** — in each of `static/coach_widget.css`, `static/theme.css` and the 7 templates, replace (longest-first to avoid partial hits):
  `var(--volt-glow)`→`var(--color-primary-glow)`, `var(--volt-dark)`→`var(--color-primary-strong)`, `var(--volt-dim)`→`var(--color-primary-soft)`, then `var(--volt)`→`var(--color-primary)`. Use `Edit` with `replace_all: true` per token per file (verify each file's set of variants first with `grep -oE 'var\(\s*--volt[a-z-]*\)' <file> | sort -u`).

- [ ] **Step 3: Delete the alias block** — remove `tokens.css` lines defining `--volt`, `--volt-dim`, `--volt-glow`, `--volt-dark` (the "Legacy aliases" entries).

- [ ] **Step 4: Verify zero volt + tests green**

```bash
grep -rnE 'var\(\s*--volt|^\s*--volt' static/ templates/ || echo "VOLT CLEAN"
python -m pytest tests/test_design_system.py tests/test_i18n.py -q
```
Expected: `VOLT CLEAN`; tests pass.

- [ ] **Step 5: Browser spot-check** — restart `qa_serve.py`, re-screenshot `chat`, `friends`, `premium`, `quests` (they used `--volt` most) at desktop+mobile; diff against Task-1 baselines by eye. Expected: pixel-identical (aliases resolved to the same value).

- [ ] **Step 6: Commit**

```bash
git add static/coach_widget.css static/theme.css static/tokens.css templates/chat.html templates/feed.html templates/friends.html templates/leaderboard.html templates/manage_stack.html templates/premium.html templates/quests.html
git commit -m "Retire legacy --volt aliases: migrate all usages to canonical --color-primary tokens"
```

---

### Task 3: Stage 1b — tokenize `coach_widget.css` (79 raw colors)

**Files:**
- Modify: `static/coach_widget.css`, `static/tokens.css` (only if a new shared token is required)

**Interfaces:**
- Consumes: the Task-1 global raw-color list for `coach_widget.css`.
- Produces: `coach_widget.css` with **0 raw hex/`rgba()`** (only `var(--...)`), every value mapped to a canonical token.

- [ ] **Step 1: List the raw colors** — `grep -noiE '#[0-9a-f]{3,8}\b|rgba?\([^)]*\)' static/coach_widget.css | grep -viE 'var\('`. For each, pick the closest existing token from `tokens.css` (surfaces `--color-surface-*`, borders `--color-border-*`, text `--color-text-*`, primary `--color-primary*`, overlays `--overlay-*`, states `--color-success/-warning/-danger`). Record any value with no token match.

- [ ] **Step 2: Add shared tokens for genuine gaps** — for each unmatched value, add a semantic token to `tokens.css` (e.g. `--color-chat-bubble-user: <value>;`) in the appropriate block; do NOT leave the literal in the page CSS.

- [ ] **Step 3: Replace** every raw color in `coach_widget.css` with its `var(--...)`.

- [ ] **Step 4: Verify**

```bash
grep -noiE '#[0-9a-f]{3,8}\b|rgba?\([^)]*\)' static/coach_widget.css | grep -viE 'var\(' || echo "COACH CSS CLEAN"
python -m pytest tests/test_design_system.py -q
```
Expected: `COACH CSS CLEAN`; tests pass.

- [ ] **Step 5: Browser spot-check** — open `/chat` (widget visible) + any page with the floating coach; screenshot; confirm the widget looks identical to the Task-1 baseline (colors preserved, just tokenized).

- [ ] **Step 6: Commit**

```bash
git add static/coach_widget.css static/tokens.css
git commit -m "Tokenize coach_widget.css: map raw colors to canonical design tokens"
```

---

### Task 4: Stage 1c — tokenize `components.css` (21), `nutrition.css` (6), `theme.css` (7)

**Files:**
- Modify: `static/components.css`, `static/nutrition.css`, `static/theme.css`, `static/tokens.css` (only if a new shared token is required)

**Interfaces:**
- Consumes: Task-1 global raw-color lists for these files.
- Produces: these three files with **0 raw hex/`rgba()`** except values that are genuinely non-tokenizable primitives kept only when no `--overlay-*`/`--elevation-*`/color token fits (each such retained literal gets a `/* intentional: no token */` comment).

- [ ] **Step 1: List + map** — for each file run `grep -noiE '#[0-9a-f]{3,8}\b|rgba?\([^)]*\)' static/<f>.css | grep -viE 'var\('`; map each to the closest token as in Task 3 Step 1.
- [ ] **Step 2: Add shared tokens** for any gap (in `tokens.css`).
- [ ] **Step 3: Replace** each raw color with its `var(--...)`; annotate any deliberately-kept literal.
- [ ] **Step 3b: Consolidate flagged duplication (audit-conditional)** — if the Task-1 audit flagged a rule block duplicated across per-page CSS that belongs in the shared library, move it into `components.css` and delete the copies (only where it clearly reduces debt; no speculative abstraction). If the audit flagged none, skip this step.
- [ ] **Step 4: Verify**

```bash
for f in components nutrition theme; do printf "%s: " "$f"; grep -oiE '#[0-9a-f]{3,8}\b|rgba?\([^)]*\)' static/$f.css | grep -viE 'var\(' | grep -v 'intentional' | wc -l; done
python -m pytest tests/test_design_system.py -q
```
Expected: each count `0` (or only annotated intentionals); tests pass.

- [ ] **Step 5: Browser spot-check** — re-screenshot `/nutrition` + one component-heavy page (`/leaderboard`); diff vs baseline. Expected: identical.
- [ ] **Step 6: Commit**

```bash
git add static/components.css static/nutrition.css static/theme.css static/tokens.css
git commit -m "Tokenize residual raw colors in components/nutrition/theme CSS"
```

---

### Tasks 5–13: Stage 2 — per-surface polish for the 9 inline-`<style>` surfaces

**One task per surface**, in this order (highest audit priority first; the executor uses the Task-1 audit ranking if it differs): **5** `index`, **6** `friends`, **7** `feed`, **8** `leaderboard`, **9** `quests`, **10** `manage_stack`, **11** `premium`, **12** `chat`, **13** `pump_check_gallery`.

Each task follows the **identical procedure** below, substituting `<surface>` and its template/CSS paths.

**Files (per task):**
- Modify: `templates/<surface>.html`
- Create: `static/<surface>.css` (**exception:** for `index`, first check whether the inline block's rules belong in the existing `static/dashboard.css` — if `index.html` already links `dashboard.css`, append the extracted rules there instead of creating `index.css`)
- Modify (only if the audit lists a JS finding): `static/<surface>.js` if one exists

**Interfaces:**
- Consumes: the `<surface>` section of `2026-07-08-phase5-final-qa-audit.md` (the High/Med fix list); Stage-1's tokenized CSS + canonical tokens.
- Produces: `<surface>.html` with **no inline `<style>`**, its CSS in a linked file, and its High/Med findings resolved.

- [ ] **Step 1: Extract the inline `<style>`** — copy the exact inner CSS from `templates/<surface>.html`'s `<style nonce="{{ csp_nonce }}">…</style>` into a new `static/<surface>.css` (byte-identical selectors/rules). In `<head>`, add `<link rel="stylesheet" href="/static/<surface>.css">` (after the other stylesheet links). Delete the inline `<style>` block from the template.

- [ ] **Step 2: Verify no inline style remains** — `grep -c '<style' templates/<surface>.html` → `0`; `grep -c 'href="/static/<surface>.css"' templates/<surface>.html` → `1`.

- [ ] **Step 3: Apply the audit's High/Med fixes for `<surface>`** — from the audit report, apply each `[High]`/`[Med]` fix (spacing→`--space-*`, font→`--text-*`/`--weight-*`, color→token, responsive breakpoint, a11y focus/target/aria, empty/loading state). Make each fix in `static/<surface>.css` (or the template markup for aria/structure). Do **not** apply `[Low]` items.

- [ ] **Step 4: Token/CSP guard**

```bash
grep -oiE '#[0-9a-f]{3,8}\b|rgba?\([^)]*\)|var\(\s*--volt' static/<surface>.css | grep -viE 'var\(--color|var\(--overlay|var\(--elevation|intentional' || echo "<surface> CSS CLEAN"
```
Expected: `<surface> CSS CLEAN` (no raw colors, no `--volt`).

- [ ] **Step 5: Browser-verify before/after** — restart `qa_serve.py`; screenshot `/<surface-route>` at 390 + 1280; compare to the Task-1 baseline: the extraction must be visually identical, and each High/Med fix must be visibly resolved. Confirm the page's interactions still work (buttons/sheets/forms behave as before).

- [ ] **Step 6: Regression tests** — `python -m pytest tests/test_app_shell.py tests/test_design_system.py -q` (shared-shell + design-system stay green).

- [ ] **Step 7: Commit**

```bash
git add templates/<surface>.html static/<surface>.css   # + static/dashboard.css for the index exception; + static/<surface>.js only if changed
git commit -m "Polish <surface>: extract inline CSS to static/<surface>.css + fix High/Med audit findings"
```

---

### Task 14: Stage 2 — polish the 4 already-extracted surfaces (fixes only)

**Files:**
- Modify: `static/{nutrition,training,progress,profile}.css` and/or `templates/{nutrition,training,progress,edit_profile}.html` (only where the audit lists a High/Med fix)

**Interfaces:**
- Consumes: the `nutrition`/`training`/`progress`/`edit_profile` sections of the audit report.
- Produces: those four surfaces with their High/Med findings resolved. (No inline-style extraction — already done in earlier phases.)

- [ ] **Step 1: Apply High/Med fixes** for each of the four surfaces from the audit report (same fix types as Task 5 Step 3). Skip any surface whose audit section has no High/Med findings.
- [ ] **Step 2: Token guard** — for each modified CSS: `grep -oiE '#[0-9a-f]{3,8}\b|rgba?\([^)]*\)|var\(\s*--volt' static/<f>.css | grep -viE 'var\(--color|var\(--overlay|var\(--elevation|intentional' || echo "<f> CLEAN"`. Expected: CLEAN.
- [ ] **Step 3: Browser-verify** — re-screenshot the modified routes at 390 + 1280; confirm fixes landed, no regressions.
- [ ] **Step 4: Regression tests** — `python -m pytest tests/test_design_system.py tests/test_profile_ui.py tests/test_progress_ui.py -q`. Expected: pass.
- [ ] **Step 5: Commit**

```bash
git add <the modified static/*.css and templates/*.html files>
git commit -m "Polish nutrition/training/progress/profile: fix High/Med audit findings"
```

---

### Task 15: Stage 3 — full-suite verification, final report, handoff

**Files:**
- Create: the final report section inside `docs/handoff.md` (rewrite)
- Archive: current `docs/handoff.md` → `docs/archive/handoff-2026-07-08-phase5-profile.md`

**Interfaces:**
- Consumes: all prior tasks.

- [ ] **Step 1: Full suite**

```bash
python -m pytest -q
```
Expected: **≥1115 passed, 0 failures** (no test count regression; this phase adds no tests but must not break any).

- [ ] **Step 2: Final single-source-of-truth guards**

```bash
grep -rnE 'var\(\s*--volt|^\s*--volt' static/ templates/ || echo "VOLT CLEAN (app-wide)"
for f in coach_widget components nutrition theme friends feed leaderboard quests manage_stack premium chat pump_check_gallery dashboard training progress profile; do
  printf "%s: " "$f"; grep -oiE '#[0-9a-f]{3,8}\b|rgba?\([^)]*\)' "static/$f.css" 2>/dev/null | grep -viE 'var\(|intentional' | wc -l; done
grep -lE '<style' templates/{index,friends,feed,leaderboard,quests,manage_stack,premium,chat,pump_check_gallery,nutrition,training,progress,edit_profile}.html || echo "NO INLINE STYLE in the 13 surfaces"
```
Expected: `VOLT CLEAN`; each in-scope CSS `0` (or annotated intentionals); `NO INLINE STYLE`.

- [ ] **Step 3: Full-app screenshot pass** — restart `qa_serve.py`; run `qa_audit.mjs` once more; review all 13 desktop+mobile shots side-by-side with the Task-1 baselines to confirm the whole surface set is consistent and no regression slipped in.

- [ ] **Step 4: Archive prior handoff + write the final report/handoff**

```bash
git mv docs/handoff.md docs/archive/handoff-2026-07-08-phase5-profile.md
```
Then write a new `docs/handoff.md` (the phase-5.txt-required **final report**) covering: scope (13 core surfaces), **modified files**, **architecture improvements** (single source of truth: `--volt` retired, coach_widget/components/nutrition/theme tokenized, 9 inline blocks extracted), **reusable components created/consolidated**, **remaining technical debt** (the logged Low findings + the 5 out-of-scope legacy auth pages), **future recommendations**, the verification results, and the same manual-QA caveat as prior surfaces. Note Phase 5 is now complete (Workout→Progress→Profile→Final QA all shipped).

- [ ] **Step 5: Commit**

```bash
git add docs/handoff.md docs/archive/handoff-2026-07-08-phase5-profile.md
git commit -m "Phase 5 final-QA: single-source-of-truth guards green + final report/handoff"
```

- [ ] **Step 6: Open the PR** — push `feat/phase5-final-qa`; `gh pr create --base main` with a summary of the token migration, inline-style extraction, per-surface High/Med fixes, `pytest` result, and the screenshot QA. (Merge/deploy is the user's call, as with prior surfaces.)

---

## Notes for the executor

- **Audit-driven Stage 2:** Tasks 5–14 intentionally reference the Task-1 audit report for their exact fix list — that report is the detailed fix-spec, produced before any Stage-2 work. If a surface's audit section lists no High/Med items, that task is just the inline-style extraction (Tasks 5–13) or a no-op (Task 14).
- **Production-safety:** the token migration is same-computed-value; inline-style extraction keeps selectors byte-identical. Any visual delta between a before/after screenshot that is NOT an intended fix is a regression — stop and investigate before committing.
- **`git add -A` is forbidden** (untracked scratch at repo root). Scratchpad QA files are never committed.
- **If a "fix" would require changing a backend route, model, or business rule, it is out of scope** — log it as remaining tech debt in the Stage-3 report instead.
