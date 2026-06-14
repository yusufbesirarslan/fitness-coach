# FitX — Things To Look At (from the 3-persona review)

Consolidated, deduplicated action list distilled from the three sub-agent reviews on
2026-06-14. Full verbatim reviews: [`persona-reviews-2026-06-14.md`](persona-reviews-2026-06-14.md).
Evidence: [`live-walkthrough-notes.md`](live-walkthrough-notes.md).

**How to read:** each item has `Flagged by` (🎨 UI/UX dev · 📈 Marketing · 🙂 Customer),
the screen/`file:line`, why it matters, and the fix. Severity: 🔴 P0 (broken / breaks
trust) · 🟠 P1 (high impact) · 🟡 P2 (polish). Tick the box when done.

---

## ⭐ Root cause to fix first (unlocks many others)
> 🎨 **There is no shared Jinja base template.** All ~16 templates re-declare their own
> `<head>`, fonts, stylesheet order, header and nav. This is *why* there are two nav
> shells, three brand names, two stylesheets, copy-pasted heads, and a stray backslash
> in a URL — every fix has to be made 16 times, so it drifts forever.

- [ ] **Create `templates/_base.html`** with `{% block head %}`, `{% block nav %}`,
  `{% block content %}`, `{% block scripts %}`, and migrate every page to `{% extends %}`.
  Brand mark, fonts, favicon, meta tags, GA, and nav then live in ONE place. — 🎨

---

## 🔴 P0 — Broken / trust-breaking (do first)

- [ ] **`/progress` returns raw JSON `[]` in the browser.** The UI is at `/progress-page`;
  the bare `/progress` route is the weight-log API. — Where: `app/blueprints/tracking.py:75`
  (API) vs `:305` (page). — Why: a guessed/bookmarked/shared link shows raw code, looks
  broken. — Fix: rename API → `/api/progress`; redirect `/progress` → the page.
  — 🎨 📈 🙂

- [ ] **Two different navigation shells in one app.** Most pages = mobile top-bar + bottom
  tab bar; `/chat` and `/edit-profile` = desktop left sidebar. — Where: `templates/chat.html:179`,
  `templates/edit_profile.html:184` (legacy `.sidebar`, already force-hidden elsewhere by
  `static/nav.css:250 !important`). — Why: feels like two apps; most jarring inconsistency.
  — Fix: make the v3 hybrid shell canonical, convert those two pages, delete `.sidebar*`. — 🎨 📈 🙂

- [ ] **Three brand names: FITX / FC / FITNESS COACH.** — Where: `index.html:28` (FITX),
  `chat.html:180` & `edit_profile.html:185` (FC), `login.html:70` & `register.html:75`
  (FITNESS COACH). — Why: destroys recall/recommendation/branded search; reads as a prototype.
  — Fix: standardize on **FitX** everywhere, including `<title>` tags. — 🎨 📈 🙂

- [ ] **AI training plan prescribes 7 days/week with zero rest days.** Cardio days are just
  "Bisiklet" with no detail. — Where: `/training` (plan generator service). — Why: visibly
  unsafe advice on the product's core promise → distrust + liability; poisons word-of-mouth.
  — Fix: constrain generator to ≥1 rest/active-recovery day, expand cardio detail, add a
  "not medical advice" disclaimer. — 🎨(content) 📈 🙂

- [ ] **Dead/empty network with a "test" account in production.** Leaderboard shows 2 users
  (one literally "test"); friends list has 1. — Where: `/leaderboard`, `/friends`. — Why:
  signals an abandoned product; kills the competitive/social loop. — Fix: clean test data
  from prod; gate leaderboard behind a min cohort or show relative framing ("ilk %20'desin");
  drive density with a referral loop. — 📈 🙂

---

## 🟠 P1 — High impact

### Acquisition & measurement
- [ ] **No public landing page** — every route but `/health` is gated; visitors see only a
  bare login with zero value prop. — Where: `templates/login.html`. — Why: caps top-of-funnel
  near zero; nothing to share/index. — Fix: public logged-out landing (hero + 3–4 feature
  blocks w/ screenshots + the social hook + one "Ücretsiz Başla" CTA); redirect authed users
  to the dashboard. — 🎨 📈
- [ ] **Analytics only fires on the dashboard** — GA `G-YXSGLN7C7Y` is in `index.html` only;
  not on login/register/setup. — Why: the signup→activation funnel is unmeasurable. — Fix:
  GA in `_base.html`; events for `register_submit/success`, `setup_step_n`, `first_meal_logged`,
  `first_plan_generated`; define "activation". — 📈
