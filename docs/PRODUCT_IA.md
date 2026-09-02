# Product Information Architecture Contract

Durable product-level Information Architecture (IA) for AxisAI. Subsequent UX
convergence PRs implement **this** document. They do not reopen the destination
model, ownership matrix, or navigation-level assignments recorded here.

This is a product contract, not a visual redesign, not a route migration, and
not a feature flag rollout.

| Companion | Role |
|---|---|
| `app/nav.py` | Presentational shell metadata (UIUX Sprint 1 PR1). Reads as Today · Plan · Coach · Progress. Default **OFF** via `UIUX_NAV_V2_ENABLED`. |
| `templates/_nav.html`, `templates/_actionbar.html` | Header, drawer, and mobile bottom bar. Flag-branched: legacy 5-tab vs v2 4-tab. |
| `app/hooks.py` `inject_nav` | Injects `nav_v2`, `nav_primary`, `nav_secondary`, `nav_resolve_active` into every template. |
| `docs/FEATURE_FLAGS.md` | Rollout order for `UIUX_NAV_V2_ENABLED`, `UIUX_TODAY_V2_ENABLED`, `UIUX_PLAN_V2_ENABLED`, `UIUX_COACH_PAGE_V2_ENABLED`. |
| `docs/MOBILE_TODAY.md` | Native Today **read** contract (`GET /api/v1/today`). Not a navigation contract. |
| Native app `lib/app/shell/app_shell.dart` | Already ships Today · Plan · Coach · Progress as the only bottom destinations. |

**Authority rule.** `app/nav.py` is the executable presentational list for the
flagged v2 shell. This document is the **product** authority. Where they
disagree about *secondary placement* (Nutrition as a drawer peer, Pump Check
Gallery as Community, hamburger as the long-term secondary access method), this
document wins. PR2 updates `app/nav.py` and its tests to match. Primary
destinations already agree and stay locked.

**This PR changes no production behavior.** No template, route, flag default,
or navigation component is modified here.

---

## A. Purpose

AxisAI is a premium AI fitness coach: it understands the user's training,
nutrition, recovery and progress, then helps them see what matters today and
what to do next.

The current web product does not present that identity. Navigation is
fragmented across a bottom bar, a hamburger drawer, a Profile hub that is also
an app directory, a notification bell, a floating Coach button, and duplicated
feature links. Destinations sit at the wrong level of the mental model
(Nutrition as a peer of Home; Pump Check Gallery as a Community item; Premium
as a global destination; XP as a Home hero).

This contract exists so that:

1. every user-facing destination has **exactly one canonical home**;
2. global navigation reflects the user's mental model (Today / Plan / Coach /
   Progress), not implementation boundaries (blueprints, HTML files, flags);
3. later PRs can change chrome, labels, and access paths **without**
   re-arguing ownership;
4. deep links and bookmarked URLs survive the chrome change.

It governs: product destinations, navigation levels, canonical ownership,
legacy/deprecation of chrome, and route-stability rules.

It does **not** govern: visual style, copy tone beyond IA labels, API payloads,
backend authority, billing, or Coach model/prompt behavior.

---

## B. Product navigation principles

Enforceable. A later PR that violates one of these is out of contract, not a
taste disagreement.

1. **Today before totals.** The first product question is "what matters today
   and what should I do next?", not calorie remaining, BMR/TDEE, or XP.
2. **Interpretation before raw metrics.** Surfaces lead with a state and a next
   action. Charts, diaries and galleries are inside the owning domain.
3. **One dominant action per state.** A page may have secondary links. It may
   not present a grid of equal CTAs as its primary hierarchy.
4. **Workflows before feature inventories.** Navigation names jobs (Today,
   Plan, Coach, Progress), not a directory of utilities.
5. **AI is a product layer.** Coach is a first-class destination, not a
   floating utility bolted onto other pages.
6. **Progressive disclosure.** Summary at the top level; full management UI
   inside the owning domain. Today may *summarize* nutrition; it may not *be*
   the nutrition diary.
7. **Quietly premium.** Hierarchy and restraint over chrome, badges, and
   promotional tiles in primary navigation.
8. **One canonical home per feature.** A feature may be *reachable* from more
   than one place (contextual link, deep link). It may not have two canonical
   homes.
9. **Global navigation follows mental models, not code boundaries.** `/training`
   remaining the Plan URL does not make the destination "Training" in the tab
   bar. Blueprint names do not appear in chrome.
10. **Retention mechanics are secondary.** XP, streaks, levels, achievements,
    quests and leaderboards support coaching. They do not define primary
    navigation or dominate Today.

---

## C. Current-state inventory

Source of truth: `origin/main` at the commit this contract was written against
(`27ae521`, `feat(training): durable idempotent native plan generation command`).
Do not treat UIUX Sprint 1 handoff prose as current without the files below.

### C.1 Two shells, one production default

| Shell | Gate | Primary tabs | Secondary access |
|---|---|---|---|
| **Legacy** (production default) | `UIUX_NAV_V2_ENABLED` OFF | Home `/`, Nutrition `/nutrition`, Training `/training`, Progress `/progress-page`, Profile `/edit-profile` | Hamburger drawer (`templates/_nav.html` legacy branch); Profile hub (`templates/edit_profile.html`); header bell + avatar |
| **Nav v2** (shipped dark) | `UIUX_NAV_V2_ENABLED` ON | Today `/`, Plan `/training`, Coach `/coach`, Progress `/progress-page` | Same hamburger, now populated from `app/nav.py` `SECONDARY`; header bell + avatar |

