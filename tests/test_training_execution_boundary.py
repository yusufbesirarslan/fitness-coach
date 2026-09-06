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
    expect(page.locator('#sv-body [data-field="weight"]').first).to_have_value('82.5')
    expect(page.locator('#sv-body [data-field="reps"]').first).to_have_value('11')
    expect(page.locator('#sv-body .set-row[data-ex="0"][data-set="0"]')).to_have_class(
        re.compile(r'\bis-done\b')
    )
    assert any(path.endswith('/resume') and status == 200
               for path, _, status in traffic)


def test_plan_v2_start_checkpoint_and_complete_use_durable_contract(
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
    assert any(path == '/workout/session/start' and status == 201
               for path, _, status in traffic)

    page.locator('#sv-body [data-field="weight"]').first.fill('82.5')
    page.locator('#sv-body [data-field="reps"]').first.fill('11')
    page.locator('#sv-body [data-field="done"]').first.click()
    page.wait_for_function(
        'document.querySelector("#sv-body .set-row").classList.contains("is-done")'
    )
    page.locator('[data-action="closeSession"]').click()
    expect(page.locator('#session-view')).not_to_have_class('session-view open')
    expect(page.locator('[data-workout-action="resume"]')).to_have_count(1)
    page.locator('[data-action="startWorkout"]').click()
    expect(page.locator('#session-view')).to_have_class('session-view open')
    expect(page.locator('#sv-body [data-field="weight"]').first).to_have_value('82.5')
    expect(page.locator('#sv-body [data-field="reps"]').first).to_have_value('11')
    assert any(path.endswith('/resume') and status == 200
               for path, _, status in traffic)
    page.locator('[data-action="finishSession"]').click()
    expect(page.locator('#plan-completion')).to_have_class('plan-completion open')
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
            'index': 0, 'completed': True, 'reps': 11, 'weight_kg': 82.5,
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
