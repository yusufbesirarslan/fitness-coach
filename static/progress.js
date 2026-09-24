/* ══════════════════════════════════════════════════════════════════════
   progress.js — Progress page controller (redesign PR1/PR2, V2 PR1)

   showToast / escapeHTML / selectOverload / submitCheckin / openCheckin /
   closeCheckin / activateOnEnter are preserved VERBATIM — the weekly
   Check-In POST flow must keep working unchanged.

   Hard rule for this file: it RENDERS, it does not decide. CURRENT STATE
   and the three TRENDS cards all render one canonical server payload
   (GET /api/progress/summary) through the view model in
   static/progress_presentation.js, which owns every state → copy table;
   there is no `sessions >= 3 → on track`, no `weight dropped → good`, no
   streak-as-consistency, and no threshold of any kind below. An enum the
   presentation model does not know renders the neutral state rather than a
   guess, exactly like a missing signal.

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
    // F8: msg is text, never markup (server `error` / exception text reaches it).
    const icon = document.createElement('span');
    icon.className = 'toast-icon';
    icon.textContent = icons[type] || 'ℹ';
    const text = document.createElement('span');
    text.textContent = msg == null ? '' : String(msg);
    t.appendChild(icon);
    t.appendChild(text);
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

// Fills one TRENDS card. `detail` may be empty; the value never is (a card
// with no signal shows the neutral dash).
function _fillCard(id, value, detail) {
  var card = _el(id);
  if (!card) return;
  var v = card.querySelector('[data-slot="value"]');
  var s = card.querySelector('[data-slot="detail"]');
  if (v) v.textContent = value;
  if (s) s.textContent = detail;
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
  // UX-1 PR3: the contextual Coach entry is a server-rendered link to the
  // canonical Coach destination, so it needs no client gating and cannot
  // disappear when a script fails to load.
  loadProgress();
}
function loadProgress() {
  loadSummary();
  loadHistory();
  loadAxisInsights();
  loadPhysique();
}

// PROGRESS HISTORY lives in its own module (static/progress_history.js).
// This file only hands off so a failure to load that script degrades only
// this section, exactly like a failing fetch does.

// PHYSIQUE PROGRESS lives in its own module (static/progress_physique.js).
// This file only hands off so a failure to load that script degrades only
// this section, exactly like a failing fetch does.

// AXIS INSIGHTS lives in its own module (static/progress_insights.js) — this
// file hands off rather than rendering it, so the three-slot contract has one
// owner. Guarded because a failure to load that script must degrade only its
// own section, exactly like a failing fetch does.
function loadAxisInsights() {
  var mod = window.FitXAxisInsights;
  if (mod && typeof mod.load === 'function') mod.load();
}
document.addEventListener('DOMContentLoaded', initProgress);

/* ── THE CANONICAL SUMMARY ────────────────────────────────────────────
   One fetch of /api/progress/summary drives CURRENT STATE and all three
   TRENDS cards, so they can never contradict each other. The server owns
   every state; static/progress_presentation.js turns the payload into one
   view model (current_state + metrics.{weight,training_volume,consistency})
   and owns every state → copy table. This file only writes that view into
   the DOM — it holds no mapping of its own. */

function _presentation() { return window.FitXProgressPresentation || null; }

// A copy descriptor ({key, params}) → text; no descriptor → empty line.
function _text(desc) {
  return desc ? __t(desc.key, desc.params || undefined) : '';
}

function loadSummary() {
  _getJSON('/api/progress/summary')
    .then(renderSummary)
    .catch(summaryUnavailable);
}

function renderSummary(d) {
  var P = _presentation();
  if (!P) { _summaryModuleMissing(); return; }
  renderSummaryView(P.buildSummaryView(d));
}

// A failed summary must NOT read as "building baseline": that is a truthful
// statement about the user's history, and the request failing says nothing
// about their history at all. The presentation model's unavailable view is
// the one definition of that state.
function summaryUnavailable() {
  var P = _presentation();
  if (!P) { _summaryModuleMissing(); return; }
  renderSummaryView(P.buildSummaryView(null));
}

// The presentation script itself failed to load: degrade this section only,
// and never leave it on "Loading…".
function _summaryModuleMissing() {
  _setCurrentState('', __t('progress.load_error'), '', '');
  ['tr-weight', 'tr-volume', 'tr-consistency'].forEach(function (id) {
    _fillCard(id, '—', __t('progress.load_error'));
  });
}

function renderSummaryView(view) {
  renderCurrentState(view.current_state);
  renderMetric('tr-weight', view.metrics.weight);
  renderMetric('tr-volume', view.metrics.training_volume);
  renderMetric('tr-consistency', view.metrics.consistency);
}

// ── CURRENT STATE ──
function _setCurrentState(state, headline, summary, evidence) {
  var card = _el('ps-card');
  var stateEl = _el('ps-state');
  var ledeEl = _el('ps-lede');
  var metaEl = _el('ps-meta');
  // The accent is decoration; the headline carries the meaning either way.
  if (card) card.setAttribute('data-state', state || '');
  if (stateEl) stateEl.textContent = headline;
  if (ledeEl) ledeEl.textContent = summary;
  if (metaEl) metaEl.textContent = evidence;
}

function renderCurrentState(cs) {
  _setCurrentState(cs.state, _text(cs.headline), _text(cs.summary), _text(cs.evidence));
}

// ── TRENDS ──
// One card per metric. status is published on the card so styling and
// later PRs can key on availability without re-deriving it.
function renderMetric(id, metric) {
  var card = _el(id);
  if (card) card.setAttribute('data-status', metric.status);
  _fillCard(id, metric.value ? _text(metric.value) : '—', _text(metric.detail));
}

// ── PROGRESS HISTORY ─────────────────────────────────────────────────
// Delegates to static/progress_history.js. This file must not fetch
// /checkin-history, compute a weight delta, or classify a historical row.
function loadHistory() {
  var mod = window.FitXProgressHistory;
  if (mod && typeof mod.load === 'function') {
    mod.load();
    return;
  }
  _sectionError(_el('history-list'));
}

// ── PHYSIQUE PROGRESS ────────────────────────────────────────────────
// Delegates to static/progress_physique.js. This file must not fetch
// physique data, compare images, or decide whether a physique improved.
function loadPhysique() {
  var mod = window.FitXPhysiqueProgress;
  if (mod && typeof mod.load === 'function') {
    mod.load();
    return;
  }
  _sectionError(_el('physique-body'));
}
