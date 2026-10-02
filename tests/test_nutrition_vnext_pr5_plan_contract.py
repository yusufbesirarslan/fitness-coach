"""NUTR-PR5 — Nutrition → Plan contract (structure, authority, copy, backend invariant).

Rendered `/nutrition`, the shipped script, the real save/active routes and
the locale catalogs, asserted by meaning:

  * Nutrition keeps exactly Today | Plan on `/nutrition`; no Plan page/route;
  * Plan leads with target → current plan → planned meals, and generation is
    a subordinate, closed-by-default workflow;
  * NutritionPlan (via the ONE shared `/nutrition-plan/active` read) is the
    current plan; a generated option is a proposal that only
    `/nutrition-plan/save` can make current, and nothing is marked current
    before the save and a canonical re-read;
  * absent ≠ read failure, target missing ≠ target failure, Planned ≠ Logged;
  * Supplements is a child link, Grocery Guide is absent;
  * the save route validates score and schema BEFORE it deletes;
  * no eager generation or provider call; no schema change; EN/TR parity.

Every check takes its source as a parameter so the PR5 non-vacuity suite can
run it against a mutated script / template / route and watch it fail.
"""
import ast
import html as html_lib
import json
import re
from pathlib import Path

import pytest

from test_nutrition_vnext_pr2_navigation_contract import Elements, render

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (ROOT / 'static' / 'nutrition.js').read_text(encoding='utf-8').replace('\r\n', '\n')
TEMPLATE = (ROOT / 'templates' / 'nutrition.html').read_text(encoding='utf-8').replace('\r\n', '\n')
PLAN_ROUTES = (ROOT / 'app' / 'blueprints' / 'nutrition' / 'plan.py').read_text(encoding='utf-8')

INTERNAL_WORDS = ('nutritionplan', 'plan_data', 'overall_score', 'schema', 'proposal_state',
                  'ai_plan', 'activeplanresponse', 'score_label', 'json')
OVERCLAIMS = ('optimal', 'optimum', 'perfect', 'guarantee', 'personaliz', 'scientific', 'ready',
              'safe', 'mükemmel', 'garanti', 'kişiselleştir', 'bilimsel', 'güvenli')
PLAN_FUNCTIONS = ('renderPlanTarget', 'renderCurrentPlan', 'renderCurrentPlanUnavailable',
                  'loadActivePlan', 'renderActivePlanDetail', 'generatePlan', 'renderPlans',
                  'selectPlan', 'openPlanReplace', 'cancelPlanReplace', 'confirmPlanReplace',
                  'savePlanOption', 'finishPlanSave', 'openPlanBuilder', 'closePlanBuilder')


def _catalog(language):
    return json.loads((ROOT / 'locales' / f'{language}.json').read_text(encoding='utf-8'))


def _text(markup):
    return re.sub(r'\s+', ' ', html_lib.unescape(re.sub(r'<[^>]+>', ' ', markup))).strip()


def function_body(name, script=SCRIPT):
    match = re.search(r'^(?:async )?function ' + re.escape(name) + r'\(', script, re.M)
    assert match, name
    end = script.find('\n}\n', match.start())
    return script[match.start():end + 2]


def function_containing(index, script=SCRIPT):
    starts = [m for m in re.finditer(r'^(?:async )?function (\w+)\(', script, re.M) if m.start() < index]
    return starts[-1].group(1)


def plan_panel(html):
    start = html.index('id="panel-plan"')
    return html[start:html.index('<!-- /panel-plan -->', start)]


# ── IA / ROUTES ─────────────────────────────────────────────────────────


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_exactly_today_and_plan_on_the_canonical_route(client, make_user, login, language):
    html = render(client, make_user, login, language)
    rows = Elements(html).rows
    assert [a['data-tab-name'] for _, a, _ in rows if a.get('role') == 'tab'] == ['today', 'plan']
    assert len([a for _, a, _ in rows if a.get('role') == 'tabpanel']) == 2
    assert html.count('<h1') == 1
    title = re.search(r'<h2[^>]*id="nut-plan-title"[^>]*>(.*?)</h2>', html, re.S)
    assert title and _text(title.group(1)) == _catalog(language)['nutrition.plan.title']