Both shells always render a header (`templates/_nav.html`) and a mobile bottom
bar (`templates/_actionbar.html`). CSS shows exactly one primary chrome per
breakpoint (bottom bar `<1024px`, header tabs `≥1024px`). The hamburger exists
in both shells.

Page templates set `{% set nav_active = '...' %}` before including the shell.
V2 maps that id through `app.nav.resolve_active`. Legacy compares the string
directly.

### C.2 Navigation surfaces (complete)

| Surface | Implementation | What it currently holds |
|---|---|---|
| Desktop header tabs | `templates/_nav.html` `.header-nav` | Legacy 5 tabs **or** v2 4 tabs from `nav.PRIMARY` |
| Mobile bottom bar | `templates/_actionbar.html` `.action-bar` | Same destinations as header tabs |
| Hamburger / drawer | `templates/_nav.html` `#nav-drawer` | Legacy: Notifications, Friends, Feed, Club, Quests, Challenges, Pump Check Gallery, Supplements, Premium. V2: entire `nav.SECONDARY` list (Nutrition, Notifications, Friends, Feed, Club, Quests, Challenges, Gallery, Supplements, Premium, Profile, Logout) |
| Header bell | `templates/_nav.html` `.header-bell` | `/notifications` in **both** shells. Unread badge via `GET /notifications/unread-count` |
| Header avatar | `templates/_nav.html` `.header-avatar` | `/edit-profile` in **both** shells |
| Profile hub | `templates/edit_profile.html` `.hub` | Community: Friends, Feed, Club, Quests. You: Pump Check Gallery, Supplements, Premium. Settings: language, Logout. Plus identity/XP hero, membership card, wearables, read-only stack |
| Coach FAB | `static/coach_widget.js` `#cw-fab` | Injected on Home/Today, Nutrition, Training/Plan, Progress, and `/coach`. **Not** on Profile, Community, Notifications, Premium, Gallery, Supplements |
| Nutrition log FAB | `templates/nutrition.html` `#log-fab` | In-page logging, not Coach |
| Contextual links | Home quick actions, Today secondary links, Progress "Ask Axis", Profile hub, achievement → `/quests` | Duplicate entry points into Nutrition, Training, Gallery, Premium, Quests, Coach |

### C.3 Destination inventory

User-visible HTML destinations currently reachable through chrome, hub, FAB,
direct URL, or redirect. APIs are listed only when they back a visible surface.

| Destination | Canonical route (today) | Entry points today | Surface | Current semantic owner | Problems / ambiguity |
|---|---|---|---|---|---|
| Home / Today | `GET /` (`app/blueprints/tracking.py` `home`) → `templates/index.html` (legacy) or `templates/today.html` (`UIUX_TODAY_V2_ENABLED`) | Bottom bar, header tab, brand mark | Primary | "Dashboard" / Today | Mixes daily guidance, calorie hero, quick actions, full weight entry+graph+BMR/TDEE, achievements/XP, generic tip carousel. Incomplete profile redirects to `/setup`. |
| Plan / Training | `GET /training` → `templates/training.html` or `templates/plan.html` (`UIUX_PLAN_V2_ENABLED`) | Bottom bar, header tab, Home "workout" quick action | Primary | Training page | Label is Training in legacy chrome, Plan in v2. Route name is still `/training`. Page owns only the workout plan, not Nutrition. |
| Coach | `GET /coach` → `templates/coach.html` or `templates/coach_v2.html` (`UIUX_COACH_PAGE_V2_ENABLED`) | V2 primary tab; direct URL; FAB on core pages. **Legacy shell has no Coach tab and no drawer link.** | Primary (v2) / floating utility (legacy) | Floating widget (`static/coach_widget.js`) hosted by a thin page | Page auto-opens the same widget the FAB opens. FAB remains even on `/coach`. Feature flags doc already records this: Coach v2 is not observable as a destination until Nav v2 is on. |
| Progress | `GET /progress-page` → `templates/progress.html` | Bottom bar, header tab | Primary | Progress redesign (summary, body, performance, consistency, Axis Insights, Physique, History, check-in sheet) | Physique already lives here via `GET /api/progress/physique`, but Pump Check Gallery is a separate global destination. |
| Progress alias | `GET /progress` | Bookmarks / guessed URL | Redirect | Compatibility | Redirects to `/progress-page`. Keep. |
| Nutrition | `GET /nutrition` → `templates/nutrition.html` | Legacy primary tab; v2 **drawer**; Home/Today log-meal links; Profile does **not** link here | Primary (legacy) / secondary product (v2) | Own page with in-page tabs: today, diary, plan, history, water | Peer of Home in production chrome. Product home should be Plan → Nutrition. `nav_active = 'nutrition'` lights a primary tab in legacy and **no** primary tab in v2. |
| Notifications | `GET /notifications` | Header bell (both shells); v2 drawer; **not** in Profile hub; **not** in legacy drawer-only wait — it **is** in the legacy drawer **and** the bell | Utility | Own page | Duplicated: bell + drawer. Profile hub omits it. `nav_active = 'profile'` (wrong owner). |
| Friends | `GET /friends` | Drawer; Profile hub | Community peer | Social blueprint | Presented as a global destination, not a Community child. `nav_active = 'profile'`. |
| Feed | `GET /feed` | Drawer; Profile hub | Community peer | Social / Feed V2 | Same. |
| Club / Leaderboard | `GET /leaderboard` | Drawer; Profile hub (`nav.club`) | Community peer | Gamification weekly XP board | Label is Club; route is `/leaderboard`. Peer of Friends/Feed. |
| Quests | `GET /quests` | Drawer; Profile hub; Home achievements footer | Community peer **and** Home retention | Gamification | Also linked from Home achievements — mixes Community with Home identity. |
| Challenges | `GET /challenges` | Drawer only. **Missing from Profile hub.** | Community peer | Challenges blueprint | Asymmetric: drawer has it, Profile hub does not. |
| Pump Check Gallery | `GET /pump-check-gallery` (`app/blueprints/profile.py`) | Drawer; Profile hub "You" section | Community (v2 `tier: community`) / Profile | Gallery of Pump Checks | Natural owner is Progress → Physique. Progress already has a Physique section. Capture itself happens on workout complete (`POST /workout/complete`), not in the gallery. |
| Supplement Cabinet | `GET /supplements` → `templates/manage_stack.html` | Drawer; Profile hub; Profile "My Stack" | Product secondary / Profile | Supplements blueprint | Natural owner is Plan → Nutrition. Duplicated on Profile as a directory item **and** a stack preview. |
| Premium | `GET /premium` | Drawer (styled premium); Profile hub; Profile membership CTA | Product secondary / Profile | Freemium promo (`app/blueprints/pages.py`). No billing. | Treated as a global nav destination. Owner is Account → Subscription. |
| Profile / Account | `GET /edit-profile` | Legacy primary tab; header avatar (both); v2 drawer | Primary (legacy) / utility (v2) | Account + app directory | Hub is both identity/settings **and** a second sitemap. XP/level/streak hero dominates the page. |
| Logout | `GET /logout` | V2 drawer; Profile hub | Utility | Auth | Fine as an account action. Must not be a primary destination. |
| Direct messages | `GET /chat/<username>` → `templates/chat.html` | Friends | Community / Friends | Social | Not in global nav. Keep under Friends. |
| Onboarding setup | `GET /setup` | Redirect from `/` when `profile_complete` is false | Auth/onboarding | Profile | Not a product destination. No app shell. |
| Marketing landing | `GET /welcome` | Public. Authenticated users redirect to `/`. | Marketing | `pages.landing` | Out of product IA. |
| Invite | `GET /davet/<code>` | Referral links | Growth | Sets cookie, redirects to register | Out of product chrome. |
| Auth pages | `/login`, `/register`, `/forgot-password`, `/reset-password`, `/verify` | Public | Auth | Auth blueprint | Out of product chrome. |

