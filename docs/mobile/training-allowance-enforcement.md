# Native initial Training generation — AI allowance enforcement (MON-02-BE-03B-01B)

**Dark.** `AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED` defaults OFF. Activation also
needs `AI_ALLOWANCE_LEDGER_ENABLED`, an installed `AllowancePolicy` (none exists in
code or env) and the founder decisions listed below. No deployment, migration or
payment activation is authorized by this document.

Scope: `POST /api/v1/training/plans` (first plan only). Every other AI route —
Training replacement, web Training, Coach (native/web/streaming), Nutrition
generation, Pump Check, QR menu, background summaries — remains unmetered.

## Authorities

| Authority | Owns |
| --- | --- |
| `TrainingPlanGenerationOperation` | owner, `Idempotency-Key`, semantic fingerprint, dispatch marker, candidate, canonical plan receipt, logical outcome, metering mode (`allowance_metered`) |
| `training_plan_generation_allowance` | the one ledger hold of a metered operation (PK = operation, UNIQUE ledger `public_id`), and the settlement state last observed |
| AI allowance ledger | tier (via the #436 entitlement), eligibility, bucket arithmetic, hold, consumption/release, expiry, billing evidence |

`app/services/mobile_training_generation/allowance.py` is the only Training module
that imports the ledger (AST gate).

## Admission and dispatch

`AUTHENTICATE → VALIDATE → onboarding / existing plan / active operation (short
owner arbitration) → CLAIM (fingerprint bound; mode fixed) → rate limit +
concurrency slot → ONE TRANSACTION { operation row lock, False marker check,
ledger owner lock, lazy expiry, grant, bucket UPDATE, hold, binding, marker True }
→ inference (spend guard at each physical call) → stage → plan + SUCCEEDED →
settle → respond`.

* Metered claims skip the legacy weekly quota: the ledger is the one quota
  authority for them. `User.is_premium` is never read on the metered path.
* The hold is created only in the commit that authorizes dispatch. There is never
  a hold without the marker, and never a metered marker without a hold. A refusal
  before that commit (rate limit, capacity, exhausted, not in plan, unconfigured,
  disabled, subscription unavailable) holds nothing and deletes the never-dispatched
  claim, so it never blocks a retry.
* Lock order: operation → `user` (ledger owner lock) → reservations → bucket; the
  binding is a new row. Final persistence keeps operation → `user` NO KEY UPDATE →
  plan. Settlement takes `user` (ledger) then the binding row, never the operation.
  No lock or transaction spans provider I/O.
* The mode fixed at claim must still match the flag at dispatch; otherwise the
  claim is abandoned and the command answers retryable
  `AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE` (a legacy reservation is refunded).

## Settlement

Every dispatch-bound hold carries billing evidence **UNKNOWN** (`provider_billed`
NULL): the adapter gives no charge receipt, and internal repair, transient retries
and provider fallback stay inside one authorized invocation.

| Case | Operation | Ledger | Units |
| --- | --- | --- | --- |
| Plan + SUCCEEDED committed | SUCCEEDED | `success` → COMMITTED | 1 |
| Lost HTTP response / replay | SUCCEEDED replay | finalize replays | 0 more |
| Refusal before dispatch | claim deleted | no hold | 0 |
| Unusable provider output (schema/semantic/exercise, or parse/truncation after the repair turn) | FAILED (stored code) | `invalid_output`, UNKNOWN: released while `billed_releases < max_billed_releases`, else consumed | policy cap |
| Provider unavailable / timeout | FAILED 503 | `provider_error`, UNKNOWN, same cap rule | policy cap |
| Output lost before staging / worker died | IN_PROGRESS → 409 → FAILED OUTCOME_UNKNOWN after 15 min | hold already past its TTL (same instant as the marker) → EXPIRED | 1 |
| Candidate staged, save failed | GENERATED; save-only recovery | held; success settles once (EXPIRED if after TTL) | 1 |
| GENERATED never saved (a browser plan appeared) | GENERATED | held until the ledger expires it (owner's next reserve or `flask ai-allowance-sweep`) | 1 |
| Success committed, settlement lost | SUCCEEDED, binding RESERVED | finished on replay or the owner's next command; ledger replays | 1 |
| Retention purge removed the hold | — | binding UNOBSERVABLE | unchanged |

Settlement runs only after the terminal commit and is idempotent. An expired hold
is reported as EXPIRED, never as a fresh COMMITTED charge. Retirement fences late
staging, not remote execution: a distinct new key may overlap an old request whose
termination cannot be proven; the new key is a new logical action with its own
hold.

## Errors (existing mobile envelope)

Refusals reuse the `ai-allowances-v1` catalogue unchanged: `AI_ALLOWANCE_EXHAUSTED`
402, `AI_FEATURE_NOT_IN_PLAN` 402, `AI_ALLOWANCE_POLICY_UNCONFIGURED` 503,
`AI_ALLOWANCE_DISABLED` 503, `AI_ALLOWANCE_ACCOUNT_UNAVAILABLE` 409,
`AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE` 503 retryable (`Retry-After: 30`). The
envelope carries no `feature`/`scope`/`reset` fields; the client re-reads
`GET /api/v1/ai/allowances` (unchanged, `contract_version` 1) after any 402.

## iOS follow-up (not implemented here)

Map the codes above on Training generation; never derive balances or tier from
StoreKit; keep same-key retry for IN_PROGRESS and lost responses; never start a
new key automatically after ambiguity; treat `TRAINING_PLAN_GENERATION_OUTCOME_UNKNOWN`
as unresolved, not success; a 402 is a paywall/upsell state, not a failure to retry.

## Founder decisions required before activation

1. Quantities (Starter, Premium) and existing-user Starter eligibility.
2. UNKNOWN-billing failures (invalid output, provider error/timeout) share the
   per-scope `max_billed_releases` cap; the cap value (0 = always consume) is the
   decision. Unlimited release is not supported.
3. Abandoned dispatched work and never-saved GENERATED candidates consume one unit
   (ledger expiry). A release policy would need a ledger change.
4. Provider fallback / last-good success consumes one unit like any success.
5. Retiring the legacy weekly Training quota (and the `is_premium` bypass) for
   metered claims.
6. Renewal/rollover cadence and deletion/re-registration eligibility (ledger).
