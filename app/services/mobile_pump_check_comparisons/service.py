"""Owner-scoped eligibility, convergence, and serialization for comparisons."""
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from flask import current_app
from sqlalchemy import null as sa_null
from sqlalchemy.exc import IntegrityError

import s3_helper
from app.extensions import db
from app.models import (
    PumpCheck,
    PumpCheckComparison,
    PumpCheckComparisonRequest,
)
from app.services.mobile_pump_check_comparisons.analysis import (
    ANALYSIS_VERSION,
    InvalidComparisonAnalysis,
    analyze_images,
)
from app.services.mobile_pump_check_comparisons.identity import (
    fingerprint,
    is_valid_comparison_id,
    new_comparison_id,
)
from app.services.mobile_pump_checks.analysis import (
    ANALYSIS_VERSION as SOURCE_ANALYSIS_VERSION,
    InvalidAnalysis,
    parse_analysis as parse_source_analysis,
)
from app.services.mobile_pump_checks.identity import is_valid_pump_check_id
from app.services.vision_images import (
    ImagePreparationError,
    prepare_image_for_vision,
)


COMMAND_FIELDS = frozenset({
    "baseline_pump_check_id", "current_pump_check_id",
})
BLOCKING_SOURCE_QUALITY = "insufficient"
LIMITED_SOURCE_QUALITY = "limited"
ANALYSIS_LEASE_SECONDS = 900
# `invalid_media` is terminal: the stored object cannot become decodable, so
# reclaiming it would only repeat the same bounded failure. `persistence` is
# terminal for the same reason PR1 treats it so — the row is unreconciled.
RETRYABLE_FAILURES = frozenset({"storage", "invalid_output", "provider"})


class InvalidCommand(ValueError):
    """The create command does not match the exact public contract."""


class PumpCheckNotFound(Exception):
    """A source Pump Check is unknown, malformed, or owned by someone else."""


class ComparisonNotFound(Exception):
    """A comparison is unknown, malformed, or owned by someone else."""


class ChecksNotComparable(Exception):
    """The pair fails a deterministic eligibility rule."""


class MediaNotComparable(ChecksNotComparable):
    """Canonical media is permanently unusable for a comparison."""


class IdempotencyConflict(Exception):
    """The owner reused a key for a different command."""


class ComparisonUnavailable(Exception):
    """A bounded transient failure; the same key may be retried."""


class PersistenceFinalizationUncertain(RuntimeError):
    """Finalization could neither be committed nor reconciled."""


@dataclass(frozen=True)
class CreateCommand:
    baseline_token: str
    current_token: str


@dataclass(frozen=True)
class SourceMedia:
    """Plain values captured before any transaction boundary is crossed."""

    baseline_key: str
    current_key: str
    body_region: str
    source_quality_cap: str


@dataclass(frozen=True)
class EligibleSources:
    baseline: PumpCheck
    current: PumpCheck
    source_quality_cap: str


def create_command(value):
    if not isinstance(value, dict) or set(value) != COMMAND_FIELDS:
        raise InvalidCommand("comparison command shape is invalid")
    baseline = value["baseline_pump_check_id"]
    current = value["current_pump_check_id"]
    if (not is_valid_pump_check_id(baseline)
            or not is_valid_pump_check_id(current)):
        raise InvalidCommand("comparison source tokens are invalid")
    # The pair is directional: baseline and current are never sorted.
    return CreateCommand(baseline_token=baseline, current_token=current)


def _owned_source(user_id, token):
    row = PumpCheck.query.filter_by(
        user_id=user_id, public_id=token).first()
    if row is None:
        # Unknown, malformed, and cross-owner tokens are indistinguishable.
        raise PumpCheckNotFound()
    return row


def _source_quality(row):
    """Re-validate the stored PR1 analysis instead of trusting the column."""
    try:
        analysis = parse_source_analysis(json.dumps(row.analysis))
    except (InvalidAnalysis, TypeError, ValueError) as exc:
        raise ChecksNotComparable("source analysis is not usable") from exc
    return analysis.get("quality")


def _require(condition, detail):
    if not condition:
        raise ChecksNotComparable(detail)


