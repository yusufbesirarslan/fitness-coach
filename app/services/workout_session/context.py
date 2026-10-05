"""Frozen TI-00 V2 request and same-revision execution-context binding.

The sidecar is part of the session checkpoint CAS, never a separate authority.
``actual_rest`` is client-observed between-set logging interval evidence.
"""
import hashlib
from .prescription import project as project_prescription

from .checkpoint import Checkpoint, _canonical_json, load_snapshot, parse_checkpoint
from .errors import InvalidSessionRequest

MAX_CONTEXT_BYTES = 131072
MAX_V2_BODY_BYTES = 262144
_FIELDS = {'exercise_id', 'index', 'actual_rir', 'tempo_adherence', 'actual_rest'}
_RIR = frozenset({'0', '1', '2', '3', '4_plus'})
_TEMPO = frozenset({'as_prescribed', 'faster', 'slower', 'lost_control'})


def parse_contract(raw):
    if raw is None:
        return 1
    if raw not in ('1', '2'):
        raise InvalidSessionRequest('workout contract version is invalid')
    return int(raw)


def base_sets(snapshot):
    return {(e['exercise_id'], s['index']): s
            for e in (snapshot or {}).get('exercises', []) for s in e['sets']}


def _anchor(value):
    return {k: value[k] for k in ('completed', 'reps', 'weight_kg')}


def parse_v2(body, allowed):
    if not isinstance(body, dict) or set(body) != {'checkpoint', 'execution_context'}:
        raise InvalidSessionRequest('V2 checkpoint fields do not match the contract')
    try:
        size = len(_canonical_json(body).encode('utf-8'))
    except (ValueError, TypeError, OverflowError):
        raise InvalidSessionRequest('V2 checkpoint is malformed') from None
    if size > MAX_V2_BODY_BYTES:
        raise InvalidSessionRequest('V2 checkpoint exceeds its size bound')
    base = parse_checkpoint(body['checkpoint'], allowed)
    context = _parse_context(body['execution_context'], base.snapshot, allowed)
    encoded = _canonical_json({'checkpoint': base.snapshot, 'execution_context': context}).encode('utf-8')
    digest = hashlib.sha256(b'axisai:training-workout-checkpoint:v2\0' + encoded).hexdigest()
    return Checkpoint(base.snapshot, digest, context)


def _parse_context(context, snapshot, allowed):
    """One context validator for commands and anchor-verified stored projection."""
    if (not isinstance(context, dict) or set(context) != {'schema_version', 'sets'}
            or type(context['schema_version']) is not int or context['schema_version'] != 1):
        raise InvalidSessionRequest('execution context schema is invalid')
    entries = context['sets']
    if not isinstance(entries, list) or len(entries) > 640:
        raise InvalidSessionRequest('execution context set count is invalid')
    if len(_canonical_json(context).encode('utf-8')) > MAX_CONTEXT_BYTES:
        raise InvalidSessionRequest('execution context exceeds its size bound')
    bases = base_sets(snapshot)
    seen, normalized = set(), []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _FIELDS:
            raise InvalidSessionRequest('execution context fields are invalid')
        exercise, index = entry['exercise_id'], entry['index']
        if not isinstance(exercise, str) or type(index) is not int or not 0 <= index < 20:
            raise InvalidSessionRequest('execution context identity is invalid')
        identity = (exercise, index)
        if identity in seen or identity not in bases or not bases[identity]['completed']:
            raise InvalidSessionRequest('execution context requires a unique completed set')
        seen.add(identity)
        rir, tempo, interval = (entry[k] for k in ('actual_rir', 'tempo_adherence', 'actual_rest'))
        if rir is not None and (not isinstance(rir, str) or rir not in _RIR):
            raise InvalidSessionRequest('actual_rir is invalid')
        if tempo is not None and (not isinstance(tempo, str) or tempo not in _TEMPO):
            raise InvalidSessionRequest('tempo_adherence is invalid')
        if interval is not None:
            predecessor = bases.get((exercise, index - 1))
            if (not isinstance(interval, dict) or set(interval) != {'seconds', 'method', 'quality'}
                    or type(interval['seconds']) is not int or not 0 <= interval['seconds'] <= 3600
                    or interval['method'] != 'completion_gap'
                    or interval['quality'] != 'foreground_contiguous'
                    or index < 1 or not predecessor or not predecessor['completed']):
                raise InvalidSessionRequest('logging interval is invalid')
        if any(value is not None for value in (rir, tempo, interval)):
            normalized.append(dict(entry))
    normalized.sort(key=lambda e: (tuple(allowed).index(e['exercise_id']), e['index']))
    return {'schema_version': 1, 'sets': normalized}


