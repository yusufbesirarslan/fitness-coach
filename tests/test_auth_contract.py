"""Cross-client authentication contract: parity assertions and drift gates.

The suite already covers each client on its own — tests/test_require_auth.py
for the browser boundary, tests/test_mobile_auth_*.py for the API boundary,
tests/test_cognito_jwt.py for the validator. What it never asserted is that the
two boundaries AGREE. Both routing through app/services/cognito_jwt.py today is
a fact about the current code, not a property anything enforces; one path could
start accepting an ID token, widen its expiry window, or turn a temporary JWKS
outage into a logout, and every existing test would still pass.

These tests assert the agreement itself, and the static gates at the bottom
assert that a future change cannot re-introduce a second validation path
without failing CI.

    python -m pytest tests/test_auth_contract.py -v
"""

import ast
import calendar
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.auth_middleware import require_auth
from app.extensions import db
from app.models import CognitoSession, MobileAuthSession, User
from app.services import (
    auth_contract, cognito_jwt, cognito_service, mobile_auth, runtime_metrics,
    session_store,
)


REPO_ROOT = Path(__file__).resolve().parent.parent


def _future_exp(hours=1):
    """A provider expiry far enough ahead that login does not force a refresh.

    Wall-clock relative on purpose: a fixed date would silently start failing
    once it passed, and a session-expiry test that stops testing expiry is
    worse than no test.
    """
    return calendar.timegm((datetime.utcnow() + timedelta(hours=hours)).timetuple())


def _valid_claims():
    return {"sub": "sub-shared", "exp": _future_exp()}


# ---------------------------------------------------------------------------
# Fixtures — one live session per client, built the way production builds it.
# ---------------------------------------------------------------------------

@pytest.fixture
def probe_route(app):
    """A @require_auth route, so the web boundary is exercised in isolation."""
    app.config["SESSION_PROTECTION"] = None

    @app.route("/__contract_probe")
    @require_auth
    def _contract_probe():
        return "ok", 200

    app.url_map.update()
    return "/__contract_probe"


@pytest.fixture
def provider(monkeypatch):
    """Stand in for Cognito: a signed access token and a signed ID token."""
    monkeypatch.setattr(
        cognito_service, "authenticate", lambda username, password: {
            "tokens": {
                "access_token": "provider-access", "id_token": "provider-id",
                "refresh_token": "provider-refresh", "expires_in": 3600,
            }, "claims": {"sub": "sub-shared"},
        })

    def validate(token, expected_use, leeway_seconds=0):
        if expected_use == "id":
            return {"sub": "sub-shared", "email": "shared@example.com",
                    "email_verified": True}
        return _valid_claims()

    monkeypatch.setattr(cognito_jwt, "validate_token", validate)
    return validate


@pytest.fixture
def shared_user(app, make_user):
    """One local account both clients authenticate into."""
    return make_user("shared", cognito_sub="sub-shared")


@pytest.fixture
def web_session(client, shared_user):
    sid = session_store.create(shared_user, {
        "access_token": "provider-access", "refresh_token": "provider-refresh",
        "id_token": "provider-id", "expires_in": 3600,
    }, "shared")
    with client.session_transaction() as browser:
        browser["_user_id"] = str(shared_user.id)
        browser["cognito_sid"] = sid
    return sid


@pytest.fixture
def mobile_session(shared_user, provider):
    return mobile_auth.login("shared", "correct")


def _mobile_headers(issued):
    return {"Authorization": f"Bearer {issued.access_credential}"}


def _rejecting_validator(reason):
    def validate(token, expected_use, leeway_seconds=0):
        raise cognito_jwt.TokenValidationError(reason)
    return validate


# ---------------------------------------------------------------------------
# Parity — the two boundaries must agree on WHAT is accepted.
# ---------------------------------------------------------------------------

