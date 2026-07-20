# Sprint 6 PR4 AI Coach AdaptivePlan Integration Design

Date: 2026-07-20  
Status: Approved for implementation planning  
Scope: The first production runtime consumer of the canonical `AdaptivePlan`

## Goal

Expose the deterministic adaptive-training recommendation to both AI Coach provider
paths as a small, versioned, read-only context contract. Preserve existing coach
behavior byte-for-byte while `AI_ADAPTIVE_PLAN_CONTEXT` is disabled, isolate all
enabled-path failures, and leave every training heuristic and unrelated runtime reader
unchanged.

## Existing Architecture

The current training stack is intentionally one-way:

```text
training_history
      |
      v
training_progression
      |
      v
training_planning
      |
      v
adaptive_plan_context / context_builder
      |
      v
AI Coach pipeline and providers
```

- `training_history` is the canonical ORM-backed, user-scoped reader. It owns
  Istanbul-day boundaries and completion-marker semantics.
- `training_progression` reads that foundation once and emits the normalized,
  deterministic `ProgressionReport` and its single canonical `next_signal`.
- `training_planning` maps only `next_signal` into the immutable `AdaptivePlan`.
  It is the sole source of overload, maintenance, deload, volume, intensity, and
  reason-code decisions.
- `context_builder.fetch_coach_context` is the shared context boundary used by
  `ai_pipeline` for blocking and streaming requests. The provider-specific OpenAI
  and Bedrock builders receive the same completed context string.

PR4 adds only the integration boundary above `training_planning`. It does not alter
any lower layer.

## Rollout and Rollback Contract

Add `AI_ADAPTIVE_PLAN_CONTEXT`, parsed with the repository's existing boolean
environment convention and defaulting to `0`/OFF. Copy it into Flask configuration
so tests and operations can change it explicitly.

The guarantee is behavioral, not tied to a particular import technique. When OFF:

- no `AdaptivePlan` is constructed;
- no planner code executes;
- no adaptive serialization occurs;
- no planner-related log is emitted;
- no placeholder or adaptive context is allocated or appended;
- no prompt content, ordering, whitespace, or provider payload changes.

The flag is the only rollout gate. Turning it OFF must restore the pre-PR4 runtime
behavior without reverting code. The implementation may choose the least costly safe
import strategy as long as the behavioral guarantees above remain true.

When ON, `context_builder.fetch_coach_context` invokes the adapter once and inserts
its completed block immediately after the existing `[ANTRENMAN GEÇMİŞİ (7 gün)]`
section. The adaptive block is independent of the legacy seven-day history query: a
failure or omission in either section does not suppress the other.

## Serializer Ownership and Purity

Create `app/services/adaptive_plan_context.py`. It is the only repository component
permitted to transform `AdaptivePlan` into prompt-ready serialized data. Alternative
serializers are forbidden. Future prompt consumers import this adapter; future
non-prompt consumers may consume `AdaptivePlan` directly.

Serialization is a pure transformation. It must not mutate `AdaptivePlan`, its
embedded `ProgressionReport`, a `TrainingHistorySummary`, or any contained sequence.
It must not query the database, read the clock, inspect Flask state, or derive a
planning decision.

## Version 1 Canonical Serialization Contract

The serializer emits compact UTF-8-compatible JSON with:

- `ensure_ascii=False`;
- `separators=(",", ":")`;
- no indentation or trailing newline;
- explicit dictionary construction in the canonical order below;
- JSON lowercase `true`/`false` booleans;
- JSON numbers for `weeks` and `volume_delta_pct`;
- `reason_codes` converted to a JSON array without sorting, preserving canonical
  planner order;
- every Version 1 field present on every output;
- no `null` values and no conditional omission rules.

Exact canonical field order:

1. `schema_version`
2. `source`
3. `plan`
   1. `weeks`
   2. `has_data`
   3. `week_focus`
   4. `volume_action`
   5. `intensity_action`
   6. `volume_delta_pct`
   7. `overload_ready`
   8. `maintenance_recommended`
   9. `reason_codes`
4. `progression`
   1. `volume_trend`
   2. `strength_trend`
   3. `is_progressing`
   4. `is_plateau`
   5. `deload_due`
   6. `load_consistency`
   7. `next_signal`

Canonical shape:

```json
{"schema_version":1,"source":"adaptive_plan","plan":{"weeks":4,"has_data":true,"week_focus":"overload","volume_action":"increase","intensity_action":"progress","volume_delta_pct":0.05,"overload_ready":true,"maintenance_recommended":false,"reason_codes":["progressing"]},"progression":{"volume_trend":"up","strength_trend":"up","is_progressing":true,"is_plateau":false,"deload_due":false,"load_consistency":"consistent","next_signal":"progressing"}}
```

### Stability and Forward Compatibility

Version 1 evolves additively only. Existing keys cannot be removed, renamed,
repurposed, reordered in canonical output, or given new semantics. New fields are
appended after existing fields at the appropriate object level. A breaking change
requires a new `schema_version` and an intentional migration plan.

Consumers must:

- ignore unknown fields;
- tolerate appended fields;
- not depend on incidental in-memory mapping order beyond the documented canonical
  serialized contract;
- never infer semantics from an absent field;
- treat all Version 1 fields as present;
- use field names and values rather than reconstructing planner logic.

Golden characterization tests pin names, nesting, ordering, booleans, array order,
numeric representation, omission/null behavior, and exact bytes.

## Prompt Envelope and Read-Only Consumer Policy

The enabled context block has a fixed short envelope:

```text
[ADAPTIVE PLAN CONTRACT v1 - READ ONLY]
Use this canonical deterministic plan only for explanation, personalization, motivation, education, and natural-language presentation. Never recompute, reinterpret, or override its decisions.
<canonical compact JSON>
```

The AI Coach is a read-only consumer. It may explain, personalize, motivate, educate,
and present the plan naturally. It must never calculate overload or deload, infer a
plateau, change volume or intensity actions, reinterpret reason codes as a competing
decision, override the plan, or introduce an alternative planning algorithm.

The enabled block is assembled once in `context_builder`; provider-specific builders
must not add, transform, or independently serialize it. Therefore blocking/streaming
and OpenAI/Bedrock paths receive the same contract bytes.

## Minimal Payload and Token Budget Target

Only normalized, high-level deterministic data is serialized. The payload excludes:

- `WorkoutLog` rows and exercise history;
- weekly volume or strength series;
- historical tables;
- raw progression history;
- `TrainingHistorySummary`;
- duplicated lower-layer inputs;
- user profile or identifying data.

The design target is approximately 100-160 prompt tokens for the compact contract and
read-only envelope. This is not a strict runtime invariant. Future revisions should
remain in approximately the same budget unless a deliberate schema-version change
documents and tests the larger cost.

## Failure and Session-Recovery Contract

The enabled adapter catches `Exception`, never `BaseException`. Consequently expected
application, database, planner, and serialization failures cannot terminate coach
context construction, while `KeyboardInterrupt`, `SystemExit`, cancellation-like
process controls, and other process-level exceptions continue to propagate normally.

Plan construction and serialization are atomic from the caller's perspective: the
adapter returns either a complete canonical plan contract or a complete canonical
neutral contract. It never returns a partial plan, partial JSON, missing fields, or an
empty block.

If `build_adaptive_plan(user_id)` raises:

1. discard all partial state;
2. restore SQLAlchemy session usability before later context sections run if the
   failure left the session unusable, using the safest mechanism for the current
   transaction state;
3. use exactly `AdaptivePlan(weeks=0)` as the neutral plan;
4. serialize it through the same sole canonical serializer;
5. continue normal context construction.

The canonical neutral contract has all Version 1 keys. It uses `weeks=0`,
`has_data=false`, `week_focus="insufficient_data"`, both actions `"hold"`, delta
`0.0`, both recommendation flags `false`, `reason_codes=["insufficient_history"]`,
and the embedded neutral `ProgressionReport` fields.

An unexpected serialization failure is also isolated from coach availability. The
adapter falls back to a complete neutral serialization produced by the same canonical
serializer, never by a second formatting implementation.

## Observability Contract

There is no adaptive log activity while the flag is OFF. After the enabled gate has
been crossed, generic debug-level lifecycle events may record:

- planner enabled;
- planner construction succeeded;
- planner fallback used;
- serialization completed;
- optionally, an exception class name.

Logs must never include user identifiers, `AdaptivePlan` contents, workout history,
progression values, serialized JSON, SQL, stack traces, or exception messages. Logging
must not affect the returned contract or coach availability.

