# Sprint 11 PR3 — Training Generation Output Reliability — Final Report

Date: 2026-08-20
Status: final validation complete
Repo: `yusufbesirarslan/fitness-coach`

## Executive verdict

**READY TO SHIP.** After a PR2-accepted request, AxisAI no longer treats provider JSON as canonical. Output is extracted strictly, validated structurally, validated semantically against the accepted request, repaired at most once when parse/truncation-eligible, and re-validated before save. Semantic misses do not re-enter repair. Save persists the canonical validated value, and invalid plans cannot replace the current plan.

## Base / branch / HEAD

| | |
| --- | --- |
| Worktree | `C:\Users\yusuf\develop\fitness-coach\.worktrees\sprint11-pr3-training-generation-output-reliability` |
| Branch | `sprint11-pr3-training-generation-output-reliability` |
| Base | `origin/main` `5628e0d` (`feat(training): add canonical preference contract and capability matrix (#222)`) |
| Final implementation | Implementation and this report are committed together as the branch's sole commit ahead of base; exact SHA is recorded in the ship handoff. |
| PR2 | present on base |
| PR1 discovery | `docs/superpowers/specs/2026-08-19-sprint11-training-generator-reliability-discovery.md` on `sprint11-pr1-training-generator-reliability-discovery` (not merged; used as evidence) |

## PR2 prerequisites verified

- `preference_contract.py` + `capability.py` gate generate before `chat_fn`.
- Typed `{error, code, retryable}` already on `POST /training-plan`.
- CrossFit / home Powerlifting / overfull weeks invoke the provider zero times.
- `docs/TRAINING_GENERATOR.md` existed and is now the single generator authority (updated, not duplicated).
- Exercise catalog still absent. Adaptive Coaching generate-undo still absent.

## Previous output architecture

```
chat_fn string
→ fence strip + first `{` to last `}`
→ json.loads
→ validate_generated_plan (clamp, backfill weekdays, default reps/rest)
→ on JSONDecodeError | PlanValidationError: one compact retry (“keep it short”, 7000 tokens)
→ HTTP 500 TRAINING_PLAN_GENERATION_INVALID
→ client POST /training-plan/save {plan: program array} with no re-validation
→ DELETE all TrainingPlan rows, INSERT
```

Provider finish reasons were logged and discarded. Semantic day-count misses used the same retry as truncated JSON. Save trusted the browser.

## New generated-plan contract

Closed shape in `plan_schema.py`:

- `program[7]` with unique canonical `gun` values
- `tip` ∈ {antrenman, dinlenme, kardiyo}
- day keys: gun, tip, odak, sure_dk, tahmini_kalori, egzersizler
- exercise keys: isim, set, tekrar, dinlenme, not
- `haftalik_ozet` scores 1–10 integers (required on generate; derived on save)

Generate HTTP still returns `{program, haftalik_ozet, overall_score, score_label, injury_warnings, classification, …}`. Both web clients still persist `program`. Save accepts that array or the full object.

## Parser/extractor behavior

`extractor.extract_plan_object`:

- Accepts one outer markdown fence (existing provider constraint).
- `JSONDecoder.raw_decode` of the remainder. Leftover values → parse failure.
- Prose, multiple objects, arrays, empty payload → parse failure.
- No first-`{` / last-`}` best-effort slice.

## Truncation detection

Adapters record metadata without changing string-returning helpers:

- OpenAI `finish_reason == "length"`
- Bedrock `stop_reason == "max_tokens"`

`_heavy_complete` returns `ChatCompletion(text, truncated, finish_reason, provider, fallback_used)`. Incomplete JSON **with** that flag is `GENERATION_TRUNCATED`. Incomplete JSON **without** it is `GENERATION_PARSE_FAILED`. Valid complete JSON near the limit is accepted. Arbitrary malformed JSON is not classified as truncation.

## Repair eligibility

Eligible: `ParseFailedError`, `TruncatedError`.

Not eligible: schema, semantic, PR2 contract, provider unavailable.

## Repair budget

Maximum one repair. Repair output is fully extracted + structurally + semantically re-validated. Failed repair stops. The old “keep it short” suffix is gone.

- Parse repair: `max_tokens=4000`
- Truncation repair: `max_tokens=7000`

## Provider fallback interaction

Generation layer: at most **2** `chat_fn` invocations (`MAX_PROVIDER_COMPLETIONS`).

Each `_heavy_complete` may still internally: Bedrock `ai_recovery` (default 2 transient attempts) → OpenAI fallback (same) → last-good cache (no extra HTTP).

Models unchanged. Repair does not add a third generation-layer call. Contract failures remain 0 calls.

## Structural validation

Fail closed. No weekday backfill, no `"45 dk"` parsing, no set clamping, no 0→7 scores. Bool is not an int. Unknown keys fail. Rest days cannot carry exercises. Training and cardio days must be non-empty.

## Semantic validation

Against the accepted PR2 `TrainingPreferences`:

- exact `gun_sayisi` training days
- exact dedicated cardio days
- rest count = 7 − training − cardio
- training duration in [max(15, sure/2), sure*2]
- style session size: general ≥1, bodybuilding ≥4, powerlifting ≥3, calisthenics ≥3, functional ≥3

## Style-specific validation

| Style | Enforceable today | Gap until PR4 |
| --- | --- | --- |
| General | 7-day usable sessions | equipment of named lifts |
| Bodybuilding | ≥4 exercises / training day | muscle-group coverage by name |
| Powerlifting | ≥3 exercises / training day | SBD identity, %1RM |
| Calisthenics | ≥3 exercises / training day | bodyweight-only names |
| Functional | ≥3 exercises / training day | pattern identity |
| CrossFit | not generated (PR2 unsupported) | schema still cannot express WOD |

A one-lift “bodybuilding” week is rejected. Invented names still pass: they remain untrusted strings.

## Exercise-authority boundary

No catalog, aliases, fuzzy matching, substitutions, or canonical IDs. Injury annotation remains warn-only. `EXERCISE_KB` was not wired.

## Prompt changes

System and contract prompts now require exactly one JSON object, no markdown/fences, closed keys, integer `set`/`sure_dk`. Not a prompt-optimization pass.

## Token/output budget findings

`BEDROCK_MAX_TOKENS` default 8000. Primary generate 4000, truncation repair 7000.

A representative 6-day × 8-exercise serialized week is well under 4000 tokens at 4 chars/token (`test_representative_plan_fits_primary_token_budget`). The schema was not widened. The old “write less” retry was the wrong lever for truncation.

## Typed output errors

| Code | HTTP | Retryable |
| --- | --- | --- |
| `TRAINING_PLAN_GENERATION_PARSE_FAILED` | 500 | yes |
| `TRAINING_PLAN_GENERATION_TRUNCATED` | 500 | yes |
| `TRAINING_PLAN_GENERATION_SCHEMA_INVALID` | 500 | yes |
| `TRAINING_PLAN_GENERATION_SEMANTICALLY_INVALID` | 500 | yes |
| `TRAINING_PLAN_GENERATION_UNAVAILABLE` | 500 | yes |
| `TRAINING_PLAN_SAVE_INVALID` | 422 | no |

User-facing `error` is i18n only. Raw provider text is not returned.

## Retryability semantics

- User may click generate again on parse/truncation/schema/semantic/unavailable.
- System may perform **one** internal repair on parse/truncation only.
- PR2 contract rejection: 0 provider calls, not retryable.
- Save invalid: not a provider retry.

## Save-time re-validation

`validate_plan_for_save` runs **before** `DELETE`. Handcrafted `{v:1}`, wrong weekday count, empty training days, and client-emptied sessions return 422 and leave the current row. The save route persists the validator's canonical return value while preserving list-input/list-storage and object-input/object-storage compatibility.

## Save anti-tamper behavior

No durable generation-request identity exists. PR3 does not add a signed token or temp generation row.

**Ruling (after P1 review):** save without stored preferences still enforces the product training-day allow-list `{3,4,5,6}` plus the 7-day structural contract. That blocks 1-day and 7-day client mutations. It still cannot bind style/cardio to the original generate command. Generation identity is deferred.

## Persistence safety

Invalid plan cannot persist. Repair cannot skip validation. Direct save cannot skip validation. Provider output is not written until generate succeeds **and** save re-validates. Delete happens only after validation. Canonicalizable client values such as padded weekday strings are normalized before persistence rather than validated and then written raw.

Adaptive Coaching versioning untouched. Save is still a lineage reset.

## Adaptive Coaching boundary

Generate does not journal mutations. `PlanMutationRecord` / `undo_last_change` are not imported. Deliberate regenerate is not coaching undo.

## Provider call upper bound

| Path | Generation-layer `chat_fn` calls |
| --- | --- |
| Valid first response | 1 |
| Eligible parse/truncation | 1 + 1 repair |
| Schema/semantic invalid | 1 |
| Invalid/unsupported/conflicting request | 0 |
| Provider unavailable | 1 (raises; no repair) |

Hard generation-layer cap: 2 `chat_fn` invocations. This is not a two-network-request claim: each `_heavy_complete` invocation contains bounded provider recovery, SDK retry, Bedrock-to-OpenAI fallback, and optional last-good-cache behavior.

## Performance / cost impact

Wasted calls drop vs the old path: day-count / schema failures no longer spend a second 7000-token completion. Parse/truncation still get one recovery shot. Save validation is local JSON, negligible vs a provider call.

## Security / privacy review

- Invalid direct save tampering: 422, no delete. Canonicalizable input is normalized before persistence.
- Arbitrary JSON / oversized names: structural reject.
- Raw provider output not in HTTP bodies or structured logs.
- Prompt/plan/PII not logged.
- Only `POST /training-plan/save` wholesale-writes `TrainingPlan`; it now validates first.
- Session ownership of save unchanged (`current_user.id`).

Not a general pentest.

## Tests added/updated

Added `tests/test_sprint11_training_generation_output.py` (parser, truncation vs parse, repair budget, style semantics, save safety, canonicalized save round trip, call-count cap, architecture guards, mocked generate→save path).

