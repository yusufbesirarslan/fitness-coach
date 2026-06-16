# FitX — Remaining Work (deferred from the 2026-06-14 frontend review)

This file tracks the review findings that were **intentionally not** implemented in the
first pass (branch `claude/pr44-frontend-review-md-l757gg`). They were deferred because
they are large refactors, touch production data, or need a real device / live app to
verify safely. Source backlog: [`fitx-things-to-fix.md`](fitx-things-to-fix.md).

Status legend: 🔴 P0 · 🟠 P1 · 🟡 P2 · ⭐ root cause · ✅ done.

> **2026-06-15 update (branch `claude/fitx-remaining-work-6xe42v`):** most code-feasible
> items below are now implemented. New shared head (`templates/_head.html`) + nav partials
> (`_nav.html`, `_actionbar.html`); site-wide GA + funnel events (`static/analytics.js`);
> public landing (`/welcome`); referral/invite loop (`/davet/<kod>`, `/referral`); premium
> entry (`/premium`); setup guard + value framing; weight single-source; never-idle
> pause/resume; gamification quest enrichment. Tests: `tests/test_remaining_work.py`.
> Items needing prod data or a live device remain owner actions (see ⚠️ below).

---

## ✅ Shared head + nav partials (was ⭐ `_base.html`)
**Done:** instead of one monolithic `{% extends %}` base (risky to land without live diffing
all 16 pages), the shared surface was factored into includes every page pulls in:
`templates/_head.html` (charset/viewport, favicon, **preconnect + unified fonts**, **site-wide
GA**, funnel helper) and `templates/_nav.html` + `templates/_actionbar.html` (the v3 shell).
All 13 user-facing pages now `{% include "_head.html" %}`; favicon + GA are therefore on every
page. A `tests/test_remaining_work.py::test_templates_compile` guards Jinja health and
`test_all_main_pages_render` renders every page logged-in.
**Remaining (optional):** converting the per-page inline nav blocks (index/nutrition/… still
inline the identical shell) to the `_nav.html`/`_actionbar.html` includes — purely a DRY
cleanup, functionally identical; the two converted pages (chat, edit_profile) + new pages
(landing, premium) already use the includes.

## ✅ Unify to ONE navigation shell
**Done:** `/chat` and `/edit-profile` converted from the legacy `aside.sidebar` to the v3 shell
(now via the shared `_nav.html` + `_actionbar.html` includes). Dead `.sidebar*`, `.bottom-nav*`,
`.bn-item*` and `.glass*` rules removed from `static/theme.css`; the force-hide overrides removed
from `static/nav.css`; focus-visible selectors repointed to `.drawer-link`/`.ab-tab`.

## 🔴 ✅/⚠️ Clean test/seed accounts out of production
**Done (tooling):** added a FK-safe Flask CLI command to purge leftover test/seed accounts and
all their dependent rows (and refresh the leaderboard sorted sets):

```
flask --app starter cleanup-test-users               # dry run — lists matches only
flask --app starter cleanup-test-users --yes         # actually delete (default test/seed pattern)
flask --app starter cleanup-test-users --username test --yes   # target one account
```

Default match is a deliberately narrow pattern (`test`/`testuser`/`seed`/`demo`/`dummy`/`deneme`…)
so real users aren't caught; `--pattern <regex>` overrides. Covered by tests in
`test_remaining_work.py`.
**⚠️ Owner action:** this still has to be **run against prod** (EC2) by the owner — the sandbox
has no prod DB access. Optionally still worth gating the global leaderboard behind a minimum
cohort size until density grows.

## ✅ Public landing page
**Done:** `templates/landing.html` + public `GET /welcome` (`app/blueprints/pages.py`) — hero
("3 dakikada sana özel AI plan"), 4 feature blocks, social/invite hook, single "Ücretsiz Başla"
CTA. Logged-in users are redirected to the dashboard. (Login still stays at `/login` so an
expired session lands on the form, not marketing.)

## ✅ Site-wide GA + funnel events
**Done:** GA moved into `_head.html` → fires on **every** page. `static/analytics.js` adds the
funnel helpers (`fxTrack`/`fxTrackOnce`/`fxActivation`) and a declarative `data-ga-event` hook.
Events wired: `register_submit`/`register_success`, `setup_step_0..3`, `first_meal_logged`,
`first_plan_generated`, `upgrade_intent`, `invite_copy`, landing CTA clicks. "Activation" = the
once-only `activation` event fired on first meal **or** first plan.

## ✅ Avatar: stop inlining ~245 KB base64 on every page
**Done (now that the S3 bucket exists):** new `User.profile_picture_key` column + `User.avatar_src`
resolver; `app/services/avatars.py::set_user_avatar` uploads the avatar to S3 (prefix `avatars/`,
via `s3_helper.upload_image`) and stores only the object key, clearing the base64 from the column
so HTML responses no longer carry ~245 KB. Display sites (dashboard, drawer/header, friends,
leaderboard, chat, premium, edit-profile, JSON serializers) all read `avatar_src`, which returns a
short-lived pre-signed URL; list avatars render with `loading="lazy" decoding="async"`. The 500 KB
+ real-image validator is kept as the upload guard. **Graceful fallback:** if S3 is disabled
(local without bucket, tests) or an upload fails, it transparently falls back to the old base64
column — so nothing breaks where S3 isn't configured. Covered by `test_remaining_work.py`.
**Remaining (optional):** a one-off backfill to migrate *existing* base64 avatars in prod to S3
(new/changed avatars migrate automatically on next save); pre-signed URLs aren't long-cacheable —
CloudFront/public-read would improve that later.

