/* ══════════════════════════════════════════════════════════════════════
   progress.js — Progress redesign PR1 (shell) + PR2 (canonical summary)

   showToast / escapeHTML / selectOverload / submitCheckin / openCheckin /
   closeCheckin / activateOnEnter are preserved VERBATIM — the weekly
   Check-In POST flow must keep working unchanged.

   Hard rule for this file: it TRANSLATES, it does not decide. YOUR PROGRESS,
   BODY, PERFORMANCE and CONSISTENCY all render one canonical server payload
   (GET /api/progress/summary); there is no `sessions >= 3 → on track`,
   no `weight dropped → good`, no streak-as-consistency, and no threshold of
   any kind below. Every state arrives as a bounded enum and is looked up in
   a table — an enum this file does not know renders the neutral state rather
   than a guess, exactly like a missing signal.

   No IIFE: functions must resolve as window.<name> for actions.js's
   data-action dispatcher (see static/actions.js).
   ══════════════════════════════════════════════════════════════════════ */

var __t = (window.t) || function (k) { return k; };

// ── TOAST ── (verbatim)
function showToast(msg, type = 'info') {
    const icons = { success: '✓', error: '✗', info: 'ℹ' };
    const wrap = document.getElementById('toast-wrap');
    const t = document.createElement('div');
    t.className = `toast toast-${type}`;
    t.innerHTML = `<span class="toast-icon">${icons[type] || 'ℹ'}</span><span>${msg}</span>`;
    wrap.appendChild(t);
    setTimeout(() => { t.classList.add('hide'); setTimeout(() => t.remove(), 280); }, 3200);
}

// Escape untrusted strings before they touch innerHTML (XSS koruması). (verbatim)
function escapeHTML(str) {
    const d = document.createElement('div');
    d.textContent = str == null ? '' : String(str);
    return d.innerHTML;
}

// ── OVERLOAD ── (verbatim)
let selectedOverload = 'evet';
function selectOverload(val, el) {
    selectedOverload = val;
    document.querySelectorAll('.overload-chip').forEach(c => {
        c.classList.remove('selected');
        c.setAttribute('aria-pressed', 'false');
    });
    el.classList.add('selected');
    el.setAttribute('aria-pressed', 'true');
}

// ── CHECK-IN ── (verbatim: POST /checkin, coach_feedback escape, CW hand-off)
async function submitCheckin() {
    const weight = document.getElementById('ci-weight').value;
    if (!weight) { showToast(__t('progress.weight_required'), 'error'); return; }

    const btn = document.getElementById('checkin-btn');
    btn.classList.add('loading');
    btn.textContent = __t('progress.sending');

    try {
        const res = await fetch('/checkin', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                weight: parseFloat(weight),
                yogunluk:       parseInt(document.getElementById('ci-yogunluk').value),
                fatigue:        parseInt(document.getElementById('ci-fatigue').value),
                uyku_kalitesi:  parseInt(document.getElementById('ci-uyku').value),
                beslenme_uyumu: parseInt(document.getElementById('ci-beslenme').value),
                progressive_overload: selectedOverload,
                note: document.getElementById('ci-note').value
            })
        });
        const data = await res.json();
        if (data.error) { showToast(data.error, 'error'); return; }

        const fb = document.getElementById('feedback-card');
        // AI çıktısı güvenilmez: HTML entity'lerini escape et, sonra satır
        // sonlarını <br>'e çevir (XSS koruması).
        const safeFeedback = (data.coach_feedback || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/\n/g, '<br>');
        document.getElementById('feedback-text').innerHTML = safeFeedback;
        fb.classList.add('visible');
        fb.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        showToast(__t('progress.checkin_saved'), 'success');
        if (window.CW) window.CW.receiveCheckinFeedback(data.coach_feedback);
        // A fresh check-in changes Body/Consistency and adds a history row —
        // repaint the data-driven sections so the page stays truthful without
        // a manual reload.
        loadProgress();
    } catch (err) {
        showToast(__t('progress.error_prefix') + err.message, 'error');
    } finally {
        btn.classList.remove('loading');
        btn.textContent = __t('progress.submit_checkin');
    }
}

