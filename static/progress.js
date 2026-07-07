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

// ── TEMPORARY STUBS (no-op; replaced in Tasks 6-9 so this page runs standalone) ──
function loadOverviewAndExtras() {}
function loadWeightTab() {}
function loadNutritionTab() {}
function loadWorkoutTab() {}
function loadAchievementsTab() {}
