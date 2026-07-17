# PR #155 Required Fixes Design

**Goal:** Implement all fourteen actionable findings from `docs/TRIAGE_FIXES.md` as sequential, independently verified fixes without changing unrelated product behavior.

**Baseline:** Work starts from merged `origin/main` at `03e6b36` on `codex/pr-155-required-fixes`. PR #155 contains no review comments or unresolved threads; its merged audit document is the source of requirements.

## Delivery structure

Each finding receives a focused red-green-refactor cycle, targeted regression verification, and a short commit before work begins on the next finding. Findings 1 and 2 share one production change because decoupling provider inference from SSE delivery fixes both the re-entrant semaphore deadlock and model-slot starvation; they still receive separate assertions.

The audit order is preserved. No GitHub threads will be replied to or resolved because PR #155 has none. The final branch will receive affected-suite verification, the complete test suite, `git diff --check`, and a review of every commit relative to `origin/main`.

## 1–2. Decouple streaming inference from delivery

`ai_stream._stream_bedrock` will no longer hold `model_concurrency_slot()` across tool dispatch or client-controlled generator suspension. Each Bedrock turn will use a small producer that reads the provider stream under the model slot and places deltas plus the final message into an unbounded in-process queue. The queue is naturally bounded by the configured per-turn model token limit, so a slow client cannot block provider completion while retaining the slot.

The SSE consumer will yield queued deltas immediately. Tool dispatch happens only after the producer reports the final message and has released the model slot. A gym-photo or nutrition-search tool may therefore acquire its own model slot without re-entering an outer semaphore. If the client disconnects, provider reading may finish the already-started bounded turn, but it cannot block on queue capacity or retain a slot indefinitely.

Tests will prove that a one-slot configuration can execute a nested model-using tool without deadlock and that delaying consumption of queued deltas does not keep the model slot occupied after provider inference completes.

## 3. Treat partial stream failures as interruptions

Streaming error events will carry a server-internal `work_performed` signal when text was emitted or a tool ran, plus the partial text when available. The pipeline will persist non-empty partial text with `interrupted=True`. The route will keep the quota consumed and leave the consecutive-failure counter unchanged for these interruption events.

Errors before any delta or tool remain clean failures: no memory turn is recorded, the failure counter increments, and the reserved quota is refunded. Provider exception details remain server-only and never enter SSE payloads.

## 4. Sanitize AI meal totals

A dedicated `sanitize_meal_total_macros` helper will protect multi-item meal totals without applying the stricter single-serving ceilings. It will coerce non-finite or negative values to zero, proportionally scale totals above a generous 10,000 kcal ceiling, cap individual macro totals consistently, and lower declared calories when the macros cannot support them under the existing Atwater hard-tolerance rule.

The AI-estimate branch of `/meal-log` will call this helper before constructing `MealLog`. Existing serving-level paths continue using `clamp_serving_macros`. Tests will cover negatives, non-finite values, an Atwater-only violation, and an excessive but parseable AI total.

## 5. Validate weight consistently

A shared parser in `tracking.py` will accept only finite numeric weights in the inclusive range 20–500 kg. `/log`, `/checkin`, and `/update-weight` will use it before creating rows or updating the user profile. Missing, non-numeric, non-finite, and out-of-range inputs return translated 400 responses and perform no writes.

Focused tests will cover every entry point and confirm that an invalid weight cannot reach activity-calorie calculations through `current_user.weight`.

## 6. Include the complete summary note in the token budget

`build_context_window` will construct the full summary note before selecting recent messages and seed `used` with the note's complete estimated cost, including the header. If the note alone reaches the budget, it will be truncated to fit and no recent message will be forced in. Message selection will continue newest-first and preserve the existing adjacent-role merge behavior.

Tests will use a boundary budget where the summary text alone previously appeared to fit but the header caused an overrun.

## 7. Preserve reservation-week guards during stream refunds

The quota service will expose the captured reservation week and allow `refund_ai_quota` to receive it explicitly. `/ask/stream` will resolve that value before returning its generator and pass it through `_refund_chat_quota` to the freshly loaded user row.

A week-rollover regression test will reserve in week A, move the stored quota to week B, attempt a stream refund with week A, and verify that week B is not decremented.

