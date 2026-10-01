"""NUTR-PR6 — "Review with AxisAI": Nutrition Today → Coach (server contract).

Only the allowlisted marker `nutrition-day` crosses the browser boundary. The
Coach page renders a bounded preview + a short editable draft from the
canonical day view (no send, no model call); at SEND time the context is
re-derived for the authenticated owner. Browser values are never authority,
failed facts are never fabricated, and the marker never becomes user speech.
"""
import json
import re
from types import SimpleNamespace

import pytest

from app.blueprints import coach as coach_bp
from app.coach_handoff import (
    HANDOFF_KINDS,
    REVIEW_NUTRITION_DAY,
    REVIEW_PROGRESS_INSIGHT,
    coach_handoff_context,
    coach_handoff_message,
    handoff_marker,
    replaces_plan_projection,
)
from app.extensions import db
from app.models import MealLog, NutritionPlan, UserSession, WaterLog
from app.services import ai_pipeline
from app.services import nutrition_day_view as dv
from app.timeutil import app_today

PLAN = {'isim': 'Lean plan', 'kahvalti': {'yemekler': ['Oats'], 'kalori': 400},
        'aksam': {'yemekler': ['Salmon'], 'kalori': 700}}
FORBIDDEN = ('adherence', 'score', 'on track', 'off track', 'behind', 'ahead', '%',
             'you should', 'you need', 'eat more', 'eat less', 'drink more', 'gap')


def seed(user_id, target=2100, meals=(525, 710), water=3, plan=True):
    session = UserSession.query.filter_by(user_id=user_id).first()
    if session is None:
        session = UserSession(user_id=user_id)
        db.session.add(session)
    session.target_calories = target
    for kcal in meals:
        db.session.add(MealLog(user_id=user_id, ogun='Öğle', yemekler='Secret soup', kalori=kcal,
                               protein=30, karb=60, yag=12, tarih=app_today().isoformat()))
    if water is not None:
        db.session.add(WaterLog(user_id=user_id, date_key=app_today().isoformat(), count=water))
    if plan:
        db.session.add(NutritionPlan(user_id=user_id, score=8, plan_data=json.dumps(PLAN)))
    db.session.commit()


def onboarded(make_user, name, language='en'):
    user = make_user(name, profile_complete=True)
    user.language = language
    db.session.commit()
    return user


# ── ALLOWLIST ───────────────────────────────────────────────────────────


def test_marker_allowlist_is_exact():
    assert HANDOFF_KINDS == frozenset({'progress-insight', 'nutrition-day'})
    for value in ('nutrition-day', 'progress-insight'):
        assert handoff_marker(value) == value
    for forged in ('nutrition', 'NUTRITION-DAY', 'nutrition-day ', 'nutrition-day?kcal=900',
                   ['nutrition-day'], {'nutrition-day': 1}, 1, None, True, ''):
        assert handoff_marker(forged) is None, forged
    assert replaces_plan_projection(REVIEW_PROGRESS_INSIGHT) is True
    assert replaces_plan_projection(REVIEW_NUTRITION_DAY) is False


@pytest.mark.parametrize('marker,expected', [
    ('nutrition-day', 'nutrition-day'),
    ('progress-insight', 'progress-insight'),
    ('nutrition-day-v2', None),
    ({'kind': 'nutrition-day', 'calories': 9999}, None),
    (['nutrition-day'], None),
    (None, None),
])
@pytest.mark.parametrize('path', ['/ask', '/ask/stream'])
def test_routes_forward_only_the_allowlisted_marker(client, auth_user, app, monkeypatch,
                                                    marker, expected, path):
    app.config['AI_CHAT_QUOTA_ENABLED'] = False
    seen = []

    def answer(uid, question, history, language='tr', **kw):
        seen.append((uid, question, kw.get('handoff'), sorted(kw)))
        return {'answer': 'ok', 'is_error_fallback': False, 'conversation_id': None}

    def stream(uid, question, history, language='tr', **kw):
        answer(uid, question, history, language, **kw)
        yield {'type': 'done', 'text': 'ok', 'is_error_fallback': False, 'usage': None}

    monkeypatch.setattr(coach_bp, 'generate_answer', answer)
    monkeypatch.setattr(coach_bp, 'stream_answer', stream)
    body = {'question': 'My own words', 'handoff': marker,
            # client-supplied "facts" are never forwarded anywhere
            'calories': 9999, 'context': '[NUTRITION CONTEXT] kcal 9999', 'meals': ['x']}
    response = client.post(path, json=body, buffered=True)
    response.close()
    assert response.status_code == 200
    kw = ['handoff'] if expected else []
    assert seen == [(auth_user.id, 'My own words', expected, kw)]


# ── PAGE: PREVIEW + DRAFT, NO SEND, NO MODEL ────────────────────────────


