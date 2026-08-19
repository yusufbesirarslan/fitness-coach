"""Owner-scoped bounded reads for Physique Progress.

This is the only impure module in the package. Every query carries the
authenticated owner id as a filter — there is no ``user_id``-free path and no
cross-owner join. Reads are aggregates or ``LIMIT``-bounded; they never
materialise a user's full Pump Check history in Python.

This module performs no write, no provider call, no image decode, and no
comparison creation. Image signing happens only after the query has already
proved ownership, and only for the unique keys the section will render.
"""
from datetime import timezone

from sqlalchemy import func
from sqlalchemy.orm import aliased

import s3_helper
from app.extensions import db
from app.models import PumpCheck, PumpCheckComparison
from app.services.progress_physique.models import (
    IMAGE_URL_EXPIRES_IN,
    RECENT_CHECK_LIMIT,
    PhysiqueCheck,
    PhysiqueRegion,
)

TERMINAL_ANALYSIS_STATUS = "completed"
UNKNOWN_ANALYSIS_STATUS = "unavailable"
_COMPLETED_COMPARISON = "completed"


def _iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _quality(analysis, analysis_status):
    """Publish quality only from a terminal analysis, and only as a string.

    Allowed values are enforced once, at write time, by the canonical parser.
    This read model does not restate that set: importing the parser would pull
    the vision adapter into a Progress package, and restating it would create
    a second authority that could drift. Same stance as
    ``mobile_pump_checks.history``.
    """
    if analysis_status != TERMINAL_ANALYSIS_STATUS:
        return None
    if not isinstance(analysis, dict):
        return None
    quality = analysis.get("quality")
    return quality if isinstance(quality, str) else None


def list_canonical_regions(user_id):
    """Body areas that have at least one canonical (``captured_at``-set) check.

    One aggregate query. The result is at most the canonical region vocabulary
    (currently six values), ordered by most recent capture then region name.
    """
    rows = (
        db.session.query(
            PumpCheck.body_region,
            func.count(PumpCheck.id),
            func.max(PumpCheck.captured_at),
        )
        .filter(
            PumpCheck.user_id == user_id,
            PumpCheck.captured_at.isnot(None),
            PumpCheck.body_region.isnot(None),
        )
        .group_by(PumpCheck.body_region)
        .all()
    )
    regions = [
        PhysiqueRegion(
            body_region=body_region,
            check_count=int(count),
            latest_captured_at=latest,
        )
        for body_region, count, latest in rows
        if isinstance(body_region, str) and body_region and latest is not None
    ]
    regions.sort(
        key=lambda item: (-item.latest_captured_at.timestamp(), item.body_region)
    )
    return tuple(regions)


def has_legacy_gallery_rows(user_id):
    """True when the owner has at least one pre-canonical Pump Check.

    Legacy means ``captured_at IS NULL``. Those rows stay in the gallery; they
    are never promoted into this read model. One ``EXISTS``-style query.
    """
    exists = (
        db.session.query(PumpCheck.id)
        .filter(
            PumpCheck.user_id == user_id,
            PumpCheck.captured_at.is_(None),
        )
        .limit(1)
        .first()
    )
    return exists is not None


def list_recent_canonical_checks(user_id, body_region, limit=RECENT_CHECK_LIMIT):
    """Latest ``limit`` canonical checks in ``body_region``, newest first.

    Chronology is ``captured_at DESC, id DESC`` — the same order canonical
    history uses. ``created_at`` is never consulted.
    """
    rows = (
        db.session.query(
            PumpCheck.public_id,
            PumpCheck.captured_at,
            PumpCheck.body_region,
            PumpCheck.analysis_status,
            PumpCheck.analysis,
            PumpCheck.image_key,
        )
        .filter(
            PumpCheck.user_id == user_id,
            PumpCheck.captured_at.isnot(None),
            PumpCheck.body_region == body_region,
            PumpCheck.public_id.isnot(None),
        )
        .order_by(PumpCheck.captured_at.desc(), PumpCheck.id.desc())
        .limit(limit)
        .all()
    )
    return tuple(rows)


def load_latest_completed_comparison(user_id, body_region):
    """Newest completed owner comparison whose sources share ``body_region``.

    "Newest" means the comparison whose *current* source has the latest
    ``captured_at``, then ``PumpCheckComparison.id DESC``. Chronology is the
    authority — not comparability, not analysis prose, not "nicest" result.

    Both sources must themselves be canonical (non-null ``captured_at``,
    opaque ``public_id``) and owned by the same user. Pending/failed
    comparisons are invisible here: this package never retries them.
    """
    current = aliased(PumpCheck, name="pp_current")
    baseline = aliased(PumpCheck, name="pp_baseline")
    return (
        db.session.query(
            PumpCheckComparison.public_id,
            PumpCheckComparison.comparability,
            PumpCheckComparison.analysis,
            baseline.public_id.label("baseline_public_id"),
            current.public_id.label("current_public_id"),
            baseline.captured_at.label("baseline_captured_at"),
            current.captured_at.label("current_captured_at"),
            baseline.image_key.label("baseline_image_key"),
            current.image_key.label("current_image_key"),
        )
        .join(current, PumpCheckComparison.current_pump_check_id == current.id)
        .join(baseline, PumpCheckComparison.baseline_pump_check_id == baseline.id)
        .filter(
            PumpCheckComparison.user_id == user_id,
            PumpCheckComparison.status == _COMPLETED_COMPARISON,
            current.user_id == user_id,
            baseline.user_id == user_id,
            current.body_region == body_region,
            baseline.body_region == body_region,
            current.captured_at.isnot(None),
            baseline.captured_at.isnot(None),
            current.public_id.isnot(None),
            baseline.public_id.isnot(None),
        )
        .order_by(current.captured_at.desc(), PumpCheckComparison.id.desc())
        .limit(1)
        .first()
    )


def sign_owned_image(user_id, image_key):
    """Mint one short-lived private URL after ownership is already proven.

    The calling query filtered ``user_id``. Signing still passes
    ``expected_user_id`` so a key that does not belong to that owner is
    refused by the existing S3 helper — no second presigner.
    """
    if not image_key:
        return None
    if not s3_helper.key_belongs_to_user(image_key, user_id):
        return None
    if not s3_helper.is_enabled():
        return None
    return s3_helper.generate_presigned_url(
        image_key,
        expires_in=IMAGE_URL_EXPIRES_IN,
        expected_user_id=user_id,
    )


def project_check(row, image_url):
    return PhysiqueCheck(
        public_id=row.public_id,
        captured_at=row.captured_at,
        body_region=row.body_region,
        analysis_status=row.analysis_status or UNKNOWN_ANALYSIS_STATUS,
        analysis_quality=_quality(row.analysis, row.analysis_status),
        image_url=image_url,
    )


__all__ = [
    "has_legacy_gallery_rows",
    "list_canonical_regions",
    "list_recent_canonical_checks",
    "load_latest_completed_comparison",
    "project_check",
    "sign_owned_image",
    "_iso",
]
