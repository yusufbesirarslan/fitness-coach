const test = require('node:test');
const assert = require('node:assert/strict');
const { createWorkoutStateClient } = require('../../static/workout_state_client.js');
const {
  createWorkoutDraft,
  buildCheckpointSnapshot,
  flushWorkoutDraft,
} = require('../../static/workout_draft.js');

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function headers(values = {}) {
  const normalized = Object.fromEntries(
    Object.entries(values).map(([key, value]) => [key.toLowerCase(), value]));
  return { get(name) { return normalized[String(name).toLowerCase()] || null; } };
}

function response(body, ok = true, status = ok ? 200 : 500, responseHeaders = {}) {
  return { ok, status, headers: headers(responseHeaders), json: async () => body };
}

function session(revision, checkpoint = null) {
  return {
    public_id: 'session-1', status: 'active', resumable: true,
    checkpoint_revision: revision, checkpoint,
    checkpoint_exercise_ids: ['squat-v1'],
  };
}

function bootstrap(revision, checkpoint = null) {
  return {
    workout: { state: {
      contract_version: 2, session_state: 'active_resumable',
      session: session(revision, checkpoint),
    } },
    plan: { exists: true }, today_plan: { egzersizler: [] },
  };
}

const S1 = {
  current_exercise_index: 0, elapsed_seconds: 10,
  exercises: [{ exercise_id: 'squat-v1', sets: [
    { index: 0, completed: true, reps: 8, weight_kg: 80 },
  ] }],
};

const S2 = {
  current_exercise_index: 0, elapsed_seconds: 20,
  exercises: [{ exercise_id: 'squat-v1', sets: [
    { index: 0, completed: true, reps: 9, weight_kg: 82.5 },
  ] }],
};

function clientOptions(fetchImpl, extra = {}) {
  let key = 0;
  return Object.assign({
    fetchImpl,
    createIdempotencyKey: () => `checkpoint-key-${++key}`,
    documentRef: { hidden: false, addEventListener() {}, removeEventListener() {} },
    addEventListener() {}, removeEventListener() {},
  }, extra);
}

test('checkpoint sends the full contract and accepts only the server revision', async () => {
  const requests = [];
  const client = createWorkoutStateClient(clientOptions(async (url, init = {}) => {
    requests.push({ url, init });
    if (url === '/training/bootstrap') return response(bootstrap(3, S1));
    return response({ outcome: 'checkpointed', session: session(7, S2) });
  }));
  await client.refresh('load');

  const result = await client.flushCheckpoint(S2);

  assert.equal(result.ok, true);
  assert.equal(client.getCheckpointRevision(), 7);
  assert.equal(requests[1].url, '/workout/session/session-1/checkpoint');
  assert.equal(requests[1].init.method, 'POST');
  assert.equal(requests[1].init.headers['If-Match'], '3');
  assert.equal(requests[1].init.headers['Idempotency-Key'], 'checkpoint-key-1');
  assert.deepEqual(JSON.parse(requests[1].init.body), { checkpoint: S2 });
});

test('one in-flight command queues only the newest snapshot against the acknowledged revision', async () => {
  const first = deferred();
  const second = deferred();
  const checkpointRequests = [];
  const client = createWorkoutStateClient(clientOptions((url, init = {}) => {
    if (url === '/training/bootstrap') return Promise.resolve(response(bootstrap(0)));
    checkpointRequests.push({ url, init });
    return checkpointRequests.length === 1 ? first.promise : second.promise;
  }));
  await client.refresh('load');

  const savingFirst = client.flushCheckpoint(S1);
  const savingNewest = client.flushCheckpoint(S2);
  assert.equal(checkpointRequests.length, 1);
  first.resolve(response({ outcome: 'checkpointed', session: session(4, S1) }));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(checkpointRequests.length, 2);
  assert.equal(checkpointRequests[1].init.headers['If-Match'], '4');
  assert.equal(checkpointRequests[1].init.headers['Idempotency-Key'], 'checkpoint-key-2');
  assert.deepEqual(JSON.parse(checkpointRequests[1].init.body), { checkpoint: S2 });
  second.resolve(response({ outcome: 'checkpointed', session: session(9, S2) }));

  assert.equal((await savingFirst).ok, true);
  assert.equal((await savingNewest).ok, true);
  assert.equal(client.getCheckpointRevision(), 9);
  assert.equal(client.hasDirtyCheckpoint(), false);
});

