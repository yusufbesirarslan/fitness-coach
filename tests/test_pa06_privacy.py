"""PA06 M01–M04: synthetic canaries inspected at mocked SDK entry points."""
import json
import socket
from datetime import datetime
from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest

from app.extensions import db
from app.models import (Activity, CoachMessage, Friendship, MealLog, TrainingPlan,
                        User, UserSession, WorkoutLog, WeeklyCheckIn)
from app.services import (ai, ai_coach, ai_pipeline, ai_stream, coach_memory_provenance as provenance,
                          coach_privacy, coach_context_queries, memory_manager as memory)
from app.timeutil import app_today
from test_mobile_coach_api import bearer  # real opaque-session fixture; Cognito mocked

CANARY = 'PA06_FORBIDDEN_THIRD_PARTY_9F7A'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('PA06 tests must not open network connections')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)


def frozen(kwargs):
    return json.dumps(kwargs, ensure_ascii=False,
                      default=lambda item: vars(item)).encode()


def answer(text='OWN_TRAINING_OK', calls=None):
    return NS(choices=[NS(message=NS(content=text, tool_calls=calls or []))])


def tool(name, args=None, call_id='t1'):
    return NS(id=call_id, function=NS(name=name, arguments=json.dumps(args or {})))


def openai_capture(monkeypatch, responses):
    captures = []
    responses = iter(responses)
    def create(**kwargs):
        captures.append(frozen(kwargs))  # freeze before subsequent rounds mutate lists
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(ai_coach, 'openai_client', NS(chat=NS(completions=NS(create=create))))
    return captures


def seed(user):
    friend = User(username=f'{CANARY}_{user.id}', full_name=CANARY,
                  email=f'friend-{user.id}@example.invalid')
    db.session.add(friend)
    db.session.flush()
    db.session.add(Friendship(sender_id=user.id, receiver_id=friend.id, status='accepted'))
    db.session.add(Activity(user_id=friend.id, activity_type='PA06_SOCIAL_ACTIVITY',
                           content=CANARY, timestamp=datetime(2001, 1, 2, 3, 4, 5)))
    db.session.add(TrainingPlan(user_id=user.id, score=8, plan_data=json.dumps({
        'program': [], 'social': {'friend': CANARY}, 'notes': [CANARY]})))
    user.goal = 'kas kazanma'
    user.fitness_level = 'intermediate'
    user.user_metadata = {
        'friends_count': 987654321,
        'friends': [{'username': CANARY, 'name': CANARY, 'id': CANARY,
                     'activities': [{'type': CANARY, 'content': CANARY, 'time': CANARY}]}],
        'preferences': json.dumps({'nested': [CANARY]}),
        'injuries': f'meniscus {CANARY}',
        'equipment_available': ['dumbbell', {'social': CANARY}],
    }
    db.session.add(WorkoutLog(user_id=user.id, exercise_name=CANARY,
                              sets=3, reps=10, weight_kg=42, volume=1260))
    db.session.add(UserSession(user_id=user.id, target_calories=2400))
    db.session.add(WeeklyCheckIn(user_id=user.id, weight=80, yogunluk=3,
                                 fatigue=2, uyku_kalitesi=4, beslenme_uyumu=5,
                                 note=CANARY, progressive_overload=CANARY))
    db.session.commit()


