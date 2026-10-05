# Training generator — preference contract, capability matrix, and output reliability

Sprint 11 PR2 owns **whether** AxisAI will attempt to generate a weekly training
plan. Sprint 11 PR3 owns **whether provider output becomes a canonical plan**.
Sprint 11 PR4 owns **what an exercise IS** — a server-owned catalog is the single
authority on exercise identity, at both plan-write doors.
A plan is valid because AxisAI validated it, not because the model returned JSON.

This is not the Adaptive Planning layer (`docs/TRAINING_PLANNING.md`) and it is
not Adaptive Coaching mutation/undo (`docs/ADAPTIVE_COACHING.md`).

`POST /training-plan` is the only generation entrypoint. Mobile Create Plan is
not connected and is out of scope.

## Accepted preference fields

The HTTP body is parsed by `parse_canonical_preferences` in
`app/services/training_generation/preference_contract.py`. A missing or empty
object uses documented defaults. A non-object body (`[]`, string, number,
boolean) is **rejected** as `INVALID_PAYLOAD`. Present-but-unknown values are
**rejected** — they are never clamped, coerced (including JSON floats such as
`6.9` → `6`), or mapped to General.

| Field | Canonical values | Default |
| --- | --- | --- |
| `antrenman_tarzi` | `genel`, `bodybuilding`, `powerlifting`, `calisthenics`, `crossfit`, `fonksiyonel` | `genel` |
| `ekipman` | `spor_salonu`, `ev`, `minimal` | `spor_salonu` |
| `gun_sayisi` | `3`, `4`, `5`, `6` | `3` |
| `sure` | `30`, `45`, `60`, `90` | `45` |
| `odak` | `tum_vucut`, `ust_vucut`, `sirt`, `alt_vucut`, `core` | `tum_vucut` |
| `odak_hedef` | `genel`, `guc`, `kondisyon`, `kas_kutlesi`, `yag_yakimi`, `esneklik` | `genel` |
| `kardiyo_tipi` | `yok`, `kosu`, `bisiklet`, `yuzme`, `ip_atlama`, `yuruyus`, `karisik` | `yok` |
| `kardiyo_gun` | `0`–`6` | `0` |
| `kardiyo_sure` | `15`, `20`, `30`, `45` | `20` |
| `kardiyo_yogunluk` | `dusuk`, `orta`, `yuksek`, `karisik` | `orta` |
| `injuries` | free text (not a capability dimension) | stored metadata, else `""` |

Declared style aliases (only these): `general` / `general_fitness` → `genel`;
`functional` → `fonksiyonel`. Unknown styles fail. They never become General.

## Style vocabulary

UI token → style-rules / few-shot key:

| UI | Canonical style key |
| --- | --- |
| `genel` | `general_fitness` |
| `bodybuilding` | `bodybuilding` |
| `powerlifting` | `powerlifting` |
| `calisthenics` | `calisthenics` |
| `crossfit` | `crossfit` |
| `fonksiyonel` | `functional` |

`canonical_style()` raises on unknown input. `build_program_context` does not
fall back to `general_fitness` rules.

## `odak_hedef` decision

**Wired, not removed.** The field is part of the canonical request.

- Profile `goal` (`UserSession.goal` / `User.goal`) is **body/composition context**.
- `odak_hedef` is the **training-program emphasis**. Directives come from
  `app/services/training_assets/rules/goals.json`.
- Both appear in the generation prompt. The model is not given the profile goal
  as a substitute for the selected focus.

## Capability matrix

Evaluated by `evaluate_capability` after a successful parse. Provider-independent.

| Style | Gym | Home `ev` | Minimal | Notes |
| --- | --- | --- | --- | --- |
| General | supported | supported | supported | 3–6 days if the week fits |
| Bodybuilding | supported | supported | supported | hypertrophy week is representable |
| Powerlifting | supported | **unsupported** | **unsupported** | needs gym barbell/rack/bench |
| Calisthenics | supported | supported | supported | bodyweight week is representable |
| Functional | supported | supported | supported | movement-pattern week is representable |
| CrossFit | **unsupported** | **unsupported** | **unsupported** | 7-day schema cannot express WOD / mixed-modality structure |

A smaller supported matrix is intentional. Truthful support beats advertised
support.

## Cardio / day compatibility

