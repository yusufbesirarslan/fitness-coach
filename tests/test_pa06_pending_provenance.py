"""PA06-R2: real deterministic commits, display history and serialized SDK reuse."""
import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.extensions import db
from app.models import MealLog, PendingAction, TrainingPlan, User, WorkoutLog
from app.observability import assign_request_id
from app.services import ai, ai_coach, ai_pipeline, memory_manager as memory
from app.services import coach_memory_provenance as provenance
from tests.test_pa06_privacy import answer, frozen
from tests.test_coach_plan_tools import REMOVE, call, names, tools_on  # noqa: F401
from tests.test_coach_plan_tool_characterization import planned_user  # noqa: F401

CANARY = 'FORBIDDEN_LEGACY_FRIEND_ALICE_9F7A'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('PA06-R2 must not open network connections')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)


def capture(monkeypatch, provider, label):
    bodies = []
    def create(**kwargs):
        body = frozen(kwargs)
        bodies.append(body)
        evidence = os.environ.get('PA06_R2_SDK_EVIDENCE')
        if evidence:
            with Path(evidence).open('a') as out:
                out.write(json.dumps({'case': label, 'provider': provider,
                                      'payload': json.loads(body)}) + '\n')
        if provider == 'bedrock':
            return NS(stop_reason='end_turn', content=[NS(type='text', text='OWN_REPLY')], usage=None)
        return answer('OWN_SAFE_SUMMARY' if provider == 'summary' else 'OWN_REPLY')
    if provider == 'summary':
        monkeypatch.setattr(ai, 'openai_client', NS(chat=NS(completions=NS(create=create))))
    else:
        # Install both mocks even for deterministic confirmation: any invocation
        # is captured and fails the zero-call assertion.
        monkeypatch.setattr(ai_coach, 'openai_client', NS(chat=NS(completions=NS(create=create))))
        monkeypatch.setattr(ai_coach, 'bedrock_client', NS(messages=NS(create=create)))
        monkeypatch.setattr(ai_coach, 'BEDROCK_ENABLED', provider == 'bedrock')
        monkeypatch.setattr(ai_coach, '_anthropic', object())
    return bodies


def turn(app, user_id, question, streaming):
    with app.test_request_context('/ask/stream' if streaming else '/ask', method='POST'):
        assign_request_id()
        if streaming:
            events = list(ai_pipeline.stream_answer(user_id, question, language='en'))
            return events[-1]['text']
        return ai_pipeline.generate_answer(user_id, question, language='en')['answer']


def seed_pending(user_id, kind):
    payload = {'notes': CANARY, 'historical_provider_text': CANARY}
    if kind == 'meal':
        payload.update(food_name=CANARY, calories=350, protein=20, carbs=40, fat=10)
    else:
        payload.update(exercise_name=CANARY, sets=3, reps=10, weight_kg=42, volume=1260)
    db.session.add(PendingAction(user_id=user_id, action_type='log_' + kind, payload=payload))
    db.session.commit()


def assert_quarantined(user_id, receipt):
    conv, rows = memory.recent_messages(user_id)
    assert [(row.role, row.content) for row in rows] == [('user', 'yes'), ('assistant', receipt)]
    assert CANARY in receipt
    assert provenance.trusted_rows(conv, rows) == [rows[0]]
    assert str(rows[1].id) not in db.session.get(User, user_id).user_metadata[provenance.INDEX_KEY]
    assert memory.build_context_window(conv) == [{'role': 'user', 'content': 'yes'}]
    return conv, rows[1].id