In-page modules that currently behave like destinations:

| Module | Where it lives today | Problem |
|---|---|---|
| Full weight entry + sparkline + BMR/TDEE | `templates/index.html` weight card; compact sparkline still on `templates/today.html` | Weight management belongs to Progress. Today v2 still shows a weight sparkline in the summary grid. |
| Achievements / XP / level / streak | Home identity + achievements card; Today chips + `<details class="today-more">`; Profile hero | Dominates Home. Quests link from Home achievements. |
| Generic tip carousel | `templates/index.html` AI tip; Today v2 `today.tip` inside "more" | Not a product destination. Future Today prefers a personalized Coach Insight. |
| Quick Actions launcher | `templates/index.html` (log meal, barcode **Soon**, menu scan, workout) | Feature directory on Home. Barcode tile is a disabled "Soon" promotion. |
| Nutrition in-page tabs | today / diary / plan / history / water on `/nutrition` | Correct as **domain-level** nav inside Nutrition, once Nutrition itself sits under Plan. |
| Progress check-in sheet | `templates/progress.html` `#checkin-sheet` | Correct owner (Progress). Includes weight. Conflicts with Home weight form. |
| Progress "Ask Axis" | `templates/progress.html` `data-action="askAxis"` | Contextual Coach entry. Must remain contextual, not a second Coach. |
| Weekly reward overlay | Home and Today (`/leaderboard/reward-check`) | Celebration, not a destination. Keep as a modal, not chrome. |
| Wearables | Profile integrations (WHOOP, Google Health) | Correct owner: Account. |

### C.4 Duplicated destinations (current)

A destination is duplicated when more than one **global** chrome item points at
it, or when two surfaces both present themselves as its home.

| Feature | Duplicate entry points |
|---|---|
| Notifications | Header bell + drawer (both shells) |
| Profile | Legacy bottom tab + header avatar + v2 drawer + Profile page itself |
| Friends / Feed / Club / Quests | Drawer + Profile hub |
| Challenges | Drawer only (hub gap) — still a *peer* of the others in the drawer |
| Pump Check Gallery | Drawer + Profile hub; Progress Physique is a third, better, home |
| Supplements | Drawer + Profile hub + Profile stack section |
| Premium | Drawer + Profile hub + membership CTA |
| Nutrition | Legacy primary tab **or** v2 drawer + Home/Today log-meal tiles |
| Coach | FAB on five core templates + `/coach` page that auto-opens the same widget + Progress "Ask Axis" |
| Weight | Home form + Progress check-in + Progress body card |
| Quests | Drawer + Profile hub + Home achievements footer |

### C.5 Flagged page variants (not IA forks)

These flags swap **page internals**, not the destination:

| Flag | Default | Route | OFF template | ON template |
|---|---|---|---|---|
| `UIUX_TODAY_V2_ENABLED` | OFF | `/` | `index.html` | `today.html` |
| `UIUX_PLAN_V2_ENABLED` | OFF | `/training` | `training.html` | `plan.html` |
| `UIUX_COACH_PAGE_V2_ENABLED` | OFF | `/coach` | `coach.html` | `coach_v2.html` |
| `UIUX_NAV_V2_ENABLED` | OFF | all authed HTML | legacy 5-tab shell | v2 4-tab + `nav.SECONDARY` drawer |

