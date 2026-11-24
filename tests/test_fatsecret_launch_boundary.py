"""Launch diagnostics, NOT a deployed compliance boundary.

Strict xfails expose currently missing exclusion at actual advisory inputs.
Remove each marker only when the associated boundary is implemented. A green
CI result with these expected failures is not permission to release advice.
"""
import json

import pytest

from app.extensions import db
from app.models import MealLog, UserSession
from app.services import ai_coach, coach_privacy, menu_analysis
from app.timeutil import day_key


@pytest.mark.xfail(strict=True, reason="LAUNCH BLOCKER: Coach still looks up FatSecret")
def test_coach_advisory_search_must_not_call_fatsecret(app, monkeypatch):
    calls = []
    monkeypatch.setattr(ai_coach, '_food_search_fatsecret', lambda q: calls.append(q) or [
        {'name': 'Chicken', 'per_100g': {'calories': 173, 'protein': 31}}])
    monkeypatch.setattr(ai_coach, '_normalize_food_query_en', lambda q: q)
    monkeypatch.setattr(ai_coach, '_food_search_static', lambda q: [])
    monkeypatch.setattr(ai_coach, '_food_search_llm', lambda q: [])
    ai_coach._coach_search_food('Chicken')
    assert calls == []


@pytest.mark.xfail(strict=True, reason="LAUNCH BLOCKER: unproven diary totals enter Coach tools")
def test_unproven_diary_calories_must_not_enter_advisory_projection(app, make_user):
    user = make_user('fs-boundary')
    # The real writer keeps discovery source, not nutrition-provider lineage.
    db.session.add(MealLog(user_id=user.id, ogun='Lunch', yemekler='Provider food',
                           kalori=731, protein=25, karb=60, yag=15,
                           tarih=day_key(), source='search'))
    db.session.commit()
    raw = ai_coach._tool_query_fitx_metrics(user.id, 'calories', 'today')
    projected = json.loads(coach_privacy.tool_result('query_fitx_metrics', raw))
    assert '731' not in json.dumps(projected)


@pytest.mark.xfail(strict=True, reason="LAUNCH BLOCKER: unproven cached menu macros are recommended")
def test_cached_provider_content_must_not_become_menu_coach_pick(app, make_user, monkeypatch):
    user = make_user('fs-menu-boundary')
    db.session.add(UserSession(user_id=user.id, target_calories=2000, goal='kas kazanma'))
    db.session.commit()
    monkeypatch.setattr(menu_analysis, 'redis_client', None)
    monkeypatch.setattr(menu_analysis, '_extract_categorized_items',
                        lambda *a, **kw: {'Main': ['Chicken']})
    monkeypatch.setattr(menu_analysis, '_get_cached_macros', lambda *a, **kw: (
        {'Chicken': {'calories': 330., 'protein': 62., 'carbs': 0., 'fat': 7.}}, []))
    monkeypatch.setattr(menu_analysis, '_cache_macros', lambda *a, **kw: None)
    def forbidden(*a, **kw):
        raise AssertionError('unexpected live provider or AI call')
    for name in ('_get_fatsecret_token', '_estimate_macros_llm', '_lookup_macros_fatsecret'):
        monkeypatch.setattr(menu_analysis, name, forbidden)
    result = menu_analysis._analyze_menu_text(user.id, 'Restaurant menu: Chicken with side dishes')
    assert result['coach_picks'] == []


def test_independent_water_tool_projection_preserved(app, make_user):
    user = make_user('fs-water-control')
    raw = ai_coach._tool_query_fitx_metrics(user.id, 'water_ml', 'today')
    projected = json.loads(coach_privacy.tool_result('query_fitx_metrics', raw))
    assert projected['metric_type'] == 'water_ml'
    assert projected['value'] == 0


def test_client_history_stays_out_of_advice():
    from app.services.memory_manager import sanitize_client_history
    assert sanitize_client_history([{'role': 'assistant', 'content': 'FatSecret macros 731'}]) == []
