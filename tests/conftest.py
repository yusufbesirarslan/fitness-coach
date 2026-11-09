"""Shared pytest fixtures for the FitX test suite.

Test ortamı hermetiktir: ağ'a çıkmaz, in-memory SQLite kullanır ve gerçek
API anahtarı gerektirmez. Env değişkenleri app paketi import edilmeden ÖNCE
ayarlanmalıdır — app/config.py ve app/extensions.py bunları import sırasında
okur (ör. OpenAI istemcisi anahtar yoksa import'ta patlar).

    python -m pytest -v
"""
import os
import sys
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Hermetic test env — assigned (not setdefault) so a developer's real .env /
# shell values can never leak into the suite.
os.environ["OPENAI_API_KEY"] = "test-key-not-used"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["DATABASE_URL"] = "sqlite://"  # in-memory, per-app engine
os.environ["FATSECRET_BASE_URL"] = "https://fatsecret.invalid"  # TLS guard passes, host unroutable
os.environ["FITX_SKIP_DB_INIT"] = "1"  # tables come from db.create_all() in the app fixture
os.environ["AUTH_WRITE_RATELIMIT"] = "2 per minute"
os.environ["LOGIN_FAIL_CLOSED"] = "1"
# REDIS_URL: pop YETMEZ — app/config.py'deki load_dotenv() .env'deki
# 'redis://redis:6379' (docker hostname) değerini geri yükler ve limiter/redis
# çözümlenemeyen host'a bağlanmaya çalışır. Boş string ata: load_dotenv mevcut
# değeri override ETMEZ → redis_client=None, limiter memory:// (hermetik).
os.environ["REDIS_URL"] = ""
# BEDROCK_ENABLED: .env'de prod için =1; testlerde KAPALI olmalı — açıksa AI koç
# döngüsü gerçek (lazy) AnthropicBedrock istemcisini kurup AWS'ye çıkmaya çalışır.
# Boş string ata (load_dotenv mevcut değeri override etmez) → hermetik. Bedrock
# yolunu sınayan testler bunu kendi içinde monkeypatch'ler.
os.environ["BEDROCK_ENABLED"] = "0"
os.environ["AI_ADAPTIVE_PLAN_CONTEXT"] = "0"
# S3 + Cognito: .env'de prod değerleri var; testlerde KAPALI olmalı. Açık kalırsa
# testler gerçek AWS'ye çıkar — S3 yüklemeleri prod kovasına yazar, register akışı
# gerçek Cognito'ya gider. Suite zaten "varsayılan kapalı" varsayar (test_s3_helper /
# test_cognito her durumu kendi içinde monkeypatch'ler). Boş ata → hermetik.
os.environ["S3_BUCKET_NAME"] = ""
os.environ["COGNITO_USER_POOL_ID"] = ""
os.environ["COGNITO_APP_CLIENT_ID"] = ""
# Resend (merkezi e-posta altyapısı): testlerde KAPALI — anahtar boş, servis
os.environ['MOBILE_AUTH_DERIVATION_KEYRING'] = (
    json.dumps({'test-v1': 'a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s'})
)
os.environ['MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION'] = 'test-v1'
os.environ['MOBILE_AUTH_ENABLED'] = '1'
# no-op. email_service testleri _get_resend'i kendi içinde monkeypatch'ler.
os.environ["RESEND_API_KEY"] = ""
os.environ.pop("FLASK_DEBUG", None)
os.environ.pop("FLASK_ENV", None)

import pytest  # noqa: E402
from flask.testing import FlaskClient  # noqa: E402
from werkzeug.datastructures import Headers  # noqa: E402


@pytest.fixture(autouse=True)
def _release_disposable_pg_engines(request):
    """Outlive function fixtures, retaining real pooling during each PG race."""
    if (os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1"
            or request.node.get_closest_marker("pg_concurrency") is None):
        yield
        return
    from tests.pg_engine_lifecycle import disposable_pg_engine_scope
    with disposable_pg_engine_scope():
        yield


@pytest.fixture(autouse=True)
def _isolate_source_launch_policy():
    # Existing layer tests supply synthetic policies/contracts independently.
    # Do not weaken production's final cohort/readiness fence to accommodate
    # them. Launch integration tests opt in to an explicit source policy.
    from app.services.ai_allowances import launch_policy
    # Some crash tests explicitly undo their own patches mid-test. Keep this
    # layer boundary independent of that operation.
    with pytest.MonkeyPatch.context() as isolated:
        isolated.setattr(launch_policy, "APPROVED_POLICY", None)
        yield


