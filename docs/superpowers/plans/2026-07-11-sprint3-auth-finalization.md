# Sprint 3 Authentication Finalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete native Cognito password recovery, remove local-password authentication, bind all protected requests to verified Cognito identity, enforce session expiry, and finish the security/authorization/documentation audits.

**Architecture:** Forgot Password hands a canonical Cognito username to Reset Password through short-lived Flask session state. Successful confirmation deletes every application-managed session for the user, clears the browser session, and requires a fresh login. Cognito remains the sole credential authority; `@require_auth` validates and binds JWT `sub`, Flask user, and `CognitoSession` on every protected request.

**Tech Stack:** Flask, Flask-Login, Flask-Limiter, SQLAlchemy, boto3 Cognito IDP, Authlib JOSE, Fernet, Jinja, vanilla JavaScript, pytest.

## Global Constraints

- Do not introduce Cognito Hosted UI, OAuth redirects, or a new design language.
- Preserve existing auth layout, spacing, typography, colors, responsive behavior, language switch, theme control, CSRF integration, and accessibility conventions.
- Cognito is the only credential authority; runtime authentication must never read `password_hash`.
- Keep the nullable `User.password_hash` column and its migration history for compatibility.
- Every protected request must resolve the local user through the verified Cognito `sub`.
- Never log identifiers, passwords, reset codes, or Cognito tokens.
- Tests are hermetic and must not use live AWS or JWKS endpoints.
- No database migration is expected.
- Run focused tests after every task and commit only green stages.

---

## File Responsibility Map

- `app/services/cognito_service.py`: canonical Cognito API calls and safe provider-error mapping.
- `app/blueprints/auth.py`: registration, login, verification, password-recovery routes, reset state, and logout.
- `app/services/session_store.py`: encrypted token rows, user binding, expiry, and bulk invalidation.
- `app/auth_middleware.py`: protected-request validation and canonical `cognito_sub` resolution.
- `app/config.py` / `.env.example`: Cognito and session-security configuration.
- `templates/forgot_password.html` / `templates/reset_password.html`: auth-card recovery pages.
- `templates/login.html`: forgot link and reset-success flash.
- `static/auth.js`: recovery form submission using current notice/loading helpers.
- `locales/tr.json` / `locales/en.json`: all recovery copy.
- `tests/test_password_recovery.py`: service/route/session recovery behavior.
- `tests/test_auth_phase6_ui.py`: recovery UI contract.
- `tests/test_session_store.py` / `tests/test_require_auth.py`: expiry and identity binding.
- `tests/test_auth_audit.py`: route allowlist and static legacy-auth audit.
- `docs/cognito.md` / `docs/handoff.md`: final architecture and handoff.

---

### Task 1: Cognito Password-Recovery Service Methods

**Files:**
- Modify: `app/services/cognito_service.py`
- Modify: `tests/test_cognito_service_tokens.py`

**Interfaces:**
- Consumes: `_get_client()`, `_maybe_secret(dict, username)`, `_wrap(Exception)`.
- Produces: `forgot_password(username: str) -> None` and `confirm_forgot_password(username: str, code: str, new_password: str) -> None`.

- [ ] **Step 1: Add failing service tests**

Add fake-client methods and tests that assert exact boto3 request shapes:

```python
def forgot_password(self, **kw):
    self.calls.append(("forgot_password", kw))
    if self.raises:
        raise self.raises
    return {}

def confirm_forgot_password(self, **kw):
    self.calls.append(("confirm_forgot_password", kw))
    if self.raises:
        raise self.raises
    return {}


def test_forgot_password_calls_cognito(monkeypatch):
    fake = _FakeIdp()
    _use_fake(monkeypatch, fake)
    cognito_service.forgot_password("alice")
    assert fake.calls[-1] == ("forgot_password", {
        "ClientId": "client-123", "Username": "alice"
    })


def test_confirm_forgot_password_calls_cognito(monkeypatch):
    fake = _FakeIdp()
    _use_fake(monkeypatch, fake)
    cognito_service.confirm_forgot_password("alice", "123456", "Newpass123")
    assert fake.calls[-1] == ("confirm_forgot_password", {
        "ClientId": "client-123", "Username": "alice",
        "ConfirmationCode": "123456", "Password": "Newpass123",
    })


@pytest.mark.parametrize("code", [
    "ExpiredCodeException", "CodeMismatchException", "LimitExceededException",
    "NotAuthorizedException", "TooManyRequestsException", "UserNotFoundException",
    "InvalidPasswordException",
])
def test_recovery_errors_are_wrapped(monkeypatch, code):
    fake = _FakeIdp(raises=_FakeClientError(code))
    _use_fake(monkeypatch, fake)
    with pytest.raises(CognitoServiceError) as exc:
        cognito_service.forgot_password("alice")
    assert exc.value.code == code
    assert code not in exc.value.message
```