def resolve_eligible_sources(user_id, command):
    """Finish every deterministic rule before any S3 read or provider call."""
    baseline = _owned_source(user_id, command.baseline_token)
    current = _owned_source(user_id, command.current_token)

    _require(baseline.id != current.id, "sources are the same Pump Check")
    _require(
        baseline.captured_at is not None and current.captured_at is not None,
        "sources lack canonical capture times")
    _require(
        baseline.captured_at < current.captured_at,
        "baseline must be captured before current")

    region = (baseline.body_region or "").strip()
    _require(bool(region), "sources lack a canonical body region")
    _require(
        region == (current.body_region or "").strip(),
        "sources compare different body regions")

    qualities = []
    for row in (baseline, current):
        _require(bool(row.valid), "source is not a valid Pump Check")
        _require(
            row.analysis_status == "completed",
            "source analysis is not terminal")
        _require(
            row.analysis_version == SOURCE_ANALYSIS_VERSION,
            "source analysis version is not canonical")
        _require(
            bool(row.image_key)
            and s3_helper.key_belongs_to_user(row.image_key, user_id),
            "source image is not a private owner object")
        qualities.append(_source_quality(row))

    for quality in qualities:
        _require(
            quality != BLOCKING_SOURCE_QUALITY,
            "source quality is insufficient")

    cap = (
        LIMITED_SOURCE_QUALITY
        if LIMITED_SOURCE_QUALITY in qualities
        else "comparable"
    )
    return EligibleSources(
        baseline=baseline, current=current, source_quality_cap=cap)


def _media_of(sources):
    return SourceMedia(
        baseline_key=sources.baseline.image_key,
        current_key=sources.current.image_key,
        body_region=sources.baseline.body_region,
        source_quality_cap=sources.source_quality_cap,
    )


def _log_outcome(event, *, status, comparability=None,
                 attempt=None, duration_ms=None):
    current_app.logger.info(
        "pump_check_comparison event=%s status=%s "
        "comparability=%s attempt=%s duration_ms=%s",
        event, status, comparability, attempt, duration_ms)


def _request_for_key(user_id, key):
    return PumpCheckComparisonRequest.query.filter_by(
        user_id=user_id, idempotency_key=key).first()


def _existing_pair(user_id, sources):
    return PumpCheckComparison.query.filter_by(
        user_id=user_id,
        baseline_pump_check_id=sources.baseline.id,
        current_pump_check_id=sources.current.id,
        analysis_version=ANALYSIS_VERSION,
    ).first()


def _create_or_get_pair(user_id, sources):
    existing = _existing_pair(user_id, sources)
    if existing is not None:
        return existing, False
    row = PumpCheckComparison(
        user_id=user_id,
        baseline_pump_check_id=sources.baseline.id,
        current_pump_check_id=sources.current.id,
        public_id=new_comparison_id(
            current_app.config["SECRET_KEY"], user_id),
        status="pending",
        analysis_version=ANALYSIS_VERSION,
        analysis_attempt=0,
    )
    db.session.add(row)
    try:
        db.session.commit()
        return row, True
    except IntegrityError:
        # A concurrent request won the pair. This is convergence, never a
        # provider failure.
        db.session.rollback()
        existing = _existing_pair(user_id, sources)
        if existing is None:
            raise
        return existing, False


def _attach_ledger(user_id, key, digest, comparison_id):
    row = PumpCheckComparisonRequest(
        user_id=user_id,
        idempotency_key=key,
        fingerprint=digest,
        comparison_id=comparison_id,
    )
    db.session.add(row)
    try:
        db.session.commit()
        return comparison_id
    except IntegrityError:
        db.session.rollback()
        existing = _request_for_key(user_id, key)
        if existing is None:
            raise
        if existing.fingerprint != digest:
            raise IdempotencyConflict()
        return existing.comparison_id


def _create_or_get_pair_and_request(user_id, key, digest, sources):
    row, created = _create_or_get_pair(user_id, sources)
    row_id = row.id
    attached_id = _attach_ledger(user_id, key, digest, row_id)
    return attached_id, created and attached_id == row_id