`tip` is one of `antrenman` | `dinlenme` | `kardiyo`. Those are mutually
exclusive day types in a 7-day plan.

- `kardiyo_tipi=yok` and `kardiyo_gun>0` → **conflicting**
  (`CARDIO_DAYS_WITHOUT_TYPE`). The days are not silently zeroed.
- `gun_sayisi + dedicated_cardio_days > 7` → **conflicting**
  (`WEEK_ALLOCATION_EXCEEDS_SEVEN_DAYS`).
- Dedicated cardio days are `kardiyo_gun` only when `kardiyo_tipi != yok`.
- Plan v2 currently omits cardio-day chips and therefore sends `kardiyo_gun=0`.
  That is “no dedicated cardio days”, which is representable.

## Typed errors

`POST /training-plan` contract failures return:

```json
{
  "error": "<user-safe message>",
  "code": "TRAINING_PLAN_<CATEGORY>",
  "retryable": false
}
```

| Code | HTTP | Meaning | Retryable |
| --- | --- | --- | --- |
| `TRAINING_PLAN_NO_SESSION` | 400 | No `UserSession` yet | no |
| `TRAINING_PLAN_INVALID_PREFERENCE` | 422 | Malformed / unknown field | no |
| `TRAINING_PLAN_UNSUPPORTED_CONFIGURATION` | 422 | Recognized but not representable | no |
| `TRAINING_PLAN_CONFLICTING_PREFERENCES` | 422 | Logically impossible week | no |
| `TRAINING_PLAN_GENERATION_PARSE_FAILED` | 500 | Output was not one JSON object after the allowed repair | yes (user click) |
| `TRAINING_PLAN_GENERATION_TRUNCATED` | 500 | Provider finish reason showed truncation and repair failed | yes |
| `TRAINING_PLAN_GENERATION_SCHEMA_INVALID` | 500 | Parsed JSON violated the structural contract | yes |
| `TRAINING_PLAN_GENERATION_SEMANTICALLY_INVALID` | 500 | Structurally valid week failed the accepted PR2 request | yes |
| `TRAINING_PLAN_GENERATION_UNAVAILABLE` | 500 | Provider/upstream unavailable | yes |
| `TRAINING_PLAN_SAVE_INVALID` | 422 | Save payload failed canonical re-validation | no |

PR4 adds six more exercise-identity codes; they are tabulated with the rules
they enforce under "Exercise identity — the canonical catalog" → "Typed
failures". This table is not the complete list on its own.

Internal reasons (`CROSSFIT_SCHEMA_UNSUPPORTED`,
`POWERLIFTING_REQUIRES_GYM_EQUIPMENT`, …) are logged, not returned.
Raw provider text is never returned.

## Generated-plan schema

Canonical provider object (PR B — the provider chooses identity, the catalog
names it):

```json
{
  "program": [
    {
      "gun": "Pazartesi",
      "tip": "antrenman",
      "odak": "Full Body",
      "sure_dk": 45,
      "tahmini_kalori": 320,
      "egzersizler": [
        {"exercise_id": "ex_goblet_squat", "set": 3, "tekrar": "8-12", "dinlenme": "90 sn", "not": ""}
      ]
    }
  ],
  "haftalik_ozet": {
    "toplam_antrenman_gun": 3,
    "toplam_tahmini_kalori": 1400,
    "yogunluk_skoru": 7,
    "denge_skoru": 8,
    "uygunluk_skoru": 8
  }
}
```

- Exactly seven unique canonical Turkish weekdays.
- `tip` ∈ {antrenman, dinlenme, kardiyo}.
- Closed keys. Unknown fields fail.
- Numeric fields are integers in bounds; strings such as `"45 dk"` fail.
- A *provider* exercise is exactly `exercise_id` / `set` / `tekrar` /
  `dinlenme` / `not` (`PROVIDER_EXERCISE_KEYS`). `exercise_id` is required and
  `isim` is an **unknown key**: the provider chooses an ID from the request's
  closed choice set and never writes a display name. Structure checks only that
  the ID is a bounded string; generation canonicalization then validates it
  against the catalog and that set and writes the catalog's canonical `isim`.
  The persisted/served exercise shape is unchanged (`isim` + `exercise_id` +
  prescription). See "Exercise identity — the canonical catalog".