- [ ] **Step 2: Verify the focused tests fail**

Run: `python -m pytest tests/test_cognito_service_tokens.py -k "forgot or confirm_forgot or recovery_errors" -v`

Expected: FAIL because the two service methods do not exist.

- [ ] **Step 3: Implement both service methods**

Add below `resend_code`:

```python
def forgot_password(username):
    kwargs = _maybe_secret({
        "ClientId": COGNITO_APP_CLIENT_ID,
        "Username": username,
    }, username)
    try:
        _get_client().forgot_password(**kwargs)
    except Exception as exc:
        raise _wrap(exc)


def confirm_forgot_password(username, code, new_password):
    kwargs = _maybe_secret({
        "ClientId": COGNITO_APP_CLIENT_ID,
        "Username": username,
        "ConfirmationCode": code,
        "Password": new_password,
    }, username)
    try:
        _get_client().confirm_forgot_password(**kwargs)
    except Exception as exc:
        raise _wrap(exc)
```

Keep `_ERROR_MESSAGES` fixed and user-safe; add no raw exception text to logs.

- [ ] **Step 4: Run service regression tests**

Run: `python -m pytest tests/test_cognito_service_tokens.py tests/test_cognito_idp.py -v`

Expected: PASS.

- [ ] **Step 5: Commit stage 1**

```bash
git add app/services/cognito_service.py tests/test_cognito_service_tokens.py
git commit -m "Add Cognito password recovery operations"
```

---

### Task 2: Recovery Routes and Single-Use Reset Lifecycle

**Files:**
- Modify: `app/blueprints/auth.py`
- Modify: `app/services/session_store.py`
- Create: `tests/test_password_recovery.py`
- Modify: `docs/cognito.md`

**Interfaces:**
- Consumes: Task 1 service methods, `validate_password`, Flask session/flash, `CognitoSession`.
- Produces: `GET|POST /forgot-password`, `GET|POST /reset-password`, `session_store.delete_for_user(user_id: int) -> int`.

- [ ] **Step 1: Write failing route and invalidation tests**

Create `tests/test_password_recovery.py` with tests equivalent to:

