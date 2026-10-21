# AI model routing: Sonnet 5.5 + Haiku 5.5 on Bedrock (MON-AI-ROUTING-5.5-MIGRATION-01)

Status: **implemented dark**. `AI_MODEL_ROUTING` defaults to `legacy`, so a
merge and deploy of this change serves exactly what `0c84616` serves. Nothing
here was activated anywhere, and no live Bedrock call was made.

## 1. Baseline (main `0c84616`)

| Path | Model | Transport | Spend class |
|---|---|---|---|
| Coach tool loop (blocking + stream), coach feedback, check-in feedback | Sonnet 4.5 `global.` | Bedrock InvokeModel (`AnthropicBedrock`) | heavy |
| Training plan generation/repair, nutrition plan generation, menu extraction | Sonnet 4.5 `global.` (`_heavy_chat`) | Bedrock | heavy |
| Pump Check analysis/compare, gym-photo and menu image validation | Sonnet 4.5 `global.` | Bedrock | heavy |
| Heavy fallback (any non-refusal Bedrock failure), coach fallback loop | gpt-4o-mini | OpenAI direct | light |
| Food translation/macros/servings, meal review, conversation summary | gpt-4o-mini (`_openai_chat`) | OpenAI direct | light |
| Menu image / scanned-PDF OCR | gpt-4o-mini vision | OpenAI direct | light |
| Deep-health probe | Sonnet 4.5 `global.`, 1 output token | Bedrock stream | none |

D1 (`ai_consent`) has no published disclosure: every AI family is closed on
main, which this change does not alter.

## 2. PR #342 disposition

PR #342 (`refactor/light-ai-to-haiku45-eu`, head `6702957`, opened 2026-09-24,
DRAFT) moved light inference to **Haiku 4.5 EU** and removed OpenAI. It was
blocked on a 10 RPM `L-CCA5DF70` quota, and it is 41 files and many main
commits stale. **Recommendation: close as superseded, not merged.** What was
carried over:

- An explicit exact-id policy where the spend class is a property of the model
  id, never of the transport label or a substring (`ai_model_policy.SPECS`).
- Unregistered ids fail closed before charge or invocation (`UnknownModelPolicy`).
- Its parity finding that Haiku handled the light golden set (31/31 parse) is a
  reason to qualify Haiku 5.5, not evidence about it.

What was not carried over: Haiku 4.5 (Haiku 5.5 is cheaper at $0.10/$0.50 vs
$1/$5 and newer), removing OpenAI from the codebase (legacy stays the rollback
path), and #342's spend-ceiling changes (main already has 100/500 light ceilings).

## 3. Routing matrix (`AI_MODEL_ROUTING=claude55`)

| Task class (`feature`) | Model | Effort | Thinking | Fallback on non-refusal failure |
|---|---|---|---|---|
| `coach` (tool loop, stream, feedback) | Sonnet 5.5 | low | `between_tools` | Haiku 5.5 tool loop (only before any delta / tool side effect, the existing B-rule) |
| `training_plan` | Sonnet 5.5 | medium | `between_tools` | Haiku 5.5 |
| `nutrition_plan` | Sonnet 5.5 | medium | `between_tools` | Haiku 5.5 |
| `menu_extract` (heavy text) | Sonnet 5.5 | low | `between_tools` | Haiku 5.5 |
| `vision` (Pump Check, photo/menu validation) | Sonnet 5.5 | medium | `between_tools` | none (unchanged: vision never fell back) |
| `nutrition` (translate/macros/servings/review) | Haiku 5.5 | low | `disabled` | none |
| `summary` | Haiku 5.5 | low | `disabled` | none |
| `menu_ocr` | Haiku 5.5 vision | low | `disabled` | none |
| `health_probe` | Sonnet 5.5, 1 token | low | `between_tools` | none |

Deterministic/non-AI paths are untouched. The client never selects a model:
the model is a server constant from config, and `admit()` refuses any id that
is not the configured, registered one.

Why thinking is off: every per-call output budget (coach 700, vision 1200,
nutrition plan 2000) was sized for Sonnet 4.5, which ran without thinking, and
thinking tokens count against `max_tokens`. `between_tools` is Sonnet 5.5's
lowest setting (`disabled` is a 400 on it); Haiku 5.5 accepts `disabled` at
effort <= high. Every effort above is <= high. Adaptive thinking per route is a
separate change, to be justified by the qualification run.

