# Phase 5 · Surface 3 — Profile (`/edit-profile`) Redesign — Design Spec

Date: 2026-07-08
Branch: `feat/phase5-profile` (off `origin/main` after the Progress surface merged, PR #128 @ `4ff2d8b`)
Phase: AxisAI V2 · Phase 5 (decomposed: Workout ✅ → Progress ✅ → **Profile (this)** → app-wide Final QA)
Source directive: `phase-5.txt` → "Profile — Simplify Profile. Avatar · XP · Membership · Goals · Friends · Club · Premium · Settings · Support · Logout."

## Goal

"Simplify Profile" — turn the dense single-scroll `/edit-profile` page into a clean,
app-like **identity / membership landing** on the canonical AxisAI design system,
with account editing moved behind a **bottom sheet**. Frontend-first: all existing
backend logic and business functionality is preserved; the only backend change is
one **additive** render-context value (`is_premium`) for the Membership card.

## Architecture

- Rewrite `templates/edit_profile.html` on **canonical tokens** + reusable
  components; extract the inline `<style>` → new `static/profile.css`, inline
  `<script>` → new `static/profile.js` (same pattern shipped for Workout/Progress).
- Keep the shell exactly as-is: `theme.css` + `nav.css` links, `{% include "_head.html" %}`,
  `{% set nav_active = 'profile' %}`, `{% include "_nav.html" %}`, `{% include "_actionbar.html" %}`.
- **Reuse the existing `/edit-profile` POST verbatim** (username / full_name / goal /
  target_weight / avatar). No new endpoints, no schema, no migration. Expand-only →
  rollback-safe (per CLAUDE.md A2).
- `data-action` delegation via `static/actions.js`; every page JS function is a
  top-level global; inline `<script nonce="{{ csp_nonce }}">`; no JS-injected `<style>`.

## Global Constraints (bind every task)

- **Canonical tokens only** in `profile.css`: `--color-*` / `--space-*` / `--radius-*` /
  `--text-*` / `--weight-*` / `--duration-*` / `--ease-*` / `--elevation-*`. No `--volt*`,
  no raw hex, no ad-hoc `rgba()` (the rendered HTML must contain no `--volt`).
- **Backend preserved:** the `/edit-profile` POST handler is unchanged. The GET route
  gains exactly one additive kwarg: `is_premium=current_user.is_premium`. No other
  `app/` change. No model/schema/migration.
- **Test-pinned HTML contract** — the rendered `/edit-profile` MUST keep:
  1. `<a href="/friends" class="hub-link…">` and the same `class="hub-link…"` anchors for
     `/feed`, `/leaderboard`, `/quests`, `/supplements`, `/premium`, `/logout`
     (`tests/test_app_shell.py::test_profile_hub_lists_secondary_destinations`).
  2. `data-action="setLang"` present (same test).
  3. The strings `EDIT YOUR INFO`, `Target Weight`, `SAVE`, `Türkçe`, and `["kilo verme"]`
     in the EN render (`tests/test_i18n.py::test_edit_profile_renders_en`) — these live in
     the edit sheet markup (bottom-sheet content is in the DOM even when hidden).
  4. `class="global-header"`, `class="action-bar"`, and no `fx-drawer`
     (`tests/test_app_shell.py::test_all_app_pages_render_shared_shell`).
  5. GET returns 200 and the existing POST field-update / username-validation behavior
     (`tests/test_profile_routes.py`) is unchanged.
- **i18n parity:** every `profile.*` key added exists in both `locales/tr.json` and
  `locales/en.json`. Canonical backend values (goal `kilo verme`/`kas kazanma`, language
  endonyms `Türkçe`/`English`) stay literal; only visible copy is translated.
- **Mobile-first**, ≥44px touch targets, `:focus-visible`, `prefers-reduced-motion` respected.

## Landing Composition (top → bottom)

1. **Identity / XP hero** — a `.card` containing:
   - `avatar-xl` (96px) that is tap-to-upload (hidden file input, pencil-badge overlay,
     preserved base64→POST avatar flow with the existing 2 MB client guard).
   - Full name (or username fallback) + `@handle`.
   - `user_title` · `Level {{ user_level }}` (from the `inject_rank` context processor).
   - **XP progress bar** using `.pbar-track` > `.pbar-fill` at `xp_in_level / xp_for_next`
     (both already injected; currently unused) + a "N / 500 XP" label.
   - 🔥 streak chip (`streak_count`).
   - *Replaces* today's separate avatar-section + stats-row + avatar-rank.

2. **Membership card** — a `.card`, visually prominent but not overwhelming, driven by
   `is_premium` only (presentation-layer; no new fields/logic):
   - **Free** (`is_premium` falsy): heading "Free Plan" (`profile.plan_free`) + a short
     benefit line + a prominent **"Upgrade to Premium"** CTA (`.btn-volt`) linking to
     `/premium`, carrying `data-ga-event="premium_nav_click"`.
   - **Premium** (`is_premium` truthy): heading "Premium Member" (`profile.plan_premium`)
     + a `.badge badge-success` premium badge + a short thank-you/status line
     (`profile.plan_premium_thanks`). **No upgrade CTA.**

3. **Edit Profile** — a full-width `.btn-ghost` "Edit Profile" trigger that opens a
   **bottom sheet** (`.sheet-backdrop` > `.sheet`, `role="dialog"` / `aria-modal`,
   focus-on-open + focus-return + Esc-to-close, mirroring the Progress check-in sheet).
   Sheet body = the existing form, relocated verbatim in behavior:
   - Section title `t('editprofile.edit_info')` → "EDIT YOUR INFO".
   - Full name input, username input (+ `editprofile.username_hint`).
   - Goal cards: loss `data-args='["kilo verme"]'`, gain `data-args='["kas kazanma"]'`
     (canonical values preserved), selected state from `goal`.
   - Target weight input (label "Target Weight") + hint.
   - Language cards: Türkçe / English (`data-action="setLang"` with `["tr"]`/`["en"]`;
     endonyms literal).
   - **Save** button (`common.save` = "SAVE") → `saveProfile()` → existing
     `POST /edit-profile` (JSON), toast + reload on success (existing behavior).

4. **Navigation rows** — the existing `.hub` block (canonical public-API classes in
   nav.css) preserved and regrouped into labelled `.hub-card` groups:
   - **Community:** Friends `/friends`, Feed `/feed`, Club `/leaderboard`, Quests `/quests`.
   - **You:** Pump-Check gallery `/pump-check-gallery`, Premium `/premium`
     (Premium remains here as a complete-nav destination in addition to the Membership card;
     the duplication is intentional and test-pinned).
   - **Settings:** Language TR/EN (`.hub-lang` + `data-action="setLang"`), Logout `/logout`
     (`.hub-link-danger`).
   - A `<!-- SUPPORT-SEAM -->` HTML comment marks where a future Support row slots in
     (Support omitted this phase by product decision; no placeholder destination shipped).

5. **Integrations** — a section of `.card`s for WHOOP + Google Health connect/sync,
   restyled on canonical tokens, reusing the existing `data-provider` markup and the
   existing wearable connect/sync routes + JS behavior (they have no other home).

6. **My Stack** — the read-only active-supplement list (existing `supplements` context),
   restyled compactly, linking to `/supplements`; existing empty-state preserved.

## Data Flow

- **GET `/edit-profile`** (route in `app/blueprints/profile.py`): unchanged except one
  additive kwarg `is_premium=current_user.is_premium`. Still renders `username`,
  `full_name`, `profile_picture`, `goal`, `target_weight`, `streak_count`, `supplements`,
  `icons`, `wearable_connections`. `user_xp` / `user_level` / `user_title` / `xp_in_level`
  / `xp_for_next` arrive via the global `inject_rank` context processor.
- **POST `/edit-profile`**: unchanged. `saveProfile()` in `profile.js` posts the same JSON
  payload (`full_name`, `username`, `goal`, `target_weight`, optional `profile_picture`).
- **Wearables:** `saveProfile()`-adjacent handlers reuse `GET /api/auth/wearable/<p>` (connect
  redirect) and `POST /api/wearables/<p>/sync` (unchanged).
- **Language:** `setLang` (existing global in actions.js) — unchanged.

## Error Handling

- Save: existing toast-on-error / toast-on-success + reload path preserved.
- Avatar: existing >2 MB client guard + server-side validation (via `set_user_avatar`) preserved.
- Wearable sync: existing failure toast preserved.
- Empty supplement stack: existing empty-state ("no supps" + "add first" link) preserved.

## Testing

- **`tests/test_profile_ui.py`** (new render smoke test): asserts structural anchors —
  identity hero, membership card (both `is_premium` states via two users), edit sheet
  (`role="dialog"` + `EDIT YOUR INFO` + `SAVE`), the seven test-pinned `hub-link` hrefs,
  integrations + my-stack sections, `/static/profile.js` + `/static/profile.css` links,
  and `--volt` absent from the rendered HTML.
- **Membership backend/render test:** a Free user renders "Upgrade to Premium" → `/premium`
  and no premium badge; a `is_premium=True` user renders "Premium Member" + the badge and
  **no** upgrade CTA.
- **Regression (must stay green):** `tests/test_app_shell.py`, `tests/test_i18n.py`
  (EN edit-profile), `tests/test_profile_routes.py`, `tests/test_design_system.py`.
- `node --check static/profile.js`; i18n key parity check for new `profile.*` keys.

## Out of Scope / Deferred

- **Support** item — omitted this phase (no help/FAQ/AI-support/contact infra exists);
  the `SUPPORT-SEAM` comment leaves a structural slot so a later phase can add it without
  restructuring.
- **Membership** — presentation only over the existing `is_premium` boolean; no plan tiers,
  billing, expiry, or new fields.
- **Body-measurement / new gamification data** — none introduced.
- Global Polish + Final QA remain their own later Phase 5 cycles.

## Architectural Decisions

1. **Frontend-first, additive-only backend** — one render-context value (`is_premium`);
   POST reused verbatim; no schema/migration. Rollback-safe.
2. **Preserve the `.hub` block as the nav rows** — reusing the canonical public-API hub
   classes keeps all test-pinned secondary destinations and the language toggle intact and
   avoids inventing parallel row components; it is regrouped/restyled, not replaced.
3. **Edit behind a bottom sheet** — the densest content (the form) leaves the landing,
   which is the core "simplify" win; the sheet reuses the Progress check-in sheet's
   a11y posture (focus-on-open/return + Esc; full focus-trap deferred, consistent with
   prior surfaces).
4. **Membership card driven solely by `is_premium`** — visually prominent, Free shows an
   upgrade CTA, Premium shows a badge + thank-you and drops the CTA.
5. **Integrations + My Stack kept on the page** — wearables have no other home; the
   supplement stack is preserved (not removed) to avoid losing a functional surface.
