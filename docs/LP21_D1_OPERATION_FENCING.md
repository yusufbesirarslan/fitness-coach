# LP21 D1 operation-wide consent fencing

Engineering prerequisite for D1 consent binding. It does **not** publish a
disclosure or activate any consent-gated operation: `ai_consent.POLICIES` stays
`MappingProxyType({})`, so in production every AI path below still refuses with
the existing D1 codes. Legal dependencies (LD-09, FP-01..03) are unchanged.

## Problem

D1 fenced each *physical* provider call: an `Admission` captured the account
consent generation once and `dispatch` re-checked it under the owner lock on
every SDK attempt. A *logical* operation spans several admissions (recovery
ladder, Bedrock→light fallback, format repair), a durable candidate (first-plan
`GENERATED`, replacement proposal, Nutrition proposal token) and a later
canonical write. Before this change:

- a fresh admission inside the same operation re-read the generation, so a
  revoke → regrant (g+2) between admissions silently re-authorized old work;
- staging, `GENERATED` recovery, replacement publication/confirmation and the
  Nutrition save wrote AI output with no consent predicate at all;
- the last-good cache could serve or store a result outside any authority;
- the browser plan-save aliases persisted client-held AI output with no
  server-held provenance.

## Contract (`app/services/ai_consent.py`)

| Piece | Rule |
|---|---|
| `begin_operation(owner, feature, recipient, model)` | The `preflight` predicate under the account lock, plus capture of the immutable `OperationAuthority(owner, feature, generation, version)`. Called once per NEW explicit user operation; recovery never calls it. |
| `operation_scope(authority)` / `admission_generation` | Every `Admission` inside the scope fences on the operation's ORIGINAL generation. Wrong owner → `ConsentDenied()`; wrong feature → `AI_CONSENT_RECIPIENT_OR_SCOPE_UNAVAILABLE`. Per-attempt recipient/model checks in `dispatch` are unchanged, so a #456 claude55 fallback to Haiku still needs Haiku in the grant. |
| `fence(authority, owner)` | Transaction-local: on the caller's `db.session`, owner row `FOR NO KEY UPDATE` (the `lock_plan_owner` mode, a no-op re-lock where the caller already holds it), then the committed consent state in the same READ COMMITTED transaction. The caller writes and commits under that lock. Generation ≠ captured → `AI_CONSENT_CHANGED`; grant/version gone → `AI_CONSENT_REQUIRED`; `None` authority → `AI_CONSENT_PATH_UNAVAILABLE`; foreign owner → refused; SQL failure / non-PG / non-READ-COMMITTED → `AI_CONSENT_AUTHORITY_UNAVAILABLE` (503). Never opens D1's separate connection. |
| `authorize_operation(authority)` | Publication decision with no domain transaction to join (Nutrition proposals, replacement replay). D1's own locked transaction; caller must hold no owner lock. |
| `bind_candidate` / `unbind_candidate` | Staged AI text (first-plan `candidate_plan_data`, replacement `candidate_plan_data`) carries its authority in a versioned envelope in the SAME column — no migration. The document bytes are kept exactly. Legacy/foreign/corrupt material is stale (`AI_CONSENT_CHANGED`). |

The account generation is conservative: ANY consent mutation after capture
leaves the operation stale for good. A new user action is a new operation.

## Boundaries

| Workflow | Fence |
|---|---|
| First plan | `begin_operation` after the dispatch marker; `operation_scope` over the generator; `stage_candidate` fences (operation → owner) and stores the envelope; `commit_plan` fences under its owner lock before the insert. Definitive refusal → `FAILED` with the D1 code, candidate discarded, settlement from the true attempt evidence. Authority outage at commit keeps `GENERATED` recoverable at the original g. Stale `GENERATED` work of an owner is terminalized (`refuse_stale_generated`) instead of stranding a new operation. |
| Replacement | Authority frozen into the lease `generation_context`; lease recovery uses the frozen one (missing → refused before dispatch); `publish` fences (operation → owner → plan); confirm unbinds the proposal and `replace_training_plan_in_transaction` fences; a replayed proposal is re-published only under its original authority. |
| Nutrition | `begin_operation` before the quota; generation in `operation_scope`; `authorize_operation` before any token is issued; the signed proposal token carries `g`/`v` (pre-LP21 tokens are no longer proposals); `replace_nutrition_plan` fences in the save transaction. |
| Browser aliases | `training.save_training_plan` / `nutrition.save_nutrition_plan` joined D1 `_CLOSED_ENDPOINTS` (their generators were already closed); the shared writers also refuse `authority=None`. |
| Last-good cache | `ai_recovery.remember_last_good` / `recall_last_good` are no-ops inside an operation (`ai.py` unchanged). |
| Replacement replay | The publishing authority is bound into the SUCCEEDED row's `review_data` (same envelope; `ck_replacement_generation_result` forbids `generation_context` there), so a replay still works after the proposal is privacy-cleaned and is refused after any consent mutation. Pre-LP21 rows are stale. |

## Coach boundaries (for the #463 / #137 integration owner)

Coach has NO binding here and stays in `_CLOSED_ENDPOINTS`. The shared writers
it will reach are fenced **only inside an `operation_scope`**, so every
first-party/non-AI caller is byte-identical:

