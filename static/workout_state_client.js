(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.FitXWorkoutStateClient = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var CHECKPOINT_DEBOUNCE_MS = 800;
  var FOCUS_REFRESH_MIN_MS = 5000;
  var documentOwners = new WeakMap();

  function clone(value) {
    return value == null ? value : JSON.parse(JSON.stringify(value));
  }

  function semanticKey(value) {
    return value == null ? null : JSON.stringify(value);
  }

  function defaultIdempotencyKey() {
    var cryptoRef = typeof crypto !== 'undefined' ? crypto : null;
    if (cryptoRef && typeof cryptoRef.randomUUID === 'function') {
      return cryptoRef.randomUUID();
    }
    if (cryptoRef && typeof cryptoRef.getRandomValues === 'function') {
      var bytes = new Uint8Array(16);
      cryptoRef.getRandomValues(bytes);
      return 'cp-' + Array.from(bytes, function (byte) {
        return byte.toString(16).padStart(2, '0');
      }).join('');
    }
    throw new Error('secure_random_unavailable');
  }

  function createWorkoutStateClient(options) {
    var fetchImpl = options.fetchImpl;
    var onSnapshot = options.onSnapshot || function () {};
    var onBlocked = options.onBlocked || function () {};
    var onCheckpointRetryable = options.onCheckpointRetryable || function () {};
    var onCheckpointAcknowledged = options.onCheckpointAcknowledged || function () {};
    var scheduler = options.scheduler || {};
    var setTimer = scheduler.setTimeout || setTimeout;
    var clearTimer = scheduler.clearTimeout || clearTimeout;
    var now = scheduler.now || Date.now;
    var createIdempotencyKey = options.createIdempotencyKey || defaultIdempotencyKey;
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
    var refreshController = null;
    var checkpointController = null;
    var saveInFlight = null;
    var saveTimer = null;
    var pendingCommand = null;
    var dirtySnapshot = null;
    var acknowledgedSnapshot = null;
    var checkpointRevision = null;
    var contractVersion = null;
    var sessionId = null;
    var persistenceBlocked = false;
    var usedKeys = new Set();
    var destroyed = false;
    var lastRefreshAt = -Infinity;
    var api = null;

    function abortRefresh() {
      if (refreshController) refreshController.abort();
      refreshController = null;
    }

    function cancelScheduledSave() {
      if (saveTimer !== null) clearTimer(saveTimer);
      saveTimer = null;
    }

    function clearCheckpointState() {
      cancelScheduledSave();
      pendingCommand = null;
      dirtySnapshot = null;
      acknowledgedSnapshot = null;
      checkpointRevision = null;
      sessionId = null;
      persistenceBlocked = false;
    }

    function activeSessionFrom(snapshot) {
      var state = snapshot && snapshot.workout && snapshot.workout.state;
      var session = state && state.session;
      if (!state || state.contract_version !== 2 ||
          state.session_state !== 'active_resumable' || !session ||
          session.status !== 'active' || session.resumable === false) return null;
      return session;
    }

    function acceptCanonical(snapshot, forceReplace) {
      var state = snapshot && snapshot.workout && snapshot.workout.state;
      contractVersion = state && state.contract_version;
      var session = activeSessionFrom(snapshot);
      if (!session) {
        clearCheckpointState();
        return { replaceDraft: true };
      }
      if (!Number.isInteger(session.checkpoint_revision) ||
          session.checkpoint_revision < 0 ||
          (session.checkpoint_revision > 0 && session.checkpoint === null)) {
        clearCheckpointState();
        persistenceBlocked = true;
        onBlocked('checkpoint_unavailable');
        return { replaceDraft: true, blocked: true };
      }
      var preserveDirty = dirtySnapshot !== null && !forceReplace;
      if (!preserveDirty) {
        sessionId = session.public_id;
        checkpointRevision = session.checkpoint_revision;
        acknowledgedSnapshot = clone(session.checkpoint);
        pendingCommand = null;
        dirtySnapshot = null;
        persistenceBlocked = false;
        cancelScheduledSave();
      }
      return { replaceDraft: !preserveDirty };
    }

    async function refresh(reason, refreshOptions) {
      if (destroyed) return null;
      var ownGeneration = ++generation;
      var ownMutationEpoch = mutationEpoch;
      var forceReplace = Boolean(refreshOptions && refreshOptions.forceReplace);
      abortRefresh();
      refreshController = typeof AbortController !== 'undefined'
        ? new AbortController() : null;
      var ownController = refreshController;
      try {
        var response = await fetchImpl('/training/bootstrap', ownController ? {
          signal: ownController.signal,
        } : {});
        if (!response.ok) throw new Error('bootstrap_unavailable');
        var snapshot = await response.json();
        if (destroyed || ownGeneration !== generation ||
            ownMutationEpoch !== mutationEpoch) return null;
        lastRefreshAt = now();
        var meta = acceptCanonical(snapshot, forceReplace);
        onSnapshot(snapshot, reason, meta);
        return snapshot;
      } catch (error) {
        if (error && error.name === 'AbortError') return null;
        if (ownGeneration === generation && ownMutationEpoch === mutationEpoch) {
          onBlocked('bootstrap_unavailable');
        }
        return null;
      } finally {
        if (refreshController === ownController) refreshController = null;
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
      }()).catch(function (error) {
        if (destroyed || (error && error.name === 'AbortError')) return null;
        throw error;
      });
      return mutationInFlight;
    }

    function freshKey() {
      for (var attempt = 0; attempt < 8; attempt++) {
        var key = createIdempotencyKey();
        if (typeof key === 'string' && /^[A-Za-z0-9._:-]{8,64}$/.test(key) &&
            !usedKeys.has(key)) {
          usedKeys.add(key);
          return key;
        }
      }
      throw new Error('idempotency_key_unavailable');
    }

    function newCommand() {
      if (!sessionId || !Number.isInteger(checkpointRevision) || !dirtySnapshot) {
        return null;
      }
      return {
        sessionId: sessionId,
        baseRevision: checkpointRevision,
        snapshot: clone(dirtySnapshot),
        snapshotKey: semanticKey(dirtySnapshot),
        idempotencyKey: freshKey(),
      };
    }

    function errorResult(response, body, resolution) {
      return {
        ok: false,
        status: response ? response.status : 0,
        code: body && body.code ? body.code : 'checkpoint_unavailable',
        resolution: resolution || null,
        retryable: false,
      };
    }

    async function attemptCheckpoint(command) {
      checkpointController = typeof AbortController !== 'undefined'
        ? new AbortController() : null;
      var ownController = checkpointController;
      var response;
      var body = {};
      try {
        response = await fetchImpl(
          '/workout/session/' + encodeURIComponent(command.sessionId) + '/checkpoint',
          Object.assign({
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'If-Match': String(command.baseRevision),
              'Idempotency-Key': command.idempotencyKey,
            },
            body: JSON.stringify({ checkpoint: command.snapshot }),
          }, ownController ? { signal: ownController.signal } : {}),
        );
        body = await response.json().catch(function () { return {}; });
      } catch (error) {
        if (destroyed || (error && error.name === 'AbortError')) return null;
        var networkResult = {
          ok: false, status: 0, code: 'network_error', resolution: 'retry',
          retryable: true,
        };
        onCheckpointRetryable(networkResult);
        return networkResult;
      } finally {
        if (checkpointController === ownController) checkpointController = null;
      }
      if (destroyed) return null;
      if (response.ok) {
        var returned = body && body.session;
        if (!returned || returned.public_id !== command.sessionId ||
            !Number.isInteger(returned.checkpoint_revision) ||
            returned.checkpoint_revision < 0) {
          persistenceBlocked = true;
          onBlocked('checkpoint_unavailable');
          return errorResult(response, { code: 'checkpoint_unavailable' }, null);
        }
        checkpointRevision = returned.checkpoint_revision;
        acknowledgedSnapshot = clone(command.snapshot);
        pendingCommand = null;
        if (semanticKey(dirtySnapshot) === command.snapshotKey) dirtySnapshot = null;
        generation++;
        abortRefresh();
        onCheckpointAcknowledged(clone(returned));
        return { ok: true, status: response.status, body: body };
      }

      var resolution = response.headers && response.headers.get
        ? response.headers.get('Session-Resolution') : null;
      var result = errorResult(response, body, resolution);
      if (response.status === 429 || response.status >= 500 || resolution === 'retry') {
        result.retryable = true;
        onCheckpointRetryable(result);
        return result;
      }

      pendingCommand = null;
      cancelScheduledSave();
      if (body.code === 'session_terminal' || resolution === 'terminal') {
        persistenceBlocked = true;
        dirtySnapshot = null;
        sessionId = null;
        await refresh('checkpoint_terminal', { forceReplace: true });
      } else if (resolution === 'reread') {
        dirtySnapshot = null;
        await refresh('checkpoint_reconcile', { forceReplace: true });
      } else {
        persistenceBlocked = true;
        dirtySnapshot = null;
        onBlocked(body.code || 'checkpoint_unavailable');
      }
      return result;
    }

    function pumpCheckpoint() {
      if (destroyed) return Promise.resolve(null);
      if (saveInFlight) {
        return saveInFlight.then(function (result) {
          if (result && result.ok && dirtySnapshot) return pumpCheckpoint();
          return result;
        });
      }
      if (persistenceBlocked || contractVersion !== 2 || !sessionId) {
        return Promise.resolve({ disabled: true });
      }
      if (!pendingCommand && semanticKey(dirtySnapshot) === semanticKey(acknowledgedSnapshot)) {
        dirtySnapshot = null;
      }
      if (!pendingCommand && !dirtySnapshot) {
        return Promise.resolve({ ok: true, unchanged: true });
      }
      if (!pendingCommand) pendingCommand = newCommand();
      var command = pendingCommand;
      saveInFlight = attemptCheckpoint(command).finally(function () {
        saveInFlight = null;
      });
      return saveInFlight.then(function (result) {
        if (result && result.ok && dirtySnapshot) return pumpCheckpoint();
        return result;
      });
    }

    function setDirtySnapshot(snapshot) {
      if (destroyed || persistenceBlocked || contractVersion !== 2 || !sessionId) return false;
      var next = clone(snapshot);
      if (semanticKey(next) === semanticKey(acknowledgedSnapshot) && !pendingCommand) {
        dirtySnapshot = null;
        cancelScheduledSave();
        return false;
      }
      dirtySnapshot = next;
      return true;
    }

    function scheduleCheckpoint(snapshot) {
      if (!setDirtySnapshot(snapshot)) return Promise.resolve({ unchanged: true });
      cancelScheduledSave();
      saveTimer = setTimer(function () {
        saveTimer = null;
        return pumpCheckpoint();
      }, CHECKPOINT_DEBOUNCE_MS);
      return Promise.resolve({ scheduled: true });
    }

    function flushCheckpoint(snapshot) {
      if (snapshot !== undefined) setDirtySnapshot(snapshot);
      cancelScheduledSave();
      return pumpCheckpoint();
    }

    function retryCheckpoint() {
      if (!pendingCommand) return Promise.resolve({ ok: false, retryable: false });
      return pumpCheckpoint();
    }

    function stopCheckpointing() {
      cancelScheduledSave();
      persistenceBlocked = true;
      dirtySnapshot = null;
      pendingCommand = null;
      if (checkpointController) checkpointController.abort();
      checkpointController = null;
    }

    function onVisibilityChange() {
      if (!documentRef.hidden) refresh('visible');
    }

    function onFocus() {
      if (now() - lastRefreshAt >= FOCUS_REFRESH_MIN_MS) refresh('focus');
    }

    function destroy() {
      if (destroyed) return;
      destroyed = true;
      generation++;
      mutationGeneration++;
      abortRefresh();
      cancelScheduledSave();
      if (mutationController) mutationController.abort();
      if (checkpointController) checkpointController.abort();
      mutationController = null;
      checkpointController = null;
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
      scheduleCheckpoint: scheduleCheckpoint,
      flushCheckpoint: flushCheckpoint,
      retryCheckpoint: retryCheckpoint,
      stopCheckpointing: stopCheckpointing,
      destroy: destroy,
      getSessionId: function () { return sessionId; },
      getCheckpointRevision: function () { return checkpointRevision; },
      hasDirtyCheckpoint: function () { return dirtySnapshot !== null; },
    };
    documentOwners.set(documentRef, api);
    return api;
  }

  return {
    createWorkoutStateClient: createWorkoutStateClient,
    CHECKPOINT_DEBOUNCE_MS: CHECKPOINT_DEBOUNCE_MS,
    FOCUS_REFRESH_MIN_MS: FOCUS_REFRESH_MIN_MS,
  };
}));
