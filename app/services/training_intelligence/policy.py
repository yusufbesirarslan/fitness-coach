"""Frozen primary selection and the minimum-effective-intervention policy.

TI-00 §16 as amended by TI-03: exactly one primary insight per session, owned
by one canonical exercise, chosen by a fixed kind priority and then canonical
workout order. TI-00 §15: at most ONE lever, only in the explicit cases below;
otherwise no recommendation. Nothing here can reach a plan writer.
"""
from __future__ import annotations

from .diagnostics import DECLINED, INCREASED, MIXED
from .models import (
    KIND_EFFORT_INCREASED,
    KIND_EXECUTION_QUALITY,
    KIND_INSUFFICIENT,
    KIND_PRIORITY,
    LEVER_EFFORT,
    LEVER_HOLD,
    LEVER_TEMPO,
    MISSING_CODES,
    OBSERVE_NEXT,
    RIR_ORDER,
    STATE_AVAILABLE,
    STATE_INSUFFICIENT,
    STATE_NOT_COMPARABLE,
    Selection,
)


def _ordered_missing(codes) -> tuple:
    return tuple(code for code in MISSING_CODES if code in codes)


def select_primary(assessments, session_missing=frozenset()) -> Selection:
    """One deterministic primary insight across the session's exercises.

    1. Each assessed exercise's headline is its highest-priority available kind.
       The primary is the smallest (kind rank, canonical workout position).
    2. No available kind: the first exercise in canonical order whose
       comparison was not comparable gives ``not_comparable``.
    3. Otherwise ``insufficient_data`` for the first exercise in canonical order
       with a completed set, with empty evidence.
    """
    assessments = sorted(assessments, key=lambda a: a.position)
    ranked = [a for a in assessments if a.kinds]
    if ranked:
        chosen = min(ranked, key=lambda a: (KIND_PRIORITY.index(a.kinds[0]), a.position))
        kind = chosen.kinds[0]
        return Selection(
            state=STATE_AVAILABLE, kind=kind, exercise_id=chosen.exercise_id,
            evidence=chosen.evidence,
            missing=_ordered_missing(chosen.missing | session_missing),
            recommended_action=recommend(chosen, kind),
        )
    blocked = [a for a in assessments if a.comparison == STATE_NOT_COMPARABLE]
    if blocked:
        chosen = blocked[0]
        return Selection(STATE_NOT_COMPARABLE, None, chosen.exercise_id, chosen.evidence,
                         _ordered_missing(chosen.missing | session_missing), None)
    chosen = assessments[0] if assessments else None
    missing = (chosen.missing if chosen else frozenset()) | session_missing
    return Selection(STATE_INSUFFICIENT, KIND_INSUFFICIENT,
                     chosen.exercise_id if chosen else None, (),
                     _ordered_missing(missing), None)


def _action(lever) -> dict:
    return {"lever": lever[0], "action": lever[1], "observe": OBSERVE_NEXT}


def recommend(assessment, kind):
    """At most one lever, evaluated in TI-00 §15 priority order.

    1. Rest: reserved; a completion gap never qualifies, so never emitted.
    2. Declared tempo loss against a stable structured tempo target with >=2
       paired observations: follow the prescribed tempo.
    3. Isolated effort increase below a comparable structured RIR target, with
       no unresolved performance or tempo confound: aim at prescribed effort.
    4. Performance decline with increased recorded exposure while effort and
       execution quality are ambiguous: hold the current approach and observe.
    Anything else: no recommendation. No deload, load, set, rep, frequency or
    exercise change is ever recommended.
    """
    policy = assessment.policy
    if (kind == KIND_EXECUTION_QUALITY and policy.get("tempo_changed")
            and policy.get("target_tempo") is not None
            and "lost_control" in policy.get("current_tempo", ())):
        return _action(LEVER_TEMPO)
    target_rir = policy.get("target_rir")
    if (kind == KIND_EFFORT_INCREASED and policy.get("effort") == INCREASED
            and target_rir is not None and policy.get("performance") != MIXED
            and not policy.get("tempo_changed")
            and any(RIR_ORDER.index(value) < RIR_ORDER.index(target_rir)
                    for value in policy.get("current_rir", ()))):
        return _action(LEVER_EFFORT)
    if (policy.get("performance") == DECLINED and policy.get("exposure_increase")
            and policy.get("effort") in (None, MIXED) and not policy.get("tempo_changed")):
        return _action(LEVER_HOLD)
    return None