// Keyboard activation for non-native "button" elements (overload chips are
// plain divs with tabindex=0 + role=button): data-action-keydown fires on
// EVERY keydown while focused, so this only forwards Enter/Space to the
// element's own click handler (data-action="selectOverload") — anything
// else (Tab, arrows, ...) is a no-op and keeps its default behavior.
//
// The element and event MUST be read off the END of the argument list.
// actions.js dispatches as fn.apply(el, dataArgs.concat([el, event])), and the
// chips also carry data-args ('["kismen"]') for their click action — those
// values are prepended to EVERY handler on the element, so fixed positional
// parameters (el, e) landed on the string "kismen" and the element instead.
// `e.key` was then undefined, the guard returned early, and the chips were
// focusable but not operable by keyboard (role=button advertised to assistive
// tech with no keyboard behavior behind it).
function activateOnEnter() {
  var e = arguments[arguments.length - 1];
  var el = arguments[arguments.length - 2];
  if (!e || !el || (e.key !== 'Enter' && e.key !== ' ')) return;
  e.preventDefault();
  el.click();
}

// ── CHECK-IN SHEET ──
// Focus-on-open (first field) / focus-return-on-close (opener button) +
// Esc-to-close for keyboard/screen-reader users.
var _checkinOpener = null;
function openCheckin(btn) {
  _checkinOpener = (btn && typeof btn.focus === 'function') ? btn : document.activeElement;
  document.getElementById('checkin-sheet').classList.add('open');
  var first = document.getElementById('ci-weight');
  if (first) { try { first.focus({ preventScroll: true }); } catch (e) { first.focus(); } }
}
function closeCheckin() {
  document.getElementById('checkin-sheet').classList.remove('open');
  var opener = _checkinOpener;
  _checkinOpener = null;
  if (opener && typeof opener.focus === 'function') {
    try { opener.focus({ preventScroll: true }); } catch (e) { opener.focus(); }
  }
}
document.addEventListener('keydown', function (e) {
  if (e.key !== 'Escape') return;
  var sheet = document.getElementById('checkin-sheet');
  if (sheet && sheet.classList.contains('open')) closeCheckin();
});

/* ══════════════════════════════════════════════════════════════════════
   REDESIGNED SECTIONS
   ══════════════════════════════════════════════════════════════════════ */

// Small helpers ───────────────────────────────────────────────────────
function _el(id) { return document.getElementById(id); }

// Signed number for a delta ("+0.4" / "-1.2"). Callers only pass values they
// already proved are finite numbers.
function _signed(n, digits) {
  var v = n.toFixed(digits == null ? 1 : digits);
  return (n > 0 ? '+' : '') + v;
}

// Fills one WHAT CHANGED card. `sub` is optional; when a card has no signal
// the caller passes the neutral copy so the card never renders blank.
function _fillCard(id, value, sub) {
  var card = _el(id);
  if (!card) return;
  var v = card.querySelector('[data-slot="value"]');
  var s = card.querySelector('[data-slot="sub"]');
  if (v) v.textContent = value;
  if (s) s.textContent = sub;
}

// Renders a components.css `.empty-state` block — used wherever a section has
// nothing truthful to show.
function _emptyState(title, desc) {
  return '<div class="empty-state"><div class="empty-title">' + escapeHTML(title) +
    '</div><p class="empty-desc">' + escapeHTML(desc) + '</p></div>';
}

// A section that fails to load says so plainly instead of showing a stale or
// invented value; one failing fetch must not take the page down.
function _sectionError(container) {
  if (container) container.innerHTML = '<p class="prog-note">' + escapeHTML(__t('progress.load_error')) + '</p>';
}

function _getJSON(url) {
  return fetch(url, { headers: { 'Accept': 'application/json' } }).then(function (r) {
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  });
}

