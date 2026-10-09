"""Native replacement generation and closed HTTP contract qualification."""
import json
from contextlib import nullcontext
from datetime import datetime, timedelta
from types import SimpleNamespace
import pytest
from sqlalchemy import event
from app.extensions import db
from app.models import (TrainingPlan, UserSession, TrainingPlanReplacementGenerationOperation as Operation,
                        TrainingPlanReplacementProposal as Proposal)
from app.services.training_plan_replacement import generation as gen, service as authority
from app.services.mobile_training_generation.contract import parse_native_request
from app.services.mobile_training_generation.errors import GenerationInProgress, IdempotencyConflict, StoredGenerationFailure
from app.services.training_generation.output_errors import GenerationUnavailableError
from test_mobile_training_generation_api import CANONICAL, _document, mobile_user, as_mobile

GENERATE='/api/v1/training/plans/replacement-proposals'
CONFIRM='/api/v1/training/plans/replacement/confirm'


@pytest.fixture
def replacement(mobile_user):
    plan=TrainingPlan(user_id=mobile_user.id,plan_data=json.dumps(_document()),score=8)
    db.session.add(plan);db.session.commit()
    return mobile_user


@pytest.fixture
def provider(monkeypatch):
    calls=[]
    def candidate(*args, **kwargs):
        assert not db.session().in_transaction(), 'provider inside transaction'
        assert kwargs['frozen_features'].weekly_frequency >= 3
        calls.append(kwargs)
        return SimpleNamespace(document=_document(),overall_score=8.5)
    monkeypatch.setattr(gen,'generate_training_plan_candidate',candidate)
    return calls


def run(user, key='generation-key-1', body=None):
    return gen.generate_proposal(user,parse_native_request(body or CANONICAL),key,
                                chat_fn=lambda:None,provider_guard=nullcontext)



def successful(call):
    try:
        result=call()
    except Exception as error:
        result=error
    assert not isinstance(result,Exception), 'expected successful operation'
    return result

def expire(row):
    row.lease_expires_at=datetime.utcnow()-timedelta(seconds=1);db.session.commit()


def test_generation_replay_conflict_and_no_plan_write(replacement,provider):
    original=TrainingPlan.query.one().lineage_id
    uid=replacement.id
    first=run(replacement);db.session.remove()
    from app.models import User
    user=db.session.get(User,uid)
    replay=successful(lambda:run(user))
    assert replay.replayed and replay.proposal_token==first.proposal_token and replay.review==first.review
    assert len(provider)==1 and Proposal.query.count()==Operation.query.count()==1
    assert TrainingPlan.query.one().lineage_id==original
    with pytest.raises(IdempotencyConflict):run(user,body={**CANONICAL,'sure':60})
    assert len(provider)==1
    row=Operation.query.one()
    assert row.key_digest!='generation-key-1' and len(row.key_digest)==64
    assert row.generation_context is None and row.lease_token is None


def test_claim_precedes_provider_and_frozen_recovery(replacement,provider):
    admitted=gen.claim(replacement,parse_native_request(CANONICAL),'generation-key-1')
    row=Operation.query.one(); frozen=row.generation_context
    assert row.status=='IN_PROGRESS' and Proposal.query.count()==0 and not provider
    with pytest.raises(GenerationInProgress):run(replacement)
    with pytest.raises(GenerationInProgress):run(replacement,'generation-key-2')
    replacement.weight=110;db.session.commit();expire(row)
    result=successful(lambda:run(replacement))
    assert result.proposal_token and len(provider)==1
    assert provider[0]['frozen_features'].weight==json.loads(frozen)['features']['weight']
    assert Operation.query.one().attempt_count==2


def test_late_attempt_fenced_and_recovery_bounded(replacement):
    first=gen.claim(replacement,parse_native_request(CANONICAL),'generation-key-1')
    expire(Operation.query.one())
    second=gen.claim(replacement,parse_native_request(CANONICAL),'generation-key-1')
    with pytest.raises(GenerationInProgress):gen.publish(first[0],first[1],_document(),8)
    with pytest.raises(GenerationInProgress):gen.fail(first[0],first[1],'TRAINING_PLAN_GENERATION_UNAVAILABLE',503)
    assert Proposal.query.count()==0 and Operation.query.one().lease_token==second[1]
    expire(Operation.query.one())
    try:
        gen.claim(replacement,parse_native_request(CANONICAL),'generation-key-1')
        error=None
    except Exception as caught:error=caught
    assert isinstance(error,StoredGenerationFailure)
    assert error.public_code=='TRAINING_PLAN_GENERATION_RECOVERY_EXHAUSTED'
    assert Operation.query.one().status=='FAILED'
    with pytest.raises(StoredGenerationFailure):run(replacement)


