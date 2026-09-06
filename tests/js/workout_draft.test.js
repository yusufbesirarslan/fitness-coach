const test = require('node:test');
const assert = require('node:assert/strict');
const {
  createWorkoutDraft,
  buildCheckpointSnapshot,
  selectWorkoutDraft,
} = require('../../static/workout_draft.js');

const day = {
  gun: 'Pazartesi', tip: 'agirlik', odak: 'Guc',
  egzersizler: [
    { isim: 'Displayed squat', set: 2, tekrar: '8-12', dinlenme: '90 sn', not: 'note' },
    { isim: 'Displayed row', set: 1, tekrar: '10', dinlenme: '60 sn', not: '' },
  ],
};

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
