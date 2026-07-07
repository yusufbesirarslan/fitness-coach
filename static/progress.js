/* ══════════════════════════════════════════════════════════════════════
   progress.js — Phase 5 (AxisAI V2) progress page
   showToast / escapeHTML / selectOverload / submitCheckin are preserved
   VERBATIM from the old templates/progress.html inline <script> (Phase 5
   Task 5) — the weekly Check-In POST flow must keep working unchanged.
   Everything else (tabs, sheet open/close, loaders) is new scaffold for
   this redesign; Tasks 6-9 replace the no-op stubs at the bottom.
   No IIFE: functions must resolve as window.<name> for actions.js's
   data-action dispatcher (see static/actions.js).
   ══════════════════════════════════════════════════════════════════════ */

var __t = (window.t) || function (k) { return k; };
var _EN = (window.LOCALE === 'en');

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
    document.querySelectorAll('.overload-chip').forEach(c => c.classList.remove('selected'));
    el.classList.add('selected');
}

// ── CHECK-IN ── (verbatim: POST /checkin, coach_feedback escape, CW hand-off)
async function submitCheckin() {
    const weight = document.getElementById('ci-weight').value;
    if (!weight) { showToast('Kilo zorunludur.', 'error'); return; }

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
        showToast('Check-in kaydedildi!', 'success');
        if (window.CW) window.CW.receiveCheckinFeedback(data.coach_feedback);
    } catch (err) {
        showToast('Hata: ' + err.message, 'error');
    } finally {
        btn.classList.remove('loading');
        btn.textContent = __t('progress.submit_checkin');
    }
}

// ── TABS ──
function switchTab(name, btn) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  if (btn) btn.classList.add('active');
  var panel = document.getElementById('tab-' + name);
  if (panel) panel.classList.add('active');
  if (name === 'weight')       loadWeightTab();        // Task 7
  if (name === 'nutrition')    loadNutritionTab();     // Task 8
  if (name === 'workout')      loadWorkoutTab();       // Task 8
  if (name === 'achievements') loadAchievementsTab();  // Task 9
}

// ── CHECK-IN SHEET ──
function openCheckin()  { document.getElementById('checkin-sheet').classList.add('open'); }
function closeCheckin() { document.getElementById('checkin-sheet').classList.remove('open'); }

// ── INIT ──
// Task 6/7/8/9 define the loaders + renderHeatmap/renderInsights/overview.
function initProgress() { loadOverviewAndExtras(); loadWeightTab(); }
document.addEventListener('DOMContentLoaded', initProgress);

// ── OVERVIEW / HEATMAP / INSIGHTS ── (Task 6)
async function loadOverviewAndExtras() {
  try {
    var [hm, ins, ach] = await Promise.all([
      fetch('/api/progress/heatmap?weeks=26').then(r => r.json()),
      fetch('/api/progress/insights').then(r => r.json()),
      fetch('/api/progress/achievements').then(r => r.json()),
    ]);
    renderHeatmap(hm.cells || []);
    renderInsights(ins.insights || []);
    renderOverview(ach);
  } catch (e) {}
}

function renderHeatmap(cells) {
  var grid = document.getElementById('heatmap-grid');
  if (!grid) return;
  grid.innerHTML = cells.map(function (c) {
    return '<div class="hm-cell lvl-' + (c.level || 0) + '" title="' +
      escapeHTML(c.date) + '"></div>';
  }).join('');
}

function renderInsights(list) {
  var row = document.getElementById('insight-row');
  if (!row) return;
  if (!list.length) { row.innerHTML = ''; return; }
  row.innerHTML = list.map(function (n) {
    return '<div class="insight-card"><div class="ic-head"><span class="ic-icon">' +
      escapeHTML(n.icon || '💡') + '</span><span class="ic-title badge badge-' +
      (n.tone || 'info') + '">' + escapeHTML(n.title) + '</span></div>' +
      '<div class="ic-body">' + escapeHTML(n.body) + '</div></div>';
  }).join('');
}

function renderOverview(a) {
  if (!a) return;
  var set = function (id, v) { var el = document.getElementById(id); if (el) el.textContent = v; };
  set('po-streak', (a.streak || 0));
  set('po-level', (a.level || 0));
  set('po-xp', (a.weekly_xp || 0));
}

// Shared render helper (used by the Weight/Workout/Achievements tabs, Tasks 7–9).
function statCard(v, label) {
  return '<div class="stat-card"><div class="stat-value">' + v +
    '</div><div class="stat-label">' + escapeHTML(label) + '</div></div>';
}

