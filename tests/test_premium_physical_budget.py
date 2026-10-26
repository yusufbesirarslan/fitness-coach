"""Synthetic aggregate boundaries; no production policy or real provider."""
import json
from contextlib import nullcontext

import pytest

from app.extensions import db
from app.models import AiUsageReservation as Hold
from app.services import ai_input_budget as B, ai_model_policy as M, ai_provider_call as P
from app.services import ai_consent as C
from app.services.ai_allowances import execution as E, physical as F
from test_premium_allowance_execution import ready, hold, counts  # noqa: F401
from physical_budget_support import payload


def request(size=2000, output=700, provider='bedrock', image=False):
    result = payload('vision' if image else 'coach')
    if provider == 'openai':
        result['model'] = 'gpt-4o-mini'
    result['max_tokens'] = output
    fixed, _ = B.input_upper_bound(result)
    assert size >= fixed
    if image:
        result['messages'][0]['content'].append({'type':'text','text':'x' * (size-fixed-30)})
    else:
        result['messages'][0]['content'] += 'x' * (size-fixed)
    return result


@pytest.fixture
def door(ready, monkeypatch):
    monkeypatch.setattr(C, 'BINDINGS', {f:('synthetic',('synthetic',)) for f in B.FEATURES})
    monkeypatch.setattr(P, 'model_concurrency_slot', lambda *a, **kw: nullcontext())
    monkeypatch.setattr(P.ai_spend_guard, 'charge', lambda *a, **kw: None)
    monkeypatch.setattr(P.time, 'sleep', lambda _: None)
    monkeypatch.setattr(C, 'admission_generation', lambda *a: 1)
    monkeypatch.setattr(C, 'dispatch', lambda *a, **kw: nullcontext())
    return ready


def invoke(owner, feature, p, *, provider='bedrock', sdk=None):
    with P.admit(feature=feature, provider=provider, payload=p) as admission:
        return admission.create(sdk or (lambda **kw: 'complete synthetic answer'))


@pytest.mark.parametrize('family', list(F.LIMITS))
@pytest.mark.parametrize('axis', ['input', 'output', 'images', 'cost', 'attempts'])
def test_exact_last_resource_and_one_more_is_atomic(ready, family, axis):
    from app.services.ai_allowances.errors import AllowanceError
    action = hold(ready, family)
    p = payload('vision' if family == 'pump_check' or (family == 'coach' and axis == 'images') else 'coach')
    physical = E.CONSENT_FEATURE[family]
    inp, images = B.input_upper_bound(p)
    cost = inp * 3000 + 15000
    row = Hold.query.one()
    limits = json.loads(row.physical_budget)
    column, debit = {'input':('physical_input_reserved',inp),
        'output':('physical_output_reserved',1), 'images':('physical_images_reserved',images),
        'cost':('physical_cost_reserved',cost), 'attempts':('physical_attempts',1)}[axis]
    if axis == 'images' and images == 0:
        # This family never authorizes an image. Its zero boundary is exact.
        p['messages'][0]['content'] = payload('vision')['messages'][0]['content']
        with pytest.raises(AllowanceError):
            F.reserve(ready, action.reference, physical, 'bedrock', p)
        assert Hold.query.one().physical_attempts == 0
        return
    setattr(row, column, limits[axis] - debit)
    db.session.commit()
    F.reserve(ready, action.reference, physical, 'bedrock', p)
    db.session.expire_all()
    row = Hold.query.one()
    before = tuple(getattr(row,n) for n in (*F.COUNTERS,'physical_attempts'))
    assert getattr(row, column) == limits[axis]
    with pytest.raises(AllowanceError):
        F.reserve(ready, action.reference, physical, 'bedrock', p)
    assert tuple(getattr(Hold.query.one(), n) for n in (*F.COUNTERS,'physical_attempts')) == before


def test_frozen_payload_not_later_usage_authorizes_call(door):
    action = hold(door, 'nutrition_generation')
    p = request(17000, 2000)
    sent = []
    with E.scope(action):
        with P.admit(feature='nutrition_plan', provider='bedrock', payload=p) as call:
            p['messages'][0]['content'] = 'mutated after admission'
            def sdk(**kw):
                sent.append(kw)
                return type('Response', (), {'usage':{'input_tokens':1,'output_tokens':1}})()
            call.create(sdk)
        with pytest.raises(E.ExecutionRefused):
            invoke(door, 'nutrition_plan', request(17000, 2000), sdk=lambda **kw: sent.append(kw))
    row = Hold.query.one()
    assert len(sent) == row.physical_attempts == 1
    assert row.physical_input_reserved == 17000 and row.physical_output_reserved == 2000
    assert B.input_upper_bound({k:v for k,v in sent[0].items() if k != 'timeout'})[0] == 17000
    action.finish()
    assert Hold.query.one().billing_evidence == 'UNKNOWN'


