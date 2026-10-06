# TI-03: deterministic training facts and diagnostics

Implements TI-00 §10/§14/§15 and the frozen §16 Training Insight projection,
as amended narrowly by TI-03 (exercise ownership + frozen primary selection;
see the amendment block in the [TI-00 contract](superpowers/specs/2026-10-05-ti-00-training-intelligence-contract.md)).
Read-only, deterministic, no migration, no persisted diagnostics, no provider.

## Read API

`GET /api/v1/training/workout-sessions/<session_reference>/training-insight`
(bearer owner, `mobile_api`, same `_flag_gated` family discipline as the other
session routes). Absent (404 `TRAINING_SESSION_NOT_FOUND`) unless
`FITX_WORKOUT_SESSIONS_ENABLED`, `FITX_TRAINING_EXECUTION_CONTEXT_ENABLED` (P0)
and `FITX_TRAINING_INSIGHTS_ENABLED` (P1) are all ON; P1 without P0 is invalid
and stays absent. Absent and foreign sessions are the same 404. A read failure
is 503 `TRAINING_SESSION_UNAVAILABLE`, retryable, `Session-Resolution: retry`,
never an empty or insufficient answer. Missing evidence is a normal 200.
`GET /api/v1/training/workout-execution-capabilities` now reports
`insights_enabled` from the same readiness.

```json
{"training_insight": {
  "contract_version": 1, "ruleset_version": "ti_rules_v1",
  "session_ref": "<opaque owned ref>", "checkpoint_revision": 4,
  "state": "available", "kind": "performance_improved",
  "exercise_id": "ex_barbell_back_squat",
  "title_key": "training_insight.performance_improved",
  "evidence": [
    {"metric": "reps", "unit": "int", "previous": 24, "current": 25,
     "paired_sets": 3, "previous_session_ref": "<opaque owned ref>"}],
  "missing_data": ["missing_tempo"],
  "recommended_action": null}}
```

`state` ∈ `available|insufficient_data|not_comparable`. `kind` is the closed
§14 vocabulary (`short_rest_confound` is reserved and unreachable), `null` for
`not_comparable`, `insufficient_comparable_history` for insufficient data.
`title_key` is `training_insight.<kind>`, or `training_insight.not_comparable`.
At most 8 evidence entries (7 metrics exist), at most 12 missing codes. The
projection re-validates every value and raises (→ 503) rather than emit
anything outside the contract.

## Provenance and eligibility

Evidence comes only from the final acknowledged checkpoint of an owned
`COMPLETED` `WorkoutSession`, its TI-01A anchor-verified execution context
(`project_context`) and its immutable start prescription (`prescription.project`).

* Session: owned, `status = completed`, `completed_at` present, and the
  Istanbul date of `completed_at` equals `workout_date`. Otherwise the session
  is excluded (`inconsistent_date`), never re-dated.
* Checkpoint: re-validated with the canonical write-path rules
  (`checkpoint.parse_stored_exercises`). Missing or out-of-contract →
  the whole session is `unavailable` and counted in coverage.
* Set: `completed = true`. Comparisons additionally need non-null reps AND
  load, and an index below the prescribed set count.
* Never evidence: ACTIVE/ABANDONED sessions, `TrainingPlan` (current plan),
  `WorkoutLog` markers or name-only legacy rows, Pump Check, notes, provider
  output, client drafts.
* Plan identity (lineage, mutation version) comes from the start snapshot, which
  both transports capture; browser-started rows have null lineage columns.

## Facts (`facts.py`, pure)

| Fact | Definition | Unit | Coverage / missing |
|---|---|---|---|
| Weekly set exposure | completed set identities (session, exercise, index) per catalog exercise, Mon–Sun Istanbul window | sets | eligible vs observed sessions; unavailable sessions are never zero |
| Exercise frequency | distinct `workout_date`s with ≥1 completed set of the exercise | days | same |
| Training frequency | distinct dates of date-consistent completed sessions (completion is the evidence; no set count implied) | days | same |
| Rep volume | Σ reps of completed sets, null excluded, 0 kept | reps | observed / eligible counts; `None` if nothing observed |
| Load volume | Σ reps×kg over completed sets with both values, exact in tenths | kg·reps | observed / eligible; internal fact, not v1 evidence |
| Recent performance | last ≤8 observed sessions before the target within 56 days containing the exercise, by (`completed_at`, ref) desc | — | excluded rows in the horizon reported |
| RIR | recorded buckets `0..3, 4_plus` over completed sets | ordinal | samples / eligible; unknown stays unknown |
| Logging interval | `completion_gap` intervals only, lower median | seconds | samples / eligible; other methods never combined |

