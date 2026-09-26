# Progress Summary — canonical read model

The single server-owned authority behind the Progress page's top surfaces.
Introduced by **Progress Redesign PR2**; PR1 (#212) built the information
architecture and deliberately left the trajectory neutral because no such
authority existed.

It answers three questions and nothing more:

1. **How am I doing?** → `trajectory`
2. **What changed?** → `body`, `performance`, `consistency`
3. **Is there enough evidence to say?** → `building_baseline` /
   `insufficient_data` / `partial`, published explicitly instead of being
   papered over.

---

## 1. Authority map

After PR2 exactly one layer owns each concept:

| Concept | Owner |
|---|---|
| Trajectory | `app/services/progress_summary` |
| Training progression (trend / plateau / deload / consistency / `next_signal`) | `app/services/training_progression` |
| Raw workout history, week geometry, marker semantics | `app/services/training_history` |
| Current weight, target weight, check-in ledger | `User` / `WeeklyCheckIn` model semantics |
| Day / timezone | `app/timeutil` (fixed `Europe/Istanbul`) |
| Presentation | `static/progress.js` + `locales/{en,tr}.json` |

Dependency direction is one-way and enforced by a test
(`tests/test_progress_summary.py::test_dependency_direction_is_one_way`):

```
static/progress.js
      ↓
GET /api/progress/summary          (app/blueprints/tracking.py)
      ↓
progress_summary                   (+ narrow User / WeeklyCheckIn reads)
      ↓
training_progression
      ↓
training_history
```

Nothing under `training_*` imports `progress_summary`, so no cycle can form.

---

## 2. Architecture

Layering mirrors `training_progression`:

| Module | Responsibility |
|---|---|
| `models.py` | Frozen value objects + every bounded state constant |
| `analysis.py` | Pure mapping: signal → trajectory, signal → performance state, body facts → body summary, window geometry. No DB, no Flask, no clock |
| `queries.py` | The only impure reads: `User.weight` / `User.target_weight` / qualifying `WeeklyCheckIn` rows |
| `payload.py` | Explicit JSON projection of the wire contract |
| `__init__.py` | `build_progress_summary` orchestrator + public API |

### Public service API

```python
build_progress_summary(user_id, *, end_day=None) -> ProgressSummary
```

Invariants:

- user-scoped; the caller's id is the only owner expressible
- read-only — no add, no delete, no flush, no commit, no plan mutation, no
  quest/streak/XP side effect
- deterministic for a fixed `end_day`
- no LLM / Bedrock / OpenAI / provider call
- `end_day` defaults through `app.timeutil.app_today()` and is resolved **once**
  before the progression report is built, so a request straddling Istanbul
  midnight cannot report a window the signals were not computed over
- window fixed at `SUMMARY_WEEKS = 4`

---

## 3. API contract

```
GET /api/progress/summary
```

Authenticated with the existing web boundary (`@require_auth`). **No input at
all**: no `user_id`, no `weeks`, no `end_day`. A client that can re-window the
analysis until the answer improves owns the trajectory, and the server is
supposed to. Query strings are ignored, not rejected.

Response carries `Cache-Control: private, no-store`.

Example (illustrative values only — nothing here is hardcoded):

```json
{
  "contract_version": 1,
  "window":      { "weeks": 4, "start": "2026-07-19", "end": "2026-08-15",
                   "timezone": "Europe/Istanbul" },
  "trajectory":  { "state": "on_track", "reason": "progressing" },
  "body":        { "status": "available", "current_weight_kg": 78.4,
                   "weight_delta_kg": -0.6, "target_weight_kg": 75.0,
                   "distance_to_target_kg": 3.4,
                   "weight_series": [ { "day": "2026-08-01", "weight_kg": 79.4 },
                                      { "day": "2026-08-08", "weight_kg": 79.0 },
                                      { "day": "2026-08-15", "weight_kg": 78.4 } ] },
  "performance": { "state": "progressing", "volume_trend": "up",
                   "strength_trend": "flat", "next_signal": "progressing" },
  "consistency": { "state": "consistent", "active_weeks": 4,
                   "analyzed_weeks": 4, "sessions": 12 },
  "weekly":      [ { "start": "2026-07-19", "sessions": 3, "active": true,
                     "volume_kg": 4200.0 }, "… one entry per analysed week …" ]
}
```

**Progress V2 PR2 (additive, `contract_version` unchanged).** Two series feed
the Trends visualizations; every v1 field is unchanged:

- `body.weight_series` — the qualifying check-in weights (`yogunluk IS NOT
  NULL`), oldest first, capped at `WEIGHT_SERIES_POINTS = 8`, each on its
  Istanbul day (`app_date_of`). Produced by the SAME check-in query the delta
  already used (only its `LIMIT` grew from 2 to 8); the delta still compares
  the newest two rows only. Empty when there are none — never padded.
- `weekly` — one entry per analysed week, oldest first: the progression
  report's own `weekly_volume` buckets (the objects `consistency` counts and
  `volume_trend` was computed from). `active` is `sessions > 0`, the exact rule
  `active_weeks` uses; `volume_kg` is rounded to 1 dp. Zero weeks are measured
  zeros. No extra query, no second report.

