
/* ── CONSTANTS ── */
const RING_CIRC = 301.6; // 2π × 48
let targetCalories = 2000;
let selectedMealType = 'Kahvaltı';
let quickAddOpen = false;

/* ── HTML ESCAPE (XSS guard — innerHTML'e giren kullanıcı/AI/FatSecret metni) ── */
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/* ── SERVING LABEL HELPER ── */
function formatServingLabel(desc, metricAmt, calories, isBulk) {
  let label = desc;
  if (metricAmt > 0 && !/^\d+\s*g$/i.test(desc))
    label += ' (' + Math.round(metricAmt) + 'g)';
  label += ' — ' + Math.round(calories) + ' kcal';
  if (isBulk) label += ' ⚠ tüm tarif';
  return label;
}

/* ── TOAST SYSTEM ── */
function showToast(msg, type = 'info', duration = 3500) {
  const wrap = document.getElementById('toast-wrap');
  const icons = { success: '✓', error: '✕', info: 'ℹ' };
  const t = document.createElement('div');
  t.className = `toast toast-${type}`;
  t.innerHTML = `<span class="toast-icon">${icons[type] || '•'}</span><span>${msg}</span>`;
  wrap.appendChild(t);
  setTimeout(() => {
    t.classList.add('hide');
    setTimeout(() => t.remove(), 300);
  }, duration);
}

/* ── TAB SYSTEM ── */
function switchTab(name, btn) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('panel-' + name).classList.add('active');
  if (name === 'today')   { loadTodayData(); loadQuickAddSection(); }
  if (name === 'diary')   { loadDiary(); }
  if (name === 'history') { loadMealHistory(); }
}

/* ── data-action köprüleri (CSP: satır-içi on* yerine) ──
   Tıklanan öğe (eski `this`) bazı eski çağrılarda ortada/başta argümandı ya da
   this.value iletiliyordu; delegasyon öğeyi sona koyduğu için bu ince
   sarmalayıcılar argüman sırasını ve değer okumayı korur. */
function fxQuickWater()  { openWater(); toggleQuickAdd(); }
function fxQuickScroll() { scrollToForm(); toggleQuickAdd(); }
function fxGoToPlanTab() { switchTab('plan', document.querySelectorAll('.tab-btn')[1]); }
function fxSelectFood(el) { selectFood(JSON.parse(el.dataset.f)); }
function fxAddDiaryFood(el) { addDiaryFood(el.dataset.meal, JSON.parse(el.dataset.f)); }
function fxDiaryFoodSearch(el) { diaryFoodSearch(el, el.dataset.meal); }
function fxUpdateDiaryServing(el) { updateDiaryServing(el.dataset.itemId, el.value, el.dataset.foodId); }
function fxUpdateDiaryServingQty(el) { updateDiaryServingQty(el.dataset.itemId, el.value, el.dataset.foodId); }
function fxUpdateDiaryServingQtyOnly(el) { updateDiaryServingQtyOnly(el.dataset.itemId, el.value); }
function fxUpdateDiaryGrams(el) { updateDiaryGrams(el.dataset.itemId, el.value); }

/* ── CALORIE RING ── */
function updateRing(eaten, target) {
  const pct = Math.min(eaten / Math.max(target, 1), 1);
  const offset = RING_CIRC * (1 - pct);
  const ring = document.getElementById('calorie-ring');
  ring.style.strokeDashoffset = offset;
  ring.style.stroke = eaten > target * 1.05 ? '#FF4D4D' : '#CCFF00';
  document.getElementById('ring-eaten').textContent = Math.round(eaten);
  document.getElementById('ring-pct').textContent   = Math.round(pct * 100) + '%';
  document.getElementById('ring-target').textContent = Math.round(target);
}

/* ── MACRO BARS ── */
function updateMacroBars(totals, targets) {
  const cfg = [
    { key:'protein', elVal:'macro-protein', elBar:'bar-protein', tgt: targets.protein || 140 },
    { key:'karb',    elVal:'macro-karb',    elBar:'bar-karb',    tgt: targets.karb    || 200 },
    { key:'yag',     elVal:'macro-yag',     elBar:'bar-yag',     tgt: targets.yag     || 60  },
  ];
  cfg.forEach(c => {
    const val = totals[c.key] || 0;
    const pct = Math.min(val / c.tgt, 1) * 100;
    document.getElementById(c.elVal).textContent = Math.round(val);
    document.getElementById(c.elBar).style.width = pct + '%';
  });
}

/* ── MEAL TYPE SELECTOR ── */
function selectMealType(type, el) {
  selectedMealType = type;
  document.querySelectorAll('.meal-type-opt').forEach(o => o.classList.remove('selected'));
  el.classList.add('selected');
}

/* ── LOAD TODAY DATA ── */
async function loadTodayData() {
  try {
    const [sessionRes, todayRes] = await Promise.all([
      fetch('/last-session'),
      fetch('/meal-log/today')
    ]);
    const session = await sessionRes.json();
    const today   = await todayRes.json();

    if (session.exists && session.target_calories) {
      targetCalories = Math.round(session.target_calories);
    }

    // Update ring & bars
    const eaten = today.totals || {};
    updateRing(eaten.kalori || 0, targetCalories);

    // Estimate macro targets from calories (rough: 30/40/30 split)
    const macroTargets = {
      protein: Math.round(targetCalories * 0.30 / 4),
      karb:    Math.round(targetCalories * 0.40 / 4),
      yag:     Math.round(targetCalories * 0.30 / 9),
    };
    updateMacroBars(eaten, macroTargets);

    // Render meals list
    renderTodayMeals(today.meals || []);

  } catch (e) {
    console.error('loadTodayData', e);
  }
}