`POST /training-plan/save` accepts the generate `program` array (what both web
clients persist) or the full object. Missing `haftalik_ozet` on save is derived
by AxisAI; generate still requires it.

## Exercise identity — the canonical catalog

Sprint 11 PR4. Before it, an exercise was whatever string the provider wrote.
Now the **server-owned catalog is the single authority on what an exercise is**,
and the plan document records that answer.

### Ownership

`app/services/exercise_catalog.py` owns identity, vocabulary and compatibility.
The data is one reviewed, version-controlled asset:
`app/services/training_assets/exercises.json`. It is **not a database table** —
there is no exercise table, no `exercise_id` column anywhere in the schema, and
PR4 ships **no Alembic migration** (`tests/test_migration_graph.py` pins the
head and the file count). Changing the catalog is a code review, not a data fix.

`load_exercise_catalog()` is `lru_cache`d and validates the whole asset on load;
a malformed asset raises `CatalogConfigurationError` at first use rather than
degrading. Everything it returns is frozen: `ExerciseDefinition` is a frozen
dataclass, `equipment` is a `frozenset`, and both indexes are `MappingProxyType`.

### Stable ID format and catalog size

An exercise ID matches `^ex_[a-z0-9_]+$` (`ID_PATTERN`) and is the *only* stable
identity. `canonical_name` is presentation: renaming a display name must not
change the ID, and does not.

Current catalog: **version 1, 73 exercises** (all active), carrying 60 declared
aliases, so **133 unique normalized lookup keys**. 68 are resistance entries and
5 are cardio modalities. Compatible-entry counts per accepted context:

| Context | Entries | Notes |
| --- | --- | --- |
| `spor_salonu` | 68 | every resistance entry |
| `minimal` | 40 | bodyweight + dumbbell + band |
| `ev` | 20 | bodyweight only |
| cardio `kosu` / `yuruyus` / `ip_atlama` / `bisiklet` / `yuzme` | 1 each | one modality each |
| cardio `karisik` | 5 | all five modalities |

Cardio compatibility is **independent of** the equipment context: a home user
who runs outdoors is a real product case, so cardio is gated by the accepted
`cardio_type` only (see "No substitution, and the cardio carve-out").

### Aliases and normalization

`normalize_exercise_lookup` canonicalizes **safe spelling variants only**: NFKC
normalization, unicode dash characters folded to `-`, case-folded, whitespace
collapsed. It never stems, never deletes tokens, never scores similarity.
"Bench Press" and "bench  press" are the same lookup key; "Incline Bench Press"
is a different exercise and stays one.

Aliases are declared per entry and are part of the reviewed asset. The loader
rejects a catalog in which two entries' names or aliases normalize to the same
key, so an ambiguous lookup cannot be introduced by data edit — ambiguity is a
review-time failure, not a runtime coin flip.

There is **no fuzzy matching anywhere**. `tests/test_sprint11_exercise_authority.py`
scans the executable text of every module in `exercise_catalog.py`,
`training_generation/` and `plan_mutation/` — the file set derived from the
package directories, so a new module cannot escape it — for `levenshtein`,
`fuzzy`, `difflib` and `rapidfuzz`.

### Resolver hierarchy

`resolve_exercise(exercise_id=None, name=None, catalog=None)`, in order:

1. **A supplied `exercise_id` wins outright.** It must match `ID_PATTERN`
   (`ExerciseIdentityInvalid`), must exist in the catalog
   (`ExerciseIdentityInvalid`), and must be active (`ExerciseInactive`). A valid
   ID beside a tampered display name resolves to the ID's entry and the
   catalog's name is what persists.
2. **Otherwise the name is looked up exactly**, after normalization. Missing,
   blank or non-string → `ExerciseUnresolved`. No match → `ExerciseUnresolved`.
   More than one match → `ExerciseAmbiguous` (fails closed; never "first hit").
   Inactive entry → `ExerciseInactive`.

A *name* shaped like an ID never becomes identity. `exercise_resolution.py`
rejects it as `GenerationExerciseIdentityInvalidError` rather than letting it
fall through as an unrelated "unknown exercise".

