/* Adaptive Weekly Program — frontend initialization boundary (Sprint 6 PR6.1).
 *
 * SCOPE: find the server-rendered mount shell and mark it initialized, exactly once.
 * That is the whole feature in this PR. Loaded ONLY when WEEKLY_PROGRAM_UI_ENABLED is
 * on (templates/training.html renders the tag conditionally), so the OFF path executes
 * nothing at all — this file is not even requested.
 *
 * DELIBERATELY ABSENT, and guarded by tests/test_weekly_program_ui_js.py:
 *   - fetch / XMLHttpRequest / any call to GET /api/training/weekly-program (PR6.2)
 *   - rendering, retries, polling, timers, event listeners
 *   - any planning logic — volume/intensity/deload thresholds, target or weekly-window
 *     arithmetic, week-focus inference, local fallback recommendations. AdaptivePlan is
 *     the single planning authority (docs/TRAINING_PLANNING.md); the browser never
 *     becomes a second one.
 *   - any read of AI_ADAPTIVE_PLAN_CONTEXT — that flag governs the AI coach prompt and
 *     is independent of this UI rollout.
 *
 * PR6.2 extends `window.FitXWeeklyProgram.init`; it is exposed for that reason and
 * because it makes the "initializes once" contract testable. */
(function () {
    'use strict';

    var MOUNT_SELECTOR = '[data-weekly-program-mount]';

    /* Returns true only when THIS call performed the initialization, so a caller (and
     * a test) can tell a first run from a repeat. Idempotent by a marker written on the
     * shell itself rather than a module-local flag: the app's bootstrapping can run more
     * than once (the script is cached and re-evaluated on bfcache restores), and the DOM
     * is the one piece of state shared across those runs. */
    function initWeeklyProgram(root) {
        var scope = root || document;
        var mount = scope.querySelector(MOUNT_SELECTOR);
        if (!mount) return false;                                     // flag OFF → no-op
        if (mount.dataset.weeklyProgramInitialized === '1') return false;  // already done
        mount.dataset.weeklyProgramInitialized = '1';
        return true;
    }

    window.FitXWeeklyProgram = { init: initWeeklyProgram };
    initWeeklyProgram(document);
})();