@pytest.mark.parametrize('dimension',['lineage_id','mutation_version','plan_data'])
def test_publish_checks_full_binding(replacement,dimension):
    admitted=gen.claim(replacement,parse_native_request(CANONICAL),'generation-key-1')
    plan=TrainingPlan.query.one()
    setattr(plan,dimension,{'lineage_id':'other-lineage','mutation_version':1,'plan_data':plan.plan_data+' '}[dimension]);db.session.commit()
    with pytest.raises(StoredGenerationFailure) as e:gen.publish(*admitted[:2],_document(),8)
    assert e.value.public_code=='TRAINING_PLAN_STALE_CURRENT_PLAN'
    assert Proposal.query.count()==0 and Operation.query.one().status=='FAILED'


@pytest.mark.parametrize('fault',['proposal','operation','commit'])
def test_publication_rolls_back_atomically(replacement,monkeypatch,fault):
    admitted=gen.claim(replacement,parse_native_request(CANONICAL),'generation-key-1')
    def fail(*args):raise RuntimeError('injected persistence fault')
    target={'proposal':Proposal,'operation':Operation}.get(fault)
    listener='before_insert' if fault=='proposal' else 'before_update'
    if target:event.listen(target,listener,fail)
    else:event.listen(db.session(),'before_commit',fail)
    try:
        with pytest.raises(RuntimeError):gen.publish(*admitted[:2],_document(),8)
    finally:
        if target:event.remove(target,listener,fail)
        else:event.remove(db.session(),'before_commit',fail)
    assert Proposal.query.count()==0 and Operation.query.one().status=='IN_PROGRESS'
    db.session.remove()
    result=gen.publish(*admitted[:2],_document(),8)
    assert result.proposal_token and Operation.query.one().status=='SUCCEEDED'


def test_quota_once_recovery_and_failure_refund(replacement,app,monkeypatch):
    app.config['AI_PLAN_QUOTA_ENABLED']=True
    admitted=gen.claim(replacement,parse_native_request(CANONICAL),'generation-key-1')
    row=Operation.query.one()
    assert row.quota_reserved and replacement.user_metadata['ai_plan_quota']['training']==1
    expire(row)
    second=gen.claim(replacement,parse_native_request(CANONICAL),'generation-key-1')
    assert replacement.user_metadata['ai_plan_quota']['training']==1
    gen.fail(*second[:2],'TRAINING_PLAN_GENERATION_UNAVAILABLE',503)
    db.session.refresh(replacement)
    assert replacement.user_metadata['ai_plan_quota']['training']==0
    with pytest.raises(StoredGenerationFailure):run(replacement)
    assert replacement.user_metadata['ai_plan_quota']['training']==0


def test_candidate_projection_bounds_and_no_execution(replacement,provider):
    result=run(replacement)
    assert set(result.review)=={'score','days'}
    assert all('workout_ref' not in day for day in result.review['days'])
    with pytest.raises(Exception):gen.review_candidate(_document(33),8)
    invalid=_document();invalid['program'][0]['egzersizler'][0]['exercise_id']='unowned'
    with pytest.raises(Exception):gen.review_candidate(invalid,8)


def test_http_success_confirm_replay_and_zero_provider(client,replacement,as_mobile,provider):
    headers=as_mobile(replacement,'generation-key-1')
    first=client.post(GENERATE,json=CANONICAL,headers=headers)
    assert first.status_code==201 and first.headers['Cache-Control']=='no-store'
    assert set(first.json)=={'contract_version','proposal_token','expires_at','candidate'}
    again=client.post(GENERATE,json=CANONICAL,headers=headers)
    assert again.json==first.json and again.headers['Idempotency-Replayed']=='true' and len(provider)==1
    body={'proposal_token':first.json['proposal_token'],'confirmed':True}
    accepted=client.post(CONFIRM,json=body,headers=as_mobile(replacement,'confirm-key-1'))
    assert accepted.status_code==200
    assert accepted.json=={'contract_version':1,'outcome':'applied','reread_required':True}
    replay=client.post(CONFIRM,json=body,headers=as_mobile(replacement,'confirm-key-1'))
    assert replay.json==accepted.json and replay.headers['Idempotency-Replayed']=='true'
    assert len(provider)==1 and TrainingPlan.query.one().mutation_version==0


@pytest.mark.parametrize('route',[GENERATE,CONFIRM])
def test_http_bearer_cookie_refused(client,replacement,route):
    with client.session_transaction() as session:session['_user_id']=str(replacement.id);session['_fresh']=True
    response=client.post(route,json=CANONICAL,headers={'Idempotency-Key':'generation-key-1'})
    assert response.status_code==401 and response.headers['Cache-Control']=='no-store'


