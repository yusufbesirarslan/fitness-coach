"""Real Flask bootstrap and browser action boundaries for Sprint 14 PR3.

Removing the execution projection must break fresh/reload hydration. Making
Close discard its draft must break the delayed ACK race and durable completion.
Only external completion image/vision services are stubbed.
"""
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright, expect

from app.extensions import db
from app.models import User
from test_sprint14_workout_execution_contract import (
    SQUAT, BENCH, checkpoint_over_http, proof_accepted, row_for,
    save_workout_plan, sessions_on, start_session_over_http,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def execution_session(app, auth_user, client, sessions_on):
    with app.app_context():
        db.session.get(User, auth_user.id).profile_complete = True
        plan = save_workout_plan(auth_user.id)
        data = json.loads(plan.plan_data)
        for day in data['program']:
            day.update(odak='Strength', sure_dk=30, tahmini_kalori=150)
            for exercise in day['egzersizler']:
                exercise.update(set=1, tekrar='8', dinlenme='60 sn', not_='')
        plan.plan_data = json.dumps(data)
        db.session.commit()
    return start_session_over_http(client)


def browser_draft(raw):
    """Pass the unmodified HTTP response to the actual JS client in a new VM."""
    script = r"""
const fs = require('node:fs');
const assert = require('node:assert/strict');
const {createWorkoutStateClient} = require('./static/workout_state_client.js');
const {createWorkoutDraft, buildCheckpointSnapshot} = require('./static/workout_draft.js');
const raw = fs.readFileSync(0, 'utf8');
(async () => {
  let projection, blocked;
  const client = createWorkoutStateClient({
    fetchImpl: async () => ({ok:true, status:200, json:async () => JSON.parse(raw)}),
    onSnapshot: data => { projection = data; },
    onBlocked: reason => { blocked = reason; },
    documentRef:{hidden:false, addEventListener(){}, removeEventListener(){}},
    addEventListener(){}, removeEventListener(){}
  });
  await client.refresh('load');
  assert.equal(blocked, undefined, blocked);
  const data = JSON.parse(raw);
  assert.equal(client.getSessionId(), data.workout.state.session.public_id);
  const draft = createWorkoutDraft(projection.today_plan, projection.workout.state.session, 1000000);
  console.log(JSON.stringify({revision:client.getCheckpointRevision(), draft,
    checkpoint:buildCheckpointSnapshot(draft, 1000000)}));
  client.destroy();
})().catch(e => { console.error(e); process.exitCode = 1; });
"""
    return subprocess.run(['node', '-e', script], input=raw, text=True,
                          capture_output=True, cwd=ROOT, timeout=30)


def test_real_bootstrap_fresh_first_checkpoint(app, auth_user, client, execution_session):
    response = client.get('/training/bootstrap')
    assert response.status_code == 200
    result = browser_draft(response.get_data(as_text=True))
    assert result.returncode == 0, result.stderr
    built = json.loads(result.stdout)
    assert built['revision'] == 0
    session = response.json['workout']['state']['session']
    assert session['checkpoint'] is None
    assert session['checkpoint_exercise_ids'] == [SQUAT, BENCH]
    private = {'id', 'user_id', 'plan_id', 'plan_fingerprint', 'checkpoint_fingerprint',
               'checkpoint_idempotency_key', 'workout_ref', 'plan_lineage_id'}
    assert private.isdisjoint(session)
    saved = checkpoint_over_http(client, execution_session, 0, built['checkpoint'])
    assert saved.status_code == 200, saved.json
    with app.app_context():
        assert row_for(auth_user.id).checkpoint_revision == 1


@pytest.mark.skip(reason="WEB-UX3-PR6B deleted training.js globals; Plan coverage is test_plan_v2_resume_uses_shared_execution_and_hydrates_acknowledged_progress")
def test_real_bootstrap_reload_hydrates_acknowledged_progress(app, auth_user, client, execution_session, training_page):
    page, _, _, _ = training_page
    page.goto("http://localhost/training")
    page.locator('[data-action="startWorkout"]').click()
    expect(page.locator("#session-view")).to_have_class("session-view open")
    checkpoint = {
        'current_exercise_index': 1, 'elapsed_seconds': 321,
        'exercises': [
            {'exercise_id': SQUAT, 'sets': [{'index': 0, 'completed': True, 'reps': 11, 'weight_kg': 82.5}]},
            {'exercise_id': BENCH, 'sets': [{'index': 0, 'completed': False, 'reps': 6, 'weight_kg': 45}]},
        ],
    }
    assert checkpoint_over_http(client, execution_session, 0, checkpoint).status_code == 200
    response = client.get('/training/bootstrap')
    assert response.status_code == 200
    result = browser_draft(response.get_data(as_text=True))
    assert result.returncode == 0, result.stderr
    built = json.loads(result.stdout)
    assert built['revision'] == 1
    assert built['checkpoint'] == checkpoint
    assert built['draft']['elapsedBaselineSeconds'] == 321
    # Reload destroys the first page client and all of its in-memory draft.
    page.reload()
    page.locator('[data-action="startWorkout"]').click()
    expect(page.locator('#session-view')).to_have_class('session-view open')
    hydrated = page.evaluate('FitXWorkoutDraft.buildCheckpointSnapshot(_session, _session.elapsedStartedAtMs)')
    assert hydrated == checkpoint


def test_real_bootstrap_corrupt_checkpoint_fails_closed(app, auth_user, client, execution_session):
    with app.app_context():
        row = row_for(auth_user.id)
        row.checkpoint_revision = 1
        row.checkpoint_data = None
        db.session.commit()
    response = client.get('/training/bootstrap')
    assert response.status_code == 200
    session = response.json['workout']['state']['session']
    assert session['checkpoint_revision'] == 1
    assert session['checkpoint'] is None
    result = browser_draft(response.get_data(as_text=True))
    assert result.returncode != 0
    assert 'checkpoint_unavailable' in result.stderr


@pytest.fixture
def training_page(client):
    """Real rendered page/scripts; route browser HTTP to the authenticated Flask client."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # Playwright's string predicates require evaluation; production CSP is
        # tested separately. The page still loads the real dispatcher and scripts.
        page = browser.new_page(bypass_csp=True)
        traffic = []
        held = []
        control = {'hold': False}

        def route_request(route):
            request = route.request
            url = urlsplit(request.url)
            if url.netloc != 'localhost':
                route.abort()
                return
            def deliver():
                response = client.open(url.path + ('?' + url.query if url.query else ''),
                                       method=request.method, data=request.post_data,
                                       headers={k: v for k, v in request.headers.items()
                                                if k.lower() in {'content-type', 'origin', 'x-csrftoken', 'if-match', 'idempotency-key'}})
                traffic.append((url.path, request.post_data, response.status_code))
                route.fulfill(status=response.status_code, body=response.get_data(),
                              headers={k: v for k, v in response.headers.items()
                                       if k.lower() not in {'content-length', 'set-cookie'}})
                return response.status_code

            if control['hold'] and url.path.endswith('/checkpoint'):
                held.append(deliver)
            else:
                deliver()

        page.route('**/*', route_request)
        yield page, traffic, held, control
        browser.close()


@pytest.mark.skip(reason="WEB-UX3-PR6B deleted training.js globals; Plan coverage is test_plan_v2_start_refresh_checkpoint_and_complete_use_durable_contract")
def test_dispatcher_close_resume_delayed_ack_finish_preserves_durable_set(
    app, auth_user, client, execution_session, proof_accepted, training_page,
):
    page, traffic, held, control = training_page
    page.goto('http://localhost/training')
    page.locator('[data-action="startWorkout"]').click()
    expect(page.locator('#session-view')).to_have_class('session-view open')
    # R1/S1: first acknowledged snapshot has an unchecked set.
    page.locator('#sv-body [data-field="weight"]').first.fill('82.5')
    page.wait_for_function('workoutStateClient.getCheckpointRevision() === 1')
    control['hold'] = True
    page.locator('#sv-body [data-field="done"]').first.click()
    page.wait_for_function('workoutStateClient.hasDirtyCheckpoint() === true')
    # Actual document dispatcher -> published callback receives element + event.
    page.locator('[data-action="closeSession"]').click()
    expect(page.locator('#session-view')).not_to_have_class('session-view open')
    page.locator('[data-action="startWorkout"]').click()
    expect(page.locator('#session-view')).to_have_class('session-view open')
    expect(page.locator('#sv-body .set-row[data-ex="0"][data-set="0"]')).to_have_class(re.compile(r'\bis-done\b'))
    assert len(held) == 1
    control['hold'] = False
    assert held.pop()() == 200
    page.wait_for_function('workoutStateClient.getCheckpointRevision() === 2')
    expect(page.locator('#sv-body .set-row[data-ex="0"][data-set="0"]')).to_have_class(re.compile(r'\bis-done\b'))
    # Make the final snapshot distinct; Finish must wait for its real ACK.
    control['hold'] = True
    page.locator('#sv-body [data-field="reps"]').first.fill('12')
    page.locator('[data-action="finishSession"]').click()
    page.wait_for_function('workoutStateClient.hasDirtyCheckpoint() === true')
    expect(page.locator('#session-view')).to_have_class('session-view open')
    assert not any(path == '/workout/complete' for path, _, _ in traffic)
    assert len(held) == 1
    control['hold'] = False
    assert held.pop()() == 200
    page.wait_for_function('workoutStateClient.getCheckpointRevision() === 3')
    page.evaluate("pumpImageData = 'data:image/jpeg;base64,dGVzdA=='")
    page.locator('[data-action="submitPumpCheck"]').click()
    page.wait_for_function("currentWorkoutState.session.status === 'completed'")
    writes = [(path, json.loads(body)) for path, body, status in traffic
              if path.endswith('/checkpoint') or path == '/workout/complete']
    assert len(writes) == 4
    assert writes[-2][0].endswith('/checkpoint')
    assert writes[-2][1]['checkpoint']['exercises'][0]['sets'][0]['completed'] is True
    assert writes[-1][0] == '/workout/complete'
    assert writes[-1][1]['expected_checkpoint_revision'] == 3
    with app.app_context():
        db.session.expire_all()
        row = row_for(auth_user.id)
        assert row.status == 'completed'
        assert row.checkpoint_revision == 3
        saved = json.loads(row.checkpoint_data)
        assert saved['exercises'][0]['sets'][0] == {
            'index': 0, 'completed': True, 'reps': 12, 'weight_kg': 82.5}


def test_plan_v2_resume_uses_shared_execution_and_hydrates_acknowledged_progress(
    app, auth_user, client, execution_session, training_page,
):
    checkpoint = {
        'current_exercise_index': 1,
        'elapsed_seconds': 321,
        'exercises': [
            {'exercise_id': SQUAT, 'sets': [
                {'index': 0, 'completed': True, 'reps': 11, 'weight_kg': 82.5},
            ]},
            {'exercise_id': BENCH, 'sets': [
                {'index': 0, 'completed': False, 'reps': 6, 'weight_kg': 45},
            ]},
        ],
    }
    assert checkpoint_over_http(
        client, execution_session, 0, checkpoint,
    ).status_code == 200
    app.config['UIUX_PLAN_V2_ENABLED'] = True
    page, traffic, _, _ = training_page

    page.goto('http://localhost/')
    expect(page.locator('[data-today-primary]')).to_have_attribute('href', '/training')
    page.locator('[data-today-primary]').click()
    expect(page.locator('[data-workout-action="resume"]')).to_have_count(1)
    page.locator('[data-action="startWorkout"]').click()

    expect(page.locator('#session-view')).to_have_class('session-view open')
    expect(page.locator('[data-action="closeSession"]')).to_be_focused()
    # Active workout surface: the hydrated checkpoint alone decides the states.
    # Squat's only set is done, so Bench (the persisted cursor) leads with its
    # first open set as the single editable entry.
    body = page.locator('#sv-body')
    expect(body.locator('.aw-active')).to_have_count(1)
    expect(body.locator('.aw-active')).to_have_attribute('data-ex', '1')
    expect(body.locator('.aw-current')).to_have_count(1)
    expect(body.locator('.aw-current [data-field="weight"]')).to_have_value('45')
    expect(body.locator('.aw-current [data-field="reps"]')).to_have_value('6')
    expect(body.locator('.set-row[data-ex="1"][data-set="0"]')).to_have_attribute(
        'data-set-state', 'active')
    expect(body.locator('.aw-exercise[data-ex="0"]')).to_have_attribute(
        'data-exercise-state', 'completed')
    expect(body.locator('[data-field="done"], input[type="checkbox"]')).to_have_count(0)
    expect(page.locator('#sv-count')).to_have_text(re.compile(r'^1 / 2 '))
    # The finished exercise stays reviewable, compactly, with its saved values.
    body.locator('[data-open-exercise="0"]').click()
    expect(body.locator('.aw-active')).to_have_attribute('data-ex', '0')
    completed = body.locator('.set-row[data-ex="0"][data-set="0"]')
    expect(completed).to_have_attribute('data-set-state', 'completed')
    expect(completed).to_have_class(re.compile(r'\bis-done\b'))
    expect(completed.locator('.aw-set-value')).to_have_text('82.5 kg × 11')
    expect(body.locator('.aw-current')).to_have_count(0)
    assert any(path.endswith('/resume') and status == 200
               for path, _, status in traffic)
    page.keyboard.press('Escape')
    expect(page.locator('#session-view')).not_to_have_class('session-view open')
    expect(page.locator('[data-action="startWorkout"]')).to_be_focused()


def test_plan_v2_start_refresh_checkpoint_and_complete_use_durable_contract(
    app, auth_user, client, sessions_on, proof_accepted, training_page,
):
    with app.app_context():
        db.session.get(User, auth_user.id).profile_complete = True
        plan = save_workout_plan(auth_user.id)
        data = json.loads(plan.plan_data)
        for day in data['program']:
            day.update(odak='Strength', sure_dk=30, tahmini_kalori=150)
            for exercise in day['egzersizler']:
                exercise.update(set=1, tekrar='8', dinlenme='60 sn', not_='')
        plan.plan_data = json.dumps(data)
        db.session.commit()
    app.config['UIUX_PLAN_V2_ENABLED'] = True
    page, traffic, _, _ = training_page

    page.goto('http://localhost/')
    expect(page.locator('[data-today-primary]')).to_have_attribute('href', '/training')
    page.locator('[data-today-primary]').click()
    expect(page.locator('[data-workout-action="start"]')).to_have_count(1)
    page.locator('[data-action="startWorkout"]').click()
    expect(page.locator('#session-view')).to_have_class('session-view open')
    expect(page.locator('[data-action="closeSession"]')).to_be_focused()
    page.keyboard.press('Shift+Tab')
    expect(page.locator('[data-action="finishSession"]')).to_be_focused()
    page.keyboard.press('Tab')
    expect(page.locator('[data-action="closeSession"]')).to_be_focused()
    assert any(path == '/workout/session/start' and status == 201
               for path, _, status in traffic)

    body = page.locator('#sv-body')
    finish = page.locator('[data-action="finishSession"]')
    # Complete Set is the primary action; Finish stays available but secondary.
    expect(body.locator('.aw-active')).to_have_attribute('data-ex', '0')
    expect(page.locator('#aw-active-set')).to_have_text(re.compile(r'^Set 1 '))
    expect(finish).to_have_class(re.compile(r'\bbtn-ghost\b'))
    expect(page.locator('#sv-count')).to_have_text(re.compile(r'^0 / 2 '))
    body.locator('.aw-current [data-field="weight"]').fill('82.5')
    body.locator('.aw-current [data-field="reps"]').fill('11')
    with page.expect_response(
        lambda response: urlsplit(response.url).path.endswith('/checkpoint')
        and response.status == 200
        and '"completed": true' in (response.request.post_data or '').replace('":true', '": true')
    ) as completed_set:
        body.locator('[data-set-action="complete"]').click()
    sent = json.loads(completed_set.value.request.post_data)
    assert sent['checkpoint']['exercises'][0]['sets'][0] == {
        'index': 0, 'completed': True, 'reps': 11, 'weight_kg': 82.5}
    expect(body.locator('.aw-exercise[data-ex="0"]')).to_have_attribute(
        'data-exercise-state', 'completed')
    expect(body.locator('.aw-active')).to_have_attribute('data-ex', '1')
    expect(page.locator('#aw-active-set')).to_be_focused()
    expect(page.locator('#sv-count')).to_have_text(re.compile(r'^1 / 2 '))
    bootstrap_reads = sum(path == '/training/bootstrap' for path, _, _ in traffic)
    with page.expect_response(
        lambda response: urlsplit(response.url).path == '/training/bootstrap'
        and response.status == 200
    ):
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    assert sum(path == '/training/bootstrap' for path, _, _ in traffic) == bootstrap_reads + 1
    # Rebuilt from the server's checkpoint: the completed set did not vanish.
    expect(body.locator('.aw-exercise[data-ex="0"]')).to_have_attribute(
        'data-exercise-state', 'completed')
    # Correct the completed set: Edit reopens it, Complete Set saves it again.
    body.locator('[data-open-exercise="0"]').click()
    with page.expect_response(
        lambda response: urlsplit(response.url).path.endswith('/checkpoint')
        and response.status == 200
    ):
        body.locator('.set-row[data-ex="0"][data-set="0"] [data-set-action="edit"]').click()
    expect(body.locator('.aw-current [data-field="weight"]')).to_have_value('82.5')
    body.locator('.aw-current [data-field="reps"]').fill('12')
    with page.expect_response(
        lambda response: urlsplit(response.url).path.endswith('/checkpoint')
        and response.status == 200
        and '"reps": 12' in (response.request.post_data or '').replace('":12', '": 12')
        and '"completed": true' in (response.request.post_data or '').replace('":true', '": true')
    ):
        body.locator('[data-set-action="complete"]').click()
    page.locator('[data-action="closeSession"]').click()
    expect(page.locator('#session-view')).not_to_have_class('session-view open')
    expect(page.locator('[data-workout-action="resume"]')).to_have_count(1)
    page.locator('[data-action="startWorkout"]').click()
    expect(page.locator('#session-view')).to_have_class('session-view open')
    body.locator('[data-open-exercise="0"]').click()
    expect(body.locator('.set-row[data-ex="0"][data-set="0"] .aw-set-value')).to_have_text(
        '82.5 kg × 12')
    assert any(path.endswith('/resume') and status == 200
               for path, _, status in traffic)
    finish.click()
    expect(page.locator('#plan-completion')).to_have_class('plan-completion open')
    expect(page.locator('#plan-pump-image')).to_be_focused()
    page.keyboard.press('Shift+Tab')
    expect(page.locator('[data-action="submitWorkoutCompletion"]')).to_be_focused()
    page.keyboard.press('Tab')
    expect(page.locator('#plan-pump-image')).to_be_focused()
    page.locator('#plan-pump-image').set_input_files({
        'name': 'proof.jpg', 'mimeType': 'image/jpeg', 'buffer': b'jpeg-proof',
    })
    page.locator('[data-action="submitWorkoutCompletion"]').click()
    expect(page.locator('[data-workout-action="none"]')).to_have_count(1)

    completion = [json.loads(body) for path, body, status in traffic
                  if path == '/workout/complete' and status == 200]
    assert len(completion) == 1
    assert isinstance(completion[0]['session_id'], str)
    assert completion[0]['session_id']
    assert completion[0]['expected_checkpoint_revision'] >= 1
    with app.app_context():
        row = row_for(auth_user.id)
        assert row.status == 'completed'
        saved = json.loads(row.checkpoint_data)
        assert saved['exercises'][0]['sets'][0] == {
            'index': 0, 'completed': True, 'reps': 12, 'weight_kg': 82.5,
        }


def test_plan_v2_shell_browser_qa_across_locales_and_viewports(
    app, auth_user, training_page,
):
    with app.app_context():
        user = db.session.get(User, auth_user.id)
        user.profile_complete = True
        plan = save_workout_plan(auth_user.id)
        data = json.loads(plan.plan_data)
        for day in data['program']:
            day.update(odak='Strength', sure_dk=30, tahmini_kalori=150)
            for exercise in day['egzersizler']:
                exercise.update(set=1, tekrar='8', dinlenme='60 sn', not_='')
        plan.plan_data = json.dumps(data)
        db.session.commit()
    app.config['UIUX_PLAN_V2_ENABLED'] = True
    app.config['FITX_WORKOUT_SESSIONS_ENABLED'] = False
    page, _, _, _ = training_page
    console_errors = []
    page_errors = []
    page.on('console', lambda message: console_errors.append(message.text))
    page.on('pageerror', lambda error: page_errors.append(str(error)))

    for locale in ('en', 'tr'):
        with app.app_context():
            db.session.get(User, auth_user.id).language = locale
            db.session.commit()
        for width in (320, 390, 768, 1024, 1366):
            page.set_viewport_size({'width': width, 'height': 900})
            page.goto('http://localhost/training')
            assert page.locator('html').get_attribute('lang') == locale
            assert page.locator('h1').count() == 1
            active_nav = page.locator('[data-nav-id="plan"][aria-current="page"]')
            assert active_nav.count() >= 1  # desktop and mobile chrome may coexist
            assert page.locator('a[href="/nutrition"]').count() >= 1
            assert page.locator('a[href="/supplements"]').count() >= 1
            assert 'plan.' not in page.locator('body').inner_text()
            metrics = page.evaluate('''() => ({
              overflow: document.documentElement.scrollWidth > innerWidth,
              hiddenSessionFocusable: Array.from(
                document.querySelectorAll('#session-view button, #session-view input')
              ).some(node => node.getClientRects().length > 0),
              hiddenCompletionFocusable: Array.from(
                document.querySelectorAll('#plan-completion button, #plan-completion input')
              ).some(node => node.getClientRects().length > 0),
            })''')
            assert metrics == {
                'overflow': False,
                'hiddenSessionFocusable': False,
                'hiddenCompletionFocusable': False,
            }
    assert not [error for error in console_errors
                if 'Failed to load resource' not in error]
    assert not page_errors


def test_plan_v2_sessions_off_refresh_preserves_open_legacy_draft(
    app, auth_user, training_page,
):
    with app.app_context():
        user = db.session.get(User, auth_user.id)
        user.profile_complete = True
        plan = save_workout_plan(auth_user.id)
        data = json.loads(plan.plan_data)
        for day in data['program']:
            day.update(odak='Strength', sure_dk=30, tahmini_kalori=150)
            for exercise in day['egzersizler']:
                exercise.update(set=1, tekrar='8', dinlenme='60 sn', not_='')
        plan.plan_data = json.dumps(data)
        db.session.commit()
    app.config['UIUX_PLAN_V2_ENABLED'] = True
    app.config['FITX_WORKOUT_SESSIONS_ENABLED'] = False
    page, _, _, _ = training_page
    page.goto('http://localhost/training')
    page.locator('[data-action="startWorkout"]').click()
    expect(page.locator('#session-view')).to_have_class('session-view open')
    page.locator('#sv-body [data-field="weight"]').first.fill('82.5')
    page.locator('#sv-body [data-field="reps"]').first.fill('11')

    with page.expect_response(
        lambda response: urlsplit(response.url).path == '/training/bootstrap'
        and response.status == 200
    ):
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")

    expect(page.locator('#session-view')).to_have_class('session-view open')
    expect(page.locator('#sv-body [data-field="weight"]').first).to_have_value('82.5')
    expect(page.locator('#sv-body [data-field="reps"]').first).to_have_value('11')


def _seed_progression_plan(app, user_id):
    """Two exercises: three sets whose prescription reps differ from a typed 8,
    then one set that must not inherit the previous exercise."""
    with app.app_context():
        user = db.session.get(User, user_id)
        user.profile_complete = True
        user.language = 'en'
        plan = save_workout_plan(user_id)
        data = json.loads(plan.plan_data)
        for day in data['program']:
            day.update(odak='Strength', sure_dk=30, tahmini_kalori=150)
            exercises = day['egzersizler']
            if len(exercises) < 2:
                continue
            exercises[0].update(set=3, tekrar='8-12', dinlenme='90 sn')
            exercises[1].update(set=1, tekrar='10', dinlenme='60 sn')
        plan.plan_data = json.dumps(data)
        db.session.commit()


def _open_session(page, navigate=True):
    if navigate:
        page.goto('http://localhost/training')
    page.locator('[data-action="startWorkout"]').click()
    expect(page.locator('#session-view')).to_have_class('session-view open')
    return page.locator('#sv-body')


def _complete_current(page, body):
    with page.expect_response(
        lambda response: urlsplit(response.url).path.endswith('/checkpoint')
        and response.status == 200
        and '"completed": true' in (response.request.post_data or '').replace('":true', '": true')
    ) as completed:
        body.locator('[data-set-action="complete"]').click()
    return json.loads(completed.value.request.post_data)['checkpoint']


def _surface_metrics(page):
    return page.evaluate('''() => {
      const heading = document.getElementById('aw-active-set');
      const rect = heading ? heading.getBoundingClientRect() : null;
      return {
        focusedId: document.activeElement && document.activeElement.id,
        inputFocused: !!(document.activeElement && document.activeElement.matches('input, textarea')),
        inView: !!(rect && rect.height > 0 && rect.top >= 0 && rect.bottom <= window.innerHeight),
        overflow: document.documentElement.scrollWidth > window.innerWidth,
      };
    }''')


def test_one_tap_set_progression_prefills_advances_and_survives_refresh(
    app, auth_user, client, sessions_on, training_page,
):
    _seed_progression_plan(app, auth_user.id)
    app.config['UIUX_PLAN_V2_ENABLED'] = True
    page, traffic, _, _ = training_page
    page.set_viewport_size({'width': 390, 'height': 844})
    body = _open_session(page)
    finish = page.locator('[data-action="finishSession"]')

    expect(page.locator('#aw-active-set')).to_have_text(re.compile(r'^Set 1 of 3$'))
    expect(page.locator('#sv-count')).to_have_text(re.compile(r'^0 / 4 sets$'))
    expect(finish).to_have_class(re.compile(r'\bbtn-ghost\b'))
    expect(body.locator('.aw-current [data-field="reps"]')).to_have_value('12')

    body.locator('.aw-current [data-field="weight"]').fill('60')
    body.locator('.aw-current [data-field="reps"]').fill('8')
    sent = _complete_current(page, body)
    assert sent['current_exercise_index'] == 0
    assert sent['exercises'][0]['sets'][0] == {
        'index': 0, 'completed': True, 'reps': 8, 'weight_kg': 60}
    assert sent['exercises'][0]['sets'][1] == {
        'index': 1, 'completed': False, 'reps': 8, 'weight_kg': 60}
    # Untouched sets stay on the prescription: null in the snapshot, so a
    # refresh can still copy onto them. A confirmed 12 would be stored as 12.
    assert sent['exercises'][0]['sets'][2]['weight_kg'] is None
    assert sent['exercises'][0]['sets'][2]['reps'] is None
    assert sent['exercises'][1]['sets'][0] == {
        'index': 0, 'completed': False, 'reps': None, 'weight_kg': None}

    expect(body.locator('.aw-current')).to_have_attribute('data-set', '1')
    expect(page.locator('#aw-active-set')).to_have_text(re.compile(r'^Set 2 of 3$'))
    expect(body.locator('.aw-current [data-field="weight"]')).to_have_value('60')
    expect(body.locator('.aw-current [data-field="reps"]')).to_have_value('8')
    expect(body.locator('.set-row[data-ex="0"][data-set="0"]')).to_have_attribute(
        'data-set-state', 'completed')
    metrics = _surface_metrics(page)
    assert metrics['focusedId'] == 'aw-active-set'
    assert metrics['inputFocused'] is False
    assert metrics['inView'] is True
    assert metrics['overflow'] is False

    page.reload()
    expect(page.locator('[data-workout-action="resume"]')).to_have_count(1)
    body = _open_session(page, navigate=False)
    expect(body.locator('.aw-active')).to_have_attribute('data-ex', '0')
    expect(body.locator('.aw-current')).to_have_attribute('data-set', '1')
    expect(body.locator('.aw-current [data-field="weight"]')).to_have_value('60')
    expect(body.locator('.aw-current [data-field="reps"]')).to_have_value('8')
    expect(body.locator('.set-row[data-ex="0"][data-set="0"]')).to_have_attribute(
        'data-set-state', 'completed')

    body.locator('.aw-current [data-field="weight"]').fill('62.5')
    sent = _complete_current(page, body)
    assert sent['exercises'][0]['sets'][2] == {
        'index': 2, 'completed': False, 'reps': 8, 'weight_kg': 62.5}
    expect(page.locator('#aw-active-set')).to_have_text(re.compile(r'^Set 3 of 3$'))
    expect(body.locator('.aw-current [data-field="weight"]')).to_have_value('62.5')
    expect(body.locator('.aw-current [data-field="reps"]')).to_have_value('8')

    # Correcting set 1 must not rewind the workout onto set 2 or replace set 3.
    with page.expect_response(
        lambda response: urlsplit(response.url).path.endswith('/checkpoint')
        and response.status == 200
    ):
        body.locator('.set-row[data-ex="0"][data-set="0"] [data-set-action="edit"]').click()
    expect(body.locator('.aw-current')).to_have_attribute('data-set', '0')
    body.locator('.aw-current [data-field="weight"]').fill('55')
    body.locator('.aw-current [data-field="reps"]').fill('5')
    _complete_current(page, body)
    expect(body.locator('.aw-active')).to_have_attribute('data-ex', '0')
    expect(body.locator('.aw-current')).to_have_attribute('data-set', '2')
    expect(body.locator('.aw-current [data-field="weight"]')).to_have_value('62.5')
    expect(body.locator('.aw-current [data-field="reps"]')).to_have_value('8')
    expect(body.locator('.set-row[data-ex="0"][data-set="1"]')).to_have_attribute(
        'data-set-state', 'completed')

    _complete_current(page, body)
    expect(body.locator('.aw-exercise[data-ex="0"]')).to_have_attribute(
        'data-exercise-state', 'completed')
    expect(body.locator('.aw-active')).to_have_attribute('data-ex', '1')
    expect(body.locator('.aw-active-name')).to_have_text('Bench')
    expect(page.locator('#aw-active-set')).to_have_text(re.compile(r'^Set 1 of 1$'))
    expect(body.locator('.aw-current [data-field="weight"]')).to_have_value('')
    expect(body.locator('.aw-current [data-field="reps"]')).to_have_value('10')
    metrics = _surface_metrics(page)
    assert metrics['inView'] is True
    assert metrics['inputFocused'] is False

    body.locator('.aw-current [data-field="weight"]').fill('40')
    _complete_current(page, body)
    expect(body.locator('.aw-current')).to_have_count(0)
    expect(body.locator('[data-set-state="active"]')).to_have_count(0)
    expect(body.locator('.aw-all-done')).to_be_visible()
    expect(finish).to_have_class(re.compile(r'\bbtn-volt\b'))
    expect(finish).not_to_have_class(re.compile(r'\bbtn-ghost\b'))
    expect(page.locator('#plan-completion')).not_to_have_class(re.compile(r'\bopen\b'))
    assert not any(path == '/workout/complete' for path, _, _ in traffic)

    finish.click()
    expect(page.locator('#plan-completion')).to_have_class('plan-completion open')
    assert not any(path == '/workout/complete' for path, _, _ in traffic)


def test_one_tap_set_progression_desktop_smoke(
    app, auth_user, client, sessions_on, training_page,
):
    _seed_progression_plan(app, auth_user.id)
    app.config['UIUX_PLAN_V2_ENABLED'] = True
    page, _, _, _ = training_page
    page.set_viewport_size({'width': 1366, 'height': 900})
    body = _open_session(page)
    body.locator('.aw-current [data-field="weight"]').fill('60')
    body.locator('.aw-current [data-field="reps"]').fill('8')
    _complete_current(page, body)
    expect(page.locator('#aw-active-set')).to_have_text(re.compile(r'^Set 2 of 3$'))
    expect(body.locator('.aw-current [data-field="weight"]')).to_have_value('60')
    expect(body.locator('.aw-current [data-field="reps"]')).to_have_value('8')
    expect(page.locator('[data-action="finishSession"]')).to_have_class(re.compile(r'\bbtn-ghost\b'))
    metrics = _surface_metrics(page)
    assert metrics == {
        'focusedId': 'aw-active-set',
        'inputFocused': False,
        'inView': True,
        'overflow': False,
    }


def test_active_workout_derivation_contract_passes_in_node():
    """CI runs pytest only; execute the shared draft module's node suite here so
    the active-surface state derivation (completed/active/upcoming) gates it."""
    result = subprocess.run(['node', '--test', 'tests/js/workout_draft.test.js'],
                            cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'fail 0' in result.stdout, result.stdout
