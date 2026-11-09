"""Minimal first-party evidence; no health payload, IP or device fingerprint."""
from app.extensions import db


class HealthConsentState(db.Model):
    __tablename__ = "health_consent_state"
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    generation = db.Column(db.BigInteger, nullable=False)
    record = db.Column(db.JSON, nullable=False)
    changed_at = db.Column(db.DateTime, nullable=False)
    __table_args__ = (db.CheckConstraint("generation > 0", name="ck_health_consent_generation"),)


class HealthConsentEvent(db.Model):
    __tablename__ = "health_consent_event"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    generation = db.Column(db.BigInteger, nullable=False)
    purpose = db.Column(db.String(80), nullable=False)
    version = db.Column(db.String(80), nullable=False)
    locale = db.Column(db.String(8), nullable=False)
    text_digest = db.Column(db.String(64), nullable=False)
    action = db.Column(db.String(24), nullable=False)
    occurred_at = db.Column(db.DateTime, nullable=False)
    request_id = db.Column(db.String(80), nullable=False)
    request_digest = db.Column(db.String(64), nullable=False)
    __table_args__ = (
        db.UniqueConstraint("owner_id", "request_id", name="uq_health_consent_request"),
        db.UniqueConstraint("owner_id", "generation", name="uq_health_consent_generation"),
        db.CheckConstraint("generation > 0", name="ck_health_event_generation"),
        db.CheckConstraint("action IN ('affirmative_grant', 'decline', 'withdraw')", name="ck_health_event_action"),
    )


class HealthErasure(db.Model):
    """One erasure owed after a withdrawal. Ids and times only, no health payload.

    No foreign key on purpose: the record of an erasure outlives the account it
    describes (Erasure Regulation Art. 7(3)) and is itself removed three years
    after completion by ``health_erasure.run_pending``.
    """
    __tablename__ = "health_erasure"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, nullable=False)
    generation = db.Column(db.BigInteger, nullable=False)
    requested_at = db.Column(db.DateTime, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)
    summary = db.Column(db.JSON, nullable=True)
    __table_args__ = (
        db.UniqueConstraint("owner_id", "generation", name="uq_health_erasure_generation"),
        db.CheckConstraint("generation > 0", name="ck_health_erasure_generation"),
    )


class HealthLegalHold(db.Model):
    """Controller-set exemption for named categories under one documented matter."""
    __tablename__ = "health_legal_hold"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    categories = db.Column(db.JSON, nullable=False)
    matter_ref = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False)
    review_at = db.Column(db.DateTime, nullable=False)
    released_at = db.Column(db.DateTime, nullable=True)