```python
from datetime import datetime, timedelta

from app.extensions import db
from app.models import CognitoSession, User
from app.services import cognito_service, session_store
from app.services.cognito_service import CognitoServiceError


def test_forgot_known_and_unknown_are_indistinguishable(client, monkeypatch):
    known = User(username="alice", email="alice@example.com", cognito_sub="sub-alice")
    db.session.add(known)
    db.session.commit()
    calls = []
    monkeypatch.setattr(cognito_service, "forgot_password", calls.append)
    known_response = client.post("/forgot-password", json={"identifier": " Alice@Example.com "})
    with client.session_transaction() as state:
        state.clear()
    unknown_response = client.post("/forgot-password", json={"identifier": "missing@example.com"})
    assert known_response.status_code == unknown_response.status_code == 200
    assert known_response.get_json()["message"] == unknown_response.get_json()["message"]
    assert calls == ["alice", "missing@example.com"]


def test_forgot_provider_error_still_returns_generic_success(client, monkeypatch):
    monkeypatch.setattr(cognito_service, "forgot_password", lambda value: (_ for _ in ()).throw(
        CognitoServiceError("Kullanıcı bulunamadı", "UserNotFoundException")))
    response = client.post("/forgot-password", json={"identifier": "nobody"})
    assert response.status_code == 200
    assert response.get_json()["next"] == "/reset-password"


def test_reset_success_is_single_use_and_invalidates_all_sessions(client, monkeypatch):
    user = User(username="alice", email="alice@example.com", cognito_sub="sub-alice")
    db.session.add(user)
    db.session.commit()
    for suffix in ("one", "two"):
        db.session.add(CognitoSession(
            session_id=suffix, user_id=user.id, cognito_username="alice",
            access_token="encrypted", refresh_token="encrypted",
            access_token_exp=datetime.utcnow() + timedelta(hours=1),
        ))
    db.session.commit()
    with client.session_transaction() as state:
        state["password_reset_username"] = "alice"
        state["password_reset_started_at"] = datetime.utcnow().timestamp()
        state["_user_id"] = str(user.id)
        state["cognito_sid"] = "one"
    monkeypatch.setattr(cognito_service, "confirm_forgot_password", lambda *args: None)
    response = client.post("/reset-password", json={
        "code": "123456", "password": "Newpass123", "confirm_password": "Newpass123",
    })
    assert response.status_code == 200
    assert CognitoSession.query.filter_by(user_id=user.id).count() == 0
    with client.session_transaction() as state:
        assert "password_reset_username" not in state
        assert "password_reset_started_at" not in state
        assert "_user_id" not in state
        assert "cognito_sid" not in state
    assert client.post("/reset-password", json={
        "code": "123456", "password": "Againpass123", "confirm_password": "Againpass123",
    }).status_code == 409


def test_reset_rejects_expired_local_context(client):
    with client.session_transaction() as state:
        state["password_reset_username"] = "alice"
        state["password_reset_started_at"] = (
            datetime.utcnow() - timedelta(minutes=16)
        ).timestamp()
    response = client.post("/reset-password", json={
        "code": "123456", "password": "Newpass123", "confirm_password": "Newpass123",
    })
    assert response.status_code == 409
```

Also cover missing fields, password mismatch, weak password, code mismatch,
expired code, and throttling with fixed response text.

- [ ] **Step 2: Run failing recovery tests**

Run: `python -m pytest tests/test_password_recovery.py -v`

Expected: FAIL because routes and `delete_for_user` are absent.

- [ ] **Step 3: Add bulk session invalidation**

Add to `app/services/session_store.py`:

```python
def delete_for_user(user_id):
    removed = (CognitoSession.query
               .filter_by(user_id=user_id)
               .delete(synchronize_session=False))
    db.session.commit()
    return removed
```

- [ ] **Step 4: Add reset-state helpers and routes**

In `app/blueprints/auth.py`, import `flash`, `datetime`, and SQLAlchemy `func`.
Add constants:

```python
_RESET_USERNAME_KEY = "password_reset_username"
_RESET_STARTED_KEY = "password_reset_started_at"
_RESET_CONTEXT_MINUTES = 15
_FORGOT_GENERIC_KEY = "auth.forgot_generic"
```

Add helpers that normalize email to a local username, store/check reset state,
and clear state. Implement routes with these response contracts:

