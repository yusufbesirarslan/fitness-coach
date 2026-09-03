/* ══════════════════════════════════════════════════════════════════════════
   today.js — Today's read-only hydration (UX-2 PR4).

   Hard rule for this file: it FORMATS, it does not decide.

   Everything that answers "what is my training state" and "what should I do
   next" was resolved on the server and is already in the HTML — the canonical
   `primary_state`, the single primary action, the brief and today's plan
   summary. This file only fills the three values that are cheap to fetch and
   have no bearing on that decision: today's calories, today's water, and the
   last recorded check-in weight.

   What must never appear here:
     * a next-action rule, a time-of-day branch, or a "recommended" anything,
     * a completion / rest-day / plan inference (the server owns all three),
     * `new Date()` used to pick a DAY (the canonical Istanbul day comes from
       the server in `data-today-date`; the browser only formats it),
     * a missing value rendered as 0 — unknown stays "—" and says so.

   Idempotent: a second evaluation of this file is a clean no-op, so a
   bfcache restore or a duplicated include cannot double-fetch.
   ══════════════════════════════════════════════════════════════════════════ */

(function () {
  if (window.__axTodayInit) return;
  window.__axTodayInit = true;

  var __t = window.t || function (k) { return k; };

  function $(id) { return document.getElementById(id); }

  function locale() {
    return window.LOCALE === 'en' ? 'en-US' : 'tr-TR';
  }

  // ── A. The day line ────────────────────────────────────────────────────
  // The date comes from the server's canonical Istanbul day. It is split into
  // parts and rebuilt as a LOCAL Date so the browser's timezone cannot shift
  // it: `new Date('2026-09-03')` parses as UTC and renders as the 2nd in any
  // negative offset. Formatting failure leaves the line empty (the CSS hides
  // it) because a wrong date is worse than no date.
  function renderDate() {
    var el = $('today-date');
    if (!el) return;
    var iso = el.getAttribute('data-today-date') || '';
    var parts = iso.split('-');
    if (parts.length !== 3) return;
    var y = parseInt(parts[0], 10), m = parseInt(parts[1], 10), d = parseInt(parts[2], 10);
    if (!(y > 0 && m > 0 && d > 0)) return;
    try {
      el.textContent = new Date(y, m - 1, d).toLocaleDateString(locale(), {
        weekday: 'long', day: 'numeric', month: 'long'
      });
    } catch (e) {
      el.textContent = '';
    }
  }

  // ── D. Compact status: calories + water ────────────────────────────────
  // Both read canonical endpoints that already exist. Neither value is
  // computed here beyond rounding for display: the calorie target and the
  // remaining budget are the server's canonical nutrition targets, and a user
  // with no configured target gets no invented one (see
  // app/services/nutrition_targets.py — absence is not a number).
  function renderNutrition(meals) {
    var el = $('today-stat-calories');
    if (!el) return;
    var totals = (meals && meals.totals) || {};
    var targets = (meals && meals.targets) || null;
    var consumed = Math.round(totals.kalori || 0);
    if (targets && targets.kalori > 0) {
      el.textContent = consumed + ' / ' + Math.round(targets.kalori) + ' kcal';
    } else {
      // No configured target. Show what was actually eaten and claim nothing
      // about a goal that does not exist.
      el.textContent = consumed + ' kcal';
    }
  }

  function renderWater(water) {
    var el = $('today-stat-water');
    if (!el) return;
    if (!water || typeof water.count !== 'number') return;   // stays "—"
    el.textContent = water.goal
      ? (water.count + ' / ' + water.goal)
      : String(water.count);
  }

  // ── F. Progress signal: the last qualified check-in ────────────────────
  // Weight and its change against the previous check-in — a measurement, not
  // a verdict. Direction is shown with a neutral arrow and never labelled
  // good or bad: this build owns no validated rate-of-gain/loss authority
  // (docs/PROGRESS_SUMMARY.md keeps body weight contextual for that reason).
  function renderProgress(history) {
    var el = $('today-progress-line');
    if (!el) return;
    if (!Array.isArray(history) || history.length === 0) {
      el.textContent = __t('today.progress_empty');
      el.setAttribute('data-empty', '1');
      return;
    }
    el.removeAttribute('data-empty');
    var last = history[history.length - 1];
    var kg = parseFloat(last && last.kilo);
    if (!isFinite(kg)) {
      el.textContent = __t('today.progress_empty');
      el.setAttribute('data-empty', '1');
      return;
    }
    el.textContent = '';
    var value = document.createElement('span');
    value.textContent = __t('today.progress_weight', { kg: kg });
    el.appendChild(value);

    if (history.length < 2) return;
    var prev = parseFloat(history[history.length - 2].kilo);
    if (!isFinite(prev)) return;
    var diff = kg - prev;
    var delta = document.createElement('span');
    delta.className = 'today-progress-delta';
    if (Math.abs(diff) < 0.05) {
      delta.textContent = ' · ' + __t('today.progress_same');
    } else {
      var arrow = diff > 0 ? '↑' : '↓';
      delta.textContent = ' · ' + arrow + ' ' +
        __t('today.progress_delta', { kg: Math.abs(diff).toFixed(1) });
    }
    el.appendChild(delta);
  }

  // ── Loading ────────────────────────────────────────────────────────────
  // The two groups fail INDEPENDENTLY. A broken nutrition read must not blank
  // the progress line and vice versa, and neither may touch the server-
  // rendered brief, primary action or training status above them.
  var ac = ('AbortController' in window) ? new AbortController() : null;
  var sig = ac ? ac.signal : undefined;

  function json(url) {
    return fetch(url, { signal: sig }).then(function (r) {
      if (!r.ok) throw new Error('http');
      return r.json();
    });
  }

  function loadStatus() {
    Promise.all([json('/meal-log/today'), json('/water')])
      .then(function (res) {
        renderNutrition(res[0]);
        renderWater(res[1]);
      })
      .catch(function () {
        // Leave both values as "—" and say so. Unknown is not zero.
        var err = $('today-status-error');
        if (err) err.hidden = false;
      });
  }

  function loadProgress() {
    json('/checkin-history')
      .then(renderProgress)
      .catch(function () {
        var el = $('today-progress-line');
        if (!el) return;
        el.textContent = __t('today.progress_unavailable');
        el.setAttribute('data-empty', '1');
      });
  }

  // ── Weekly reward celebration (behaviour carried over unchanged) ───────
  function rewardModal() {
    var btn = $('reward-btn'), overlay = $('reward-overlay'), body = $('reward-body');
    if (!btn || !overlay || !body) return;
    function dismiss() {
      overlay.classList.remove('open');
      fetch('/leaderboard/reward-dismiss', { method: 'POST' }).catch(function () {});
    }
    json('/leaderboard/reward-check').then(function (d) {
      if (!d || !d.show) return;
      var ordinals = { 1: '1st', 2: '2nd', 3: '3rd' };
      var place = (window.LOCALE === 'en')
        ? (ordinals[d.rank] || (d.rank + 'th'))
        : (d.rank + '.');
      var xp = Number(d.xp).toLocaleString(locale());
      body.textContent = __t('today.reward_body', { place: place, xp: xp });
      overlay.classList.add('open');
      btn.focus();
      btn.addEventListener('click', dismiss);
      overlay.addEventListener('click', function (e) { if (e.target === overlay) dismiss(); });
      document.addEventListener('keydown', function esc(e) {
        if (e.key === 'Escape') { dismiss(); document.removeEventListener('keydown', esc); }
      });
    }).catch(function () {});
  }

  renderDate();
  loadStatus();
  loadProgress();
  rewardModal();
})();
