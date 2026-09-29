# Native registration & email verification (LP-01)

Status: implemented behind the existing `MOBILE_AUTH_ENABLED` gate (default
off). No flag, default or rollout state changed.

Companion documents: [adr/0001-native-mobile-authentication.md](adr/0001-native-mobile-authentication.md)
(envelope, login/refresh/logout), [AUTH_CONTRACT.md](AUTH_CONTRACT.md)
(web ↔ mobile agreement), [CAPACITY.md](CAPACITY.md) (blocking-slot inventory).

Executable form: `tests/test_mobile_registration_api.py`,
`tests/test_account_registration_service.py`,
`tests/test_account_registration_architecture.py`,
`tests/test_web_registration_characterization.py`,
`tests/test_web_registration_lp01_deltas.py`.

---

## 1. One authority, two transports

```
Web  POST /register, /verify, /verify/resend ─┐
                                              ├→ app/services/account_registration.py → cognito_service → Cognito
Mobile POST /api/v1/auth/{register,verify,    │
            verify/resend} ───────────────────┘
```

`account_registration` owns normalization, validation (the existing
`validators` rules), the local username/e-mail collision pre-check, the
provider call inside `blocking_concurrency_slot`, classification of provider
failures into a closed `Outcome` vocabulary, the local `User` row written at
registration, and the post-confirmation effects (pending referral, welcome
e-mail). It returns no HTTP, reads no request/session/cookie and issues no
session.

Only this module calls `cognito_service.sign_up` / `confirm_sign_up` /
`resend_code` — `tests/test_account_registration_architecture.py` scans
`app/` and `fitx_mcp/` and fails on a second caller.

Transport-specific behaviour stays at the edges:

| | Web | Mobile |
|---|---|---|
| Language fallback | body → browser session → `tr` | body (must be a supported locale) → `tr` |
| Referral input | body `ref` or `fitx_ref` cookie | none (not part of the native contract) |
| Error shape | `{"error": "<i18n sentence>"}` | ADR 0001 envelope, `AUTH_*` code |
| Provider sentence | rendered (pre-LP-01 behaviour) | never read |

---

## 2. Routes

All three are pre-authentication routes on the `mobile_api` blueprint
(`no-store`, CSRF-exempt bearer surface, shared 413/429/unhandled handlers,
approved-route allow-list). A presented `Authorization` header is ignored:
they are listed in `_MOBILE_PREAUTH_ENDPOINTS`, so no credential lookup runs
and the default limiter stays IP-keyed.

### `POST /api/v1/auth/register`

Request (JSON object; unknown keys ignored, as on login):

```json
{"username": "string", "email": "string", "password": "string", "language": "tr|en (optional)"}
```

Success — `201 Created`:

```json
{"registration": {"status": "verification_required", "username": "<as submitted>"}}
```

The provider e-mails a confirmation code. The account cannot sign in until it
is verified.

### `POST /api/v1/auth/verify`

Request: `{"username": "string", "code": "string"}` — both trimmed; username
≤ 128 and code ≤ 64 characters before any provider work.

Success — `200 OK`: `{"verification": {"status": "verified"}}`

### `POST /api/v1/auth/verify/resend`

Request: `{"username": "string"}` — trimmed, ≤ 128 characters.

Success — `202 Accepted`: `{"verification_resend": {"status": "accepted"}}`

`accepted` means "if this username has an account awaiting verification, a
new code was requested". It is the same answer for every account (§5).

---

## 3. Errors

Every error is the ADR 0001 envelope
`{"error": {"code", "message", "retryable", "request_id"}}` — no other key.

| Code | HTTP | Retryable | When |
|---|---:|---|---|
| `AUTH_INVALID_REQUEST` | 400 | false | non-JSON / non-object body, missing or non-string field, over-long username/code, unsupported `language`, blank value, provider rejected a parameter |
| `AUTH_USERNAME_INVALID` | 400 | false | register: username fails the 3–80 `[A-Za-z0-9_.-]` rule |
| `AUTH_EMAIL_INVALID` | 400 | false | register: malformed e-mail |
| `AUTH_PASSWORD_POLICY` | 400 | false | register: local policy (8–128, letter + digit) or provider password policy |
| `AUTH_IDENTITY_UNAVAILABLE` | 409 | false | register: username **or** e-mail already taken (any source) |
| `AUTH_VERIFICATION_CODE_INVALID` | 400 | false | verify: wrong, expired, unknown user, or account not confirmable |
| `AUTH_RATE_LIMITED` | 429 | true | our per-IP / per-username budget (`Retry-After` set), or the provider's own attempt limit |
| `AUTH_TEMPORARILY_UNAVAILABLE` | 503 | true | blocking capacity full (`Retry-After: 15`), provider transient or unrecognized failure, local storage failure, provider not configured, verification throttle store down |

Provider error names and sentences, stack traces, `cognito_sub`, database ids
and limiter internals never reach a client (`test_*_provider_failures_are_typed_and_sanitized`).

---

## 4. Session boundary

