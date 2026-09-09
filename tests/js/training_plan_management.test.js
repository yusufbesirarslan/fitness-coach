/* UX-3 PR3 — the shared Training-management contract.
 *
 * These are behaviour tests, not string checks: each one would pass on a
 * version of the module that had lost the invariant only if the module actually
 * stopped issuing (or started issuing) a real request.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const management = require('../../static/training_plan_management.js');

const PROGRAM = [
  { gun: 'Pazartesi', tip: 'guc', odak: 'Push', egzersizler: [{ isim: 'Bench' }] },
  { gun: 'Salı', tip: 'dinlenme', egzersizler: [] },
];

function recorder(routes) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, method: (init && init.method) || 'GET', body: init && init.body });
    const handler = routes[url];
    if (!handler) throw new Error('unrouted ' + url);
    const reply = typeof handler === 'function' ? handler(calls.length) : handler;
    return {
      ok: reply.ok !== false,
      status: reply.status || (reply.ok === false ? 500 : 200),
      json: async () => reply.body || {},
    };
  };
  return { calls, fetchImpl };
}

function bootstrapPlan(lineage, version) {
  return { body: { plan: { exists: true, plan_lineage: lineage, mutation_version: version } } };
}

function make(routes, baseline, extra) {
  const rec = recorder(routes);
  const manager = management.createPlanManagement(Object.assign({
    fetchImpl: rec.fetchImpl,
    baseline: baseline,
    refresh: async () => true,
  }, extra || {}));
  return { manager, calls: rec.calls };
}

const GENERATED = {
  body: {
    program: PROGRAM, overall_score: 8.2, exercise_context_token: 'signed.token',
  },
};

test('generating a plan persists nothing and never reports an active plan', async () => {
  const { manager, calls } = make({ '/training-plan': GENERATED },
    { present: false });
  const result = await manager.generate({ gun_sayisi: 3 });

  assert.equal(result.ok, true);
  assert.equal(manager.getState(), management.STATES.PROPOSAL_READY);
  // The load-bearing assertion: no write request of any kind was issued.
  assert.deepEqual(calls.map(c => c.url), ['/training-plan']);
  assert.equal(calls.some(c => c.url === management.SAVE_URL), false);
});

test('a malformed generator response is a failure, not a saveable proposal', async () => {
  const { manager, calls } = make({ '/training-plan': { body: { program: [] } } },
    { present: false });
  const result = await manager.generate({});

  assert.equal(result.ok, false);
  assert.equal(result.code, 'generation_unusable');
  assert.equal(manager.getProposal(), null);
  assert.deepEqual(calls.map(c => c.url), ['/training-plan']);
});

test('the no-session 400 is classified, and a typed 400 is not', async () => {
  const untyped = make({ '/training-plan': { ok: false, status: 400, body: {} } },
    { present: false });
  assert.equal((await untyped.manager.generate({})).code, management.CODE_NO_SESSION);

  const typed = make({
    '/training-plan': { ok: false, status: 400, body: { code: 'TRAINING_PREFERENCES_UNSUPPORTED', error: 'nope' } },
  }, { present: false });
  const result = await typed.manager.generate({});
  assert.equal(result.code, 'TRAINING_PREFERENCES_UNSUPPORTED');
  assert.equal(result.message, 'nope');
});

test('replacement forwards the signed context verbatim and then refreshes', async () => {
  let refreshed = 0;
  const { manager, calls } = make({
    '/training-plan': GENERATED,
    '/training/bootstrap': bootstrapPlan('lineage-a', 4),
    '/training-plan/save': { body: { message: 'saved' } },
  }, { present: true, plan_lineage: 'lineage-a', mutation_version: 4 },
  { refresh: async () => { refreshed += 1; return true; } });

  await manager.generate({});
  const result = await manager.replace();

  assert.equal(result.ok, true);
  assert.equal(manager.getState(), management.STATES.REPLACED);
  assert.equal(refreshed, 1);
  // Freshness is proven BEFORE the destructive write, never after it.
  assert.deepEqual(calls.map(c => c.url),
    ['/training-plan', '/training/bootstrap', '/training-plan/save']);
  const saved = JSON.parse(calls[2].body);
  assert.equal(saved.exercise_context_token, 'signed.token');
  assert.equal(saved.score, 8.2);
  assert.deepEqual(saved.plan, PROGRAM);
});

test('a plan that moved under the page is REFUSED without any write', async () => {
  const { manager, calls } = make({
    '/training-plan': GENERATED,
    // The page rendered against version 4; the server is now at 5.
    '/training/bootstrap': bootstrapPlan('lineage-a', 5),
    '/training-plan/save': () => { throw new Error('save must not be reached'); },
  }, { present: true, plan_lineage: 'lineage-a', mutation_version: 4 });

  await manager.generate({});
  const result = await manager.replace();

  assert.equal(result.ok, false);
  assert.equal(result.code, 'plan_changed');
  assert.equal(manager.getState(), management.STATES.STALE_PLAN);
  assert.equal(calls.some(c => c.url === management.SAVE_URL), false);
});

test('a replaced lineage at the same version is also refused', async () => {
  const { manager, calls } = make({
    '/training-plan': GENERATED,
    '/training/bootstrap': bootstrapPlan('lineage-b', 0),
    '/training-plan/save': () => { throw new Error('save must not be reached'); },
  }, { present: true, plan_lineage: 'lineage-a', mutation_version: 0 });

  await manager.generate({});
  assert.equal((await manager.replace()).code, 'plan_changed');
  assert.equal(calls.some(c => c.url === management.SAVE_URL), false);
});

test('creation is refused when a plan appeared while the page was open', async () => {
  const { manager, calls } = make({
    '/training-plan': GENERATED,
    '/training/bootstrap': bootstrapPlan('lineage-new', 0),
    '/training-plan/save': () => { throw new Error('save must not be reached'); },
  }, { present: false });

  await manager.generate({});
  assert.equal((await manager.replace()).code, 'plan_changed');
  assert.equal(calls.some(c => c.url === management.SAVE_URL), false);
});

test('an unreadable canonical state blocks the write instead of guessing', async () => {
  const { manager, calls } = make({
    '/training-plan': GENERATED,
    '/training/bootstrap': { ok: false, status: 500, body: { code: 'bootstrap_unavailable' } },
    '/training-plan/save': () => { throw new Error('save must not be reached'); },
  }, { present: true, plan_lineage: 'lineage-a', mutation_version: 1 });

  await manager.generate({});
  const result = await manager.replace();

  assert.equal(result.code, 'freshness_unavailable');
  // An unavailable read is NOT "no plan": nothing may be destroyed on it.
  assert.equal(calls.some(c => c.url === management.SAVE_URL), false);
});

test('a failed save leaves the proposal a proposal and claims nothing', async () => {
  const { manager } = make({
    '/training-plan': GENERATED,
    '/training/bootstrap': bootstrapPlan('lineage-a', 2),
    '/training-plan/save': { ok: false, status: 422, body: { error: 'invalid', code: 'PLAN_INVALID' } },
  }, { present: true, plan_lineage: 'lineage-a', mutation_version: 2 });

  await manager.generate({});
  const result = await manager.replace();

  assert.equal(result.ok, false);
  assert.equal(result.code, 'PLAN_INVALID');
  assert.equal(manager.getState(), management.STATES.ERROR);
  assert.notEqual(manager.getProposal(), null);   // still only a proposal
});

test('a write that cannot be confirmed is reported as unconfirmed, not as done', async () => {
  const { manager } = make({
    '/training-plan': GENERATED,
    '/training/bootstrap': bootstrapPlan('lineage-a', 0),
    '/training-plan/save': { body: {} },
  }, { present: true, plan_lineage: 'lineage-a', mutation_version: 0 },
  { refresh: async () => false });

  await manager.generate({});
  const result = await manager.replace();

  assert.equal(result.ok, false);
  assert.equal(result.code, 'refresh_failed');
  assert.notEqual(manager.getState(), management.STATES.REPLACED);
});

test('cancelling discards the proposal and touches nothing', async () => {
  const { manager, calls } = make({ '/training-plan': GENERATED }, { present: true, plan_lineage: 'l', mutation_version: 0 });
  await manager.generate({});
  manager.discardProposal();

  assert.equal(manager.getProposal(), null);
  assert.equal(manager.getState(), management.STATES.IDLE);
  assert.equal((await manager.replace()).code, 'no_proposal');
  assert.deepEqual(calls.map(c => c.url), ['/training-plan']);
});

test('adopting a canonical snapshot is what makes a Coach mutation visible', async () => {
  const { manager } = make({}, { present: true, plan_lineage: 'l', mutation_version: 1 });
  assert.deepEqual(manager.getBaseline(), { present: true, lineage: 'l', version: 1 });
  // A later canonical refresh (focus/visibility/mutation) restates the identity.
  manager.adoptBaseline({ exists: true, plan_lineage: 'l', mutation_version: 2 });
  assert.deepEqual(manager.getBaseline(), { present: true, lineage: 'l', version: 2 });
});

test('an unreadable identity on either side is undecidable, never a match', () => {
  const known = { present: true, plan_lineage: 'l', mutation_version: 1 };
  assert.equal(management.compareBaselines(known, known), 'match');
  assert.equal(management.compareBaselines(known, { present: true }), 'undecidable');
  assert.equal(management.compareBaselines({ present: false }, { present: false }), 'match');
  assert.equal(management.compareBaselines({ present: false }, known), 'changed');
  assert.equal(management.compareBaselines(known, { present: false }), 'changed');
});
