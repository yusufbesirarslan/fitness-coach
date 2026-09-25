(function () {
  'use strict';

  var bootstrapNode = document.getElementById('plan-workout-bootstrap');
  if (!bootstrapNode) return;

  var canonical = JSON.parse(bootstrapNode.textContent);
  var workoutState = canonical.workout && canonical.workout.state;
  var todayPlan = canonical.today_plan;
  var draft = null;
  var trigger = null;
  // Server-selected prior actuals, keyed by canonical exercise id. Display
  // only until the user edits or completes the first set.
  var priorPerformance = {};

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
    if (snapshot && snapshot.prior_performance &&
        typeof snapshot.prior_performance === 'object') {
      priorPerformance = snapshot.prior_performance;
    }
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
          if (restTimer && restTimer.sessionId !== draft.sessionId) invalidateTransient();
          if (wasOpen) {
            renderDraft();
            document.getElementById('sv-abandon').hidden = false;
          }
        } catch (error) {
          draft = null;
          invalidateTransient();
          if (wasOpen) setOpen(sessionView, false);
          showError(copy('training.progress_unavailable'));
        }
      } else {
        draft = null;
        invalidateTransient();
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
  // In-memory absolute deadline for the current between-set rest. Not part of
  // the checkpoint. A hard refresh drops it; background time does not.
  var restTimer = null;
  var restTick = null;
  // One Complete Set checkpoint at a time. A second tap while that save is
  // in flight is ignored so it cannot complete the next set as well.
  var completionInFlight = false;
  var completionFlight = null;
  // Presentation only: whether the next-set editor is open during rest, and
  // which exercise's completed-set history the user expanded.
  var restEditOpen = false;
  var historyOpenFor = null;

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

  // "Set X of Y" already says how many sets; the target line only names reps.
  function targetText(exercise) {
    var text = exercise.tekrar == null ? '' : String(exercise.tekrar).trim();
    return /^\d+(\s*[-\u2013]\s*\d+)?$/.test(text)
      ? copy('training.target_reps', { reps: text })
      : text;
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

  function historyFor(exercise) {
    return window.FitXWorkoutDraft.historicalEntry(
      priorPerformance, exercise && exercise.exerciseId);
  }

  function shownSet(exercise, set, setIndex) {
    if (setIndex === 0) {
      return window.FitXWorkoutDraft.presentFirstSet(set, historyFor(exercise));
    }
    return { weightKg: set.weightKg, reps: set.reps };
  }

  function trackTraining(name, params) {
    try {
      if (typeof window.fxTrack !== 'function') return;
      window.fxTrack(name, params || {});
    } catch (error) { /* analytics must not affect the workout */ }
  }

  function clearRest() {
    restTimer = null;
    restEditOpen = false;
    if (restTick != null) {
      clearInterval(restTick);
      restTick = null;
    }
  }

  function removeRestBanner() {
    var node = document.getElementById('aw-rest');
    if (node) node.remove();
  }

  // Rest and the cue live only on restTimer. Drop both, and drop any
  // telemetry still waiting on a completion that no longer belongs to this
  // session.
  function invalidateTransient() {
    if (restTimer) restTimer.suppressTelemetry = true;
    if (completionFlight) completionFlight.cancelled = true;
    completionFlight = null;
    completionInFlight = false;
    clearRest();
    removeRestBanner();
  }

  function captureMutable(set) {
    return {
      done: set.done === true,
      reopened: set.reopened === true,
      weightKg: set.weightKg,
      reps: set.reps,
      repsExplicit: set.repsExplicit === true,
      weightExplicit: set.weightExplicit === true,
      repsTouched: set.repsTouched === true,
    };
  }

  function restoreMutable(set, snap) {
    if (!set || !snap) return;
    set.done = snap.done;
    set.reopened = snap.reopened;
    set.weightKg = snap.weightKg;
    set.reps = snap.reps;
    set.repsExplicit = snap.repsExplicit;
    set.weightExplicit = snap.weightExplicit;
    set.repsTouched = snap.repsTouched;
  }

  function ensureLoggingSeed(set, exercise) {
    if (!set || set.loggingSeed) return;
    set.loggingSeed = window.FitXWorkoutDraft.loggingSeedSource(
      set, historyFor(exercise));
  }

  function releaseRestTelemetry(timer) {
    if (!timer || timer.suppressTelemetry || timer.telemetryReady) return;
    timer.telemetryReady = true;
    trackTraining('training_rest_started', {
      duration_bucket: window.FitXWorkoutDraft.restDurationBucket(timer.durationMs),
    });
    if (timer.cueType && !timer.cueEmitted) {
      timer.cueEmitted = true;
      trackTraining('training_coach_cue_shown', { cue_type: timer.cueType });
    }
    var pending = timer.pendingExtensions || 0;
    timer.pendingExtensions = 0;
    for (var i = 0; i < pending; i++) {
      trackTraining('training_rest_extended', { extension: 'plus_30' });
    }
    if (timer.outcome === 'skipped') trackTraining('training_rest_skipped', {});
    else if (timer.outcome === 'expired') trackTraining('training_rest_expired', {});
  }

  function noteRestExtended(timer) {
    if (!timer || timer.suppressTelemetry || timer.outcome) return;
    if (timer.telemetryReady) {
      trackTraining('training_rest_extended', { extension: 'plus_30' });
      return;
    }
    timer.pendingExtensions = (timer.pendingExtensions || 0) + 1;
  }

  function emitRestTerminal(timer, eventName) {
    if (!eventName || !timer || !timer.telemetryReady || timer.suppressTelemetry) return;
    trackTraining(eventName, {});
  }

  function expireRest() {
    var timer = restTimer;
    if (!timer) return;
    var eventName = window.FitXWorkoutDraft.claimRestTerminal(timer, 'expired');
    clearRest();
    removeRestBanner();
    rerenderOpenDraft();
    emitRestTerminal(timer, eventName);
  }

  function skipRest() {
    var timer = restTimer;
    if (!timer) return;
    var eventName = window.FitXWorkoutDraft.claimRestTerminal(timer, 'skipped');
    clearRest();
    removeRestBanner();
    rerenderOpenDraft();
    emitRestTerminal(timer, eventName);
  }

  // Rest ending swaps REST MODE for ACTIVE SET MODE in place: no stale rest
  // UI, no reload. Focus stays on the set heading, never on an input.
  function rerenderOpenDraft() {
    var sessionView = document.getElementById('session-view');
    if (!draft || !sessionView || !sessionView.classList.contains('open')) return;
    // Removing the rest surface drops focus from its buttons to <body>; the
    // dialog is modal, so hand that focus back to the set heading.
    var active = document.activeElement;
    var hadFocus = !active || active === document.body ||
      !!(active.closest && active.closest('#sv-body'));
    renderDraft();
    if (hadFocus) focusActiveSurface();
  }

  function settleCompletion(flight, result) {
    if (completionFlight !== flight) return;
    completionFlight = null;
    completionInFlight = false;
    if (flight.cancelled) return;
    var ok = !!(result && result.ok === true);
    var persisted = ok || !!(result && (result.disabled === true || result.unchanged === true));
    if (!persisted) {
      if (flight.rest) flight.rest.suppressTelemetry = true;
      if (restTimer === flight.rest) {
        clearRest();
        removeRestBanner();
      }
      if (draft === flight.draft) {
        restoreMutable(flight.set, flight.setSnap);
        restoreMutable(flight.nextSet, flight.nextSnap);
        focusIndex = flight.focusIndex;
        draft.currentExerciseIndex = flight.exerciseCursor;
        renderDraft();
        focusActiveSurface();
      } else if (draft) {
        var sessionView = document.getElementById('session-view');
        if (sessionView && sessionView.classList.contains('open')) renderDraft();
      }
      return;
    }
    var button = document.querySelector('#sv-body [data-set-action="complete"]');
    if (button) button.disabled = false;
    if (!ok || result.unchanged === true || flight.reopened) return;
    trackTraining('training_set_completed', window.FitXWorkoutDraft.buildSetCompletedParams(
      flight.exercisePosition,
      flight.setPosition,
      flight.logging,
      flight.rest != null,
      flight.rest && flight.rest.cueType,
    ));
    if (flight.rest) releaseRestTelemetry(flight.rest);
  }

  function activeRest() {
    if (!restTimer || !draft || draft.sessionId !== restTimer.sessionId) return null;
    var exercise = draft.exercises[restTimer.exerciseIndex];
    var set = exercise && exercise.sets[restTimer.setIndex];
    if (!exercise || !set || set.done === true) return null;
    if (window.FitXWorkoutDraft.remainingRestMs(restTimer.endsAt, Date.now()) <= 0) {
      return null;
    }
    return { exercise: exercise, set: set };
  }

  function ensureRestTick() {
    if (restTick !== null) return;
    restTick = setInterval(paintRestClock, 250);
  }

  function paintRestClock() {
    if (!restTimer) return;
    var remaining = window.FitXWorkoutDraft.remainingRestMs(restTimer.endsAt, Date.now());
    if (remaining <= 0) {
      expireRest();
      return;
    }
    if (!activeRest()) {
      clearRest();
      removeRestBanner();
      rerenderOpenDraft();
      return;
    }
    var clock = document.getElementById('aw-rest-clock');
    if (clock) clock.textContent = window.FitXWorkoutDraft.formatRestClock(remaining);
  }

  function renderRestCue() {
    var cueType = restTimer.cueType;
    if (cueType !== 'below_target' && cueType !== 'on_target' && cueType !== 'above_target') {
      return '';
    }
    var detail = restTimer.cueReps != null && restTimer.cueTarget
      ? '<span class="aw-rest-cue-detail">' + escapeHTML(copy('training.cue_detail', {
        reps: restTimer.cueReps, target: restTimer.cueTarget })) + '</span>'
      : '';
    return '<p class="aw-rest-cue" data-coach-cue="' + cueType + '">' + detail +
      '<span class="aw-rest-cue-label">' + escapeHTML(copy('training.cue_' + cueType)) +
      '</span></p>';
  }

  // REST MODE. `editor` is the next set's weight/reps form: supplied only when
  // the user opened Edit, and then it REPLACES the one-line summary so the
  // values are never shown twice. Complete Set never renders here.
  function renderRestBanner(editor) {
    var rest = activeRest();
    if (!rest) return '';
    var remaining = window.FitXWorkoutDraft.remainingRestMs(restTimer.endsAt, Date.now());
    var canEdit = typeof editor === 'string';
    var next = canEdit && restEditOpen
      ? '<div class="aw-rest-edit">' + editor +
        '<button class="btn-ghost aw-rest-link" type="button" data-rest-action="edit-done"' +
        ' aria-expanded="true">' +
        escapeHTML(copy('training.done')) + '</button></div>'
      : '<p class="aw-rest-next"><span class="aw-label">' + escapeHTML(copy('training.next_set')) +
        '</span> <span class="aw-rest-next-value" id="aw-rest-next-value">' +
        escapeHTML(setSummary(rest.set)) + '</span>' +
        (canEdit
          ? '<button class="btn-ghost aw-rest-link" type="button" data-rest-action="edit"' +
            ' aria-expanded="false" aria-label="' + escapeHTML(copy('training.edit_next_set')) +
            '">' + escapeHTML(copy('training.edit_set')) + '</button>'
          : '') + '</p>';
    return '<div class="aw-rest" id="aw-rest" data-mode="rest">' +
      '<div class="aw-rest-timer" role="timer" aria-label="' + escapeHTML(copy('training.rest')) + '">' +
      '<p class="aw-rest-kicker"><span class="aw-label">' + escapeHTML(copy('training.rest')) +
      '</span></p>' +
      '<p class="aw-rest-clock" id="aw-rest-clock">' +
      escapeHTML(window.FitXWorkoutDraft.formatRestClock(remaining)) + '</p></div>' +
      renderRestCue() +
      next +
      '<div class="aw-rest-actions">' +
      '<button class="btn-ghost aw-rest-link" type="button" data-rest-action="add">' +
      escapeHTML(copy('training.rest_add_30')) + '</button>' +
      '<button class="btn-ghost aw-rest-link" type="button" data-rest-action="skip">' +
      escapeHTML(copy('training.skip_rest')) + '</button></div></div>';
  }

  function onRestAction(action) {
    if (!restTimer) return;
    if (action === 'add') {
      var extended = window.FitXWorkoutDraft.extendRestDeadline(
        restTimer.endsAt, Date.now(), 30000);
      if (extended == null) {
        expireRest();
        return;
      }
      restTimer.endsAt = extended;
      noteRestExtended(restTimer);
      paintRestClock();
      return;
    }
    if (action === 'edit' || action === 'edit-done') {
      restEditOpen = action === 'edit';
      renderDraft();
      var focusTarget = document.querySelector(restEditOpen
        ? '#aw-rest [data-field="weight"]'
        : '#aw-rest [data-rest-action="edit"]');
      if (focusTarget) focusTarget.focus({ preventScroll: true });
      return;
    }
    if (action === 'skip') skipRest();
  }

  function renderEntryFields(exerciseIndex, setIndex, shown) {
    return '<div class="aw-current" data-ex="' + exerciseIndex + '" data-set="' + setIndex + '">' +
      '<div class="aw-fields">' +
      '<label class="aw-field"><span class="aw-label">' + escapeHTML(copy('training.weight_kg')) +
      '</span><input class="set-input" type="number" inputmode="decimal" min="0" step="0.5"' +
      ' data-field="weight" value="' + (shown.weightKg == null ? '' : shown.weightKg) + '"></label>' +
      '<label class="aw-field"><span class="aw-label">' + escapeHTML(copy('training.reps')) +
      '</span><input class="set-input" type="number" inputmode="numeric" min="0" step="1"' +
      ' data-field="reps" value="' + (shown.reps == null ? '' : shown.reps) + '"></label>' +
      '</div>';
  }

  // Completed sets, collapsed behind one line so history never competes with
  // the current set. Every row keeps its Edit action.
  function renderHistory(exercise, exerciseIndex, openByDefault) {
    var rows = [];
    exercise.sets.forEach(function (set, index) {
      if (set.done) rows.push(renderSetRow(exerciseIndex, set, index, 'completed'));
    });
    if (!rows.length) return '';
    var open = historyOpenFor === exerciseIndex ||
      (openByDefault && historyOpenFor !== -1 - exerciseIndex);
    return '<details class="aw-history" data-history-ex="' + exerciseIndex + '"' +
      (open ? ' open' : '') + '><summary class="aw-history-summary">' +
      escapeHTML(rows.length === 1
        ? copy('training.sets_completed_one')
        : copy('training.sets_completed', { n: rows.length })) + '</summary>' +
      '<ol class="aw-set-list" aria-label="' + escapeHTML(copy('training.sets')) + '">' +
      rows.join('') + '</ol></details>';
  }

  function renderActiveExercise(exercise, exerciseIndex, setIndex) {
    var total = exercise.sets.length;
    var current = setIndex === -1 ? null : exercise.sets[setIndex];
    if (current) ensureLoggingSeed(current, exercise);
    var shown = current ? shownSet(exercise, current, setIndex) : null;
    var resting = !!(current && activeRest() && restTimer.exerciseIndex === exerciseIndex &&
      restTimer.setIndex === setIndex);
    var head = '<h3 class="aw-active-name" id="aw-active-name">' + escapeHTML(exercise.isim) + '</h3>' +
      '<p class="aw-active-set" id="aw-active-set" tabindex="-1">' +
      escapeHTML(current
        ? copy('training.set_of', { n: setIndex + 1, total: total })
        : copy('training.sets_progress', { done: total, total: total })) + '</p>';
    if (resting) {
      // REST MODE: wait, or deliberately skip. The next set's values appear
      // once; the weight/reps form only replaces them while Edit is open.
      return '<section class="aw-active is-resting" data-ex="' + exerciseIndex +
        '" data-exercise-state="active" data-mode="rest" aria-labelledby="aw-active-name">' +
        head + renderRestBanner(renderEntryFields(exerciseIndex, setIndex, shown) + '</div>') +
        '</section>';
    }
    var target = targetText(exercise);
    var entry = current
      ? renderEntryFields(exerciseIndex, setIndex, shown) +
        '<button class="btn-volt w-full aw-complete" type="button" data-set-action="complete"' +
        (completionInFlight ? ' disabled' : '') + '>' +
        escapeHTML(copy('training.set_done')) + '</button></div>'
      : '<p class="aw-exercise-done">' + escapeHTML(copy('training.exercise_complete')) + '</p>';
    return '<section class="aw-active" data-ex="' + exerciseIndex +
      '" data-exercise-state="active" data-mode="' + (current ? 'active' : 'review') +
      '" aria-labelledby="aw-active-name">' + head +
      (current && target
        ? '<p class="aw-target"><span class="aw-label">' + escapeHTML(copy('training.target')) +
          '</span> <span class="aw-target-value">' + escapeHTML(target) + '</span></p>'
        : '') +
      entry +
      renderHistory(exercise, exerciseIndex, !current) +
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
    if (restTimer && !activeRest()) clearRest();
    var rest = activeRest();
    if (rest && view.exerciseIndex !== restTimer.exerciseIndex) {
      restEditOpen = false;
      parts.push(renderRestBanner());
    }
    if (rest) ensureRestTick();
    var resting = !!(rest && view.exerciseIndex === restTimer.exerciseIndex &&
      view.setIndex === restTimer.setIndex);
    if (rest && !resting) restEditOpen = false;
    if (view.exerciseIndex !== -1) {
      parts.push(renderActiveExercise(
        draft.exercises[view.exerciseIndex], view.exerciseIndex, view.setIndex));
    }
    body.dataset.mode = resting ? 'rest' : view.complete ? 'done' : 'active';
    if (resting) {
      // The rest surface is the whole task; the workout map returns with it.
      body.innerHTML = parts.join('');
      updateProgress(view);
      return;
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
    // While sets remain, Finish is a quiet text action, not a second button.
    finish.classList.toggle('is-quiet', !view.complete);
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
    if (workoutState.contract_version !== 2) {
      draft = null;
      invalidateTransient();
    }
    if (trigger && trigger.focus) trigger.focus();
  }

  async function abandonWorkout() {
    var session = workoutState && workoutState.session;
    if (!session || session.status !== 'active') return;
    client.stopCheckpointing();
    invalidateTransient();
    closeSession();
    draft = null;
    var result = await client.mutate(
      '/workout/session/' + encodeURIComponent(session.public_id) + '/abandon',
      { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'user_abandoned' }) },
    );
    if (result && result.ok) trackTraining('training_workout_abandoned', {});
    return result;
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
      trackTraining('training_workout_finished', {});
      invalidateTransient();
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
      // A cleared weight stays cleared for this session. History must not
      // refill it on the next render.
      set.weightExplicit = true;
    } else if (event.target.dataset.field === 'reps') {
      set.reps = event.target.value === '' ? null : Number(event.target.value);
      // Blank stays eligible for a later copy. A typed number does not.
      // Touched blanks are still the user's, so history does not refill them.
      set.repsExplicit = set.reps != null;
      set.repsTouched = true;
    }
    var preview = document.getElementById('aw-rest-next-value');
    if (preview && restTimer && Number(row.dataset.ex) === restTimer.exerciseIndex &&
        Number(row.dataset.set) === restTimer.setIndex) {
      preview.textContent = setSummary(set);
    }
    checkpoint(false);
  });
  // <details> toggles do not bubble; capture them so a re-render keeps the
  // user's choice. Pure presentation: no checkpoint, no analytics.
  document.getElementById('sv-body').addEventListener('toggle', function (event) {
    var node = event.target;
    if (!node || !node.matches || !node.matches('details[data-history-ex]')) return;
    var index = Number(node.dataset.historyEx);
    historyOpenFor = node.open ? index : -1 - index;
  }, true);
  document.getElementById('sv-body').addEventListener('click', function (event) {
    if (!draft) return;
    var restButton = event.target.closest('[data-rest-action]');
    if (restButton) {
      onRestAction(restButton.dataset.restAction);
      return;
    }
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
    if (!button || completionInFlight) return;
    if (button.dataset.setAction === 'edit') historyOpenFor = null;
    var row = button.closest('[data-set]');
    var exerciseIndex = Number(row.dataset.ex);
    var setIndex = Number(row.dataset.set);
    var exercise = draft.exercises[exerciseIndex];
    var set = exercise.sets[setIndex];
    if (button.dataset.setAction === 'edit') {
      // Reopening an earlier set is not forward execution. Leave the rest
      // deadline where it is, and remember the reopen so saving it does not
      // start another one.
      set.reopened = true;
      set.done = false;
      focusIndex = exerciseIndex;
      draft.currentExerciseIndex = exerciseIndex;
      renderDraft();
      focusActiveSurface();
      checkpoint(true);
      return;
    }
    if (button.disabled) return;
    var reopened = set.reopened === true;
    var forwardRest = !reopened &&
      window.FitXWorkoutDraft.shouldStartRest(exercise, setIndex);
    var setSnap = captureMutable(set);
    var nextSet = exercise.sets[setIndex + 1] || null;
    var nextSnap = nextSet ? captureMutable(nextSet) : null;
    var savedFocus = focusIndex;
    var savedCursor = draft.currentExerciseIndex;
    ensureLoggingSeed(set, exercise);
    var logging = window.FitXWorkoutDraft.deriveSetLogging(
      set.loggingSeed, set.weightExplicit === true, set.repsTouched === true);
    if (setIndex === 0) {
      window.FitXWorkoutDraft.adoptHistoricalDefault(set, historyFor(exercise));
    }
    // Same persisted field the old checkbox toggled; the next active set is
    // derived from it on render, never stored separately.
    set.done = true;
    set.reopened = false;
    // One-tap: lend this set's weight and reps to the immediate next set in
    // this exercise when that set is still missing them. The copy is part of
    // the same checkpoint as completion, so a refresh keeps it.
    window.FitXWorkoutDraft.prepareNextSet(exercise, setIndex);
    var cueType = window.FitXWorkoutDraft.coachCueForCompletion({
      reopened: reopened,
      forwardRest: forwardRest,
      actualReps: set.reps,
      targetText: exercise.tekrar,
    });
    // A reopened set is not a new rest. Forward completion either starts the
    // next between-set rest or ends the one that just finished.
    var startedRest = null;
    if (!reopened) {
      if (forwardRest) {
        var durationMs = window.FitXWorkoutDraft.deriveRestDurationMs(exercise.dinlenme);
        restTimer = {
          sessionId: draft.sessionId,
          exerciseIndex: exerciseIndex,
          setIndex: setIndex + 1,
          endsAt: Date.now() + durationMs,
          durationMs: durationMs,
          cueType: cueType,
          // Display only: the reps the cue was classified from, and the target.
          cueReps: set.reps,
          cueTarget: exercise.tekrar == null ? '' : String(exercise.tekrar).trim(),
          outcome: null,
          telemetryReady: false,
          suppressTelemetry: false,
          pendingExtensions: 0,
          cueEmitted: false,
        };
        startedRest = restTimer;
        ensureRestTick();
      } else {
        clearRest();
      }
    }
    if (exercise.sets.every(function (item) { return item.done; })) {
      focusIndex = null;
      var view = window.FitXWorkoutDraft.deriveActiveWorkout(draft, null);
      draft.currentExerciseIndex = view.exerciseIndex === -1 ? exerciseIndex : view.exerciseIndex;
    } else {
      focusIndex = exerciseIndex;
      draft.currentExerciseIndex = exerciseIndex;
    }
    if (window.FitXWorkoutDraft.deriveActiveWorkout(draft, null).complete) clearRest();
    if (startedRest && restTimer !== startedRest) {
      startedRest.suppressTelemetry = true;
      startedRest = null;
    }
    var flight = {
      draft: draft,
      set: set,
      setSnap: setSnap,
      nextSet: nextSet,
      nextSnap: nextSnap,
      focusIndex: savedFocus,
      exerciseCursor: savedCursor,
      reopened: reopened,
      rest: startedRest,
      logging: logging,
      exercisePosition: exerciseIndex + 1,
      setPosition: setIndex + 1,
      cancelled: false,
    };
    completionFlight = flight;
    completionInFlight = true;
    renderDraft();
    focusActiveSurface();
    var saved;
    try {
      saved = checkpoint(true);
    } catch (error) {
      settleCompletion(flight, { ok: false });
      return;
    }
    Promise.resolve(saved).then(function (result) {
      settleCompletion(flight, result);
    }).catch(function () {
      settleCompletion(flight, { ok: false });
    });
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
  document.addEventListener('visibilitychange', paintRestClock);
  window.addEventListener('focus', paintRestClock);
  window.addEventListener('pagehide', function () {
    invalidateTransient();
    client.destroy();
  });
}());
