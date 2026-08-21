# Training generator — preference contract, capability matrix, and output reliability

Sprint 11 PR2 owns **whether** AxisAI will attempt to generate a weekly training
plan. Sprint 11 PR3 owns **whether provider output becomes a canonical plan**.
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
- Exercise `isim` is an untrusted bounded string. There are no canonical IDs.

`POST /training-plan/save` accepts the generate `program` array (what both web
clients persist) or the full object. Missing `haftalik_ozet` on save is derived
by AxisAI; generate still requires it.

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

Cannot be proven without PR4 catalog authority (documented gaps):

- Exercise identity / aliases / substitutions.
- Equipment compatibility of named lifts.
- Powerlifting SBD/%1RM first-class fields.
- Calisthenics bodyweight-only names.
- Injury rejection (warn-only annotation remains).

## Save-time re-validation

`POST /training-plan/save` runs `validate_plan_for_save` **before** delete+insert.

There is no durable generation-request identity. Save therefore re-validates
the 7-day structural contract plus the product training-day allow-list
(`3`–`6`). It cannot bind a payload to the original style/cardio command
without a later generation token. Client mutation of empty sessions, 1-day or
7-day weeks, wrong weekday counts, and handcrafted `{v:1}` objects fail closed
and leave the current row untouched.

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

- No exercise catalog / identity resolver (PR4).
- No provider or model change.
- No fake fallback plans.
- No mobile generator.
- No Adaptive Coaching undo of generate.
- No second training-plan authority.

## Future PR ownership

| PR | Owns |
| --- | --- |
| PR4 | Exercise identity: catalog / constrained names, substitutions, equipment truth |
| Later | Generation identity / signed save token, typed replace on save, generate idempotency, mobile generate contract |

## Tests

- `tests/test_sprint11_training_preference_contract.py` — PR2 request contract.
- `tests/test_sprint11_training_generation_output.py` — PR3 parser, truncation,
  repair budget, semantics, save safety, call-count upper bound.
No live AI is required.