test('an ambiguous retry keeps the exact command and key without inventing a revision', async () => {
  const requests = [];
  let checkpointAttempt = 0;
  const client = createWorkoutStateClient(clientOptions(async (url, init = {}) => {
    if (url === '/training/bootstrap') return response(bootstrap(2, S1));
    requests.push(init);
    checkpointAttempt++;
    if (checkpointAttempt === 1) throw new Error('connection_reset');
    return response({ outcome: 'checkpointed', session: session(6, S2) });
  }));
  await client.refresh('load');

  const failed = await client.flushCheckpoint(S2);
  assert.equal(failed.retryable, true);
  assert.equal(client.getCheckpointRevision(), 2);
  const retried = await client.retryCheckpoint();

  assert.equal(retried.ok, true);
  assert.equal(requests[0].headers['Idempotency-Key'], 'checkpoint-key-1');
  assert.equal(requests[1].headers['Idempotency-Key'], 'checkpoint-key-1');
  assert.equal(requests[1].headers['If-Match'], '2');
  assert.equal(requests[1].body, requests[0].body);
  assert.equal(client.getCheckpointRevision(), 6);
});

test('temporary 429 and 5xx refusals retain the same command for an explicit retry', async () => {
  for (const status of [429, 503]) {
    const requests = [];
    let attempt = 0;
    const client = createWorkoutStateClient(clientOptions(async (url, init = {}) => {
      if (url === '/training/bootstrap') return response(bootstrap(1, S1));
      requests.push(init);
      attempt++;
      if (attempt === 1) return response({ code: 'session_unavailable' }, false, status,
        { 'Session-Resolution': 'retry' });
      return response({ outcome: 'checkpointed', session: session(2, S2) });
    }));
    await client.refresh('load');

    assert.equal((await client.flushCheckpoint(S2)).retryable, true);
    assert.equal((await client.retryCheckpoint()).ok, true);
    assert.equal(requests[0].headers['Idempotency-Key'], requests[1].headers['Idempotency-Key']);
    client.destroy();
  }
});

test('revision conflict rereads once, replaces stale state, and never retries the rejected command', async () => {
  const calls = [];
  const applied = [];
  let bootstrapCall = 0;
  const client = createWorkoutStateClient(clientOptions(async (url) => {
    calls.push(url);
    if (url === '/training/bootstrap') {
      bootstrapCall++;
      return response(bootstrap(bootstrapCall === 1 ? 1 : 2, bootstrapCall === 1 ? S1 : S2));
    }
    return response({ code: 'revision_conflict' }, false, 409,
      { 'Session-Resolution': 'reread' });
  }, {
    onSnapshot: (value, reason, meta) => applied.push({ value, reason, meta }),
  }));
  await client.refresh('load');

  const result = await client.flushCheckpoint(S2);

  assert.equal(result.ok, false);
  assert.equal(result.code, 'revision_conflict');
  assert.deepEqual(calls, [
    '/training/bootstrap',
    '/workout/session/session-1/checkpoint',
    '/training/bootstrap',
  ]);
  assert.equal(client.getCheckpointRevision(), 2);
  assert.equal(client.hasDirtyCheckpoint(), false);
  assert.equal(applied[1].reason, 'checkpoint_reconcile');
  assert.equal(applied[1].meta.replaceDraft, true);
});

