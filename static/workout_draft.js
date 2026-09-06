(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.FitXWorkoutDraft = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var MAX_ELAPSED_SECONDS = 86400;
  var MAX_REPS = 1000;
  var MAX_WEIGHT_KG = 1000;

  function fail(code) {
    var error = new Error(code);
    error.code = code;
    throw error;
  }

  function defaultReps(value) {
    var matches = String(value || '').match(/\d+/g);
    return matches && matches.length ? parseInt(matches[matches.length - 1], 10) : null;
  }

  function assertInteger(value, min, max, code) {
    if (!Number.isInteger(value) || value < min || value > max) fail(code);
    return value;
  }

  function optionalInteger(value, max, code) {
    if (value === null) return null;
    return assertInteger(value, 0, max, code);
  }

  function weight(value) {
    if (value === null) return null;
    if (typeof value !== 'number') fail('checkpoint_weight_invalid');
    var numeric = value;
    if (!Number.isFinite(numeric) || numeric < 0 || numeric > MAX_WEIGHT_KG) {
      fail('checkpoint_weight_invalid');
    }
    return Math.round((numeric + Number.EPSILON) * 10) / 10;
  }

  function canonicalIdentities(day, session) {
    var exercises = day && Array.isArray(day.egzersizler) ? day.egzersizler : [];
    var ids = session && session.checkpoint_exercise_ids;
    if (!Array.isArray(ids) || ids.length !== exercises.length ||
        ids.some(function (id) { return typeof id !== 'string' || !id; }) ||
        new Set(ids).size !== ids.length) {
      fail('checkpoint_identity_mismatch');
    }
    return ids.slice();
  }

  function freshExercises(day, ids) {
    return day.egzersizler.map(function (exercise, exerciseIndex) {
      var count = parseInt(exercise.set, 10);
      if (!Number.isInteger(count) || count < 1 || count > 20) {
        fail('checkpoint_set_mismatch');
      }
      var sets = [];
      for (var index = 0; index < count; index++) {
        sets.push({
          index: index,
          weightKg: null,
          reps: defaultReps(exercise.tekrar),
          done: false,
          isPR: false,
        });
      }
      return {
        exerciseId: ids[exerciseIndex],
        isim: exercise.isim,
        tekrar: exercise.tekrar,
        dinlenme: exercise.dinlenme,
        not: exercise.not || '',
        sets: sets,
      };
    });
  }

  function hydrateExercises(exercises, checkpoint) {
    if (!checkpoint || !Array.isArray(checkpoint.exercises)) {
      fail('checkpoint_unavailable');
    }
    var byId = new Map();
    checkpoint.exercises.forEach(function (entry) {
      if (!entry || typeof entry.exercise_id !== 'string' ||
          byId.has(entry.exercise_id) || !Array.isArray(entry.sets)) {
        fail('checkpoint_identity_mismatch');
      }
      byId.set(entry.exercise_id, entry);
    });
    if (byId.size !== exercises.length) fail('checkpoint_identity_mismatch');
    exercises.forEach(function (exercise) {
      var persisted = byId.get(exercise.exerciseId);
      if (!persisted || persisted.sets.length !== exercise.sets.length) {
        fail('checkpoint_identity_mismatch');
      }
      var byIndex = new Map();
      persisted.sets.forEach(function (set) {
        if (!set || byIndex.has(set.index)) fail('checkpoint_set_mismatch');
        byIndex.set(set.index, set);
      });
      exercise.sets.forEach(function (set) {
        var saved = byIndex.get(set.index);
        if (!saved || typeof saved.completed !== 'boolean') {
          fail('checkpoint_set_mismatch');
        }
        set.done = saved.completed;
        set.reps = optionalInteger(saved.reps, MAX_REPS, 'checkpoint_reps_invalid');
        set.weightKg = weight(saved.weight_kg);
      });
    });
  }

  function createWorkoutDraft(day, session, nowMs) {
    if (!session || session.status !== 'active' || session.resumable === false) {
      fail('session_not_editable');
    }
    var revision = assertInteger(
      session.checkpoint_revision, 0, 999999999, 'checkpoint_revision_invalid');
    var ids = canonicalIdentities(day, session);
    var exercises = freshExercises(day, ids);
    var baseline = 0;
    var currentIndex = 0;
    if (revision > 0 && session.checkpoint === null) fail('checkpoint_unavailable');
    if (session.checkpoint !== null) {
      baseline = assertInteger(
        session.checkpoint.elapsed_seconds, 0, MAX_ELAPSED_SECONDS,
        'checkpoint_elapsed_invalid');
      currentIndex = assertInteger(
        session.checkpoint.current_exercise_index, 0, exercises.length - 1,
        'checkpoint_exercise_index_invalid');
      hydrateExercises(exercises, session.checkpoint);
    }
    return {
      sessionId: session.public_id,
      checkpointRevision: revision,
      startedAt: (Number(nowMs) || 0) - baseline * 1000,
      day: day,
      currentExerciseIndex: currentIndex,
      elapsedBaselineSeconds: baseline,
      elapsedStartedAtMs: Number(nowMs) || 0,
      exercises: exercises,
    };
  }

  function buildCheckpointSnapshot(draft, nowMs) {
    if (!draft || !Array.isArray(draft.exercises) || !draft.exercises.length) {
      fail('checkpoint_unavailable');
    }
    var localElapsed = Math.max(0, Math.floor((Number(nowMs) - draft.elapsedStartedAtMs) / 1000));
    var elapsed = draft.elapsedBaselineSeconds + localElapsed;
    assertInteger(elapsed, 0, MAX_ELAPSED_SECONDS, 'checkpoint_elapsed_invalid');
    assertInteger(
      draft.currentExerciseIndex, 0, draft.exercises.length - 1,
      'checkpoint_exercise_index_invalid');
    var seen = new Set();
    var exercises = draft.exercises.map(function (exercise) {
      if (!exercise || typeof exercise.exerciseId !== 'string' ||
          !exercise.exerciseId || seen.has(exercise.exerciseId) ||
          !Array.isArray(exercise.sets)) {
        fail('checkpoint_identity_mismatch');
      }
      seen.add(exercise.exerciseId);
      var seenSets = new Set();
      return {
        exercise_id: exercise.exerciseId,
        sets: exercise.sets.map(function (set) {
          if (!set || typeof set.done !== 'boolean' || seenSets.has(set.index)) {
            fail(typeof set.done !== 'boolean'
              ? 'checkpoint_completed_invalid' : 'checkpoint_set_mismatch');
          }
          seenSets.add(set.index);
          return {
            index: assertInteger(set.index, 0, 19, 'checkpoint_set_mismatch'),
            completed: set.done,
            reps: optionalInteger(set.reps, MAX_REPS, 'checkpoint_reps_invalid'),
            weight_kg: weight(set.weightKg),
          };
        }),
      };
    });
    return {
      current_exercise_index: draft.currentExerciseIndex,
      elapsed_seconds: elapsed,
      exercises: exercises,
    };
  }

  async function flushWorkoutDraft(client, draft, nowMs) {
    var snapshot = buildCheckpointSnapshot(draft, nowMs);
    var result = await client.flushCheckpoint(snapshot);
    if (!result || !result.ok) return result || { ok: false };
    return {
      ok: true,
      checkpointRevision: client.getCheckpointRevision(),
      snapshot: snapshot,
    };
  }

  function selectWorkoutDraft(day, session, existingDraft, nowMs) {
    if (existingDraft && session && existingDraft.sessionId === session.public_id) {
      return existingDraft;
    }
    return createWorkoutDraft(day, session, nowMs);
  }

  return {
    createWorkoutDraft: createWorkoutDraft,
    buildCheckpointSnapshot: buildCheckpointSnapshot,
    flushWorkoutDraft: flushWorkoutDraft,
    selectWorkoutDraft: selectWorkoutDraft,
  };
}));
