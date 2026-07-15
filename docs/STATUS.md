# FitX — Triage & Hardening Tracker (living document)

**This is the single canonical tracker.** Add new triage findings and their status
*here* — do **not** create new `TRIAGE_<date>.md` files at the repo root (that sprawl
is what this document replaces). The historical point-in-time reports were pruned on
2026-06-28 (all items resolved or captured below); they remain in git history if ever needed.

Roadmap detail for the in-flight workstream lives in
[`updates-plan-2026-06-28.md`](updates-plan-2026-06-28.md).

Last updated: 2026-07-15.

> **2026-07-15 — Sprint 4 PR 5 (final): WS8 background jobs + WS6 observability +
> WS12 docs + load tests.** **WS8** — an optional RQ worker. `app/jobs/`:
> `get_queue()` (None-safe), `enqueue_or_run(func, ...)` (enqueue when a
> worker/Redis exists, else run inline), a worker heartbeat, and dead-letter
> helpers (`flask rq-failed` / `rq-requeue`). `worker.py` is the entrypoint
> (`rq.Worker` on Linux, `SimpleWorker` on Windows/dev) with a heartbeat daemon
> thread; a `worker` compose service (same image, 512m, log-rotated) runs it.
> **The worker is optional** — without it (or without `rq`), jobs fall back to
> inline execution, so nothing breaks. First wired task: `summarize_conversation`
> (PR2's inline lazy-summarize now enqueues via `ai_pipeline._memory_stage`).
> `/health?deep=1` reports `worker: alive|down|unknown` as *informational* (not
> gating). **WS6** — `app/services/ai_metrics.py` emits CloudWatch `FitX/AI`
> metrics (AITurn, AIErrors, Prompt/Completion/TotalTokens, SummarizeJob), **default
> off** (`AI_METRICS_ENABLED=0`; needs `cloudwatch:PutMetricData`), total no-op when
> disabled/no boto3. Request tracing: a server-generated 16-hex `request_id` per
> request in the logfmt line, the `/ask/stream` SSE `meta` frame, and a Sentry tag.
> Real token usage was already recorded onto `CoachMessage` (PR2). **WS12** — six
> docs under `docs/` (AI_ARCHITECTURE, MEMORY, STREAMING, OBSERVABILITY,
> RATE_LIMITING, DEPLOYMENT). **Load tests** — `tests/load/` behind
> `@pytest.mark.load`, deselected by default (`pytest.ini addopts = -m "not load"`),
> run with `-m load`: model-slot concurrency bound, failure injection under
> concurrency, context-window token-overflow pruning. No migration
> (`CoachMessage` token columns already exist). `rq==2.10.0` pinned. Tests:
> `test_jobs.py`, `test_ai_metrics.py`, request-id in `test_observability.py`,
> worker field in `test_health.py`.

> **2026-07-15 — Sprint 4 PR 4: WS5 caching + WS9 error recovery + WS7 rate
> hardening.** Three defensive layers around the AI paths, all default-on and all
> degrading to no-ops without Redis. **WS5** `app/services/ai_cache.py` — a
> generic `cache_get`/`cache_set` helper (key `ai:cache:<feature>:v1:<sha256>`,
> JSON values, per-feature TTLs, foodcache-style swallow+log). Wired into the
> deterministic TR→EN food-name normalization (`ai_nutrition._normalize_food_
> query_en` + batch): identical lookups skip the OpenAI call. Only successful
> (non-empty) results are cached — the `""` graceful-degrade is never pinned.
> **WS9** `app/services/ai_recovery.py` — the recovery ladder for `_heavy_chat`:
> transient errors (new `TransientAIError`, a `RuntimeError` subclass raised by
> `_claude_chat`/`_openai_chat` for rate-limit/timeout/connection) trigger a
> bounded jittered retry; a permanent error skips retry and (for Bedrock) falls
> to OpenAI; if *both* providers fail, a stale-but-real **last-good** Redis value
> is served (keyed by content hash — two users share it only on byte-identical
> input, so no leak); otherwise the existing friendly `RuntimeError` surfaces. The
> Bedrock SDK `max_retries` dropped 2→1 (`BEDROCK_MAX_RETRIES`) so our layer's
> retry doesn't multiply. **WS7** — a burst cap `AI_BURST_RATELIMIT="5 per
> minute"` on `/ask*` (atop `AI_RATELIMIT`), plus a per-user failure cooldown:
> `AI_FAILURE_THRESHOLD` consecutive AI failures arm a Redis NX key for
> `AI_FAILURE_COOLDOWN_SECONDS`, during which `/ask*` returns 429+Retry-After
> *before* reserving quota (a cooling-down user spends no weekly right); the first
> success clears the streak. Tests: `test_ai_cache.py`, `test_ai_recovery.py`,
> recovery+last-good in `test_ai_routing.py`, cooldown/burst/failure-recording in
> `test_coach_routes.py`, cache-hit in `test_ai_nutrition_llm.py`.

> **2026-07-15 — Sprint 4 PR 3: WS2 streaming + WS10 frontend.** The coach now
> streams token-by-token. New `POST /ask/stream` (SSE: `meta → delta* →
> done | error`) sits beside the untouched blocking `/ask`; both share the same
> gates, quota, and pipeline. Backend: `app/services/ai_stream.py` runs a real
> Bedrock streaming tool loop (`bedrock_client.messages.stream()`), and
> `ai_pipeline.stream_answer` wraps it — persisting the turn on `done` (B16: not
> error-fallbacks) and, if the client disconnects mid-stream (`GeneratorExit`),
> saving the partial answer flagged `interrupted=True`. Provider fallback obeys
> the B-rule: Bedrock→OpenAI only *before* the first delta reaches the client and
> *before* any tool side effect; after that a mid-stream failure yields a friendly
> i18n `error` frame (provider exception text never leaks). A new
> `ai_stream_concurrency_gate` holds the AI slot until the response *closes*
> (`call_on_close`) — the normal gate releases on view return, which for a
> streamed response is before the first token, so it would never limit streams.
> Frontend (`static/coach_widget.js`): fetch POST + `ReadableStream` SSE reader
> (EventSource can't send `X-CSRFToken`), `AbortController`-backed Stop button,
> Regenerate, typing indicator, rAF-throttled incremental append, markdown via
> marked + DOMPurify (jsdelivr pinned + SRI, CSP updated), and hydration from
> `GET /coach/history` on open. Kill switch unchanged (`AI_MEMORY_ENABLED=0`
> degrades streaming to the client-history path). Tests: `test_ai_stream.py`
> (provider loop + pipeline), streaming route SSE + quota-refund cases in
> `test_coach_routes.py`, gate hold/release in `test_ai_gate.py`.

> **2026-07-14 — Sprint 4 PR 2: WS1 conversation memory.** The coach now has
> persistent memory: `CoachConversation` + `CoachMessage` (migration
> `cc33dd44ee55`, expand-only), a rolling context window built newest-first to
> `AI_CONTEXT_TOKEN_BUDGET` (default 3000), and lazy summarization that folds
> older turns into `conversation.summary` once the unsummarized backlog exceeds
> `AI_SUMMARY_TRIGGER_TOKENS` (6000) — messages are never deleted, only pruned
> out of the window. `GET /coach/history` survives a browser refresh;
> `POST /coach/conversation/reset` **archives** (never deletes) the active
> conversation. Every memory step is failure-tolerant: if it raises, the request
> rolls back and falls through to the legacy client-history path, so a memory
> outage cannot break chat. Error-fallback replies are not persisted (B16 —
> consistent with the quota refund). Kill switch: `AI_MEMORY_ENABLED=0`.
> Note for future migrations: fresh-DB boot runs `create_all()` *then* replays
> everything after `aa11bb22cc33`, so any new table-creating migration must be
> re-runnable (`has_table` gate — see `cc33dd44ee55`).

> **2026-07-14 — Sprint 4 started (AI Coach Platform & Performance).** PR 1
> lands WS3 (AI Response Pipeline) + WS4 (Prompt Engineering Layer) as a pure
> refactor — zero behavior change, existing tests pass unchanged. New stage
> modules: `app/services/{ai_pipeline,context_builder,memory_manager,
> prompt_builder,response_formatter,moderation}.py`; all LLM prompt templates
> now live in `app/prompts/` (system/goals/nutrition/workout/progress).
> `ai_coach.py` keeps the tool ecosystem + provider loops and re-exports moved
> symbols under their old names. Remaining Sprint 4 workstreams (conversation
> memory, streaming, caching, error recovery, rate limits, background jobs,
> observability, docs) land in follow-up PRs.

> **2026-07-13 — root tracker sprawl consolidated.** Six competing trackers had
> reappeared at the repo root (`FIXES.md`, `FIXES_NEEDED.md`, `NEEDED_FIXES.md`,
> `TRIAGE_2026-07-05.md`, `TRIAGE_2026-07-11.md`, `TRIAGE_FINDINGS.md`) — exactly
> what the paragraph above forbids. They are archived under
> [`docs/archive/`](archive/) with a "not current" banner. This mattered beyond
> tidiness: `TRIAGE_FINDINGS.md` still presented the Cognito **auth bypass (S1)**
> and **unverified JWT signature (S3)** as open HIGH findings even though both
> were fixed in Sprint 2 — during an incident that sends the reader down the
> wrong path.

---

## Sprint 1–3 production audit (2026-07-13)

Full pre-Sprint-4 audit. No Critical issues and no exploitable auth bypass were
found; the gaps were **operational**. Recurring theme: the app handled its own
failures gracefully but treated *transient infrastructure failures as permanent
user-facing failures*, and could not tell you when its most critical path had
silently died.

| # | Finding | Status | Where |
|---|---------|--------|-------|
| H1 | Transient Cognito/JWKS failure permanently destroyed sessions (correlated mass logout on one throttle) | ✅ Fixed | `session_store.SessionTransient`, `auth_middleware._service_unavailable` → 503 + `Retry-After`, session preserved |
| H2 | Register race orphaned the account permanently; the documented recovery path (`get_or_create_user`) had been deleted | ✅ Fixed | `auth._reconcile_local_user` — links/creates from **verified** claims |
| H3 | `main` unprotected and deploy not gated on CI (deploy raced CI) | ✅ Fixed (deploy gate) / ⚠ manual (branch protection) | `deploy.yml` `workflow_run` on CI success |
| H4 | Cognito pool not in IaC; `PreventUserExistenceErrors`/MFA/auth-flows unenforced | ✅ Non-blocking drift check | `scripts/check_cognito_pool.py`, run each deploy |
| H5 | Auth-email delivery failed silently with **zero** alerting; real KMS decrypt path never ran in CI | ✅ Fixed | SNS + metric filter + 3 alarms in `template.yaml`; real `_decrypt_code` now tested |
| M1 | Session purge depended on an unversioned host cron | ✅ Fixed | daily NX-locked purge in `hooks.maybe_weekly_rollover` |
| M2 | No Docker log rotation → disk exhaustion takes down everything | ✅ Fixed | `logging:` caps on both compose services |
| M3 | `/health?deep=1` public: posture disclosure + outbound amplification | ✅ Fixed | loopback/private-only gate |
| M4 | Plaintext `.env` on host (RDS creds, token key, API keys) | ⚠ Hardened, SSM deferred | deploy enforces `chmod 600`; see below |
| M5 | Rollback reverts code but not migrations | ✅ Mitigated | non-blocking pre-deploy RDS snapshot |
| L1 | nginx sent `Connection: upgrade` on every request | ✅ Fixed | `map $http_upgrade $connection_upgrade` |
| L2 | Repo `nginx.conf` drifted from prod and is never deployed | ✅ Fixed | correct `server_name`; labelled a source template (certbot owns the live file) |
| L3 | `_secret_hash(None)` → `TypeError` → 500 instead of 401 | ✅ Fixed | empty creds rejected pre-Cognito; `_secret_hash` null-safe |
| L4 | Two JWT validators, two JWKS caches (Authlib + joserfc) | ✅ Fixed | single joserfc validator; `cognito_service` delegates to `cognito_jwt` |
| L5 | Stale comments (deleted `get_or_create_user`; Lambda timeout 5s vs 20s) | ✅ Fixed | — |
| D1–D8 | Docs described previous sprints' behavior as current | ✅ Fixed | `cognito.md`, `auth-emails.md`, `CLAUDE.md`, this file |

### M4 — secrets: accepted state and the next step

`docker-compose.yml` loads `.env` via `env_file`, so `SECRET_KEY`, the RDS
credentials, `COGNITO_TOKEN_ENC_KEY`, `RESEND_API_KEY` and `OPENAI_API_KEY` live
in a plaintext file on the host and are visible via `docker inspect` /
`/proc/<pid>/environ`. Deploy now enforces `chmod 600` on that file, but that is
hardening, not a fix.

This is inconsistent with the rest of the project, which already uses an **EC2
instance profile** for S3 and Bedrock precisely so no long-lived keys are stored.
The identity-provider and database secrets simply never got the same treatment.

**Rotation coupling worth knowing before an incident:** rotating `SECRET_KEY` or
`COGNITO_TOKEN_ENC_KEY` invalidates *every active session* (the first signs
session cookies; the second decrypts the stored Cognito tokens). So the incentive
to rotate after an exposure runs exactly backwards. Plan the rotation during a
low-traffic window and expect all users to be logged out.

**Next step (not done):** move these to SSM Parameter Store (SecureString) and
fetch at boot using the existing instance profile. Requires `ssm:GetParameters`
+ `kms:Decrypt` on the EC2 instance role.

---

## ✅ Resolved (recent)

| Item | Summary | Shipped in |
|------|---------|-----------|
| H1 | Diary ingest clamps macros via `clamp_serving_macros` | PR #99 |
| H2 | `/ask` caps question length (400 on oversize) | PR #99 |
| M1 | Display dates routed through `timeutil` (Istanbul) | PR #99 |
| M2 | `meal_history` capped by days, not 50 rows | PR #99 |
| M3 | One activity row per day (no intensity double-count) | PR #99 |
| M4 | Premium-aware weekly `/ask` chat quota | PR #99 |
| M5 | Silent LLM/JSON `except` paths now log `warning`+`exc_info` | PR #99 |
| M6 | MCP FatSecret token cache: lock across fetch + payload validation | PR #99 |
| M7 | Account-enum tradeoff documented (Cognito-gated + rate-limited) | PR #99 |
| M9 | Dev flag evaluated once in `configure_app` | PR #99 |
| L1–L6 | NULL weight, TDEE-default guard, rejected-status hidden, print→logger, XFF→remote_addr | PR #99 |
| SEC1 | jsdelivr pinned + SRI; CSP narrowed to exact files | PR #100 |
| i18n CI gate | TR/EN key/placeholder parity test blocks PRs | PR #100 |
| A6 | `calculate_tdee` logs a warning on unknown activity (no longer silent) | already in tree |
| A5 | `_repair_truncated_json` handles mid-value truncation + validates output | PR #101 |
| D4 | Completion-marker `WorkoutLog` excluded from volume/count aggregation | PR #101 |
| I-M1 | Boot raw ALTER/UPDATE loop → Alembic chain (migration `f1a2b3c4d5e6`, inspector-guarded); schema-drift guard now **blocking**; `FITX_DB_AUTO_UPGRADE` gate added | PR #102 |
| D7 | `user_metadata` JSONB-only raw `ALTER` removed together with the boot loop (column already has migration `e5f6a7b8c9d0`) — **subsumed by I-M1** | PR #102 |

## 🔧 Open / backlog

| ID | Summary | Next action | Effort |
|----|---------|-------------|:------:|
| **D4-mcp** | MCP server (`fitx_mcp/server.py`) computes its own workout totals and does **not** yet exclude the completion marker — app-side fixed in PR #101; MCP parity still open | Mirror the `WORKOUT_COMPLETION_MARKER` filter in the MCP SQL | S |

## 🅾️ Accepted tradeoffs (won't fix — documented)

| ID | Why accepted |
|----|--------------|
| M7 | `UserNotConfirmedException` hint preserves the verify-redirect UX; Cognito-gated + rate-limited |
| M8 | Per-username lockout is a documented brute-force tradeoff |
| I-M2 | `drop_user_daily_nutrition` is a one-time, backfill-preceded data drop |
| L5 | Vision OCR output is bounded (4000 tok) + rate-limited; cost covered by H2/M4 |
| L7 | `style-src-attr 'unsafe-inline'` (dynamic bars) + GA wildcard host (Google's official CSP guidance) |

## 📁 History

The old point-in-time reports (`FIXES.md`, `TRIAGE.md`, `TRIAGE_FIXES.md`,
`TRIAGE_2026-06-23/24/26/28.md`, and the 2026-06-17 docs) were pruned on 2026-06-28
once all their items were resolved or captured above. They remain retrievable from git
history (`git log --all -- 'docs/archive/*'`). Treat **this file** as the current truth.
