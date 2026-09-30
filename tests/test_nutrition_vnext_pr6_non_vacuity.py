"""NUTR-PR6 non-vacuity: each controlled mutation must make its guard fail.

No product file is changed on disk. The service is mutated by executing an
edited copy of its real source inside the real module (and the route rebound
to it); the browser script through a per-page route; templates through a
scoped Jinja loader patch; Coach seams by monkeypatching the real call sites.
Browser mutations are checked to be the code the page actually ran.

  P6-N1  failed intake rendered as zero                     → isolation tests fail
  P6-N2  missing target rendered as 0                       → target-state test fails
  P6-N3  hydration failure rendered as zero                 → isolation + Coach tests fail
  P6-N4  no plan and failed plan read collapsed             → isolation + Coach tests fail
  P6-N5  next_action computed client-side                   → structural + browser tests fail
  P6-N6  "on track" / adherence score introduced            → shape/semantics/copy tests fail
  P6-N7  Nutrition open invokes AI/provider                 → no-provider + topology tests fail
  P6-N8  Review click automatically sends a Coach message   → review browser test fails
  P6-N9  nutrition facts serialized into the Coach URL      → review-link test fails
  P6-N10 browser calories accepted as Coach context         → forged-calories test fails
  P6-N11 send-time context reuses the stale preview         → freshness test fails
  P6-N12 unknown handoff marker reaches the pipeline        → allowlist tests fail
  P6-N13 a partial failed read produces a prescription      → next-action + Coach tests fail
  P6-N14 PR6 breaks the Progress Coach handoff              → Progress handoff tests fail
"""
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from ux4_gate_support import gate, ready  # noqa: F401

import test_coach_progress_handoff as progress
import test_coach_progress_handoff_browser as progress_browser
import test_nutrition_vnext_pr6_browser as browser
import test_nutrition_vnext_pr6_coach_handoff as handoff
import test_nutrition_vnext_pr6_day_view as contract
from app.blueprints import coach as coach_bp
from app.blueprints.nutrition import day_view as route_module
from app.services import nutrition_day_view as dv

ROOT = Path(__file__).resolve().parent.parent
SERVICE_PATH = ROOT / 'app' / 'services' / 'nutrition_day_view.py'


@pytest.fixture(autouse=True)
def close_adapter_responses(client, monkeypatch):
    original = client.open

    def buffered_open(*args, **kwargs):
        response = original(*args, **kwargs)
        response.get_data()
        response.close()
        return response
    monkeypatch.setattr(client, 'open', buffered_open)


@contextmanager
def mutated_service(*edits):
    """Run an edited copy of the REAL service source inside the real module."""
    source = SERVICE_PATH.read_text(encoding='utf-8').replace('\r\n', '\n')
    for old, new in edits:
        assert source.count(old) == 1, 'service mutation target drifted: ' + old
        source = source.replace(old, new)
    saved = dict(dv.__dict__)
    saved_route = (route_module.build_nutrition_day_view, route_module.nutrition_day_view_payload)
    exec(compile(source, str(SERVICE_PATH), 'exec'), dv.__dict__)
    route_module.build_nutrition_day_view = dv.build_nutrition_day_view
    route_module.nutrition_day_view_payload = dv.nutrition_day_view_payload
    try:
        yield
    finally:
        dv.__dict__.clear()
        dv.__dict__.update(saved)
        route_module.build_nutrition_day_view, route_module.nutrition_day_view_payload = saved_route


def serve_script(page, client, edits):
    source = client.get('/static/nutrition.js').get_data(as_text=True).replace('\r\n', '\n')
    for old, new in edits:
        assert source.count(old) == 1, 'script mutation target drifted: ' + old
        source = source.replace(old, new)
    page.route('**/static/nutrition.js*', lambda r: r.fulfill(
        status=200, content_type='application/javascript', body=source))
    return source


