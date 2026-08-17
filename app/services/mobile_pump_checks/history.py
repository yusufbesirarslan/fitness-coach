"""Canonical owner-private Pump Check history — a pure read model.

The authority is the `PumpCheck` row owned by the authenticated user. History is
never derived from Feed, from a comparison, from the comparison request ledger,
from logs or from a cached session: Feed decides who may see a check socially and
never who owns it.

This module reads. It performs no write, no provider call, no image signing and
no image read, and it creates no comparison. One bounded query serves one page.
"""
from datetime import timezone

from sqlalchemy import and_, or_

from app.extensions import db
from app.models import PumpCheck
from app.services.mobile_pump_checks.history_cursor import (
    InvalidCursor,
    decode_cursor,
    encode_cursor,
)


DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50
TERMINAL_ANALYSIS_STATUS = "completed"
UNKNOWN_ANALYSIS_STATUS = "unavailable"


class InvalidPageSize(ValueError):
    """Raised for a page size this endpoint will not serve."""


def parse_page_size(value):
    """Reject an unusable page size instead of quietly clamping it.

    A clamped limit lets a client ask for 500, receive 50 and conclude the
    history holds 50 rows.
    """
    if value is None:
        return DEFAULT_PAGE_SIZE
    # ASCII digits only. `str.isdigit()` alone also accepts other Unicode digit
    # forms, and for some of them (superscripts) `int()` then raises — turning a
    # permanently invalid query string into an unhandled 500.
    if not isinstance(value, str) or not (value.isascii() and value.isdigit()):
        raise InvalidPageSize("page size must be a positive integer")
    size = int(value)
    if size < 1 or size > MAX_PAGE_SIZE:
        raise InvalidPageSize("page size is out of range")
    return size


def _seek(captured_at, row_id):
    """`(captured_at, id) < (cursor)` under `captured_at DESC, id DESC`."""
    return or_(
        PumpCheck.captured_at < captured_at,
        and_(PumpCheck.captured_at == captured_at, PumpCheck.id < row_id),
    )


def _query(user_id, cursor_position):
    query = db.session.query(
        PumpCheck.public_id,
        PumpCheck.captured_at,
        PumpCheck.body_region,
        PumpCheck.analysis_status,
        PumpCheck.analysis,
        PumpCheck.id,
    ).filter(
        PumpCheck.user_id == user_id,
        PumpCheck.captured_at.isnot(None),
    )
    if cursor_position is not None:
        query = query.filter(_seek(*cursor_position))
    return query.order_by(PumpCheck.captured_at.desc(), PumpCheck.id.desc())


def _iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _quality(analysis, analysis_status):
    """Publish quality only from a terminal analysis, and only as a string."""
    if analysis_status != TERMINAL_ANALYSIS_STATUS:
        return None
    if not isinstance(analysis, dict):
        return None
    quality = analysis.get("quality")
    return quality if isinstance(quality, str) else None


def _item(row):
    return {
        "id": row.public_id,
        "captured_at": _iso(row.captured_at),
        "body_region": row.body_region,
        "analysis_status": row.analysis_status or UNKNOWN_ANALYSIS_STATUS,
        "analysis_quality": _quality(row.analysis, row.analysis_status),
    }


def list_history(user_id, secret, limit=DEFAULT_PAGE_SIZE, cursor=None):
    """Return one owner-scoped page of canonical Pump Check history.

    Ownership comes from `user_id`, never from the cursor. `cursor` is the raw
    client string; an unusable one raises `InvalidCursor`.
    """
    position = None
    if cursor is not None:
        position = decode_cursor(secret, user_id, cursor)

    # One extra row answers "is there another page?" without a COUNT(*).
    rows = _query(user_id, position).limit(limit + 1).all()
    has_more = len(rows) > limit
    page = rows[:limit]

    next_cursor = None
    if has_more:
        last = page[-1]
        next_cursor = encode_cursor(secret, user_id, last.captured_at, last.id)
    return {
        "pump_checks": [_item(row) for row in page],
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "InvalidCursor",
    "InvalidPageSize",
    "list_history",
    "parse_page_size",
]
