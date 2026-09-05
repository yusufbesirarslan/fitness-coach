const test = require('node:test');
const assert = require('node:assert/strict');
const { createWorkoutStateClient } = require('../../static/workout_state_client.js');

function deferred() {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return { promise, resolve };
}

function response(body, ok = true, status = ok ? 200 : 500) {
  return { ok, status, headers: { get: () => null }, json: async () => body };
}

function options(fetchImpl, extra = {}) {
  return Object.assign({
    fetchImpl,
    addEventListener() {}, removeEventListener() {},
    documentRef: { hidden: false, addEventListener() {}, removeEventListener() {} },
  }, extra);
}

test('an older bootstrap response cannot overwrite a newer snapshot', async () => {
  const first = deferred();
  const second = deferred();
  const applied = [];
  let call = 0;
  const client = createWorkoutStateClient(options(
    () => (++call === 1 ? first.promise : second.promise),
    { onSnapshot: value => applied.push(value) },
  ));

  const oldRefresh = client.refresh('load');
  const newRefresh = client.refresh('focus');
  second.resolve(response({ workout: { state: { contract_version: 1 } }, plan: {} }));
  await newRefresh;
  first.resolve(response({ workout: { state: { contract_version: 2 } }, plan: {} }));
  await oldRefresh;

  assert.equal(applied.length, 1);
  assert.equal(applied[0].workout.state.contract_version, 1);
});

test('a mutation invalidates an in-flight focus refresh', async () => {
  const focus = deferred();
  const mutation = deferred();
  const after = deferred();
  const queue = [focus.promise, mutation.promise, after.promise];
  const applied = [];
  const client = createWorkoutStateClient(options(
    () => queue.shift(), { onSnapshot: value => applied.push(value) },
  ));

  const stale = client.refresh('focus');
  const changed = client.mutate('/workout/session/start', { method: 'POST' });
  mutation.resolve(response({ outcome: 'created', session: { public_id: 's1' } }));
  after.resolve(response({ workout: { state: { contract_version: 1 } }, plan: {} }));
  await changed;
  focus.resolve(response({ workout: { state: { contract_version: 2 } }, plan: {} }));
  await stale;

  assert.equal(applied.length, 1);
  assert.equal(applied[0].workout.state.contract_version, 1);
});

test('concurrent repeated lifecycle mutations share one write and one refresh', async () => {
  const write = deferred();
  const bootstrap = deferred();
  const urls = [];
  const client = createWorkoutStateClient(options(url => {
    urls.push(url);
    return url === '/workout/session/start' ? write.promise : bootstrap.promise;
  }));

  const first = client.mutate('/workout/session/start', { method: 'POST' });
  const repeated = client.mutate('/workout/session/start', { method: 'POST' });
  write.resolve(response({ outcome: 'created' }, true, 201));
  bootstrap.resolve(response({ workout: { state: { contract_version: 1 } }, plan: {} }));
  const [firstResult, repeatedResult] = await Promise.all([first, repeated]);

  assert.deepEqual(urls, ['/workout/session/start', '/training/bootstrap']);
  assert.equal(firstResult.ok, true);
  assert.deepEqual(repeatedResult, firstResult);
});

test('a non-ok lifecycle mutation still refreshes canonical state', async () => {
  const urls = [];
  const applied = [];
  const client = createWorkoutStateClient(options(url => {
    urls.push(url);
    if (url === '/workout/complete') return Promise.resolve(response({ code: 'revision_conflict' }, false, 409));
    return Promise.resolve(response({ workout: { state: { contract_version: 1 } }, plan: {} }));
  }, { onSnapshot: value => applied.push(value) }));

  const result = await client.mutate('/workout/complete', { method: 'POST' });

  assert.deepEqual(urls, ['/workout/complete', '/training/bootstrap']);
  assert.equal(result.ok, false);
  assert.equal(result.status, 409);
  assert.equal(applied.length, 1);
});

test('a thrown lifecycle mutation propagates and still refreshes canonical state', async () => {
  const urls = [];
  const client = createWorkoutStateClient(options(url => {
    urls.push(url);
    if (url === '/workout/complete') return Promise.reject(new Error('network_down'));
    return Promise.resolve(response({ workout: { state: { contract_version: 1 } }, plan: {} }));
  }));

  await assert.rejects(client.mutate('/workout/complete', { method: 'POST' }), /network_down/);
  assert.deepEqual(urls, ['/workout/complete', '/training/bootstrap']);
});

test('destroy is idempotent and removes owned listeners exactly once', () => {
  let removeVisibility = 0;
  let removeFocus = 0;
  const client = createWorkoutStateClient(options(
    () => Promise.resolve(response({})),
    {
      documentRef: {
        hidden: false, addEventListener() {},
        removeEventListener(name) { if (name === 'visibilitychange') removeVisibility++; },
      },
      removeEventListener: name => { if (name === 'focus') removeFocus++; },
    },
  ));

  client.destroy();
  client.destroy();

  assert.equal(removeVisibility, 1);
  assert.equal(removeFocus, 1);
});

test('owner replacement aborts an old pending mutation without stale success', async () => {
  const oldMutation = deferred();
  const documentRef = { hidden: false, addEventListener() {}, removeEventListener() {} };
  const first = createWorkoutStateClient(options(() => oldMutation.promise, { documentRef }));
  const pending = first.mutate('/workout/complete', { method: 'POST' });
  const second = createWorkoutStateClient(options(
    () => Promise.resolve(response({ workout: { state: { contract_version: 1 } } })),
    { documentRef },
  ));
  oldMutation.resolve(response({ outcome: 'completed' }));

  assert.equal(await pending, null);
  second.destroy();
});