@pytest.mark.parametrize('transport', ['web', 'native', 'web-stream'])
@pytest.mark.parametrize('language', ['en', 'tr'])
def test_m01_owned_training_reaches_sdk_without_social(
        app, client, make_user, login, bearer, monkeypatch, transport, language):
    user = make_user('pa06_owner', profile_complete=True, language=language)
    seed(user)
    def forbidden(*args, **kwargs):
        pytest.fail('automatic whole/social query was called')
    for name in ('get_friend_activities', 'get_user_fitness_summary', 'get_user_workout_history',
                 'get_user_supplement_stack', 'get_user_nutrition_log'):
        monkeypatch.setattr(coach_context_queries, name, forbidden)
    captures = openai_capture(monkeypatch, [answer()])
    headers = bearer(user)  # also register the Cognito fixture's principal
    if transport == 'native':
        response = client.post('/api/v1/coach/messages', headers=headers,
                               json={'message': 'Review my workout progress'})
    else:
        login(user.username)
        response = client.post('/ask/stream' if transport == 'web-stream' else '/ask',
                               json={'question': 'Review my workout progress',
                                     'history': [{'role': 'bot', 'text': CANARY}]})
    assert response.status_code == 200
    assert b'OWN_TRAINING_OK' in response.data
    assert captures and all(CANARY.encode() not in body for body in captures)
    assert b'1260' in captures[0] and b'intermediate' in captures[0]
    assert b'dumbbell' in captures[0] and b'deep squat' in captures[0]
    assert b'pa06_owner' not in captures[0]
    assert b'987654321' not in captures[0] and b'2001-01-02' not in captures[0]


@pytest.mark.parametrize('name', ['query_fitx_metrics', 'manage_user_memory',
    'fetch_nutrition_and_stage_log', 'confirm_and_commit_meal_log',
    'stage_workout_log', 'confirm_and_commit_workout_log', 'cancel_pending_log',
    'analyze_gym_photo', 'add_training_plan_exercise'])
def test_m02_tool_canaries_absent_in_multiple_openai_rounds(app, make_user, monkeypatch, name):
    user = make_user('tool_owner')
    contaminated = {'status': 'ok', 'value': 1260, 'metric_type': 'volume_lifted', 'unit': 'kg',
        'friends': [CANARY], 'metadata': json.dumps({'private': CANARY}),
        'key': 'preferences', 'memory': {'social': CANARY},
        'staged_food': {'calories': 350, 'food_name': CANARY, 'nested': [CANARY]},
        'logged': {'sets': 3, 'reps': 10, 'weight_kg': 42, 'exercise_name': CANARY,
                   'social': {'other': CANARY}}, 'message': CANARY, 'summary': CANARY,
        'change': {'exercise': CANARY, 'sets': 3, 'unknown': CANARY}}
    monkeypatch.setattr(ai_coach, '_dispatch_coach_tool', lambda *a: json.dumps(contaminated))
    captures = openai_capture(monkeypatch, [answer(calls=[tool(name)]),
                             answer(calls=[tool(name, call_id='t2')]), answer()])
    result = ai_coach._run_coach_conversation_openai(user.id, 'review', '', [])
    assert result == 'OWN_TRAINING_OK'
    assert len(captures) == 3
    assert all(CANARY.encode() not in body for body in captures)
    assert b'350' in captures[-1] if name == 'fetch_nutrition_and_stage_log' else b'status' in captures[-1]


@pytest.mark.parametrize('streaming', [False, True])
def test_m02_bedrock_followup_projection(app, make_user, monkeypatch, streaming):
    user = make_user('bedrock_owner')
    captures = []
    responses = iter([
        NS(stop_reason='tool_use', content=[NS(type='tool_use', name='query_fitx_metrics',
            input={'metric_type': 'volume_lifted', 'date_range': 'today'}, id='t1')], usage=None),
        NS(stop_reason='end_turn', content=[NS(type='text', text='OWN_TRAINING_OK')], usage=None),
    ])
    def create(**kwargs):
        captures.append(frozen(kwargs))
        return next(responses)
    @contextmanager
    def stream(**kwargs):
        result = create(**kwargs)
        yield NS(text_stream=iter(['OWN_TRAINING_OK'] if result.stop_reason == 'end_turn' else []), get_final_message=lambda: result)
    monkeypatch.setattr(ai_coach, 'bedrock_client', NS(messages=NS(create=create, stream=stream)))
    monkeypatch.setattr(ai_coach, 'BEDROCK_ENABLED', True)
    monkeypatch.setattr(ai_coach, '_anthropic', object())
    monkeypatch.setattr(ai_coach, '_dispatch_coach_tool', lambda *a: json.dumps({
        'status': 'ok', 'value': 1260, 'metric_type': 'volume_lifted', 'unit': 'kg',
        'metadata': [{'friend': CANARY}], 'detail': CANARY}))
    if streaming:
        events = list(ai_stream.stream_coach_answer(user.id, 'workout', '', []))
        assert events[-1]['text'] == 'OWN_TRAINING_OK'
    else:
        assert ai_coach._run_coach_conversation_bedrock(user.id, 'workout', '', []) == 'OWN_TRAINING_OK'
    assert len(captures) == 2 and b'1260' in captures[-1]
    assert all(CANARY.encode() not in body for body in captures)