def test_transport_retries_keep_full_output_and_unknown(door):
    action = hold(door, 'nutrition_generation')
    calls = []
    class Transient(Exception):
        status_code = 500
    def sdk(**kw):
        calls.append(kw)
        raise Transient()
    with E.scope(action), pytest.raises(E.ExecutionRefused):
        invoke(door, 'nutrition_plan', request(12000, 2000, provider='openai'), provider='openai', sdk=sdk)
    action.finish()
    row = Hold.query.one()
    assert len(calls) == row.physical_attempts == 2
    assert row.physical_output_reserved == 4000
    assert row.billing_evidence == 'UNKNOWN'


def test_observer_refusal_is_proven_zero_and_releases_visible_unit(app,door):
    from app.services.ai_allowances.policy import AllowancePolicy, ScopePolicy, SUPPORTED_FEATURES
    app.config['AI_ALLOWANCE_POLICY'] = AllowancePolicy('synthetic-zero-observer',
        premium=ScopePolicy('WEEKLY',{f:2 for f in SUPPORTED_FEATURES},0))
    action = hold(door,'nutrition_generation')
    def refuse(): raise P.AttemptEvidenceUnavailable()
    with E.scope(action), P.attempt_observer(refuse), pytest.raises(P.AttemptEvidenceUnavailable):
        invoke(door,'nutrition_plan',request(10000,2000),sdk=lambda **kw:pytest.fail('SDK reached'))
    action.finish()
    row = Hold.query.one()
    assert row.state == 'RELEASED' and row.physical_attempts == 0
    assert row.billing_evidence == 'CONFIRMED_NOT_BILLED'
    assert counts(door,'nutrition_generation') == (0,0,0)


def test_missing_snapshot_does_not_bootstrap_historical_authority(door):
    action = hold(door, 'coach')
    Hold.query.one().physical_budget = None
    db.session.commit()
    with E.scope(action), pytest.raises(E.ExecutionRefused):
        invoke(door, 'coach', request())
    action.finish()
    assert Hold.query.one().physical_attempts == 0
    assert counts(door,'coach') == (0,0,0)


@pytest.mark.parametrize('kind', ['model', 'cache', 'routing', 'ocr', 'owner'])
def test_unqualified_authority_refuses_without_sdk(door, monkeypatch, kind):
    action = hold(door,'qr_menu' if kind == 'ocr' else 'coach')
    p = request()
    feature, owner = 'coach', door
    if kind == 'model': p['model'] = 'unpriced-model'
    if kind == 'cache': p['messages'][0]['cache_control'] = {'type':'ephemeral'}
    if kind == 'routing': monkeypatch.setattr(M._cfg(), 'AI_MODEL_ROUTING','claude55')
    if kind == 'ocr': feature = 'menu_ocr'
    if kind == 'owner': owner += 1
    with E.scope(action), pytest.raises((E.ExecutionRefused,C.ConsentDenied,P.ai_spend_guard.AISpendLimitExceeded)):
        if kind == 'owner':
            E.before_attempt(owner, feature, provider='bedrock', payload=p)
        invoke(owner, feature, p, sdk=lambda **kw: pytest.fail('SDK reached'))
    assert Hold.query.one().physical_attempts == 0


def test_swallowed_nested_refusal_cannot_publish_success(door):
    action = hold(door,'nutrition_generation')
    with E.scope(action):
        invoke(door,'nutrition_plan',request(20000,2000))
        try:
            invoke(door,'nutrition_plan',request(20000,2000))
        except E.ExecutionRefused:
            pass
        with pytest.raises(E.ExecutionRefused): action.success()
        with pytest.raises(E.ExecutionRefused): E.canonical_within(door)
    action.finish()
    assert Hold.query.one().outcome != 'success'


def test_summary_and_nested_exemption_share_coach_budget(door):
    action = hold(door,'coach')
    with E.scope(action):
        invoke(door,'coach',request(40000))
        with E.exempt_operation('coach_summary'):
            invoke(door,'summary',request(15000,500,provider='openai'),provider='openai')
        with E.exempt_operation('workout_photo_proof'):
            invoke(door,'vision',payload('vision'))
    row = Hold.query.one()
    assert row.physical_attempts == 3 and row.physical_images_reserved == 1
    assert row.physical_input_reserved > 55000