```python
@bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per 15 minutes", methods=["POST"])
def forgot_password():
    if request.method == "GET":
        return render_template("forgot_password.html")
    data = request.get_json(silent=True) or {}
    identifier = (data.get("identifier") or "").strip()
    if not identifier:
        return jsonify({"error": t("auth.identifier_required")}), 400
    canonical = identifier.lower() if "@" in identifier else identifier
    if "@" in canonical:
        user = User.query.filter(func.lower(User.email) == canonical).first()
        if user:
            canonical = user.username
    try:
        cognito_service.forgot_password(canonical)
    except CognitoServiceError:
        pass
    session[_RESET_USERNAME_KEY] = canonical
    session[_RESET_STARTED_KEY] = datetime.utcnow().timestamp()
    return jsonify({"message": t(_FORGOT_GENERIC_KEY), "next": url_for("auth.reset_password")})


@bp.route("/reset-password", methods=["GET", "POST"])
@limiter.limit("10 per 15 minutes", methods=["POST"])
def reset_password():
    username = _valid_reset_username()
    if not username:
        if request.method == "GET":
            flash(t("auth.reset_context_expired"), "error")
            return redirect(url_for("auth.forgot_password"))
        return jsonify({"error": t("auth.reset_context_expired")}), 409
    if request.method == "GET":
        return render_template("reset_password.html")
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    password = data.get("password") or ""
    confirmation = data.get("confirm_password") or ""
    if not code or not password or not confirmation:
        return jsonify({"error": t("auth.reset_fields_required")}), 400
    if password != confirmation:
        return jsonify({"error": t("auth.password_mismatch")}), 400
    password_error = validate_password(password)
    if password_error:
        return jsonify({"error": password_error}), 400
    try:
        cognito_service.confirm_forgot_password(username, code, password)
    except CognitoServiceError as exc:
        return _reset_error_response(exc)
    user = User.query.filter_by(username=username).first()
    if user:
        session_store.delete_for_user(user.id)
    logout_user()
    session.clear()
    flash(t("auth.reset_success"), "success")
    return jsonify({"message": t("auth.reset_success"), "next": url_for("auth.login")})
```

`_reset_error_response` maps `CodeMismatchException`/`ExpiredCodeException` to
400, `LimitExceededException`/`TooManyRequestsException` to 429, and
`UserNotFoundException`/`NotAuthorizedException` to the same invalid-or-expired
message.

- [ ] **Step 5: Run recovery and auth regression tests**

Run: `python -m pytest tests/test_password_recovery.py tests/test_auth.py tests/test_session_store.py -v`

Expected: PASS.

- [ ] **Step 6: Document the recovery backend**

Update `docs/cognito.md` with the session-bound handoff, generic forgot response,
15-minute local context, single-use confirmation, session deletion, and forced
reauthentication.

- [ ] **Step 7: Commit stage 2**

```bash
git add app/blueprints/auth.py app/services/session_store.py tests/test_password_recovery.py docs/cognito.md
git commit -m "Add single-use Cognito reset flow"
```

---

### Task 3: Recovery UI, Login Link, Flash, and Translations

**Files:**
- Create: `templates/forgot_password.html`
- Create: `templates/reset_password.html`
- Modify: `templates/login.html`
- Modify: `static/auth.js`
- Modify: `locales/tr.json`
- Modify: `locales/en.json`
- Modify: `tests/test_auth_phase6_ui.py`
- Modify: `tests/test_password_recovery.py`

**Interfaces:**
- Consumes: Task 2 JSON `{message,next}` and `{error}` responses.
- Produces: `window.forgotPassword`, `window.resetPassword`, Enter handlers, shared auth-card pages.

- [ ] **Step 1: Add failing UI contract tests**

```python
def test_password_recovery_pages_reuse_auth_design(client, monkeypatch):
    forgot = _html(client, "/forgot-password")
    assert 'class="auth-card"' in forgot
    assert "/static/auth.css" in forgot and "/static/auth.js" in forgot
    assert "<style" not in forgot and "<script nonce=" not in forgot
    with client.session_transaction() as state:
        state["password_reset_username"] = "alice"
        state["password_reset_started_at"] = __import__("time").time()
    reset = _html(client, "/reset-password")
    assert 'autocomplete="one-time-code"' in reset
    assert reset.count('autocomplete="new-password"') == 2
    assert 'data-action="resetPassword"' in reset


def test_login_links_to_forgot_password(client):
    assert 'href="/forgot-password"' in _html(client, "/login")
```

- [ ] **Step 2: Verify UI tests fail**

Run: `python -m pytest tests/test_auth_phase6_ui.py tests/test_password_recovery.py -v`

Expected: FAIL because templates and handlers do not exist.

- [ ] **Step 3: Create both templates from existing auth components**

Each template includes `_head.html`, `auth.css`, `auth.js`, and `actions.js`, uses
`auth-shell`/`auth-topbar`/`auth-main`/`auth-card`/`auth-form`/`auth-field`, and
contains no inline style or script. Forgot uses `identifier`; Reset uses `code`,
`password`, and `confirm-password` with existing password toggles and strength UI.

