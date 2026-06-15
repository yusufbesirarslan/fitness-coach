# FitX — Remaining Work (deferred from the 2026-06-14 frontend review)

This file tracks the review findings that were **intentionally not** implemented in the
first pass (branch `claude/pr44-frontend-review-md-l757gg`). They were deferred because
they are large refactors, touch production data, or need a real device / live app to
verify safely. Source backlog: [`fitx-things-to-fix.md`](fitx-things-to-fix.md).

Status legend: 🔴 P0 · 🟠 P1 · 🟡 P2 · ⭐ root cause.

---

## ⭐ Shared base template (`_base.html`)
**Why deferred:** migrating all ~16 templates onto one Jinja layout is the highest-leverage
change but also the riskiest to land without rendering every page live. It underpins several
items below (site-wide GA, favicon, fonts, nav).
**Plan:** create `templates/_base.html` with `{% block head %}`, `{% block nav %}`,
`{% block content %}`, `{% block scripts %}`; move brand mark, fonts, favicon, meta tags and
GA into it; convert pages one at a time, diffing each rendered output.

## 🔴 Unify to ONE navigation shell
**What:** most pages use the v3 hybrid shell (header + bottom action-bar + drawer), but
`/chat` and `/edit-profile` still use the legacy `aside.sidebar`.
**Why deferred:** converting those two pages and deleting `.sidebar*` needs visual QA on
desktop + mobile. The brand fix (FC → FITX) already removed the most jarring symptom.
**Plan:** make v3 canonical, convert `chat.html` + `edit_profile.html`, delete dead
`.sidebar*` rules from `static/nav.css`.

## 🔴 Clean test/seed accounts out of production
**What:** leaderboard shows a user literally named `test`; friends list is sparse.
**Why deferred:** this is **production data**, not code — I can't (and shouldn't) mutate the
prod DB from here.
**Plan (owner action):** remove/anonymize the `test` account in prod; optionally gate the
global leaderboard behind a minimum cohort size or switch to relative framing
("ilk %20'desin") until density grows.

## 🟠 Public landing page
**What:** every route except `/health` is gated; first-time visitors hit a bare login.
**Why deferred:** net-new marketing page + public route + redirect logic for authed users;
better designed deliberately than rushed.
**Plan:** `templates/landing.html` + public route (hero, 3–4 feature blocks with
screenshots, the social hook, single "Ücretsiz Başla" CTA); redirect logged-in users to the
dashboard.

## 🟠 Site-wide GA + funnel events  *(partially done)*
**Done:** GA tag added to login / register / setup / index.
**Remaining:** move GA into `_base.html` so it fires on every page, and add custom funnel
events (`register_submit/success`, `setup_step_n`, `first_meal_logged`,
`first_plan_generated`); define "activation".

## 🟠 Avatar: stop inlining ~245 KB base64 on every page
**What:** `profile_picture` is `db.Text`, rendered inline into every HTML response
(uncacheable).
**Why deferred:** requires wiring uploads through `s3_helper.py`, a model/storage change, and
a migration/backfill of existing avatars.
**Plan:** store avatars in S3, render a cacheable `<img>` URL with `loading="lazy"` + fixed
width/height; keep the 500 KB validator as an upload guard.

## 🟠 Never-idle pages (battery / CPU / tooling)
**What:** tip carousel `setInterval(…,8000)`, the `ins-pbar` loop, and Chart.js/sparkline
canvases keep pages from reaching document-idle.
**Why deferred:** needs careful pause/resume wiring and live profiling to confirm idle.
**Plan:** pause animation/polling on `document.hidden` + `prefers-reduced-motion`, clear
intervals when off-screen, ensure Chart.js animations settle.

## 🟠 Value framing before `/setup` body-metric ask
**What:** `/setup` opens straight into weight/height/age.
**Plan:** prepend a 1-screen "3 dakikada sana özel AI plan" value step + sample-plan preview
before collecting metrics; pre-fill or redirect already-onboarded users (don't re-open an
empty wizard that can overwrite an existing profile/plan).

## 🟠 Surface the viral hook + referral/invite loop
**What:** friend meal/workout suggestions live only inside `/chat/<user>` behind a 1-friend
list; no invite funnel for non-users.
**Plan:** surface "Bir arkadaşına plan öner" on the dashboard + after plan generation; add a
shareable invite link that rewards both sides and ties into the "Help a Friend" quest.

## 🟠 Deeper gamification economy
**What:** only 4 daily quests, auto-granted (no explicit "claim" moment).
**Done already:** quest names localized; unique icons per quest.
**Remaining:** rotating quest set, explicit "Ödülü Topla" claim with animation, weekly
milestones.

## 🟠 Self-host / preconnect fonts
**What:** render-blocking third-party Google Fonts, no `preconnect`, inconsistent weights.
**Plan:** `preconnect` + self-hosted woff2 (`font-display:swap`) + unified weight set in
`_base.html`.

## 🟠 Reconcile dashboard vs check-in weight source
**What:** dashboard shows 76.1 kg while the check-in placeholder pre-fills 78.5 kg.
**Why deferred:** needs to trace which model/field each surface reads and pick a single
source of truth (likely the latest `WeeklyCheckIn` / `WeeklyLog`).

## 🟠 Monetization plan (instrument now, build later)
**Plan:** define a freemium line (free: tracking/quests/1 AI plan per week; premium:
unlimited re-plans, advanced analytics, custom macros); add a non-blocking "Premium'a Geç"
entry and GA upgrade-intent events before building billing.

## 🟡 Polish & consistency (P2 batch)
- Consolidate button/tab/input variants into one component set; retire ad-hoc blue/orange.
- Move remaining hardcoded colors (ring/trend/chat bubbles/macros) to CSS tokens; add
  semantic tokens (`--accent-chat`, `--status-streak`); reserve red for errors/over-target.
- Retire the second stylesheet system (`static/style.css` for auth) into the token system.
- Delete dead CSS (`.glass*`, force-hidden `.sidebar`/`.bottom-nav`, unused `@keyframes`).
- Desktop breakpoint (≥1024px) to rebalance the bento and remove whitespace gaps.
- Collapse the 3 meal-logging paths (HIZLI EKLE / MANUEL EKLE / Günlük) into one primary flow.
- Chat clutter: suppress the duplicate "Önerini kabul ettim" echo; fix message ordering.
- Tip-carousel clip mid-transition; leaderboard loading flash → skeleton/server-render.
- Icon-only controls: add `aria-label`; drawer `role="dialog"` + focus trap; tablist roles.
- Outcome-led microcopy / aspirational tagline.

---

## 🔎 Verify on a real device (couldn't confirm live)
- Mobile rendering (the automated capture clamped min-width).
- Deep form submits end-to-end (meal log, AI plan generation, menu scan) — were blocked by
  the never-idle pages during the walkthrough.
