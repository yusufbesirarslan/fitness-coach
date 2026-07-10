# PR #134 Required Fixes Design

**Goal:** Implement all fifteen actionable findings from `FIXES_NEEDED.md` as independently tested, reviewable fixes without changing unrelated product behavior.

**Baseline:** The work starts from merged `origin/main` at `f576000`. The clean suite contains 1,127 passing tests. Existing deprecation warnings are baseline noise and are not part of this scope.

## Delivery structure

The work stays on `codex/pr-134-fixes`. Each numbered finding is a separate red-green-refactor cycle and a separate short commit. The order follows the audit's risk ranking: data integrity first, then availability and cost controls, then failure handling, security hardening, performance, and cleanup.

No schema migration is required. Existing database columns and JSON metadata are sufficient for the atomic claims, quota reservations, wearable state, and deferred referral state.

## 1. Atomic diary logging

`diary_log_meal` will keep its ownership, already-logged, and non-empty checks. Immediately before creating the canonical `MealLog`, it will claim the `CustomMeal` with a conditional update scoped by `id`, `user_id`, and `is_logged=False`. A zero-row update returns the existing translated 400 response. The claim and `MealLog` insert commit in the same transaction, so a failed insert also rolls back the claim.

The regression test will demonstrate that the conditional claim succeeds once and prevents a second ledger row.

## 2 and 11. Model-level concurrency and fail-fast route admission

The route gate and model-call gate serve different purposes and will use separate semaphores:

- The existing route gate reserves Gunicorn capacity. Its default wait becomes zero seconds, so excess requests receive 503 immediately instead of occupying the remaining worker threads.
- A new model-call semaphore bounds actual `_heavy_chat` provider activity across internal fan-out. It is acquired around the complete Bedrock-to-OpenAI fallback sequence so fallback does not temporarily double the counted concurrency.
- Direct heavy Bedrock image validation will use the same model slot, ensuring pump-check calls also respect the provider ceiling.

`AI_MODEL_MAX_CONCURRENCY` will default to the existing `AI_MAX_CONCURRENCY` value and remain independently configurable. Tests will prove fail-fast admission, slot release on exceptions, and that concurrent provider calls never exceed the model ceiling.

## 3. Write-rate limits

The limiter will receive a conservative default of `600 per hour`, configurable through `DEFAULT_RATELIMIT`. Explicit user-or-IP keyed limits will protect the highest-abuse social writes:

- friend requests: `20 per hour`;
- chat sends: `60 per minute; 600 per hour`;
- suggestions: `30 per hour`.

These values will be named config constants so operations can adjust them without changing route code. Existing stricter AI, scrape, authentication, and search limits remain layered on top.

Tests will inspect registered limits and exercise at least one social endpoint with the limiter enabled.

## 4. Atomic freemium quota reservation

Quota checks will become reservations. A shared helper will lock the user's row, read fresh metadata through a column query, reject when the limit is reached, or increment and commit before expensive work begins. A paired refund helper will lock and decrement only the reserved counter.

The plan decorator will reserve before calling the route and refund on every non-200 response or raised exception. The coach route will reserve after input validation and refund when the model returns the known fallback or when processing fails. Premium users and disabled quota flags remain no-ops.

This intentionally accepts the small crash window where a process can terminate after reservation and before refund; eliminating that window would require a durable reservation table and recovery job, which is outside the audit's requested scope.

Tests will simulate two stale user objects attempting the final allowance and verify only one reservation succeeds, plus successful consumption and failure refunds.

## 5. Wearable reauthentication state

`get_wearable_connection` will catch Fernet `InvalidToken` while decrypting either token. It will mark the connection `reauth_required`, commit that state, and return `None`. The status endpoint will expose the stored status while preserving the existing `connected` boolean.

Malformed token data will therefore follow the normal not-connected path instead of producing an unhandled 500. Tests will cover corrupt access and refresh ciphertext and the status response.

## 6. Training-plan truncation retry

A partial seven-day program cannot be safely invented by structural JSON repair, so the service will retry once instead of accepting salvaged incomplete content. The first request remains at 4,000 tokens. If JSON parsing or plan validation fails, the second request will ask for compact JSON and use 7,000 tokens, still subject to the configured provider cap.

If the retry also fails, the existing translated generation error remains the outward behavior. Tests will prove a truncated first response can recover, a valid first response is not retried, and two invalid responses still fail cleanly.