IA ownership does not change with these flags. Future Home work (PR4+) still
targets `/`, whether the current template is `index.html` or `today.html`.

---

## D. Target information architecture

### D.1 Canonical hierarchy

```
PRIMARY (bottom bar + desktop header, fixed order)
├── Today          GET /
├── Plan           GET /training
│     ├── Training         (the plan itself; same route)
│     ├── Nutrition        GET /nutrition
│     │     ├── Today / diary / plan / history / water   (in-page tabs)
│     │     └── Supplement Cabinet   GET /supplements
│     └── Recovery         (future; no page exists today)
├── Coach          GET /coach
└── Progress       GET /progress-page
      ├── Overview / trajectory
      ├── Check-in
      ├── Body / weight
      ├── Training progress
      ├── Nutrition adherence (signal, not the diary)
      ├── Physique / Pump Check   (gallery is the archive of this)
      ├── History
      └── Axis Insights

GLOBAL UTILITIES (chrome, not tabs)
├── Notifications  GET /notifications     → header bell only
└── Account        GET /edit-profile      → header avatar only
      ├── Identity, goals, preferences
      ├── Integrations / wearables
      ├── Subscription / Premium
      ├── Language
      ├── Notification preferences (future; not a separate page today)
      ├── Privacy / help (future)
      └── Sign out

SECONDARY DOMAIN (not a fifth primary tab)
└── Community
      ├── Feed            GET /feed
      ├── Friends         GET /friends
      │     └── Direct messages   GET /chat/<username>
      ├── Club            GET /leaderboard
      ├── Challenges      GET /challenges
      └── Quests          GET /quests
```

### D.2 Navigation levels (do not collapse)

| Level | What belongs here | What does not |
|---|---|---|
| **1. Primary navigation** | Today, Plan, Coach, Progress — and nothing else | Nutrition, Profile, Community items, Premium, Gallery, Supplements |
| **2. Global utilities** | Bell = Notifications. Avatar = Account | Using the avatar as a sitemap. Using the bell as a drawer substitute |
| **3. Domain-level navigation** | Plan: Training / Nutrition (/ Recovery later). Nutrition in-page tabs. Progress sections | Promoting a domain child to a primary tab |
| **4. Secondary / social** | Community and its children | Five Community peers in global chrome |
| **5. Contextual actions** | "Log meal" on Today, "Ask Axis" on Progress, "Complete workout" on Plan, menu scan from Coach | A global FAB that is the only way to reach Coach. Home Quick Actions as a feature launcher |

### D.3 Locked global model

Unless a later PR records a documented blocker in this file, implement:

- Primary: **Today · Plan · Coach · Progress**
- Utilities: **Notifications → bell**, **Account → avatar**
- Secondary: **Community**, reached from Account (single entry), not from a
  fifth tab and not as six drawer peers
- Hamburger / drawer: **legacy chrome, targeted for removal in PR2**
- Coach FAB: **legacy chrome, targeted for removal in PR3** once Coach is a
  first-class primary destination in the **production** shell (Nav v2 on, or
  the successor shell PR2 ships)

Do not remove the hamburger or the FAB in this PR.

---

## E. Canonical ownership matrix

No feature has two canonical homes. "Reachable from" is not "home".