@pytest.fixture
def approved_launch_policy(app, monkeypatch):
    from app.services.ai_allowances import launch_policy
    monkeypatch.setattr(launch_policy, "APPROVED_POLICY", launch_policy.LAUNCH_V1_POLICY)
    app.config["AI_ALLOWANCE_POLICY"] = launch_policy.LAUNCH_V1_POLICY
    return launch_policy.LAUNCH_V1_POLICY


@pytest.fixture
def social_reopened(monkeypatch):
    """TEST-ONLY: reopen the Launch V1 social boundary.

    Production has no channel for this (``launch_social.SOCIAL_AVAILABLE`` is a
    code constant). The legacy social suites use it so the dormant social code
    stays characterized for a future, separately authorized reactivation; the
    closed default is proven in ``tests/test_launch_v1_social_boundary.py``.
    """
    from app.services import launch_social
    monkeypatch.setattr(launch_social, "SOCIAL_AVAILABLE", True)


@pytest.fixture(autouse=True)
def _isolate_ai_spend_guard_local_counters():
    # The production guard uses Redis windows. Hermetic tests intentionally
    # have no Redis, so process-local windows must not leak between test cases.
    from app.services import ai_spend_guard
    ai_spend_guard._reset_local_for_tests()
    yield
    ai_spend_guard._reset_local_for_tests()


def pytest_addoption(parser):
    parser.addoption(
        "--run-authoritative-linux-lock-tests",
        action="store_true",
        default=False,
        help="confirm the dedicated root Linux production-lock CI environment",
    )

_STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


_TEST_CSRF_TOKEN = "test-csrf-token"


class AutoOriginClient(FlaskClient):
    """Test client that mimics a real browser fetch() on state-changing requests
    so they pass both CSRF layers (app/hooks.py):
      - a same-origin Origin header (layer 1: Origin/Referer)
      - an X-CSRFToken header matching a token seeded into the session
        (layer 2: synchronizer token)
    Tests that exercise the guard itself pass their own headers or use the
    `raw_client` fixture, which adds nothing."""

    def open(self, *args, **kwargs):
        if kwargs.get("method", "GET").upper() in _STATE_CHANGING:
            headers = Headers(kwargs.get("headers") or {})
            if "Origin" not in headers and "Referer" not in headers:
                headers["Origin"] = "http://localhost"
            if "X-CSRFToken" not in headers:
                # Oturuma token tohumla ve aynısını başlık olarak gönder — gerçek
                # tarayıcının meta etiketinden okuyup gönderdiği akışın eşi.
                with self.session_transaction() as sess:
                    token = sess.get("_csrf_token")
                    if not token:
                        token = _TEST_CSRF_TOKEN
                        sess["_csrf_token"] = token
                headers["X-CSRFToken"] = token
            kwargs["headers"] = headers
        return super().open(*args, **kwargs)


