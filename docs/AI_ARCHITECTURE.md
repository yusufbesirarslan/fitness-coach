# AI Architecture (Sprint 4)

The AI coach is a modular pipeline. Each stage is failure-isolated: a stage that
crashes degrades gracefully (falls back) rather than breaking the whole reply.

## Request flow

```
User message
  → moderation.validate_question        input gate (deterministic, non-model)
  → memory_manager                       WS1: active conversation + rolling
                                          context window + lazy/queued summarize
                                          (failure → client-history fallback)
  → context_builder.fetch_coach_context  per-turn ORM/FatSecret context blocks
  → provider:
      generate_answer → ai_coach._run_coach_conversation   (blocking /ask)
      stream_answer   → ai_stream.stream_coach_answer       (SSE /ask/stream)
  → response_formatter.finalize_reply    error-fallback decision + friendly text
  → moderation.moderate_reply            output gate (extension point)
  → memory_manager.record_turn           persist REAL turns only (B16: no
                                          error-fallbacks)
```

`ai_pipeline.py` is the canonical orchestrator; `generate_answer` (blocking) and
`stream_answer` (SSE generator) are its two entrypoints. The `/ask` and
`/ask/stream` routes call these; the quota reserve/refund decision stays in the
route (driven by the `is_error_fallback` flag).

## Module map (`app/services/`)

| Module | Responsibility |
|---|---|
| `ai_pipeline` | Orchestrator; `generate_answer` / `stream_answer`. |
| `context_builder` | Per-turn coach context (ORM reads, FRIEND_DATA fence). |
| `memory_manager` | Persistent memory: conversation window, `estimate_tokens`, summarize, `record_turn`. See [MEMORY.md](MEMORY.md). |
| `prompt_builder` | System prompt + language directive + Bedrock prompt-cache block. |
| `ai_coach` | Provider tool loops (Bedrock/OpenAI), staging→commit, tools. Re-exports legacy names. |
| `ai_stream` | Real Bedrock streaming tool loop (`messages.stream()`). See [STREAMING.md](STREAMING.md). |
| `ai` | `_heavy_chat` router (Bedrock→OpenAI) + recovery ladder. |
| `ai_recovery` | WS9: `TransientAIError` retry → last-good → friendly; failure cooldown. See [RATE_LIMITING.md](RATE_LIMITING.md). |
| `ai_cache` | WS5: generic Redis result cache (`cache_get`/`cache_set`). |
| `ai_metrics` | WS6: CloudWatch `FitX/AI` metrics (default off). See [OBSERVABILITY.md](OBSERVABILITY.md). |
| `moderation` | Input length gate + output hook. |
| `response_formatter` | Fallback detection, `COACH_FALLBACKS`. |
| `premium` | Weekly AI quota (free vs premium-unlimited). |
| `ai_gate` | Process-local concurrency semaphores (AI=4). |

Prompt templates live in `app/prompts/` (pure strings, no logic). Background jobs
live in `app/jobs/` (see [DEPLOYMENT.md](DEPLOYMENT.md)).

## Providers

- **Primary heavy path:** Bedrock / Claude Sonnet 4.5 via the AnthropicBedrock SDK
  (`BEDROCK_ENABLED=1`). Credentials come from the EC2 IAM instance profile — no
  keys in code or `.env`.
- **Fallback / light path:** OpenAI `gpt-4o-mini`.
- **Provider-switch rule (B-rule):** Bedrock→OpenAI only *before* the first delta
  reaches the client *and* before any tool side effect. See [STREAMING.md](STREAMING.md).

## Cross-cutting invariants

- All queries scoped to `current_user.id` with ownership checks.
- Turkish UI, English code; user-facing strings via `app/i18n.py` with TR/EN parity.
- Token estimate is `len(text)//4`; real usage read from provider `usage` and
  recorded onto `CoachMessage.prompt_tokens`/`completion_tokens`.
- Every operational layer (cache, queue, metrics, cooldown) degrades to a no-op
  without Redis/boto3 — the app never hard-depends on them.
