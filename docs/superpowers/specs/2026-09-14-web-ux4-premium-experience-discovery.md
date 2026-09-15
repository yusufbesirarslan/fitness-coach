# WEB-UX4-PR1 — Primary Journey + Premium Experience Discovery

**Status:** discovery only — no runtime code, no CSS, no templates, no production mutation.
**Date:** 2026-09-14
**Author role:** Product Design Engineer / Product UX Architect

---

## A. Executive verdict

AxisAI's web product is **architecturally honest and visually unfinished**, and the two
facts are related. Three convergence sprints (UX-1…UX-3) rebuilt *who owns what* —
Today, Plan, Coach, Progress, canonical server-side state, honest empty/error copy —
and they succeeded. What they did not do is give that architecture a **shared physical
language**. The result is a product whose thinking reads as premium and whose surface
reads as assembled.

The evidence is unusually clear about *where* the debt lives, and it is not evenly
spread:

- The surfaces a convergence PR actually rewrote are **disciplined**. Today renders
  **6** distinct text sizes, **1** card, **1** primary action, and a correct heading
  outline. Plan V2 renders **8–9** sizes against the legacy renderer's **12**, and a
  full `h1 → h2 → h3` outline against the legacy renderer's single `h1`.
- The surfaces no convergence PR reached are where the product looks cheap.
  Supplements renders **12** text sizes, **6 full-colour emoji as interface icons**,
  **20 `★` text glyphs as a rating control**, **48 keyboard tab stops**, and
  **40 of 47 controls under 44 px**. Coach renders a **360 × 500 floating chat box on
  an otherwise empty 1366 × 900 page**.
- The design system exists, is documented, and is **only half-adopted**. The canonical
  Modal component has **zero** consumers while the product ships **21 `role="dialog"`
  surfaces built nine different ways**. The canonical input has **three**.

Underneath the visual debt sit two defects that are not cosmetic at all. The design
system's focus token, `--focus-ring`, resolves to **1.09 : 1** against every app
surface (WCAG 2.2 SC 1.4.11 requires 3 : 1), and three rule blocks feed that token into
the `outline` property, where it is invalid at computed-value time and silently erases
the focus ring — including a *correct* rule earlier in the same file. Measured under
real `Tab` presses: **12 controls across Nutrition, Supplements and Coach compute
`outline-style: none`**, and the meal edit, meal delete, water quick-add and Coach
composer receive **no visible keyboard focus indication at all**. Separately,
`.auth-card` references an **undefined token** (`--space-7`), so the login, register,
verify, forgot-password and reset-password cards render with **`padding: 0`** at every
width above 560 px — the first screen of the product, on desktop.

**Verdict: the gap between AxisAI and a WHOOP-grade product is not a redesign. It is
one corrective foundation pass plus five bounded journey passes.** A foundation PR is
justified — but as *repair and adoption*, not as a new design system. Discovery
recommends `UX4-PR2 … UX4-PR8`, sequenced by user consequence rather than by page
order, with the Coach destination first among the journey PRs because it is the only
primary destination that has never been designed as one.

**WEB-UX4 DISCOVERY READY TO SHIP.**

---

## B. Exact baseline

| Item | Value |
|---|---|
| Repository | `yusufbesirarslan/fitness-coach` |
| Expected baseline (task brief) | `6cace40e6dc7bb3908f2931e1f6bd07e7d1bfe76` |
| **Verified `origin/main` at discovery** | **`6cace40e6dc7bb3908f2931e1f6bd07e7d1bfe76`** |
| Commit subject | `fix(ux): harden converged Plan experience (#308)` |
| Drift vs brief | **none** — `git fetch origin` then `git log -1 origin/main` matched the brief exactly |
| Discovery branch | `web-ux4-pr1-premium-experience-discovery` (created from the baseline SHA) |
| Runtime files changed | **0** |
| Production mutations | **0** |

`git fetch origin` advanced the local remote ref `3e709be..6cace40`; the resulting
`origin/main` is the brief's SHA. No drift to record.

### Flag state used for observation

Rollout flags live in the production host's `.env` and are **not derivable from the
repository** (`app/feature_flags.py` defaults every UI flag to `False`; `docs/ROLLOUT.md`
and `CLAUDE.md` both record that the deploy pipeline does not carry `.env`). The brief
asserts `UIUX_PLAN_V2_ENABLED` is production-visible; the state of
`UIUX_COACH_PAGE_V2_ENABLED` is **unprovable from the repo**. Discovery therefore
captured **both branches** and reports both:

| Pass | Flags | Cells | Failures |
|---|---|---|---|
| 1 — repo defaults (legacy branch, = the live rollback path) | all `UIUX_*` off | 92 | 0 |
| 2 — production-equivalent | `UIUX_PLAN_V2_ENABLED`, `WEEKLY_PROGRAM_UI_ENABLED`, `UIUX_COACH_PAGE_V2_ENABLED`, `UIUX_NAV_V2_ENABLED`, `UIUX_TODAY_V2_ENABLED`, `FITX_WORKOUT_SESSIONS_ENABLED` = 1 | 26 | 0 |

`UIUX_TODAY_V2_ENABLED` was confirmed to be a **no-op**: Today renders byte-identical
metrics with the flag on and off (6 text sizes, 1 card, nesting 1, headings `[1,2,2]`,
9 controls, document height 844 px in both passes), matching the note in `CLAUDE.md`
that UX-2 PR4 deleted the branch the flag used to select.

---

## C. Product north star (carried forward unchanged)

Guide → Action → Feedback. Interpretation before raw metrics. Today before totals.
One dominant action per state. Workflow before feature inventory. AI as a product
layer, not a chat gimmick. Progressive disclosure. Quietly premium.

WHOOP is used in this document **only** as a quality bar for typography discipline,
proportion, quiet hierarchy and interaction finish. No WHOOP layout, component or brand
element is proposed, referenced as a target, or reproduced. Every recommendation below
is derived from AxisAI's own existing tokens, components and IA.

---

## D. Audit methodology

Three lenses, run simultaneously and cross-checked against each other: **product
journey**, **interaction quality**, **visual system quality**.

**Source lens.** Full read of `templates/*.html` (31), `static/*.css` (23),
`static/*.js` (21), `app/nav.py`, `app/feature_flags.py`, `app/i18n.py`,
`docs/PRODUCT_IA.md`, `docs/design-system.md`. Programmatic census of every CSS rule
for type, spacing, radius, z-index, transition, focus and surface declarations.

**Browser lens.** The repository's own hermetic audit harness
(`scripts/frontend_audit/app.py` — SQLite, no network, synthetic seed, fixed clock,
loopback-only, `assert_safe_settings` enforced) was booted inside WSL Ubuntu-24.04 and
driven with the pinned Chromium (Playwright 1.61.0, rev 1228) already provisioned for
the Sprint-0 audit. **118 cells** were captured across two flag passes:

- surfaces: Today, Plan, Nutrition, Supplements, Coach, Progress, Account,
  Notifications, Pump-Check Gallery, Login, Landing, 404, 500
- states: `active-workout`, `active-rest-day`, `completed-workout`, `new-empty`,
  `progress-history`, `coach-history`, `anonymous`
- viewports: **320 · 390 · 768 · 1024 · 1366**
- locales: **TR and EN** (switched through the product's own `POST /set-language`,
  which persists to `current_user.language` — so EN cells are genuinely server-rendered
  in English, not client-patched)

Each cell recorded a full-page screenshot plus a structural DOM probe: computed
typography histogram, radius histogram, card census with nesting depth, complete
control inventory with measured geometry, overflow detection (ignoring elements inside
a genuinely scrollable ancestor), heading outline, accessible-name gaps, contrast with
correct alpha compositing, fixed-element stacking, console and network failures.

Three further targeted passes settled claims that a screenshot cannot settle: a
**computed-font-family attribution** pass, a **real keyboard `Tab` walk** (press `Tab`,
read `document.activeElement`, repeat until the order wraps) across 8 surfaces, and a
**`:focus-visible` measurement** pass.

**What was deliberately not done.** No production data was created, altered or deleted.
No plan was regenerated, no session abandoned, no meal or supplement mutated, no profile
changed, no Coach plan mutation triggered. No infrastructure was weakened to obtain
evidence.

**Stated limits of the evidence.** Three things were *not* exercised and are scored
accordingly, with the limit named at each place it matters:

1. **Live workout execution and completion** (J2's second half, J3 entirely). The
   fixtures carry no active `WorkoutSession` row, so Resume, session conflict, stale
   recovery and the completion dialog were reviewed **from source only**.
2. **A real Coach AI round-trip** (J7's answer/tool/mutation half). The hermetic app
   runs with `BEDROCK_ENABLED=0` and no provider key by design. Streaming, tool states
   and mutation confirmation were reviewed **from source only**.
3. **Production rendering.** `fitx.duckdns.org` is not reachable from this environment
   (recorded previously and re-confirmed), so all browser evidence is local-hermetic.
   Because every surface audited is server-rendered from canonical read models, the
   structural and typographic findings transfer; anything that could depend on real
   production data volume is marked as such.

---

## E. Current IA map (as shipped — an input, not a question)

```
Today            /                 nav_active 'home'         → today.html
Plan             /training         nav_active 'plan'         → plan.html  (V2, flag on)
  ├─ Training                                                  ↳ training.html (legacy, flag off)
  └─ Nutrition   /nutrition        nav_active 'nutrition'    → nutrition.html
       └─ Supplements /supplements nav_active 'supplements'  → manage_stack.html
Coach            /coach            nav_active 'coach'        → coach.html / coach_v2.html
Progress         /progress-page    nav_active 'progress'     → progress.html
  └─ Pump Check  /pump-check-gallery  nav_active 'gallery'

Utility  Notifications /notifications (bell)   ·  Account /edit-profile (avatar)
Account hub → Friends · Feed · Club · Quests · Challenges · Premium · Logout
```

`app/nav.py` is the single source for both shells and `resolve_active()` maps
`nav_active` → primary destination. Verified in the browser: at every viewport exactly
**one** primary navigation is in the accessibility tree and the tab order — the desktop
`.header-nav` is `display: none` below 1024 px and the `.action-bar` is hidden above it,
and the keyboard walk confirms neither variant leaks focusable duplicates.

**The IA is sound and discovery does not reopen it.** One IA-adjacent contradiction is
recorded as a finding rather than an IA change (F-14: destination labels and page titles
disagree), and one naming collision is recorded (F-15: `/nutrition`'s own `h1` is
"Beslenme Planı", which is also the name of its third tab).

---

## F. Journey scorecard

Scores are 1–5 (1 = poor/confusing, 3 = usable but ordinary, 5 = clear, intentional,
premium). They are deliberately not inflated; three journeys carry an explicit
evidence limit.

| Journey | Orientation | Interpretation | Action | Feedback | Continuity | Premium Feel | Overall |
|---------|-------------|----------------|--------|----------|------------|--------------|---------|
| J1 — New / no-plan user | 3 | 2 | 2 | 3 | 3 | 2 | **2** |
| J2 — Scheduled training day | 4 | 4 | 5 | 3 ⚠ | 2 | 3 | **3** |
| J3 — Active / interrupted workout ⚠ | — | — | — | — | — | — | **not exercised** |
| J4 — Nutrition daily workflow | 3 | 3 | 3 | 3 | 3 | 2 | **3** |
| J5 — Nutrition Plan | 2 | 3 | 3 | 3 | 3 | 2 | **2** |
| J6 — Supplements | 3 | 2 | 2 | 3 | 3 | 1 | **2** |
| J7 — Coach | 2 | 2 | 2 | 2 ⚠ | 1 | 1 | **2** |
| J8 — Progress | 4 | 4 | 2 | 3 | 3 | 3 | **3** |
| J9 — Account / utility continuity | 3 | 3 | 2 | 3 | 4 | 2 | **3** |
| J10 — Failure / degraded states | 4 | 4 | 3 | 3 | 3 | 3 | **3** |

⚠ = the marked cell rests partly or wholly on source review; see the per-journey note.

### J1 — New / no-plan user · overall 2

**Path observed:** `/` (`new-empty`) → `/training` (`no_active_plan`) at 320/390/1366.

Today's no-plan state is fine. The Plan destination is where the journey breaks. At
390 px the no-plan Plan page is a **2 758 px-tall single column** — 3.3 screens — built
from **48 radio-cards across 8 question groups** (days, style, goal, equipment, focus,
duration, cardio type, injuries), ending in one "Programımı Oluştur" button at the very
bottom. Measured: 51 card-like surfaces, 49 of them at 12 px radius, 48 pill radios.

This is the first thing AxisAI asks a new user to do, and it is a configuration form,
not a coached first step. Nothing on the page states what will be produced, how long it
takes, or that any answer can be changed later. Above the questions, a
`HEDEF / SEVİYE / TDEE` strip renders **three em-dashes** — a new user's first
impression of their own data is three blanks. Orientation is further weakened because
the page's `h1` reads "Antrenman Programı" while the navigation item the user just
clicked reads "Plan".

**What is right and must be preserved:** the form is server-authoritative, the proposal
block is explicitly labelled as not-yet-saved, and Cancel discards. The problem is
presentation and sequencing, not authority.

### J2 — Scheduled training day · overall 3

**Path observed:** `/` (`active-workout`) at all five viewports, TR and EN → `/training`
(Plan V2, `active_plan`). ⚠ Execution and completion reviewed from source only.

**Today is the best surface in the product and should be the template for the rest.**
Measured at 390: exactly **one** primary-filled control (`.btn-volt.today-cta`,
358 × 48), **one** card, **6** distinct text sizes, **3** font weights, a correct
`h1 → h2 → h2` outline, and the brief sentence + focus + duration + exercise count
above the fold. Action scores 5 on evidence.

Two things hold the journey to 3.

First, **completion has no next step**. In the `completed-workout` and
`active-rest-day` fixtures Today renders **zero** primary-filled controls. The moment
the product should feel best — you finished — is the moment it offers nothing. Same on
a rest day.

Second, **desktop composition**. At 1366 × 900 Today's content ends at roughly 500 px
and the remaining ~44 % of the viewport is empty. The reading column is deliberately
capped at 46 rem, which is right; what is missing is anything composed to occupy the
rest. The page's own `h1` is 20 px here while every other surface's `h1` is 38–52 px,
so the single most important sentence in the product is also the smallest page title in
it.

### J3 — Active / interrupted workout · not exercised

The synthetic fixtures contain no active `WorkoutSession` row, and creating one would
have meant mutating state to obtain a screenshot — which §21 forbids and which would
have produced evidence about the harness rather than the product. Source review of
`templates/plan.html`, `static/plan_workout.js`, `static/workout_state_client.js` and
`app/services/workout_session/` shows the *semantics* are strong: `plan.workout_action`
resolves to `start` / `resume` server-side, `plan.workout_session_stale_reason` is
surfaced as a sentence and never re-classified client-side, and the replacement dialog
is a genuine destructive confirm. **No score is claimed.** The visual-quality findings
that apply to Plan (F-06 nesting, F-21 ghost-button geometry) apply here too, and
`UX4-PR5` carries a non-negotiable exit criterion that Resume, conflict and stale states
are captured at all five viewports before it ships.

### J4 — Nutrition daily workflow · overall 3

**Path observed:** `/nutrition` at all five viewports, TR and EN, `active-workout` and
`new-empty`.

The workflow is coherent: breadcrumb `Plan / Beslenme`, five local tabs, calorie ring,
per-meal sections with edit and delete, water quick-add, child-domain link to
Supplements. Nothing is architecturally wrong.

What holds it at 3 is density and hierarchy. At 390 px only **3.5 of the 5 tabs** are
visible; History and Water are off-screen on arrival. The horizontal scroll is
deliberate and scroll-snapped — `components.css` documents why, and it is not a bug —
but the *consequence* is that two of five daily workflows are undiscoverable at the
most common width. The only primary-filled control on the page is an **icon-only FAB
with no visible label**. When the nutrition target is unavailable the page reads
`HEDEF: — KCAL` above a ring whose own sub-label is a bare `—`; two em-dashes stacked
read as breakage rather than as "not set yet". And the template carries **17 inline
`style="font-size:…"` declarations**, including four hand-rolled copies of a section
micro-label that already exists as `.sec-label` — copies which, being `<div>`s, are why
this page has **no `<h2>` at all**.

### J5 — Nutrition Plan · overall 2

Orientation scores 2 for one concrete reason: the page's `h1` is **"BESLENME PLANI"**
and its third tab is also **"Beslenme Planı"**. A user who clicks the tab named after
the page they are already on cannot form a correct mental model of what is nested in
what — and this sits one level below a global destination also called **Plan**. The
relationship between "what I ate today" and "what I planned to eat" is expressed only
by tab adjacency; nothing on the Today tab states the plan it is being measured against
when the target read fails.

### J6 — Supplements · overall 2, the lowest-quality surface in the product

**Path observed:** `/supplements` at 390 and 1366, plus a full keyboard walk.

The placement work from UX-3 PR5 is correct and visible: the `Plan / Beslenme /
Takviyeler` context line is there, `/supplements` is still the one editable cabinet, and
no second navbar was added. Everything *inside* the page is the problem.

Measured: **48 keyboard tab stops**, of which **20 are `★` text-glyph buttons**;
**40 of 47 controls under 44 px**; **12** distinct text sizes; **6 full-colour emoji**
(🥤 💧 ⚡ 💊 💪 📦) used as category icons, sourced from
`app/blueprints/supplements.py:158`, in a product whose design system explicitly
forbids emoji as interface icons and whose every other surface uses 1.8-stroke SVG.
Reaching the "EKLE" button by keyboard takes **34 tabs**.

The add form occupies roughly two thirds of the page **above** the user's own cabinet,
so the page answers "add a supplement" before "what do I have". And on each saved item,
the destructive **Sil** sits as the fourth of four visually identical ghost buttons
beside three status toggles — no `.btn-danger`, no separation, no confirmation
affordance in the control itself.

The page also has **no `<h2>`**: one `h1` and nothing else, so both the visual and the
assistive-technology section structure are absent.

### J7 — Coach · overall 2, and the largest gap between intent and execution

**Path observed:** `/coach` (`coach-history` and `new-empty`) at all five viewports, TR
and EN, both flag branches, plus a full keyboard walk. ⚠ The AI round-trip itself —
streaming, tool states, mutation confirmation — was reviewed from source only.

**Coach is a primary destination that has never been designed as a destination.** The
page renders an `h1` and one sentence, then a `setInterval` polls up to 40 × 50 ms to
pop the legacy floating widget on top of it. Measured: the Coach document height equals
the viewport height **exactly** (844 px at 390, 900 px at 1366) — the page has no
content of its own. At 1366 × 900 roughly **87 % of the viewport is empty**, and the
product — the conversation — is a fixed **360 × 500** box in the bottom-right corner,
positioned and proportioned like a third-party support widget. At 390 px the widget
auto-opens **over** the page's own `h1`, clipping it in half; Coach V2's own
"Open Coach" CTA is completely hidden behind the thing it opens.

The widget is also the highest-debt stylesheet in the repository and it shows on the
highest-intent surface:

- The AI avatar is a **blue → lime-green gradient** (`--color-chat-avatar-accent:
  #99CC00`) and the send button turns **lime-yellow on hover** (`#D6FF1A`) in a
  blue-primary product — the last survivors of a retired palette, on the Coach screen.
- The widget title reads **"AI Fitness Coach"** — hardcoded twice in
  `static/coach_widget.js:79,84`, absent from both locale catalogues, so it renders in
  English on the Turkish product.
- Coach answers — the longest text in the product — render at **13.5 px, weight 300**.
- Message timestamps use `--color-text-4` at **1.97 : 1**.
- `#cw-root` sits at **z-index 9998** against a documented scale that tops out at
  `--z-toast: 400`, so any toast raised while Coach is open renders behind it.
- The composer has **no accessible name**, measures **240 × 36** at 390 px, and its
  36 px height is below the 44 px guideline while its 13.5 px font is below the 16 px
  that prevents focus-zoom on iOS.

Continuity scores 1: **the keyboard tab order on the Coach destination begins at the
chat's Send button**, then the invisible launcher, then the page header, then the four
bottom-nav tabs — and reaches the composer **last**, on tab 13. A keyboard user
encounters "send" before they can reach the field to type in. There is also no route
from a Coach answer back into Plan; the widget is a terminal.

### J8 — Progress · overall 3

**Path observed:** `/progress-page` at all five viewports, TR and EN, `progress-history`
and `new-empty`.

Progress and Today are the only two surfaces with a correct heading outline (`h1 → h2 →
h3`, twelve headings). Interpretation is genuinely good: the canonical summary decides
the trajectory server-side and four sections render the *same* payload, so the page
cannot contradict itself.

Two things cap it. First, **Progress has no action.** Measured: **zero** primary-filled
controls; the only controls are two 33 px ghost buttons. The "SONRAKİ ADIM / Next Move"
slot literally names the next step in prose — *"Üzerine konuşulacak bir zemin için
birkaç antrenman kaydet."* — and attaches no control to it. The product tells you what
to do and gives you nothing to press.

Second, **honesty is rendering as repetition.** In the `progress-history` fixture the
string "TEMEL OLUŞUYOR" appears **four times** on one screen at three different sizes,
and the sentence "Bunu söylemek için yeterli antrenman geçmişi yok." appears **verbatim
twice**, in two adjacent Axis Insights slots. Re-publishing the canonical sentence is
the right architecture; rendering it four times is a presentation decision that reads as
a bug. At 1366 the two bottom sections are ~1 232 × 200 px boxes containing one centred
sentence each.

### J9 — Account / utility continuity · overall 3

Navigation continuity is good — the hub is a clean list, `nav_active` resolves
correctly, nothing dead-ends. Hierarchy is the problem: Account carries **three**
`.btn-volt` primary buttons — "Premium'a Yükselt" (316 × 45) and two wearable "Bağla"
buttons — and the page's actual purpose, "Profili Düzenle", is a **32 px ghost button
positioned below the upsell**. The strongest visual element on the user's own account
page sells them something.

Two systemic leaks surface here. The section labels "Topluluk" and "Ayarlar" use
`--color-text-4` at **2.22 : 1**, while two other section labels on the *same page*
("ENTEGRASYONLAR", "GÜNCEL SUPPLEMENT STACK") are white Bebas titles — two label systems
on one screen. And the supplement status renders as **"ACTİVE"** (the raw English
database value) here while the Supplements page renders the same row as **"AKTİF"**.

Notifications is the weakest utility surface: **nine full-colour emoji** are its
canonical type icons, and its failure path is broken — `catch (err) { firstLoad = false; }`
leaves the initial "Yükleniyor…" text on screen permanently, so a failed notification
read is indistinguishable from a slow one, forever (F-09).

### J10 — Failure / degraded states · overall 3

This is where the convergence work most clearly paid off, and the score reflects real
strength rather than politeness. Today distinguishes `needs_attention` / `error` from a
calm day by colour **and** copy; Plan refuses to render "no supplements yet" when the
cabinet read failed; Nutrition holds unknown values at `—` instead of zero; Progress
renders a neutral state per section rather than one page-level failure; Axis Insights is
omitted entirely rather than neutralised when there is nothing to say. Rest-day,
completed-day and no-plan states all have authored copy.

Three gaps hold it at 3: the permanent-loading bug on Notifications (F-09); the stacked
`—` on Nutrition when a target is missing (F-16); and the 404/500 pages, which render a
**120 px** numeral and a single link in a product whose next-largest type is 52 px.
---

## G. Visual-quality scorecard

Scores 1–5 per surface. Every row is backed by measured values in §H–§N.

| Surface | Typography | Spacing | Positioning | Buttons | Surfaces | Hierarchy | Forms | Navigation | Motion | Perceived quality |
|---|---|---|---|---|---|---|---|---|---|---|
| Today | 4 | 4 | 3 | 3 | 5 | 4 | — | 3 | 4 | **4** |
| Plan (V2) | 4 | 3 | 3 | 3 | 2 | 3 | 3 | 3 | 3 | **3** |
| Plan (legacy, rollback path) | 2 | 3 | 2 | 3 | 3 | 2 | 2 | 3 | 3 | **2** |
| Nutrition | 2 | 2 | 3 | 2 | 3 | 2 | 2 | 3 | 3 | **2** |
| Supplements | 1 | 2 | 3 | 1 | 3 | 1 | 1 | 3 | 3 | **1** |
| Coach | 2 | 2 | 1 | 2 | 2 | 1 | 1 | 3 | 3 | **1** |
| Progress | 4 | 3 | 2 | 2 | 3 | 4 | 3 | 3 | 3 | **3** |
| Account | 2 | 3 | 3 | 2 | 3 | 2 | 3 | 3 | 3 | **2** |
| Notifications | 3 | 3 | 3 | 2 | 3 | 2 | — | 3 | 3 | **2** |

The distribution is the finding: **the two surfaces a convergence PR rewrote score 3–4;
every surface it did not reach scores 1–2.** This is not a product with uniform polish
debt. It is a product with a half-executed adoption.

**Perceived quality — "if the logo were removed, would this still feel like a premium
performance product?"** Today: yes. Progress: nearly — it reads as a well-built
dashboard that forgot to offer an action. Plan V2: it reads as well-organised but
over-boxed. Nutrition, Account, Notifications: it reads as a competent web app.
Supplements and Coach: no — Supplements reads as an internal admin form and Coach reads
as a marketing site with a support-chat bubble.

---

## H. Typography inventory

### The system as declared (`static/tokens.css`)

| Role | Token | Value |
|---|---|---|
| UI face | `--font-sans` | `'Inter', 'DM Sans', system-ui, -apple-system, 'Segoe UI', sans-serif` |
| Display face | `--font-display` | `'Bebas Neue', var(--font-sans)` |
| Body face | `--font-body` | `var(--font-sans)` |
| UI sizes | `--text-2xs … --text-3xl` | 10 · 11 · 12 · 13 · 14 · 15 · 17 · 20 · 24 px |
| Display sizes | `--text-display-sm/md/lg` | clamp 28–36 / 32–44 / 38–52 px |
| Metric sizes | `--text-metric-sm/md/lg` | clamp 22–26 / 28–34 / 34–44 px |
| Weights | `--weight-light … --weight-extrabold` | 300–800 |
| Leading | `--leading-none … --leading-relaxed` | 1 / 1.1 / 1.2 / 1.4 / 1.6 / 1.75 |
| Tracking | `--tracking-tight … --tracking-label` | −0.01 / 0.04 / 0.08 / 0.12 / 0.16 em |

The scale is good. Fifteen UI/display/metric steps is a reasonable ladder for a product
this size, and the metric scale in particular is a genuinely premium idea.

### The system as shipped

| Measure | Value |
|---|---|
| `font-size` declarations using a token | **292** |
| `font-size` declarations using a raw value | **218** (190 in `static/*.css`, 28 inline in `templates/*.html`) |
| Token adoption | **57 %** |
| Distinct raw `font-size` values authored | **29** — incl. 9, 10.5, 11.5, 13.5, 13 px |
| Distinct **computed** text sizes rendered (390 px, TR, all authenticated surfaces) | **23** — incl. 9, 13.3333, 13.5, 35.1, 46.8, 54.6, 120 px |
| Font families actually rendered | **Inter · Bebas Neue · Arial · DM Sans** |
| Distinct `h1` sizes across the product | **8** — 20 (Today) · 24 (Coach) · 28 (Gallery) · 35.1 (Login) · 38→52 (`.page-hdr`) · 46.8 (setup) · 54.6 (Landing) · 120 (404/500) |

Per-surface distinct computed sizes (390 px, TR):

| Surface | Sizes | Families | Weights |
|---|---|---|---|
| Today | 6 | 2 | 3 |
| Coach | 6 | 2 | 4 |
| Gallery | 7 | 3 | 4 |
| Notifications | 8 | 2 | 2 |
| Plan (V2) | 8 | 3 | 4 |
| Progress | 9 | 3 | 5 |
| Nutrition | 11 | 3 | 5 |
| Account | 11 | 3 | 5 |
| Plan (legacy) | 12 | 3 | 5 |
| Supplements | 12 | 3 | 4 |

### Three specific typography defects

**1. Arial is a shipped typeface.** 41 of 223 sampled text-bearing/control elements
computed to **Arial**, Chromium's UA default for form controls. Attributed precisely:

| Selector | Rendered as | Where |
|---|---|---|
| `.btn-ghost` (as `<button>`) | **Arial 12–13 px / 500** | every surface — Plan, Progress, Account, Nutrition |
| `.cat-chip` ×9 | Arial 13 px | Supplements |
| `.star` ×20 | Arial 20 px | Supplements |
| `.supp-act-btn` ×4 (incl. `.del`) | Arial 11 px | Supplements |
| `.mc-edit`, `.mc-del`, `.log-fab`, `#cw-close` | Arial 13.333 px | Nutrition, Coach |

The cause is a one-line omission with a wide blast radius: `.btn-volt` and `.btn-danger`
set `font-family: var(--font-display)`, **`.btn-ghost` sets none**. Because anchors
inherit the body font and `<button>` does not, **the same component renders in Inter
when it is a link and in Arial when it is a button** — confirmed both in source
(`components.css:56–65`) and in the browser (`.btn-ghost` on Account: Arial 12 px/500;
`a.btn-ghost` on Plan: Inter). The net effect across the product: **the primary button
is Bebas Neue, the secondary button is Arial, and neither is the body font.**

**2. DM Sans is downloaded on every page to serve four elements.** `_head.html`
requests Bebas Neue + **DM Sans (5 weights + italic)** + Inter (300–800) in one blocking
Google Fonts stylesheet. DM Sans is only a *fallback* in the token stack; measured
across 118 cells it rendered **4 times**. Every page pays for a family it does not use.

**3. `.page-hdr h1` sets `line-height: 0.95`** while `--leading-headline: 1.1` exists
and is unused. Below 1.0, Turkish ascenders and cedillas on a two-line title crowd the
line above — visible on Account ("PROFİL / AYARLARI") and Supplements ("SUPPLEMENT /
DOLABI"), both of which force the break with a literal `<br>` inside the heading.

### Verdict

**The typeface choice is not the root problem and must not be changed.** Inter + Bebas
Neue is a defensible, athletic pairing and the token ladder is sound. The problem is
that 43 % of type declarations bypass the ladder, one component silently renders in the
browser's default face, and a third family is shipped for nothing. **No new font
import is proposed. No new size token is proposed.** The work is adoption and two
deletions.

---

## I. Button / control inventory

93 rules in `static/*.css` declare `cursor: pointer` plus a box. Across them:
**43 distinct `padding` values**, **18 distinct `border-radius` expressions**,
**15 distinct `font-size` values**.

### The canonical three (`static/components.css`)

| | `.btn-volt` (primary) | `.btn-ghost` (secondary) | `.btn-danger` (destructive) |
|---|---|---|---|
| padding | `13px 28px` | `9px 18px` (`8px 14px` ≤640) | `13px 28px` |
| measured height | **45 px** | **32 px** (390) / **35 px** (1366) | 45 px |
| radius | `--radius-md` (12) | `--radius-sm` (8) | `--radius-md` (12) |
| family | **Bebas Neue** | **none → Arial** | **Bebas Neue** |
| size / tracking | 16 px / 2.5 px | 12–13 px / normal | 16 px / 2.5 px |
| hover | bg + shadow + `translateY(-1px)` | colour + border + overlay | `brightness(0.92)` |
| pressed | `scale(0.97)` | `scale(0.97)` | `scale(0.97)` |
| focus | **none authored** | **none authored** | **none authored** |
| disabled | **none** (`.loading` only) | **none** | **none** (`.loading` only) |
| loading | `.loading` opacity + no-pointer | **none** | `.loading` |

Three problems follow directly from this table.

- **Primary and destructive are visually identical apart from hue.** Same padding, same
  radius, same face, same tracking, same 45 px height. A destructive action should not
  be a recolouring of the primary action.
- **Primary and secondary share no geometry at all** — 45 px vs 32 px, 12 px vs 8 px
  radius, Bebas vs Arial. They do not read as two ranks of one family; they read as two
  systems.
- **Neither disabled nor focus is designed** on any of the three.

### Beyond the canonical three

At least **eleven** further primary-rank buttons exist with their own geometry:
`.auth-btn` (48 px, radius 8, Inter 13/700), `.setup-btn`, `.landing-cta` (48 px),
`.add-btn` (Supplements, 43 px), `.reward-btn` (Today's reward overlay, 48 px, own
`opacity` hover, no focus rule), `.log-fab` (56 px circle), `#cw-send` (38 px),
`.notif-markall`, `.prem-cta`, `.wearable-connect`, `.qab`. So the login CTA, the app
CTA and the Coach send button are three unrelated implementations of "the main button".

### Interaction-state coverage across all stylesheets

| State | Rule count |
|---|---|
| `:hover` | **108** |
| `:focus-visible` | 68 |
| `:focus` (non-visible) | 27 |
| `:active` (pressed) | **25** |
| `:disabled` / `[disabled]` | **16** |

Pressed is designed on roughly a quarter of the surfaces that design hover; disabled on
roughly a seventh. That ratio *is* the "not quite premium" feeling, quantified — premium
interaction quality is mostly carried by the states that are currently missing.

### Primary-action dominance, measured (390 px, primary-filled controls per surface)

| Surface | Count | Detail |
|---|---|---|
| Today (workout day) | **1** | `.btn-volt.today-cta` 358 × 48 — textbook |
| Plan V2 | 1 | `.btn-volt` "Antrenmana Başla" |
| Plan (no plan) | 1 | `.btn-volt` "Programımı Oluştur" (below 2 700 px of form) |
| Today (rest / completed) | **0** | no action offered |
| Progress | **0** | two 33 px ghosts only |
| Notifications, Gallery | 0 | — |
| Nutrition | 1 | icon-only FAB, no visible label |
| Coach | 1 | a 38 × 38 send icon |
| Supplements | 3 | **two are selected filter chips** painted in the primary fill |
| Account | **3** | upsell + two wearable connects |

Two entries are their own finding. On Supplements, a *selected category chip* carries
the same solid primary fill as a call to action, so a filter outranks "EKLE". On
Account, the three strongest elements on the user's own page all sell or connect
something.

### Verdict

**Do not normalise these in UX4-PR1, and do not build a new button system.** The
correct target is a **four-rank hierarchy already implied by the code** — primary,
secondary, quiet/text, destructive — with one shared control height, one shared radius
rule, one shared face, and complete `hover / pressed / focus / disabled / loading`
coverage on each rank. That is an edit to three existing classes plus the retirement of
duplicates, not a new library.

---

## J. Surface / card inventory

**202** rules across 22 stylesheets declare a background + radius + border/shadow, with
**77 distinct padding values** among them. Rendered at 390 px, the product shows
**12 distinct corner radii**: 4 · 6 · 8 · 9 · 10 · 11 · 12 · 14 · 16 · 20 px · 50 % ·
9999 px — against a token scale of 4 / 8 / 12 / 16 / 24 / full. **10 px is the single
most common authored raw radius (27 declarations) and is not in the scale at all.**

Per-surface card census (390 px, TR):

| Surface | Card-like surfaces | Max nesting | Classification |
|---|---|---|---|
| Today (rest / completed) | **0** | 0 | — |
| Today (workout day) | 1 | 1 | FUNCTIONAL |
| Coach | 1–2 | 1 | FUNCTIONAL |
| Notifications, Gallery | 1 | 1 | FUNCTIONAL |
| Supplements | 4 | 2 | FUNCTIONAL + VISUAL |
| Nutrition | 6 | 2 | FUNCTIONAL + VISUAL |
| Progress | 10 | 2 | FUNCTIONAL + **UNNECESSARY CONTAINER** |
| Account | 12 | 2 | FUNCTIONAL + VISUAL |
| **Plan V2** | **17** | **3** | **NESTED-CARD DEBT** |
| Plan V2 (completed) | 20 | 3 | NESTED-CARD DEBT |
| **Plan (no plan)** | **51** | 1 | VISUAL GROUPING at scale |

**Plan V2 is the clearest nested-card debt in the product.** The observed structure at
both 320 px and 1366 px is: an outer `ANA ALAN / ANTRENMAN` card → an inner
`HAFTALIK PROGRAM` info card → seven day cards → an `ANTRENMAN PLANI` block; then a
second outer `PLAN ALANI / BESLENME` card → a third-level `TAKVİYELER` card. Three
levels of rounded box to express two levels of ownership. Four of the seven day cards
contain the single word "Dinlenme" and carry exactly the same visual weight as a real
training day, so **more than half of the weekly program's vertical space is spent
rendering rest**.

The cost is measurable: Plan V2's document is **2 363 px at 390 px and 1 972 px at
1366 px**, against the legacy renderer's 1 050 px and 900 px. Plan V2 is the better
product — better typography (8–9 sizes vs 12), a correct heading outline against the
legacy `h1`-only — and it is **more than twice as tall**. That trade is what UX4 has to
unwind.

**Progress** carries a different variant: at 1366 the Physique and History sections
render as ~1 232 × 200 px bordered boxes containing one centred sentence each. The box
is not grouping anything; it is framing emptiness.

`.card` itself is well defined (`surface-2`, hairline border, `--radius-lg`,
`--space-5`) and `.card-flush` exists for content that manages its own insets. The
problem is not the component — it is that **202 separate rules re-implement it**.

### Verdict

Premium hierarchy here should come from **whitespace, alignment and type**, not from a
border around every content group. The correct direction is *fewer, stronger surfaces*:
one card per ownership boundary, sections separated by rhythm rather than by outline,
and rest days expressed as a quiet row rather than a full card. That is a layout
decision per surface, not a token change — which is why it belongs in the journey PRs
and not the foundation PR.

---

## K. Layout / positioning findings

| Aspect | Observed |
|---|---|
| Content ceiling | `--content-max: 1280px`; Today overrides to `46rem` |
| Page gutters | `--space-4` mobile, `--space-5`/`--space-6` desktop — consistent |
| Fixed chrome | `.global-header` 56 px (z 100), `.action-bar` 68 px (z 100, <1024 px) |
| FAB rail | `--fab-btn` 56, `--fab-rail-inset` 15/36, `--fab-rail-h` reserved by `.page-body.has-fab-rail` |
| Horizontal overflow | **none — 0 offenders across all 118 cells at 320/390/768/1024/1366** |

Horizontal-overflow cleanliness at 320 px across every surface and both locales is a
genuinely strong result and should be protected by a regression assertion, not
re-litigated.

The positioning problems are all about **desktop composition**, not correctness:

- **Coach at 1366 × 900: ~87 % empty**, with the product in a 360 × 500 corner box.
- **Today at 1366 × 900: ~44 % empty** below a 46 rem column.
- **Plan V2 at 1366: a ~690 px column centred in 1 366 px**, with ~340 px of gutter on
  each side and no use of the horizontal axis — a phone layout stretched vertically
  onto a desktop canvas, 1 972 px tall.
- **Progress at 1366: full-width stretch** — sections span 1 232 px, so a one-sentence
  empty state sits alone in a 1 232 × 200 px box.

So the product has **two opposite desktop failures at once**: Today and Plan are too
narrow for their canvas, Progress is too wide for its content. Neither is a broken
layout; both are unconsidered ones.

One alignment inconsistency inside a single surface: on Plan V2 at 320 px the
"Beslenmeyi aç" and "Takviyeleri aç" links are **centred** while every other element in
the same card is left-aligned.

**A candidate finding that the evidence refuted, recorded for honesty:** the Nutrition
full-page screenshot appears to show the `.log-fab` overlapping the "Hedefin hakkında
AxisAI'ya sor" button. It does not. Full-page capture renders `position: fixed`
elements at their scroll-zero position, which is a known artefact of the technique; and
`static/nutrition.css:231` documents `.log-fab.is-tucked`, which slides the FAB off the
left edge while the meal list is scrolled, specifically so nothing floats over the meals
being read. **Not a defect. Not reported as one.**

---

## L. Responsive matrix

320 · 390 · 768 · 1024 · 1366, TR and EN. `OK` = no overflow, no truncation, no
occlusion, content order preserved.

| Surface | 320 | 390 | 768 | 1024 | 1366 | EN parity | Note |
|---|---|---|---|---|---|---|---|
| Today | OK | OK | OK | OK | OK¹ | OK | ¹ ~44 % empty viewport |
| Plan V2 | OK | OK | OK | OK | OK² | OK | ² 690 px column in 1366; 1 972 px tall |
| Plan (legacy) | OK | OK | OK | OK | OK | OK | rollback path |
| Nutrition | OK³ | OK³ | OK | OK | OK | OK | ³ 3.5 of 5 tabs visible; scroll is intentional and snapped |
| Supplements | OK | OK | OK | OK | OK | OK | — |
| Coach | OK⁴ | OK⁴ | OK⁵ | OK⁵ | OK⁵ | OK | ⁴ widget covers the page `h1`; ⁵ 360 × 500 box, page empty |
| Progress | OK | OK | OK | OK | OK⁶ | OK | ⁶ 1 232 px-wide single-sentence empty states |
| Account | OK | OK | OK | OK | OK | OK | — |
| Notifications | OK | OK | OK | OK | OK | OK | — |
| Login / Landing | OK | OK | **FAIL⁷** | **FAIL⁷** | **FAIL⁷** | OK | ⁷ `.auth-card` computed `padding: 0` — see F-01 |

EN and TR were captured as separate server-rendered pages for all eight primary
surfaces at all five viewports. **The locale catalogues are at exact parity (1 382 keys
each, zero missing in either direction)** and no EN-specific wrapping, truncation or
overflow was observed. One untranslated string leaks into TR (F-13, "AI Fitness Coach")
and one raw database value leaks into both (F-19, "ACTİVE").

The one hard responsive failure is on the acquisition funnel and is detailed next.

---

## M. State-quality matrix

`D` default · `L` loading · `E` empty · `S` success · `P` partial · `X` error ·
`—` disabled · `C` completed · `!` attention. ✓ designed · ~ present but generic ·
✗ missing/wrong · n/a not applicable.

| Component | D | L | E | S | P | X | — | C | ! |
|---|---|---|---|---|---|---|---|---|---|
| Today brief / primary action | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | n/a | ✓ | ✓ |
| Today status row | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | n/a | n/a | n/a |
| Today progress signal | ✓ | ✓ | ✓ | ✓ | n/a | ✓ | n/a | n/a | n/a |
| Plan V2 domain cards | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | n/a | ✓ | ✓ |
| Plan weekly program | ✓ | ✓ | ✓ | ✓ | ~ | ✓ | n/a | n/a | n/a |
| Plan create / regenerate | ✓ | ~ | ✓ | ✓ | n/a | ✓ | ✗ | n/a | n/a |
| Nutrition ring / targets | ✓ | ~ | ~ | ✓ | ~ | ✗ | n/a | n/a | n/a |
| Nutrition meal cards | ✓ | ~ | ✓ | ✓ | n/a | ~ | ✗ | ✓ | n/a |
| Supplements cabinet | ✓ | ✗ | ✓ | ✓ | n/a | ✗ | ✗ | n/a | n/a |
| Supplements add form | ✓ | ✗ | n/a | ~ | n/a | ~ | ✗ | n/a | n/a |
| **Coach conversation** | ✓ | ~ | ~ | ✓ | ✗ | ~ | ✗ | n/a | n/a |
| Progress summary | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | n/a | n/a | ✓ |
| Progress Axis Insights | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | n/a | n/a | ✓ |
| Progress physique / history | ✓ | ✓ | ✓ | ✓ | ~ | ✓ | n/a | n/a | n/a |
| **Notifications list** | ✓ | ~ | ✓ | ✓ | n/a | **✗** | n/a | n/a | n/a |
| Account hub / integrations | ✓ | ~ | n/a | ~ | n/a | ~ | ✗ | n/a | ~ |
| `.btn-volt` / `.btn-ghost` / `.btn-danger` | ✓ | ~/✗ | n/a | n/a | n/a | n/a | **✗** | n/a | n/a |

Read the table by column rather than by row. **Default, empty and error are broadly
designed** — that is the convergence work paying off, and it is genuinely above average
for a product at this stage. **Loading and disabled are the two columns that were never
designed.** `.btn-ghost` has no loading state at all, no canonical button has a
`:disabled` rule, and `.skeleton` — which exists and is documented — has **five**
consumers. That is why in-flight interactions feel unfinished even where the end states
are good.

The one outright wrong cell is Notifications' error state (F-09).

---

## N. Accessibility findings

Discovery-level, classified by consequence. Every number below is a measurement from a
real keyboard `Tab` walk or a computed-style read, not an inference.

### N1 — Focus indication is the product's largest accessibility gap (P1)

Two independent root causes, both in the token layer, which is why this belongs in the
foundation PR.

**Cause 1 — `--focus-ring` is below perceptibility.**
`--focus-ring: 0 0 0 3px rgba(var(--color-primary-rgb), 0.07)` (`tokens.css:226`).
Composited over every app surface it yields:

| Surface | Ring colour | Contrast vs surface |
|---|---|---|
| `--color-bg` #121212 | rgb(21, 26, 35) | **1.08 : 1** |
| `--color-surface-1` #1A1A1A | rgb(28, 34, 42) | **1.09 : 1** |
| `--color-surface-2` #1E1E1E | rgb(32, 38, 46) | **1.09 : 1** |
| `--color-surface-3` #252525 | rgb(39, 44, 52) | **1.09 : 1** |

WCAG 2.2 SC 1.4.11 requires **3 : 1**. This token is the focus treatment at **11 sites**
across `auth.css`, `components.css` (`.fc-input` — the canonical input), `nutrition.css`,
`plan.css`, `profile.css`, `progress.css` and `training.css`.

**Cause 2 — the same token is fed into `outline`, where it silently erases the ring.**
Three rule blocks write `outline: var(--focus-ring, 2px solid var(--color-primary))`:
`nutrition.css:547`, `progress.css:396`, `progress.css:413`. `--focus-ring` *is*
defined, so the fallback never applies and the substituted value is
`outline: 0 0 0 3px rgba(…)` — a `box-shadow` value in an `outline` property. Because
the declaration contains `var()`, it cannot be rejected at parse time; it is **invalid
at computed-value time**, and the property therefore resolves to its **initial value**
(`outline-style: none`) rather than falling back to the previous cascade winner.

That last clause is the damaging part: it means the broken rule **destroys correct
earlier rules**. `nutrition.css:230` correctly gives `.log-fab:focus-visible` a
`2px solid var(--color-primary)` outline; the block at line 547 overwrites it with
nothing.

**Measured under real `Tab` presses** (390 px, `:focus-visible` confirmed true):

| Control | Surface | Measured focus |
|---|---|---|
| `.mc-edit` (edit a logged meal) | Nutrition | `outline-style: none`, no shadow — **no indicator** |
| `.mc-del` (delete a logged meal) | Nutrition | `outline-style: none`, no shadow — **no indicator** |
| `.qab` (water quick-add) | Nutrition | `outline-style: none`, no shadow — **no indicator** |
| `.log-fab` (the page's only primary action) | Nutrition | `outline-style: none`; only its resting elevation shadow — **no indicator** |
| `#cw-input` (the Coach composer) | Coach | `outline-style: none`, `box-shadow: rgba(0,0,0,0) 0 0 0 0` — **no indicator** |
| `.slot-empty` ×3 | Nutrition | `outline-style: none` + the 1.09 : 1 token ring |
| `#f-name`, `#f-brand`, `#f-price`, `#f-review` | Supplements | `outline-style: none`; a **1 px** border-colour change only — below SC 2.4.11's minimum indicator area |

**12 controls compute `outline-style: none`**, of which **5 have no visible focus
indication whatsoever** — and they include the meal edit and **meal delete** buttons.

Across all 8 surfaces walked, **20 distinct (outline, shadow) focus outcomes** were
measured. The product's own blue ring covers 103 of 154 stops; **10 stops fall back to
the browser's default grey ring** because the canonical buttons have no authored focus
rule (`.btn-ghost` ×4, `.supp-act-btn` ×3, `.cat-chip` ×3).

### N2 — Coach keyboard order is inverted (P1)

Measured tab order on `/coach`:

```
1 #cw-send → 2 #cw-fab (invisible) → 3 header-brand → 4 header-bell → 5 header-avatar
→ 6-9 .ab-tab ×4 → 10 #cw-close → 11 #cw-qr → 12 #cw-input
```

A keyboard user reaches the chat's **Send** button first and the **composer last**, on
tab 12, after the entire bottom navigation. `#cw-fab.cw-hidden` is `opacity: 0` and
therefore still focusable — the only genuinely invisible-but-focusable control found in
the product (45 × 45). `#cw-input` additionally has **no accessible name**; its only
label is a placeholder.

### N3 — Missing accessible names and labels (P2)

| Control | Surface | Gap |
|---|---|---|
| `#cw-input` | Coach, **and Nutrition** | no `<label>`, no `aria-label` — placeholder only |
| `#cw-close` (30 × 30) | Coach | no text, no `aria-label`, no `title` |
| `#f-name` `#f-brand` `#f-price` `#f-review` | Supplements | visible text labels exist but are not `<label for>`-associated |
| `#injury-other` | Plan (create) | placeholder only |

### N4 — Heading structure is absent on six of nine primary surfaces (P2)

Measured heading outlines at 390 px:

| Surface | Outline |
|---|---|
| Progress | `h1 h2 h2 h3 h3 h3 h2 h3 h3 h3 h2 h2` ✓ |
| Plan V2 | `h1 h2 h2 h2 h2 h2 h3` ✓ |
| Today | `h1 h2 h2` ✓ |
| **Nutrition · Supplements · Coach · Gallery · Account · Notifications** | **`h1` only** |
| Landing | `h1 h3 h3 h3 h3` — skips `h2` |

The cause is directly traceable and is the same cause as the visual inconsistency:
the surfaces that use the real `.sec-label` component get real `<h2>`s; the surfaces
that hand-rolled a micro-label out of a `<div>` (Nutrition's four inline copies,
Supplements' `.add-card-title`, Account's `.pf-section-title`) got the styling and lost
the semantics. **Bypassing the component costs the structure** — this is the single
strongest argument in the audit for adoption over re-authoring.

### N5 — `aria-modal` is asserted without the behaviour that makes it true (P2)

The product ships **21 `role="dialog"` surfaces** across 10 files, built from at least
nine different class families. Of those:

| Behaviour | Files |
|---|---|
| `aria-modal="true"` | **9** |
| focus trap | **1** (`training.js`) |
| focus restore on close | 3 |
| `inert` on the background | 2 |
| `Escape` to close | 11 |

`aria-modal="true"` tells assistive technology to ignore everything outside the dialog.
Without a trap or `inert`, a keyboard user tabs straight out behind it while AT still
reports the rest of the page as hidden. Today's reward overlay is a concrete instance:
it sets `role="dialog" aria-modal="true"`, calls `btn.focus()`, handles `Escape` — and
neither traps focus nor restores it to the opener.

### N6 — Contrast (P2, narrow)

With correct alpha compositing, real text-contrast failures are few and share one cause:
**`--color-text-4` (#4D4D4D) used as text.**

| Element | Size/weight | On | Ratio | Needs |
|---|---|---|---|---|
| `.cw-ts` — every Coach message timestamp | 10 px / 400 | #1E1E1E | **1.97 : 1** | 4.5 |
| `.mc-time` — meal-log timestamps | 11 px / 400 | #1E1E1E | **1.97 : 1** | 4.5 |
| `.hub-section-label` — Account section labels | 10 px / 700 | #121212 | **2.22 : 1** | 4.5 |
| `.pp-link` | 13 px / 600 | #212832 | 4.49 : 1 | 4.5 (borderline) |

`--color-text-3` (#909090) measures 5.22 : 1 and is fine. The fix is a usage rule, not
a palette change: `--color-text-4` is a *hairline/decoration* value and should not carry
text.

> **A correction recorded deliberately.** An earlier compositing pass in this audit
> reported additional failures (notably `.tab-btn.active`) caused by a bug in the
> auditor's own alpha maths, which forced composited alpha to 1 after the first
> translucent layer. The compositor was rewritten to proper Porter–Duff `over` and every
> contrast figure was re-measured. **Those findings were false and are not in this
> document.** Only the table above is claimed.

### N7 — Touch targets (P2)

At 390 px, against the 44 px guideline (WCAG 2.2 SC 2.5.8 AA requires 24 px; 44 px is
the platform convention this product should meet):

| Surface | Under 44 px | Of | Notable |
|---|---|---|---|
| **Supplements** | **40** | 47 | `.cat-chip` 94 × 29, 20 × `.star`, `.supp-act-btn` |
| Nutrition | 14 | 27 | `.tab-btn` 88 × 38 |
| Coach | 6 | 12 | `#cw-close` 30 × 30, `#cw-input` 240 × 36, `#cw-send` 38 × 38 |
| Account | 6 | 23 | `.btn-ghost` 358 × 32, `.hub-lang-opt` 47 × 34 |
| Plan V2 | 5 | 21 | `.btn-ghost` 199 × 33, `.plan-secondary-link` 93 × 38 |
| Every surface | 2 | — | `.header-bell` 40 × 40, `.header-avatar` 36 × 36 |

`.btn-ghost` at **32–33 px** is the systemic entry: the product's secondary button is
11 px short of the guideline everywhere it appears.

### N8 — Iconography (P2)

The design system states, in `docs/design-system.md`, that `.icon-tile` is the single
box for interface icons and that emoji must not be used. Shipped anyway:

| Surface | Emoji as interface iconography |
|---|---|
| Supplements | 🥤 💧 ⚡ 💊 💪 📦 — the **canonical category icons** (`app/blueprints/supplements.py:158`) |
| Notifications | ❤️ 💬 👋 🤝 🔁 🏆 🔔 — the **canonical type icons** and the empty state |
| Account | 🔥 (streak), 📦, ★ / ☆ |
| Supplements / Account | **`★` ×32** as a rating control and display |
| Coach widget / Nutrition / Progress / Training | ✓ ✗ ⚠ ✅ 💬 inside rendered output |

Two of the four utility/primary surfaces in scope use full-colour emoji as their primary
visual language, in a product whose every other icon is a 1.8-stroke monochrome SVG.
Emoji also render differently per platform, are not theme-aware, and cannot take a
state colour.

### N9 — Not an issue, confirmed

- **No horizontal overflow** anywhere, at any viewport, in either locale.
- **Exactly one primary navigation** is exposed to AT and the tab order per breakpoint.
- **No hidden modal content leaks into the tab order** — `display: none` correctly
  excludes it on 7 of 8 surfaces walked. (An earlier static-selector heuristic in this
  audit suggested otherwise; the real keyboard walk disproved it. The single genuine
  exception is `#cw-fab`, N2.)
- **No placeholder or `undefined`/`NaN` text** rendered in any of the 118 cells.
- **No page errors** in any cell; the only console errors are the hermetic
  environment's blocked Google Analytics beacons and the two intentional 404/500 probes.
---

## O. Cross-surface consistency matrix

`C` consistent · `V` variant with a reason · `I` inconsistent · `M` missing.

| | Today | Plan | Nutrition | Supplements | Coach | Progress | Account |
|---|---|---|---|---|---|---|---|
| Page title | **I** (20 px, own class) | C (`.page-hdr`, 38–52) | C | C | **I** (24 px, in `nav.css`) | C | C |
| Section title | C (`.sec-label` → `h2`) | C (`.sec-label`) | **I** (4 inline `div` copies) | **I** (`.add-card-title` div) | **M** | C (`.sec-label`) | **I** (two systems on one page) |
| Primary CTA | C (`.btn-volt`) | C (`.btn-volt`) | **V** (icon-only FAB) | **I** (`.add-btn`) | **I** (`#cw-send` 38 px) | **M** (none on page) | C (×3, all upsell) |
| Secondary CTA | **V** (text link) | C (`.btn-ghost`) | C (`.btn-ghost`) | **I** (`.supp-act-btn`) | **M** | C (`.btn-ghost`) | C (`.btn-ghost`) |
| Card | C (none used) | **I** (3-level nesting) | C | C | **I** (widget shell) | **V** (empty framing) | C |
| Stat | C (`.today-stat`) | **I** (legacy `.stat-card`) | **I** (inline 30 px ring) | **M** | **M** | C (`.wc-card`) | **I** (`.pf-xp`) |
| Tab | **M** | **M** | C (`.tab-bar`) | **M** | **M** | **M** | **M** |
| Input | **M** | **V** (`.plan-select`) | C (`.fc-input`) | **I** (bare `input`) | **I** (`#cw-input`) | **I** (`.pf-input`-like) | **I** (`.pf-input`) |
| Empty state | **V** (inline sentence) | **V** (inline sentence) | C (`.empty-state`) | C (`.empty-state`) | **I** (italic `.cw-empty`) | **V** (per-section copy) | **M** |
| Loading | **V** (inline text) | C (`.skeleton`) | C (`.skeleton`) | **M** | **I** (`.cw-typing` dots) | **V** (inline text) | **M** |
| Error | C (inline, honest) | C (inline, honest) | **V** | **M** | **V** | C (per section) | **M** |
| Modal / sheet | **I** (`.reward-overlay`) | **I** (`.pump-modal`) | **I** (`.sheet` + 2 more) | **M** | **I** (widget dialog) | **I** (`.sheet`) | **I** (`.pf-sheet`) |
| Destructive action | **M** | C (confirm dialog) | **V** (`.mc-del` icon) | **I** (`Sil` = a status chip) | **M** | **M** | **V** (`.hub-link-danger`) |
| Icon-only action | **V** (none) | **V** (none) | C (SVG) | **I** (emoji + `★`) | C (SVG) | C (SVG) | **I** (emoji 🔥) |

Counting the cells: **43 C**, **19 V**, **26 I**, **10 M**. The `I` column concentrates
on exactly four rows — **section title, input, modal, icon-only action** — and on
exactly three surfaces — **Supplements, Coach, Account**.

That concentration is the whole case for the decomposition in §V: two rows
(**input**, **modal**) are single-component problems that a foundation PR fixes once for
everyone; two rows (**section title**, **icon-only action**) are adoption problems that
each journey PR fixes for its own surface; and the three worst surfaces each get their
own bounded PR.

The `M` cells are mostly legitimate (Today needs no tabs; Plan needs no local tab bar).
The two that are **not** legitimate: Coach has **no section title, no secondary action
and no designed error state**, and Progress has **no primary CTA**.

---

## P. Architecture / authority constraints

Every recommendation in this document was tested against §17's question — *would this
require new persistence, new domain authority, a new backend route, duplicated business
logic, client-side truth, or new AI behaviour?* — and those that would are separated
out here rather than smuggled into the UX work.

### Pure UX (no new authority) — the whole of UX4-PR2 … PR8

Typography, spacing, radius, button hierarchy, focus treatment, surface nesting, page
composition, control geometry, icon replacement, section-heading semantics, copy and
label corrections, empty/loading/disabled state design, modal focus behaviour, keyboard
order. **None of these require a new route, a new query, a new table or new AI
behaviour.** Plan V2's data payload already contains everything the recommended Plan
layout needs; Progress's canonical summary already contains everything its recommended
action needs.

### Explicitly NOT PURE UX — separated, and not scheduled in UX4

| Candidate | Why it is not pure UX | Disposition |
|---|---|---|
| A control on Progress's "Next Move" slot | The slot's copy is generated by `progress_insights`; wiring it to an action requires the read model to emit a **target**, not just a sentence | **Deferred.** UX4-PR7 may give the slot a *link to an existing destination* only; anything that needs a new field is a backend change and out of scope. |
| Coach → "return to Plan" continuity | A Coach answer knowing which plan it mutated is `coach_plan_tools`' business, not the widget's | **Deferred.** UX4-PR3 may link to `/training` unconditionally; a contextual deep link needs server support. |
| Progressive / staged plan creation (J1) | Splitting the create form into steps must not create a second client-side draft authority | UX4-PR4 is constrained to **presentation only**: the same single form, the same single submit, the same server-authoritative proposal. Grouping and disclosure, never a client-held multi-step state. |
| Nutrition target when unavailable | Rendering something other than `—` requires deciding a fallback target — that is `nutrition_targets`' authority | UX4-PR6 may only improve **how absence is worded and laid out**. |
| Wearable "Bağla" CTAs on Account | Whether these integrations function is a product-claim question, not a UX one | **Recorded as a risk** (F-22), referred to product. UX4 does not alter their behaviour. |

### Authority boundaries reaffirmed (unchanged by anything proposed here)

Plan owns placement, not business authority. Training authority remains the canonical
server services. Nutrition writes remain `MealLog` / the canonical nutrition
authorities. Supplements editing remains `POST /supplement/{add,edit,delete}` in
`manage_stack.html` — and `tests/test_ux3_pr5_supplements_placement.py` enforces that by
scanning every template and script, so any UX4 change that accidentally introduced a
supplement form elsewhere would fail the build. Coach mutation authority remains narrow.
Progress owns Pump Check and history. **No page-local duplicate writer is proposed
anywhere in this roadmap.**

---

## Q. Full finding register

Each finding: ID · surface · journey · category · severity · evidence · current
behaviour · why it matters · user consequence · perceived-quality consequence ·
architecture implication · recommended direction · likely owner PR.

---

**F-01 · Auth cards render with zero padding above 560 px**
Surface: Login / Register / Verify / Forgot / Reset · Journey: J1 entry · Category:
VISUAL · **Severity: P1**
*Evidence:* `static/auth.css:132` declares `padding: var(--space-7)`; `--space-7` is
**not defined anywhere** in the repository (239 custom properties defined; this is the
only undefined reference, used 3×). Computed `.auth-card` padding measured at
`/login`: **20 px at 390 px** (from the `max-width: 560px` override at `auth.css:691`),
**`0px` at 768 px, 1024 px and 1366 px**.
*Current behaviour:* on tablet and desktop the sign-in card's content sits flush against
its own border.
*Why it matters:* this is the first screen of the product for every new user on desktop.
*User consequence:* the acquisition surface looks broken before the user has an account.
*Perceived-quality consequence:* severe — nothing else on the page can recover a
zero-padding card.
*Architecture implication:* none. One token definition or three declaration fixes.
*Direction:* define the missing step or retarget the three references to `--space-6`;
add a CI assertion that every `var(--*)` reference resolves.
*Owner:* **UX4-PR2**

---

**F-02 · The design-system focus token is below perceptibility**
Surface: all · Journey: all · Category: ACCESSIBILITY · **Severity: P1**
*Evidence:* `tokens.css:226` — `--focus-ring: 0 0 0 3px rgba(var(--color-primary-rgb),
0.07)`. Composited: **1.08–1.09 : 1** against `--color-bg`, `--surface-1/2/3`. WCAG 2.2
SC 1.4.11 requires 3 : 1. Consumed at 11 sites across 7 stylesheets, including
`.fc-input` — the canonical input.
*Current behaviour:* the product's official focus indicator is invisible in practice.
*User consequence:* keyboard and switch users cannot see where they are.
*Perceived-quality consequence:* high — focus quality is one of the clearest signals of
interaction craft.
*Architecture implication:* none. One token value.
*Direction:* raise the token to a ≥3 : 1 ring (a solid 2 px primary outline at 2 px
offset is already the de-facto pattern on `.ab-tab` / `.hub-link` and measures well);
keep it as **one** token so every consumer improves at once.
*Owner:* **UX4-PR2**

---

**F-03 · `outline: var(--focus-ring, …)` silently deletes the focus ring**
Surface: Nutrition, Progress · Journey: J4, J8 · Category: ACCESSIBILITY ·
**Severity: P1**
*Evidence:* `nutrition.css:547`, `progress.css:396`, `progress.css:413`. `--focus-ring`
is a `box-shadow` value; used as `outline` it is invalid **at computed-value time** (the
`var()` defeats the parse-time check), so `outline` resolves to its initial value
`none` — and, critically, does **not** fall back to the previous cascade winner.
Measured under real `Tab` with `:focus-visible` true: `.mc-edit`, `.mc-del`, `.qab`,
`.log-fab`, `.slot-empty` ×3 all compute `outline-style: none`. `.log-fab` has a
**correct** rule at `nutrition.css:230` that this block overwrites.
*Current behaviour:* five controls — including **delete a logged meal** — have no
visible focus indication at all.
*User consequence:* a keyboard user can land on "delete" with no indication.
*Perceived-quality consequence:* invisible to mouse users, disqualifying for keyboard
users.
*Architecture implication:* none. Delete three declarations.
*Direction:* remove the three `outline: var(--focus-ring…)` declarations; let the
repaired token (F-02) and the authored rules apply. Add a lint/test that `--focus-ring`
is never used as an `outline` value.
*Owner:* **UX4-PR2**

---

**F-04 · Coach is a primary destination with no destination**
Surface: Coach · Journey: J7 · Category: PRODUCT UX · **Severity: P1**
*Evidence:* measured document height equals viewport height exactly (844 px at 390,
900 px at 1366) in **both** flag branches — the page has no content. `#cw-window` is a
fixed `360 × 500` box; at 1366 × 900 ≈ **87 %** of the viewport is empty.
`templates/coach.html` and `coach_v2.html` each run a `setInterval` polling up to
40 × 50 ms to open the widget over their own content; at 390 px the widget covers the
page's `h1` and hides Coach V2's own CTA entirely.
*Current behaviour:* one of four primary destinations renders as an empty page with a
support-chat bubble.
*User consequence:* the product's AI — its core differentiator — is presented as an
accessory.
*Perceived-quality consequence:* the single largest gap in the product.
*Architecture implication:* **presentation only.** No change to `/ask`, `/ask/stream`,
`coach_plan_tools`, prompts, persistence or mutation authority. The existing widget
remains the one implementation; UX4 changes where it lives and how it is composed.
*Direction:* make Coach a real page — the conversation occupies the content column,
with entry context (today's plan state), visible capability affordances, and a route
back into Plan. The floating widget remains as-is on other pages.
*Owner:* **UX4-PR3**

---

**F-05 · Coach keyboard order starts at Send and ends at the composer**
Surface: Coach · Journey: J7 · Category: ACCESSIBILITY · **Severity: P1**
*Evidence:* measured `Tab` order — `#cw-send` → `#cw-fab` (invisible, `opacity: 0`,
45 × 45, still focusable) → header → 4 × `.ab-tab` → `#cw-close` → `#cw-qr` →
`#cw-input` (stop 12). `#cw-input` has no accessible name.
*User consequence:* a keyboard user must traverse the whole page to reach the field, and
meets "send" first.
*Perceived-quality consequence:* high on the surface that most needs to feel considered.
*Architecture implication:* none — DOM order and `hidden`/`inert` on the closed launcher.
*Direction:* order the widget composer before its send control; remove the closed
launcher from the tab order; give the composer a real label.
*Owner:* **UX4-PR3**

---

**F-06 · Plan V2 spends three levels of card on two levels of ownership**
Surface: Plan · Journey: J2 · Category: VISUAL · **Severity: P2**
*Evidence:* measured 17 card-like surfaces, **nesting depth 3**, at every viewport
(20 in the completed-workout state). Document height **2 363 px at 390** and
**1 972 px at 1366**, against the legacy renderer's 1 050 / 900. Four of seven day cards
contain only "Dinlenme" at full card weight.
*Current behaviour:* the converged Plan is better organised and more than twice as tall.
*User consequence:* the weekly program requires 2–3 screens of scrolling, most of it
rest days.
*Perceived-quality consequence:* the dominant instance of "everything is a dark
rectangle".
*Architecture implication:* none — the payload already distinguishes rest from training.
*Direction:* one card per ownership boundary; express sections by rhythm and type;
render rest days as a quiet row.
*Owner:* **UX4-PR5**

---

**F-07 · The no-plan Plan page is an 8-question form, not a first step**
Surface: Plan · Journey: J1 · Category: PRODUCT UX · **Severity: P2**
*Evidence:* 390 px full-page height **2 758 px**; **51 card-like surfaces**, 48 radio
cards across 8 groups; the single submit is at the very bottom. The header strip renders
`HEDEF / SEVİYE / TDEE` as three em-dashes.
*User consequence:* a new user's first act is a long configuration form with no stated
outcome; the three blanks read as breakage.
*Perceived-quality consequence:* high — this is where "premium AI coach" has to be felt
and instead reads as a settings screen.
*Architecture implication:* **presentation only.** Same single form, same single submit,
same server-authoritative proposal; **no client-side multi-step draft state.**
*Direction:* progressive disclosure with sensible defaults preselected, a stated outcome
("AxisAI will build a 7-day program you can change any time"), and a persistent submit;
suppress or explain the empty stat strip.
*Owner:* **UX4-PR4**

---

**F-08 · Supplements is the lowest-quality surface in the product**
Surface: Supplements · Journey: J6 · Category: VISUAL / INTERACTION ·
**Severity: P2**
*Evidence:* **48** tab stops (20 are `★` glyph buttons; "EKLE" is tab 34);
**40 of 47** controls under 44 px; **12** distinct text sizes; **6 full-colour emoji**
as category icons (`app/blueprints/supplements.py:158`); controls render in **Arial**;
4 unlabeled inputs with **no focus outline**; **no `<h2>`**; the add form occupies ~⅔ of
the page above the user's own cabinet.
*User consequence:* the cabinet — the thing the user came for — is below a long form.
*Perceived-quality consequence:* reads as an internal admin tool.
*Architecture implication:* none. `/supplements` remains the one editable cabinet and
the existing placement test keeps it that way.
*Direction:* cabinet first, add behind a deliberate action; replace `★` with a real
rating control; replace emoji with the icon system; adopt `.fc-input` and `.sec-label`.
*Owner:* **UX4-PR6**

---

**F-09 · A failed notification read renders as permanent loading**
Surface: Notifications · Journey: J9, J10 · Category: STATE · **Severity: P2**
*Evidence:* `templates/notifications.html` — `catch (err) { firstLoad = false; }`. The
initial `<div class="loading-text">` is never replaced, so a failed fetch leaves
"Yükleniyor…" on screen indefinitely. `markRead` failures are also swallowed silently.
*User consequence:* a broken read is indistinguishable from a slow one, forever.
*Perceived-quality consequence:* a permanent spinner is the most legible possible signal
of an unfinished product — and it contradicts the honest-state discipline every other
surface follows.
*Architecture implication:* none.
*Direction:* an honest error state with a retry, matching Plan's and Progress's existing
pattern.
*Owner:* **UX4-PR7**

---

**F-10 · `.btn-ghost` renders in the browser's default typeface**
Surface: all · Journey: all · Category: SYSTEM CONSISTENCY · **Severity: P2**
*Evidence:* `components.css:56–65` sets no `font-family`. Measured: `.btn-ghost` as a
`<button>` computes **Arial 12 px/500** (390) and **Arial 13 px/500** (1366); as an
`<a>` it inherits Inter. 41 Arial elements were attributed in total across four
surfaces.
*Current behaviour:* the product's secondary button is Arial, its primary is Bebas Neue,
and the same component renders in two different faces depending on its element.
*Perceived-quality consequence:* a systemic, low-level "cheap" signal on every surface.
*Architecture implication:* none. One declaration.
*Direction:* set `font-family` on `.btn-ghost` and every bare control class; cover
`.cat-chip`, `.star`, `.supp-act-btn`, `.add-btn`, `.auth-btn`, `.setup-btn`,
`.log-fab`, `#cw-close`.
*Owner:* **UX4-PR2**

---

**F-11 · The canonical Modal component has zero consumers**
Surface: all · Journey: all · Category: SYSTEM CONSISTENCY · **Severity: P2**
*Evidence:* `.modal-backdrop` / `.modal` are defined and documented in
`docs/design-system.md`; consumers in `templates/` and `static/`: **0**. `.card-hover`:
**0**. `.stat-card`: **1**. `.fc-input`: **3**. Meanwhile the product ships **21
`role="dialog"` surfaces** built from ≥9 class families (`.sheet`, `.modal-overlay`,
`.comments-sheet`, `.sug-modal`, `.scan-overlay`, `.reward-overlay`, `.pump-modal`,
`.gallery-modal`, `.ch-sheet-panel`), of which **1** traps focus and **3** restore it.
*Why it matters:* this is the load-bearing evidence that the problem is **adoption, not
absence**. Writing more components would not help.
*Architecture implication:* none.
*Direction:* do not add components. Migrate dialogs onto the existing Modal/Sheet
primitives, and give those primitives the focus trap, `inert` background and focus
restore once.
*Owner:* **UX4-PR2** (primitive hardening) + each journey PR (its own dialogs)

---

**F-12 · The section micro-label exists in at least five divergent forms**
Surface: Nutrition, Supplements, Account, Coach, Progress · Category: SYSTEM
CONSISTENCY · **Severity: P2**
*Evidence:* `.sec-label` (11 px / 700 / 0.16 em / `text-3` / rule) · `.cat-label`
(10 px / 700 / **0.14 em raw** / `text-3`) · Nutrition's **four inline `<div>` copies**
(11 px / 700 / 0.14 em) · `.hub-section-label` (10 px / 700 / 0.12 em / **`text-4` —
2.22 : 1**) · `.pf-section-title` (white Bebas). Account renders **two of these systems
on one page**.
*Consequence:* the same idea reads five ways, and the three `<div>` variants are exactly
why six surfaces have no `<h2>` (F-17).
*Architecture implication:* none.
*Direction:* one `.sec-label`, always an `<h2>`/`<h3>`; delete the copies.
*Owner:* **UX4-PR2** (canonicalise) + journey PRs (adopt)

---

**F-13 · "AI Fitness Coach" is hardcoded English on the Turkish product**
Surface: Coach · Journey: J7 · Category: COPY · **Severity: P2**
*Evidence:* `static/coach_widget.js:79` (`aria-label`) and `:84` (visible title). Absent
from both `locales/tr.json` and `locales/en.json` (which are otherwise at exact
1 382-key parity, zero gaps in either direction).
*Consequence:* the product's own AI introduces itself in the wrong language on its
canonical surface.
*Direction:* move both strings into the catalogue.
*Owner:* **UX4-PR3**

---

**F-14 · Destination labels and page titles disagree**
Surface: Plan, Progress, Account, Supplements · Category: COPY · **Severity: P2**
*Evidence:* nav "Plan" → `h1` "ANTRENMAN PLANIN" (V2) / "ANTRENMAN PROGRAMI" (legacy);
nav "İlerleme" → `h1` "İLERLEME TAKİBİ"; nav/IA "Account" → `h1` "PROFİL AYARLARI".
Supplements is named **four** ways in one journey: "Takviyeler" (breadcrumb),
"Takviye dolabı" (Nutrition child link), "Supplement Dolabı" (`h1`), "Güncel Supplement
Stack" (Account section).
*Consequence:* the user cannot confirm they arrived where they clicked; Plan's `h1`
names only one of its three children.
*Architecture implication:* none — `app/nav.py` label keys already exist.
*Direction:* one name per destination, derived from the nav label key.
*Owner:* **UX4-PR2** (decide) + journey PRs (apply)

---

**F-15 · `/nutrition`'s page title is also the name of its own third tab**
Surface: Nutrition · Journey: J5 · Category: PRODUCT UX · **Severity: P2**
*Evidence:* `h1` = "BESLENME PLANI"; tab 3 = "Beslenme Planı"; the parent destination is
"Plan".
*Consequence:* three nested things called "plan"; the daily-intake vs planned-target
distinction the tab exists to express is destroyed by the naming.
*Direction:* rename the page to its domain ("Beslenme") and keep "Beslenme Planı" for
the tab only.
*Owner:* **UX4-PR6**

---

**F-16 · Unknown nutrition targets render as stacked em-dashes**
Surface: Nutrition · Journey: J4 · Category: STATE · **Severity: P3**
*Evidence:* `HEDEF: — KCAL` above a ring whose sub-label is a bare `—`.
*Consequence:* honest, but reads as breakage rather than as "not set yet".
*Architecture implication:* wording and layout only — `nutrition_targets` remains the
sole authority and no fallback target may be invented.
*Owner:* **UX4-PR6**

---

**F-17 · Six of nine primary surfaces have no heading below `h1`**
Surface: Nutrition, Supplements, Coach, Gallery, Account, Notifications · Category:
ACCESSIBILITY · **Severity: P2**
*Evidence:* measured outlines — Progress `h1 h2 h2 h3 h3 h3 h2 h3 h3 h3 h2 h2`, Plan V2
`h1 h2 h2 h2 h2 h2 h3`, Today `h1 h2 h2`; the other six: **`h1` only**. Landing skips
`h1 → h3`.
*Cause:* the surfaces that use `.sec-label` get real headings; the surfaces that
hand-rolled it from a `<div>` (F-12) lost them.
*Direction:* adopting the component restores both the look and the structure.
*Owner:* journey PRs

---

**F-18 · `--color-text-4` is used as text and fails contrast**
Surface: Coach, Nutrition, Account · Category: ACCESSIBILITY · **Severity: P2**
*Evidence:* `.cw-ts` 1.97 : 1 (every Coach message timestamp), `.mc-time` 1.97 : 1,
`.hub-section-label` 2.22 : 1; all need 4.5 : 1. `--color-text-3` measures 5.22 : 1 and
is fine.
*Direction:* a usage rule — `--color-text-4` is a hairline/decoration value, never text.
Replace these three with `--color-text-3`. No palette change.
*Owner:* **UX4-PR2**

---

**F-19 · Supplement status renders as a raw English database value on Account**
Surface: Account · Journey: J9 · Category: COPY · **Severity: P3**
*Evidence:* the same row renders **"AKTİF"** on `/supplements` and **"ACTİVE"** on
`/edit-profile`.
*Direction:* route both through the existing status label keys.
*Owner:* **UX4-PR7**

---

**F-20 · Emoji are shipped as interface iconography**
Surface: Supplements, Notifications, Account · Category: VISUAL · **Severity: P2**
*Evidence:* 🥤💧⚡💊💪📦 as Supplements' canonical category icons
(`app/blueprints/supplements.py:158`); ❤️💬👋🤝🔁🏆🔔 as Notifications' canonical type
icons and empty state; 🔥 on Account; `★`/`☆` ×32 as a rating control and display.
`docs/design-system.md` explicitly forbids emoji as interface icons.
*Consequence:* two of four utility/primary surfaces speak a different visual language
from the rest of the product; emoji cannot take a state colour or respond to theme.
*Direction:* replace with the existing 1.8-stroke SVG set and `.icon-tile`; build the
rating control from icons rather than glyphs.
*Owner:* **UX4-PR6** (Supplements) + **UX4-PR7** (Notifications, Account)

---

**F-21 · The secondary button is 32 px tall and has no designed focus or disabled state**
Surface: all · Category: INTERACTION · **Severity: P2**
*Evidence:* measured `.btn-ghost` **32 px** at 390 / **35 px** at 1366 vs `.btn-volt`
**45 px**; radius 8 vs 12; Arial vs Bebas. No `:focus-visible`, `:disabled` or loading
rule on any of `.btn-volt` / `.btn-ghost` / `.btn-danger`. Across all stylesheets:
`:hover` **108**, `:active` **25**, `:disabled`/`[disabled]` **16**. Measured focus
outcomes: **20 distinct** (outline, shadow) pairs across 8 surfaces; **10 stops** fall
back to the browser's grey default ring.
*Consequence:* the product's secondary action is below the touch guideline everywhere,
does not read as the same family as its primary, and in-flight and unavailable states
are undesigned.
*Direction:* a four-rank hierarchy (primary / secondary / quiet / destructive) with one
shared control height, one radius rule, one face, and complete
`hover · pressed · focus · disabled · loading` coverage.
*Owner:* **UX4-PR2**

---

**F-22 · Account's three strongest actions all sell or connect something**
Surface: Account · Journey: J9 · Category: PRODUCT UX · **Severity: P2**
*Evidence:* three `.btn-volt` — "Premium'a Yükselt" (316 × 45) and two wearable "Bağla";
"Profili Düzenle", the page's stated purpose, is a **32 px ghost button below the
upsell**. Both wearable cards display "BAĞLI DEĞİL" with a primary-rank connect CTA.
*Consequence:* the strongest visual element on the user's own account page is an upsell.
*Architecture implication:* re-ranking is pure UX. **Whether the wearable integrations
are functional and publicly claimable is a product question and is referred, not
resolved** — UX4 does not change their behaviour.
*Owner:* **UX4-PR7** (hierarchy) · product (integration claims)

---

**F-23 · Progress interprets and then offers nothing to do**
Surface: Progress · Journey: J8 · Category: PRODUCT UX · **Severity: P2**
*Evidence:* **zero** primary-filled controls; two 33 px ghost buttons. The "SONRAKİ
ADIM / Next Move" slot states the next step in prose and carries no control.
*Consequence:* the clearest interpretation→action gap in the product.
*Architecture implication:* **constrained.** UX4-PR7 may raise the rank of an existing
action and link the slot to an existing destination. Making the slot's *own* next move
actionable requires `progress_insights` to emit a target — a backend change,
**deferred**.
*Owner:* **UX4-PR7**

---

**F-24 · Canonical honesty is rendering as visible repetition**
Surface: Progress · Journey: J8 · Category: VISUAL · **Severity: P3**
*Evidence:* "TEMEL OLUŞUYOR" appears **4×** on one screen at three sizes; "Bunu
söylemek için yeterli antrenman geçmişi yok." appears **verbatim twice** in two adjacent
Axis Insights slots. At 1366 the two bottom sections are ~1 232 × 200 px boxes holding
one centred sentence each.
*Consequence:* the right architecture reads as a bug.
*Architecture implication:* none — the same payload, differently composed. Do **not**
introduce a second sentence source.
*Owner:* **UX4-PR7**

---

**F-25 · Today offers no action after completion or on a rest day**
Surface: Today · Journey: J2 · Category: PRODUCT UX · **Severity: P2**
*Evidence:* `today-done` and `today-rest` fixtures render **0** primary-filled controls.
*Consequence:* the best moment in the product — you finished — offers nothing next.
*Architecture implication:* the canonical Today projection already distinguishes these
states; the secondary-action list already exists.
*Owner:* **UX4-PR7**

---

**F-26 · The z-index scale is bypassed; the Coach widget outranks toasts**
Surface: Coach, Nutrition, Today, and others · Category: SYSTEM CONSISTENCY ·
**Severity: P2**
*Evidence:* **17 raw `z-index` values vs 20 token uses.** `#cw-root` **9998** (measured
live on `/coach` and `/nutrition`) and `#cw-fab` **9999**, against a documented scale
topping out at `--z-toast: 400`. Also `.reward-overlay` 1000, `.chat` 500,
`.nutrition` 100, `premium`/`quests` 9999.
*Consequence:* a toast raised while Coach is open renders behind it — so confirmation of
a Coach-initiated action can be invisible, on the surface where confirmation matters
most.
*Direction:* bring every layer onto the `--z-*` scale; the widget belongs below toast.
*Owner:* **UX4-PR2** (scale) + **UX4-PR3** (widget)

---

**F-27 · Coach body text is 13.5 px at weight 300**
Surface: Coach · Journey: J7 · Category: VISUAL · **Severity: P2**
*Evidence:* `.cw-bubble { font-size: 13.5px; font-weight: 300; line-height: 1.62 }`;
`#cw-input` 13.5 px. Half-pixel sizes 13.5 / 11.5 / 10.5 / 9 px appear only in this
stylesheet.
*Consequence:* the longest text in the product renders in its lightest, smallest style;
a sub-16 px input triggers focus-zoom on iOS.
*Owner:* **UX4-PR3**

---

**F-28 · The retired palette survives on the Coach surface**
Surface: Coach · Journey: J7 · Category: VISUAL · **Severity: P3**
*Evidence:* `--color-chat-avatar-accent: #99CC00` (the AI avatar is a blue→lime
gradient) and `--color-chat-send-hover: #D6FF1A` (send turns lime on hover), in a
blue-primary product. `docs/design-system.md` already records these as known deviations.
*Owner:* **UX4-PR3**

---

**F-29 · Off-scale radii and spacing are pervasive, including inside the component library**
Surface: all · Category: SYSTEM CONSISTENCY · **Severity: P3**
*Evidence:* **12 distinct rendered radii** (4 · 6 · 8 · 9 · 10 · 11 · 12 · 14 · 16 · 20 ·
50 % · 9999) against a 6-step scale; **10 px is the most common authored raw radius
(27×) and is not in the scale**. 126 raw vs 192 tokenised `border-radius` declarations;
**77 distinct padding values** on card-like surfaces; **43** on interactive controls.
`components.css` itself ships `.tab-btn { border-radius: 9px; padding: 10px …; gap: 7px }`
and `.cat-label { letter-spacing: 0.14em }` — **the library does not obey its own
scale**, which is why page-by-page correction cannot converge.
*Owner:* **UX4-PR2** (library) + journey PRs (pages)

---

**F-30 · Desktop composition is unconsidered in both directions**
Surface: Today, Plan, Coach, Progress · Category: RESPONSIVE · **Severity: P2**
*Evidence:* at 1366 × 900 — Coach ~87 % empty; Today ~44 % empty below a 46 rem column;
Plan V2 a ~690 px column centred in 1 366 px and 1 972 px tall; Progress stretched to
1 232 px so a one-sentence empty state sits alone in a 1 232 × 200 px box.
*Consequence:* two opposite failures at once — too narrow and too wide — with no
composed desktop layout anywhere.
*Architecture implication:* none. Same payloads.
*Owner:* **UX4-PR3 / PR5 / PR7** per surface

---

**F-31 · Navigation expresses itself as two different products across 1024 px**
Surface: all · Category: SYSTEM CONSISTENCY · **Severity: P3**
*Evidence:* `.hn-link` — 12 px, 600, `--tracking-wide` (0.04 em), sentence case, no
icon, filled pill when active. `.ab-tab` — 10 px, 700, `--tracking-wider` (0.08 em),
**UPPERCASE**, 22 px icon above, colour-only active state. Same four destinations.
*Consequence:* crossing one breakpoint changes the product's navigation identity.
*Owner:* **UX4-PR2**

---

**F-32 · A full DM Sans family is downloaded to serve four elements**
Surface: all · Category: VISUAL / performance · **Severity: P3**
*Evidence:* `_head.html` requests Bebas Neue + **DM Sans (5 weights + italic)** + Inter
(300–800) in one blocking stylesheet; DM Sans is a fallback in the token stack and
rendered **4 times** across 118 cells.
*Owner:* **UX4-PR2**

---

**F-33 · Motion is duplicated rather than systematised**
Surface: all · Category: INTERACTION · **Severity: P3**
*Evidence:* **31 `transition: all`** declarations across 9 stylesheets; **19
`@keyframes`** including near-duplicates (`slide-up` **and** `slideUp`, `spin` **and**
`nut-spin`, `pulse` **and** `pulse-glow`, three shimmer implementations). Only **11 of
23** stylesheets carry a `prefers-reduced-motion` block — and **`coach_widget.css`, the
most animated surface in the product, is not one of them.**
*Owner:* **UX4-PR2** (tokens, reduced-motion coverage) + **UX4-PR3** (widget)

---

**F-34 · Nutrition carries 17 inline `style` font declarations**
Surface: Nutrition · Category: SYSTEM CONSISTENCY · **Severity: P3**
*Evidence:* `templates/nutrition.html` lines 25–417 — 17 inline `font-size`
declarations, including a one-off `30px` calorie-ring numeral where `--text-metric-*`
exists, and four hand-rolled `.sec-label` copies. Inline styles cannot be overridden by
any stylesheet or theme.
*Owner:* **UX4-PR6**

---

**F-35 · Nutrition's local tab bar hides two of five workflows at 390 px**
Surface: Nutrition · Journey: J4 · Category: RESPONSIVE · **Severity: P3**
*Evidence:* 3.5 of 5 tabs visible at 390 px. The overflow is intentional and
scroll-snapped and **is not reported as a bug**; the consequence is that History and
Water are undiscoverable on arrival at the most common width.
*Direction:* keep the scroller; make the affordance explicit, or shorten labels so five
fit.
*Owner:* **UX4-PR6**

---

**F-36 · Destructive actions carry no distinct rank**
Surface: Supplements, Nutrition · Category: INTERACTION · **Severity: P2**
*Evidence:* on Supplements, **Sil** is the fourth of four visually identical ghost
buttons beside three status toggles, in Arial 11 px, with the browser's default focus
ring. `.btn-danger` is not used. On Nutrition, `.mc-del` is an icon button with **no
focus indicator** (F-03). `.btn-danger` is itself identical to `.btn-volt` apart from
hue (F-21).
*Consequence:* destroy sits at the same visual rank as toggle, one tab away, with no
focus indication.
*Owner:* **UX4-PR2** (rank) + **UX4-PR6** (apply)

---

**F-37 · Selected filter chips are painted as primary calls to action**
Surface: Supplements, Account · Category: VISUAL · **Severity: P3**
*Evidence:* measured primary-filled controls on Supplements: `.cat-chip.active` and the
status chip carry the **same solid primary fill** as a CTA — so a selected filter
outranks "EKLE". Same on `.hub-lang-opt.on`.
*Owner:* **UX4-PR2** (selection vs action language) + journey PRs

---

**F-38 · `role="dialog" aria-modal="true"` without a focus trap**
Surface: Today, Plan, Nutrition, Progress, Account, Challenges, Feed, Gallery ·
Category: ACCESSIBILITY · **Severity: P2**
*Evidence:* 21 dialogs, `aria-modal` on 9, focus trap in **1**, focus restore in 3,
`inert` in 2. Today's reward overlay focuses its button, handles `Escape`, and neither
traps nor restores focus.
*Direction:* implement once on the shared Modal/Sheet primitive (F-11) rather than 21
times.
*Owner:* **UX4-PR2**

---

**F-39 · Nutrition loads the entire Coach widget**
Surface: Nutrition · Category: SYSTEM CONSISTENCY · **Severity: P3**
*Evidence:* `/nutrition` requests `coach_widget.js` (41 KB) + `coach_widget.css`
(16 KB) + `GET /coach/history`, and mounts `#cw-root` as a fixed **z-index 9998**
element, on a page whose own `nutrition.js` is 81 KB.
*Direction:* record for the query/asset budget; decide during UX4-PR3 whether the
floating widget still belongs on Nutrition now that Coach is a destination.
*Owner:* **UX4-PR3**

---

**F-40 · 404 and 500 render a 120 px numeral**
Surface: error pages · Journey: J10 · Category: VISUAL · **Severity: P3**
*Evidence:* measured `h1` = **120 px**, against a product whose next-largest type is
52 px; one link, no context.
*Owner:* **UX4-PR8**

---

## R. Severity summary

| Severity | Count | IDs |
|---|---|---|
| **P0** | **0** | — |
| **P1** | **5** | F-01, F-02, F-03, F-04, F-05 |
| **P2** | **21** | F-06, F-07, F-08, F-09, F-10, F-11, F-12, F-13, F-14, F-15, F-17, F-18, F-20, F-21, F-22, F-23, F-25, F-26, F-27, F-30, F-36, F-38 |
| **P3** | **14** | F-16, F-19, F-24, F-28, F-29, F-31, F-32, F-33, F-34, F-35, F-37, F-39, F-40 |

**No P0.** Nothing found in this audit is a security, privacy, data-loss or
unrecoverable-destructive-UX defect. The two P1 accessibility findings (F-02, F-03) are
P1 rather than P2 because they remove a user's ability to see where they are on a
control that deletes their data; the three remaining P1s are P1 because they damage the
acquisition surface (F-01) and the product's core differentiator (F-04, F-05).

---

## S. Prioritised top 10

Scored 1–5 on user consequence, frequency, launch criticality, perceived-quality
impact, cross-surface leverage, architectural safety, implementation scope (5 = small),
testability.

| # | ID | Surface | Sev | Why now | User consequence | Perceived-quality consequence | Owner | Score |
|---|---|---|---|---|---|---|---|---|
| 1 | **F-03** | Nutrition, Progress | P1 | Three declarations remove focus from five controls, one of which deletes data. Cheapest high-severity fix in the audit. | Keyboard users cannot see what they are about to activate | Invisible to most, disqualifying to some | PR2 | 36/40 |
| 2 | **F-02** | all | P1 | The system's own focus token is 1.09 : 1; every fix downstream depends on it | No visible focus anywhere it is used | Focus quality is a primary craft signal | PR2 | 35/40 |
| 3 | **F-01** | Login / Register | P1 | Zero padding on the product's first desktop screen, from one undefined token | The sign-up surface looks broken | Severe and unrecoverable at first contact | PR2 | 35/40 |
| 4 | **F-04** | Coach | P1 | A primary destination renders an empty page with a chat bubble; 87 % of a desktop viewport is unused | The core differentiator reads as an accessory | The largest single gap in the product | PR3 | 32/40 |
| 5 | **F-10** | all | P2 | The secondary button renders in Arial on every surface; one declaration | Subtle but constant wrongness | Systemic "cheap" signal | PR2 | 32/40 |
| 6 | **F-07** | Plan | P2 | A new user's first act is a 2 758 px, 48-option form | Highest-drop-off moment in the funnel | "Settings screen", not "AI coach" | PR4 | 30/40 |
| 7 | **F-21** | all | P2 | Primary and secondary share no geometry; pressed/disabled/focus undesigned (hover 108 : active 25 : disabled 16) | Actions do not read as ranked | The difference between "styled" and "designed" | PR2 | 30/40 |
| 8 | **F-08** | Supplements | P2 | 48 tab stops, 40/47 sub-44 px targets, emoji icons, `★` glyphs, Arial controls | The cabinet sits below a long form | Reads as an internal admin tool | PR6 | 28/40 |
| 9 | **F-06** | Plan | P2 | The converged Plan is twice as tall as the renderer it replaced; nesting depth 3 | 2–3 screens of scrolling, mostly rest days | The dominant "everything is a box" instance | PR5 | 27/40 |
| 10 | **F-23** | Progress | P2 | The page interprets well and offers nothing to do | Users have no reason to return | Breaks Guide→Action→Feedback on a whole destination | PR7 | 26/40 |

Ranking notes: F-09 (permanent loading) and F-11 (zero-adoption Modal) rank 11 and 12
and are scheduled. Cosmetic items with high counts — off-scale radii (F-29), motion
duplication (F-33) — deliberately rank **below** journey problems despite being easy;
they ride along inside PR2 rather than displacing anything.

---

## T. Foundation-PR justification — **YES, bounded**

§26 requires three things to be proven before a foundation PR is justified.

**1. Multiple surfaces suffer from the same root inconsistency.** Proven, repeatedly:

| Root cause | Surfaces affected | Sites |
|---|---|---|
| `--focus-ring` at 1.09 : 1 | all | 11 |
| `outline: var(--focus-ring…)` | Nutrition, Progress | 3 rules → 12 controls |
| `.btn-ghost` has no `font-family` | Plan, Progress, Account, Nutrition, Supplements | 1 declaration → 41 elements |
| No `:disabled` / `:focus-visible` on the canonical buttons | all | 3 classes |
| Section micro-label divergence | Nutrition, Supplements, Account, Progress, Coach | ≥5 variants |
| Off-scale radius / spacing | all | 126 raw radii, 77 padding values |
| `z-index` scale bypassed | Coach, Nutrition, Today, Chat, Premium, Quests | 17 raw values |
| `aria-modal` without a trap | 8 surfaces | 21 dialogs, 1 trap |

**2. Page-by-page fixing would duplicate work.** Directly demonstrable. Fixing
`.btn-ghost`'s typeface on Account leaves it broken on Plan, Progress, Nutrition and
Supplements; fixing the focus token once repairs 11 sites and 12 controls across two
surfaces. Conversely, five separate teams repairing five section-label variants produce
a sixth variant. And the decisive evidence: **`components.css` itself violates the
scale** (`.tab-btn { border-radius: 9px; gap: 7px }`, `.cat-label { letter-spacing:
0.14em }`), so pages that *correctly* adopt the library still inherit off-scale values.
Page-level work cannot converge while the library is the source of the drift.

**3. A bounded foundation layer can improve consistency without a design-system
rewrite.** This is the constraint that shapes the PR, and the evidence says the layer
must be **corrective, not expansive**:

- The token file is good. 239 custom properties, a 15-step type ladder, a metric scale,
  an 8-pt grid, a motion scale, a z-scale, a documented light theme. **Exactly one token
  is undefined** and **exactly one has a wrong value.**
- The component library is good and **under-adopted**: Modal **0** consumers,
  `.card-hover` **0**, `.stat-card` **1**, `.fc-input` **3**, `.skeleton` **5** — while
  21 bespoke dialogs exist.
- Therefore the correct foundation PR **adds no new tokens and no new components.** It
  repairs two token values, deletes three invalid declarations, completes the state
  coverage of three existing button classes, hardens one existing Modal primitive, and
  adds regression gates.

**Conclusion: a foundation PR is justified — as repair and adoption.** Discovery
explicitly rejects the expansive reading of §26: no new typography scale, no new spacing
tokens, no new radius system, no new component library, no new font. Any UX4-PR2 that
mints a token is out of scope by this document's own definition.

---

## U. Candidate implementation objectives

1. Every interactive control in the product has a visible, consistent, WCAG-conformant
   focus indicator.
2. Actions read as exactly four ranks — primary, secondary, quiet, destructive — with
   one control height, one radius rule, one typeface, and complete
   hover/pressed/focus/disabled/loading coverage.
3. Coach is a destination whose page *is* the conversation, with entry context and a
   route back into Plan.
4. A new user's first act with AxisAI feels coached, not configured.
5. Plan expresses two levels of ownership with one level of surface, and rest reads as
   rest.
6. Nutrition and Supplements consume the design system rather than re-implementing it —
   which restores their heading structure as a side effect.
7. Progress offers a next step, and canonical honesty stops rendering as repetition.
8. Every surface is composed for the desktop canvas it is given.
9. Zero horizontal overflow at 320–1366 in TR and EN is *protected by assertion*, not
   just currently true.

---

## V. Recommended UX4 PR decomposition

The brief's candidate sequence is re-ordered on evidence. Two changes: **Coach moves
ahead of Today and Plan** because it is the only primary destination that has never been
designed as one and it scores lowest on premium feel; and **Today does not get its own
PR** because it already scores 4 and its two defects (F-25 no post-completion action,
F-30 desktop composition) are small enough to ride with Progress. Supplements is folded
into the Nutrition PR because they are one journey (`Plan → Nutrition → Supplements`)
and share one debt profile.

---

### UX4-PR2 — Premium control & focus foundations *(corrective)*

**Objective.** Repair the two broken token values, delete the three declarations that
erase focus, complete the state coverage of the three canonical buttons, harden the one
Modal primitive, and gate all of it.
**User consequence.** Every control in the product becomes visibly focusable, visibly
ranked, and visibly disabled when it is.
**Surfaces.** `tokens.css`, `components.css`, `nav.css`, `auth.css`, `nutrition.css`,
`progress.css`, `_head.html`.
**Dependencies.** None. Must land first.
**Architecture boundary.** No template logic, no route, no schema, no flag, no new
token, no new component, no font import.
**Scope.** F-01, F-02, F-03, F-10, F-12 (canonicalise), F-14 (decide names), F-18,
F-21, F-26, F-29 (library only), F-31, F-32, F-33 (tokens + reduced-motion), F-36
(rank), F-37, F-38, F-11 (primitive hardening only).
**Tests.** Every `var(--*)` reference resolves to a defined property; `--focus-ring` is
never an `outline` value; `--focus-ring` meets 3 : 1 against all four surface tokens;
`.btn-volt`/`.btn-ghost`/`.btn-danger` each declare `font-family` and have
`:focus-visible` + `:disabled` rules; a keyboard walk asserts a visible indicator on
every stop of three surfaces; no horizontal overflow at 320–1366 TR/EN.
**Non-goals.** Any page layout change. Any new token. Any visual redesign.
**Rollback.** Pure CSS; revert the commit. No flag needed.
**Exit criteria.** Zero controls compute `outline-style: none` under `:focus-visible`
across the eight primary surfaces; `.auth-card` padding is non-zero at 768/1024/1366;
zero elements compute to Arial.

---

### UX4-PR3 — Coach as a destination

**Objective.** Give the Coach destination a page: the conversation in the content
column, entry context, visible capability affordances, a route back to Plan, correct
keyboard order, catalogue copy, and the system palette.
**User consequence.** The product's differentiator stops reading as a support widget.
**Surfaces.** `coach.html`, `coach_v2.html`, `coach_widget.css`, `coach_widget.js`
(presentation and DOM order only), `nav.css` (`.coach-page`), `locales/*.json`.
**Dependencies.** PR2.
**Architecture boundary.** **Hard.** No change to `/ask`, `/ask/stream`, `ai_pipeline`,
`prompt_builder`, `coach_plan_tools`, conversation persistence, streaming frames, or
mutation authority. One widget implementation remains; UX4 changes where it lives and
how it is composed. Nothing may make Coach a second Plan writer.
**Scope.** F-04, F-05, F-13, F-26 (widget layer), F-27, F-28, F-30 (Coach), F-33
(widget reduced-motion), F-39 (decide).
**Tests.** Coach's document height exceeds the viewport at 1366 (i.e. the page has
content); tab order reaches the composer before the send control; the composer has an
accessible name; `#cw-fab` is not focusable when hidden; no hardcoded "AI Fitness Coach"
in `static/`; widget z-index is below `--z-toast`; no horizontal overflow 320–1366
TR/EN.
**Non-goals.** Any AI behaviour, prompt, provider, streaming or tool change. Any Coach
mutation-authority change.
**Rollback.** `UIUX_COACH_PAGE_V2_ENABLED` already exists and already selects a Coach
page branch — use it. Keep the floating widget's other-page behaviour untouched so
rollback is a page-level revert.
**Exit criteria.** Coach captured at 320/390/768/1024/1366 in TR and EN with the
conversation in the content column and the keyboard order verified.

---

### UX4-PR4 — Plan first run

**Objective.** Turn the no-plan Plan surface from a configuration form into a coached
first step: progressive disclosure, defaults preselected, a stated outcome, a persistent
submit, and an honest empty stat strip.
**User consequence.** A new user understands what AxisAI is about to build for them.
**Surfaces.** `plan.html` (`plan_manage` macro), `plan.css`,
`plan_training_manage.js` (presentation only), `locales/*.json`.
**Dependencies.** PR2.
**Architecture boundary.** **Presentation only.** The same single form, the same single
submit, the same server-authoritative proposal, the same `management_baseline`
precondition. **No client-held multi-step draft state** and no second plan writer.
**Scope.** F-07, plus F-14 for Plan's own title.
**Tests.** The create form still posts exactly once with the same field set; the
baseline precondition is unchanged; every option remains reachable by keyboard; the
submit is reachable without scrolling past it at 320/390; no horizontal overflow.
**Non-goals.** Plan generation, validation, the injury contract, the exercise authority.
**Rollback.** Template + CSS revert; `UIUX_PLAN_V2_ENABLED` still gates the whole
surface.
**Exit criteria.** `no_active_plan` captured at all five viewports, TR and EN, with the
390 px document height materially reduced and the submit persistently reachable.

---

### UX4-PR5 — Plan surface hierarchy

**Objective.** One card per ownership boundary; rest days as quiet rows; sections
separated by rhythm and type; a composed desktop layout.
**User consequence.** The weekly program is readable in one or two screens instead of
three.
**Surfaces.** `plan.html`, `plan.css`.
**Dependencies.** PR2, PR4 (same files).
**Architecture boundary.** No change to the Plan payload, `workout_state`,
`workout_session`, `/training/bootstrap`, or the weekly-program consumer contract.
**Scope.** F-06, F-30 (Plan), plus the centred-link alignment inconsistency at 320 px.
**Tests.** Nesting depth ≤ 2; document height at 390 reduced against the recorded
baseline; the start/resume action stays above the fold; **Resume, session-conflict and
stale states captured at all five viewports** (this PR closes the J3 evidence gap);
no horizontal overflow.
**Non-goals.** Nutrition and Supplements content inside the Plan domain cards.
**Rollback.** `UIUX_PLAN_V2_ENABLED`.
**Exit criteria.** J3 has a real score in the follow-up report.

---

### UX4-PR6 — Nutrition & Supplements

**Objective.** Make one journey out of `Plan → Nutrition → Supplements`: adopt the
design system, restore heading structure, put the cabinet before the form, replace
glyph and emoji iconography, resolve the naming collision.
**User consequence.** The two highest-frequency workflows stop looking like a different
product.
**Surfaces.** `nutrition.html`, `nutrition.css`, `manage_stack.html`,
`manage_stack.css`, `locales/*.json`, `app/blueprints/supplements.py`
(`CATEGORY_ICONS` only).
**Dependencies.** PR2.
**Architecture boundary.** Nutrition writes remain the canonical `MealLog` /
`mobile_diary_mutation` path. `POST /supplement/{add,edit,delete}` in
`manage_stack.html` remains the **only** mutation authority —
`tests/test_ux3_pr5_supplements_placement.py` enforces this and must stay green. Zero
added queries. No invented nutrition target.
**Scope.** F-08, F-15, F-16, F-17 (these two surfaces), F-20 (Supplements), F-34,
F-35, F-36 (apply).
**Tests.** The placement test stays green; no new Supplement query on `/nutrition`;
zero inline `style="font-size"` in `nutrition.html`; both surfaces have an `h2`;
Supplements' tab-stop count materially reduced; touch targets ≥ 44 px on both; no
horizontal overflow 320–1366 TR/EN.
**Non-goals.** Food search, barcode, the menu scanner, diary mutation semantics.
**Rollback.** Template + CSS revert; both surfaces are unflagged, so this PR must be
independently revertible by commit.
**Exit criteria.** Supplements' perceived-quality score moves off 1.

---

### UX4-PR7 — Progress action, Today continuity, Account & Notifications hierarchy

**Objective.** Give Progress a next step and stop its honesty reading as repetition;
give Today an action after completion and on rest days; re-rank Account; fix
Notifications' error state and iconography.
**User consequence.** Every destination answers "what now?".
**Surfaces.** `progress.html`, `progress.css`, `today.html`, `today.css`,
`edit_profile.html`, `profile.css`, `notifications.html`, `notifications.css`,
`locales/*.json`.
**Dependencies.** PR2.
**Architecture boundary.** **Tight.** Progress may raise the rank of an existing action
and link the Next Move slot to an existing destination — it may **not** require
`progress_insights` to emit a new field (deferred, §P). Today's secondary-action list
and canonical state projection are unchanged. No second sentence source anywhere.
**Scope.** F-09, F-19, F-20 (Notifications, Account), F-22 (hierarchy only), F-23,
F-24, F-25, F-30 (Today, Progress).
**Tests.** Notifications renders an error state with a retry on a failed fetch (this is
the one genuinely new behavioural assertion in the roadmap); Progress exposes exactly
one primary-rank action; Today exposes an action in the completed and rest states; the
supplement status label is identical on `/supplements` and `/edit-profile`; no emoji in
shipped interface iconography on these surfaces.
**Non-goals.** Pump Check capture, the gallery, the comparison engine, wearable
behaviour.
**Rollback.** Per-surface commit revert.
**Exit criteria.** J8's Action score moves off 2.

---

### UX4-PR8 — Cross-surface regression & consistency gate

**Objective.** Convert this document's measurements into standing assertions so UX4
cannot silently regress, and clear the residual polish.
**Surfaces.** `tests/`, plus `404.html` / `500.html`.
**Dependencies.** PR2–PR7.
**Architecture boundary.** Tests and two error templates only.
**Scope.** F-40, plus the consistency gates listed in §X.
**Tests.** The full matrix in §X.
**Non-goals.** Any new visual change beyond the error pages.
**Rollback.** Test-only.
**Exit criteria.** The §X gate runs in CI and fails on a deliberate regression.

---

## W. Dependencies

```
UX4-PR2  (foundations)
   ├── UX4-PR3  Coach
   ├── UX4-PR4  Plan first run ──► UX4-PR5  Plan surface   (same files, strict order)
   ├── UX4-PR6  Nutrition + Supplements
   └── UX4-PR7  Progress · Today · Account · Notifications
                                   └── UX4-PR8  regression gate (after all)
```

PR3, PR4, PR6 and PR7 touch disjoint files and can run in parallel once PR2 lands.
PR5 must follow PR4 because both edit `plan.html` and `plan.css`. Every PR is
independently revertible.

### WEB-UX3-PR6B — when it becomes safe (determined, not implemented)

Discovery was asked to determine *when*, and the evidence supports a clear answer.

Both branches were captured. The legacy Plan renderer is **materially worse** — 12
distinct text sizes against Plan V2's 8–9, and a single `h1` against V2's full
`h1 → h2 → h3` outline — and it is a **separate 32 KB stylesheet and 81 KB script**.

The consequence for UX4 is concrete. **From UX4-PR4 onward, the rollback target stops
being equivalent to the thing it would roll back to.** Every Plan improvement would
either have to be duplicated into `training.html`/`training.css` (doubling PR4 and PR5
and re-introducing exactly the drift UX-3 removed) or the rollback path would silently
serve a materially worse Plan. A rollback that lands users on a worse surface is not a
safety net; it is an untested second product.

**Recommendation: schedule PR6B between UX4-PR2 and UX4-PR4** — after the foundation
lands (so the flag retirement is evaluated against a repaired system) and before any
Plan layout change makes the branches diverge further. The preconditions are the ones
already in `docs/ROLLOUT.md`: Plan V2 production-visible through a full release cycle
with no rollback invoked, and the deploy health gate green across that window.

**PR6B is not implemented, not started and not scheduled by this PR.**
`UIUX_PLAN_V2_ENABLED` is not retired, the legacy renderer is not deleted, and the
rollback branch remains intentionally available.

---

## X. Test strategy

What later implementation will require. Nothing in this list is added by this
discovery PR.

**Semantic DOM.** Every primary surface has exactly one `h1` and at least one `h2`;
no heading level is skipped; every input has an associated `<label>`, `aria-label` or
`aria-labelledby`; every icon-only control has an accessible name; `.sec-label` is
rendered as a heading element.

**Token integrity (new class of gate, highest value per line).**
Every `var(--*)` reference in `static/**` and `templates/**` resolves to a defined
property — this alone would have caught F-01. `--focus-ring` never appears as an
`outline` value — this alone would have caught F-03. `--focus-ring` composited over
`--color-bg`/`--surface-1/2/3` meets 3 : 1. `--color-text-4` is never used as `color`.
Each canonical button class declares `font-family` and has `:focus-visible` and
`:disabled` rules. Every `z-index` is a `--z-*` token.

**Interaction.** Keyboard walk per surface asserting: order matches visual order, every
stop has a visible indicator, no `opacity: 0` element is focusable, dialogs trap focus
and restore it to the opener, `Escape` closes.

**Browser E2E / responsive.** The 320 · 390 · 768 · 1024 · 1366 × TR/EN matrix as a
standing assertion: no horizontal overflow (currently clean — protect it), no clipped
copy, the primary action reachable without scrolling past a competing control.

**Visual regression candidates** (highest value, lowest flake): Today all four states ·
Plan V2 active + no-plan · Coach at 390 and 1366 · Supplements cabinet · Progress
1366 empty states · Login at 768.

**State assertions.** Every high-frequency component renders its loading, empty,
partial, error and disabled states; Notifications renders an error with a retry;
Nutrition never renders `0` for an unknown value.

**Partial-failure isolation.** One failing read degrades one section, never the page —
already true on Progress and Today; assert it.

**Query / asset budgets.** Plan fact gathering stays at 7/9 SELECTs; `/nutrition` issues
no Supplement query; record the per-page script and font payload so F-32 and F-39 cannot
regress.

**i18n.** `locales/tr.json` and `locales/en.json` stay at exact key parity (currently
1 382 each, zero gaps); no hardcoded user-visible English in `static/**`; no raw
database enum values rendered as labels.

**Governance.** This discovery PR is docs-only; the repository's existing docs and
governance checks apply and no new test is added here.

---

## Y. Rollback philosophy

Every UX4 PR must be revertible **without** a flag, because most of these surfaces are
unflagged today. Concretely:

- **PR2** is pure CSS: `git revert` restores the previous visual state exactly.
- **PR3** reuses the **existing** `UIUX_COACH_PAGE_V2_ENABLED`, which already selects a
  Coach page branch. No new flag is minted.
- **PR4/PR5** sit behind the **existing** `UIUX_PLAN_V2_ENABLED` for as long as it
  lives — which is precisely why §W recommends resolving PR6B before PR4.
- **PR6/PR7** are unflagged and must therefore be **small enough to revert by commit**.
  That is a scope constraint on those PRs, not an afterthought.
- **No new feature flag is proposed by this roadmap.** The registry already carries two
  UI flags whose retirement is overdue; adding a third would make that worse.

---

## Z. Explicitly out of scope

- **WEB-UX3-PR6B** — flag retirement and legacy-renderer deletion. Determined (§W), not
  started. `UIUX_PLAN_V2_ENABLED` is not retired; the legacy Plan renderer and rollback
  branch remain.
- **MOB-S15-PR7** and all Flutter work. Not inspected beyond recording that nothing in
  this roadmap requires a change to `/api/v1` `WorkoutSession` semantics, native auth,
  mobile transport, the terminal reread contract, or Flutter composition.
- **Mobile Progress / Coach work.** Cross-platform follow-up only.
- **Distribution and store work.**
- **AI behaviour** — prompts, providers, streaming, tool loops, Coach mutation
  authority, adaptive plan context.
- **Backend authority changes** — including the two deferred items in §P (a target field
  on Progress's Next Move slot; contextual Coach→Plan deep links).
- **Community surfaces** (Feed, Friends, Club, Quests, Challenges, Chat, Premium).
  They were censused — together they hold 87 of the 190 raw `font-size` declarations and
  62 of the 126 raw radii — but they are secondary destinations reached through Account
  and are not part of the four primary journeys. Recorded for a later sprint.
- **The light theme.** Defined in tokens, not currently selected by any app page.
- **Any new design system, component library, token or font.**

### Cross-platform follow-ups recorded (not pulled into UX4)

1. Supplement status labels differ between web surfaces (F-19); the native client reads
   the same rows and should consume one label source.
2. If Coach becomes a full destination on web (PR3), the native Coach entry point will
   diverge conceptually. Record only; MOB-S15-PR7 is out of scope.
3. Emoji iconography (F-20) is web-authored but the category values come from
   `app/blueprints/supplements.py`, which the native client also reads — the icon choice
   should move to the client layer rather than the blueprint.
---

## AA. Visual-system debt table

| Component family | Variants found | Desired direction | Severity | Cross-surface leverage |
|---|---|---|---|---|
| **Buttons** | 3 canonical (`.btn-volt` 45 px/Bebas/r12, `.btn-ghost` 32 px/**Arial**/r8, `.btn-danger` = primary recoloured) + ≥11 bespoke primaries (`.auth-btn` 48, `.setup-btn`, `.landing-cta` 48, `.add-btn` 43, `.reward-btn` 48, `.log-fab` 56, `#cw-send` 38, `.notif-markall`, `.prem-cta`, `.wearable-connect`, `.qab`). 43 distinct padding values across 93 control rules. No `:disabled` or `:focus-visible` on any canonical class. | Four ranks — primary · secondary · quiet · destructive — with one control height, one radius rule, one typeface, and full `hover · pressed · focus · disabled · loading` coverage. Destructive must differ in more than hue. | **P2** | **Very high** — every surface, every journey |
| **Typography** | 218 raw `font-size` declarations vs 292 tokenised (57 % adoption); 29 distinct authored raw values incl. 9 / 10.5 / 11.5 / 13.5 px; **23 distinct computed sizes** at one viewport; **8 distinct `h1` sizes**; 4 families rendered (Inter, Bebas Neue, **Arial**, DM Sans); `.page-hdr h1` at `line-height: 0.95`. | Keep Inter + Bebas Neue and the existing 15-step ladder. Adopt it. Set `font-family` on every bare control. Drop DM Sans from the font request. Use `--leading-headline`. **No new font, no new size token.** | **P2** | **Very high** |
| **Cards / surfaces** | 202 card-like rules across 22 stylesheets; **77 distinct padding values**; **12 distinct rendered radii** vs a 6-step scale (10 px is the most common raw value and is off-scale); Plan V2 at **nesting depth 3**; Progress framing empty space in 1 232 × 200 px boxes; `.card-hover` **0 consumers**. | Fewer, stronger surfaces. One card per ownership boundary; rhythm and type instead of outlines; rest days as quiet rows. | **P2** | **High** — Plan, Progress, Nutrition, Account |
| **Tabs** | One good component (`.tab-bar` / `.tab-btn`, ARIA-correct, scroll-snapped) with **one consumer** (Nutrition) — and it carries off-scale `border-radius: 9px`, `padding: 10px`, `gap: 7px`. Active state adds a border that inactive tabs lack. | Keep exactly one tab component; bring it onto the scale; give active/inactive equal box metrics. | **P3** | Low — one consumer |
| **Inputs** | `.fc-input` is canonical with **3 consumers**. Supplements uses bare `<input>` with **no label association and no focus outline**; Coach uses `#cw-input` (no label, 36 px, 13.5 px); Account uses `.pf-input`; Plan uses `.plan-select`; auth uses its own. `.fc-input` is 14 px — below the 16 px that prevents iOS focus-zoom. | One input primitive, ≥44 px, ≥16 px text, a real `<label>`, and the repaired focus ring. | **P2** | **High** — 6 surfaces |
| **Icons** | Two systems. 1.8-stroke monochrome SVG (correct, dominant) **and** full-colour emoji as canonical iconography on Supplements (6) and Notifications (9), plus 🔥 on Account and **`★` ×32** as a rating control. `docs/design-system.md` forbids emoji as interface icons; `.icon-tile` exists. | One system. Replace emoji and glyphs with the SVG set; build the rating control from icons. | **P2** | Medium — 3 surfaces, high visibility |
| **Modals / sheets** | **21 `role="dialog"` surfaces** across 10 files from ≥9 class families; canonical `.modal-backdrop` has **0 consumers**. Focus trap in **1**, focus restore in 3, `inert` in 2, `Escape` in 11, `aria-modal` asserted on 9. | Do not write new components. Harden the existing Modal/Sheet primitive **once** (trap, `inert`, restore) and migrate. | **P2** | **Very high** — 8 surfaces |
| **Stats / metrics** | `--text-metric-sm/md/lg` exists and is good. Nutrition's calorie ring is a one-off inline `30px`; Account's XP block is bespoke; `.stat-card` has **1 consumer** (the legacy renderer); Progress's three "what changed" cards give a real number and two status strings equal weight. | Every primary number picks one of the three metric roles. Differentiate a measured value from a status string. | **P3** | Medium |
| **Empty states** | `.empty-state` exists with 11 consumers — but **not** on Today, Plan V2, Coach or Progress, which each write their own inline sentence. Coach's is italic (`.cw-empty`). At 1366 Progress's empty states sit in 1 232 px-wide boxes. | Two legitimate patterns, chosen deliberately: a *section* empty state (one honest sentence, no box) and a *collection* empty state (`.empty-state`). Never a framed void. | **P3** | Medium |
| **Loading** | `.skeleton` exists with **5 consumers**. Elsewhere: inline "Yükleniyor…" text, `.cw-typing` dots, `.loading-overlay` (2), or nothing. `.btn-ghost` has no loading state; `.btn-volt`/`.btn-danger` have `.loading` but no `:disabled`. | One in-place skeleton for content, one in-button loading state for actions. This is one of the two columns never designed (§M). | **P2** | **High** — every surface |
| **Errors** | Genuinely good where authored (Today, Plan, Progress: honest, per-section, never "something went wrong"). Absent or wrong elsewhere: **Notifications shows permanent loading on failure**; Supplements has no error state; Coach's is generic. | Extend the existing honest-error pattern to the surfaces that lack it. The pattern is already right — it just has not been adopted. | **P2** | Medium |

The table's shape repeats the audit's central finding: in nine of eleven families the
*right* component already exists and is under-consumed. The debt is adoption, not
absence.

---

## AB. Independent review

A separate critical pass was run against the draft before publication, using §37's ten
questions. Four findings were raised; all four were resolved in the document above.

**1. Are visual complaints grounded in actual evidence?**
Mostly yes — every claim traces to a measured value, a screenshot, or a cited source
line. **One failure was found and fixed:** the draft carried a contrast table listing
`.tab-btn.active` and several other elements as failures. Those figures came from the
auditor's own alpha compositor, which forced composited alpha to 1 after the first
translucent layer and so mis-composited any element stacked on a semi-transparent
background. The compositor was rewritten to proper Porter–Duff `over` and every contrast
number was re-measured. **The false findings were deleted**, the correction is recorded
in-line at §N6, and only the four surviving pairs are claimed. *(Review finding: P1 —
resolved.)*

**2. Are journey findings more important than cosmetic preferences?**
Yes, and the top-10 ranking enforces it: the two highest-count cosmetic items
(off-scale radii, 126 instances; motion duplication, 31 instances) rank **below** every
journey problem and ride along inside PR2 rather than displacing anything. The three P1
accessibility/entry findings rank above the Coach redesign **because they are cheaper
and their blast radius is wider**, not because they are more important as product work.

**3. Does the roadmap preserve authority boundaries?**
Yes, and §P was expanded to prove it rather than assert it. Three recommendations that
*looked* like UX were reclassified as NOT PURE UX and removed from the schedule: an
actionable Progress "Next Move" (needs a new field from `progress_insights`), a
contextual Coach→Plan deep link (needs server support), and any staged plan-creation
flow that would hold draft state client-side. PR4 is explicitly constrained to the same
single form and single submit. **No page-local duplicate writer is proposed anywhere.**

**4. Does it imitate WHOOP instead of benchmarking quality?**
No. Re-read specifically for this: the document contains no WHOOP layout, component,
colour, type choice or screen reference. Every recommendation is expressed in AxisAI's
own existing tokens and components, and the two most concrete direction statements —
"keep Inter + Bebas Neue" and "add no new tokens" — actively prevent drift toward any
other product's look.

**5. Does it create a huge design-system rewrite?**
It was heading that way and was cut back. **Review finding: P2 — resolved.** The draft's
PR2 proposed a spacing-token pass and a radius-system pass. Both were removed once the
census showed the token file is already correct (one undefined property, one wrong
value) and the *library itself* is the source of the drift. PR2 is now explicitly
corrective: **no new tokens, no new components, no font import**, and "any UX4-PR2 that
mints a token is out of scope by this document's own definition" is written into §T.

**6. Does it overuse cards / tokens / components?**
No — it argues the opposite. The dominant surface recommendation is *fewer* cards
(F-06, F-24, §J), and the dominant system recommendation is to adopt existing components
rather than write new ones. The evidence that makes this safe is F-11: a canonical Modal
with zero consumers alongside 21 bespoke dialogs means more components would not have
helped.

**7. Are P1/P2 severities justified?**
Re-derived from §24. **One demotion was made: F-06 (Plan nesting) was P1 in the draft
and is now P2** — it degrades comprehension and premium feel but does not break the
journey; the workout action stays above the fold and every state is reachable.
*(Review finding: P2 — resolved.)* The five remaining P1s each meet a §24 P1 clause:
F-01 damages the acquisition surface, F-02/F-03 make a data-deleting control
invisible to keyboard users, and F-04/F-05 leave a primary destination undesigned and
its keyboard order inverted. **No P0 is claimed, and none exists** — nothing found
touches security, privacy, data loss, or unrecoverable destructive UX.

**8. Does the sequence maximise cross-surface leverage?**
Yes. PR2 fixes 11 focus sites, 41 Arial elements, 3 button classes and 21 dialogs'
shared primitive in one pass, and it is a hard prerequisite for everything else. After
it, four PRs touch disjoint files and can run in parallel. The one ordering constraint
(PR4 → PR5) is a genuine file conflict, not a preference.

**9. Are the MOB-S15-PR7 and WEB-UX3-PR6B boundaries preserved?**
Yes. No Flutter file was opened; three cross-platform risks are *recorded* in §Z and
none is pulled into UX4 scope; nothing proposed touches `/api/v1` `WorkoutSession`
semantics, native auth, mobile transport, the terminal reread contract or Flutter
composition. PR6B is **determined but not started** — §W gives a scheduling
recommendation with reasoning, and the flag, the legacy renderer and the rollback branch
are all untouched.

**10. Can each proposed implementation PR ship independently?**
Yes, after PR2. **Review finding: P2 — resolved.** The draft assumed a flag for every
PR; the census showed Nutrition, Supplements, Progress, Account and Notifications are
**unflagged**, so "revert by flag" was not available. §Y now states the rollback route
per PR honestly and converts it into a **scope constraint** on PR6 and PR7 (they must be
small enough to revert by commit), and the roadmap mints **no new flag** — the registry
already carries two UI flags whose retirement is overdue.

### Review outcome

| Severity | Count | Disposition |
|---|---|---|
| **P0** | 0 | — |
| **P1** | 1 | False contrast findings from a compositor bug — **deleted, re-measured, correction recorded in §N6** |
| **P2** | 3 | Design-system rewrite scope cut (§T); F-06 demoted P1→P2; rollback story corrected and made a scope constraint (§Y) |
| **P3** | 0 | — |

All P0/P1 and all P2 review findings are resolved in the published document.

---

## AC. Final report

**A · Verdict** — **WEB-UX4 DISCOVERY READY TO SHIP.**

**B · Baseline SHA** — `6cace40e6dc7bb3908f2931e1f6bd07e7d1bfe76`
(`fix(ux): harden converged Plan experience (#308)`). Matches the brief exactly; **no
drift**.

**C · Branch** — `web-ux4-pr1-premium-experience-discovery`, cut from the baseline SHA.

**D · Production observation method** — **none; production was not observed.**
`fitx.duckdns.org` is not reachable from this environment. All browser evidence comes
from the repository's own hermetic audit harness (`scripts/frontend_audit`) — SQLite,
no network, synthetic seed, fixed clock, loopback-bound, `assert_safe_settings`
enforced — running the pinned Chromium under WSL Ubuntu-24.04. **118 cells** across 13
surfaces × 7 states × 5 viewports × 2 locales × 2 flag branches, **0 capture failures**,
plus three targeted passes (font attribution, real keyboard `Tab` walk,
`:focus-visible` measurement). **Zero production reads that mutate, zero production
writes, zero production data created or altered.**

**E · Journey scores** — J1 **2** · J2 **3** · J3 **not exercised** · J4 **3** ·
J5 **2** · J6 **2** · J7 **2** · J8 **3** · J9 **3** · J10 **3**. Full table §F.

**F · Surface scores (perceived quality)** — Today **4** · Plan V2 **3** ·
Plan legacy **2** · Progress **3** · Nutrition **2** · Account **2** ·
Notifications **2** · Supplements **1** · Coach **1**. Full matrix §G.

**G · P0 findings** — **none.**

**H · P1 findings (5)** — F-01 auth cards render `padding: 0` above 560 px from an
undefined token · F-02 `--focus-ring` is 1.09 : 1 · F-03 `outline: var(--focus-ring…)`
silently erases focus on 12 controls incl. meal-delete · F-04 Coach is a primary
destination with no destination · F-05 Coach keyboard order starts at Send and ends at
the composer.

**I · P2 findings (21)** — F-06, F-07, F-08, F-09, F-10, F-11, F-12, F-13, F-14, F-15,
F-17, F-18, F-20, F-21, F-22, F-23, F-25, F-26, F-27, F-30, F-36, F-38.

**J · P3 findings (14)** — F-16, F-19, F-24, F-28, F-29, F-31, F-32, F-33, F-34, F-35,
F-37, F-39, F-40.

**K · Top 10 ranked** — §S: F-03 · F-02 · F-01 · F-04 · F-10 · F-07 · F-21 · F-08 ·
F-06 · F-23.

**L · Typography verdict** — **The typeface is not the problem; adoption is.** Inter +
Bebas Neue is a defensible athletic pairing and the 15-step ladder is sound. But 43 % of
type declarations bypass it, **23 distinct sizes** render at one viewport, there are
**8 distinct `h1` sizes**, the secondary button renders in **Arial** because
`.btn-ghost` declares no `font-family`, and a full **DM Sans** family is downloaded to
serve four elements. **No font change, no new size token.** Adopt the ladder, set the
missing `font-family`, drop DM Sans.

**M · Button-system verdict** — **No hierarchy exists.** Primary and destructive are the
same button recoloured; primary and secondary share no height (45 vs 32 px), no radius
(12 vs 8) and no typeface (Bebas vs Arial); eleven further bespoke primaries exist; and
`hover : pressed : disabled` coverage is **108 : 25 : 16**. Target: four ranks, one
geometry, complete state coverage. Achievable by editing three existing classes.

**N · Surface/card verdict** — **"Everything is a dark rectangle" is real and
quantified**: 202 card-like rules, 77 padding values, 12 rendered radii. Plan V2 is the
worst instance — nesting depth 3 and **more than twice the height** of the renderer it
replaced. Premium hierarchy must come from whitespace, alignment and type, not from an
outline around every group.

**O · Layout/positioning verdict** — **Correct everywhere, composed nowhere.** Zero
horizontal overflow across all 118 cells at 320–1366 in both locales — a genuinely
strong result worth protecting by assertion. But desktop fails in two opposite
directions at once: Coach ~87 % empty and Today ~44 % empty at 1366, while Progress
stretches a one-sentence empty state across 1 232 px.

**P · Responsive verdict** — **Pass, with one hard failure.** Every primary surface
holds at 320, 390, 768, 1024 and 1366 in TR and EN with no overflow, no truncation and
no reordering. The single failure is `.auth-card` computing `padding: 0` above 560 px
(F-01) — on the sign-in screen. Locale parity is exact (1 382 keys each, zero gaps);
one untranslated string and one raw enum leak.

**Q · Coach verdict** — **The largest gap between intent and execution in the product.**
Coach's document height equals the viewport exactly in both flag branches: the page has
no content. The conversation is a 360 × 500 corner box that covers the page's own `h1`
at 390 px. Body text is 13.5 px at weight 300, the title is hardcoded English, the AI
avatar is lime-green, timestamps sit at 1.97 : 1, the widget outranks toasts at z 9998,
and the keyboard order reaches the composer **last**. The AI is the product's
differentiator and it is presented as a support widget. **PR3, and it is presentation
only — no AI, prompt, provider, streaming or mutation-authority change.**

**R · Progress verdict** — **Interprets well, then offers nothing.** The best heading
structure in the product and a genuinely good read model — and **zero** primary-rank
actions, with a "Next Move" slot that names the next step in prose and attaches no
control. Its canonical honesty is also rendering as visible repetition (one phrase 4×,
one sentence verbatim twice).

**S · Nutrition verdict** — **Coherent workflow, un-adopted surface.** The journey works;
the implementation carries 17 inline `style` font declarations, four hand-rolled
`.sec-label` copies (which is why the page has **no `<h2>`**), a one-off 30 px metric,
and — via F-03 — **no focus indicator on its edit, delete and quick-add controls.**
Two of five tabs are off-screen at 390 px.

**T · Today verdict** — **The strongest surface and the template for the rest.** One
primary action, one card, six text sizes, a correct heading outline, honest states. Two
gaps: no action at all after completion or on a rest day, and ~44 % of an empty desktop
viewport below a 20 px `h1` — the smallest page title in a product where every other
`h1` is 38–52 px.

**U · Plan verdict** — **Right architecture, wrong density.** Plan V2 is measurably
better than the renderer it replaced (8–9 sizes vs 12; a full heading outline vs a lone
`h1`) and **more than twice as tall**, at nesting depth 3, with four of seven day cards
spending full card weight on the word "Dinlenme". The no-plan state is worse: a 2 758 px,
48-option form is a new user's first act with an AI coach.

**V · Foundation PR recommendation — YES, bounded.**
All three of §26's tests are met: the same root causes recur across surfaces (§T table);
page-by-page fixing would demonstrably duplicate work — decisively so because
`components.css` itself violates the scale, so correct adoption still inherits drift;
and a bounded layer suffices because the token file has **exactly one undefined property
and one wrong value** while the component library is under-consumed rather than absent
(Modal **0** consumers against 21 bespoke dialogs). **Therefore UX4-PR2 is corrective:
no new tokens, no new components, no new font.** Discovery explicitly rejects the
expansive reading — a UX4-PR2 that mints a token is out of scope by this document's own
definition.

**W · Recommended PR sequence**
`UX4-PR2` Premium control & focus foundations → `UX4-PR3` Coach as a destination ·
`UX4-PR4` Plan first run → `UX4-PR5` Plan surface hierarchy · `UX4-PR6` Nutrition &
Supplements · `UX4-PR7` Progress · Today · Account · Notifications → `UX4-PR8`
Cross-surface regression & consistency gate. Full objectives, boundaries, tests,
non-goals, rollback and exit criteria in §V.

**X · Dependencies** — PR2 blocks everything. PR3, PR4, PR6, PR7 are parallel after it.
PR5 must follow PR4 (same files). PR8 is last. Diagram in §W.

**Y · Explicitly deferred work** — **WEB-UX3-PR6B** (determined: schedule between PR2
and PR4, because from PR4 onward the rollback target stops being equivalent; **not
started**) · **MOB-S15-PR7** and all Flutter work · mobile Progress/Coach ·
distribution/store · AI behaviour · the two backend-dependent items in §P · community
surfaces · the light theme.

**Z · Repository proof**

| | |
|---|---|
| Files changed | **1** — `docs/superpowers/specs/2026-09-14-web-ux4-premium-experience-discovery.md` (new) |
| Runtime files changed | **0** — no `.py`, `.html`, `.css`, `.js`, migration, workflow or config |
| Production mutations | **0** |
| Feature flags changed | **0** |
| Deploys | **0** |
| AWS actions | **0** |
| Candidate SHA | `1ff35af72fe42304a5ee9ad6713d2bc366729b48` (discovery commit, on base `6cace40e6dc7bb3908f2931e1f6bd07e7d1bfe76`) |
| Worktree | clean apart from the single added document |

**AA · Independent review** — P0: **0** · P1: **1** (false contrast findings from the
auditor's own compositor bug — deleted and re-measured; correction recorded in §N6) ·
P2: **3** (design-system rewrite scope cut; F-06 demoted P1→P2; rollback story corrected
and converted into a scope constraint) · P3: **0**. **All resolved before publication.**
Detail in §AB.

**AB · PR** — [#309](https://github.com/yusufbesirarslan/fitness-coach/pull/309) `docs(ux): define premium web experience roadmap`, head `1ff35af`, base `main`. CI: **all 5 required checks green** (run 34928173010). Open, unmerged. Detail in §AD.

**AC · Merge** — **NOT PERFORMED — explicit authorization required.**

---

## AD. Publication record

| | |
|---|---|
| Branch | `web-ux4-pr1-premium-experience-discovery` |
| Base | `main` @ `6cace40e6dc7bb3908f2931e1f6bd07e7d1bfe76` |
| PR | [#309](https://github.com/yusufbesirarslan/fitness-coach/pull/309) |
| Head SHA (discovery commit) | `1ff35af72fe42304a5ee9ad6713d2bc366729b48` |
| CI | **all 5 required checks green** on `1ff35af` — run [34928173010](https://github.com/yusufbesirarslan/fitness-coach/actions/runs/34928173010): pytest 12m33s · PostgreSQL concurrency 1m58s · schema-drift guard 54s · authoritative image revision immutability 38s · authoritative Linux production locks 32s |
| Merge | **not performed — awaiting explicit authorization** |

This publication record was written after PR #309 was opened and CI reported, so it
lands as a follow-up commit on the same branch; the discovery content itself is the
tree at `1ff35af`, which is the commit CI verified.

Repository proof at publication:

| | |
|---|---|
| Files changed | 1 (`docs/superpowers/specs/2026-09-14-web-ux4-premium-experience-discovery.md`) |
| Runtime files changed | **0** — no CSS, templates, JS, tokens, fonts, routes, schemas, migrations |
| Feature flags changed | **0** — `UIUX_PLAN_V2_ENABLED` not retired, legacy Training renderer not deleted, WEB-UX3-PR6B not started |
| Production mutations | **0** |
| Mobile / `/api/v1` WorkoutSession / Flutter / MOB-S15-PR7 | untouched |
| `git diff --check` | clean |
| Worktree | clean |