| Feature | Current location(s) | Canonical destination | Navigation level | Migration action (later PRs) | Legacy / deep-link | Notes |
|---|---|---|---|---|---|---|
| Today / Home | `/` (index or today.html) | Today `/` | Primary | Keep route. Reshape contents in PR4+ | `/` stays | Do not rename to `/today` on web. Native Flutter path `/today` is client-local. |
| Training plan | `/training` | Plan `/training` | Primary, domain child Training | Keep route. Label is Plan | `/training` stays | Do not rename to `/plan` on web in this program. |
| Weekly program card | `/training` mount `#weekly-program` | Plan | Domain (Training) | None | — | Flag `WEEKLY_PROGRAM_UI_ENABLED`. Not a nav destination. |
| Nutrition diary / logging | `/nutrition` + Home/Today tiles + legacy tab / v2 drawer | Plan → Nutrition `/nutrition` | Domain | Demote from primary/drawer. Reach from Plan domain nav + Today compact CTA | Keep `/nutrition` | `nav_active` must resolve to **Plan** (today it does not, in v2). |
| Nutrition plan | `/nutrition` in-page "plan" tab | Plan → Nutrition | Domain | Stay an in-page tab | — | Not a primary destination. |
| Water logging | `/nutrition` water tab + `/water` API + Today summary | Plan → Nutrition | Domain / Today summary | Keep API. Compact Today signal allowed | `/water` is API, not a page | |
| Menu scan / barcode | Home quick action; Coach widget QR; Nutrition log sheet | Plan → Nutrition (logging) | Contextual + domain | Remove Home "Soon" barcode tile. Coach may offer scan in-conversation | — | Nutrition `#log-fab` stays as in-domain logging, not global chrome. |
| Supplement Cabinet | `/supplements` + Profile hub + drawer | Plan → Nutrition | Domain | Remove from drawer and Profile sitemap. Profile may keep a *read-only* stack summary that links in | Keep `/supplements` | |
| AI Coach | FAB + `/coach` + Progress Ask Axis | Coach `/coach` | Primary | Promote in production chrome (PR2). Remove FAB (PR3). Keep Ask Axis as contextual | Keep `/coach` | Same widget (`coach_widget.js`). No second Coach app. |
| Coach conversation APIs | `/ask`, `/ask/stream`, `/coach/history`, `/coach/conversation/reset` | Coach | — | None | — | Not destinations. |
| Notifications | Bell + drawer | Notifications `/notifications` | Global utility | Bell only. Remove drawer item. Fix `nav_active` (today `'profile'`) | Keep `/notifications` | |
| Account / Profile | Legacy tab + avatar + drawer + hub | Account `/edit-profile` | Global utility | Avatar only. Strip sitemap rows that have a real home elsewhere | Keep `/edit-profile` | |
| Goals / language / identity | Profile sheet | Account | Account internals | Stay | — | |
| Wearables | Profile integrations | Account | Account internals | Stay | `/api/wearables/*` | |
| Premium / subscription | `/premium` + drawer + hub | Account → Subscription | Account internals | Remove from drawer and as a global destination. Membership card on Account links in | Keep `/premium` | Billing still does not exist; the promo page is the subscription surface. |
| Weight tracking | Home form + Progress check-in + Progress body | Progress (body / check-in) | Domain | Remove full weight management from Today/Home. Compact *signal* on Today is allowed | `POST /update-weight`, `POST /checkin` | Progress is already the management UI. |
| Check-ins | Progress sheet | Progress | Domain | Stay | `POST /checkin`, `GET /checkin-history` | `/checkin-history` is JSON consumed by Home sparkline — later Today should not treat it as Home-owned. |
| Physique / Pump Check | Progress Physique section **and** `/pump-check-gallery` | Progress → Physique | Domain | Gallery ceases to be global nav. Progress Physique is the home; gallery is the archive of the same domain | Keep `/pump-check-gallery` | Capture remains the workout-complete flow, not a nav item. |
| Training progress / heatmap | Progress + `/api/progress/*` | Progress | Domain | Stay | — | |
| Axis Insights | Progress | Progress | Domain | Stay | Distinct from Home generic tips | |
| Progress history | Progress | Progress | Domain | Stay | `GET /history` is a different, session JSON endpoint — not a page | |
| XP / level / title | Home, Today chips, Profile hero | Account identity (secondary) + optional compact Progress signal | Secondary | Demote on Today. Must not be a primary tab or Home hero | — | See §J. |
| Streak | Home, Today, Profile | Same as XP | Secondary | Same | Streak is login-ish, not training consistency (Progress already stopped using it as consistency) | |
| Achievements / badges | Home card; `/api/progress/achievements` | Progress (secondary) / Account | Secondary | Remove oversized Home hierarchy | — | Not a primary destination. |
| Friends | Drawer + Profile hub | Community → Friends | Secondary domain | Remove as global peer. Single Community entry on Account | Keep `/friends` | |
| Feed | Drawer + Profile hub | Community → Feed | Secondary domain | Same | Keep `/feed` | |
| Club | Drawer + Profile hub | Community → Club | Secondary domain | Same. Label remains Club; route remains `/leaderboard` | Keep `/leaderboard` | |
| Challenges | Drawer only | Community → Challenges | Secondary domain | Add to Community domain (hub currently omits it). Remove as global peer | Keep `/challenges` | |
| Quests | Drawer + hub + Home | Community → Quests | Secondary domain | Remove Home achievements → Quests as a Home hierarchy item | Keep `/quests` | |
| Direct messages | `/chat/<username>` | Community → Friends | Contextual | Stay | Keep | Distinct from Coach. |
| Referral | `/referral` JSON, `/davet/<code>` | Account / growth | Out of chrome | Stay out of primary nav | Keep | |
| Setup | `/setup` | Onboarding | Out of chrome | Stay | Keep | |
| Landing | `/welcome` | Marketing | Out of chrome | Stay | Authenticated redirect to `/` | |

---

## F. Duplication / deprecation matrix

Nothing in this table is removed in this PR.

### F.1 Chrome to retire (PR2 unless noted)

| Item | Action | PR |
|---|---|---|
| Hamburger / `#nav-drawer` / `#header-menu-btn` | Remove as a navigation surface | PR2 |
| Legacy 5-tab primary (Home, Nutrition, Training, Progress, Profile) | Replace with Today, Plan, Coach, Progress | PR2 (already implemented behind `UIUX_NAV_V2_ENABLED`; PR2 makes the product contract the shell, including secondary placement fixes) |
| Nutrition as a primary tab | Demote; reach from Plan | PR2 |
| Profile as a primary tab | Demote to avatar utility | PR2 |
| Drawer items: Notifications, Friends, Feed, Club, Quests, Challenges, Gallery, Supplements, Premium, Profile, Logout | Delete with the drawer. Re-home per §E | PR2 |
| Coach FAB `#cw-fab` as global/core-page chrome | Remove once Coach is a production primary destination | PR3 |
| Home Quick Actions feature launcher | Remove / replace with state-aware next action | PR4+ Today |
| Home full weight entry, graph, BMR/TDEE block | Relocate to Progress; Today may keep a compact signal | PR4+ Today |
| Home oversized achievements / XP / streak hero | Demote | PR4+ Today |
| Home generic Tip of the Day carousel | Replace with personalized Coach Insight or drop | PR4+ Today |
| Home disabled "Soon" barcode tile | Remove. Do not promote unavailable features in primary hierarchy | PR4+ Today |
| Profile hub rows that duplicate real homes (Gallery, Supplements, Premium as sitemap, Community peers as a second app directory) | Replace with Account internals + **one** Community entry | PR2 |

