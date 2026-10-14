"""V1 wire qualification, with explicit test policy and no Apple/network I/O."""
import copy
import json
from datetime import timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from ai_allowance_support import (
    LEGACY_CREATED_AT, enable_ledger, enable_subscriptions, fixture_policy, premium_row,
)
from app.extensions import db
from app.models import User, AiAllowanceBucket
from app.services import ai_allowances as A

SCHEMA = json.loads((Path(__file__).resolve().parents[1] /
                     'docs/mobile/ai-allowances-v1.schema.json').read_text())
VALIDATOR = Draft202012Validator(SCHEMA)


def check(body):
    VALIDATOR.validate(body)
    assert [f['feature'] for f in body['features']] == list(A.SUPPORTED_FEATURES)
    assert (body['subscription']['state'] == 'DISABLED') == (body['subscription']['entitlement'] is None)
    assert (body['starter']['state'] == 'GRANTED') == (body['starter']['granted_at'] is not None)
    for feature in body['features']:
        counters = [feature[k] for k in ('limit', 'used', 'reserved', 'remaining')]
        if feature['state'] in ('NOT_IN_PLAN', 'NOT_CONFIGURED'):
            assert counters == [None] * 4 and feature['reset'] is None and feature['resets_at'] is None
        else:
            limit, used, reserved, remaining = counters
            assert all(type(n) is int and n >= 0 for n in counters)
            assert remaining == limit - used - reserved
            assert (feature['state'] == 'AVAILABLE') == (remaining > 0)
            if feature['scope'] == 'STARTER':
                assert feature['reset'] == 'NEVER' and feature['resets_at'] is None
            else:
                assert feature['reset'] in ('WEEKLY', 'MONTHLY') and feature['resets_at'].endswith('Z')
    return body


@pytest.mark.parametrize('state', ['unconfigured', 'eligible', 'ineligible', 'granted', 'held',
                                  'used', 'zero', 'premium_weekly', 'premium_monthly',
                                  'premium_missing', 'expired', 'stale'])
def test_wire_states_remain_distinct(app, make_user, monkeypatch, state):
    owner = make_user('contract-' + state).id
    enable_ledger(app)
    if state == 'unconfigured':
        app.config['AI_ALLOWANCE_POLICY'] = None
    elif state == 'ineligible':
        db.session.get(User, owner).created_at = LEGACY_CREATED_AT
        db.session.commit()
    elif state in ('granted', 'held', 'used'):
        A.grant_starter(owner)
        if state != 'granted':
            hold = A.reserve(owner, 'qr_menu', 'contract-menu-1')
            if state == 'used':
                A.finalize(owner, hold.reference, 'success')
    elif state == 'zero':
        # A schema-valid historical zero bucket; policy constructors require positive grants.
        A.grant_starter(owner)
        bucket = AiAllowanceBucket.query.filter_by(user_id=owner, feature='coach').one()
        bucket.granted = 0
        db.session.commit()
    elif state.startswith('premium') or state in ('expired', 'stale'):
        enable_subscriptions(app, monkeypatch)
        app.config['AI_ALLOWANCE_POLICY'] = fixture_policy(
            premium=state != 'premium_missing',
            period='MONTHLY' if state == 'premium_monthly' else 'WEEKLY')
        kwargs = ({'expires_in': timedelta(seconds=-1)} if state == 'expired' else
                  {'verified_ago': timedelta(days=2)} if state == 'stale' else {})
        premium_row(owner, **kwargs)
    body = check(A.allowance_view(owner))
    coach, menu = body['features'][0], body['features'][4]
    if state == 'unconfigured':
        assert body['access'] == 'UNCONFIGURED' and coach['state'] == 'NOT_CONFIGURED'
    elif state == 'ineligible':
        assert body['access'] == 'NONE' and body['starter']['state'] == 'NOT_ELIGIBLE'
    elif state in ('eligible', 'granted'):
        assert body['starter']['state'] == state.upper() and coach['state'] == 'AVAILABLE'
    elif state in ('held', 'used'):
        assert body['starter']['state'] == 'GRANTED' and menu['state'] == 'EXHAUSTED'
        assert (menu['used'], menu['reserved'], menu['remaining']) == ((0, 1, 0) if state == 'held' else (1, 0, 0))
    elif state == 'zero':
        assert coach['limit'] == 0 and coach['state'] == 'EXHAUSTED'
        assert menu['state'] == 'AVAILABLE' and menu['remaining'] == 1
    elif state.startswith('premium'):
        assert body['access'] == 'PREMIUM' and body['subscription']['entitlement']['effective_premium'] is True
        assert coach['state'] == ('NOT_IN_PLAN' if state == 'premium_missing' else 'AVAILABLE')
    else:
        assert body['access'] == 'STARTER'
        assert body['subscription']['entitlement']['status'] == ('EXPIRED' if state == 'expired' else 'VERIFICATION_PENDING')
        assert body['subscription']['entitlement']['effective_premium'] is False


@pytest.mark.parametrize('mutation', ['version', 'missing', 'bool_count', 'negative', 'unknown_state', 'timestamp'])
def test_schema_rejects_unsupported_or_malformed_observations(app, make_user, mutation):
    enable_ledger(app)
    body = copy.deepcopy(A.allowance_view(make_user('bad-' + mutation).id))
    if mutation == 'version': body['contract_version'] = 2
    elif mutation == 'missing': del body['features'][0]['remaining']
    elif mutation == 'bool_count': body['features'][0]['limit'] = True
    elif mutation == 'negative': body['features'][0]['remaining'] = -1
    elif mutation == 'unknown_state': body['features'][0]['state'] = 'UNLIMITED'
    else: body['starter']['granted_at'] = '2026-10-14T00:00:00+03:00'
    with pytest.raises(ValidationError):
        VALIDATOR.validate(body)
