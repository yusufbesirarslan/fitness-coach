# Sprint 11 PR2 — Canonical Training Preference Contract — Final Report

Date: 2026-08-19
Status: implementation complete
Repo: `yusufbesirarslan/fitness-coach`

## Executive verdict

**READY.** AxisAI now answers “is this exact training request something the current generator can truthfully support?” **before** any Bedrock/OpenAI call.

Unknown styles fail. They never become General. `odak_hedef` is consumed. Impossible weeks fail. CrossFit and home Powerlifting are explicitly unsupported. Typed `{error, code, retryable}` bodies are returned. Deterministic failures produce zero provider calls and zero compact retries.

## Base / branch / HEAD

| | |
| --- | --- |
| Worktree | `C:\Users\yusuf\develop\fitness-coach\.worktrees\sprint11-pr2-training-preference-contract` |
| Branch | `sprint11-pr2-training-preference-contract` |
| Base | `origin/main` `49a8af0` (`feat(coach): add safe training plan mutation tools (#221)`) |
| PR1 discovery | commit `34ee2c7` on `sprint11-pr1-training-generator-reliability-discovery` — **not merged** to `origin/main` |
| Strategy | Built PR2 on current `origin/main`. Used PR1 report/tests as evidence. Did not merge PR1 history. |

`origin/main` advanced past the PR1 discovery base (`f2887e5` → `49a8af0`), including Adaptive Coaching plan-mutation tools (#221). PR2 does not route generate through undo.

## PR1 findings addressed

| Finding | PR2 |
| --- | --- |
| Unknown style → General | Fail `TRAINING_PLAN_INVALID_PREFERENCE` |
| `odak_hedef` collected and dropped | Wired into prompt via `goals.json` |
| 6 training + 5 cardio accepted | `TRAINING_PLAN_CONFLICTING_PREFERENCES` |
| CrossFit advertised, schema cannot express WOD | `TRAINING_PLAN_UNSUPPORTED_CONFIGURATION` |
| Home Powerlifting accepted | unsupported (`POWERLIFTING_REQUIRES_GYM_EQUIPMENT`) |
| Deterministic failures retried | Contract failures never call the provider |
| Generic 500 only | Typed codes + user-safe `error` |
| Two incomplete UIs | Backend authoritative; Plan v2 no longer maps every 400 to “complete your profile” |

## Canonical preference contract

`app/services/training_generation/preference_contract.py`

- One allow-list parse (`parse_canonical_preferences`, re-exported as `parse_preferences`).
- Missing fields use defaults. Present unknown/malformed values raise. No silent clamp.
- Declared aliases only: `general` / `general_fitness` → `genel`; `functional` → `fonksiyonel`.

## Style vocabulary

| UI | Style-rules key |
| --- | --- |
| `genel` | `general_fitness` |
| `bodybuilding` | `bodybuilding` |
| `powerlifting` | `powerlifting` |
| `calisthenics` | `calisthenics` |
| `crossfit` | `crossfit` |
| `fonksiyonel` | `functional` |

`canonical_style()` raises on unknown input. `build_program_context` no longer falls back to `general_fitness` rules.

## Capability matrix

`app/services/training_generation/capability.py`

| Style | Gym | Home | Minimal |
| --- | --- | --- | --- |
| General | supported | supported | supported |
| Bodybuilding | supported | supported | supported |
| Powerlifting | supported | unsupported | unsupported |
| Calisthenics | supported | supported | supported |
| Functional | supported | supported | supported |
| CrossFit | unsupported | unsupported | unsupported |

**Ruling:** Bodybuilding at home/minimal is supported because the 7-day `{isim,set,tekrar}` schema can express a hypertrophy week. Discovery labeled it provider-fragile, not unrepresentable. CrossFit is unsupported because WOD / mixed-modality structure is not first-class.

## `odak_hedef` decision

**Preferred path: wired.**

- Profile `goal` = body/composition context.
- `odak_hedef` = training-program emphasis, from `goals.json`.
- Both appear in the prompt. Tests prove `kas_kutlesi` / `Hypertrophy` / `odak_hedef=` reach the generation command.

## Cardio / day compatibility

Day `tip` values are mutually exclusive. A 7-day week cannot allocate more dedicated training + cardio days than 7.

- `kardiyo_tipi=yok` and `kardiyo_gun>0` → conflicting (`CARDIO_DAYS_WITHOUT_TYPE`). Days are **not** silently zeroed.
- `gun_sayisi + dedicated_cardio_days > 7` → conflicting (`WEEK_ALLOCATION_EXCEEDS_SEVEN_DAYS`).
- Plan v2 still omits cardio-day chips and sends `kardiyo_gun=0` (no dedicated cardio days). That is representable.

## Typed error contract

```json
{"error": "<user-safe message>", "code": "TRAINING_PLAN_<CATEGORY>", "retryable": false}
```

| Code | HTTP | Retryable |
| --- | --- | --- |
| `TRAINING_PLAN_NO_SESSION` | 400 | no |
| `TRAINING_PLAN_INVALID_PREFERENCE` | 422 | no |
| `TRAINING_PLAN_UNSUPPORTED_CONFIGURATION` | 422 | no |
| `TRAINING_PLAN_CONFLICTING_PREFERENCES` | 422 | no |
| `TRAINING_PLAN_GENERATION_INVALID` | 500 | yes (user may click again) |
| `TRAINING_PLAN_GENERATION_UNAVAILABLE` | 500 | yes |

Internal reasons stay in logs.

## Retry behavior

- Contract failure → 0 provider calls, 0 compact retries.
- Supported request + unusable JSON / `PlanValidationError` → one compact retry (4000 → 7000 tokens). Unchanged provider-output repair.
- `ai_recovery` transient retries on `_heavy_chat` unchanged.
- Models unchanged (Bedrock Claude Sonnet 4.5 primary, OpenAI `gpt-4o-mini` fallback).

## Provider-call boundary

Spies on `_heavy_chat` / `chat_fn` prove zero invocations for invalid, unsupported, and conflicting requests. `generation_started` is logged only after capability passes.

## Frontend changes

Not a Training UI redesign.

- Plan v2: 400 is “complete profile” **only** when `code` is missing or `TRAINING_PLAN_NO_SESSION`. Other failures show `data.error`.
- Plan v2: capability honesty note (`plan.create.capability_note`).
- Legacy `/training`: still toasts `data.error`; client-side guard for training+cardio overflow.
- Selections are unchanged on failure.
- CrossFit remains selectable; backend 422 is authoritative (allowed by the spec).

## Persistence impact

None, except: injuries are persisted only after a request is capability-supported, so a rejected generate does not write `user_metadata`. Save is still delete+insert, new lineage, no journal.

A contract-rejected generate cannot be saved as a successful plan (no `TrainingPlan` row is written).

## Adaptive Coaching impact

None. Generate still sits outside version/mutation/undo. Save is still a lineage reset.

## Mobile impact

None. Create Plan stays disconnected.

## Tests added

`tests/test_sprint11_training_preference_contract.py`

- Normalization: every official style/focus/day/duration/equipment/cardio; aliases; unknown style/focus/duration/equipment/cardio.
- Matrix: supported examples for General, Bodybuilding, Powerlifting gym, Calisthenics home, Functional minimal; unsupported CrossFit (all equipment) and home/minimal Powerlifting; conflicting 6+5 cardio and cardio-days-without-type.
- Provider boundary: 0 calls for unknown, CrossFit, home PL, overfull week.
- Supported request: style, focus, days, duration, equipment reach the prompt.
- Silent-General regression.
- `odak_hedef` regression (wired).
- API shape + retryability.
- Architecture guards (no unknown→General, capability before provider, no compact retry of contract failure).
- Plan v2 / legacy client string guards.

## Existing tests changed

None deleted. PR1 discovery tests were **not on `origin/main`**. Their “accepted but wrong” pins are converted in the new file rather than merged as failing characterization.

Existing generator/route tests continue to pass (default General gym 3×45 is supported; Powerlifting gym 3-day is supported; cardio 3+2 is supported).

## Architecture guards

- `canonical_style("banana_program")` raises.
- `build_program_context` with unknown style raises (no `general_fitness` rule fallback).
- `generate_training_plan_payload` calls `require_supported` before `chat_fn`.
- Compact retry is unreachable for CrossFit / overfull weeks.

## Full validation

| Check | Result |
| --- | --- |
| `git diff --check` | clean |
| `tests/test_sprint11_training_preference_contract.py` | passed |
| `tests/test_training_generation.py` | passed |
| `tests/test_training_routes.py` | passed |
| `tests/test_i18n.py` (key parity, localized generate) | passed |
| `tests/test_premium_quota.py` | passed |
| `tests/test_plan_v2.py` | passed |
| `tests/test_prompt_builder.py` | passed |
| `tests/test_training_ui.py` | passed |
| `tests/test_training_page_characterization.py` | passed |
| `tests/test_dependency_boundaries.py` | passed |
| Full suite | **4392 passed, 11 skipped, 3 deselected**; 1 timed out under suite load, **passed in isolation** |

Unrelated baseline failure (documented):

```
FAILED tests/test_extensions.py::test_extensions_import_without_openai_key
subprocess.TimeoutExpired: import app.extensions timed out after 60s
```

Re-run isolated: **PASSED in 27.84s**. The test subprocess-imports `app.extensions` with a 60s cap; it is unrelated to the preference contract and flakes when the full suite saturates the machine. No production change.

No browser driver was available. Frontend behavior is covered by route tests, client-source guards, and Plan v2 422 → `data.error` (the existing `!res.ok || data.error` path).

## Independent review

Focus: contract bypass, silent fallbacks, capability holes, FE/BE mismatch, retry leakage, provider on deterministic errors, scope, General regression.

| ID | Sev | Finding | Disposition |
| --- | --- | --- | --- |
| R1 | P0 | Unknown style could still alias to General via `canonical_style` `.get(..., "general_fitness")` | **Fixed.** Raises `PreferenceContractError`. |
| R2 | P0 | `build_program_context` used `_style_rules().get(style, general_fitness)` | **Fixed.** Direct key lookup. |
| R3 | P0 | Compact retry on contract failure | **Fixed.** `require_supported` before `_heavy_chat`. |
| R4 | P1 | Plan v2 mapped every HTTP 400 to “complete your profile” | **Fixed.** Only no-session 400. Contract failures are 422 with `data.error`. |
| R5 | P1 | `kardiyo_tipi=yok` silently zeroed `kardiyo_gun` | **Fixed.** Conflicting if days > 0. |
| R6 | P2 | CrossFit still clickable in both UIs | **Accepted.** Spec allows selection + typed unsupported. Backend is authoritative. |
| R7 | P2 | Provider day-count mismatch still compact-retries | **Accepted / PR3.** Spec allows provider-output repair on a *valid* command. |
| R8 | P2 | Plan v2 still omits cardio-day / intensity chips | **Accepted.** Sending `kardiyo_gun=0` is a truthful “no dedicated cardio days”. |

No remaining P0/P1.

## Known P2s

- CrossFit remains in the style `<select>` / chips.
- Compact retry still used for malformed provider JSON on supported requests.
- Plan v2 cardio-day UX incomplete (pre-existing).
- Save still trusts the client body (PR3/later).
- Exercise names remain untrusted provider text (PR4).

## Deferred scope

Exercise catalog, structured output, semantic validation, generate/save idempotency, mobile generate, Adaptive Coaching typed replace, provider/model changes.

## Files changed

**New**

- `app/services/training_generation/preference_contract.py`
- `app/services/training_generation/capability.py`
- `docs/TRAINING_GENERATOR.md`
- `tests/test_sprint11_training_preference_contract.py`
- `docs/superpowers/specs/2026-08-19-sprint11-pr2-training-preference-contract-final-report.md`

**Modified**

- `app/services/training_generation/feature_extractor.py`
- `app/services/training_generation/program_generator.py`
- `app/services/training_generation/prompt_builder.py`
- `app/services/training_generation/service.py`
- `app/services/training_generation/__init__.py`
- `app/blueprints/training.py`
- `locales/en.json`, `locales/tr.json`
- `static/plan_create.js`, `static/training.js`, `static/plan.css`
- `templates/plan.html`
- `docs/AI_ARCHITECTURE.md`, `docs/handoff.md`

## Commit list

See `git log` on `sprint11-pr2-training-preference-contract` after the implementation commit(s).

## Final repository state

Isolated worktree on `sprint11-pr2-training-preference-contract`, based on `origin/main` `49a8af0`. `main` checkout is untouched.

## PR3 recommended scope

**Generation Output Reliability**

1. Structured JSON (schema / tool-use) for the 7-day contract.
2. Split parse/truncation repair from unrepairable day-count misses — do not “write less” as the repair for a structurally wrong week.
3. Re-validate on `POST /training-plan/save`.
4. Bound the generate-layer retry to parse/truncation only.
5. Do **not** invent an exercise catalog (PR4). Do **not** merge generate into Adaptive Coaching undo.

## Final verdict

PR2 is complete against the contract, provider-boundary, API, architecture, and test acceptance criteria. The one full-suite timeout is an unrelated `app.extensions` import flake, proven green in isolation.