Nothing above touches the database. A representative full week (27 exercise
references, repeated names, alias spellings, a cardio day) resolves with
**zero SQL statements executed** — proven by an engine event listener, not a
mock (`test_representative_plan_resolution_executes_no_sql`). Within one
canonicalization pass the catalog is loaded once and each distinct normalized
name is looked up once, not once per occurrence.

### Equipment vocabulary and the context map

Closed vocabularies, owned by the catalog module:

- `EQUIPMENT_VOCABULARY` (16): `barbell`, `bench`, `bodyweight`, `cable`,
  `cardio_machine`, `dumbbell`, `kettlebell`, `machine`, `outdoor_running`,
  `outdoor_walking`, `pool`, `pull_up_bar`, `rack`, `resistance_band`, `rope`,
  `stationary_bicycle`.
- `MOVEMENT_VOCABULARY` (16): `anti_extension`, `anti_rotation`, `calf_raise`,
  `carry`, `cardio`, `core_dynamic`, `curl`, `dip`, `hinge`, `horizontal_pull`,
  `horizontal_push`, `lunge`, `mobility`, `squat`, `vertical_pull`,
  `vertical_push`. `CARDIO_MOVEMENT = "cardio"` is the one modality value.
- `REGION_VOCABULARY` (10): `arms`, `back`, `calves`, `cardio`, `chest`, `core`,
  `full_body`, `lower_body`, `mobility`, `shoulders`.

The UI equipment token maps to a set of catalog equipment:

| `ekipman` | Allowed catalog equipment |
| --- | --- |
| `ev` | `bodyweight` |
| `minimal` | `bodyweight`, `dumbbell`, `resistance_band` |
| `spor_salonu` | the whole `EQUIPMENT_VOCABULARY` |

| `kardiyo_tipi` | Allowed catalog equipment |
| --- | --- |
| `kosu` | `outdoor_running` |
| `yuruyus` | `outdoor_walking` |
| `ip_atlama` | `rope` |
| `bisiklet` | `stationary_bicycle` |
| `yuzme` | `pool` |
| `karisik` | all five of the above |

An entry is compatible when **every** item in its `equipment` set is allowed —
a multi-item entry needs all of them, not one. An unknown equipment or cardio
token is not compatible with anything; it fails closed rather than widening.

> **Do not confuse `MOVEMENT_VOCABULARY` with `REQUIRED_MOVEMENT_COVERAGE`**
> (`training_generation/movement_coverage.py`). The latter is prompt-directive
> prose interpolated into a sentence for the provider; it is never resolved,
> compared to a catalog value, or persisted. The two lists overlap on six
> strings and deliberately differ on two (`core_anti_extension` /
> `core_anti_rotation` versus `anti_extension` / `anti_rotation`). Renaming
> either to match the other changes the text of the generation prompt.

### The provider chooses identity; the server validates and names it

PR B (training generation reliability). Before it the prompt carried a closed
vocabulary of display NAMES, the provider returned a free-form `isim`, and the
server tried to map that string back to the catalog. A translated, misspelled
or invented name passed structure and then failed resolution as a terminal
`EXERCISE_UNRESOLVED` (seen on a physical device during first-plan
onboarding). Identity no longer depends on any provider-written text.

1. **One closed choice set per request.** `exercise_choices.compatible_exercise_choices(context)`
   returns a frozen `CompatibleExerciseChoices`: every active,
   context-compatible catalog entry as an `(exercise_id, canonical_name)` pair,
   sorted by name — no aliases, no equipment metadata. `service.py` builds it
   ONCE and passes the same object to the prompt and to validation.
2. **Prompt (advisory).** `build_training_prompt(..., exercise_choices=...)`
   (required, non-empty) renders an `EXERCISE CHOICES` block of
   `- ex_… | Name` lines, tells the provider to copy `exercise_id` exactly, never
   translate, re-case, shorten or invent one, and never write `isim`; both the
   system prompt and the content-language rule carve `exercise_id` out of "all
   visible text is Turkish". The JSON FORMAT example uses an ID taken from the
   same set, so the example can never teach an ID this request will refuse.
3. **Structure.** `validate_generated_plan` →
   `validate_plan_structure(..., provider_identity=True)`: exact
   `PROVIDER_EXERCISE_KEYS`, `exercise_id` required and a bounded string,
   `isim` unknown. A name-only or name-bearing entry is `SCHEMA_INVALID` — a
   provider formatting miss, eligible for the one existing bounded repair turn
   (whose text now names the ID contract). Save keeps its own shapes
   (`allow_exercise_id=True`); the two flags are exclusive and both call sites
   are pinned by AST.