def test_plan_leads_with_current_state_and_keeps_generation_subordinate(client, make_user, login,
                                                                       html=None):
    html = html or render(client, make_user, login, 'en')
    panel = plan_panel(html)
    order = [panel.index(marker) for marker in (
        'id="plan-target"', 'id="plan-current"', 'id="plan-builder"', 'class="nutrition-child-domain"')]
    assert order == sorted(order), order
    builder = re.search(r'<section[^>]*id="plan-builder"[^>]*>', panel).group(0)
    assert ' hidden' in builder                                   # closed until Create/Replace
    generate = panel.index('id="plan-btn"')
    assert panel.index('id="plan-builder"') < generate < panel.index('class="nutrition-child-domain"')
    assert 'data-plan-state="loading"' in re.search(r'<section[^>]*id="plan-current"[^>]*>', panel).group(0)
    for region in ('plan-current-status', 'plan-gen-status'):
        assert re.search(r'id="%s" role="status" aria-live="polite"' % region, panel), region


def test_no_new_nutrition_plan_route(app):
    rules = list(app.url_map.iter_rules())
    plan_rules = {(r.rule, m) for r in rules for m in (r.methods or ()) - {'HEAD', 'OPTIONS'}
                  if r.rule.startswith('/nutrition-plan')}
    assert plan_rules == {('/nutrition-plan', 'POST'), ('/nutrition-plan/save', 'POST'),
                          ('/nutrition-plan/active', 'GET')}, plan_rules
    names = {r.rule for r in rules}
    assert '/nutrition' in names
    for forbidden in ('/nutrition/plan', '/plan/nutrition', '/plan'):
        assert forbidden not in names, forbidden
    assert not any(r.rule.startswith('/nutrition/') for r in rules)


# ── AUTHORITY / PROPOSAL / SAVE ─────────────────────────────────────────


def test_current_plan_authority_is_the_one_shared_active_read(script=SCRIPT):
    assert script.count("'/nutrition-plan/active'") == 1
    assert function_containing(script.index("'/nutrition-plan/active'"), script) == 'getActivePlan'
    callers = sorted({function_containing(m.start(), script)
                      for m in re.finditer(r'\bgetActivePlan\(', script)} - {'getActivePlan'})
    assert callers == ['loadActivePlan', 'loadQuickAddSection'], callers
    for name in PLAN_FUNCTIONS:
        body = function_body(name, script)
        for storage in ('localStorage', 'sessionStorage', 'indexedDB'):
            assert storage not in body, (name, storage)
    route = PLAN_ROUTES.split('def get_active_nutrition_plan', 1)[1].split('\n@bp.route', 1)[0]
    assert 'NutritionPlan.query.filter_by(user_id=current_user.id)' in route


def test_generation_is_a_proposal_not_a_save(script=SCRIPT):
    writers = {"fetch('/nutrition-plan', {": ['generatePlan'],
               "fetch('/nutrition-plan/save', {": ['savePlanOption']}
    for call, owners in writers.items():
        assert [function_containing(m.start(), script)
                for m in re.finditer(re.escape(call), script)] == owners, call
    for name in ('generatePlan', 'renderPlans'):
        body = function_body(name, script)
        for forbidden in ('/nutrition-plan/save', 'savePlanOption(', 'renderCurrentPlan(',
                          'renderActivePlanDetail(', 'invalidateActivePlan(', '_planShown =',
                          'active-plan-detail', 'plan-current-status', "'nutrition.plan.saved'"):
            assert forbidden not in body, (name, forbidden)


def test_nothing_is_marked_current_before_the_save_and_the_reread(script=SCRIPT):
    body = function_body('savePlanOption', script)
    sent = body.index("await fetch('/nutrition-plan/save'")
    reread = body.index('await reread')
    assert sent < body.index('invalidateActivePlan()') < reread < body.index('finishPlanSave()')
    assert body[:sent].count('_planShown =') == 0
    assigned = {function_containing(m.start(), script) for m in re.finditer(r'\b_planShown = ', script)
                if not script[m.start() - 4:m.start()] == 'let '}
    assert assigned == {'renderCurrentPlan', 'renderCurrentPlanUnavailable'}, assigned
    finish = function_body('finishPlanSave', script)
    assert "'nutrition.plan.saved'" in finish
    assert [function_containing(m.start(), script)
            for m in re.finditer(r"'nutrition\.plan\.saved'", script)] == ['finishPlanSave']
    # A refusal or an unprovable outcome never reaches the success path.
    refused = body[body.index('if (refused) {'):body.index('invalidateActivePlan()')]
    assert 'finishPlanSave' not in refused and 'renderCurrentPlan' not in refused
    assert 'return;' in refused


