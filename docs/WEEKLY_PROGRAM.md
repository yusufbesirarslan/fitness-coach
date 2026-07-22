# Weekly Program Consumer

`app/services/weekly_program/` — the canonical, deterministic **weekly-program
consumer** of the Adaptive Training Engine (Sprint 6 PR5). It translates the
`AdaptivePlan` produced by the planning layer (`docs/TRAINING_PLANNING.md`, Sprint 6
PR3) into a structured weekly **program recommendation** that future UI/runtime
consumers can display or explain: which kind of week to run, whether volume/intensity
move, the absolute weekly-volume target the plan's own delta implies, and
locale-neutral explanation keys for coach copy.

**Not a second planning engine.** `AdaptivePlan` remains the sole planning authority.
This layer is read-only over it and never independently decides whether the user is
progressing, plateauing, deload-due, overload-ready, or whether volume/intensity
should move — every such field is a verbatim echo. Anything the plan does not model is
reported as *unsupported* rather than guessed.

**Not the plan generator.** `training_generation/` + the `TrainingPlan` model produce
LLM-authored workout *content* at `POST /training-plan`. This layer emits a
deterministic recommendation object and shares no vocabulary with it.

## Scope

Purely additive. **No** schema, **no** migration, **no** coach-prompt, flag, or UI
change. One runtime surface exists — the read-only `GET /api/training/weekly-program`
(below). Coach wiring and any UI presentation remain later PRs' work.

## Layering

| Module | Purity | Responsibility |
|--------|--------|----------------|
| `models.py`   | pure | Frozen value object (`WeeklyProgramRecommendation`) + `UNSUPPORTED_CAPABILITIES`. No logic. |
| `analysis.py` | pure | Deterministic translation rules over an `AdaptivePlan`. Fixture-free tests. |
| `payload.py`  | pure | JSON-safe projection of the value object for HTTP consumers. |
| `__init__.py` | impure (reads the plan via `build_adaptive_plan` only) | Public API + `build_weekly_program` orchestrator. |

Dependency is strictly one-way:

```
training_history → training_progression → training_planning → weekly_program
```

No lower layer imports this one, and this layer reads **no** history of its own — it
never touches `WorkoutLog`, `app.models`, `app.extensions`, or Flask. Pinned by
`tests/test_dependency_boundaries.py`
(`test_adaptive_training_layers_preserve_one_way_imports`, which now covers
`weekly_program` under the same forbidden-import set as its siblings).

## Public API

- `build_weekly_program(user_id, weeks=4, *, end_day=None) -> WeeklyProgramRecommendation`
  — the entry point. Builds one `AdaptivePlan` (which reads progression once, which
  reads history once), then translates it purely. `end_day` defaults to today
  (Istanbul); pass it explicitly for hermetic tests. Empty history (or `weeks <= 0`)
  → the fully neutral recommendation.
- `derive_weekly_program(plan) -> WeeklyProgramRecommendation` — the pure core; the
  whole consumer is testable without a DB.
- `select_volume_baseline(plan)`, `target_volume_for(baseline, delta)`,
  `derive_explanation_keys(plan)` — the individual pure rules (see below).
- `weekly_program_payload(recommendation) -> dict` — pure JSON-safe projection of the
  value object (`date` → ISO string, tuples → lists, `None` preserved). Owned here, not
  by the route, so the HTTP contract cannot drift into a second shape.

## Runtime surface — `GET /api/training/weekly-program`

One read-only, user-scoped JSON endpoint in `app/blueprints/training.py`
(`get_weekly_program`), added in Sprint 6 PR5 part 2. It exists so a future UI or
client can fetch the deterministic recommendation without re-deriving planning logic.

The route is deliberately three lines: `@require_auth`, one
`build_weekly_program(current_user.id, weeks=4, end_day=None)` call, one
`weekly_program_payload(...)` projection into `jsonify`. It performs no query, holds no
threshold, and reads no `WorkoutLog` — `tests/test_weekly_program_route.py` asserts
that structurally by parsing the view's own AST, because `training.py` legitimately
imports `WorkoutLog` and `db` for its other routes.