Updated PR2 contract tests, generator tests, training routes, workout completion/convergence, pump-check fixtures (dummy `{v:1}` saves now seed via DB where they were persistence fixtures, not save-contract tests).

## Architecture guards

- Save calls `validate_plan_for_save` before delete.
- Repair suffix is not “write less”.
- Semantic except-block does not repair.
- No exercise-catalog DDL / alias / fuzzy.
- Generate does not journal Adaptive Coaching mutations.
- PR2 capability still runs before provider.

## Full validation

| Check | Result |
| --- | --- |
| `python -m compileall -q app tests` | clean |
| `git diff --check` | clean (CRLF normalize warnings only) |
| Critical focused suite | **244 passed**, 0 failed, exit 0 (`test_sprint11_training_generation_output.py`, PR2 contract, generator, routes, mutation); 992 known warnings |
| `tests/test_i18n.py::test_locale_key_parity_tr_en` | passed |
| `tests/test_premium_quota.py` | passed |
| `tests/test_prompt_builder.py` | passed |
| `tests/test_plan_v2.py` | passed |
| `tests/test_dependency_boundaries.py` | passed |
| `tests/test_training_ui.py` | passed |
| `tests/test_workout_completion.py` / `test_workout_convergence.py` / `test_pump_check_sharing.py` / `test_ai_routing.py` / `test_ai_chat.py` | passed |
| Full suite after final P1 fix | `python -m pytest -q`: **4504 passed, 11 skipped, 3 deselected, 0 failed/errors**, exit 0; 15447 known warnings; 2570.00s |

No live provider required.

## Independent review

Independent read-only review focused on repair loops, call amplification, save bypass, truncation misclassification, fallback+repair stacking, leakage, catalog drift, Adaptive Coaching coupling.

| | |
| --- | --- |
| P0 | none |
| P1 | three found and fixed |
| P2 | parked (below) |

P1 fixes:

1. **Save request-bound floor.** Save with `preferences=None` now requires training-day count ∈ `{3,4,5,6}` (same PR2 allow-list). 1-day and 7-day client weeks 422 before delete.
2. **Truncated-but-closed JSON.** If adapter metadata says truncated and the closed object then fails schema/semantic (typical short week that still balanced braces), the first call is treated as `TruncatedError` and gets the one 7000-token repair. A fully valid week with truncation metadata is still accepted.
3. **Canonical save persistence.** The save route previously validated a normalized copy but serialized the raw client payload. It now serializes the validator's canonical return while preserving input container shape. A route/DB regression test proved RED on padded weekday persistence and GREEN after the fix.

Independent scoped re-review of all three: addressed. Final P0 = 0 and P1 = 0. Remaining gaps are accepted P2 / PR4 scope.

## Remaining P2s

- Save still cannot bind style/cardio to the original accepted request without a generation token.
- Inner Bedrock→OpenAI fallback + `ai_recovery` still lives inside each generation-layer call (capped at 2 such calls, not removed).
- Last-good cache can still supply text to generate; it is still fully validated.
- Closed schema rejects unknown exercise keys on re-save of exotic client payloads (intentional).
- No generate-time cap matching workout-state’s 50-exercise projection limit.
- Repair is a full regenerate + suffix, not a surgical JSON patch.
- Save acceptance directly tests the 1/3/7-day boundaries; successful 4/5/6-day save cases are enforced by the shared `{3,4,5,6}` authority but are not each parameterized at the save route.

## Deferred scope

Exercise catalog (PR4). Mobile Create Plan. Adaptive Coaching undo of generate. Typed replace on save. Generation identity. Provider/model change. JSON-mode / tool-use architecture.

## Files changed

New: `extractor.py`, `output_errors.py`, `plan_schema.py`, `semantic_validator.py`, `tests/test_sprint11_training_generation_output.py`, this report.

Modified: `training.py`, `ai.py`, generator service/validator/prompt/preference_contract, locales, `docs/TRAINING_GENERATOR.md`, adjacent tests.

## Commit list

Implementation and this report are committed together in one conventional commit after final static validation.

## Final repository state

Branch is one commit ahead of `5628e0d`. Exact-SHA focused validation and final status are recorded in the ship handoff. Not pushed.

## PR4 recommended scope

Canonical exercise authority:

- stable exercise IDs
- catalog / source of truth
- aliases and provider-name resolution
- equipment compatibility
- substitutions
- unknown-name rejection
- injury/restriction enforcement that currently can only warn

Until then AxisAI cannot truthfully claim a named lift is bodyweight-safe or a Powerlifting week contains squat/bench/deadlift.

## Final verdict

**READY TO SHIP.** PR3 meets the acceptance criteria: one canonical generated-plan shape, fail-closed parse, explicit repair eligibility, repair budget 1, semantic validation against PR2, canonical save persistence after re-validation and before delete, hard generation-layer call cap of 2, no persistence bypass, no catalog, no Adaptive Coaching generate-undo, and no mobile Create Plan.
