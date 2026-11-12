# TR 13–17 onboarding V1 (source-pinned, closed by default)

Founder decision: minimum age 13, with full onboarding in 1.0.0. Everything below runs only when
`account_eligibility.APPROVED_TR_MINOR_NOTICE_VERSION == TR_MINOR_NOTICE_VERSION`. The pin is
`None` in source. Unpinning returns every TR minor record to `review_required`.

## Journey (native)

| Step | Behaviour | Authority |
|---|---|---|
| Age 12 (any market) | `ineligible` / `UNDER_MINIMUM_AGE`, no ticket | `evaluate` |
| TR 13–17 declaration | `screen_passed`, `13_17`, wire capabilities all false | `evaluate` |
| Sign-up | Existing sign-up screen. The minor ticks the separate notice box. The register body carries `minor_notice_version`, which is bound into the signed record | `_ticket_record` |
| F01 | The minor's **own** explicit grant of the unchanged F01 text. The notice is not consent and grants nothing | `health_data` capability (adult or TR minor) + `health_consent.mutate` |
| Profile | `PUT /api/v1/account/profile` (same wire shape). TR minor branch: age 13–17, goal `build_muscle` only, no `target_weight_kg`. No BMR/TDEE/calorie target or nutrition plan text is computed or stored; those stay NULL, which readers treat as "no target", never 0 | `general_profile` + `account_profile` teen branch |
| First plan | Non-AI general plan `POST /api/v1/training/plans/general` (days, equipment, cardio preferences). Needs no adult capability | unchanged |
| TR 18+ | Unchanged adult flow, including weight targets | unchanged |

## Still closed for TR 13–17

- **AI Coach, Nutrition Plan, Pump Check, QR Menu:** `ai_service_eligible` admits adult TR only for these.
- **AI Training plan:** closed until its own pin (below).
- **Automated weight targets:** check-ins, weight log, web profile edit and web coach chat.
- **Body photos:** Pump Check and photo completion.
- **Purchases:** `purchase_eligible` admits adults only.
- **Social:** closed by the code constant.

## Public ranking guard

`app/services/public_ranking.py` defines one predicate, `age IS NULL OR age >= 18`. It applies at:

- Redis publication: snapshots, with eviction of minor entries
- the PostgreSQL leaderboard fallback
- Redis reads
- challenge boards
- weekly winners

It covers every account with a minority age signal, not only TR. A minor still sees their own XP, without a rank.

## AI Training first plan (second pin, closed)

`account_eligibility.APPROVED_TR_MINOR_TRAINING_AI_VERSION` (`None` in source) opens **only** the
`training_plan` feature for a live TR 13–17 record. Every other D1 purpose stays adult-only.

- **AI service eligibility is separate from purchase eligibility.** `ai_launch.require_cohort(user, feature)` asks
  `account_eligibility.ai_service_eligible`. `purchase_eligible` and the subscription path stay adult TR only.
- **Allocation** (`ai_launch.check_allocation(user, family)`) maps only the `training_generation` ledger family.
  A Starter grant without a named feature stays adult-only. The teen uses the same finite Starter/Launch Free
  allowance, idempotency, D1 fence, revoke and erasure as adults. No new provider route.
- **Consent:** the minor's own F01 grant, then the same reviewed D1 Training text an adult sees (R1/R2). No guardian event.
- **Youth boundary** (`training_generation/youth_safety.py`, any profile age < 18). Adults are byte-identical.
  - Fat-loss focus and the powerlifting style are refused before any claim or hold (existing `TRAINING_PLAN_UNSUPPORTED_CONFIGURATION`).
  - One fixed directive goes into the system and user prompt: technique first, ≤4 sets, ≥6 reps, RPE ≤7, and no 1RM, failure, diet, supplement or appearance goals.
  - The output is `SemanticInvalidError` when sets, reps or RPE fall outside those bounds, or when a note names maximal, failure or calorie-deficit work.
- **Opening needs, in order:**
  1. founder adoption of the counsel position;
  2. the F01 owner's Training source installed (`POLICIES`, `LAUNCH_VERSIONS`, cohort and F01 pins);
  3. this pin, behind `APPROVED_TR_MINOR_NOTICE_VERSION`;
  4. a real-provider qualification at ages 13, 15 and 17.

  A synthetic SDK pass is source evidence only.

## Rollout flag

A signed 13–17 record keeps the negative boundary when `ACCOUNT_ELIGIBILITY_ENABLED` is off (`_enforced`), and the teen profile branch is decided from the record alone. The flag cannot open a minor's adult-only lanes.

Tests: `tests/test_tr_minor_onboarding.py`, `tests/test_tr_minor_full_onboarding.py`,
`tests/test_tr_minor_training_ai.py`, `tests/test_tr_minor_training_ai_pg.py` (CI PG list).
