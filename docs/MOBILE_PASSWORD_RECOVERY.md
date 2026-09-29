# Native password recovery (LP-02)

Status: implemented behind the existing `MOBILE_AUTH_ENABLED` gate (default
off). No flag, default, migration or rollout state changed.

Companion documents: [MOBILE_REGISTRATION.md](MOBILE_REGISTRATION.md) (LP-01,
same conventions), [adr/0001-native-mobile-authentication.md](adr/0001-native-mobile-authentication.md)
(envelope, login/refresh/logout), [AUTH_CONTRACT.md](AUTH_CONTRACT.md)
(web ↔ mobile agreement), [CAPACITY.md](CAPACITY.md) (blocking-slot inventory).

Executable form: `tests/test_mobile_password_recovery_api.py`,
`tests/test_account_recovery_service.py`,
`tests/test_account_recovery_architecture.py`,
`tests/test_web_recovery_characterization.py`,
`tests/test_web_recovery_lp02_deltas.py`, plus the pre-existing
`tests/test_password_recovery.py` / `tests/test_password_reset.py`
(revocation, credential fence, password-changed notice).

---

## 1. One authority, two transports

```
Web  POST /forgot-password, /reset-password ─┐
                                             ├→ app/services/account_recovery.py → cognito_service → Cognito
Mobile POST /api/v1/auth/password/{forgot,   │
            reset} ──────────────────────────┘
```

`account_recovery` owns identifier normalization (a username is used as
submitted; an e-mail is lower-cased and replaced by the username of the local
account carrying it, else used as-is), the canonical password policy
(`validators.validate_password` — the rule registration uses), the provider
call inside `blocking_concurrency_slot`, classification of provider failures
into a closed `Outcome` vocabulary, the non-enumeration rule for reset
requests, and what a successful reset does to existing sessions (§4). It
returns no HTTP, reads no request/session/cookie and issues no session.

Only this module calls `cognito_service.forgot_password` /
`confirm_forgot_password`, and only it runs `mobile_auth.revoke_all_for_user`
(the credential-change sweep) — `tests/test_account_recovery_architecture.py`
scans `app/` and `fitx_mcp/` and fails on a second caller.

Transport-specific behaviour stays at the edges:

| | Web | Mobile |
|---|---|---|
| Identity at reset | the canonical identity stored in the browser reset context by `/forgot-password` (15 min) | the identifier submitted again with the code |
| Password confirmation field | required, must match | none (a client concern) |
| After success | Flask-Login logout, browser session cleared, flash | nothing — no cookie exists |
| Error shape | `{"error": "<i18n sentence>"}` | ADR 0001 envelope, `AUTH_*` code |
| Validator sentence | rendered | never read |

---

## 2. Routes

Both are pre-authentication routes on the `mobile_api` blueprint (`no-store`,
CSRF-exempt bearer surface, shared 413/429/unhandled handlers, approved-route
allow-list). A presented `Authorization` header is ignored: they are listed in
`_MOBILE_PREAUTH_ENDPOINTS`, so no credential lookup runs and the default
limiter stays IP-keyed.

### `POST /api/v1/auth/password/forgot`

Request (JSON object; unknown keys ignored, as on login):

```json
{"identifier": "username or e-mail"}
```

`identifier` is trimmed; ≤ 254 characters before any provider work.

Success — `202 Accepted`:

```json
{"password_reset": {"status": "accepted"}}
```

`accepted` means "if this identifier names an account that can be recovered,
a reset code was requested". It is the same answer for every account (§5).

### `POST /api/v1/auth/password/reset`

Request:

```json
{"identifier": "string", "code": "string", "new_password": "string"}
```

`identifier` and `code` are trimmed (≤ 254 / ≤ 64 characters); the password is
used exactly as sent and is bounded by the policy itself (8–128). The
identifier resolves to the same account the forgot request targeted, whether
the user types the username or the e-mail.

Success — `200 OK`:

```json
{"password_reset": {"status": "completed"}}
```

The password changed and every session issued under the old one is revoked.
The caller is **not** signed in: the client returns to its normal login screen
and signs in through the unchanged `POST /api/v1/auth/login`.

---

## 3. Errors

Every error is the ADR 0001 envelope
`{"error": {"code", "message", "retryable", "request_id"}}` — no other key. No
new code was added; all are LP-01 / ADR codes.

