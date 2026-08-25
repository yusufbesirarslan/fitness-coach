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

Canonical object:

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
        {"isim": "Goblet Squat", "set": 3, "tekrar": "8-12", "dinlenme": "90 sn", "not": ""}
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
- Exercise `isim` in a *provider response* is an untrusted bounded string, and
  `exercise_id` is **not** an accepted generation key — the provider never
  authors identity. Server-side canonicalization then replaces `isim` with the
  catalog's canonical display name and writes the catalog-owned `exercise_id`;
  from that point identity is `exercise_id` and `isim` is presentation only.
  See "Exercise identity — the canonical catalog".

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

### The provider never authors identity

The prompt carries a **closed vocabulary of canonical display names**
(`canonical_exercise_vocabulary(context)` in `prompt_builder.py`) filtered to
the accepted context, sorted and deduplicated. It carries no aliases, no IDs and
no equipment metadata — it is a hint that narrows what the model is told it may
use, never an authority.

Structurally, `EXERCISE_KEYS` does not contain `exercise_id`.
`validate_plan_structure` takes a keyword-only `allow_exercise_id: bool = False`:

- the **generation** call site (`validate_generated_plan`) leaves it `False`, so
  a provider-authored ID is a schema violation;
- the **save** call site (`validate_plan_for_save`) passes `True`, where the key
  is optional *input* that catalog resolution re-checks from scratch — never an
  identity the caller gets to assert.

Both call sites are pinned by AST, and a third one fails the guard.

### Canonicalization on generate

`exercise_resolution.canonicalize_plan_exercises(plan, context)` runs **exactly
once**, on the final accepted candidate, strictly outside the parse/truncation
repair boundary in `service.py`. Moving it inside would let an
exercise-authority failure be misclassified as repairable and re-sent to the
provider.

For each entry it writes `exercise_id` and replaces `isim` with the catalog's
canonical display name; every prescription field (`set` / `tekrar` /
`dinlenme` / `not`) is carried through unchanged. It never adds catalog
metadata (equipment / movement / region) to the plan — that stays server-side.

### Injury annotation (warn-only, after identity)

`annotate_injuries` runs **after** canonicalization. It requires a
non-empty `exercise_id` (canonical identity as a gate) and then matches
with the existing string overlay against the catalog display name already
written into `isim`. It is warn-only: it prepends a note onto `not` and
never rejects, deletes, substitutes, or mutates sets/reps/load. A plan
entry without `exercise_id` cannot reach the matcher, so a raw provider
spelling is not warning authority. Save does not re-derive the overlay;
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

| Code | HTTP | Raised when | Retryable |
| --- | --- | --- | --- |
| `TRAINING_PLAN_GENERATION_EXERCISE_UNRESOLVED` | 500 | generated name matches no active catalog entry | yes |
| `TRAINING_PLAN_GENERATION_EXERCISE_AMBIGUOUS` | 500 | generated name matches more than one entry | yes |
| `TRAINING_PLAN_GENERATION_EXERCISE_IDENTITY_INVALID` | 500 | generated reference is not usable catalog identity (e.g. an ID-shaped name) | yes |
| `TRAINING_PLAN_GENERATION_EXERCISE_INCOMPATIBLE` | 500 | resolves, but outside the accepted equipment context, or cardio on a non-cardio day | yes |
| `TRAINING_PLAN_SAVE_CONTEXT_INVALID` | 422 | the signed exercise context could not be trusted for this user | no |
| `TRAINING_PLAN_SAVE_EXERCISE_INVALID` | 422 | the saved plan names an exercise the catalog will not authorize | no |

None of the four generation codes is parse-repairable: a well-formed plan
outside the constrained vocabulary is a closed-authority failure, not a
malformed response.

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
