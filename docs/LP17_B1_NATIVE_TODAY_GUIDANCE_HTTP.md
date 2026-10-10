# LP17-B1 native Today Guidance HTTP contract

Baseline: `758dd8b988c5d9de34d191c961f717d57a7739cf` (main, LP17-A #423).
Branch: `lp17/b1-native-guidance-http`. Dedicated worktree:
`/Users/yusuf/develop/fitness-coach-lp17-b1`.

`GET /api/v1/today/guidance` is a **dormant transport** over the merged LP17
read model (`docs/LP17_TODAY_GUIDANCE.md`). It adds no domain logic, no query,
no persistence and no provider call, and it does not replace or change
`GET /api/v1/today`.

## Rollout gate

`FITX_MOBILE_TODAY_GUIDANCE_ENABLED` — canonical registry record in
`app/feature_flags.py` (default OFF, strict `0|1` parser, `shipped_dark`,
depends on `MOBILE_AUTH_ENABLED`, review 2026-11-10). Read only from
`current_app.config` at request time; never from the environment.

| `MOBILE_AUTH_ENABLED` | guidance flag | Answer |
|---|---|---|
| OFF | any | `/api/v1` not registered — every native route 404 (unchanged rule) |
| ON | OFF | app 404 page **before `require_mobile_auth`** — same status, body and content type as an unregistered path; the read model never runs |
| ON | ON | Bearer auth → owner-scoped guidance |

The route is registered unconditionally (the repository's request-time gating
pattern), so it is admitted by exact path in the route inventories. That
leaves residual differences from an unregistered path, shared by every
request-time-gated `mobile_api` route and none carrying guidance data
(pinned by `test_flag_off_residuals_under_the_production_limiter`,
`test_flag_off_wrong_method_is_405_from_the_registered_rule` and
`test_flag_off_default_limit_answers_429_from_the_limiter`):

- blueprint-wide `Cache-Control: no-store` on the 404;
- with the limiter on (production), `bind_mobile_request_principal` still runs
  `authenticate_access` for a presented Bearer in `before_request` (an expired
  or mismatched family is revoked there, exactly as on any mobile route), and
  the 600/hour default limit can answer 429 `AUTH_RATE_LIMITED`;
- OPTIONS answers 200 with `Allow`, and other methods 405 (a POST, PUT,
  PATCH or DELETE without a same-origin Origin/Referer gets the shared CSRF
  hook's 403 first, as an unregistered path does);
- the request log names the path instead of `<unmatched>`.

Removing them needs conditional route registration or a shared middleware
change; neither is in this PR.

## Transport

`app/blueprints/mobile_today_guidance.py`, on the existing `mobile_api`
blueprint: `@_rollout_gated` (outermost) → `@require_mobile_auth` → capture
`g.mobile_user.id` → `build_today_guidance(owner_id)` → `jsonify`. The module
imports no `request`: no query, body or header beyond the auth boundary is read,
so `?user_id=`, `?date=`, `X-Date`, `X-Timezone` etc. have no effect.

| Outcome | Status | Body |
|---|---|---|
| success | 200 | LP17 `contract_version: 1` payload, unchanged |
| missing/malformed Bearer | 401 | `AUTH_SESSION_EXPIRED` (shared envelope) |
| unknown/revoked credential | 401 | existing mobile auth codes |
| strict training failure, mixed server day/week, non-finite or >16 KiB payload | 503 | `TODAY_GUIDANCE_TEMPORARILY_UNAVAILABLE`, `retryable: true` |

Secondary Nutrition/Hydration/Check-in failures stay inside the 200 payload as
explicit `unavailable` sources (never zero, empty or rest). The 503 is never
the blueprint's auth-flavoured catch-all. The one failure log line is
`mobile_today_guidance event=guidance_read_failed error_type=<type> request_id=<id>`
— no message, owner or fact. All answers are `Cache-Control: no-store`.

Semantics are LP17-A's: training authority `mobile_today.build_today`,
Nutrition/Hydration `nutrition_day_view`, check-ins
`mobile_weekly_checkin.history`, recovery `unsupported`,
`action_priority.state=not_established`, freshness
`independent_source_snapshots` with `revision: null`. No composite revision,
no global CTA, no readiness/recovery/hydration/check-in-due rule.

A 503 caused by Istanbul midnight between source snapshots is expected and
retryable; the client rereads.

## Query budget and size (SQLite, measured)

| Scenario | SELECTs (stubbed credential) | Payload |
|---|---|---|
| empty user / active plan / populated Nutrition / 12–30 check-ins | 9 | ~1.1–1.6 KiB |
| secondary readers failed | 3 | ~0.9 KiB |
| adversarial legacy history (no in-window positive weight), 13–40 rows | 21 (9 + 12 bounded prior-day lookups) | ~1.6 KiB |
| 4,000-character plan focus and nutrition plan name (one-off measurement, not pinned) | 9 | ~2.0 KiB (canonical bounds) |

The real Bearer pipeline adds 3 fixed reads. Each normal read also issues four
SAVEPOINT/RELEASE pairs from the day view. Cost is constant in history size
(no N+1); `/api/v1/today` alone is 3 SELECTs. A cheaper latest-only check-in
reader would remove the adversarial lookups but belongs to the check-in owner.

## Compatibility

`app/blueprints/mobile_today.py`, `app/services/mobile_today.py` and the LP17
service/projection are unchanged. `/api/v1/today` is byte-identical with the
guidance flag OFF and ON (test). Inventories updated by exact path only:
`tests/test_mobile_auth_feature_gate.py`,
`tests/test_sprint12_daily_coach_discovery.py`,
`tests/test_mobile_today_architecture.py`.

## Qualification

`tests/test_lp17_b1_today_guidance_api.py` (real Flask boundary, real
persistence): flag registry/parse/boot; native auth OFF; flag OFF before auth
across header shapes; flag OFF residuals with the production limiter on; missing/malformed/unknown/revoked Bearer; cookie-only
browser session refused; real-credential cross-account isolation; spoofed
query/header owner/date/timezone; training parity with `/api/v1/today` for
scheduled/in-progress/completed/rest/no-plan/needs-attention; Nutrition and
Hydration parity with `/api/v1/nutrition/day-view` (populated, absent, zero,
section failure); check-in parity with `/api/v1/progress/check-ins` (1/12/14
rows, nullable legacy values); secondary failures; strict 503 matrix with
log/body leak checks; SELECT-only and no-flush capture through the real
credential pipeline; provider detonators; single delegation; thin-module AST;
registration; `/api/v1/today` flag invariance; query budgets.

Mutations: `python tests/qualification/run_lp17_b1_mutations.py` — gate removed,
gate inside auth, owner from query string, strict failure as 200, registry
default ON. Each must be killed; sources are restored.

TEST HAZARD: the read model's canonical snapshots release the scoped session, so
ORM instances held by a test detach after a request — capture ids/usernames
first and stub the principal by reloading the user per request.

No staging, AWS, Cognito, Parameter Store, tunnel, device or deployment work.
No flag is activated anywhere.
