"""Canonical owner-scoped Pump Check history: ordering, paging, projection."""
from datetime import datetime

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import PumpCheck
from app.services.mobile_pump_checks import history
from app.services.mobile_pump_checks.history_cursor import InvalidCursor


SECRET = "history-service-secret"
BASE = datetime(2026, 8, 13, 5, 0, 0)


@pytest.fixture
def owner(make_user):
    return make_user("history-owner")


@pytest.fixture
def stranger(make_user):
    return make_user("history-stranger")


def _add(user, captured_at, token, **fields):
    fields.setdefault("body_region", "upper_body")
    fields.setdefault("analysis_status", "completed")
    row = PumpCheck(
        user_id=user.id, captured_at=captured_at, public_id=token, **fields)
    db.session.add(row)
    db.session.commit()
    return row


def _ids(page):
    return [item["id"] for item in page["pump_checks"]]


def _page(user, limit=None, cursor=None):
    return history.list_history(
        user.id, SECRET,
        limit=history.DEFAULT_PAGE_SIZE if limit is None else limit,
        cursor=cursor)


# ---------------------------------------------------------------------------
# Page size
# ---------------------------------------------------------------------------

def test_absent_page_size_falls_back_to_the_default():
    assert history.parse_page_size(None) == history.DEFAULT_PAGE_SIZE
    assert history.DEFAULT_PAGE_SIZE == 20


def test_a_valid_page_size_is_honoured():
    assert history.parse_page_size("7") == 7
    assert history.parse_page_size(str(history.MAX_PAGE_SIZE)) == 50


@pytest.mark.parametrize("value", [
    "0", "-1", "51", "abc", "", "1.5", "  ", "1e2", "+5", "20 ", "0x10",
    None.__class__, 20, 20.0, ["20"],
])
def test_an_unusable_page_size_is_rejected_rather_than_clamped(value):
    with pytest.raises(history.InvalidPageSize):
        history.parse_page_size(value)


@pytest.mark.parametrize("limit", [0, -1, 51, None, "20"])
def test_the_read_model_enforces_the_page_size_it_documents(app, owner, limit):
    """`list_history` is public, so it cannot trust its caller to have checked.

    An unchecked `limit=0` slices the probe row away and then reads `page[-1]`
    to mint the next cursor — an IndexError instead of an error response.
    """
    _add(owner, BASE, "history00000000000000001")

    with pytest.raises(history.InvalidPageSize):
        history.list_history(owner.id, SECRET, limit=limit)


@pytest.mark.parametrize("value", ["٣", "²", "⁵", "１"])
def test_non_ascii_digit_forms_are_rejected(value):
    """`str.isdigit()` accepts these; `int()` then crashes on some of them, so
    a permanently invalid query string would surface as a retryable 503."""
    with pytest.raises(history.InvalidPageSize):
        history.parse_page_size(value)


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

def test_history_is_ordered_by_capture_time_descending(app, owner):
    _add(owner, BASE.replace(day=11), "history00000000000000001")
    _add(owner, BASE.replace(day=13), "history00000000000000002")
    _add(owner, BASE.replace(day=12), "history00000000000000003")

    assert _ids(_page(owner)) == [
        "history00000000000000002",
        "history00000000000000003",
        "history00000000000000001",
    ]


def test_identical_capture_times_break_the_tie_deterministically(app, owner):
    first = _add(owner, BASE, "history00000000000000001")
    second = _add(owner, BASE, "history00000000000000002")
    third = _add(owner, BASE, "history00000000000000003")

    assert [row.id for row in (third, second, first)] == sorted(
        (first.id, second.id, third.id), reverse=True)
    assert _ids(_page(owner)) == [
        "history00000000000000003",
        "history00000000000000002",
        "history00000000000000001",
    ]


def test_capture_time_outranks_write_order(app, owner):
    """A check uploaded late but captured early sorts by when it was taken."""
    _add(owner, BASE.replace(day=13), "history00000000000000001")
    _add(owner, BASE.replace(day=10), "history00000000000000002")

    assert _ids(_page(owner))[0] == "history00000000000000001"


# ---------------------------------------------------------------------------
# Legacy rows
# ---------------------------------------------------------------------------

def test_legacy_rows_without_capture_time_are_excluded(app, owner):
    _add(owner, None, "history00000000000000001", body_region=None,
         analysis_status=None)
    _add(owner, BASE, "history00000000000000002")

    assert _ids(_page(owner)) == ["history00000000000000002"]