Contract rules:

- versioned (`contract_version`), machine-readable, deterministic
- no localized prose, no database id, no user id, no ORM serialization, no
  provider output
- `null` means *unavailable / not known*; `0` means *a real measured zero*

---

## 4. Trajectory states

Exactly three. There is deliberately **no `off_track`** in V1.

```
building_baseline
on_track
needs_attention
```

### `next_signal` → trajectory (the whole contract)

| canonical `next_signal` | trajectory | performance state |
|---|---|---|
| `insufficient_data` | `building_baseline` | `building_baseline` |
| `progressing` | `on_track` | `progressing` |
| `keep_pushing` | `on_track` | `steady` |
| `build_consistency` | `needs_attention` | `building_consistency` |
| `plateau` | `needs_attention` | `plateau` |
| `deload` | `needs_attention` | `deload` |
| *anything else* | **raises `UnknownProgressionSignal`** | **raises** |

The performance column is a **presentation** rename only; it does not alter
`training_progression` semantics, and `next_signal` is echoed verbatim in the
payload so the mapping stays auditable.

`tests/test_progress_summary.py::test_trajectory_table_covers_the_training_contract_exactly`
derives the expected key set from `derive_next_signal`'s own reachable outputs
rather than restating it, so a seventh canonical signal breaks the test instead
of production.

### `needs_attention` does not mean failure

It means *the current canonical training signal says there is something worth
paying attention to*. It is not regression, not "off track", not a judgement of
the athlete, and not a medical statement. Copy in both locales is written to
that meaning, and `progress.css` tints only the card's left rule — no filled
red/green background that would read as a verdict.

### Unknown values fail closed

An unmapped signal raises rather than resolving to a state.

- Mapping unknown → `on_track` would invent success.
- Mapping unknown → `building_baseline` would report a **contract drift** (a
  system fault) as "you have not logged enough yet", which is a lie about the
  user. See §8.

---

## 5. Why V1 is training-led

`training_progression` is the only domain in the product with a validated,
deterministic, documented longitudinal authority
(`docs/TRAINING_PROGRESSION.md`). It already resolves every overlap between its
own booleans into one `next_signal` with a fixed precedence, so this layer
consumes that resolution.

What is deliberately **not** built:

- no weighted cross-domain score (`body × 0.3 + training × 0.4 + …`)
- no 0–100 Progress Score, no readiness score, no confidence percentage
- no adherence percentage (there is no canonical denominator for one)
- no second precedence chain built from `is_plateau` / `deload_due` / raw
  booleans — that would put two disagreeing authorities in one product

A structural guard (`test_no_weighted_arithmetic_across_domains`) rejects
multiplication and division anywhere in the package.

---

## 6. Why body does not override the trajectory

Weight is noisy, and the repository owns no validated longitudinal
body-composition or body-trend authority comparable to `training_progression`.
Deciding whether a weight movement is "good" would require inventing rate
thresholds, target rates or body-composition assumptions — new product
semantics this PR has no authority to create.

Therefore in V1:

- BODY is summarized truthfully and contributes **context**
- BODY never independently produces `on_track` or `needs_attention`
- BODY never overrides the training trajectory
  (`test_body_does_not_override_the_training_trajectory` proves it in both
  directions: a 4 kg gain and a 4 kg loss leave the state byte-identical)

No BMI. No body-fat estimate. No rate-of-loss prescription.

### Body semantics

| Field | Source |
|---|---|
| `current_weight_kg` | `User.weight`, falling back to the newest `WeeklyCheckIn` — the same fallback `/progress-page` already established |
| `target_weight_kg` | `User.target_weight`, `null` when unset |
| `weight_delta_kg` | latest minus previous of the **two latest qualifying** check-ins, rounded to 1 dp |
| `distance_to_target_kg` | `abs(current - target)`, only when both exist |

