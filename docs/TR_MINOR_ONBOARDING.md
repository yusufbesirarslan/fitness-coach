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

- **AI (all five D1 purposes, including Training):** `ai_launch.require_cohort` admits adult TR only.
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

It covers every account with a minority age signal, not only TR. The account still sees its own standing.

Tests: `tests/test_tr_minor_onboarding.py`, `tests/test_tr_minor_full_onboarding.py`.