- [ ] **Step 4: Add login link and flash rendering**

Place the link below the password field without changing card geometry classes:

```html
<div class="auth-row">
    <span></span>
    <a class="auth-link" href="/forgot-password">{{ t('login.forgot_password') }}</a>
</div>
```

Render categorized Flask messages through `auth-alert-error` and
`auth-alert-success` before the form.

- [ ] **Step 5: Add JavaScript handlers**

Reuse `showNotice`, `setButtonLoading`, `normalizedValue`, `passwordScore`, and
`submitOnEnter`. POST JSON through existing CSRF-wrapped `fetch`; redirect only
from `data.next` after success:

```javascript
window.forgotPassword = async function (el) {
  var identifier = normalizedValue("identifier");
  if (!identifier) { showNotice("error", tr("auth.identifier_required")); return; }
  setButtonLoading(el, true, tr("forgot.submit"));
  try {
    var response = await fetch("/forgot-password", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ identifier: identifier })
    });
    var data = await response.json();
    if (!response.ok) { showNotice("error", data.error); return; }
    showNotice("success", data.message);
    window.setTimeout(function () { window.location.href = data.next; }, 700);
  } catch (err) { showNotice("error", tr("common.conn_error_retry")); }
  finally { setButtonLoading(el, false); }
};
```

`resetPassword` sends code/password/confirmation, validates match and strength
client-side, renders server errors, and redirects to `data.next` on success.

- [ ] **Step 6: Add matching Turkish and English keys**

Add keys under consistent auth/login/forgot/reset namespaces for titles,
subtitles, identifier, code, new password, confirmation, submit buttons, generic
success, context expiry, mismatch, invalid/expired code, reset success, and link.

- [ ] **Step 7: Run focused UI and recovery tests**

Run: `python -m pytest tests/test_auth_phase6_ui.py tests/test_password_recovery.py tests/test_i18n.py -v`

Expected: PASS.

- [ ] **Step 8: Commit stage 3**

```bash
git add templates/forgot_password.html templates/reset_password.html templates/login.html static/auth.js locales/tr.json locales/en.json tests/test_auth_phase6_ui.py tests/test_password_recovery.py
git commit -m "Add matching password recovery UI"
```

---

### Task 4: Remove Local-Password Authentication and Obsolete Helpers

**Files:**
- Modify: `app/blueprints/auth.py`
- Modify: `app/models.py`
- Modify: `app/services/validators.py`
- Delete: `app/services/cognito.py`
- Delete: `app/services/cognito_idp.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_auth.py`
- Modify/Delete compatibility assertions: `tests/test_cognito.py`, `tests/test_cognito_idp.py`
- Modify: `docs/cognito.md`

**Interfaces:**
- Consumes: canonical `cognito_service.authenticate` and `cognito_jwt.validate_token`.
- Produces: login that authenticates Cognito first and resolves `User` only by verified `sub`; no local fallback.

- [ ] **Step 1: Change tests to require Cognito-only authentication**

Update auth tests so registration/login explicitly stub Cognito. Add assertions:

```python
def test_login_never_calls_local_password_helper(client, monkeypatch):
    assert not hasattr(User, "check_password")


def test_auth_posts_fail_controlled_when_cognito_disabled(client):
    assert client.post("/login", json={"username": "x", "password": "Password1"}).status_code == 503
    assert client.post("/register", json={
        "username": "userx", "email": "x@example.com", "password": "Password1"
    }).status_code == 503
```

Change shared `make_user` to assign `cognito_sub=f"sub-{username}"` by default.
Change `login` fixture to stub `authenticate` and `validate_token`, then POST the
existing login form. `make_users_bulk` also assigns unique `cognito_sub` values.

- [ ] **Step 2: Run auth tests and confirm the new expectations fail**

Run: `python -m pytest tests/test_auth.py tests/test_cognito_auth.py -v`

Expected: FAIL while local branches/helpers still exist.

- [ ] **Step 3: Rewrite registration/login gates**

