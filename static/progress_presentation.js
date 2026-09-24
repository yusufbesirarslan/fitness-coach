/* ══════════════════════════════════════════════════════════════════════
   progress_presentation.js — Progress V2 PR1 presentation model

   The ONE place the Progress page turns canonical server state into
   presentation. Pure: no DOM, no fetch, no storage, no clock. Everything
   here is a function of the payload it is handed, which is what lets
   tests/test_progress_presentation_js.py execute it under node.

   Two responsibilities, nothing else:

   1. The state → copy tables. Internal identifiers (`needs_attention`,
      `insufficient_data`, `building_consistency`, ...) are business values
      owned by app/services/progress_summary; they are looked up here and
      mapped to locale KEYS. They are never rendered, never prettified with
      string surgery, and an identifier this build does not know maps to
      nothing — the caller then renders the neutral/unavailable state, never
      a guess. progress.js (controller and its history module) reads these tables,
      so a state has one wording across the whole page.

   2. The summary view model. GET /api/progress/summary is fetched once and
      turned into one view with explicit semantic ownership:

        current_state  — the concise, trajectory-level answer ("how am I
                         doing?"), the window it is based on, nothing more.
                         The signal-specific interpretation (plateau,
                         consistency, deload, ...) is deliberately NOT here:
                         Axis Insight owns interpretation.
        metrics        — the measurable evidence the Trends section renders:
                         weight · training_volume · consistency. Each metric
                         has a status (available | partial |
                         insufficient_data | unavailable), its own facts and
                         the copy descriptors to render them.

   Copy descriptors are `{ key, params }` (or null) — locale keys, never
   prose — so the view model is locale-independent and the renderer is the
   only place that calls the translator.

   Hard rule, same as every other Progress module: this file TRANSLATES, it
   does not decide. No threshold on a count, no weight judgement, no score,
   no percentage. The one numeric comparison is the display tie-break that
   renders a sub-0.05 kg delta as "no change" instead of "+0.0 kg".
   ══════════════════════════════════════════════════════════════════════ */

