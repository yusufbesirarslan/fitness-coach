# MON-BE-PREMIUM-ALLOWANCE-ENFORCEMENT-01 — architecture

Qualified and fetched main: `e6af3067cec6ad623e16c57bae0141aa9d6ad91d`.
At recovery, origin/main equaled the qualified baseline. The work is isolated on
`feat/mon-be-premium-allowance-enforcement-01`; no other worktree was modified.
Open-PR/worktree inventory and ownership are retained in the external task
directory's `evidence/` and `OWNERSHIP.md`. No concurrent allowance implementation
PR was open. Shared D1, Training, nutrition and monetization hunks were bounded
to the seams described below. Mobile StoreKit/convergence files are untouched.

| Component | Reused authority | Change / remaining boundary |
|---|---|---|
| `ai_allowances/policy.py`, `service.py`, `errors.py` | Owner locks, immutable existing grants, buckets, reserve/finalize, expiry, retention, typed catalogue | Same tables and balances; generic dispatch evidence lives on the existing reservation |
| `subscriptions.entitlement` | Verified Apple environment/account ownership, expiry, freshness | Both new dispatch and unified-mode native Training resolve after the owner lock; pending/unavailable never falls back to Starter |
| Native Training `allowance.py`, `store.py` | Original operation, same-transaction binding/dispatch marker, provider-attempt evidence, recovery/retirement/compensation | Preserved; adapter carries the operation scope to the provider backstop |
| `ai_allowances/execution.py` | Original ledger and D1 OperationAuthority | New common logical scope, keyed semantic digest, committed pre-SDK evidence, bounded physical fan-out, protected response marker |
| Pump analysis / comparison | Existing durable command and analysis lease | Reservation commits with lease. Pair identity prevents distinct comparison keys authorizing duplicate inference |
| Coach / web generators / nutrition / menu | Existing shared domain services | One outer action; internal retries, tool rounds, repair and joined menu workers share it |
| `ai_provider_call.py` | Existing spend guard, input budgets, retry ladder and D1 dispatch | Unbound/unclassified paid work refuses in unified mode; evidence commits before each SDK entry |
| D1 `ai_consent.py`, `consent_publication.py`, consent blueprint | Original operation/generation, canonical-write fencing, closed route registry, Gunicorn 26 deadline/socket contract | Registry and transport unchanged. No consent disclosure or Coach activation supplied by this work |
| `GET /api/v1/ai/allowances` | Existing v1 read projection/schema | No balance mutation or alternate endpoint/schema |
| `premium.py` | Legacy behavior while new gate OFF | Unified mode stops legacy Boolean quota admission from granting unlimited Premium; provider backstop is mandatory |

The state machine remains AUTHORIZE → RESERVE → EXECUTE → FINALIZE. A generic
fresh reservation is its durable dispatch marker. A retained key never returns
execution authority, even after a zero-attempt release. Domain-backed Training,
Pump and comparison retain their existing result-recovery authorities. Generic
Coach/web results have no new result store: terminal replay returns an existing
409 rather than executing again. Replay protection is bounded by the existing
90-day terminal retention; clients must never reuse an old key as a new action.

The additive migration `b5e7a9c1d3f0` adds four nullable columns to
`ai_usage_reservation`: fingerprint, physical attempt count/limit and canonical
commit receipt. Existing rows remain NULL. No backfill, grant, scheduler, new
ledger, default production policy or extension mapping is created. The older
foundation verifier recognizes these later-owned columns without changing its
DDL; the new migration verifies their exact shape. Downgrade preserves evidence.

Financial writes hold only short database transactions. No new account or bucket
lock spans provider I/O. D1 retains its existing separate-connection dispatch
serialization; the new pre-attempt update locks only the reservation row. Generic
settlement locks owner then reservation in one transaction, sealing evidence
against a late producer. A canonical action marks the same reservation in the
canonical writer's transaction, so a lost commit acknowledgement is not free.

Reusable but dark: all enforcement, entitlement and consent integrations. Missing
commercial policy: Starter cohort/cutoff/backfill/deletion rules; exact Premium
quantities and billed-release budget; comparison/replacement classification.
Missing launch qualification: the real client/transports and D1 disclosure release
remain separately gated. Web streaming deliberately refuses synchronously under
unified mode, preserving D1's unqualified-stream closure. Web menu acquisition
can perform OCR in a separate request: without an outer analysis scope that paid
OCR is refused, so a combined web operation is required before offering that flow.

Potential bypasses: legacy Boolean quotas are unlimited while unified enforcement
is OFF (preserved compatibility). With it ON, physical calls require a matching
logical family or an explicit documented exemption. Lost worker scopes fail
closed. `legacy_web_ai` is not arbitrarily classified. See the coverage matrix.

All-family readiness requires the ledger, native Training, Apple subscriptions
and complete five-family Premium policy together. There is no independent new
per-family flag. This prevents a partial metering rollout from leaving an
unmetered Premium family behind. It does not open a D1-closed route.
