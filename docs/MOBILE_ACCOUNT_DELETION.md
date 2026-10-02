# Native account deletion (LP-11)

`DELETE /api/v1/account` lets a signed-in native user delete their own account.
Backend only; the Settings UI is LP-12.

- Route (transport only): `app/blueprints/mobile_account_deletion.py`, on the
  existing `mobile_api` blueprint (`MOBILE_AUTH_ENABLED`-gated, `no-store`,
  ADR 0001 envelope, approved-route allow-list).
- Authority: `app/services/account_deletion.py` — the ONE deletion lifecycle.
- Provider adapter: `cognito_service.delete_user` (Cognito `DeleteUser`).
- Local purge: the canonical `_purge_user` (`app/cli.py`), the same primitive
  the operator `cleanup-test-users` command uses.
- Anti-resurrection: `app/services/deleted_identity.py` (tombstone written with
  the purge; checked by web and mobile login before a local row is bound or
  created — §6).

## 1. Contract

```
DELETE /api/v1/account
Authorization: Bearer <opaque access credential>
(no body, no query string)
```

| Status | Code | `retryable` | Meaning for the account | Client (LP-12) should |
|---|---|---|---|---|
| `204`, no body | — | — | Deleted: identity, data, media, every session | Clear all local account state; show signed-out |
| `401` | `AUTH_SESSION_EXPIRED` (or another `AUTH_*` from the middleware) | false | Nothing deleted | Sign in again, then retry |
| `503` | `ACCOUNT_DELETION_UNAVAILABLE` | true | Nothing deleted; account intact and usable | Say it failed; allow retry (honour `Retry-After`) |
| `503` | `ACCOUNT_DELETION_INCOMPLETE` | true | Sign-in identity deleted; local data cleanup did not finish | Say deletion started but is unfinished; retry with the SAME session. Never report success. If the session is gone, direct to support |
| `400` | `ACCOUNT_DELETION_INVALID_REQUEST` | false | Nothing deleted | Client bug: send a bare DELETE |

`204` with no body follows the established mobile deletion answer
(`POST /auth/logout`, `DELETE /nutrition/logs/<token>`). Every answer carries
`Cache-Control: no-store`. Error bodies are the ADR 0001 envelope
`{"error": {"code", "message", "retryable", "request_id"}}` and never contain
provider text, usernames, e-mail, storage keys, SQL, table names or ids.

## 2. Ownership

The account deleted is the verified Bearer principal — `g.mobile_user`, the
family it authenticated through (`g.mobile_session`) and that family's verified
provider claims (`g.mobile_claims`) — and nothing else.

- A body or query string is **refused** (400), not ignored, so a client can
  never believe it chose an account (`user_id`), an identity (`sub`) or objects
  (storage keys). Headers are not read.
- The service re-checks the principal is coherent (`family.user_id == user.id`,
  `family.cognito_sub == user.cognito_sub == claims.sub`) before any side
  effect.
- The Cognito call takes only the family's stored access token. `DeleteUser`
  has no username parameter and Cognito evaluates no IAM for it, so it can only
  delete the principal the token was issued to. No IAM change was needed.