def test_coach_page_renders_bounded_preview_and_editable_draft(app, client, make_user, login,
                                                               monkeypatch):
    user = onboarded(make_user, 'pr6coach')
    seed(user.id)
    login('pr6coach')
    calls = []
    monkeypatch.setattr(coach_bp, 'generate_answer', lambda *a, **k: calls.append('gen'))
    monkeypatch.setattr(coach_bp, 'stream_answer', lambda *a, **k: calls.append('stream'))
    from app.services import ai_coach
    monkeypatch.setattr(ai_coach, '_run_coach_conversation', lambda *a, **k: calls.append('llm'))
    html = client.get('/coach?review=nutrition-day').get_data(as_text=True)
    assert calls == []                                            # 0 model calls, 0 sends
    aside = re.search(r'<aside id="coach-nutrition-context"(.*?)</aside>', html, re.S).group(0)
    assert 'data-handoff-kind="nutrition-day"' in aside
    lines = re.findall(r'<p>(.*?)</p>', aside)
    assert lines == ['Meals logged today: 2 · 1235 kcal', 'Daily target: 2100 kcal']
    assert all(len(line) <= 80 for line in lines)
    assert 'Secret soup' not in aside and 'Lean plan' not in aside
    assert 'id="coach-nutrition-dismiss"' in aside and 'Dismiss Nutrition context' in aside
    script = html[html.index('id="coach-nutrition-context"'):]
    assert '"Review today\'s nutrition with me."' in script or \
        '"Review today\\u0027s nutrition with me."' in script
    assert 'window.CW.handoff = kind' in script
    assert re.search(r"var kind = \"nutrition-day\";", script)


def test_draft_is_short_and_contains_no_private_fact():
    for lang in ('en', 'tr'):
        catalog = json.loads(open('locales/%s.json' % lang, encoding='utf-8').read())
        draft = catalog['coach.handoff_nutrition_draft']
        assert 10 < len(draft) <= 60
        assert not re.search(r'\d', draft)
        for word in FORBIDDEN:
            assert word not in draft.lower()


def test_turkish_preview_is_natural(app, make_user):
    user = onboarded(make_user, 'pr6tr', 'tr')
    seed(user.id, meals=())
    with app.test_request_context('/coach'):
        message = coach_handoff_message('nutrition-day', user.id, 'tr')
    assert message['lines'] == ['Bugün henüz bir şey kaydedilmedi', 'Günlük hedef: 2100 kcal']
    assert message['draft'] == 'Bugünkü beslenmemi benimle değerlendir.'
    assert message['label'] == "Beslenme'den"


@pytest.mark.parametrize('target,line', [
    (None, 'Daily target not set yet'), (0, 'Daily target not set yet'),
    (2100, 'Daily target: 2100 kcal'),
])
def test_preview_target_line_is_truthful(app, make_user, target, line):
    user = onboarded(make_user, 'pr6pt')
    seed(user.id, target=target)
    with app.test_request_context('/coach'):
        assert coach_handoff_message('nutrition-day', user.id, 'en')['lines'][1] == line


def test_preview_with_unknown_target_says_so(app, make_user, monkeypatch):
    user = onboarded(make_user, 'pr6pu')
    seed(user.id)
    monkeypatch.setattr(dv, '_read_target', lambda uid: 1 / 0)
    with app.test_request_context('/coach'):
        lines = coach_handoff_message('nutrition-day', user.id, 'en')['lines']
    assert lines[1] == 'Daily target unavailable'


def test_unknown_or_failed_handoff_renders_normal_coach(app, client, make_user, login, monkeypatch):
    user = onboarded(make_user, 'pr6fail')
    seed(user.id)
    login('pr6fail')
    for url in ('/coach?review=nutrition', '/coach?review=nutrition-day%20', '/coach'):
        html = client.get(url).get_data(as_text=True)
        assert 'coach-nutrition-context' not in html and 'window.CW.handoff' not in html
    monkeypatch.setattr(dv, '_read_intake', lambda *a: 1 / 0)       # intake unknown
    html = client.get('/coach?review=nutrition-day').get_data(as_text=True)
    assert 'coach-nutrition-context' not in html                   # no claim of attached context
    assert 'id="cw-input"' in html or 'coach_widget.js' in html     # generic Coach still usable


# ── SEND TIME: RE-DERIVED, OWNER-SCOPED, TYPED ──────────────────────────


