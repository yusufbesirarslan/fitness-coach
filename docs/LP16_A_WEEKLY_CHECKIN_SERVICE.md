# LP16-A — Weekly check-in canonical service extraction

Status: implementation, backend only. Base `origin/main` 088d04d.
No route added, no schema/migration, no mobile code, no Progress contract change.

## 1. What moved

`app/services/weekly_checkin/` is now the one persistence authority for
weekly check-ins and the body-weight effect they carry:

| Module | Owns |
|---|---|
| `models.py` | framework-free values: `FullCheckIn` (already-parsed domain values), `CheckInContext`, `BodyWeightUpdate`, `SubmissionOutcome`/`SubmissionResult` |
| `queries.py` | owner-scoped reads moved verbatim from `tracking.py`: `lock_owner` (`SELECT user.id … FOR UPDATE`), `find_by_idempotency_key`, `latest_full_checkin` (`yogunluk IS NOT NULL`, `created_at DESC, id DESC`), `same_app_day_checkin` (legacy, no ORDER BY); `FULL_CHECKIN` filter |
| `service.py` | `apply_body_weight` (the shared User.weight + derived-session primitive, verbatim `_apply_weight_to_profile`), `claim_submission`, `load_context`, `stage_full_checkin`, `commit_full_checkin`, `record_legacy_weight_update` |

`tracking.checkin` and `tracking.update_weight` keep transport work only:
header/body parsing (including the legacy metric parser that fills missing
values with 3), the legacy web fingerprint, the Bedrock feedback call,
quest/challenge awards, response building. `_apply_weight_to_profile` and
the route's own `WeeklyCheckIn` / `UserSession` / lock / commit code are gone.