Profiles (Bedrock model cards, re-read 2026-10-21): eu-central-1 has **no
in-Region** option for either model, only `eu.` (EU geo: Frankfurt, Stockholm,
Milan, Spain, Ireland, Paris) or `global.`. Both are registered;
`BEDROCK_SONNET55_MODEL` / `BEDROCK_HAIKU55_MODEL` have no default, so the
destination is an explicit operator decision (LEGAL-V2 D-11). Sonnet 4.5
production uses `global.` today.

## 4. Compatibility decisions

| Change on 5.5 | Where handled | Behaviour |
|---|---|---|
| Non-default `temperature`/`top_p`/`top_k` → 400 | `ai_model_policy.prepare` (inside `admit`) | stripped from every 5.5 payload; legacy untouched |
| `thinking: disabled` → 400 on Sonnet 5.5 | `prepare` | `between_tools` (Sonnet) / `disabled` (Haiku) |
| Effort recalibrated / Haiku default `medium` | `prepare` | explicit `output_config.effort` per task class |
| Assistant prefill → 400 | `prepare` | refused before spend (`UnsupportedRequestShape`); no call site prefills today |
| Forced `tool_choice` any/tool → 400 (Bedrock: auto/none only) | `prepare` | refused before spend; coach uses `auto` |
| Thinking/progress blocks in `content` | all readers select by `type`; coach loops append `resp.content` unchanged | test replays a `thinking` block with its signature |
| `refusal` stop reason (Sonnet: 5 categories; Haiku: no server fallback) | `_claude_chat`, vision helpers, coach loop, stream, OCR | an answer, not a failure: no retry, no fallback model; category exported from an allowlist |
| Truncation (`max_tokens`) | unchanged | existing `truncated` handling |
| Tokenizer (~30% more tokens on Haiku 5.5 vs 4.5; Sonnet 5.x tokenizer) | `ai_input_budget` counts UTF-8 bytes | byte bound still holds for any byte-level BPE; `bound_violation` stays the alarm |
| High-res vision (up to 2576 px, ~4,784 tokens/image) | `vision_images.fit_standard_envelope` + model-aware bound | 5.5 images downscaled to the Sonnet 4.5 envelope (<= 1568 px, <= 1.15 MP) so the model sees what 4.5 saw at the same image tokens; an image the bound cannot verify inside the envelope is counted at 4,784 |
| Streaming / cancellation | unchanged | `text_stream` ignores thinking deltas; producer cancellation unchanged; stream consent stays closed |
| Client SDK | `anthropic[bedrock]==0.120.2` | supports `output_config`; `between_tools` sent as a plain dict |
| Prompt caching | unchanged, `BEDROCK_PROMPT_CACHE=0` | see §7 |

Not changed on purpose: per-call output budgets, spend ceilings, D-22
allowance quantities, rate limits, the 30 s per-attempt transport cap.

## 5. Consent and spend preservation

- D1 is not touched. `dispatch` still checks `(recipient, payload.model)`
  against the disclosure's exact recipients, so a grant naming Sonnet 4.5 does
  **not** authorize Sonnet 5.5 or Haiku 5.5 (test
  `test_consent_grants_are_bound_to_the_exact_model`). A model change needs a
  new reviewed disclosure; nothing is inherited.
- The heavy→Haiku fallback is a fresh `admit()` with its own consent check,
  input budget and light-class charge. `heavy_primary_route()` (used by metered
  preflight) names Sonnet 5.5 under claude55.
- Spend refusals, consent denials, refusals and unregistered models never
  fall back. Under claude55 there is no OpenAI recipient at all.

## 6. Cost instrumentation

`[AI-USAGE]` schema 3 (additive): exact 5.5 identities
(`claude-sonnet-5-5`, `claude-haiku-5-5`), price per (model, billing profile),
`stop_reason`, `refusal_category`, `routing`. Prices are from the eu-central-1
price list (publication 2026-10-12, sha256 `ca50edc0…`):

| Model | Global in/out | EU geo in/out | Cache write 5m / read (global) |
|---|---|---|---|
| Sonnet 5.5 | $2.00 / $10.00 | $2.20 / $11.00 | $2.50 / $0.10 |
| Haiku 5.5 (<= 100K prompt) | $0.10 / $0.50 | $0.11 / $0.55 | $0.125 / $0.01 |
| Sonnet 4.5 (unchanged) | $3.00 / $15.00 | unpriced (as before) | $3.75 / $0.30 |

Haiku prompts over 100K tokens (long-context card) and any unregistered id stay
unpriced. Per-attempt events, admission ids, fallback flags (now also on the
Haiku fallback attempt) and the spend guard's one-unit-per-physical-attempt
rule are unchanged.