For registration POST, return controlled 503 when Cognito is disabled; remove the
local user/password branch. For login POST, return 503 when disabled, call Cognito
for the submitted username, validate the ID token, resolve `User` with:

```python
verified_claims = cognito_jwt.validate_token(tokens["id_token"], "id")
sub = (verified_claims.get("sub") or "").strip()
user = User.query.filter_by(cognito_sub=sub).first() if sub else None
if user is None:
    return jsonify({"error": t("auth.bad_credentials")}), 401
```

Then call `_login_fresh` and create a new encrypted Cognito session. Do not query
by username before Cognito authenticates.

- [ ] **Step 4: Remove password helpers and duplicate modules**

Remove `set_password`/`check_password` and Werkzeug password imports from
`app/models.py`; remove `_DUMMY_PW_HASH` from validators; remove duplicate unused
`cognito.py` and `cognito_idp.py` after `rg` confirms no runtime imports. Retain
`password_hash = db.Column(..., nullable=True)` unchanged.

- [ ] **Step 5: Run auth and broad fixture-dependent tests**

Run: `python -m pytest tests/test_auth.py tests/test_cognito_auth.py tests/test_require_auth.py tests/test_profile_routes.py tests/test_tracking_routes.py tests/test_social_routes.py -v`

Expected: PASS.

- [ ] **Step 6: Update migration summary in `docs/cognito.md`**

Document that the column remains but runtime local authentication and duplicate
services are gone.

- [ ] **Step 7: Commit stage 4**

```bash
git add -A app/blueprints/auth.py app/models.py app/services tests/conftest.py tests/test_auth.py tests/test_cognito.py tests/test_cognito_idp.py docs/cognito.md
git commit -m "Remove legacy local authentication"
```

---

### Task 5: Session Expiry and Verified-Identity Binding

**Files:**
- Modify: `app/config.py`
- Modify: `.env.example`
- Modify: `app/services/session_store.py`
- Modify: `app/auth_middleware.py`
- Modify: `tests/test_session_store.py`
- Modify: `tests/test_require_auth.py`
- Modify: `docs/cognito.md`

**Interfaces:**
- Produces config `COGNITO_SESSION_IDLE_HOURS: int = 24`, `COGNITO_SESSION_ABSOLUTE_DAYS: int = 7`.
- Changes `get_valid_access_token(session_id: str, expected_user_id: int | None = None) -> str`.
- Marks protected views with `_require_auth = True` for audit introspection.

- [ ] **Step 1: Add failing timeout/binding tests**

```python
def test_idle_timeout_deletes_session(app, cognito_user):
    sid = _create_session(cognito_user)
    row = session_store.get(sid)
    row.last_used_at = datetime.utcnow() - timedelta(hours=25)
    db.session.commit()
    with pytest.raises(session_store.SessionInvalid) as exc:
        session_store.get_valid_access_token(sid, cognito_user.id)
    assert exc.value.args[0] == "idle_timeout"
    assert session_store.get(sid) is None


def test_absolute_timeout_deletes_session(app, cognito_user):
    sid = _create_session(cognito_user)
    row = session_store.get(sid)
    row.created_at = datetime.utcnow() - timedelta(days=8)
    db.session.commit()
    with pytest.raises(session_store.SessionInvalid) as exc:
        session_store.get_valid_access_token(sid, cognito_user.id)
    assert exc.value.args[0] == "absolute_timeout"


def test_session_user_mismatch_invalidated(client, probe_route, cognito_user, make_user):
    other = make_user("other")
    sid = _create_session(other)
    _login_session(client, cognito_user, sid)
    assert client.get(probe_route).status_code == 302


def test_verified_sub_must_resolve_same_user(client, probe_route, cognito_user, monkeypatch):
    monkeypatch.setattr(cognito_jwt, "validate_token", lambda token, use: {"sub": "sub-other"})
    sid = _create_session(cognito_user)
    _login_session(client, cognito_user, sid)
    assert client.get(probe_route).status_code == 302
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `python -m pytest tests/test_session_store.py tests/test_require_auth.py -v`

Expected: FAIL on new timeout and binding cases.

- [ ] **Step 3: Add config and enforce expiry**

```python
COGNITO_SESSION_IDLE_HOURS = int(os.getenv("COGNITO_SESSION_IDLE_HOURS", "24"))
COGNITO_SESSION_ABSOLUTE_DAYS = int(os.getenv("COGNITO_SESSION_ABSOLUTE_DAYS", "7"))
```

In `get_valid_access_token`, reject user mismatch first, then absolute and idle
deadlines before any refresh. Delete the row and raise `SessionInvalid` with the
exact reasons in the tests.

- [ ] **Step 4: Bind middleware identity by verified sub**

Call `get_valid_access_token(sid, current_user.id)`, validate JWT, resolve:

```python
resolved = User.query.filter_by(cognito_sub=claims.get("sub")).first()
if resolved is None or resolved.id != current_user.id:
    return _invalidate()