test('stale-session reread cannot create an automatic checkpoint loop', async () => {
  const calls = [];
  const client = createWorkoutStateClient(clientOptions(async (url) => {
    calls.push(url);
    if (url === '/training/bootstrap') return response(bootstrap(1, S1));
    return response({ code: 'stale_session_requires_resolution' }, false, 409,
      { 'Session-Resolution': 'reread' });
  }));
  await client.refresh('load');
  await client.flushCheckpoint(S2);
  await new Promise(resolve => setImmediate(resolve));

  assert.equal(calls.filter(url => url.includes('/checkpoint')).length, 1);
  assert.equal(calls.filter(url => url === '/training/bootstrap').length, 2);
});

test('ordinary refresh marks a dirty draft as preserved', async () => {
  const applied = [];
  const timers = [];
  const client = createWorkoutStateClient(clientOptions(
    async () => response(bootstrap(1, S1)),
    {
      onSnapshot: (value, reason, meta) => applied.push({ reason, meta }),
      scheduler: {
        setTimeout(fn, ms) { timers.push({ fn, ms }); return timers.length; },
        clearTimeout() {}, now: () => 0,
      },
    },
  ));
  await client.refresh('load');
  client.scheduleCheckpoint(S2);
  await client.refresh('focus');

  assert.equal(timers[0].ms, 800);
  assert.equal(applied[1].reason, 'focus');
  assert.equal(applied[1].meta.replaceDraft, false);
  assert.equal(client.hasDirtyCheckpoint(), true);
});

test('a bootstrap started before checkpoint ack cannot roll back the ack revision', async () => {
  const staleRefresh = deferred();
  const applied = [];
  let bootstrapCalls = 0;
  const client = createWorkoutStateClient(clientOptions(async url => {
    if (url === '/training/bootstrap') {
      bootstrapCalls++;
      if (bootstrapCalls === 1) return response(bootstrap(1, S1));
      return staleRefresh.promise;
    }
    return response({ outcome: 'checkpointed', session: session(2, S2) });
  }, { onSnapshot: value => applied.push(value) }));
  await client.refresh('load');

  const refreshing = client.refresh('focus');
  await client.flushCheckpoint(S2);
  staleRefresh.resolve(response(bootstrap(1, S1)));
  await refreshing;

  assert.equal(client.getCheckpointRevision(), 2);
  assert.equal(applied.length, 1);
});

test('contract v1 remains inert and never creates a session request', async () => {
  const calls = [];
  const client = createWorkoutStateClient(clientOptions(async url => {
    calls.push(url);
    return response({
      workout: { state: {
        contract_version: 1,
        session_state: 'active_resumable',
        session: session(0),
      } },
      plan: {},
    });
  }));
  await client.refresh('load');

  const result = await client.flushCheckpoint(S1);

  assert.equal(result.disabled, true);
  assert.deepEqual(calls, ['/training/bootstrap']);
  assert.equal(client.getCheckpointRevision(), null);
});

test('destroy cancels a scheduled save and prevents future checkpoint creation', async () => {
  let timer;
  let cleared = 0;
  const calls = [];
  const client = createWorkoutStateClient(clientOptions(async url => {
    calls.push(url);
    return response(bootstrap(0));
  }, {
    scheduler: {
      setTimeout(fn) { timer = fn; return 11; },
      clearTimeout(id) { assert.equal(id, 11); cleared++; },
      now: () => 0,
    },
  }));
  await client.refresh('load');
  client.scheduleCheckpoint(S1);
  client.destroy();
  await timer();

  assert.equal(cleared, 1);
  assert.deepEqual(calls, ['/training/bootstrap']);
});

test('destroy aborts an in-flight checkpoint and does not publish a late result', async () => {
  const pending = deferred();
  let checkpointSignal;
  const client = createWorkoutStateClient(clientOptions((url, init = {}) => {
    if (url === '/training/bootstrap') return Promise.resolve(response(bootstrap(0)));
    checkpointSignal = init.signal;
    return pending.promise;
  }));
  await client.refresh('load');

  const saving = client.flushCheckpoint(S1);
  client.destroy();
  assert.equal(checkpointSignal.aborted, true);
  pending.resolve(response({ outcome: 'checkpointed', session: session(1, S1) }));
  assert.equal(await saving, null);
});