The canonical session is read through the existing
`account_profile.canonical_session` (identical query to the route's old one).
Onboarding (`complete_onboarding`) and the Coach `/coach` profile path compute
targets from a submitted profile, not from a weight change; their semantics are
not identical and they were deliberately NOT moved.

## 2. Boundaries (enforced by `tests/test_lp16a_weekly_checkin_architecture.py`)

- The package imports only stdlib, `sqlalchemy.exc`, `app.extensions`,
  `app.models` (`User`, `UserSession`, `WeeklyCheckIn`),
  `app.services.account_profile`, `app.services.calculations`, `app.timeutil`.
  No Flask, `request`, `current_user`, `g`, blueprint, mobile transport,
  Coach, Progress, AI/provider module.
- The service takes explicit owners (`owner` = a `User`; the web passes
  `current_user._get_current_object()`).
- `WeeklyCheckIn(...)` is constructed only in `weekly_checkin/service.py`
  (repo-wide scan of `app/` + `fitx_mcp/`).
- `bmr`/`tdee`/`target_calories` are assigned only in `apply_body_weight`.
- `commit`/`rollback` appear only in the three named transaction owners.
- The two web views contain no `db`, `WeeklyCheckIn`, `UserSession`,
  `with_for_update`, `commit`, `rollback`, `add`, `query`, or weight/derived
  attribute assignment; the AST pins the order
  `claim_submission → load_context → generate_checkin_feedback → stage_full_checkin`.
- `record_legacy_weight_update` has exactly one caller: `tracking.update_weight`.
- Never written by the service: `User.target_weight`, `TrainingPlan`,
  `NutritionPlan`, Today, Coach state.

## 3. Transaction model

Full check-in (`/checkin`):

1. Keyed only — `claim_submission(owner_id, key, fingerprint)`: owner lock,
   key lookup. REPLAYED / CONFLICT end the transaction (rollback) before
   returning; FRESH leaves the lock held.
2. `load_context(owner_id)`: latest full check-in, canonical session.
3. (web) feedback call — outside the service.
4. `stage_full_checkin(owner, FullCheckIn, context, coach_feedback=…, idempotency_key=…, request_fingerprint=…)`:
   add the row, `apply_body_weight`. No commit.
5. (web, keyed) `_claim_quest` stages quest/challenge writes into the same
   transaction; the response is built.
6. `commit_full_checkin(entry, response=…)`: the ONE commit of row + User.weight
   + derived session (+ quest + replay snapshot when keyed). Keyed
   `IntegrityError` → rollback, winner lookup, REPLAYED/CONFLICT, else re-raise.
7. (web, unkeyed) `complete_quest_for_user` — its own second commit, as before.

`/update-weight`: `record_legacy_weight_update(owner, weight)` = canonical
session → `apply_body_weight` → Istanbul-day row lookup (autoflush) → update or
insert weight-only row → one commit; then the route's
`complete_quest_for_user` commit, as before.

Atomicity evidence: a fault injected into the `weekly_check_in` INSERT (all
three paths) or between staging and commit (keyed quest staging) leaves
User.weight, the session and the rows unchanged — SQLite
(`test_lp16a_web_checkin_characterization.py`) and real PostgreSQL
(`test_lp16a_weekly_checkin_pg.py`); on PostgreSQL a separate connection sees
none of the flushed-but-uncommitted row/weight/session until the single commit.

## 4. Idempotency model

Unchanged authority: `uq_weekly_checkin_user_key (user_id, idempotency_key)` +
`request_fingerprint` + `response_snapshot`. The service primitives are
fingerprint-format agnostic: the caller supplies an opaque fingerprint and the
replay body. The web keeps its legacy fingerprint (sha256 of the parsed legacy
dict) in `tracking.py`. No `native.v1` fingerprint exists yet (LP16-B).

## 5. Lock timing

Unchanged. The keyed owner lock is taken at the same statement position (after
parsing + fingerprint, before the reads and the feedback call) and held across
the feedback call until the single commit/rollback. The lock-across-Bedrock
debt is preserved as recorded, not fixed. `/update-weight` still takes no lock.

## 6. Before/after matrix

Evidence: an in-view SQL trace (statements + ORM-level `FOR UPDATE` marks +
commit count + status + body + feedback calls) of 7 scenarios is
**byte-identical** on 088d04d and on the LP16-A head: unkeyed check-in, keyed
check-in, keyed replay, keyed conflict, same-day `/update-weight`, invalid key,
invalid weight. The characterization (29) and PG (7) suites pass unchanged on
both sides.

### POST /checkin

| Aspect | Before | After | Class |
|---|---|---|---|
| Request parsing | route: `Idempotency-Key` → 400 invalid; `_parse_weight` → 400; `_to_int(…, 3)` metrics; overload → `kismen`; note default `""` | unchanged, in route | BYTE PRESERVED |
| Web fingerprint | route, sha256 of legacy dict | unchanged, in route | BYTE PRESERVED |
| AI call position | route, after context reads, before insert; under the lock when keyed | unchanged, in route (AST-pinned) | BYTE PRESERVED |
| Locking | keyed: `SELECT user.id … FOR UPDATE` before reads/feedback, held to commit; unkeyed: none | same statement/position via `claim_submission` | BYTE PRESERVED |
| Row selection (previous) | `yogunluk IS NOT NULL`, `created_at DESC, id DESC` | `queries.latest_full_checkin`, same SQL | BYTE PRESERVED |
| Session selection | route query `created_at DESC, id DESC` | `account_profile.canonical_session`, identical SQL | BYTE PRESERVED |
| Idempotency lookup / replay / conflict | route | `claim_submission` (same order: conflict rollback→409; replay loads snapshot→rollback→200) | BYTE PRESERVED |
| Race arbitration (`IntegrityError`) | rollback → winner lookup via `current_user.id` | rollback → winner lookup via owner id captured before the commit | INTERNAL ONLY (no refresh of the expired user before the lookup; same answer) |
| User.weight | `_apply_weight_to_profile` | `apply_body_weight` (verbatim) | BYTE PRESERVED |
| Derived session | complete profile → BMR/TDEE/target; else weight only | same | BYTE PRESERVED |
| target_weight | untouched | untouched | BYTE PRESERVED |
| Commit count | keyed 1; unkeyed 2 (row, then quest) | same | BYTE PRESERVED |
| Responses | `{"message","coach_feedback"[, "quest_awarded"]}`; 409 `{"error"}` | identical | BYTE PRESERVED |
| Errors | 400 / 409 / persistence fault → raised/500 with nothing durable | same | BYTE PRESERVED |
| New service guards | — | `ValueError` on missing intensity, foreign context/session, key without fingerprint (and vice versa); `TypeError` on non-`FullCheckIn` | INTERNAL ONLY (unreachable from the web transport) |

### POST /update-weight

| Aspect | Before | After | Class |
|---|---|---|---|
| Request parsing | `_parse_weight` → 400 | unchanged, in route | BYTE PRESERVED |
| AI call | none | none | BYTE PRESERVED |
| Locking | none (known defect) | none | BYTE PRESERVED |
| Row selection | any row in the Istanbul day (`utc_day_bounds(app_today())`), no ORDER BY | `queries.same_app_day_checkin`, same SQL | BYTE PRESERVED |
| Full same-day row | weight overwritten (known defect) | same (pinned by tests) | BYTE PRESERVED |
| Otherwise | weight-only row (metrics NULL) | same | BYTE PRESERVED |
| User.weight / derived session / target_weight | shared helper / unchanged | `apply_body_weight` / unchanged | BYTE PRESERVED |
| Commit count | 2 (write, then quest) | same | BYTE PRESERVED |
| Responses | `{"bmr","tdee","target_calories","profile_incomplete"[, "quest_awarded"]}` | identical | BYTE PRESERVED |

## 7. Recorded debt kept on purpose (not LP16-A findings)

- Keyed `/checkin` holds the owner lock across the feedback call.
- `/update-weight` overwrites a same-day full check-in's weight, takes no
  lock, and `same_app_day_checkin` has no ORDER BY (several rows that day →
  the database picks one).
- Web legacy parser: missing metrics become 3; `_to_int` does not catch
  `OverflowError` (`1e400` → 500). LP16-B must use a strict native parser.
- A flush fault leaves the request session needing rollback; later request
  hooks then raise `PendingRollbackError` (still no durable write).

## 8. Tests

| File | Scope |
|---|---|
| `tests/test_lp16a_web_checkin_characterization.py` | 29 route characterizations, written and run on 088d04d first |
| `tests/test_lp16a_weekly_checkin_service.py` | 17 transport-free service tests |
| `tests/test_lp16a_weekly_checkin_architecture.py` | 11 AST gates + runtime delegation spy |
| `tests/test_lp16a_weekly_checkin_pg.py` | 7 real-PostgreSQL races/atomicity (CI `mobile-pg-concurrency` list) |

14 mutations (User.weight, derived session, target_weight, partial commit,
route bypass, AI import, sparse-row-as-full ×2, UTC day, conflict→replay,
dropped lock, accidental `/update-weight` fix, incomplete-profile recalc,
race-loser conflict→replay) were each killed by focused tests and restored
byte-exactly.

## 9. Next

LP16-B: native `POST/GET /api/v1/progress/check-ins` over these primitives
(strict parser, `native.v1` fingerprint, empty `coach_feedback`, no provider).
