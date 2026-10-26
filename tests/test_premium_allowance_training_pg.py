"""Unified mode through the original native Training command and SDK door."""
import os
from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.models import AppleSubscription, AiUsageReservation, AiStarterGrant
from app.services.ai_allowances.policy import AllowancePolicy, ScopePolicy, SUPPORTED_FEATURES
from tests.test_training_allowance_zero_attempt_pg import (  # noqa: F401
    chain, pg_generation_app, generate, consent, snap)
from ai_allowance_support import enable_subscriptions, premium_row

pytestmark = pytest.mark.pg_concurrency
if os.environ.get('FITX_PG_CONCURRENCY_TEST') != '1':
    pytest.skip('requires explicitly disposable PostgreSQL', allow_module_level=True)


def unified(chain):
    enable_subscriptions(chain.app, chain.monkeypatch)
    chain.app.config.update(AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED=True,
        AI_ALLOWANCE_POLICY=AllowancePolicy('synthetic-native-all',
            premium=ScopePolicy('WEEKLY',{f:2 for f in SUPPORTED_FEATURES},1)))
    premium_row(chain.owners[0])
    consent(chain.owners[0],chain.policy,'affirmative_grant','unified-native-grant')


def test_unified_native_training_keeps_original_dispatch_and_recovery(chain):
    with chain.app.app_context():
        unified(chain)
        result=generate(chain.owners[0],'unified-native-training-key')
        assert result is not None
        state=snap(chain.owners[0])
        assert state.operations==[('SUCCEEDED',True,True)]
        assert state.bindings==['COMMITTED']
        assert state.plans==1 and state.bucket[:2]==(1,0)
        assert sum(chain.calls.values())==1
        generate(chain.owners[0],'unified-native-training-key')
        assert sum(chain.calls.values())==1
        assert snap(chain.owners[0]).plans==1


def test_unified_native_training_refuses_pending_without_hold_or_sdk(chain):
    from app.services.mobile_training_generation.errors import TrainingAllowanceRefused
    with chain.app.app_context():
        unified(chain)
        row=AppleSubscription.query.one()
        row.last_verified_at=datetime.now(timezone.utc)-timedelta(days=2)
        db.session.commit()
        with pytest.raises(TrainingAllowanceRefused) as error:
            generate(chain.owners[0],'unified-native-pending-key')
        assert error.value.public_code=='AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE'
        assert sum(chain.calls.values())==0
        assert AiUsageReservation.query.count()==AiStarterGrant.query.count()==0


def test_native_twenty_attempt_witness_now_stops_at_four_and_replay_is_terminal(chain):
    import anthropic
    import httpx
    import openai
    from app.services import ai
    from app.services.mobile_training_generation.errors import StoredGenerationFailure
    calls = {'bedrock':0,'openai':0}
    def sdk(provider, **kwargs):
        calls[provider] += 1
        response = httpx.Response(429, request=httpx.Request('POST','https://synthetic.invalid'))
        error = anthropic.RateLimitError if provider == 'bedrock' else openai.RateLimitError
        raise error('synthetic',response=response,body={})
    with chain.app.app_context():
        unified(chain)
        chain.monkeypatch.setattr(ai,'BEDROCK_ENABLED',True)
        chain.monkeypatch.setattr(ai.bedrock_client.messages,'create',lambda **kw:sdk('bedrock',**kw))
        chain.monkeypatch.setattr(ai.openai_client.chat.completions,'create',lambda **kw:sdk('openai',**kw))
        with pytest.raises(StoredGenerationFailure):
            generate(chain.owners[0],'native-aggregate-four')
        assert sum(calls.values()) == 4
        row = AiUsageReservation.query.one()
        assert row.physical_attempts == row.physical_attempt_limit == 4
        assert row.physical_output_reserved == 16000 and row.physical_input_reserved > 0
        assert row.billing_evidence == 'UNKNOWN' and row.provider_billed is None
        assert snap(chain.owners[0]).plans == 0
        with pytest.raises(StoredGenerationFailure):
            generate(chain.owners[0],'native-aggregate-four')
        assert sum(calls.values()) == 4


def test_native_repair_and_canonical_replay_share_the_original_vector(chain):
    from tests.test_training_allowance_zero_attempt_pg import _response
    partial = _response('openai')
    partial.choices[0].finish_reason = 'length'
    partial.choices[0].message.content = '{"program": ['
    from app.services import ai
    outputs = [partial, _response('openai')]
    calls = []
    def sdk(**kwargs):
        calls.append(kwargs)
        return outputs.pop(0)
    with chain.app.app_context():
        unified(chain)
        chain.monkeypatch.setattr(ai.openai_client.chat.completions,'create',sdk)
        generate(chain.owners[0],'native-repair-vector')
        row = AiUsageReservation.query.one()
        assert row.physical_attempts == 2 and row.physical_output_reserved == 11000
        assert [c['max_tokens'] for c in calls] == [4000,7000]
        generate(chain.owners[0],'native-repair-vector')
        assert len(calls) == 2 and snap(chain.owners[0]).plans == 1


