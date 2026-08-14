/* ══════════════════════════════════════════════════════════════════════
   progress.js — Progress redesign PR1 (information architecture + shell)

   showToast / escapeHTML / selectOverload / submitCheckin / openCheckin /
   closeCheckin / activateOnEnter are preserved VERBATIM — the weekly
   Check-In POST flow must keep working unchanged.

   Everything below the check-in block renders the redesigned sections. Hard
   rule for this file: every user-visible number comes from an existing
   endpoint or an existing canonical calculation. Nothing here classifies a
   trajectory, scores adherence, or assesses a physique — when a signal is
   missing the section degrades to a neutral state instead of inventing one.

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
function activateOnEnter(el, e) {
  if (e.key !== 'Enter' && e.key !== ' ') return;
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
  loadSummaryAndPerformance();
  loadBodyAndHistory();
  loadConsistency();
  loadInsights();
  loadPhysique();
}
document.addEventListener('DOMContentLoaded', initProgress);

// Reuses the existing coach widget rather than creating a second AI surface.
function askAxis() {
  if (window.CW && typeof window.CW.toggle === 'function') window.CW.toggle();
}

// ── YOUR PROGRESS + PERFORMANCE ──────────────────────────────────────
// Both read the SAME existing endpoint (/api/progress/workout?range=month),
// so they are fetched once. `totals.sessions` is the endpoint's own count of
// distinct days with a workout in the last 30 days — no new aggregation.
function loadSummaryAndPerformance() {
  var meta = _el('ps-meta');
  _getJSON('/api/progress/workout?range=month').then(function (d) {
    var sessions = (d && d.totals && d.totals.sessions) || 0;
    if (meta) meta.textContent = __t('progress.summary_meta', { workouts: sessions });
    _fillCard('wc-perf', String(sessions), __t('progress.perf_sub'));
  }).catch(function () {
    if (meta) meta.textContent = __t('progress.summary_meta_none');
    _fillCard('wc-perf', '—', __t('progress.card_nodata'));
  });
}

// ── BODY CARD + PROGRESS HISTORY ─────────────────────────────────────
// Both are built from the existing /checkin-history payload (ascending by
// date), so they share one fetch.
function loadBodyAndHistory() {
  _getJSON('/checkin-history').then(function (rows) {
    renderBodyCard(Array.isArray(rows) ? rows : []);
    renderHistory(Array.isArray(rows) ? rows : []);
  }).catch(function () {
    _fillCard('wc-body', '—', __t('progress.card_nodata'));
    _sectionError(_el('history-list'));
  });
}

// BODY = current weight + the delta against the PREVIOUS check-in. That
// two-point delta is the same calculation /api/progress/insights already
// performs canonically — nothing new is derived here. Falls back to the
// profile weight (window.__PROGRESS.current_weight, written by the same
// canonical source the dashboard uses) when there are no check-ins yet.
function renderBodyCard(rows) {
  var p = window.__PROGRESS || {};
  var weighed = rows.filter(function (r) { return typeof r.kilo === 'number' && r.kilo > 0; });
  var latest = weighed.length ? weighed[weighed.length - 1].kilo
             : (typeof p.current_weight === 'number' && p.current_weight > 0 ? p.current_weight : null);

  if (latest == null) {
    _fillCard('wc-body', '—', __t('progress.body_sub_none'));
    return;
  }

  var value = latest.toFixed(1) + ' ' + __t('progress.unit_kg');
  var sub;
  if (weighed.length >= 2) {
    var delta = latest - weighed[weighed.length - 2].kilo;
    sub = Math.abs(delta) < 0.05
      ? __t('progress.body_sub_flat')
      : __t('progress.body_sub_delta', { delta: _signed(delta) });
  } else if (p.goal_weight > 0) {
    // Nothing to compare against, but the profile carries a goal weight —
    // distance to goal is a plain subtraction of two stored values.
    sub = __t('progress.body_sub_goal', { delta: _signed(latest - p.goal_weight) });
  } else if (weighed.length === 1) {
    sub = __t('progress.body_sub_first');
  } else {
    // The weight came from the profile, not from a check-in — say so rather
    // than implying a check-in exists.
    sub = __t('progress.body_sub_profile');
  }
  _fillCard('wc-body', value, sub);
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

// ── CONSISTENCY CARD ─────────────────────────────────────────────────
// streak_count is the product's existing canonical consistency counter
// (surfaced by /api/progress/achievements). It is NOT re-derived here, and
// the retired Level/XP hero strip is not reinstated — only this one signal.
function loadConsistency() {
  _getJSON('/api/progress/achievements').then(function (a) {
    var streak = (a && a.streak) || 0;
    _fillCard('wc-cons', String(streak) + ' ' + __t('progress.unit_day'), __t('progress.cons_sub'));
  }).catch(function () {
    _fillCard('wc-cons', '—', __t('progress.card_nodata'));
  });
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
