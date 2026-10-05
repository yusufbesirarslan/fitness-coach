"""Bounded immutable server targets captured at start; never client input.

Current plans only support exact legacy reps/rest parsing. Load/RIR/tempo remain
unavailable; cue prose is neither parsed nor stored here.
"""
import re

from .checkpoint import MAX_SNAPSHOT_BYTES, _canonical_json, load_snapshot

_REPS = re.compile(r'([0-9]{1,4})(?:-([0-9]{1,4}))?')
_TARGETS = ('target_reps', 'target_load_kg', 'target_rir', 'target_tempo', 'planned_rest_seconds')


def capture(plan, entries):
    from app.services.exercise_catalog import resolve_exercise
    from app.services.mobile_training import _rest

    if not entries or len(entries) > 32:
        return None
    result, seen = [], set()
    for entry in entries:
        identity, sets = entry.get('exercise_id'), entry.get('set')
        if not isinstance(identity, str) or identity in seen or type(sets) is not int or not 1 <= sets <= 20:
            return None
        try:
            resolve_exercise(exercise_id=identity)
        except (ValueError, KeyError):
            return None
        seen.add(identity)
        reps = None
        match = _REPS.fullmatch(entry.get('tekrar', '')) if isinstance(entry.get('tekrar'), str) else None
        if match:
            low, high = int(match[1]), int(match[2] or match[1])
            if 0 <= low <= high <= 1000:
                reps = {'min': low, 'max': high}
        rest = _rest(entry['dinlenme'])['seconds'] if isinstance(entry.get('dinlenme'), str) else None
        values = dict(target_reps=reps, target_load_kg=None, target_rir=None,
                      target_tempo=None, planned_rest_seconds=rest)
        result.append({'exercise_id': identity, 'sets': sets, **values,
                       'provenance': {key: 'legacy_parsed' if value is not None else 'unavailable'
                                      for key, value in values.items()}})
    document = {'schema_version': 1, 'source_plan_lineage': plan.lineage_id,
                'source_mutation_version': plan.mutation_version, 'exercises': result}
    encoded = _canonical_json(document)
    return encoded if len(encoded.encode('utf-8')) <= MAX_SNAPSHOT_BYTES else None


def project(raw):
    """Fail closed on a malformed stored snapshot rather than infer targets."""
    document = load_snapshot(raw)
    if not document or set(document) != {'schema_version', 'source_plan_lineage', 'source_mutation_version', 'exercises'}:
        return None
    if type(document['schema_version']) is not int or document['schema_version'] != 1:
        return None
    lineage, version = document['source_plan_lineage'], document['source_mutation_version']
    if lineage is not None and (not isinstance(lineage, str) or not 1 <= len(lineage) <= 64
                                or any(ord(char) < 33 or ord(char) > 126 for char in lineage)):
        return None
    if version is not None and (type(version) is not int or version < 0):
        return None
    entries = document['exercises']
    if not isinstance(entries, list) or not 1 <= len(entries) <= 32:
        return None
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'exercise_id', 'sets', 'provenance', *_TARGETS}:
            return None
        identity = entry['exercise_id']
        if not isinstance(identity, str) or identity in seen or type(entry['sets']) is not int or not 1 <= entry['sets'] <= 20:
            return None
        seen.add(identity)
        provenance = entry['provenance']
        if (not isinstance(provenance, dict) or set(provenance) != set(_TARGETS)
                or any(value not in ('structured', 'legacy_parsed', 'unavailable') for value in provenance.values())):
            return None
        for key, maximum in (('target_reps', 1000), ('target_load_kg', 1000)):
            value = entry[key]
            if value is not None:
                if not isinstance(value, dict) or set(value) != {'min', 'max'}:
                    return None
                low, high = value['min'], value['max']
                types = (int,) if key == 'target_reps' else (int, float)
                if type(low) not in types or type(high) not in types or not 0 <= low <= high <= maximum:
                    return None
                if key == 'target_load_kg' and (round(low, 1) != low or round(high, 1) != high):
                    return None
        if entry['target_rir'] is not None and entry['target_rir'] not in ('0', '1', '2', '3', '4_plus'):
            return None
        rest = entry['planned_rest_seconds']
        if rest is not None and (type(rest) is not int or not 0 <= rest <= 86400):
            return None
        tempo = entry['target_tempo']
        if tempo is not None:
            if not isinstance(tempo, dict):
                return None
            if tempo.get('kind') == 'phases':
                phases = {'eccentric_seconds', 'bottom_pause_seconds', 'concentric_seconds', 'top_pause_seconds'}
                if set(tempo) != {'kind', *phases} or any(type(tempo[k]) is not int or not 0 <= tempo[k] <= 10 for k in phases):
                    return None
                if sum(tempo[k] for k in phases) == 0:
                    return None
            elif tempo.get('kind') != 'cue' or set(tempo) != {'kind', 'cue'} or tempo['cue'] not in ('controlled_eccentric', 'pause_at_stretch', 'explosive_concentric'):
                return None
    try:
        return document if len(_canonical_json(document).encode('utf-8')) <= MAX_SNAPSHOT_BYTES else None
    except (TypeError, ValueError):
        return None
