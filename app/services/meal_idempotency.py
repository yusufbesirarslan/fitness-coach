"""Durable, user-scoped replay protection for meal ledger writes."""
import re

from flask import request
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import MealLog


_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


def read_idempotency_key():
    """Return a valid optional request key without exposing it in responses."""
    key = request.headers.get("Idempotency-Key", "")
    return key if _KEY_RE.fullmatch(key) else None


def find_existing(user_id, key):
    """Find a prior ledger write only within the authenticated user's scope."""
    if not key:
        return None
    return MealLog.query.filter_by(user_id=user_id, idempotency_key=key).first()


def commit_once(entry, key):
    """Commit a meal once, resolving a uniqueness race to its winner row."""
    entry.idempotency_key = key
    db.session.add(entry)
    try:
        db.session.commit()
        return entry, True
    except IntegrityError:
        db.session.rollback()
        existing = find_existing(entry.user_id, key)
        if existing is None:
            raise
        return existing, False
