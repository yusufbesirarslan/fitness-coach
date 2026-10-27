"""Logical five-family enforcement; fake consent/provider, no activation.

PostgreSQL proves serialization separately; these tests deliberately replace
D1 decision points and do not constitute disclosure or transport approval.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from physical_budget_support import before_attempt as physical_attempt
import sqlalchemy as sa

from ai_allowance_support import enable_subscriptions, premium_row
from app.extensions import db
from app.models import AiAllowanceBucket, AiStarterGrant, AiUsageReservation, User
from app.services import ai_consent, ai_spend_guard
from app.services.ai_allowances import service as L
from app.services.ai_allowances import execution as E
from app.services.ai_allowances.policy import AllowancePolicy, ScopePolicy, SUPPORTED_FEATURES


@pytest.fixture
def ready(app, make_user, monkeypatch):
    user = make_user('finite-premium')
    owner = user.id
    app.config.update(AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED=True,
        AI_ALLOWANCE_LEDGER_ENABLED=True, AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED=True,
        AI_ALLOWANCE_POLICY=AllowancePolicy('test-finite',
            starter=ScopePolicy('NEVER', {f: 2 for f in SUPPORTED_FEATURES}, 1),
            starter_eligible_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
            premium=ScopePolicy('WEEKLY', {f: 2 for f in SUPPORTED_FEATURES}, 1)))
    enable_subscriptions(app, monkeypatch)
    premium_row(owner)
    monkeypatch.setattr(ai_consent, 'begin_operation', lambda uid, f, *route:
        ai_consent.OperationAuthority(uid, f, 1, 'synthetic'))
    monkeypatch.setattr(ai_consent, 'authorize_operation', lambda authority: authority)
    monkeypatch.setattr(ai_consent, 'fence', lambda *args: None)
    return owner


def counts(owner, feature):
    b = AiAllowanceBucket.query.filter_by(user_id=owner, feature=feature).one()
    return b.used, b.reserved, b.billed_releases


def hold(owner, family, key='logical-key-0001'):
    a = E.prepare(owner, family, {'command': 'same'}, key=key)
    a.start()
    return a


@pytest.mark.parametrize('family', SUPPORTED_FEATURES)
def test_first_last_exhausted_and_cross_feature_isolation(ready, family):
    owner = ready
    for n in range(2):
        with E.logical_action(owner, family, {'n': n}, key=f'new-action-key-{n}') as action:
            E.check_provider(owner, next(iter(E.PHYSICAL_FEATURES[family])))
            action.success()  # Includes last-good/cache result without an SDK.
    assert counts(owner, family) == (2, 0, 0)
    with pytest.raises(E.ExecutionRefused) as refused:
        hold(owner, family, 'exhausted-key')
    assert refused.value.code == 'AI_ALLOWANCE_EXHAUSTED'
    assert {b.feature for b in AiAllowanceBucket.query.all()} == {family}
    assert AiUsageReservation.query.count() == 2


@pytest.mark.parametrize('family', SUPPORTED_FEATURES)
def test_same_key_in_progress_terminal_and_changed_command_never_execute(ready, family):
    action = hold(ready, family)
    for command, code in [({'command': 'same'}, 'AI_ALLOWANCE_IN_PROGRESS'),
                          ({'command': 'changed'}, 'AI_ALLOWANCE_IDEMPOTENCY_CONFLICT')]:
        with pytest.raises(E.ExecutionRefused) as denied:
            E.prepare(ready, family, command, key='logical-key-0001').start()
        assert denied.value.code == code
    action.finish()  # Atomically sealed zero physical attempts; release once.
    assert counts(ready, family) == (0, 0, 0)
    with pytest.raises(E.ExecutionRefused) as denied:
        hold(ready, family)
    assert denied.value.code == 'AI_ALLOWANCE_RESERVATION_CONFLICT'
    assert AiUsageReservation.query.count() == 1


@pytest.mark.parametrize('family', SUPPORTED_FEATURES)
def test_unknown_failures_are_bounded_and_retry_is_one_visible_unit(ready, family):
    physical = next(iter(E.PHYSICAL_FEATURES[family]))
    for n in range(3):
        with E.logical_action(ready, family, {'n': n}, key=f'failed-action-{n}') as action:
            for _ in range(2):
                E.check_provider(ready, physical)
                physical_attempt(ready, physical)
    assert counts(ready, family) == (2, 0, 1)
    rows = AiUsageReservation.query.order_by(AiUsageReservation.id).all()
    assert [r.state for r in rows] == ['RELEASED', 'COMMITTED', 'COMMITTED']
    assert all(r.provider_billed is None and r.billing_evidence == 'UNKNOWN' for r in rows)
    assert all(r.physical_attempts == 2 for r in rows)
    with pytest.raises(E.ExecutionRefused):
        hold(ready, family, 'failure-budget-exhausted')


@pytest.mark.parametrize('family', SUPPORTED_FEATURES)
def test_pre_provider_refusal_and_crash_expiry(ready, family):
    action = hold(ready, family)
    action.finish()
    row = AiUsageReservation.query.one()
    assert (row.state, row.billing_evidence, row.provider_billed) == ('RELEASED', 'CONFIRMED_NOT_BILLED', False)
    crash = hold(ready, family, 'crashed-operation')
    expired = row.expires_at.replace(tzinfo=timezone.utc) + timedelta(minutes=20)
    L.expire_abandoned(now=expired)
    assert counts(ready, family) == (1, 0, 0)
    with pytest.raises(L.AllowanceError):
        L.record_dispatch_attempt(ready, crash.reference, now=expired)
    assert L.finalize_dispatch(ready, crash.reference).state == 'EXPIRED'


@pytest.mark.parametrize('family', SUPPORTED_FEATURES)
def test_atomic_dispatch_rollback_cannot_leave_a_hold(ready, family):
    action = E.prepare(ready, family, {'x': 1}, key='atomic-rollback-key')
    action.bind_within()
    db.session.rollback()  # durable lease and hold both lose authorization
    assert AiUsageReservation.query.count() == 0
    assert all(b.reserved == 0 for b in AiAllowanceBucket.query.all())
    with pytest.raises(L.AllowanceError):
        L.record_dispatch_attempt(ready, action.reference)


@pytest.mark.parametrize('family', SUPPORTED_FEATURES)
def test_owner_and_family_crossover_refused(ready, make_user, family):
    action = hold(ready, family)
    other = make_user('other-owner').id
    physical = next(iter(E.PHYSICAL_FEATURES[family]))
    with E.scope(action):
        with pytest.raises((E.ExecutionRefused, ai_consent.ConsentDenied)):
            E.check_provider(other, physical)
    with pytest.raises(L.AllowanceError):
        L.record_dispatch_attempt(other, action.reference)
    with pytest.raises(L.AllowanceError):
        L.finalize_dispatch(other, action.reference, successful=True)
    assert counts(ready, family) == (0, 1, 0)


def test_premium_boolean_cannot_bypass_and_starter_is_once(ready, make_user):
    owner = make_user('legacy-premium', is_premium=True)
    owner.created_at = datetime(2026, 10, 1)
    uid = owner.id
    db.session.commit()
    for n in range(2):
        with E.logical_action(uid, 'pump_check', {'n': n}, key=f'starter-action-{n}') as a:
            a.success()
    assert AiStarterGrant.query.filter_by(user_id=uid).count() == 1
    assert AiAllowanceBucket.query.filter_by(user_id=uid, scope='PREMIUM').count() == 0
    with pytest.raises(E.ExecutionRefused) as error:
        hold(uid, 'pump_check', 'starter-exhausted')
    assert error.value.code == 'AI_ALLOWANCE_EXHAUSTED'
    assert L.grant_starter(uid) == 'ALREADY_GRANTED'


def test_consent_denial_precedes_reservation(ready, monkeypatch):
    def deny(*args):
        raise ai_consent.ConsentDenied()
    monkeypatch.setattr(ai_consent, 'begin_operation', deny)
    with pytest.raises(ai_consent.ConsentDenied):
        hold(ready, 'coach')
    assert AiUsageReservation.query.count() == 0


@pytest.mark.parametrize('before_attempt', [False, True])
def test_flag_rollback_never_disables_active_operation_evidence(app, ready, before_attempt):
    action = hold(ready, 'coach')
    with E.scope(action):
        if before_attempt:
            physical_attempt(ready, 'coach')
        app.config[E.FLAG] = False
        for guard in [E.check_provider, physical_attempt]:
            with pytest.raises(E.ExecutionRefused):
                guard(ready, 'coach')
    action.finish()
    row = AiUsageReservation.query.one()
    assert row.physical_attempts == int(before_attempt)
    assert row.billing_evidence == ('UNKNOWN' if before_attempt else 'CONFIRMED_NOT_BILLED')


def test_canonical_commit_lost_response_is_never_free(ready):
    action = hold(ready, 'coach')
    with E.scope(action):
        E.canonical_within(ready)
        db.session.commit()  # canonical domain transaction committed
    # Process lost the acknowledgement and has no Python success flag.
    assert not action.successful
    action.finish()
    assert counts(ready, 'coach') == (1, 0, 0)
    assert AiUsageReservation.query.one().outcome == 'success'


def test_finalization_seals_zero_attempt_against_late_producer(ready):
    action = hold(ready, 'coach')
    action.finish()
    with pytest.raises(L.AllowanceError):
        L.record_dispatch_attempt(ready, action.reference)
    assert AiUsageReservation.query.one().physical_attempts == 0


def test_physical_tool_budget_cannot_expand_under_one_unit(ready):
    action = hold(ready, 'qr_menu')
    with E.scope(action):
        for _ in range(E.ATTEMPT_LIMITS['qr_menu']):
            physical_attempt(ready, 'nutrition')
        with pytest.raises(E.ExecutionRefused):
            physical_attempt(ready, 'nutrition')
    action.finish()
    row = AiUsageReservation.query.one()
    assert row.physical_attempts == E.ATTEMPT_LIMITS['qr_menu']
    assert row.billing_evidence == 'UNKNOWN'


def test_scope_lost_worker_and_undecided_paths_fail_closed(ready):
    for feature in ['coach', 'nutrition', 'vision', 'menu_ocr', 'training_plan']:
        with pytest.raises(E.ExecutionRefused) as denied:
            E.check_provider(ready, feature)
        assert denied.value.code == 'AI_FEATURE_NOT_IN_PLAN'
    for family in ['training_replacement', 'pump_comparison', 'legacy_web_ai']:
        with pytest.raises(E.ExecutionRefused) as denied:
            E.prepare(ready, family, {}, key='undecided-key')
        assert denied.value.code == 'AI_FEATURE_NOT_IN_PLAN'
    assert AiUsageReservation.query.count() == 0


def test_candidate_extension_mapping_is_explicit_and_no_default(app, ready):
    app.config['AI_ALLOWANCE_APPROVED_EXTENSIONS'] = {
        'pump_comparison': 'pump_check', 'training_replacement': 'training_generation'}
    for extension, family in app.config['AI_ALLOWANCE_APPROVED_EXTENSIONS'].items():
        action = E.prepare(ready, extension, {}, key=f'candidate-{extension}')
        assert action.feature == family
    assert AiUsageReservation.query.count() == 0


def test_secret_keyed_fingerprint_is_semantic_and_owner_scoped(app, ready):
    a = E.fingerprint(ready, 'pump_check', {'image': b'private', 'a': 1})
    assert a == E.fingerprint(ready, 'pump_check', {'a': 1, 'image': b'private'})
    assert a != E.fingerprint(ready+1, 'pump_check', {'image': b'private', 'a': 1})
    app.config['SECRET_KEY'] += 'rotated'
    assert a != E.fingerprint(ready, 'pump_check', {'image': b'private', 'a': 1})


def test_unavailable_entitlement_never_falls_to_starter(ready, monkeypatch):
    from app.services import subscriptions
    from app.services.apple_subscriptions import SubscriptionError
    def unavailable(*args):
        raise SubscriptionError('SUBSCRIPTION_TEMPORARILY_UNAVAILABLE', 503)
    monkeypatch.setattr(subscriptions, 'entitlement', unavailable)
    with pytest.raises(E.ExecutionRefused) as denied:
        hold(ready, 'coach')
    assert denied.value.code == 'AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE'
    assert AiUsageReservation.query.count() == AiStarterGrant.query.count() == 0


@pytest.mark.parametrize('native', [False, True])
def test_pending_entitlement_never_spends_starter(ready, native):
    from app.models import AppleSubscription
    row = AppleSubscription.query.filter_by(user_id=ready).one()
    row.last_verified_at = datetime.now(timezone.utc) - timedelta(days=2)
    db.session.commit()
    with pytest.raises(L.AllowanceError) as error:
        if native:
            L.reserve_within(ready, 'training_generation', 'pending-native-key', strict_entitlement=True)
        else:
            L.reserve_dispatch_within(ready, 'coach', 'pending-generic-key', 'a'*64, 30)
    assert error.value.code == 'AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE'
    db.session.rollback()
    assert AiUsageReservation.query.count() == AiStarterGrant.query.count() == 0


def test_native_training_uses_strict_entitlement_only_in_new_mode(app, ready, monkeypatch):
    from app.services.mobile_training_generation import allowance
    calls = []
    def refuse(*args, **kwargs):
        calls.append(kwargs.get('strict_entitlement', False))
        raise L.AllowanceError('AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE')
    monkeypatch.setattr(allowance.ai_allowances, 'reserve_within', refuse)
    operation = SimpleNamespace(allowance_metered=True, user_id=ready)
    for value in [True, False]:
        app.config[E.FLAG] = value
        with pytest.raises(allowance.TrainingAllowanceRefused):
            allowance.bind_for_dispatch(operation, datetime.now(timezone.utc))
    assert calls == [True, False]


def test_account_switch_cannot_hide_inside_attribution_scope(app, ready):
    from flask import g
    with app.test_request_context('/', headers={'Idempotency-Key':'account-switch-key'}):
        g.mobile_user = SimpleNamespace(id=ready)
        action = hold(ready, 'coach', 'account-switch-key')
        with E.scope(action):
            g.mobile_user = SimpleNamespace(id=ready+1)
            for operation in [action.success, lambda: E.canonical_within(ready),
                              lambda: physical_attempt(ready, 'coach')]:
                with pytest.raises(ai_consent.ConsentDenied):
                    operation()
        action.finish()
        assert AiUsageReservation.query.one().billing_evidence == 'CONFIRMED_NOT_BILLED'


def test_typed_refusal_recovers_swallowed_legacy_errors(app, ready):
    from flask import g
    # Request routing only; D1 registry stays closed and no provider is called.
    @app.post('/synthetic/allowance-error')
    def route():
        g.mobile_user = SimpleNamespace(id=ready)
        try:
            E._refuse('AI_ALLOWANCE_EXHAUSTED')
        except Exception:
            return {'legacy_fallback': True}
    response = app.test_client().post('/synthetic/allowance-error')
    assert response.status_code == 402
    assert response.json['error']['code'] == 'AI_ALLOWANCE_EXHAUSTED'
    assert response.json['error']['retryable'] is False
    assert response.headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('family', SUPPORTED_FEATURES)
@pytest.mark.parametrize('outcome', ['provider_timeout','invalid_output','cancelled_before_delivery'])
def test_failure_outcome_does_not_invent_billing_evidence(ready, family, outcome):
    action = hold(ready, family, 'logical-failure-outcome')
    with E.scope(action):
        physical_attempt(ready, next(iter(E.PHYSICAL_FEATURES[family])))
        E.failure(outcome)
    action.finish()
    row = AiUsageReservation.query.one()
    assert (row.outcome,row.billing_evidence,row.provider_billed)==(outcome,'UNKNOWN',None)


@pytest.mark.parametrize('comparison', [False, True])
def test_lost_pump_lease_cannot_commit_false_canonical_success(ready, comparison):
    action = hold(ready,'pump_check','lost-pump-lease-key')
    with E.scope(action):
        if comparison:
            from app.services.mobile_pump_check_comparisons import service
            assert service._finalize_success(9999,ready,1,'comparable',{}) is False
        else:
            from app.services.mobile_pump_checks import service
            assert service._finalize_success(9999,ready,1,{}) is False
    action.finish()
    row = AiUsageReservation.query.one()
    assert row.canonical_committed is False
    assert row.state == 'RELEASED'