@pytest.mark.parametrize('streaming', [False, True])
def test_m02_provider_fallback_cannot_restore_legacy(app, make_user, monkeypatch, streaming):
    user = make_user('fallback_owner')
    captures = openai_capture(monkeypatch, [answer()])
    bedrock = []
    def fail(**kwargs):
        bedrock.append(frozen(kwargs))
        raise RuntimeError('offline simulated provider failure')
    monkeypatch.setattr(ai_coach, 'bedrock_client', NS(messages=NS(create=fail, stream=fail)))
    monkeypatch.setattr(ai_coach, 'BEDROCK_ENABLED', True)
    monkeypatch.setattr(ai_coach, '_anthropic', object())
    conv = memory.get_or_create_active_conversation(user.id)
    conv.summary = CANARY
    db.session.add(CoachMessage(conversation_id=conv.id, role='assistant', content=CANARY))
    db.session.commit()
    if streaming:
        events = list(ai_pipeline.stream_answer(user.id, 'workout', [{'role': 'bot', 'text': CANARY}]))
        assert events[-1]['text'] == 'OWN_TRAINING_OK'
    else:
        assert ai_pipeline.generate_answer(user.id, 'workout', [{'role': 'bot', 'text': CANARY}])['answer'] == 'OWN_TRAINING_OK'
    assert bedrock and captures
    assert all(CANARY.encode() not in body for body in bedrock + captures)


def test_m03_legacy_quarantine_preserves_display_and_new_continuity(app, make_user, monkeypatch):
    user = make_user('legacy_owner')
    seed(user)
    conv = memory.get_or_create_active_conversation(user.id)
    conv.summary = CANARY
    db.session.add(CoachMessage(conversation_id=conv.id, role='assistant', content=CANARY))
    db.session.commit()
    memory.record_turn(conv, 'OWN_FIRST_PARTY_FACT', 'OWN_CLEAN_REPLY', assistant_memory_eligible=True)
    captures = openai_capture(monkeypatch, [answer()])
    assert ai_pipeline.generate_answer(user.id, 'workout')['answer'] == 'OWN_TRAINING_OK'
    assert b'OWN_FIRST_PARTY_FACT' in captures[0] and b'1260' in captures[0]
    assert CANARY.encode() not in captures[0]
    _, displayed = memory.recent_messages(user.id)
    assert any(CANARY in row.content for row in displayed)
    assert conv.summary == CANARY  # no deletion or rewriting of historical content


def test_m03_background_summary_excludes_legacy_and_keeps_signed_facts(app, make_user, monkeypatch):
    from app.jobs.tasks import summarize_conversation
    user = make_user('summary_owner')
    conv = memory.get_or_create_active_conversation(user.id)
    conv.summary = CANARY
    for i in range(205):
        db.session.add(CoachMessage(conversation_id=conv.id, role='assistant', content=CANARY * 200))
    db.session.commit()
    monkeypatch.setattr('app.config.AI_SUMMARY_TRIGGER_TOKENS', 100)
    for i in range(6):
        memory.record_turn(conv, 'OWN_FIRST_PARTY_FACT ' + 'z' * 600, 'CLEAN_RESPONSE ' + 'x' * 600, assistant_memory_eligible=True)
    captures = openai_capture(monkeypatch, [])  # summary uses ai.openai_client
    summary_bytes = []
    def create(**kwargs):
        summary_bytes.append(frozen(kwargs))
        return answer('OWN_SAFE_SUMMARY')
    monkeypatch.setattr(ai, 'openai_client', NS(chat=NS(completions=NS(create=create))))
    assert summarize_conversation(conv.id) is True
    assert summary_bytes and b'OWN_FIRST_PARTY_FACT' in summary_bytes[0]
    assert all(CANARY.encode() not in body for body in summary_bytes)
    assert provenance.summary_text(conv) == 'OWN_SAFE_SUMMARY'
    captures = openai_capture(monkeypatch, [answer()])
    ai_pipeline.generate_answer(user.id, 'workout')
    assert b'OWN_SAFE_SUMMARY' in captures[0] and CANARY.encode() not in captures[0]
    assert CoachMessage.query.filter_by(conversation_id=conv.id, content=CANARY * 200).count() == 205


