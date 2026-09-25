/* ══════════════════════════════════════════════════════════════════════
   progress_insights.js — AXIS INSIGHT renderer (Progress V2 PR3)

   Hard rule for this file: it RENDERS, it does not decide. The server
   (app/services/progress_insights → `insight`) chose the interpretation,
   the evidence and the one action; static/progress_presentation.js
   (buildAxisInsightView) mapped them to locale keys. This file fetches the
   payload once per load, writes the view into the one surface, and formats
   the planner's volume change for display.

   What must never appear here: `if (sessions >= n)`, `if (volumeTrend ...)`,
   `if (consistency ...)`, `if (trajectory ...) then choose a next move`,
   `if (weightDelta ...)`. Those are decisions and they live on the server.
   tests/test_progress_insights_ui.py enforces that structurally.

   The one number this file touches is a display format: the canonical
   signed fraction from AdaptivePlan (0.05) rendered as a locale percentage
   ("+5%") by Intl.NumberFormat. It formats the server's magnitude; it never
   chooses one, and it never compares one against anything.

   Deliberately a separate file from progress_presentation.js: that module's
   guards forbid percentage handling, and this surface needs percentage
   FORMATTING — so it lives here, under its own equally strict guards.

   Public entry point: progress.js calls window.FitXAxisInsights.load() on
   first paint and after a check-in.
   ══════════════════════════════════════════════════════════════════════ */

(function () {
  var __t = (window.t) || function (k) { return k; };

  var ENDPOINT = '/api/progress/axis-insights';

  function _el(id) { return document.getElementById(id); }

  function _text(desc) {
    return desc ? __t(desc.key, desc.params || undefined) : '';
  }

  function _isNumber(v) { return typeof v === 'number' && isFinite(v); }

  /* The canonical volume adjustment, formatted for display only.

     Intl handles the fraction→percent conversion and the locale's own sign and
     separator conventions, so this file performs no arithmetic of its own: the
     magnitude is entirely the server's (AdaptivePlan.volume_delta_pct). The
     view model already dropped a hold (0) — "+0%" is noise, not information. */
  function _formatDelta(fraction) {
    if (!_isNumber(fraction)) return null;
    try {
      return new Intl.NumberFormat(window.LOCALE || 'tr', {
        style: 'percent',
        maximumFractionDigits: 0,
        signDisplay: 'exceptZero'
      }).format(fraction);
    } catch (e) {
      // A runtime without the Intl option set must not take the surface
      // down; the action line already carries the instruction on its own.
      return null;
    }
  }

  /* One view → the one surface. `data-status` drives quiet styling only; the
     interpretation text always states what the surface is saying (WCAG
     1.4.1). Evidence and the action block are hidden rather than left empty,
     so nothing blank reaches the accessibility tree. */
  function _render(view) {
    var card = _el('ax-card');
    var interp = _el('ax-interpretation');
    var meaning = _el('ax-meaning');
    var list = _el('ax-evidence');
    var action = _el('ax-action');
    var actionText = _el('ax-action-text');
    var detail = _el('ax-action-detail');
    if (!card || !interp) return;

    card.setAttribute('data-status', view.status);
    card.removeAttribute('aria-busy');
    interp.textContent = _text(view.interpretation);
    if (meaning) {
      meaning.textContent = _text(view.meaning);
      meaning.hidden = !view.meaning;
    }

    if (list) {
      var items = (view.evidence || []).map(function (desc) {
        var li = document.createElement('li');
        li.textContent = _text(desc);
        return li;
      });
      list.replaceChildren.apply(list, items);
      list.hidden = !items.length;
    }

    if (action && actionText) {
      var move = view.action;
      actionText.textContent = move ? _text(move.text) : '';
      action.hidden = !move;
      if (detail) {
        var delta = move ? _formatDelta(move.volume_delta) : null;
        detail.textContent = delta ? __t('progress.axis_next_volume_delta', { delta: delta }) : '';
        detail.hidden = !delta;
      }
    }
  }

  function _view(payload) {
    var P = window.FitXProgressPresentation;
    if (!P || typeof P.buildAxisInsightView !== 'function') return null;
    return P.buildAxisInsightView(payload);
  }

  /* A failed fetch is NOT an insight. A broken request says nothing at all
     about the user's training, so it renders its own plain statement — never
     the baseline copy, never an all-clear. */
  function _unavailable() {
    _render(_view(null) || {
      status: 'unavailable',
      interpretation: { key: 'progress.axis_unavailable' },
      meaning: null,
      evidence: [],
      action: null
    });
  }

  function load() {
    return fetch(ENDPOINT, { headers: { 'Accept': 'application/json' } })
      .then(function (r) {
        if (!r.ok) throw new Error(String(r.status));
        return r.json();
      })
      .then(function (d) {
        var view = _view(d);
        if (!view) { _unavailable(); return; }
        _render(view);
      })
      .catch(_unavailable);
  }

  window.FitXAxisInsights = { load: load };
})();
