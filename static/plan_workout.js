(function () {
  'use strict';

  var bootstrapNode = document.getElementById('plan-workout-bootstrap');
  if (!bootstrapNode) return;

  var canonical = JSON.parse(bootstrapNode.textContent);
  var workoutState = canonical.workout && canonical.workout.state;
  var todayPlan = canonical.today_plan;
  var draft = null;
  var trigger = null;

  function copy(key, vars) {
    return window.t ? window.t(key, vars) : key;
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
    // UX-3 PR3: this client already re-reads the canonical bootstrap on
    // focus/visibility/mutation. Hand the plan half of that SERVER reading to
    // the Training-management renderer so it can answer "is the rendered
    // program still current?" from the read that just happened rather than
    // issuing a second one. Absent on the server render, whose snapshot carries
    // no plan payload — that render IS the baseline.
    if (typeof window.FitXPlanManageObserve === 'function' && snapshot &&
        snapshot.plan) {
      window.FitXPlanManageObserve(snapshot.plan);
    }
    var previousTodayPlan = todayPlan;
    canonical = snapshot;
    workoutState = snapshot && snapshot.workout && snapshot.workout.state;
    todayPlan = snapshot && snapshot.today_plan;
    if (meta && meta.replaceDraft && reason !== 'server_render') {
      var sessionView = document.getElementById('session-view');
      var wasOpen = sessionView && sessionView.classList.contains('open');
      var session = workoutState && workoutState.session;
      var preservesLegacyDraft = draft && workoutState &&
        workoutState.contract_version === 1 &&
        (workoutState.action === 'start' || workoutState.action === 'resume') &&
        todayPlan && todayPlan.tip !== 'dinlenme' &&
        JSON.stringify(previousTodayPlan) === JSON.stringify(todayPlan);
      if (preservesLegacyDraft) {
        if (wasOpen) document.getElementById('sv-abandon').hidden = true;
      } else if (draft && workoutState && workoutState.contract_version === 2 &&
          session && session.status === 'active' && session.resumable !== false &&
          todayPlan && todayPlan.tip !== 'dinlenme') {
        try {
          focusIndex = null;
          draft = window.FitXWorkoutDraft.createWorkoutDraft(
            todayPlan, session, Date.now());
          if (wasOpen) {
            renderDraft();
            document.getElementById('sv-abandon').hidden = false;
          }
        } catch (error) {
          draft = null;
          if (wasOpen) setOpen(sessionView, false);
          showError(copy('training.progress_unavailable'));
        }
      } else {
        draft = null;
        if (wasOpen) setOpen(sessionView, false);
      }
    }
    syncActionFromCanonical();
  }

  function syncActionFromCanonical() {
    var domain = document.querySelector('[data-plan-domain="training"]');
    var button = document.querySelector('[data-action="startWorkout"]');
    var recovery = document.querySelector('[data-action="recoverBlockedWorkout"]');
    if (!domain) return;
    var action = workoutState && workoutState.action;
    var actionable = action === 'start' || action === 'resume';
    domain.dataset.workoutAction = actionable ? action : 'none';
    if (button) {
      button.hidden = !actionable;
      if (actionable) button.textContent = copy('plan.action.' + action + '_workout');
    }
    if (recovery) recovery.hidden = !(workoutState &&
      workoutState.contract_version === 2 &&
      workoutState.session_state === 'active_blocked' &&
      workoutState.session && workoutState.session.status === 'active');
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

  // Exercise the user explicitly opened on the active surface (presentation
  // only). The persisted cursor stays draft.currentExerciseIndex.
  var focusIndex = null;

  var SET_ICONS = { completed: '&#10003;', active: '&#9679;', upcoming: '&#9675;' };

  function setSummary(set) {
    if (set.weightKg != null && set.reps != null) {
      return copy('training.set_summary', { weight: set.weightKg, reps: set.reps });
    }
    if (set.reps != null) return copy('training.set_summary_reps', { reps: set.reps });
    if (set.weightKg != null) return copy('training.set_summary_weight', { weight: set.weightKg });
    return '';
  }

  function prescription(exercise) {
    return exercise.sets.length + ' &times; ' + escapeHTML(exercise.tekrar);
  }

  function renderSetRow(exerciseIndex, set, setIndex, state) {
    var summary = state === 'active' ? '' : setSummary(set);
    return '<li class="set-row aw-set is-' + state + (set.done ? ' is-done' : '') +
      '" data-ex="' + exerciseIndex + '" data-set="' + setIndex +
      '" data-set-state="' + state + '"' + (state === 'active' ? ' aria-current="step"' : '') + '>' +
      '<span class="aw-set-icon" aria-hidden="true">' + SET_ICONS[state] + '</span>' +
      '<span class="aw-set-name">' + escapeHTML(copy('training.set_label', { n: setIndex + 1 })) +
      '</span><span class="aw-set-state">' + escapeHTML(copy('training.set_state_' + state)) +
      '</span>' + (summary ? '<span class="aw-set-value">' + escapeHTML(summary) + '</span>' : '') +
      (state === 'completed'
        ? '<button class="btn-ghost aw-set-edit" type="button" data-set-action="edit" aria-label="' +
          escapeHTML(copy('training.edit_set_label', { n: setIndex + 1 })) + '">' +
          escapeHTML(copy('training.edit_set')) + '</button>'
        : '') +
      '</li>';
  }

  function renderActiveExercise(exercise, exerciseIndex, setIndex) {
    var total = exercise.sets.length;
    var current = setIndex === -1 ? null : exercise.sets[setIndex];
    var target = prescription(exercise) +
      (exercise.dinlenme ? ' &middot; ' + escapeHTML(copy('training.rest')) + ' ' +
        escapeHTML(exercise.dinlenme) : '');
    var entry = current
      ? '<div class="aw-current" data-ex="' + exerciseIndex + '" data-set="' + setIndex + '">' +
        '<div class="aw-fields">' +
        '<label class="aw-field"><span class="aw-label">' + escapeHTML(copy('training.weight_kg')) +
        '</span><input class="set-input" type="number" inputmode="decimal" min="0" step="0.5"' +
        ' data-field="weight" value="' + (current.weightKg == null ? '' : current.weightKg) + '"></label>' +
        '<label class="aw-field"><span class="aw-label">' + escapeHTML(copy('training.reps')) +
        '</span><input class="set-input" type="number" inputmode="numeric" min="0" step="1"' +
        ' data-field="reps" value="' + (current.reps == null ? '' : current.reps) + '"></label>' +
        '</div><button class="btn-volt w-full aw-complete" type="button" data-set-action="complete">' +
        escapeHTML(copy('training.set_done')) + '</button></div>'
      : '<p class="aw-exercise-done">' + escapeHTML(copy('training.exercise_complete')) + '</p>';
    return '<section class="aw-active" data-ex="' + exerciseIndex +
      '" data-exercise-state="active" aria-labelledby="aw-active-name">' +
      '<h3 class="aw-active-name" id="aw-active-name">' + escapeHTML(exercise.isim) + '</h3>' +
      '<p class="aw-active-set" id="aw-active-set" tabindex="-1">' +
      escapeHTML(current
        ? copy('training.set_of', { n: setIndex + 1, total: total })
        : copy('training.sets_progress', { done: total, total: total })) + '</p>' +
      '<p class="aw-target"><span class="aw-label">' + escapeHTML(copy('training.target')) +
      '</span> ' + target + '</p>' + entry +
      '<ol class="aw-set-list" aria-label="' + escapeHTML(copy('training.sets')) + '">' +
      exercise.sets.map(function (set, index) {
        var state = set.done ? 'completed' : index === setIndex ? 'active' : 'upcoming';
        return renderSetRow(exerciseIndex, set, index, state);
      }).join('') + '</ol>' +
      (exercise.not
        ? '<details class="aw-note"><summary>' + escapeHTML(copy('training.coaching_note')) +
          '</summary><p>' + escapeHTML(exercise.not) + '</p></details>'
        : '') +
      '</section>';
  }

  function renderDraft() {
    var body = document.getElementById('sv-body');
    var view = window.FitXWorkoutDraft.deriveActiveWorkout(draft, focusIndex);
    var parts = [];
    if (view.complete) {
      parts.push('<p class="aw-all-done" role="status">' +
        escapeHTML(copy('training.all_sets_complete')) + '</p>');
    }
    if (view.exerciseIndex !== -1) {
      parts.push(renderActiveExercise(
        draft.exercises[view.exerciseIndex], view.exerciseIndex, view.setIndex));
    }
    if (view.nextExerciseIndex !== -1) {
      var next = draft.exercises[view.nextExerciseIndex];
      parts.push('<p class="aw-next"><span class="aw-label">' + escapeHTML(copy('training.next_up')) +
        '</span> <span class="aw-next-name">' + escapeHTML(next.isim) + '</span> ' +
        '<span class="aw-next-meta">' + prescription(next) + '</span></p>');
    }
    parts.push('<ol class="aw-exercise-list" aria-label="' +
      escapeHTML(copy('training.exercises_label')) + '">' +
      draft.exercises.map(function (exercise, index) {
        var state = view.exerciseStates[index];
        var done = exercise.sets.filter(function (set) { return set.done; }).length;
        var inner = '<span class="aw-set-icon" aria-hidden="true">' + SET_ICONS[state] + '</span>' +
          '<span class="aw-exercise-name">' + escapeHTML(exercise.isim) + '</span>' +
          '<span class="aw-exercise-meta"><span class="aw-set-state">' +
          escapeHTML(copy('training.set_state_' + state)) + '</span> &middot; ' +
          escapeHTML(copy('training.sets_progress', { done: done, total: exercise.sets.length })) +
          '</span>';
        return '<li class="aw-exercise is-' + state + '" data-ex="' + index +
          '" data-exercise-state="' + state + '">' +
          (state === 'active'
            ? '<div class="aw-exercise-row" aria-current="step">' + inner + '</div>'
            : '<button class="aw-exercise-row" type="button" data-open-exercise="' + index + '">' +
              inner + '</button>') +
          '</li>';
      }).join('') + '</ol>');
    body.innerHTML = parts.join('');
    updateProgress(view);
  }

  function updateProgress(view) {
    var done = view.completedSets;
    var total = view.totalSets;
    document.getElementById('sv-count').textContent =
      copy('training.sets_progress', { done: done, total: total });
    document.getElementById('sv-progress-bar').style.width =
      (total ? done / total * 100 : 0) + '%';
    // Complete Set is the primary action until every set is done; only then
    // does Finish Workout take the primary treatment.
    var finish = document.querySelector('#session-view [data-action="finishSession"]');
    finish.classList.toggle('btn-volt', view.complete);
    finish.classList.toggle('btn-ghost', !view.complete);
  }

  function focusActiveSurface() {
    var heading = document.getElementById('aw-active-set');
    var anchor = heading || document.querySelector('#sv-body .aw-all-done');
    // Keep the new active set on screen without opening the keyboard.
    // `nearest` does not move the page when the target is already visible.
    if (anchor && anchor.scrollIntoView) {
      anchor.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }
    var target = heading ||
      document.querySelector('#session-view [data-action="finishSession"]');
    if (target && target.focus) target.focus({ preventScroll: true });
  }

  function openDraft() {
    if (!todayPlan || todayPlan.tip === 'dinlenme') return;
    var previousDraft = draft;
    try {
      draft = workoutState.contract_version === 2
        ? window.FitXWorkoutDraft.selectWorkoutDraft(
          todayPlan, workoutState.session, draft, Date.now())
        : window.FitXWorkoutDraft.createLegacyWorkoutDraft(todayPlan, Date.now());
    } catch (error) {
      showError(copy('training.progress_unavailable'));
      return;
    }
    if (draft !== previousDraft) focusIndex = null;
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
    return await client.mutate(
      '/workout/session/' + encodeURIComponent(session.public_id) + '/abandon',
      { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'user_abandoned' }) },
    );
  }

  async function recoverBlockedWorkout() {
    if (!workoutState || workoutState.session_state !== 'active_blocked' ||
        !workoutState.session || workoutState.session.status !== 'active') return;
    if (!window.confirm(copy('plan.abandon.confirm'))) return;
    try {
      var result = await abandonWorkout();
      if (result && result.ok) {
        window.location.reload();
      } else {
        showError(copy('training.progress_unavailable'));
      }
    } catch (error) {
      showError(copy('training.progress_unavailable'));
    }
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
    var row = event.target.closest('.aw-current');
    if (!row || !draft) return;
    var set = draft.exercises[Number(row.dataset.ex)].sets[Number(row.dataset.set)];
    draft.currentExerciseIndex = Number(row.dataset.ex);
    if (event.target.dataset.field === 'weight') {
      set.weightKg = event.target.value === '' ? null : Number(event.target.value);
    } else if (event.target.dataset.field === 'reps') {
      set.reps = event.target.value === '' ? null : Number(event.target.value);
      // Blank stays eligible for a later copy. A typed number does not.
      set.repsExplicit = set.reps != null;
    }
    checkpoint(false);
  });
  document.getElementById('sv-body').addEventListener('click', function (event) {
    if (!draft) return;
    var open = event.target.closest('[data-open-exercise]');
    if (open) {
      focusIndex = Number(open.dataset.openExercise);
      draft.currentExerciseIndex = focusIndex;
      renderDraft();
      focusActiveSurface();
      checkpoint(false);
      return;
    }
    var button = event.target.closest('[data-set-action]');
    if (!button) return;
    var row = button.closest('[data-set]');
    var exerciseIndex = Number(row.dataset.ex);
    var exercise = draft.exercises[exerciseIndex];
    var set = exercise.sets[Number(row.dataset.set)];
    // Same persisted field the old checkbox toggled; the next active set is
    // derived from it on render, never stored separately.
    set.done = button.dataset.setAction === 'complete';
    // One-tap: lend this set's weight and reps to the immediate next set in
    // this exercise when that set is still missing them. The copy is part of
    // the same checkpoint as completion, so a refresh keeps it.
    if (set.done) {
      window.FitXWorkoutDraft.prepareNextSet(exercise, Number(row.dataset.set));
    }
    if (set.done && exercise.sets.every(function (item) { return item.done; })) {
      focusIndex = null;
      var view = window.FitXWorkoutDraft.deriveActiveWorkout(draft, null);
      draft.currentExerciseIndex = view.exerciseIndex === -1 ? exerciseIndex : view.exerciseIndex;
    } else {
      focusIndex = exerciseIndex;
      draft.currentExerciseIndex = exerciseIndex;
    }
    renderDraft();
    focusActiveSurface();
    checkpoint(true);
  });

  function trapDialogFocus(container) {
    if (!container) return;
    container.addEventListener('keydown', function (event) {
      if (event.key !== 'Tab' || !container.classList.contains('open')) return;
      var focusables = Array.prototype.filter.call(
        container.querySelectorAll(
          'button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
          'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'),
        function (element) { return element.getClientRects().length > 0; }
      );
      if (!focusables.length) return;
      var first = focusables[0];
      var last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
  }

  trapDialogFocus(document.getElementById('session-view'));
  trapDialogFocus(document.getElementById('plan-completion'));
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    var completion = document.getElementById('plan-completion');
    if (completion && completion.classList.contains('open')) {
      event.preventDefault();
      cancelWorkoutCompletion();
      return;
    }
    var session = document.getElementById('session-view');
    if (session && session.classList.contains('open')) {
      event.preventDefault();
      closeSession();
    }
  });

  window.startWorkout = startWorkout;
  window.closeSession = closeSession;
  window.abandonWorkout = abandonWorkout;
  window.recoverBlockedWorkout = recoverBlockedWorkout;
  window.finishSession = finishSession;
  window.cancelWorkoutCompletion = cancelWorkoutCompletion;
  window.submitWorkoutCompletion = submitWorkoutCompletion;
  window.addEventListener('pagehide', function () { client.destroy(); });
}());