def test_a_history_of_only_legacy_rows_reads_as_empty(app, owner):
    _add(owner, None, "history00000000000000001", body_region=None)

    page = _page(owner)

    assert page["pump_checks"] == []
    assert page["has_more"] is False
    assert page["next_cursor"] is None


def test_legacy_rows_never_reappear_through_pagination(app, owner):
    for index in range(4):
        _add(owner, None, "historylegacy0000000000%d" % index, body_region=None)
        _add(owner, BASE.replace(minute=index), "history0000000000000000%d" % index)

    seen = []
    page = _page(owner, limit=1)
    while True:
        seen.extend(_ids(page))
        if not page["next_cursor"]:
            break
        page = _page(owner, limit=1, cursor=page["next_cursor"])

    assert len(seen) == 4
    assert not any(token.startswith("historylegacy") for token in seen)


# ---------------------------------------------------------------------------
# Owner scope
# ---------------------------------------------------------------------------

def test_history_contains_only_rows_owned_by_the_caller(app, owner, stranger):
    _add(owner, BASE, "history00000000000000001")
    _add(stranger, BASE.replace(day=14), "history00000000000000002")

    assert _ids(_page(owner)) == ["history00000000000000001"]
    assert _ids(_page(stranger)) == ["history00000000000000002"]


def test_feed_visibility_does_not_grant_history_visibility(app, owner, stranger):
    """Sharing to Feed changes who may SEE a check, never who OWNS it."""
    _add(owner, BASE, "history00000000000000001", visibility="feed")

    assert _ids(_page(stranger)) == []


def test_a_cursor_from_another_owner_cannot_widen_a_page(app, owner, stranger):
    _add(owner, BASE, "history00000000000000001")
    _add(owner, BASE.replace(day=14), "history00000000000000002")
    foreign = _page(owner, limit=1)["next_cursor"]
    assert foreign is not None

    with pytest.raises(InvalidCursor):
        _page(stranger, limit=1, cursor=foreign)


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def test_an_empty_history_is_an_empty_page_not_an_error(app, owner):
    page = _page(owner)

    assert page == {"pump_checks": [], "next_cursor": None, "has_more": False}


def test_a_full_walk_yields_every_row_once_and_in_order(app, owner):
    for index in range(7):
        _add(owner, BASE.replace(minute=index), "history0000000000000000%d" % index)

    seen = []
    page = _page(owner, limit=2)
    pages = 1
    while page["next_cursor"]:
        seen.extend(_ids(page))
        page = _page(owner, limit=2, cursor=page["next_cursor"])
        pages += 1
    seen.extend(_ids(page))

    assert seen == ["history0000000000000000%d" % i for i in range(6, -1, -1)]
    assert len(seen) == len(set(seen))
    assert pages == 4


def test_paging_through_identical_capture_times_skips_and_repeats_nothing(
        app, owner):
    for index in range(6):
        _add(owner, BASE, "history0000000000000000%d" % index)

    seen = []
    page = _page(owner, limit=2)
    while True:
        seen.extend(_ids(page))
        if not page["next_cursor"]:
            break
        page = _page(owner, limit=2, cursor=page["next_cursor"])

    assert len(seen) == 6
    assert len(set(seen)) == 6


def test_next_cursor_is_present_only_while_more_rows_remain(app, owner):
    _add(owner, BASE, "history00000000000000001")
    _add(owner, BASE.replace(day=14), "history00000000000000002")

    first = _page(owner, limit=1)
    assert first["has_more"] is True
    assert isinstance(first["next_cursor"], str)

    second = _page(owner, limit=1, cursor=first["next_cursor"])
    assert second["has_more"] is False
    assert second["next_cursor"] is None


def test_an_exactly_full_final_page_reports_the_end(app, owner):
    """limit == remaining must not promise a page that does not exist."""
    _add(owner, BASE, "history00000000000000001")
    _add(owner, BASE.replace(day=14), "history00000000000000002")

    page = _page(owner, limit=2)

    assert len(page["pump_checks"]) == 2
    assert page["has_more"] is False
    assert page["next_cursor"] is None


def test_repeating_the_first_page_is_stable(app, owner):
    for index in range(4):
        _add(owner, BASE.replace(minute=index), "history0000000000000000%d" % index)

    assert _ids(_page(owner, limit=2)) == _ids(_page(owner, limit=2))


