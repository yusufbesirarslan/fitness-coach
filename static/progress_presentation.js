/* ══════════════════════════════════════════════════════════════════════
   progress_presentation.js — Progress V2 presentation model (PR1 + PR2)

   The ONE place the Progress page turns canonical server state into
   presentation. Pure: no DOM, no fetch, no storage, no clock. Everything
   here is a function of the payload (and the display locale) it is handed,
   which is what lets tests/test_progress_presentation_js.py execute it
   under node.

   Three responsibilities, nothing else:

   1. The state → copy tables. Internal identifiers (`needs_attention`,
      `insufficient_data`, `building_consistency`, ...) are business values
      owned by app/services/progress_summary; they are looked up here and
      mapped to locale KEYS. They are never rendered, never prettified with
      string surgery, and an identifier this build does not know maps to
      nothing — the caller then renders the neutral/unavailable state, never
      a guess. progress.js (controller and its history module) reads these
      tables, so a state has one wording across the whole page.

   2. The summary view model. GET /api/progress/summary is fetched once and
      turned into one view with explicit semantic ownership:

        current_state  — STATE → EVIDENCE → ACTION at trajectory level: the
                         window, the headline, one summary line, at most two
                         measured evidence facts and one next-move line keyed
                         on the TRAJECTORY. Which signal drives the state is
                         deliberately NOT here: Axis Insight owns
                         interpretation, and the next move hands off to it.
        metrics        — the measurable evidence the Trends section renders:
                         weight · training_volume · consistency. Each metric
                         has a status (available | partial |
                         insufficient_data | unavailable), its own facts, and
                         the copy + visualization descriptors to render them:
                         value · change (with a direction) · viz · note · meta.

   3. Visualization GEOMETRY (V2 PR2). Sparkline points and bar rectangles
      are computed here, in a fixed SVG viewBox, from series the server
      published (body.weight_series, weekly[]). Nothing is interpolated,
      padded or smoothed; a series too short to be a trend yields NO viz and
      an explicit note instead — never a fake flat line or an empty frame.

   Copy descriptors are `{ key, params }` (or null) — locale keys, never
   prose — so the view model is locale-independent. The only locale-aware
   step is number formatting (Intl.NumberFormat for volume), which is why
   buildSummaryView takes an optional { locale }.

   Hard rule, same as every other Progress module: this file TRANSLATES, it
   does not decide. No threshold on a count, no weight judgement, no score,
   no percentage. The one numeric comparison is the display tie-break that
   renders a sub-0.05 kg delta as "no change" instead of "+0.0 kg"; a trend
   direction glyph follows the SIGN of a server delta or the canonical
   volume_trend — it is never coloured as good or bad.
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

  // A line through fewer points than this restates the delta rather than
  // showing a trend, so the Weight card says "not enough check-ins" instead.
  // A display rule about drawing a line, not a verdict about the user.
  var MIN_LINE_POINTS = 3;

  // Fixed SVG geometry. The renderer scales the viewBox with CSS; nothing
  // is measured at runtime, so there is no resize work at all.
  var VIEW_W = 120;
  var VIEW_H = 36;
  var PAD = 3;

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

  // trajectory.state → the recommended next move. Trajectory-level on
  // purpose: the specific adjustment is Axis Insight's NEXT MOVE, so
  // `needs_attention` hands off to it rather than restating it.
  var CURRENT_STATE_NEXT = {
    building_baseline: 'progress.state_next_building_baseline',
    on_track: 'progress.state_next_on_track',
    needs_attention: 'progress.state_next_needs_attention'
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

  // volume_trend → standalone metric value ("Rising"). Used only when the
  // payload carries no weekly series (a pre-PR2 server): the honest
  // semantic fallback.
  var VOLUME_TREND = {
    up: 'progress.metric_volume_up',
    flat: 'progress.metric_volume_flat',
    down: 'progress.metric_volume_down'
  };

  // volume_trend → the Trends change line ("Rising across 4 weeks").
  var VOLUME_CHANGE = {
    up: 'progress.metric_volume_change_up',
    flat: 'progress.metric_volume_change_flat',
    down: 'progress.metric_volume_change_down'
  };

  // volume_trend → Current State evidence fact.
  var STATE_FACT_VOLUME = {
    up: 'progress.state_fact_volume_up',
    flat: 'progress.state_fact_volume_flat',
    down: 'progress.state_fact_volume_down'
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

  // Sign of a server-published delta → direction glyph. Not a verdict: a
  // falling weight is neither good nor bad here.
  function direction(n) {
    if (n > 0) return 'up';
    if (n < 0) return 'down';
    return 'flat';
  }

  function round1(n) { return Number(n.toFixed(1)); }

  // Volume is formatted compactly ("24.8K") in the display locale. The
  // number itself is the server's; only its spelling happens here.
  function formatVolume(n, locale) {
    try {
      return new Intl.NumberFormat(locale || 'en', {
        notation: 'compact', maximumFractionDigits: 1
      }).format(n);
    } catch (e) {
      return String(Math.round(n));
    }
  }

  // ── Visualization geometry (pure) ──────────────────────────────────────

  // values (chronological) → polyline points inside the fixed viewBox. Equal
  // values draw a level line: with MIN_LINE_POINTS real observations that
  // IS the trend. Returns null for anything that is not a drawable series.
  function sparkline(values) {
    if (!values || values.length < MIN_LINE_POINTS) return null;
    for (var i = 0; i < values.length; i++) {
      if (!isNumber(values[i])) return null;
    }
    var min = Math.min.apply(null, values);
    var max = Math.max.apply(null, values);
    var span = max - min;
    var stepX = (VIEW_W - 2 * PAD) / (values.length - 1);
    var points = values.map(function (v, idx) {
      var y = span === 0 ? VIEW_H / 2
        : PAD + ((max - v) / span) * (VIEW_H - 2 * PAD);
      return { x: round1(PAD + idx * stepX), y: round1(y) };
    });
    return { width: VIEW_W, height: VIEW_H, points: points };
  }

  // values (chronological, >= 0) → one bar per value, bottom-aligned. A zero
  // week is a real measured zero, so it keeps its slot and is flagged, not
  // dropped. An all-zero series has nothing to compare and yields null.
  function bars(values) {
    if (!values || !values.length) return null;
    for (var i = 0; i < values.length; i++) {
      if (!isNumber(values[i]) || values[i] < 0) return null;
    }
    var max = Math.max.apply(null, values);
    if (max === 0) return null;
    var gap = 4;
    var slot = VIEW_W / values.length;
    var width = round1(slot - gap);
    var rects = values.map(function (v, idx) {
      var h = round1((v / max) * (VIEW_H - PAD));
      return { x: round1(idx * slot + gap / 2), y: round1(VIEW_H - h),
               width: width, height: h, zero: v === 0 };
    });
    return { width: VIEW_W, height: VIEW_H, bars: rects };
  }

  // ── The window's weekly series (payload.weekly, oldest first) ──────────

  function weeklySeries(d) {
    var weekly = d && d.weekly;
    if (!Array.isArray(weekly) || !weekly.length) return null;
    for (var i = 0; i < weekly.length; i++) {
      var w = weekly[i];
      if (!w || typeof w !== 'object' || typeof w.active !== 'boolean' ||
          !isNumber(w.volume_kg) || !isNumber(w.sessions)) return null;
    }
    return weekly;
  }

  // ── Current State ──────────────────────────────────────────────────────

  function unavailableCurrentState() {
    return {
      status: UNAVAILABLE,
      state: null,
      window: null,
      headline: copy('progress.traj_unavailable'),
      summary: copy('progress.load_error'),
      evidence: [],
      next_action: null
    };
  }

  // At most two MEASURED facts, in a fixed order: how many weeks were
  // trained, then which way weekly volume moved. Each appears only when its
  // source is real; neither is chosen by a rule about the user's data.
  function currentStateEvidence(d) {
    var facts = [];
    var cons = d.consistency || {};
    if (keyFor(CONSISTENCY_AVAILABILITY, cons.state) &&
        isNumber(cons.active_weeks) && isNumber(cons.analyzed_weeks)) {
      facts.push(copy('progress.state_fact_weeks',
                      { active: cons.active_weeks, total: cons.analyzed_weeks }));
    }
    var perf = d.performance || {};
    if (keyFor(TRAINING_VOLUME_AVAILABILITY, perf.state) === AVAILABLE) {
      var fact = keyFor(STATE_FACT_VOLUME, perf.volume_trend);
      if (fact) facts.push(copy(fact));
    }
    return facts;
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
      window: when ? copy('progress.state_window', when) : null,
      headline: copy(headline),
      summary: copy(summary),
      evidence: currentStateEvidence(d),
      next_action: copy(keyFor(CURRENT_STATE_NEXT, trajectory.state))
    };
  }

  // ── Trends: the three measurable signals ───────────────────────────────

  function unavailableMetric(metric) {
    return {
      metric: metric,
      status: UNAVAILABLE,
      value: null,
      unit_label: null,
      change: null,
      viz: null,
      note: copy('progress.card_nodata'),
      meta: []
    };
  }

  function weightPoints(body) {
    var series = Array.isArray(body.weight_series) ? body.weight_series : [];
    var out = [];
    for (var i = 0; i < series.length; i++) {
      var p = series[i];
      if (p && isNumber(p.weight_kg)) out.push(p.weight_kg);
    }
    return out;
  }

  // Weight — reported, never judged. Value, the server's two-check-in delta
  // as the change, and a sparkline over the qualifying check-ins when there
  // are enough of them to be a trend.
  function weightMetric(body) {
    if (!body || typeof body !== 'object') return unavailableMetric('weight');
    var current = isNumber(body.current_weight_kg) ? body.current_weight_kg : null;
    var delta = isNumber(body.weight_delta_kg) ? body.weight_delta_kg : null;
    var target = isNumber(body.distance_to_target_kg) ? body.distance_to_target_kg : null;

    // The server's availability ladder, echoed. Without a current weight
    // there is nothing to show whatever the status says.
    var status = current === null ? INSUFFICIENT
      : (keyFor(BODY_STATUS, body.status) || PARTIAL);

    var change = null;
    if (current !== null && delta !== null) {
      change = Math.abs(delta) < 0.05
        ? { text: copy('progress.body_sub_flat'), direction: 'flat' }
        : { text: copy('progress.body_sub_delta', { delta: signed(delta) }),
            direction: direction(delta) };
    }

    var points = current === null ? [] : weightPoints(body);
    var line = sparkline(points);
    var viz = null;
    var note = null;
    var meta = [];
    if (current === null) {
      note = copy('progress.body_sub_none');
    } else if (line) {
      viz = {
        kind: 'line',
        width: line.width,
        height: line.height,
        points: line.points,
        label: copy('progress.metric_weight_series_sr', {
          n: points.length,
          first: points[0].toFixed(1),
          last: points[points.length - 1].toFixed(1)
        })
      };
      meta.push(copy('progress.metric_weight_period', { n: points.length }));
    } else {
      note = copy('progress.metric_weight_no_trend');
    }
    // Absolute distance; which side of the target is not a verdict the
    // server makes, so neither does this file.
    if (current !== null && target !== null) {
      meta.push(copy('progress.body_sub_target', { distance: target.toFixed(1) }));
    }

    return {
      metric: 'weight',
      status: status,
      unit: WEIGHT_UNIT,
      current: current,
      delta: delta,
      comparison_period: delta !== null ? WEIGHT_COMPARISON : null,
      distance_to_target: target,
      series_points: line ? points.length : 0,
      value: current === null ? null
        : copy('progress.metric_weight_value', { value: current.toFixed(1) }),
      unit_label: null,
      change: change,
      viz: viz,
      note: note,
      meta: meta
    };
  }

  // Training volume — the latest trailing week's real volume, the canonical
  // weekly-volume direction as the change, and the window's weekly totals as
  // bars. Without a weekly series the canonical direction alone is rendered
  // (semantic fallback); no number is ever invented for it.
  function trainingVolumeMetric(perf, windowInfo, weekly, locale) {
    var availability = perf ? keyFor(TRAINING_VOLUME_AVAILABILITY, perf.state) : null;
    if (!availability) return unavailableMetric('training_volume');
    var when = period(windowInfo);
    var latest = weekly ? weekly[weekly.length - 1].volume_kg : null;
    var value = latest === null ? null
      : copy('progress.metric_volume_value', { value: formatVolume(latest, locale) });
    var meta = latest === null ? [] : [copy('progress.metric_volume_latest')];

    if (availability === INSUFFICIENT) {
      return {
        metric: 'training_volume',
        status: INSUFFICIENT,
        trend: null,
        latest_kg: latest,
        comparison_period: when,
        value: value,
        unit_label: null,
        change: null,
        viz: null,
        note: copy('progress.metric_volume_insufficient'),
        meta: meta
      };
    }
    var trendKey = keyFor(VOLUME_TREND, perf.volume_trend);
    if (!trendKey) return unavailableMetric('training_volume');
    var trend = perf.volume_trend;

    if (latest === null) {
      // Semantic fallback: the direction is all that is known.
      return {
        metric: 'training_volume',
        status: AVAILABLE,
        trend: trend,
        latest_kg: null,
        comparison_period: when,
        value: copy(trendKey),
        unit_label: null,
        change: null,
        viz: null,
        note: null,
        meta: []
      };
    }

    var volumes = weekly.map(function (w) { return w.volume_kg; });
    var shape = bars(volumes);
    return {
      metric: 'training_volume',
      status: AVAILABLE,
      trend: trend,
      latest_kg: latest,
      comparison_period: when,
      value: value,
      unit_label: null,
      change: when ? { text: copy(VOLUME_CHANGE[trend], when), direction: trend } : null,
      viz: shape ? {
        kind: 'bars',
        width: shape.width,
        height: shape.height,
        bars: shape.bars,
        label: copy('progress.metric_volume_bars_sr', {
          values: volumes.map(function (v) { return formatVolume(v, locale); }).join(', ')
        })
      } : null,
      note: null,
      meta: meta
    };
  }

  // Consistency — the counts ARE the value ("2 / 4 weeks active"); the
  // canonical state is the change line; each week of the window is one
  // cell. The counts are real measured values even with little history
  // (zero weeks trained is a fact, not a gap), so they render when sent.
  function consistencyMetric(cons, windowInfo, weekly) {
    var availability = cons ? keyFor(CONSISTENCY_AVAILABILITY, cons.state) : null;
    if (!availability) return unavailableMetric('consistency');
    var active = isNumber(cons.active_weeks) ? cons.active_weeks : null;
    var total = isNumber(cons.analyzed_weeks) ? cons.analyzed_weeks : null;
    var sessions = isNumber(cons.sessions) ? cons.sessions : null;
    var counted = active !== null && total !== null;
    var label = copy(CONSISTENCY_STATE[cons.state]);

    // One cell per analysed week, only when the series covers exactly the
    // weeks the counts describe — otherwise the cells could contradict them.
    var viz = null;
    if (counted && weekly && weekly.length === total) {
      viz = {
        kind: 'weeks',
        label: copy('progress.metric_weeks_label'),
        cells: weekly.map(function (w, idx) {
          return {
            active: w.active,
            label: copy(w.active ? 'progress.metric_week_active'
                                 : 'progress.metric_week_inactive', { n: idx + 1 })
          };
        })
      };
    }

    var meta = [];
    if (sessions !== null && total !== null) {
      meta.push(copy('progress.metric_consistency_sessions', { n: sessions, total: total }));
    }

    return {
      metric: 'consistency',
      status: availability,
      state: cons.state,
      active_weeks: active,
      total_weeks: total,
      session_count: sessions,
      comparison_period: period(windowInfo),
      // Both week counts or neither: "— / 4" says less than the state label.
      value: counted ? copy('progress.metric_consistency_value', { active: active, total: total })
                     : label,
      unit_label: counted ? copy('progress.metric_consistency_unit') : null,
      change: counted ? { text: label, direction: null } : null,
      viz: viz,
      note: null,
      meta: meta
    };
  }

  // ── The view model ─────────────────────────────────────────────────────

  // A payload that is not a summary at all (failed fetch, malformed body)
  // is "unavailable" everywhere — never "building baseline", which is a
  // truthful claim about the user's history that a failure cannot make.
  function buildSummaryView(d, options) {
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
    var locale = options && options.locale;
    var weekly = weeklySeries(d);
    return {
      current_state: currentState(d),
      metrics: {
        weight: weightMetric(d.body),
        training_volume: trainingVolumeMetric(d.performance, d.window, weekly, locale),
        consistency: consistencyMetric(d.consistency, d.window, weekly)
      }
    };
  }

  window.FitXProgressPresentation = {
    STATUS: { AVAILABLE: AVAILABLE, PARTIAL: PARTIAL,
              INSUFFICIENT: INSUFFICIENT, UNAVAILABLE: UNAVAILABLE },
    TRAJECTORY: TRAJECTORY,
    CURRENT_STATE_SUMMARY: CURRENT_STATE_SUMMARY,
    CURRENT_STATE_NEXT: CURRENT_STATE_NEXT,
    TRAINING_STATE: TRAINING_STATE,
    TRAINING_VOLUME_AVAILABILITY: TRAINING_VOLUME_AVAILABILITY,
    CONSISTENCY_STATE: CONSISTENCY_STATE,
    CONSISTENCY_AVAILABILITY: CONSISTENCY_AVAILABILITY,
    VOLUME_TREND: VOLUME_TREND,
    VOLUME_CHANGE: VOLUME_CHANGE,
    STATE_FACT_VOLUME: STATE_FACT_VOLUME,
    TREND_INLINE: TREND_INLINE,
    MIN_LINE_POINTS: MIN_LINE_POINTS,
    keyFor: keyFor,
    sparkline: sparkline,
    bars: bars,
    buildSummaryView: buildSummaryView
  };
})();
