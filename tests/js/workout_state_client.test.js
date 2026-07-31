const test = require('node:test');
const assert = require('node:assert/strict');
const { createWorkoutStateClient } = require('../../static/workout_state_client.js');

function deferred() {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return { promise, resolve };
}

function response(body, ok = true, status = ok ? 200 : 500) {
  return { ok, status, json: async () => body };
}

test('an older bootstrap response cannot overwrite a newer snapshot', async () => {
  const first = deferred();
  const second = deferred();
  const applied = [];
  let call = 0;
  const client = createWorkoutStateClient({
    fetchImpl: () => (++call === 1 ? first.promise : second.promise),
    onSnapshot: value => applied.push(value),
    addEventListener: () => {}, removeEventListener: () => {},
    documentRef: { hidden: false, addEventListener() {}, removeEventListener() {} },
  });

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
  const client = createWorkoutStateClient({
    fetchImpl: () => queue.shift(), onSnapshot: value => applied.push(value),
    addEventListener: () => {}, removeEventListener: () => {},
    documentRef: { hidden: false, addEventListener() {}, removeEventListener() {} },
  });

  const stale = client.refresh('focus');
  const changed = client.mutate('/workout/session/start', { method: 'POST' });
  mutation.resolve(response({ outcome: 'created', session: { public_id: 's1' } }));
  after.resolve(response({ workout: { state: { contract_version: 2 } }, plan: {} }));
  await changed;
  focus.resolve(response({ workout: { state: { contract_version: 1 } }, plan: {} }));
  await stale;

  assert.equal(applied.length, 1);
  assert.equal(applied[0].workout.state.contract_version, 2);
});

test('heartbeat is 60 seconds, single-flight, and terminal response refreshes', async () => {
  const timers = [];
  const ticks = [];
  let clears = 0;
  const scheduler = {
    setInterval(fn, ms) { timers.push({ fn, ms }); return timers.length; },
    clearInterval() { clears++; },
    now: () => 10000,
  };
  const beat = deferred();
  let calls = 0;
  const client = createWorkoutStateClient({
    fetchImpl: (url) => {
      calls++;
      if (url.includes('checkpoint')) return beat.promise;
      return Promise.resolve(response({ workout: { state: { contract_version: 2 } }, plan: {} }));
    },
    scheduler, onSnapshot: value => ticks.push(value),
    addEventListener: () => {}, removeEventListener: () => {},
    documentRef: { hidden: false, addEventListener() {}, removeEventListener() {} },
  });

  client.startHeartbeat('session-1');
  assert.equal(timers[0].ms, 60000);
  const p1 = timers[0].fn();
  const p2 = timers[0].fn();
  assert.equal(calls, 1);
  beat.resolve(response({ outcome: 'already_completed', session: null }));
  await Promise.all([p1, p2]);

  assert.equal(calls, 2);
  assert.equal(ticks.length, 1);
  assert.equal(clears, 1);
  assert.equal(client.getSessionId(), null);
});

test('blocked refresh and hidden visibility stop heartbeat immediately', async () => {
  let visibilityHandler;
  let clears = 0;
  const documentRef = {
    hidden: false,
    addEventListener(name, fn) { if (name === 'visibilitychange') visibilityHandler = fn; },
    removeEventListener() {},
  };
  const scheduler = {
    setInterval() { return 9; }, clearInterval() { clears++; }, now: () => 0,
  };
  const client = createWorkoutStateClient({
    fetchImpl: () => Promise.resolve(response({
      workout: { state: { contract_version: 2, session_state: 'active_blocked',
        session: { status: 'active', public_id: 's1' } } }, plan: {}
    })),
    scheduler, documentRef, onSnapshot: () => {},
    addEventListener: () => {}, removeEventListener: () => {},
  });

  client.startHeartbeat('s1');
  await client.refresh('blocked');
  assert.equal(clears, 1);
  client.startHeartbeat('s1');
  documentRef.hidden = true;
  visibilityHandler();
  assert.equal(clears, 2);
  assert.equal(client.getSessionId(), null);
});

test('destroy tears down heartbeat and prevents later refreshes', async () => {
  let clears = 0;
  let calls = 0;
  const client = createWorkoutStateClient({
    fetchImpl: () => { calls++; return Promise.resolve(response({})); },
    scheduler: { setInterval() { return 1; }, clearInterval() { clears++; }, now: () => 0 },
    documentRef: { hidden: false, addEventListener() {}, removeEventListener() {} },
    addEventListener: () => {}, removeEventListener: () => {},
  });
  client.startHeartbeat('s1');
  client.destroy();
  await client.refresh('late');

  assert.equal(clears, 1);
  assert.equal(calls, 0);
});

