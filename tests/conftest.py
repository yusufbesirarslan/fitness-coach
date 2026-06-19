"""Shared pytest fixtures for the FitX test suite.

Test ortamı hermetiktir: ağ'a çıkmaz, in-memory SQLite kullanır ve gerçek
API anahtarı gerektirmez. Env değişkenleri app paketi import edilmeden ÖNCE
ayarlanmalıdır — app/config.py ve app/extensions.py bunları import sırasında
okur (ör. OpenAI istemcisi anahtar yoksa import'ta patlar).

    python -m pytest -v
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Hermetic test env — assigned (not setdefault) so a developer's real .env /
# shell values can never leak into the suite.
os.environ["OPENAI_API_KEY"] = "test-key-not-used"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["DATABASE_URL"] = "sqlite://"  # in-memory, per-app engine
os.environ["FATSECRET_BASE_URL"] = "https://fatsecret.invalid"  # TLS guard passes, host unroutable
os.environ["FITX_SKIP_DB_INIT"] = "1"  # tables come from db.create_all() in the app fixture
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
os.environ.pop("FLASK_DEBUG", None)
os.environ.pop("FLASK_ENV", None)

import pytest  # noqa: E402
from flask.testing import FlaskClient  # noqa: E402
from werkzeug.datastructures import Headers  # noqa: E402

_STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


class AutoOriginClient(FlaskClient):
    """Test client that sends a same-origin Origin header on state-changing
    requests — exactly what a real browser fetch() does — so requests pass the
    Origin-based CSRF guard (app/hooks.py). Tests that exercise the guard
    itself either pass their own Origin/Referer or use the `raw_client`
    fixture, which adds nothing."""

    def open(self, *args, **kwargs):
        if kwargs.get("method", "GET").upper() in _STATE_CHANGING:
            headers = Headers(kwargs.get("headers") or {})
            if "Origin" not in headers and "Referer" not in headers:
                headers["Origin"] = "http://localhost"
            kwargs["headers"] = headers
        return super().open(*args, **kwargs)


@pytest.fixture
def app():
    from app import create_app
    from app.extensions import db, limiter
    from app.services import gamification

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    flask_app.test_client_class = AutoOriginClient
    # Rate limits off by default; the dedicated rate-limit tests re-enable.
    limiter.enabled = False
    # Disarm the 5-minute weekly-rollover hook so request-driven tests are
    # deterministic; rollover is tested by calling run_weekly_rollover directly.
    gamification._last_rollover_check[0] = datetime.utcnow()

    with flask_app.app_context():
        db.create_all()
        yield flask_app
        db.session.remove()
        db.drop_all()


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
        user = User(username=username, email=email or f"{username}@example.com")
        user.set_password(password)
        for key, value in fields.items():
            setattr(user, key, value)
        db.session.add(user)
        db.session.commit()
        return user

    return _make


@pytest.fixture
def login(client):
    def _login(username="testuser", password="Sifre123"):
        return client.post("/login", json={"username": username, "password": password})

    return _login


@pytest.fixture
def auth_user(make_user, login):
    """Oturum açmış varsayılan kullanıcı; `client` aynı oturumu taşır."""
    user = make_user("testuser")
    login("testuser")
    return user


@pytest.fixture
def make_users_bulk(app):
    """Şifre hash'i olmadan hızlı toplu kullanıcı üretimi (giriş yapamazlar);
    leaderboard gibi çok-kullanıcılı senaryolar için."""
    from app.extensions import db
    from app.models import User

    def _make(n, prefix="bulk", **fields):
        users = []
        for i in range(n):
            user = User(username=f"{prefix}{i}", email=f"{prefix}{i}@example.com",
                        password_hash="not-a-real-hash")
            for key, value in fields.items():
                setattr(user, key, value)
            users.append(user)
            db.session.add(user)
        db.session.commit()
        return users

    return _make
