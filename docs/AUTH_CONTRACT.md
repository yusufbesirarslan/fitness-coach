# Authentication contract — web and mobile

One canonical statement of what the two clients agree on, what they
deliberately do differently, and what is enforced in CI.

Companion documents: [cognito.md](cognito.md) (web flow),
[adr/0001-native-mobile-authentication.md](adr/0001-native-mobile-authentication.md)
(mobile flow), [OBSERVABILITY.md](OBSERVABILITY.md) (metrics),
[FEATURE_FLAGS.md](FEATURE_FLAGS.md) (`MOBILE_AUTH_ENABLED` lifecycle).

Executable form: `tests/test_auth_contract.py`. Where this document and that
file disagree, the test is right and this document is a bug.

---

## 0. What was actually found

The hardening brief assumed native mobile auth "may drift from web Cognito
behaviour" and that the two would need converging. They do not. Both clients
already route through **one validator**, `app/services/cognito_jwt.py`, with
one JWKS cache and one key-rotation path. There is no duplicated validation to
remove.

What was missing is different, and narrower: nothing *enforced* the agreement.
Each middleware re-derived for itself which failures are temporary, the web
path hardcoded its expiry leeway while the mobile path read a knob, and no test
compared the two. The paths agreed by coincidence of authorship, not by
construction — so PR3 is a verification PR, not a refactor.

`app/services/auth_contract.py` is the single place those shared decisions now
live. It owns the required token use, the expiry leeway, the
transient/definitive split and the metric vocabulary. It does **not** own the
response shape.

---

## 1. Shared and enforced

| Property | Value | Enforced by |
|---|---|---|
| Validator | `app/services/cognito_jwt.py` — the only one | `test_no_second_jwt_validator_exists` |
| Validated | signature, `exp`, `iss`, `token_use`, `aud`/`client_id` | `tests/test_cognito_jwt.py` |
| Request token use | `access` on both paths | `test_both_paths_require_an_access_token_not_an_id_token` |
| Login token use | `id` on both paths | `app/services/cognito_service.py` (shared) |
| Key rotation | unknown `kid` triggers exactly one JWKS refetch | `tests/test_cognito_jwt.py` |
| Temporary outage | `jwks_unavailable` → 503 + `Retry-After`, session preserved | `test_both_paths_survive_a_temporary_jwks_outage` |
| Definitive rejection | session destroyed (web row deleted, mobile family revoked) | `test_both_paths_destroy_the_session_on_a_definitive_rejection` |
| Identity binding | verified `sub` must resolve to the same local user | `test_both_paths_bind_the_verified_subject_to_the_local_user` |
| Provider tokens | stored server-side, Fernet-encrypted, never returned to a client | `app/services/session_store.py` |
| Blast radius | one client's rejection never touches the other's session | `test_web_session_row_is_untouched_by_a_mobile_rejection` |

**`jwks_unavailable` is the load-bearing distinction.** "The signature could
not be verified" is not "the signature is invalid". A cold JWKS cache is real
on every fresh container — that is, after every deploy — and mapping it to a
rejection produces a correlated mass logout across the whole population at once.
Both paths must answer 503 and leave the session alone.

---

## 2. Intentional differences — keep

These are not drift. Converging them would break a client.

| Difference | Web | Mobile | Why it stays |
|---|---|---|---|
| Credential | Flask-Login cookie + `cognito_sid` | opaque bearer credential, hashed rotating refresh family | Browsers have cookies and CSRF; native apps have neither |
| Failure response | redirect to `/login`, or `{"error": "<i18n text>"}` | `{"code", "message", "retryable", "request_id"}` | ADR 0001 specifies the machine-readable envelope; a browser cannot use it and a native client cannot follow a redirect |
| Revocation unit | one session row | a refresh family | Mobile rotates refresh credentials; family revocation is what makes rotation meaningful |
| Identifier | internal row id | `public_id` only | Sequential ids must never leave the server |
| CSRF | enforced on state-changing requests | exempt (bearer, `no-store`) | A bearer credential is not sent ambiently by the client |

Pinned by `test_error_shapes_stay_different_on_purpose` and
`test_mobile_boundary_never_accepts_a_browser_session`, so "intentional" stays
a decision rather than an assumption.

---

## 3. Expiry leeway — one value, pinned to zero

`cognito_jwt.validate_token` takes `leeway_seconds`, the window in which an
already-expired token is still accepted. It is **zero on every path**, and
there is no setting that can change it.

| Path | Leeway | Source |
|---|---|---|
| Web request auth | `0` | `auth_contract.REQUEST_CLOCK_SKEW_SECONDS` |
| Mobile request auth | `0` | `auth_contract.REQUEST_CLOCK_SKEW_SECONDS` |
| Web login (ID token) | `0`, pinned default | `app/blueprints/auth.py` |
| Shared login decode | `0`, pinned default | `app/services/cognito_service.py` |

