const test = require('node:test');
const assert = require('node:assert/strict');
const {
  createWorkoutDraft,
  createLegacyWorkoutDraft,
  buildCheckpointSnapshot,
  selectWorkoutDraft,
  deriveActiveWorkout,
  prepareNextSet,
} = require('../../static/workout_draft.js');

const day = {
  gun: 'Pazartesi', tip: 'agirlik', odak: 'Guc',
  egzersizler: [
    { isim: 'Displayed squat', set: 2, tekrar: '8-12', dinlenme: '90 sn', not: 'note' },
    { isim: 'Displayed row', set: 1, tekrar: '10', dinlenme: '60 sn', not: '' },
  ],
};

test('legacy fallback draft stays in the shared draft boundary without server identities', () => {
  const draft = createLegacyWorkoutDraft(day, 5000);

  assert.equal(draft.sessionId, null);
  assert.equal(draft.startedAt, 5000);
  assert.equal(draft.exercises[0].sets.length, 2);
  assert.equal(draft.exercises[0].sets[0].reps, 12);
  assert.equal(draft.exercises[0].sets[0].done, false);
});

test('legacy fallback preserves canonical plans with more than durable checkpoint set limits', () => {
  const legacyDay = {
    gun: 'Pazartesi', tip: 'agirlik', odak: 'Guc',
    egzersizler: [
      { isim: 'High-volume squat', set: 21, tekrar: '5', dinlenme: '90 sn' },
    ],
  };

  const draft = createLegacyWorkoutDraft(legacyDay, 5000);

  assert.equal(draft.exercises[0].sets.length, 21);
  assert.equal(draft.exercises[0].sets[20].reps, 5);
});

test('fresh draft uses server identities and emits only the exact full snapshot', () => {
  const draft = createWorkoutDraft(day, {
    public_id: 'session-1', status: 'active', resumable: true,
    checkpoint_revision: 0, checkpoint: null,
    checkpoint_exercise_ids: ['squat-v1', 'row-v1'],
  }, 1000);
  draft.exercises[0].sets[0].weightKg = 82.26;
  draft.exercises[0].sets[0].reps = 9;
  draft.exercises[0].sets[0].done = true;
  draft.exercises[0].sets[0].isPR = true;
  draft.currentExerciseIndex = 1;

  const snapshot = buildCheckpointSnapshot(draft, 6000);

  assert.deepEqual(snapshot, {
    current_exercise_index: 1,
    elapsed_seconds: 5,
    exercises: [
      { exercise_id: 'squat-v1', sets: [
        { index: 0, completed: true, reps: 9, weight_kg: 82.3 },
        { index: 1, completed: false, reps: 12, weight_kg: null },
      ] },
      { exercise_id: 'row-v1', sets: [
        { index: 0, completed: false, reps: 10, weight_kg: null },
      ] },
    ],
  });
  assert.equal(JSON.stringify(snapshot).includes('Displayed squat'), false);
  assert.equal(JSON.stringify(snapshot).includes('isPR'), false);
  assert.equal(JSON.stringify(snapshot).includes('note'), false);
});

test('hydration maps exercises by exercise_id and sets by index', () => {
  const draft = createWorkoutDraft(day, {
    public_id: 'session-1', status: 'active', resumable: true,
    checkpoint_revision: 4,
    checkpoint_exercise_ids: ['squat-v1', 'row-v1'],
    checkpoint: {
      current_exercise_index: 1, elapsed_seconds: 321,
      exercises: [
        { exercise_id: 'row-v1', sets: [
          { index: 0, completed: true, reps: 14, weight_kg: 41.5 },
        ] },
        { exercise_id: 'squat-v1', sets: [
          { index: 1, completed: true, reps: 7, weight_kg: 90.0 },
          { index: 0, completed: false, reps: 8, weight_kg: 87.5 },
        ] },
      ],
    },
  }, 10000);

  assert.equal(draft.currentExerciseIndex, 1);
  assert.equal(draft.elapsedBaselineSeconds, 321);
  assert.deepEqual(
    draft.exercises[0].sets.map(s => [s.index, s.done, s.reps, s.weightKg]),
    [[0, false, 8, 87.5], [1, true, 7, 90]],
  );
  assert.deepEqual(
    draft.exercises[1].sets.map(s => [s.index, s.done, s.reps, s.weightKg]),
    [[0, true, 14, 41.5]],
  );
  assert.deepEqual(buildCheckpointSnapshot(draft, 10000), {
    current_exercise_index: 1, elapsed_seconds: 321,
    exercises: [
      { exercise_id: 'squat-v1', sets: [
        { index: 0, completed: false, reps: 8, weight_kg: 87.5 },
        { index: 1, completed: true, reps: 7, weight_kg: 90 },
      ] },
      { exercise_id: 'row-v1', sets: [
        { index: 0, completed: true, reps: 14, weight_kg: 41.5 },
      ] },
    ],
  });
});

