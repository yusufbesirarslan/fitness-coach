# Phase 5 · Surface 4 — Global Polish + Final QA (Design Spec)

Date: 2026-07-08
Branch: `feat/phase5-final-qa` (off `main` @ `4b7a6c5`, after Profile merged)
Phase source: `phase-5.txt` (§ Global Polish + § FINAL QA)
Prior surface: `docs/handoff.md` (Phase 5 Profile)

## Goal

Close out the AxisAI V2 redesign with a whole-app consistency pass over the
**13 redesigned core surfaces**: audit and fix visual/interaction
inconsistencies, and refactor remaining legacy styling so **every redesigned
surface uses the canonical design system exclusively** — a single source of
truth (`tokens.css` + `components.css` + `nav.css`) with **no legacy `--volt`
aliases and no ad-hoc raw colors**. Frontend-only; backend and business logic
untouched.

## Scope

**In scope — the 13 canonical surfaces** (already on tokens/components/nav):
`index` (dashboard), `nutrition`, `training`, `progress`, `edit_profile`,
`friends`, `feed`, `leaderboard`, `quests`, `manage_stack` (supplements),
`premium`, `chat`, `pump_check_gallery` — plus the app-wide `coach_widget.css`
(the AI-coach widget rendered across surfaces) and the shared
`tokens.css`/`components.css`/`theme.css`/`nav.css`.

**Out of scope (explicit):**
- The 5 legacy auth/onboarding pages (`login`, `register`, `setup`, `verify`,
  `landing`) still on `style.css` — a separate future cycle.
- Any new feature, backend route, model, migration, or business-logic change.

## Guardrails

- **No new features.** No backend/business-logic/route/model/schema changes.
- **Preserve functionality** — every interaction behaves identically after.
- **Production-safe & incremental** — one branch `feat/phase5-final-qa`, one PR,
  logically-grouped commits; full `pytest` green throughout; expand-only.
- **Single source of truth** — after this phase: zero `--volt*` usages, the
  legacy aliases removed from `tokens.css`; no raw hex/`rgba()` in the in-scope
  surface CSS except where a genuinely new shared token is introduced (defined
  in `tokens.css`, referenced everywhere else).
- **Regression safety** — no visual-regression test infra exists, so use
  Playwright before/after screenshots (per surface, mobile + desktop) as the
  diff harness, plus the design-system/i18n pytest guards.

## Design-system context (from the Stage-0 static audit)

- **Primitives already centralized:** `.card`, `.stat-card`, `.sheet`, `.badge`,
  `.skeleton`, `.empty-state`, `.btn-*` live only in `components.css` — no
  per-page redefinition. So Stage 1 is **token migration, not class dedupe.**
- **Legacy `--volt` aliases** in `tokens.css` (`--volt: var(--color-primary)`,
  `--volt-dim/-glow/-dark`) — ~91 usages remain: `coach_widget.css` (16),
  `theme.css` (16), and inline `<style>` blocks in 7 templates (`chat` 15,
  `friends` 12, `manage_stack` 9, `leaderboard` 8, `premium` 7, `feed` 4,
  `quests` 4).
- **Raw-color debt:** `coach_widget.css` 79, `components.css` 21, `nutrition.css`
  6 (others 0). `coach_widget.css` is the top offender and is in scope.
- **Inline `<style>` blocks** in 9 of 13 templates: `index`, `friends`, `feed`,
  `leaderboard`, `quests`, `manage_stack`, `premium`, `chat`,
  `pump_check_gallery` (earlier-phase surfaces). `nutrition`/`training`/
  `progress`/`edit_profile` are already fully extracted to per-page CSS.

## Stage 0 — Audit (static + visual)

Produce one ranked findings report at
`docs/superpowers/specs/2026-07-08-phase5-final-qa-audit.md`.

1. **Static inventory** (extends the numbers above): per-file raw-color list,
   `--volt` usage map, inline-`<style>` inventory, and per-surface spot-checks
   for hardcoded spacing/font-size not using `--space-*`/`--text-*`.
2. **Visual audit:** reuse the Profile QA Playwright harness — seed one rich
   user (streak, XP, meals, workouts, supplements, a friend/quest so lists are
   non-empty), drive all 13 routes authenticated at **390px + 1280px**, capture
   screenshots, and catalog inconsistencies across the eight dimensions:
   **spacing, typography, color, responsive layout, accessibility, component
   behavior, interaction patterns, loading states, empty states.**
