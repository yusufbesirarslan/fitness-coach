# Phase 5 Handoff — AxisAI V2 Profile Redesign (Surface 3)

Date: 2026-07-08
Branch: `feat/phase5-profile` (off `main` @ 4ff2d8b, after the Progress surface merged)
Spec: `docs/superpowers/specs/2026-07-08-phase5-profile-redesign-design.md`
Plan: `docs/superpowers/plans/2026-07-08-phase5-profile-redesign.md`
Previous surface: `docs/archive/handoff-2026-07-08-phase5-progress.md`

Phase 5 is decomposed into independent surface cycles — **Workout ✅ →
Progress ✅ → Profile (this) → app-wide FINAL QA.** This handoff covers surface 3:
Profile (`/edit-profile`).

## Completed Work

`/edit-profile` is rebuilt from a settings form into a clean, app-like
**identity + membership landing** on the canonical AxisAI design system, with
account editing moved behind a bottom sheet — **frontend-first, backend
preserved.** The only backend change is one additive GET render-context kwarg
(`is_premium`); the `POST /edit-profile` handler is byte-for-byte unchanged.
**Zero schema/migration** (expand-only, rollback-safe).

**Page structure (top → bottom, all on canonical tokens):**
- **Identity / XP hero** (`.pf-hero` nested in `.pf-card`) — avatar (upload
  overlay, letter fallback), name/`@handle`, rank + level, XP progress bar
  (reuses `.pbar-track`/`.pbar-fill`), streak pill.
- **Membership card** (`.pf-membership`) — presentation-only over the existing
  `is_premium` boolean: free → plan name + `btn-volt pf-upgrade` CTA to
  `/premium`; premium → `badge badge-success` "PREMIUM" + thanks copy, no CTA.
- **Edit-profile trigger** — `btn-ghost` opening the bottom sheet.
- **Preserved `.hub` nav rows** — Community (friends/feed/leaderboard/quests),
  You (gallery/supplements/premium), Settings (language TR/EN + logout). All
  test-pinned `href`s and `data-action="setLang"` kept verbatim.
- **Integrations** — WHOOP + Google Health wearable cards (connect/sync,
  status, last-sync), reused from the current template's provider loop.
- **My Stack** — read-only supplement list (icons, ratings, review) + empty
  state, linking out to `/supplements`.
- **Edit sheet** (`role="dialog"`) — full name, username, fitness goal
  (`["kilo verme"]`/`["kas kazanma"]` canonical args), target weight, language;
  `saveProfile()` POSTs to the unchanged `/edit-profile`.

## Files Modified

- **Created:** `static/profile.css` (119 L, canonical tokens only),
  `static/profile.js` (page JS), `tests/test_profile_ui.py` (4 render/membership
  tests), the spec + plan docs.
- **Rewritten:** `templates/edit_profile.html` (517 → ~204 L; inline
  `<style>`/`<script>` removed, moved to the new `.css`/`.js`).
- **Backend:** `app/blueprints/profile.py` (+1 line: `is_premium=bool(
  current_user.is_premium)` on the GET `render_template` call; POST untouched).
- **i18n:** `locales/{tr,en}.json` (+16 `profile.*` keys each, parity).
- **Docs:** `docs/handoff.md` (this); Phase 5 Progress handoff archived.

## Components Created or Refactored

- **New page CSS (canonical tokens):** `.pf-wrap`, `.pf-card`, `.pf-hero`,
  `.pf-avatar`(+overlay), `.pf-name`/`.pf-handle`/`.pf-rank`, `.pf-xp`(+label),
  `.pf-streak`, `.pf-membership`/`.pf-plan-*`, `.pf-edit-btn`, `.pf-sheet-body`/
  `.pf-field`/`.pf-input`/`.pf-hint`, `.pf-cards`/`.pf-choice`(+`.selected`),
  `.pf-section-title`, `.pf-integrations`/`.pf-wear-*`, `.pf-stack`/
  `.pf-stack-*`, `.pf-link`.
- **Reused:** `.card` chrome, `.badge`/`.badge-success`/`.badge-warning`,
  `.pbar-track`/`.pbar-fill`, `.hub`/`.hub-link`/`.hub-card`/`.hub-lang`,
  `.sheet`/`.sheet-backdrop`/`.sheet-title`, `.btn-volt`/`.btn-ghost`, `.toast`.
- **New JS (top-level globals for `data-action` + listeners):** `toast`,
  `selectGoal`, `updateAvatarLetter`, `openEditSheet`/`closeEditSheet` (focus-on-
  open + return-to-opener + Esc), `saveProfile`, plus the avatar-`change` /
  wearable-connect / wearable-sync `DOMContentLoaded` wiring — logic moved
  verbatim from the old template.

## Architectural Decisions

