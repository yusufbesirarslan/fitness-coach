"""NUTR-PR6 — the bounded, server-owned NutritionDayView (contract).

Every claim is checked against the real service, the real tables and the real
browser-session route:

  FACTS MAY BE PARTIAL · CLAIMS MAY NOT EXCEED THE FACTS
  target missing != target failed · zero meals != failed meal read ·
  zero water != failed water read · no plan != failed plan read ·
  invalid != empty · one failed section never erases a sibling ·
  next_action is a deterministic server table over intake + target only ·
  no adherence, score, threshold, prescription, AI, provider or write
"""
import ast
import itertools
import json
import math
import re
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import event, text

from app.extensions import db
from app.models import MealLog, NutritionPlan, User, UserSession, WaterLog
from app.services import nutrition_day_view as dv
from app.timeutil import APP_TZ, app_today, audit_clock

ROOT = Path(__file__).resolve().parent.parent
SERVICE = (ROOT / 'app' / 'services' / 'nutrition_day_view.py').read_text(encoding='utf-8')
ROUTE = (ROOT / 'app' / 'blueprints' / 'nutrition' / 'day_view.py').read_text(encoding='utf-8')
PLAN = {'isim': 'Lean plan',
        'kahvalti': {'kalori': 450, 'protein': 30, 'karb': 50, 'yag': 12, 'yemekler': ['Oats']},
        'aksam': {'kalori': 650, 'protein': 45, 'karb': 60, 'yag': 20, 'yemekler': ['Salmon']}}
TOP_KEYS = {'contract_version', 'day', 'target', 'intake', 'hydration', 'plan', 'next_action'}
FORBIDDEN = ('adherence', 'score', 'on_track', 'off_track', 'behind', 'ahead', 'gap',
             'percent', 'compliance', 'eat_more', 'eat_less', 'drink_more', 'hit_protein',
             'follow_plan', 'change_plan', 'needs_more')


# ── helpers ─────────────────────────────────────────────────────────────


def user(make_user, name='pr6'):
    return make_user(name, profile_complete=True)


def seed(user_id, target=2100, meals=((525, 30, 60, 12), (710, 42, 80, 18)), water=3,
         plan=PLAN, day=None):
    day = day or app_today().isoformat()
    if target is not None:
        session = UserSession.query.filter_by(user_id=user_id).first()
        if session is None:
            session = UserSession(user_id=user_id)
            db.session.add(session)
        session.target_calories = target
        session.goal = 'kas kazanma'
    for kcal, p, c, f in meals:
        db.session.add(MealLog(user_id=user_id, ogun='Öğle', yemekler='x', kalori=kcal,
                               protein=p, karb=c, yag=f, tarih=day))
    if water is not None:
        db.session.add(WaterLog(user_id=user_id, date_key=day, count=water))
    if plan is not None:
        db.session.add(NutritionPlan(user_id=user_id, score=8,
                                     plan_data=plan if isinstance(plan, str)
                                     else json.dumps(plan, ensure_ascii=False)))
    db.session.commit()


def payload(user_id):
    return dv.nutrition_day_view_payload(dv.build_nutrition_day_view(user_id))


class Statements:
    """Every SQL statement the build issues (SAVEPOINTs included)."""

    def __enter__(self):
        self.seen = []
        self.engine = db.engine
        event.listen(self.engine, 'before_cursor_execute', self._record)
        return self

    def _record(self, conn, cursor, statement, params, context, executemany):
        self.seen.append(statement.strip().split()[0].upper())

    def __exit__(self, *exc):
        event.remove(self.engine, 'before_cursor_execute', self._record)


# ── SHAPE / OWNERSHIP ───────────────────────────────────────────────────