## 7. Prompt caching (evaluated, kept OFF)

Eligibility: Bedrock 5.5 minimum checkpoint is 512 tokens (Sonnet 4.5: 1,024);
the coach system+tools prefix (~5.6K tokens) qualifies; 4 checkpoints; 5 m and
1 h TTL. Break-even with 5 m writes (1.25x input) and reads (0.1x): caching is
cheaper only when reads/writes > 0.28, i.e. a prefix hit rate above ~22%.
Multi-round tool turns hit on every round after the first; single coach turns
from a sparse user base mostly write (+25% on the prefix). Caches are
model-scoped, so a Haiku fallback cannot reuse a Sonnet cache. Accounting
already splits cache write/read; 1 h writes (Sonnet 5.5 $4.00) are not used
by any call site. Decision: keep `BEDROCK_PROMPT_CACHE=0` until staging shows
an observed hit rate above break-even; it is independent of this migration.

## 8. Modeled cost (not observed)

MON-COST-01 token profiles (sha-pinned), re-priced. Ratio of AI COGS vs today:

| Routing | Token factor | Ratio (all profiles) | F5 typical premium / month |
|---|---|---|---|
| Current (Sonnet 4.5 global + gpt-4o-mini) | 1.00 | 1.000 | $3.88 |
| claude55 global | 1.00 | 0.667 | $2.59 |
| claude55 global | 1.30 | 0.867 | $3.37 |
| claude55 EU geo | 1.00 | 0.734 | $2.85 |
| claude55 EU geo | 1.30 | 0.954 | $3.70 |

The saving comes from Sonnet 5.5's lower price on heavy work. Light work moves
from gpt-4o-mini to Haiku 5.5, which costs less per input token and slightly
less per output token, but light work is a small share of the total. The token
factor is unmeasured for AxisAI prompts; 1.30 is the documented Haiku 5.5 vs 4.5
tokenizer change used as a pessimistic case. This is **not** the Minerva
"architecture C" 0.5685 multiplier (Haiku for coach + caching), which is not
what this change routes. Model and checks:
`~/axisai-mon-ai-routing-55-migration-01/model/routing55_cost.py`.

## 9. Qualification

Done (mocked SDK, this PR): `tests/test_ai_model_routing_55.py` covers config
validation, legacy byte identity, the 5.5 request surface, spend class, OpenAI
exclusion, unregistered ids, prefill/forced-tool refusal, Haiku fallback with
telemetry, refusal without fallback, spend refusal without fallback, the
vision/thinking-block readers, the image envelope and bound, Haiku OCR, coach
thinking-block replay, coach fallback, coach refusal, the D1 model binding and
real D1 denial, consent preflight, health probe, stream, and pricing. **None of
this is live Bedrock evidence.**

Not done, blocking activation:

1. Effective quotas for `eu.`/`global.` Sonnet 5.5 and Haiku 5.5 InvokeModel
   (RPM, TPM, daily tokens) in eu-central-1. The founder's quota confirmation
   does not say which model or profile; the operator role cannot read Service
   Quotas.
2. Live comparison: `python -m scripts.ai_routing55_qualification` (dry run by
   default; `--live --confirm-synthetic-staging` refuses outside
   `axisai-staging`/eu-central-1; 14 calls, worst case $0.18). It records
   pass/fail, tokens, latency, stop reason and cost per fixture for Sonnet 4.5
   vs the 5.5 model, which also measures the real token factor.
3. Latency against the 30 s per-attempt cap for training plan output
   (2–6K tokens).
4. Staging soak on the app path once a staging-only D1 disclosure exists
   (today every family is closed, so the app path cannot dispatch).

## 10. Rollback

Set `AI_MODEL_ROUTING=legacy` (or remove it) and restart. The two 5.5 id
variables are then ignored. No schema change, migration or stored state
depends on the routing, so rollback is instantaneous and lossless. An invalid
value stops the boot (the deploy health gate rolls back).

## 11. Activation dependencies

Staging: AWS sign-in; Bedrock model access for both models in the staging
account; quota evidence (item 1); `bedrock:InvokeModel` /
`InvokeModelWithResponseStream` on both inference profiles in the instance
role; profile choice; the live harness run; a staging-only disclosure if the
app path is to be soaked.

Production (separate authorization): the same, plus the LEGAL-V2 D-11
destination decision, a reviewed D1 disclosure naming the exact 5.5 model ids,
a deploy, and the env change.