### F.2 Profile hub rows — keep vs remove later

| Hub row | Later fate |
|---|---|
| Friends, Feed, Club, Quests | Collapse under a single Community entry |
| Challenges (missing today) | Join that Community entry; do not leave it drawer-only |
| Pump Check Gallery | Remove from Account sitemap; Progress → Physique is home |
| Supplements | Remove from Account sitemap; optional read-only stack summary may remain |
| Premium | Keep as Account → Subscription (membership card already does this) |
| Language | Keep (Account) |
| Logout | Keep (Account) |
| Wearables | Keep (Account) |
| Identity / goals / target weight sheet | Keep (Account) |

### F.3 Routes that stay valid but stop being primary navigation

`/nutrition`, `/supplements`, `/pump-check-gallery`, `/premium`, `/friends`,
`/feed`, `/leaderboard`, `/quests`, `/challenges`, `/notifications`,
`/edit-profile`, `/coach` (already not primary in the **legacy** shell).

### F.4 Compatibility aliases / redirects already in tree

| Route | Behavior | Later rule |
|---|---|---|
| `GET /progress` | Redirects to `/progress-page` | Keep forever (or until `/progress-page` itself is renamed, which this program does **not** do) |
| `GET /welcome` | Marketing; authed → `/` | Keep |
| `GET /` with incomplete profile | Redirects to `/setup` | Keep |

No new aliases are required for PR2 if routes do not change (see §G).

---

## G. Route migration contract

**Do not migrate routes in this PR. Prefer not to migrate them in PR2–PR4
either.** Changing ownership of a destination is a chrome/IA change, not a URL
change.

### G.1 Locked web URLs

| Destination | Locked web path | Do not rename to |
|---|---|---|
| Today | `/` | `/today` |
| Plan | `/training` | `/plan` |
| Coach | `/coach` | — |
| Progress | `/progress-page` | `/progress` as the HTML route (the alias already redirects) |
| Nutrition | `/nutrition` | `/plan/nutrition` |
| Account | `/edit-profile` | `/account` (unless a later PR adds a redirect and a dedicated Account IA) |
| Notifications | `/notifications` | — |
| Gallery | `/pump-check-gallery` | — |
| Club | `/leaderboard` | `/club` |

Native Flutter paths (`/today`, `/plan`, `/coach`, `/progress` in
`lib/app/router/app_routes.dart`) are **client-local**. They must not drive web
URL churn. The shared contract is the *destination identity* (Today/Plan/Coach/
Progress), not the string in the address bar.

### G.2 Rules for any future URL change

If a later PR truly must change a path:

1. Keep the old path as a `302` (authenticated HTML) or documented alias.
2. Preserve browser history: back/forward must land on the same *destination*,
   with the correct primary active state.
3. `nav_active` / `resolve_active` maps the new page onto the same primary id.
4. Deep links (push, email, shared Pump Check, invite) keep working.
5. Do not break `tests/test_frontend_audit_inventory.py` (inventory of rendered
   templates) without updating `docs/frontend-readiness/sprint-0/inventory.json`.

### G.3 Active navigation state (PR2 must implement)

| Page | Today (`nav_active` → primary) | Required |
|---|---|---|
| `/` | `home` → Today | Keep |
| `/training` | `training` or `plan` → Plan | Keep |
| `/coach` | `coach` → Coach | Keep |
| `/progress-page` | `progress` → Progress | Keep |
| `/nutrition` | v2: **none**; legacy: Nutrition tab | **Must become Plan** |
| `/supplements` | none / profile | **Must become Plan** |
| `/pump-check-gallery` | `profile` | **Must become Progress** |
| `/notifications` | `profile` | **None** (utility; bell is the chrome) |
| `/edit-profile` | `profile` | **None** (utility; avatar is the chrome) |
| Community pages | `profile` | **None** (secondary domain) |
| `/premium` | `profile` | **None** (Account internal) |

`app/nav.py` `resolve_active` and `tests/test_nav_contract.py` currently encode
the opposite for Nutrition (`None`) and treat Gallery as a Community secondary.
PR2 updates those tests. That is expected, not a regression.

### G.4 Route ownership (code)

IA ownership is not blueprint ownership. `app/blueprints/profile.py` serving
`/pump-check-gallery` does not make Gallery an Account feature. Do not move
blueprints in this program unless a later PR has a separate engineering reason.

---

## H. Responsibility boundaries

### Today

**Owns:** prioritized daily guidance; one state-aware next action; compact
status for training, nutrition, recovery/check-in, and a relevant progress
signal; a personalized Coach Insight.

**Does not own:** full weight management; full nutrition diary or macro
dashboard; achievement galleries; XP/level as hierarchy; settings; Community;
plan generation; Coach conversation UI (may deep-link to Coach).

**Future Today must remove or demote:** full weight entry and graph; BMR/TDEE
presentation; oversized achievements/XP; generic Quick Actions launcher;
duplicated full nutrition dashboards; generic Tip of the Day; unavailable/"Soon"
feature promotion.

**Future Today must center:** Daily Coach Brief; state-aware next action;
compact Today status; personalized Coach Insight; compact relevant progress
signal.

Do not implement that UI in this PR. Today v2 (`templates/today.html`) is a
step toward this (server-rendered primary action, summary grid, quick log) but
still shows calorie rings, a weight sparkline, streak/XP chips, and a generic
tip in "more". PR4+ continues from this contract, not from a new argument.

### Plan