**`weeks` and `end_day` are pinned to the service defaults and are not readable from
the query string.** The analysis window is a planning knob; exposing one over HTTP
would hand a caller partial planning authority and make the response non-deterministic
for a given user and day. `?weeks=1` is ignored, and a test pins that.

| Property | Behavior |
|---|---|
| Auth | `@require_auth`; unauthenticated → 302 to login. |
| Methods | `GET` only; `POST`/`DELETE` → 405. |
| Scoping | `current_user.id`, through the planner's own filter. This layer runs no query of its own. |
| Determinism | Same user + same day → identical bytes. Repeated calls are pinned equal. |
| Side effects | None. No write, no session mutation, no coach call, no flag read. |
| Empty history | 200 with the neutral payload — never an error, never a 404. |

The response body is the value object field-for-field — same names, same order, no
extra decision fields:

```json
{
  "weeks": 4,
  "has_data": true,
  "week_focus": "overload",
  "volume_action": "increase",
  "intensity_action": "progress",
  "volume_delta_pct": 0.05,
  "overload_ready": true,
  "maintenance_recommended": false,
  "baseline_week_start": "2026-07-15",
  "baseline_weekly_volume": 400.0,
  "target_weekly_volume": 420.0,
  "reason_codes": ["progressing"],
  "explanation_keys": ["weekly_program.focus.overload",
                       "weekly_program.reason.progressing"],
  "unsupported": ["session_frequency", "intensity_magnitude", "exercise_selection"]
}
```

`baseline_weekly_volume` and `target_weekly_volume` are `null` together whenever no
positive volume exists — never `0`. The endpoint changes none of the semantics defined
below; it publishes them.

This is a *consumer*, not a planner. It never decides progression, plateau, deload,
overload, maintenance, or whether volume/intensity should move — every such value
arrives already decided by `AdaptivePlan`. Its serialization is also distinct from the
PR4 prompt contract (`adaptive_plan_context.py`, the sole `AdaptivePlan` → JSON owner):
that one serializes the *plan* for the coach prompt, this one serializes the
*recommendation* for HTTP, and `payload.py` never imports `AdaptivePlan`.

## The recommendation model — `WeeklyProgramRecommendation`

Three kinds of field, and the distinction is the point of the layer.

| Field | Kind | Type | Meaning |
|-------|------|------|---------|
| `weeks` | echoed | int | Window count analysed. |
| `has_data` | echoed | bool | Any workout history in the window. |
| `week_focus` | echoed | enum | `overload` / `steady` / `maintenance` / `deload` / `build_consistency` / `insufficient_data`. |
| `volume_action` | echoed | enum | `increase` / `hold` / `decrease`. |
| `intensity_action` | echoed | enum | `progress` / `hold` / `deload`. |
| `volume_delta_pct` | echoed | float | Signed fraction; `0.0` whenever the action is `hold`. |
| `overload_ready` | echoed | bool | |
| `maintenance_recommended` | echoed | bool | |
| `reason_codes` | echoed | tuple[str] | The plan's ordered codes, unchanged; position 0 is the primary cause. |
| `baseline_week_start` | observed | date \| None | Start of the trailing 7-day window the baseline was measured in; always in the past (`baseline_week_start + 6 <= today`). |
| `baseline_weekly_volume` | observed | float \| None | Most recent **positive** weekly volume in the plan's embedded series — the total across a complete trailing 7-day window, not a single session. |
| `target_weekly_volume` | derived | float \| None | `baseline * (1 + volume_delta_pct)`, rounded to 2 dp. |
| `explanation_keys` | derived | tuple[str] | Locale-neutral message keys for coach/UI copy. |
| `unsupported` | constant | tuple[str] | Capabilities `AdaptivePlan` does not model. |