def test_replacement_is_confirmed_and_single_flight(script=SCRIPT):
    select = function_body('selectPlan', script)
    assert "_planShown.state === 'absent'" in select and 'openPlanReplace(i, btn)' in select
    assert re.search(r'if \(_planSaveInFlight \|\| _planSaveLocked \|\| !_planOptions', function_body('savePlanOption', script))
    assert 'if (i === null || _planSaveInFlight) return;' in function_body('confirmPlanReplace', script)
    assert re.search(r'if \(_planGenInFlight \|\| _planSaveInFlight\) return;', function_body('generatePlan', script))
    cancel = function_body('cancelPlanReplace', script)
    assert 'fetch(' not in cancel and 'savePlanOption' not in cancel
    # An unprovable save is never re-sent: no retry loop, no timer.
    save = function_body('savePlanOption', script)
    assert save.count("fetch('/nutrition-plan/save'") == 1
    for forbidden in ('setTimeout', 'setInterval', 'while (', 'for ('):
        assert forbidden not in save, forbidden


def test_absent_is_not_a_read_failure(script=SCRIPT):
    load = function_body('loadActivePlan', script)
    catch = load[load.index('} catch (e) {'):]
    assert 'renderCurrentPlanUnavailable()' in catch
    assert 'exists' not in catch and "'absent'" not in catch
    current = function_body('renderCurrentPlan', script)
    assert current.index('if (!d.exists)') < current.index("'nutrition.plan.absent'") < current.index('} else if')
    unavailable = function_body('renderCurrentPlanUnavailable', script)
    assert "'nutrition.plan_unavailable'" in unavailable and 'nutrition.plan.absent' not in unavailable
    assert "'retry'" in unavailable and "'create'" not in unavailable
    for language in ('en', 'tr'):
        catalog = _catalog(language)
        assert catalog['nutrition.plan.absent'] != catalog['nutrition.plan_unavailable']


def test_target_missing_is_not_target_failure(script=SCRIPT):
    body = function_body('renderPlanTarget', script)
    absent = body[body.index("} else if (target.state === 'absent') {"):body.index('} else {')]
    failed = body[body.index('} else {'):]
    assert "'nutrition.target_absent'" in absent and "'nutrition.target_unavailable'" in failed
    assert 'fetch(' not in body                                   # mirrors Today's one read
    for language in ('en', 'tr'):
        catalog = _catalog(language)
        for key in ('nutrition.target_absent', 'nutrition.target_unavailable'):
            assert catalog[key] and not re.search(r'\d', catalog[key]), (language, key)
    today = function_body('renderDaySummary', script)
    assert 'renderPlanTarget(target)' in today
    assert "renderPlanTarget({ state: 'unavailable' })" in function_body('renderDaySummaryFailure', script)


def test_planned_is_never_logged_on_plan(script=SCRIPT):
    detail = function_body('renderActivePlanDetail', script)
    assert "esc(__t('nutrition.planned'))" in detail and 'data-planned="true"' in detail
    for name in PLAN_FUNCTIONS:
        body = function_body(name, script)
        for forbidden in ('logged_state', "'nutrition.logged", 'quickAddMeal(', '/api/quick-add-meal',
                          "'/meal-log'"):
            assert forbidden not in body, (name, forbidden)
    # The one planned writer and its PR3 locks are untouched.
    assert script.count("'/api/quick-add-meal'") == 1
    assert function_containing(script.index("'/api/quick-add-meal'"), script) == 'quickAddMeal'
    for line in ("  _plannedWriteLocks.set(mealKey, 'unconfirmed');\n  lockPlannedRow(btn, 'unconfirmed');\n",
                 "    _plannedWriteLocks.set(mealKey, 'logged');\n",
                 "  if (planKey !== _plannedLocksPlan) { _plannedWriteLocks.clear(); _plannedLocksPlan = planKey; }\n"):
        assert script.count(line) == 1, line