## Comparator v1 (`comparability.py`)

The immediately previous observed session within 56 days, among the last ≤8
containing the exercise, whose start context matches: same weekday slot, same
non-null snapshot lineage and mutation version, identical prescription entry.
Then ≥2 eligible pairs with identical index sets. Refusals use only the TI-00
codes:

| Condition | State | Code |
|---|---|---|
| unknown/inactive identity, cardio/mobility, bodyweight/pull-up-bar/band equipment | insufficient | `insufficient_pairs` |
| target session has no snapshot entry | insufficient | `missing_prescription` |
| no previous occurrence | insufficient | `insufficient_pairs` |
| latest occurrence: no snapshot / other slot only | insufficient | `missing_prescription` / `insufficient_pairs` |
| latest occurrence: other lineage/version/targets | not comparable | `plan_changed` |
| < 2 eligible pairs | insufficient | `insufficient_pairs` |
| eligible index sets differ | not comparable | `insufficient_pairs` |
| performance tradeoff | not comparable | `mixed_performance` |

## Diagnostics (`diagnostics.py`) and thresholds (`models.py`)

| Kind | Evidence required | Minimum | Meaning |
|---|---|---|---|
| `performance_improved` / `_declined` / `_stable` | paired reps+load | 2 pairs | equal loads → summed reps; equal reps → per-index loads; else per-index dominance on both. Tradeoffs are mixed. No e1RM. |
| `effort_increased` / `_decreased` / `_stable` / `_mixed` | paired non-null RIR | 2 pairs | ordinal; lower RIR = more self-reported effort |
| `execution_quality_context_changed` | paired non-null tempo adherence | 2 pairs | categorical distribution changed; not a score |
| `volume_increase_context` | two complete prior weeks, full coverage | prior > 0 | ≥ 2 extra sets AND extra ≥ 20% of prior |
| `exposure_frequency_context` | same | prior days > 0 | ≥ 1 extra trained date |

`missing_rir` / `missing_tempo` are reported only when pairs exist but fewer
than 2 carry the value. `rest_method_unsupported` is reported whenever paired
logging intervals exist: they are described, never diagnosed. Exposure with a
zero baseline yields no context (no code); incomplete coverage or a truncated
history read yields `incomplete_coverage`; excluded rows in the horizon yield
`history_unavailable`.

## Policy (`policy.py`)

Primary selection is the frozen amended §16 rule. At most one lever, for the
primary exercise, `observe = next_comparable_session`:

1. rest — reserved, never emitted (no precise rest method exists);
2. `tempo/follow_prescribed_tempo` — tempo changed, current includes
   `lost_control`, structured target tempo present (identical across the pair);
3. `effort/aim_prescribed_effort` — effort increased, structured target RIR,
   some current RIR below target, no mixed performance or tempo change;
4. `current_approach/hold_current_approach` — performance declined with an
   exposure-increase context while effort/quality are ambiguous.

Current legacy plans carry no structured tempo/RIR targets, so only (4) is
reachable at launch. No load, set, rep, frequency, exercise or deload action
exists; nothing can reach a plan writer.

## Non-causality

Recovery, fatigue, injury, readiness, physiological rest and e1RM are never
inferred. A completion gap is a between-set logging interval; it can never
produce `short_rest_confound`. Pump Check and notes are not read.

## Query bounds

Two statements per request: the owned-session lookup, then one history query
(`user_id`, `status = completed`, `workout_date IN` the 57 Istanbul day keys
ending at the target's `workout_date`, `ORDER BY completed_at DESC NULLS LAST,
public_id DESC`, `LIMIT 64`). Hitting the limit is reported as
`incomplete_coverage`. Everything after is pure and linear in ≤64 rows × 32
exercises × 20 sets. Windows anchor on the target session, so the answer does
not depend on read time.

## Observability and privacy

Metric `TrainingInsight`, one `Event` dimension: `insight_generated`,
`insight_insufficient`, `insight_not_comparable`, `insight_unavailable`,
`history_row_excluded`. No user, session, exercise or value labels; no success
logging; a failure logs only the error type and request id. The payload carries
only the target's and the previous session's opaque owned references and public
catalog IDs.

## Non-scope

Flutter/TI-04 UI, LLM prose, plan mutation, persistent exercise notes, Pump
Check interpretation, e1RM, recovery scoring and analytics refactors.
