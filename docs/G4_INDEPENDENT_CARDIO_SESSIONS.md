# G4: independent cardio sessions (#530) with P3A preserved

Status: source only, stacked on #555 (G1–G3). Nothing is activated in any environment, and there is no production or staging change.

The founder rulings (2026-11-23) are G4_SOURCE_GO, D1, D2 and D3. #537 was reverted and is **not** re-landed unchanged. Its independent frequencies, its general-template slot order and its prompt intent are reused. Its mixed strength/cardio list is replaced by a separate session.

## 1. Plan model

A strength day (`tip="antrenman"`) may carry **one** cardio session:

```json
{"gun": "Pazartesi", "tip": "antrenman", "odak": "...", "sure_dk": 45, "tahmini_kalori": 300,
 "egzersizler": [ /* strength / core only (P3A) */ ],
 "kardiyo_seansi": {"egzersizler": [{"exercise_id": "ex_outdoor_run", "isim": "...",
                                       "set": 1, "tekrar": "20 dk", "dinlenme": "0 sn", "not": ""}]}}
```

- **Contents.** Cardio movements only, at least one entry. Each entry is one round of `"<n> dk"`.
- **Duration.** Derived from the entries, never stored, and capped at 600 minutes. `sure_dk`, `odak` and `tahmini_kalori` describe the strength session only.
- **Where it can appear.** A `kardiyo` or `dinlenme` day never carries a session. There are still exactly seven unique weekdays.
- **Cardio count.** Cardio = `kardiyo` days + sessions. Rest = 7 − strength − `kardiyo` days. Strength 3..6 and cardio 0..6 are all representable.

## 2. Placement: one rule for generation, save, editor and Coach

- `exercise_resolution.check_session_placement` is the dedicated-cardio-day rule (`require_cardio_on_dedicated_day=True`) applied against `{"tip": "kardiyo"}`.
- `canonicalize_generated_exercises` and `canonicalize_plan_exercises` canonicalize the session with that rule. The day list keeps the original `check_placement`.
- The editor engine (`plan_mutation/document.py`), which Coach writes also go through, uses the same placement for `session="cardio"`.
- Equipment is not bypassed. Cardio is still gated by `cardio_type`, which is only sound because a session **is** a cardio placement. Swimming in an `ev`/`kosu` plan is refused in both lists.
- Nothing moves between lists automatically: no substitution and no reassignment.
- **Legacy mixed days.** These are pre-P3A or #537-era cardio items inside a strength list. Founder ruling: they are **not locked**.
  - Strength edits are accepted, and the stored cardio is preserved byte-for-byte.
  - An edit may never add misplaced cardio.
  - An edit may never leave misplaced cardio beside no strength or core work, because that would be a misleading day type.
  - Correcting the cardio (Replace or Remove) is always available.

## 3. Capability gate: `include_sessions=1`

The opt-in is strict, like LP21's `include_origin`: absent means legacy, exactly `1` means sessions, anything else is refused and never coerced.

| Surface | Without the opt-in (every existing client, including 101119 and 101124) | With `include_sessions=1` |
|---|---|---|
| `GET /training/preferences` | unchanged; the `WEEK_ALLOCATION_EXCEEDS_SEVEN_DAYS` constraint stays | that constraint is absent |
| `POST /training/plans` (AI) | unchanged: prompt byte-identical, strength + cardio > 7 conflicts, the session key is refused | the prompt describes the session block; the opt-in is part of the request fingerprint |
| `POST /training/plans/general` | unchanged choices (cardio ≤ 6 − strength) and byte-identical content | cardio 1..6; overflow lands on strength days as sessions; pre-G4 combinations produce identical content and fingerprints |
| `GET /training/plans/current`, `/training/editor` | strength session only (exact-key DTOs unchanged) | adds `cardio_session` to each day, plus `today_progress` |
| `POST /training/plans/replacement-proposals` | unchanged | not opted in yet: replacement candidates never contain sessions (follow-up) |

A legacy client reading an account whose plan was made by a new client sees the strength session only. It can view, start, complete and edit exactly what it can represent. The cardio session is preserved and comes back on an updated client. Nothing is lost or rewritten.

## 4. Sessions, identity and progress (D1, D2)

- **Identity.** The session reference is `workout_ref(..., slot + 7)`: opaque, HMAC-bound to owner, lineage and version, and never equal to a day reference.
  - `build_workout` never resolves it, so it cannot start a workout session and cannot claim the day.
