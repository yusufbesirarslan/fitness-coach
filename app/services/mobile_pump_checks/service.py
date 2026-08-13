"""Idempotent owner-scoped creation and reading of canonical Pump Checks."""
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from flask import current_app
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

import s3_helper
from app.extensions import db
from app.models import PumpCheck
from app.services.mobile_pump_checks.analysis import (
    ANALYSIS_VERSION,
    InvalidAnalysis,
    analyze_image,
)
from app.services.mobile_pump_checks.identity import (
    pump_check_id,
    resolve_pump_check_row_id,
)


BODY_REGIONS = frozenset({
    "full_body", "upper_body", "lower_body", "back", "arms", "legs",
})
ENVIRONMENTS = frozenset({"gym", "home", "outdoor", "other"})
FINGERPRINT_DOMAIN = "axisai/mobile-pump-check-create/v1"
ANALYSIS_LEASE_SECONDS = 120


class InvalidCommand(ValueError):
    pass


class IdempotencyConflict(Exception):
    pass


class PumpCheckNotFound(Exception):
    pass


class PumpCheckUnavailable(Exception):
    pass


class StorageUnavailable(PumpCheckUnavailable):
    pass


class ProviderUnavailable(PumpCheckUnavailable):
    pass


class AnalysisInvalid(PumpCheckUnavailable):
    pass


@dataclass(frozen=True)
class CreateCommand:
    image_bytes: bytes
    media_type: str
    body_region: str
    environment: str
    description: str
    captured_at: datetime


