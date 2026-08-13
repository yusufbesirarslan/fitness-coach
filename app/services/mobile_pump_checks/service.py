"""Idempotent owner-scoped creation and reading of canonical Pump Checks."""
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from flask import current_app
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
    matches_pump_check_id,
    pump_check_id,
)


BODY_REGIONS = frozenset({
    "full_body", "upper_body", "lower_body", "back", "arms", "legs",
})
ENVIRONMENTS = frozenset({"gym", "home", "outdoor", "other"})
FINGERPRINT_DOMAIN = "axisai/mobile-pump-check-create/v1"


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
    if not created and row.analysis_status in {"completed", "analyzing"}:
        return row, False
    claimed = PumpCheck.query.filter(
        PumpCheck.id == row.id,
        PumpCheck.user_id == user_id,
        PumpCheck.analysis_status.in_(("pending", "failed")),
    ).update({PumpCheck.analysis_status: "analyzing"}, synchronize_session=False)
    db.session.commit()
    if not claimed:
        return db.session.get(PumpCheck, row.id), False
    row = db.session.get(PumpCheck, row.id)
    try:
        if not row.image_key:
            if not s3_helper.is_enabled():
                raise StorageUnavailable("private image storage unavailable")
            row.image_key = s3_helper.upload_image(
                command.image_bytes,
                content_type=command.media_type,
                prefix="pump-checks",
                user_id=user_id,
            )
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
        row.analysis = analysis
        row.analysis_version = ANALYSIS_VERSION
        row.analysis_status = "completed"
        db.session.commit()
        return row, created
    except Exception as exc:
        db.session.rollback()
        row = db.session.get(PumpCheck, row.id)
        row.analysis = None
        row.analysis_version = None
        row.analysis_status = "failed"
        db.session.commit()
        if isinstance(exc, StorageUnavailable):
            raise
        if isinstance(exc, s3_helper.S3Error):
            raise StorageUnavailable("private image storage unavailable") from exc
        if isinstance(exc, InvalidAnalysis):
            raise AnalysisInvalid("provider analysis was invalid") from exc
        raise ProviderUnavailable("pump check provider unavailable") from exc


def get_owned(user_id, token, secret):
    if not isinstance(token, str) or len(token) != 24:
        raise PumpCheckNotFound()
    for row in PumpCheck.query.filter_by(user_id=user_id).all():
        if matches_pump_check_id(secret, user_id, row.id, token):
            return row
    raise PumpCheckNotFound()


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