def test_m03_provenance_rejects_forged_copied_and_changed_rows(app, make_user):
    a, b = make_user('owner_a'), make_user('owner_b')
    ca, cb = memory.get_or_create_active_conversation(a.id), memory.get_or_create_active_conversation(b.id)
    rows = memory.record_turn(ca, 'OWN_FACT', 'CLEAN', assistant_memory_eligible=True)
    assert memory.build_context_window(ca)
    b.user_metadata = a.user_metadata
    db.session.add(CoachMessage(conversation_id=cb.id, role='user', content='OWN_FACT'))
    db.session.commit()
    assert memory.build_context_window(cb) == []
    rows[0].content = CANARY
    db.session.commit()
    assert CANARY not in json.dumps(memory.build_context_window(ca))
    ca.summary = json.dumps({'version': 1, 'text': CANARY, 'signature': 'forged'})
    assert provenance.summary_text(ca) == ''


def test_m04_accounts_have_separate_provider_context(app, make_user, monkeypatch):
    a, b = make_user('owner_a'), make_user('owner_b')
    seed(a)
    captures = openai_capture(monkeypatch, [answer(), answer()])
    ai_pipeline.generate_answer(a.id, 'workout')
    ai_pipeline.generate_answer(b.id, 'workout')
    assert b'1260' in captures[0] and b'1260' not in captures[1]
    assert all(CANARY.encode() not in body for body in captures)
    with app.test_request_context('/ask'):
        from flask_login import login_user
        login_user(a)
        from app.services.context_builder import fetch_coach_context
        with pytest.raises(PermissionError):
            fetch_coach_context(b.id, 'workout')


@pytest.mark.parametrize('body', [CANARY, '- ' + CANARY, '- rice\n- beans', '- ' + CANARY + '\n- rice'])
@pytest.mark.parametrize('lookup', ['cached', 'missing', 'per_100g', 'failure'])
def test_m04_sender_content_never_analyzed(app, monkeypatch, body, lookup):
    from app.blueprints import social
    def forbidden(*args, **kwargs):
        pytest.fail('sender content reached an AI analysis helper')
    for name in ('_parse_suggestion_items', '_estimate_macros_llm', '_estimate_serving_weights_llm'):
        monkeypatch.setattr(social, name, forbidden)
    macros = {'calories': 200, 'protein': 10, 'carbs': 30, 'fat': 5}
    monkeypatch.setattr(social, '_get_cached_macros', lambda items, **kw:
                        ({item: macros for item in items}, []) if lookup == 'cached' else ({}, items))
    monkeypatch.setattr(social, '_get_fatsecret_token', lambda: 'synthetic')
    def search(items, token):
        if lookup == 'failure':
            raise RuntimeError('synthetic lookup outage')
        return ({}, {item: macros for item in items}) if lookup == 'per_100g' else ({}, {})
    monkeypatch.setattr(social, '_lookup_macros_fatsecret', search)
    result = social._calculate_meal_suggestion(NS(body=body, message_id=1))
    if body == '- rice\n- beans' and lookup == 'cached':
        assert result['kalori'] == 400
    elif lookup != 'cached' or body == CANARY:
        assert result is None