def parse_captured_at(value, now=None):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise InvalidCommand("captured_at must be UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise InvalidCommand("captured_at is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise InvalidCommand("captured_at must be UTC")
    now = now or datetime.now(timezone.utc)
    if parsed > now + timedelta(minutes=10) or parsed < now - timedelta(days=365):
        raise InvalidCommand("captured_at is outside bounds")
    return parsed.replace(tzinfo=None)


def create_command(image_bytes, media_type, body_region, environment,
                   description, captured_at):
    body_region = (body_region or "").strip().lower()
    environment = (environment or "").strip().lower()
    description = (description or "").strip()
    if body_region not in BODY_REGIONS or environment not in ENVIRONMENTS:
        raise InvalidCommand("invalid region or environment")
    if len(description) > 200:
        raise InvalidCommand("description is too long")
    return CreateCommand(
        image_bytes=image_bytes,
        media_type=media_type,
        body_region=body_region,
        environment=environment,
        description=description,
        captured_at=parse_captured_at(captured_at),
    )


def fingerprint(command):
    semantic = {
        "domain": FINGERPRINT_DOMAIN,
        "image_sha256": hashlib.sha256(command.image_bytes).hexdigest(),
        "body_region": command.body_region,
        "environment": command.environment,
        "description": command.description,
        "captured_at": command.captured_at.isoformat(timespec="seconds") + "Z",
    }
    encoded = json.dumps(
        semantic, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _existing(user_id, key):
    return PumpCheck.query.filter_by(
        user_id=user_id, idempotency_key=key).first()


def _claim_row(user_id, key, command):
    digest = fingerprint(command)
    existing = _existing(user_id, key)
    if existing is not None:
        if existing.idempotency_fingerprint != digest:
            raise IdempotencyConflict()
        return existing, False
    row = PumpCheck(
        user_id=user_id,
        captured_at=command.captured_at,
        body_region=command.body_region,
        location_type=command.environment,
        description=command.description,
        visibility="private",
        valid=True,
        fallback=False,
        analysis_status="pending",
        idempotency_key=key,
        idempotency_fingerprint=digest,
        date_key=None,
    )
    db.session.add(row)
    try:
        db.session.commit()
        return row, True
    except IntegrityError:
        db.session.rollback()
        existing = _existing(user_id, key)
        if existing is None:
            raise
        if existing.idempotency_fingerprint != digest:
            raise IdempotencyConflict()
        return existing, False


def create_or_replay(user_id, key, command):
    row, created = _claim_row(user_id, key, command)
    row_id = row.id
    db.session.rollback()
    attempt = _claim_analysis(row_id, user_id)
    if attempt is None:
        return db.session.get(PumpCheck, row_id), False
    image_key = db.session.query(PumpCheck.image_key).filter_by(
        id=row_id, user_id=user_id).scalar()
    db.session.rollback()
    try:
        if not image_key:
            if not s3_helper.is_enabled():
                raise StorageUnavailable("private image storage unavailable")
            image_key = s3_helper.upload_image(
                command.image_bytes,
                content_type=command.media_type,
                prefix="pump-checks",
                user_id=user_id,
            )
            PumpCheck.query.filter_by(
                id=row_id, user_id=user_id,
                analysis_status="analyzing",
                analysis_attempt=attempt,
            ).update({PumpCheck.image_key: image_key}, synchronize_session=False)
            db.session.commit()
        analysis = analyze_image(
            command.image_bytes,
            command.media_type,
            {
                "body_region": command.body_region,
                "environment": command.environment,
                "description": command.description,
            },
        )
        if not _finalize_success(row_id, user_id, attempt, analysis):
            return db.session.get(PumpCheck, row_id), False
        return db.session.get(PumpCheck, row_id), created
    except Exception as exc:
        db.session.rollback()
        kind = _failure_kind(exc)
        _finalize_failure(row_id, user_id, attempt, kind)
        if isinstance(exc, StorageUnavailable):
            raise
        if isinstance(exc, s3_helper.S3Error):
            raise StorageUnavailable("private image storage unavailable") from exc
        if isinstance(exc, InvalidAnalysis):
            raise AnalysisInvalid("provider analysis was invalid") from exc
        raise ProviderUnavailable("pump check provider unavailable") from exc


def _claim_analysis(row_id, user_id, now=None):
    now = now or datetime.utcnow()
    stale_before = now - timedelta(seconds=ANALYSIS_LEASE_SECONDS)
    eligible = or_(
        PumpCheck.analysis_status == "pending",
        (
            (PumpCheck.analysis_status == "failed")
            & (PumpCheck.analysis_failure_kind != "persistence")
        ),
        (
            (PumpCheck.analysis_status == "analyzing")
            & (PumpCheck.analysis_started_at < stale_before)
        ),
    )
    claimed = PumpCheck.query.filter(
        PumpCheck.id == row_id,
        PumpCheck.user_id == user_id,
        eligible,
    ).update({
        PumpCheck.analysis_status: "analyzing",
        PumpCheck.analysis_started_at: now,
        PumpCheck.analysis_attempt: func.coalesce(
            PumpCheck.analysis_attempt, 0) + 1,
        PumpCheck.analysis_failure_kind: None,
    }, synchronize_session=False)
    db.session.commit()
    if not claimed:
        return None
    attempt = db.session.query(PumpCheck.analysis_attempt).filter_by(
        id=row_id, user_id=user_id).scalar()
    db.session.rollback()
    return attempt


def _finalize_success(row_id, user_id, attempt, analysis):
    updated = PumpCheck.query.filter_by(
        id=row_id,
        user_id=user_id,
        analysis_status="analyzing",
        analysis_attempt=attempt,
    ).update({
        PumpCheck.analysis: analysis,
        PumpCheck.analysis_version: ANALYSIS_VERSION,
        PumpCheck.analysis_status: "completed",
        PumpCheck.analysis_started_at: None,
        PumpCheck.analysis_failure_kind: None,
    }, synchronize_session=False)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        completed = db.session.query(PumpCheck.analysis_status).filter_by(
            id=row_id, user_id=user_id,
            analysis_attempt=attempt).scalar() == "completed"
        db.session.rollback()
        if completed:
            return True
        _finalize_failure(row_id, user_id, attempt, "persistence")
        raise
    return bool(updated)


def _finalize_failure(row_id, user_id, attempt, kind):
    PumpCheck.query.filter_by(
        id=row_id,
        user_id=user_id,
        analysis_status="analyzing",
        analysis_attempt=attempt,
    ).update({
        PumpCheck.analysis: None,
        PumpCheck.analysis_version: None,
        PumpCheck.analysis_status: "failed",
        PumpCheck.analysis_started_at: None,
        PumpCheck.analysis_failure_kind: kind,
    }, synchronize_session=False)
    db.session.commit()


def _failure_kind(error):
    if isinstance(error, (StorageUnavailable, s3_helper.S3Error)):
        return "storage"
    if isinstance(error, InvalidAnalysis):
        return "invalid_output"
    return "provider"


def get_owned(user_id, token, secret):
    row_id = resolve_pump_check_row_id(secret, user_id, token)
    if row_id is None:
        raise PumpCheckNotFound()
    row = PumpCheck.query.filter_by(id=row_id, user_id=user_id).first()
    if row is None:
        raise PumpCheckNotFound()
    return row


def _iso(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def serialize_pump_check(row, user_id, secret):
    image_url = None
    if row.image_key and s3_helper.is_enabled():
        image_url = s3_helper.generate_presigned_url(
            row.image_key, expires_in=3600, expected_user_id=user_id)
    return {
        "id": pump_check_id(secret, user_id, row.id),
        "captured_at": _iso(row.captured_at),
        "created_at": _iso(row.created_at),
        "body_region": row.body_region,
        "environment": row.location_type,
        "description": row.description or "",
        "image_url": image_url,
        "analysis_status": row.analysis_status or "unavailable",
        "analysis_version": row.analysis_version,
        "analysis": row.analysis,
    }