This is the one **accidental** difference PR3 found. The mobile request path
used to read `MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS` (default `0`, maximum
`300`) while web had no equivalent, so setting it made mobile accept expired
tokens that web rejected — with no web counterpart and no signal it had
happened. Three of the four paths already ignored the knob.

**It was retired rather than generalised.** A shared configurable knob would
have been uniform, but a mobile-named setting would then have widened the
browser's expired-token window too — a security loosening arriving through an
unrelated-looking lever. Leeway is a security decision, not a tuning parameter.
If production evidence later shows non-zero leeway is genuinely required, that
is a separate, security-reviewed change applying to **both** paths at once.

Enforced by:

- `auth_contract.validation_clock_skew_seconds` returns the pinned value and
  consults no configuration — `test_supplying_the_retired_setting_cannot_reintroduce_leeway`
  proves the key cannot beat it even if it reappears in `app.config`;
- `test_only_the_contract_module_varies_the_expiry_leeway` fails CI if any
  other module passes `leeway_seconds` at all;
- `test_nothing_writes_the_retired_key_into_config` — a booted application does
  not carry the key, so nothing can read it back;
- boot logs `[AUTH_CONTRACT] token_use=access skew=0s paths=web,mobile configurable=no`;
- `/health?deep=1` publishes the same values under `auth_contract`.

### Migration prerequisite — read before deploying

`MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS` is **rejected at boot**, not
ignored. A retired setting that is silently dropped is worse than one that is
still honoured: the host believes mobile tolerates expired tokens and nothing
contradicts it.

| Value in the host `.env` | Result |
|---|---|
| absent | boots normally — nothing to do |
| `=0` | boots normally (agrees with the pinned contract); removing the line is still tidier |
| any non-zero value | **boot fails** with a message naming the key and this document |
| empty, or non-numeric | **boot fails** — the same strictness the setting had before retirement |

The check runs **unconditionally**, not behind `MOBILE_AUTH_ENABLED`. A host
with the mobile flag off can still be carrying a stale non-zero value, and that
is precisely the value that would take effect the day mobile auth is switched
on — the worst possible moment to discover it.

**Action before deploying:** remove the line from the host `.env`. The
pre-deployment scan in [ROLLOUT.md](ROLLOUT.md) §1 checks it alongside the
rollout flags. If it is missed, the deploy health gate fails and rolls back to
the previous commit automatically — a rolled-back deploy, not an outage, but
finding it first is cheaper.

---

## 4. Where the validator may be called from

`auth_contract.VALIDATOR_CALL_SITES` declares every module allowed to call
`cognito_jwt.validate_token`, with its reason:

| Module | Why |
|---|---|
| `app/services/auth_contract.py` | the canonical request-authentication call for both clients |
| `app/blueprints/auth.py` | web login identity assertion (ID token, pinned leeway 0) |
| `app/services/cognito_service.py` | shared provider login decode used by both clients (ID token, pinned leeway 0) |

`test_only_declared_modules_call_the_validator` derives its allow-list from
that mapping rather than keeping a second copy, and fails in **both**
directions — an undeclared call site, and a declared site that no longer
exists. Adding a call site therefore means declaring it and its reason, which
is the same data-driven rule the flag registry uses for `parsed_by`.

---

## 5. Metrics

`AuthOutcomes`, namespace `FitX/Runtime`, emitted through PR1's buffered
emitter (off unless `RUNTIME_METRICS_ENABLED=1`).

Dimensions: `Path` ∈ {`web`, `mobile`} × `Outcome` ∈ {`ok`, `no_identity`,
`session_invalid`, `token_rejected`, `provider_unavailable`}. Ten series, fixed.

Never a user id, a token, an `Authorization` header or a raw path — asserted by
`test_auth_outcomes_carry_no_user_identity`. `Path` is derived from which
boundary served the request, never from a client-supplied header.

This answers what `HttpRequests` cannot: a 503 on an auth route could be an
overloaded gate or an unreachable provider, and a 401 could be an expired
session or a rejected token. `provider_unavailable` rising while
`token_rejected` stays flat is an outage; the reverse is worth investigating as
an attack.

Throttling is deliberately absent — a 429 is not an authentication outcome and
PR1 already counts it as `HttpThrottled`.

`test_metric_failure_never_breaks_authentication` pins the obvious invariant:
instrumentation never decides whether a request is authenticated.

---

## 6. Operator notes

- `MOBILE_AUTH_ENABLED=0` (current production default) means the `/api/v1`
  blueprint is not registered at all: there is no mobile boundary to diverge.
  Activation is blocked until PR4 — see [ROLLOUT.md](ROLLOUT.md) §3.
- After a restart, confirm the contract from the boot log:
  `docker compose logs --no-color --tail 40 web | grep '\[AUTH_CONTRACT\]'`.
  Expect `skew=0s paths=web,mobile configurable=no`.
- `/health?deep=1` carries the same values in its `auth_contract` block. It is
  restricted to internal networks; there is deliberately no public equivalent.
- `MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS` is **retired**. Remove it from the
  host `.env` before deploying this change; a non-zero or malformed value stops
  the boot. See §3 for the full table and the reasoning.