def create_or_replay(user_id, key, command):
    digest = fingerprint(
        command.baseline_token, command.current_token, ANALYSIS_VERSION)
    existing_request = _request_for_key(user_id, key)
    if existing_request is not None:
        if existing_request.fingerprint != digest:
            raise IdempotencyConflict()
        row_id, created, media = existing_request.comparison_id, False, None
    else:
        # Every deterministic rule finishes before a row or ledger entry exists.
        sources = resolve_eligible_sources(user_id, command)
        media = _media_of(sources)
        row_id, created = _create_or_get_pair_and_request(
            user_id, key, digest, sources)
    db.session.rollback()
    return _run_or_reuse_analysis(user_id, row_id, created, media)


def _terminal_state(user_id, row_id):
    state = db.session.query(
        PumpCheckComparison.status,
        PumpCheckComparison.analysis_failure_kind,
    ).filter_by(id=row_id, user_id=user_id).first()
    db.session.rollback()
    return state if state is not None else (None, None)


def _fresh(row_id):
    db.session.rollback()
    return db.session.get(PumpCheckComparison, row_id)


def _media_for_row(user_id, row_id):
    row = db.session.get(PumpCheckComparison, row_id)
    command = CreateCommand(
        baseline_token=row.baseline_pump_check.public_id,
        current_token=row.current_pump_check.public_id,
    )
    media = _media_of(resolve_eligible_sources(user_id, command))
    db.session.rollback()
    return media


def _run_or_reuse_analysis(user_id, row_id, created, media):
    status, _kind = _terminal_state(user_id, row_id)
    if status == "completed":
        return _fresh(row_id), created
    if media is None:
        media = _media_for_row(user_id, row_id)

    attempt = _claim_analysis(row_id, user_id)
    if attempt is None:
        status, kind = _terminal_state(user_id, row_id)
        if status == "failed" and kind not in RETRYABLE_FAILURES:
            if kind == "invalid_media":
                raise MediaNotComparable("canonical media is not comparable")
            raise ComparisonUnavailable("comparison could not be finalized")
        # Another worker holds an unexpired lease: return its representation.
        return _fresh(row_id), False

    started = datetime.utcnow()
    try:
        baseline_image = _load_image(media.baseline_key, user_id)
        current_image = _load_image(media.current_key, user_id)
        if not _owns_attempt(row_id, user_id, attempt):
            return _fresh(row_id), False
        comparability, analysis = analyze_images(
            baseline_image, current_image,
            {"body_region": media.body_region},
            media.source_quality_cap)
        if not _finalize_success(
                row_id, user_id, attempt, comparability, analysis):
            return _fresh(row_id), False
        _log_outcome(
            "analyzed", status="completed", comparability=comparability,
            attempt=attempt,
            duration_ms=_elapsed_ms(started))
        return _fresh(row_id), created
    except PersistenceFinalizationUncertain as exc:
        db.session.rollback()
        _log_outcome("failed", status="failed", attempt=attempt,
                     duration_ms=_elapsed_ms(started))
        raise ComparisonUnavailable(
            "comparison finalization unavailable") from exc
    except Exception as exc:
        db.session.rollback()
        kind = _failure_kind(exc)
        _finalize_failure(row_id, user_id, attempt, kind)
        _log_outcome("failed", status="failed", attempt=attempt,
                     duration_ms=_elapsed_ms(started))
        if kind == "invalid_media":
            raise MediaNotComparable(
                "canonical media is not comparable") from exc
        raise ComparisonUnavailable(
            "comparison is temporarily unavailable") from exc


def _elapsed_ms(started):
    return int((datetime.utcnow() - started).total_seconds() * 1000)


def _load_image(object_key, user_id):
    """Read the private original and normalize it in memory only."""
    raw = s3_helper.get_object_bytes(object_key, expected_user_id=user_id)
    return prepare_image_for_vision(
        raw, s3_helper.media_type_for_key(object_key))


def _failure_kind(error):
    if isinstance(error, ImagePreparationError):
        return "invalid_media"
    if isinstance(error, s3_helper.S3Error):
        return "storage"
    if isinstance(error, InvalidComparisonAnalysis):
        return "invalid_output"
    return "provider"


