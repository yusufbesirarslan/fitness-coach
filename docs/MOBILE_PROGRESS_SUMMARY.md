# Mobile Progress Summary Contract

The canonical Progress summary the native AxisAI client reads (LP-04, spec J1 in
[mobile/progress-vertical-slice.md](mobile/progress-vertical-slice.md)). Web
Progress behaviour is unchanged by everything in this file: LP-04 adds a second
transport over a read model that already exists, and modifies none of it.

- Endpoint: `GET /api/v1/progress/summary`
- Route: `app/blueprints/mobile_progress.py` (on the existing `mobile_api` blueprint)
- Canonical authority: `app/services/progress_summary` —
  [PROGRESS_SUMMARY.md](PROGRESS_SUMMARY.md) owns every rule, state and window
- Tests: `tests/test_mobile_progress_summary_api.py`,
  `tests/test_mobile_progress_summary_architecture.py`
- Auth contract: [AUTH_CONTRACT.md](AUTH_CONTRACT.md),
  [adr/0001-native-mobile-authentication.md](adr/0001-native-mobile-authentication.md)
- Downstream consumer: Flutter LP-06 (not in this repository)

## 1. The one rule

> This endpoint **transports** the canonical Progress summary. It is not a
> Progress authority.

```
static/progress.js ── GET /api/progress/summary    ──┐   (cookie session)
                                                     ├→ build_progress_summary
Flutter (LP-06) ───── GET /api/v1/progress/summary ──┘      + progress_summary_payload
                                                     (bearer)
```

The route makes exactly one call — `build_progress_summary(g.mobile_user.id)` —
and serializes the result through the same hand-written
`progress_summary_payload` the web route uses. It owns no threshold, window,
date, state vocabulary, query or fallback. Enforced by
`tests/test_mobile_progress_summary_architecture.py`: a delegation spy, an
exact import set, a route-source scan, a repository-wide "exactly two callers of
the builder" check, and a web ↔ mobile byte-parity test.

No provider or model call is reachable: the canonical path is deterministic, and
a test runs the real request with every provider client replaced by a detonator.

## 2. Request

```
GET /api/v1/progress/summary
Authorization: Bearer <opaque access credential>
```

There is **no input**. The owner is the Bearer principal; the window is pinned by
the service (4 weeks ending on the application's Europe/Istanbul day, resolved
once by `app.timeutil.app_today()`). Query strings, headers and bodies are
ignored, not rejected — the same policy as the web route and `GET /api/v1/today`.
`?user_id=`, `?account_id=`, `?username=`, `?email=`, `?owner_id=`, `?weeks=`,
`?end_day=` and `X-User-Id`-style headers change nothing.

Only `GET` is published (`405` otherwise).

## 3. Response — `200`

`Content-Type: application/json`, `Cache-Control: no-store`.

The body is the canonical `contract_version: 1` summary, byte-identical to what
the web route returns for the same account and day (see
[PROGRESS_SUMMARY.md §3](PROGRESS_SUMMARY.md) for field provenance):

```json
{
  "contract_version": 1,
  "window":      { "weeks": 4, "start": "2026-06-26", "end": "2026-07-23",
                   "timezone": "Europe/Istanbul" },
  "trajectory":  { "state": "on_track", "reason": "progressing" },
  "body":        { "status": "available", "current_weight_kg": 78.4,
                   "weight_delta_kg": -0.6, "target_weight_kg": 75.0,
                   "distance_to_target_kg": 3.4,
                   "weight_series": [ { "day": "2026-07-09", "weight_kg": 79.4 },
                                      { "day": "2026-07-16", "weight_kg": 79.0 },
                                      { "day": "2026-07-23", "weight_kg": 78.4 } ] },
  "performance": { "state": "progressing", "volume_trend": "up",
                   "strength_trend": "up", "next_signal": "progressing" },
  "consistency": { "state": "consistent", "active_weeks": 4,
                   "analyzed_weeks": 4, "sessions": 12 },
  "weekly":      [ { "start": "2026-06-26", "sessions": 3, "active": true,  "volume_kg": 3000.0 },
                   { "start": "2026-07-03", "sessions": 3, "active": true,  "volume_kg": 3450.0 },
                   { "start": "2026-07-10", "sessions": 3, "active": true,  "volume_kg": 3900.0 },
                   { "start": "2026-07-17", "sessions": 3, "active": true,  "volume_kg": 4350.0 } ]
}
```

This exact body is pinned by
`test_populated_account_reads_the_literal_canonical_contract`.

### Field rules