// ── INIT ──
// Each section owns its own fetch + catch: a secondary failure degrades that
// one section, never the whole page.
function initProgress() {
  var ask = _el('ps-ask');
  // Reuse of the EXISTING coach entry point (coach_widget.js). Only revealed
  // when the widget actually loaded — no new AI workflow is introduced here.
  if (ask && window.CW && typeof window.CW.toggle === 'function') ask.hidden = false;
  loadProgress();
}
function loadProgress() {
  loadSummary();
  loadHistory();
  loadInsights();
  loadPhysique();
}
document.addEventListener('DOMContentLoaded', initProgress);

// Reuses the existing coach widget rather than creating a second AI surface.
function askAxis() {
  if (window.CW && typeof window.CW.toggle === 'function') window.CW.toggle();
}

/* ── THE CANONICAL SUMMARY ────────────────────────────────────────────
   One fetch of /api/progress/summary drives YOUR PROGRESS and all three
   WHAT CHANGED cards, so they can never contradict each other. The server
   owns every state below; these tables are pure enum → i18n key lookups.
   A value missing from a table is treated exactly like a missing signal. */

var TRAJECTORY_LABEL = {
  building_baseline: 'progress.traj_building_baseline',
  on_track: 'progress.traj_on_track',
  needs_attention: 'progress.traj_needs_attention'
};