def test_both_paths_require_an_access_token_not_an_id_token(
        client, raw_client, probe_route, web_session, mobile_session,
        monkeypatch):
    """An ID token is an identity assertion, not an API credential.

    If either boundary accepted `token_use=id`, a token minted for a different
    purpose would authorize API calls. The stub below rejects anything that is
    not asked for as an access token, so a path that asks for the wrong use
    fails here rather than in production.
    """
    def only_access(token, expected_use, leeway_seconds=0):
        if expected_use != auth_contract.REQUEST_TOKEN_USE:
            raise AssertionError(f"asked for token_use={expected_use!r}")
        raise cognito_jwt.TokenValidationError("wrong_use")

    monkeypatch.setattr(cognito_jwt, "validate_token", only_access)
    assert client.get(probe_route).status_code == 302
    assert raw_client.get(
        "/api/v1/account/me",
        headers=_mobile_headers(mobile_session)).status_code == 401


def test_both_paths_record_the_same_token_use(
        client, raw_client, probe_route, web_session, mobile_session,
        monkeypatch):
    """Capture the real arguments — the assertion above proves rejection, this
    proves the two calls are actually identical in use and leeway."""
    seen = []

    def capture(token, expected_use, leeway_seconds=0):
        seen.append((expected_use, leeway_seconds))
        return _valid_claims()

    monkeypatch.setattr(cognito_jwt, "validate_token", capture)
    assert client.get(probe_route).status_code == 200
    assert raw_client.get(
        "/api/v1/account/me",
        headers=_mobile_headers(mobile_session)).status_code == 200
    assert len(seen) == 2
    assert {use for use, _ in seen} == {auth_contract.REQUEST_TOKEN_USE}
    assert {leeway for _, leeway in seen} == {0}, (
        "web and mobile disagreed on expiry leeway with default configuration")


def test_both_paths_survive_a_temporary_jwks_outage(
        client, raw_client, probe_route, web_session, mobile_session,
        monkeypatch):
    """A cold JWKS cache is real on every fresh container, i.e. after every
    deploy. Treating it as a rejection logs the whole population out at once,
    so BOTH boundaries must answer 503 and leave the session intact."""
    monkeypatch.setattr(
        cognito_jwt, "validate_token", _rejecting_validator("jwks_unavailable"))

    web = client.get(probe_route)
    assert web.status_code == 503
    assert web.headers.get("Retry-After")
    assert session_store.get(web_session) is not None

    mobile = raw_client.get(
        "/api/v1/account/me", headers=_mobile_headers(mobile_session))
    assert mobile.status_code == 503
    assert mobile.json["error"]["retryable"] is True
    assert MobileAuthSession.query.one().revoked_at is None


def test_both_paths_destroy_the_session_on_a_definitive_rejection(
        client, raw_client, probe_route, web_session, mobile_session,
        monkeypatch):
    """Counter-test to the one above: the transient/definitive split was not
    made by softening definitive rejections."""
    monkeypatch.setattr(
        cognito_jwt, "validate_token",
        _rejecting_validator("invalid_signature"))

    assert client.get(probe_route).status_code == 302
    assert session_store.get(web_session) is None

    assert raw_client.get(
        "/api/v1/account/me",
        headers=_mobile_headers(mobile_session)).status_code == 401
    assert MobileAuthSession.query.one().revoked_at is not None


def test_both_paths_bind_the_verified_subject_to_the_local_user(
        client, raw_client, probe_route, web_session, mobile_session,
        shared_user, monkeypatch):
    """A valid signature is not authorization. Both boundaries must refuse a
    token whose verified `sub` belongs to somebody else, otherwise a token
    issued for user A would act as user B."""
    intruder = User(username="intruder", email="intruder@example.com",
                    cognito_sub="sub-intruder")
    db.session.add(intruder)
    db.session.commit()

    def other_subject(token, expected_use, leeway_seconds=0):
        return {"sub": "sub-intruder", "exp": _future_exp()}

    monkeypatch.setattr(cognito_jwt, "validate_token", other_subject)
    assert client.get(probe_route).status_code == 302
    assert raw_client.get(
        "/api/v1/account/me",
        headers=_mobile_headers(mobile_session)).status_code == 401


def test_the_validator_is_actually_on_both_request_paths(
        client, raw_client, probe_route, web_session, mobile_session,
        monkeypatch):
    """Importing the validator is not the same as calling it. If a refactor
    left a cached-claims shortcut in place, this is what would catch it."""
    called = []

    def counting(token, expected_use, leeway_seconds=0):
        called.append(expected_use)
        return _valid_claims()

    monkeypatch.setattr(cognito_jwt, "validate_token", counting)
    client.get(probe_route)
    assert len(called) == 1
    raw_client.get("/api/v1/account/me", headers=_mobile_headers(mobile_session))
    assert len(called) == 2