4. **Authority (server).** `exercise_resolution.canonicalize_generated_exercises(plan, choices)`
   validates each ID in a fixed order — `ID_PATTERN` shape → exists → active →
   member of THIS request's set — then re-checks equipment compatibility
   (defence in depth) and cardio placement, and rebuilds the entry field by
   field: `isim` and `exercise_id` from the catalog, `set`/`tekrar`/`dinlenme`/
   `not` from the provider. There is no name lookup on this path.

`canonicalize_plan_exercises` (exact normalized name/alias, or a declared ID)
remains the SAVE boundary's resolver, because a browser client may still post
a name-only plan. No fuzzy matching, translation alias or substitution was
added anywhere.

### Canonicalization on generate

`canonicalize_generated_exercises` runs **exactly once**, on the final accepted
candidate, strictly outside the parse/truncation/schema repair boundary in
`service.py`. Moving it inside would let an exercise-authority failure be
misclassified as repairable and re-sent to the provider. It never adds catalog
metadata (equipment / movement / region) to the plan — that stays server-side.

### Injury annotation (warn-only, after identity)

`annotate_injuries` runs **after** canonicalization. It requires every
entry to be an exact catalog `(exercise_id, canonical name)` pair — since PR B
a validated provider entry carries a raw, unchecked `exercise_id`, so the mere
presence of an ID is no longer proof — and checks all entries before it
annotates any. It then matches with the existing string overlay against the
catalog display name written into `isim`. It is warn-only: it prepends a note onto `not` and
never rejects, deletes, substitutes, or mutates sets/reps/load. A plan
entry that is not a canonical pair cannot reach the matcher, so raw provider
output is not warning authority. Save does not re-derive the overlay;
it re-validates the already-canonical annotated payload and preserves
`not`. Historical rows are not rewritten.

### No substitution, and the cardio carve-out

There is **no automatic substitution, ever**. An unresolvable, ambiguous,
inactive or context-incompatible reference fails the whole generation attempt
with a typed error. Quietly swapping in "the nearest thing we do have" would
hand the user a plan they did not ask for and could not tell apart from one
they did.

`check_placement` (public, in `exercise_resolution.py`, reused by the Adaptive
Coaching mutation boundary rather than copied) binds a cardio-movement entry to
a `kardiyo` day. It exists because cardio compatibility deliberately ignores
`equipment_context`: without the placement rule, an `ekipman="ev"` plan could
prescribe swimming inside a strength day and persist under `"ev"`, i.e. the
equipment gate would be bypassable by placement alone. It is one-directional on
purpose — forbidding a non-cardio exercise on a cardio day is a plan-quality
opinion, and this boundary only answers authority questions.

### Signed save context

The accepted `ExerciseContext` is server-owned truth derived at generation time.
Save happens later, over a separate call, and must re-check that truth before it
destroys the stored plan — but the context is not part of the plan document and
must never be re-declared by the caller, or "home workout" becomes a field the
browser fills in.

`training_generation/exercise_context_token.py` carries it: an opaque,
domain-separated `HMAC-SHA256` token (`~170` chars, hard-capped at 512) over
`{v, uid, eq, cardio, style, catalog}`, signed with the app `SECRET_KEY` under
the domain prefix `axisai.training.exercise_context` so a signature minted for
any other purpose can never be replayed here. Standard-library crypto only; no
expiry and no replay store; the module knows nothing about HTTP and never logs.

It is an **integrity device, not a capability grant**. It is bound to the exact
`user_id`, and everything it carries is still re-checked against the catalog on
arrival. Every rejection reason — bad signature, wrong user, unknown vocabulary,
catalog-version mismatch, malformed charset — raises the single
`ExerciseContextInvalid`, because distinguishing them is an oracle.

`resolve_save_exercise_context` is the one translation point into the save
boundary's typed `SaveContextInvalidError`.

### Typed failures