## ✅ Never-idle pages (battery / CPU)  *(mostly done)*
**Done:** the dashboard tip carousel + `ins-pbar` now pause on `document.hidden` and skip
auto-advance under `prefers-reduced-motion`; the leaderboard countdown and the chat 5 s
message poll pause when the tab is hidden and resume on `visibilitychange`.
**Remaining:** live profiling to confirm true document-idle; Chart.js animations settle on
their own (one-shot, no persistent timer) so were left as-is.

## ✅ Value framing before `/setup` body-metric ask
**Done:** setup header reframed to the value prop ("3 dakikada sana özel AI plan…"); `setup_step_n`
funnel events added. **Onboarded-user guard:** `GET /setup` now redirects a completed profile to
the dashboard (so the empty wizard can't overwrite an existing profile/plan); `?yeniden=1` allows
a deliberate redo.
**Remaining (optional):** a dedicated sample-plan preview screen before the first metric ask.

## ✅ Surface the viral hook + referral/invite loop
**Done:** every user gets a unique `referral_code`; shareable link `GET /davet/<kod>` sets a
`fitx_ref` cookie and routes to register; on signup the inviter↔invitee are linked and **both get
+75 XP** (`app/services/referral.py`), tying into the new "Bir Arkadaşını Davet Et" quest. The
friends page shows a copyable invite card (`GET /referral`) with a referred-count.
**Remaining (optional):** also surface the invite card on the dashboard + right after plan
generation.

## ✅ Deeper gamification economy  *(partially done)*
**Done already:** quest names localized; unique icons per quest.
**Done now:** quest set enriched/rotated — added `meal_logged`, `water_logged`, `checkin_done`
and `friend_invited` daily quests (seeded idempotently in `app/db_init.py`).
**Remaining:** explicit "Ödülü Topla" claim flow (the current model awards XP immediately on
completion; splitting completion→claim touches login/meal/workout flows + immediate-XP toasts and
the leaderboard, so it was left for a focused change) and weekly milestones.

## ✅ Self-host / preconnect fonts  *(preconnect done)*
**Done:** `preconnect` to `fonts.googleapis.com` + `fonts.gstatic.com` and a single unified weight
set, both centralized in `_head.html` (every page).
**Remaining:** self-hosting the woff2 files (`font-display:swap`) to drop the third-party hop.

## ✅ Reconcile dashboard vs check-in weight source
**Done:** single source of truth = `current_user.weight` (falling back to the latest
`WeeklyCheckIn`). `progress_page` passes `current_weight`; the check-in form pre-fills it (the
hardcoded `78.5` placeholder is gone) so it matches the dashboard. Covered by
`test_progress_prefills_current_weight`.

## ✅ Monetization plan (instrument now, build later)
**Done:** freemium line defined (`FREEMIUM` in `app/blueprints/pages.py`): free = tracking/quests/
1 AI plan per week; premium = unlimited re-plans, advanced analytics, custom macros. Non-blocking
"Premium'a Geç" entry (`/premium` page + drawer link) fires the `upgrade_intent` GA event; no
billing yet. `is_premium`/`premium_since` columns added for later.

## 🟡 Polish & consistency (P2 batch)  *(partially done)*
- ✅ Deleted dead CSS (`.glass*`, force-hidden `.sidebar`/`.bottom-nav`/`.bn-item*`) from
  `theme.css`/`nav.css`.
- ✅ Icon-only controls: `aria-label` on the drawer trigger + header avatar; drawer
  `role="dialog"` + `aria-modal`; decorative SVGs marked `aria-hidden` (in `_nav.html`/`_actionbar.html`).
- ⏳ Consolidate button/tab/input variants into one component set; retire ad-hoc blue/orange.
- ⏳ Move remaining hardcoded colors (ring/trend/chat bubbles/macros) to CSS tokens; add
  semantic tokens (`--accent-chat`, `--status-streak`); reserve red for errors/over-target.
- ⏳ Retire the second stylesheet system (`static/style.css` for auth) into the token system.
- ⏳ Desktop breakpoint (≥1024px) to rebalance the bento and remove whitespace gaps.
- ⏳ Collapse the 3 meal-logging paths (HIZLI EKLE / MANUEL EKLE / Günlük) into one primary flow.
- ⏳ Chat clutter: suppress the duplicate "Önerini kabul ettim" echo; fix message ordering.
- ⏳ Tip-carousel clip mid-transition; leaderboard loading flash → skeleton/server-render.
- ⏳ Drawer focus trap; tablist roles.
- ⏳ Outcome-led microcopy / aspirational tagline.

---

## 🔎 ⚠️ Verify on a real device (couldn't confirm live — owner action)
The code changes above are validated by the pytest suite (`tests/test_remaining_work.py` renders
every page and asserts the shared head/nav resolved) but **not** by live rendering. Still worth a
manual pass:
- Mobile rendering of the converted `chat` / `edit_profile` pages on the v3 shell.
- Deep form submits end-to-end (meal log, AI plan generation, menu scan).
- The new landing / premium / invite flows on a real browser.
