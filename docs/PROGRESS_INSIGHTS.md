# Progress Insights — canonical Axis Insights

The single server-owned authority behind the Progress page's **AXIS INSIGHTS**
section. Introduced by **Progress Redesign PR3**; PR1 (#212) built the
information architecture and PR2 (#215) built the trajectory read model that
this layer selects from.

It answers exactly three questions, one per slot, and nothing more:

1. **What's working?** → the one positive signal worth saying out loud
2. **What should I watch?** → the one concern that outranks the others
3. **What do I do next?** → the canonical next training action

Everything else the Progress page shows — trajectory, body, performance,
consistency, heatmap, achievements — is unchanged and still owned by its
existing layer.

---

## 1. Authority map

After PR3 exactly one layer owns each concept:

| Concept | Owner |
|---|---|
| Which signal earns which insight slot | `app/services/progress_insights` |
| Trajectory, performance state, consistency state, body context | `app/services/progress_summary` |
| The next weekly adjustment (`week_focus`, volume/intensity action, delta) | `app/services/training_planning` |
| Trend / plateau / deload / consistency / `next_signal` | `app/services/training_progression` |
| Raw workout history, week geometry, marker semantics | `app/services/training_history` |
| Day / timezone | `app/timeutil` (fixed `Europe/Istanbul`) |
| Presentation | `static/progress_insights.js` + `locales/{en,tr}.json` |

Dependency direction is one-way and enforced by a test
(`tests/test_progress_insights.py::test_dependency_direction_is_one_way`):

```
static/progress_insights.js
      ↓
GET /api/progress/axis-insights     (app/blueprints/tracking.py)
      ↓
progress_insights
      ↓
progress_summary        training_planning
      ↓                       ↓
      └──→ training_progression
                  ↓
           training_history
```

Nothing under `progress_summary`, `training_planning`, `training_progression` or
`training_history` imports `progress_insights`, so no cycle can form.

**This layer measures nothing.** Not one threshold, count, percentage or trend
is computed in it. It is *selection* on top of vocabularies other layers own —
which is the whole reason it can exist without becoming a second progress
authority.

---

## 2. Architecture

Layering mirrors `progress_summary`:

| Module | Responsibility |
|---|---|
| `models.py` | Frozen value objects + every bounded vocabulary constant |
| `analysis.py` | Pure selection: canonical state → slot. No DB, no Flask, no clock |
| `payload.py` | Explicit JSON projection of the wire contract |
| `__init__.py` | `build_progress_insights` orchestrator + public API |

There is deliberately **no `queries.py`**. A read of its own would make this
layer the owner of a fact nobody else owns — exactly the second authority PR3
exists to prevent. Everything it needs is composed from existing public pure
functions over a **single** `build_progression_report` call:

```python
report      = build_progression_report(user_id, weeks=INSIGHTS_WEEKS, end_day=end_day)
performance = summarize_performance(report)     # progress_summary, pure
consistency = summarize_consistency(report)     # progress_summary, pure
plan        = derive_adaptive_plan(report)      # training_planning, pure
```

`tests/test_progress_insights.py::test_history_is_read_exactly_once` pins that
count at one, so a future refactor cannot quietly double the history read.

### Public service API

```python
build_progress_insights(user_id, *, end_day=None) -> ProgressInsights
```

Invariants:

- user-scoped; the caller's id is the only owner expressible
- read-only — no add, no delete, no flush, no commit, no plan mutation, no
  quest/streak/XP side effect
- deterministic for a fixed `end_day`
- no LLM / Bedrock / OpenAI / Groq / Anthropic call, no prompt, no prompt cache,
  no model fallback
- `end_day` defaults through `app.timeutil.app_today()` and is resolved **once**,
  so a request straddling Istanbul midnight cannot report a window the signals
  were not computed over
- window fixed at `INSIGHTS_WEEKS`, which **is** `progress_summary.SUMMARY_WEEKS`
  (imported, not re-declared)

---

## 3. API contract

```
GET /api/progress/axis-insights
```

Authenticated with the existing web boundary (`@require_auth`). **No input at
all**: no `user_id`, no `weeks`, no `end_day`. A client that can re-window the
analysis until the answer improves owns the insight, and the server is supposed
to. Query strings are ignored, not rejected.

Response carries `Cache-Control: private, no-store`.

Example (illustrative values only — nothing here is hardcoded):

```json
{
  "contract_version": 1,
  "window": { "weeks": 4, "start": "2026-07-19", "end": "2026-08-15",
              "timezone": "Europe/Istanbul" },
  "working": {
    "status": "available", "code": "training_progressing", "domain": "training",
    "evidence": { "performance_state": "progressing" }, "action": null
  },
  "watch": {
    "status": "available", "code": "deload_due", "domain": "training",
    "evidence": { "reason_code": "deload_due", "week_focus": "deload" },
    "action": null
  },
  "next_move": {
    "status": "available", "code": "deload", "domain": "training",
    "evidence": null,
    "action": { "week_focus": "deload", "volume_action": "decrease",
                "intensity_action": "hold", "volume_delta_pct": -0.4 }
  }
}
```

Contract rules:

- versioned (`contract_version`), machine-readable, deterministic
- all three slots always present, always the same five keys, so a client never
  branches on key existence
- no localized prose, no database id, no user id, no ORM serialization, no
  provider output, no free text
- `null` means *unavailable / not known*; `0` means *a real measured zero*
- no lists anywhere: a slot is one decision, and a list would let the client
  re-rank it
- the window is identical to `/api/progress/summary`'s window
  (`tests/test_progress_insights_api.py` compares the two responses), so the two
  sections cannot describe different periods

---

## 4. The three slots

Every slot is one of:

```
available            a canonical code, with the evidence behind it
empty                understood, and there is genuinely nothing to say
insufficient_data    not enough history to say anything honestly
```

`empty` and `insufficient_data` are **different statements** and are never
collapsed. "Nothing needs your attention" is a finding; "we cannot tell yet" is
not.

### WHAT'S WORKING — `select_working`

Precedence, most specific first:

| # | Condition | Published code |
|---|---|---|
| 1 | performance `progressing` | `training_progressing` |
| 2 | performance `steady` | `training_steady` |
| 3 | consistency `consistent` | `training_consistent` |
| 4 | performance `building_baseline` | *(status `insufficient_data`)* |
| 5 | otherwise (`building_consistency`) | *(status `empty`)* |

Rule 3 is the invariant this slot exists for: a user whose trajectory is
`needs_attention` because of a plateau or a due deload **has still trained
consistently**, and saying so is true. Its copy describes consistency and
nothing else, so it cannot read as "everything is fine".

`plateau`, `deload` and `building_consistency` deliberately map to no positive
code. None of them is a positive claim, and manufacturing one from them is
precisely what this slot must not do.

### WATCH THIS — `select_watch`

`AdaptivePlan.reason_codes` is already ordered by the planner: position 0 is the
primary cause and the trend nuances follow. This walks that list **in the
planner's order** and takes the first attention-worthy code. It never re-sorts —
a second priority ladder here is the duplicated authority PR3 forbids.

| Planner reason code | Published watch code |
|---|---|
| `inconsistent_training` | `build_consistency` |
| `deload_due` | `deload_due` |
| `plateau_detected` | `plateau_detected` |
| `volume_trend_down` | `volume_trend_down` |
| `strength_trend_down` | `strength_trend_down` |
| `insufficient_history`, `progressing`, `steady_state` | *(not a concern — skipped)* |

Those two sets **partition** the planner's entire vocabulary, so an
unrecognised code is unambiguously new rather than merely uninteresting, and
raises instead of degrading into an all-clear.

A non-attention primary does not suppress the rest of the list: when the planner
says `["steady_state", "volume_trend_down"]` it has chosen not to *act* on the
down-trend while still recording it, and surfacing that recorded nuance is what
the secondary codes are for. `week_focus == "insufficient_data"`
short-circuits to `insufficient_data` — with too little history there is no
honest way to name what deserves attention.

### NEXT MOVE — `select_next_move`

The single mandatory rule of PR3: the next move comes from
`AdaptivePlan.week_focus` and **from nothing else**.

| `week_focus` | Published code |
|---|---|
| `insufficient_data` | `build_baseline` |
| `build_consistency` | `prioritize_consistency` |
| `deload` | `deload` |
| `maintenance` | `maintain_and_consolidate` |
| `overload` | `progress_training` |
| `steady` | `maintain_current_training` |

Sessions, volume trend, strength trend, consistency counts, trajectory and body
change are *evidence the planner already weighed*; deriving a move from them
here would be a second planning engine that can contradict the first.

This slot is **always `available`**. Even with no history the planner emits a
real, canonical, deliberately neutral decision (`insufficient_data` →
`build_baseline`), which is a genuine next move — "log some training so there is
something to coach on" — rather than a filled-in blank.

The quantified adjustment (`volume_action`, `intensity_action`,
`volume_delta_pct`) is copied **verbatim** from the plan. This layer never
computes a magnitude and never decides that 5% is appropriate; it only carries
the number `training_planning` owns (`VOLUME_INCREASE_STEP`,
`DELOAD_VOLUME_CUT`), and a test compares the published fraction against those
constants.

---

## 5. Why `AdaptivePlan` is the next-move authority

`training_planning` already maps `next_signal` 1:1 onto a weekly decision, with
its constants explicit and its non-decisions (per-exercise intensity magnitude)
deliberately unmodelled. Re-deriving "what should I do next" on the Progress
page would produce a second answer that can disagree with the AI Coach, the
weekly program surface and the plan page — all of which read the same planner.
One authority, three renderings.

`tests/test_progress_insights.py` compares `NEXT_MOVE_BY_WEEK_FOCUS`'s keys
against `training_planning`'s own focus table, so a new focus breaks PR3 loudly
instead of quietly becoming "steady".

---

## 6. Why no LLM in V1

An insight is a *contract*, not a sentence. Every published value is a bounded
enum from a table in this repository; the localized text lives in
`locales/{en,tr}.json` and is chosen by code. That buys determinism (the same
history always yields the same insight), testability (the decision tables are
asserted exhaustively), zero provider cost and latency on a page load, and no
possibility of a model inventing a recommendation nobody wrote down.

The AI Coach remains the place where a user gets prose. It reads the same
`AdaptivePlan` through `adaptive_plan_context`, so the two cannot contradict.

---

## 7. Why the body is not judged

Calling a kilogram "working" or "worth watching" requires validated
rate-of-change thresholds this repository does not own — and the same movement
means opposite things for a cut and a bulk. `docs/PROGRESS_SUMMARY.md` keeps
body **contextual, not a vote**, for exactly this reason; PR3 inherits that
decision, so weight movement is not a candidate for any slot at any position.

Nutrition, recovery, hydration, sleep and readiness are excluded for the same
reason at a larger scale: this build has no canonical scoring authority for any
of them. `DOMAINS = ("training",)` is a single-element tuple on purpose — the
contract already carries a `domain` field so a future domain can be added
additively, without renaming anything or re-cutting the three slots.

---

## 8. Missing is not failure

Three outcomes are kept strictly apart:

| Situation | Slot status | Meaning |
|---|---|---|
| Understood, nothing to report | `empty` | a finding |
| Not enough history | `insufficient_data` | evidence gap |
| The endpoint failed | HTTP 500 → client renders `unavailable` | an outage |

A failure is **never** presented as an empty insight, and an empty insight is
never presented as a failure. `tests/test_progress_insights_api.py` and
`tests/test_progress_insights_ui.py` both pin this, and the browser matrix
audits the failure state at all four viewports.

### Error behaviour

Unknown canonical vocabulary fails closed: `UnknownCanonicalVocabulary` →
generic 500 with the localized retry message. The route logs one PII-free line:

```
[PROGRESS][AXIS_INSIGHTS] request_id=... working=... watch=... next_move=...
[PROGRESS][AXIS_INSIGHTS] request_id=... state=error error_class=contract_drift
```

`error_class` reuses the PR2 bucketing helper (`contract_drift` /
`unexpected_error`); no exception text, user id, payload or history reaches the
log. The failure is isolated: `/api/progress/summary`,
`/checkin-history`, `/api/progress/achievements` and the Progress page itself
all still return 200 when this endpoint is down. (The legacy
`/api/progress/insights` heuristic was retired by Sprint 13 PR5.)

---

## 9. Window and time

- `weeks` = `INSIGHTS_WEEKS` = `progress_summary.SUMMARY_WEEKS` (4)
- `end` = `app_today()`, resolved once in the orchestrator
- `start` comes from `training_history.weekly_windows(end_day, 4)[0]` via
  `progress_summary.build_window` — no parallel date arithmetic exists here
- `timezone` is the fixed `Europe/Istanbul` from `app/timeutil`
- the window is **not** query-driven and not client-derived

---

## 10. Read-only guarantee

No write of any kind: no `session.add`, no `flush`, no `commit`, no plan
mutation, no XP/quest/streak/challenge/notification side effect, no cache, no
persistence, no schema change, no migration. A test runs the whole endpoint with
`db.session.commit` / `add` / `flush` monkeypatched to raise.

---

## 11. Frontend boundary

`static/progress_insights.js` is a **translator, not a decider**:

- one `fetch` of `/api/progress/axis-insights`, no second endpoint
- every published code is looked up in an explicit table; an unknown code
  degrades to the slot's neutral copy instead of being guessed at or printed raw
- no threshold, no comparison against any canonical field, no score, no
  percentage arithmetic — structural tests reject `* 100`, `Math.round`,
  `toFixed`, and any numeric comparison
- the only number it renders is `volume_delta_pct`, formatted (not computed) by
  `Intl.NumberFormat` with `style: 'percent'`
- DOM is built with `textContent`; no `innerHTML`
- copy comes from `window.I18N` — the same catalog the server uses, so EN and TR
  are symmetric by construction and a missing key is a test failure

The three slots are server-rendered as structural landmarks (`<h2>` section,
three `<h3>`-labelled `<article>`s in a fixed order) and stay legible without
JavaScript. Status is never colour-only: each state changes text as well.
There is no carousel, no tab strip, no chart, and no horizontal scroller.

---

## 12. Legacy compatibility

`GET /api/progress/insights` (the streak/calorie/workout-count/weight heuristic
list) was **retired by Sprint 13 PR5 (F9)**. It had no first-party consumer
and owned an unowned calorie-adherence judgement (`80 ≤ pct ≤ 110` →
`success`). The live Axis Insights surface is this document's
`GET /api/progress/axis-insights` route; the two must not be confused.
Canonical Progress did not inherit the heuristic.

---

## 13. Deferred

Not in PR3, by explicit scope: Pump Check (PR4), Progress History (PR5),
nutrition/recovery/hydration insight domains, body verdicts, gamification
signals, persistence or caching of insights, and any mobile/Flutter surface.

---

## 14. Evidence

- `tests/test_progress_insights.py` — service, decision tables, purity,
  fail-closed, authority direction
- `tests/test_progress_insights_api.py` — contract, auth, isolation, headers
- `tests/test_progress_insights_ui.py` — template semantics, client boundary,
  locale symmetry
- `docs/frontend-readiness/progress-pr3/validation-manifest.json` — hermetic
  Chromium matrix: 7 states × 4 viewports (390 / 768 / 1280 / 1440), including
  the endpoint-failure state, produced by
  `scripts/frontend_audit/progress_pr3_matrix.py`