test('corrupt and identity-ambiguous projections fail closed', () => {
  assert.throws(() => createWorkoutDraft(day, {
    public_id: 'session-1', status: 'active', resumable: true,
    checkpoint_revision: 2, checkpoint: null,
    checkpoint_exercise_ids: ['squat-v1', 'row-v1'],
  }, 0), /checkpoint_unavailable/);
  assert.throws(() => createWorkoutDraft(day, {
    public_id: 'session-1', status: 'active', resumable: true,
    checkpoint_revision: 1,
    checkpoint_exercise_ids: ['squat-v1', 'row-v1'],
    checkpoint: {
      current_exercise_index: 0, elapsed_seconds: 0,
      exercises: [
        { exercise_id: 'squat-v1', sets: [] },
        { exercise_id: 'squat-v1', sets: [] },
      ],
    },
  }, 0), /checkpoint_identity_mismatch/);
  assert.throws(() => createWorkoutDraft(day, {
    public_id: 'session-1', status: 'active', resumable: true,
    checkpoint_revision: 0, checkpoint: null,
    checkpoint_exercise_ids: ['squat-v1', 'squat-v1'],
  }, 0), /checkpoint_identity_mismatch/);
});

test('snapshot building refuses malformed UI values instead of coercing them', () => {
  const draft = createWorkoutDraft(day, {
    public_id: 'session-1', status: 'active', resumable: true,
    checkpoint_revision: 0, checkpoint: null,
    checkpoint_exercise_ids: ['squat-v1', 'row-v1'],
  }, 0);
  draft.exercises[0].sets[0].done = 'yes';
  assert.throws(() => buildCheckpointSnapshot(draft, 0), /checkpoint_completed_invalid/);
  draft.exercises[0].sets[0].done = false;
  draft.exercises[0].sets[0].weightKg = '80';
  assert.throws(() => buildCheckpointSnapshot(draft, 0), /checkpoint_weight_invalid/);
  draft.exercises[0].sets[0].weightKg = 80;
  draft.exercises[0].sets[1].index = 0;
  assert.throws(() => buildCheckpointSnapshot(draft, 0), /checkpoint_set_mismatch/);
});

test('ordinary close and reopen retains the pending draft for the same session', () => {
  const projection = {
    public_id: 'session-1', status: 'active', resumable: true,
    checkpoint_revision: 0, checkpoint: null,
    checkpoint_exercise_ids: ['squat-v1', 'row-v1'],
  };
  const dirty = createWorkoutDraft(day, projection, 0);
  dirty.exercises[0].sets[0].done = true;

  const reopened = selectWorkoutDraft(day, projection, dirty, 1000);

  assert.equal(reopened, dirty);
  assert.equal(reopened.exercises[0].sets[0].done, true);
});

