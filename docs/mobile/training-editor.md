# Native Training Editor

All routes are under `/api/v1` and use the existing mobile bearer, account eligibility, and first-party health-consent boundaries. Responses are non-cacheable. Manual editing is independent of the Coach write flag and AI consent. It makes no provider calls.

## Read and search

- `GET /training/editor` returns `{contract_version: 1, plan: null}` for an owner without a current plan. Otherwise `plan` has the existing `GET /training/plans/current` shape plus `snapshot`, an opaque exact-document digest. Send `plan_lineage`, `mutation_version`, and `snapshot` together on every edit. The existing Today, current-plan, and workout response shapes stay compatible. Plans without a verified exercise context or without projectable exercise IDs return `TRAINING_EDITOR_PLAN_UNAVAILABLE`. They require a supported canonical plan before native edits.
- `GET /training/exercise-catalog?q=...` returns `{contract_version: 1, catalog_version: 2, exercises: [...]}`. Each result has `exercise_id`, `display_name`, `equipment`, `movement`, and `primary_region`. Omit `q` or send an empty value to browse. A search accepts up to 80 characters. Accent-insensitive TR/EN partial matching ranks exact names and aliases first. Each result retains its own canonical ID. A missing movement returns an empty array. The picker can use the metadata while the mutation enforces the plan's saved equipment and cardio context.

## Write contract

Send JSON and an `Idempotency-Key` header of 8–64 ASCII letters, digits, or `._:-`. Every operation requires the three read revision fields. Use the same key and body on delivery retry. A new intent, including an edit resubmitted from a fresher revision, needs a new key.

| POST route | Additional required fields | Optional fields |
|---|---|---|
| `/training/editor/add` | `day_slot`, `exercise_id`, `sets`, `reps` | |
| `/training/editor/remove` | `day_slot`, `position`, `exercise_id` | |
| `/training/editor/replace` | `day_slot`, `position`, `exercise_id`, `replacement_id` | `sets`, `reps` |
| `/training/editor/prescription` | `day_slot`, `position`, `exercise_id`, at least one of `sets` or `reps` | Either prescription field when the other is present |
| `/training/editor/undo` | `mutation_id` from the most recent reversible user edit | |

`day_slot` is Monday through Sunday, indexed 0–6. `position` is the zero-based index in that day's `exercises` array. The server verifies the exact plan revision and the ID at the specified index under lock. Send the index from the original response if the UI sorts or filters rows. Position expires when that revision changes. It can distinguish two occurrences with identical IDs and prescriptions.

`sets` accepts integers 1–100. `reps` is non-empty trimmed text up to 40 characters, such as `"10"`, `"8-12"`, or `"30 sn"`. New exercises start with zero rest and empty notes. Replacement keeps the slot and omitted prescription fields. Existing duplicate rows are addressable individually. Adding the same exercise and prescription yields a no-op, while a different prescription or a replacement that would introduce a duplicate is refused. The editor refuses edits that would empty a non-rest day, add to a rest day, exceed 32 exercises in a day, or drop the last strength movement from a mixed day with cardio remaining. The targeted mutation rules limit new cardio placements to dedicated cardio days.

Success is HTTP 200 with:

```json
{
  "contract_version": 1,
  "outcome": "applied",
  "mutation_id": "<opaque receipt>",
  "mutation_version": 5,
  "reread_required": true
}
```

`outcome` can be `applied` or `no_op`. `Idempotency-Replayed` is `true` on a durable replay and `false` on the original response. The version in a replay describes its operation and can be older than the current plan. Refresh through the editor read after any success before using another row position. Undo restores the exact prior plan bytes and advances the version. It requires the same revision checks, an explicit latest reversible user-edit receipt, and a new key. Coach and system edits cannot be undone through the native editor.

- **400** `TRAINING_EDITOR_INVALID_REQUEST` or `TRAINING_EDITOR_INVALID_PRESCRIPTION` for malformed requests, unknown or unavailable catalog selections, or incompatible edits. **413** for a request body over 4 KiB.
- **404** `TRAINING_EDITOR_PLAN_NOT_FOUND` or `TRAINING_EDITOR_TARGET_NOT_FOUND` when the caller's plan or indicated occurrence is unavailable.
- **409** `TRAINING_EDITOR_STALE_PLAN` means reread and reselect. `TRAINING_EDITOR_IDEMPOTENCY_CONFLICT` means the key already belongs to a different intent. Other domain conflicts use `TRAINING_EDITOR_PLAN_UNAVAILABLE`, `TRAINING_EDITOR_ACTIVE_WORKOUT`, `TRAINING_EDITOR_EXERCISE_ALREADY_PRESENT`, `TRAINING_EDITOR_AMBIGUOUS_EXERCISE`, or `TRAINING_EDITOR_UNDO_UNAVAILABLE`. An in-flight workout must be completed or abandoned through its existing flow before a fresh edit.
- **503** `TRAINING_EDITOR_UNAVAILABLE` for transient persistence or catalog availability failures. Retry the same operation key and body.

Writes update the canonical plan and mutation journal together. Workout completion and performed-history rows are outside that transaction. The plan editor does not create sessions or count planned prescriptions as performed exercise.

## Catalog coverage

The bundled V1 catalog had 73 active IDs and 60 declared aliases. V2 keeps those IDs and raises coverage to 89 active IDs and 150 declared aliases. It adds flat dumbbell and incline barbell bench presses, machine chest and shoulder presses, a standing cable fly, a barbell overhead press, cable lateral raise and face pull, chest-supported machine row, seated leg curl and calf raise, seated hip abduction and adduction, barbell curls, dumbbell reverse lunges, and side planks. The five existing cardio identities stay in place. The live catalog still validates equipment and cardio context and supplies the generator's closed choice set. Already-signed V1 browser drafts can be saved under the existing signature and live compatibility checks.

Catalog coverage remains bounded. Gaps include Zercher squats, landmine press, and dedicated treadmill, elliptical, and rowing ergometer identities. The implemented missing-exercise path is a reviewed catalog addition with a new stable ID, compatible equipment and movement metadata, declared aliases, and a version bump. The editor shows an empty result for unlisted movements and rejects an unrecognized ID. Self-service custom entries and new cardio modalities require separate product and identity decisions.