@pytest.mark.parametrize('family,workflow', [
    ('coach',[('coach',40000,700)]*5 + [('summary',15000,500)]),
    ('training_generation',[('training_plan',15000,4000),('training_plan',17000,7000)]),
    ('nutrition_generation',[('nutrition_plan',15000,2000)]*2),
    ('pump_check',[('vision',6000,1200)]*2),
    # Cold 80-item menu: extraction + name normalization + six 15-item macro
    # batches + serving weights + six re-estimation batches, all heavy allowed.
    ('qr_menu',[('menu_extract',50000,5000),('nutrition',7000,2000)]
       + [('nutrition',6500,1275)]*6 + [('nutrition',7000,2000)]
       + [('nutrition',6500,1275)]*6),
])
def test_synthetic_heavy_envelopes_fit_without_reducing_outputs(door, family, workflow):
    action = hold(door,family)
    calls = []
    with E.scope(action):
        for feature,size,out in workflow:
            invoke(door, feature, request(size,out,image=feature=='vision'),
                   sdk=lambda **kw: calls.append(kw) or 'complete')
        action.success()
    action.finish()
    assert len(calls) == Hold.query.one().physical_attempts == len(workflow)
    assert Hold.query.one().outcome == 'success'


def test_current_routing_snapshot_contains_exact_cost_weights(ready):
    row = hold(ready,'coach')
    snapshot = json.loads(Hold.query.one().physical_budget)
    assert snapshot['catalog'] == F.CATALOG
    assert ['bedrock',M.heavy_model(),3000,15000] in snapshot['routes']
    assert snapshot['attempts'] == 8


@pytest.mark.parametrize('routing,heavy,light,heavy_rates,light_rates', [
    ('claude55','global.anthropic.claude-sonnet-5-5','global.anthropic.claude-haiku-5-5',(2000,10000),(100,500)),
    ('claude55','eu.anthropic.claude-sonnet-5-5','eu.anthropic.claude-haiku-5-5',(2200,11000),(110,550)),
])
def test_dark_routing_price_identity_is_snapshotted_without_activation(ready, monkeypatch,
        routing,heavy,light,heavy_rates,light_rates):
    cfg = M._cfg()
    monkeypatch.setattr(cfg,'AI_MODEL_ROUTING',routing)
    monkeypatch.setattr(cfg,'BEDROCK_ENABLED',True)
    monkeypatch.setattr(cfg,'BEDROCK_SONNET55_MODEL',heavy)
    monkeypatch.setattr(cfg,'BEDROCK_HAIKU55_MODEL',light)
    action = hold(ready,'coach')
    snapshot = json.loads(Hold.query.one().physical_budget)
    assert ['bedrock',heavy,*heavy_rates] in snapshot['routes']
    assert ['bedrock',light,*light_rates] in snapshot['routes']
    for model,rates in [(heavy,heavy_rates),(light,light_rates)]:
        p = request()
        p['model'] = model
        F.reserve(ready,action.reference,'coach','bedrock',p)
    row = Hold.query.one()
    assert row.physical_cost_reserved > 0 and row.physical_attempts == 2


def test_budget_error_is_customer_visible_nonretryable_and_not_partial_success(app,door):
    from flask import g
    from types import SimpleNamespace
    @app.post('/synthetic/budget-overflow')
    def overflow():
        g.mobile_user = SimpleNamespace(id=door)
        with E.logical_action(door,'nutrition_generation',{},key='customer-overflow'):
            invoke(door,'nutrition_plan',request(20000,2000))
            try:
                invoke(door,'nutrition_plan',request(20000,2000))
            except E.ExecutionRefused:
                return {'misleading_partial':'not published'}
    response = app.test_client().post('/synthetic/budget-overflow')
    assert response.status_code == 422
    assert response.json['error']['code'] == 'AI_ACTION_BUDGET_EXHAUSTED'
    assert response.json['error']['retryable'] is False
    assert 'misleading_partial' not in response.json


