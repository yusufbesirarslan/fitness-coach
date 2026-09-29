# Native onboarding profile (LP-03)

Status: implemented behind the existing `MOBILE_AUTH_ENABLED` gate (default
off). No flag, default, rollout state, model, column or migration changed
(Alembic head unchanged).

Companion documents: [adr/0001-native-mobile-authentication.md](adr/0001-native-mobile-authentication.md)
(envelope, `account/me`), [MOBILE_REGISTRATION.md](MOBILE_REGISTRATION.md)
(LP-01: the account this flow starts from),
[mobile/training-plan-generation-command.md](mobile/training-plan-generation-command.md)
(the first-plan command whose prerequisite this clears).

Executable form: `tests/test_mobile_account_profile_api.py`,
`tests/test_account_profile_service.py`,
`tests/test_account_profile_architecture.py`,
`tests/test_mobile_onboarding_first_plan.py`,
`tests/test_web_setup_characterization.py`.

Target flow:

```
verified account → POST /api/v1/auth/login → GET /api/v1/account/me (profile_complete=false)
  → PUT /api/v1/account/profile → profile_complete=true → POST /api/v1/training/plans accepted
```

---

## 1. One authority, two transports

```
Web     POST /setup ─────────────────┐
                                     ├→ app/services/account_profile.py → User + canonical UserSession (one transaction)
Mobile  PUT /api/v1/account/profile ─┘
```

`account_profile` owns the token vocabulary, validation (`OnboardingProfile`
— construction is validation), the BMR/TDEE/target/plan-text calculation
calls, the atomic write and the readiness rule. It imports no Flask, i18n or
logging and returns no HTTP. The adapters parse their own wire format, build
an `OnboardingProfile` and render the typed failures (`ProfileRejected`,
`ProfilePersistenceFailed`). `tests/test_account_profile_architecture.py`
fails if a transport regains domain logic or a second module touches
`profile_complete`.

## 2. The ONE readiness rule

```
onboarding_state(user).complete  ⇔  user.profile_complete  AND  canonical UserSession exists
```

The canonical session is the owner's newest `UserSession` (`created_at` desc,
then `id` desc) — the row every consumer already reads.

Before LP-03 two rules answered one question: `account/me`, the `/` gate and
`GET /setup` read the flag; both first-plan generators (native
`generate_and_persist`, browser `POST /training-plan`) only checked that a
session existed. Each could say yes while the other said no:

| Persisted state | Source | Before | After |
|---|---|---|---|
| flag, no session | old `/setup` failed between its two commits | `me`=complete, first plan refused | incomplete everywhere → `/` sends the user to `/setup` to repair |
| session, no flag | legacy `/chat` form (no UI caller left) | `me`=incomplete, first plan allowed | incomplete everywhere (the `/` gate already sent them to `/setup`) |
| both | normal onboarding | complete | complete |

Consumers, all through `onboarding_state`: `GET /api/v1/account/me` and the
`PUT` response (`account_projection`), `GET /setup` redirect, the `/` gate,
`mobile_training_generation.service._required_session`, and the browser
`POST /training-plan` prerequisite. The service still writes the flag, in the
same transaction as the session, so a code rollback to pre-LP-03 readers sees
consistent data.

## 3. `PUT /api/v1/account/profile`

Bearer-only (`require_mobile_auth`) on the `mobile_api` blueprint: `no-store`,
CSRF-exempt bearer surface, shared 413/429 handlers, approved-route
allow-list. The owner is `g.mobile_user` and nothing else.

Request — a JSON object with exactly these keys:

| Key | Type | Rule |
|---|---|---|
| `weight_kg` | number | finite, 20–500 (canonical body-weight range, shared with the weight log) |
| `height_cm` | number | finite, > 0 |
| `age` | integer | > 0 (JSON integer; `30.0` is refused) |
| `gender` | string | `male` \| `female` |
| `goal` | string | `lose_weight` \| `build_muscle` |
| `fitness_level` | string | `beginner` \| `intermediate` \| `advanced` |
| `activity_level` | string | `sedentary` \| `active` \| `very_active` |
| `target_weight_kg` | number \| null, optional | finite, 20–500; absent or `null` keeps the stored value |

Any other key is refused — including `user_id`, `account_id`, `owner_id`,
`email`, `username`, `profile_complete` — never silently ignored. Booleans are
not numbers. Numeric strings are not numbers.

Success — `200`, the `account/me` projection (byte-for-byte what the next
`GET /api/v1/account/me` returns):