test('abandon preparation cancels and permanently blocks local checkpoint work', async () => {
  const pending = deferred();
  let checkpointSignal;
  let checkpointCalls = 0;
  const client = createWorkoutStateClient(clientOptions((url, init = {}) => {
    if (url === '/training/bootstrap') return Promise.resolve(response(bootstrap(0)));
    checkpointCalls++;
    checkpointSignal = init.signal;
    return pending.promise;
  }));
  await client.refresh('load');

  const saving = client.flushCheckpoint(S1);
  client.stopCheckpointing();
  assert.equal(checkpointSignal.aborted, true);
  pending.reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
  assert.equal(await saving, null);
  assert.equal((await client.flushCheckpoint(S2)).disabled, true);
  assert.equal(checkpointCalls, 1);
});

test('a non-resumable or previous-day projection never enables checkpoint writes', async () => {
  for (const sessionState of ['active_previous_day', 'active_non_resumable']) {
    const calls = [];
    const client = createWorkoutStateClient(clientOptions(async url => {
      calls.push(url);
      const value = bootstrap(4, S1);
      value.workout.state.session_state = sessionState;
      value.workout.state.session.resumable = false;
      return response(value);
    }));
    await client.refresh('load');

    assert.equal((await client.flushCheckpoint(S2)).disabled, true);
    assert.deepEqual(calls, ['/training/bootstrap']);
    client.destroy();
  }
});

test('deterministic checkpoint refusals never retry or fabricate a revision', async () => {
  for (const [code, status, resolution] of [
    ['idempotency_conflict', 409, null],
    ['session_terminal', 409, 'terminal'],
    ['not_found', 404, null],
    ['invalid_checkpoint', 400, null],
  ]) {
    const calls = [];
    const client = createWorkoutStateClient(clientOptions(async url => {
      calls.push(url);
      if (url === '/training/bootstrap') return response(bootstrap(3, S1));
      return response({ code }, false, status,
        resolution ? { 'Session-Resolution': resolution } : {});
    }));
    await client.refresh('load');
    const result = await client.flushCheckpoint(S2);
    await new Promise(resolve => setImmediate(resolve));

    assert.equal(result.code, code);
    assert.equal(result.retryable, false);
    assert.equal(client.getCheckpointRevision(), 3);
    assert.equal(calls.filter(url => url.includes('/checkpoint')).length, 1);
    client.destroy();
  }
});

test('terminal checkpoint response stops saves and performs one canonical reread', async () => {
  const calls = [];
  const applied = [];
  let bootstrapCalls = 0;
  const client = createWorkoutStateClient(clientOptions(async url => {
    calls.push(url);
    if (url === '/training/bootstrap') {
      bootstrapCalls++;
      const value = bootstrap(bootstrapCalls === 1 ? 3 : 4, S1);
      if (bootstrapCalls === 2) {
        value.workout.state.session_state = 'completed';
        value.workout.state.session.status = 'completed';
        value.workout.state.session.resumable = false;
      }
      return response(value);
    }
    return response({ code: 'session_terminal' }, false, 409,
      { 'Session-Resolution': 'terminal' });
  }, { onSnapshot: value => applied.push(value) }));
  await client.refresh('load');

  const result = await client.flushCheckpoint(S2);

  assert.equal(result.code, 'session_terminal');
  assert.deepEqual(calls, [
    '/training/bootstrap',
    '/workout/session/session-1/checkpoint',
    '/training/bootstrap',
  ]);
  assert.equal(applied.at(-1).workout.state.session.status, 'completed');
  assert.equal((await client.flushCheckpoint(S2)).disabled, true);
});