def test_payload_is_the_bounded_semantic_shape(app, make_user):
    u = user(make_user)
    seed(u.id)
    body = payload(u.id)
    assert set(body) == TOP_KEYS
    assert body['contract_version'] == 1
    assert body['day'] == app_today().isoformat()
    assert set(body['target']) == {'state', 'value', 'unit'}
    assert set(body['intake']) == {'state', 'totals', 'meal_count'}
    assert set(body['intake']['totals']) == {'calories', 'protein', 'carbs', 'fat'}
    assert set(body['hydration']) == {'state', 'amount', 'unit'}
    assert set(body['plan']) == {'state', 'summary'}
    assert set(body['plan']['summary']) == {'name', 'planned_meal_count'}
    assert set(body['next_action']) == {'state', 'kind', 'label_key'}
    assert body['target'] == {'state': 'available', 'value': 2100.0, 'unit': 'kcal'}
    assert body['intake'] == {'state': 'available', 'meal_count': 2, 'totals': {
        'calories': 1235.0, 'protein': 72.0, 'carbs': 140.0, 'fat': 30.0}}
    assert body['hydration'] == {'state': 'available', 'amount': 3, 'unit': 'glass'}
    assert body['plan'] == {'state': 'available',
                            'summary': {'name': 'Lean plan', 'planned_meal_count': 2}}
    assert body['next_action'] == {'state': 'available', 'kind': 'log_food',
                                   'label_key': 'nutrition.next.log_food'}


def test_no_web_dom_concept_or_sentence_in_the_contract(app, make_user):
    u = user(make_user)
    seed(u.id)
    flat = json.dumps(payload(u.id))
    for dom in ('button', 'css', 'class', 'modal', 'href', 'url', '<', 'onclick'):
        assert dom not in flat, dom
    # label keys only — no user-facing sentence leaves the service
    assert not re.search(r'[A-Za-z]+ [A-Za-z]+ [A-Za-z]+', flat.replace('Lean plan', ''))


def test_view_is_owner_scoped(app, make_user):
    a, b = user(make_user, 'pr6a'), user(make_user, 'pr6b')
    seed(a.id)
    body = payload(b.id)
    assert body['intake']['meal_count'] == 0 and body['plan']['state'] == 'empty'
    assert body['hydration'] == {'state': 'empty', 'amount': 0, 'unit': 'glass'}


def test_route_is_a_thin_authenticated_private_transport(app, client, make_user, login):
    anonymous = client.get('/nutrition-day-view')
    assert anonymous.status_code in (302, 401)
    a, b = user(make_user, 'pr6ra'), user(make_user, 'pr6rb')
    seed(a.id)
    login('pr6rb')
    res = client.get('/nutrition-day-view?user_id=%d&day=2020-01-01&next_action=eat_more' % a.id)
    assert res.status_code == 200
    assert res.headers['Cache-Control'] == 'private, no-store'
    body = res.get_json()
    assert body == payload(b.id)                     # query string never selects anything
    assert body['intake']['meal_count'] == 0
    # The route delegates; it holds no rule of its own.
    tree = ast.parse(ROUTE)
    calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)}
    assert {'build_nutrition_day_view', 'nutrition_day_view_payload'} <= calls
    body_src = ROUTE.split('def nutrition_day_view')[1]
    assert not re.search(r'(?<![_a-z])request\.', body_src)          # reads no query/body/header


def test_route_failure_is_typed_never_a_partial_view(app, client, make_user, login, monkeypatch):
    user(make_user, 'pr6rf')
    login('pr6rf')
    monkeypatch.setattr('app.blueprints.nutrition.day_view.build_nutrition_day_view',
                        lambda uid: (_ for _ in ()).throw(RuntimeError('bug')))
    res = client.get('/nutrition-day-view')
    assert res.status_code == 503
    assert res.get_json() == {'error': 'nutrition_day_view_unavailable'}
    assert res.headers['Cache-Control'] == 'private, no-store'


def test_no_browser_session_route_is_native(app):
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert '/nutrition-day-view' in rules
    # NUTR-PR7 wraps the same service in exactly ONE native transport.
    native = {r for r in rules if r.startswith('/api/') and ('day-view' in r or 'day_view' in r)}
    assert native == {'/api/v1/nutrition/day-view'}


# ── PERSISTENCE / WRITES / AI ───────────────────────────────────────────