def patch_template(app, monkeypatch, name, old, new):
    loader = app.jinja_env.loader
    original = loader.get_source

    def mutated(environment, template):
        source, filename, uptodate = original(environment, template)
        if template == name:
            source = source.replace('\r\n', '\n')
            assert source.count(old) == 1, 'template mutation target drifted: ' + old
            source = source.replace(old, new)
        return source, filename, uptodate
    monkeypatch.setattr(loader, 'get_source', mutated)
    app.jinja_env.cache.clear()


def assert_served(page, functions, needle):
    source = page.evaluate('names => names.map(n => window[n].toString()).join("\\n")', functions)
    assert needle in source, needle


# ── P6-N1 … P6-N4 · truthful sections ──────────────────────────────────


GUARD_FALLBACK = {
    'intake': ('intake = _guard(lambda: _read_intake(user_id, day_iso), IntakeSection(UNAVAILABLE))',
               'intake = _guard(lambda: _read_intake(user_id, day_iso), IntakeSection(EMPTY, '
               'totals={k: 0.0 for k in INTAKE_KEYS}, meal_count=0))'),
    'hydration': ('hydration = _guard(lambda: _read_hydration(user_id, day_iso),\n'
                  '                       HydrationSection(UNAVAILABLE))',
                  'hydration = _guard(lambda: _read_hydration(user_id, day_iso),\n'
                  '                       HydrationSection(EMPTY, amount=0))'),
    'plan': ('plan = _guard(lambda: _read_plan(user_id), PlanSection(UNAVAILABLE))',
             'plan = _guard(lambda: _read_plan(user_id), PlanSection(EMPTY))'),
}


def test_n1_failed_intake_as_zero_is_detected(app, make_user, monkeypatch):
    with mutated_service(GUARD_FALLBACK['intake']), pytest.raises(AssertionError):
        contract.test_one_failed_section_never_erases_a_sibling(app, make_user, monkeypatch, 'intake')


def test_n2_missing_target_as_zero_is_detected(app, make_user):
    edit = ('    if row is None or row[0] is None:\n        return TargetSection(EMPTY)',
            '    if row is None or row[0] is None:\n        return TargetSection(EMPTY, value=0.0)')
    with mutated_service(edit), pytest.raises(AssertionError):
        contract.test_target_states(app, make_user, 'none', ('empty', None))


def test_n3_failed_hydration_as_zero_is_detected(app, make_user, monkeypatch):
    with mutated_service(GUARD_FALLBACK['hydration']):
        with pytest.raises(AssertionError):
            contract.test_one_failed_section_never_erases_a_sibling(
                app, make_user, monkeypatch, 'hydration')
        with pytest.raises(AssertionError), pytest.MonkeyPatch.context() as scoped:
            handoff.test_partial_context_keeps_unknown_unknown(
                app, make_user, scoped, '_read_hydration', '- hydration today: unknown (read failed)')


def test_n4_no_plan_and_failed_plan_collapsed_is_detected(app, make_user, monkeypatch):
    with mutated_service(GUARD_FALLBACK['plan']):
        with pytest.raises(AssertionError):
            contract.test_one_failed_section_never_erases_a_sibling(app, make_user, monkeypatch, 'plan')
        with pytest.raises(AssertionError), pytest.MonkeyPatch.context() as scoped:
            handoff.test_partial_context_keeps_unknown_unknown(
                app, make_user, scoped, '_read_plan', '- nutrition plan: unknown (read failed)')


# ── P6-N5 · client-side decision ───────────────────────────────────────


N5 = [("    const kind = allowedNextAction(view);\n",
       "    const kind = document.getElementById('nut-day').dataset.targetState === 'absent'\n"
       "      ? 'set_target' : 'log_food';\n")]


def test_n5_client_side_next_action_is_detected(app, gate, client, make_user, login,
                                                monkeypatch):  # noqa: F811
    mutated = serve_script(gate.page, client, N5)
    with pytest.raises(AssertionError):
        contract.test_next_step_is_rendered_never_decided_client_side(script=mutated)
    with pytest.raises(AssertionError):
        browser.test_intake_failure_asks_for_retry_and_recovers_with_focus(
            app, gate, make_user, login, monkeypatch)
    assert_served(gate.page, ['loadDayView'], "dataset.targetState === 'absent'")