test('concurrent repeated mutations share one write and one authoritative refresh', async () => {
  const write = deferred();
  const bootstrap = deferred();
  const urls = [];
  const applied = [];
  const client = createWorkoutStateClient({
    fetchImpl: url => {
      urls.push(url);
      return url === '/workout/session/start' ? write.promise : bootstrap.promise;
    },
    onSnapshot: value => applied.push(value),
    addEventListener: () => {}, removeEventListener: () => {},
    documentRef: { hidden: false, addEventListener() {}, removeEventListener() {} },
  });

  const first = client.mutate('/workout/session/start', { method: 'POST' });
  const repeated = client.mutate('/workout/session/start', { method: 'POST' });
  write.resolve(response({ outcome: 'created' }, true, 201));
  bootstrap.resolve(response({ workout: { state: { contract_version: 2 } }, plan: {} }));
  const [firstResult, repeatedResult] = await Promise.all([first, repeated]);

  assert.deepEqual(urls, ['/workout/session/start', '/training/bootstrap']);
  assert.equal(applied.length, 1);
  assert.equal(firstResult.ok, true);
  assert.deepEqual(repeatedResult, firstResult);
});

test('a non-ok mutation never reports success and still refreshes canonical state', async () => {
  const urls = [];
  const applied = [];
  const client = createWorkoutStateClient({
    fetchImpl: url => {
      urls.push(url);
      if (url === '/workout/complete') {
        return Promise.resolve(response({ error: 'conflict' }, false, 409));
      }
      return Promise.resolve(response({ workout: { state: { contract_version: 2 } }, plan: {} }));
    },
    onSnapshot: value => applied.push(value),
    addEventListener: () => {}, removeEventListener: () => {},
    documentRef: { hidden: false, addEventListener() {}, removeEventListener() {} },
  });

  const result = await client.mutate('/workout/complete', { method: 'POST' });

  assert.deepEqual(urls, ['/workout/complete', '/training/bootstrap']);
  assert.equal(result.ok, false);
  assert.equal(result.status, 409);
  assert.equal(applied.length, 1);
});

test('a thrown mutation propagates without success and still refreshes canonical state', async () => {
  const urls = [];
  const applied = [];
  const client = createWorkoutStateClient({
    fetchImpl: url => {
      urls.push(url);
      if (url === '/workout/complete') return Promise.reject(new Error('network_down'));
      return Promise.resolve(response({ workout: { state: { contract_version: 2 } }, plan: {} }));
    },
    onSnapshot: value => applied.push(value),
    addEventListener: () => {}, removeEventListener: () => {},
    documentRef: { hidden: false, addEventListener() {}, removeEventListener() {} },
  });

  await assert.rejects(
    client.mutate('/workout/complete', { method: 'POST' }),
    /network_down/
  );
  assert.deepEqual(urls, ['/workout/complete', '/training/bootstrap']);
  assert.equal(applied.length, 1);
});

test('a superseded refresh failure cannot block or clear a newer active snapshot', async () => {
  const oldRequest = deferred();
  const newRequest = deferred();
  let call = 0;
  let blocked = 0;
  const client = createWorkoutStateClient({
    fetchImpl: () => (++call === 1 ? oldRequest.promise : newRequest.promise),
    onBlocked: () => { blocked++; },
    scheduler: { setInterval() { return 1; }, clearInterval() {}, now: () => 0 },
    addEventListener: () => {}, removeEventListener: () => {},
    documentRef: { hidden: false, addEventListener() {}, removeEventListener() {} },
  });

  const stale = client.refresh('load');
  const current = client.refresh('focus');
  newRequest.resolve(response({ workout: { state: {
    contract_version: 2,
    session_state: 'active_resumable',
    session: { status: 'active', public_id: 'new-session' },
  } }, plan: {} }));
  await current;
  oldRequest.resolve(Promise.reject(new Error('superseded_failure')));
  await stale;

  assert.equal(blocked, 0);
  assert.equal(client.getSessionId(), 'new-session');
});

test('destroy is idempotent and removes owned listeners exactly once', () => {
  let removeVisibility = 0;
  let removeFocus = 0;
  const client = createWorkoutStateClient({
    fetchImpl: () => Promise.resolve(response({})),
    documentRef: {
      hidden: false,
      addEventListener() {},
      removeEventListener(name) { if (name === 'visibilitychange') removeVisibility++; },
    },
    addEventListener: () => {},
    removeEventListener: name => { if (name === 'focus') removeFocus++; },
  });

  client.destroy();
  client.destroy();

  assert.equal(removeVisibility, 1);
  assert.equal(removeFocus, 1);
});

