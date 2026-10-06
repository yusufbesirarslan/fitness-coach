"""The single public Training Insight projection (TI-00 §16 as amended).

Fixed keys, fixed tokens, bounded values. The projection re-validates every
value it emits and raises instead of publishing anything outside the contract:
an internal invariant failure is a 503, never a malformed 200. No database id,
user id, note text, provider prose, raw checkpoint JSON or exception text can
reach the payload -- only the opaque session references the owner already has
and the public catalog exercise identity.
"""
from __future__ import annotations

import math

from .models import (
    CONTRACT_VERSION,
    KIND_INSUFFICIENT,
    KINDS,
    LEVER_EFFORT,
    LEVER_HOLD,
    LEVER_TEMPO,
    MAX_EVIDENCE,
    MAX_PAIRED_SETS,
    METRIC_MAXIMUM,
    METRIC_UNITS,
    MISSING_CODES,
    OBSERVE_NEXT,
    RESERVED_KINDS,
    RIR_ORDER,
    RULESET_VERSION,
    STATE_AVAILABLE,
    STATE_INSUFFICIENT,
    STATE_NOT_COMPARABLE,
    STATES,
    TEMPO_TOKENS,
    TITLE_NOT_COMPARABLE,
    TITLE_PREFIX,
    Selection,
)

_UNITS = dict(METRIC_UNITS)
_TOKENS = {"rir": RIR_ORDER, "tempo": TEMPO_TOKENS}
_ACTIONS = {LEVER_TEMPO, LEVER_EFFORT, LEVER_HOLD}


class ProjectionInvariantError(RuntimeError):
    """An internal value fell outside the frozen projection contract."""


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ProjectionInvariantError(message)


def _value(metric: str, value):
    unit = _UNITS[metric]
    if unit == "token":
        _check(isinstance(value, str) and value in _TOKENS[metric], "evidence token")
        return value
    if unit == "int":
        _check(type(value) is int and 0 <= value <= METRIC_MAXIMUM[metric], "evidence int")
        return value
    _check(type(value) in (int, float) and math.isfinite(value)
           and 0 <= value <= METRIC_MAXIMUM[metric], "evidence number")
    return round(float(value), 1)


def _evidence(item) -> dict:
    _check(item.metric in _UNITS, "evidence metric")
    _check(type(item.paired_sets) is int and 0 <= item.paired_sets <= MAX_PAIRED_SETS,
           "paired_sets")
    _check(item.previous_session_ref is None or isinstance(item.previous_session_ref, str),
           "previous_session_ref")
    return {
        "metric": item.metric,
        "unit": _UNITS[item.metric],
        "previous": _value(item.metric, item.previous),
        "current": _value(item.metric, item.current),
        "paired_sets": item.paired_sets,
        "previous_session_ref": item.previous_session_ref,
    }


def _title(selection: Selection) -> str:
    if selection.state == STATE_NOT_COMPARABLE:
        return TITLE_NOT_COMPARABLE
    return TITLE_PREFIX + selection.kind


def insight_payload(session_ref: str, checkpoint_revision: int, selection: Selection) -> dict:
    _check(selection.state in STATES, "state")
    if selection.state == STATE_AVAILABLE:
        _check(selection.kind in KINDS and selection.kind != KIND_INSUFFICIENT, "kind")
    elif selection.state == STATE_INSUFFICIENT:
        _check(selection.kind == KIND_INSUFFICIENT, "insufficient kind")
    else:
        _check(selection.kind is None, "not comparable kind")
    _check(selection.kind not in RESERVED_KINDS, "reserved kind")
    _check(selection.exercise_id is None or isinstance(selection.exercise_id, str), "exercise")
    _check(selection.state != STATE_AVAILABLE or selection.exercise_id is not None, "owner")
    _check(len(selection.evidence) <= MAX_EVIDENCE, "evidence bound")
    metrics = [item.metric for item in selection.evidence]
    _check(len(set(metrics)) == len(metrics), "duplicate evidence metric")
    _check(len(set(selection.missing)) == len(selection.missing)
           and all(code in MISSING_CODES for code in selection.missing), "missing codes")
    action = selection.recommended_action
    if action is not None:
        _check(selection.state == STATE_AVAILABLE, "action without insight")
        _check(set(action) == {"lever", "action", "observe"}
               and (action["lever"], action["action"]) in _ACTIONS
               and action["observe"] == OBSERVE_NEXT, "action vocabulary")
    _check(type(checkpoint_revision) is int and checkpoint_revision >= 0, "revision")
    return {"training_insight": {
        "contract_version": CONTRACT_VERSION,
        "ruleset_version": RULESET_VERSION,
        "session_ref": session_ref,
        "checkpoint_revision": checkpoint_revision,
        "state": selection.state,
        "kind": selection.kind,
        "exercise_id": selection.exercise_id,
        "title_key": _title(selection),
        "evidence": [_evidence(item) for item in selection.evidence],
        "missing_data": list(selection.missing),
        "recommended_action": dict(action) if action is not None else None,
    }}