- Storage keys come only from the owner's own rows and must pass the canonical
  owner-checked grammar (`s3_helper.managed_object_key_is_deletable` /
  `meal_photo_key_is_deletable`) with this owner's id. A reference that fails
  it (a URL, another owner's key) is never deleted and never blocks deletion;
  its row is purged and a PII-free count is logged. Every upload call since the
  first S3 commit (`8ba733a`) minted `meals/`, `pump-checks/` or `avatars/`
  `<uid>/<YYYY>/<MM>/<uuid4hex>.<ext>` keys, so no genuinely minted media falls
  outside the grammar.

## 3. Data lifecycle classification

Authority: `_purge_user` + `_user_child_models` (`app/cli.py`), the introspective
guard `tests/test_cascade_delete.py::test_purge_user_covers_every_foreign_key_to_user`
(every user FK must be covered), and the STAGING.md account-removal runbook.
The repository has **no** retention or anonymization policy for user data, and
LP-11 does not invent one: every user-owned row is hard-deleted, exactly as the
operator purge already does.

| Domain | Rows / resource | Class | Source |
|---|---|---|---|
| Account/profile | `User` (profile, `user_metadata` injuries/preferences, base64 avatar, XP) | HARD DELETE | `_purge_user` |
| Sessions | `MobileAuthSession` (+ `MobileAccessCredential`, `MobileRefreshCredential` via FK cascade), web `CognitoSession` | HARD DELETE (same transaction) | `_purge_user` |
| Onboarding | `UserSession` | HARD DELETE | `_purge_user` |
| Training | `TrainingPlan`, `PlanMutationRecord`, `TrainingPlanConfirmationProposal`, `TrainingPlanGenerationOperation`, `WorkoutLog`, `WorkoutSession` | HARD DELETE | `_purge_user` |
| Progress / check-ins | `WeeklyLog`, `WeeklyCheckIn` | HARD DELETE | `_purge_user` |
| Pump Check | `PumpCheck`, `PumpCheckComparison`, `PumpCheckComparisonRequest`, likes/comments on the user's checks | HARD DELETE | `_purge_user` |
| Nutrition | `MealLog`, `MealPhotoCleanup`, `NutritionPlan`, `CustomMeal`, `CustomMealItem`, `WaterLog`, `PendingAction`, `Supplement` | HARD DELETE | `_purge_user` |
| Coach | `CoachConversation`, `CoachMessage` | HARD DELETE | `_purge_user` |
| Activity / wearables | `DailyActivity`, `UserWearableConnection`, `WearableSleepLog`, `WearableActivityLog`, `WearableWorkoutLog` | HARD DELETE | `_purge_user` |
| Social | `Activity`, `Friendship`/`Message` (both directions), `FeedItem` (own + others' reposts of the user's checks), `FeedItemLike`/`Comment` (both directions), `FeedHide`, `FeedReport` filed by the user, `Notification` (received + triggered) | HARD DELETE | `_purge_user` |
| Gamification | `UserQuestProgress`, `WeeklyWinner`, `UserChallengeProgress`, `UserBadge` | HARD DELETE | `_purge_user` |
| Referral link on OTHER users | `User.referred_by_id` of invitees | ANONYMIZE (set NULL; invitee kept) | `_purge_user`, FK `SET NULL` |
| Media | S3 `avatars/<uid>/…`, `pump-checks/<uid>/…`, `meals/<uid>/…` named by the user's rows (incl. pending `MealPhotoCleanup` intents) | HARD DELETE | F4/F14 lifecycle: when the owning row is deleted the object is released (`delete_managed_object`, `delete_meal_photo`); `_purge_user` alone records this as debt |
| Identity | Cognito user | HARD DELETE | STAGING.md runbook (Cognito AND database, Cognito first) |
| Leaderboard cache | Redis `lb:*` sorted-set member | HARD DELETE, best effort | derived from Postgres; `lb_rebuild` corrects drift |
| Ephemeral Redis | Coach plan-clarification record (30 min TTL), rate-limit counters | Not purged; expire by existing TTL | existing TTL design |
| Other users' records | their `FeedHide`/`FeedReport` targets, `PumpCheck.shared_friend_ids` integers naming the deleted user's content/id | NOT USER-OWNED; unchanged (as operator purge) | belong to the other user; bare ids, no restorable data |
| Catalogs | `DailyQuest`, `Challenge`, `WeeklyResetLog`, `BarcodeFoodCache` | NOT USER-OWNED | no user FK |

No category was UNCLEAR under the repository's own authority.

## 4. Order and why

1. **Preflight** (no side effect): principal coherence; decrypt the family's
   stored provider access token; read the storage inventory; if the account owns
   objects but no object store is configured, stop (`UNAVAILABLE`).
2. **Identity**: Cognito `DeleteUser` inside `blocking_concurrency_slot`.
   A live Cognito identity recreates the local account at login
   (`mobile_auth._resolve_user`), so it goes first — the runbook's order.
   `UserNotFoundException` for the token's own principal = already absent
   (retry after `INCOMPLETE`) → continue. `NotAuthorizedException` → 401.
   Anything else → `UNAVAILABLE`, nothing changed.
3. **Storage**: release every owned object while the rows naming it still
   exist (keys carry a random uuid and cannot be re-derived after the purge).
   Any failure stops before the purge → `INCOMPLETE`, rows intact.
4. **Local purge**, one transaction: `SELECT … FOR UPDATE` on the owner row;
   re-read the inventory under the lock and, if a racing request committed a new
   object, roll back and release it first (at most `MAX_RELEASE_ROUNDS`); then
   record the deleted-identity tombstone (§6), `_purge_user`, and commit.
   Sessions and the tombstone commit with the account.
5. **Derived cache**: `gamification.lb_remove_user` (best effort).

No database transaction or row lock is held across a network call.

## 5. Partial-failure model

| # | Failure | State left | HTTP | Retry | Can still sign in? | Manual remediation |
|---|---|---|---|---|---|---|
| F1 | Cognito delete fails | Nothing changed | 503 `UNAVAILABLE` (401 if provider refuses the token) | Yes | Yes | No |
| F2 | Cognito ok, local purge fails | Identity gone; local account, data and sessions intact; objects released | 503 `INCOMPLETE` | Yes, same session: provider answers `UserNotFound` → resumes | No new sign-in; existing session works until its provider token expires | Only if the retry can't run (session expired, or provider answers the ambiguous `NotAuthorized`): operator `cleanup-test-users --username <u> --yes` (log line `event=incomplete … user_id=`) |
| F3 | Storage fails before purge | As F2, some objects released, rows intact | 503 `INCOMPLETE` | Yes | As F2 | As F2 |
| F4 | One object fails | Same as F3; already-missing objects count as released | 503 `INCOMPLETE` | Yes (DeleteObject idempotent) | As F2 | As F2 |
| F5 | Session removal fails | Part of the purge transaction → whole purge rolled back = F2 | 503 `INCOMPLETE` | Yes | As F2 | As F2 |
| F6 | Request interrupted | Before step 2: nothing. After: F2/F3 (PostgreSQL rolls back an uncommitted purge) | client sees transport error | Yes | As F2 | As F2 |
| F7 | Duplicate/concurrent DELETE | Both run; second gets `UserNotFound` → continues; releases are idempotent; purge serialises on the owner lock, the second finds the row gone → success | 204 / 204 (or 401 once sessions are gone) | — | No | No |

No path returns 2xx unless the identity step completed AND the purge committed
together with its tombstone. A tombstone write failure is F5 (the whole purge
rolls back → `INCOMPLETE`); a retry, where Cognito answers `UserNotFound`, still
records it.

## 6. Concurrency

- Child INSERT vs purge: the child's FK takes a key-share lock on the owner
  row, which conflicts with the purge's `FOR UPDATE`. A child committed first is
  visible and purged (its new media is released first); one arriving during the
  purge waits and then fails its FK. Proved on PostgreSQL:
  `tests/test_account_deletion_pg.py` (CI `PostgreSQL concurrency` job).
- An already-authenticated in-flight request may finish a read of pre-deletion
  data; it cannot persist anything after the purge.

### Anti-resurrection (deleted-identity tombstone)

**The race.** A login (web `POST /login` or native `POST /api/v1/auth/login`)
authenticates at Cognito, THEN `DELETE /api/v1/account` deletes the identity
and purges the account (204), THEN the login resumes and looks for its local
row. It finds none, and login-time reconciliation — which exists to repair
registration orphans — would provision a new, empty `User` with a session for
the identity that was just deleted. Nothing the purge leaves in `user` can
stop that, the provider call cannot hold a lock, and the native credential
fence cannot either (it keys on the typed identifier, which names no row for a
renamed legacy account, and the web login has none).

**Invariant.** After a 204, the deleted Cognito subject can never again be
given a local row by any login. Formally: the state "user row gone AND no
tombstone" never exists for a subject this lifecycle removed.

**Mechanism** — `app/services/deleted_identity.py`, table
`deleted_identity_tombstone` (migration `d0e1f2a3b4c5`):

- Step 4 writes `deleted_identity.record(sub)` in the SAME transaction as
  `_purge_user`, before it, under the owner-row lock; a rollback removes both.
  When the owner row is already gone (a concurrent deletion won), the tombstone
  is still recorded before the success return. A retry after `INCOMPLETE`
  (Cognito answers `UserNotFound`) reaches step 4 like any other run and
  records it — "already absent at the provider" never skips it.
- The only two places a local row gains a provider subject from a login —
  `mobile_auth._resolve_user` and web `auth._reconcile_local_user`, in both
  their bind and create branches — call `deleted_identity.refuse_if_deleted`
  after writing the subject and before committing or issuing any session.
  A tombstoned subject is refused with the path's ordinary answer (native
  `401 AUTH_INVALID_CREDENTIALS`, internal reason `identity_deleted`; web
  `401 auth.bad_credentials`): no `User`, no family, no `CognitoSession`.
  Registration (`account_registration.register_account`) is guarded the same
  way: its subject is minted by `SignUp` in the same request, but a request
  stalled after `SignUp` could INSERT it after that identity was confirmed,
  signed in and deleted; it is refused as `IDENTITY_UNAVAILABLE` (native
  `409 AUTH_IDENTITY_UNAVAILABLE`, web 409). The set is pinned by
  `tests/test_account_deletion_architecture.py` — a fourth writer fails there.