def test_native_parallel_workers_and_restart_cannot_refill_budget(chain):
    from app.models import TrainingPlanGenerationOperation as Op
    from app.models import TrainingPlanGenerationAllowance as Bind
    from app.services.mobile_training_generation import store
    from app.services.ai_allowances import physical as F, service as L
    from physical_budget_support import payload
    from tests.test_ai_allowances_pg import race
    with chain.app.app_context():
        unified(chain)
        op = store.claim(chain.owners[0],'native-vector-race','f'*64)
        operation_id = op.id
        store.authorize_dispatch(operation_id)
        reference = Bind.query.one().reservation_ref
    def attempt():
        return F.reserve(chain.owners[0],reference,'training_plan','bedrock',payload('training_plan'),
                         operation_id=operation_id)
    results = race(chain.app,attempt,[()]*8)
    assert results.count(None) == 4
    assert results.count('AI_ACTION_BUDGET_EXHAUSTED') == 4
    with chain.app.app_context():
        assert Op.query.one().provider_attempt_possible is True
        assert AiUsageReservation.query.one().physical_attempts == 4
        db.session.remove()
        with pytest.raises(L.AllowanceError): attempt()
        assert AiUsageReservation.query.one().physical_attempts == 4


def test_native_six_day_heavy_plan_keeps_full_generation_output(chain):
    import copy
    import json
    from contextlib import nullcontext
    from functools import partial
    from types import SimpleNamespace as Obj
    from app.models import User
    from app.services import ai
    from app.services.mobile_training_generation import service, parse_native_request
    from tests.test_mobile_training_generation_pg import _request
    from tests.test_mobile_training_generation_api import _provider_document
    request=parse_native_request(dict(_request().normalized,gun_sayisi=6,sure=90))
    document=_provider_document('ex_barbell_back_squat')
    template=copy.deepcopy(document['program'][0])
    for index,day in enumerate(document['program']):
        if index<6:
            name=day['gun']
            day.update(copy.deepcopy(template));day['gun']=name
            day['sure_dk']=90
            # Varied catalog-backed movements; the heavy fixture is a real
            # structured weekly plan, not repeated copies of one exercise.
            exercise_ids=(('ex_barbell_bench_press','ex_incline_dumbbell_press',
                'ex_dumbbell_shoulder_press','ex_lateral_raise','ex_triceps_pushdown','ex_push_up'),
                ('ex_barbell_row','ex_lat_pulldown','ex_seated_cable_row',
                 'ex_reverse_dumbbell_fly','ex_dumbbell_biceps_curl','ex_hammer_curl'),
                ('ex_barbell_back_squat','ex_barbell_romanian_deadlift','ex_leg_press',
                 'ex_lying_leg_curl','ex_leg_extension','ex_calf_raise'))[index%3]
            day['odak']=('Push','Pull','Legs')[index%3]
            day['egzersizler']=[dict(copy.deepcopy(template['egzersizler'][0]),exercise_id=identity)
                               for identity in exercise_ids]
        else:
            day.update(tip='dinlenme',odak='Recovery',sure_dk=0,tahmini_kalori=0,egzersizler=[])
    document['haftalik_ozet'].update(toplam_antrenman_gun=6,toplam_tahmini_kalori=1800)
    calls=[]
    def sdk(**kw):
        calls.append(kw)
        return Obj(usage=None,choices=[Obj(finish_reason='stop',message=Obj(content=json.dumps(document)))])
    with chain.app.app_context():
        unified(chain)
        chain.monkeypatch.setattr(ai.openai_client.chat.completions,'create',sdk)
        result=service.generate_and_persist(db.session.get(User,chain.owners[0]),request,
            'native-six-day-heavy',chat_fn=partial(ai._heavy_complete,feature='training_plan'),
            provider_guard=nullcontext)
        assert result is not None and snap(chain.owners[0]).plans==1
        row=AiUsageReservation.query.one()
        assert row.physical_attempts==1 and row.physical_output_reserved==4000
        assert calls[0]['max_tokens']==4000



def test_native_original_metered_scope_survives_flag_change(chain):
    from app.models import TrainingPlanGenerationOperation as Op
    from app.services.mobile_training_generation import store
    from app.services.ai_allowances import execution as E
    from physical_budget_support import payload
    with chain.app.app_context():
        unified(chain)
        op=store.claim(chain.owners[0],'native-flag-change','f'*64)
        operation_id=op.id
        store.authorize_dispatch(operation_id)
        chain.app.config.update(AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED=False,
            AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED=False)
        with store.attempt_evidence(operation_id,True):
            E.before_attempt(chain.owners[0],'training_plan',provider='bedrock',payload=payload('training_plan'))
        assert AiUsageReservation.query.one().physical_attempts==1
        assert Op.query.one().provider_attempt_possible is True
