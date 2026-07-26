(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.FitXWorkoutStateClient = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var HEARTBEAT_MS = 60000;
  var FOCUS_REFRESH_MIN_MS = 5000;
  var NON_TERMINAL = new Set([
    'created', 'existing_active', 'resumed', 'checkpointed'
  ]);
  var documentOwners = new WeakMap();

  function createWorkoutStateClient(options) {
    var fetchImpl = options.fetchImpl;
    var onSnapshot = options.onSnapshot || function () {};
    var onBlocked = options.onBlocked || function () {};
    var scheduler = options.scheduler || {
      setInterval: setInterval, clearInterval: clearInterval, now: Date.now
    };
    var documentRef = options.documentRef || document;
    var addWindowListener = options.addEventListener || function (name, fn) {
      window.addEventListener(name, fn);
    };
    var removeWindowListener = options.removeEventListener || function (name, fn) {
      window.removeEventListener(name, fn);
    };
    var generation = 0;
    var mutationEpoch = 0;
    var mutationGeneration = 0;
    var mutationInFlight = null;
    var mutationController = null;
    var controller = null;
    var heartbeatTimer = null;
    var heartbeatInFlight = false;
    var heartbeatController = null;
    var heartbeatGeneration = 0;
    var sessionId = null;
    var destroyed = false;
    var lastRefreshAt = -Infinity;
    var api = null;

    function abortRefresh() {
      if (controller) controller.abort();
      controller = null;
    }

    async function refresh(reason) {
      if (destroyed) return null;
      var ownGeneration = ++generation;
      var ownMutationEpoch = mutationEpoch;
      abortRefresh();
      controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
      try {
        var response = await fetchImpl('/training/bootstrap', controller ? {
          signal: controller.signal
        } : {});
        if (!response.ok) throw new Error('bootstrap_unavailable');
        var snapshot = await response.json();
        if (destroyed || ownGeneration !== generation ||
            ownMutationEpoch !== mutationEpoch) return null;
        lastRefreshAt = scheduler.now ? scheduler.now() : Date.now();
        onSnapshot(snapshot, reason);
        syncHeartbeat(snapshot);
        return snapshot;
      } catch (error) {
        if (error && error.name === 'AbortError') return null;
        if (ownGeneration === generation && ownMutationEpoch === mutationEpoch) {
          stopHeartbeat();
          onBlocked('bootstrap_unavailable');
        }
        return null;
      }
    }

    function beginMutation() {
      mutationEpoch++;
      mutationGeneration++;
      generation++;
      abortRefresh();
    }

    function mutate(url, init) {
      if (destroyed) return Promise.resolve(null);
      if (mutationInFlight) return mutationInFlight;
      beginMutation();
      var ownMutationGeneration = mutationGeneration;
      mutationController = typeof AbortController !== 'undefined'
        ? new AbortController() : null;
      var ownController = mutationController;
      var requestInit = Object.assign({}, init || { method: 'POST' });
      if (ownController) requestInit.signal = ownController.signal;
      mutationInFlight = (async function () {
        var response;
        var body = null;
        try {
          response = await fetchImpl(url, requestInit);
          body = await response.json().catch(function () { return {}; });
        } finally {
          try {
            await refresh('mutation');
          } finally {
            if (mutationController === ownController) mutationController = null;
            mutationInFlight = null;
          }
        }
        if (destroyed || ownMutationGeneration !== mutationGeneration) return null;
        return { ok: response.ok, status: response.status, body: body };
      }());
      return mutationInFlight;
    }

    function syncHeartbeat(snapshot) {
      var state = snapshot && snapshot.workout && snapshot.workout.state;
      var session = state && state.session;
      if (!state || state.contract_version !== 2 ||
          state.session_state !== 'active_resumable' || !session ||
          session.status !== 'active' || documentRef.hidden) {
        stopHeartbeat();
        return;
      }
      startHeartbeat(session.public_id);
    }

    function startHeartbeat(publicId) {
      if (!publicId || documentRef.hidden || destroyed) return;
      if (heartbeatTimer && sessionId === publicId) return;
      stopHeartbeat();
      sessionId = publicId;
      heartbeatTimer = scheduler.setInterval(checkpoint, HEARTBEAT_MS);
    }

    function stopHeartbeat() {
      heartbeatGeneration++;
      if (heartbeatController) heartbeatController.abort();
      heartbeatController = null;
      if (heartbeatTimer !== null) scheduler.clearInterval(heartbeatTimer);
      heartbeatTimer = null;
      heartbeatInFlight = false;
      sessionId = null;
    }

    async function checkpoint() {
      if (heartbeatInFlight || !sessionId || documentRef.hidden || destroyed) return null;
      var ownGeneration = heartbeatGeneration;
      heartbeatController = typeof AbortController !== 'undefined'
        ? new AbortController() : null;
      var ownController = heartbeatController;
      heartbeatInFlight = true;
      try {
        var response = await fetchImpl(
          '/workout/session/' + encodeURIComponent(sessionId) + '/checkpoint',
          Object.assign(
            { method: 'POST' },
            ownController ? { signal: ownController.signal } : {}
          )
        );
        var body = await response.json().catch(function () { return {}; });
        if (destroyed || ownGeneration !== heartbeatGeneration) return null;
        if (!response.ok || !NON_TERMINAL.has(body.outcome)) {
          stopHeartbeat();
          return refresh('heartbeat_terminal');
        }
        return body;
      } catch (error) {
        if (destroyed || ownGeneration !== heartbeatGeneration ||
            (error && error.name === 'AbortError')) return null;
        stopHeartbeat();
        return refresh('heartbeat_error');
      } finally {
        if (ownGeneration === heartbeatGeneration &&
            heartbeatController === ownController) {
          heartbeatController = null;
          heartbeatInFlight = false;
        }
      }
    }

    function onVisibilityChange() {
      if (documentRef.hidden) stopHeartbeat();
      else refresh('visible');
    }

    function onFocus() {
      var now = scheduler.now ? scheduler.now() : Date.now();
      if (now - lastRefreshAt >= FOCUS_REFRESH_MIN_MS) refresh('focus');
    }

    function destroy() {
      if (destroyed) return;
      destroyed = true;
      generation++;
      mutationGeneration++;
      abortRefresh();
      if (mutationController) mutationController.abort();
      mutationController = null;
      stopHeartbeat();
      documentRef.removeEventListener('visibilitychange', onVisibilityChange);
      removeWindowListener('focus', onFocus);
      if (documentOwners.get(documentRef) === api) documentOwners.delete(documentRef);
    }

    var previousOwner = documentOwners.get(documentRef);
    if (previousOwner) previousOwner.destroy();
    documentRef.addEventListener('visibilitychange', onVisibilityChange);
    addWindowListener('focus', onFocus);

    api = {
      refresh: refresh,
      mutate: mutate,
      startHeartbeat: startHeartbeat,
      stopHeartbeat: stopHeartbeat,
      checkpoint: checkpoint,
      destroy: destroy,
      getSessionId: function () { return sessionId; },
    };
    documentOwners.set(documentRef, api);
    return api;
  }

  return {
    createWorkoutStateClient: createWorkoutStateClient,
    HEARTBEAT_MS: HEARTBEAT_MS,
    FOCUS_REFRESH_MIN_MS: FOCUS_REFRESH_MIN_MS,
  };
}));