def test_read_model_is_a_projection_not_persistence(app, make_user):
    tree = ast.parse(SERVICE)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = {ast.unparse(b) for b in node.bases}
            assert not bases & {'db.Model', 'Model'}, node.name
        if isinstance(node, ast.Attribute):
            assert node.attr not in {'add', 'add_all', 'commit', 'flush', 'delete',
                                     'merge', 'execute', 'bulk_save_objects'}, node.attr
    assert not any('day_view' in name for name in db.metadata.tables)


def test_exact_canonical_authorities_and_bounded_statements(app, make_user):
    u = user(make_user)
    seed(u.id)
    imported = {n.module for n in ast.walk(ast.parse(SERVICE))
                if isinstance(n, ast.ImportFrom)}
    assert imported == {'__future__', 'dataclasses', 'datetime', 'sqlalchemy', 'app.extensions',
                        'app.models', 'app.services.nutrition_plan_schema',
                        'app.services.nutrition_targets', 'app.timeutil'}
    models = next(n for n in ast.walk(ast.parse(SERVICE))
                  if isinstance(n, ast.ImportFrom) and n.module == 'app.models')
    assert {a.name for a in models.names} == {'MealLog', 'NutritionPlan', 'UserSession', 'WaterLog'}
    uid = u.id
    db.session.expire_all()
    with Statements() as sql:
        dv.build_nutrition_day_view(uid)
    assert sql.seen.count('SELECT') == 4, sql.seen          # one per section, no N+1
    assert not set(sql.seen) - {'SELECT', 'SAVEPOINT', 'RELEASE'}, sql.seen


def test_bounded_regardless_of_history_size(app, make_user):
    u = user(make_user)
    seed(u.id, meals=[(100, 1, 1, 1)] * 40)
    for i in range(30):
        db.session.add(MealLog(user_id=u.id, ogun='Öğle', yemekler='old', kalori=900,
                               protein=1, karb=1, yag=1, tarih='2020-01-%02d' % (i % 28 + 1)))
    db.session.commit()
    uid = u.id
    with Statements() as sql:
        body = payload(uid)
    assert sql.seen.count('SELECT') == 4
    assert body['intake']['meal_count'] == 40                # only today, no history


def test_no_ai_provider_or_network_on_build_or_route(app, client, make_user, login, monkeypatch):
    user(make_user, 'pr6net')
    seed(User.query.filter_by(username='pr6net').one().id)
    login('pr6net')

    def refuse(*a, **k):
        raise AssertionError('network/provider used by the day view')
    monkeypatch.setattr(socket.socket, 'connect', refuse)
    import app.services.ai as ai
    for name in ('_heavy_chat', '_openai_chat', '_claude_chat'):
        if hasattr(ai, name):
            monkeypatch.setattr(ai, name, refuse)
    from app.services import ai_coach
    monkeypatch.setattr(ai_coach, '_run_coach_conversation', refuse)
    assert client.get('/nutrition-day-view').status_code == 200
    assert client.get('/nutrition').status_code == 200
    forbidden_imports = ('ai', 'ai_coach', 'fatsecret', 'openai', 'boto3', 'requests', 'httpx')
    assert not any(re.search(r'\b%s\b' % m, line) for m in forbidden_imports
                   for line in SERVICE.splitlines() if line.startswith(('import', 'from')))


def test_no_score_threshold_or_prescription_semantics(app, make_user):
    tree = ast.parse(SERVICE)
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
                  and n.body and isinstance(n.body[0], ast.Expr)
                  and isinstance(n.body[0].value, ast.Constant)}
    tokens = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            tokens.append(node.id)
        elif isinstance(node, ast.Attribute):
            tokens.append(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            tokens.append(node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings:
            tokens.append(node.value)
    for token in tokens:
        for word in FORBIDDEN:
            assert word not in token.lower(), (word, token)
    # no numeric threshold compares a fact against the target
    compares = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Compare)]
    assert not [c for c in compares if 'target' in c and ('intake' in c or 'totals' in c)]
    u = user(make_user)
    seed(u.id)
    flat = json.dumps(payload(u.id)).lower()
    for word in FORBIDDEN:
        assert word not in flat, word