def _claim_analysis(row_id, user_id, now=None):
    now = now or datetime.utcnow()
    stale_before = now - timedelta(seconds=ANALYSIS_LEASE_SECONDS)
    eligible = (
        (PumpCheckComparison.status == "pending")
        | (
            (PumpCheckComparison.status == "failed")
            & PumpCheckComparison.analysis_failure_kind.in_(
                sorted(RETRYABLE_FAILURES))
        )
        | (
            (PumpCheckComparison.status == "analyzing")
            & (PumpCheckComparison.analysis_started_at < stale_before)
        )
    )
    claimed = PumpCheckComparison.query.filter(
        PumpCheckComparison.id == row_id,
        PumpCheckComparison.user_id == user_id,
        eligible,
    ).update({
        PumpCheckComparison.status: "analyzing",
        PumpCheckComparison.analysis_started_at: now,
        PumpCheckComparison.analysis_attempt:
            PumpCheckComparison.analysis_attempt + 1,
        PumpCheckComparison.analysis_failure_kind: None,
    }, synchronize_session=False)
    db.session.commit()
    if not claimed:
        return None
    attempt = db.session.query(
        PumpCheckComparison.analysis_attempt).filter_by(
            id=row_id, user_id=user_id).scalar()
    db.session.rollback()
    return attempt


def _owns_attempt(row_id, user_id, attempt):
    owner = db.session.query(PumpCheckComparison.id).filter_by(
        id=row_id,
        user_id=user_id,
        status="analyzing",
        analysis_attempt=attempt,
    ).scalar()
    db.session.rollback()
    return owner is not None


def _finalize_success(row_id, user_id, attempt, comparability, analysis):
    updated = PumpCheckComparison.query.filter_by(
        id=row_id,
        user_id=user_id,
        status="analyzing",
        analysis_attempt=attempt,
    ).update({
        PumpCheckComparison.analysis: analysis,
        PumpCheckComparison.comparability: comparability,
        PumpCheckComparison.status: "completed",
        PumpCheckComparison.analysis_started_at: None,
        PumpCheckComparison.analysis_failure_kind: None,
    }, synchronize_session=False)
    try:
        db.session.commit()
    except Exception as commit_error:
        db.session.rollback()
        try:
            completed = db.session.query(
                PumpCheckComparison.status).filter_by(
                    id=row_id, user_id=user_id,
                    analysis_attempt=attempt).scalar() == "completed"
        except Exception as reconcile_error:
            db.session.rollback()
            raise PersistenceFinalizationUncertain(
                "cannot reconcile comparison finalization") from reconcile_error
        db.session.rollback()
        if completed:
            return True
        try:
            _finalize_failure(row_id, user_id, attempt, "persistence")
        except Exception as reconcile_error:
            db.session.rollback()
            raise PersistenceFinalizationUncertain(
                "cannot persist terminal comparison failure"
            ) from reconcile_error
        raise PersistenceFinalizationUncertain(
            "comparison completion was not persisted") from commit_error
    return bool(updated)


def _finalize_failure(row_id, user_id, attempt, kind):
    PumpCheckComparison.query.filter_by(
        id=row_id,
        user_id=user_id,
        status="analyzing",
        analysis_attempt=attempt,
    ).update({
        # A JSON column turns a Python None into the JSON `null` VALUE, which
        # is not SQL NULL — the terminal-coherence check rejects it, as it
        # should: a failed comparison must carry no analysis at all.
        PumpCheckComparison.analysis: sa_null(),
        PumpCheckComparison.comparability: None,
        PumpCheckComparison.status: "failed",
        PumpCheckComparison.analysis_started_at: None,
        PumpCheckComparison.analysis_failure_kind: kind,
    }, synchronize_session=False)
    db.session.commit()


def get_owned(user_id, token):
    if not is_valid_comparison_id(token):
        raise ComparisonNotFound()
    row = PumpCheckComparison.query.filter_by(
        user_id=user_id, public_id=token).first()
    if row is None:
        raise ComparisonNotFound()
    return row


def _iso(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def serialize_comparison(row):
    return {
        "id": row.public_id,
        "baseline_pump_check_id": row.baseline_pump_check.public_id,
        "current_pump_check_id": row.current_pump_check.public_id,
        "status": row.status,
        "comparability": row.comparability,
        "analysis": row.analysis,
        "analysis_version": row.analysis_version,
        "created_at": _iso(row.created_at),
    }
