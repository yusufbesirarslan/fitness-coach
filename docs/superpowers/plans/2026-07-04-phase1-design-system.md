# AxisAI V2 Phase 1 — Design System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the app's scattered styling (2 competing token sets + ~2 400 lines of inline template CSS with hardcoded values) into one canonical, documented, token-based design system with a reusable component library — with **zero intentional visual change** except the body font moving to Inter.

**Architecture:** A new `static/tokens.css` (single source of truth: primitives → semantic tokens → legacy aliases, dark default + `[data-theme="light"]` overrides) and `static/components.css` (16 reusable components, consolidating what already exists in `theme.css`/`style.css` under their current class names) are loaded globally from `templates/_head.html` *before* all page CSS, so existing pages keep working unchanged while every stylesheet and inline block gets its hardcoded values re-pointed at tokens.

**Tech Stack:** Plain CSS custom properties, Jinja2 templates, Google Fonts (Inter), pytest for render regression, no build step.

## Global Constraints

- **Do NOT redesign pages.** Visual output must stay pixel-equivalent except: body font `DM Sans` → `Inter` (spec-mandated).
- Typography: **Inter** (spec). `--font-display` stays Bebas Neue for now — replacing it would visually redesign every heading (later phases' job).
- 8-point spacing system (4 px half-steps allowed).
- Light/Dark **readiness** only: semantic tokens themed under `[data-theme]`; all pages currently hardcode `data-theme="dark"`.
- CSP: any new inline `<style>` needs `nonce="{{ csp_nonce }}"`; new stylesheets are same-origin `<link>` (allowed by `style-src-elem 'self'`). Fonts: `fonts.googleapis.com`/`gstatic` already allowed.
- Keep legacy CSS variable names (`--volt*`, `--accent*`, `--s1..s10`, `--r-*`, `--t-*`, …) working as aliases — hundreds of references; no template class renames.
- Phase 2 redesigns navigation: tokenize `nav.css` but do NOT restructure it.
- Branch: `feat/phase1-design-system` off `origin/main` (90b36cd). Short commit messages (CLAUDE.md).
- Do not commit `.superpowers/sdd/*` or `AGENTS.md` (pre-existing scratch).
- `docs/handoff.md` (old pump-check handoff, work 100 % merged) gets **replaced** at phase end per phase spec.

## Current-State Map (verified)

| File | Role | State |
|---|---|---|
| `static/theme.css` (27 KB) | App-page design system "v2.0" | Has `:root` tokens (`--volt`, `--surface-2`…), components §8–§31, ~25 hardcoded hexes + many raw rgba/px |
| `static/style.css` (15 KB) | Auth/onboarding pages (landing, login, register, setup, verify) | Own token set (`--accent`, `--bg2`, `--card`…) incl. dormant light theme; duplicates buttons/inputs/chips/stat-card |
| `static/nav.css` (9 KB) | Nav shell v3 | Mostly tokenized, ~3 hexes + raw rgba |
| `static/dashboard.css` (21 KB) | index.html only | ~57 hardcoded hexes |
| `static/coach_widget.css` (12 KB) | Coach chat widget | ~54 hardcoded hexes |
| Templates ×20 | 1 inline `<style nonce>` block each, 26–320 lines | Hardcoded palette/spacing/fonts throughout |
| `templates/_head.html` | Shared `<head>`: fonts, CSRF, i18n, GA | Loads Bebas Neue + DM Sans only |
| `404.html` / `500.html` | Standalone, no `_head.html` | 6 css lines each — leave alone |

Exact value equivalences between the two legacy systems (verified): `--accent`≡`--volt`≡`#3D8BFF`, `--accent2`≡`--volt-dark`≡`#1E6FE0`, `--accent-glow`≡`--volt-dim`, `--bg`≡`#121212`, `--bg2`≡`--surface`≡`#1A1A1A`, `--card`≡`--surface-2`≡`#1E1E1E`, `--bg3`≡`--surface-3`≡`#252525`, text scales identical. **Sole mismatch:** style.css `--border:#242424` (solid) vs theme.css `--border:rgba(255,255,255,0.07)` — keep both, as `--color-border-solid` primitive for auth.

## File Structure After Phase 1

- Create: `static/tokens.css` — all tokens (spec below), no selectors other than `:root`/`[data-theme="light"]`
- Create: `static/components.css` — 16 components + shared keyframes
- Create: `docs/design-system.md` — full token + component documentation
- Create: `tests/test_design_system.py` — regression guards
- Modify: `templates/_head.html` — Inter font; link tokens.css + components.css
- Modify: `static/theme.css` — remove `:root` token block + moved component sections; tokenize remainder
- Modify: `static/style.css` — theme blocks re-pointed at canonical tokens; duplicated component styles tokenized
- Modify: `static/nav.css`, `static/dashboard.css`, `static/coach_widget.css` — tokenize values
- Modify: 18 templates' inline `<style>` blocks — tokenize values
- Replace: `docs/handoff.md` — Phase 1 handoff

## Token Specification (authoritative — implement exactly)

`static/tokens.css` layout, all under `:root` unless noted:

**1. Color primitives** (raw values, never used directly by pages):
`--gray-950:#0A0A0C; --gray-900:#121212; --gray-850:#1A1A1A; --gray-820:#1E1E1E; --gray-800:#252525; --gray-400:#4D4D4D; --gray-300:#808080; --gray-200:#A6A6A6; --gray-50:#F4F4F4; --blue-300:#60A8FF; --blue-400:#3D9EFF; --blue-500:#3D8BFF; --blue-600:#1E6FE0; --blue-vivid:#007BFF; --red-500:#FF4D4D; --red-400:#FF7070; --green-500:#00C48C; --amber-500:#FFB020; --color-border-solid:#242424;`

**2. Semantic colors** (dark = default):
- Primary: `--color-primary: var(--blue-500); --color-primary-strong: var(--blue-600); --color-primary-soft: rgba(61,139,255,0.09); --color-primary-glow: rgba(61,139,255,0.30); --color-on-primary: #121212;`
- Background/surface: `--color-bg: var(--gray-900); --color-surface-1: var(--gray-850); --color-surface-2: var(--gray-820); --color-surface-3: var(--gray-800);`
- Text: `--color-text-1: var(--gray-50); --color-text-2: var(--gray-200); --color-text-3: var(--gray-300); --color-text-4: var(--gray-400);`
- Status: success/warning/danger/info + `-soft` pairs (values = existing `--green/--orange/--red/--blue` + dims)
- Borders: `--color-border-1: rgba(255,255,255,0.07); --color-border-2: rgba(255,255,255,0.13);`
- Interaction overlays (kills the biggest rgba duplication): `--overlay-2/-3/-4/-6/-10: rgba(255,255,255,0.02/0.03/0.04/0.06/0.10);`

**3. Typography:** `--font-sans:'Inter','DM Sans',system-ui,-apple-system,'Segoe UI',sans-serif; --font-display:'Bebas Neue',var(--font-sans); --font-body:var(--font-sans);`
Sizes: `--text-2xs:10px; --text-xs:11px; --text-sm:12px; --text-md:13px; --text-base:14px; --text-lg:15px; --text-xl:17px; --text-2xl:20px; --text-3xl:24px;` responsive display: `--text-display-sm:clamp(28px,4vw,36px); --text-display-md:clamp(32px,5vw,44px); --text-display-lg:clamp(38px,6vw,52px);`
Weights 300–800 (`--weight-light…--weight-extrabold`); line-heights `--leading-none:1 /-tight:1.2 /-snug:1.4 /-normal:1.6 /-relaxed:1.75`; tracking `--tracking-wide:0.04em /-wider:0.08em /-widest:0.12em /-label:0.16em`.

**4. Spacing (8-pt grid):** `--space-1:4px; --space-2:8px; --space-3:12px; --space-4:16px; --space-5:20px; --space-6:24px; --space-8:32px; --space-10:40px; --space-12:48px; --space-16:64px; --space-20:80px;`

**5. Radius:** `--radius-xs:4px; --radius-sm:8px; --radius-md:12px; --radius-lg:16px; --radius-xl:24px; --radius-full:9999px;`

**6. Border widths:** `--border-w-1:1px; --border-w-2:1.5px; --border-w-3:2px;`

**7. Elevation:** `--elevation-1: 0 1px 2px rgba(0,0,0,0.40); --elevation-2: 0 4px 16px rgba(0,0,0,0.45); --elevation-3: 0 12px 32px rgba(0,0,0,0.55); --shadow-primary: 0 2px 10px rgba(61,139,255,0.14); --focus-ring: 0 0 0 3px rgba(61,139,255,0.07);`

**8. Opacity:** `--opacity-disabled:0.65; --opacity-muted:0.7; --opacity-faint:0.85;`

**9. Icon sizes:** `--icon-xs:14px; --icon-sm:16px; --icon-md:18px; --icon-lg:22px; --icon-xl:26px;`

**10. Motion:** `--duration-fast:0.14s; --duration-base:0.2s; --duration-slow:0.28s; --duration-slower:0.32s; --ease-standard:cubic-bezier(0.4,0,0.2,1); --ease-out-expo:cubic-bezier(0.16,1,0.3,1); --ease-out-quint:cubic-bezier(0.22,1,0.36,1); --ease-bounce:cubic-bezier(0.34,1.56,0.64,1);`

**11. Z-index:** `--z-header:100; --z-drawer-backdrop:199; --z-drawer:200; --z-fab:200; --z-overlay:300; --z-toast:400;`

**12. Layout & breakpoints:** `--content-max:1280px; --header-h:56px; --action-bar-h:68px; --drawer-w:280px;` breakpoint reference tokens (`--bp-sm:520px; --bp-md:640px; --bp-lg:768px; --bp-xl:1024px; --bp-2xl:1280px`) with comment: CSS vars can't drive `@media` — keep queries in sync with docs table.

**13. Legacy aliases** (single block, commented "do not use in new code"): every `theme.css` v2 token (`--bg --surface --surface-2 --surface-3 --border --border-2 --volt --volt-dim --volt-glow --volt-dark --blue --blue-dim --blue-light --red --red-dim --green --green-dim --orange --orange-dim --text --text-2 --text-3 --text-4 --s1..--s10 --r-sm..--r-full --font-display --font-body --shadow-sm/md/lg/volt/blue --t-fast/base/spring`) and every `style.css` name not already claimed (`--accent --accent2 --accent-glow --bg2 --bg3 --card --input-bg --text2 --text3`) → `var(--canonical)`. `--t-*` become composite `var(--duration-*) var(--ease-*)`.
⚠️ `--border` alias = `var(--color-border-1)` (theme.css meaning). style.css keeps its own `--border:#242424` override → `var(--color-border-solid)` inside its `[data-theme]` block so auth pages don't shift.

**14. `[data-theme="light"]` block:** overrides *semantic* tokens only, values = style.css's existing light theme (`bg #f5f5f0, surfaces #eeede8/#e5e4df/#fff, text #1a1a1a/#5c5c5c/#808080, primary #1A66D0/strong #1550B8/soft rgba(26,102,208,0.1), border rgba(0,0,0,0.10)/solid #d5d4cf, overlays rgba(0,0,0,…), danger #d44, warning #c89800, info #2a70c0`). Aliases flip automatically since they reference semantic tokens.

## Component Inventory for `static/components.css`

Existing class names are the public API — moved verbatim (then tokenized), NOT renamed. New components get new names. Shared keyframes (`spin, fade-in, slide-up, toast-in, pulse-glow, skeleton-shimmer`) move here from theme.css.

| Spec component | Class(es) | Source |
|---|---|---|
| Button | `.btn-volt`, `.btn-ghost` + new `.btn-danger` | move from theme.css §11 |
| Input | `.fc-input` (+ new `.field`, `.field-label` wrapper) | move from theme.css §12 |
| Card | `.card`, `.card-hover` | move from theme.css §8 |
| Modal | **new** `.modal-backdrop`, `.modal`, `.modal-header/-title/-close/-body/-footer` | new (matches existing ad-hoc template modals: fixed inset backdrop blur, centered surface-2 card, r-lg) |
| Bottom Sheet | **new** `.sheet-backdrop`, `.sheet`, `.sheet-handle`, `.sheet-title` | new (slide-up from bottom, r-xl top corners, safe-area padding) |
| Badge | **new** `.badge` + `.badge-primary/-success/-warning/-danger/-neutral` (generalizes `.meal-badge`, which stays in theme.css) | new |
| Chip | `.chip`, `.chip-dot` | move from theme.css §10 |
| Avatar | **new** `.avatar` + `.avatar-sm(28)/-md(34)/-lg(48)/-xl(64)` — gradient `linear-gradient(135deg,var(--color-primary),var(--color-info))`, initials style per nav.css `.header-avatar` | new |
| Progress Ring | `.ring-svg/-track/-fill/-wrap/-label` | move from theme.css §13 |
| Progress Bar | `.pbar-track/-fill` | move from theme.css §14 |
| Navigation Item | `.tab-bar`, `.tab-btn`, `.tab-panel` | move from theme.css §9 (drawer/action-bar items stay in nav.css for Phase 2) |
| FAB | `.quick-add-*`, `.fab-row/-lbl/-sub` (documented as the FAB) | move from theme.css §19 |
| Empty State | `.empty-state/-icon/-title/-sub` | move from theme.css §25 |
| Loading Skeleton | `.skeleton` + new `.skeleton-text`, `.skeleton-circle` | move from theme.css §17 |
| Stat Card | `.stat-card/-label/-value/-unit` (canonical, tokenized) | lift from style.css (style.css keeps none — verify usage) |
| Section Header | `.sec-label`, `.cat-label` | move from theme.css §22 |
| (bonus, shared) | `.toast-*`, `.loading-overlay/-spinner/-text`, `.fc-divider*` | move from theme.css §18/§24/§27 |

Also in components.css: tabular-figures rule (§33), focus-visible rule (§34), reduced-motion rule (§35) — they target component classes.

## Hardcoded-Value Mapping Table (for all tokenization sweeps)

Apply only exact matches (case-insensitive hex), reviewing each diff hunk:

`#3D8BFF→var(--volt)` · `#1E6FE0→var(--volt-dark)` · `rgba(61,139,255,0.09)→var(--volt-dim)` · `rgba(61,139,255,0.3)/0.30→var(--volt-glow)` · `#121212→var(--bg)` (as color/background; keep `--color-on-primary` context on primary buttons) · `#1A1A1A→var(--surface)` · `#1E1E1E→var(--surface-2)` · `#252525→var(--surface-3)` · `#F4F4F4/#F2F2F2→var(--text)` · `#A6A6A6→var(--text-2)` · `#808080→var(--text-3)` · `#4D4D4D→var(--text-4)` · `rgba(255,255,255,0.07)→var(--border)` · `rgba(255,255,255,0.13)→var(--border-2)` · `rgba(255,255,255,0.02/0.03/0.04/0.06/0.10)→var(--overlay-2/-3/-4/-6/-10)` · `#FF4D4D→var(--red)` · `#00C48C→var(--green)` · `#FFB020→var(--orange)` · `#007BFF→var(--blue)` · `#3D9EFF→var(--blue-light)` · `#60A8FF→var(--blue-300)` · `'DM Sans',sans-serif→var(--font-body)` · `'Bebas Neue',sans-serif→var(--font-display)` · easing literals→`var(--ease-*)`.
Leave alone: one-off rgba tints with no token twin, gradient stops that don't match exactly, SVG data-URI colors, GA/vendor code.

---

### Task 1: Branch + tokens.css + global wiring + Inter

**Files:** Create `static/tokens.css`, `tests/test_design_system.py`; Modify `templates/_head.html`.
**Produces:** every page serves tokens.css before page CSS; Inter loaded; all legacy var names now resolve from tokens.css (still also defined in theme/style.css — harmless duplicate definition until Task 3/4).

- [ ] Step 1: `git checkout -b feat/phase1-design-system origin/main`
- [ ] Step 2: Write failing test `tests/test_design_system.py` (use existing conftest client fixture pattern):

```python
def test_head_serves_design_system_assets(client):
    resp = client.get("/login")
    html = resp.get_data(as_text=True)
    assert "/static/tokens.css" in html
    assert "/static/components.css" in html
    assert "family=Inter" in html

def test_tokens_css_defines_canonical_and_legacy_tokens(client):
    css = client.get("/static/tokens.css").get_data(as_text=True)
    for token in ("--color-primary:", "--space-2:", "--radius-md:",
                  "--volt:", "--accent:", "--font-sans:", "[data-theme=\"light\"]"):
        assert token in css

def test_components_css_defines_spec_components(client):
    css = client.get("/static/components.css").get_data(as_text=True)
    for sel in (".btn-volt", ".fc-input", ".card", ".modal", ".sheet",
                ".badge", ".chip", ".avatar", ".ring-svg", ".pbar-track",
                ".tab-btn", ".quick-add-btn", ".empty-state", ".skeleton",
                ".stat-card", ".sec-label"):
        assert sel in css
```

- [ ] Step 3: Run `python -m pytest tests/test_design_system.py -v` → expect 3 FAIL (404 / missing links).
- [ ] Step 4: Create `static/tokens.css` per Token Specification above, fully commented per section.
- [ ] Step 5: In `_head.html`: extend fonts link with `family=Inter:wght@300;400;500;600;700;800` (keep Bebas Neue + DM Sans); after fonts add `<link rel="stylesheet" href="/static/tokens.css?v={{ _v }}">` and `<link rel="stylesheet" href="/static/components.css?v={{ _v }}">` (components.css created empty-with-header this task so the link isn't a 404).
- [ ] Step 6: `python -m pytest tests/test_design_system.py tests/test_auth.py -v` → design-system tests 1–2 PASS (3rd passes after Task 2; mark selectors present via placeholder comment is NOT allowed — instead move test 3 to Task 2 file addition if it fails here).
- [ ] Step 7: Commit `Add design tokens foundation (tokens.css, Inter)`.

### Task 2: components.css — consolidate + new components

**Files:** Modify `static/components.css` (fill), `static/theme.css` (delete moved sections §8–§14, §17–§19, §22, §24–§25, §27, §33–§35 + keyframes + `:root` token block — tokens now live in tokens.css).
**Produces:** the 16-component library per Component Inventory; theme.css shrinks to base/reset/layout + feature-specific sections (§15–16, §20–21, §23, §26, §28–§31, responsive overrides).

- [ ] Step 1: Fill components.css: keyframes, then each component section moved verbatim from theme.css, then new Modal / Bottom Sheet / Badge / Avatar / skeleton variants / field wrapper / btn-danger written token-first. New-component styling constraints: Modal backdrop `position:fixed; inset:0; background:rgba(0,0,0,0.55); backdrop-filter:blur(6px); z-index:var(--z-overlay)`; modal panel `background:var(--color-surface-2); border:var(--border-w-1) solid var(--color-border-1); border-radius:var(--radius-lg); box-shadow:var(--elevation-3); max-width:min(92vw,480px)`; Sheet slides from bottom, `border-radius:var(--radius-xl) var(--radius-xl) 0 0; padding-bottom:env(safe-area-inset-bottom,var(--space-4))`, handle 36×4 px `var(--overlay-10)`; Badge = `.meal-badge` geometry generalized (`font-size:var(--text-2xs); padding:3px var(--space-2); border-radius:var(--radius-xs); letter-spacing:var(--tracking-widest); text-transform:uppercase`) with color variants from status soft/solid pairs; Avatar circle + gradient + centered initials, sizes 28/34/48/64.
- [ ] Step 2: Delete moved sections + `:root` block + keyframes from theme.css; tokenize every remaining hardcoded value in theme.css per mapping table (body font → `var(--font-body)`).
- [ ] Step 3: `python -m pytest tests/test_design_system.py -v` → all PASS. Then broad render regression: `python -m pytest tests/test_auth.py tests/test_nutrition_routes.py tests/test_training_routes.py tests/test_profile_routes.py tests/test_social_routes.py -q`.
- [ ] Step 4: Commit `Add component library, slim theme.css to tokens`.

### Task 3: Tokenize remaining shared stylesheets

**Files:** Modify `static/nav.css`, `static/dashboard.css`, `static/coach_widget.css`, `static/style.css`.

- [ ] Step 1: nav.css — apply mapping table; move its `:root` sizing tokens' values into comments pointing at tokens.css (tokens.css owns `--header-h` etc.; delete nav.css `:root` block, keep `--fab-size/--fab-protrude` there if unused elsewhere → move them to tokens.css layout section instead).
- [ ] Step 2: dashboard.css + coach_widget.css — apply mapping table (≈57 + 54 hexes). Only exact matches; review diff.
- [ ] Step 3: style.css — replace `:root[data-theme="dark"]` values with `var(--canonical)` refs (keep `--border: var(--color-border-solid)`); delete its `[data-theme="light"]` block (now canonical in tokens.css) BUT first confirm every name it set is aliased in tokens.css; tokenize component bodies (fonts, radii, spacing, colors); delete its duplicated `@keyframes spin`.
- [ ] Step 4: Render regression: `python -m pytest tests/test_auth.py tests/test_i18n.py tests/test_hooks.py -q` + start app briefly and eyeball `/login`, `/` (see Task 6 protocol).
- [ ] Step 5: Commit `Tokenize shared stylesheets`.

### Task 4: Tokenize inline styles — app pages

**Files:** Modify inline `<style>` blocks of `index, nutrition, training, progress, chat, friends, feed, leaderboard, quests, manage_stack, edit_profile, premium, pump_check_gallery` (13 templates).

- [ ] Step 1: Per file: apply mapping table to the `<style>` block only (never touch Jinja/JS/HTML attrs); also swap raw font-family/easing literals. Review each diff hunk before moving on.
- [ ] Step 2: After each 3–4 files, run matching route tests (`test_nutrition_routes`, `test_training_routes`, `test_social_routes`, `test_gamification_routes`, `test_tracking_routes`, `test_supplements_routes`, `test_profile_routes`) — templates render server-side, so Jinja damage surfaces here.
- [ ] Step 3: Commit `Tokenize app page styles`.

### Task 5: Tokenize inline styles — auth/onboarding pages

**Files:** Modify `landing.html, login.html, register.html, setup.html, verify.html` inline blocks (these use `--accent` family; hardcoded values map to the same canonical tokens).

- [ ] Step 1: Apply mapping table (+ `--accent`-family equivalents) per file, diff review.
- [ ] Step 2: `python -m pytest tests/test_auth.py tests/test_cognito.py -q`.
- [ ] Step 3: Commit `Tokenize auth page styles`.

### Task 6: Visual smoke + fixes

- [ ] Step 1: `$env:FLASK_DEBUG=1; flask --app starter run` (memory: FLASK_DEBUG=1 required locally; scratch SQLite; `LOGIN_FAIL_CLOSED=0`).
- [ ] Step 2: Browser-check `/login`, `/register`, landing, then authed: `/`, `/nutrition`, `/training`, `/progress`, `/quests`, `/friends`, `/feed` — compare against expectations: identical layout/colors, Inter body text, no unstyled regions, focus rings intact.
- [ ] Step 3: Fix any regressions found; re-run full suite `python -m pytest -q` (baseline: 1031 passed).
- [ ] Step 4: Commit fixes if any: `Fix design-token regressions`.

### Task 7: Documentation + handoff

**Files:** Create `docs/design-system.md`; Replace `docs/handoff.md`.

- [ ] Step 1: `docs/design-system.md`: architecture (load order, aliasing, theming), every token with value + usage guidance (tables per category), breakpoint table (with "media queries can't read vars" note), component catalog with copy-paste HTML snippets for all 16 components, extension guide (how to add a token/component, legacy-alias policy: new code uses canonical names only), CSP/nonce gotcha, Phase-2+ TODOs (unify auth `--border`, retire legacy aliases, adopt Modal/Sheet/Badge/Avatar in pages).
- [ ] Step 2: Rewrite `docs/handoff.md` per phase-spec structure: completed work, files modified, components created/refactored, architectural decisions, remaining tasks, known issues, next steps + quality metrics review (responsiveness, accessibility, visual consistency, maintainability, reusability, performance, UX clarity) with explicit weak-spot callouts.
- [ ] Step 3: Final `python -m pytest -q`; `git status --short` (no scratch files staged).
- [ ] Step 4: Commit `Add design system docs and phase 1 handoff`.

## Self-Review Notes

- Spec coverage: spacing/radius/typography/colors/shadows/borders/opacity/icons/motion/breakpoints/light-dark → Task 1; 16 components → Task 2 (inventory maps every spec item); replace hardcoded values → Tasks 2–5; remove duplicated styles → Tasks 2–3 (component consolidation + deleted duplicate token blocks/keyframes); document every token → Task 7; "don't redesign" → global constraint; handoff → Task 7.
- Risk register: (1) moving rules from theme.css to components.css changes file order vs nav/dashboard CSS — safe because theme.css already loaded first; inline page styles still load last. (2) components.css now loads on auth pages — new class definitions there are inert unless templates use the names; login/register inline blocks load later and win ties. (3) `--border` divergence handled via `--color-border-solid`. (4) DM Sans 300-weight usage → Inter 300 explicitly loaded.