@pytest.mark.parametrize('body',[{},None,{'confirmed':1,'proposal_token':'x'*43},{'confirmed':True,'proposal_token':'x'*43,'user_id':1}, {'confirmed':False,'proposal_token':'x'*43}])
def test_confirm_strict_schema(client,replacement,as_mobile,body):
    response=client.post(CONFIRM,json=body,headers=as_mobile(replacement,'confirm-key-1'))
    assert response.status_code==422 and response.json['error']['code']=='TRAINING_PLAN_INVALID_REQUEST'
    assert set(response.json['error'])=={'code','message','retryable','request_id'}


@pytest.mark.parametrize('body,key,status',[(CANONICAL,None,400),({**CANONICAL,'user_id':1},'generation-key-1',422),({**CANONICAL,'sure':'45'},'generation-key-1',422)])
def test_generation_strict_schema(client,replacement,as_mobile,provider,body,key,status):
    response=client.post(GENERATE,json=body,headers=as_mobile(replacement,key))
    assert response.status_code==status and not provider and Operation.query.count()==0


def test_cross_owner_proposal_indistinguishable(client,replacement,as_mobile,provider,make_user):
    result=run(replacement);other=make_user('other-replacement')
    wrong=client.post(CONFIRM,json={'proposal_token':result.proposal_token,'confirmed':True},headers=as_mobile(other,'confirm-key-1'))
    missing=client.post(CONFIRM,json={'proposal_token':'x'*43,'confirmed':True},headers=as_mobile(other,'confirm-key-1'))
    assert wrong.status_code==missing.status_code==404 and wrong.json['error']['code']==missing.json['error']['code']
    assert len(provider)==1


def test_expired_lease_cannot_publish_without_takeover(replacement):
    admitted=gen.claim(replacement,parse_native_request(CANONICAL),'generation-key-1')
    expire(Operation.query.one())
    with pytest.raises(GenerationInProgress):gen.publish(*admitted[:2],_document(),8)
    assert Proposal.query.count()==0


def test_superseded_expired_key_is_terminal(replacement,provider):
    admitted=gen.claim(replacement,parse_native_request(CANONICAL),'generation-key-1')
    expire(Operation.query.one())
    result=run(replacement,'generation-key-2')
    with pytest.raises(StoredGenerationFailure) as error:run(replacement)
    assert error.value.public_code=='TRAINING_PLAN_GENERATION_SUPERSEDED'
    with pytest.raises(GenerationInProgress):gen.publish(*admitted[:2],_document(),8)
    assert len(provider)==1 and Proposal.query.count()==1 and result.proposal_token


def test_canonical_provider_failure_is_durable_refunded(replacement,app,monkeypatch):
    app.config['AI_PLAN_QUOTA_ENABLED']=True;calls=[]
    def unavailable(*args,**kwargs):calls.append(1);raise GenerationUnavailableError('private provider reason')
    monkeypatch.setattr(gen,'generate_training_plan_candidate',unavailable)
    for _ in range(2):
        try:
            run(replacement)
            error=None
        except Exception as caught:error=caught
        assert isinstance(error,StoredGenerationFailure)
        assert error.public_code=='TRAINING_PLAN_GENERATION_UNAVAILABLE' and error.http_status==503 and not error.retryable
    db.session.refresh(replacement)
    assert calls==[1] and replacement.user_metadata['ai_plan_quota']['training']==0
    assert Operation.query.one().generation_context is None


def test_http_confirm_missing_key_and_conflict(client,replacement,as_mobile,provider):
    first=run(replacement);body={'proposal_token':first.proposal_token,'confirmed':True}
    missing=client.post(CONFIRM,json=body,headers=as_mobile(replacement))
    assert missing.status_code==400
    accepted=client.post(CONFIRM,json=body,headers=as_mobile(replacement,'confirm-key-1'))
    assert accepted.status_code==200
    body['proposal_token']='x'*43
    conflict=client.post(CONFIRM,json=body,headers=as_mobile(replacement,'confirm-key-1'))
    assert conflict.status_code==409 and conflict.json['error']['code']=='TRAINING_PLAN_IDEMPOTENCY_CONFLICT'
    assert len(provider)==1


@pytest.mark.parametrize('reason,code',[
    ('STALE','TRAINING_PLAN_STALE_CURRENT_PLAN'),('ACTIVE_REFUSED','TRAINING_PLAN_ACTIVE_SESSION_REFUSED'),
    ('COACH_PENDING_REFUSED','TRAINING_PLAN_COACH_PENDING_REFUSED'),('EXPIRED','TRAINING_PLAN_PROPOSAL_EXPIRED'),('CONSUMED','TRAINING_PLAN_PROPOSAL_CONSUMED')])