@pytest.fixture
def app():
    monkeypatch = pytest.MonkeyPatch()
    from app import create_app
    from app.extensions import db, limiter
    from app.services import gamification

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    # Existing domain/transport tests use synthetic, prospectively consented
    # owners. This SQLite adapter is NOT distributed-authority qualification;
    # test_health_consent_pg exercises the real cross-connection PG boundary.
    # New refusal/existing-account tests explicitly request health_consent=False.
    from contextlib import contextmanager
    from app.services import health_consent, health_disclosure
    @contextmanager
    def sqlite_health_authority(owner, *, exclusive=False):
        from app.extensions import db
        with db.engine.begin() as connection:
            yield connection
    monkeypatch.setattr(health_consent, "_authority", sqlite_health_authority)
    monkeypatch.setattr(health_disclosure, "APPROVED_VERSION", health_disclosure.VERSION)
    # WSGI servers close every live iterable. Tests may intentionally retain
    # responses (including streaming ones) until the test ends. Close those at
    # fixture teardown, never eagerly during assertions/call_on_close checks.
    import weakref
    live_iterables = weakref.WeakSet()
    application = flask_app.wsgi_app
    def track_iterables(environ, start_response):
        result = application(environ, start_response)
        if hasattr(result, "close"):
            live_iterables.add(result)
        return result
    flask_app.wsgi_app = track_iterables
    flask_app.test_client_class = AutoOriginClient
    # Rate limits off by default; the dedicated rate-limit tests re-enable.
    limiter.enabled = False
    # Freemium AI-plan kotası da kapalı; davranış testleri planı tekrar tekrar
    # üretebilsin. Kotanın kendisini sınayan testler config'i açar.
    flask_app.config["AI_PLAN_QUOTA_ENABLED"] = False
    # Disarm the 5-minute weekly-rollover hook so request-driven tests are
    # deterministic; rollover is tested by calling run_weekly_rollover directly.
    gamification._last_rollover_check[0] = datetime.utcnow()
    # Disarm the daily maintenance throttle too. A test that forces only the
    # rollover throttle (e.g. test_weekly_rollover_receives_istanbul_now) would
    # otherwise pass the process-global purge throttle whenever it is the first
    # in its process to reach it, and the hook dispatches the REAL
    # run_daily_maintenance on a daemon thread (no queue in tests). That thread
    # outlives the test and queries the single shared in-memory SQLite
    # connection while teardown's drop_all() runs (CI run 42621146878 shard 2).
    # Dispatch tests opt back in by patching _purge_throttle_passed or
    # _last_purge_check themselves; this is restored at teardown.
    from app import hooks
    monkeypatch.setattr(hooks, "_last_purge_check", [datetime.utcnow()])

    with flask_app.app_context():
        db.create_all()
        yield flask_app
        for iterable in list(live_iterables):
            iterable.close()
        db.session.remove()
        db.drop_all()
    monkeypatch.undo()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def raw_client(app):
    """Plain client without the auto-Origin header — for CSRF guard tests."""
    return FlaskClient(app, app.response_class)


@pytest.fixture
def make_user(app):
    from app.extensions import db
    from app.models import User

    def _make(username="testuser", password="Sifre123", email=None, **fields):
        from onboarding_support import mark_onboarded

        user = User(username=username, email=email or f"{username}@example.com")
        fields.setdefault("cognito_sub", f"sub-{username}")
        health_permission = fields.pop("health_consent", True)
        onboarded = fields.pop("profile_complete", None) is True
        for key, value in fields.items():
            setattr(user, key, value)
        db.session.add(user)
        if onboarded:
            # "Profile complete" means onboarded under the ONE canonical rule
            # (flag AND canonical UserSession) — see tests/onboarding_support.py.
            mark_onboarded(user)
        db.session.commit()
        if health_permission:
            from app.services import health_consent, health_disclosure
            health_consent.mutate(user.id, dict(action="affirmative_grant",
                purpose=health_disclosure.PURPOSE, version=health_disclosure.VERSION,
                locale="en", text_digest=health_disclosure.DIGESTS["en"],
                expected_generation=0, request_id="synthetic-first-party-grant"))
        return user

    return _make


@pytest.fixture
def login(client, monkeypatch):
    from app.blueprints import auth as auth_bp
    from app.services import cognito_jwt, cognito_service

    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", True)

    def _authenticate(username, _password):
        return {
            "tokens": {
                "access_token": f"acc-{username}",
                "id_token": f"id-{username}",
                "refresh_token": f"ref-{username}",
                "expires_in": 3600,
            },
            "claims": {"sub": f"sub-{username}"},
        }

    monkeypatch.setattr(cognito_service, "authenticate", _authenticate)
    monkeypatch.setattr(
        cognito_jwt,
        "validate_token",
        # **kw: the canonical caller (app/services/auth_contract.py) passes
        # leeway_seconds; the stub must accept the real signature.
        lambda token, use, **kw: {
            "sub": f"sub-{token.removeprefix('id-').removeprefix('acc-')}"
        },
    )

    def _login(username="testuser", password="Sifre123"):
        return client.post("/login", json={"username": username, "password": password})

    return _login


@pytest.fixture
def cognito_registration(monkeypatch):
    """Enable hermetic native Cognito registration/confirmation for route tests."""
    from app.blueprints import auth as auth_bp
    from app.services import cognito_service

    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", True)
    monkeypatch.setattr(
        cognito_service,
        "sign_up",
        lambda username, password, email, name, language=None: f"sub-{username}",
    )
    monkeypatch.setattr(
        cognito_service, "confirm_sign_up", lambda username, code: None)


@pytest.fixture
def auth_user(make_user, login):
    """Oturum açmış varsayılan kullanıcı; `client` aynı oturumu taşır."""
    user = make_user("testuser")
    login("testuser")
    return user