# ── SECTION STATES ──────────────────────────────────────────────────────


@pytest.mark.parametrize('stored,expected', [
    ('none', ('empty', None)), (None, ('empty', None)), (0, ('empty', None)),
    (-50, ('empty', None)), (2100.4, ('available', 2100.4)),
    (float('inf'), ('invalid', None)),
])
def test_target_states(app, make_user, stored, expected):
    u = make_user('pr6t')
    if stored != 'none':
        db.session.add(UserSession(user_id=u.id, target_calories=stored))
        db.session.commit()
    body = payload(u.id)
    assert (body['target']['state'], body['target']['value']) == expected
    assert body['target']['value'] != 0                      # never 0 kcal


def test_target_nan_is_invalid_not_empty(app, make_user, monkeypatch):
    u = user(make_user)
    # SQLite cannot store NaN; feed the canonical read a NaN the way PostgreSQL can.
    class Query:
        def filter_by(self, **k): return self
        def with_entities(self, *a): return self
        def order_by(self, *a): return self
        def first(self): return (float('nan'), 'kas kazanma')
    monkeypatch.setattr(dv.UserSession, 'query', Query())
    assert payload(u.id)['target'] == {'state': 'invalid', 'value': None, 'unit': 'kcal'}


def test_zero_meals_is_measured_zero_not_failure(app, make_user):
    u = user(make_user)
    seed(u.id, meals=())
    assert payload(u.id)['intake'] == {'state': 'empty', 'meal_count': 0, 'totals': {
        'calories': 0.0, 'protein': 0.0, 'carbs': 0.0, 'fat': 0.0}}


class _Aggregate:
    """The ledger aggregate as a corrupt row would produce it. The table's own
    CHECK constraint refuses negative macros and PostgreSQL can hold NaN, so
    the read's guard is exercised on the value the query would return."""

    def __init__(self, row):
        self.row = row

    def filter(self, *a):
        return self

    def one(self):
        return self.row


def corrupt_ledger(monkeypatch, row):
    real = db.session.query

    def query(*entities):
        if len(entities) == 5:                     # the intake aggregate
            return _Aggregate(row)
        return real(*entities)
    monkeypatch.setattr(db.session, 'query', query)


@pytest.mark.parametrize('row', [(1, -500.0, 1, 1, 1), (1, float('nan'), 1, 1, 1),
                                 (1, 100.0, float('inf'), 1, 1), (-1, 0, 0, 0, 0),
                                 (1, 'abc', 0, 0, 0)])
def test_invalid_persisted_meals_are_not_empty(app, make_user, monkeypatch, row):
    u = user(make_user)
    seed(u.id, meals=((500, 1, 1, 1),))
    uid = u.id
    corrupt_ledger(monkeypatch, row)
    body = payload(uid)
    assert body['intake'] == {'state': 'invalid', 'totals': None, 'meal_count': None}
    assert body['next_action'] == {'state': 'empty', 'kind': None,
                                   'label_key': 'nutrition.next.none'}


def test_null_macros_follow_the_ledger_rule(app, make_user):
    """/meal-log/today sums `m.kalori or 0`; SQL SUM skips NULL — same totals."""
    u = user(make_user)
    db.session.add(MealLog(user_id=u.id, ogun='Öğle', yemekler='x', kalori=300,
                           protein=None, karb=None, yag=None, tarih=app_today().isoformat()))
    db.session.commit()
    assert payload(u.id)['intake']['totals'] == {'calories': 300.0, 'protein': 0.0,
                                                 'carbs': 0.0, 'fat': 0.0}


@pytest.mark.parametrize('count,expected', [(None, ('empty', 0)), (0, ('empty', 0)),
                                            (5, ('available', 5)), (-1, ('invalid', None))])
def test_hydration_states(app, make_user, count, expected):
    u = user(make_user)
    seed(u.id, water=count)
    h = payload(u.id)['hydration']
    assert (h['state'], h['amount']) == expected