# ---------------------------------------------------------------------------
# Intentional differences — pinned so they stay deliberate.
# ---------------------------------------------------------------------------

def test_error_shapes_stay_different_on_purpose(client, raw_client, probe_route):
    """ADR 0001 gives mobile a machine-readable envelope; the browser gets a
    redirect. Converging them would break one client or the other, so the
    difference is asserted rather than removed."""
    web = client.get(probe_route)
    assert web.status_code == 302
    assert "/login" in web.headers["Location"]

    mobile = raw_client.get("/api/v1/account/me")
    assert mobile.status_code == 401
    assert set(mobile.json["error"]) == {
        "code", "message", "retryable", "request_id"}
    assert mobile.json["error"]["code"] == "AUTH_SESSION_EXPIRED"


def test_mobile_boundary_never_accepts_a_browser_session(client, shared_user):
    """The two credential mechanisms are intentionally separate: a cookie must
    not authenticate the API, and the API credential is not a cookie."""
    with client.session_transaction() as browser:
        browser["_user_id"] = str(shared_user.id)
        browser["_fresh"] = True
    response = client.get("/api/v1/account/me")
    assert response.status_code == 401
    assert "Set-Cookie" not in response.headers


# ---------------------------------------------------------------------------
# Clock skew — one canonical value, pinned to zero, knob retired.
# ---------------------------------------------------------------------------

def test_both_paths_use_exactly_zero_leeway(app):
    """One canonical value for request authentication, and it is zero."""
    with app.test_request_context("/"):
        for path in auth_contract.PATHS:
            assert auth_contract.validation_clock_skew_seconds(path) == 0


def test_supplying_the_retired_setting_cannot_reintroduce_leeway(app):
    """Even if the retired key reappears in config — through a stale deploy
    artefact, a fixture, or a future refactor that resurrects it — neither path
    reads it. The pinning is structural, not a default that config can beat."""
    app.config[auth_contract.RETIRED_CLOCK_SKEW_KEY] = 120
    with app.test_request_context("/"):
        for path in auth_contract.PATHS:
            assert auth_contract.validation_clock_skew_seconds(path) == 0


def test_supplying_the_retired_setting_does_not_change_the_wire_call(
        client, raw_client, probe_route, web_session, mobile_session, app,
        monkeypatch):
    """The end-to-end version of the assertion above: with the retired key set,
    both middlewares still call the validator with leeway 0."""
    app.config[auth_contract.RETIRED_CLOCK_SKEW_KEY] = 300
    seen = []

    def capture(token, expected_use, leeway_seconds=0):
        seen.append(leeway_seconds)
        return _valid_claims()

    monkeypatch.setattr(cognito_jwt, "validate_token", capture)
    assert client.get(probe_route).status_code == 200
    assert raw_client.get(
        "/api/v1/account/me",
        headers=_mobile_headers(mobile_session)).status_code == 200
    assert seen == [0, 0]


def test_unknown_path_is_rejected_rather_than_defaulted(app):
    """A typo must not silently resolve to "some path's" leeway."""
    with app.test_request_context("/"):
        with pytest.raises(ValueError):
            auth_contract.validation_clock_skew_seconds("desktop")


@pytest.mark.parametrize("environ", [
    {},
    {auth_contract.RETIRED_CLOCK_SKEW_KEY: "0"},
    {auth_contract.RETIRED_CLOCK_SKEW_KEY: " 0 "},
])
def test_absent_or_zero_retired_setting_still_boots(environ):
    """Migration must not break hosts that never set it, or that set it to the
    value the contract now pins anyway."""
    auth_contract.enforce_retired_settings(environ)


@pytest.mark.parametrize("value", ["1", "120", "300", "-1", "301"])
def test_non_zero_retired_setting_refuses_to_boot(value):
    """A retired setting that is silently ignored is worse than one that is
    still honoured: the host believes mobile tolerates expired tokens and
    nothing contradicts it. Fail the boot instead — the deploy health gate then
    rolls back rather than shipping a system that disagrees with its own docs.
    """
    with pytest.raises(auth_contract.AuthContractConfigurationError) as exc:
        auth_contract.enforce_retired_settings(
            {auth_contract.RETIRED_CLOCK_SKEW_KEY: value})
    message = str(exc.value)
    assert auth_contract.RETIRED_CLOCK_SKEW_KEY in message
    assert "docs/AUTH_CONTRACT.md" in message