| Field | Type | Nullability |
|---|---|---|
| `contract_version` | int | required, always `1` |
| `window.weeks` | int | required |
| `window.start`, `window.end` | ISO date | required |
| `window.timezone` | IANA zone | required |
| `trajectory.state` | enum `building_baseline · on_track · needs_attention` | required |
| `trajectory.reason` | canonical `next_signal` | required |
| `body.status` | enum `available · partial · insufficient_data` | required |
| `body.current_weight_kg`, `weight_delta_kg`, `target_weight_kg`, `distance_to_target_kg` | float | **nullable** — `null` = unknown, never `0` |
| `body.weight_series[]` | `{day: ISO date, weight_kg: float}` | required list, may be empty; never padded |
| `performance.state` | enum `building_baseline · progressing · steady · building_consistency · plateau · deload` | required |
| `performance.volume_trend`, `strength_trend` | enum `up · flat · down` | required |
| `performance.next_signal` | canonical `next_signal` | required |
| `consistency.state` | enum `consistent · inconsistent · insufficient_data` | required |
| `consistency.active_weeks`, `analyzed_weeks`, `sessions` | int | required; `0` is a real measured zero |
| `weekly[]` | `{start: ISO date, sessions: int, active: bool, volume_kg: float}` | required, one entry per analysed week, oldest first |

Every key is present in every state. No field is omitted to express absence;
absence is `null` (body values) or a bounded state (`insufficient_data`,
`building_baseline`). No database id, user id, username, e-mail, timestamp,
score, percentage, streak or localized prose is published.

## 4. Baseline / insufficient data

A new account receives a deterministic, non-fabricated baseline — pinned in full
by `test_new_account_reads_the_literal_baseline_contract`:

- `trajectory = {building_baseline, insufficient_data}`,
  `performance.state = building_baseline`, trends `flat` (the canonical neutral);
- `body.status = insufficient_data`, every body value `null`, `weight_series = []`;
- `consistency.state = insufficient_data`, `sessions = 0`, `active_weeks = 0`,
  `analyzed_weeks = 4` (four weeks really were analysed, and nothing was in them);
- four `weekly` entries with measured zeros.

A client distinguishes *measured* from *not enough data yet* through the state
enums and `null`s, never through a number it has to interpret. The matrix
(body only · training only · consistency only · completion markers only ·
incomplete data) is pinned against the canonical builder's own answer.

Known limitation (spec §C): native WorkoutSession completion writes only the
completion marker, so for a native-only user `consistency.sessions` counts
completions while `volume_trend`/`strength_trend` stay neutral. The client
renders those states literally.

## 5. Errors

Every error is the ADR 0001 envelope and carries `Cache-Control: no-store`:

```json
{ "error": { "code": "…", "message": "…", "retryable": true, "request_id": "…" } }
```

| Situation | Status | Code | Retryable |
|---|---|---|---|
| missing / malformed `Authorization` | 401 | `AUTH_SESSION_EXPIRED` | false |
| unknown, expired or revoked credential | 401 | shared middleware `AUTH_*` code | per middleware |
| summary could not be computed (storage fault, unmapped canonical signal, any unexpected failure) | **503** | `PROGRESS_UNAVAILABLE` | **true** |
| app-wide `DEFAULT_RATELIMIT` exceeded | 429 | `AUTH_RATE_LIMITED` (blueprint handler, `Retry-After`) | true |

A failure is **never** served as a summary. `building_baseline` is an honest user
state ("not enough of your history yet"); returning it — or any summary-shaped
body — for a broken read would report broken infrastructure as an assessment of
the user. The 503 body contains no summary key at all. `UnknownProgressionSignal`
(canonical contract drift) is the same 503 and never resolves to a state.

The route catches the failure itself rather than letting it reach the
blueprint's catch-all, which answers `AUTH_TEMPORARILY_UNAVAILABLE`: a client that
read an auth code would discard a good session. No exception text, SQL, stack
trace, path or identifier reaches the body.

## 6. Security

- **Owner authority.** `g.mobile_user`, resolved by the shared
  `require_mobile_auth` middleware from the opaque Bearer credential, is the only
  owner expressible. Cookie sessions cannot authenticate this route.
- **Account isolation.** Proven with two accounts holding real issued
  credentials, requested in interleaved order, with ownership selectors in the
  query, headers and body — each bearer only ever receives its own canonical
  summary. A statement-level test additionally shows the other account's id is
  never a bound SQL parameter of the request.
- **No caching of the summary anywhere.** The read model is recomputed per
  request; there is no process or Redis cache that could be keyed wrongly.
- **`Cache-Control: no-store`** comes from the `mobile_api` blueprint, like every
  `/api/v1` response (the web route keeps its own `private, no-store`).
- **Route classification.** Authenticated: listed in the exact approved-route
  allow-list (`tests/test_mobile_auth_feature_gate.py`) and the Sprint 12
  exact-surface gate, never in the pre-auth set. Registered only when
  `MOBILE_AUTH_ENABLED` is on; no new flag.

## 7. Observability

One line per request, states only:

- success: `mobile_progress event=summary_read trajectory=<state> body=<status> request_id=<id>`
- failure: `mobile_progress event=summary_read_failed error_type=<exception class> request_id=<id>`

No weight, session count, payload, username or credential is logged.

## 8. Out of scope

No schema change, migration, table, cache or flag. No Flutter change (LP-06), no
Pump Check, Axis Insights, physique, history, nutrition, Coach or AI content in
this contract. Adding a field is a change to `progress_summary/payload.py` and
therefore to both transports at once; a breaking change must move
`CONTRACT_VERSION`, which native clients fail closed on.