def test_score_is_neutral_secondary_metadata(script=SCRIPT):
    for forbidden in ('score_color', 'score_label', 'scoreLabel', 'SCORE_LABELS_EN', '#3D8BFF',
                      '#FFB020', '#FF4D4D', 'score-big'):
        assert forbidden not in script, forbidden
    for language in ('en', 'tr'):
        catalog = _catalog(language)
        rating = (catalog['nutrition.plan.rating'] + ' ' + catalog['nutrition.plan.rating_hint']).lower()
        for claim in ('health', 'sağlık', 'safe', 'güvenli', 'adherence', 'uyum', 'success', 'başarı'):
            assert claim not in rating, (language, claim)


# ── SUPPLEMENTS / GROCERY ───────────────────────────────────────────────


def test_supplements_is_only_a_child_link(client, make_user, login, html=None, script=SCRIPT):
    html = html or render(client, make_user, login, 'en')
    panel = plan_panel(html)
    child = panel.split('class="nutrition-child-domain"', 1)[1].split('</nav>', 1)[0]
    assert 'href="/supplements"' in child
    assert panel.count('/supplement') == 1                         # the one link, nothing else
    for writer in ('/supplement/add', '/supplement/edit', '/supplement/delete'):
        assert writer not in html and writer not in script, writer
    assert not re.search(r'<form\b', panel)
    assert "fetch('/supplements" not in script


def test_grocery_guide_is_absent(client, make_user, login, html=None, script=SCRIPT):
    html = html or render(client, make_user, login, 'en')
    body = re.sub(r'(?s)<script\b.*?</script>', '', html).lower()
    for text in (body, script.lower()):
        for word in ('grocery', 'shopping list', 'alışveriş listesi', 'market listesi'):
            assert word not in text, word
    for language in ('en', 'tr'):
        for key, value in _catalog(language).items():
            if key.startswith('nutrition.'):
                assert 'grocery' not in value.lower() and 'alışveriş listesi' not in value.lower(), key


# ── BACKEND SAVE INVARIANT ──────────────────────────────────────────────


def _call_order(source):
    tree = ast.parse(source)
    route = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'save_nutrition_plan')
    order = []
    for node in ast.walk(route):
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name in ('parse_plan_score', 'validate_nutrition_plan_for_save', 'db.session.add',
                        'db.session.commit', 'replace_nutrition_plan') or name.endswith('.delete'):
                order.append((node.lineno, 'delete' if name.endswith('.delete') else name))
    return [name for _, name in sorted(order)]


def test_save_route_validates_before_it_deletes(source=PLAN_ROUTES):
    # NUTR-PR7 moved delete → insert → commit into the ONE replacement boundary
    # (app/services/nutrition_plan_store.py) shared with the native transport;
    # the route still validates score and schema BEFORE it reaches it.
    assert _call_order(source) == ['parse_plan_score', 'validate_nutrition_plan_for_save',
                                   'replace_nutrition_plan']


PLAN_STORE = (ROOT / 'app' / 'services' / 'nutrition_plan_store.py').read_text(encoding='utf-8')


def _store_order(source):
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == 'replace_nutrition_plan')
    order = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name in ('check', 'db.session.add', 'db.session.commit') or name.endswith('.delete'):
                order.append((node.lineno, 'delete' if name.endswith('.delete') else name))
    return [name for _, name in sorted(order)]


def test_replacement_boundary_checks_before_it_deletes(source=PLAN_STORE):
    assert _store_order(source) == ['check', 'delete', 'db.session.add', 'db.session.commit']


def _active(client):
    return client.get('/nutrition-plan/active').get_json()


VALID = {'isim': 'Plan B', 'ogle': {'yemekler': ['Rice - 150g'], 'kalori': 500, 'protein': 30,
                                    'karb': 60, 'yag': 10}, 'toplam_kalori': 500}
REFUSED = [
    {'plan': VALID, 'score': 'abc'},                                 # score validation
    {'plan': VALID, 'score': 11},
    {'plan': dict(VALID, extra='x'), 'score': 8},                    # schema validation
    {'plan': {'isim': 'No meal'}, 'score': 8},
    {'plan': {'ogle': {'yemekler': '<b>not a list</b>'}}, 'score': 8},
    {'score': 8},                                                    # plan missing
]