@pytest.mark.parametrize("value", ["", "   ", "true", "0.5", "60s"])
def test_malformed_retired_setting_refuses_to_boot(value):
    """Same strictness the setting had before retirement — a malformed value
    was never read as zero, and must not start being read as zero now."""
    with pytest.raises(auth_contract.AuthContractConfigurationError):
        auth_contract.enforce_retired_settings(
            {auth_contract.RETIRED_CLOCK_SKEW_KEY: value})


def test_retired_setting_is_checked_independently_of_the_mobile_flag():
    """Checked unconditionally, NOT behind MOBILE_AUTH_ENABLED.

    A host with mobile auth off can still carry a stale non-zero value, and
    that is exactly the value that would take effect on the day mobile auth is
    switched on — the worst possible moment to discover it.
    """
    with pytest.raises(auth_contract.AuthContractConfigurationError):
        auth_contract.enforce_retired_settings({
            "MOBILE_AUTH_ENABLED": "0",
            auth_contract.RETIRED_CLOCK_SKEW_KEY: "120",
        })


def test_nothing_writes_the_retired_key_into_config(app):
    """The key must not exist in a booted application's config at all: a dead
    setting that still appears operational is what lets docs and behaviour
    drift apart."""
    assert auth_contract.RETIRED_CLOCK_SKEW_KEY not in app.config


def test_boot_line_states_the_pinned_contract(app, caplog):
    with caplog.at_level("INFO"):
        auth_contract.log_contract_state(app)
    assert "[AUTH_CONTRACT]" in caplog.text
    assert "skew=0s" in caplog.text
    assert "configurable=no" in caplog.text


def test_deep_health_publishes_the_contract_without_secrets(client):
    body = client.get("/health?deep=1").json
    assert body["auth_contract"] == {
        "request_token_use": "access",
        "request_clock_skew_seconds": 0,
        "clock_skew_configurable": False,
        "paths": ["web", "mobile"],
    }


# ---------------------------------------------------------------------------
# Outcome metrics — bounded vocabulary, no identity.
# ---------------------------------------------------------------------------

def test_outcome_vocabulary_is_closed():
    assert set(auth_contract.MOBILE_CODE_OUTCOMES.values()) <= (
        auth_contract.OUTCOMES)
    assert auth_contract.mobile_outcome_for("SOMETHING_NEW") in (
        auth_contract.OUTCOMES)
    assert auth_contract.OUTCOME_OK in auth_contract.OUTCOMES


@pytest.fixture
def captured_metrics(monkeypatch):
    recorded = []
    monkeypatch.setattr(runtime_metrics, "is_enabled", lambda: True)
    monkeypatch.setattr(
        runtime_metrics, "increment",
        lambda name, dimensions=None, value=1: recorded.append(
            (name, dict(dimensions or {}))))
    # The real latency/gauge writers start the background flush thread; the
    # test only cares about counters, so stub them out and stay hermetic.
    monkeypatch.setattr(
        runtime_metrics, "record_latency",
        lambda name, millis, dimensions=None: None)
    monkeypatch.setattr(
        runtime_metrics, "set_gauge", lambda name, value, dimensions=None: None)
    return recorded


def test_auth_outcomes_carry_no_user_identity(
        client, raw_client, probe_route, web_session, mobile_session, provider,
        shared_user, captured_metrics):
    """Dimensions are a fixed 2x5 grid. A user id, a token or a raw path here
    would be both a cardinality explosion and a privacy leak."""
    assert client.get(probe_route).status_code == 200
    assert raw_client.get(
        "/api/v1/account/me",
        headers=_mobile_headers(mobile_session)).status_code == 200

    outcomes = [dims for name, dims in captured_metrics
                if name == "AuthOutcomes"]
    assert len(outcomes) == 2
    for dims in outcomes:
        assert set(dims) == {"Path", "Outcome"}
        assert dims["Path"] in auth_contract.PATHS
        assert dims["Outcome"] in auth_contract.OUTCOMES
    assert {dims["Path"] for dims in outcomes} == set(auth_contract.PATHS)
    assert {dims["Outcome"] for dims in outcomes} == {auth_contract.OUTCOME_OK}
    blob = str(captured_metrics)
    assert str(shared_user.id) not in blob
    assert mobile_session.access_credential not in blob