- The existing-row path needs no check: a live row with the subject means the
  subject was not deleted. A login that found the row before the purge is
  handled by the owner-row lock (its session INSERT waits and then fails, or
  commits first and is purged).

**Why it has no check-then-create window.** The guard writes first and checks
second. `uq_user_cognito_sub` serializes that write against the purge: while
the old row holds the subject the write fails; while the purge that removes it
is uncommitted the write WAITS; so it can only succeed after the purge — and
its tombstone — committed, and the check, a later statement under the
default READ COMMITTED isolation, sees it. No lock is held across a provider
call, nothing global is locked, no new provider call is made. Proved on
PostgreSQL for web, mobile and registration
(`test_login_create_racing_the_purge_waits_for_it_then_sees_the_tombstone`).

**What is stored and for how long.** `fingerprint` = HMAC-SHA256 of the
Cognito `sub` under a subkey of the application `SECRET_KEY`
(`axisai/deleted-identity/v1`, the same subkey idiom as the persisted
pump-check/nutrition identities), and `deleted_at`. No subject, username,
e-mail, token, profile field or free text. It is a security anti-resurrection
record, not retained account data, and is kept **indefinitely** (owner
decision): a Cognito `sub` is never reissued, so a person who signs up again
gets a new subject and is not blocked, and no e-mail/username tombstone
exists. Rotating `SECRET_KEY` orphans existing fingerprints; that can only
matter to a login already in flight across the rotation, because a deleted
identity can no longer authenticate at the provider.

