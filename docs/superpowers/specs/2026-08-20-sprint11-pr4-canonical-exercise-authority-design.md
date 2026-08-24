# Sprint 11 PR4 — Canonical Exercise Authority & Resolution

Date: 2026-08-20  
Status: approved design; implementation pending  
Repository: `yusufbesirarslan/fitness-coach`  
Base: `origin/main` `95fb056` (`feat(training): harden generated plan output validation (#223)`)

## 1. Objective

PR4 makes AxisAI, rather than the model or browser, authoritative for exercise
identity. A provider may propose an exercise name, but a plan is eligible for
generation success or persistence only after every exercise has resolved to a
stable product-owned ID and passed deterministic equipment checks.

The resulting flow is:

```text
PR2-accepted request
→ PR3 parse/structure/semantic validation
→ canonical exercise resolution
→ equipment compatibility validation
→ canonical plan candidate + signed save context
→ save-time signature verification and re-resolution
→ delete old plan only after every check passes
→ persist canonical plan
```

PR3's provider budget remains unchanged: at most two generation-layer
completions and at most one repair, with repair limited to parse/truncation.
Exercise failures are local deterministic failures and never invoke a provider.

## 2. Repository findings and authority decision

`origin/main` contains no canonical exercise database model or stable exercise
ID. `TrainingPlan.plan_data`, `WorkoutLog.exercise_name`, workout-session
fingerprints, training history, and Adaptive Coaching mutations are name-based.
The six-entry `training_generation/exercise_knowledge_base.py` is unused and
lacks canonical names, aliases, equipment truth, and sufficient product
coverage. It is not a viable authority.

PR1's unmerged discovery report is referenced by the merged PR2 and PR3 final
reports, but its branch and commit are no longer present on the remote. PR2 and
PR3 record the relevant PR1 findings and are the available evidence base.

PR4 will introduce one code-owned, version-controlled catalog as the canonical
exercise authority. A database table was rejected because these entries are
global product data, are deployed with application code, and do not require
runtime administration. A static catalog avoids migration risk, gives identical
IDs in every environment, and makes whole-plan resolution query-free.

The old `EXERCISE_KB` will be removed or converted to consume the new catalog;
it must not remain a second exercise authority.

## 3. Catalog contract

The curated catalog will live under the existing training assets/domain and be
loaded through one exercise-catalog module. Each entry contains only metadata
needed by current product behavior:

```json
{
  "exercise_id": "ex_barbell_back_squat",
  "canonical_name": "Barbell Back Squat",
  "aliases": ["Back Squat", "Barbell Squat"],
  "equipment": ["barbell", "rack"],
  "movement": "squat",
  "primary_region": "lower_body",
  "active": true
}
```

IDs are explicit opaque/domain-safe tokens and are never derived at runtime
from the display name. Renaming `canonical_name` therefore does not change plan
identity. IDs, canonical names, normalized aliases, metadata vocabulary, and
active state are validated when the catalog is loaded.

Catalog coverage is bounded to officially supported PR2 combinations and
current product fixtures: General Fitness, Bodybuilding, Powerlifting,
Calisthenics, Functional, and supported cardio modalities across gym,
bodyweight-home, and dumbbell-plus-band minimal contexts. It covers the core
compound, isolation, bodyweight, mobility/core, and cardio movements already
used by AxisAI. The final report will give exact counts and coverage groups.

No catalog entry is created from provider or client output. Catalog updates are
reviewed code changes. There is no runtime seeding, external catalog fetch, user
content, or private data.

## 4. Equipment vocabulary and compatibility

Catalog equipment uses a closed product vocabulary such as `bodyweight`,
`dumbbell`, `resistance_band`, `barbell`, `bench`, `rack`, `cable`, `machine`,
`pull_up_bar`, `kettlebell`, `cardio_machine`, and modality-specific equipment
where current cardio support requires it. The exact vocabulary is validated by
the catalog loader and documented in `docs/TRAINING_GENERATOR.md`.

The existing PR2 request contexts remain authoritative:

- `spor_salonu`: full supported gym catalog.
- `ev`: bodyweight movements, matching the current UI label.
- `minimal`: bodyweight, dumbbell, and resistance-band movements, matching the
  current UI label.

Cardio modality compatibility is explicit and separate where the PR2 cardio
choice establishes availability independently of strength-equipment context.
Compatibility reads catalog metadata and the accepted request; it never infers
equipment from an exercise name.

## 5. Normalization, aliases, and resolution

One pure normalization function performs Unicode NFKC normalization, trimming,
case folding, repeated-whitespace collapse, and conservative normalization of
hyphen variants. It does not delete words, stem tokens, or collapse distinct
movements.

Resolution order is deterministic:

1. exact active canonical ID, when an ID is supplied;
2. exact normalized canonical name;
3. exact normalized declared alias;
4. unresolved.

If a supplied ID is unknown or inactive, resolution fails even when the
supplied name happens to be valid. If a valid ID is paired with a tampered
name, the server replaces the name with catalog display data. The browser and
provider cannot override names, aliases, equipment, movement, or active state.

The loader rejects normalized canonical-name or alias collisions. The resolver
also represents ambiguity explicitly and fails closed, so a bad catalog or test
fixture cannot authorize persistence. There is no Levenshtein, nearest-neighbor,
embedding, or LLM fallback.

## 6. Generated-plan contract and prompt strategy

The provider continues to emit the PR3 exercise shape (`isim`, `set`, `tekrar`,
`dinlenme`, `not`). Provider-authored IDs would look authoritative while still
being hallucinations, so they are neither requested nor trusted.

The generation prompt receives the compatible canonical display vocabulary,
filtered by the accepted equipment/cardio context. This reduces deterministic
resolution failures while keeping server-side resolution authoritative. The
vocabulary is bounded and deduplicated; token-size tests will prevent accidental
prompt expansion.

After PR3 validation, the resolver returns the canonical exercise shape:

```json
{
  "exercise_id": "ex_barbell_back_squat",
  "isim": "Barbell Back Squat",
  "set": 3,
  "tekrar": "5",
  "dinlenme": "180 sn",
  "not": ""
}
```

Provider and canonical schemas are distinct. Provider validation does not allow
the provider to smuggle `exercise_id` or authority metadata. Canonical
validation requires a valid server-resolved ID for successful generation and
new persistence.

Unknown, ambiguous, inactive, or equipment-incompatible exercises produce
typed generation-output failures. They are semantic/deterministic failures,
are not repair-eligible, and do not add provider calls.

## 7. Signed save context and save-time authority

PR3 save has no trusted copy of the accepted equipment request. Trusting a
browser-posted equipment label would make save-time compatibility decorative.
PR4 therefore returns a compact signed exercise-context token with a successful
canonical candidate. The token contains a version, authenticated user identity,
accepted equipment/cardio context, style where needed for catalog validation,
and catalog contract version. It contains no prompt, raw provider output,
injury text, or other private profile data.

The token uses the application's existing signing facilities and secret. It is
not a generated-plan database row, idempotency key, or Adaptive Coaching
lineage. It binds the server-owned context needed by PR4 without expanding into
the broader generation-identity work deferred after PR3.

Both existing web clients will retain this token from `POST /training-plan` and
send it with `POST /training-plan/save`. Mobile generation remains disconnected.

Before `TrainingPlan.query.delete()` runs, save will:

1. verify the token signature and bind it to `current_user.id`;
2. structurally validate the posted plan;
3. batch/deduplicate exercise references;
4. resolve every supplied ID/name against the current catalog;
5. overwrite client names with canonical display names;
6. validate active state and signed equipment/cardio compatibility;
7. run the existing PR3 semantic validation;
8. build the canonical persisted document.

Only then may the route delete and insert. Tokenless new saves, fake IDs,
unresolved name-only exercises, inactive identities, or incompatible exercises
fail with a typed 422 and leave the prior plan byte-for-byte intact. A valid ID
paired with a tampered name is persisted with the catalog's canonical name.

The persisted document records the verified non-secret exercise context and
catalog contract version alongside `program`. This lets later server-owned
mutations apply the same compatibility policy. Client-supplied context is never
persisted as authority.

## 8. Substitution and restriction policy

PR4 implements no automatic substitutions. The prompt's compatible vocabulary
should make the common path reliable; an incompatible, ambiguous, or unknown
movement fails closed. This avoids silently changing user intent and eliminates
recursive chains, provider retries, and hidden fallback behavior.

The existing free-text injury overlay remains warning-only. PR4 does not create
a medical contraindication engine or claim deterministic medical knowledge.
Only product-owned equipment constraints and any already-explicit canonical
exercise exclusion inputs may block an exercise. No diagnosis or medical
inference is added.

## 9. Legacy, Adaptive Coaching, and workout logging

Existing name-only `TrainingPlan` documents remain readable by all current
projections. PR4 performs no destructive backfill and fabricates no ID for an
ambiguous legacy name.

Adaptive Coaching remains outside deliberate regeneration and retains its
exactly-once journal and undo semantics. For a new canonical plan, add/replace
operations resolve replacements through the shared catalog, preserve canonical
IDs, and enforce the persisted exercise context. Targets may still be expressed
by name in the existing tool contract, but server matching uses canonical
identity when present. Legacy plans keep their current name-based mutation path
until the planned convergence work; this is explicitly compatibility behavior,
not a second authority.