def test_invalid_save_never_destroys_the_current_plan(app, client, auth_user):
    from app.extensions import db
    from app.models import NutritionPlan
    current = {'isim': 'Plan A', 'kahvalti': {'yemekler': ['Oats'], 'kalori': 400}}
    with app.app_context():
        db.session.add(NutritionPlan(user_id=auth_user.id, score=7, plan_data=json.dumps(current)))
        db.session.commit()
    for body in REFUSED:
        response = client.post('/nutrition-plan/save', json=body)
        assert response.status_code == 400, body
        after = _active(client)
        assert after['exists'] is True and after['plan'] == current and after['score'] == 7, body
    assert client.post('/nutrition-plan/save', json={'plan': VALID, 'score': 8}).status_code == 200
    after = _active(client)
    assert after['plan'] == VALID and after['score'] == 8
    with app.app_context():
        assert NutritionPlan.query.filter_by(user_id=auth_user.id).count() == 1


# ── NO EAGER GENERATION / NO SCHEMA ─────────────────────────────────────


def test_no_eager_generation_or_provider_call(app, client, auth_user, monkeypatch, script=SCRIPT):
    from app.blueprints.nutrition import plan as plan_module

    def forbidden(**kwargs):
        raise AssertionError('provider called outside an explicit Generate')
    monkeypatch.setattr(plan_module, '_heavy_chat', forbidden)
    assert client.get('/nutrition').status_code == 200
    assert client.get('/nutrition-plan/active').status_code == 200
    init = script[script.index('/* ── INIT ── */'):]
    assert 'generatePlan' not in init and "'/nutrition-plan'" not in init
    for name in ('applyNutritionNavigation', 'switchTab', 'initNutritionNavigation', 'loadActivePlan',
                 'renderCurrentPlan', 'openPlanBuilder', 'loadQuickAddSection'):
        body = function_body(name, script)
        assert 'generatePlan(' not in body and "fetch('/nutrition-plan', {" not in body, name
    for forbidden_timer in ('setInterval(',):
        assert forbidden_timer not in script


def test_no_schema_change_to_nutrition_plan():
    from app.models import NutritionPlan
    assert {c.name for c in NutritionPlan.__table__.columns} == {
        'id', 'user_id', 'plan_data', 'score', 'created_at'}


# ── COPY ────────────────────────────────────────────────────────────────


def test_locale_parity_and_plain_bounded_copy():
    en, tr = _catalog('en'), _catalog('tr')
    assert set(en) == set(tr)
    keys = sorted(k for k in en if k.startswith('nutrition.plan.') and k != 'nutrition.plan.pick_one')
    assert len(keys) >= 40, keys
    for key in keys:
        assert en[key].strip() and tr[key].strip(), key
        assert en[key] != tr[key], key                                # translated, not copied
        assert set(re.findall(r'\{\w+\}', en[key])) == set(re.findall(r'\{\w+\}', tr[key])), key
        for language, value in (('en', en[key]), ('tr', tr[key])):
            lowered = value.lower()
            for word in INTERNAL_WORDS:
                assert word not in lowered, (language, key, word)
            for claim in OVERCLAIMS:
                assert not re.search(r'\b' + claim, lowered), (language, key, claim)
    # Replacement copy says what the route does: the old plan is replaced for good.
    assert "can't be restored" in en['nutrition.plan.confirm_body']
    assert 'geri getirilemez' in tr['nutrition.plan.confirm_body']
    assert en['nutrition.plan.save_unconfirmed'].startswith("Couldn't confirm replacement")
    for retired in ('nutrition.select_plan', 'nutrition.active_plan', 'nutrition.new_plan',
                    'nutrition.score_text', 'nutrition.score_desc', 'nutrition.plan_word'):
        assert retired not in en and retired not in tr, retired


def test_every_plan_key_the_script_uses_exists(script=SCRIPT):
    en, tr = _catalog('en'), _catalog('tr')
    used = set(re.findall(r"'(nutrition\.plan[._][a-z_.]+)'", script))
    assert used, 'scan found nothing'
    assert used <= set(en) and used <= set(tr), used - set(en)