@pytest.fixture
def make_users_bulk(app):
    """Stable Cognito identities ile hızlı toplu kullanıcı üretimi."""
    from app.extensions import db
    from app.models import User

    def _make(n, prefix="bulk", **fields):
        from onboarding_support import mark_onboarded

        onboarded = fields.pop("profile_complete", None) is True
        users = []
        for i in range(n):
            user = User(
                username=f"{prefix}{i}",
                email=f"{prefix}{i}@example.com",
                cognito_sub=f"sub-{prefix}{i}",
            )
            for key, value in fields.items():
                setattr(user, key, value)
            users.append(user)
            db.session.add(user)
        if onboarded:
            for user in users:
                mark_onboarded(user)
        db.session.commit()
        return users

    return _make


@pytest.fixture
def plan_expectation(app):
    """The concurrency precondition a caller that has just read canonical state
    would send to ``POST /training-plan/save``.

    ``None`` when the owner has no plan ("I expect no active plan"), otherwise
    the current lineage/version pair. Derived from ``get_active_plan`` — the
    canonical active-plan selector — so a test never encodes a second opinion
    about which row is active.
    """
    from app.services.today_facts import get_active_plan

    def _expectation(user_id):
        plan = get_active_plan(user_id)
        if plan is None:
            return None
        return {"lineage_id": plan.lineage_id,
                "mutation_version": plan.mutation_version}

    return _expectation


@pytest.fixture
def synthetic_ai_layer(app, monkeypatch):
    """Explicit isolation for legacy mocked-provider domain/transport tests.

    This is NOT evidence of consent authorization. Real D1 grant/deny/fencing
    tests never request this fixture and use the PostgreSQL authority. The
    patch is undone after each selected test; it grants no account permission.
    """
    from consent_projection_support import allow_synthetic_authority
    allow_synthetic_authority(monkeypatch, app, memory=False)


@pytest.fixture
def synthetic_ai_memory_layer(app, monkeypatch):
    """Explicit mock ALLOW for optional-memory behavior with fake providers.

    Only memory-specific legacy tests opt in. This does not qualify real
    permission or change the release's AI Memory default OFF.
    """
    from consent_projection_support import allow_synthetic_authority
    allow_synthetic_authority(monkeypatch, app, memory=True)


@pytest.fixture
def synthetic_operation_authority(app, monkeypatch):
    """Explicit ALLOW at the LP21 operation-fencing decision points only.

    For pre-existing first-plan/replacement/Nutrition command tests whose fake
    ``chat_fn`` never reaches the provider door: operation capture,
    publication and canonical-write fences answer ALLOW at generation 1. Every
    other D1 surface (closed endpoints, dispatch, registry) stays real. Real
    fencing is qualified only by ``tests/test_lp21_d1_operation_fencing_pg.py``.
    """
    from consent_projection_support import allow_operation_fences
    allow_operation_fences(monkeypatch)


@pytest.fixture
def legacy_browser_plan_saves(app, monkeypatch):
    """Re-open ONLY the two D1-closed browser plan-save aliases (LP21).

    For the pre-existing save-path characterization suites (score, schema,
    precondition, markup, lock order). The shared writers still refuse a real
    stale authority; only the provenance-less ``None`` the aliases pass is
    allowed here. Production keeps both aliases closed.
    """
    from consent_projection_support import open_browser_plan_saves
    open_browser_plan_saves(monkeypatch)


@pytest.fixture
def provider_persistence_permitted(monkeypatch):
    """TEST-ONLY stand-in for a future verified written FatSecret retention
    permission (D-B2). Production keeps PROVIDER_PERSISTENCE_PERMISSION_REF=None,
    so new provider-backed diary/saved-meal persistence is held. Opt in only in
    suites that qualify the persistence mechanics themselves; the hold has its
    own tests in tests/test_fatsecret_provider_persistence_hold.py."""
    from app.services import fatsecret_boundary
    monkeypatch.setattr(fatsecret_boundary, "PROVIDER_PERSISTENCE_PERMISSION_REF",
                        "TEST-ONLY-not-a-real-permission")
    # Also opens the paths the retention design does not cover yet (web barcode
    # add, social suggestion totals) so their MECHANICS stay qualified; their
    # production hold is pinned in tests/test_fatsecret_provider_persistence_hold.py.
    monkeypatch.setattr(fatsecret_boundary, "UNCOVERED_PROVIDER_PERSISTENCE_REF",
                        "TEST-ONLY-not-a-real-permission")
