"""The history cursor is opaque, owner-bound, versioned and fail-safe."""
import base64
from datetime import datetime

import pytest

from app.services.mobile_pump_checks import history_cursor


SECRET = "cursor-signing-secret"
WHEN = datetime(2026, 8, 13, 5, 0, 0, 123456)


def _encode(user_id=7, when=WHEN, row_id=42):
    return history_cursor.encode_cursor(SECRET, user_id, when, row_id)


def test_round_trip_returns_the_exact_position():
    assert history_cursor.decode_cursor(SECRET, 7, _encode()) == (WHEN, 42)


def test_the_cursor_never_exposes_the_position_it_carries():
    """Opaque: neither the capture time nor the internal row id is readable."""
    raw = base64.urlsafe_b64decode(_encode(row_id=987654).encode("ascii"))

    assert b"2026-08-13" not in raw
    assert b"987654" not in raw
    assert b"v1" not in raw


def test_two_cursors_for_the_same_position_are_not_byte_identical():
    """Encryption is randomised, so the token is not a stable row fingerprint."""
    assert _encode() != _encode()


def test_cursor_minted_for_one_owner_is_rejected_for_another():
    """Ownership never travels in the cursor, so a foreign cursor cannot bind."""
    with pytest.raises(history_cursor.InvalidCursor):
        history_cursor.decode_cursor(SECRET, 8, _encode(user_id=7))


def test_a_tampered_cursor_is_rejected():
    cursor = _encode()
    middle = len(cursor) // 2
    swapped = "A" if cursor[middle] != "A" else "B"

    with pytest.raises(history_cursor.InvalidCursor):
        history_cursor.decode_cursor(
            SECRET, 7, cursor[:middle] + swapped + cursor[middle + 1:])


def test_a_cursor_sealed_with_another_secret_is_rejected():
    with pytest.raises(history_cursor.InvalidCursor):
        history_cursor.decode_cursor("a-different-secret", 7, _encode())


@pytest.mark.parametrize("payload", [
    b"v2|7|2026-08-13T05:00:00.123456|42",
    b"v1|7|2026-08-13T05:00:00.123456",
    b"v1|7|2026-08-13T05:00:00.123456|42|extra",
    b"v1|7|not-a-timestamp|42",
    b"v1|7|2026-08-13T05:00:00.123456|not-an-integer",
    b"v1|7|2026-08-13T05:00:00.123456|4.2",
    b"v1|not-an-integer|2026-08-13T05:00:00.123456|42",
    b"v1|7||42",
    b"v1|7|2026-08-13T05:00:00.123456|",
    b"",
    b"\xff\xfe not utf-8",
])
def test_structurally_invalid_but_authentically_sealed_payloads_are_rejected(
        payload):
    """Even a payload this server sealed is re-validated, never trusted."""
    cursor = history_cursor._seal(SECRET, payload)

    with pytest.raises(history_cursor.InvalidCursor):
        history_cursor.decode_cursor(SECRET, 7, cursor)


@pytest.mark.parametrize("cursor", [
    "",
    "not-a-cursor",
    "!!!not-base64!!!",
    "çöp",
    None,
    42,
    b"bytes-are-not-a-cursor",
    [],
])
def test_malformed_cursors_raise_the_one_invalid_cursor_error(cursor):
    with pytest.raises(history_cursor.InvalidCursor):
        history_cursor.decode_cursor(SECRET, 7, cursor)


def test_an_excessively_long_cursor_is_rejected_before_decryption():
    with pytest.raises(history_cursor.InvalidCursor):
        history_cursor.decode_cursor(SECRET, 7, "a" * 5000)


def test_the_length_cap_is_bounded_well_above_a_real_cursor():
    assert len(_encode()) < history_cursor.MAX_CURSOR_LENGTH <= 512


def test_microsecond_precision_survives_the_round_trip():
    """A truncated timestamp would make two adjacent rows collide on the seek."""
    precise = datetime(2026, 8, 13, 5, 0, 0, 1)

    assert history_cursor.decode_cursor(
        SECRET, 7, _encode(when=precise)) == (precise, 42)


def test_a_whole_second_capture_time_keeps_its_microsecond_field():
    exact = datetime(2026, 8, 13, 5, 0, 0)

    assert history_cursor.decode_cursor(
        SECRET, 7, _encode(when=exact)) == (exact, 42)