@pytest.mark.parametrize('kind', ['meal', 'workout', 'plan'])
@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('provider', ['openai', 'bedrock'])
def test_legacy_confirmation_never_promotes_raw_copy(
        app, planned_user, tools_on, monkeypatch, kind, streaming, provider):
    user = planned_user
    label = f'{kind}/{streaming}/{provider}'
    bodies = capture(monkeypatch, provider, label)
    monkeypatch.setattr(ai_pipeline, '_maybe_enqueue_summarize', lambda conv: None)
    if kind == 'plan':
        plan = TrainingPlan.query.filter_by(user_id=user.id).first()
        plan.plan_data = plan.plan_data.replace('Bench Press', CANARY)
        db.session.commit()
        with app.test_request_context('/ask', method='POST'):
            assign_request_id()
            ai_coach._begin_coach_turn('')
            staged = call(user.id, REMOVE, {'day': 'Pazartesi', 'exercise': CANARY})
            assert staged['status'] == 'confirmation_required'
    else:
        seed_pending(user.id, kind)
    receipt = turn(app, user.id, 'yes', streaming)
    assert bodies == []  # deterministic confirmation calls neither SDK
    conv, receipt_id = assert_quarantined(user.id, receipt)
    if kind == 'meal':
        logs = MealLog.query.filter_by(user_id=user.id).all()
        assert len(logs) == 1 and logs[0].yemekler == CANARY
        assert logs[0].kalori == 350
    elif kind == 'workout':
        logs = WorkoutLog.query.filter_by(user_id=user.id).all()
        assert len(logs) == 1 and logs[0].exercise_name == CANARY
        assert (logs[0].sets, logs[0].reps, logs[0].volume) == (3, 10, 1260)
    else:
        assert names(user.id) == ['Shoulder Press']
        assert plan.mutation_version == 1
    assert PendingAction.query.filter_by(user_id=user.id).count() == 0
    # Unchanged current question; real prompt/provider path, serialized kwargs.
    assert turn(app, user.id, 'hello', False) == 'OWN_REPLY'
    assert bodies and all(CANARY.encode() not in body for body in bodies)
    assert b'yes' in bodies[0] and b'hello' in bodies[0]
    if kind == 'workout':
        # Existing operation-specific first-party numeric facts remain useful.
        assert turn(app, user.id, 'Review my workout progress', False) == 'OWN_REPLY'
        assert b'1260' in bodies[-1] and CANARY.encode() not in bodies[-1]
    # Force the original receipt inside the summary age range; capture the
    # actual summarizer SDK kwargs via the production background job.
    for _ in range(6):
        memory.record_turn(conv, 'OWN_FACT ' + 'z' * 600, 'OWN_REPLY ' + 'x' * 600,
                           assistant_memory_eligible=True)
    monkeypatch.setattr('app.config.AI_SUMMARY_TRIGGER_TOKENS', 100)
    summary_bodies = capture(monkeypatch, 'summary', label)
    from app.jobs.tasks import summarize_conversation
    assert summarize_conversation(conv.id) is True
    assert summary_bodies and b'yes' in summary_bodies[0] and b'OWN_FACT' in summary_bodies[0]
    assert all(CANARY.encode() not in body for body in summary_bodies)
    assert provenance.summary_text(conv) == 'OWN_SAFE_SUMMARY'
    assert any(row.id == receipt_id and row.content == receipt
               for row in memory.recent_messages(user.id)[1])


@pytest.mark.parametrize('streaming', [False, True])
def test_canonical_cancellation_receipt_retains_continuity(
        app, make_user, monkeypatch, streaming):
    user = make_user('canonical_owner')
    seed_pending(user.id, 'meal')
    bodies = capture(monkeypatch, 'openai', f'canonical/{streaming}')
    monkeypatch.setattr(ai_pipeline, '_maybe_enqueue_summarize', lambda conv: None)
    receipt = turn(app, user.id, 'cancel', streaming)
    assert bodies == [] and CANARY not in receipt
    assert MealLog.query.filter_by(user_id=user.id).count() == 0
    conv, rows = memory.recent_messages(user.id)
    assert provenance.trusted_rows(conv, rows) == rows
    assert turn(app, user.id, 'hello', False) == 'OWN_REPLY'
    assert receipt.encode() in bodies[0]


@pytest.mark.parametrize('eligible', [False, True, None, 'yes', 1])
def test_exact_recording_eligibility(app, make_user, eligible):
    user = make_user('eligibility_owner')
    conv = memory.get_or_create_active_conversation(user.id)
    rows = memory.record_turn(conv, 'yes', 'Canonical workout: 3 sets, 10 reps.',
                              assistant_memory_eligible=eligible)
    assert memory.recent_messages(user.id)[1] == list(rows)
    assert provenance.trusted_rows(conv, rows) == (list(rows) if eligible is True else [rows[0]])


def test_default_recording_is_display_only(app, make_user):
    user = make_user('default_owner')
    conv = memory.get_or_create_active_conversation(user.id)
    rows = memory.record_turn(conv, 'yes', CANARY)
    assert provenance.trusted_rows(conv, rows) == [rows[0]]


@pytest.mark.parametrize('streaming', [False, True])
def test_independent_p1_serialized_sdk_reproduction(app, make_user, monkeypatch, streaming):
    """Original reviewer path: no eligibility assertion before SDK inspection."""
    user = make_user('independent_owner')
    seed_pending(user.id, 'meal')
    bodies = capture(monkeypatch, 'openai', f'independent/{streaming}')
    monkeypatch.setattr(ai_pipeline, '_maybe_enqueue_summarize', lambda conv: None)
    receipt = turn(app, user.id, 'yes', streaming)
    assert CANARY in receipt and bodies == []
    assert MealLog.query.filter_by(user_id=user.id).count() == 1
    assert memory.recent_messages(user.id)[1][-1].content == receipt
    assert turn(app, user.id, 'hello', False) == 'OWN_REPLY'
    assert len(bodies) == 1
    assert CANARY.encode() not in bodies[0]
    assert MealLog.query.filter_by(user_id=user.id).count() == 1