**Scope.** The operator `cleanup-test-users` command does not write
tombstones (unchanged; it purges test users by username).

## 7. Consistency with operator cleanup

Same identity-first order, same `_purge_user`, same session removal. Difference:
`cleanup-test-users` still does no object-store I/O (recorded debt); the native
lifecycle releases media before purging.

## 8. Tests

- `tests/test_mobile_account_deletion_api.py` — contract, A/B isolation on fully
  populated accounts (census over every user FK), resurrection, failure
  injection F1–F7, privacy of logs and bodies.
- `tests/test_account_deletion_architecture.py` — one authority, thin route,
  single callers of `delete_user` / `_purge_user`.
- `tests/test_account_deletion_pg.py` — PostgreSQL lock races, including a
  login's create waiting on an uncommitted purge and then seeing its tombstone
  (web, mobile and registration).
- `tests/test_mobile_auth_feature_gate.py`,
  `tests/test_sprint12_daily_coach_discovery.py` — route allow-lists.
- `tests/test_account_deletion_resurrection.py` — anti-resurrection: in-flight
  login race on web AND mobile, renamed legacy row, new subject for the same
  person, tombstone/purge atomicity and retry, A/B, no raw identifier stored.
- `tests/test_deleted_identity_migration.py` — migration `d0e1f2a3b4c5`
  (existing DB, fresh DB, after `create_all`, reversible).