| Code | HTTP | Raised when (generation, PR B) | Retryable |
| --- | --- | --- | --- |
| `TRAINING_PLAN_GENERATION_EXERCISE_IDENTITY_INVALID` | 500 | chosen `exercise_id` is malformed (`malformed_id`) or names no catalog entry (`unknown_id`) | yes |
| `TRAINING_PLAN_GENERATION_EXERCISE_UNRESOLVED` | 500 | chosen `exercise_id` is a real but retired entry (`inactive_id`) | yes |
| `TRAINING_PLAN_GENERATION_EXERCISE_INCOMPATIBLE` | 500 | real active ID outside this request's choice set (`outside_choice_set`), outside the equipment context (`equipment`), or cardio on a non-cardio day (`cardio_placement`) | yes |
| `TRAINING_PLAN_GENERATION_EXERCISE_AMBIGUOUS` | 500 | unreachable from generation (ID lookup is exact); kept for the save resolver's name path | yes |
| `TRAINING_PLAN_SAVE_CONTEXT_INVALID` | 422 | the signed exercise context could not be trusted for this user | no |
| `TRAINING_PLAN_SAVE_EXERCISE_INVALID` | 422 | the saved plan names an exercise the catalog will not authorize | no |

None of the four generation codes is parse-repairable: a well-formed plan
outside the constrained vocabulary is a closed-authority failure, not a
malformed response.

`EXERCISE_UNRESOLVED` is no longer reachable from a translated, misspelled or
invented NAME: generation never resolves a name. A provider that still writes
a name produces `SCHEMA_INVALID` after the one bounded repair, never
`UNRESOLVED`.

The table above describes the browser `POST /training-plan` response. The
native `POST /api/v1/training/plans` command currently stores every
exercise-authority failure as a terminal `FAILED` operation and returns HTTP 422 with
`retryable=false`; it refunds reserved quota and creates no TrainingPlan.
Replaying the same idempotency key returns that stored failure without another
model call. A new key starts a fresh generation attempt, which may independently
succeed or fail. The browser and native status/retryability difference is an
explicit follow-up decision, not reconciled by this reliability change.

Since LP-14 PR-B2, every native `FAILED` answer is `retryable=false`, because
the same key can only replay it. That covers `GENERATION_UNAVAILABLE` (503) and
`PARSE_FAILED`/`TRUNCATED` (500) too. Native `retryable` means "the same key can
make progress"; it does not mean "asking the provider again may help". The
public code carries that second fact: `TRAINING_PLAN_GENERATION_UNAVAILABLE`
tells the client to start a new request. The browser `POST /training-plan`
table above is unchanged. It has no idempotency key, so a browser retry already
is a new attempt.

On any generation exercise-authority failure (identity invalid, unresolved,
incompatible), `exercise_resolution_failed` logs only the public code, the
fixed resolution category from the table above, request ID, catalog version,
coarse equipment and cardio context, and provider completion count. It does not
log the chosen ID, any generated name, prompt, injuries, model response,
credentials, or idempotency key. Categories are a closed set validated at
construction; there is no per-ID metric.

### Provider last-good cache (PR B)

`ai._heavy_complete` writes the raw provider text to the last-good cache on
every successful provider call — before any validation — and serves it when
both providers fail. Generation treats a cached answer exactly like a live
one: it goes through the same `_parse_and_validate` and
`canonicalize_generated_exercises` against the CURRENT catalog and the CURRENT
request's choice set. No cache versioning was needed: a pre-PR B name-only
payload fails the provider schema (`isim` unknown, `exercise_id` missing), an
unknown/retired/incompatible cached ID fails identity validation, and the cache
key is a hash of the full prompt, which now embeds the closed ID set, so a
pre-PR B prompt and a different choice set can never address the same entry.
Caching raw output before validation is pre-existing behaviour and unchanged.

At the save boundary the *five* distinguishable reasons (unknown, ambiguous,
inactive, fake ID, equipment-incompatible) deliberately collapse into one
`SAVE_EXERCISE_INVALID`. Telling a client which one it hit turns the save
endpoint into a catalog oracle it was never given.
`SAVE_CONTEXT_INVALID` stays separate because the honest recovery differs:
"generate the plan again", not "edit the plan".

### The legacy logging gap

`WorkoutLog.exercise_name` is `db.String(120), nullable=False`. **Workout
history identifies an exercise by name, not by `exercise_id`.** PR4 does not
migrate it and adds no backfill.

Two consequences, stated plainly because they are real:

- Historical logs **cannot be joined to catalog identity**. A row logged as
  "Back Squat" is a string; it is not a reference to `ex_barbell_back_squat`.
- **Renaming a catalog entry does not retroactively rename what is already
  logged.** History is a record of what happened, not a claim about what was
  authorized — and filtering it through the catalog would silently delete part
  of a user's past.

The same holds for legacy *plans*: a pre-PR4 row is a bare JSON list (or a
`{"program": …}` object) with no `exercise_id` and no `exercise_context`. Those
keep working read-only through every reader — presenter, workout state, workout
session fingerprinting, Adaptive Coaching context, training history — and are
**never silently upgraded**. An ambiguous legacy name is refused
(`AmbiguousExerciseTarget`), never resolved by position and never given a
fabricated ID.

The session plan fingerprint hashes ordered `isim` values only, so saving the
same week through the PR4 boundary does not change it and does not orphan a
running session.

`exercise_id` is server-side authority and does not reach clients: the bounded
public day projection (`workout_state/serialization.py`) emits exactly
`isim` / `set` / `tekrar` / `dinlenme` / `not`.

### Deployment

No migration, no table, no backfill, no flag. The catalog ships as code. The
rollback is reverting the commit; nothing in the database needs undoing.

## Parser / extractor

`extract_plan_object` in `app/services/training_generation/extractor.py`.

