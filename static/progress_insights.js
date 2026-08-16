/* ══════════════════════════════════════════════════════════════════════
   progress_insights.js — AXIS INSIGHTS consumer (Progress redesign PR3)

   Hard rule for this file: it TRANSLATES, it does not decide. The server
   chose which canonical signal earns WHAT'S WORKING / WATCH THIS /
   NEXT MOVE; every code below is looked up in an explicit table and a code
   this build does not know renders the neutral state rather than a guess.

   What must never appear here: `if (sessions >= n)`, `if (volumeTrend ...)`,
   `if (consistency ...)`, `if (trajectory ...) then choose a next move`,
   `if (weightDelta ...)`. Those are decisions and they live on the server.
   tests/test_progress_insights_ui.py enforces that structurally.

   The one number this file computes is a display format: the canonical
   signed fraction from AdaptivePlan (0.05) rendered as a locale percentage
   ("+5%") by Intl.NumberFormat. It formats the server's magnitude; it never
   chooses one, and it never compares one against anything.

   Deliberately a separate file from progress.js. That file carries PR2's
   structural guards ("no arithmetic, no percent, exactly one fractional
   comparison") and this surface needs percentage FORMATTING — so it gets its
   own module with its own equally strict guards instead of loosening those.
   Same pattern as the standalone weekly-program consumer.

   No IIFE for the public entry point: progress.js calls
   window.FitXAxisInsights.load() on first paint and after a check-in.
   ══════════════════════════════════════════════════════════════════════ */