test('active surface derives completed, active and upcoming state from done flags only', () => {
  const projection = {
    public_id: 'session-1', status: 'active', resumable: true,
    checkpoint_revision: 1,
    checkpoint: {
      current_exercise_index: 0, elapsed_seconds: 30,
      exercises: [
        { exercise_id: 'squat-v1', sets: [
          { index: 0, completed: true, reps: 8, weight_kg: 60 },
          { index: 1, completed: false, reps: 8, weight_kg: null },
        ] },
        { exercise_id: 'row-v1', sets: [
          { index: 0, completed: false, reps: 10, weight_kg: null },
        ] },
      ],
    },
    checkpoint_exercise_ids: ['squat-v1', 'row-v1'],
  };
  const draft = createWorkoutDraft(day, projection, 0);
  const before = JSON.stringify(draft);

  const view = deriveActiveWorkout(draft, null);
  assert.equal(JSON.stringify(draft), before, 'derivation must not mutate the draft');
  assert.deepEqual(
    [view.exerciseIndex, view.setIndex, view.nextExerciseIndex], [0, 1, 1]);
  assert.deepEqual([view.completedSets, view.totalSets, view.complete], [1, 3, false]);
  assert.deepEqual(view.exerciseStates, ['active', 'upcoming']);

  // Finishing the exercise moves the lead to the next open exercise, even
  // though the persisted cursor still points at the finished one.
  draft.exercises[0].sets[1].done = true;
  const advanced = deriveActiveWorkout(draft, null);
  assert.deepEqual(
    [advanced.exerciseIndex, advanced.setIndex, advanced.nextExerciseIndex], [1, 0, -1]);
  assert.deepEqual(advanced.exerciseStates, ['completed', 'active']);

  // An explicitly opened finished exercise leads for review with no open set.
  const review = deriveActiveWorkout(draft, 0);
  assert.deepEqual([review.exerciseIndex, review.setIndex], [0, -1]);

  draft.exercises[1].sets[0].done = true;
  const done = deriveActiveWorkout(draft, null);
  assert.deepEqual([done.exerciseIndex, done.complete], [-1, true]);
  assert.deepEqual(done.exerciseStates, ['completed', 'completed']);
});

const progressionDay = {
  gun: 'Pazartesi', tip: 'agirlik', odak: 'Guc',
  egzersizler: [
    { isim: 'Lat Pulldown', set: 3, tekrar: '8-12', dinlenme: '90 sn', not: '' },
    { isim: 'Barbell Row', set: 1, tekrar: '10', dinlenme: '60 sn', not: '' },
  ],
};

function progressionDraft(checkpoint) {
  return createWorkoutDraft(progressionDay, {
    public_id: 'session-1', status: 'active', resumable: true,
    checkpoint_revision: checkpoint ? 1 : 0,
    checkpoint: checkpoint || null,
    checkpoint_exercise_ids: ['pulldown', 'row'],
  }, 0);
}

function fieldSnapshot(exercise) {
  return exercise.sets.map(set => [set.index, set.done, set.reps, set.weightKg]);
}

test('prepareNextSet copies missing weight and reps onto the immediate next set only', () => {
  const draft = progressionDraft();
  const pulldown = draft.exercises[0];
  const row = draft.exercises[1];
  const rowBefore = fieldSnapshot(row);
  pulldown.sets[0].weightKg = 60;
  pulldown.sets[0].reps = 8;
  pulldown.sets[0].done = true;
  pulldown.sets[1].weightKg = null;
  pulldown.sets[1].reps = null;

  assert.equal(prepareNextSet(pulldown, 0), true);

  assert.deepEqual(fieldSnapshot(pulldown), [
    [0, true, 8, 60],
    [1, false, 8, 60],
    [2, false, 12, null],
  ]);
  assert.equal(pulldown.sets[1].repsExplicit, true);
  assert.notEqual(pulldown.sets[2].repsExplicit, true);
  assert.deepEqual(fieldSnapshot(row), rowBefore);
  // 60 stays 60. Nothing here invents the next load.
  assert.equal(pulldown.sets[1].weightKg, 60);
});

test('prepareNextSet keeps existing values and fills only the blank field', () => {
  const draft = progressionDraft();
  const sets = draft.exercises[0].sets;
  sets[0].weightKg = 60;
  sets[0].reps = 8;
  sets[0].done = true;

  sets[1].weightKg = 62.5;
  sets[1].reps = 6;
  sets[1].repsExplicit = true;
  assert.equal(prepareNextSet(draft.exercises[0], 0), false);
  assert.deepEqual([sets[1].weightKg, sets[1].reps, sets[1].done], [62.5, 6, false]);

  sets[1].weightKg = 62.5;
  sets[1].reps = null;
  sets[1].repsExplicit = false;
  assert.equal(prepareNextSet(draft.exercises[0], 0), true);
  assert.deepEqual([sets[1].weightKg, sets[1].reps], [62.5, 8]);

  sets[1].weightKg = null;
  sets[1].reps = 6;
  sets[1].repsExplicit = true;
  assert.equal(prepareNextSet(draft.exercises[0], 0), true);
  assert.deepEqual([sets[1].weightKg, sets[1].reps], [60, 6]);
});

