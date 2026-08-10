"""Semantic idempotency identity for the canonical mobile LogFood command."""
from dataclasses import replace
from decimal import Decimal
import re

from app.services.mobile_log_food import (
    ManualLogFoodCommand,
    ManualNutritionSnapshot,
    ProviderBackedLogFoodCommand,
    semantic_fingerprint,
)


def provider_command(**changes):
    command = ProviderBackedLogFoodCommand(
        provider="fatsecret",
        food_id="33691",
        serving_id="5501",
        quantity=Decimal("1"),
        slot="kahvalti",
        discovery_source="search",
    )
    return replace(command, **changes)


def manual_command(**changes):
    command = ManualLogFoodCommand(
        description="Ev yapımı yemek",
        slot="ogle",
        nutrition=ManualNutritionSnapshot(
            energy_kcal=Decimal("420"),
            protein_g=Decimal("25"),
            carbohydrate_g=Decimal("40"),
            fat_g=Decimal("14"),
        ),
    )
    return replace(command, **changes)


def test_fingerprint_is_a_sha256_hex_digest():
    assert re.fullmatch(r"[0-9a-f]{64}", semantic_fingerprint(provider_command()))


def test_decimal_formatting_does_not_change_provider_semantics():
    whole = semantic_fingerprint(provider_command(quantity=Decimal("1")))
    decimal = semantic_fingerprint(provider_command(quantity=Decimal("1.0")))

    assert whole == decimal


def test_decimal_formatting_does_not_change_manual_semantics():
    whole = semantic_fingerprint(manual_command())
    alternate = semantic_fingerprint(manual_command(
        nutrition=ManualNutritionSnapshot(
            energy_kcal=Decimal("420.00"),
            protein_g=Decimal("25.0"),
            carbohydrate_g=Decimal("40.000"),
            fat_g=Decimal("14.00"),
        )))

    assert whole == alternate


def test_every_provider_semantic_dimension_changes_the_fingerprint():
    baseline = semantic_fingerprint(provider_command())

    variants = [
        provider_command(food_id="different-food"),
        provider_command(serving_id="different-serving"),
        provider_command(quantity=Decimal("2")),
        provider_command(slot="ogle"),
        provider_command(discovery_source="barcode"),
    ]

    assert all(semantic_fingerprint(command) != baseline for command in variants)


def test_every_manual_semantic_dimension_changes_the_fingerprint():
    baseline = semantic_fingerprint(manual_command())

    variants = [
        manual_command(description="Ev yapımı başka yemek"),
        manual_command(slot="aksam"),
        manual_command(nutrition=replace(
            manual_command().nutrition, energy_kcal=Decimal("421"))),
        manual_command(nutrition=replace(
            manual_command().nutrition, protein_g=Decimal("26"))),
        manual_command(nutrition=replace(
            manual_command().nutrition, carbohydrate_g=Decimal("41"))),
        manual_command(nutrition=replace(
            manual_command().nutrition, fat_g=Decimal("15"))),
    ]

    assert all(semantic_fingerprint(command) != baseline for command in variants)


def test_provider_and_manual_commands_have_distinct_domain_identity():
    assert semantic_fingerprint(provider_command()) != semantic_fingerprint(
        manual_command())