| Writer | Fence |
|---|---|
| `plan_mutation.apply_plan_mutation` / `undo_last_change` (Coach plan tools, confirmed proposals) | `fence_active(user_id)` before `_locked_active_plan` (owner → plan; a Coach confirm is proposal → owner → plan, the same prefix `confirm_replacement` already takes). Replays of committed journal rows are not fenced (completed actions reconcile). |
| `plan_confirmation.create_or_reuse_pending` | `fence_active(user_id)` before the pending insert; the proposing authority is stored under `ai_consent.PAYLOAD_AUTHORITY_KEY` in the server-written `command_payload`. |
| `coach_plan_tools.executor._apply_confirmed` | Inside an operation, the confirmed command is applied under the PROPOSING authority (revoke → regrant between proposal and "yes" refuses); a pending row without one is stale. |
| `memory_manager.record_turn` | `fence_active(conversation.user_id)` FIRST (owner before any conversation row). |
| `ai_pipeline._record` | Re-raises `ConsentDenied` (never swallowed by the best-effort catch). |
| `ai_pipeline.generate_answer` | A non-fallback reply that was not persisted (memory off/unavailable) calls `authorize_active()` before it is returned. |
| `coach_plan_tools.execute_plan_tool` | Propagates `ConsentDenied`; every other failure is still a bounded tool result. |

### Interface the integration owner composes (no parallel authority)

1. Add the ONE reviewed Coach binding to `ai_consent.BINDINGS` (and its
   approved disclosure to `POLICIES`) — Legal-gated, not done here.
2. Per explicit user turn: `authority = ai_consent.begin_operation(owner,
   "coach", recipient, model)` with `(recipient, model) =
   ai.heavy_primary_route()`, BEFORE allowance reservation and context build;
   then run the whole turn (provider loop, tools, `_record`, publication) in
   `with ai_consent.operation_scope(authority):`.
3. Map `ConsentDenied` to `consent_error_response` in the Coach routes, and
   in `ai_coach`'s provider loops re-raise `ConsentDenied` before the generic
   `except AISpendLimitExceeded` soft fallback. Today that catch turns a
   refusal into the canned fallback text: fail-closed (no AI content
   persisted or published) but not a typed refusal. `ai_coach.py` is a
   PA03-BE-02 file and the loop belongs to the integration, so it is not
   edited here.
4. Streaming stays closed (`stream_dispatch` denies); `stream_answer` is not
   qualified for publication fencing.

Lock graph: every fence re-takes the owner row in the mode the writer already
holds (`lock_plan_owner`) or takes it at the writer's existing owner-lock
position (`stage_candidate`: operation → owner, the `commit_plan` prefix). The
consent state read takes no lock. No new lock edge; revoke (`mutate`, owner
`FOR UPDATE`) serializes against every fenced write.

## Qualification

- `tests/test_lp21_d1_operation_fencing_pg.py` (CI PG list) — real chain on
  disposable PostgreSQL, synthetic disclosures only, no fence stubs: R01–R10,
  revoke-first / write-first for every writer (forced by parking a revoker
  inside `mutate` and the writer before its lock / after its fence, proceeding
  only when PostgreSQL reports a lock waiter), routing-55 fallback, browser
  aliases, and non-vacuity controls (without the operation generation the
  regranted recovery admission dispatches; without the write fence a revoked
  save lands).
- `tests/test_lp21_d1_operation_fencing.py` — codec, owner/None refusals,
  last-good guard, empty `POLICIES`, active-scope no-ops, `_record` /
  executor refusal propagation, unpersisted-reply publication fence.
- Coach boundaries are qualified in the PG suite with a synthetic, test-only
  `coach` binding: stale turn/tool/pending writes refused with nothing
  written; revoke-first and write-first for turns and plan tools; cross-account
  refusal; unscoped writes unchanged.
- Pre-existing command/lock-order suites that use fake providers opt into
  `synthetic_operation_authority` / `allow_operation_fences` (ALLOW at the
  three operation decision points only; dispatch and the codec stay real).

## Open before any activation (not engineering-closable here)

- **Publication record (R1 §5):** Nutrition proposals and replacement replay
  are authorized under the account lock (`authorize_operation`) and then sent;
  no durable publication decision is recorded and Nutrition has no operation
  ledger. Security must accept this check-then-send residual, or a ledger is
  added, before Nutrition generation or replay is opened.
- **Operation identity:** `OperationAuthority` carries owner, purpose,
  generation and policy version, not an operation id/intent fingerprint (R1 §1).
  The conservative account generation makes it fail closed; Security to accept.
- **Typed Coach refusals:** see interface item 3.
- Accepted P3s: weekly-quota refund after a possible attempt on the
  replacement/Nutrition ledgers (pre-existing semantics, not billing
  evidence); a stale-GENERATED retirement racing its own worker returns 503
  instead of the D1 code (nothing written); R03 session-loss variant not
  modelled.

## Not claimed

No disclosure, recipient list, legal text or approval. No atomic DB-commit +
HTTP delivery: a result authorized before a revoke can still reach the client
after the revoke is acknowledged. A replacement confirm refused by the fence
records no receipt (the refusal is stable because the generation never goes
back). Remote processing already submitted cannot be recalled.
