# MON-BE-PREMIUM-AGGREGATE-BUDGET-ENFORCEMENT-03

This source change adds a durable aggregate physical execution boundary to the existing allowance ledger and original native Training authority. The proposed weekly offer remains Coach 20, Training 2, Nutrition 2, Pump Check 3 and QR Menu 1; it is a qualification target, not an installed production policy. Source qualification and paid-launch acceptance are separate decisions.

## Scope and evidence

Baseline: `f4bccebcd7a5a1f43f00293931371377b4bf7d57`, PR #466 and its release checklist. This follows the existing `mon-be-premium-physical-cost-qualification-02/REPORT.md` and `README.md` findings: per-attempt limits alone did not bound aggregate tokens, the native Training mocked recovery ladder reached 20 attempts, and the five-family full-use offer remained financially exposed. Existing provider-door, input budget, spend guard, reservation ledger, billing evidence and native OperationAuthority remain authoritative at their respective boundaries. No complete financial model is repeated; [ECONOMICS.md](ECONOMICS.md) recomputes only the affected offer.

No AWS, Apple, provider, deployment or migration operation is performed against staging or production. The working branch is isolated. #467 coordination is recorded in [the owner’s PR](https://github.com/yusufbesirarslan/fitness-coach/pull/467#issuecomment-6416017327): one CI push-branch entry plus a new additive migration parent; no Apple implementation or active worktree edit.

## Physical envelopes

Input values use the existing conservative frozen-payload upper-bound units: UTF-8 JSON bytes, model-specific image ceilings and protocol overhead. Output is the full requested maximum, even on a failed, truncated or UNKNOWN attempt. These are per logical action and include all nested calls, retries and fallback providers.

| Family | Aggregate input | Authorized output | Attempts | Images | Weighted cost limit USD |
|---|---:|---:|---:|---:|---:|
| Coach | 256,000 | 7,000 | 8 | 2 | 0.8730 |
| Training, native and web | 64,000 | 22,000 | 4 | 0 | 0.5220 |
| Nutrition | 32,000 | 4,000 | 2 | 0 | 0.1560 |
| Pump Check | 24,000 | 2,400 | 2 | 2 | 0.1080 |
| QR Menu, text | 208,000 | 34,000 | 16 | 0 | 1.0308 |

The limits are a vector: the first exhausted component stops the next complete invocation. They do not promise that every combination of extrema is attainable. No call’s requested output is silently reduced. Existing per-call input/output and spend guards still apply. This is a processing safety boundary, not a second subscriber dollar balance or an undisclosed weekly quota. Failures follow the original visible allowance settlement authority.

## Admission and durability

`ai_allowances/physical.py` freezes a versioned exact-model standard-price catalog, active routing identity and engineering envelope on a fresh dispatch hold in its original transaction. Monetary accounting uses integer USD nanodollars. Unknown model identities, routing changes, unsupported cache/service tiers and unqualified server-priced tools refuse. Haiku 5.5 requests above 100K conservative input refuse regardless of enlarged per-call environment settings. Telemetry cannot authorize or discount a call. There is no actual-usage credit path; UNKNOWN retains the full input/output reservation.

Immediately before each blocking or streaming SDK entry, after D1 and ordinary pre-attempt observers, one conditional PostgreSQL UPDATE checks owner, original hold, active RESERVED state, expiry, snapshot identity, attempt ceiling and every cumulative component. It increments the whole vector and commits before SDK entry. Concurrent processes arbitrate on the row; process restarts and new request contexts cannot refill it. If commit acknowledgement is lost, no provider call is admitted and any committed debit remains conservative. A crash after admission is potentially billed. Terminal or historical NULL evidence refuses instead of bootstrapping a new budget.

Frozen payload counting includes repeated system/tool schemas, expanded conversation history, tool results, inline vision payloads and each worker’s actual maximum output. Parallel menu workers inherit the same action and D1 authority. A sticky typed refusal prevents queued local workers, success publication and later canonical writes even when legacy helpers catch the provider exception. Already committed domain receipts retain the original ledger settlement semantics.

Native Training retains its existing OperationAuthority, lease, operation row, binding and recovery ladder. The original Boolean `provider_attempt_possible` marker and vector debit now commit atomically, with operation → hold lock order and no nested User lock. The obsolete redundant marker observer is removed from that path. A failed debit rolls back both. Replay of canonical success and stored terminal failure creates no SDK call; expired/recovered operations cannot replenish the same hold. The historical 20-attempt mocked 429 witness is stopped at four SDK entries, including recovery and fallback.

Coach summary work executes inside the admitted turn when unified enforcement is enabled, so a deferred job cannot acquire a new unmetered budget after settlement. This can add synchronous latency and requires real-client latency evaluation before launch. Standalone exempt workout proof/lookup authorities are preserved; nested work cannot use an exemption to escape a Coach vector.

## Failure and customer experience

Exhaustion produces `AI_ACTION_BUDGET_EXHAUSTED`, HTTP 422, nonretryable for that operation, with “This AI request could not be completed. Try a simpler request.” It does not publish a partial AI result as success. Original native stored-failure/replay semantics remain. Known zero physical attempts release a visible hold as confirmed unbilled without consuming a billed-release allowance; tests include a failing ordinary pre-SDK observer with `max_billed_releases=0`. Any ambiguous admitted attempt stays UNKNOWN and follows the existing finite failed-release/consume policy. No policy values are installed here.

[QUALITY.md](QUALITY.md) describes actual synthetic ordinary/heavy payloads, useful output allocations, repairs, fallback, exhaustion and remaining semantic/latency evidence gaps. Fake SDK success proves application completion and budget fit for those fixtures; it does not measure live-model answer quality, real completion rate or customer acceptance. Missing D1 mapping/consent still refuses. Composite QR testing uses an explicit synthetic authority and does not open the unqualified production transport. Remote PDF/image OCR and unapproved comparison/replacement mappings remain closed.

## Migration and compatibility

`c6f8b0d2e4a1` expands parent `b5e7a9c1d3f0` with five nullable fields, no defaults or backfill. It validates existing field width/type/nullability/default/identity before DDL and supports repeat upgrade. Downgrade intentionally retains financial evidence. The original ledger migration’s exact later-column allowlist recognizes these fields for create_all-first bootstrap; no schema mismatch waiver is added. SQLite malformed-schema and PostgreSQL history/reentry/width checks accompany the source. Historical in-flight NULL snapshots fail closed; a future rollout must drain or retire old work before enabling admission. Mixed old/new workers must never serve an activated policy.

## Qualification and release

Deterministic vectors cover exact limits and one-over refusal, owner/model/payload tampering, no retrospective authorization, transient retry/UNKNOWN accounting, swallowed errors, nested tool/summary work and customer response handling. PostgreSQL covers five-family concurrent resource races, terminal settlement, restarted contexts, native recovery, original D1/entitlement fences and migration compatibility. Existing CI also runs Linux image/lock/schema and repository-wide regression authorities. Final commit, hosted exact-HEAD receipts and independent source review are attached to the PR and external qualification evidence; a source report alone is not a PASS receipt.

[RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) preserves the explicit source/deploy/paid-launch gates. Financially, the enforced tail is finite but still much larger than subscription proceeds. Expected current-routing Monthly/Annual 30% margins are approximately 36.36% / 33.18%, conditional on inherited usage assumptions. Full five/six-week mean-mix AI cost is $5.6082 / $6.7298. Keep the 20-Coach offer as requested and expose that conflict for decision; no price or allowance reduction, routing activation or cache assumption is used.
