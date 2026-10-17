# Settlement proposal — not activated

> MON-02-BE-03B-01B implements this as a dark, policy-parameterised mechanism:
> see [training-allowance-enforcement.md](training-allowance-enforcement.md).
> Nothing below is activated; the founder decisions remain open.

Logical action success and provider billing are independent. Current native completion handling reduces ChatCompletion to text/truncation, discards provider/cache provenance, and normalizes exceptions. `ai_provider_call.Admission._emit` is best-effort usage telemetry, possibly estimated and lossy. Spend admission is permission to attempt, not payment proof. None is a cost receipt.

Evidence vocabulary: CONFIRMED_NOT_BILLED requires proof **no physical dispatch** for the whole logical operation; CONFIRMED_BILLED requires a reliable charge receipt the current adapter does not supply; otherwise UNKNOWN. Dispatch True is neither confirmed billing nor confirmed execution. Historical NULL is uncertainty. Invalid output can cost money. Provider refusal after an earlier internal attempt is not whole-operation zero billing. No fabricated provider_billed value is stored by this correction.

| Case | Observed evidence / billing | Proposed client outcome | Proposed future allowance settlement / units | Provider cost | Founder decision |
| --- | --- | --- | --- | --- | --- |
| Valid persisted, owner-retrievable and delivered generation | SUCCEEDED; billing UNKNOWN | 201/replay | Commit logical success, consume 1 under later approved policy | Possible | Ordinary success policy must be approved for activation; fallback provenance still required |
| Refusal proven before any dispatch | No marker authorization/physical attempt; CONFIRMED_NOT_BILLED | Existing quota/rate/capacity refusal | No hold or release, 0 | Excluded for this operation | No special ambiguous-cost decision; ordinary policy approval still needed |
| Provider timeout | Marker True, no authoritative receipt; UNKNOWN | Stored unavailable, or IN_PROGRESS then terminal unknown after crash | Leave commercial choice unactivated: consume or capped release must be decided | Possible | **YES: who bears unknown cost?** |
| Invalid provider output | Definite logical invalid result; UNKNOWN billing | Stored typed terminal error | Proposed release only if founder permits bounded failed-output subsidy; otherwise consume | Possible, including repair | **YES** |
| Output generated but lost before staging | IN_PROGRESS True; UNKNOWN billing | 409 then terminal unknown 503; never same-key redispatch | No automatic release proposal activated; founder must choose consumed vs bounded subsidy | Possible | **YES** |
| Output staged, final save failed | GENERATED; candidate recoverable, UNKNOWN billing | Retry save only; no inference | Retain pending settlement intent; consume 1 only once retrievable success; policy required if never persisted | Possible | **YES** for abandonment/non-delivery; current ledger expiry already consumes holds |
| Disconnect/lost response after successful commit | SUCCEEDED owner/lineage receipt; UNKNOWN billing | GET/replay existing plan | Commit logical success once, consume 1; no transport-loss refund | Possible | Standard success rule; no second unit for replay |
| Cache/last-good fallback | Candidate currently loses provenance; UNKNOWN whether earlier attempt billed | Valid persisted plan may still be logical success | Defer fallback charging until provenance and policy approved | Zero or possible earlier-attempt cost | **YES** |

All allowance settlements above are PROPOSED_NOT_ACTIVATED. No reserve/finalize calls, holds, grants, balances, GET v1 or ledger schema change. Existing legacy definitive-failure refunds remain unchanged and do not assert provider_billed=False. Ambiguous staging/crash reservations remain retained as before; retention is not a new customer billing decision. Existing quota/rate/capacity/spend admission constrains new explicit attempts; same-key free inference replay is eliminated.

Smallest future contract extension: represent billing evidence as a tri-state enum independent from logical outcome (e.g. nullable/provider_billing_evidence alongside legacy provider_billed, with explicit accepted UNKNOWN semantics), and durably associate operation/allowance reservation + settlement intent. Review how existing Boolean finalization, expiry-as-consumed and billed-release cap handle UNKNOWN before changing them. Do not overload False for uncertainty or arbitrarily set True. Add bounded privacy-safe per-invocation provenance across repair/transient retry/provider fallback/cache; aggregate evidence conservatively. This task deliberately does not add that ledger schema/API or commercial policy.
