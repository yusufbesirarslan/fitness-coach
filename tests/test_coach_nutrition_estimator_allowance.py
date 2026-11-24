"""C2 x launch allowance runtime: the Coach-initiated Nutrition edit estimator.

Found by the isolated Linux Coach E2E (AXISAI-W0-COACH-FULL-E2E-CLOSURE): with
the allowance execution ON, a Coach turn's ``replace_nutrition_plan_item``
re-estimates the meal through ``_heavy_chat(feature="nutrition_plan")`` under
the owner's separate nutrition.plan D1 authority. The Coach logical action did
not admit that physical feature, so EVERY Coach Nutrition edit was refused
(AI_ALLOWANCE_IDEMPOTENCY_CONFLICT -> ESTIMATE_UNAVAILABLE, turn 409).

The fix admits exactly that nested call inside the SAME Coach unit, and only
while the independent nutrition.plan authority of the same owner is current.
"""
import contextlib

import pytest

from physical_budget_support import before_attempt as physical_attempt
from app.services import ai_consent
from app.services.ai_allowances import execution as E
from app.services.ai_allowances.policy import SUPPORTED_FEATURES
from app.models import AiUsageReservation
from tests.test_premium_allowance_execution import counts, ready  # noqa: F401


def authority(owner, feature):
    return ai_consent.OperationAuthority(owner, feature, 1, "synthetic")


def test_coach_unit_funds_the_nested_nutrition_estimator(ready):
    owner = ready
    with E.logical_action(owner, "coach", {"turn": 1}, key="coach-nutrition-edit-1") as action:
        E.check_provider(owner, "coach")
        physical_attempt(owner, "coach")
        with ai_consent.operation_scope(authority(owner, "nutrition_plan")):
            E.check_provider(owner, "nutrition_plan")
            physical_attempt(owner, "nutrition_plan")
        action.success()
    assert counts(owner, "coach") == (1, 0, 0)
    row = AiUsageReservation.query.one()
    assert (row.feature, row.physical_attempts) == ("coach", 2)
    # No second visible unit: the Nutrition generation allowance is untouched.
    assert {r.feature for r in AiUsageReservation.query.all()} == {"coach"}


@pytest.mark.parametrize("current", [None, "coach", "vision", "training_plan"])
def test_without_the_nutrition_plan_authority_the_estimator_is_refused(ready, current):
    owner = ready
    with E.logical_action(owner, "coach", {"turn": current}, key=f"coach-no-nutrition-{current}"):
        # None: only the turn's own Coach authority (set by the action scope).
        scope = (ai_consent.operation_scope(authority(owner, current)) if current
                 else contextlib.nullcontext())
        with scope:
            for check in (E.check_provider, lambda o, f: physical_attempt(o, f)):
                with pytest.raises(E.ExecutionRefused) as refused:
                    check(owner, "nutrition_plan")
                assert refused.value.code == "AI_ALLOWANCE_IDEMPOTENCY_CONFLICT"


def test_another_owners_nutrition_authority_is_refused(ready, make_user):
    owner, other = ready, make_user("other-nutrition-owner").id
    with E.logical_action(owner, "coach", {"turn": "x"}, key="coach-foreign-authority"):
        with ai_consent.operation_scope(authority(other, "nutrition_plan")):
            assert E.physical_allowed("coach", "nutrition_plan", owner) is False
            with pytest.raises(E.ExecutionRefused):
                E.check_provider(owner, "nutrition_plan")


@pytest.mark.parametrize("family", sorted(set(SUPPORTED_FEATURES) - {"coach", "nutrition_generation"}))
def test_no_other_family_gains_the_nested_estimator(ready, family):
    owner = ready
    with ai_consent.operation_scope(authority(owner, "nutrition_plan")):
        assert E.physical_allowed(family, "nutrition_plan", owner) is False
    with E.logical_action(owner, family, {"f": family}, key=f"other-family-{family}"):
        with ai_consent.operation_scope(authority(owner, "nutrition_plan")):
            with pytest.raises(E.ExecutionRefused) as refused:
                E.check_provider(owner, "nutrition_plan")
            assert refused.value.code == "AI_ALLOWANCE_IDEMPOTENCY_CONFLICT"


def test_static_table_is_unchanged_and_only_coach_is_widened(ready):
    assert "nutrition_plan" not in E.PHYSICAL_FEATURES["coach"]
    for family, features in E.PHYSICAL_FEATURES.items():
        for feature in features:
            assert E.physical_allowed(family, feature, ready) is True
    assert E.physical_allowed("coach", "menu_extract", ready) is False
    assert E.physical_allowed("coach", "training_plan", ready) is False
