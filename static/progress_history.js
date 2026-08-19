/* ══════════════════════════════════════════════════════════════════════
   progress_history.js — PROGRESS HISTORY consumer (Progress redesign PR5)

   Hard rule for this file: it TRANSLATES canonical facts, it does not decide.
   The server chose the qualifying check-ins, the analysis day, the trajectory,
   performance, consistency, weight and weight delta. This file never:

     - computes a trajectory / performance / consistency state
     - subtracts two weights
     - infers an analysis window
     - fetches /checkin-history
     - calls Axis Insights or the planner for a historical row

   Every string from the payload is written with textContent. No innerHTML of
   payload fields. Dates are localized from ISO; numbers are formatted.

   progress.js only calls window.FitXProgressHistory.load().
   ══════════════════════════════════════════════════════════════════════ */

(function () {
  var __t = (window.t) || function (k) { return k; };
  var ENDPOINT = '/api/progress/history';

  var TRAJECTORY_LABEL = {
    building_baseline: 'progress.traj_building_baseline',
    on_track: 'progress.traj_on_track',
    needs_attention: 'progress.traj_needs_attention'
  };

  var PERFORMANCE_LABEL = {
    building_baseline: 'progress.perf_state_building_baseline',
    progressing: 'progress.perf_state_progressing',
    steady: 'progress.perf_state_steady',
    building_consistency: 'progress.perf_state_building_consistency',
    plateau: 'progress.perf_state_plateau',
    deload: 'progress.perf_state_deload'
  };

  var CONSISTENCY_LABEL = {
    consistent: 'progress.cons_state_consistent',
    inconsistent: 'progress.cons_state_inconsistent',
    insufficient_data: 'progress.cons_state_insufficient_data'
  };

  var TREND_LABEL = {
    up: 'progress.trend_up',
    flat: 'progress.trend_flat',
    down: 'progress.trend_down'
  };

  var STATES = { empty: true, available: true };

  function _el(id) { return document.getElementById(id); }

  function _text(tag, value, className) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = value == null ? '' : String(value);
    return node;
  }

  function _clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function _label(table, value) {
    return (typeof value === 'string' && table[value]) ? __t(table[value]) : null;
  }

  function _isNumber(v) { return typeof v === 'number' && isFinite(v); }

  function _formatDay(isoDate) {
    // Calendar-day formatting of an ISO date. UTC noon + timeZone UTC so a
    // browser timezone cannot change the analysis day the server published.
    if (typeof isoDate !== 'string' || !/^\d{4}-\d{2}-\d{2}/.test(isoDate)) {
      return isoDate || '';
    }
    var y = +isoDate.slice(0, 4);
    var m = +isoDate.slice(5, 7);
    var d = +isoDate.slice(8, 10);
    try {
      return new Intl.DateTimeFormat(window.LOCALE || 'tr', {
        year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC'
      }).format(new Date(Date.UTC(y, m - 1, d, 12)));
    } catch (e) {
      return isoDate.slice(0, 10);
    }
  }

  function _signed(n) {
    var v = n.toFixed(1);
    return (n > 0 ? '+' : '') + v;
  }

  function _empty(title, desc) {
    var wrap = document.createElement('div');
    wrap.className = 'empty-state';
    wrap.appendChild(_text('div', title, 'empty-title'));
    wrap.appendChild(_text('p', desc, 'empty-desc'));
    return wrap;
  }

  function _unavailable() {
    var box = _el('history-list');
    if (!box) return;
    box.removeAttribute('data-state');
    box.setAttribute('data-status', 'unavailable');
    _clear(box);
    box.appendChild(_text('p', __t('progress.history_unavailable'), 'prog-note'));
  }

  function _toggle(btn, detail) {
    var expanded = btn.getAttribute('aria-expanded') === 'true';
    btn.setAttribute('aria-expanded', expanded ? 'false' : 'true');
    if (expanded) {
      detail.setAttribute('hidden', '');
    } else {
      detail.removeAttribute('hidden');
    }
  }

  function _dlRow(term, value) {
    var wrap = document.createElement('div');
    wrap.className = 'hist-fact';
    wrap.appendChild(_text('dt', term, 'hist-fact-term'));
    wrap.appendChild(_text('dd', value, 'hist-fact-value'));
    return wrap;
  }

  function _renderEntry(entry, index) {
    var item = document.createElement('li');
    item.className = 'hist-item';
    var trajState = (entry.trajectory && entry.trajectory.state) || '';
    var trajLabel = _label(TRAJECTORY_LABEL, trajState);
    if (trajLabel) item.setAttribute('data-state', trajState);

    var detailId = 'hist-detail-' + index;
    var dateLabel = _formatDay(entry.analysis_day);

    var perfLabel = entry.performance ? _label(PERFORMANCE_LABEL, entry.performance.state) : null;
    var consLabel = entry.consistency ? _label(CONSISTENCY_LABEL, entry.consistency.state) : null;
    var secondaryParts = [];
    if (perfLabel) secondaryParts.push(perfLabel);
    if (consLabel) secondaryParts.push(consLabel);

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'hist-trigger';
    btn.setAttribute('aria-expanded', 'false');
    btn.setAttribute('aria-controls', detailId);
    btn.setAttribute('aria-label', __t('progress.history_expand', { date: dateLabel }));

    var summary = document.createElement('div');
    summary.className = 'hist-summary';
    summary.appendChild(_text('span', dateLabel, 'hist-date'));
    summary.appendChild(_text('span', trajLabel || '—', 'hist-traj'));
    if (secondaryParts.length) {
      summary.appendChild(_text('span', secondaryParts.join(' · '), 'hist-meta'));
    }
    var body = entry.body || {};
    if (_isNumber(body.weight_kg)) {
      summary.appendChild(_text(
        'span',
        __t('progress.history_weight', { weight: body.weight_kg.toFixed(1) }),
        'hist-weight'
      ));
    }
    btn.appendChild(summary);

    var detail = document.createElement('div');
    detail.id = detailId;
    detail.className = 'hist-detail';
    detail.setAttribute('hidden', '');

    detail.appendChild(_text('p', __t('progress.history_asof'), 'hist-asof'));

    var facts = document.createElement('dl');
    facts.className = 'hist-facts';

    var window = entry.window || {};
    if (window.start && window.end) {
      var windowLabel = _isNumber(window.weeks)
        ? __t('progress.history_window', { weeks: window.weeks })
        : __t('progress.history_window_range', {
            start: _formatDay(window.start),
            end: _formatDay(window.end)
          });
      var windowRange = __t('progress.history_window_range', {
        start: _formatDay(window.start),
        end: _formatDay(window.end)
      });
      facts.appendChild(_dlRow(windowLabel, windowRange));
    }

    if (perfLabel) {
      var perfValue = perfLabel;
      var trend = entry.performance ? _label(TREND_LABEL, entry.performance.volume_trend) : null;
      if (trend) perfValue = perfLabel + ' · ' + __t('progress.history_volume', { trend: trend });
      facts.appendChild(_dlRow(__t('progress.history_performance'), perfValue));
    }

    if (consLabel) {
      var consValue = consLabel;
      var cons = entry.consistency || {};
      var consBits = [];
      if (_isNumber(cons.sessions)) {
        consBits.push(__t('progress.history_sessions', { n: cons.sessions }));
      }
      if (_isNumber(cons.active_weeks) && _isNumber(cons.analyzed_weeks)) {
        consBits.push(__t('progress.history_weeks_active', {
          active: cons.active_weeks, total: cons.analyzed_weeks
        }));
      }
      if (consBits.length) consValue = consLabel + ' · ' + consBits.join(' · ');
      facts.appendChild(_dlRow(__t('progress.history_consistency'), consValue));
    }

    var bodyValue;
    if (_isNumber(body.weight_kg)) {
      bodyValue = __t('progress.history_weight', { weight: body.weight_kg.toFixed(1) });
      if (_isNumber(body.weight_delta_kg)) {
        bodyValue += ' · ' + __t('progress.history_delta', {
          delta: _signed(body.weight_delta_kg)
        });
      } else {
        bodyValue += ' · ' + __t('progress.history_no_delta');
      }
    } else {
      bodyValue = __t('progress.history_no_weight');
    }
    facts.appendChild(_dlRow(__t('progress.history_body'), bodyValue));
    detail.appendChild(facts);

    btn.addEventListener('click', function () { _toggle(btn, detail); });

    item.appendChild(btn);
    item.appendChild(detail);
    return item;
  }

  function render(payload) {
    var box = _el('history-list');
    if (!box) return;
    if (!payload || !STATES[payload.state]) {
      _unavailable();
      return;
    }

    box.setAttribute('data-state', payload.state);
    box.removeAttribute('data-status');
    _clear(box);

    if (payload.state === 'empty' || !payload.entries || !payload.entries.length) {
      box.appendChild(_empty(
        __t('progress.history_empty_title'),
        __t('progress.history_empty_desc')
      ));
      return;
    }

    var list = document.createElement('ul');
    list.className = 'hist-list';
    for (var i = 0; i < payload.entries.length; i++) {
      list.appendChild(_renderEntry(payload.entries[i], i));
    }
    box.appendChild(list);

    if (payload.has_more) {
      box.appendChild(_text('p', __t('progress.history_has_more'), 'prog-note'));
    }
  }

  function load() {
    var box = _el('history-list');
    if (!box) return;
    fetch(ENDPOINT, { headers: { 'Accept': 'application/json' } })
      .then(function (r) {
        if (!r.ok) throw new Error(String(r.status));
        return r.json();
      })
      .then(render)
      .catch(function () { _unavailable(); });
  }

  window.FitXProgressHistory = { load: load };
})();