- [ ] **No favicon / meta description / Open-Graph / Twitter cards** in any template. — Why:
  default tab icon, poor SEO, ugly link unfurls (kills the social feature's virality). — Fix:
  add favicon + `<meta name="description">` + OG/Twitter tags + branded share image in `_base.html`.
  — 🎨 📈

### Activation & retention
- [ ] **`/setup` asks for body data before showing value.** — Where: `templates/setup.html:250`.
  — Why: data ask before payoff increases drop-off at the most fragile moment. — Fix: prepend a
  1-screen value step ("3 dakikada sana özel AI plan") + sample-plan preview, then collect metrics.
  — 📈 🙂
- [ ] **`/setup` re-opens an empty wizard for already-onboarded users** and can overwrite the
  existing profile/plan if submitted. — Where: `/setup`. — Fix: pre-fill from profile or redirect
  onboarded users to dashboard/edit-profile. — 📈
- [ ] **The viral hook is buried** — meal/workout suggestions live only inside `/chat/<user>`
  behind a 1-friend list. — Why: the most differentiated, shareable mechanic is invisible. — Fix:
  surface "Bir arkadaşına plan öner" on the dashboard + after plan generation; add an invite flow.
  — 📈
- [ ] **No referral / invite loop** — the cheapest acquisition channel is unused. — Fix:
  shareable invite link rewarding both sides; tie to the "Help a Friend" quest. — 📈
- [ ] **Shallow gamification** — only 4 daily quests, auto-granted (no "claim" moment), two share
  the 🤝 emoji. — Where: seeded in `app/db_init.py:100-108`, `app/cli.py:12-14`; `templates/quests.html`.
  — Fix: rotating quest set, explicit "Ödülü Topla" claim w/ animation, weekly milestones, unique icons.
  — 📈 🎨 🙂

### Performance / front-end health
- [ ] **~245 KB base64 avatar inlined into HTML on every page** (no caching). — Where:
  `app/models.py:31` (`profile_picture` = `db.Text`), rendered in headers
  (`leaderboard.html:165`, `manage_stack.html:110`); cap 500KB at `validators.py:69`. — Fix:
  store in S3 (`s3_helper.py` already exists) and render a cacheable `<img>` URL with
  `loading="lazy"` + fixed width/height. — 🎨 📈
- [ ] **Pages never reach "document idle"** (battery/CPU; blocks tooling). — Where: tip carousel
  `setInterval(...,8000)` `index.html:673` + `ins-pbar` 8s loop `dashboard.css:360`; Chart.js +
  sparkline canvases. — Fix: pause on `document.hidden`/`prefers-reduced-motion`, clear interval
  when off-screen, ensure Chart.js animation settles. — 🎨 📈
- [ ] **Duplicate `/nutrition-plan/active` fetch on load.** — Where: `static/nutrition.js:470` &
  `:542`, called back-to-back at `:1282–1283`; refetched on tab switch `:45`. — Fix: fetch once,
  cache in a module var, share to both renderers. — 🎨 📈
- [ ] **Render-blocking third-party Google Fonts, no preconnect, inconsistent weights.** — Where:
  per-page `<link>` (`index.html:15`, `chat.html:7`, `404.html:6`). — Fix: `preconnect` +
  self-host woff2 (`font-display:swap`) + unify weights in `_base.html`. — 🎨 📈

### Localization & trust
- [ ] **Mixed Turkish/English UI** — English quest names with Turkish descriptions; "SUPPLEMENT
  STACK" / nav "Supplements"; status labels English. — Where: `app/db_init.py:100-108`,
  `app/cli.py:12-14`, `templates/supplements.html`, nav. — Fix: localize all user-facing strings
  (also fixes the "ACTİVE" bug). — 📈 🙂 🎨
- [ ] **Cross-screen weight mismatch** — dashboard 76.1 kg vs check-in placeholder 78.5 kg. — Where:
  dashboard vs `/progress-page`. — Why: app disagreeing with itself erodes trust in its math. — Fix:
  surface a single source of truth for current weight. — 🙂

### Monetization (plan now, build later)
- [ ] **No monetization path** — AI plan generation (the costly part) is unmetered; no tiers.
  — Fix: define a freemium line (free: tracking/quests/1 AI plan per week; premium: unlimited
  re-plans, advanced analytics, custom macros), add a non-blocking "Premium'a Geç" entry, and
  instrument upgrade-intent in GA before building billing. — 📈

---

## 🟡 P2 — Polish & consistency

- [ ] **Turkish-locale uppercase bug ("Active" → "ACTİVE").** — Where: `.status-badge{text-transform:
  uppercase}` `manage_stack.html:86` on English values `:154,216,236`. — Fix: localize labels
  ("Aktif/Azalıyor/Bitti"); decouple display text from the stored enum. — 🎨 🙂
- [ ] **Heading splits a single word mid-stem** ("ARKADAŞLARIN" → "ARKADAŞ / LARIN"). — Where:
  `templates/friends.html:161`; accent pattern `theme.css:468`. — Fix: don't hardcode `<br>`;
  accent whole/second word; wrap naturally. — 🎨 🙂
- [ ] **Hardcoded colors bypass the token system.** — Where: ring/trend `#FF4D4D/#FFB020/#CCFF00`
  `index.html:427,558,563`; chat bubbles `chat.html:55,97`; inline macro colors. — Fix: use
  `var(--red/orange/volt)`; add semantic tokens (`--accent-chat`, `--status-streak`) so blue/orange
  are intentional. — 🎨
- [ ] **Ambiguous red semantics** — red marks both "over calorie goal" and the neutral weight trend.
  — Fix: reserve red for errors/over-target; neutral/volt weight line; pair color with icon/text
  (never color-only). — 🎨
- [ ] **Two parallel stylesheet systems** — auth uses `static/style.css`; rest use `theme.css`.
  — Fix: fold auth rules into the token system; retire `style.css`. — 🎨
- [ ] **Dead CSS shipped to every user** — `.glass`/`.glass-volt` (`theme.css:178`, zero refs);
  `.sidebar`/`.bottom-nav` force-hidden (`nav.css:250–251`); unused `@keyframes`. — Fix: delete. — 🎨
- [ ] **Backslash in avatar href `"\edit-profile"`.** — Where: `manage_stack.html:110`,
  `leaderboard.html:165`, `friends.html:140`. — Fix: `/edit-profile` (ideally `url_for`). — 🎨
- [ ] **Desktop reads as a stretched mobile layout** (centered column, big empty gaps, mismatched
  card heights). — Where: `theme.css:92` (`--content-max`), `dashboard.css`. — Fix: desktop
  breakpoint that rebalances the bento (stretch/masonry) or adds a right rail. — 🎨 🙂
- [ ] **Three overlapping meal-logging paths** — "HIZLI EKLE", "MANUEL EKLE", and the "Günlük"
  diary tab. — Where: `/nutrition`, `templates/nutrition.html` + `nutrition.js`. — Why: users
  don't know which to use → log nothing. — Fix: pick one primary flow; demote the others. — 🙂 🎨
- [ ] **Chat clutter** — each accepted suggestion spawns a duplicate "Önerini kabul ettim" echo;
  timestamps out of order (…15:21 then 12:15 at bottom); stray text in a meal name. — Where:
  `/chat/<user>`, `templates/chat.html`. — Fix: suppress/merge the echo into the card state; fix
  message ordering. — 🎨 📈 🙂
- [ ] **Tip carousel clips/overlaps mid-transition.** — Where: `.ins-slide` ±105% translate inside
  `#ins-body{overflow:hidden}` `dashboard.css:343–357`. — Fix: absolutely position both slides at
  identical size during the swap (or 2-layer crossfade). — 🎨 🙂
- [ ] **Leaderboard loading flash** ("YÜKLENİYOR…") before data. — Where: `/leaderboard`. — Fix:
  skeleton rows or server-render the first paint. — 🎨
- [ ] **Icon-only controls lack accessible names** (hamburger, FAB, chat send, logout "→"). — Where:
  `leaderboard.html:163`, `chat.html:228,89`. — Fix: `aria-label` on each; drawer `role="dialog"`
  + focus trap; tablists `role`/`aria-selected`. — 🎨
- [ ] **Flat microcopy, no mission / emotional hook.** — Fix: aspirational tagline + outcome-led
  CTAs ("Ücretsiz planımı oluştur" instead of "KAYIT OL"). — 📈

---

## ✅ Quick wins (high impact ÷ low effort — grab these first)
- [ ] `/progress` JSON route (P0) — small route change.
- [ ] GA into `_base.html` + funnel events (P1).
- [ ] Favicon + meta description + OG/Twitter tags (P1).
- [ ] Dedupe `/nutrition-plan/active` fetch (P1).
- [ ] Localize quest names + nav "Supplements" + status labels → fixes "ACTİVE" too (P1/P2).
- [ ] Fix heading word-split and the `\edit-profile` backslash (P2).
- [ ] Reconcile dashboard vs check-in weight source (P1).

## 🔎 Worth verifying on a real device (couldn't confirm live)
- [ ] Mobile rendering (Chrome min-width clamp blocked true mobile capture) — do a real phone pass.
- [ ] Deep form submits (meal log, AI plan generation, menu scan) — blocked by the never-idle
  pages during the automated walkthrough; confirm they work end-to-end on device.