| Code | HTTP | Retryable | When |
|---|---:|---|---|
| `AUTH_INVALID_REQUEST` | 400 | false | non-JSON / non-object body; missing, non-string, blank or over-long `identifier` / `code`; missing or non-string `new_password` |
| `AUTH_PASSWORD_POLICY` | 400 | false | reset: local policy (8–128, letter + digit) or provider password / password-history policy |
| `AUTH_VERIFICATION_CODE_INVALID` | 400 | false | reset: wrong code, expired code, unknown account, unconfirmed / disabled account, account without a verified e-mail |
| `AUTH_RATE_LIMITED` | 429 | true | our per-IP / per-identifier budget (`Retry-After` set), or the provider's own attempt limit on reset |
| `AUTH_TEMPORARILY_UNAVAILABLE` | 503 | true | blocking capacity full (`Retry-After: 15`), reset: provider transient or unrecognized failure, provider not configured, reset throttle store down, local storage failure |
| `AUTH_TEMPORARILY_UNAVAILABLE` | 503 | **false** | reset only: the password **did** change but open sessions could not be revoked (§4). Replaying the spent code cannot help; the client restarts recovery, whose next success revokes them |

Provider error names and sentences, stack traces, `cognito_sub`, database ids,
validator sentences and limiter internals never reach a client
(`test_reset_provider_failures_are_typed_and_sanitized`,
`test_forgot_provider_failures_never_leak_either`).

---

## 4. Session boundary and revocation

- Neither route issues a credential, a cookie or a `MobileAuthSession` /
  `CognitoSession` / credential row; the service references no issuing
  authority (AST-guarded, with an explicit allow-list of the revocation members
  it may touch).
- **Revocation is preserved, not invented.** Before LP-02 the web reset already
  revoked every session issued under the old credential (PR #312, F12). LP-02
  moved that authority, unchanged, into the service, so the native reset runs
  exactly the same steps:
  1. `mobile_auth.revoke_all_for_user` — bumps `User.credential_epoch` (the
     fence that refuses a login which authenticated with the old password
     while the reset ran) and revokes every live native family;
  2. every browser `CognitoSession` row is deleted;
  3. stored provider refresh tokens are revoked, best-effort, bounded
     (`PROVIDER_REVOKE_LIMIT` = 20), inside the blocking slot — a capacity or
     provider failure here never fails the reset;
  4. the password-changed notice is sent, best-effort.
  Steps 1–2 are authoritative: if storage fails there the answer is the
  non-retryable 503 above, never success, and no notice is sent.
- Revocation is keyed on the local account whose `username` equals the
  resolved identity — the same key the credential fence uses. A reset for an
  identity with no local row changes the provider password and revokes
  nothing locally (there is nothing local to revoke).
- Login, refresh, logout, `account/me`, token TTLs and refresh-family rotation
  are untouched.

---

## 5. Enumeration resistance

| Distinction an attacker wants | Answer |
|---|---|
| forgot: known vs unknown vs verified vs unverified vs disabled vs local row without provider identity vs provider `UserNotFound` vs undeliverable vs per-account throttle vs provider/Lambda failure | one `202 accepted` — status, body and headers identical |
| reset: wrong vs expired code vs unknown account vs unconfirmed / disabled account | one `400 AUTH_VERIFICATION_CODE_INVALID` |

Why forgot absorbs even provider failures: with `PreventUserExistenceErrors`
enabled the provider only reaches account state (unconfirmed, no verified
e-mail, disabled, delivery, the Lambda e-mail sender, the per-account send
limit) for accounts that exist, so ANY provider error surfacing would say
"this account exists". The service therefore swallows every provider answer
for a reset request — for both transports — and logs its class. The only
non-202 answers are request-shape errors, our own capacity refusal and our own
rate limits, none of which depend on the account. Known and unknown
identifiers take the same path: exactly one provider round-trip each, no local
short-circuit.

Residual, documented rather than hidden:

- **A provider outage is invisible to the forgot caller** (202, no e-mail
  arrives). This is the web behaviour since Sprint 3 and the price of the rule
  above; operators see `account_recovery event=request outcome=provider_unavailable`.
- **Reset: the provider's own attempt limit** (`LimitExceeded` /
  `TooManyFailedAttempts`) answers 429 and only arises for an existing account
  after repeated wrong codes. Our per-identifier failure budget (5 / 15 min,
  identical for every identifier) is set to be the first wall a guesser meets.
  Same residual as LP-01 verify.
