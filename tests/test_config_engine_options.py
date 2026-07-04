"""A3: dış RDS'e karşı SQLAlchemy havuz dayanıklılığı (app/config.py).

Boşta kalan havuz bağlantılarını RDS/NAT sessizce koparır; pool_pre_ping ölü
bağlantıyı kullanmadan önce yakalamalı, pool_recycle (< RDS idle timeout)
proaktif tazelemeli. SQLite'ta (lokal/test) bu ayarlara gerek yok.

    python -m pytest tests/test_config_engine_options.py -v
"""
from flask import Flask

from app.config import configure_app


def test_engine_options_pool_resiliency_for_postgres(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@rds.example:5432/fitx")
    app = Flask(__name__)
    configure_app(app)
    opts = app.config["SQLALCHEMY_ENGINE_OPTIONS"]
    assert opts["pool_pre_ping"] is True
    assert 0 < opts["pool_recycle"] < 300  # RDS idle timeout'un altında


def test_engine_options_applied_to_legacy_postgres_scheme(monkeypatch):
    # Heroku-tarzı postgres:// → postgresql:// normalizasyonundan sonra da uygulanmalı.
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@rds.example:5432/fitx")
    app = Flask(__name__)
    configure_app(app)
    assert app.config["SQLALCHEMY_ENGINE_OPTIONS"]["pool_pre_ping"] is True


def test_engine_options_not_set_for_sqlite(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    app = Flask(__name__)
    configure_app(app)
    assert "SQLALCHEMY_ENGINE_OPTIONS" not in app.config
