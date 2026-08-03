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

## 3. The one accidental difference — expiry leeway

`cognito_jwt.validate_token` takes `leeway_seconds`, the window in which an
already-expired token is still accepted.

| Path | Leeway | Source |
|---|---|---|
| Web request auth | `0`, not configurable | `auth_contract.WEB_CLOCK_SKEW_SECONDS` |
| Mobile request auth | `MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS`, default `0`, maximum `300` | `app/services/mobile_credentials.py` |
| Web login (ID token) | `0`, pinned | `app/blueprints/auth.py` |
| Shared login decode | `0`, pinned | `app/services/cognito_service.py` |

Three of the four are pinned at zero. Only the mobile request path can be
widened, and widening it means mobile accepts expired tokens that web rejects —
with no web equivalent and, before PR3, no signal that it had happened.

**PR3 does not change the values.** With the default configuration every path
uses `0`, and `test_both_paths_record_the_same_token_use` asserts it. What
changed is that the divergence can no longer be silent:

- both paths read their leeway from `auth_contract.validation_clock_skew_seconds`,
  so the two numbers are decided in one file;
- `test_only_the_contract_module_varies_the_expiry_leeway` fails CI if any
  other module passes `leeway_seconds` at all;
- boot logs one `[AUTH_CONTRACT]` line, and a `WARNING` when the paths disagree;
- `/health?deep=1` publishes `auth_contract` with both numbers and
  `clock_skew_aligned`.

### Open decision for review

Converging is a three-line change inside
`auth_contract.validation_clock_skew_seconds`, and there are two directions:

1. **Pin mobile to zero as well.** Uniform and strictest. Costs the operator
   the only remaining lever for provider clock drift.
2. **Give both paths one shared knob.** Uniform and configurable — but a knob
   named for mobile would then widen the browser path too, which is a security
   loosening arriving through an unrelated-looking setting.

Recommendation: **option 1**, on the evidence that the knob has never been set
away from its default and that three of the four validation paths already
ignore it. It is left undone here because it is a behaviour change and PR3 is
scoped to verification; the alternative — landing it silently inside a test PR
— is exactly the kind of change this document exists to prevent.

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
  Expect `aligned=yes`.
- `/health?deep=1` carries the same values in its `auth_contract` block. It is
  restricted to internal networks; there is deliberately no public equivalent.
- Changing `MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS` needs no deploy — it
  lives in the host `.env` like the rollout flags — so the boot line and the
  deep-health block are the only reliable ways to see its current value.
