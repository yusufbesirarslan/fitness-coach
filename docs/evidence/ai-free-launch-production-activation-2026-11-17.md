# AI free launch: production activation record (2026-11-17)

This file is a record, not a runbook. Nothing in this repository activates a flag
(ROLLOUT.md §0). The production host `.env` change and the runtime-role IAM change
happen outside the repository, under separate founder GOs. This commit only gives the
next protected blue/green deployment a reviewed revision that changes no code. That
deployment runs the full R6 adult protected sequence (docs/R6_ADULT_CUTOVER.md): a
temporary interruption, registration closing and reopening, and forward-only behaviour
after closure. It starts the new slot web and worker from the updated host `.env`.

## Why

Production 6b3a7712 keeps the five native AI features closed at admission:
`ai_launch.require_runtime` needs `AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED`, and the
legacy routing selects a heavy model that no published D1 disclosure names. A read-only
production readback (2026-11-17 12:42Z) showed `AI_MODEL_ROUTING` and all three
allowance flags unset in the host `.env`, the blue slot web and the worker.

## Scope

- Seven host `.env` lines, appended once:
  - `AI_MODEL_ROUTING=claude55`, `BEDROCK_SONNET55_MODEL=eu.anthropic.claude-sonnet-5-5`,
    `BEDROCK_HAIKU55_MODEL=eu.anthropic.claude-haiku-5-5`, `BEDROCK_REGION=eu-central-1`.
    These are exactly the two recipients every launch D1 disclosure names. Under
    `claude55` the light route is Bedrock Haiku 5.5, so no request goes to OpenAI.
  - `AI_ALLOWANCE_LEDGER_ENABLED=1`, `AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED=1`,
    `AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED=1`.
- The allowance policy is source-only (`launch_policy.LAUNCH_V1_POLICY`, unchanged).
  `launch-free-v1` grants 30/2/2/20/5 (Coach/Training/Nutrition/Pump Check/QR Menu)
  per 30-day window from 2026-11-25 00:00 Europe/Istanbul. Before that instant the
  once-per-account Starter scope (10/1/1/1/1) applies.
- These stay unchanged:
  - `APPLE_SUBSCRIPTIONS_ENABLED` and every other Apple flag stay off.
    `execution.require_ready` admits Apple off only under the complete campaign policy.
    There are no purchases, subscriptions or ads.
  - `ACCOUNT_ELIGIBILITY_ENABLED=1` and `FITX_WORKOUT_SESSIONS_ENABLED=1`.
  - `FITX_TRAINING_EXECUTION_CONTEXT_ENABLED` and `FITX_TRAINING_INSIGHTS_ENABLED` stay unset.
  - `BEDROCK_ENABLED=1` and `AI_SPEND_GUARD_ENABLED=1` stay as they are. The spend
    ceilings stay at the source defaults.
  - The teen pins stay `None`.
- The runtime role needs invoke permission on the two EU inference profiles before
  this deployment. Without it, the candidate's Bedrock deep-health check would fail
  after closure. A read-only check on 2026-11-17 found that the role already carries
  an inline policy, `AxisAIProdClaude55EuInvoke`, added on 2026-11-08. It allows only
  the two EU 5.5 profiles and their 12 EU destination models, each model bound to its
  own profile, with no wildcard. Simulation allows all 28 required decisions and
  denies the direct-model, global-profile and US-profile controls.

## Rollback

1. Emergency stop with no deployment: delete that inline policy from the runtime
   role, after saving its document. Every provider attempt is then refused, and no
   OpenAI fallback exists under `claude55`. The next deployment must carry the
   configuration rollback below, or the policy must be restored first.
2. Configuration rollback: remove the seven `.env` lines with the reviewed block
   writer (rollback mode). Then run the next protected deployment, which brings the
   service back to today's closed admission.

Ledger rows are retained. Migrations are expand-only and are not rolled back.