@pytest.mark.parametrize('source', ['workout_proposal', 'plan_proposal', 'plan_applied', 'plan_clarification'])
def test_raw_confirmation_formatters_classify_before_signing(app, make_user, source):
    from app.services import coach_confirmation as confirmation
    user = make_user('formatter_owner')
    raw = {'exercise': CANARY, 'replacement': CANARY, 'day': CANARY,
           'sets': 3, 'reps': 10, 'exercise_name': CANARY, 'notes': CANARY}
    if source == 'workout_proposal':
        reply = confirmation._format_workout_proposal(raw, 'en')
    elif source == 'plan_proposal':
        reply = confirmation._format_plan_proposal(NS(command_payload=raw, command_type='replace_exercise'), 'en')
    elif source == 'plan_applied':
        reply = confirmation._format_plan_applied({'change': raw, 'operation': 'add_exercise'}, 'en')
    else:
        reply = confirmation._format_plan_clarification({'change': raw, 'reason': 'missing_prescription',
                                                         'detail': CANARY}, 'en', user.id)
    assert isinstance(reply, provenance.DisplayOnlyReply) and CANARY in reply
    conv = memory.get_or_create_active_conversation(user.id)
    rows = ai_pipeline._record(conv, 'yes', reply,
                               assistant_memory_eligible=not isinstance(reply, provenance.DisplayOnlyReply))
    assert rows[1].content == reply
    assert provenance.trusted_rows(conv, rows) == [rows[0]]


def test_interrupted_display_only_receipt_stays_quarantined(app, make_user, monkeypatch):
    user = make_user('interrupted_owner')
    seed_pending(user.id, 'meal')
    bodies = capture(monkeypatch, 'openai', 'interrupted')
    monkeypatch.setattr(ai_pipeline, '_maybe_enqueue_summarize', lambda conv: None)
    with app.test_request_context('/ask/stream', method='POST'):
        assign_request_id()
        stream = ai_pipeline.stream_answer(user.id, 'yes', language='en')
        assert next(stream)['type'] == 'meta'
        event = next(stream)
        assert event['type'] == 'delta'
        stream.close()
    conv, rows = memory.recent_messages(user.id)
    assert bodies == [] and rows[1].interrupted
    assert provenance.trusted_rows(conv, rows) == [rows[0]]
    assert MealLog.query.filter_by(user_id=user.id).count() == 1


def test_equal_bedrock_copy_keeps_source_classification(app, make_user, monkeypatch):
    """Text equality must not discard the deterministic formatter's classification."""
    from app.services import coach_confirmation
    from tests.test_ai_stream import _FakeStream, _final, _text_block
    user = make_user('equal_copy_owner')
    bodies = capture(monkeypatch, 'bedrock', 'equal-copy')
    def stream(**kwargs):
        bodies.append(frozen(kwargs))
        return _FakeStream([CANARY], _final(content=[_text_block(CANARY)]))
    monkeypatch.setattr(ai_coach.bedrock_client.messages, 'stream', stream, raising=False)
    monkeypatch.setattr(coach_confirmation, 'grounded_provider_reply',
                        lambda *args: provenance.DisplayOnlyReply(CANARY))
    monkeypatch.setattr(ai_pipeline, '_maybe_enqueue_summarize', lambda conv: None)
    assert turn(app, user.id, 'hello', True) == CANARY
    conv, rows = memory.recent_messages(user.id)
    assert provenance.trusted_rows(conv, rows) == [rows[0]]
    assert bodies and CANARY.encode() not in bodies[0]


def test_explicit_projected_canonical_receipt_reaches_sdk(app, make_user, monkeypatch):
    from app.services.coach_privacy import tool_result
    user = make_user('projected_owner')
    conv = memory.get_or_create_active_conversation(user.id)
    # Construct memory independently from the closed PA06 projection rather
    # than trying to sanitize opaque display prose or trust its authorship.
    receipt = tool_result('confirm_and_commit_workout_log', json.dumps({
        'status': 'committed', 'logged': {'sets': 3, 'reps': 10, 'volume': 1260,
                                         'exercise_name': CANARY, 'notes': CANARY}}))
    assert CANARY not in receipt
    rows = memory.record_turn(conv, 'yes', receipt, assistant_memory_eligible=True)
    assert provenance.trusted_rows(conv, rows) == list(rows)
    bodies = capture(monkeypatch, 'openai', 'projected-canonical')
    monkeypatch.setattr(ai_pipeline, '_maybe_enqueue_summarize', lambda conv: None)
    assert turn(app, user.id, 'hello', False) == 'OWN_REPLY'
    payload = json.loads(bodies[0])
    assert {'role': 'assistant', 'content': receipt} in payload['messages']
    assert CANARY.encode() not in bodies[0]
