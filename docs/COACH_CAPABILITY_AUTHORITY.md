# Coach capability authority (#533 / #547 F3): default closed

Source preparation only. This change registers, approves and publishes no disclosure, opens no gate and activates nothing. Ordinary Coach chat under `d1-closure02-coach-candidate-r2` is unchanged.

## Gap this closes

Before this change, the D1 contracts and the code did not line up in three places:

- **Training WRITE.** The Coach plan-mutation tools were gated by the rollout flag `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED` and the R6 containment, and nothing else. They ran under the r2 Coach grant, and r2 does not name plan editing. Turning the flag on would therefore have expanded every historical r2 grant.
- **Nutrition READ/WRITE (C2).** These were keyed on Coach *conversation* versions (`APPROVED_COACH_EDIT_DISCLOSURES`, `APPROVED_NUTRITION_READ_DISCLOSURES`), which left two bad options:
  - replace r2 with an r3, which locks r2 users out at `ai_launch.require_selected`;
  - list r2, which expands r2.
- **F3 risk-based confirmation.** This was not implemented: every Coach Training change required confirmation (#558).

## Model

Each Coach capability is a **separate D1 purpose** with its own affirmative grant, stored in the existing one-grant-per-purpose `AIConsentState.grants`. No migration is needed.

| Capability | Purpose | Also requires |
|---|---|---|
| `training_plan_write` | `coach.training_plan_edit` | the active Coach operation |
| `nutrition_plan_access` | `coach.nutrition_plan_access` | the active Coach operation + own `nutrition.plan` grant |

The registry is `app/services/coach_capabilities.py`. `APPROVED_VERSIONS` and `DIRECT_APPLY_VERSIONS` are code constants with no other channel, and both are **empty**.

A capability version opens (`version_open`) only when it meets every condition below:

- it has an exact `POLICIES` entry;
- its EN/TR texts are byte-pinned;
- `LAUNCH_VERSIONS` selects it;
- it is listed in `APPROVED_VERSIONS`;
- its category tuple is exactly the capability's own;
- it has no external scope and no nested helper;
- its recipients equal the launched Coach contract's recipients.

Capability purposes are never in `BINDINGS`, so they never open a provider dispatch. `mutate` refuses an affirmative grant of a capability purpose that is not open (`grant_open`).

## Authority lifecycle (`app/services/ai_consent.py`)

- `capture_capability(owner, capability)` refuses before any DB access when the source is closed. Otherwise it works under D1's owner lock:
  - it requires the active Coach operation;
  - it re-runs `_check_operation`, which proves the generation is unchanged;
  - it validates the capability record, plus `nutrition.plan` where required;
  - it returns `CapabilityAuthority(owner, capability, purpose, version, generation)`.
- `capability_scope(...)` puts authorities into the active operation, which must have the same owner and generation. Inside it, `fence_active` also fences every active capability in the caller's transaction (`_fence_capabilities`, run after `fence` has locked the owner row). This covers plan mutation, proposal staging and turn persistence.
- Proposals store their capability under `PAYLOAD_CAPABILITY_KEY`. Confirmation re-enters the *stored* capability (`executor.stored_training_capability`). If the capability is missing, foreign or stale, the result is a 403 and the proposal is marked stale. Revoke followed by regrant never revives it.
- Refusal codes:
  - `AI_CAPABILITY_UNAVAILABLE`: the source is closed or the version is not approved.
  - `AI_CAPABILITY_CONSENT_REQUIRED`: there is no valid grant, or the stored authority is missing or wrong.
  - Existing codes apply for generation changes.

## Wiring

**Training (`coach_plan_tools.executor`):**

- Inside a Coach operation, the write family is published and executable only with the turn's captured capability (`training_write_authority`, one capture per turn, cached on `g`).
- Cancel stays available.
- The read tool (B1) and ordinary chat are untouched.
- Outside any AI operation (non-AI callers and tests), `NOT_IN_AI_OPERATION` applies and behaviour is unchanged.

**Nutrition (C1/C2):**

- `access.Authorities` gains an optional `capability`.
- `access.fence` refuses without it, with `AI_CAPABILITY_CONSENT_REQUIRED`. When present, it fences coach, nutrition and the capability.
- C2 captures the capability for both edits (`turn_authorities`) and the saved-plan READ (`_read_authorities`).
- Quotes stored without a capability never apply.

**Risk policy (`coach-risk-v1`):** `is_direct_apply_eligible` treats a change as low impact only when all of the following hold:

- it is an `UpdateExercisePrescriptionCommand` with `sets` only;
- the set count is the user's own typed value for this turn (or the server-owned clarification it completes, via `grounding.user_owned_intent`), never one the model chose, and the user typed no rep target;
- exactly one canonical-document leaf (`set`) changes, by ±1, between positive integers (`proposals.changed_document_leaves`);
- the impact is determinable;
- no ACTIVE session is touched.

Such a change applies directly only when `direct_apply_allowed`, which needs an approved version that is also listed in `DIRECT_APPLY_VERSIONS`. Every other change keeps preview + explicit confirmation. Undo keeps its confirmed restore.

A Training proposal staged by an AI operation before this change carries no capability key: confirm answers `PENDING_CONFIRMATION_STALE` (409 on the native endpoint) and retires it, rather than asking for a consent the user may already hold. The saved Nutrition Plan READ also rechecks the capability (`authorize_capabilities`) before publishing plan content to the model.

## Capability request and disclosure availability (companion D-1/D-2)

**D-2, disclosure availability.** `ai_consent_disclosures.available_disclosures()` publishes a capability purpose only while `coach_capabilities.version_open` holds for its launched contract. A version that is registered (POLICIES + PUBLISHED_TEXTS + LAUNCH_VERSIONS) but not in `APPROVED_VERSIONS`, or that deviates in any other way, is not published, so no client can show an Allow for it. A forged grant is still refused by `mutate` (`grant_open`). The five core D1 purposes are unchanged.

**D-1, the typed request.** A Coach turn that needs a missing capability has no write/read tool for it, and a client must never infer missing authority from reply text. The server gives it a typed signal instead:

- **Predicate.** `ai_consent.capability_request_open(owner, capability)` is True only when all of these hold. Anything else, an outage included, is False, and it never raises or writes.
  - The capability's launched contract is open. When the source is closed (all of this source), it returns before any database read.
  - An active Coach operation for this owner is still valid at its own generation (`_check_operation`).
  - The capability is not in effect: its own grant at the launched version is missing, or for Nutrition the separate `nutrition.plan` grant is missing.
  - Every missing purpose is published and passes `_require_grant_cohort`.
- **Tool** (`coach_plan_tools/capability_request.py`). `request_coach_capability` takes a single enum argument listing only the capabilities that are actionable now.
  - "Actionable" means the predicate holds and granting would actually open a tool: Training needs the plan-tool flag; Nutrition needs an approved saved-plan READ pair or the open C1 edit boundary (`nutrition.capability_applicable`).
  - It is published inside the flag block, and never without the user's own text or on a typed confirm/cancel answer.
  - Execution re-checks the predicate and records the first request of the turn.
  - The model sees a closed result (`coach_privacy`: `status` + `outcome` ∈ {`review_offered`, `unavailable`}).
  - It never grants, reads plan content, stages, writes or changes allowance accounting.
  - A refused write tool is never turned into a request.
- **Effect.** `turn_plan_effects` adds `capability_request = {capability, purpose}`.
  - It is re-verified at response time in the same operation, so a consent change during the turn retires it.
  - It is bound to the turn's owner and reset by `begin_turn` and the B2 direct turn.
  - The purpose comes from `SPECS`, never from the model.
  - `mobile_coach.project_plan_effects` publishes only an exact registered pair (wire: docs/MOBILE_COACH.md §Capability request).
- **No replay.** After a grant the client returns the user's own message as an unsent draft. The next turn the user sends publishes the write family and no request tool.

Tests:

- `tests/test_coach_capability_request.py`: predicate, D-2, publication, execution, effect, wire, and the native route end to end.
- `tests/test_coach_capability_request_pg.py`: real grant/revoke/regrant, Nutrition's two grants and the cohort, on the owner lock; in the CI PG list.

## Opening it later (separate reviewed PR, after counsel + founder approval)

1. Register the approved contracts: `POLICIES`, `PUBLISHED_TEXTS` generated from the approved files, and `LAUNCH_VERSIONS`.
2. Add the approved capability versions to `APPROVED_VERSIONS`.
3. Add to `DIRECT_APPLY_VERSIONS` only if counsel accepts the direct-apply wording.
4. Set Nutrition to `APPROVED_COACH_EDIT_DISCLOSURES = {r2}`, `APPROVED_NUTRITION_READ_DISCLOSURES = {(r2, nutrition-plan)}` and `NUTRITION_EDITS_ENABLED = True`.

Production activation remains separate: flags, R1 watch2 and deployment. The mobile in-flow prompt and Settings rows are mobile #235; the typed request it consumes is D-1 above.

Tests:

- `tests/test_coach_capability_authority.py`
- `tests/test_coach_capability_authority_pg.py`: real owner lock; in the CI PG list.