**Owns:** "What is my plan?" — active training program; nutrition plan and
diary; supplements; future recovery planning; plan creation/regeneration
entry (already in-page on `/training` when no plan exists).

**Does not own:** workout *history* as Progress; physique gallery; Coach
conversation; Today next-action selection (Plan supplies facts; Today
prioritizes); Community; Account.

### Coach

**Owns:** personalized guidance; the conversation; contextual follow-up from
other domains ("Ask Axis"); product-context-aware tools that already exist
(plan mutation tools, menu scan inside the widget).

**Does not own:** being the only entry point via a FAB; primary navigation of
other domains; a second Coach implementation. `coach_widget.js` remains the
one widget. `/coach` is its canonical host. After PR3 the FAB is gone; the
widget lives on the Coach destination (and may still be invoked contextually).

### Progress

**Owns:** "Is what I am doing working?" — trajectory, check-ins, body/weight,
training progress, nutrition *adherence signal*, recovery trends (when they
exist), physique / Pump Check, history, Axis Insights. Secondary
achievement/progress mechanics may appear here without dominating.

**Does not own:** daily logging (that's Plan → Nutrition / Plan → Training
execution); plan mutation; Coach conversation; Community feed of Pump Checks
(Feed may *display* a shared check; Progress *owns* the user's physique
record).

### Notifications

**Owns:** the notification list and unread state. Bell only.

**Does not own:** other drawer destinations; settings (notification
*preferences* are Account, when they exist).

### Account

**Owns:** identity, photo, goals, target weight *as a preference*, language,
integrations, subscription/Premium, sign out, and a single entry into
Community. May show XP/level/streak as identity metadata.

**Does not own:** being a second app directory; Pump Check Gallery; Supplement
Cabinet as a sitemap item; Nutrition; Training; Coach.

### Community

See §I. Not a primary tab.

### Marketing / auth / onboarding

`/welcome`, `/login`, `/register`, `/setup`, `/davet/<code>` are outside the
product chrome contract. Do not fold them into primary navigation.

---

## I. Community decision boundary

Community is a **secondary domain**. Repository evidence does **not** justify
making it a fifth primary tab:

- Native `AppShell` already has exactly four destinations and no Community tab
  (`lib/app/shell/app_shell.dart`).
- Web v2 already demoted Feed/Friends/Club/Quests/Challenges/Gallery from
  primary (`app/nav.py` `SECONDARY`, `tier: community`).
- Production legacy still hides them in the drawer rather than the five-tab bar.

**How the children relate (do not redesign their internals in this PR):**

| Child | Job | Relation |
|---|---|---|
| Feed | Shared activity / Pump Checks / posts | The social stream |
| Friends | People graph; requests; DMs (`/chat/<username>`) | The social graph that Feed and Club sit on |
| Club | Weekly XP leaderboard (`/leaderboard`) | Competitive surface over the same graph |
| Challenges | Time-boxed group goals | Opt-in competition; not the same as Quests |
| Quests | Personal/daily missions | Retention mechanic with a social skin; not Progress trajectory |

Gallery is **not** Community. It is Progress → Physique, even though
`app/nav.py` currently tags it `tier: community` and Profile puts it under
"You".

**Access after the drawer is gone:** Account exposes **one** Community entry
(the hub's community section, collapsed to a single row or a Community
landing). Until a dedicated Community landing exists, the existing hub
grouping (Friends / Feed / Club / Quests, plus Challenges) is the access
path. Do not re-scatter those five as global utilities.

Community must not fragment the core coaching IA: no Community item in the
primary four; no FAB; no Home module that is actually a social launcher.

---

## J. Gamification boundary

XP, streak, level, title, badges, weekly Club rank, Quests, and Challenges are
**secondary retention / progress mechanics**.

They must not:

- appear as a primary navigation destination;
- dominate Today/Home hierarchy (no XP hero, no achievements card as a
  principal module, no streak as the main status);
- replace Progress trajectory (`docs/PROGRESS_SUMMARY.md` already dropped
  streak as a consistency signal — keep that);
- justify keeping a hamburger so that Quests/Club can stay globally peer-level.

They may:

- appear as compact identity metadata on Account;
- appear as a compact signal on Progress if they do not outrank trajectory;
- celebrate via the existing weekly-reward overlay (modal, not chrome);
- live fully inside Community (Club, Quests, Challenges).

---

## K. Future PR dependencies

Planned sequence. Decisions below are **locked** for those PRs.

### PR2 — Global Navigation Convergence

Implement the chrome in §D.3 and the active-state table in §G.3.

Locked for PR2:

- Primary destinations and order: Today, Plan, Coach, Progress.
- Web paths stay `/`, `/training`, `/coach`, `/progress-page`.
- Nutrition is not a primary tab. It is Plan → Nutrition; `/nutrition` stays;
  Plan is the active primary while the user is there.
- Profile is not a primary tab. Avatar is Account.
- Notifications are not a drawer item. Bell only.
- Hamburger/drawer is removed (or rendered nowhere). Secondary access moves to
  Account (Community entry, subscription, logout, language) and to domain
  navigation (Plan → Nutrition, Progress → Physique).
- Pump Check Gallery is not Community and not a drawer item.
- Supplements and Premium are not global destinations.
- `app/nav.py` + `tests/test_nav_contract.py` + `tests/test_nav_shell_v2.py` are
  the implementation surface. Expect tests that currently assert "nutrition is
  secondary and activates no primary" and "gallery is a community secondary"
  to change to match this contract.
