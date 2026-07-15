# Conversation Memory (Sprint 4 WS1 + WS8)

Persistent AI-coach chat memory: history survives refresh, an old backlog is
compressed into a summary, and the rolling context window is bounded by a token
budget. Kill switch: `AI_MEMORY_ENABLED=0` reverts to the old client-history-only
behavior (nothing written to the DB).

## Models (`app/models.py`)

- **`CoachConversation`** — `id`, `user_id` (FK, indexed), `started_at`,
  `archived_at` (nullable — active = `NULL`), `summary` (Text), `summary_tokens`,
  `summarized_upto_id` (last folded message id). One active conversation per user,
  enforced in code with a row lock (`get_or_create_active_conversation`).
- **`CoachMessage`** — `id`, `conversation_id` (FK, indexed), `role`
  (`user`/`assistant`), `content` (Text), `token_estimate`, `prompt_tokens`,
  `completion_tokens` (nullable — real provider usage), `created_at`.

Migration `cc33dd44ee55` is **re-runnable** (`has_table` guard) because the fresh
DB boot path runs `create_all()` first, then stamps + upgrades.

## Rolling context window

`build_context_window(conversation, budget=AI_CONTEXT_TOKEN_BUDGET)` assembles
`[summary-as-system-note] + recent messages`, walking newest→oldest until the
`estimate_tokens` budget (default 3000) is full. Older messages fall out of the
window but are **never deleted** — they remain in the DB and may already be
captured in the summary.

## Summarization

When the unsummarized backlog exceeds `AI_SUMMARY_TRIGGER_TOKENS` (default 6000),
the oldest turns are folded into `conversation.summary` via one light LLM call and
`summarized_upto_id` advances. It is **idempotent** (threshold + upto guard):
re-running or racing never double-summarizes.

- **WS1 (PR2):** ran inline at the start of the triggering request.
- **WS8 (PR5):** moved to a background job. `ai_pipeline._memory_stage` calls
  `jobs.enqueue_or_run(summarize_conversation, conversation.id)` — enqueued to the
  RQ worker when one is available (async, off the request path), else executed
  inline (the old synchronous behavior). Either way, a failure never breaks the
  chat: the summarize step swallows its own errors and the context window is still
  built. See [DEPLOYMENT.md](DEPLOYMENT.md) for the worker.

## Endpoints (`app/blueprints/coach.py`)

- `POST /ask` and `POST /ask/stream` persist each real turn (B16: error-fallback
  replies are **not** persisted, consistent with the quota refund).
- `GET /coach/history` — recent messages of the active conversation (widget
  hydrates from this on open; server is the source of truth).
- `POST /coach/conversation/reset` — archives the active conversation (messages
  kept; the next question opens a fresh one).

Every memory query is scoped to `current_user.id`.

## Failure resilience

Each step of the memory layer is fault-tolerant: if it crashes (e.g. transient DB
error), the pipeline rolls back and falls to the old client-history path — the
conversation never breaks, it just loses persistence for that turn.