# ── P6-N6 · scores / verdicts ──────────────────────────────────────────


def test_n6_adherence_or_on_track_is_detected(app, make_user):
    edit = ('        "next_action": {"state": view.next_action.state, "kind": view.next_action.kind,',
            '        "adherence": 82, "status": "on_track",\n'
            '        "next_action": {"state": view.next_action.state, "kind": view.next_action.kind,')
    with mutated_service(edit):
        with pytest.raises(AssertionError):
            contract.test_payload_is_the_bounded_semantic_shape(app, make_user)
    with mutated_service(edit), pytest.raises(AssertionError):
        contract.test_no_score_threshold_or_prescription_semantics(app, _Users(app))
    catalogs = {lang: json.loads((ROOT / 'locales' / f'{lang}.json').read_text(encoding='utf-8'))
                for lang in ('en', 'tr')}
    catalogs['en']['nutrition.next.log_food_lead'] = "You're on track — adherence 82%"
    with pytest.raises(AssertionError):
        contract.test_pr6_copy_is_plain_bounded_and_at_parity(catalogs=catalogs)


# ── P6-N7 · AI/provider on open ────────────────────────────────────────


def test_n7_provider_on_open_is_detected(app, client, make_user, login, monkeypatch):
    edit = ('    """Owner-scoped day view. Never raises for a section read; never writes."""\n',
            '    """Owner-scoped day view. Never raises for a section read; never writes."""\n'
            '    import app.services.ai as _ai\n'
            '    _ai._heavy_chat(system="insight", user="today")\n')
    with mutated_service(edit), pytest.raises(AssertionError):
        contract.test_no_ai_provider_or_network_on_build_or_route(app, client, make_user, login,
                                                                   monkeypatch)


def test_n7_eager_request_on_open_is_detected(app, gate, client, make_user, login):  # noqa: F811
    edit = [("  renderNextStep('loading', null);\n",
             "  renderNextStep('loading', null);\n"
             "  fetch('/nutrition-plan/active');\n")]
    serve_script(gate.page, client, edit)
    with pytest.raises(AssertionError):
        browser.test_request_topology(app, gate, make_user, login)
    assert_served(gate.page, ['loadDayView'], "fetch('/nutrition-plan/active')")


# ── P6-N8 / N9 · Review with AxisAI ────────────────────────────────────


def test_n8_auto_send_on_review_is_detected(app, gate, make_user, login, monkeypatch):  # noqa: F811
    patch_template(app, monkeypatch, '_coach_handoff.html',
                   '    window.CW.handoff = kind;\n',
                   "    window.CW.handoff = kind;\n"
                   "    setTimeout(function () { document.getElementById('cw-send').click(); }, 0);\n")
    with pytest.raises(AssertionError):
        browser.test_review_opens_coach_without_sending_and_send_uses_the_marker(
            app, gate, make_user, login, monkeypatch)


def test_n9_facts_in_coach_url_are_detected(app, gate, make_user, login, monkeypatch):  # noqa: F811
    patch_template(app, monkeypatch, 'nutrition.html',
                   'href="/coach?review=nutrition-day"',
                   'href="/coach?review=nutrition-day&kcal=1235&water=3"')
    with pytest.raises(AssertionError):
        browser.test_review_link_carries_only_the_constant(app, gate, make_user, login)


# ── P6-N10 … P6-N13 · Coach context authority ──────────────────────────


def test_n10_browser_calories_as_context_are_detected(app, client, auth_user, monkeypatch):
    from flask import request
    from app.services import ai_pipeline
    original = ai_pipeline._context_stage

    def trusting(uid, question, language, handoff=None):
        forged = (request.get_json(silent=True) or {}).get('context', '')
        return original(uid, question, language, handoff) + '\n' + forged
    monkeypatch.setattr(ai_pipeline, '_context_stage', trusting)
    with pytest.raises(AssertionError):
        handoff.test_browser_calories_never_become_model_context(app, client, auth_user, monkeypatch)


