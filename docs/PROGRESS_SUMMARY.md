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
                   "distance_to_target_kg": 3.4 },
  "performance": { "state": "progressing", "volume_trend": "up",
                   "strength_trend": "flat", "next_signal": "progressing" },
  "consistency": { "state": "consistent", "active_weeks": 4,
                   "analyzed_weeks": 4, "sessions": 12 }
}
```

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

`static/progress.js` **translates**; it does not decide.

- YOUR PROGRESS, BODY, PERFORMANCE and CONSISTENCY all render one payload, so
  the page cannot show a card that disagrees with its own headline.
- Every state arrives as a bounded enum and is looked up in an explicit table.
  An enum the build does not know renders the neutral state, never a guess.
- Forbidden and test-enforced: `sessions >= 3 → on_track`,
  `weightDelta < 0 → success`, `streak >= X → consistent`, or any threshold on
  a summary field (`test_client_never_fabricates_a_trajectory`).
- The gamification streak is no longer a Progress consistency signal at all.
  Logging in is not training. `/api/progress/achievements` is unchanged and
  still serves its other consumers.
- Trajectory is never communicated by colour alone: `#ps-state` always spells
  the state out, and `data-state` (accent only) is written by the same function
  that writes the label.
- A summary failure degrades YOUR PROGRESS and WHAT CHANGED only. AXIS
  INSIGHTS, PHYSIQUE PROGRESS and PROGRESS HISTORY own separate fetches and keep
  loading.

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