@pytest.mark.parametrize('language', ['en','tr'])
@pytest.mark.parametrize('rounds', [1,5])
def test_real_coach_prompts_and_tool_loop_complete_synthetic_workflow(door,monkeypatch,language,rounds):
    from types import SimpleNamespace as Obj
    from app.services import ai_coach
    action = hold(door,'coach')
    calls = []
    expected = ('Keep the load comfortable and use controlled repetitions.' if language=='en'
                else 'Yükü rahat bir düzeyde tut ve tekrarları kontrollü yap.')
    def sdk(**kw):
        calls.append(kw)
        if len(calls) < rounds:
            return Obj(stop_reason='tool_use',usage=None,content=[Obj(type='tool_use',
                name='get_user_memory',input={},id=f'tool-{len(calls)}')])
        return Obj(stop_reason='end_turn',usage=None,content=[Obj(type='text',text=expected)])
    monkeypatch.setattr(ai_coach,'bedrock_client',Obj(messages=Obj(create=sdk)))
    monkeypatch.setattr(ai_coach,'_dispatch_coach_tool',lambda *a:json.dumps({
        'training':'three controlled sets', 'restriction':'avoid painful movements',
        'history':['completed a moderate workout']*8}))
    monkeypatch.setattr(ai_coach.coach_confirmation,'grounded_provider_reply',lambda uid,lang,text:text)
    question = 'How should I adjust today’s workout?' if language=='en' else 'Bugünkü antrenmanı nasıl düzenlemeliyim?'
    context = ('Goal: build strength. Available equipment: dumbbells. Injury restriction: avoid painful overhead movements.\n'
               if language=='en' else 'Hedef: kuvvet kazanmak. Ekipman: dambıl. Kısıt: ağrılı baş üstü hareketlerden kaçın.\n')
    if rounds == 5:
        context += context * 60
    history = [{'role': 'user' if n%2==0 else 'assistant','content':question*3} for n in range(6)]
    with E.scope(action):
        result = ai_coach._run_coach_conversation_bedrock(door,question,context,history,language)
        assert result == expected
        assert all('overhead' in c['system'] or 'baş üstü' in c['system'] for c in calls)
        action.success()
    action.finish()
    assert Hold.query.one().physical_attempts == rounds
    assert Hold.query.one().physical_input_reserved <= 256000
    assert all(c['max_tokens'] == 700 for c in calls)


@pytest.mark.parametrize('language', ['en','tr'])
def test_real_nutrition_prompts_parse_complete_synthetic_options(door,monkeypatch,language):
    from types import SimpleNamespace as Obj
    from app.services import ai, nutrition_plan_generation as N
    plans = [{'ad': f'Option {p+1}', 'ogunler': [
        {'ad':meal,'yiyecekler':'grilled chicken, rice and vegetables','kalori':650,
         'protein':45,'karbonhidrat':70,'yag':20} for meal in ['Breakfast','Lunch','Dinner']]}
        for p in range(3)]
    calls = []
    def sdk(**kw):
        calls.append(kw)
        return Obj(stop_reason='end_turn',usage=None,
            content=[Obj(type='text',text=json.dumps({'planlar':plans}))])
    monkeypatch.setattr(ai,'BEDROCK_ENABLED',True)
    monkeypatch.setattr(ai,'bedrock_client',Obj(messages=Obj(create=sdk)))
    with E.logical_action(door,'nutrition_generation',{},key='nutrition-real-prompts') as action:
        for _ in range(2):  # initial output and a bounded correction/repair
            result = N.generate_plan_options(ai._heavy_chat,language,2200,'kas kazanma',
                ['chicken','lentils'],['rice','oats'],['olive oil'],['yogurt'])
            assert len(result)==3 and all(len(p['ogunler'])==3 for p in result)
        action.success()
    assert [c['max_tokens'] for c in calls] == [2000,2000]
    assert Hold.query.one().physical_attempts == 2


