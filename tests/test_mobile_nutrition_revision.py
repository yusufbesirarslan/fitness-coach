"""Pure contract tests for opaque mobile diary entry revisions."""
from dataclasses import replace
from datetime import datetime

import pytest

from app.services.mobile_nutrition.identity import diary_entry_id
from app.services.mobile_nutrition.revision import (
    DiaryEntryRevisionState,
    diary_entry_revision,
    matches_diary_entry_revision,
)


SECRET = "revision-test-secret"


@pytest.fixture
def state():
    return DiaryEntryRevisionState(
        user_id=7,
        entry_id=19,
        meal_label="KahvaltÄ±",
        description="Yulaf (50 g)",
        energy_kcal=320.0,
        protein_g=12.0,
        carbohydrate_g=40.0,
        fat_g=9.0,
        diary_date="2026-08-11",
        source="manual",
        idempotency_key="log-food-key-0001",
        idempotency_fingerprint="a" * 64,
        photo_key="meals/photo.jpg",
        created_at=datetime(2026, 8, 11, 5, 24, 1, 123456),
    )


def test_revision_is_deterministic_opaque_and_separate_from_entry_identity(state):
    first = diary_entry_revision(SECRET, state)
    second = diary_entry_revision(SECRET, state)

    assert first == second
    assert len(first) == 24
    assert first.isascii() and all(c.isalnum() or c in "-_" for c in first)
    assert first != diary_entry_id(SECRET, state.user_id, state.entry_id)
    assert str(state.entry_id) not in first


@pytest.mark.parametrize(("field", "changed"), [
    ("user_id", 8),
    ("entry_id", 20),
    ("meal_label", "Ã–ÄŸle"),
    ("description", "Yulaf (60 g)"),
    ("energy_kcal", 321.0),
    ("protein_g", 13.0),
    ("carbohydrate_g", 41.0),
    ("fat_g", 10.0),
    ("diary_date", "2026-08-12"),
    ("source", "barcode"),
    ("idempotency_key", "log-food-key-0002"),
    ("idempotency_fingerprint", "b" * 64),
    ("photo_key", "meals/other.jpg"),
    ("created_at", datetime(2026, 8, 11, 5, 24, 1, 123457)),
])
def test_every_material_row_field_changes_the_revision(state, field, changed):
    assert diary_entry_revision(SECRET, replace(state, **{field: changed})) != (
        diary_entry_revision(SECRET, state)
    )


@pytest.mark.parametrize("field", [
    "meal_label", "description", "energy_kcal", "protein_g",
    "carbohydrate_g", "fat_g", "diary_date", "source",
    "idempotency_key", "idempotency_fingerprint", "photo_key", "created_at",
])
def test_null_is_canonical_and_distinct_from_a_present_value(state, field):
    nullable = replace(state, **{field: None})
    assert diary_entry_revision(SECRET, nullable) == diary_entry_revision(
        SECRET, nullable)
    assert diary_entry_revision(SECRET, nullable) != diary_entry_revision(
        SECRET, state)


@pytest.mark.parametrize("field", [
    "energy_kcal", "protein_g", "carbohydrate_g", "fat_g",
])
def test_negative_zero_is_the_same_semantic_number_as_zero(state, field):
    assert diary_entry_revision(
        SECRET, replace(state, **{field: -0.0})
    ) == diary_entry_revision(SECRET, replace(state, **{field: 0.0}))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_historical_non_finite_float_values_encode_stably(state, value):
    unusual = replace(state, energy_kcal=value)
    assert diary_entry_revision(SECRET, unusual) == diary_entry_revision(
        SECRET, unusual)


def test_revision_match_is_constant_contract_and_rejects_bad_tokens(state):
    token = diary_entry_revision(SECRET, state)

    assert matches_diary_entry_revision(SECRET, state, token)
    assert not matches_diary_entry_revision(
        SECRET, replace(state, meal_label="Ã–ÄŸle"), token)
    assert not matches_diary_entry_revision(SECRET, state, "")
    assert not matches_diary_entry_revision(SECRET, state, None)