// ── WEIGHT & BODY TAB (Task 7) ──
// Shared responsive Chart.js base options, extracted from the old inline
// `baseOpts` (grid/text colors kept as JS literals — documented CSP
// exception). Tasks 8/9 reuse this for the nutrition/workout charts.
function _chartBase(yOpts) {
  var gridColor = 'rgba(255,255,255,0.05)';
  var textColor = '#606060';
  var yScale = { grid: { color: gridColor }, ticks: { color: textColor, font: { size: 11 } } };
  Object.assign(yScale, yOpts || {});
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { labels: { color: textColor, font: { family: 'DM Sans', size: 11 } } } },
    scales: {
      x: { grid: { color: gridColor }, ticks: { color: textColor, font: { size: 11 } } },
      y: yScale
    }
  };
}

var weightChart, wellnessChart;
async function loadWeightTab() {
  var data = await fetch('/checkin-history').then(function (r) { return r.json(); });
  renderBodyStats(data);

  var noData = data.length === 0;
  ['weight', 'wellness'].forEach(function (k) {
    var nd = document.getElementById(k + '-nodata');
    if (nd) nd.style.display = noData ? 'block' : 'none';
  });
  if (noData) return;

  var labels = data.map(function (d) { return d.tarih; });

  if (weightChart) weightChart.destroy();
  weightChart = new Chart(document.getElementById('weightChart'), {
    type: 'line',
    data: {
      labels: labels,
      datasets: [{
        label: __t('progress.chart_weight'), data: data.map(function (d) { return d.kilo; }),
        borderColor: '#3D8BFF', backgroundColor: 'rgba(61,139,255,0.08)',
        fill: true, tension: 0.35, pointRadius: 5, pointHoverRadius: 8,
        pointBackgroundColor: '#3D8BFF', borderWidth: 2
      }]
    },
    options: _chartBase({ beginAtZero: false })
  });

  if (wellnessChart) wellnessChart.destroy();
  wellnessChart = new Chart(document.getElementById('wellnessChart'), {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        { label: __t('progress.chart_intensity'), data: data.map(function (d) { return d.yogunluk; }),
          borderColor: '#3D8BFF', borderWidth: 2, tension: 0.3, pointRadius: 4, fill: false },
        { label: __t('progress.chart_fatigue'), data: data.map(function (d) { return d.fatigue; }),
          borderColor: '#FF4D4D', borderWidth: 2, tension: 0.3, pointRadius: 4, fill: false },
        { label: __t('progress.chart_sleep'), data: data.map(function (d) { return d.uyku; }),
          borderColor: '#3D9EFF', borderWidth: 2, tension: 0.3, pointRadius: 4, fill: false },
        { label: __t('progress.chart_nutrition'), data: data.map(function (d) { return d.beslenme; }),
          borderColor: '#FFB020', borderWidth: 2, tension: 0.3, pointRadius: 4, fill: false }
      ]
    },
    options: _chartBase({ min: 0, max: 5 })
  });
}

// current weight / BMI / Δ vs first check-in — writes into #body-stats via
// the shared statCard() helper (Task 6).
function renderBodyStats(data) {
  var el = document.getElementById('body-stats');
  if (!el) return;
  var p = window.__PROGRESS || {};

  var latest = data.length ? data[data.length - 1].kilo : p.current_weight;
  var first  = data.length ? data[0].kilo : latest;

  var weightVal = (latest != null && latest > 0) ? latest.toFixed(1) + ' kg' : '—';

  var bmi = (latest > 0 && p.height_cm > 0)
    ? latest / Math.pow(p.height_cm / 100, 2)
    : null;
  var bmiVal = (bmi != null) ? bmi.toFixed(1) : '—';

  var delta = (latest != null && first != null) ? (latest - first) : null;
  var deltaVal = (delta != null)
    ? (delta > 0 ? '+' : '') + delta.toFixed(1) + ' kg'
    : '—';

  el.innerHTML =
    statCard(weightVal, 'Güncel Kilo') +
    statCard(bmiVal, 'BMI') +
    statCard(deltaVal, 'Değişim');
}

// ── TEMPORARY STUBS (no-op; replaced in Tasks 8-9 so this page runs standalone) ──
function loadNutritionTab() {}
function loadWorkoutTab() {}
function loadAchievementsTab() {}