def test_a_cursor_still_pages_after_its_row_is_deleted(app, owner):
    """Keyset seeks on VALUES, so a deleted anchor does not break the walk."""
    for index in range(3):
        _add(owner, BASE.replace(minute=index), "history0000000000000000%d" % index)
    first = _page(owner, limit=1)
    anchor = PumpCheck.query.filter_by(
        public_id=_ids(first)[0]).one()
    db.session.delete(anchor)
    db.session.commit()

    rest = _page(owner, limit=5, cursor=first["next_cursor"])

    assert _ids(rest) == [
        "history00000000000000001", "history00000000000000000"]


# ---------------------------------------------------------------------------
# Item projection
# ---------------------------------------------------------------------------

def test_a_history_item_publishes_exactly_the_lightweight_contract(app, owner):
    _add(owner, BASE, "history00000000000000001",
         analysis={"quality": "sufficient", "summary": "x" * 400},
         analysis_version="pump-check-analysis/v1",
         description="private note",
         location_type="gym",
         image_key="pump-checks/1/2026/08/private.jpg")

    item = _page(owner)["pump_checks"][0]

    assert item == {
        "id": "history00000000000000001",
        "captured_at": "2026-08-13T05:00:00Z",
        "body_region": "upper_body",
        "analysis_status": "completed",
        "analysis_quality": "sufficient",
    }


def test_a_row_without_an_analysis_status_reads_as_unavailable(app, owner):
    _add(owner, BASE, "history00000000000000001", analysis_status=None)

    assert _page(owner)["pump_checks"][0]["analysis_status"] == "unavailable"


@pytest.mark.parametrize("analysis,status,expected", [
    ({"quality": "sufficient"}, "completed", "sufficient"),
    ({"quality": "insufficient"}, "completed", "insufficient"),
    ({}, "completed", None),
    ({"quality": 7}, "completed", None),
    ("not-a-dict", "completed", None),
    (None, "completed", None),
    ({"quality": "sufficient"}, "pending", None),
    ({"quality": "sufficient"}, "failed", None),
])
def test_quality_is_published_only_from_a_completed_analysis(
        app, owner, analysis, status, expected):
    _add(owner, BASE, "history00000000000000001",
         analysis=analysis, analysis_status=status)

    assert _page(owner)["pump_checks"][0]["analysis_quality"] == expected


def test_capture_times_are_published_as_utc_zulu(app, owner):
    _add(owner, datetime(2026, 8, 13, 5, 0, 0, 123456),
         "history00000000000000001")

    assert _page(owner)["pump_checks"][0]["captured_at"] == (
        "2026-08-13T05:00:00.123456Z")


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------

def test_a_page_costs_exactly_one_pump_check_query(app, owner):
    for index in range(5):
        _add(owner, BASE.replace(minute=index), "history0000000000000000%d" % index)
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        page = _page(owner, limit=3)
    finally:
        event.remove(db.engine, "before_cursor_execute", record)

    assert len(page["pump_checks"]) == 3
    assert len([s for s in statements if "pump_check" in s]) == 1


def test_a_later_page_costs_exactly_one_pump_check_query(app, owner):
    for index in range(5):
        _add(owner, BASE.replace(minute=index), "history0000000000000000%d" % index)
    cursor = _page(owner, limit=3)["next_cursor"]
    statements = []

    def record(conn, cursor_, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        _page(owner, limit=3, cursor=cursor)
    finally:
        event.remove(db.engine, "before_cursor_execute", record)

    assert len([s for s in statements if "pump_check" in s]) == 1


def test_a_page_reads_no_more_rows_than_it_needs(app, owner):
    """The page probes limit+1, never the whole history."""
    for index in range(9):
        _add(owner, BASE.replace(minute=index), "history0000000000000000%d" % index)
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        _page(owner, limit=2)
    finally:
        event.remove(db.engine, "before_cursor_execute", record)

    reads = [s for s in statements if "pump_check" in s]
    assert "LIMIT" in reads[0].upper()


def test_history_never_writes(app, owner):
    _add(owner, BASE, "history00000000000000001")
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().upper())

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        _page(owner)
    finally:
        event.remove(db.engine, "before_cursor_execute", record)

    assert not any(
        s.startswith(("INSERT", "UPDATE", "DELETE")) for s in statements)