**Qualifying** means a full weekly check-in, identified by
`yogunluk IS NOT NULL` — the exact filter `/checkin-history` already
uses to keep sparse `/update-weight` rows out of
Progress history (BUG-5). The two concepts are **not** merged: a sparse row is a
perfectly good answer to "what does this user weigh" (so it feeds the current
weight fallback) and not an answer to "is this a Progress observation" (so it
never produces a delta).

`status` ladder — availability, not quality:

| status | meaning |
|---|---|
| `available` | current weight known **and** a two-observation delta exists |
| `partial` | current weight known, delta not derivable |
| `insufficient_data` | no canonical current weight at all |

---

## 7. Missing is not zero

| Situation | Published | Never |
|---|---|---|
| No target weight configured (or a stored non-positive one) | `null` | `0` |
| Fewer than two qualifying check-ins | `weight_delta_kg: null` | `0.0` |
| No training history | `building_baseline` / `insufficient_data` | flat progress presented as certainty |
| Four analyzed weeks, none trained | `sessions: 0` (a real measured zero) | `null` |

A stored non-positive `target_weight` is treated as unset, matching the mobile
nutrition boundary's handling of a non-positive calorie goal
(`docs/MOBILE_NUTRITION.md`).

---

## 8. Error behaviour

Infrastructure failure and insufficient evidence are different states and must
stay visibly different.

- Any exception from the service → the blueprint's generic JSON 500
  (`route.generic_error_retry`). Never `building_baseline`.
- No exception message, SQL, stack trace, identifier or internal path is
  returned. One PII-free log line carries a coarse error class only:
  `contract_drift` (unknown canonical signal), `upstream_error`
  (`SQLAlchemyError`), `unexpected_error`.
- Success logs `trajectory=<state> body=<status>` and nothing else — no weights,
  no session counts, no payload.

The client mirrors the rule: a failed fetch renders
`progress.traj_unavailable` + `progress.load_error`, never a trajectory
(`test_summary_failure_does_not_read_as_insufficient_data`).

---

## 9. Window

One fixed, server-owned analysis window: **4 weeks**.

`start` comes from `training_history.weekly_windows(end_day, 4)[0]` — the same
call `build_progression_report` makes — so the reported window is by
construction the window the signals were computed over, not parallel date
arithmetic that could drift. The foundation's windows are **trailing**: the
newest covers `[end_day - 6, end_day]`.

`timezone` is `app.timeutil.APP_TZ.key`; the constant is not duplicated.

---

## 10. Frontend ownership boundary

**Progress V2 PR1** re-cut the page into explicit, independently replaceable
sections — one Jinja partial each, rendered in this order:

| Section | Partial | Question it owns |
|---|---|---|
| ProgressHeader | `_progress_header.html` | — |
| Current State | `_progress_current_state.html` | What is my current state? + next action |
| Trends | `_progress_trends.html` | What changed? (measurable evidence) |
| Axis Insight | `_progress_axis_insight.html` | What does that mean, and what should I do? (V2 PR3: one surface) |
| Physique | `_progress_physique.html` | Do I have visual progress data — and if not, how do I start? (V2 PR4: quiet secondary section) |
| Recent Check-ins | `_progress_recent_checkins.html` | What happened recently? (V2 PR4: flat, day-grouped recent list) |

`static/progress_presentation.js` (`window.FitXProgressPresentation`) is the
**one** presentation model: every state → locale-key table on the page and a
pure `buildSummaryView(payload, {locale})` that turns this contract into

- `current_state` (V2 PR2: STATE → EVIDENCE → ACTION) — `status`, `state`
  (accent only), `window` ("Your last 4 weeks"), `headline`, `summary`,
  `evidence` (at most two MEASURED facts, fixed order: weeks trained, then the
  volume direction when it is evidence) and `next_action` (keyed on the
  trajectory; `needs_attention` hands off to Axis Insight rather than naming
  the signal). Keyed on the **trajectory**, never on `trajectory.reason`:
  which signal needs attention is Axis Insight's to say.
- every metric carries the same render slots, in falling prominence:
  `value` · `unit_label` · `change` (`{text, direction}`) · `viz` · `note` ·
  `meta` (list), plus its facts.
- `metrics.weight` — value, the server delta as change (direction = its sign,
  never a verdict), a `line` viz over `weight_series` only when it has at
  least `MIN_LINE_POINTS = 3` points (fewer → a "not enough check-ins" note,
  never a fake flat line), target distance as meta.