3. **Rank** findings High/Med/Low; each maps to a Stage-1 (global) or Stage-2
   (per-surface) fix.

## Stage 1 — Global foundation (cross-cutting, production-safe)

Done once, up front, because it is inherently multi-surface. Each step verified
by design-system pytest + a browser spot-check of representative surfaces.

1. **Retire `--volt`:** replace every `var(--volt)`/`--volt-dim`/`-glow`/`-dark`
   with the canonical `var(--color-primary)`/`-soft`/`-glow`/`-strong` — in
   `coach_widget.css`, `theme.css`, and the inline `<style>` blocks of the 7
   templates. Aliases resolve to the same computed value, so the migration is
   visually a no-op. **Then delete the `--volt*` alias block from `tokens.css`.**
2. **Tokenize `coach_widget.css`** — map its 79 raw colors to existing tokens
   (surface/border/text/primary/overlay); where a value has no token, add a
   **shared** token to `tokens.css` and reference it. End state: no raw colors.
3. **Tokenize residual raw colors** in `components.css` (21), `nutrition.css`
   (6), and `theme.css` (7) the same way (keep legitimately non-tokenizable
   primitives — e.g. a shadow's `rgba` — only if no token fits, preferring an
   `--elevation-*`/`--overlay-*` token).
4. **Consolidate** any genuine one-off duplication the audit surfaces into
   `components.css` (only where it clearly reduces debt; no speculative
   abstraction).

## Stage 2 — Per-surface polish (surface-by-surface)

For each of the 13 surfaces, in audit-priority order: apply that surface's
**High/Med** Stage-0 findings — fix spacing/typography/color-token compliance,
responsive breakpoints, a11y (focus-visible, touch targets ≥44px, aria,
keyboard), loading/empty states, and interaction consistency. **Extract the
inline `<style>` block from all 9 templates** that still carry one (`index`,
`friends`, `feed`, `leaderboard`, `quests`, `manage_stack`, `premium`, `chat`,
`pump_check_gallery`) into per-page CSS files (identical selectors, relocated +
the `<style nonce>` block removed, linked via `<link>`) — this is mandatory, not
a judgment call. **Low-severity** findings are logged as "remaining tech debt"
in the Stage-3 report, not fixed here, so the pass converges. Browser-verify
before/after (mobile + desktop); commit per surface.

## Stage 3 — Final report + handoff

- Write the phase-5.txt-required **final report**: modified files, architecture
  improvements, reusable components created/consolidated, remaining technical
  debt, future recommendations.
- Rewrite `docs/handoff.md` (completed work + remaining tech debt); archive the
  Profile handoff to `docs/archive/`.

## Verification

- **Per step:** `python -m pytest tests/test_design_system.py tests/test_i18n.py -q`
  after CSS/token changes; **full `pytest -q`** before the PR (target: ≥1115
  passed, 0 fail).
- **Token guard:** `grep -rE "var\(\s*--volt" static/ templates/` → **0 hits**;
  `--volt` alias block absent from `tokens.css`; no raw hex/`rgba()` in in-scope
  surface CSS except new shared tokens defined in `tokens.css`.
- **Visual:** Playwright before/after screenshots per surface, diffed by eye,
  confirming no unintended visual change from the refactor and that each fix
  landed.

## Risks & mitigations

- **CSS cascade/specificity shifts** when tokenizing or relocating rules →
  migrations are same-computed-value (`--volt` aliases) or token-for-literal
  swaps; browser before/after diffing catches regressions; extraction keeps
  selectors byte-identical.
- **Scope creep** (13 surfaces × 8 dimensions) → the audit ranks findings;
  fix **High/Med**, log **Low** as "remaining tech debt" in the report rather
  than chasing every pixel. (Inline-`<style>` extraction is the one blanket
  mandate — all 9 — since it directly serves the single-source-of-truth goal.)
- **CSP** → extracted CSS moves to `<link>`; any remaining inline `<style>`
  keeps its `nonce="{{ csp_nonce }}"`; no JS-injected `<style>`.

## Deliverables

- Stage-0 audit report (`…-final-qa-audit.md`).
- `tokens.css` with `--volt` aliases removed (+ any new shared tokens);
  `coach_widget.css`/`components.css`/`nutrition.css`/`theme.css` fully
  tokenized; per-surface fixes; inline `<style>` extracted where done.
- Final report + rewritten `docs/handoff.md`; Profile handoff archived.
- One PR `feat/phase5-final-qa` → `main`, full suite green.