@pytest.mark.parametrize('document,expected', [
    (None, ('empty', None)),
    (PLAN, ('available', {'name': 'Lean plan', 'planned_meal_count': 2})),
    ({'kahvalti': {'yemekler': ['Eggs']}}, ('available', {'name': None, 'planned_meal_count': 1})),
    ('{not json', ('invalid', None)),
    (json.dumps(['a list']), ('invalid', None)),
    ({'isim': 'Names only', 'toplam_kalori': 1500}, ('invalid', None)),
])
def test_plan_states(app, make_user, document, expected):
    u = user(make_user)
    seed(u.id, plan=document)
    p = payload(u.id)['plan']
    assert (p['state'], p['summary']) == expected


def test_newest_saved_plan_is_the_only_plan_authority(app, make_user):
    u = user(make_user)
    seed(u.id, plan={'isim': 'Old', 'aksam': {'yemekler': ['x']}})
    db.session.add(NutritionPlan(user_id=u.id, score=5, created_at=datetime(2099, 1, 1),
                                 plan_data=json.dumps(PLAN)))
    db.session.commit()
    assert payload(u.id)['plan']['summary']['name'] == 'Lean plan'


# ── FAILURE ISOLATION ───────────────────────────────────────────────────


SECTIONS = {'target': '_read_target', 'intake': '_read_intake',
            'hydration': '_read_hydration', 'plan': '_read_plan'}


@pytest.mark.parametrize('failed', list(SECTIONS))
def test_one_failed_section_never_erases_a_sibling(app, make_user, monkeypatch, failed):
    u = user(make_user)
    seed(u.id)
    healthy = payload(u.id)

    def failing(*args):
        # A REAL failing statement inside the section's own savepoint.
        with db.session.begin_nested():
            db.session.execute(text('SELECT * FROM no_such_pr6_table'))
    monkeypatch.setattr(dv, SECTIONS[failed], failing)
    body = payload(u.id)
    assert body[failed]['state'] == 'unavailable'
    for key, value in body[failed].items():
        if key not in ('state', 'unit'):
            assert value is None, (failed, key, value)          # unknown, never 0 / empty
    for sibling in SECTIONS:
        if sibling != failed:
            assert body[sibling] == healthy[sibling], sibling
    # the session is still usable after the failed statement
    assert User.query.get(u.id) is not None


def test_all_sections_failing_is_still_a_truthful_view(app, make_user, monkeypatch):
    u = user(make_user)
    for name in SECTIONS.values():
        monkeypatch.setattr(dv, name, lambda *a: 1 / 0)
    body = payload(u.id)
    assert {body[s]['state'] for s in SECTIONS} == {'unavailable'}
    assert body['next_action'] == {'state': 'available', 'kind': 'retry',
                                   'label_key': 'nutrition.next.retry'}


# ── DAY / TIMEZONE ──────────────────────────────────────────────────────


@pytest.mark.parametrize('instant,expected_day', [
    ('2026-09-30T23:30:00+03:00', '2026-09-30'),   # 20:30 UTC — still the Istanbul day
    ('2026-10-01T00:30:00+03:00', '2026-10-01'),   # 21:30 UTC Sep 30 — already Oct 1 here
])
def test_day_boundary_matches_nutrition_today(app, client, make_user, login, instant, expected_day):
    u = user(make_user, 'pr6tz')
    login('pr6tz')
    for day, kcal in (('2026-09-30', 400), ('2026-10-01', 900)):
        db.session.add(MealLog(user_id=u.id, ogun='Öğle', yemekler='x', kalori=kcal,
                               protein=1, karb=1, yag=1, tarih=day))
        db.session.add(WaterLog(user_id=u.id, date_key=day, count=2 if day.endswith('30') else 6))
    db.session.commit()
    with audit_clock(datetime.fromisoformat(instant).astimezone(APP_TZ)):
        view = client.get('/nutrition-day-view').get_json()
        today = client.get('/meal-log/today').get_json()
        water = client.get('/water').get_json()
    assert view['day'] == expected_day
    utc_day = datetime.fromisoformat(instant).astimezone(timezone.utc).date().isoformat()
    if expected_day == '2026-10-01':
        assert utc_day == '2026-09-30'          # a UTC guess would pick the wrong day here
    assert view['intake']['totals']['calories'] == today['totals']['kalori']
    assert view['intake']['meal_count'] == len(today['meals'])
    assert view['hydration']['amount'] == water['count']


