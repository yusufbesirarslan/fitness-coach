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

  function freshExercises(day, ids, maxSets) {
    var setLimit = Number.isInteger(maxSets) ? maxSets : 20;
    return day.egzersizler.map(function (exercise, exerciseIndex) {
      var count = parseInt(exercise.set, 10);
      if (!Number.isInteger(count) || count < 1 || count > setLimit) {
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
        if (set.reps == null && set.done !== true) {
          // Null on an open set means the prescription, not a confirmed
          // number. Show that fallback locally; it stays eligible for copy.
          set.reps = defaultReps(exercise.tekrar);
          set.repsExplicit = false;
        } else {
          // A stored integer is the user's, including when it equals the
          // prescription and weight is still blank.
          set.repsExplicit = set.reps != null;
        }
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

  function createLegacyWorkoutDraft(day, nowMs) {
    var exercises = day && Array.isArray(day.egzersizler) ? day.egzersizler : [];
    return {
      sessionId: null,
      checkpointRevision: null,
      startedAt: Number(nowMs) || 0,
      day: day,
      currentExerciseIndex: 0,
      elapsedBaselineSeconds: 0,
      elapsedStartedAtMs: Number(nowMs) || 0,
      exercises: freshExercises(
        { egzersizler: exercises }, exercises.map(function () { return null; }), 100),
    };
  }

  // Open sets still showing only the prescription are stored as null so
  // refresh can tell them from a confirmed number. Completed sets and any
  // set the user (or an earlier copy) has marked explicit keep the integer.
  function persistedReps(set) {
    var reps = optionalInteger(set.reps, MAX_REPS, 'checkpoint_reps_invalid');
    if (reps == null) return null;
    if (set.done === true || set.repsExplicit === true) return reps;
    return null;
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
            reps: persistedReps(set),
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

  // Presentation only: which exercise/set the active workout surface leads with.
  // Everything is derived from the draft's `done` flags and its persisted
  // `currentExerciseIndex`; nothing here is written back or stored separately.
  // `focusIndex` is the exercise the user explicitly opened (it may be finished,
  // so it can be reviewed); otherwise the persisted current exercise leads while
  // it still has an open set, then the first exercise with an open set.
  function deriveActiveWorkout(draft, focusIndex) {
    var exercises = draft && Array.isArray(draft.exercises) ? draft.exercises : [];
    var totalSets = 0;
    var completedSets = 0;
    exercises.forEach(function (exercise) {
      exercise.sets.forEach(function (set) {
        totalSets += 1;
        if (set.done) completedSets += 1;
      });
    });
    function inRange(index) {
      return Number.isInteger(index) && index >= 0 && index < exercises.length;
    }
    function firstOpenSet(index) {
      var sets = exercises[index].sets;
      for (var i = 0; i < sets.length; i++) if (!sets[i].done) return i;
      return -1;
    }
    var exerciseIndex = -1;
    if (inRange(focusIndex)) {
      exerciseIndex = focusIndex;
    } else if (draft && inRange(draft.currentExerciseIndex) &&
        firstOpenSet(draft.currentExerciseIndex) !== -1) {
      exerciseIndex = draft.currentExerciseIndex;
    } else {
      for (var e = 0; e < exercises.length; e++) {
        if (firstOpenSet(e) !== -1) { exerciseIndex = e; break; }
      }
    }
    var nextExerciseIndex = -1;
    for (var step = 1; step <= exercises.length; step++) {
      var candidate = ((exerciseIndex < 0 ? -1 : exerciseIndex) + step) % exercises.length;
      if (candidate !== exerciseIndex && firstOpenSet(candidate) !== -1) {
        nextExerciseIndex = candidate;
        break;
      }
    }
    return {
      exerciseIndex: exerciseIndex,
      setIndex: exerciseIndex === -1 ? -1 : firstOpenSet(exerciseIndex),
      nextExerciseIndex: nextExerciseIndex,
      completedSets: completedSets,
      totalSets: totalSets,
      complete: totalSets > 0 && completedSets === totalSets,
      exerciseStates: exercises.map(function (exercise, index) {
        if (index === exerciseIndex) return 'active';
        return exercise.sets.every(function (set) { return set.done; })
          ? 'completed' : 'upcoming';
      }),
    };
  }

  // Copy missing weight/reps from the set just completed onto the immediate
  // next set in the same exercise. Precedence is: a value already on that
  // set, then the previous set, then the prescription fallback. Weight is
  // kept whenever it is non-null. Reps are kept when `repsExplicit` is set
  // (typed, copied earlier, or hydrated as real data). Completed sets are
  // never written. Returns whether the next set changed.
  function prepareNextSet(exercise, completedIndex) {
    var sets = exercise && Array.isArray(exercise.sets) ? exercise.sets : null;
    if (!sets || !Number.isInteger(completedIndex) || completedIndex < 0) return false;
    var source = sets[completedIndex];
    var next = sets[completedIndex + 1];
    if (!source || !next || source.done !== true || next.done === true) return false;
    var changed = false;
    if (next.weightKg == null && typeof source.weightKg === 'number' &&
        Number.isFinite(source.weightKg)) {
      next.weightKg = source.weightKg;
      changed = true;
    }
    if (Number.isInteger(source.reps) &&
        (next.reps == null || next.repsExplicit !== true)) {
      if (next.reps !== source.reps) {
        next.reps = source.reps;
        changed = true;
      }
      if (next.repsExplicit !== true) {
        next.repsExplicit = true;
        changed = true;
      }
    }
    return changed;
  }

  // Canonical prior performance is already selected server-side. This only
  // accepts one entry: integer reps, and weight that is null or a real kg
  // value. Null weight stays null — 0 kg is not invented.
  function historicalEntry(prior, exerciseId) {
    if (!prior || typeof exerciseId !== 'string' || !exerciseId) return null;
    var entry = prior[exerciseId];
    if (!entry || typeof entry !== 'object') return null;
    var reps = entry.reps;
    var weightKg = entry.weight_kg;
    if (!Number.isInteger(reps) || reps < 0 || reps > MAX_REPS) return null;
    if (weightKg == null) return { reps: reps, weightKg: null };
    if (typeof weightKg !== 'number' || !Number.isFinite(weightKg) ||
        weightKg < 0 || weightKg > MAX_WEIGHT_KG) return null;
    return {
      reps: reps,
      weightKg: Math.round((weightKg + Number.EPSILON) * 10) / 10,
    };
  }

  // Display-only fallback for the first set. User-owned draft fields win.
  // Nothing here is written onto the set, so an untouched open set still
  // checkpoints as reps null.
  function presentFirstSet(set, entry) {
    var weightKg = set ? set.weightKg : null;
    var reps = set ? set.reps : null;
    if (!set || set.index !== 0 || set.done === true || !entry) {
      return { weightKg: weightKg, reps: reps };
    }
    if (set.weightExplicit !== true && weightKg == null && entry.weightKg != null) {
      weightKg = entry.weightKg;
    }
    if (set.repsExplicit !== true && set.repsTouched !== true &&
        Number.isInteger(entry.reps)) {
      reps = entry.reps;
    }
    return { weightKg: weightKg, reps: reps };
  }

  // Call when the user completes the first set, before it is marked done, so
  // the accepted historical numbers become this workout's actual values and
  // the same-exercise copy reads them. A field the user already owns is kept.
  function adoptHistoricalDefault(set, entry) {
    if (!set || set.index !== 0 || set.done === true || set.reopened === true || !entry) {
      return false;
    }
    var changed = false;
    if (set.weightExplicit !== true && set.weightKg == null && entry.weightKg != null) {
      set.weightKg = entry.weightKg;
      changed = true;
    }
    if (set.repsExplicit !== true && set.repsTouched !== true &&
        Number.isInteger(entry.reps)) {
      if (set.reps !== entry.reps) {
        set.reps = entry.reps;
        changed = true;
      }
      if (set.repsExplicit !== true) {
        set.repsExplicit = true;
        changed = true;
      }
    }
    return changed;
  }

  // Same display contract the native plan projection already parses:
  // "90 sn", "2 dk", or "0". Anything else has no duration.
  function deriveRestDurationMs(value) {
    var text = typeof value === 'string' ? value.trim() : '';
    if (text === '0') return 0;
    var match = /^(0|[1-9][0-9]*) (sn|dk)$/.exec(text);
    if (!match) return null;
    var amount = parseInt(match[1], 10);
    var seconds = match[2] === 'sn' ? amount : amount * 60;
    if (seconds > MAX_ELAPSED_SECONDS) return null;
    return seconds * 1000;
  }

  // Forward completion of a set that still has an open later set in THIS
  // exercise. Call before marking the set done. A reopened earlier set, a
  // later set that is already done, the last set of the exercise, and a
  // missing/zero prescribed rest all decline.
  function shouldStartRest(exercise, completedIndex) {
    var sets = exercise && Array.isArray(exercise.sets) ? exercise.sets : null;
    if (!sets || !Number.isInteger(completedIndex) || completedIndex < 0 ||
        completedIndex >= sets.length) return false;
    var current = sets[completedIndex];
    if (!current || current.reopened === true) return false;
    if (completedIndex + 1 >= sets.length) return false;
    for (var i = completedIndex + 1; i < sets.length; i++) {
      if (sets[i].done === true) return false;
    }
    var duration = deriveRestDurationMs(exercise.dinlenme);
    return typeof duration === 'number' && duration > 0;
  }

  function remainingRestMs(endsAt, nowMs) {
    var ends = Number(endsAt);
    var now = Number(nowMs);
    if (!Number.isFinite(ends) || !Number.isFinite(now)) return 0;
    return Math.max(0, ends - now);
  }

  // One rest session only. Null means the deadline has already passed.
  function extendRestDeadline(endsAt, nowMs, extraMs) {
    if (remainingRestMs(endsAt, nowMs) <= 0) return null;
    if (!Number.isFinite(extraMs)) return null;
    return endsAt + extraMs;
  }

  function formatRestClock(ms) {
    var seconds = Math.ceil(Math.max(0, Number(ms) || 0) / 1000);
    var minutes = Math.floor(seconds / 60);
    var remain = seconds % 60;
    return (minutes < 10 ? '0' : '') + minutes + ':' +
      (remain < 10 ? '0' : '') + remain;
  }

  // Product targets are a fixed count ("10") or a hyphen/dash range ("8-12",
  // "8–10"). Anything else — words, extra clauses, inverted ranges — is not
  // a cue. No guess.
  function parseRepTarget(value) {
    if (typeof value !== 'string') return null;
    var text = value.trim();
    var fixed = /^(0|[1-9]\d*)$/.exec(text);
    if (fixed) {
      var only = parseInt(fixed[1], 10);
      if (only > MAX_REPS) return null;
      return { min: only, max: only };
    }
    var range = /^(0|[1-9]\d*)\s*[-–—]\s*(0|[1-9]\d*)$/.exec(text);
    if (!range) return null;
    var min = parseInt(range[1], 10);
    var max = parseInt(range[2], 10);
    if (min > max || max > MAX_REPS) return null;
    return { min: min, max: max };
  }

  function classifySetAgainstTarget(actualReps, targetText) {
    var target = parseRepTarget(targetText);
    if (!target || !Number.isInteger(actualReps) || actualReps < 0 ||
        actualReps > MAX_REPS) return null;
    if (actualReps < target.min) return 'below_target';
    if (actualReps > target.max) return 'above_target';
    return 'on_target';
  }

  // Cue only for a forward completion that is about to show between-set rest.
  // Re-completing an earlier set, a failed parse, and the last set of an
  // exercise all stay silent.
  function coachCueForCompletion(options) {
    if (!options || options.reopened === true || options.forwardRest !== true) {
      return null;
    }
    return classifySetAgainstTarget(options.actualReps, options.targetText);
  }

  // Where the values on screen came from, stamped before the user edits them.
  // Current workout data beats history. History is only the untouched first set.
  function loggingSeedSource(set, entry) {
    if (!set) return 'blank';
    var historyEligible = set.index === 0 && set.reopened !== true && !!entry &&
      set.weightExplicit !== true && set.repsExplicit !== true &&
      set.repsTouched !== true && set.weightKg == null;
    if (historyEligible) return 'history';
    if (set.repsExplicit === true || set.weightExplicit === true || set.weightKg != null) {
      return 'current';
    }
    if (set.reps != null) return 'prescription';
    return 'blank';
  }

  function deriveSetLogging(seedSource, weightEdited, repsEdited) {
    var source = seedSource === 'current' || seedSource === 'history' ||
      seedSource === 'prescription' || seedSource === 'blank' ? seedSource : 'blank';
    var weight = weightEdited === true;
    var reps = repsEdited === true;
    return {
      default_source: source,
      weight_edited: weight,
      reps_edited: reps,
      fields_edited: (weight ? 1 : 0) + (reps ? 1 : 0),
    };
  }

  var CUE_TYPES = { below_target: true, on_target: true, above_target: true };

  function buildSetCompletedParams(exercisePosition, setPosition, logging, hadRest, cueType) {
    return {
      exercise_position: exercisePosition,
      set_position: setPosition,
      default_source: logging.default_source,
      weight_edited: logging.weight_edited === true,
      reps_edited: logging.reps_edited === true,
      fields_edited: logging.fields_edited,
      had_rest: hadRest === true,
      cue_type: CUE_TYPES[cueType] ? cueType : 'none',
    };
  }

  function restDurationBucket(durationMs) {
    var ms = Number(durationMs);
    if (!Number.isFinite(ms) || ms < 0) return 'lt_60';
    var seconds = Math.floor(ms / 1000);
    if (seconds < 60) return 'lt_60';
    if (seconds < 90) return '60_89';
    if (seconds < 120) return '90_119';
    return '120_plus';
  }

  // One terminal outcome per rest. Skip and natural expiry cannot both win.
  function claimRestTerminal(timer, outcome) {
    if (!timer || timer.outcome) return null;
    if (outcome !== 'skipped' && outcome !== 'expired') return null;
    timer.outcome = outcome;
    return outcome === 'skipped' ? 'training_rest_skipped' : 'training_rest_expired';
  }

  function selectWorkoutDraft(day, session, existingDraft, nowMs) {
    if (existingDraft && session && existingDraft.sessionId === session.public_id) {
      return existingDraft;
    }
    return createWorkoutDraft(day, session, nowMs);
  }

  return {
    createWorkoutDraft: createWorkoutDraft,
    createLegacyWorkoutDraft: createLegacyWorkoutDraft,
    buildCheckpointSnapshot: buildCheckpointSnapshot,
    flushWorkoutDraft: flushWorkoutDraft,
    selectWorkoutDraft: selectWorkoutDraft,
    deriveActiveWorkout: deriveActiveWorkout,
    prepareNextSet: prepareNextSet,
    historicalEntry: historicalEntry,
    presentFirstSet: presentFirstSet,
    adoptHistoricalDefault: adoptHistoricalDefault,
    deriveRestDurationMs: deriveRestDurationMs,
    shouldStartRest: shouldStartRest,
    remainingRestMs: remainingRestMs,
    extendRestDeadline: extendRestDeadline,
    formatRestClock: formatRestClock,
    parseRepTarget: parseRepTarget,
    classifySetAgainstTarget: classifySetAgainstTarget,
    coachCueForCompletion: coachCueForCompletion,
    loggingSeedSource: loggingSeedSource,
    deriveSetLogging: deriveSetLogging,
    buildSetCompletedParams: buildSetCompletedParams,
    restDurationBucket: restDurationBucket,
    claimRestTerminal: claimRestTerminal,
  };
}));
