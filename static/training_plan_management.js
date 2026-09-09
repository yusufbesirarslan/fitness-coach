/* AxisAI UX-3 PR3 — the ONE browser Training-management contract.

 * Legacy `/training` and the Plan v2 shell are two RENDERERS of the same
 * workflow until PR6 retires one of them. Before this module they were also two
 * implementations of it: `static/training.js` owned generate → preview → save,
 * and `static/plan_create.js` owned a second, subtly different copy. This module
 * is the presentation-independent half both now call — exactly the boundary
 * `workout_execution.js` established for execution in PR2.
 *
 * It is NOT a second training-plan authority. It renders nothing, stores
 * nothing, builds no plan structure, invents no default the server does not own,
 * and holds no version of its own. It owns five things and nothing else:
 *
 *   1. the canonical request SEQUENCE (POST /training-plan → POST
 *      /training-plan/save), with the server-signed exercise context forwarded
 *      verbatim from the first response into the second;
 *   2. the bounded state machine that keeps a PROPOSAL distinguishable from a
 *      PERSISTED ACTIVE PLAN at every instant;
 *   3. proposal_origin — the canonical TrainingPlan identity from which THIS
 *      proposal was created, frozen for the life of the proposal;
 *   4. a canonical FRESHNESS PRECHECK before any destructive replacement; and
 *   5. the rule that a successful write is not reported as done until a
 *      canonical refresh has actually run.
 *
 * ── On expected_plan (PR #293 is the replacement-concurrency authority) ──────
 * POST /training-plan/save requires expected_plan. null means "I expect no
 * active plan". {lineage_id, mutation_version} names the exact active plan the
 * caller intends to replace. The server compares that pair UNDER the same lock
 * that protects replacement. This module does not mint, edit, or increment
 * those fields. It only remembers a canonical server reading as proposal_origin
 * and sends THAT reading as expected_plan.
 *
 * CREATE        → expected_plan: null
 * REGENERATE    → expected_plan: proposal_origin
 * UNKNOWN       → no destructive POST (never coerced to null)
 *
 * A later fresh read may detect staleness early. It must NEVER upgrade the
 * proposal's replacement authority. A typed 409 TRAINING_PLAN_SAVE_PLAN_CHANGED
 * is an expected outcome: no replacement, no automatic retry, no rewriting of
 * expected_plan. The user must regenerate intentionally against current state.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.FitXPlanManagement = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var GENERATE_URL = '/training-plan';
  var SAVE_URL = '/training-plan/save';
  var CANONICAL_READ_URL = '/training/bootstrap';

  var IDLE = 'idle';
  var GENERATING = 'generating';
  var PROPOSAL_READY = 'proposal_ready';
  var SAVING = 'saving';
  var REFRESHING = 'refreshing';
  var REPLACED = 'replaced';
  var ERROR = 'error';
  var STALE_PLAN = 'stale_plan';

  var CODE_NO_SESSION = 'TRAINING_PLAN_NO_SESSION';
  var CODE_PLAN_CHANGED = 'TRAINING_PLAN_SAVE_PLAN_CHANGED';

  /* A baseline is the server's statement about the plan the surface was
   * rendered against: whether one existed and, if so, its canonical identity.
   * Normalised to a total shape so every comparison below is decidable. */
  function baselineFrom(source) {
    if (!source || typeof source !== 'object') {
      return { present: false, lineage: null, version: null };
    }
    var present = source.present !== undefined
      ? Boolean(source.present)
      : Boolean(source.exists);
    // Idempotent: accepts the PR #293 browser projection (`lineage_id`),
    // the Plan-presenter bootstrap (`plan_lineage`), the native name, or an
    // already-normalised baseline. Normalising a normalised value must not
    // erase the identity — that would silently turn every comparison into
    // "undecidable" and make the freshness check unfalsifiable.
    var rawLineage = source.lineage_id !== undefined
      ? source.lineage_id
      : (source.plan_lineage !== undefined
          ? source.plan_lineage
          : source.lineage);
    var rawVersion = source.mutation_version !== undefined
      ? source.mutation_version : source.version;
    var lineage = typeof rawLineage === 'string' && rawLineage ? rawLineage : null;
    var version = Number.isInteger(rawVersion) ? rawVersion : null;
    return { present: present, lineage: lineage, version: version };
  }

  /* Compare two SERVER readings of the plan identity.
   *
   * Returns 'match' | 'changed' | 'undecidable'. `undecidable` is never
   * collapsed into `match`: an identity we cannot read is not an identity we
   * verified, and a whole-plan replacement is not a write to guess at. */
  function compareBaselines(rendered, current) {
    var a = baselineFrom(rendered);
    var b = baselineFrom(current);
    if (a.present !== b.present) return 'changed';
    if (!a.present) return 'match';
    if (a.lineage === null || a.version === null ||
        b.lineage === null || b.version === null) return 'undecidable';
    if (a.lineage !== b.lineage) return 'changed';
    return a.version === b.version ? 'match' : 'changed';
  }

  /* Translate a frozen origin into the exact expected_plan the save route
   * accepts. `unknown` is a first-class result: it is not null and it is not
   * a pair, and the caller must not POST. */
  function expectedPlanFromOrigin(origin) {
    var b = baselineFrom(origin);
    if (!b.present) return { kind: 'create', value: null };
    if (b.lineage === null || b.version === null) {
      return { kind: 'unknown', value: undefined };
    }
    return {
      kind: 'replace',
      value: { lineage_id: b.lineage, mutation_version: b.version },
    };
  }

  function jsonBody(response) {
    return response.json().then(null, function () { return {}; });
  }

  function createPlanManagement(options) {
    var settings = options || {};
    var fetchImpl = settings.fetchImpl;
    // The destructive write may need a renderer-specific transport (legacy
    // Training routes it through the workout-state client so the mutation and
    // its bootstrap refresh stay ordered). Same signature either way.
    var persistImpl = settings.persist || null;
    // Canonical re-read AFTER a successful write. Required: this module refuses
    // to report a replacement as done on the strength of a 200 alone.
    var refreshImpl = settings.refresh;

    var state = IDLE;
    var proposal = null;
    var lastError = null;
    var baseline = baselineFrom(settings.baseline);
    var inFlight = false;

    function setState(next) {
      state = next;
      if (typeof settings.onStateChange === 'function') settings.onStateChange(next);
    }

    function fail(code, message, nextState) {
      lastError = { code: code || null, message: message || null };
      setState(nextState || ERROR);
      return { ok: false, code: lastError.code, message: lastError.message,
               state: state };
    }

    async function send(url, init) {
      var response = await fetchImpl(url, init);
      var body = await jsonBody(response);
      return { ok: response.ok, status: response.status, body: body };
    }

    /* Read the canonical plan identity as the server sees it RIGHT NOW.
     * Returns a baseline, or null when the canonical read itself failed —
     * which the caller must treat as "cannot prove freshness", never as "no
     * plan" (that confusion is how a populated plan gets silently destroyed). */
    async function readCanonicalBaseline() {
      try {
        var result = await send(CANONICAL_READ_URL, { method: 'GET' });
        if (!result.ok) return null;
        var plan = result.body && result.body.plan;
        if (!plan || typeof plan !== 'object') return null;
        return baselineFrom(plan);
      } catch (error) {
        return null;
      }
    }

    /* Ask the canonical generator for a PROPOSAL. Persists nothing: no branch
     * of this function reaches SAVE_URL, and the state it leaves behind
     * (`proposal_ready`) is not an active plan.
     *
     * proposal_origin is captured HERE from the last canonical server reading
     * this manager holds, then frozen on the proposal. A later adoptBaseline
     * (Coach mutation, second tab, mutate() bootstrap refresh) restates the
     * PAGE's view of current identity; it must not upgrade this proposal's
     * replacement authority. */
    async function generate(selections) {
      if (inFlight) return fail('busy', null, state);
      inFlight = true;
      lastError = null;
      proposal = null;
      setState(GENERATING);
      var origin = baselineFrom(baseline);
      try {
        var result = await send(GENERATE_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(selections),
        });
        var body = result.body || {};
        if (result.status === 400 && (!body.code || body.code === CODE_NO_SESSION)) {
          return fail(CODE_NO_SESSION, body.error || null);
        }
        if (!result.ok || body.error) {
          return fail(body.code || 'generation_failed', body.error || null);
        }
        if (!Array.isArray(body.program) || !body.program.length) {
          // A malformed generator response is a failed generation, never a
          // saveable proposal — the current plan must survive it untouched.
          return fail('generation_unusable', null);
        }
        // The signed equipment context is forwarded from generate into save
        // VERBATIM: never parsed, edited, displayed, or stored anywhere else.
        proposal = {
          program: body.program,
          score: body.overall_score,
          exercise_context_token: body.exercise_context_token,
          payload: body,
          origin: origin,
        };
        setState(PROPOSAL_READY);
        return { ok: true, proposal: proposal, state: state };
      } catch (error) {
        return fail('generation_unavailable', null);
      } finally {
        inFlight = false;
      }
    }

    /* Persist the held proposal as the user's active plan.
     *
     * Order is the guarantee: prove origin is sendable → optional freshness
     * precheck against THAT origin → write with expected_plan = origin →
     * canonical refresh. A refusal or a failure at any step leaves the CURRENT
     * plan untouched, and the proposal is still only a proposal. */
    async function replace() {
      if (inFlight) return fail('busy', null, state);
      if (state !== PROPOSAL_READY || !proposal) {
        return fail('no_proposal', null, state);
      }
      inFlight = true;
      lastError = null;
      try {
        var origin = proposal.origin;
        var expectation = expectedPlanFromOrigin(origin);
        if (expectation.kind === 'unknown') {
          // The client can prove it has no valid expectation. Do not rely on
          // the server 400 as ordinary UI control flow for that case.
          return fail('unknown_origin', null);
        }

        var current = await readCanonicalBaseline();
        if (current === null) {
          // Freshness is unknown. Unknown is not permission to replace.
          return fail('freshness_unavailable', null);
        }
        var verdict = compareBaselines(origin, current);
        if (verdict === 'undecidable') return fail('freshness_unavailable', null);
        if (verdict === 'changed') {
          // Someone else moved the plan between origin capture and confirmation.
          // Refuse locally. expected_plan is NOT rewritten to `current`.
          return fail('plan_changed', null, STALE_PLAN);
        }

        setState(SAVING);
        var payload = savePayload(expectation.value);
        var write = persistImpl
          ? await persistImpl(SAVE_URL, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload),
            })
          : await send(SAVE_URL, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload),
            });
        if (write && write.status === 409) {
          // Typed stale conflict. Canonical refresh for UI truth; the proposal
          // stays a proposal; origin stays the origin. No automatic retry.
          try { await refreshImpl('plan_changed'); } catch (error) { /* refresh is best-effort here */ }
          return fail('plan_changed', null, STALE_PLAN);
        }
        if (!write || !write.ok || (write.body && write.body.error)) {
          var detail = write && write.body ? write.body : {};
          // The proposal is retained so the user can retry, and it is STILL a
          // proposal — the caller must not present it as the active plan.
          return fail(detail.code || 'save_failed', detail.error || null);
        }

        setState(REFRESHING);
        var refreshed = await refreshImpl('plan_replaced');
        if (!refreshed) {
          // The server accepted the write; we simply cannot show that it did.
          // Saying "synchronized" here would be a lie, so we say the truth and
          // let the renderer offer a reload.
          return fail('refresh_failed', null);
        }
        proposal = null;
        setState(REPLACED);
        return { ok: true, state: state };
      } catch (error) {
        return fail('save_unavailable', null);
      } finally {
        inFlight = false;
      }
    }

    function savePayload(expectedPlan) {
      return {
        plan: proposal.program,
        score: proposal.score,
        exercise_context_token: proposal.exercise_context_token,
        expected_plan: expectedPlan,
      };
    }

    function discardProposal() {
      // Cancelling a regeneration must change NOTHING about the current plan.
      proposal = null;
      lastError = null;
      setState(IDLE);
    }

    return {
      getState: function () { return state; },
      getError: function () { return lastError; },
      getProposal: function () { return proposal; },
      getBaseline: function () { return baselineFrom(baseline); },
      getProposalOrigin: function () {
        return proposal && proposal.origin ? baselineFrom(proposal.origin) : null;
      },
      /* The baseline may only be re-stated from a SERVER reading (a canonical
       * snapshot), never computed by a renderer. A pending proposal's origin
       * is NOT updated: that would turn a later fresh read into replacement
       * authority for a stale proposal. */
      adoptBaseline: function (source) { baseline = baselineFrom(source); },
      generate: generate,
      replace: replace,
      discardProposal: discardProposal,
    };
  }

  return {
    createPlanManagement: createPlanManagement,
    baselineFrom: baselineFrom,
    compareBaselines: compareBaselines,
    expectedPlanFromOrigin: expectedPlanFromOrigin,
    GENERATE_URL: GENERATE_URL,
    SAVE_URL: SAVE_URL,
    CANONICAL_READ_URL: CANONICAL_READ_URL,
    CODE_NO_SESSION: CODE_NO_SESSION,
    CODE_PLAN_CHANGED: CODE_PLAN_CHANGED,
    STATES: {
      IDLE: IDLE,
      GENERATING: GENERATING,
      PROPOSAL_READY: PROPOSAL_READY,
      SAVING: SAVING,
      REFRESHING: REFRESHING,
      REPLACED: REPLACED,
      ERROR: ERROR,
      STALE_PLAN: STALE_PLAN,
    },
  };
}));