## 7. Cognito ID-token verification

One canonical verifier will replace the two duplicate payload decoders. It will:

- download Cognito's pool-specific JWKS with a bounded timeout;
- cache the key set in-process and refresh once when a key ID is unknown;
- accept only RS256;
- validate signature, issuer, audience, expiration, token use `id`, and a non-empty subject;
- normalize the existing `sub`, `email`, `email_verified`, and `name` result.

The already-installed `joserfc` implementation supplied through pinned Authlib will perform JOSE verification. `cognito_idp.py` will become a compatibility re-export of `cognito_service.py`, preventing the implementations from diverging again.

Verification or JWKS failures will fail closed as `CognitoServiceError` without leaking provider internals. Tests will use a generated RSA key and local JWKS data; no test uses the network.

## 8 and 9. Canonical macro sanitation

`clamp_serving_macros` will become the single complete sanitation boundary. It will first coerce all four values to non-negative numbers. It will retain proportional scaling for absolute caps. When the remaining invalid reason is excessive declared calories relative to macro energy, it will reduce calories to the Atwater value `4P + 4C + 9F`; zero macros therefore produce zero calories.

This central fix protects quick-add, override, menu, coach, and future callers consistently. Route-level regression tests will still cover the two reported negative-input paths, and pipeline tests will cover the Atwater-only invalid case.

## 10. Batched serving-weight estimation

FatSecret search processing will parse candidates first, collect unique per-serving food names, and call `_estimate_serving_weights_llm` once. The returned estimates and fallback set will then be reused while constructing each result. Duplicate names will share one estimate, result order remains the FatSecret order, and cache confidence rules remain unchanged.

Tests will assert one estimator call for multiple per-serving results and unchanged scaling and fallback-cache behavior.

## 12. Deterministic Redis leaderboard ties

Redis scores remain unchanged to avoid a data migration. The application will request scores with the global candidates, include all members tied at the top-N cutoff, and sort by descending score then numeric ascending user ID before slicing.

For a user outside the displayed list, rank will be calculated as the count of strictly higher scores plus equal-score users with a smaller numeric ID. Friends ranking already uses this rule and will share the helper where practical.

The fake Redis test implementation will model real Redis's reverse-lexicographic tie ordering so the regression would fail under the old code.

## 13. Complete test-user purge

The explicit purge will delete `PumpCheckLike` and `PumpCheckComment` before `PumpCheck`, then delete wearable sleep, activity, workout, and connection rows before the user. This remains useful defense in depth even though the current SQLite connection hook enables foreign-key cascades.

The CLI test will create every newly covered child type and verify no rows remain.

## 14. Referral reward after Cognito verification

Cognito registration will store the submitted referral code as `pending_referral_code` in the new user's metadata and will not call `consume_referral`. The verification route will confirm Cognito first, load the local user, consume the pending code through the existing atomic referral claim, remove the pending metadata key, and return whether a referral was awarded.

Local non-Cognito registration remains immediate. Invalid codes are cleared after successful verification so they are not retried indefinitely. Tests will verify no pre-confirmation XP, one post-confirmation award, and idempotent duplicate handling.

## 15. WHOOP query allow-list

The proxy will define resource-specific allowed parameters. Profile and body accept none; recovery, sleep, and workout accept `start`, `end`, and `limit`. Unknown parameters are dropped. `limit` must be an integer from 1 through 25, and start/end values are length-bounded before forwarding.

Tests will capture adapter arguments and prove arbitrary paging or injected keys never leave the application.

## Error handling and observability

Existing translated user responses remain intact unless the audit explicitly requires new state. Concurrency saturation continues to return 503 with `Retry-After`; quota exhaustion continues to return 402; malformed write inputs return 400; wearable corruption becomes disconnected/reauth-required; Cognito verification fails closed; training generation retains the existing user-facing failure after its retry.

New exceptional paths will log concise operational context without tokens, JWT bodies, referral codes, or upstream secrets.

## Verification

Every finding will follow test-driven development: add one focused failing test, run it to confirm the expected failure, implement the smallest fix, and rerun the focused file before continuing. After all fifteen fixes, run the affected test files together, then the complete `python -m pytest -q` suite. Compare the final result with the 1,127-test baseline and inspect `git diff --check` and the branch diff before completion.