@pytest.mark.parametrize('language', ['en','tr'])
@pytest.mark.parametrize('edge', [512,3000])
def test_real_pump_image_preparation_and_schema_complete(door,monkeypatch,language,edge):
    import io
    from PIL import Image
    from types import SimpleNamespace as Obj
    from app.services import ai
    from app.services.mobile_pump_checks.analysis import analyze_image
    buf = io.BytesIO()
    Image.new('RGB',(edge,edge),(140,125,110)).save(buf,format='JPEG')
    output = {'summary':'Visible pose supports a limited visual check.',
        'observations':['Lighting is even.'],'strengths':['The subject is visible.'],
        'focus_areas':['Use the same camera angle next time.'],
        'limitations':['A photograph cannot measure progress.'],
        'next_check_guidance':'Keep lighting consistent.','quality':'limited'}
    calls = []
    def sdk(**kw):
        calls.append(kw)
        return Obj(stop_reason='end_turn',usage=None,content=[Obj(type='text',text=json.dumps(output))])
    monkeypatch.setattr(ai,'bedrock_client',Obj(messages=Obj(create=sdk)))
    with E.logical_action(door,'pump_check',{},key='pump-real-preparation') as action:
        for _ in range(2):
            result = analyze_image(buf.getvalue(),'image/jpeg',{'body_region':'upper_body'},language=language)
            assert result == output
        action.success()
    row=Hold.query.one()
    assert row.physical_attempts == row.physical_images_reserved == 2
    assert row.physical_output_reserved == 2400
    assert all(c['max_tokens']==1200 for c in calls)


def test_real_coach_provider_fallback_shares_one_action(door,monkeypatch):
    from types import SimpleNamespace as Obj
    from app.services import ai_coach as coach
    calls=[]
    def unavailable(**kw):
        calls.append(('bedrock',kw))
        raise ValueError('synthetic unavailable first provider')
    def available(**kw):
        calls.append(('openai',kw))
        return Obj(usage=None,choices=[Obj(finish_reason='stop',
            message=Obj(content='Keep the load comfortable and progress gradually.',tool_calls=None))])
    monkeypatch.setattr(coach,'BEDROCK_ENABLED',True)
    monkeypatch.setattr(coach,'bedrock_client',Obj(messages=Obj(create=unavailable)))
    monkeypatch.setattr(coach,'openai_client',Obj(chat=Obj(completions=Obj(create=available))))
    monkeypatch.setattr(coach.coach_confirmation,'resolve_pending_turn',lambda *a:None)
    monkeypatch.setattr(coach.coach_confirmation,'grounded_provider_reply',lambda uid,lang,text:text)
    with E.logical_action(door,'coach',{},key='coach-provider-fallback') as action:
        answer=coach._run_coach_conversation(door,'How should I progress?',
            'Goal: strength. Restriction: avoid painful movements.',client_history=[],language='en')
        assert answer == 'Keep the load comfortable and progress gradually.'
        action.success()
    row=Hold.query.one()
    assert [c[0] for c in calls]==['bedrock','openai']
    assert row.physical_attempts==2 and row.physical_output_reserved==1400


def test_queued_worker_cannot_start_after_terminal_sibling_refusal(ready):
    action=hold(ready,'coach')
    action.refusal=E.ExecutionRefused('AI_ACTION_BUDGET_EXHAUSTED')
    with E.scope(action), pytest.raises(E.ExecutionRefused):
        E.before_attempt(ready,'coach',provider='bedrock',payload=payload('coach'))
    assert Hold.query.one().physical_attempts==0



def test_future_haiku_long_context_refuses_even_if_per_call_config_is_enlarged(ready,monkeypatch):
    from app import config
    from app.services.ai_allowances.errors import AllowanceError
    monkeypatch.setattr(config,'AI_MODEL_ROUTING','claude55')
    monkeypatch.setattr(config,'BEDROCK_ENABLED',True)
    monkeypatch.setattr(config,'BEDROCK_SONNET55_MODEL','global.anthropic.claude-sonnet-5-5')
    monkeypatch.setattr(config,'BEDROCK_HAIKU55_MODEL','global.anthropic.claude-haiku-5-5')
    action=hold(ready,'coach')
    p=request(110000,700)
    p['model']='global.anthropic.claude-haiku-5-5'
    with pytest.raises(AllowanceError):F.reserve(ready,action.reference,'coach','bedrock',p)
    assert Hold.query.one().physical_attempts==0



def test_nested_consent_refusal_cannot_be_swallowed_into_partial_success(door,monkeypatch):
    action=hold(door,'qr_menu')
    def denied(*a,**kw):raise C.ConsentDenied('AI_CONSENT_AUTHORITY_UNAVAILABLE',503)
    monkeypatch.setattr(C,'dispatch',denied)
    sdk=[]
    with E.scope(action):
        with pytest.raises(C.ConsentDenied):
            invoke(door,'nutrition',request(2000,1000),sdk=lambda **kw:sdk.append(kw))
        with pytest.raises(E.ExecutionRefused):action.success()
    action.finish()
    assert not sdk and Hold.query.one().physical_attempts==0
    assert Hold.query.one().billing_evidence=='CONFIRMED_NOT_BILLED'
