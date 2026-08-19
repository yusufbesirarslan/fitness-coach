# Training generator — preference contract & capability matrix

Sprint 11 PR2. This is the canonical document for **whether AxisAI will attempt
to generate a weekly training plan** for a given request. It is not the Adaptive
Planning layer (`docs/TRAINING_PLANNING.md`) and it is not Adaptive Coaching
mutation/undo (`docs/ADAPTIVE_COACHING.md`).

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
| `TRAINING_PLAN_GENERATION_INVALID` | 500 | Provider JSON unusable after the compact retry | yes (user click) |
| `TRAINING_PLAN_GENERATION_UNAVAILABLE` | 500 | Unexpected / provider-path failure | yes |

Internal reasons (`CROSSFIT_SCHEMA_UNSUPPORTED`,
`POWERLIFTING_REQUIRES_GYM_EQUIPMENT`, …) are logged, not returned.

## Retry semantics

- Contract failures happen **before** `_heavy_chat`. Zero provider calls, zero
  compact retries.
- After a **supported** request, malformed JSON / `PlanValidationError` still
  gets one compact retry (`max_tokens` 4000 → 7000). That is provider-output
  repair, not a second attempt at an impossible request.
- `ai_recovery` transient retries on `_heavy_chat` are unchanged.
- Models and providers are unchanged.

## Provider-call boundary

Invalid, unsupported, and conflicting requests invoke `chat_fn` zero times.
Capability evaluation is the authority; frontend disabling is optional UX.

## Observability

```
[TRAINING] generation_rejected code=... reason=... provider_invoked=0 request_id=...
[TRAINING] generation_started style=... days=... duration=... equipment=... focus=... cardio_type=... cardio_days=... provider_invoked=1
```

Do not log raw prompts, provider bodies, tokens, secrets, injuries, or other
profile PII.

## Non-goals (this PR)

- No exercise catalog / identity resolver (PR4).
- No structured-output / semantic plan validation (PR3).
- No provider or model change.
- No fake fallback plans.
- No mobile generator.
- No Adaptive Coaching undo of generate. Save remains a lineage reset.
- No second training-plan authority.

## Future PR ownership

| PR | Owns |
| --- | --- |
| PR3 | Provider output reliability: structured JSON, bounded repair of parse failures, stop retrying unrepairable day-count misses as “write less” |
| PR4 | Exercise identity: catalog / constrained names, substitutions |
| Later | Typed replace on save, generate idempotency, mobile generate contract |

## Tests

`tests/test_sprint11_training_preference_contract.py` is the contract suite.
No live AI is required.
