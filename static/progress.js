/* ══════════════════════════════════════════════════════════════════════
   progress.js — Progress page controller (redesign PR1/PR2, V2 PR1)

   showToast / escapeHTML / selectOverload / submitCheckin / openCheckin /
   closeCheckin / activateOnEnter are preserved VERBATIM — the weekly
   Check-In POST flow must keep working unchanged.

   Hard rule for this file: it RENDERS, it does not decide. CURRENT STATE
   and the three TRENDS cards (V2 PR2: value · change · viz · note · meta,
   geometry computed by the presentation model) all render one canonical server payload
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

// Shows a slot with text, or hides it when there is nothing to say — an
// empty line never holds space and never reads as a blank value.
function _setSlot(node, text) {
  if (!node) return;
  node.textContent = text || '';
  if (text) node.removeAttribute('hidden'); else node.setAttribute('hidden', '');
}

// Fills one TRENDS card's text slots. The value never goes blank (a card
// with no signal shows the neutral dash); every other slot hides when empty.
function _fillCard(id, value, note) {
  var card = _el(id);
  if (!card) return;
  var v = card.querySelector('[data-slot="value"]');
  if (v) v.textContent = value;
  _setSlot(card.querySelector('[data-slot="unit"]'), '');
  _setSlot(card.querySelector('[data-slot="change"]'), '');
  _setSlot(card.querySelector('[data-slot="note"]'), note);
  _setSlot(card.querySelector('[data-slot="meta"]'), '');
  _renderViz(card.querySelector('[data-slot="viz"]'), null);
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

// PROGRESS HISTORY lives in its own module (the self-contained IIFE at the
// end of this file). This controller only hands off, so a missing module
// degrades only this section, exactly like a failing fetch does.

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
  renderSummaryView(P.buildSummaryView(d, { locale: window.LOCALE }));
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
  _setCurrentState('', '', __t('progress.load_error'), '', [], '');
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
// STATE → EVIDENCE → ACTION. Evidence is a short list of measured facts;
// the next-move line and the evidence list are hidden when empty.
function _setCurrentState(state, windowText, headline, summary, evidence, next) {
  var card = _el('ps-card');
  var stateEl = _el('ps-state');
  var ledeEl = _el('ps-lede');
  var list = _el('ps-evidence');
  var move = _el('ps-next-move');
  // The accent is decoration; the headline carries the meaning either way.
  if (card) card.setAttribute('data-state', state || '');
  _setSlot(_el('ps-window'), windowText);
  if (stateEl) stateEl.textContent = headline;
  if (ledeEl) ledeEl.textContent = summary;
  if (list) {
    var items = evidence.map(function (text) {
      var li = document.createElement('li');
      li.textContent = text;
      return li;
    });
    list.replaceChildren.apply(list, items);
    if (items.length) list.removeAttribute('hidden'); else list.setAttribute('hidden', '');
  }
  _setSlot(_el('ps-next-text'), next);
  if (move) {
    if (next) move.removeAttribute('hidden'); else move.setAttribute('hidden', '');
  }
}

function renderCurrentState(cs) {
  _setCurrentState(cs.state, _text(cs.window), _text(cs.headline), _text(cs.summary),
                   (cs.evidence || []).map(_text), _text(cs.next_action));
}

// ── TRENDS ──
// One card per metric. status is published on the card so styling and
// later PRs can key on availability without re-deriving it.
var _DIRECTION_GLYPH = { up: '\u2191', down: '\u2193', flat: '\u2192' };
var _SVG_NS = 'http://www.w3.org/2000/svg';

function renderMetric(id, metric) {
  var card = _el(id);
  if (!card) return;
  card.setAttribute('data-status', metric.status);
  var v = card.querySelector('[data-slot="value"]');
  if (v) v.textContent = metric.value ? _text(metric.value) : '—';
  _setSlot(card.querySelector('[data-slot="unit"]'), _text(metric.unit_label));
  _renderChange(card.querySelector('[data-slot="change"]'), metric.change);
  _renderViz(card.querySelector('[data-slot="viz"]'), metric.viz);
  _setSlot(card.querySelector('[data-slot="note"]'), _text(metric.note));
  _setSlot(card.querySelector('[data-slot="meta"]'),
           (metric.meta || []).map(_text).join(' \u00b7 '));
}

// The change line: an aria-hidden direction glyph + the words. The words
// always carry the direction ("+0.6 kg", "Rising"), so the glyph and any
// styling are redundant, never the only signal (WCAG 1.4.1).
function _renderChange(node, change) {
  if (!node) return;
  if (!change || !change.text) { _setSlot(node, ''); node.removeAttribute('data-direction'); return; }
  var parts = [];
  var glyph = change.direction ? _DIRECTION_GLYPH[change.direction] : null;
  if (glyph) {
    var g = document.createElement('span');
    g.className = 'tr-glyph';
    g.setAttribute('aria-hidden', 'true');
    g.textContent = glyph;
    parts.push(g);
  }
  parts.push(document.createTextNode(_text(change.text)));
  node.replaceChildren.apply(node, parts);
  if (change.direction) node.setAttribute('data-direction', change.direction);
  else node.removeAttribute('data-direction');
  node.removeAttribute('hidden');
}

function _svg(name, attrs) {
  var node = document.createElementNS(_SVG_NS, name);
  for (var k in attrs) {
    if (Object.prototype.hasOwnProperty.call(attrs, k)) node.setAttribute(k, attrs[k]);
  }
  return node;
}

// Visualizations. The geometry comes from the presentation model in a fixed
// viewBox, so this only builds a handful of nodes — no measuring, no resize
// listener, no animation. The drawing is aria-hidden; its text equivalent
// is a visually-hidden sentence (line / bars) or a real list (weeks).
function _renderViz(node, viz) {
  if (!node) return;
  if (!viz) { node.replaceChildren(); node.setAttribute('hidden', ''); node.removeAttribute('data-kind'); return; }
  var children = [];
  if (viz.kind === 'weeks') {
    var list = document.createElement('ol');
    list.className = 'tr-weeks';
    list.setAttribute('aria-label', _text(viz.label));
    viz.cells.forEach(function (cell) {
      var li = document.createElement('li');
      li.className = 'tr-week';
      li.setAttribute('data-active', cell.active ? 'true' : 'false');
      var sr = document.createElement('span');
      sr.className = 'tr-sr';
      sr.textContent = _text(cell.label);
      li.appendChild(sr);
      list.appendChild(li);
    });
    children.push(list);
  } else {
    var svg = _svg('svg', {
      viewBox: '0 0 ' + viz.width + ' ' + viz.height,
      preserveAspectRatio: 'none', 'aria-hidden': 'true', focusable: 'false',
      'class': 'tr-svg'
    });
    if (viz.kind === 'line') {
      var pts = viz.points.map(function (p) { return p.x + ',' + p.y; }).join(' ');
      // One polyline, no end marker: the viewBox scales non-uniformly, so a
      // circle would render as an ellipse. The stroke stays crisp via
      // vector-effect (progress.css).
      svg.appendChild(_svg('polyline', { points: pts, 'class': 'tr-line' }));
    } else if (viz.kind === 'bars') {
      viz.bars.forEach(function (b, i) {
        var cls = 'tr-bar' + (i === viz.bars.length - 1 ? ' tr-bar-latest' : '') +
                  (b.zero ? ' tr-bar-zero' : '');
        svg.appendChild(_svg('rect', {
          x: b.x, y: b.zero ? viz.height - 1 : b.y, width: b.width,
          height: b.zero ? 1 : b.height, rx: 1.5, 'class': cls
        }));
      });
    }
    children.push(svg);
    var text = document.createElement('span');
    text.className = 'tr-sr';
    text.textContent = _text(viz.label);
    children.push(text);
  }
  node.replaceChildren.apply(node, children);
  node.setAttribute('data-kind', viz.kind);
  node.removeAttribute('hidden');
}

// ── PROGRESS HISTORY ─────────────────────────────────────────────────
// Delegates to the history module at the end of this file. This file must not fetch
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

/* ═══ BEGIN progress history module ══════════════════════════════════════
   PROGRESS HISTORY consumer (Progress redesign PR5), carried inside
   progress.js since V2 PR1 so the page keeps its pre-V2 static request
   count (progress_presentation.js took the slot the separate
   progress_history.js file used to occupy). It stays a self-contained IIFE
   that only exposes window.FitXProgressHistory; loadHistory() above is its
   only caller, and tests/test_progress_history_ui.py guards this block on
   its own (between the BEGIN/END markers).

   Hard rule for this block: it TRANSLATES canonical facts, it does not
   decide. The server chose the qualifying check-ins, the analysis day, the
   trajectory, performance, consistency, weight and weight delta. It never:

     - computes a trajectory / performance / consistency state
     - subtracts two weights
     - infers an analysis window
     - fetches /checkin-history
     - calls Axis Insights or the planner for a historical row

   Every string from the payload is written with textContent. No innerHTML of
   payload fields. Dates are localized from ISO; numbers are formatted.
   State labels come from the shared tables in
   static/progress_presentation.js.
   ════════════════════════════════════════════════════════════════════════ */

(function () {
  var __t = (window.t) || function (k) { return k; };
  var ENDPOINT = '/api/progress/history';

  // State → copy tables are shared with CURRENT STATE and TRENDS and live in
  // static/progress_presentation.js, so a state reads the same everywhere on
  // the page. If that script failed to load, every table is empty and each
  // row degrades to its neutral '—' instead of guessing.
  var P = window.FitXProgressPresentation || {};
  var TRAJECTORY_LABEL = P.TRAJECTORY || {};
  var PERFORMANCE_LABEL = P.TRAINING_STATE || {};
  var CONSISTENCY_LABEL = P.CONSISTENCY_STATE || {};
  var TREND_LABEL = P.TREND_INLINE || {};

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
    var key = (typeof value === 'string' &&
               Object.prototype.hasOwnProperty.call(table, value)) ? table[value] : null;
    return key ? __t(key) : null;
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
/* ═══ END progress history module ═══ */