- Register, verify and resend never issue a credential, a cookie or a
  `MobileAuthSession`/credential row; the service references no session
  authority at all (AST-guarded).
- A verified account is not signed in: `GET /api/v1/account/me` still answers
  `AUTH_SESSION_EXPIRED`. The client signs in through the unchanged
  `POST /api/v1/auth/login`.
- Login, refresh, logout, `account/me`, token TTLs and refresh-family rotation
  are untouched.

---

## 5. Enumeration resistance

| Distinction an attacker wants | Answer |
|---|---|
| username taken vs e-mail taken vs provider `UsernameExists`/`AliasExists` vs local commit race | one `409 AUTH_IDENTITY_UNAVAILABLE` |
| wrong code vs expired code vs unknown user vs already-confirmed/disabled | one `400 AUTH_VERIFICATION_CODE_INVALID` |
| resend: pending vs unknown vs already confirmed vs provider per-account throttle | one `202 accepted` |

`CODE_EXPIRED` is a distinct internal outcome (the web still shows its own
sentence) but a single wire code: whether a code was ever issued for a
username is not something the answer may say. Client action is identical —
request a new code.

Residual, documented rather than hidden:

- **Registration must say "taken".** A user choosing a username has to learn
  it is unavailable. The answer never says *which* of username/e-mail is taken;
  usernames are already discoverable through friend search, as on web.
- **Timing.** A local collision answers before the provider round-trip, so it
  is faster than a successful registration — the same as web. Not equalized:
  doing so would require creating provider accounts. Bounded by the per-IP
  budget.
- **Verify throttle.** A provider attempt limit (`LimitExceeded`) answers 429.
  It only arises after repeated failures for one username, which our
  per-username budget (10 / 15 min) normally stops first.
- **Web is unchanged.** The browser routes keep their pre-LP-01 answers,
  including provider sentences that differ per condition. LP-01 does not
  regress them and does not widen them; tightening web is a separate change.

---

## 6. Rate limits and capacity

| Route | Per IP | Per username | Notes |
|---|---|---|---|
| register | 5 / hour | — | same as web |
| verify | 10 / 15 min | 10 / 15 min, charged on 400 only | fails closed (503) when the distributed throttle store is unreachable and `LOGIN_FAIL_CLOSED` is on — code guessing is credential guessing |
| resend | 3 / 15 min | 3 / 15 min, every request | stops a resend flood at one inbox from many IPs |

The username key is `strip().lower()` of the submitted value (like login), and
falls back to the IP when absent or over-long; it is always stacked on an IP
limit, never alone.

Global capacity: every provider round-trip — web and mobile — runs inside the
shared `blocking_concurrency_slot` (only the network call; no DB work while a
permit is held; the pre-check's read transaction is closed first). A full slot
answers `503` + `Retry-After: 15` without calling the provider.

---

## 7. Repeat requests

| Request | Behaviour |
|---|---|
| register twice | first 201, then 409 (`AUTH_IDENTITY_UNAVAILABLE`); one local row, one provider account |
| register → 503 after the provider account was created (local commit failed) | a retry answers 409; the account exists at the provider and can be verified and signed in (login reconciles the missing local row) |
| verify after success | whatever the provider answers for a confirmed account; it maps to `AUTH_VERIFICATION_CODE_INVALID` — the client should proceed to login |
| resend repeatedly | 202 each time until a budget answers 429 |

No outcome is fabricated: an unrecognized provider answer is 503, never
success.

---

## 8. Logging

One line per outcome from the service:
`account_registration event=<register|confirm|resend> outcome=<outcome> request_id=<id>`.
Never a password, code, e-mail or token (`test_no_password_code_or_email_reaches_the_logs`).
The pre-existing web lines (Cognito orphan on commit failure, welcome-email
`user=<username> to=<masked>`) are unchanged.

---

## 9. Web deltas

Everything browser-visible is pinned by
`tests/test_web_registration_characterization.py` (written against the
pre-extraction code). Two deliberate differences, `tests/test_web_registration_lp01_deltas.py`:

1. Saturated blocking capacity → `503 {"error": auth.service_busy}` +
   `Retry-After: 15` (previously the request parked a thread on the provider).
2. Non-string JSON field values → the route's existing "fields required" 400
   (previously an unhandled `AttributeError`/`TypeError` 500, or a list
   password length-checked as text).

---

## 10. Not in LP-01

Password recovery (LP-02 — [MOBILE_PASSWORD_RECOVERY.md](MOBILE_PASSWORD_RECOVERY.md)), Flutter signup/verification UI (LP-07),
onboarding (LP-03/LP-08), referral input on native, account deletion, any
flag/rollout change. Native login still answers an unconfirmed account with
`AUTH_INVALID_CREDENTIALS` (pinned by `test_mobile_unconfirmed_and_invalid_logins_are_indistinguishable`);
ADR 0001's `AUTH_VERIFICATION_REQUIRED` row is not emitted today — LP-07 must
offer "verify your account" from its own state (e.g. after register), not from
a login error.