- `metrics.training_volume` — value = the newest trailing week's `volume_kg`
  (compact, locale-formatted), change = the canonical `volume_trend`, a
  `bars` viz over the weekly totals. `building_baseline` → `insufficient_data`
  (note, no change, no bars). A payload without `weekly` falls back to the
  semantic trend word — no number is invented.
- `metrics.consistency` — value `active / total` + "weeks active", change =
  the canonical state label (its only rendering on the page), a `weeks` viz
  (one cell per week, only when the series covers exactly the counted weeks).

Visualization geometry (fixed SVG viewBox) is computed in the model;
`progress.js` builds a handful of nodes with `createElementNS`, marks the
drawing `aria-hidden`, and renders its text equivalent (visually hidden
sentence, or a real `<ol>` for the week cells). No chart library, no canvas,
no ResizeObserver/rAF/animation; layout is CSS-only (`auto-fit` grid).

Every rendered string is a `{key, params}` descriptor; internal identifiers are
never rendered or reshaped into copy. The consistency state renders exactly
once (its Trends card). `static/progress.js` only writes the view into the DOM,
and its history module reads the same tables. The module is pure, so
`tests/test_progress_presentation_js.py` executes it under node.

Static cost: none. The page keeps its pre-V2 count of 15 static files. The
history consumer (formerly `static/progress_history.js`) now ships as a
self-contained IIFE at the end of `static/progress.js`, between
`BEGIN/END progress history module` markers. Its guards in
`tests/test_progress_history_ui.py` run on that block alone, and the
controller guards run on the rest of the file. The wire
contract in §3 is unchanged — this is a presentation adapter, not a new
contract.

Rules that still hold:

- Every surface renders one summary payload, fetched once, so the page cannot
  show a card that disagrees with its own headline.
- An enum the build does not know renders the neutral/unavailable state, never
  a guess.
- Forbidden and test-enforced: `sessions >= 3 → on_track`,
  `weightDelta < 0 → success`, `streak >= X → consistent`, or any threshold on
  a summary field (`test_client_never_fabricates_a_trajectory`).
- The gamification streak is no longer a Progress consistency signal at all.
  Logging in is not training. `/api/progress/achievements` is unchanged and
  still serves its other consumers.
- Trajectory is never communicated by colour alone: `#ps-state` always spells
  the state out, and `data-state` (accent only) is written by the same function
  that writes the headline.
- A summary failure degrades Current State and Trends only. Axis Insight,
  Physique and Recent Check-ins own separate fetches and keep loading.

**Progress V2 PR3 — Axis Insight as one coaching surface.** The three equal
WHAT'S WORKING / WATCH THIS / NEXT MOVE cards are retired for ONE surface in
coaching order: interpretation (lead line + "why it matters") → at most two
evidence facts → THIS WEEK (one action) → "Review with AxisAI". Semantic
ownership on the page is now explicit and test-enforced (no rendered sentence
is shared between sections, `tests/test_progress_axis_insight.py` §C):

| Section | Owns | Never says |
|---|---|---|
| Current State | *What state am I in?* — trajectory headline, summary, ≤ 2 measured facts, trajectory-level next move, the check-in | which signal drives the state |
| Trends | *What happened?* — weight · volume · consistency values, changes, visualizations | any interpretation or advice |
| Axis Insight | *What does it mean, and what do I do?* — the interpretation, its evidence, the ONE action, the Coach continuation | the trajectory label, a Trends value |

The insight is decided on the server (`app/services/progress_insights`,
additive `insight` key on `/api/progress/axis-insights`, see
`docs/PROGRESS_INSIGHTS.md` §4b); `buildAxisInsightView` in
`progress_presentation.js` maps it to copy and `progress_insights.js` renders
it. Current State's needs-attention next move now points at "Axis Insight
below", and its generic "Ask AxisAI" link is gone: the page's one Coach entry is
the insight's contextual review link. Static cost unchanged (15 files); network
unchanged (summary, axis-insights, physique, history — one read each).

**Progress V2 PR4 — Physique + Recent Check-ins as secondary sections.** Both
stay below the intelligence layer and were made visibly quieter than it:

- *Physique* (`static/progress_physique.js`, same `/api/progress/physique`
  read): the empty state is a compact title + one sentence + ONE action (no
  bordered placeholder card). On the web a Pump Check is taken when a workout
  is finished, so the action is "Go to Training" (`/training`); when only
  legacy Pump Checks exist it is the gallery instead. Photos render as bounded
  140px 3:4 thumbnails (lazy, `decoding=async`, intrinsic size reserved), body
  typography replaces the display face, the duplicate warning-coloured
  "Limited comparison" line and rail are gone (the reliability is said once,
  in words), and stable/focus lists stay out of Progress (observed changes,
  limitations and next-check guidance remain). Nothing is analysed, scored or
  compared in the browser.