The object is frozen and flat — it deliberately does **not** embed the `AdaptivePlan`.
That is what lets a future route/UI depend on this layer alone instead of importing
`training_planning` itself, keeping the planner's approved outside owners to the two
recorded in `ADAPTIVE_PLAN_IMPORT_ALLOWLIST`.

## What the consumer receives from `AdaptivePlan`

Every decision, verbatim: `week_focus`, `volume_action`, `intensity_action`,
`volume_delta_pct`, `overload_ready`, `maintenance_recommended`, `reason_codes`,
`weeks`, `has_data` — plus the embedded `ProgressionReport`, from which this layer
reads exactly one thing: the `weekly_volume` series, and only to *observe* a baseline.

`test_decisions_ignore_observed_volume` pins the boundary from the other side: two
plans carrying the same signal but wildly different volume series must produce
identical decision fields. If a decision ever tracks the observed volumes, the layer
has started planning on its own and the test fails.

## Volume baseline and target

The plan states volume change *relatively* (`+0.05`), but consumers need an absolute
number to show. The rule is arithmetic, not judgment:

1. **Baseline (observed).** Scan the plan's embedded `progression.weekly_volume`
   newest-first; take the first window with `total_volume > 0`. Zero-volume windows
   are **skipped**, never treated as a baseline of `0.0` — a rest week (or a
   marker-only "I trained" week) is missing data for this purpose, not a measurement
   of zero, and anchoring to it would scale every recommendation down to nothing.
2. **Target (derived).** `round(baseline * (1 + volume_delta_pct), 2)`. Two decimals
   matches the rest of the stack (`training_history.estimated_1rm`,
   `GET /api/progress/workout`) and keeps binary float noise —
   `400 * 1.05 == 420.00000000000006` — out of a displayed number.
3. **No baseline → no target.** Both fields are `None` *together*, never `0.0`, which
   would read as "train nothing this week" instead of "not enough data to say".