- Do not implement Today V2 content redesign here (that's PR4+).
- Do not remove the Coach FAB here (that's PR3), unless Coach is unreachable
  without it in the shell PR2 ships — in which case PR2 must still leave a
  primary Coach tab, which is the replacement.

### PR3 — Coach navigation / global FAB removal

Locked for PR3:

- Coach is already a primary destination (`/coach`, v2 tab, native tab).
- There is one Coach implementation: `static/coach_widget.js`.
- Remove `#cw-fab` from core pages once the production shell includes Coach.
- Contextual "Ask Axis" on Progress remains valid and should open Coach, not
  resurrect a global FAB.
- Do not change AI prompts, streaming, persistence, rate limits, or tools.

### PR4+ — Today / Home V2 and downstream domain convergence

Locked for PR4+:

- Today ownership in §H and §8 (below). The page is `/`.
- Weight management moves to Progress; Today may summarize.
- XP/achievements/tips/Soon/Quick Actions follow §H and §J.
- Plan domain nav (Training | Nutrition) may land here or as a follow-on;
  ownership is already locked, timing is not.
- Progress already matches the target domain internally; remaining work is
  pointing Gallery at it and stopping Home from owning weight.

### Relationship to shipped-dark UIUX Sprint 1

UIUX Sprint 1 PR1–PR3 already built flag-gated shells and page variants that
*point at* this model (four primaries; thin `/coach`; Today hierarchy; Plan
v2). They are **not** this contract:

- They left Nutrition as a drawer peer, Gallery as Community, and the hamburger
  as the long-term secondary pattern.
- They left the Coach FAB in place by design.
- Production still serves the legacy 5-tab shell.

This document is the product-level lock those flags only partially implemented.
PR2 is the chrome convergence; it is not a second argument about the four
primaries.

---

## L. Acceptance criteria

### This PR (PR1) is done when

- Current IA is audited against source (this file, §C).
- Every user-facing destination in §C.3 has exactly one canonical home in §E.
- Duplicated navigation is listed in §C.4 and §F.
- Target global IA is explicit (§D).
- Hamburger is marked for later removal (§F.1, §K PR2).
- Coach FAB is marked for later removal (§F.1, §K PR3).
- Today/Home, Account, Community, and gamification boundaries are defined
  (§H–§J).
- Route/deep-link rules are defined (§G).
- PR2 can be implemented without reopening primary destinations, URL lock, or
  ownership.
- No production UI behavior changed.

### PR2 is done when

- Production chrome (or the flagged shell that PR2 makes the product default)
  shows only Today · Plan · Coach · Progress as primary.
- No hamburger.
- Bell → Notifications only; avatar → Account only.
- `/nutrition` keeps working and lights Plan.
- Community is reachable from Account without being a primary tab.
- Legacy 5-tab and drawer lists are gone from the shipping shell.
- Direct URLs in §G.1 still 200 (or redirect as already documented).

### PR3 is done when

- `#cw-fab` is not part of global/core chrome.
- `/coach` remains the canonical Coach destination and still hosts the one
  widget.
- Contextual Coach entry points still work.

### PR4+ Today is done when

- Today matches §H (brief, next action, compact status, insight, compact
  progress) and does not own the demoted modules.

---

## Home / Today ownership (locked)

Repeated from §H because later Home work depends on it.

Today **may** surface compact summaries of training, nutrition,
recovery/check-in, and progress.

Today **must not** own the full management UI for those domains.

Future Home convergence **removes or demotes:**

- full weight entry
- full weight graph
- BMR/TDEE-heavy presentation
- oversized achievements/XP hierarchy
- generic Quick Actions feature launcher
- duplicated full nutrition dashboards
- generic Tip of the Day
- unavailable/Soon feature promotion

Future Today **centers:**

- Daily Coach Brief
- state-aware next action
- compact Today status
- personalized Coach Insight
- compact relevant progress signal

Do not implement this UI in this PR.

---

## Web / mobile / shared implications

Present in this repository and in the sibling native app
(`yusufbesirarslan/axisai-mobile`):

| Topic | Finding | Contract |
|---|---|---|
| Native primary tabs | Already Today · Plan · Coach · Progress (`AppShell`) | Web PR2 converges **to** this mental model. Native does not add a hamburger or a Nutrition tab to "match" legacy web. |
| Native paths | `/today`, `/plan`, `/coach`, `/progress` | Client-local. Web keeps `/`, `/training`, `/coach`, `/progress-page`. |
| Mobile Today API | `GET /api/v1/today` projects workout-state; it is not a navigation authority | Unchanged by this contract. |
| Mobile Nutrition API | `GET /api/v1/nutrition/diary/today` | Nutrition remains Plan-owned on both clients. |
| Flags | All UIUX presentation flags default OFF on web | This contract does not turn them on. PR2 decides how the shipping web shell relates to `UIUX_NAV_V2_ENABLED`. |
| No shared nav package | `app/nav.py` is web-only | Do not import it from Flutter. Destination **names** are the shared contract. |

No mobile code changes in this PR. No Flask route changes in this PR.

---

## Document control

| Field | Value |
|---|---|
| Status | Accepted for UX-1 PR1 |
| Scope | Web product chrome + destination ownership; native alignment notes |
| Non-goals | Visual redesign, route moves, flag defaults, FAB/hamburger removal, Home implementation |
| Supersedes (product-level) | Secondary placement in `app/nav.py` (Nutrition drawer peer, Gallery as Community, hamburger as durable secondary) |
| Does not supersede | Primary four in `app/nav.py`; Progress domain internals; Coach widget identity; auth/onboarding routes |