## Automated Architecture Enforcement

Add an automated architecture test that parses imports (or provides equivalent
executable validation) rather than relying on a manual repository grep. It must prove
that the lower packages `training_history`, `training_progression`, and
`training_planning` do not import:

- `adaptive_plan_context`;
- `context_builder`;
- `ai_coach` or the coach pipeline;
- provider-specific modules or clients.

It must also preserve the established one-way lower-layer relationships: history does
not import progression/planning, and progression does not import planning. The adapter
may import `training_planning`; `context_builder` may invoke the adapter; coach paths
may invoke `context_builder`. Reverse edges are forbidden.

## Test-First Strategy

### Phase 1: Pre-integration characterization

Before modifying `context_builder` or configuration, add tests that pass against the
current code and pin:

- exact coach-context bytes for deterministic mocked sections;
- exact section ordering and whitespace;
- exact OpenAI messages, roles, and content;
- exact Bedrock system payload with prompt caching both OFF and ON;
- the same unmodified context bytes embedded in both provider representations.

These tests characterize existing behavior without referring to a not-yet-existing
flag. They remain unchanged after integration and prove the OFF output is still the
baseline.

### Phase 2: Contract and integration TDD

Add failing tests before production implementation for:

- flag absent/default OFF and explicit OFF;
- no plan construction, execution, serialization, planner logs, or adaptive prompt
  content while OFF;
- exact OFF context and provider payload identity against Phase 1 goldens;
- exact enabled golden serialization and prompt block;
- stable keys, nesting, canonical order, JSON formatting, boolean/numeric forms,
  array order, complete fields, and absence of nulls;
- immutability of `AdaptivePlan`, `ProgressionReport`, and their sequences;
- repeated identical serialization and repeated identical enabled calls;
- overload, maintenance, deload, empty-history, and neutral semantics;
- correct `user_id` passed to the canonical planner and cross-user isolation;
- planner `Exception` fallback to the exact neutral golden contract;
- `BaseException` subclasses continuing to propagate;
- session usability restored when a simulated planner DB failure poisons it;
- remaining context sections continuing after planner fallback;
- generic enabled-only logging with no prohibited data;
- blocking/streaming and OpenAI/Bedrock provider parity;
- automated import direction and exclusive serializer ownership;
- runtime rollback: switching the flag OFF restores the baseline output.

### Phase 3: Regression verification

Run the new focused tests plus existing suites covering `ai_coach`, `ai_pipeline`,
`ai_stream`, `prompt_builder`, `training_history`, `training_progression`,
`training_planning`, training routes, and progress routes. Finish with the full suite
using the repository's documented Windows timeout expectations.

## Files in Scope

Expected production/configuration changes:

- `app/services/adaptive_plan_context.py` (new, sole adapter/serializer)
- `app/services/context_builder.py` (one enabled-only integration point)
- `app/config.py` (default-OFF flag)
- `.env.example` (rollout documentation)

Expected tests:

- a focused adaptive-plan context contract/integration test module;
- characterization additions around coach context and provider payloads;
- an automated architecture/import-boundary test.

Expected documentation:

- `docs/TRAINING_PLANNING.md`
- `docs/handoff.md`
- `CLAUDE.md`
- this design and its subsequent implementation plan.

## Explicit Non-Goals

PR4 makes no changes to:

- progression or planning heuristics;
- training-history behavior or readers;
- workout generation;
- tracking endpoints;
- MCP;
- analytics;
- UI or localized reason-code copy;
- schema or migrations;
- fatigue/recovery enrichment;
- later Sprint 6 runtime consumers.

## Future Consumer Rule

Every future runtime integration must either consume `AdaptivePlan` directly or use
the canonical serialized contract from `adaptive_plan_context.py`. Generators,
recovery/fatigue systems, nutrition planning, UI integrations, and other consumers
must not independently reconstruct progression signals, overload logic, plateau
detection, deload decisions, or planning precedence. `AdaptivePlan` remains the
single source of truth for adaptive training decisions throughout the codebase.

## Independent Merge Safety

PR4 is independently safe to merge because its only runtime behavior is behind one
default-OFF gate, the OFF path is characterization-pinned, enabled failures degrade to
one complete neutral contract, no schema or heuristics change, and disabling the flag
is an immediate code-free rollback.
