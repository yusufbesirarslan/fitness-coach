const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const trainingSource = fs.readFileSync(
  path.join(__dirname, '../../static/training.js'), 'utf8',
);
const sandbox = { module: { exports: {} } };
vm.runInNewContext(
  trainingSource.slice(0, trainingSource.indexOf('var __t =')),
  sandbox,
);
const {
  runWorkoutStart,
  runWorkoutEdit,
  runWorkoutFinish,
  attachWorkoutCompletion,
  TRAINING_ACTION_NAMES,
  publishTrainingActions,
} = sandbox.module.exports;

test('every Training data-action is published to the browser dispatcher', () => {
  const browserWindow = {};
  const handlers = Object.fromEntries(
    TRAINING_ACTION_NAMES.map(name => [name, function () {}]),
  );

  publishTrainingActions(browserWindow, handlers);

  const expected = [
    'abandonWorkout', 'addRest', 'closeCelebration', 'closeDayPreview',
    'closeSession', 'finishSession', 'generatePlan', 'previewDay',
    'resetPlan', 'savePlan', 'skipRest', 'startWorkout', 'submitPumpCheck',
  ];
  const sources = [
    trainingSource,
    path.join(__dirname, '../../templates/training.html'),
  ].map(value => value.endsWith('.html') ? fs.readFileSync(value, 'utf8') : value).join('\n');
  const declared = Array.from(sources.matchAll(
    /data-action(?:-self|-input|-change|-keydown)?="([^"]+)"/g,
  ), match => match[1]).sort();

  assert.deepEqual(Array.from(new Set(declared)), expected);
  assert.deepEqual(Array.from(TRAINING_ACTION_NAMES), expected);
  TRAINING_ACTION_NAMES.forEach(name => {
    assert.equal(typeof browserWindow[name], 'function', name);
  });
});

test('contract v2 browser flow orders start, edit checkpoint, finish flush, Pump, completion', async () => {
  const events = [];
  let revision = 0;

  await runWorkoutStart(2, async () => {
    events.push('session:start');
    return { ok: true };
  }, () => events.push('draft:open'));
  await runWorkoutEdit(2, async () => {
    events.push('checkpoint:R0');
    revision = 1;
    return { ok: true };
  });
  const finished = await runWorkoutFinish(2, async () => {
    events.push('checkpoint:R1');
    revision = 2;
    return { ok: true };
  }, () => events.push('pump:open'));
  const payload = attachWorkoutCompletion(2, {}, 'session-1', revision);
  events.push(`complete:R${payload.expected_checkpoint_revision}`);

  assert.equal(finished.ok, true);
  assert.deepEqual(events, [
    'session:start', 'draft:open', 'checkpoint:R0',
    'checkpoint:R1', 'pump:open', 'complete:R2',
  ]);
});

test('contract v2 final-flush failure blocks Pump and unlinked completion', async () => {
  let pumpOpened = false;
  const result = await runWorkoutFinish(
    2, async () => ({ ok: false, retryable: true }),
    () => { pumpOpened = true; },
  );

  assert.equal(result.ok, false);
  assert.equal(pumpOpened, false);
  assert.throws(
    () => attachWorkoutCompletion(2, {}, null, null),
    /session_completion_unavailable/,
  );
});

test('contract v1 start edit and finish stay legacy and make no session request', async () => {
  const sessionRequests = [];
  const events = [];
  const durableRequest = async () => sessionRequests.push('unexpected');

  await runWorkoutStart(1, durableRequest, () => events.push('draft:open'));
  const edit = await runWorkoutEdit(1, durableRequest);
  await runWorkoutFinish(1, durableRequest, () => events.push('pump:open'));
  const payload = attachWorkoutCompletion(1, { image: 'data' }, null, null);

  assert.deepEqual(sessionRequests, []);
  assert.deepEqual(events, ['draft:open', 'pump:open']);
  assert.equal(edit.disabled, true);
  assert.deepEqual(payload, { image: 'data' });
});