test('repeated active snapshots keep exactly one heartbeat timer', async () => {
  let sets = 0;
  let clears = 0;
  const active = { workout: { state: {
    contract_version: 2,
    session_state: 'active_resumable',
    session: { status: 'active', public_id: 'session-1' },
  } }, plan: {} };
  const client = createWorkoutStateClient({
    fetchImpl: () => Promise.resolve(response(active)),
    scheduler: {
      setInterval() { sets++; return sets; },
      clearInterval() { clears++; },
      now: () => 0,
    },
    addEventListener: () => {}, removeEventListener: () => {},
    documentRef: { hidden: false, addEventListener() {}, removeEventListener() {} },
  });

  await client.refresh('load');
  await client.refresh('focus');

  assert.equal(sets, 1);
  assert.equal(clears, 0);
  assert.equal(client.getSessionId(), 'session-1');
});

test('authentication expiration clears heartbeat and session identity', async () => {
  let intervalFn;
  let clears = 0;
  let bootstrapCalls = 0;
  let blocked = 0;
  const client = createWorkoutStateClient({
    fetchImpl: url => {
      if (url.includes('checkpoint')) return Promise.resolve(response({}, false, 401));
      bootstrapCalls++;
      return Promise.resolve(response({}, false, 401));
    },
    onBlocked: () => { blocked++; },
    scheduler: {
      setInterval(fn) { intervalFn = fn; return 1; },
      clearInterval() { clears++; },
      now: () => 0,
    },
    addEventListener: () => {}, removeEventListener: () => {},
    documentRef: { hidden: false, addEventListener() {}, removeEventListener() {} },
  });

  client.startHeartbeat('session-1');
  await intervalFn();

  assert.equal(clears, 1);
  assert.equal(client.getSessionId(), null);
  assert.equal(bootstrapCalls, 1);
  assert.equal(blocked, 1);
});

test('heartbeat failure performs one bounded refresh without immediate checkpoint retry', async () => {
  let intervalFn;
  let clears = 0;
  const urls = [];
  const client = createWorkoutStateClient({
    fetchImpl: url => {
      urls.push(url);
      if (url.includes('checkpoint')) return Promise.reject(new Error('offline'));
      return Promise.resolve(response({ workout: { state: {
        contract_version: 2, session_state: 'none', session: null,
      } }, plan: {} }));
    },
    scheduler: {
      setInterval(fn) { intervalFn = fn; return 1; },
      clearInterval() { clears++; },
      now: () => 0,
    },
    addEventListener: () => {}, removeEventListener: () => {},
    documentRef: { hidden: false, addEventListener() {}, removeEventListener() {} },
  });

  client.startHeartbeat('session-1');
  await intervalFn();

  assert.deepEqual(urls, [
    '/workout/session/session-1/checkpoint',
    '/training/bootstrap',
  ]);
  assert.equal(clears, 1);
  assert.equal(client.getSessionId(), null);
});

test('a checkpoint superseded by hide and visible refresh cannot clear the newer heartbeat', async () => {
  const oldCheckpoint = deferred();
  let visibilityHandler;
  let nextTimer = 0;
  const activeTimers = new Map();
  const urls = [];
  const documentRef = {
    hidden: false,
    addEventListener(name, fn) {
      if (name === 'visibilitychange') visibilityHandler = fn;
    },
    removeEventListener() {},
  };
  const active = { workout: { state: {
    contract_version: 2,
    session_state: 'active_resumable',
    session: { status: 'active', public_id: 'session-1' },
  } }, plan: {} };
  const client = createWorkoutStateClient({
    fetchImpl: (url, init) => {
      urls.push({ url, init });
      if (url.includes('checkpoint')) return oldCheckpoint.promise;
      return Promise.resolve(response(active));
    },
    scheduler: {
      setInterval(fn) {
        const id = ++nextTimer;
        activeTimers.set(id, fn);
        return id;
      },
      clearInterval(id) { activeTimers.delete(id); },
      now: () => 0,
    },
    documentRef,
    addEventListener: () => {}, removeEventListener: () => {},
  });

  client.startHeartbeat('session-1');
  const oldTick = activeTimers.values().next().value();
  assert.ok(urls[0].init.signal, 'checkpoint request owns an abort signal');
  assert.equal(urls[0].init.signal.aborted, false);
  documentRef.hidden = true;
  visibilityHandler();
  assert.equal(urls[0].init.signal.aborted, true);

  documentRef.hidden = false;
  visibilityHandler();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(activeTimers.size, 1);
  assert.equal(client.getSessionId(), 'session-1');

  oldCheckpoint.resolve(response({ outcome: 'already_completed', session: null }));
  await oldTick;

  assert.equal(urls.filter(call => call.url === '/training/bootstrap').length, 1);
  assert.equal(activeTimers.size, 1);
  assert.equal(client.getSessionId(), 'session-1');
});