(function () {
  var __t = (window.t) || function (k) { return k; };

  var ENDPOINT = '/api/progress/axis-insights';

  // Slot id → the i18n key for its neutral/empty line. The three slots differ:
  // "nothing positive stands out yet" and "nothing needs your attention" are
  // different statements, and NEXT MOVE is never empty (the planner always has
  // a canonical decision, even if that decision is "build a baseline").
  var SLOTS = {
    working: { id: 'ax-working', empty: 'progress.axis_working_empty',
               insufficient: 'progress.axis_working_insufficient' },
    watch:   { id: 'ax-watch',   empty: 'progress.axis_watch_empty',
               insufficient: 'progress.axis_watch_insufficient' },
    next_move: { id: 'ax-next',  empty: 'progress.axis_next_empty',
                 insufficient: 'progress.axis_next_insufficient' }
  };

  // ── Code → copy tables ──────────────────────────────────────────────
  // One entry per code the server can publish. Kept flat and explicit so
  // tests/test_progress_insights_ui.py can compare them against the Python
  // constants three ways: server enum ↔ client table ↔ locale catalog.
  var WORKING_COPY = {
    training_progressing: 'progress.axis_working_training_progressing',
    training_steady: 'progress.axis_working_training_steady',
    training_consistent: 'progress.axis_working_training_consistent'
  };

  var WATCH_COPY = {
    build_consistency: 'progress.axis_watch_build_consistency',
    deload_due: 'progress.axis_watch_deload_due',
    plateau_detected: 'progress.axis_watch_plateau_detected',
    volume_trend_down: 'progress.axis_watch_volume_trend_down',
    strength_trend_down: 'progress.axis_watch_strength_trend_down'
  };

  var NEXT_MOVE_COPY = {
    build_baseline: 'progress.axis_next_build_baseline',
    prioritize_consistency: 'progress.axis_next_prioritize_consistency',
    deload: 'progress.axis_next_deload',
    maintain_and_consolidate: 'progress.axis_next_maintain_and_consolidate',
    progress_training: 'progress.axis_next_progress_training',
    maintain_current_training: 'progress.axis_next_maintain_current_training'
  };

  var COPY_BY_SLOT = {
    working: WORKING_COPY,
    watch: WATCH_COPY,
    next_move: NEXT_MOVE_COPY
  };

  // ── Helpers ─────────────────────────────────────────────────────────
  function _el(id) { return document.getElementById(id); }

  // Translate a code through its table, or return null when the server sent
  // something this build does not know. Null always degrades to the neutral
  // state — never to a plausible-looking default.
  function _copy(table, code) {
    return (typeof code === 'string' && table[code]) ? __t(table[code]) : null;
  }

  function _isNumber(v) { return typeof v === 'number' && isFinite(v); }

  // Writes one slot. `detail` is optional; passing a falsy value hides the
  // line entirely rather than leaving an empty paragraph in the a11y tree.
  // status and the visible headline are written by the SAME function, so an
  // accent can never appear without the text that carries its meaning.
  function _render(slotId, status, headline, detail) {
    var slot = _el(slotId);
    if (!slot) return;
    var head = slot.querySelector('[data-slot="headline"]');
    var det = slot.querySelector('[data-slot="detail"]');
    slot.setAttribute('data-status', status);
    if (head) head.textContent = headline;
    if (det) {
      det.textContent = detail || '';
      det.hidden = !detail;
    }
  }

  /* The canonical volume adjustment, formatted for display only.

     Intl handles the fraction→percent conversion and the locale's own sign and
     separator conventions, so this file performs no arithmetic of its own: the
     magnitude is entirely the server's (AdaptivePlan.volume_delta_pct) and
     nothing here decides that it is appropriate. A hold (0) renders no line at
     all — "+0%" is noise, not information. */
  function _formatDelta(fraction) {
    if (!_isNumber(fraction) || fraction === 0) return null;
    try {
      return new Intl.NumberFormat(window.LOCALE || 'tr', {
        style: 'percent',
        maximumFractionDigits: 0,
        signDisplay: 'exceptZero'
      }).format(fraction);
    } catch (e) {
      // A runtime without the Intl option set must not take the slot down; the
      // headline already carries the actionable instruction on its own.
      return null;
    }
  }

  // Detail line for an available slot, built ONLY from canonical evidence the
  // server chose to publish. Each branch is keyed on which evidence the server
  // sent, never on a comparison against one of its values.
  function _detailFor(key, slot) {
    var evidence = slot.evidence || {};
    if (key === 'next_move') {
      var action = slot.action || {};
      var delta = _formatDelta(action.volume_delta_pct);
      return delta ? __t('progress.axis_next_volume_delta', { delta: delta }) : null;
    }
    if (_isNumber(evidence.active_weeks) && _isNumber(evidence.analyzed_weeks)) {
      // Both counts or neither: half a sentence ("of the last — weeks") says
      // less than the headline already did. No threshold on either number.
      return __t('progress.axis_active_weeks', {
        active: evidence.active_weeks, total: evidence.analyzed_weeks
      });
    }
    return null;
  }

  // One slot of the payload → the rendered slot.
  function _renderSlot(key, slot) {
    var config = SLOTS[key];
    if (!slot || typeof slot.status !== 'string') {
      _render(config.id, 'empty', __t(config.empty), null);
      return;
    }
    if (slot.status === 'insufficient_data') {
      _render(config.id, 'insufficient_data', __t(config.insufficient), null);
      return;
    }
    if (slot.status === 'available') {
      var headline = _copy(COPY_BY_SLOT[key], slot.code);
      // An unknown code is treated exactly like a missing insight: the client
      // must not invent a sentence for a decision it cannot name.
      if (headline) {
        _render(config.id, 'available', headline, _detailFor(key, slot));
        return;
      }
    }
    _render(config.id, 'empty', __t(config.empty), null);
  }

  /* A failed fetch is NOT an empty slot. "Nothing needs your attention" is a
     real, reassuring product claim, and a broken request says nothing at all
     about the user's training — rendering one as the other would present an
     infrastructure failure as an all-clear. Distinct copy, distinct status. */
  function _unavailable() {
    Object.keys(SLOTS).forEach(function (key) {
      _render(SLOTS[key].id, 'unavailable', __t('progress.axis_unavailable'), null);
    });
  }

  function load() {
    return fetch(ENDPOINT, { headers: { 'Accept': 'application/json' } })
      .then(function (r) {
        if (!r.ok) throw new Error(String(r.status));
        return r.json();
      })
      .then(function (d) {
        if (!d || typeof d !== 'object') { _unavailable(); return; }
        Object.keys(SLOTS).forEach(function (key) { _renderSlot(key, d[key]); });
      })
      .catch(_unavailable);
  }

  window.FitXAxisInsights = { load: load };
})();