- One optional outer ` ```json ` fence, because the current text stack still emits one.
- Then exactly one JSON object via `JSONDecoder.raw_decode`. Leftover values fail.
- Prose around the object fails. First-`{` / last-`}` slicing is gone.
- Wrong top-level type fails.

Truncation is **not** inferred from every parse error. `finish_reason=length`
(OpenAI) and `stop_reason=max_tokens` (Bedrock) are preferred. Incomplete JSON
without that metadata is `PARSE_FAILED`, still repair-eligible. A provider
max-token stop on a closed-but-invalid object (for example a 3-day week that
happened to close) is still truncation-eligible for the one repair. A fully
valid week with truncation metadata is accepted.

## Repair eligibility and budget

Eligible: parse failure, definitive truncation.

Not eligible: schema invalid, semantic invalid, PR2 contract rejection,
provider unavailable.

Maximum **one** repair attempt per generation candidate. Repair output is fully
re-parsed and re-validated. A failed repair stops. Semantic misses are not
sent back with “keep it short”.

Repair prompt is the original contract plus a “return the complete JSON object”
suffix. Truncation repair uses `max_tokens=7000`; parse repair stays at 4000.

**Provider-call invariants** (`training_generation/plan_schema.py`, pinned by
`tests/test_sprint11_exercise_authority.py`):

| Constant | Value | Meaning |
| --- | --- | --- |
| `MAX_PROVIDER_COMPLETIONS` | `2` | hard ceiling on generation-layer completions: one primary plus **at most one** repair. `_CompletionBudget` enforces it and there are exactly two `budget.complete(...)` call sites. |
| `PRIMARY_MAX_TOKENS` | `4000` | primary completion, and parse repair |
| `REPAIR_MAX_TOKENS` | `7000` | truncation repair only |

Repair exists for **parse and truncation only**. A semantically invalid plan
never triggers one, and neither does an exercise-authority failure —
canonicalization runs strictly outside the repair boundary, so it can never be
caught by it or looped back into it.

## Provider fallback interaction

Generation-layer `chat_fn` invocations are capped at **2**.

Each `_heavy_complete` call may still:

1. Try Bedrock with `ai_recovery` (default 2 attempts on transient errors).
2. Fall back to OpenAI with the same recovery budget.
3. Serve last-good cache (no extra network call).

Models are unchanged (Bedrock Claude Sonnet 4.5 primary, OpenAI `gpt-4o-mini`
fallback). Typical success is 1 completion. Pathological transient+fallback+repair
is bounded; it is not an unbounded loop.

## Structural vs semantic validation

Structural (`response_validator.py`): shape, types, closed keys, weekday set,
non-empty training/cardio sessions, empty rest days, integer bounds.

Semantic (`semantic_validator.py`), against the accepted PR2 request:

- Exact training-day count and dedicated cardio-day count.
- Rest-day count = 7 − training − cardio.
- Training duration within 50%–200% of requested `sure`.
- Style session size: General ≥1, Bodybuilding ≥4, Powerlifting ≥3,
  Calisthenics ≥3, Functional ≥3.

Exercise identity, aliases and equipment compatibility of named lifts are
**settled by PR4's catalog**, in a separate pass after semantics — see
"Exercise identity — the canonical catalog".

Still not provable (documented gaps):

- Powerlifting SBD/%1RM first-class fields.
- Calisthenics bodyweight-only *programming* (the catalog constrains equipment,
  not whether the week is a coherent calisthenics progression).
- Injury rejection (warn-only annotation remains).

## Save-time re-validation

`POST /training-plan/save` runs `validate_plan_for_save` **before** delete+insert.

Order is the guarantee, and it is pinned by an AST guard: **verify the signed
exercise context → structure → semantics → catalog exercise resolution →
equipment compatibility → and only then `delete()`**. `/training-plan/save` is
the only destructive `TrainingPlan` path in the app, so anything that fails
leaves the user's current plan exactly as it was.

Save is bound to the accepted equipment context by the PR4
`exercise_context_token` — an HMAC-signed, user-bound token minted at generate
and required at save (see "Signed save context"). It is not optional: a save
without a verifiable token is refused with `SAVE_CONTEXT_INVALID` before
anything is read from the plan.

Save then **re-resolves every exercise against the catalog under the VERIFIED
context, never under anything the payload claims**. A submitted `exercise_id` is
a claim, not identity: it is re-resolved on every submission, so a retired or
renamed entry stops being savable the moment the product retires it. Both
shapes a client can honestly hold are accepted — the provider-style name-only
program, and the ID/name pairs canonicalization produced at generation time —
and both are re-validated from scratch (structure, then semantics, then catalog
identity and equipment compatibility).

What is persisted is the canonical document: `program`, the client's
`haftalik_ozet` when (and only when) it supplied one, and a **server-created
`exercise_context` block**. Scores are preserved, never fabricated; a save is
not a planning decision.

Client mutation of empty sessions, 1-day or 7-day weeks, wrong weekday counts,
and handcrafted `{v:1}` objects still fail closed and leave the current row
untouched.

Generate still does not enter Adaptive Coaching mutation/undo. Save remains a
lineage reset.

## Retry semantics

- Contract failures happen **before** `_heavy_complete`. Zero provider calls.
- Eligible parse/truncation: one internal repair.
- Schema/semantic invalid: no internal repair; user may click generate again.
- Provider unavailable / rate limit: user may retry later.
- Save validation failure: not retryable as a provider call.

## Observability

```
[TRAINING] generation_rejected code=... reason=... provider_invoked=0 request_id=...
[TRAINING] generation_started style=... days=... duration=... equipment=... focus=... cardio_type=... cardio_days=... provider_invoked=1
[TRAINING] parse_failed|truncated|schema_invalid|semantic_invalid|repair_attempted|repair_failed calls=...
[TRAINING] save_validation_rejected code=... request_id=...
```

Do not log raw prompts, provider bodies, complete plans, tokens, secrets,
injuries, or other profile PII.

## Non-goals

- No provider or model change.
- No fake fallback plans.
- No mobile generator.
- No Adaptive Coaching undo of generate.
- No second training-plan authority.

## Future PR ownership

| PR | Owns | Status |
| --- | --- | --- |
| PR4 | Exercise identity: the server-owned catalog, constrained provider vocabulary, exact resolution, equipment/cardio truth at both plan-write doors, and the HMAC-signed save context token | **shipped** |
| Later | Typed replace on save, generate idempotency, mobile generate contract, catalog-joined workout history | open |

PR4 explicitly did **not** add automatic substitution, a `WorkoutLog` →
catalog join, or a migration.

## Tests

- `tests/test_sprint11_training_preference_contract.py` — PR2 request contract.
- `tests/test_sprint11_training_generation_output.py` — PR3 parser, truncation,
  repair budget, semantics, save safety, call-count upper bound.
- `tests/test_sprint11_exercise_authority.py` — PR4 catalog shape, exact
  resolution, compatibility, prompt vocabulary, signed context token, the
  architecture guards (no legacy KB, no fuzzy path, no catalog persistence,
  zero SQL, save-before-delete ordering, provider budget) and legacy-plan
  compatibility.

No live AI is required.