(function () {
  var AVAILABLE = 'available';
  var PARTIAL = 'partial';
  var INSUFFICIENT = 'insufficient_data';
  var UNAVAILABLE = 'unavailable';

  var WEIGHT_UNIT = 'kg';
  // The weight delta is always "this check-in vs. the previous qualifying
  // one" — that is how app/services/progress_summary derives it.
  var WEIGHT_COMPARISON = 'previous_checkin';

  // ── State → copy tables (the only mapping on the page) ─────────────────

  // trajectory.state → headline. Shared with the history rows.
  var TRAJECTORY = {
    building_baseline: 'progress.traj_building_baseline',
    on_track: 'progress.traj_on_track',
    needs_attention: 'progress.traj_needs_attention'
  };

  // trajectory.state → the one supporting sentence under the headline.
  // Keyed on the TRAJECTORY, not on the signal behind it: which signal needs
  // attention is Axis Insight's to say, so Current State never repeats it.
  var CURRENT_STATE_SUMMARY = {
    building_baseline: 'progress.state_summary_building_baseline',
    on_track: 'progress.state_summary_on_track',
    needs_attention: 'progress.state_summary_needs_attention'
  };

  // performance.state → label. Only the history rows render it; the Trends
  // section shows the measurable volume trend instead.
  var TRAINING_STATE = {
    building_baseline: 'progress.perf_state_building_baseline',
    progressing: 'progress.perf_state_progressing',
    steady: 'progress.perf_state_steady',
    building_consistency: 'progress.perf_state_building_consistency',
    plateau: 'progress.perf_state_plateau',
    deload: 'progress.perf_state_deload'
  };

  // performance.state → whether the volume trend is evidence at all. The
  // canonical trend is "flat" when there is too little history to compare,
  // so without this a brand-new user would read "Steady".
  var TRAINING_VOLUME_AVAILABILITY = {
    building_baseline: INSUFFICIENT,
    progressing: AVAILABLE,
    steady: AVAILABLE,
    building_consistency: AVAILABLE,
    plateau: AVAILABLE,
    deload: AVAILABLE
  };

  // consistency.state → label (Trends card + history rows).
  var CONSISTENCY_STATE = {
    consistent: 'progress.cons_state_consistent',
    inconsistent: 'progress.cons_state_inconsistent',
    insufficient_data: 'progress.cons_state_insufficient_data'
  };

  var CONSISTENCY_AVAILABILITY = {
    consistent: AVAILABLE,
    inconsistent: AVAILABLE,
    insufficient_data: INSUFFICIENT
  };

  // volume_trend → standalone metric value ("Rising").
  var VOLUME_TREND = {
    up: 'progress.metric_volume_up',
    flat: 'progress.metric_volume_flat',
    down: 'progress.metric_volume_down'
  };

  // volume_trend → inline word used inside a history sentence ("rising").
  var TREND_INLINE = {
    up: 'progress.trend_up',
    flat: 'progress.trend_flat',
    down: 'progress.trend_down'
  };

  var BODY_STATUS = { available: AVAILABLE, partial: PARTIAL, insufficient_data: INSUFFICIENT };

  // ── Helpers ────────────────────────────────────────────────────────────

  // Own-property lookup only, so an identifier such as "constructor" can
  // never resolve to something inherited.
  function keyFor(table, value) {
    return (typeof value === 'string' &&
            Object.prototype.hasOwnProperty.call(table, value)) ? table[value] : null;
  }

  function copy(key, params) {
    return key ? { key: key, params: params || null } : null;
  }

  function isNumber(v) { return typeof v === 'number' && isFinite(v); }

  function signed(n) {
    return (n > 0 ? '+' : '') + n.toFixed(1);
  }

  function period(windowInfo) {
    return (windowInfo && isNumber(windowInfo.weeks)) ? { weeks: windowInfo.weeks } : null;
  }

  // ── Current State ──────────────────────────────────────────────────────

  function unavailableCurrentState() {
    return {
      status: UNAVAILABLE,
      state: null,
      headline: copy('progress.traj_unavailable'),
      summary: copy('progress.load_error'),
      evidence: null
    };
  }

  function currentState(d) {
    var trajectory = d.trajectory || {};
    var headline = keyFor(TRAJECTORY, trajectory.state);
    var summary = keyFor(CURRENT_STATE_SUMMARY, trajectory.state);
    if (!headline || !summary) return unavailableCurrentState();
    var when = period(d.window);
    return {
      status: AVAILABLE,
      state: trajectory.state,
      headline: copy(headline),
      summary: copy(summary),
      evidence: when ? copy('progress.state_evidence', when) : null
    };
  }

  // ── Trends: the three measurable signals ───────────────────────────────

  function unavailableMetric(metric) {
    return {
      metric: metric,
      status: UNAVAILABLE,
      value: null,
      detail: copy('progress.card_nodata')
    };
  }

  // Weight — reported, never judged. One piece of context, in order of how
  // directly it was observed: a two-check-in delta, else distance to a
  // configured target, else what is missing.
  function weightMetric(body) {
    if (!body || typeof body !== 'object') return unavailableMetric('weight');
    var current = isNumber(body.current_weight_kg) ? body.current_weight_kg : null;
    var delta = isNumber(body.weight_delta_kg) ? body.weight_delta_kg : null;
    var target = isNumber(body.distance_to_target_kg) ? body.distance_to_target_kg : null;

    // The server's availability ladder, echoed. Without a current weight
    // there is nothing to show whatever the status says.
    var status = current === null ? INSUFFICIENT
      : (keyFor(BODY_STATUS, body.status) || PARTIAL);
    var detail;
    if (current === null) {
      detail = copy('progress.body_sub_none');
    } else if (delta !== null) {
      detail = Math.abs(delta) < 0.05
        ? copy('progress.body_sub_flat')
        : copy('progress.body_sub_delta', { delta: signed(delta) });
    } else if (target !== null) {
      // Absolute distance; which side of the target is not a verdict the
      // server makes, so neither does this file.
      detail = copy('progress.body_sub_target', { distance: target.toFixed(1) });
    } else {
      detail = copy('progress.body_sub_partial');
    }

    return {
      metric: 'weight',
      status: status,
      unit: WEIGHT_UNIT,
      current: current,
      delta: delta,
      comparison_period: delta !== null ? WEIGHT_COMPARISON : null,
      distance_to_target: target,
      value: current === null ? null
        : copy('progress.metric_weight_value', { value: current.toFixed(1) }),
      detail: detail
    };
  }

  // Training volume — the canonical weekly-volume direction over the window.
  function trainingVolumeMetric(perf, windowInfo) {
    var availability = perf ? keyFor(TRAINING_VOLUME_AVAILABILITY, perf.state) : null;
    if (!availability) return unavailableMetric('training_volume');
    var when = period(windowInfo);
    if (availability === INSUFFICIENT) {
      return {
        metric: 'training_volume',
        status: INSUFFICIENT,
        trend: null,
        comparison_period: when,
        value: null,
        detail: copy('progress.metric_volume_insufficient')
      };
    }
    var trendKey = keyFor(VOLUME_TREND, perf.volume_trend);
    if (!trendKey) return unavailableMetric('training_volume');
    return {
      metric: 'training_volume',
      status: AVAILABLE,
      trend: perf.volume_trend,
      comparison_period: when,
      value: copy(trendKey),
      detail: when ? copy('progress.metric_volume_period', when) : null
    };
  }

  // Consistency — the canonical state plus the counts that explain it. The
  // counts are real measured values even with little history (zero weeks
  // trained is a fact, not a gap), so they render whenever they are sent.
  function consistencyMetric(cons, windowInfo) {
    var availability = cons ? keyFor(CONSISTENCY_AVAILABILITY, cons.state) : null;
    if (!availability) return unavailableMetric('consistency');
    var active = isNumber(cons.active_weeks) ? cons.active_weeks : null;
    var total = isNumber(cons.analyzed_weeks) ? cons.analyzed_weeks : null;
    var sessions = isNumber(cons.sessions) ? cons.sessions : null;

    // Both week counts or neither: "of the last — weeks" says less than the
    // state label already did.
    var detail = null;
    if (active !== null && total !== null) {
      detail = sessions !== null
        ? copy('progress.metric_consistency_detail', { active: active, total: total, n: sessions })
        : copy('progress.cons_active_weeks', { active: active, total: total });
    }
    return {
      metric: 'consistency',
      status: availability,
      state: cons.state,
      active_weeks: active,
      total_weeks: total,
      session_count: sessions,
      comparison_period: period(windowInfo),
      value: copy(CONSISTENCY_STATE[cons.state]),
      detail: detail
    };
  }

  // ── The view model ─────────────────────────────────────────────────────

  // A payload that is not a summary at all (failed fetch, malformed body)
  // is "unavailable" everywhere — never "building baseline", which is a
  // truthful claim about the user's history that a failure cannot make.
  function buildSummaryView(d) {
    if (!d || typeof d !== 'object' || !d.trajectory) {
      return {
        current_state: unavailableCurrentState(),
        metrics: {
          weight: unavailableMetric('weight'),
          training_volume: unavailableMetric('training_volume'),
          consistency: unavailableMetric('consistency')
        }
      };
    }
    return {
      current_state: currentState(d),
      metrics: {
        weight: weightMetric(d.body),
        training_volume: trainingVolumeMetric(d.performance, d.window),
        consistency: consistencyMetric(d.consistency, d.window)
      }
    };
  }

  window.FitXProgressPresentation = {
    STATUS: { AVAILABLE: AVAILABLE, PARTIAL: PARTIAL,
              INSUFFICIENT: INSUFFICIENT, UNAVAILABLE: UNAVAILABLE },
    TRAJECTORY: TRAJECTORY,
    CURRENT_STATE_SUMMARY: CURRENT_STATE_SUMMARY,
    TRAINING_STATE: TRAINING_STATE,
    TRAINING_VOLUME_AVAILABILITY: TRAINING_VOLUME_AVAILABILITY,
    CONSISTENCY_STATE: CONSISTENCY_STATE,
    CONSISTENCY_AVAILABILITY: CONSISTENCY_AVAILABILITY,
    VOLUME_TREND: VOLUME_TREND,
    TREND_INLINE: TREND_INLINE,
    keyFor: keyFor,
    buildSummaryView: buildSummaryView
  };
})();
