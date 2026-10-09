# LP17 Today Guidance foundation

Baseline: `05eca64b3f1009d71a48df6e7de8c7c1cbf392cd` (main, LP18-B1 #422).
Branch: `lp17/today-guidance-read-model`. Dedicated worktree:
`/Users/yusuf/develop/fitness-coach-lp17`.

This adds an **internal, version 1 read model**, not an HTTP or mobile integration.
`build_today_guidance(authenticated_user_id)` in
`app/services/today_guidance_read_model.py` uses narrow canonical adapters;
`today_guidance_projection.py` is the pure projection. No existing file is
modified. Existing `/api/v1/today`, flags, workout decisions and mutation
authorities retain their contracts.

## Authority audit

| Signal | Canonical owner and reused reader | Date / absence / failure | Identity and guarantee |
|---|---|---|---|
| Training | `mobile_today.build_today` → `today_facts.get_active_plan`, `workout_state.resolve_workout_state(strict_reads=True)`, `serialize_today_plan` | Server `app_today()`; preserves `no_plan`, `rest_day`, `completed`, `in_progress`, `needs_attention` and other canonical states verbatim. Missing summary is not rest. Storage failure raises. | `daily_context` lineage/version/day is passed through. Completion and sessions can change without plan mutation; this tuple is not an all-facts revision. Tests: `test_mobile_today_api`, `test_mobile_today_architecture`, `test_workout_state_sessions`. Contract: `MOBILE_TODAY.md`, `WORKOUT_STATE.md`. |
| Training action | `app.today_guidance.decide_today_guidance` | Validates state/action compatibility; unknown vocabulary emits error and no candidate. | Existing UX-2 PR5 owns Resume/Start/Create Plan precedence **within training**. No cross-domain precedence. Tests: `test_today_guidance`; design: `superpowers/specs/2026-09-04-ux2-pr5-today-guidance-orchestration-design.md`. |
| Nutrition target | `nutrition_day_view.build_nutrition_day_view` → newest owner `UserSession`, `nutrition_targets.derive_daily_macro_targets` | Missing/null/nonpositive target is empty with null value under the existing contract. Invalid and unavailable remain distinct. | No day-view revision or freshness timestamp. Not an adherence verdict. Tests: `test_nutrition_vnext_pr6_day_view`. Contract: `NUTRITION_VNEXT_PR6.md`. |
| Nutrition intake | Same day view → owner/day `MealLog` aggregate | Istanbul `MealLog.tarih`; successful no-row read is measured zero totals/count. Invalid or failed read has null facts, never zero. | Ledger aggregate only; no “behind”, timing, remaining-budget or health interpretation is added. |
| Nutrition saved plan | Same day view → newest owner `NutritionPlan` | Successful no-row read is empty; malformed content is invalid; failure unavailable. | Existing bounded name/meal-count projection, no full plan. |
| Hydration | Same day view → owner/day `WaterLog` | Istanbul `date_key`; missing row and count 0 both mean measured 0 glasses under the existing authority. Invalid/failed counts are null. | No new goal, glass-size conversion, reminder or “drink more” rule. `nutrition_native.hydration.read_today` also exposes an owner/day/count revision for writes; LP17 does not mint or substitute a token. Tests: day view and `test_nutrition_vnext_pr7_native_hydration`. |
| Check-in | `mobile_weekly_checkin.history.build_history` → `progress_history.fetch_qualifying_checkins`, `previous_daily_row`, `historical_body`; `current_week` uses `weekly_checkin.FULL_CHECKIN` | Full means `yogunluk IS NOT NULL`; weight-only rows are excluded. Newest `created_at DESC, id DESC`. Istanbul analysis days and Monday–Sunday display week. No rows = empty; reader failure = unavailable. Legacy invalid/missing ratings remain null. | Latest public history item and current-week submission fact only. No note, feedback, owner, id, idempotency key or fingerprint. Timestamp is observational, not a unique revision (ties and legacy weight updates exist). Tests: `test_lp16b_native_checkin_api`, architecture guards. Contracts: LP16-B history implementation and `weekly_checkin/queries.py`. |
| Recovery | No authorized daily Today recovery reader found | Explicit `unsupported`, facts null. Check-in fatigue/sleep stay dated self-reports under check-in. | `training_generation/recovery_model.recovery_capacity_factor` is a generation heuristic with defaults, not a daily readiness authority. `ai_recovery` is provider failure recovery. Neither is invoked. No recovery/readiness/strain score. |
| Progress interpretation | `progress_insights.build_progress_insights` exists | Fixed historical training window; not a check-in-due or daily recovery authority. | Existing web `today_facts._gather_insight` displays Watch before Working, excludes Next Move from Today primary ranking. Deferred from this small foundation to avoid expanding historical reads. `PROGRESS_INSIGHTS.md` excludes nutrition/recovery/hydration insight domains. |

All reused reads are explicitly owner-scoped. LP17 accepts the scalar principal
id, not a request-selected owner. It is **not itself authentication middleware**.
Any future transport must derive that id from verified `g.mobile_user` and use
the existing mobile auth gate, typed errors and `Cache-Control: no-store`.
No route is admitted or registered here. No cross-account cache is introduced.

## Version 1 contract

The root has `contract_version`, `day`, `timezone`, `training`, `nutrition`,
`hydration`, `checkin`, `recovery`, `action_priority`, and `freshness`.

- `training.state=available` means the strict canonical read succeeded, even
  for `status=needs_attention`. Status, action, workout summary/session, and
  daily context are copied from the existing Today contract. `guidance` contains
  only the existing training decision's state and kind.
- `nutrition.state=available` means the adapter returned a settled day view,
  **not** that all its sections succeeded. `facts.target`, `intake`, and `plan`
  each retain `available|empty|invalid|unavailable`. `facts.next_action` is the
  existing Nutrition decision table (`log_food|set_target|retry|none`), unchanged.
  Whole-reader failure returns `state=unavailable, facts=null`.
- `hydration` retains its own section state, amount and glass unit. A failed
  whole Nutrition read yields unavailable/null hydration, never zero.
- `checkin.state=available|empty|unavailable`. `latest` is one existing bounded
  public history item or null. `current_week.submitted=false` proves no full
  check-in in that week; it does **not** mean overdue. No check-in action.
- `recovery.state=unsupported, facts=null` is stable and distinct from a read
  failure. No fabricated measurement or interpreted recovery statement.
- `action_priority.state=not_established, primary=null`. Source actions are
  informational and have no numeric priority, ordering or implicit winner.
- `freshness.cacheable=false, revision=null,
  consistency=independent_source_snapshots`. No composite ETag or freshness TTL.

Training failure, dirty session, incompatible server dates/weeks, non-finite
JSON or excess payload raises `GuidanceUnavailable`; there is no fabricated
successful day. Secondary failures are isolated and emit explicit unknowns.
Exceptions and their text are never serialized or logged by this service.

Canonical source helpers release the scoped ORM session. Call only at a clean
read boundary after capturing principal id; pending new/dirty/deleted objects
are rejected before any helper can flush or discard them. Training has its own
strict snapshot. Nutrition and check-in each have independent snapshots; this
does not promise an atomic multi-domain view or prevent concurrent changes
between reads. Dates are checked against the final server day; a midnight
crossing fails closed for reread. Check-in facts are week/history facts, so they
remain coherent across a day change inside the same display week.

## Freshness, bounds, privacy and localization

Reread on surface entry/foreground, explicit refresh, retry after failure,
server day rollover, training generation/replacement/mutation, session
start/checkpoint/abandon/completion/PumpCheck, food log/edit/delete, target or
profile/body-weight change, nutrition save/replace, water update, and full
check-in submit or legacy weight update. A changed lineage/version invalidates
training context; an unchanged tuple never proves all displayed facts unchanged.
Do not reuse a previous owner's response after account switch or sign-out.

Output is limited to **16,384 UTF-8 JSON bytes**, one training summary/session,
three nutrition fact sections plus their existing action, one hydration section,
and one check-in item. Overflow fails closed; semantic facts are not truncated.
No lists of exercises, foods, plans or check-ins are published. Canonical
read cost stays bounded: existing Today read, four Nutrition sections, and the
native history reader's 13-row window, week-existence query and at most 12
bounded prior-day lookups. No LP17 queries or N+1 loop are added. Future demand
for a cheaper latest-only history reader belongs to the check-in owner.

No new provider, HTTP, persistence, write, feature activation or domain authority.
No user identifiers, notes, stored coach feedback, injuries, credentials or
secret-derived tokens are exposed. Weight and self-reported ratings are private
owner-only display facts. Unknown remains unknown; units and field meanings
are inherited. EN/TR clients own interface copy for states and existing
semantic action identifiers; Nutrition's existing `label_key` is retained.
Author-written training focus and nutrition plan name are content, not enums;
never translate them to decide state. No user-facing sentences are generated.

## Admission and remaining product decision

This isolated service is admitted because every implemented display fact and
source action is already owned elsewhere. No change to existing transport or
screen is required. It is dormant until a separately reviewed integration.

The smallest decision needed for a future single Today CTA is: **should Today
retain the established training primary action and show Nutrition actions as
secondary, or should Nutrition compete; if it competes, which exact source
state/action pairs may win over each eligible training pair?** Hydration and
check-in need their own eligibility contracts before entering that comparison.
No urgency rule, due cadence or global precedence is implemented in LP17.
Daily recovery scoring would require a separately authorized owner and product
contract; it is not a prerequisite for this factual foundation.

## Qualification and isolation

`tests/test_lp17_today_guidance_read_model.py` covers persisted training states,
canonical parity, zero/unknown, account isolation, changed check-ins, self-report
nulls, Istanbul midnight, mixed-date refusal, partial and strict failure, pending
write refusal, SQL/flush guards, provider detonators and payload ceiling.
Architecture guards pin imports and keep projection I/O-free.

Focused regression command (local in-memory SQLite, frozen clock, no network):

```sh
python -m pytest -q tests/test_lp17_today_guidance_read_model.py \
  tests/test_mobile_today_api.py tests/test_mobile_today_architecture.py \
  tests/test_today_guidance.py tests/test_nutrition_vnext_pr6_day_view.py \
  tests/test_lp16b_native_checkin_architecture.py \
  tests/test_lp16a_weekly_checkin_architecture.py \
  tests/test_lp16b_native_checkin_api.py tests/test_mobile_auth_feature_gate.py
```

Semantic mutation reproduction (run separately; restores source files):

```sh
python tests/qualification/run_lp17_mutations.py
```

Mutations: unavailable water → 0; accept mixed training/server dates; invent
global training primary. Each must be killed by a targeted behavioral test.

Local evidence (2026-10-09): combined focused/regression/architecture command
above **282 passed**, with four existing SQLAlchemy `Query.get()` deprecation
warnings in Nutrition tests. LP17 alone: **18 passed**. Final semantic mutations:
**3/3 killed**; originals restored. The first date mutation changed only one
of two date predicates and survived because the sibling check still rejected
the mixed day; the final mutation removes the full date guard and is killed.
No full local CI run or PostgreSQL operational qualification was performed.

Preflight open PRs were #400 (TI05 qualification), #383 (triage documentation),
and #342 (AI refactor); LP17's new paths overlap none. The existing LP16-E,
LP18 and other worktrees were inspected but not modified. No mobile screen,
router, controller, composition, deployment or infrastructure file changes.
No staging, AWS, Cognito, tunnels, devices or production operations.
One natural exact-head PR CI is the remote qualification. No merge/deployment.
