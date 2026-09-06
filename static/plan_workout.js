(function () {
  'use strict';

  var bootstrapNode = document.getElementById('plan-workout-bootstrap');
  if (!bootstrapNode) return;

  var canonical = JSON.parse(bootstrapNode.textContent);
  var workoutState = canonical.workout && canonical.workout.state;
  var todayPlan = canonical.today_plan;
  var draft = null;
  var trigger = null;

  function copy(key) {
    return window.t ? window.t(key) : key;
  }

  function escapeHTML(value) {
    var node = document.createElement('span');
    node.textContent = value == null ? '' : String(value);
    return node.innerHTML;
  }

  function setOpen(node, open) {
    node.classList.toggle('open', open);
    node.setAttribute('aria-hidden', open ? 'false' : 'true');
    document.body.style.overflow = open ? 'hidden' : '';
  }

  function showError(message) {
    var completion = document.getElementById('plan-completion');
    var session = document.getElementById('session-view');
    var node = completion && completion.classList.contains('open')
      ? document.getElementById('plan-completion-error')
      : session && session.classList.contains('open')
        ? document.getElementById('sv-error')
        : document.getElementById('plan-workout-error');
    if (!node) return;
    node.textContent = message;
    node.hidden = false;
  }

  function applySnapshot(snapshot, reason, meta) {
    canonical = snapshot;
    workoutState = snapshot && snapshot.workout && snapshot.workout.state;
    todayPlan = snapshot && snapshot.today_plan;
    if (meta && meta.replaceDraft && reason !== 'server_render') draft = null;
    if (reason !== 'server_render') syncActionFromCanonical();
  }

  function syncActionFromCanonical() {
    var domain = document.querySelector('[data-plan-domain="training"]');
    var button = document.querySelector('[data-action="startWorkout"]');
    if (!domain || !button) return;
    var action = workoutState && workoutState.action;
    var actionable = action === 'start' || action === 'resume';
    domain.dataset.workoutAction = actionable ? action : 'none';
    button.hidden = !actionable;
    if (actionable) button.textContent = copy('plan.action.' + action + '_workout');
  }

  var client = window.FitXWorkoutStateClient.createWorkoutStateClient({
    fetchImpl: window.fetch.bind(window),
    onSnapshot: applySnapshot,
    onBlocked: function () { showError(copy('training.progress_unavailable')); },
    onCheckpointRetryable: function () {
      showError(copy('training.progress_save_failed'));
    },
    onCheckpointAcknowledged: function (session) {
      if (workoutState && workoutState.session &&
          workoutState.session.public_id === session.public_id) {
        workoutState.session = session;
      }
      if (draft && draft.sessionId === session.public_id) {
        draft.checkpointRevision = session.checkpoint_revision;
      }
    },
    documentRef: document,
    addEventListener: window.addEventListener.bind(window),
    removeEventListener: window.removeEventListener.bind(window),
  });
  client.hydrate(canonical, 'server_render');

  function renderDraft() {
    var body = document.getElementById('sv-body');
    body.innerHTML = draft.exercises.map(function (exercise, exerciseIndex) {
      var rows = exercise.sets.map(function (set, setIndex) {
        return '<div class="set-row' + (set.done ? ' is-done' : '') +
          '" data-ex="' + exerciseIndex + '" data-set="' + setIndex + '">' +
          '<div class="set-idx">' + (setIndex + 1) + '</div>' +
          '<input class="set-input" type="number" inputmode="decimal" min="0" step="0.5"' +
          ' data-field="weight" aria-label="' + escapeHTML(copy('training.weight')) +
          '" value="' + (set.weightKg == null ? '' : set.weightKg) + '">' +
          '<input class="set-input" type="number" inputmode="numeric" min="0" step="1"' +
          ' data-field="reps" aria-label="' + escapeHTML(copy('training.reps')) +
          '" value="' + (set.reps == null ? '' : set.reps) + '">' +
          '<button class="set-check" type="button" data-field="done" aria-label="' +
          escapeHTML(copy('training.set_done')) + '">&#10003;</button></div>';
      }).join('');
      return '<article class="exercise-card"><div class="ec-head"><span class="ec-name">' +
        escapeHTML(exercise.isim) + '</span><span class="ec-prescribed">' +
        exercise.sets.length + '&times;' + escapeHTML(exercise.tekrar) + '</span></div>' +
        (exercise.not ? '<p class="ec-note">' + escapeHTML(exercise.not) + '</p>' : '') +
        '<div class="set-list">' + rows + '</div></article>';
    }).join('');
    updateProgress();
  }

  function updateProgress() {
    var sets = draft ? draft.exercises.flatMap(function (exercise) {
      return exercise.sets;
    }) : [];
    var done = sets.filter(function (set) { return set.done; }).length;
    document.getElementById('sv-count').textContent = done + '/' + sets.length;
    document.getElementById('sv-progress-bar').style.width =
      (sets.length ? done / sets.length * 100 : 0) + '%';
  }

  function openDraft() {
    if (!todayPlan || todayPlan.tip === 'dinlenme') return;
    try {
      draft = workoutState.contract_version === 2
        ? window.FitXWorkoutDraft.selectWorkoutDraft(
          todayPlan, workoutState.session, draft, Date.now())
        : window.FitXWorkoutDraft.createLegacyWorkoutDraft(todayPlan, Date.now());
    } catch (error) {
      showError(copy('training.progress_unavailable'));
      return;
    }
    document.getElementById('sv-title').textContent =
      todayPlan.odak || copy('training.session');
    document.getElementById('sv-abandon').hidden = !(
      workoutState.contract_version === 2 && workoutState.session &&
      workoutState.session.status === 'active');
    renderDraft();
    setOpen(document.getElementById('session-view'), true);
    document.querySelector('#session-view [data-action="closeSession"]').focus();
  }

  async function startWorkout(element) {
    trigger = element || document.activeElement;
    return window.FitXTrainingFlow.runWorkoutStart(
      workoutState.contract_version,
      function () {
        var session = workoutState.session;
        var url = workoutState.action === 'resume' && session
          ? '/workout/session/' + encodeURIComponent(session.public_id) + '/resume'
          : '/workout/session/start';
        if (workoutState.action !== 'start' && workoutState.action !== 'resume') {
          return Promise.resolve({ ok: false });
        }
        return client.mutate(url, { method: 'POST' });
      },
      openDraft,
    );
  }

  function checkpoint(immediate) {
    return window.FitXTrainingFlow.runWorkoutEdit(
      workoutState.contract_version,
      function () {
        var snapshot = window.FitXWorkoutDraft.buildCheckpointSnapshot(draft, Date.now());
        return immediate
          ? client.flushCheckpoint(snapshot)
          : client.scheduleCheckpoint(snapshot);
      },
    );
  }

  function closeSession() {
    setOpen(document.getElementById('session-view'), false);
    if (workoutState.contract_version !== 2) draft = null;
    if (trigger && trigger.focus) trigger.focus();
  }

  async function abandonWorkout() {
    var session = workoutState && workoutState.session;
    if (!session || session.status !== 'active') return;
    client.stopCheckpointing();
    closeSession();
    draft = null;
    await client.mutate(
      '/workout/session/' + encodeURIComponent(session.public_id) + '/abandon',
      { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'user_abandoned' }) },
    );
  }

  function openCompletion() {
    setOpen(document.getElementById('session-view'), false);
    setOpen(document.getElementById('plan-completion'), true);
    document.getElementById('plan-pump-image').focus();
  }

  function finishSession() {
    return window.FitXTrainingFlow.runWorkoutFinish(
      workoutState.contract_version,
      function () {
        return window.FitXWorkoutDraft.flushWorkoutDraft(client, draft, Date.now());
      },
      openCompletion,
    );
  }

  function cancelWorkoutCompletion() {
    setOpen(document.getElementById('plan-completion'), false);
    setOpen(document.getElementById('session-view'), true);
    document.querySelector('[data-action="finishSession"]').focus();
  }

  function readImage(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(reader.result); };
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  }

  async function submitWorkoutCompletion() {
    var file = document.getElementById('plan-pump-image').files[0];
    if (!file || !file.type || !file.type.startsWith('image/')) {
      showError(copy('training.pump_upload_first'));
      return;
    }
    try {
      var payload = {
        image: await readImage(file),
        location_type: 'Other',
        description: '',
        visibility: 'private',
        shared_friend_ids: [],
      };
      window.FitXTrainingFlow.attachWorkoutCompletion(
        workoutState.contract_version, payload,
        client.getSessionId(), client.getCheckpointRevision());
      var result = await client.mutate('/workout/complete', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!result || (!result.ok && result.body.code !== 'already_completed')) {
        showError((result && result.body && result.body.error) ||
          copy('training.verify_failed'));
        return;
      }
      setOpen(document.getElementById('plan-completion'), false);
      draft = null;
      window.location.reload();
    } catch (error) {
      showError(copy('training.progress_unavailable'));
    }
  }

  document.getElementById('sv-body').addEventListener('input', function (event) {
    var row = event.target.closest('.set-row');
    if (!row || !draft) return;
    var set = draft.exercises[Number(row.dataset.ex)].sets[Number(row.dataset.set)];
    draft.currentExerciseIndex = Number(row.dataset.ex);
    if (event.target.dataset.field === 'weight') {
      set.weightKg = event.target.value === '' ? null : Number(event.target.value);
    } else if (event.target.dataset.field === 'reps') {
      set.reps = event.target.value === '' ? null : Number(event.target.value);
    }
    checkpoint(false);
  });
  document.getElementById('sv-body').addEventListener('click', function (event) {
    var button = event.target.closest('[data-field="done"]');
    if (!button || !draft) return;
    var row = button.closest('.set-row');
    var set = draft.exercises[Number(row.dataset.ex)].sets[Number(row.dataset.set)];
    draft.currentExerciseIndex = Number(row.dataset.ex);
    set.done = !set.done;
    row.classList.toggle('is-done', set.done);
    updateProgress();
    checkpoint(true);
  });

  window.startWorkout = startWorkout;
  window.closeSession = closeSession;
  window.abandonWorkout = abandonWorkout;
  window.finishSession = finishSession;
  window.cancelWorkoutCompletion = cancelWorkoutCompletion;
  window.submitWorkoutCompletion = submitWorkoutCompletion;
  window.addEventListener('pagehide', function () { client.destroy(); });
}());