# ── NEXT ACTION ─────────────────────────────────────────────────────────


NEXT_TABLE = [
    # (intake, target) → kind (None = no actionable recommendation)
    ('unavailable', 'available', 'retry'), ('unavailable', 'empty', 'retry'),
    ('unavailable', 'unavailable', 'retry'), ('unavailable', 'invalid', 'retry'),
    ('invalid', 'available', None), ('invalid', 'empty', None),
    ('invalid', 'unavailable', None), ('invalid', 'invalid', None),
    ('available', 'empty', 'set_target'), ('empty', 'empty', 'set_target'),
    ('available', 'available', 'log_food'), ('empty', 'available', 'log_food'),
    ('available', 'unavailable', 'log_food'), ('empty', 'unavailable', 'log_food'),
    ('available', 'invalid', 'log_food'), ('empty', 'invalid', 'log_food'),
    ('loading', 'available', None), ('bogus', 'available', None),
    ('available', 'bogus', None), (None, None, None),
]


@pytest.mark.parametrize('intake,target,kind', NEXT_TABLE)
def test_next_action_decision_table(intake, target, kind):
    action = dv.derive_next_action(intake, target)
    if kind is None:
        assert action == dv.NextAction(state='empty', kind=None, label_key='nutrition.next.none')
    else:
        assert action == dv.NextAction(state='available', kind=kind,
                                       label_key='nutrition.next.' + kind)


def test_next_action_table_is_total_and_deterministic():
    states = dv.SERVER_SECTION_STATES
    for intake, target in itertools.product(states, states):
        first = dv.derive_next_action(intake, target)
        assert first == dv.derive_next_action(intake, target)
        assert first.kind in (None,) + dv.NEXT_ACTION_KINDS
    assert set(dv.NEXT_ACTION_KINDS) == {'log_food', 'set_target', 'retry'}
    assert set(dv.NEXT_ACTION_LABEL_KEYS) == set(dv.NEXT_ACTION_KINDS)


def test_next_action_ignores_hydration_and_plan(app, make_user, monkeypatch):
    u = user(make_user)
    seed(u.id)
    expected = payload(u.id)['next_action']
    for broken in ('_read_hydration', '_read_plan'):
        with monkeypatch.context() as m:
            m.setattr(dv, broken, lambda *a: 1 / 0)
            assert payload(u.id)['next_action'] == expected   # no stronger action invented
    params = list(ast.parse(SERVICE).body)
    fn = next(n for n in params if isinstance(n, ast.FunctionDef) and n.name == 'derive_next_action')
    assert [a.arg for a in fn.args.args] == ['intake_state', 'target_state']


@pytest.mark.parametrize('seed_kw,kind', [
    ({}, 'log_food'),
    ({'meals': ()}, 'log_food'),
    ({'target': None}, 'set_target'),
    ({'target': 0}, 'set_target'),
])
def test_next_action_through_the_real_reads(app, make_user, seed_kw, kind):
    u = make_user('pr6na')
    seed(u.id, **seed_kw)
    assert payload(u.id)['next_action']['kind'] == kind


def test_labels_resolve_in_both_locales():
    for lang in ('en', 'tr'):
        catalog = json.loads((ROOT / 'locales' / f'{lang}.json').read_text(encoding='utf-8'))
        for key in list(dv.NEXT_ACTION_LABEL_KEYS.values()) + [dv.NO_ACTION_LABEL_KEY]:
            assert catalog.get(key, '').strip(), (lang, key)


# ── CLIENT RENDERS, NEVER DECIDES ───────────────────────────────────────