// Keyed by trajectory.reason — the canonical training signal. One line per
// signal, deterministic, bounded. Richer interpretation belongs to PR3.
var TRAJECTORY_LEDE = {
  insufficient_data: 'progress.traj_lede_insufficient_data',
  progressing: 'progress.traj_lede_progressing',
  keep_pushing: 'progress.traj_lede_keep_pushing',
  build_consistency: 'progress.traj_lede_build_consistency',
  plateau: 'progress.traj_lede_plateau',
  deload: 'progress.traj_lede_deload'
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

// Translate an enum through a table, or return null when the server sent
// something this build does not know. Null always degrades to the neutral
// state — never to a plausible-looking default.
function _label(table, value) {
  return (typeof value === 'string' && table[value]) ? __t(table[value]) : null;
}

function _isNumber(v) { return typeof v === 'number' && isFinite(v); }

function loadSummary() {
  _getJSON('/api/progress/summary')
    .then(renderSummary)
    .catch(summaryUnavailable);
}

function renderSummary(d) {
  if (!d || !d.trajectory) { summaryUnavailable(); return; }
  renderTrajectory(d);
  renderBodyCard(d.body);
  renderPerformanceCard(d.performance);
  renderConsistencyCard(d.consistency);
}

// A failed summary must NOT read as "building baseline": that is a truthful
// statement about the user's history, and the request failing says nothing
// about their history at all.
function summaryUnavailable() {
  _setTrajectory('', __t('progress.traj_unavailable'), __t('progress.load_error'), '');
  _fillCard('wc-body', '—', __t('progress.card_nodata'));
  _fillCard('wc-perf', '—', __t('progress.card_nodata'));
  _fillCard('wc-cons', '—', __t('progress.card_nodata'));
}

function _setTrajectory(state, label, lede, meta) {
  var card = _el('ps-card');
  var stateEl = _el('ps-state');
  var ledeEl = _el('ps-lede');
  var metaEl = _el('ps-meta');
  // The accent is decoration; the label carries the meaning either way.
  if (card) card.setAttribute('data-state', state || '');
  if (stateEl) stateEl.textContent = label;
  if (ledeEl) ledeEl.textContent = lede;
  if (metaEl) metaEl.textContent = meta;
}

function renderTrajectory(d) {
  var label = _label(TRAJECTORY_LABEL, d.trajectory.state);
  var lede = _label(TRAJECTORY_LEDE, d.trajectory.reason);
  if (!label || !lede) { summaryUnavailable(); return; }
  _setTrajectory(d.trajectory.state, label, lede, _summaryMeta(d));
}

// "Last 4 weeks · 12 sessions · CONSISTENT". Each part is dropped rather
// than faked when the server did not send it; no percentage is derived.
function _summaryMeta(d) {
  var parts = [];
  var w = d.window || {};
  var c = d.consistency || {};
  if (_isNumber(w.weeks)) parts.push(__t('progress.meta_window', { weeks: w.weeks }));
  if (_isNumber(c.sessions)) parts.push(__t('progress.meta_sessions', { n: c.sessions }));
  var cons = _label(CONSISTENCY_LABEL, c.state);
  if (cons) parts.push(cons);
  return parts.join(' · ');
}

// BODY — reported, never judged. The card shows the canonical current weight
// and ONE piece of context, in order of how directly it was observed:
// a two-check-in delta, else distance to a configured target, else nothing.
// No delta is classified as success or failure.
function renderBodyCard(body) {
  if (!body || !_isNumber(body.current_weight_kg)) {
    _fillCard('wc-body', '—', __t('progress.body_sub_none'));
    return;
  }

  var value = body.current_weight_kg.toFixed(1) + ' ' + __t('progress.unit_kg');
  var sub;
  if (_isNumber(body.weight_delta_kg)) {
    sub = Math.abs(body.weight_delta_kg) < 0.05
      ? __t('progress.body_sub_flat')
      : __t('progress.body_sub_delta', { delta: _signed(body.weight_delta_kg) });
  } else if (_isNumber(body.distance_to_target_kg)) {
    // Absolute distance; the server deliberately does not say which side of
    // the target the user is on, because that is not a verdict it can make.
    sub = __t('progress.body_sub_target', {
      distance: body.distance_to_target_kg.toFixed(1)
    });
  } else {
    sub = __t('progress.body_sub_partial');
  }
  _fillCard('wc-body', value, sub);
}

// PERFORMANCE — the canonical training state, plus at most one compact
// canonical trend. No volume number, no session count, no chart.
function renderPerformanceCard(perf) {
  var label = perf ? _label(PERFORMANCE_LABEL, perf.state) : null;
  if (!label) {
    _fillCard('wc-perf', '—', __t('progress.card_nodata'));
    return;
  }
  var trend = _label(TREND_LABEL, perf.volume_trend);
  _fillCard('wc-perf', label,
    trend ? __t('progress.perf_sub_volume', { trend: trend }) : '');
}

// CONSISTENCY — canonical training consistency, explained by the counts the
// server sent. The gamification streak is deliberately no longer used here:
// logging in is not training.
function renderConsistencyCard(cons) {
  var label = cons ? _label(CONSISTENCY_LABEL, cons.state) : null;
  if (!label) {
    _fillCard('wc-cons', '—', __t('progress.card_nodata'));
    return;
  }
  // Both counts or neither: they are one sentence, and a half-known one
  // ("of the last — weeks") says less than the state label already did. No
  // threshold on either number — the server sizes the window, not this file.
  var sub = '';
  if (_isNumber(cons.active_weeks) && _isNumber(cons.analyzed_weeks)) {
    sub = __t('progress.cons_active_weeks',
              { active: cons.active_weeks, total: cons.analyzed_weeks });
  }
  _fillCard('wc-cons', label, sub);
}

// ── PROGRESS HISTORY ─────────────────────────────────────────────────
// Unchanged surface, unchanged source: the existing /checkin-history rows.
function loadHistory() {
  _getJSON('/checkin-history')
    .then(function (rows) { renderHistory(Array.isArray(rows) ? rows : []); })
    .catch(function () { _sectionError(_el('history-list')); });
}

// HISTORY = one row per existing weekly check-in, newest first. The only
// derived number is the weight delta against the previous check-in. No week
// is labelled On Track / Needs Attention — that classification does not
// exist canonically (PR5 converges history semantics).
var HISTORY_LIMIT = 12;
function renderHistory(rows) {
  var box = _el('history-list');
  if (!box) return;

  if (!rows.length) {
    box.innerHTML = _emptyState(__t('progress.history_empty_title'), __t('progress.history_empty_desc'));
    return;
  }

  // rows arrive oldest-first; walk backwards so each row can see the check-in
  // that preceded it.
  var items = [];
  for (var i = rows.length - 1; i >= 0 && items.length < HISTORY_LIMIT; i--) {
    var cur = rows[i];
    var prev = rows[i - 1];
    var weightTxt = (typeof cur.kilo === 'number' && cur.kilo > 0)
      ? cur.kilo.toFixed(1) + ' ' + __t('progress.unit_kg')
      : __t('progress.history_no_weight');

    var deltaTxt = '';
    if (prev && typeof cur.kilo === 'number' && typeof prev.kilo === 'number' &&
        cur.kilo > 0 && prev.kilo > 0) {
      var d = cur.kilo - prev.kilo;
      deltaTxt = Math.abs(d) < 0.05 ? '±0.0' : _signed(d);
      deltaTxt += ' ' + __t('progress.unit_kg');
    }

    items.push(
      '<li class="hist-row">' +
        '<span class="hist-date">' + escapeHTML(cur.tarih || '') + '</span>' +
        '<span class="hist-weight">' + escapeHTML(weightTxt) + '</span>' +
        (deltaTxt ? '<span class="hist-delta">' + escapeHTML(deltaTxt) + '</span>' : '') +
      '</li>'
    );
  }

  var html = '<ul class="hist-list">' + items.join('') + '</ul>';
  // Never truncate silently: say what is being shown when the list is capped.
  if (rows.length > HISTORY_LIMIT) {
    html += '<p class="prog-note">' +
      escapeHTML(__t('progress.history_showing', { shown: HISTORY_LIMIT, total: rows.length })) +
      '</p>';
  }
  box.innerHTML = html;
}

// ── AXIS INSIGHTS ────────────────────────────────────────────────────
// Renders the existing deterministic insight payload. `tone` is the
// endpoint's own field; it drives the accent AND a visible text label so the
// signal is never carried by colour alone.
function loadInsights() {
  var box = _el('insight-list');
  _getJSON('/api/progress/insights').then(function (d) {
    var list = (d && d.insights) || [];
    if (!box) return;
    if (!list.length) {
      box.innerHTML = _emptyState(__t('progress.insights_empty_title'), __t('progress.insights_empty_desc'));
      return;
    }
    box.innerHTML = list.map(function (n) {
      var tone = (n.tone === 'success' || n.tone === 'warning') ? n.tone : 'info';
      return '<article class="insight-card" data-tone="' + tone + '">' +
        '<div class="ic-head">' +
          '<span class="ic-icon" aria-hidden="true">' + escapeHTML(n.icon || '💡') + '</span>' +
          '<h3 class="ic-title">' + escapeHTML(n.title) + '</h3>' +
          '<span class="ic-tone tone-' + tone + '">' + escapeHTML(__t('progress.tone_' + tone)) + '</span>' +
        '</div>' +
        '<p class="ic-body">' + escapeHTML(n.body) + '</p>' +
      '</article>';
    }).join('');
  }).catch(function () { _sectionError(box); });
}

// ── PHYSIQUE PROGRESS ────────────────────────────────────────────────
// Presentational shell for PR4. Reads the EXISTING
// /pump-check-gallery/data contract (unchanged) purely to link recent Pump
// Checks; it does not compare images, detect body regions, or assess
// physique change.
var PHYSIQUE_THUMBS = 3;
function loadPhysique() {
  var box = _el('physique-body');
  _getJSON('/pump-check-gallery/data?per_page=' + PHYSIQUE_THUMBS).then(function (d) {
    if (!box) return;
    var items = ((d && d.items) || []).filter(function (x) { return x && x.imageUrl; });
    if (!items.length) {
      box.innerHTML = _emptyState(__t('progress.physique_empty_title'), __t('progress.physique_empty_desc'));
      return;
    }
    box.innerHTML =
      '<p class="prog-note">' + escapeHTML(__t('progress.physique_recent')) + '</p>' +
      '<div class="pp-strip">' + items.map(function (it) {
        var when = it.timePosted || '';
        return '<a class="pp-thumb" href="/pump-check-gallery">' +
          '<img src="' + escapeHTML(it.imageUrl) + '" loading="lazy" alt="' +
          escapeHTML(__t('progress.physique_alt', { date: when })) + '">' +
        '</a>';
      }).join('') + '</div>' +
      '<a class="pp-link" href="/pump-check-gallery">' + escapeHTML(__t('progress.physique_view_all')) + '</a>';
  }).catch(function () { _sectionError(box); });
}