1. **Frontend-first, additive backend** — one additive GET kwarg
   (`is_premium`); `POST /edit-profile` unchanged; no schema/migration.
   Rollback-safe (expand-only).
2. **`.hub` nav rows preserved** — the friends/feed/leaderboard/quests/
   supplements/premium/logout/setLang destinations are test-pinned; kept as-is
   (the `/premium` hub-link row is intentionally retained *in addition to* the
   membership card).
3. **Edit-behind-a-sheet** — the account form moved into a `role="dialog"`
   bottom sheet, mirroring the Progress check-in sheet's a11y posture (focus-on-
   open, return-to-opener, Esc-to-close).
4. **Membership presentation-only** — a view over the existing `is_premium`
   boolean; no plan tiers, billing, expiry, or new fields/models. A
   `SUPPORT-SEAM` comment marks where a future Support row slots in.
5. **Integrations + stack kept** — the wearable provider loop and read-only
   supplement stack were preserved (class-renamed to `pf-*`), not dropped.

## Verification

- Full `pytest`: **1115 passed, 0 failures** (incl. 4 new `test_profile_ui.py`:
  structural anchors, hub destinations, membership free→CTA, premium→badge/no-
  CTA). All 2682 warnings are pre-existing `datetime.utcnow()` deprecations.
- **Token cleanliness:** `grep -nE "\-\-volt|#[0-9a-fA-F]{3,6}|rgba\("
  static/profile.css` → `CSS CLEAN`; rendered `/edit-profile` contains no
  `--volt`.
- **i18n parity:** `set(tr) == set(en)` holds (`PARITY OK`); 16 `profile.*`
  keys in both locales.
- **JS:** `node --check static/profile.js` clean.
- **A11y:** avatar `role="button"`+`tabindex="0"`+`aria-label`; edit sheet
  `role="dialog"`+`aria-modal`+`aria-labelledby`; `:focus-visible`+`--focus-
  ring`; `prefers-reduced-motion` block; ≥44px targets.
- Backend read-only-ish: `git diff` shows only `app/blueprints/profile.py` in
  `app/`, a single additive kwarg.
- **Not yet done — manual browser QA:** the interactive flows (avatar upload
  preview, edit-sheet open/save/Esc, goal/lang selection, wearable connect/
  sync, membership free vs premium, responsive widths, EN locale) were verified
  via render/membership tests + `node --check` only, not driven in a browser.

## Remaining Tasks / Known Issues

- **Manual/live QA recommended** before/after deploy: drive the edit sheet
  (open → change fields → save → reload), avatar upload preview + 2 MB guard,
  goal/language selection, wearable connect/sync, membership card in both
  `is_premium` states, empty supplement stack, EN locale, and 360/768/1024px
  widths.
- **No full focus-trap** in the edit sheet (has focus-on-open/return + Esc);
  same posture as the Progress check-in sheet and Workout session player — a
  follow-up if wanted app-wide.
- Membership copy is presentation-only; if real plan tiers/billing ever land,
  the card is the seam to extend (no tiers today, by design).

## Next Recommended Steps

1. Live-QA the Profile flows on a running app.
2. Merge `feat/phase5-profile` → `main` (health-gated EC2 deploy).
3. Start **Phase 5 surface 4: app-wide FINAL QA** — the last of the four
   Phase 5 surfaces (Workout → Progress → Profile → FINAL QA): a cross-surface
   pass (shared shell, nav, i18n parity, tokens, responsive/a11y) over the
   redesigned pages before closing out Phase 5.

## Quality Review

- **Responsiveness:** Strong (`.pf-integrations`/`.pf-stack` collapse to 1
  column ≤640px; `.pf-wrap` max-width 600px centered; sheet full-width).
  *Widths not visually verified — manual QA.*
- **Accessibility:** Good. Avatar button keyboard-reachable + labelled; sheet
  `role="dialog"`/`aria-modal`/`aria-labelledby` + focus-on-open/return + Esc;
  `:focus-visible` ring; ≥44px targets; `prefers-reduced-motion`. *Weak spot:*
  no full focus-trap in the sheet.
- **Visual consistency:** Strong. Whole surface on canonical tokens; no
  `--volt`; reuses `.card`/`.badge`/`.pbar`/`.hub`/`.sheet`/`.btn-*`.
- **Code maintainability:** Strong. Page JS isolated in `profile.js`; single-
  purpose top-level functions; template is markup-only (no inline style/script).
- **Reusability:** Strong. Membership card, hero, and nav rows are token-based;
  `.hub`/`.sheet`/`.badge` reused rather than re-implemented.
- **Performance:** Strong. Static page, one additive boolean in render context,
  no new queries/AI/blocking work.
- **UX clarity:** Strong. Identity + membership up top; editing one tap away in
  a sheet; integrations and stack below. *Minor:* membership copy is a product
  read-through candidate.