```

Remove legacy passthrough. After defining `wrapped`, set:

```python
wrapped._require_auth = True
```

- [ ] **Step 5: Document env variables and architecture**

Add both defaults to `.env.example` and explain idle/absolute invalidation and
three-way identity binding in `docs/cognito.md`.

- [ ] **Step 6: Run focused and JWT regression tests**

Run: `python -m pytest tests/test_session_store.py tests/test_require_auth.py tests/test_cognito_jwt.py tests/test_cognito_auth.py -v`

Expected: PASS.

- [ ] **Step 7: Commit stage 5**

```bash
git add app/config.py .env.example app/services/session_store.py app/auth_middleware.py tests/test_session_store.py tests/test_require_auth.py docs/cognito.md
git commit -m "Bind Cognito identity and expire sessions"
```

---

### Task 6: Authorization and Static Authentication Audits

**Files:**
- Create: `tests/test_auth_audit.py`
- Modify: `app/auth_middleware.py`
- Modify: `docs/cognito.md`

**Interfaces:**
- Consumes: `_require_auth` marker from Task 5.
- Produces: executable public-route allowlist and static legacy-auth regression guard.

- [ ] **Step 1: Add route-map audit**

Create an explicit public endpoint set:

```python
PUBLIC_ENDPOINTS = {
    "static", "health", "pages.welcome", "pages.referral_redirect",
    "auth.set_language", "auth.register", "auth.login", "auth.verify_page",
    "auth.verify_confirm", "auth.verify_resend", "auth.forgot_password",
    "auth.reset_password", "auth.cognito_login", "auth.cognito_callback",
}
TEARDOWN_ENDPOINTS = {"auth.logout"}


def test_every_non_public_route_uses_require_auth(app):
    missing = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint in PUBLIC_ENDPOINTS | TEARDOWN_ENDPOINTS:
            continue
        view = app.view_functions[rule.endpoint]
        if not getattr(view, "_require_auth", False):
            missing.append((rule.endpoint, rule.rule))
    assert missing == []
```

If the inventory reveals a differently named welcome/referral endpoint, update
the set to the exact registered endpoint and document why it is public; do not
add a protected business endpoint merely to make the test green.

- [ ] **Step 2: Add static legacy-auth guard**

```python
from pathlib import Path


def test_runtime_auth_does_not_use_password_hash():
    auth_source = Path("app/blueprints/auth.py").read_text(encoding="utf-8")
    middleware = Path("app/auth_middleware.py").read_text(encoding="utf-8")
    assert "password_hash" not in auth_source + middleware
    assert "check_password" not in auth_source + middleware
    assert "generate_password_hash" not in auth_source + middleware


def test_no_duplicate_cognito_implementations():
    assert not Path("app/services/cognito.py").exists()
    assert not Path("app/services/cognito_idp.py").exists()
