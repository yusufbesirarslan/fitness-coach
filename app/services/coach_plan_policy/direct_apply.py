"""Direct-apply eligibility for one typed Coach Training edit (#547 F3).

Separate from ``decisions``: that module's verdict vocabulary stays closed
(APPLY_NOW / REQUIRE_CONFIRMATION / REFUSE_UNSUPPORTED, no tiers or scores).
This predicate can only turn a REQUIRE_CONFIRMATION into a direct apply, and
only when the caller ALSO proves an approved capability disclosure that
permits it (``coach_capabilities.direct_apply_allowed``, empty in this
source). Server-owned rule version ``coach_capabilities.RISK_POLICY_VERSION``.
"""
# The command type comes through ``decisions`` (the approved plan_mutation
# consumer); this module adds no new domain import.
from .decisions import ImpactFacts, UpdateExercisePrescriptionCommand

DIRECT_APPLY_MAX_SET_DELTA = 1


def is_direct_apply_eligible(command, facts, changed_leaves):
    """An explicit, single-field, small, journaled prescription edit.

    ``changed_leaves`` is ``[(path, before, after), ...]`` — every leaf of the
    canonical plan document the command would change, computed by the caller
    from the authoritative plan. Eligible iff:

    * the command is a typed ``UpdateExercisePrescriptionCommand`` carrying
      ``sets`` only (never a remove, replace, add, move, Undo or unknown);
    * impact is determinable and no ACTIVE workout session is touched;
    * exactly ONE leaf changes, it is the exercise's ``set`` count, and the
      count moves by at most ``DIRECT_APPLY_MAX_SET_DELTA`` between positive
      integers (reps, rest, notes, names and schedules always confirm).

    Reversible: the canonical writer journals it and the existing Undo
    restores the exact prior document bytes.
    """
    if not isinstance(command, UpdateExercisePrescriptionCommand):
        return False
    if (not isinstance(facts, ImpactFacts) or facts.impact_undeterminable
            or facts.touches_active_session):
        return False
    if command.reps is not None or command.sets is None:
        return False
    if not isinstance(changed_leaves, (list, tuple)) or len(changed_leaves) != 1:
        return False
    path, before, after = changed_leaves[0]
    if not path or path[-1] != "set":
        return False
    if (type(before) is not int or type(after) is not int
            or before <= 0 or after <= 0):
        return False
    return 0 < abs(after - before) <= DIRECT_APPLY_MAX_SET_DELTA
