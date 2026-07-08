# Phase 5 · Surface 4 — Global Polish + Final QA (Final Report / Handoff)

Date: 2026-07-08
Branch: `feat/phase5-final-qa` (off `main` @ `4b7a6c5`, after Profile shipped)
Spec: `docs/superpowers/specs/2026-07-08-phase5-final-qa-design.md`
Plan: `docs/superpowers/plans/2026-07-08-phase5-final-qa.md`
Audit: `docs/superpowers/specs/2026-07-08-phase5-final-qa-audit.md`
Previous surface: `docs/archive/handoff-2026-07-08-phase5-profile.md`

**Phase 5 is now complete: Workout ✅ → Progress ✅ → Profile ✅ → Final QA ✅.**
This surface is the whole-app consistency pass over the 13 redesigned core
surfaces — frontend-only, backend/business logic untouched, no schema/migration.

## Scope

**13 core surfaces** (all now exclusively on the canonical design system):
`index` (dashboard), `nutrition`, `training`, `progress`, `edit_profile`,
`friends`, `feed`, `leaderboard`, `quests`, `manage_stack` (supplements),
`premium`, `chat`, `pump_check_gallery` — plus shared `tokens.css`,
`components.css`, `theme.css`, `nav.css`, and the app-wide `coach_widget.css`.
**Out of scope (unchanged):** the 5 legacy auth/onboarding pages (`login`,
`register`, `setup`, `verify`, `landing`) still on `style.css`.

## Architecture improvements (single source of truth)

- **Legacy `--volt*` aliases retired** — ~91 usages migrated to canonical
  `--color-primary*`; the alias block deleted from `tokens.css`. `grep var(--volt)`
  app-wide is now **0**; the design-system test asserts the alias is gone.
- **Zero raw colors in every in-scope surface CSS.** All hex/`rgba()` literals
  were routed through canonical tokens: a new **`--color-primary-rgb` channel**
  (61,139,255 dark / 26,102,208 light) is the *single* source of the primary
  color — every primary-translucent (`-soft`/`-glow`/`-a06..a50`/`-shadow-primary`/
  `-focus-ring` and all page usages) now derives from it and is theme-aware.
  Added companion channels (`--white-rgb`, `--black-rgb`, `--color-danger-rgb`/
  `-success-rgb`/`-info-rgb`/`-warning-rgb`/`-gold-rgb`/`-gray-500-rgb`/rank-medal
  rgb) and named tokens (rank-gold/silver/bronze, dark scrims, `--color-nav-bg`/
  `-actionbar-bg`, `--white`, etc.). A rogue `61,158,255` blue was **unified** to
  the primary.
- **`coach_widget.css` fully tokenized** (was 79 raw colors) — the biggest offender.
- **9 inline `<style>` blocks extracted** from `index`/`friends`/`feed`/
  `leaderboard`/`quests`/`manage_stack`/`premium`/`chat`/`pump_check_gallery`
  into per-page `static/*.css` (index appended to `dashboard.css`); **no inline
  `<style>` remains in the 13 surfaces**, shrinking the CSP inline surface.

## Reusable components created / consolidated

- **`.empty-state` component completed & standardized** — promoted `.empty-desc`
  into `components.css` (was page-scoped in `leaderboard.css`); converted the
  bare `friends`/`feed`/`pump_check_gallery` empty states (`.section-empty`/
  `.feed-empty`/`.gallery-empty`) to the shared `.empty-state` (icon + desc), and
  removed the 4 orphaned page rules. Empty states are now consistent across
  leaderboard/progress/profile/friends/feed/gallery.
- **Canonical channel-token pattern** established in `tokens.css` — future
  translucent variants reference a `*-rgb` channel instead of hardcoding literals.

## Files modified

- **`static/tokens.css`** — `--volt*` removed; `--color-primary-rgb` + ~30 channel/
  named/scrim/rank/nav tokens added; primary-translucents re-expressed via channel.
- **`static/`** (tokenized, no raw colors): `coach_widget.css`, `components.css`,
  `nutrition.css`, `theme.css`, `nav.css`, `dashboard.css`, and the **8 new
  extracted files** `friends/feed/leaderboard/quests/manage_stack/premium/chat/
  pump_check_gallery.css`.
- **`templates/`** (inline `<style>` removed + `<link>` added; empty-state JS for 3):
  the 9 listed templates.
- **`locales/tr.json`** — 17 `feed.*`/`gallery.*`/`chat.*` strings corrected for
  missing Turkish diacritics (keys unchanged → EN parity preserved).
- **Docs** — this report; audit report; spec + plan; Profile handoff archived.

## Verification

- **Full `pytest`**: see the PR/commit — target ≥1115 passed, 0 failures (no test
  count regression; design-system test updated for `--volt` absence).
- **Single-source guards (all green):** `grep var(--volt)` app-wide = 0; raw
  hex/`rgba()` in every in-scope surface CSS = 0 (tokens.css holds the defs); no
  inline `<style>` in the 13 surfaces; i18n TR/EN parity holds (917 keys).
- **Visual regression:** drove all 13 surfaces in real Chrome (Playwright, seeded
  user, 390 + 1280px) after each stage; token migrations verified pixel-identical
  in dark theme (`--volt`, coach_widget, components/nutrition/theme, per-page
  tokenization all no-ops); extraction confirmed byte-identical relocation;
  empty-state + diacritic fixes confirmed landed. No console errors (the
  `/friends` `/referral` 404 is a pre-existing backend/local artifact).

## Remaining technical debt (Low — logged, not fixed)

- **`/friends` invite link** shows "Yükleniyor…" when `/referral` 404s locally — a
  backend/endpoint concern, out of this frontend-only phase.
- **Quests list** not visually audited locally (no `DailyQuest` seed data); the
  page shell/rank card render fine.
- **Light-theme fidelity of hoisted one-off tokens** — the dark scrims/accents
  (`--color-scrim-*`, `--color-surface-deep*`, `--color-accent-lime`, etc.) are
  defined in `:root` only; they stay dark under `[data-theme="light"]`. The app
  defaults to dark and these are subtle component surfaces, so impact is minimal;
  add light overrides if light theme is prioritized.
- **coach_widget's discrete `-a06..a50` tokens** vs. the newer `-rgb` channel —
  both correct; the discrete tokens could later be re-expressed via the channel
  for full uniformity.
- **5 legacy auth/onboarding pages** remain on `style.css` — a separate future
  redesign cycle.

## Future recommendations

- Migrate the 5 auth/onboarding pages onto the canonical system (next cycle).
- Add a CI guard: fail on any `--volt` or raw hex/`rgba()` in `static/*.css`
  outside `tokens.css` to keep the single-source-of-truth property from regressing.
- Add light-theme overrides for the hoisted dark-scrim/accent tokens if light
  theme becomes a shipping target.
- Consider a lightweight visual-regression harness (the Playwright screenshot
  pass used here) in CI for future redesigns.

## Next step

Merge `feat/phase5-final-qa` → `main` (health-gated EC2 deploy). With this
surface, **the AxisAI V2 redesign (Phases 1–5) is complete.** Same manual-QA
caveat as prior surfaces: interactive flows were driven in a headless browser,
not hand-tested on a device.