test('retryable background failure is surfaced once without changing its command', async () => {
  const failures = [];
  const client = createWorkoutStateClient(clientOptions(async url => {
    if (url === '/training/bootstrap') return response(bootstrap(1, S1));
    return response({ code: 'session_unavailable' }, false, 503,
      { 'Session-Resolution': 'retry' });
  }, { onCheckpointRetryable: result => failures.push(result) }));
  await client.refresh('load');

  const failed = await client.flushCheckpoint(S2);

  assert.equal(failures.length, 1);
  assert.equal(failures[0], failed);
  assert.equal(client.hasDirtyCheckpoint(), true);
});

test('a thrown network failure is surfaced once and preserves its retry command', async () => {
  const failures = [];
  const keys = [];
  let attempts = 0;
  const client = createWorkoutStateClient(clientOptions(async (url, options) => {
    if (url === '/training/bootstrap') return response(bootstrap(0));
    keys.push(options.headers['Idempotency-Key']);
    attempts++;
    if (attempts === 1) throw new Error('connection reset');
    return response({ session: session(1, S1) });
  }, { onCheckpointRetryable: result => failures.push(result) }));
  await client.refresh('load');

  const refused = await client.flushCheckpoint(S1);
  const retried = await client.retryCheckpoint();

  assert.equal(refused.retryable, true);
  assert.equal(retried.ok, true);
  assert.equal(failures.length, 1);
  assert.equal(failures[0].code, 'network_error');
  assert.deepEqual(keys, [keys[0], keys[0]]);
});

test('successful checkpoint publishes its canonical session projection', async () => {
  const acknowledged = [];
  const client = createWorkoutStateClient(clientOptions(async url => {
    if (url === '/training/bootstrap') return response(bootstrap(1, S1));
    return response({ outcome: 'checkpointed', session: session(2, S2) });
  }, { onCheckpointAcknowledged: value => acknowledged.push(value) }));
  await client.refresh('load');

  await client.flushCheckpoint(S2);

  assert.deepEqual(acknowledged, [session(2, S2)]);
});

test('a key already assigned to a retired command is skipped for every new command', async () => {
  const generated = ['same-key-0001', 'same-key-0001', 'fresh-key-0002'];
  const keys = [];
  let revision = 0;
  const client = createWorkoutStateClient(clientOptions(async (url, init = {}) => {
    if (url === '/training/bootstrap') return response(bootstrap(0));
    keys.push(init.headers['Idempotency-Key']);
    revision++;
    return response({ outcome: 'checkpointed', session: session(revision, revision === 1 ? S1 : S2) });
  }, { createIdempotencyKey: () => generated.shift() }));
  await client.refresh('load');

  await client.flushCheckpoint(S1);
  await client.flushCheckpoint(S2);

  assert.deepEqual(keys, ['same-key-0001', 'fresh-key-0002']);
});

test('an active clean session schedules no periodic or empty checkpoint', async () => {
  let intervals = 0;
  const calls = [];
  const client = createWorkoutStateClient(clientOptions(async url => {
    calls.push(url);
    return response(bootstrap(0));
  }, {
    scheduler: {
      setInterval() { intervals++; }, clearInterval() {},
      setTimeout() { throw new Error('clean session must not schedule a save'); },
      clearTimeout() {}, now: () => 0,
    },
  }));

  await client.refresh('load');

  assert.equal(intervals, 0);
  assert.deepEqual(calls, ['/training/bootstrap']);
});