- **Optional explicit answer (D2).** `POST /api/v1/training/sessions/<ref>/complete` or `/skip`.
  - There is no body, no Start Workout, no timer and no set execution.
  - Only today's session of the current plan revision can be answered.
  - The same answer again is an idempotent replay. A different answer is `409 TRAINING_SESSION_OUTCOME_CONFLICT`, never an overwrite.
  - Other refusals: `TRAINING_SESSION_NOT_FOUND`, `TRAINING_SESSION_STALE`, `TRAINING_SESSION_NOT_TODAY`, `TRAINING_SESSION_INVALID_REQUEST`.
  - Health consent is required, as on every Training route.
- **Storage.** Table `training_session_outcome`, one row per owner, day and session kind (`cardio`), with outcome `completed` or `skipped`. The plan lineage and version are stored as provenance.
  - It is deleted with the `workouts` health-erasure category, by account purge, and by FK `ON DELETE CASCADE`.
- **Progress (D1).** Reported as `today_progress` (opt-in only):
  - Per session: `{session: "day" | "cardio_session", kind, workout_ref, state: completed | skipped | incomplete}`.
  - The day's own state is the existing canonical completion claim (`workout_completion_day`, or a historical Pump Check).
  - Day status:
    - `complete`: every session completed.
    - `complete_with_skips`: all sessions terminal, at least one skipped.
    - `partial`: some sessions terminal.
    - `not_started`: none terminal.
  - A single-session day keeps exactly its existing meaning.
- **Rewards (unchanged, deliberately).** A session outcome never writes or alters `workout_completion_day`, XP, streaks, quests, Pump Check or the completion fence.
  - Existing streak, reward and progress metrics keep counting the day's own workout.
  - Crediting cardio-session outcomes in any metric needs a contract-level decision first (D1).
  - The legacy `workout_state.primary_state` likewise still describes the day's own workout.

## 5. Coach

- `get_current_training_plan` publishes a day's session as `cardio_session: {exercises: [...]}`, using the same closed exercise projection. `coach_privacy` re-projects it.
- Coach write tools build commands without a session selector, so they address the strength list only, under the same placement rule.
- `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED` stays `0`. No Coach WRITE is activated.

## 6. Migration and release class (D3)

- **Migration.** `5cc5aa7e47ba` (down `19cb998acd2f`) creates the empty `training_session_outcome` table.
  - It is expand-only and verify-or-create.
  - Downgrade refuses while any outcome exists; in that case roll back code and keep the schema.
  - The graph has one head.
- **Forward-only.** A release that persists a session is forward-only. Pre-G4 code's closed `DAY_KEYS` rejects `kardiyo_seansi`, so after any session plan is saved, rolling code back would make that plan unprojectable.
  - **Recovery:** roll forward, or have the owner remove the session (editor `remove` with `session="cardio"` until it is empty).
  - No stored plan is rewritten by G4. Historical plans, completions and workouts are untouched.
  - This does not apply to frozen R1, and production deployment needs separate founder authorization.
- **Retired `c1a9b7e5d301` databases.** The opt-in reconciliation (`app/retired_revisions.py`) now upgrades through this revision too. `confirm` requires a complete upgrade to the current head.

## 7. Mobile (#210 owner: contract to implement, branch not edited here)

1. Send `include_sessions=1` on `GET /training/preferences`, `GET /training/plans/current`, `GET /training/editor`, `POST /training/plans` and `POST /training/plans/general`.
   - Decode `cardio_session` strictly with keys `{workout_ref, duration_minutes, exercises}`, nullable. The exercise DTO is unchanged.
   - Decode `today_progress` strictly: nullable, keys `{date, status, sessions}`, and session keys `{session, kind, workout_ref, state}`.
2. Render the session as its own block on WeeklyPlan and Today. Strength `duration_minutes` excludes it.
3. Offer the two explicit answers (complete or skip) for today's session. Do not route the session reference to workout start.
4. Editor: send `"session": "cardio"` for session rows. Positions are session-relative. Omit the field for strength rows.
5. General-plan cardio choices 1..6 (from #210) are valid only together with the opt-in.
6. Error codes to map: the five `TRAINING_SESSION_*` codes; a malformed opt-in is `TRAINING_READ_INVALID_REQUEST` / `TRAINING_EDITOR_INVALID_REQUEST` (400) on reads and `TRAINING_PLAN_INVALID_REQUEST` (422) on plan creation.