def test_provider_outage_is_counted_separately_from_a_rejection(
        client, probe_route, web_session, captured_metrics, monkeypatch):
    """"Could not verify" and "rejected" are different operational events; a
    single 401/503 count cannot tell an outage from an attack."""
    monkeypatch.setattr(
        cognito_jwt, "validate_token", _rejecting_validator("jwks_unavailable"))
    assert client.get(probe_route).status_code == 503

    monkeypatch.setattr(
        cognito_jwt, "validate_token",
        _rejecting_validator("invalid_signature"))
    client.get(probe_route)

    outcomes = [dims["Outcome"] for name, dims in captured_metrics
                if name == "AuthOutcomes"]
    assert auth_contract.OUTCOME_PROVIDER_UNAVAILABLE in outcomes
    assert auth_contract.OUTCOME_TOKEN_REJECTED in outcomes


def test_metric_failure_never_breaks_authentication(
        client, probe_route, web_session, provider, monkeypatch):
    """Instrumentation is never allowed to decide whether a request is
    authenticated."""
    monkeypatch.setattr(runtime_metrics, "is_enabled", lambda: True)

    def explode(*args, **kwargs):
        raise RuntimeError("metrics backend down")

    monkeypatch.setattr(runtime_metrics, "increment", explode)
    assert client.get(probe_route).status_code == 200


# ---------------------------------------------------------------------------
# Drift gates — static, so a future change cannot quietly split the paths.
# ---------------------------------------------------------------------------

def _app_sources():
    for path in sorted((REPO_ROOT / "app").rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _relative(path):
    return path.relative_to(REPO_ROOT).as_posix()


def _calls_validate_token(node):
    func = node.func
    return (isinstance(func, ast.Attribute) and func.attr == "validate_token"
            and isinstance(func.value, ast.Name)
            and func.value.id == "cognito_jwt")


def test_only_declared_modules_call_the_validator():
    """A second call site is how the two clients would drift apart again.

    The allow-list is DERIVED from auth_contract.VALIDATOR_CALL_SITES, not kept
    here, so the declaration and the code cannot disagree: adding a call site
    means declaring it and its reason.
    """
    declared = set(auth_contract.VALIDATOR_CALL_SITES)
    found = set()
    for path, tree in _app_sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _calls_validate_token(node):
                found.add(_relative(path))
    assert found <= declared, (
        f"undeclared cognito_jwt.validate_token call sites: {found - declared}")
    assert found == declared, (
        f"declared but absent call sites: {declared - found}")


def test_only_the_contract_module_varies_the_expiry_leeway():
    """Everything outside the contract gets cognito_jwt's pinned default of 0.

    This is what makes "one canonical skew source" a structural fact instead of
    a convention: no other module may pass leeway_seconds at all.
    """
    offenders = []
    for path, tree in _app_sources():
        relative = _relative(path)
        if relative in ("app/services/auth_contract.py",
                        "app/services/cognito_jwt.py"):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if any(kw.arg == "leeway_seconds" for kw in node.keywords):
                offenders.append(f"{relative}:{node.lineno}")
    assert offenders == [], (
        f"expiry leeway set outside app/services/auth_contract.py: {offenders}")


def test_no_second_jwt_validator_exists():
    """One validator means one place a security fix has to land."""
    offenders = []
    for path, tree in _app_sources():
        relative = _relative(path)
        if relative == "app/services/cognito_jwt.py":
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name.split(".")[0] == "joserfc" for name in names):
                offenders.append(f"{relative}:{node.lineno}")
    assert offenders == [], f"second JWT implementation: {offenders}"