test('destroy aborts pending mutation and checkpoint without stale refresh or blocking', async () => {
  const mutation = deferred();
  const checkpoint = deferred();
  const requests = [];
  let intervalFn;
  let snapshots = 0;
  let blocked = 0;
  const client = createWorkoutStateClient({
    fetchImpl: (url, init) => {
      requests.push({ url, init });
      return url.includes('checkpoint') ? checkpoint.promise : mutation.promise;
    },
    onSnapshot: () => { snapshots++; },
    onBlocked: () => { blocked++; },
    scheduler: {
      setInterval(fn) { intervalFn = fn; return 1; },
      clearInterval() {},
      now: () => 0,
    },
    documentRef: { hidden: false, addEventListener() {}, removeEventListener() {} },
    addEventListener: () => {}, removeEventListener: () => {},
  });

  const pendingMutation = client.mutate('/workout/complete', {
    method: 'PUT',
    headers: { 'X-Test': 'preserved' },
    body: 'payload',
  });
  client.startHeartbeat('session-1');
  const pendingCheckpoint = intervalFn();
  assert.equal(requests[0].init.method, 'PUT');
  assert.deepEqual(requests[0].init.headers, { 'X-Test': 'preserved' });
  assert.equal(requests[0].init.body, 'payload');
  assert.ok(requests[0].init.signal, 'mutation request owns an abort signal');
  assert.ok(requests[1].init.signal, 'checkpoint request owns an abort signal');
  assert.equal(requests[0].init.signal.aborted, false);
  assert.equal(requests[1].init.signal.aborted, false);

  client.destroy();
  assert.equal(requests[0].init.signal.aborted, true);
  assert.equal(requests[1].init.signal.aborted, true);

  mutation.resolve(response({ outcome: 'completed' }));
  checkpoint.resolve(response({ outcome: 'already_completed' }));
  const [mutationResult] = await Promise.all([pendingMutation, pendingCheckpoint]);

  assert.equal(requests.length, 2);
  assert.equal(mutationResult, null);
  assert.equal(snapshots, 0);
  assert.equal(blocked, 0);
});

test('a second client for the same document replaces the first owner', () => {
  let timerId = 0;
  const activeTimers = new Set();
  const visibilityListeners = new Set();
  const focusListeners = new Set();
  const documentRef = {
    hidden: false,
    addEventListener(name, fn) {
      if (name === 'visibilitychange') visibilityListeners.add(fn);
    },
    removeEventListener(name, fn) {
      if (name === 'visibilitychange') visibilityListeners.delete(fn);
    },
  };
  const scheduler = {
    setInterval() {
      const id = ++timerId;
      activeTimers.add(id);
      return id;
    },
    clearInterval(id) { activeTimers.delete(id); },
    now: () => 0,
  };
  const options = {
    fetchImpl: () => Promise.resolve(response({})),
    scheduler,
    documentRef,
    addEventListener(name, fn) {
      if (name === 'focus') focusListeners.add(fn);
    },
    removeEventListener(name, fn) {
      if (name === 'focus') focusListeners.delete(fn);
    },
  };

  const first = createWorkoutStateClient(options);
  first.startHeartbeat('old-session');
  const second = createWorkoutStateClient(options);
  second.startHeartbeat('new-session');

  assert.equal(activeTimers.size, 1);
  assert.equal(visibilityListeners.size, 1);
  assert.equal(focusListeners.size, 1);
  assert.equal(first.getSessionId(), null);
  assert.equal(second.getSessionId(), 'new-session');
});

test('owner replacement makes an old pending mutation resolve without stale success', async () => {
  const oldMutation = deferred();
  const oldUrls = [];
  let newCalls = 0;
  const documentRef = {
    hidden: false,
    addEventListener() {},
    removeEventListener() {},
  };
  const listenerOptions = {
    documentRef,
    addEventListener: () => {},
    removeEventListener: () => {},
  };
  const first = createWorkoutStateClient(Object.assign({}, listenerOptions, {
    fetchImpl: (url) => {
      oldUrls.push(url);
      return oldMutation.promise;
    },
  }));
  const pending = first.mutate('/workout/complete', { method: 'POST' });

  const second = createWorkoutStateClient(Object.assign({}, listenerOptions, {
    fetchImpl: () => {
      newCalls++;
      return Promise.resolve(response({}));
    },
  }));
  oldMutation.resolve(response({ outcome: 'completed' }));
  const result = await pending;

  assert.equal(result, null);
  assert.deepEqual(oldUrls, ['/workout/complete']);
  assert.equal(newCalls, 0);
  second.destroy();
});