function renderTodayMeals(meals) {
  const el = document.getElementById('today-meals-list');
  if (!meals.length) {
    el.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon"><svg viewBox="0 0 24 24"><path d="M18 8h1a4 4 0 0 1 0 8h-1"/><path d="M2 8h16v9a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4V8z"/></svg></div>
        <div class="empty-title">Bugün henüz öğün girilmedi</div>
        <div class="empty-sub">Aşağıdaki formdan öğünlerini ekle, AI besin değerlerini hesaplasın.</div>
      </div>`;
    return;
  }
  el.innerHTML = meals.map(m => {
    let badge = '';
    if (m.source === 'ai_plan') badge = '<span class="source-badge ai">AI Planı</span>';
    else if (m.source === 'diary') badge = '<span class="source-badge diary">Manuel Günlük</span>';
    return `
    <div class="meal-log-card" style="margin-bottom:10px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
        <span class="meal-badge">${m.ogun}${badge}</span>
        <span style="font-family:'Bebas Neue';font-size:18px;color:var(--volt);letter-spacing:1px;">${Math.round(m.kalori)} kcal</span>
      </div>
      <div style="font-size:14px;color:var(--text);font-weight:300;line-height:1.6;margin-bottom:10px;">${esc(m.yemekler)}</div>
      <div style="display:flex;gap:16px;padding-top:10px;border-top:1px solid var(--border);">
        <span style="font-size:12px;color:var(--text-2);">P: <strong style="color:var(--text);">${Math.round(m.protein)}g</strong></span>
        <span style="font-size:12px;color:var(--text-2);">K: <strong style="color:var(--text);">${Math.round(m.karb)}g</strong></span>
        <span style="font-size:12px;color:var(--text-2);">Y: <strong style="color:var(--text);">${Math.round(m.yag)}g</strong></span>
      </div>
    </div>`;
  }).join('');
}

/* ── LOG MEAL ── */
async function logMeal() {
  const input = document.getElementById('meal-input');
  const loading = document.getElementById('loading');

  if (selectedFoods.length > 0) {
    const t = selectedFoods.reduce((acc, f) => ({
      cal: acc.cal + (f.per_100g.calories || 0),
      p: acc.p + (f.per_100g.protein || 0),
      k: acc.k + (f.per_100g.carbs || 0),
      y: acc.y + (f.per_100g.fat || 0)
    }), {cal:0, p:0, k:0, y:0});
    const names = selectedFoods.map(f => f.name).join(', ');

    loading.classList.add('active');
    try {
      const res = await fetch('/meal-log', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ogun: selectedMealType,
          yemekler: names,
          override_macros: { kalori: t.cal, protein: t.p, karb: t.k, yag: t.y }
        })
      });
      const d = await res.json();
      if (d.error) { showToast(d.error, 'error'); return; }
      selectedFoods = [];
      renderSelectedFoods();
      input.value = '';
      showToast('Öğün kaydedildi! ✓', 'success');
      if (d.quest_awarded) showToast('\u{1F3AF} +' + d.quest_awarded.xp + ' XP!', 'success');
      loadTodayData();
    } catch (e) {
      showToast('Bağlantı hatası: ' + e.message, 'error');
    } finally {
      loading.classList.remove('active');
    }
    return;
  }

  const yemekler = input.value.trim();
  if (!yemekler) { showToast('Ne yediğini yaz veya yukarıdan besin ara.', 'error'); return; }

  loading.classList.add('active');
  try {
    const res = await fetch('/meal-log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ogun: selectedMealType, yemekler })
    });
    const d = await res.json();
    if (d.error) { showToast(d.error, 'error'); return; }
    input.value = '';
    showToast('Öğün kaydedildi! ✓', 'success');
    // Funnel/aktivasyon: ilk öğün kaydı.
    if (window.fxTrackOnce) fxTrackOnce('first_meal_logged');
    if (window.fxActivation) fxActivation('meal');
    if (d.quest_awarded) showToast('\u{1F3AF} +' + d.quest_awarded.xp + ' XP!', 'success');
    loadTodayData();
  } catch (e) {
    showToast('Bağlantı hatası: ' + e.message, 'error');
  } finally {
    loading.classList.remove('active');
  }
}

/* ── AI REVIEW ── */
async function getReview() {
  const btn = document.getElementById('review-btn');
  btn.textContent = 'DEĞERLENDİRİLİYOR…';
  btn.disabled = true;
  try {
    const res = await fetch('/meal-log/review', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }
    });
    const d = await res.json();
    if (d.error) { showToast(d.error, 'error'); return; }
    document.getElementById('review-text').innerHTML = esc(d.review || d.message || '').replace(/\n/g, '<br>');
    document.getElementById('review-card').classList.add('visible');
  } catch (e) {
    showToast('Değerlendirme alınamadı.', 'error');
  } finally {
    btn.textContent = 'GÜN SONU AI DEĞERLENDİRMESİ';
    btn.disabled = false;
  }
}

/* ── MEAL HISTORY ── */
async function loadMealHistory() {
  try {
    const res = await fetch('/meal-log/history');
    const data = await res.json();

    // Weekly chart (last 7 days)
    renderWeeklyChart(data.slice(0, 7).reverse());

    // History list
    const list = document.getElementById('history-list');
    if (!data.length) {
      list.innerHTML = `<div class="empty-state"><div class="empty-icon"><svg viewBox="0 0 24 24"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg></div><div class="empty-title">Henüz kayıt yok</div></div>`;
      return;
    }
    list.innerHTML = data.map(day => `
      <div class="history-day">
        <div class="history-day-hdr">
          <div class="history-date">${day.tarih}</div>
          <div class="history-totals">
            <span class="history-total">Kalori: <span>${Math.round(day.totals.kalori)}</span></span>
            <span class="history-total">P: <span>${Math.round(day.totals.protein)}g</span></span>
            <span class="history-total">K: <span>${Math.round(day.totals.karb)}g</span></span>
            <span class="history-total">Y: <span>${Math.round(day.totals.yag)}g</span></span>
          </div>
        </div>
        ${day.meals.map(m => `
          <div class="history-meal">
            <div class="history-meal-type">${m.ogun} · ${Math.round(m.kalori)} kcal</div>
            <div class="history-meal-foods">${esc(m.yemekler)}</div>
          </div>`).join('')}
      </div>`).join('');
  } catch (e) {
    console.error('loadMealHistory', e);
  }
}

function renderWeeklyChart(days) {
  const chart = document.getElementById('weekly-chart');
  if (!days.length) { chart.innerHTML = '<div style="flex:1;text-align:center;color:var(--text-3);font-size:13px;align-self:center;">Veri yok</div>'; return; }
  const maxKal = Math.max(...days.map(d => d.totals.kalori || 0), 1);
  chart.innerHTML = days.map(d => {
    const pct = Math.round((d.totals.kalori || 0) / maxKal * 100);
    return `
      <div class="bar-col">
        <div class="bar-track">
          <div class="bar-fill" style="height:${pct}%;"></div>
        </div>
        <div class="bar-col-label">${d.tarih}</div>
      </div>`;
  }).join('');
}

/* ── FOOD CHIPS (Plan Tab) ── */
const FOODS = {
  protein_hayvansal: ['Tavuk Göğsü','Yumurta','Ton Balığı','Kırmızı Et','Yoğurt','Somon','Hindi'],
  protein_bitkisel:  ['Mercimek','Nohut','Tofu','Kinoa','Edamame','Fasulye'],
  karbonhidrat:      ['Yulaf Ezmesi','Pirinç','Bulgur','Tatlı Patates','Tam Buğday Ekmeği','Muz','Elma','Makarna'],
  yag:               ['Zeytinyağı','Avokado','Badem','Ceviz','Fındık','Fıstık Ezmesi']
};
const selected    = { proteins: new Set(), carbs: new Set(), fats: new Set() };
const customFoods = [];

function createFoodChip(name, category) {
  const el = document.createElement('div');
  el.className = 'chip';
  el.innerHTML = `<span class="chip-dot"></span>${name}`;
  el.addEventListener('click', () => {
    const isSelected = el.classList.toggle('selected');
    if (isSelected) selected[category].add(name);
    else            selected[category].delete(name);
    el.querySelector('.chip-dot').style.background = isSelected ? '#CCFF00' : '';
  });
  return el;
}

function populateFoods() {
  FOODS.protein_hayvansal.forEach(f => document.getElementById('protein-hayvansal').appendChild(createFoodChip(f, 'proteins')));
  FOODS.protein_bitkisel.forEach(f  => document.getElementById('protein-bitkisel').appendChild(createFoodChip(f, 'proteins')));
  FOODS.karbonhidrat.forEach(f      => document.getElementById('karb-list').appendChild(createFoodChip(f, 'carbs')));
  FOODS.yag.forEach(f               => document.getElementById('yag-list').appendChild(createFoodChip(f, 'fats')));
}

function addCustomFood() {
  const input = document.getElementById('custom-input');
  const val   = input.value.trim();
  if (!val || customFoods.includes(val)) { input.value = ''; return; }
  customFoods.push(val);
  input.value = '';
  const tag = document.createElement('div');
  tag.className = 'custom-tag';
  tag.innerHTML = `<span>${esc(val)}</span><span class="custom-tag-remove" data-action="removeCustomFood">×</span>`;
  document.getElementById('custom-tags').appendChild(tag);
}
document.getElementById('custom-input').addEventListener('keydown', e => { if (e.key === 'Enter') addCustomFood(); });
function removeCustomFood(el) {
  const tag = el.closest('.custom-tag');
  if (!tag) return;
  const name = tag.querySelector('span').textContent;
  const i = customFoods.indexOf(name);
  if (i > -1) customFoods.splice(i, 1);
  tag.remove();
}

/* ── GENERATE PLAN ── */
async function generatePlan() {
  if (!selected.proteins.size) { showToast('En az bir protein kaynağı seç.', 'error'); return; }
  if (!selected.carbs.size)    { showToast('En az bir karbonhidrat kaynağı seç.', 'error'); return; }
  if (!selected.fats.size)     { showToast('En az bir yağ kaynağı seç.', 'error'); return; }

  const btn = document.getElementById('plan-btn');
  const loading = document.getElementById('loading');
  btn.classList.add('loading');
  btn.textContent = 'HAZIRLANIYOR...';
  loading.classList.add('active');

  try {
    const res = await fetch('/nutrition-plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        proteins:     [...selected.proteins],
        carbs:        [...selected.carbs],
        fats:         [...selected.fats],
        custom_foods: customFoods
      })
    });
    const data = await res.json();
    if (data.error) { showToast(data.error, 'error'); return; }
    renderPlans(data);
  } catch (e) {
    showToast('Plan oluşturulamadı: ' + e.message, 'error');
  } finally {
    btn.classList.remove('loading');
    btn.textContent = 'PLAN OLUŞTUR';
    loading.classList.remove('active');
  }
}

function renderPlans(data) {
  document.getElementById('plan-results').style.display = 'block';

  // Score banner
  const scoreColors = { 'İyi': '#CCFF00', 'Orta': '#FFB020', 'Kötü': '#FF4D4D' };
  const color = scoreColors[data.score_label] || '#9A9A9A';
  document.getElementById('score-banner-wrap').innerHTML = `
    <div class="score-banner" style="margin-bottom:24px;">
      <div class="score-big" style="color:${color};">${data.overall_score}</div>
      <div>
        <div class="score-label" style="color:${color};">${data.score_label} Plan</div>
        <div class="score-desc">Mikro değer, biyoyararlanım ve gluten skoru ortalaması</div>
      </div>
    </div>`;

  // Plan cards
  const grid = document.getElementById('plans-grid');
  grid.innerHTML = '';
  data.planlar.forEach((plan, i) => {
    const meals = [
      { key:'kahvalti',  label:'Kahvaltı' },
      { key:'ogle',      label:'Öğle'     },
      { key:'aksam',     label:'Akşam'    },
      { key:'ara_ogun',  label:'Ara Öğün' }
    ];
    const mealsHtml = meals.map(m => {
      const ml = plan[m.key];
      if (!ml) return '';
      return `
        <div class="plan-meal-sec">
          <div class="plan-meal-title">${m.label} · ${ml.kalori ?? '—'} kcal</div>
          <ul class="plan-meal-items">${(ml.yemekler || []).map(y => `<li>${esc(y)}</li>`).join('')}</ul>
        </div>`;
    }).join('');

    const card = document.createElement('div');
    card.className = 'plan-card';
    card.id = `plan-card-${i}`;
    card.innerHTML = `
      <div class="plan-card-hdr">
        <div class="plan-card-name">${esc(plan.isim ?? 'Plan ' + (i+1))}</div>
        <div class="plan-card-kcal">${plan.toplam_kalori ?? '—'} kcal</div>
      </div>
      <div class="plan-card-body">
        ${mealsHtml}
        <div class="plan-macro-grid">
          <div class="plan-macro-item"><div class="plan-macro-val">${plan.toplam_protein ?? '—'}g</div><div class="plan-macro-lbl">Protein</div></div>
          <div class="plan-macro-item"><div class="plan-macro-val">${plan.toplam_karb ?? '—'}g</div><div class="plan-macro-lbl">Karb</div></div>
          <div class="plan-macro-item"><div class="plan-macro-val">${plan.toplam_yag ?? '—'}g</div><div class="plan-macro-lbl">Yağ</div></div>
        </div>
        <button class="btn-select-plan" id="sel-btn-${i}"
          data-action="selectPlan" data-args="${JSON.stringify([i, plan, data.overall_score]).replace(/"/g,'&quot;')}">
          BU PLANI SEÇ
        </button>
      </div>`;
    grid.appendChild(card);
  });

  setTimeout(() => document.getElementById('plan-results').scrollIntoView({ behavior:'smooth', block:'start' }), 200);
}

async function selectPlan(i, plan, score) {
  try {
    await fetch('/nutrition-plan/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan, score })
    });
    invalidateActivePlan();
    document.querySelectorAll('.plan-card').forEach(c => c.classList.remove('chosen'));
    document.querySelectorAll('.btn-select-plan').forEach(b => { b.textContent = 'BU PLANI SEÇ'; b.classList.remove('chosen'); });
    document.getElementById(`plan-card-${i}`).classList.add('chosen');
    const btn = document.getElementById(`sel-btn-${i}`);
    btn.textContent = '✓ AKTİF PLAN';
    btn.classList.add('chosen');
    showToast('Plan kaydedildi!', 'success');
  } catch (e) {
    showToast('Kayıt hatası: ' + e.message, 'error');
  }
}

/* ── AKTİF PLAN CACHE ──
   loadActivePlan() ve loadQuickAddSection() açılışta arka arkaya çağrılıyordu;
   ikisi de /nutrition-plan/active'i çekince istek iki kez gidiyordu. In-flight
   promise'i paylaşarak tek isteğe indir; plan değişince invalidateActivePlan(). */
let _activePlanCache = null;
function getActivePlan(force = false) {
  if (force || !_activePlanCache) {
    _activePlanCache = fetch('/nutrition-plan/active')
      .then(r => r.json())
      .catch(err => { _activePlanCache = null; throw err; });
  }
  return _activePlanCache;
}
function invalidateActivePlan() { _activePlanCache = null; }

async function loadActivePlan() {
  try {
    const d = await getActivePlan();
    if (!d.exists) return;
    renderActivePlanDetail(d.plan, d.score, d.created_at);
    document.getElementById('active-plan-detail').style.display = 'block';
    document.getElementById('plan-form').style.display = 'none';
  } catch (e) {}
}

function renderActivePlanDetail(plan, score, createdAt) {
  const meals = [
    { key: 'kahvalti', label: 'Kahvaltı',  icon: '🍳' },
    { key: 'ogle',     label: 'Öğle',      icon: '🥗' },
    { key: 'aksam',    label: 'Akşam',     icon: '🍽️' },
    { key: 'ara_ogun', label: 'Ara Öğün',  icon: '🥜' }
  ];

  const mealsHtml = meals.map(m => {
    const ml = plan[m.key];
    if (!ml) return '';
    const items = (ml.yemekler || []).map(y => `<li>${y}</li>`).join('');
    return `
      <div class="apd-meal">
        <div class="apd-meal-hdr">
          <span class="apd-meal-icon">${m.icon}</span>
          <span class="apd-meal-name">${m.label}</span>
          <span class="apd-meal-kcal">${ml.kalori ?? '—'} kcal</span>
        </div>
        <ul class="apd-meal-list">${items}</ul>
      </div>`;
  }).join('');

  document.getElementById('active-plan-detail').innerHTML = `
    <div class="apd-header">
      <div>
        <div class="apd-title">${esc(plan.isim || 'Aktif Plan')}</div>
        <div class="apd-sub">${createdAt} · Skor: ${score}/10</div>
      </div>
      <button class="btn-ghost" data-action="resetPlan">+ Yeni Plan Oluştur</button>
    </div>

    <div class="apd-macro-grid">
      <div class="apd-macro-item">
        <div class="apd-macro-val">${plan.toplam_kalori ?? '—'}</div>
        <div class="apd-macro-lbl">kcal</div>
      </div>
      <div class="apd-macro-item">
        <div class="apd-macro-val">${plan.toplam_protein ?? '—'}g</div>
        <div class="apd-macro-lbl">Protein</div>
      </div>
      <div class="apd-macro-item">
        <div class="apd-macro-val">${plan.toplam_karb ?? '—'}g</div>
        <div class="apd-macro-lbl">Karb</div>
      </div>
      <div class="apd-macro-item">
        <div class="apd-macro-val">${plan.toplam_yag ?? '—'}g</div>
        <div class="apd-macro-lbl">Yağ</div>
      </div>
    </div>

    <div class="apd-meals">${mealsHtml}</div>`;
}

function resetPlan() {
  document.getElementById('active-plan-detail').style.display = 'none';
  document.getElementById('plan-form').style.display = 'block';
}

/* ── QUICK ADD FROM PLAN ── */
async function loadQuickAddSection() {
  const container = document.getElementById('quick-add-cards');
  try {
    const d = await getActivePlan();

    if (!d.exists) {
      container.innerHTML = `
        <div class="qab-no-plan" data-action="fxGoToPlanTab">
          📋 Henüz aktif planın yok — Plan Oluştur sekmesine git →
        </div>`;
      return;
    }

    const MEALS = [
      { key: 'kahvalti', label: 'Kahvaltı',  icon: '🍳' },
      { key: 'ogle',     label: 'Öğle',      icon: '🥗' },
      { key: 'aksam',    label: 'Akşam',     icon: '🍽️' },
      { key: 'ara_ogun', label: 'Ara Öğün',  icon: '🥜' }
    ];

    container.innerHTML = MEALS.map(m => {
      const ml  = d.plan[m.key];
      if (!ml) return '';
      const sub = `${ml.kalori ?? '—'} kcal · ${ml.protein ?? '—'}g protein · ${ml.karb ?? '—'}g karb`;
      return `
        <button class="qab" id="qab-${m.key}"
          data-action="quickAddMeal" data-args='["${m.key}","${m.label}"]' type="button">
          <span class="qab-icon">${m.icon}</span>
          <div class="qab-info">
            <div class="qab-title">${m.label} — ${esc(d.plan.isim || 'Aktif Plan')}</div>
            <div class="qab-sub">${sub}</div>
          </div>
          <svg class="qab-check" viewBox="0 0 24 24" fill="none"
               stroke="#CCFF00" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12"/>
          </svg>
          <svg class="qab-plus" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" stroke-width="2.4" stroke-linecap="round">
            <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
          </svg>
        </button>`;
    }).join('');

  } catch (e) {
    container.innerHTML = '';
  }
}

async function quickAddMeal(mealKey, mealLabel, btn) {
  if (btn.classList.contains('qab-done') || btn.disabled) return;
  btn.disabled = true;
  btn.style.opacity = '0.65';

  try {
    const res = await fetch('/api/quick-add-meal', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ meal_key: mealKey })
    });
    const d = await res.json();
    if (d.error) { showToast(d.error, 'error'); btn.disabled = false; btn.style.opacity = '1'; return; }

    // Success state — swap + icon for animated checkmark
    btn.classList.add('qab-done');
    btn.style.opacity = '1';
    btn.querySelector('.qab-plus').style.display  = 'none';

    showToast(`${mealLabel} eklendi ✓`, 'success');
    loadTodayData(); // live-refresh calorie ring + macro bars

  } catch (e) {
    showToast('Eklenemedi: ' + e.message, 'error');
    btn.disabled = false;
    btn.style.opacity = '1';
  }
}

/* ── QUICK ADD FAB ── */
function toggleQuickAdd() {
  quickAddOpen = !quickAddOpen;
  document.getElementById('quick-add-btn').classList.toggle('open', quickAddOpen);
  document.getElementById('quick-add-actions').classList.toggle('open', quickAddOpen);
}
document.addEventListener('click', e => {
  if (quickAddOpen && !e.target.closest('.quick-add-wrap')) {
    quickAddOpen = false;
    document.getElementById('quick-add-btn').classList.remove('open');
    document.getElementById('quick-add-actions').classList.remove('open');
  }
});

/* ── SU TAKİBİ (Water tracking) — sunucuda saklanır (/water), cihazlar arası senkron ──
   "Su Takibi" sekmesindeki bardak widget'ı ile "Bugün" sekmesindeki Hızlı Ekle
   butonu aynı sayacı paylaşır. localStorage anlık boyama / offline yedek. */
const WATER_GOAL_N = 8;
let waterCount = 0;

function readWaterCache() {
  try {
    const today = new Date().toDateString();
    const s = JSON.parse(localStorage.getItem('fc_water') || '{}');
    return s.date === today ? (s.count || 0) : 0;
  } catch (e) { return 0; }
}
function writeWaterCache(n) {
  try { localStorage.setItem('fc_water', JSON.stringify({ date: new Date().toDateString(), count: n })); } catch (e) {}
}
function setWaterSub(n) {
  const sub = document.getElementById('qab-water-sub');
  if (sub) sub.textContent = `Bugün ${n} / ${WATER_GOAL_N} bardak`;
}

/* Sayacı kaydet: bellek + cache + sunucu (fire-and-forget) */
function saveWaterCount(n) {
  waterCount = n;
  writeWaterCache(n);
  fetch('/water', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ count: n })
  }).catch(() => {});
}

/* Bardak widget'ını ("Su Takibi" sekmesi) + Hızlı Ekle altyazısını çiz */
function renderWater(count, animate) {
  waterCount = count;
  setWaterSub(count);

  const numEl = document.getElementById('water-num');
  if (numEl) {
    const prev = parseInt(numEl.textContent, 10) || 0;
    numEl.textContent = count;
    if (animate !== false && count > prev) {
      numEl.classList.remove('bump'); void numEl.offsetWidth; numEl.classList.add('bump');
      setTimeout(() => numEl.classList.remove('bump'), 220);
    }
  }
  const bar = document.getElementById('water-bar');
  if (bar) bar.style.width = Math.min(count / WATER_GOAL_N * 100, 100) + '%';

  document.querySelectorAll('.wg').forEach((g, i) => {
    const wasFilled = g.classList.contains('filled');
    const nowFilled = i < count;
    g.classList.toggle('filled', nowFilled);
    if (animate !== false && nowFilled && !wasFilled) {
      g.classList.remove('just-filled'); void g.offsetWidth; g.classList.add('just-filled');
      setTimeout(() => g.classList.remove('just-filled'), 340);
    }
  });

  const btn = document.getElementById('water-btn');
  if (btn) {
    if (count >= WATER_GOAL_N) {
      btn.disabled = true;
      btn.innerHTML = '✓ &nbsp;Günlük hedefe ulaştın!';
    } else {
      btn.disabled = false;
      btn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> Bardak Ekle';
    }
  }
}

/* Bardakları oluştur ("Su Takibi" sekmesi) */
function buildWaterGlasses() {
  const c = document.getElementById('water-glasses');
  if (!c) return;
  c.innerHTML = '';
  for (let i = 0; i < WATER_GOAL_N; i++) {
    const g = document.createElement('div');
    g.className = 'wg';
    g.innerHTML = '<div class="wg-fill"></div>';
    g.addEventListener('click', () => {
      const cur = waterCount;
      const next = i < cur ? i : i + 1;
      saveWaterCount(next);
      renderWater(next);
      if (next > cur) {
        if (next === WATER_GOAL_N) showToast('Günlük su hedefine ulaştın!', 'success');
        else showToast(`${next}. bardak içildi`, 'info');
      }
    });
    c.appendChild(g);
  }
}

/* "Bardak Ekle" butonu ("Su Takibi" sekmesi) */
function addWater() {
  if (waterCount >= WATER_GOAL_N) return;
  const next = waterCount + 1;
  saveWaterCount(next);
  renderWater(next);
  if (next === WATER_GOAL_N) showToast('Günlük su hedefine ulaştın!', 'success');
  else showToast(`${next}. bardak içildi`, 'info');
}

/* "Hızlı Ekle" su butonu ("Bugün" sekmesi) */
async function quickAddWater(btn) {
  if (waterCount >= WATER_GOAL_N) { showToast('Günlük su hedefine ulaştın! 🎉', 'success'); return; }
  const next = waterCount + 1;
  saveWaterCount(next);
  renderWater(next);
  btn.querySelector('.qab-plus').style.display = 'none';
  btn.querySelector('.qab-check').style.display = '';
  if (next >= WATER_GOAL_N) showToast('Günlük su hedefine ulaştın! 🎉', 'success');
  else showToast(`${next}. bardak içildi 💧`, 'info');
  setTimeout(() => {
    btn.querySelector('.qab-plus').style.display = '';
    btn.querySelector('.qab-check').style.display = 'none';
  }, 1500);
}

async function initWaterButton() {
  buildWaterGlasses();
  renderWater(readWaterCache(), false);   // önbellekten anlık
  try {
    const res = await fetch('/water');
    if (res.ok) {
      const d = await res.json();
      writeWaterCache(d.count || 0);
      renderWater(d.count || 0, false);
    }
  } catch (e) {}
}

/* ── WATER MODAL ── */
function openWater()  { document.getElementById('water-modal').classList.add('open'); }
function closeWater() { document.getElementById('water-modal').classList.remove('open'); }
function logWater() {
  const ml = document.getElementById('water-amount').value;
  closeWater();
  showToast(`${ml} ml su kaydedildi 💧`, 'success');
}

/* ── SCROLL TO FORM ── */
function scrollToForm() {
  // Switch to today tab and scroll to log form
  const todayTab = document.querySelector('.tab-btn');
  switchTab('today', todayTab);
  setTimeout(() => {
    document.getElementById('meal-input').scrollIntoView({ behavior:'smooth', block:'center' });
    document.getElementById('meal-input').focus();
  }, 300);
}

/* ── FOOD AUTOCOMPLETE ── */
let acTimeout = null;
let selectedFoods = [];
let acController = null;

document.getElementById('food-search-input').addEventListener('input', function() {
  clearTimeout(acTimeout);
  const q = this.value.trim();
  if (q.length < 2) {
    document.getElementById('food-autocomplete-dropdown').style.display = 'none';
    return;
  }
  acTimeout = setTimeout(() => searchFood(q), 350);
});

async function searchFood(query) {
  const dropdown = document.getElementById('food-autocomplete-dropdown');
  if (acController) acController.abort();
  acController = new AbortController();
  try {
    const res = await fetch('/api/food/search?q=' + encodeURIComponent(query), { signal: acController.signal });
    const data = await res.json();
    if (!data.results.length) {
      dropdown.innerHTML = '<div class="autocomplete-item" style="color:var(--text-3);cursor:default;">Sonuç bulunamadı — serbest metin kullanabilirsiniz</div>';
      dropdown.style.display = 'block';
      return;
    }
    dropdown.innerHTML = data.results.map(f => {
      const fj = JSON.stringify(f).replace(/&/g,'&amp;').replace(/'/g,'&#39;').replace(/"/g,'&quot;');
      return `<div class="autocomplete-item" data-action="fxSelectFood" data-f="${fj}">
        <div class="ac-name">${esc(f.name)}${f.brand ? ' <span class="ac-brand">(' + esc(f.brand) + ')</span>' : ''}</div>
        <div class="ac-macros"><strong>${Math.round(f.macros.calories)}</strong> kcal · P: ${Math.round(f.macros.protein)}g · K: ${Math.round(f.macros.carbs)}g · Y: ${Math.round(f.macros.fat)}g${f.serving ? ' · ' + esc(f.serving) : ''}</div>
      </div>`;
    }).join('');
    dropdown.style.display = 'block';
  } catch (e) {
    if (e.name !== 'AbortError') dropdown.style.display = 'none';
  }
}

function selectFood(food) {
  selectedFoods.push(food);
  document.getElementById('food-search-input').value = '';
  document.getElementById('food-autocomplete-dropdown').style.display = 'none';
  renderSelectedFoods();
}

function removeSelectedFood(index) {
  selectedFoods.splice(index, 1);
  renderSelectedFoods();
}

function renderSelectedFoods() {
  const container = document.getElementById('selected-foods-list');
  const items = document.getElementById('selected-foods-items');
  const totals = document.getElementById('selected-foods-totals');
  if (!selectedFoods.length) { container.style.display = 'none'; return; }
  container.style.display = 'block';
  items.innerHTML = selectedFoods.map((f, i) => `
    <div class="selected-food-item">
      <button class="sf-remove" data-action="removeSelectedFood" data-args="[${i}]">✕</button>
      <div style="flex:1;min-width:0;">
        <div style="font-size:13px;color:var(--text);">${esc(f.name)}</div>
        <div style="font-size:11px;color:var(--text-3);">${Math.round(f.per_100g.calories)} kcal · P:${Math.round(f.per_100g.protein)}g K:${Math.round(f.per_100g.carbs)}g Y:${Math.round(f.per_100g.fat)}g</div>
      </div>
    </div>`).join('');
  const t = selectedFoods.reduce((acc, f) => ({
    cal: acc.cal + (f.per_100g.calories || 0), p: acc.p + (f.per_100g.protein || 0),
    k: acc.k + (f.per_100g.carbs || 0), y: acc.y + (f.per_100g.fat || 0)
  }), {cal:0, p:0, k:0, y:0});
  totals.innerHTML = 'Toplam: <strong style="color:var(--volt);">' + Math.round(t.cal) + '</strong> kcal · P: ' + Math.round(t.p) + 'g · K: ' + Math.round(t.k) + 'g · Y: ' + Math.round(t.y) + 'g';
}

document.addEventListener('click', e => {
  if (!e.target.closest('#food-search-input') && !e.target.closest('#food-autocomplete-dropdown'))
    document.getElementById('food-autocomplete-dropdown').style.display = 'none';
});


/* ── SERVINGS CACHE ── */
const _servingsCache = {};
async function fetchServings(foodIdOrName) {
  if (_servingsCache[foodIdOrName]) return _servingsCache[foodIdOrName];
  try {
    let url, data;
    if (foodIdOrName && /^\d+$/.test(String(foodIdOrName))) {
      url = '/api/food/' + foodIdOrName + '/servings';
      const res = await fetch(url);
      data = await res.json();
    } else {
      url = '/api/food/servings-by-name?name=' + encodeURIComponent(foodIdOrName);
      const res = await fetch(url);
      data = await res.json();
      if (data.food_id) _smFood && (_smFood.food_id = data.food_id);
    }
    if (data.servings && data.servings.length) {
      _servingsCache[foodIdOrName] = data.servings;
      return data.servings;
    }
  } catch (e) { /* serving fetch failed — gram fallback stays visible */ }
  return null;
}

/* ── DIARY BUILDER ── */
const DIARY_MEALS = [
  { key: 'Kahvaltı', icon: '\u{1F373}' },
  { key: 'Öğle',     icon: '\u{1F957}' },
  { key: 'Akşam',    icon: '\u{1F37D}\u{FE0F}' },
  { key: 'Ara Öğün', icon: '\u{1F95C}' }
];

async function loadDiary() {
  try {
    const res = await fetch('/api/diary/today');
    const data = await res.json();
    renderDiary(data);
  } catch (e) { console.error('loadDiary', e); }
}

function renderDiary(data) {
  const container = document.getElementById('diary-meals');
  const mealMap = {};
  (data.meals || []).forEach(m => { mealMap[m.meal_name] = m; });

  container.innerHTML = DIARY_MEALS.map(dm => {
    const meal = mealMap[dm.key];
    const mealId = meal ? meal.id : '';
    const items = meal ? meal.items : [];
    const isLogged = meal ? meal.is_logged : false;
    const totals = meal ? meal.totals : {calories:0, protein:0, carbs:0, fat:0};

    const itemsHtml = items.map(item => {
      let unitHtml;
      const cached = item.fatsecret_food_id ? _servingsCache[item.fatsecret_food_id] : null;
      if (item.serving_id && cached) {
        const opts = cached.map(s =>
          `<option value="${s.serving_id}" ${s.serving_id === item.serving_id ? 'selected' : ''}>${esc(formatServingLabel(s.serving_description, s.metric_serving_amount, s.calories, s.is_bulk))}</option>`
        ).join('');
        const qVal = item.serving_quantity || 1;
        unitHtml = `<select class="diary-serving-select" data-action-change="fxUpdateDiaryServing" data-item-id="${item.id}" data-food-id="${item.fatsecret_food_id}" ${isLogged ? 'disabled' : ''}>${opts}</select>
          <input type="number" class="diary-qty-input" value="${qVal}" min="0.5" step="0.5"
            data-action-change="fxUpdateDiaryServingQty" data-item-id="${item.id}" data-food-id="${item.fatsecret_food_id}" ${isLogged ? 'disabled' : ''}>`;
      } else if (item.serving_id) {
        const qVal = item.serving_quantity || 1;
        unitHtml = `<span class="diary-serving-label">${esc(item.serving_description || '')}</span>
          <input type="number" class="diary-qty-input" value="${qVal}" min="0.5" step="0.5"
            data-action-change="fxUpdateDiaryServingQtyOnly" data-item-id="${item.id}" ${isLogged ? 'disabled' : ''}>`;
        if (item.fatsecret_food_id && !isLogged) {
          fetchServings(item.fatsecret_food_id).then(s => { if (s) loadDiary(); });
        }
      } else {
        unitHtml = `<input type="number" class="diary-gram-input" value="${item.grams}" min="1" step="10"
            data-action-change="fxUpdateDiaryGrams" data-item-id="${item.id}" ${isLogged ? 'disabled' : ''}>
          <span style="font-size:11px;color:var(--text-3);">g</span>`;
      }
      return `<div class="diary-food-row" data-item-id="${item.id}">
        <div style="flex:1;min-width:0;">
          <div style="font-size:13px;color:var(--text);">${esc(item.food_name)}</div>
          <div style="font-size:11px;color:var(--text-3);">${Math.round(item.calories)} kcal · P:${Math.round(item.protein)}g K:${Math.round(item.carbs)}g Y:${Math.round(item.fat)}g</div>
        </div>
        ${unitHtml}
        ${!isLogged ? '<button class="sf-remove" data-action="deleteDiaryItem" data-args="[' + item.id + ']">✕</button>' : ''}
      </div>`;
    }).join('');

    return `
      <div class="card diary-meal-card" style="padding:20px;margin-bottom:12px;" data-meal-name="${dm.key}" data-meal-id="${mealId}">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;">
          <div style="display:flex;align-items:center;gap:10px;">
            <span style="font-size:20px;">${dm.icon}</span>
            <span style="font-family:'Bebas Neue';font-size:18px;letter-spacing:1.5px;color:var(--text);">${dm.key}</span>
          </div>
          <span style="font-family:'Bebas Neue';font-size:16px;color:var(--volt);">${Math.round(totals.calories)} kcal</span>
        </div>
        <div class="diary-items-list">${itemsHtml}</div>
        ${!isLogged ? `
        <div style="position:relative;margin-top:10px;">
          <input class="fc-input diary-food-search" placeholder="Besin ara..."
            data-action-input="fxDiaryFoodSearch" data-meal="${dm.key}" autocomplete="off">
          <div class="autocomplete-dropdown diary-ac" style="display:none;"></div>
        </div>
        <button class="btn-volt w-full" style="margin-top:10px;" data-action="logDiaryMeal" data-args='["${dm.key}"]'>ÖĞÜNÜ KAYDET</button>
        ` : `
        <div style="text-align:center;padding:8px;color:#00C48C;font-size:12px;font-weight:600;letter-spacing:0.1em;">✓ KAYDEDİLDİ</div>
        `}
      </div>`;
  }).join('');

  updateDiaryTotals(data.totals);
}

function updateDiaryTotals(totals) {
  const el = document.getElementById('diary-grand-total');
  if (totals.calories > 0) {
    el.style.display = 'block';
    document.getElementById('diary-total-cal').textContent = Math.round(totals.calories);
    document.getElementById('diary-total-pro').textContent = Math.round(totals.protein) + 'g';
    document.getElementById('diary-total-karb').textContent = Math.round(totals.carbs) + 'g';
    document.getElementById('diary-total-fat').textContent = Math.round(totals.fat) + 'g';
  } else {
    el.style.display = 'none';
  }
}

let diaryAcTimeout = null;
let diaryAcController = null;
function diaryFoodSearch(input, mealName) {
  clearTimeout(diaryAcTimeout);
  const q = input.value.trim();
  const dropdown = input.nextElementSibling;
  if (q.length < 2) { dropdown.style.display = 'none'; return; }
  diaryAcTimeout = setTimeout(async () => {
    if (diaryAcController) diaryAcController.abort();
    diaryAcController = new AbortController();
    try {
      const res = await fetch('/api/food/search?q=' + encodeURIComponent(q), { signal: diaryAcController.signal });
      const data = await res.json();
      if (!data.results.length) {
        dropdown.innerHTML = '<div class="autocomplete-item" style="color:var(--text-3);">Sonuç bulunamadı</div>';
        dropdown.style.display = 'block';
        return;
      }
      dropdown.innerHTML = data.results.map(f => {
        const fj = JSON.stringify(f).replace(/&/g,'&amp;').replace(/'/g,'&#39;').replace(/"/g,'&quot;');
        return `<div class="autocomplete-item" data-action="fxAddDiaryFood" data-meal="${mealName}" data-f="${fj}">
          <div class="ac-name">${esc(f.name)}</div>
          <div class="ac-macros"><strong>${Math.round(f.per_100g.calories)}</strong> kcal/100g · P:${Math.round(f.per_100g.protein)}g · K:${Math.round(f.per_100g.carbs)}g · Y:${Math.round(f.per_100g.fat)}g${f.serving && f.is_per_serving ? ' · ' + esc(f.serving) : ''}</div>
        </div>`;
      }).join('');
      dropdown.style.display = 'block';
    } catch (e) {
      if (e.name !== 'AbortError') dropdown.style.display = 'none';
    }
  }, 350);
}

/* ── SERVING MODAL STATE ── */
let _smFood = null;
let _smMealName = null;
let _smServings = null;

function openServingModal(mealName, food) {
  _smFood = food;
  _smMealName = mealName;
  _smServings = null;

  const searchInput = document.querySelector('[data-meal-name="' + mealName + '"] .diary-food-search');
  if (searchInput) { searchInput.value = ''; searchInput.nextElementSibling.style.display = 'none'; }

  document.getElementById('sm-food-name').textContent = food.name;
  document.getElementById('sm-brand').textContent = food.brand || '';
  document.getElementById('sm-serving-row').style.display = 'none';
  document.getElementById('sm-qty-row').style.display = 'none';
  // Always show gram input as immediate default
  document.getElementById('sm-gram-row').style.display = 'block';
  document.getElementById('sm-gram-input').value = 100;
  document.getElementById('sm-qty-input').value = 1;
  document.getElementById('sm-confirm-btn').disabled = false;
  document.getElementById('serving-modal').classList.add('open');
  updateSmPreview();

  const lookupKey = food.food_id || food.name;
  if (!lookupKey) {
    // No food_id and no name — stay in gram-only mode
    document.getElementById('sm-loading').style.display = 'none';
    return;
  }
  document.getElementById('sm-loading').style.display = 'flex';
  fetchServings(lookupKey).then(servings => {
    document.getElementById('sm-loading').style.display = 'none';
    if (servings && servings.length) {
      _smServings = servings;
      const select = document.getElementById('sm-serving-select');
      select.innerHTML = servings.map(s =>
        '<option value="' + s.serving_id + '">' +
        esc(formatServingLabel(s.serving_description, s.metric_serving_amount, s.calories, s.is_bulk)) +
        '</option>'
      ).join('');
      // Varsayılan: devasa "tüm tarif" porsiyonu (is_bulk) ASLA seçilmez.
      // Önce makul tek porsiyon, yoksa 100 g bazına düş.
      const is100 = s => s.serving_description === '100 g' || s.serving_description === '100g';
      let preferred = servings.findIndex(s => !s.is_bulk && !is100(s));
      if (preferred < 0) preferred = servings.findIndex(is100);
      if (preferred >= 0) select.selectedIndex = preferred;
      document.getElementById('sm-serving-row').style.display = 'block';
      document.getElementById('sm-qty-row').style.display = 'block';
      // Hide gram row when servings are available (dropdown has 100g option)
      document.getElementById('sm-gram-row').style.display = 'none';
    }
    updateSmPreview();
  });
}

function closeServingModal() {
  document.getElementById('serving-modal').classList.remove('open');
  _smFood = null; _smMealName = null; _smServings = null;
}

function updateSmPreview() {
  let cal = 0, pro = 0, carb = 0, fat = 0;
  if (_smServings) {
    const select = document.getElementById('sm-serving-select');
    const srv = _smServings.find(s => s.serving_id === select.value);
    const qty = parseFloat(document.getElementById('sm-qty-input').value) || 1;
    if (srv) {
      cal = srv.calories * qty; pro = srv.protein * qty;
      carb = srv.carbs * qty; fat = srv.fat * qty;
    }
  } else if (_smFood) {
    const grams = parseFloat(document.getElementById('sm-gram-input').value) || 100;
    const p = _smFood.per_100g;
    const scale = grams / 100;
    cal = p.calories * scale; pro = p.protein * scale;
    carb = p.carbs * scale; fat = p.fat * scale;
  }
  document.getElementById('sm-cal').textContent = Math.round(cal);
  document.getElementById('sm-pro').textContent = Math.round(pro) + 'g';
  document.getElementById('sm-carb').textContent = Math.round(carb) + 'g';
  document.getElementById('sm-fat').textContent = Math.round(fat) + 'g';
}

async function confirmServingModal() {
  if (!_smFood || !_smMealName) return;
  const btn = document.getElementById('sm-confirm-btn');
  btn.disabled = true;
  btn.textContent = 'EKLENİYOR...';

  const card = document.querySelector('[data-meal-name="' + _smMealName + '"]');
  let mealId = card.dataset.mealId;
  if (!mealId) {
    const res = await fetch('/api/diary/meal', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ meal_name: _smMealName })
    });
    const d = await res.json();
    mealId = d.meal_id;
    card.dataset.mealId = mealId;
  }

  let body;
  if (_smServings) {
    const select = document.getElementById('sm-serving-select');
    const srv = _smServings.find(s => s.serving_id === select.value);
    const qty = parseFloat(document.getElementById('sm-qty-input').value) || 1;
    if (srv) {
      body = {
        food_name: _smFood.name,
        fatsecret_food_id: _smFood.food_id,
        serving_id: srv.serving_id,
        serving_description: srv.serving_description,
        serving_quantity: qty,
        serving_calories: srv.calories,
        serving_protein: srv.protein,
        serving_carbs: srv.carbs,
        serving_fat: srv.fat,
        metric_serving_amount: srv.metric_serving_amount,
      };
    }
  }
  if (!body) {
    const grams = parseFloat(document.getElementById('sm-gram-input').value) || 100;
    body = {
      food_name: _smFood.name, grams: grams,
      per_100g: _smFood.per_100g,
      fatsecret_food_id: _smFood.food_id || ''
    };
  }

  try {
    const res = await fetch('/api/diary/meal/' + mealId + '/item', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const d = await res.json();
    if (d.error) { showToast(d.error, 'error'); return; }
    closeServingModal();
    loadDiary();
  } catch (e) { showToast('Ekleme hatası', 'error'); }
  btn.disabled = false;
  btn.textContent = 'EKLE';
}

function addDiaryFood(mealName, food) {
  openServingModal(mealName, food);
}

async function updateDiaryGrams(itemId, grams) {
  if (grams < 1) return;
  try {
    await fetch('/api/diary/item/' + itemId, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ grams: parseFloat(grams) })
    });
    loadDiary();
  } catch (e) { showToast('Güncelleme hatası', 'error'); }
}

async function updateDiaryServing(itemId, servingId, foodId) {
  const servings = _servingsCache[foodId];
  if (!servings) return;
  const srv = servings.find(s => s.serving_id === servingId);
  if (!srv) return;
  const qtyInput = document.querySelector(`[data-item-id="${itemId}"] .diary-qty-input`);
  const qty = qtyInput ? parseFloat(qtyInput.value) || 1 : 1;
  try {
    await fetch('/api/diary/item/' + itemId, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        serving_id: srv.serving_id,
        serving_description: srv.serving_description,
        serving_quantity: qty,
        serving_calories: srv.calories,
        serving_protein: srv.protein,
        serving_carbs: srv.carbs,
        serving_fat: srv.fat,
        metric_serving_amount: srv.metric_serving_amount,
      })
    });
    loadDiary();
  } catch (e) { showToast('Güncelleme hatası', 'error'); }
}

async function updateDiaryServingQty(itemId, qty, foodId) {
  qty = parseFloat(qty);
  if (!qty || qty < 0.5) return;
  const servings = _servingsCache[foodId];
  const row = document.querySelector(`[data-item-id="${itemId}"]`);
  const select = row ? row.querySelector('.diary-serving-select') : null;
  const servingId = select ? select.value : null;
  const srv = servings && servingId ? servings.find(s => s.serving_id === servingId) : null;
  try {
    const body = srv ? {
      serving_id: srv.serving_id,
      serving_description: srv.serving_description,
      serving_quantity: qty,
      serving_calories: srv.calories,
      serving_protein: srv.protein,
      serving_carbs: srv.carbs,
      serving_fat: srv.fat,
      metric_serving_amount: srv.metric_serving_amount,
    } : { serving_quantity: qty };
    await fetch('/api/diary/item/' + itemId, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    loadDiary();
  } catch (e) { showToast('Güncelleme hatası', 'error'); }
}

async function updateDiaryServingQtyOnly(itemId, qty) {
  qty = parseFloat(qty);
  if (!qty || qty < 0.5) return;
  try {
    await fetch('/api/diary/item/' + itemId, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ serving_quantity: qty })
    });
    loadDiary();
  } catch (e) { showToast('Güncelleme hatası', 'error'); }
}

async function deleteDiaryItem(itemId) {
  try {
    await fetch('/api/diary/item/' + itemId, { method: 'DELETE' });
    loadDiary();
  } catch (e) { showToast('Silme hatası', 'error'); }
}

async function logDiaryMeal(mealName) {
  const card = document.querySelector('[data-meal-name="' + mealName + '"]');
  const mealId = card.dataset.mealId;
  if (!mealId) { showToast('Önce besin ekle', 'error'); return; }
  try {
    const res = await fetch('/api/diary/meal/' + mealId + '/log', { method: 'POST' });
    const d = await res.json();
    if (d.error) { showToast(d.error, 'error'); return; }
    showToast(mealName + ' kaydedildi! ✓', 'success');
    if (window.fxTrackOnce) fxTrackOnce('first_meal_logged');
    if (window.fxActivation) fxActivation('meal');
    if (d.quest_awarded) showToast('\u{1F3AF} +' + d.quest_awarded.xp + ' XP!', 'success');
    loadDiary();
  } catch (e) { showToast('Kayıt hatası', 'error'); }
}

document.addEventListener('click', e => {
  if (!e.target.closest('.diary-food-search') && !e.target.closest('.diary-ac'))
    document.querySelectorAll('.diary-ac').forEach(d => d.style.display = 'none');
});


/* ── SIDEBAR AVATAR INITIAL ── */
(function() {
  const name = document.getElementById('sb-name')?.textContent?.trim() || '';
  const av   = document.getElementById('sb-avatar');
  if (av && name) av.textContent = name[0].toUpperCase();
})();

/* ── INIT ── */
populateFoods();
loadTodayData();
loadQuickAddSection();
loadActivePlan();
initWaterButton();
