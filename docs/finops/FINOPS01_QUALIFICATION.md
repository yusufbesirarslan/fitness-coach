# FINOPS-01 implementation qualification

Base: 2a437ef350b275744ef7c417709a1a01b8d66a2d; branch finops/01-ai-unit-economics. No deploy, merge, paid inference, configuration, IAM, resource, retention, budget or production mutation performed.

See [AI_UNIT_ECONOMICS](../AI_UNIT_ECONOMICS.md) for the authoritative discovery, historical boundary, all caller categories, subject/environment/model semantics, token equations, finite taxonomy, production reconciliation and limitations. [Evidence manifest](evidence/source-manifest.json) records sanitized source export hashes; individual subject/request identifiers are not committed.

## Proven instrumentation gaps and minimal fixes

- Response-usage extraction occurred outside the never-raise emission boundary. Synthetic malformed extraction could fail an otherwise successful call: red test reproduced it; extraction and sink now remain non-authoritative.
- Successful responses without usage had no explicit best available estimate; now input upper bound / unknown output is marked estimated.
- Exact model/profile identity prevents normalization of unknown future model generations into a priced old class; no routing/model changes.
- Nutrition/menu executor and deferred summary request correlation was lost at thread boundaries; existing server identity now propagates. RQ correlation reuses safe existing job UUIDs; database owner attribution already existed and is unchanged.
- Explicit fallback boolean and admission UUID qualify legacy ambiguity and export overlap; UUID generation failure cannot block serving. Schema 2 is additive with legacy prefix compatibility.
- Deterministic offline tooling separates reported/estimated tokens, complete/partial economics and list estimates/billed attribution. All unexplained account usage remains UNATTRIBUTED.

No telemetry change is asserted to explain the unidentified production account caller. Local fixes do not alter historical logs or production.

## Local qualification

Original focused base: 134 passed. Final serving/guard/budget/stream/coach/worker focused suite: 489 passed (before seven subsequently added sentinel/UUID tests); final FINOPS-specific suite: 65 passed. Tests cover both providers, charged retries, permanent errors/timeouts, successful and abandoned streams, tool second round, real provider fallback, health probe, owner-loaded RQ worker, legitimate null owner, input/guard refusals, privacy, finite feature/model/profile identities, cache arithmetic, sink/extraction failure and invalid offline billing mapping. Exact expected event counts are pinned. All new functional defects and independent accounting-review defects were reproduced red before fixes.

Independent read-only review: no remaining Critical/Important serving, privacy or offline accounting findings; production sources and published price correctness were outside that reviewer scope. Main price table unchanged; settled Sonnet price arithmetic matches billing.

Full monolithic local regression: interrupted by Python 3.14 MemoryError while formatting a failure, not claimed green. Separate reproduction across five affected files: 388 passed, six failures and four errors. The same failure set reproduces against a separate detached checkout of the recorded base (qualification-only, no existing feature worktree used). Environmental categories: Windows default-codepage UnicodeDecodeError; Windows slash semantics; Windows 32,767-character environment-variable limit for large pytest parameter IDs; localhost handling in the real worker; local urllib3 private API mismatch. These unrelated modules and tests are unchanged. CI uses the repository's authoritative Python 3.11/Linux sharded suite, PostgreSQL checks and platform-specific gates; final CI status belongs to the PR checks, not this static report.

No intentional test exclusion or CI workflow modification. No new custom metrics. Existing warnings (datetime.utcnow and related library deprecations) are unchanged.

## Behavior contract

Provider selection/order, prompt/context/tool schemas, caps/budgets, cache payloads, spend charges, retries/timeouts, concurrency, fallback decisions, quotas, responses and exception handling are preserved. Fake SDK snapshots compare identical payload/transport timeout/output/charge with telemetry enabled versus extraction/sink failure; stream/retry/fallback regressions exercise the existing behavior. No cache activation or economics tuning. No new database schema. New code is not production-validated.

## Remaining evidence limits

Settled account token residual: 42,064 input / 2,612 output ($0.0606364) in Haiku billing class, originating caller unproven. Longer provisional residual: 304,621 metric input / 130,356 metric output and $2.7639574; metric-only 10/32 tokens are not silently timing-adjusted. Completion-time telemetry differs from AWS invocation/billing timestamp authority. Missing sink delivery cannot be made exactly-once without a different architecture; no such architecture replacement attempted. Tiny active-user sample, absent capability activity and legacy fallback absence limit economics. Current operational cache flag is expected/default OFF but not independently re-read from .env; observed cache tokens zero.

FINOPS-02 recurring infrastructure savings remain unestablished. FINOPS-01 improves observability qualification and exposes financial residuals; it does not unblock speculative savings.
