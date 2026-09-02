"""Strict native Training plan-generation request contract."""
import pytest

from app.services.mobile_training_generation import (
    InvalidIdempotencyKey,
    InvalidPlanRequest,
    parse_idempotency_key,
    parse_native_request,
)
from app.services.training_generation.preference_contract import (
    CODE_CONFLICTING,
    CODE_UNSUPPORTED,
    PreferenceContractError,
)


CANONICAL = {
    "gun_sayisi": 3,
    "ekipman": "spor_salonu",
    "odak": "tum_vucut",
    "sure": 45,
    "kardiyo_tipi": "yok",
    "kardiyo_gun": 0,
    "kardiyo_sure": 20,
    "kardiyo_yogunluk": "orta",
    "antrenman_tarzi": "genel",
    "odak_hedef": "genel",
    "injuries": "",
}


def test_native_request_accepts_the_exact_canonical_field_set():
    parsed = parse_native_request(CANONICAL)

    assert parsed.preferences.gun_sayisi == 3
    assert parsed.preferences.ekipman == "spor_salonu"
    assert parsed.normalized == CANONICAL
    assert len(parsed.fingerprint) == 64
    int(parsed.fingerprint, 16)


@pytest.mark.parametrize(
    "payload",
    [
        {**CANONICAL, "legacy_goal": "bulk"},
        {key: value for key, value in CANONICAL.items() if key != "sure"},
        None,
        [],
        "not-an-object",
    ],
)
def test_native_request_rejects_unknown_missing_and_non_object_payloads(payload):
    with pytest.raises(InvalidPlanRequest):
        parse_native_request(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("gun_sayisi", "3"),
        ("gun_sayisi", True),
        ("sure", 31),
        ("ekipman", "hotel"),
        ("kardiyo_tipi", 1),
        ("injuries", ["knee"]),
        ("injuries", "x" * 2001),
        ("injuries", "knee\x00pain"),
    ],
)
def test_native_request_rejects_wrong_types_values_and_bounds(field, value):
    with pytest.raises(InvalidPlanRequest):
        parse_native_request({**CANONICAL, field: value})


def test_native_request_trims_bounded_injuries_without_stored_fallback():
    parsed = parse_native_request({**CANONICAL, "injuries": "  knee pain  "})
    empty = parse_native_request(CANONICAL)

    assert parsed.preferences.injuries == "knee pain"
    assert parsed.normalized["injuries"] == "knee pain"
    assert empty.preferences.injuries == ""


def test_native_request_preserves_canonical_capability_failures():
    with pytest.raises(PreferenceContractError) as unsupported:
        parse_native_request({**CANONICAL, "antrenman_tarzi": "crossfit"})
    with pytest.raises(PreferenceContractError) as conflicting:
        parse_native_request({**CANONICAL, "kardiyo_gun": 1})

    assert unsupported.value.public_code == CODE_UNSUPPORTED
    assert conflicting.value.public_code == CODE_CONFLICTING


def test_fingerprint_is_order_stable_and_semantically_sensitive():
    first = parse_native_request(CANONICAL).fingerprint
    reordered = parse_native_request(
        dict(reversed(list(CANONICAL.items())))).fingerprint
    changed = parse_native_request({**CANONICAL, "sure": 60}).fingerprint

    assert first == reordered
    assert first != changed


def test_fingerprint_uses_normalized_semantics_not_formatting():
    plain = parse_native_request({**CANONICAL, "injuries": "knee pain"})
    padded = parse_native_request({**CANONICAL, "injuries": "  knee pain  "})

    assert plain.fingerprint == padded.fingerprint


@pytest.mark.parametrize(
    "key",
    [
        "12345678",
        "mobile.plan:key-01",
        "A" * 64,
    ],
)
def test_idempotency_key_accepts_the_repository_bounded_token_shape(key):
    assert parse_idempotency_key(key) == key


@pytest.mark.parametrize(
    "key",
    [
        None,
        12345678,
        "",
        "short",
        "contains space",
        "unsafe/token",
        "A" * 65,
    ],
)
def test_idempotency_key_rejects_missing_malformed_and_oversized_values(key):
    with pytest.raises(InvalidIdempotencyKey):
        parse_idempotency_key(key)