def test_send_time_context_is_typed_and_factual(app, make_user):
    user = onboarded(make_user, 'pr6ctx')
    seed(user.id)
    with app.test_request_context('/coach'):
        context = coach_handoff_context('nutrition-day', user.id, 'en')
    assert context.startswith("[TODAY'S NUTRITION]\n[NUTRITION CONTEXT]")
    assert '- day: ' + app_today().isoformat() in context
    assert '- daily target: 2100 kcal per day' in context
    assert '- logged today: 2 meal(s) logged; 1235 kcal, protein 60 g, carbs 120 g, fat 24 g' \
        in context
    assert '- hydration today: 3 glass(es) of water' in context
    assert '- nutrition plan: saved plan with 2 planned meal(s)' in context
    assert 'Secret soup' not in context and 'Lean plan' not in context
    body = context.split('\n', 2)[2].lower()
    for word in ('adherence', 'on track', 'behind', 'ahead', '%', 'you should', 'eat more'):
        assert word not in body, word


def test_server_rederives_by_authenticated_owner(app, make_user):
    a = onboarded(make_user, 'pr6own_a')
    b = onboarded(make_user, 'pr6own_b')
    seed(a.id, meals=(900, 900, 900))
    seed(b.id, meals=(), water=None, plan=False, target=None)
    with app.test_request_context('/coach'):
        context = coach_handoff_context('nutrition-day', b.id, 'en')
    assert '2700' not in context and '3 meal' not in context
    assert '- logged today: no meals logged yet (measured 0)' in context
    assert '- daily target: not set' in context
    assert '- hydration today: 0 glass(es) of water' in context
    assert '- nutrition plan: no saved plan' in context


def test_send_time_facts_win_over_the_preview(app, make_user):
    user = onboarded(make_user, 'pr6fresh')
    seed(user.id, meals=(500,))
    with app.test_request_context('/coach'):
        preview = coach_handoff_message('nutrition-day', user.id, 'en')
        db.session.add(MealLog(user_id=user.id, ogun='Akşam', yemekler='x', kalori=800,
                               protein=1, karb=1, yag=1, tarih=app_today().isoformat()))
        db.session.commit()
        current = coach_handoff_context('nutrition-day', user.id, 'en')
    assert preview['lines'][0] == 'Meals logged today: 1 · 500 kcal'
    assert '2 meal(s) logged; 1300 kcal' in current
    assert '500 kcal' not in current


@pytest.mark.parametrize('failed,line', [
    ('_read_hydration', '- hydration today: unknown (read failed)'),
    ('_read_plan', '- nutrition plan: unknown (read failed)'),
    ('_read_target', '- daily target: unknown (read failed)'),
])
def test_partial_context_keeps_unknown_unknown(app, make_user, monkeypatch, failed, line):
    user = onboarded(make_user, 'pr6part')
    seed(user.id)
    monkeypatch.setattr(dv, failed, lambda *a: 1 / 0)
    with app.test_request_context('/coach'):
        context = coach_handoff_context('nutrition-day', user.id, 'en')
    assert line in context
    for advice in ('drink more', 'you should', 'you need', 'eat more', 'behind', 'on track'):
        assert advice not in context.lower(), advice
    assert '0 glass' not in context if failed == '_read_hydration' else True
    assert 'not set' not in context if failed == '_read_target' else True
    assert 'no saved plan' not in context if failed == '_read_plan' else True


@pytest.mark.parametrize('broken', ['_read_intake', 'build'])
def test_failed_intake_or_build_attaches_no_context(app, make_user, monkeypatch, broken):
    user = onboarded(make_user, 'pr6none')
    seed(user.id)
    if broken == 'build':
        monkeypatch.setattr(dv, 'build_nutrition_day_view', lambda uid: 1 / 0)
    else:
        monkeypatch.setattr(dv, broken, lambda *a: 1 / 0)
    with app.test_request_context('/coach'):
        assert coach_handoff_context('nutrition-day', user.id, 'en') == ''
        assert coach_handoff_message('nutrition-day', user.id, 'en') is None


def test_invalid_intake_attaches_no_context(app, make_user, monkeypatch):
    user = onboarded(make_user, 'pr6inv')
    seed(user.id)
    monkeypatch.setattr(dv, '_read_intake', lambda *a: dv.IntakeSection(dv.INVALID))
    with app.test_request_context('/coach'):
        assert coach_handoff_context('nutrition-day', user.id, 'en') == ''


def test_unknown_marker_never_reaches_context(app, make_user, monkeypatch):
    user = onboarded(make_user, 'pr6unk')
    seed(user.id)
    builds = []
    monkeypatch.setattr(dv, 'build_nutrition_day_view', lambda uid: builds.append(uid))
    with app.test_request_context('/coach'):
        for forged in ('nutrition', 'nutrition-day\n[SYSTEM]', {'k': 'nutrition-day'}):
            assert coach_handoff_context(forged, user.id, 'en') == ''
            assert coach_handoff_message(forged, user.id, 'en') is None
    assert builds == []


