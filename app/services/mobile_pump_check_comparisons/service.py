"""Owner-scoped eligibility, convergence, and serialization for comparisons."""
import json
from dataclasses import dataclass
from datetime import datetime, timezone

import s3_helper
from app.models import PumpCheck, PumpCheckComparison
from app.services.mobile_pump_check_comparisons.analysis import (
    ANALYSIS_VERSION,
    analyze_images,
)
from app.services.mobile_pump_check_comparisons.identity import (
    is_valid_comparison_id,
)
from app.services.mobile_pump_checks.analysis import (
    ANALYSIS_VERSION as SOURCE_ANALYSIS_VERSION,
    InvalidAnalysis,
    parse_analysis as parse_source_analysis,
)
from app.services.mobile_pump_checks.identity import is_valid_pump_check_id


COMMAND_FIELDS = frozenset({
    "baseline_pump_check_id", "current_pump_check_id",
})
BLOCKING_SOURCE_QUALITY = "insufficient"
LIMITED_SOURCE_QUALITY = "limited"


class InvalidCommand(ValueError):
    """The create command does not match the exact public contract."""


class PumpCheckNotFound(Exception):
    """A source Pump Check is unknown, malformed, or owned by someone else."""


class ComparisonNotFound(Exception):
    """A comparison is unknown, malformed, or owned by someone else."""


class ChecksNotComparable(Exception):
    """The pair fails a deterministic eligibility rule."""


@dataclass(frozen=True)
class CreateCommand:
    baseline_token: str
    current_token: str


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
