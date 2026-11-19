"""Hermetic privacy and failure tests. No application or AWS calls."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.services.product_analytics import Grant, ReviewBuffer

NOW = datetime(2026, 11, 19, tzinfo=timezone.utc)


def payload(name='app_opened', source='client', properties=None):
    return dict(schema_version=1, event_id=str(uuid4()), action_id=str(uuid4()),
                name='axis_v1_' + name, source=source, occurred_at=NOW.isoformat(),
                properties=properties or {})


@pytest.fixture
def rig():
    state = {'grant': Grant(str(uuid4()), str(uuid4())), 'now': NOW}
    sent = []
    buffer = ReviewBuffer(enabled=True, current_grant=lambda: state['grant'],
                          sink=lambda e, s: sent.append((e, s)), clock=lambda: state['now'])
    return buffer, state, sent


def test_source_default_does_not_even_read_authority():
    def forbidden():
        pytest.fail('disabled foundation must not read authority')
    b = ReviewBuffer(current_grant=forbidden, sink=forbidden)
    assert not b.record(payload(), trusted_source='client')
    assert not b.drain_one()


@pytest.mark.parametrize('grant', [None, Grant(str(uuid4()), str(uuid4()), purpose='health'),
                                  Grant('account-id', str(uuid4())),
                                  Grant(str(uuid4()), str(uuid4()), version='old')])
def test_refusal_unknown_health_or_ai_permission_cannot_collect(rig, grant):
    b, state, sent = rig
    state['grant'] = grant
    assert not b.record(payload(), trusted_source='client')
    assert not b.drain_one()
    assert sent == []


def test_opt_out_or_authority_failure_clears_pending(rig):
    b, state, sent = rig
    assert b.record(payload(), trusted_source='client')
    state['grant'] = None
    assert not b.drain_one()
    assert not b._pending and not b._seen and sent == []
    b._authority = lambda: (_ for _ in ()).throw(RuntimeError('private'))
    assert not b.record(payload(), trusted_source='client')


@pytest.mark.parametrize('key,value', [
    ('message', 'private'), ('prompt', 'private'), ('email', 'x@example.test'),
    ('photo', 'bytes'), ('weight', 80), ('injury', 'private'),
    ('food', 'private'), ('nutrition', {'calories': 1}), ('token', 'private'),
    ('account_id', 1), ('screen', '/coach?message=private'),
])
def test_sensitive_or_unknown_properties_rejected(rig, key, value):
    b, _, sent = rig
    assert not b.record(payload(properties={key: value}), trusted_source='client')
    assert sent == []


def test_exact_envelope_authority_and_property_types(rig):
    b, _, _ = rig
    p = payload()
    p['subject'] = str(uuid4())
    assert not b.record(p, trusted_source='client')
    assert not b.record(payload('workout_completed', source='server'), trusted_source='client')
    assert not b.record(payload('screen_viewed', properties={'screen': ['coach']}), trusted_source='client')
    assert not b.record(payload('screen_viewed'), trusted_source='client')
    p = payload()
    p['schema_version'] = True
    assert not b.record(p, trusted_source='client')


def test_cross_account_and_new_consent_generation_drop_old_queue(rig):
    b, state, sent = rig
    assert b.record(payload(), trusted_source='client')
    new_grant = Grant(str(uuid4()), str(uuid4()))
    state['grant'] = new_grant
    assert not b.drain_one()
    assert b.record(payload(), trusted_source='client')
    assert b.drain_one()
    assert [s for _, s in sent] == [new_grant.subject]
    assert b.record(payload(), trusted_source='client')
    state['grant'] = Grant(new_grant.subject, str(uuid4()))
    assert not b.drain_one()
    b.clear()  # logout/deletion hook contract
    assert not b._pending and not b._seen


def test_duplicates_do_not_resend_even_after_success(rig):
    b, _, sent = rig
    p = payload()
    assert b.record(p, trusted_source='client')
    assert not b.record(p, trusted_source='client')
    assert b.drain_one()
    assert not b.record(p, trusted_source='client')
    assert len(sent) == 1


def test_network_failure_bounded_retries_same_identity_no_exception(rig):
    b, state, _ = rig
    calls = []
    def down(e, s):
        calls.append(e.event_id)
        raise OSError('never log this response')
    b._sink = down
    p = payload()
    assert b.record(p, trusted_source='client')
    assert not b.drain_one()
    assert not b.drain_one()  # no busy retry
    state['now'] += timedelta(seconds=10)
    assert not b.drain_one()
    state['now'] += timedelta(seconds=30)
    assert not b.drain_one()
    assert not b.drain_one()
    assert calls == [p['event_id']] * 3
    assert not b._pending
    assert not b.record(p, trusted_source='client')


def test_failed_delivery_can_recover_without_new_event_or_time(rig):
    b, state, sent = rig
    calls = []
    def once_down(event, subject):
        calls.append(event)
        if len(calls) == 1:
            raise OSError('offline')
        sent.append((event, subject))
    b._sink = once_down
    assert b.record(payload(), trusted_source='client')
    assert not b.drain_one()
    state['now'] += timedelta(seconds=10)
    assert b.drain_one()
    assert len(sent) == 1 and calls[0] is calls[1]
    assert sent[0][0].occurred_at == NOW


def test_safe_snapshot_minimization_time_and_memory_bounds(rig):
    b, state, sent = rig
    p = payload('screen_viewed', properties={'screen': 'coach'})
    assert b.record(p, trusted_source='client')
    p['properties']['screen'] = 'private'
    assert b.drain_one()
    assert dict(sent[0][0].properties) == {'screen': 'coach'}
    with pytest.raises(TypeError):
        sent[0][0].properties['message'] = 'private'
    for age in [timedelta(days=2), -timedelta(minutes=6)]:
        p = payload()
        p['occurred_at'] = (NOW - age).isoformat()
        assert not b.record(p, trusted_source='client')
    for _ in range(100):
        assert b.record(payload(), trusted_source='client')
    assert not b.record(payload(), trusted_source='client')
    state['now'] += timedelta(days=2)
    assert not b.drain_one()
    assert not b._pending


def test_server_success_only_through_trusted_adapter(rig):
    b, _, sent = rig
    assert b.record(payload('workout_completed', source='server'), trusted_source='server')
    assert b.drain_one()
    assert sent[0][0].source == 'server'


def test_module_not_wired_into_application():
    from pathlib import Path
    for path in Path('app').rglob('*.py'):
        if path.name == 'product_analytics.py':
            continue
        assert 'import product_analytics' not in path.read_text()
        assert 'from app.services.product_analytics' not in path.read_text()