```json
{"user": {"username": "…", "display_name": "…", "profile_complete": true,
          "preferred_language": "tr", "goal": "lose_weight", "goal_type": "loss"}}
```

Errors (ADR 0001 envelope `{code, message, retryable, request_id}`):

| HTTP | Code | Retryable | When |
|---|---|---|---|
| 400 | `PROFILE_INVALID_REQUEST` | false | not a JSON object, missing/unknown/authority key, wrong JSON type, non-finite number |
| 422 | `PROFILE_INVALID_VALUE` | false | well-typed but unsupported token, or outside a canonical rule |
| 503 | `PROFILE_TEMPORARILY_UNAVAILABLE` | true | the transaction did not commit (rolled back; safe to retry) |
| 401 | `AUTH_SESSION_EXPIRED` / … | — | Bearer missing/invalid (middleware) |
| 413 | `REQUEST_TOO_LARGE` | false | body above `MAX_CONTENT_LENGTH` |
| 429 | `AUTH_RATE_LIMITED` | true | default per-user limit |

Messages are static; no error names or echoes a submitted value.

Idempotent: a repeat — same or revised — updates the same canonical session
and returns the same projection; it never creates a second `UserSession`.

Logging: success logs nothing; failure logs one line
`mobile_account_profile event=profile_write_failed error_type=<class> request_id=<id>`
— no profile value, token, username or exception text.

## 4. Vocabulary

Gender, fitness level and activity level already are locale-independent
English tokens in the domain, so the wire token is the stored value. The goal
is not: calculations, plan text and `nutrition_targets` branch on the stored
Turkish literals. LP-03 does not rewrite that domain; the token is mapped at
the boundary, only inside `account_profile`:

| Native token | Stored value (unchanged) | `goal_type` |
|---|---|---|
| `lose_weight` | `kilo verme` | `loss` |
| `build_muscle` | `kas kazanma` | `gain` |

Token names derive from the existing English labels (`setup.goal_loss` =
"Lose Weight", `setup.goal_gain` = "Build Muscle"). No other goal exists in
the domain and none was invented. The stored literals are refused on the
native wire (`PROFILE_INVALID_VALUE`).

**Contract correction:** `GET /api/v1/account/me` `goal` now carries the token
(or `null` for a stored value that is not a canonical goal) instead of the raw
Turkish literal, so a native client is never coupled to it. The key set is
unchanged and the value is still a nullable string: the Flutter client
(`axisai-mobile` `auth_contract_parser.dart`) reads `goal` as a required
nullable string into `AuthAccount.goal` and has no other consumer of it.

## 5. Numeric validation — what is and is not canonical

Searched for existing backend rules before adding any:

- **Body weight** — canonical: `tracking._parse_weight` (the weight log)
  refuses anything outside 20–500 kg. Onboarding weight and target weight now
  use the same constant (`account_profile.BODY_WEIGHT_MIN_KG/MAX_KG`), and the
  weight log imports it — one number, one owner.
- **Height, age** — no backend rule exists. The only bounds in the repository
  are the browser wizard's client-side `min`/`max` hints (height 100–240, age
  13–100), which are presentation, not authority. LP-03 enforces only the
  structural minimum: finite and positive, age a whole number. That is what
  keeps the BMR formula and the stored row meaningful (the old route crashed
  on `inf`/`nan` after marking the profile complete). **Follow-up:** a
  product-owned range policy for height and age.

## 6. Browser `/setup` — kept, and two intentional integrity fixes

The browser keeps its wire format, its messages and its leniencies (numeric
strings; a fractional JSON age is truncated; an unusable optional target
weight is dropped, not fatal). Pinned before extraction by
`tests/test_web_setup_characterization.py`.

Intentional integrity fixes (authorized):

- **A. Atomic.** Profile columns, the canonical session and the completeness
  flag commit in one transaction. The old route committed the flag first; a
  failure before the session commit left an account reported complete that
  could not generate a plan.
- **B. Repeat-safe.** A repeated submission updates the canonical session in
  place (`created_at` and any `coach_reply` kept) instead of appending a new
  `UserSession` every time.

Fail-closed input on the browser route, unreachable from the wizard itself
(which only posts its fixed options and clamps weight 30–300, height 100–240,
age 13–100 client-side): an unknown goal/gender/level/activity is now `400`
instead of being persisted; weight outside 20–500 is `400`
(`route.weight_range`); a non-finite or non-positive number is `400`
(`route.body_numeric`) instead of a crash after a half-commit; a JSON array
body answers the missing-field `400` instead of a `500`; an out-of-range
optional target weight is dropped like an unparseable one.