SCRIPT = (ROOT / 'static' / 'nutrition.js').read_text(encoding='utf-8')
PR6_KEYS = ['nutrition.next.title', 'nutrition.next.loading', 'nutrition.next.log_food',
            'nutrition.next.log_food_lead', 'nutrition.next.set_target',
            'nutrition.next.set_target_lead', 'nutrition.next.retry', 'nutrition.next.retry_lead',
            'nutrition.next.none', 'nutrition.next.unavailable', 'nutrition.review_with_axisai',
            'coach.handoff_from_nutrition', 'coach.handoff_nutrition_dismiss',
            'coach.handoff_nutrition_draft', 'coach.handoff_nutrition_intake',
            'coach.handoff_nutrition_intake_empty', 'coach.handoff_nutrition_target',
            'coach.handoff_nutrition_target_absent', 'coach.handoff_nutrition_target_unknown']
COPY_FORBIDDEN = ('on track', 'off track', 'behind', 'ahead', 'adherence', 'score', '%',
                  'you should', 'you need', 'eat more', 'eat less', 'drink more', 'protein gap',
                  'yolunda', 'geride', 'uyum', 'puan', 'yemelisin', 'içmelisin')


def _pr6_block(script):
    script = script.replace('\r\n', '\n')
    start = script.index('/* ── NUTR-PR6 NEXT STEP')
    end = script.index('function retryDayView()', start)
    block = script[start:end]
    block = re.sub(r'/\*.*?\*/', '', block, flags=re.S)
    return re.sub(r'//[^\n]*', '', block)


def test_next_step_is_rendered_never_decided_client_side(script=None):
    code = _pr6_block(script or SCRIPT)
    code_no_strings = re.sub(r"'[^'\n]*'", "''", code)
    for fact in ('totals', 'meal_count', 'kalori', 'targetState', 'hydration',
                 'water', 'intake', 'plan', 'Math.', 'localStorage', 'sessionStorage',
                 'setInterval', 'eval(', 'new Function', 'location.assign', 'location.href',
                 'window[', 'innerHTML'):
        assert fact not in code_no_strings, fact
    assert not re.search(r'(?<!set_)target', code_no_strings)
    assert code.count("fetch('/nutrition-day-view'") == 1
    assert 'view.next_action' in code
    # the ONLY source of the kind is the server field, checked against the allowlist
    assert re.search(r"NEXT_ACTIONS\[na\.kind\]\.label === na\.label_key", code)
    assert 'hasOwnProperty.call(NEXT_ACTIONS' in code


def test_day_view_is_read_once_at_load_and_never_on_redraw(script=None):
    script = (script or SCRIPT).replace('\r\n', '\n')
    assert script.rstrip().endswith('loadDayView();')
    callers = [m.start() for m in re.finditer(r'(?<!function )\bloadDayView\(\)', script)]
    owners = set()
    for pos in callers:
        head = script.rfind('\nfunction ', 0, pos)
        head2 = script.rfind('\nasync function ', 0, pos)
        owners.add(script[max(head, head2):pos].split('(')[0].split()[-1]
                   if max(head, head2) > script.rfind('/* ── INIT', 0, pos) else 'INIT')
    assert owners == {'loadTodayData', 'retryDayView', 'INIT'}, owners
    body = script[script.index('async function loadTodayData('):script.index('function retryTodayData')]
    assert "if (_nextKind === 'retry') loadDayView();" in body   # only a pending retry re-reads


def test_pr6_copy_is_plain_bounded_and_at_parity(catalogs=None):
    catalogs = catalogs or {lang: json.loads((ROOT / 'locales' / f'{lang}.json')
                                             .read_text(encoding='utf-8')) for lang in ('en', 'tr')}
    assert set(catalogs['en']) == set(catalogs['tr'])
    for key in PR6_KEYS:
        for lang, catalog in catalogs.items():
            copy = catalog[key]
            assert copy.strip() and len(copy) <= 60, (lang, key, copy)
            assert not re.search(r'\b(null|undefined|nutrition\.|coach\.)', copy), (lang, key)
            for word in COPY_FORBIDDEN:
                assert word not in copy.lower(), (lang, key, word)
