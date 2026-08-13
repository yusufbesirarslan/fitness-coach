import re

from app.services.mobile_pump_checks.identity import (
    is_valid_pump_check_id,
    new_pump_check_id,
)


def test_pump_check_id_is_stable_for_fixed_entropy_opaque_and_url_safe():
    token = new_pump_check_id("secret", 12, nonce=b"x" * 32)
    assert token == new_pump_check_id("secret", 12, nonce=b"x" * 32)
    assert len(token) == 24
    assert re.fullmatch(r"[A-Za-z0-9_-]{24}", token)
    assert "12" not in token


def test_pump_check_id_is_owner_and_random_nonce_bound():
    token = new_pump_check_id("secret", 12, nonce=b"x" * 32)
    assert token != new_pump_check_id("secret", 13, nonce=b"x" * 32)
    assert token != new_pump_check_id("secret", 12, nonce=b"y" * 32)


def test_pump_check_id_uses_a_distinct_domain_from_diary_identity():
    from app.services.mobile_nutrition.identity import diary_entry_id
    assert new_pump_check_id(
        "secret", 12, nonce=b"x" * 32) != diary_entry_id("secret", 12, 34)


def test_pump_check_id_validation_rejects_non_tokens():
    assert is_valid_pump_check_id(
        new_pump_check_id("secret", 12, nonce=b"x" * 32)) is True
    assert is_valid_pump_check_id("") is False
    assert is_valid_pump_check_id(None) is False
    assert is_valid_pump_check_id("not/a/token") is False