def validate_tempo(context, prescription):
    targets = {e['exercise_id']: e['target_tempo'] for e in (prescription or {}).get('exercises', [])}
    for entry in context['sets']:
        tempo = entry['tempo_adherence']
        if tempo in ('as_prescribed', 'faster', 'slower'):
            target = targets.get(entry['exercise_id'])
            if target is None or (tempo in ('faster', 'slower') and target.get('kind') != 'phases'):
                raise InvalidSessionRequest('tempo adherence requires a compatible canonical target')


def project_context(row):
    empty = {'schema_version': 1, 'sets': []}
    if row is None:
        return empty
    stored = load_snapshot(row.execution_context_data)
    if (not stored or set(stored) != {'schema_version', 'bound_revision', 'sets'}
            or type(stored['schema_version']) is not int or stored['schema_version'] != 1
            or type(stored['bound_revision']) is not int
            or stored['bound_revision'] != row.checkpoint_revision):
        return empty
    try:
        bases = base_sets(load_snapshot(row.checkpoint_data))
        entries = []
        for item in stored['sets']:
            if not isinstance(item, dict) or set(item) != {'context', 'base_anchor', 'preceding_anchor'}:
                return empty
            entry = item['context']
            identity = (entry['exercise_id'], entry['index'])
            base = bases.get(identity)
            if not base or not base['completed'] or item['base_anchor'] != _anchor(base):
                continue
            entry = dict(entry)
            if entry['actual_rest'] is not None:
                previous = bases.get((identity[0], identity[1] - 1))
                if not previous or not previous['completed'] or item['preceding_anchor'] != _anchor(previous):
                    entry['actual_rest'] = None
            if any(entry[k] is not None for k in ('actual_rir', 'tempo_adherence', 'actual_rest')):
                entries.append(entry)
        # Revalidate stored public semantics, including bounds and identities.
        snapshot = load_snapshot(row.checkpoint_data)
        allowed = tuple(e['exercise_id'] for e in snapshot['exercises'])
        # Selection indexes the full workout, not this persisted subset. The
        # base was validated at acceptance; verify anchors and context here
        # without re-resolving a possibly replaced plan or reinterpreting index.
        context = _parse_context({'schema_version': 1, 'sets': entries}, snapshot, allowed)
        validate_tempo(context, project_prescription(row.prescription_data))
        return context
    except (KeyError, TypeError, ValueError, InvalidSessionRequest):
        return empty


def bind_context(row, parsed, next_revision):
    """Compute from base r; caller's WHERE r atomically installs the result."""
    bases = base_sets(parsed.snapshot)
    if parsed.execution_context is not None:
        context = parsed.execution_context
        validate_tempo(context, project_prescription(row.prescription_data))
    else:
        old_bases = base_sets(load_snapshot(row.checkpoint_data))
        entries = []
        for entry in project_context(row)['sets']:
            identity = (entry['exercise_id'], entry['index'])
            new = bases.get(identity)
            if not new or not new['completed'] or _anchor(new) != _anchor(old_bases[identity]):
                continue
            entry = dict(entry)
            preceding = (identity[0], identity[1] - 1)
            if entry['actual_rest'] is not None and old_bases.get(preceding) != bases.get(preceding):
                entry['actual_rest'] = None
            if any(entry[k] is not None for k in ('actual_rir', 'tempo_adherence', 'actual_rest')):
                entries.append(entry)
        context = {'schema_version': 1, 'sets': entries}
    stored = []
    for entry in context['sets']:
        identity = (entry['exercise_id'], entry['index'])
        stored.append({'context': entry, 'base_anchor': _anchor(bases[identity]),
                       'preceding_anchor': _anchor(bases[(identity[0], identity[1] - 1)])
                       if entry['actual_rest'] is not None else None})
    return _canonical_json({'schema_version': 1, 'bound_revision': next_revision, 'sets': stored})