def test_m02_exact_nutrition_and_recovery_operation_projections(app, make_user, monkeypatch):
    user = make_user('operation_owner')
    seed(user)
    db.session.add(MealLog(user_id=user.id, ogun=CANARY, yemekler=CANARY,
                           kalori=650, protein=40, karb=70, yag=20,
                           tarih=app_today().isoformat()))
    db.session.commit()
    captures = openai_capture(monkeypatch, [answer(), answer()])
    ai_pipeline.generate_answer(user.id, 'Review my nutrition day')
    ai_pipeline.generate_answer(user.id, 'Review recovery')
    assert b'650' in captures[0] and b'2400' in captures[0]
    assert b'1260' not in captures[0] and b'deep squat' not in captures[0]
    assert b'uyku_kalitesi' in captures[1] and b'1260' not in captures[1]
    assert all(CANARY.encode() not in body for body in captures)


def test_m02_plan_reply_retries_capture_projected_form_fields(app, monkeypatch):
    import httpx
    from openai import APITimeoutError
    from app.services import ai_recovery
    captures = []
    def create(**kwargs):
        captures.append(frozen(kwargs))
        if len(captures) == 1:
            raise APITimeoutError(request=httpx.Request('POST', 'https://offline.invalid'))
        return answer()
    monkeypatch.setattr(ai, 'openai_client', NS(chat=NS(completions=NS(create=create))))
    monkeypatch.setattr(ai, 'BEDROCK_ENABLED', False)
    monkeypatch.setattr(ai_recovery, 'AI_RECOVERY_ENABLED', True)
    monkeypatch.setattr(ai_recovery, 'AI_RETRY_ATTEMPTS', 2)
    monkeypatch.setattr(ai_recovery, '_sleep', lambda _: None)
    result = ai_coach.generate_coach_reply(CANARY, 30, CANARY, 82, 180,
        'kas kazanma', 'intermediate', CANARY, 1800, 2500, 2800,
        {'friends': [CANARY]}, {'unknown': CANARY}, 'Review my workout', language='en')
    assert result == 'OWN_TRAINING_OK' and len(captures) == 2
    assert all(CANARY.encode() not in body for body in captures)
    assert b'2800' in captures[-1] and b'intermediate' in captures[-1]


def test_m02_training_repair_keeps_constraints_without_stored_prose(app, make_user, monkeypatch):
    from app.services.training_generation import service
    from app.services.training_generation.models import TrainingPreferences
    from app.services.training_generation.output_errors import GenerationOutputError
    from test_training_generation import _features
    user = make_user('repair_owner')
    captures = []
    def create(**kwargs):
        captures.append(frozen(kwargs))
        return answer('invalid JSON')
    monkeypatch.setattr(ai, 'openai_client', NS(chat=NS(completions=NS(create=create))))
    monkeypatch.setattr(ai, 'BEDROCK_ENABLED', False)
    def chat(**kwargs):
        return ai._heavy_chat(**kwargs, feature='training_plan')
    with pytest.raises(GenerationOutputError):
        service.generate_training_plan_candidate(user, None,
            TrainingPreferences(injuries=f'meniscus {CANARY}'), chat,
            frozen_features=_features(goal=CANARY, current_activity=CANARY))
    assert len(captures) == 2
    assert all(CANARY.encode() not in body for body in captures)
    assert b'deep squat' in captures[0] and b'Weekly frequency' in captures[0]


def test_m02_checkin_feedback_omits_name_and_freeform_note(app, monkeypatch):
    captures = []
    def create(**kwargs):
        captures.append(frozen(kwargs))
        return answer()
    monkeypatch.setattr(ai, 'openai_client', NS(chat=NS(completions=NS(create=create))))
    monkeypatch.setattr(ai, 'BEDROCK_ENABLED', False)
    assert ai_coach.generate_checkin_feedback(CANARY, 82, 80, 7, 'kas kazanma',
        3, 2, 'evet', 4, 5, CANARY) == 'OWN_TRAINING_OK'
    assert b'82' in captures[0] and CANARY.encode() not in captures[0]