`WorkoutLog` remains name-only in PR4. Adding and backfilling a nullable foreign
identity across AI Coach logging, workout completion, history, and analytics is
larger than the generator reliability boundary. PR4 documents this gap and
ensures the new stable IDs can be adopted without creating another namespace.
Workout-session fingerprints remain readable; new canonical names make their
inputs stable, while changing a plan exercise still intentionally changes the
fingerprint.

## 10. Performance and caching

The catalog and normalized name/alias indexes are loaded once and cached in
memory. Resolution deduplicates repeated IDs/names before lookup and performs
no database query per exercise. A representative multi-day plan therefore uses
zero catalog DB queries and linear in-memory work. Tests will pin bounded loader
calls and prevent an N+1 regression.

## 11. Errors and observability

PR4 extends the existing typed training-generation vocabulary with closed
categories for unresolved/ambiguous exercise, invalid/inactive identity, and
equipment incompatibility. Save-time variants remain non-retryable. Exact names
may appear in bounded internal test diagnostics but not user-facing errors or
logs.

Logs contain request ID, closed failure code, counts, and provider-call count.
They do not contain catalog dumps, plans, prompts, aliases, restrictions,
tokens, or raw provider/client text.

## 12. Migration and deployment

No Alembic migration is required. Stable IDs and verified context live inside
the existing `TrainingPlan.plan_data` JSON document, and the catalog is a
version-controlled application asset. Existing rows are not rewritten.

The migration graph and fresh/incremental DB checks still run to prove PR4 did
not introduce drift. Catalog deployment is deterministic: the same reviewed
asset ships to every environment, with no seed command or runtime provider call.

## 13. Test strategy

Implementation follows test-driven development. Tests cover:

- catalog schema, stable IDs, active state, canonical names, coverage, and
  alias uniqueness;
- canonical names, declared aliases, casing/whitespace/Unicode/hyphen
  normalization, unknown names, ambiguous fixtures, and display-name changes;
- gym/home/minimal/cardio compatibility using catalog metadata;
- mocked PR3-valid generation with canonicalization and typed fail-closed paths;
- provider prompt vocabulary and unchanged two-call/one-repair upper bounds;
- valid signed save, forged/wrong-user/missing context, fake ID, tampered name,
  name-only alias resolution, inactive identity, incompatible equipment, and
  validation-before-delete ordering;
- legacy plan reads, training history, workout sessions, and name-only logs;
- canonical Adaptive Coaching add/replace plus unchanged journal/undo semantics;
- architecture guards against fuzzy matching, dynamic catalog insertion,
  duplicate authorities, provider-authored IDs, validation after delete, and
  imports that couple generation to mutation history;
- zero catalog DB queries / bounded loader behavior;
- migration graph, focused Sprint 11 and adjacent suites, full non-load suite,
  compile, and `git diff --check`.

## 14. Documentation and review

`docs/TRAINING_GENERATOR.md` becomes the canonical exercise-domain document as
well as the generator authority; no competing architecture document is added.
`docs/ADAPTIVE_COACHING.md` records the canonical-plan integration and legacy
boundary. The final report required by the task is written to:

`docs/superpowers/specs/2026-08-20-sprint11-pr4-canonical-exercise-authority-final-report.md`

Before the final verdict, an independent review classifies findings as P0/P1/P2
and focuses on duplicate authorities, alias ambiguity, unsafe normalization,
equipment bypass, fake IDs, save ordering, N+1 behavior, provider-call drift,
legacy breakage, and Adaptive Coaching scope. Confirmed P0/P1 findings must be
fixed. The final verdict is exactly `READY TO SHIP`, `READY WITH CONDITIONS`, or
`NOT READY`.

## 15. Explicit non-goals

- No PR5/mobile implementation.
- No public exercise search endpoint.
- No fuzzy persistence matching.
- No dynamic or user-owned global catalog entries.
- No automatic exercise substitution.
- No medical injury inference.
- No WorkoutLog/history schema migration or backfill.
- No provider/model selection change.
- No extra provider retry or changed repair eligibility.
- No merge, push, pull request, deployment, or production flag change.

## 16. Acceptance summary

PR4 is complete when one reviewed catalog owns exercise identity, every new
generated/saved exercise has a stable ID and canonical name, aliases resolve
only by explicit deterministic rules, unknown and incompatible exercises fail
closed, signed context makes save-time equipment checks authoritative, invalid
save cannot delete the current plan, new canonical Adaptive Coaching mutations
cannot introduce arbitrary exercises, legacy reads remain intact, provider-call
bounds are unchanged, validation is green, and independent review reports
P0 = 0 and P1 = 0.
