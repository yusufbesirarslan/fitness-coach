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
* The hold, the binding and the marker are stamped with ONE instant, and
  retirement (`store.ABANDON_AFTER`, 15 min) must not come before hold expiry
  (`ai_allowances.RESERVATION_TTL`, 15 min): then a retired operation's hold is
  already expired and consumes exactly one unit. Dispatch checks this on the real
  hold (`expires_at <= marker + ABANDON_AFTER`); drift in either constant, or a hold
  stamped after the marker, refuses before dispatch with retryable
  `AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE` (nothing held). Equal durations do not
  mean the remote provider stops after 15 minutes.

## Settlement

Every dispatch-bound hold starts with billing evidence **UNKNOWN** (`provider_billed`
NULL): the adapter gives no charge receipt, and internal repair, transient retries
and provider fallback stay inside one authorized invocation. The one exception is
a failure with durable proof of zero physical attempts (next section).

| Case | Operation | Ledger | Units |
| --- | --- | --- | --- |
| Plan + SUCCEEDED committed | SUCCEEDED | `success` → COMMITTED | 1 |
| Lost HTTP response / replay | SUCCEEDED replay | finalize replays | 0 more |
| Refusal before dispatch | claim deleted | no hold | 0 |
| Unusable provider output (schema/semantic/exercise, or parse/truncation after the repair turn) | FAILED (stored code) | `invalid_output`, UNKNOWN: released while `billed_releases < max_billed_releases`, else consumed | policy cap |
| Provider unavailable / timeout | FAILED 503 | `provider_error`, UNKNOWN, same cap rule | policy cap |
| D1 consent refused before reservation (preflight) | claim deleted, D1 403/503 envelope | no hold | 0 |
| Refused after the hold, before ANY physical attempt (consent revoked/changed, spend or input refusal, unrecordable evidence) | FAILED 503 (`provider_attempt_possible` False) | `refused_before_provider`, CONFIRMED_NOT_BILLED → RELEASED | 0 |
| Refused after an earlier possible attempt (retry, recovery, repair, fallback) | FAILED (marker True) | unchanged UNKNOWN rule | policy cap |
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

MON-02-BE-03B-A2 also orders metered retirement against local physical SDK entry
using D1's existing account fence. An aged operation keeps its operation lock and
acquires the account row with `NOWAIT` before terminal commit. Both are short
retirement locks; no new lock spans provider I/O. Active D1 ownership
refuses retirement temporarily with the existing persistence-unavailable error.
The 15-minute threshold stays fixed; retirement can be deferred while D1 owns
the fence. See `training-dispatch-recovery.md` for lock order and rollout limits.
An SDK timeout can still leave remote inference running after retirement.
Expiry compensation remains unimplemented pending founder policy; EXPIRED still
consumes exactly one unit even for a terminal False attempt marker.

## Zero-attempt settlement (MON-02-BE-03B-A1)

`provider_dispatch_possible` means the operation was **authorized** to call a
provider; it is not evidence that a call happened. Migration `e7a1c3d5f9b2` adds
`training_plan_generation_operation.provider_attempt_possible` (nullable):

* the metered dispatch commit sets it **False** (same transaction as the hold,
  binding and marker);
* `ai_provider_call.Admission.create`/`stream` run an optional
  `attempt_observer` **inside** the final D1 `ai_consent.dispatch` fence, after
  it passed and **before every physical SDK invocation**. For a metered
  operation the observer is `store.mark_attempt_possible`: a short committed
  `UPDATE … SET provider_attempt_possible = TRUE WHERE status = 'IN_PROGRESS'
  AND provider_dispatch_possible AND provider_attempt_possible IS NOT NULL`
  (`lock_timeout` 2 s, `updated_at` unchanged). If it cannot commit (retired,
  deleted or unreachable operation) the attempt is refused with
  `AttemptEvidenceUnavailable`, a spend-type refusal: no SDK call, no retry, no
  fallback, no last-good;
* NULL = untracked (unmetered, historical, or dispatched by older code) and keeps
  the UNKNOWN rule.

So a FAILED operation still False made no physical attempt, ever: True must be
committed before any SDK call and can only be committed while IN_PROGRESS.
Settlement derives `(outcome, evidence)` from that terminal row only, so replays
are deterministic. A refusal after an earlier possible attempt keeps True and
UNKNOWN. A crash, a lost process or a timeout never produces False after an
attempt; a crash before the first attempt leaves False, but retirement happens at
the hold's expiry instant, so that hold settles EXPIRED (consumed once).

Preflight: metered claims call `ai_consent.preflight` (the same grant, purpose,
category, recipient and model predicate as `dispatch`, for the heavy router's
first route, `ai.heavy_primary_route()`) after the claim and before the rate
limit, permit, hold or SDK. A refusal deletes the never-dispatched claim and
answers with D1's own envelope. It authorizes nothing; the final gate still
decides every attempt and also fences the consent generation.

Expiry race (bounded): if a proven zero-attempt failure is settled only after its
hold's TTL (ledger outage, or a crash between the FAILED commit and settlement),
the ledger reports EXPIRED and the unit stays consumed: an expiry is never
reversed. The binding records EXPIRED and the warning
`training_allowance event=zero_attempt_hold_expired` is logged (no identifiers).
Releasing it would need an explicit ledger contract (founder decision).

Lock order is unchanged. The marker write takes only the operation row, inside
D1's fence (which holds `user`); no ledger lock is taken there and no
transaction spans the SDK call.

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

## Accepted, deferred before activation (R1 P3)

* No repository-managed schedule runs `flask ai-allowance-sweep`. Units stay
  correct without it (an expired hold counts as reserved, then as one consumed
  unit on the owner's next command or reserve), but the GET view can show
  `reserved` instead of `used`. Scheduling the sweep is an activation
  prerequisite (operational qualification), not part of this source change.
* `ai_allowance_bucket.billed_releases` counts UNKNOWN releases too: it means
  "released while possibly billed", never confirmed provider billing. FinOps reads
  billing from `billing_evidence`, not this counter.
* Account deletion racing an in-flight dispatch transaction can abort on a
  database deadlock (bounded; abort, never corruption), the same class as
  `commit_plan` today.