Worked examples: overload on a 400 kg week → `420.0`; deload on a 302 kg week →
`181.2` (~60 %, the plan's `DELOAD_VOLUME_CUT`); any `hold` focus → target equals
baseline.

### What "weekly" means here

A baseline is a **complete trailing 7-day observation**. `weekly_windows` anchors the
newest window so that it *ends* on the analysis day (`[today - 6, today]`), so every
window the baseline can be drawn from is a full week that has actually been lived.
`baseline_week_start` therefore always names a past window, and rule 1 above compares
like with like.

This was not always true. Until the Sprint 6 PR5 post-audit fix the newest window
*started* on the analysis day (`[today, today + 6]`). History can never contain future
entries, so that bucket only ever held **today's** entries while presenting itself as a
week — and because rule 1 scans newest-first for positive volume, it won:

- A Mon/Wed/Fri user training ~5000 kg a session has a 15000 kg week, but on any day
  they had already trained, the API published `baseline_weekly_volume: 5000.0` — the
  headline number understated roughly threefold, and scaling with training frequency.
- `baseline_week_start` named a window running six days into the future.
- The number *fell* the moment the user logged the day's first set: before training,
  the empty newest bucket was skipped and the baseline was last week's real total;
  after training, it switched to that one session.

The fix is upstream, in `training_history.weekly_windows` — the single owner of window
geometry — not here. **This layer implements no windowing of its own and must not**;
skipping "the current partial window" locally would be a second windowing rule in a
layer whose entire contract is that it does not have one. The rest-day fallback in rule
1 still exists, but it now means what it says: a genuine rest week is skipped in favour
of the last week that carried load.

## Explanation hooks

`explanation_keys` is a mechanical namespacing of vocabulary that already exists — the
plan's `week_focus`, then its `reason_codes` in order:

```
("weekly_program.focus.overload",
 "weekly_program.reason.progressing",
 "weekly_program.reason.volume_trend_down")
```

No second taxonomy is introduced and **no user-facing text is emitted here**. Turkish
UI copy for these keys is deliberately later work (as it already is for `reason_codes`
itself).

## Unsupported capabilities

`UNSUPPORTED_CAPABILITIES = ("session_frequency", "intensity_magnitude",
"exercise_selection")`.

A weekly program would normally carry these, but `AdaptivePlan` models none of them:

- **`session_frequency`** — the planner makes no frequency decision. `WorkoutLog`'s
  session count is observed history, not a prescription, so PR5 does not turn it into
  one.
- **`intensity_magnitude`** — deliberately unmodelled upstream: a single global
  intensity percentage is meaningless without per-exercise data, which `WorkoutLog`
  does not carry.
- **`exercise_selection`** — the domain of `training_generation/` and the coach.

They are reported so a consumer can render an explicit "unsupported" state rather than
receive a silently invented number. Filling them in requires new capability in
`AdaptivePlan` *first* — never a heuristic in this layer.

## Neutral behavior

`AdaptivePlan(weeks=0)` (empty history, `weeks <= 0`, or thin data) maps to
`WeeklyProgramRecommendation(weeks=0)`: focus `insufficient_data`, both actions
`hold`, delta `0.0`, no flags, both volume fields `None`,
`reason_codes == ("insufficient_history",)`, and explanation keys that already explain
the neutral state. The default-constructed object *is* the neutral recommendation — no
special-casing anywhere in the code.

## Determinism

Every `analysis` function is a total function of its inputs; the roll-up is
deterministic for a fixed `end_day` (no hidden clock, no side effects). User-scoped
through the planner's own `user_id` filter — this layer performs no query.

## Resolved: the forward-looking window (`NEEDED_FIXES.md` #5)

`NEEDED_FIXES.md` (triage 2026-07-21) finding #5 recorded that `detect_deload_due` was
effectively gated on "trained today" because `weekly_windows` made the newest window
forward-looking. It was rated *Low / Suspected* explicitly on the grounds that the
layer was **not yet wired into runtime** — a basis PR5 part 2 removed by exposing
`GET /api/training/weekly-program`.

The post-audit fix corrects the window geometry upstream (see *What "weekly" means
here*), and **one change resolves both symptoms**, because both had the same cause:

- **This layer's volume contract** — the newest window is a real trailing week, so a
  single day's session can no longer be published as `baseline_weekly_volume`.
- **`detect_deload_due`** — the newest window no longer reads as a phantom rest week on
  days the user has not yet trained, so the existing heuristic evaluates the block it
  was written for. `week_focus == "deload"` is now reachable against a live clock.

No progression, planning, or volume threshold changed: `detect_deload_due`,
`detect_plateau`, `assess_consistency`, `VOLUME_INCREASE_STEP` and `DELOAD_VOLUME_CUT`
are untouched. This was a windowing-correctness fix, not a policy change.
`tests/test_training_progression.py::test_deload_is_not_gated_on_having_trained_today`
pins the deload half; the multi-session cases in `tests/test_weekly_program.py` pin
this half.

## Failure semantics, contract versioning, observability

Recorded decisions from the PR5 production-readiness audit (findings F4–F6):

- **Failure (F4) — propagate as JSON, never as a neutral recommendation.** A planner or
  DB failure returns `{"error": ...}` with status 500 and `application/json`, matching
  the other JSON routes in `app/blueprints/training.py`. It deliberately does *not*
  follow the PR4 coach consumer's neutral-fallback: for the coach a degraded answer
  beats no answer, but here the neutral recommendation is a legitimate user state, so
  substituting it on failure would make an outage indistinguishable from a new user.
  Without this the global `errorhandler(500)` would render `500.html` — an HTML body
  from a JSON endpoint.
- **Versioning (F5) — no `schema_version`, deliberately.** PR4's block carries one
  because it is consumed by a model whose drift is invisible and it is versioned as a
  prompt contract; no HTTP endpoint in this repository carries a version field, and
  frontend and backend ship in the same deploy, so there is no skew to negotiate.
  Adding one would also weaken the bidirectional payload↔model test, which currently
  proves the API exposes exactly the model's fields. **Evolution rule:** this contract
  is additive-only — new fields may be appended, existing fields may not be renamed,
  retyped, or given new meaning. A breaking change introduces `schema_version` (or a
  new path) at that point, not speculatively.
- **Observability (F6) — minimal, non-sensitive.** The route emits one
  `[TRAINING][WEEKLY_PROGRAM]` debug line carrying `has_data`, `week_focus` and whether
  a baseline exists, plus a warning on failure. That is enough to tell "neutral because
  the user has no history" from "populated recommendation" from "upstream failure"
  before a UI depends on it. No history, volumes, payloads, or PII are logged.

## Tests

`tests/test_weekly_program.py` — pure translation tests (fixture-free: verbatim echo
across all six signals, the decisions-ignore-volume invariant, baseline selection
including skipped zero-volume weeks, exact target arithmetic for positive/zero/negative
deltas, the `None`-not-`0.0` contract, explanation-key ordering, neutrality,
determinism, immutability, declared unsupported capabilities) + DB-backed roll-up via
the `make_user` fixture (overload, deload, maintenance, build-consistency, marker-only,
empty history, `weeks=0`, user scoping, determinism) + the multi-session regression
block described below. Boundary/ownership guards live in
`tests/test_dependency_boundaries.py`.

**Multi-session fixtures (post-audit finding F3).** The original DB-backed fixtures
seeded exactly one workout per window, which makes a single day and a full week
numerically identical — the reason the forward-window defect was invisible to a green
suite, and why one test had encoded the partial-window value as its expected baseline.
`_seed_multi_session_block` now seeds a Mon/Wed/Fri-style block (three sessions per
trailing window, counted back from `end_day`), so the suite can distinguish one-day
partial data from a complete week. It covers: a day the user has already trained (the
baseline must be the 15000 kg week, not the 5000 kg session, and `baseline_week_start`
must not run into the future), the same block on a rest day (identical result — no
switching between a completed week and a one-session bucket), one-session versus
three-session weeks reading differently, a stable multi-week block, and a fixture
self-check that every window really does hold three sessions. All five fail against
the pre-fix geometry.

`tests/test_weekly_program_route.py` — the runtime surface: auth required, user
scoping, GET-only, response equal to `weekly_program_payload(build_weekly_program(...))`,
payload keys exactly the model's fields (neither withheld nor invented), neutral payload
on empty history, baseline/target correctness including the skipped zero-volume week and
the `null`-not-`0` contract, repeated calls identical, no mutation of the plan or its
embedded `ProgressionReport`, no writes, exactly one service call with pinned arguments,
query-string tampering ignored, the live-clock multi-session baseline case, a structured
JSON 500 on upstream failure that is *not* disguised as an empty recommendation,
byte-identical responses under both `AI_ADAPTIVE_PLAN_CONTEXT` states, and two AST
guards proving the view reads no history and performs exactly one build + one
projection.

## UI rollout (Sprint 6 PR6) — three PRs

The endpoint above still has **no frontend consumer**. Presenting it is split into three
independently reviewable PRs so the rollout boundary can be reviewed before any client
data handling exists:

| PR | Scope | State |
|----|-------|-------|
| **PR6.1** | Integration-surface discovery, characterization coverage, default-OFF UI flag, server-rendered mount shell, frontend initialization boundary | **Done** (this section) |
| PR6.2 | Fetch `GET /api/training/weekly-program`; loading / populated / insufficient-data / missing-baseline / structured-failure states; malformed-payload safety; localization; accessibility and responsive presentation | Not started |
| PR6.3 | Observability finalization, cache/privacy hardening, architecture guards, full four-way flag matrix, performance and SQL verification, mobile/a11y validation, production-readiness re-audit | Not started |

### PR6.1 — what exists today

**Integration surface: `/training`** (`app/blueprints/training.py:training` →
`templates/training.html`), mounted inside `#active-plan-view`, between the weekly
stats (`#wstats`) and the plan-meta row. That block is the page's existing weekly
overview ("Bu hafta" → week strip → weekly stats), which is the same information the
weekly program comments on, and `training.js` already shows it only when the user has an
active plan — so the future card follows the existing view logic instead of inventing
one. No new navigation destination, no second training dashboard, no change to plan
generation or plan controls.

**Feature flag: `WEEKLY_PROGRAM_UI_ENABLED`** (`app/config.py`), default **OFF**,
`os.getenv("WEEKLY_PROGRAM_UI_ENABLED", "0") == "1"` like every other boolean in that
module — only the exact string `"1"` enables it. Server-controlled: it is read once at
boot and mirrored into `app.config`; it is never derived from a query string, header,
cookie, endpoint availability, prompt or DOM text.

**It is presentation only, never an authorization boundary.**
`GET /api/training/weekly-program` stays `@require_auth` in every flag state.

**Independent of `AI_ADAPTIVE_PLAN_CONTEXT`.** Separate env name, separate config key,
separate read path — the coach flag governs what the AI may read, the UI flag governs
whether a mount point is rendered. Neither module mentions the other's flag. The full
four-way *runtime* matrix is PR6.3; PR6.1 proves structural independence.

**OFF path — the important contract.** The template omits the block server-side, so OFF
ships no markup, no data attribute, no script tag, no CSS, no spacing, no hidden
placeholder, no listener, no logging and no request. Whitespace-control markers
(`{%- if %}` / `{%- endif %}`) keep the rendered document **byte-identical** to the
pre-PR6.1 page — verified once with a normalized before/after diff (`_v`, CSP nonce and
CSRF token normalized; 82,664 bytes both times, empty diff) and kept honest afterwards
by `tests/test_weekly_program_ui.py`.

**ON path — a mount point, not a feature.** Exactly one inert element:

```html
<section id="weekly-program" data-weekly-program-mount aria-hidden="true"></section>
```

plus `static/weekly_program.js`. Nothing else: no title, no locale key, no
recommendation field, no payload, no user id, no loading skeleton, no empty/error copy.
`aria-hidden` marks the empty container inert while it has nothing to announce —
**PR6.2 must remove it** when real content lands. Total ON-path delta: +153 bytes.

**Initialization boundary.** `static/weekly_program.js` finds the shell, marks it
`data-weekly-program-initialized="1"`, and returns. It initializes once (the marker
lives on the DOM node, so a re-evaluated script cannot double-initialize), no-ops when
the shell is absent, and exposes `window.FitXWeeklyProgram.init` as PR6.2's extension
point. It performs **no** `fetch`/`XHR`, registers no listener, sets no timer, renders
nothing, and contains no threshold, target, or weekly-window arithmetic — `AdaptivePlan`
remains the single planning authority and the browser never becomes a second one.

**Localization boundary.** PR6.1 adds **no** locale key; the shell has no visible copy.
Note that `locales/{tr,en}.json` already carry an unused `training.weekly_program` key —
PR6.2 can adopt it rather than adding another. Reason-code and explanation-key copy
remains open (PR6.2).

**Rollback.** Set `WEEKLY_PROGRAM_UI_ENABLED=0` (or unset it) and restart — the shell,
the script tag and the request all disappear; no data migration, no schema, nothing to
undo. Reverting the commit is equally safe.

**Tests.** `tests/test_training_page_characterization.py` (69 — the pre-change
`/training` contract: route/auth, DOM regions, controls, navigation, assets),
`tests/test_weekly_program_ui.py` (33 — OFF absence and seam preservation, ON shell
uniqueness/emptiness/placement, no recommendation data, flag isolation, route AST
guards, zero extra SQL), `tests/test_weekly_program_ui_flag.py` (21 — subprocess-verified
env defaults, parsing convention, request-data immunity, flag independence),
`tests/test_weekly_program_ui_js.py` (40 — source guards plus behavioral execution under
`node` with a DOM stub), and one `.env.example` guard in `tests/test_env_example.py`.
