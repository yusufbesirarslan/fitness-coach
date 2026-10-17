"""TI-05 contract handoff over actual start/checkpoint/complete endpoints."""
import json
from pathlib import Path
from datetime import timedelta

from tests.test_ti03_api import (
    owner, flags, as_mobile, completion_proof, _plan, _workout,
    _CompletionClock, THURSDAY_ONE, THURSDAY_TWO, get_insight,
)
from app.timeutil import audit_clock
from app.services.training_intelligence import models as m


def test_canonical_lifecycle_matches_cross_repo_golden(
        client, app, owner, flags, as_mobile, completion_proof, monkeypatch):
    from app.services.workout_completion import service
    monkeypatch.setattr(service, 'datetime', _CompletionClock)
    _plan(owner.id)
    previous = _workout(client, app, owner, as_mobile, THURSDAY_ONE, [8, 8, 8], 'ti05-golden-prev')
    current = _workout(client, app, owner, as_mobile, THURSDAY_TWO, [9, 9, 8], 'ti05-golden-current')
    with audit_clock(THURSDAY_TWO + timedelta(days=3)):
        response = get_insight(client, owner, current, as_mobile)
    assert response.status_code == 200
    payload = response.get_json()
    # Normalize only random opaque references, never derived facts or tokens.
    payload['training_insight']['session_ref'] = 'ti05_current'
    for evidence in payload['training_insight']['evidence']:
        if evidence['previous_session_ref'] == previous:
            evidence['previous_session_ref'] = 'ti05_previous'
    assert payload == json.loads(Path('tests/fixtures/ti05_training_insight.json').read_text()), json.dumps(payload, sort_keys=True)


def test_frozen_contract_manifest():
    manifest = json.loads(Path('tests/fixtures/ti05_contract.json').read_text())
    assert manifest == {
        'contract_version': m.CONTRACT_VERSION, 'ruleset_version': m.RULESET_VERSION,
        'states': list(m.STATES), 'kinds': list(m.KINDS),
        'missing_codes': list(m.MISSING_CODES), 'metric_units': dict(m.METRIC_UNITS),
        'metric_maximum': m.METRIC_MAXIMUM, 'rir': list(m.RIR_ORDER),
        'tempo': list(m.TEMPO_TOKENS),
        'actions': [list(m.LEVER_TEMPO), list(m.LEVER_EFFORT), list(m.LEVER_HOLD)],
        'observe': m.OBSERVE_NEXT, 'max_evidence': m.MAX_EVIDENCE,
        'max_missing': len(m.MISSING_CODES), 'max_paired_sets': m.MAX_PAIRED_SETS,
        'history_days': m.HISTORY_DAYS, 'max_recent_sessions': m.MAX_RECENT_SESSIONS,
        'max_history_rows': m.MAX_HISTORY_ROWS,
        'max_exercises': m.MAX_SCOPE_EXERCISES, 'max_sets': m.MAX_SCOPE_SETS,
    }


def test_representative_insight_get_latency_is_recorded(client, owner, flags, as_mobile):
    from statistics import median
    from time import perf_counter
    from tests.test_ti03_api import improving
    ref = improving(owner.id)
    samples = []
    payloads = []
    for _ in range(20):
        start = perf_counter()
        response = get_insight(client, owner, ref, as_mobile)
        samples.append((perf_counter() - start) * 1000)
        assert response.status_code == 200
        payloads.append(response.get_json())
    assert all(payload == payloads[0] for payload in payloads)
    print(f'TI05 representative GET ms: median={median(samples):.2f} max={max(samples):.2f} n=20')


def test_cross_date_completed_endpoint_refuses_fabricated_execution(client, owner, flags, as_mobile):
    from dataclasses import replace
    from tests.test_ti03_api import persist
    from tests.ti03_support import ANCHOR, SQUAT, stored, straight, utc_noon
    persist(owner.id, replace(stored('ti05-cross-date', ANCHOR,
        [(SQUAT, straight(60.0, 8, 8))]), completed_at=utc_noon(ANCHOR + timedelta(days=1))))
    response = get_insight(client, owner, 'ti05-cross-date', as_mobile)
    assert response.status_code == 200
    insight = response.get_json()['training_insight']
    assert insight['state'] == 'insufficient_data'
    assert insight['missing_data'] == ['missing_execution']
    assert insight['evidence'] == []
    assert insight['recommended_action'] is None