- *Recent Check-ins*: `buildHistoryView` in `progress_presentation.js` groups
  `/api/progress/history` rows by the server's Istanbul `analysis_day` (string
  identity — the browser's timezone cannot regroup), leads each day with its
  newest check-in, reuses `TRAINING_STATE` (trajectory as fallback) as the ONE
  summary word per row, and bounds the main page to
  `HISTORY_VISIBLE_GROUPS = 4` days. Row hierarchy: summary → weight (· delta)
  → `<time>` date as quiet metadata. Same-day check-ins are real separate
  records (`POST /checkin` has no per-day limit) and are grouped with a count,
  never dropped; their individual times/weights and the earlier days are
  disclosure buttons that BUILD the rows when opened and REMOVE them when
  closed — no hidden archive in the DOM. The per-row drilldown (window,
  performance · consistency facts, volume word) and the colour-coded state
  rails are retired, with their locale keys and `TREND_INLINE`.
- Cost: static files and reads unchanged (14 scripts/styles measured, the same
  four Progress reads). Server: same-day history rows now share ONE
  `build_progression_report` per analysis day (identical facts), so a burst
  of check-ins on one day no longer multiplies the training reads.
  Browser matrix: `tests/test_progress_physique_history_browser.py`.

**Progress V2 PR5 — final hardening (no new feature, no new authority).**

- *One visual system*: `.progress-main [hidden] { display: none !important }`
  is the page's single `hidden` owner (PR5 found the PR3 defect again on
  `#ps-evidence`, which kept its flex box in the failure state). Content
  labels are sentence-case body type (Physique "Area: …", the comparability
  headline, list headers, Axis "This week"); section headings (`.sec-label`,
  shared) are unchanged. One filled primary on the page (Weekly check-in);
  Review with AxisAI and the Physique action are both `.btn-ghost`. Axis blue
  is kept for intelligence/interaction: the latest volume bar is neutral
  brightness, not blue.
- *Truthful states*: a failed or unreadable summary no longer says "No data
  yet" on the Trends cards (`progress.card_unavailable`, the retired
  `progress.card_nodata` had no other consumer), and a failed Current State is
  a quiet body-type notice with a neutral rule (`#ps-card[data-status]`), not
  a display-face verdict with the blue accent.
- *Layout*: Axis Insight groups meaning (interpretation → evidence) and move
  (this week → review) into two columns that stack independently, so the
  evidence no longer drops to the height of the action block on desktop.
  Trends go three-across from a 720px content box (768px was 2 + 1 with an
  orphaned card). The history list keeps a 72ch reading measure.
- *Signed numbers* use the typographic minus (U+2212) — Weight card, history
  deltas and the planner percentage. Spelling only; no parsing downstream.
- Cost: unchanged — 14 CSS/JS files plus the existing shell icon, the same
  four Progress API reads, no dependency, no model call. Browser fixture
  photos are counted separately from the CSS/JS gate. Integrated matrix: `tests/test_progress_v2_final_browser.py`;
  static contract: `tests/test_progress_v2_final_ui.py`.

---

## 11. Compatibility

PR2 converges; it does not clean up. These endpoints are **unchanged** and keep
their other consumers:

`/api/progress/workout` · `/api/progress/achievements` · `/checkin-history` ·
`/api/progress/heatmap` · Pump Check gallery.
(The legacy `/api/progress/insights` heuristic was retired by Sprint 13 PR5.)

No schema change, no migration, no new table, no persisted summary, no cached
trajectory. The summary is a read model, recomputed per request.

---

## 12. Deferred

**PR3 — AXIS INSIGHTS intelligence.** What's Working / Watch This / Next Move,
cross-domain narrative, recommendations, next-action advice. PR2 answers *how am
I doing* and *why* at a bounded deterministic level; it does not answer *what
should I do next*.

**PR4 — Physique Progress.** Pump Check comparison, visual progression,
body-region change. Untouched here.

**Possible future body-trajectory authority.** If a validated longitudinal
body-composition layer is ever built (with real rate semantics, not invented
thresholds), body could graduate from context to a trajectory input. Recorded,
not implemented.

**Nutrition and recovery.** Excluded from V1 classification on purpose. Weekly
check-in sleep/fatigue data exists but has no validated Progress authority
behind it; using it would create exactly the hidden scoring model this document
forbids.
