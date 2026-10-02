"""Shared helpers for the NUTR-PR7 native Nutrition contract tests.

Authentication goes through the REAL ``require_mobile_auth`` boundary with only
the principal resolution stubbed (the credential store is proved elsewhere —
tests/test_mobile_auth_api.py), the same convention as
tests/test_mobile_nutrition_api.py. Headers carry a per-user opaque credential
so two accounts can interleave in one test.
"""
import json
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import MealLog, NutritionPlan, UserSession, WaterLog
from app.services import mobile_auth

ENVELOPE_KEYS = {"code", "message", "retryable", "request_id"}

PLAN_DOC = {
    "isim": "Lean plan",
    "kahvalti": {"yemekler": ["Oats - 60g", "Milk - 200ml"],
                 "kalori": 450, "protein": 30, "karb": 50, "yag": 12},
    "aksam": {"yemekler": ["Salmon - 150g"],
              "kalori": 650, "protein": 45, "karb": 60, "yag": 20},
    "toplam_kalori": 1100, "toplam_protein": 75,
    "toplam_karb": 110, "toplam_yag": 32,
}


@pytest.fixture
def bearer(monkeypatch):
    """``bearer(user)`` → headers resolving to that user through the real gate."""
    by_credential = {}

    def authenticate(raw):
        user = by_credential.get(raw)
        if user is None:
            raise mobile_auth.MobileAuthFailure(
                "AUTH_SESSION_EXPIRED", 401, False, "unknown")
        return mobile_auth.MobilePrincipal(
            user, SimpleNamespace(id=1), {"sub": user.cognito_sub})

    monkeypatch.setattr(mobile_auth, "authenticate_access", authenticate)

    def headers(user, **extra):
        credential = f"opaque-{user.id}"
        by_credential[credential] = user
        result = {"Authorization": f"Bearer {credential}"}
        result.update(extra)
        return result
    return headers


@pytest.fixture
def native(app):
    """A cookie-less native client (no auto-Origin, no browser session)."""
    from flask.testing import FlaskClient
    return FlaskClient(app, app.response_class)


@pytest.fixture
def no_provider(monkeypatch):
    """Any real model/provider entry point fails the test if reached."""
    from app.services import ai

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a real model provider was called")
    for name in ("_openai_chat", "_claude_chat", "_heavy_complete", "_heavy_chat"):
        monkeypatch.setattr(ai, name, forbidden)


def error_of(response):
    body = response.get_json()
    assert set(body) == {"error"}, body
    assert set(body["error"]) == ENVELOPE_KEYS, body
    return body["error"]


def quoted(token):
    return f'"{token}"'


def set_target(user_id, kcal=2100, goal="kas kazanma"):
    session = UserSession.query.filter_by(user_id=user_id).first()
    if session is None:
        session = UserSession(user_id=user_id)
        db.session.add(session)
    session.target_calories = kcal
    session.goal = goal
    db.session.commit()


def save_plan_row(user_id, document=PLAN_DOC, score=8.0, raw=None):
    row = NutritionPlan(
        user_id=user_id,
        plan_data=raw if raw is not None else json.dumps(document, ensure_ascii=False),
        score=score)
    db.session.add(row)
    db.session.commit()
    return row


def add_meal(user_id, day_key, kcal=500.0, label="Öğle", text="Rice", created_at=None):
    from datetime import datetime
    row = MealLog(user_id=user_id, ogun=label, yemekler=text, kalori=kcal,
                  protein=20.0, karb=60.0, yag=10.0, tarih=day_key,
                  source="manual", created_at=created_at or datetime.utcnow())
    db.session.add(row)
    db.session.commit()
    return row


def set_water(user_id, day_key, count):
    row = WaterLog.query.filter_by(user_id=user_id, date_key=day_key).first()
    if row is None:
        row = WaterLog(user_id=user_id, date_key=day_key, count=count)
        db.session.add(row)
    else:
        row.count = count
    db.session.commit()


class StatementCounter:
    """Counts SQL statements issued on the engine while active."""

    def __init__(self):
        self.statements = []

    def __enter__(self):
        from sqlalchemy import event
        self._engine = db.engine
        event.listen(self._engine, "before_cursor_execute", self._record)
        return self

    def _record(self, _conn, _cursor, statement, *_args):
        self.statements.append(statement)

    def __exit__(self, *_exc):
        from sqlalchemy import event
        event.remove(self._engine, "before_cursor_execute", self._record)

    def selects(self):
        return [s for s in self.statements if s.lstrip().upper().startswith("SELECT")]

    def writes(self):
        return [s for s in self.statements
                if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
