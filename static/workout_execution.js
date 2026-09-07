(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.FitXTrainingFlow = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  async function runWorkoutStart(contractVersion, startCanonical, openDraft) {
    if (contractVersion === 2) {
      var started = await startCanonical();
      if (!started || !started.ok) return started || { ok: false };
    }
    await openDraft();
    return { ok: true, legacy: contractVersion !== 2 };
  }

  async function runWorkoutEdit(contractVersion, checkpointDraft) {
    if (contractVersion !== 2) return { disabled: true };
    return checkpointDraft();
  }

  async function runWorkoutFinish(contractVersion, flushDraft, openCompletion) {
    var flushed = { ok: true, legacy: true };
    if (contractVersion === 2) {
      flushed = await flushDraft();
      if (!flushed || !flushed.ok) return flushed || { ok: false };
    }
    await openCompletion();
    return flushed;
  }

  function attachWorkoutCompletion(contractVersion, payload, sessionId, revision) {
    if (contractVersion !== 2) return payload;
    if (typeof sessionId !== 'string' || !sessionId ||
        !Number.isInteger(revision) || revision < 0) {
      var error = new Error('session_completion_unavailable');
      error.code = 'session_completion_unavailable';
      throw error;
    }
    payload.session_id = sessionId;
    payload.expected_checkpoint_revision = revision;
    return payload;
  }

  var TRAINING_ACTION_NAMES = [
    'abandonWorkout', 'addRest', 'closeCelebration', 'closeDayPreview',
    'closeSession', 'finishSession', 'generatePlan', 'previewDay',
    'resetPlan', 'savePlan', 'skipRest', 'startWorkout', 'submitPumpCheck',
  ];

  function publishTrainingActions(target, actions) {
    TRAINING_ACTION_NAMES.forEach(function (name) {
      if (!actions || typeof actions[name] !== 'function') {
        throw new Error('training_action_unavailable:' + name);
      }
      target[name] = actions[name];
    });
    return target;
  }

  return {
    runWorkoutStart: runWorkoutStart,
    runWorkoutEdit: runWorkoutEdit,
    runWorkoutFinish: runWorkoutFinish,
    attachWorkoutCompletion: attachWorkoutCompletion,
    TRAINING_ACTION_NAMES: TRAINING_ACTION_NAMES,
    publishTrainingActions: publishTrainingActions,
  };
}));