test('an acknowledged checkpoint hydrates a separate page instance after reload', async () => {
  const workoutDay = {
    gun: 'Pazartesi', tip: 'agirlik', odak: 'Guc',
    egzersizler: [{ isim: 'Display only', set: 1, tekrar: '8', dinlenme: '60 sn' }],
  };
  let persistedRevision = 0;
  let persistedCheckpoint = null;
  const transport = async (url, init = {}) => {
    if (url === '/training/bootstrap') {
      return response(bootstrap(persistedRevision, persistedCheckpoint));
    }
    const request = JSON.parse(init.body);
    persistedRevision = 5;
    persistedCheckpoint = structuredClone(request.checkpoint);
    return response({
      outcome: 'checkpointed',
      session: session(persistedRevision, persistedCheckpoint),
    });
  };
  let firstProjection;
  const firstClient = createWorkoutStateClient(clientOptions(transport, {
    onSnapshot: value => { firstProjection = value.workout.state.session; },
  }));
  await firstClient.refresh('load');
  const firstDraft = createWorkoutDraft(workoutDay, firstProjection, 1000);
  firstDraft.exercises[0].sets[0].weightKg = 100;
  firstDraft.exercises[0].sets[0].reps = 6;
  firstDraft.exercises[0].sets[0].done = true;
  firstDraft.currentExerciseIndex = 0;
  assert.equal((await flushWorkoutDraft(firstClient, firstDraft, 61000)).ok, true);
  firstClient.destroy();

  let reloadedProjection;
  const secondClient = createWorkoutStateClient(clientOptions(transport, {
    onSnapshot: value => { reloadedProjection = value.workout.state.session; },
  }));
  await secondClient.refresh('load');
  const reloadedDraft = createWorkoutDraft(workoutDay, reloadedProjection, 100000);

  assert.notEqual(reloadedDraft, firstDraft);
  assert.notEqual(reloadedDraft.exercises[0], firstDraft.exercises[0]);
  assert.equal(reloadedDraft.checkpointRevision, 5);
  assert.equal(reloadedDraft.elapsedBaselineSeconds, 60);
  assert.deepEqual(reloadedDraft.exercises[0].sets[0], {
    index: 0, weightKg: 100, reps: 6, done: true, isPR: false,
  });
});

test('completion preparation waits for the final checkpoint and returns its acknowledged revision', async () => {
  const checkpoint = deferred();
  const calls = [];
  const client = createWorkoutStateClient(clientOptions(async (url, init = {}) => {
    calls.push({ url, init });
    if (url === '/training/bootstrap') return response(bootstrap(1, S1));
    return checkpoint.promise;
  }));
  await client.refresh('load');
  const draft = {
    currentExerciseIndex: 0,
    elapsedBaselineSeconds: 20,
    elapsedStartedAtMs: 0,
    exercises: [{ exerciseId: 'squat-v1', sets: [
      { index: 0, done: true, reps: 9, weightKg: 82.5 },
    ] }],
  };

  let settled = false;
  const preparing = flushWorkoutDraft(client, draft, 0).then(value => {
    settled = true;
    return value;
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(settled, false);
  checkpoint.resolve(response({ outcome: 'checkpointed', session: session(8, S2) }));
  const prepared = await preparing;

  assert.equal(prepared.ok, true);
  assert.equal(prepared.checkpointRevision, 8);
  assert.equal(calls[1].init.headers['If-Match'], '1');
  assert.deepEqual(JSON.parse(calls[1].init.body), {
    checkpoint: buildCheckpointSnapshot(draft, 0),
  });
});

test('completion preparation fails closed when the final checkpoint is not acknowledged', async () => {
  const client = createWorkoutStateClient(clientOptions(async url => {
    if (url === '/training/bootstrap') return response(bootstrap(5, S1));
    return response({ code: 'session_unavailable' }, false, 503,
      { 'Session-Resolution': 'retry' });
  }));
  await client.refresh('load');
  const draft = {
    currentExerciseIndex: 0,
    elapsedBaselineSeconds: 20,
    elapsedStartedAtMs: 0,
    exercises: [{ exerciseId: 'squat-v1', sets: [
      { index: 0, done: true, reps: 9, weightKg: 82.5 },
    ] }],
  };

  const prepared = await flushWorkoutDraft(client, draft, 0);

  assert.equal(prepared.ok, false);
  assert.equal(prepared.retryable, true);
  assert.equal(prepared.checkpointRevision, undefined);
  assert.equal(client.getCheckpointRevision(), 5);
});