test('untouched prescription reps yield to the previous set; a completed set does not', () => {
  const draft = progressionDraft();
  const sets = draft.exercises[0].sets;
  sets[0].weightKg = 60;
  sets[0].reps = 8;
  sets[0].done = true;
  // Fresh set 2 still carries the 8-12 fallback (12) and no explicit flag.
  assert.equal(sets[1].reps, 12);
  assert.notEqual(sets[1].repsExplicit, true);

  prepareNextSet(draft.exercises[0], 0);
  assert.deepEqual([sets[1].weightKg, sets[1].reps], [60, 8]);

  sets[1].done = true;
  sets[2].done = true;
  sets[2].weightKg = 70;
  sets[2].reps = 5;
  const before = fieldSnapshot(draft.exercises[0]);
  assert.equal(prepareNextSet(draft.exercises[0], 1), false);
  assert.deepEqual(fieldSnapshot(draft.exercises[0]), before);
});

test('re-completing an earlier set does not rewrite later sets or rewind the active set', () => {
  const draft = progressionDraft();
  const sets = draft.exercises[0].sets;
  sets[0].done = true;
  sets[0].weightKg = 60;
  sets[0].reps = 8;
  sets[1].done = true;
  sets[1].weightKg = 70;
  sets[1].reps = 6;
  sets[1].repsExplicit = true;
  sets[2].weightKg = null;
  sets[2].reps = 12;

  // Edit set 1, then save it again.
  sets[0].done = false;
  sets[0].weightKg = 55;
  sets[0].reps = 5;
  let view = deriveActiveWorkout(draft, 0);
  assert.deepEqual([view.exerciseIndex, view.setIndex], [0, 0]);

  sets[0].done = true;
  assert.equal(prepareNextSet(draft.exercises[0], 0), false);
  view = deriveActiveWorkout(draft, 0);
  assert.deepEqual([view.exerciseIndex, view.setIndex], [0, 2]);
  assert.equal(sets[1].done, true);
  assert.deepEqual([sets[1].weightKg, sets[1].reps], [70, 6]);
  assert.deepEqual([sets[2].weightKg, sets[2].reps], [null, 12]);
});

test('hydrated checkpoint values win and untouched fallback reps stay copyable', () => {
  const draft = progressionDraft({
    current_exercise_index: 0, elapsed_seconds: 40,
    exercises: [
      { exercise_id: 'pulldown', sets: [
        { index: 0, completed: true, reps: 8, weight_kg: 60 },
        { index: 1, completed: false, reps: 8, weight_kg: 60 },
        { index: 2, completed: false, reps: 12, weight_kg: null },
      ] },
      { exercise_id: 'row', sets: [
        { index: 0, completed: false, reps: 10, weight_kg: null },
      ] },
    ],
  });
  const pulldown = draft.exercises[0];
  assert.equal(pulldown.sets[1].repsExplicit, true);
  assert.notEqual(pulldown.sets[2].repsExplicit, true);
  const rowBefore = fieldSnapshot(draft.exercises[1]);

  pulldown.sets[1].done = true;
  pulldown.sets[1].reps = 8;
  pulldown.sets[1].weightKg = 62.5;
  assert.equal(prepareNextSet(pulldown, 1), true);
  assert.deepEqual([pulldown.sets[2].weightKg, pulldown.sets[2].reps], [62.5, 8]);
  assert.deepEqual(fieldSnapshot(draft.exercises[1]), rowBefore);

  const preserved = progressionDraft({
    current_exercise_index: 0, elapsed_seconds: 10,
    exercises: [
      { exercise_id: 'pulldown', sets: [
        { index: 0, completed: false, reps: 8, weight_kg: 60 },
        { index: 1, completed: false, reps: 6, weight_kg: 62.5 },
        { index: 2, completed: false, reps: null, weight_kg: 62.5 },
      ] },
      { exercise_id: 'row', sets: [
        { index: 0, completed: false, reps: 10, weight_kg: null },
      ] },
    ],
  });
  const open = preserved.exercises[0];
  open.sets[0].done = true;
  assert.equal(prepareNextSet(open, 0), false);
  assert.deepEqual([open.sets[1].weightKg, open.sets[1].reps], [62.5, 6]);

  open.sets[1].done = true;
  assert.equal(prepareNextSet(open, 1), true);
  assert.deepEqual([open.sets[2].weightKg, open.sets[2].reps], [62.5, 6]);
});