- **Timing.** The e-mail → username lookup is the same single read for known
  and unknown alike, and both then make the same provider round-trip, which
  dominates. Not artificially equalized (no repository policy requires it).
- **Web reset.** The browser keeps its pre-LP-02 answers (§9), including
  distinct 429 for a provider throttle; it already required a reset context.

---

## 6. Rate limits and capacity

| Route | Per IP | Per identifier | Notes |
|---|---|---|---|
| forgot | 5 / 15 min | 3 / 15 min, every request | stops a reset-mail flood at one inbox from many IPs |
| reset | 10 / 15 min | 5 / 15 min, charged only on `AUTH_VERIFICATION_CODE_INVALID` | fails closed (503) when the distributed throttle store is unreachable and `LOGIN_FAIL_CLOSED` is on — code guessing is credential guessing |

Per-IP budgets are the browser routes' budgets. The identifier key is
`strip().lower()` of the submitted value and falls back to the IP when absent
or over-long; it is always stacked on an IP limit, never alone, and it is keyed
on what was submitted, never on a lookup — so its answer is identical for
existing and non-existing accounts. Successful resets and policy failures do
not consume the reset budget.

Global capacity: every recovery provider round-trip — web and mobile — runs
inside the shared `blocking_concurrency_slot` (only the network call; the
identifier lookup's read transaction is closed first; revocation and the
notice run after the permit is released). A full slot answers `503` +
`Retry-After: 15` without calling the provider.

---

## 7. Repeat requests

| Request | Behaviour |
|---|---|
| forgot repeatedly | 202 each time until a budget answers 429; the provider decides whether a new code is sent |
| reset after success (e.g. an uncertain network result) | whatever the provider answers for a spent code — `AUTH_VERIFICATION_CODE_INVALID`. No idempotent success is fabricated; local state is not touched again. The client should offer login (the password may already be the new one) |
| reset → 503 retryable (capacity / provider) | the provider did not change the password; retry the same request |
| reset → 503 non-retryable (sessions not revoked) | the password changed; restart recovery |

No database migration: recovery state lives at the provider; the only local
writes are the existing revocation effects.

---

## 8. Logging

One line per outcome from the service:
`account_recovery event=<request|reset> outcome=<outcome> request_id=<id>`.
Never an identifier, password, code or token
(`test_no_password_code_or_identifier_reaches_the_logs`). The relocated
pre-existing lines are unchanged: `[AUTH] şifre değişti ama oturumlar
kapatılamadı (user=<id>)`, the provider-revoke warnings (`user=<id>`), and the
password-changed notice (`user=<username> to=<masked>`).

---

## 9. Web deltas

Everything browser-visible is pinned by
`tests/test_web_recovery_characterization.py` (written and passing against the
pre-extraction code). Three deliberate differences,
`tests/test_web_recovery_lp02_deltas.py` (each fails on the base):

1. Saturated blocking capacity → `503 {"error": auth.service_busy}` +
   `Retry-After: 15`. Previously the forgot/confirm calls were the last
   ungated blocking Cognito calls and parked a thread.
2. Non-string JSON field values → the route's existing "required" 400
   (previously an unhandled `AttributeError` → 500).
3. Provider `PasswordHistoryPolicyViolationException` → the password-policy
   sentence (previously "code invalid or expired", which sent the user to
   request a code that would fail the same way).

Two existing tests were repointed at the relocated helpers (their assertions
are unchanged): `test_password_recovery.py` now patches the service's slot —
admitting the password change, refusing the advisory revocation — and reads
`account_recovery.PROVIDER_REVOKE_LIMIT`; the PG race test calls
`account_recovery.revoke_all_sessions_after_credential_change`.

---

## 10. Not in LP-02

Flutter recovery screens, any change to login/refresh/logout, a new revocation
policy, account deletion, any flag/rollout change.

Known pre-existing gap, not changed here: revocation and the credential fence
match the local `username` exactly. If the Cognito pool treats usernames
case-insensitively (not recorded in IaC), a reset requested as `alice` for the
account `Alice` changes the password but does not find the local row to
revoke. It affects web and native equally and predates LP-02; see the handoff
for the recommended follow-up.