## 8. Add request idempotency to meal writers

`MealLog` will gain a nullable `idempotency_key` plus a user-scoped unique constraint. The migration is additive and compatible with existing rows because null keys remain allowed. `/meal-log`, `/api/quick-add-meal`, and `/api/food/barcode/add` will accept the standard `Idempotency-Key` header, validate a bounded token, and return the existing row for a repeated key before performing AI calls or inserts.

The header remains optional for backward compatibility. Maintained frontend call sites will generate a stable UUID per user action and reuse it for that request, so double submission and transport retries converge on one ledger row. Unique-constraint races will roll back and re-query the existing user-scoped row rather than returning 500.

Tests will cover sequential duplicates, a simulated concurrent uniqueness race, user isolation, and avoidance of a second AI call.

## 9. Batch leaderboard rebuilds

`lb_rebuild` will iterate users in deterministic primary-key batches and execute Redis pipeline writes per batch instead of materializing all users and all pending Redis commands. Existing source-of-truth and failure behavior remain unchanged: PostgreSQL is authoritative, and Redis errors are swallowed.

Tests will use more than one batch and assert that both leaderboard sets contain every user with the current composite score.

## 10. Derive diary dates server-side

`diary_create_meal` will always use the Istanbul day key from `app.timeutil`; client `date_key` values will no longer select arbitrary past or future rows. This matches the current UI and the later diary-to-`MealLog` path, which already logs under today.

A regression test will submit a foreign date and confirm that the created or returned meal is keyed to the server day.

## 11. Finalize blocking replies once

`ai_coach._run_coach_conversation` will stop calling `finalize_reply`. It will use `is_coach_error_fallback` only to decide whether the legacy session-cookie history may include the raw result. `ai_pipeline.generate_answer` remains the sole blocking finalization point and continues moderation, memory persistence, and fallback classification.

Tests will instrument the formatter to prove one call and verify unchanged fallback-history behavior.

## 12. Emit blocking AI metrics

`_emit_metrics` will accept an explicit mode. Streaming callers pass `stream`; `generate_answer` emits `blocking` success or fallback metrics and emits a blocking error metric before re-raising unexpected exceptions. Token metrics remain stream-only until the blocking provider interface exposes usage.

Tests will verify the metric name and mode for blocking success, fallback, and exception paths.

## 13. Compare exact CSRF origins

The CSRF guard will normalize and compare `(scheme, hostname, effective_port)` from the request and the `Origin` or fallback `Referer`. `Origin: null`, malformed values, scheme mismatches, and port mismatches will be rejected. `ProxyFix` already supplies the production scheme, host, and port from trusted nginx forwarding headers, so same-origin production requests retain their current behavior.

Tests will cover null, HTTP/HTTPS mismatch, explicit port mismatch, default-port normalization, and valid same-origin requests with synchronizer tokens.

## 14. Explicitly allow deep-health source networks

Deep health will allow loopback addresses automatically and non-loopback addresses only when they belong to `DEEP_HEALTH_TRUSTED_CIDRS`. The default allowlist will contain the currently documented Docker bridge gateway as a single-host CIDR, not an entire RFC1918 range. `.env.example` and deployment documentation will explain how to set the actual bridge gateway when Docker uses a different subnet.

Tests will prove loopback and the configured gateway work while other private addresses, public addresses, malformed addresses, and spoofed forwarding chains receive only the shallow health response.

## Error handling and compatibility

- All database lookups introduced by these fixes remain scoped to the authenticated user.
- The idempotency migration is expand-only and safe for automatic boot upgrades.
- Provider exception text, request tokens, and idempotency keys are not logged.
- Existing translated UI copy is preserved except for one new weight-range error key.
- Redis remains optional; idempotency correctness relies on PostgreSQL, not Redis.
- No existing public route or response field is removed.

## Verification

Every finding begins with a focused regression test that fails for the expected reason. After the minimal fix, the focused file or subsystem suite must pass before committing and proceeding. After all findings, run the combined affected suites, the complete non-load pytest suite, migration graph/schema checks, `git diff --check origin/main...HEAD`, and inspect the commit list and working-tree status.