```

- [ ] **Step 3: Run audit tests and inspect every failure**

Run: `python -m pytest tests/test_auth_audit.py -v`

Expected: PASS after exact endpoint names are confirmed. Any missing protected
route must receive `@require_auth`; only genuinely public routes enter allowlist.

- [ ] **Step 4: Document intentionally public endpoints**

Add a table in `docs/cognito.md` with endpoint, methods, reason public, rate limit,
and CSRF applicability. Document `/logout` separately as authenticated teardown.

- [ ] **Step 5: Commit stage 6**

```bash
git add tests/test_auth_audit.py app/auth_middleware.py docs/cognito.md
git commit -m "Add authentication authorization audits"
```

---

### Task 7: Final Regression, Security Audit, Cleanup, and Handoff

**Files:**
- Modify: `docs/cognito.md`
- Modify: `docs/handoff.md`
- Modify: `docs/superpowers/specs/2026-07-11-sprint3-auth-finalization-design.md` only if implementation evidence requires clarification
- Modify: auth files/tests only for concrete audit findings

**Interfaces:**
- Produces: completed Sprint 3 documentation and evidence-backed final audit.

- [ ] **Step 1: Run focused authentication suite**

Run:

```bash
python -m pytest tests/test_auth.py tests/test_auth_phase6_ui.py tests/test_password_recovery.py tests/test_cognito_service_tokens.py tests/test_cognito_jwt.py tests/test_cognito_auth.py tests/test_session_store.py tests/test_require_auth.py tests/test_auth_audit.py tests/test_hooks.py -v
```

Expected: PASS.

- [ ] **Step 2: Run static security searches**

Run:

```bash
rg -n "password_hash|check_password|set_password|generate_password_hash|_DUMMY_PW_HASH" app
rg -n "AccessToken|RefreshToken|IdToken|password|code" app/blueprints/auth.py app/services/cognito_service.py app/services/session_store.py
rg -n "AKIA[0-9A-Z]{16}|aws_access_key_id|aws_secret_access_key" --glob "!.git/**"
rg -n "login_required" app/blueprints
```

Expected:

- `password_hash` appears only in the model compatibility column/comment, never auth runtime.
- Password/code/token matches are data handling, not logging.
- No committed AWS credentials.
- `login_required` remains only on `/logout` plus comments/imports required there.

- [ ] **Step 3: Run complete regression suite**

Run: `python -m pytest -v`

Expected: PASS entire suite.

- [ ] **Step 4: Review repository diff and commit history**

Run:

```bash
git diff main...HEAD --check
git status --short
git log --oneline --decorate main..HEAD
```

Expected: no whitespace errors, no unrelated files, clean working tree after the
final commit, and one logical commit per stage.

- [ ] **Step 5: Finalize documentation**

`docs/cognito.md` must contain final architecture, lifecycle, password recovery,
security decisions, JWT validation, protected route strategy, migration summary,
known limitations, and future enhancements.

`docs/handoff.md` must contain completed work, changed areas, architecture and
migration summaries, focused/full test commands and results, remaining non-auth
technical debt, and Production Readiness recommendations.

Update the design document only to record an unavoidable deviation; include the
reason and security impact. If there is no deviation, leave the approved design
unchanged.

- [ ] **Step 6: Commit stage 7**

```bash
git add docs/cognito.md docs/handoff.md docs/superpowers/specs/2026-07-11-sprint3-auth-finalization-design.md
git commit -m "Complete Sprint 3 auth audit and handoff"
```

- [ ] **Step 7: Re-run final verification after the documentation commit**

Run: `python -m pytest -v`

Expected: PASS entire suite with a clean `git status --short`.

---

## Plan Self-Review

- Spec coverage: Tasks 1-3 implement recovery and matching UI; Task 4 removes
  legacy auth; Task 5 hardens JWT/session identity and timeouts; Task 6 audits
  authorization/static cleanup; Task 7 completes regression and documentation.
- Data and interface consistency: recovery methods, reset-session keys,
  `delete_for_user`, timeout config names, `get_valid_access_token` signature,
  and `_require_auth` marker are identical across dependent tasks.
- Migration safety: no schema change; `password_hash` column remains.
- Scope: profile, referral, onboarding, nutrition, training, and other business
  behavior are changed only where shared tests require Cognito-auth fixture setup.
- Placeholder scan: every code-changing step contains exact behavior, commands,
  expected results, and commit scope.
