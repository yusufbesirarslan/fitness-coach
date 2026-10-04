"""Regression coverage for PR #383's CI and coach date findings."""
import ast
import json
import shlex
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.services import coach_context_queries as queries

ROOT = Path(__file__).resolve().parents[1]


def test_ci_concurrency_job_collects_every_marked_module():
    workflow = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(),
                         Loader=yaml.BaseLoader)
    job = workflow['jobs']['mobile-pg-concurrency']
    step = next(s for s in job['steps'] if s.get('name') == 'Run deterministic races')
    args = shlex.split(step['run'])
    marked = set()
    for path in (ROOT / 'tests').rglob('test_*.py'):
        tree = ast.parse(path.read_text())
        if any(isinstance(node, ast.Attribute)
               and ast.unparse(node) == 'pytest.mark.pg_concurrency'
               for node in ast.walk(tree)):
            marked.add(path.relative_to(ROOT).as_posix())
    assert marked
    assert marked <= set(args), sorted(marked - set(args))
    assert job['env']['FITX_PG_CONCURRENCY_TEST'] == '1'
    assert job['env']['PG_TEST_DATABASE_URL']


def _stub_reads(monkeypatch, one, many):
    singles, multiples = iter(one), iter(many)
    cursor = SimpleNamespace(execute=lambda *args: None,
                             fetchone=lambda: next(singles),
                             fetchall=lambda: next(multiples))

    @contextmanager
    def connection():
        yield SimpleNamespace(cursor=lambda: cursor)

    monkeypatch.setattr(queries, 'get_conn', connection)


@pytest.mark.parametrize('stamp', [datetime(2026, 10, 3, 22, 30),
                                  datetime(2026, 10, 3, 22, 30, tzinfo=timezone.utc)])
def test_coach_created_at_labels_use_istanbul_day(monkeypatch, stamp):
    user = dict(username='owner', rank_points=0, streak_count=0, last_login=None,
                goal=None, fitness_level=None, current_activity=None, weight=80,
                height=180, age=30, gender=None)
    _stub_reads(monkeypatch, [user, None, None],
                [[dict(weight=80, created_at=stamp)]])
    summary = json.loads(queries.get_user_fitness_summary(1))
    assert summary['recent_checkins'][0]['date'] == '2026-10-04'

    _stub_reads(monkeypatch, [dict(id=1)],
                [[dict(plan_data='{}', score=1, created_at=stamp)], []])
    history = json.loads(queries.get_user_workout_history(1))
    assert history['training_plans'][0]['created_at'] == '2026-10-04'

    supplement = dict.fromkeys(['brand', 'category', 'rating_effect', 'rating_taste',
                               'rating_digestion', 'rating_price', 'review_text', 'price_paid'])
    supplement.update(product_name='test', status='Active', created_at=stamp)
    _stub_reads(monkeypatch, [dict(id=1)], [[supplement]])
    stack = json.loads(queries.get_user_supplement_stack(1))
    assert stack['supplements'][0]['added'] == '2026-10-04'

    meal = dict(ogun='test', yemekler='test', kalori=10, protein=1, karb=1,
                yag=1, tarih=None, created_at=stamp)
    # The stored day remains authoritative when supplied.
    _stub_reads(monkeypatch, [dict(id=1), None],
                [[meal, dict(meal, tarih='2026-10-02')]])
    nutrition = json.loads(queries.get_user_nutrition_log(1))
    assert set(nutrition['daily_logs']) == {'2026-10-04', '2026-10-02'}


def test_nutrition_readers_agree_on_created_at_tie(client, auth_user):
    from app.extensions import db
    from app.models import MealLog, NutritionPlan
    from app.services.nutrition_day_view import _read_plan
    from app.services.nutrition_plan_schema import NAME_KEY
    from app.services.nutrition_plan_store import newest_plan_query

    stamp = datetime(2026, 10, 3, 12)
    for name, kcal in [('older', 100), ('newer', 200)]:
        document = {NAME_KEY: name, 'ogle': {'yemekler': [name], 'kalori': kcal}}
        db.session.add(NutritionPlan(user_id=auth_user.id, created_at=stamp,
                                     plan_data=json.dumps(document), score=8))
    db.session.commit()
    assert json.loads(newest_plan_query(auth_user.id).first().plan_data)[NAME_KEY] == 'newer'
    assert _read_plan(auth_user.id).summary.name == 'newer'
    assert client.get('/nutrition-plan/active').get_json()['plan'][NAME_KEY] == 'newer'
    result = client.post('/api/quick-add-meal', json={'meal_key': 'ogle'})
    assert result.status_code == 200
    assert MealLog.query.filter_by(user_id=auth_user.id).one().yemekler == 'newer'


def test_latest_checkin_tie_keeps_owner_and_real_checkin_filters(app, make_user):
    from app.extensions import db
    from app.models import WeeklyCheckIn
    from app.services.analytics_engine import _latest_checkin

    owner, other = make_user('tieowner'), make_user('tieother')
    stamp = datetime(2026, 10, 3, 12)
    old = WeeklyCheckIn(weight=80, user_id=owner.id, created_at=stamp, yogunluk=1)
    new = WeeklyCheckIn(weight=80, user_id=owner.id, created_at=stamp, yogunluk=2)
    db.session.add_all([old, new,
                        WeeklyCheckIn(weight=80, user_id=owner.id, created_at=stamp),
                        WeeklyCheckIn(weight=80, user_id=other.id, created_at=stamp, yogunluk=3)])
    db.session.commit()
    assert _latest_checkin(owner, {'WeeklyCheckIn': WeeklyCheckIn}).id == new.id