def test_n11_stale_preview_reused_at_send_is_detected(app, make_user, monkeypatch):
    memo = {}
    real = dv.build_nutrition_day_view

    def cached(uid):
        if uid not in memo:
            memo[uid] = real(uid)
        return memo[uid]
    monkeypatch.setattr(dv, 'build_nutrition_day_view', cached)
    with pytest.raises(AssertionError):
        handoff.test_send_time_facts_win_over_the_preview(app, make_user)


def test_n12_unknown_marker_reaching_the_pipeline_is_detected(client, auth_user, app, monkeypatch):
    monkeypatch.setattr(coach_bp, 'handoff_marker', lambda value: value)
    with pytest.raises(AssertionError), pytest.MonkeyPatch.context() as scoped:
        handoff.test_routes_forward_only_the_allowlisted_marker(
            client, auth_user, app, scoped, 'nutrition-day-v2', None, '/ask')
    import app.coach_handoff as module
    monkeypatch.setattr(module, 'handoff_marker',
                        lambda v: 'nutrition-day' if isinstance(v, str) and 'nutrition' in v else None)
    with pytest.raises(AssertionError), pytest.MonkeyPatch.context() as scoped:
        handoff.test_unknown_marker_never_reaches_context(app, _Users(app), scoped)


class _Users:
    """make_user stand-in with unique names (the guard is called directly)."""

    def __init__(self, app):
        self.n = 0

    def __call__(self, name, **kw):
        from app.extensions import db
        from app.models import User
        from onboarding_support import mark_onboarded
        self.n += 1
        user = User(username=f'{name}_{self.n}', email=f'{name}_{self.n}@example.com',
                    cognito_sub=f'sub-{name}-{self.n}')
        db.session.add(user)
        db.session.flush()
        if kw.get('profile_complete'):
            mark_onboarded(user)
        db.session.commit()
        return user


def test_n13_prescription_from_partial_read_is_detected(app, make_user, monkeypatch):
    edit = ('        next_action=derive_next_action(intake.state, target.state))',
            '        next_action=(NextAction(AVAILABLE, "drink_more", "nutrition.next.drink_more")\n'
            '                     if hydration.state == UNAVAILABLE\n'
            '                     else derive_next_action(intake.state, target.state)))')
    with mutated_service(edit), pytest.raises(AssertionError):
        contract.test_next_action_ignores_hydration_and_plan(app, make_user, monkeypatch)
    from app import coach_handoff
    original = coach_handoff._nutrition_context_text

    def prescribing(view, language):
        text = original(view, language)
        if view.hydration.state == 'unavailable':
            text += '\n- advice: you need to drink more water'
        return text
    monkeypatch.setattr(coach_handoff, '_nutrition_context_text', prescribing)
    with pytest.raises(AssertionError), pytest.MonkeyPatch.context() as scoped:
        handoff.test_partial_context_keeps_unknown_unknown(
            app, _Users(app), scoped, '_read_hydration', '- hydration today: unknown (read failed)')


# ── P6-N14 · Progress handoff ──────────────────────────────────────────


def test_n14_progress_handoff_break_is_detected(client, auth_user, app, monkeypatch, gate,
                                                make_user, login):  # noqa: F811
    import app.coach_handoff as module
    monkeypatch.setattr(coach_bp, 'handoff_marker',
                        lambda v: v if v == module.REVIEW_NUTRITION_DAY else None)
    with pytest.raises(AssertionError), pytest.MonkeyPatch.context() as scoped:
        progress.test_route_forwards_only_allowlisted_kind(
            client, auth_user, app, scoped, 'progress-insight', 'progress-insight', '/ask')
    patch_template(app, monkeypatch, '_coach_handoff.html',
                   "{%- set hid = 'coach-progress' if coach_handoff.kind == 'progress-insight' "
                   "else 'coach-nutrition' %}",
                   "{%- set hid = 'coach-nutrition' %}")
    with pytest.raises(AssertionError):
        progress_browser.test_handoff_preview_is_compact_across_viewports(app, gate, make_user, login)
