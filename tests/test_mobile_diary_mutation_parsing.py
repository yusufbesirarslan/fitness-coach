"""Strict transport parsing for mobile diary mutation commands."""
import pytest

from app.services.mobile_diary_mutation import (
    InvalidDiaryMutation,
    InvalidPrecondition,
    MissingPrecondition,
    SetSlotCommand,
    parse_if_match,
    parse_mutation_command,
)


def test_set_slot_is_an_absolute_typed_command():
    assert parse_mutation_command({
        "operation": "set_slot", "slot": "ogle",
    }) == SetSlotCommand(slot="ogle")


@pytest.mark.parametrize("slot", ["kahvalti", "ogle", "aksam", "ara_ogun"])
def test_every_canonical_slot_is_accepted(slot):
    assert parse_mutation_command({
        "operation": "set_slot", "slot": slot,
    }).slot == slot


@pytest.mark.parametrize("payload", [
    None,
    [],
    {},
    {"operation": "set_slot"},
    {"slot": "ogle"},
    {"operation": "move_next", "slot": "ogle"},
    {"operation": "set_slot", "slot": "lunch"},
    {"operation": "set_slot", "slot": "OGLe"},
    {"operation": "set_slot", "slot": "ogle", "revision": "token"},
])
def test_malformed_commands_are_rejected(payload):
    with pytest.raises(InvalidDiaryMutation):
        parse_mutation_command(payload)


@pytest.mark.parametrize("field", [
    "description", "nutrition", "quantity", "serving", "serving_id",
    "provider", "food_id", "user_id", "day", "timezone", "timestamp",
    "logged_at", "source", "fingerprint", "idempotency_key", "id",
])
def test_protected_and_unsupported_fields_are_never_ignored(field):
    payload = {"operation": "set_slot", "slot": "ogle", field: "forbidden"}
    with pytest.raises(InvalidDiaryMutation):
        parse_mutation_command(payload)


def test_if_match_accepts_exactly_one_strong_quoted_opaque_revision():
    assert parse_if_match('"Abcd_efgh-1234567890XY"') == (
        "Abcd_efgh-1234567890XY")


def test_missing_if_match_is_distinct_from_malformed():
    with pytest.raises(MissingPrecondition):
        parse_if_match(None)


@pytest.mark.parametrize("value", [
    "",
    "revision-without-quotes",
    'W/"Abcd_efgh-1234567890XY"',
    "*",
    '"one", "two"',
    '""',
    '"has space inside"',
    '"contains.period"',
    '"short"',
    '"' + "x" * 129 + '"',
])
def test_malformed_if_match_is_rejected(value):
    with pytest.raises(InvalidPrecondition):
        parse_if_match(value)
