"""Dormant C1 boundary. Legal approval and release authorization are separate.

No environment, request argument, existing r2 grant or feature flag can open
these source gates. The consent/release PR must supply an approved disclosure
and activation authorization separately. Ordinary Coach chat is untouched.
"""
from dataclasses import dataclass
from typing import Optional
from app.services import ai_consent
from .document import EditError

NUTRITION_EDITS_ENABLED = False
# Coach CONVERSATION versions that may be combined with a separately granted
# Coach Nutrition Plan capability (#533). Listing a version here never makes
# its grant cover Nutrition by itself: every edit ALSO needs the owner's own
# ``coach.nutrition_plan_access`` capability authority (``coach_capabilities``)
# and the separate ``nutrition.plan`` grant. Empty in this source.
APPROVED_COACH_EDIT_DISCLOSURES = frozenset()


@dataclass(frozen=True)
class Authorities:
    coach: ai_consent.OperationAuthority
    nutrition: ai_consent.OperationAuthority
    # The Coach Nutrition Plan capability (#533). ``None`` is never writable.
    capability: Optional[ai_consent.CapabilityAuthority] = None

    def to_context(self):
        value = {"coach": self.coach.to_context(), "nutrition": self.nutrition.to_context()}
        if self.capability is not None:
            value["capability"] = self.capability.to_context()
        return value

    @classmethod
    def from_context(cls, owner, value):
        if (not isinstance(value, dict)
                or set(value) not in ({"coach", "nutrition"}, {"coach", "nutrition", "capability"})):
            raise ai_consent.ConsentDenied("AI_CONSENT_CHANGED")
        coach, nutrition = (ai_consent.OperationAuthority.from_context(owner, value[k])
                            for k in ("coach", "nutrition"))
        capability = None
        if "capability" in value:
            from app.services.coach_capabilities import NUTRITION_PLAN_ACCESS
            capability = ai_consent.CapabilityAuthority.from_context(
                owner, value["capability"], NUTRITION_PLAN_ACCESS)
        return cls(coach, nutrition, capability)


def gate():
    if not NUTRITION_EDITS_ENABLED or not APPROVED_COACH_EDIT_DISCLOSURES:
        raise EditError("nutrition_edit_unavailable")


def fence(owner, authorities):
    gate()
    from app.services.coach_capabilities import NUTRITION_PLAN_ACCESS
    if (not isinstance(authorities, Authorities)
            or not isinstance(authorities.coach, ai_consent.OperationAuthority)
            or not isinstance(authorities.nutrition, ai_consent.OperationAuthority)
            or authorities.coach.feature != "coach"
            or authorities.nutrition.feature != "nutrition_plan"
            or authorities.coach.version not in APPROVED_COACH_EDIT_DISCLOSURES):
        raise ai_consent.ConsentDenied("AI_CONSENT_RECIPIENT_OR_SCOPE_UNAVAILABLE")
    capability = authorities.capability
    if (not isinstance(capability, ai_consent.CapabilityAuthority)
            or capability.capability != NUTRITION_PLAN_ACCESS
            or capability.owner != authorities.coach.owner
            or capability.generation != authorities.coach.generation):
        # No historical grant expansion: Coach + nutrition.plan alone never write.
        raise ai_consent.ConsentDenied("AI_CAPABILITY_CONSENT_REQUIRED")
    # Each original grant is rechecked under the same owner transaction lock.
    ai_consent.fence(authorities.coach, owner, "coach")
    ai_consent.fence(authorities.nutrition, owner, "nutrition_plan")
    ai_consent.fence_capabilities(owner, capability)
