"""Dormant C1 boundary. Legal approval and release authorization are separate.

No environment, request argument, existing r2 grant or feature flag can open
these source gates. The consent/release PR must supply an approved disclosure
and activation authorization separately. Ordinary Coach chat is untouched.
"""
from dataclasses import dataclass
from app.services import ai_consent
from .document import EditError

NUTRITION_EDITS_ENABLED = False
APPROVED_COACH_EDIT_DISCLOSURES = frozenset()


@dataclass(frozen=True)
class Authorities:
    coach: ai_consent.OperationAuthority
    nutrition: ai_consent.OperationAuthority

    def to_context(self):
        return {"coach": self.coach.to_context(), "nutrition": self.nutrition.to_context()}

    @classmethod
    def from_context(cls, owner, value):
        if not isinstance(value, dict) or set(value) != {"coach", "nutrition"}:
            raise ai_consent.ConsentDenied("AI_CONSENT_CHANGED")
        return cls(*(ai_consent.OperationAuthority.from_context(owner, value[k])
                     for k in ("coach", "nutrition")))


def gate():
    if not NUTRITION_EDITS_ENABLED or not APPROVED_COACH_EDIT_DISCLOSURES:
        raise EditError("nutrition_edit_unavailable")


def fence(owner, authorities):
    gate()
    if (not isinstance(authorities, Authorities)
            or not isinstance(authorities.coach, ai_consent.OperationAuthority)
            or not isinstance(authorities.nutrition, ai_consent.OperationAuthority)
            or authorities.coach.feature != "coach"
            or authorities.nutrition.feature != "nutrition_plan"
            or authorities.coach.version not in APPROVED_COACH_EDIT_DISCLOSURES):
        raise ai_consent.ConsentDenied("AI_CONSENT_RECIPIENT_OR_SCOPE_UNAVAILABLE")
    # Each original grant is rechecked under the same owner transaction lock.
    ai_consent.fence(authorities.coach, owner, "coach")
    ai_consent.fence(authorities.nutrition, owner, "nutrition_plan")
