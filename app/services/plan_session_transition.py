"""Narrow PostgreSQL authority: session start versus native replacement only.

Two-int advisory keys are disjoint from existing single-bigint advisory locks.
Never acquire from a day/session/User/plan lock holder. Never log owner/key.
SQLite is a no-op for sequential service tests, not a concurrency guarantee.
"""
from sqlalchemy import text
from app.extensions import db

TRANSITION_LOCK_NAMESPACE = 0x41584918


def lock_plan_session_transition(user_id):
    if type(user_id) is not int or not 0 < user_id < 2**31:
        raise ValueError("invalid transition owner")
    if db.session.get_bind().dialect.name == "postgresql":
        db.session.execute(text("SELECT pg_advisory_xact_lock(:namespace, :owner)"),
                           {"namespace": TRANSITION_LOCK_NAMESPACE, "owner": user_id})
