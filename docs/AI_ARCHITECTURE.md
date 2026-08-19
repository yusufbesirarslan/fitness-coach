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
| `prompt_builder` | System prompt + language directive + Bedrock prompt-cache block; `adaptive_plan_context=` selects the AdaptivePlan-authority prompt (flag-driven, from `ai_coach`). See [TRAINING_PLANNING.md](TRAINING_PLANNING.md). |
| `ai_coach` | Provider tool loops (Bedrock/OpenAI), staging→commit, tools. Re-exports legacy names. |
| `coach_plan_tools` | The **only** route from the Coach to the training-plan mutation domain: six narrow tool schemas, a strict argument parser, the server-minted operation key, `actor`, the per-turn write budget and the bounded result vocabulary. Default OFF. See [ADAPTIVE_COACHING.md](ADAPTIVE_COACHING.md) §§19-30. |
| `ai_stream` | Real Bedrock streaming tool loop (`messages.stream()`). See [STREAMING.md](STREAMING.md). |
| `ai` | `_heavy_chat` router (Bedrock→OpenAI) + recovery ladder. |
| `ai_recovery` | WS9: `TransientAIError` retry → last-good → friendly; failure cooldown. See [RATE_LIMITING.md](RATE_LIMITING.md). |
| `ai_cache` | WS5: generic Redis result cache (`cache_get`/`cache_set`). |
| `ai_metrics` | WS6: CloudWatch `FitX/AI` metrics (default off). See [OBSERVABILITY.md](OBSERVABILITY.md). |
| `moderation` | Input length gate + output hook. |
| `response_formatter` | Fallback detection, `COACH_FALLBACKS`. |
| `premium` | Weekly AI quota (free vs premium-unlimited). |
| `ai_gate` | Process-local concurrency semaphores (AI=4). |
| `training_generation` | Weekly training-plan generator. Preference allow-lists + capability matrix run **before** `_heavy_chat`. See [TRAINING_GENERATOR.md](TRAINING_GENERATOR.md). |

Prompt templates live in `app/prompts/` (pure strings, no logic). Background jobs
live in `app/jobs/` (see [DEPLOYMENT.md](DEPLOYMENT.md)).

## Tools

Tool definitions are provider-neutral dicts built once and adapted per provider
(`_to_openai_tool` / `_to_anthropic_tool`), so the two schemas cannot drift.

**Always available (8).** `fetch_nutrition_and_stage_log`,
`confirm_and_commit_meal_log`, `stage_workout_log`, `confirm_and_commit_workout_log`,
`cancel_pending_log`, `manage_user_memory`, `query_fitx_metrics`,
`analyze_gym_photo`.

**Plan mutation (6, flag-gated).** `replace_training_plan_exercise`,
`add_training_plan_exercise`, `remove_training_plan_exercise`,
`update_training_plan_prescription`, `move_training_plan_day`,
`undo_last_training_plan_change` — published **and** executable only when
`AI_COACH_PLAN_MUTATION_TOOLS_ENABLED` is on (default OFF, `staging_only`). These
are the first tools in the app whose effect is a durable write to plan data, so
they do not go through `_dispatch_coach_tool`'s ordinary path: they are handed to
`coach_plan_tools.execute_plan_tool`, which owns the flag check, the strict
parser, the operation key, `actor="ai_coach"`, the two-writes-per-turn budget and
the bounded result payload. Malformed tool-call JSON is refused for these tools
rather than defaulted to `{}` — an argument-less undo must never be the fallback
for an unreadable argument blob. Rules the model is given live in one place
(`app/prompts/system.py::PLAN_MUTATION_POLICY`) and are emitted only when the
flag is on. Full design: [ADAPTIVE_COACHING.md](ADAPTIVE_COACHING.md) §§19-30.

## Providers

- **Primary heavy path:** Bedrock / Claude Sonnet 4.5 via the AnthropicBedrock SDK
  (`BEDROCK_ENABLED=1`). Credentials come from the EC2 IAM instance profile — no
  keys in code or `.env`.
- **Fallback / light path:** OpenAI `gpt-4o-mini`.
- **Provider-switch rule (B-rule):** Bedrock→OpenAI only *before* the first delta
  reaches the client *and* before any tool side effect. See [STREAMING.md](STREAMING.md).
  Since PR3 this also guards a durable plan write: once a plan tool has run the
  turn degrades to a soft error instead of replaying against a plan that has
  already changed, and the mutation stays committed.

## Cross-cutting invariants

- All queries scoped to `current_user.id` with ownership checks.
- Turkish UI, English code; user-facing strings via `app/i18n.py` with TR/EN parity.
- Token estimate is `len(text)//4`; real usage read from provider `usage` and
  recorded onto `CoachMessage.prompt_tokens`/`completion_tokens`.
- Every operational layer (cache, queue, metrics, cooldown) degrades to a no-op
  without Redis/boto3 — the app never hard-depends on them.
