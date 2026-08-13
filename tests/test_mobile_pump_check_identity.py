import re

from app.services.mobile_pump_checks.identity import (
    matches_pump_check_id,
    pump_check_id,
    resolve_pump_check_row_id,
)


def test_pump_check_id_is_stable_opaque_and_url_safe():
    token = pump_check_id("secret", 12, 34)
    assert token == pump_check_id("secret", 12, 34)
    assert len(token) == 24
    assert re.fullmatch(r"[A-Za-z0-9_-]{24}", token)
    assert "12" not in token
    assert "34" not in token


def test_pump_check_id_is_owner_and_row_bound():
    token = pump_check_id("secret", 12, 34)
    assert matches_pump_check_id("secret", 12, 34, token) is True
    assert matches_pump_check_id("secret", 13, 34, token) is False
    assert matches_pump_check_id("secret", 12, 35, token) is False
    assert resolve_pump_check_row_id("secret", 12, token) == 34
    assert resolve_pump_check_row_id("secret", 13, token) is None


def test_pump_check_id_uses_a_distinct_domain_from_diary_identity():
    from app.services.mobile_nutrition.identity import diary_entry_id
    assert pump_check_id("secret", 12, 34) != diary_entry_id("secret", 12, 34)


def test_pump_check_id_match_rejects_non_tokens():
    assert matches_pump_check_id("secret", 12, 34, "") is False
    assert matches_pump_check_id("secret", 12, 34, None) is False
    assert matches_pump_check_id("secret", 12, 34, "not/a/token") is False