def test_m04_mobile_principal_owns_context_even_with_another_web_cookie(app, make_user, monkeypatch):
    from flask import g
    from flask_login import login_user
    from app.services.context_builder import fetch_coach_context
    from app.services import context_builder
    a, b = make_user('cookie_a'), make_user('bearer_b')
    calls = []
    def resolve(user_id):
        calls.append(user_id)
        return NS(primary_state='rest_day', completed_today=False, is_rest_day=True)
    monkeypatch.setattr(context_builder, 'resolve_workout_state', resolve)
    with app.test_request_context('/api/v1/coach/messages'):
        login_user(a)
        g.mobile_user = b
        assert json.loads(fetch_coach_context(b.id, 'hello').split('\n', 1)[1]) == {
            'primary_state': 'rest_day', 'completed_today': False, 'is_rest_day': True}
        with pytest.raises(PermissionError):
            fetch_coach_context(a.id, 'hello')
        assert calls == [b.id]


@pytest.mark.parametrize('question', ['', None, 'hello', 'Review my workout progress'])
def test_m01_canonical_state_projection_reaches_sdk_without_social(
        app, make_user, monkeypatch, question):
    from app.services import context_builder
    from app.services.workout_state.models import WorkoutStateSnapshot
    user = make_user('canonical_owner')
    seed(user)
    state = WorkoutStateSnapshot(
        today=app_today(), schedule_state='scheduled', execution_state='in_progress',
        plan_relationship='matches_scheduled', action='resume', primary_state='in_progress',
        completed_today=False, is_rest_day=False, stale_previous_workout=False,
        contract_version=2, anomaly=CANARY, session={'metadata': CANARY},
        execution_view=NS(notes=CANARY))
    calls = []
    def resolve(user_id):
        calls.append(user_id)
        return state
    def forbidden(*args, **kwargs):
        pytest.fail('unrestricted serialization/social query was called')
    monkeypatch.setattr(context_builder, 'resolve_workout_state', resolve)
    monkeypatch.setattr(WorkoutStateSnapshot, 'to_dict', forbidden)
    for name in ('get_friend_activities', 'get_user_fitness_summary', 'get_user_workout_history'):
        monkeypatch.setattr(coach_context_queries, name, forbidden)
    context = context_builder.fetch_coach_context(user.id, question)
    header = '[GÜNCEL ANTRENMAN DURUMU]\n'
    assert context.count(header) == 1
    assert json.loads(context.split(header)[1].split('\n\n')[0]) == {
        'primary_state': 'in_progress', 'completed_today': False, 'is_rest_day': False}
    assert calls == [user.id]
    if coach_privacy.operation(question) == 'chat':
        assert context.startswith(header) and '\n\n' not in context
    captures = openai_capture(monkeypatch, [answer()])
    assert ai_coach._run_coach_conversation_openai(user.id, question or 'hello', context, []) == 'OWN_TRAINING_OK'
    assert b'in_progress' in captures[0]
    assert CANARY.encode() not in captures[0] and b'987654321' not in captures[0]


def test_m01_default_context_without_plan_uses_canonical_state(app, make_user):
    from app.services.context_builder import fetch_coach_context
    user = make_user('no_workout_data')
    assert json.loads(fetch_coach_context(user.id).split('\n', 1)[1]) == {
        'primary_state': 'no_plan', 'completed_today': False, 'is_rest_day': False}


def test_m01_default_context_missing_owner_or_snapshot_fails_closed(app, make_user, monkeypatch):
    from app.services import context_builder
    calls = []
    def unavailable(user_id):
        calls.append(user_id)
        raise RuntimeError('synthetic unavailable state')
    monkeypatch.setattr(context_builder, 'resolve_workout_state', unavailable)
    assert context_builder.fetch_coach_context(-1) == ''
    assert calls == []
    user = make_user('missing_snapshot')
    assert context_builder.fetch_coach_context(user.id) == ''
    assert calls == [user.id]


def test_m02_tool_positive_receipt_preserves_closed_prescription():
    raw = json.dumps({'status': 'applied', 'change': {'day': 'Pazartesi',
        'exercise': 'Barbell Back Squat', 'sets': 3, 'reps': '8-12',
        'metadata': [CANARY]}, 'summary': CANARY})
    projected = coach_privacy.tool_result('add_training_plan_exercise', raw)
    payload = json.loads(projected)
    assert payload['change']['reps'] == '8-12'
    assert payload['change']['day'] == 'Pazartesi'
    assert payload['change']['exercise'] == 'Barbell Back Squat'
    assert CANARY not in projected
