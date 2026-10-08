"""Closed local confirmation adapter over the LP15-C proof authority."""
from decimal import Decimal

from app.services import mobile_menu
from .commands import MenuConfirmedLogFoodCommand, ManualNutritionSnapshot
from .parsing import InvalidLogFoodCommand, parse_provider_quantity
from .fingerprint import _canonical_number


def parse_menu_confirmation(data, secret, user_id):
    if (not isinstance(data, dict)
            or set(data) != {"confirmation_token", "quantity", "slot", "confirmed"}
            or data["confirmed"] is not True
            or isinstance(data["quantity"], bool)
            or not isinstance(data["quantity"], (int, float))
            or not isinstance(data["slot"], str)):
        raise InvalidLogFoodCommand()
    quantity = parse_provider_quantity(data["quantity"])
    slot = data["slot"].strip().lower()
    if slot not in {"kahvalti", "ogle", "aksam", "ara_ogun"}:
        raise InvalidLogFoodCommand()
    proof = mobile_menu.read_item_proof(
        secret, user_id, data["confirmation_token"], enforce_expiry=False)
    base = ManualNutritionSnapshot(**{
        key: Decimal(value) for key, value in proof["nutrition"].items()})
    try:
        scaled = ManualNutritionSnapshot(**{
            key: Decimal(value) * quantity
            for key, value in proof["nutrition"].items()})
        # Only signed dish name and confirmed portion; no model commentary,
        # confidence, proof identities or lineage in the persisted description.
        name = " ".join(proof["name"].split())
        portion = f"{_canonical_number(quantity)} porsiyon"
        grams = proof["portion"]["stated_grams"]
        if grams is not None:
            portion += f" ({_canonical_number(Decimal(grams) * quantity)} g)"
        return MenuConfirmedLogFoodCommand(
            analysis_id=proof["aid"], candidate_id=proof["cid"], name=name,
            stated_grams=grams, base_nutrition=base,
            estimation_source=proof["source"],
            confidence=Decimal(str(proof["confidence"])), quantity=quantity,
            slot=slot, nutrition=scaled, description=f"{name} — {portion}"[:500],
            expires_at=proof["exp"])
    except ValueError:
        raise InvalidLogFoodCommand() from None