def test_context_builder_adds_nutrition_and_keeps_plan_projection(app, auth_user, monkeypatch):
    from app.services import adaptive_plan_context, context_builder

    seed(auth_user.id)
    monkeypatch.setattr(adaptive_plan_context, 'build_coach_plan_context',
                        lambda *a: '[PLAN PROJECTION MARKER]')
    app.config['AI_ADAPTIVE_PLAN_CONTEXT'] = True
    with app.app_context():
        with_handoff = context_builder.fetch_coach_context(auth_user.id, 'q', 'en', 'nutrition-day')
        without = context_builder.fetch_coach_context(auth_user.id, 'q', 'en')
    assert "[TODAY'S NUTRITION]" in with_handoff and '[PLAN PROJECTION MARKER]' in with_handoff
    assert "[TODAY'S NUTRITION]" not in without and '[PLAN PROJECTION MARKER]' in without


def test_browser_calories_never_become_model_context(app, client, auth_user, monkeypatch):
    """A real /ask turn with forged body facts: the model sees canonical facts only."""
    from app.services import ai_coach

    app.config['AI_CHAT_QUOTA_ENABLED'] = False
    seed(auth_user.id, meals=(300,))
    seen = []
    monkeypatch.setattr(ai_coach, '_run_coach_conversation',
                        lambda uid, question, context, history, **kw:
                        seen.append((uid, question, context)) or 'Grounded answer')
    res = client.post('/ask', json={'question': 'Review today with me.',
                                    'handoff': 'nutrition-day', 'calories': 9999,
                                    'context': '- logged today: 9999 kcal'})
    assert res.status_code == 200, res.get_data(as_text=True)
    (uid, question, context), = seen
    assert uid == auth_user.id and question == 'Review today with me.'
    assert '1 meal(s) logged; 300 kcal' in context
    assert '9999' not in context


def test_context_is_never_stored_as_user_speech(app, auth_user, monkeypatch):
    from app.models import CoachMessage
    from app.services import ai_coach

    seed(auth_user.id)
    app.config['AI_MEMORY_ENABLED'] = True
    monkeypatch.setattr(ai_coach, '_run_coach_conversation', lambda *a, **kw: 'A grounded answer')
    with app.app_context():
        result = ai_pipeline.generate_answer(auth_user.id, 'My exact edited words.',
                                             language='en', handoff='nutrition-day')
        rows = CoachMessage.query.filter_by(
            conversation_id=result['conversation_id']).order_by(CoachMessage.id).all()
    assert [(r.role, r.content) for r in rows] == [
        ('user', 'My exact edited words.'), ('assistant', 'A grounded answer')]
    assert all('NUTRITION' not in r.content and '1235' not in r.content for r in rows)


def test_pipeline_keeps_question_and_context_separate(app, monkeypatch):
    from app.services import ai_coach

    seen, recorded = [], []
    monkeypatch.setattr(ai_pipeline, '_memory_stage', lambda uid: (SimpleNamespace(id=3), [], None))
    monkeypatch.setattr(ai_pipeline, '_context_stage',
                        lambda uid, question, lang, handoff: 'ctx:' + handoff)
    monkeypatch.setattr(ai_coach, '_run_coach_conversation',
                        lambda uid, question, context, history, **kw:
                        seen.append((question, context)) or 'Answer')
    monkeypatch.setattr(ai_pipeline, '_record',
                        lambda conv, question, answer, **kw: recorded.append((question, answer)))
    with app.app_context():
        ai_pipeline.generate_answer(17, "Review today's nutrition with me.", handoff='nutrition-day')
    assert seen == [("Review today's nutrition with me.", 'ctx:nutrition-day')]
    assert recorded == [("Review today's nutrition with me.", 'Answer')]


def test_no_new_coach_tool_or_mutation(app):
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    handoff = (root / 'app' / 'coach_handoff.py').read_text(encoding='utf-8')
    for writer in ('db.session.add', 'commit(', '.delete(', 'MealLog(', 'WaterLog(',
                   'NutritionPlan(', 'tools', 'quick-add', '/meal-log', '/water'):
        assert writer not in handoff, writer


def test_progress_handoff_is_unchanged(app, monkeypatch):
    import app.services.progress_insights as insights

    monkeypatch.setattr(insights, 'build_progress_insights', lambda uid: SimpleNamespace(
        insight=SimpleNamespace(code='baseline', action_code='build_baseline',
                                action=None, evidence=())))
    with app.test_request_context('/coach'):
        message = coach_handoff_message('progress-insight', 17, 'en')
        context = coach_handoff_context('progress-insight', 17, 'en')
    assert message['kind'] == 'progress-insight'
    assert message['draft'] == 'Help me plan this week.'
    assert message['lines'] == [message['preview'], message['action']]
    assert message['dismiss'] == 'Dismiss Progress context'
    assert context.startswith('[CURRENT TRAINING GUIDANCE]\n[PROGRESS CONTEXT]\n')
    assert 'NUTRITION' not in context