def test_closed_refusal_mapping(client,replacement,as_mobile,monkeypatch,reason,code):
    def refused(*args):raise authority.ReplacementRefusal(authority.ConfirmationResult('internal',reason,None,None,True))
    monkeypatch.setattr(authority,'confirm_replacement',refused)
    response=client.post(CONFIRM,json={'proposal_token':'x'*43,'confirmed':True},headers=as_mobile(replacement,'confirm-key-1'))
    assert response.status_code==409 and response.json['error']['code']==code
    assert response.headers['Idempotency-Replayed']=='true' and response.headers['Cache-Control']=='no-store'


def test_stable_review_replay_does_not_resolve_catalog(replacement,provider,monkeypatch):
    first=run(replacement)
    def forbidden_projection(*args):
        assert False, 'catalog projection replayed'
    monkeypatch.setattr(gen,'review_candidate',forbidden_projection)
    replay=run(replacement)
    assert replay.replayed and replay.review==first.review and len(provider)==1


def test_no_prerequisite_read_on_successful_replay(replacement,provider):
    first=run(replacement)
    replacement.profile_complete=False;db.session.commit()
    replay=run(replacement)
    assert replay.replayed and replay.proposal_token==first.proposal_token and len(provider)==1


def test_real_canonical_generator_candidate_only(replacement):
    from test_mobile_training_generation_candidate import _provider_document
    calls=[]
    def chat(**kwargs):
        assert not db.session().in_transaction()
        # Separate connection sees durable admission before provider execution.
        # SQLite test is complemented by independent PostgreSQL workers.
        calls.append(kwargs)
        return json.dumps(_provider_document())
    result=gen.generate_proposal(replacement,parse_native_request(CANONICAL),'generation-key-1',chat_fn=chat,provider_guard=nullcontext)
    assert len(calls)==1 and result.proposal_token and Proposal.query.count()==1
    assert TrainingPlan.query.count()==1 and TrainingPlan.query.one().plan_data==json.dumps(_document())


def test_confirm_requires_real_bearer_credential(client,replacement):
    # No mocked authentication: an opaque but unknown credential must fail.
    response=client.post(CONFIRM,json={'proposal_token':'x'*43,'confirmed':True},
                         headers={'Authorization':'Bearer invalid-opaque-credential','Idempotency-Key':'confirm-key-1'})
    assert response.status_code==401 and response.headers['Cache-Control']=='no-store'


def test_real_bearer_happy_path_and_erasure(client,replacement,provider,monkeypatch):
    from account_deletion_support import install_fakes,issue_bearer
    cognito,s3=install_fakes(monkeypatch)
    bearer=issue_bearer(cognito,replacement)
    bearer['Idempotency-Key']='generation-key-1'
    first=client.post(GENERATE,json=CANONICAL,headers=bearer)
    assert first.status_code==201
    bearer['Idempotency-Key']='confirm-key-1'
    accepted=client.post(CONFIRM,json={'proposal_token':first.json['proposal_token'],'confirmed':True},headers=bearer)
    assert accepted.status_code==200 and len(provider)==1
    deleted=client.delete('/api/v1/account',headers=bearer)
    assert deleted.status_code==204
    assert Operation.query.count()==Proposal.query.count()==0


def test_provider_busy_is_typed_durable_and_refunded(replacement,app):
    from contextlib import contextmanager
    from app.services.ai_gate import BlockingConcurrencyLimit
    app.config['AI_PLAN_QUOTA_ENABLED']=True
    @contextmanager
    def busy():
        raise BlockingConcurrencyLimit()
        yield
    for _ in range(2):
        with pytest.raises(StoredGenerationFailure) as error:
            gen.generate_proposal(replacement,parse_native_request(CANONICAL),'generation-key-1',chat_fn=lambda:pytest.fail('provider reached'),provider_guard=busy)
        assert error.value.public_code=='TRAINING_PLAN_GENERATION_BUSY' and error.value.http_status==503
    db.session.refresh(replacement)
    assert replacement.user_metadata['ai_plan_quota']['training']==0


def test_generation_replay_survives_sensitive_proposal_cleanup(replacement,provider):
    first=run(replacement);uid=replacement.id
    Proposal.query.filter_by(user_id=uid).delete();db.session.commit();db.session.remove()
    from app.models import User
    replay=run(db.session.get(User,uid))
    assert replay.replayed and replay.proposal_token==first.proposal_token
    assert replay.expires_at==first.expires_at and replay.review==first.review and len(provider)==1