def test_every_authenticated_route_uses_exactly_one_boundary(app):
    """No route may carry both decorators, and the mobile blueprint may not be
    protected by the cookie boundary (or the reverse)."""
    for rule in app.url_map.iter_rules():
        view = app.view_functions[rule.endpoint]
        web = getattr(view, "_require_auth", False)
        mobile = getattr(view, "_require_mobile_auth", False)
        assert not (web and mobile), f"{rule.endpoint} has both boundaries"
        if rule.endpoint.startswith("mobile_api."):
            assert not web, f"{rule.endpoint} uses the cookie boundary"
        else:
            assert not mobile, f"{rule.endpoint} uses the bearer boundary"


def test_contract_module_does_not_import_its_callers():
    """auth_contract is imported at boot by app/config.py; a back-import would
    turn a documentation module into a circular dependency."""
    tree = ast.parse(
        (REPO_ROOT / "app" / "services" / "auth_contract.py").read_text(
            encoding="utf-8"))
    forbidden = {"app.config", "app.auth_middleware",
                 "app.mobile_auth_middleware", "app.services.mobile_auth"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "") not in forbidden
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden


def test_documented_contract_lists_every_path_and_flag():
    """The doc is the operator-facing half of this contract; a path that exists
    in code but not in the table is a path nobody reviews."""
    doc = (REPO_ROOT / "docs" / "AUTH_CONTRACT.md").read_text(encoding="utf-8")
    for token in (auth_contract.RETIRED_CLOCK_SKEW_KEY,
                  "MOBILE_AUTH_ENABLED", "jwks_unavailable",
                  *auth_contract.VALIDATOR_CALL_SITES):
        assert token in doc, f"docs/AUTH_CONTRACT.md does not mention {token}"


def test_retirement_migration_is_documented_for_the_operator():
    """A boot-blocking setting is only safe to introduce if the operator can
    find out how to clear it before deploying."""
    contract = (REPO_ROOT / "docs" / "AUTH_CONTRACT.md").read_text(
        encoding="utf-8")
    rollout = (REPO_ROOT / "docs" / "ROLLOUT.md").read_text(encoding="utf-8")
    for doc, name in ((contract, "AUTH_CONTRACT.md"), (rollout, "ROLLOUT.md")):
        assert auth_contract.RETIRED_CLOCK_SKEW_KEY in doc, (
            f"docs/{name} does not name the retired setting")
    assert "retired" in rollout.lower()


def test_web_session_row_is_untouched_by_a_mobile_rejection(
        raw_client, web_session, mobile_session, monkeypatch):
    """Blast-radius check: revoking a mobile family must not reach into the
    browser session store, and vice versa."""
    monkeypatch.setattr(
        cognito_jwt, "validate_token",
        _rejecting_validator("invalid_signature"))
    assert raw_client.get(
        "/api/v1/account/me",
        headers=_mobile_headers(mobile_session)).status_code == 401
    assert MobileAuthSession.query.one().revoked_at is not None
    assert session_store.get(web_session) is not None


def test_expired_web_session_does_not_revoke_the_mobile_family(
        client, probe_route, web_session, mobile_session, shared_user,
        monkeypatch):
    monkeypatch.setattr(
        cognito_jwt, "validate_token",
        _rejecting_validator("invalid_signature"))
    assert client.get(probe_route).status_code == 302
    assert session_store.get(web_session) is None
    assert MobileAuthSession.query.one().revoked_at is None


def test_session_store_rows_are_scoped_to_one_user(shared_user, make_user):
    """Shared invariant behind both boundaries: a session row belongs to
    exactly one local user and is destroyed when that stops being true."""
    other = make_user("other-owner")
    sid = session_store.create(other, {
        "access_token": "a", "refresh_token": "r", "id_token": "i",
        "expires_in": 3600}, "other-owner")
    with pytest.raises(session_store.SessionInvalid):
        session_store.get_valid_access_token(sid, shared_user.id)
    assert CognitoSession.query.filter_by(session_id=sid).first() is None


def test_mobile_family_absolute_expiry_is_enforced(mobile_session, provider):
    """The mobile family has an absolute lifetime the web session also has
    (COGNITO_SESSION_ABSOLUTE_DAYS); neither may outlive it."""
    family = MobileAuthSession.query.one()
    now = datetime.utcnow()
    assert family.absolute_expires_at > now
    assert family.absolute_expires_at <= now + timedelta(days=7)
