
function newIdempotencyKey() {
  return (window.crypto && window.crypto.randomUUID)
    ? window.crypto.randomUUID()
    : ('meal-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2));
}

function mealWriteHeaders() {
  return { 'Content-Type': 'application/json',
           'Idempotency-Key': newIdempotencyKey() };
}

let selectedMealType = 'Kahvaltı';

/* ── i18n (PR5) ──
   Görünen metin İngilizce olur; backend'e giden KANONIK değerler (öğün tipi,
   plan hızlı-seçim besin adı, skor etiketi) Türkçe KALIR → FatSecret araması ve
   MealLog.ogun eşleşmesi bozulmaz. Bu dosyada bazı fonksiyonlarda yerel `t`
   değişkeni var (ör. showToast, reduce) → global çeviriyi __t aliasıyla çağır. */
var __t = (window.t) || function (k) { return k; };
var _EN = (window.LOCALE === 'en');
/* Makro kısaltmaları: TR P/K/Y → EN P/C/F (yalnızca görünen etiket). */
var MA = _EN ? { p: 'P', k: 'C', y: 'F' } : { p: 'P', k: 'K', y: 'Y' };
/* Öğün tipi: kanonik TR değer → görünen etiket. */
var MEAL_LABELS_EN = { 'Kahvaltı': 'Breakfast', 'Öğle': 'Lunch', 'Akşam': 'Dinner', 'Ara Öğün': 'Snack' };
/* Plan hızlı-seçim besinleri: değer backend'e gider (TR), etiket görünür (EN). */
var FOOD_LABELS_EN = {
  'Tavuk Göğsü':'Chicken Breast','Yumurta':'Egg','Ton Balığı':'Tuna','Kırmızı Et':'Red Meat','Yoğurt':'Yogurt','Somon':'Salmon','Hindi':'Turkey',
  'Mercimek':'Lentils','Nohut':'Chickpeas','Tofu':'Tofu','Kinoa':'Quinoa','Edamame':'Edamame','Fasulye':'Beans',
  'Yulaf Ezmesi':'Oatmeal','Pirinç':'Rice','Bulgur':'Bulgur','Tatlı Patates':'Sweet Potato','Tam Buğday Ekmeği':'Whole Wheat Bread','Muz':'Banana','Elma':'Apple','Makarna':'Pasta',
  'Zeytinyağı':'Olive Oil','Avokado':'Avocado','Badem':'Almonds','Ceviz':'Walnuts','Fındık':'Hazelnuts','Fıstık Ezmesi':'Peanut Butter'
};
function mealLabel(v)  { return (_EN && MEAL_LABELS_EN[v])  ? MEAL_LABELS_EN[v]  : v; }
function foodLabel(v)  { return (_EN && FOOD_LABELS_EN[v])  ? FOOD_LABELS_EN[v]  : v; }

/* ── HTML ESCAPE (XSS guard — innerHTML'e giren kullanıcı/AI/FatSecret metni) ── */
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/* ── SAFE NUMERIC SLOT (F2) ──
   Plan macros (kalori/protein/karb/yag, toplam_*) and the plan score are
   interpolated into innerHTML WITHOUT esc(). The server schema
   (app/services/nutrition_plan_schema.py) now guarantees they are numbers on
   the way in; this guarantees the page renders nothing but digits on the way
   out — including for rows persisted before that schema existed, which the
   read path still has to tolerate. Anything else becomes the placeholder. */
function fmtNum(v, fallback = '—') {
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : fallback;
  if (typeof v === 'string' && /^-?\d+(\.\d+)?$/.test(v)) return v;
  return fallback;
}

/* ── SERVING LABEL HELPER ── */
function formatServingLabel(desc, metricAmt, calories, isBulk) {
  let label = desc;
  if (metricAmt > 0 && !/^\d+\s*g$/i.test(desc))
    label += ' (' + Math.round(metricAmt) + 'g)';
  label += ' — ' + Math.round(calories) + ' kcal';
  if (isBulk) label += ' ⚠ ' + __t('nutrition.full_recipe');
  return label;
}

/* ── TOAST SYSTEM ── */
function showToast(msg, type = 'info', duration = 3500) {
  const wrap = document.getElementById('toast-wrap');
  const icons = { success: '✓', error: '✕', info: 'ℹ' };
  const t = document.createElement('div');
  t.className = `toast toast-${type}`;
  // F8: msg is text, never markup — it can carry server `error` strings and
  // browser exception text, so it must not cross an HTML parser boundary.
  const icon = document.createElement('span');
  icon.className = 'toast-icon';
  icon.textContent = icons[type] || '•';
  const text = document.createElement('span');
  text.textContent = msg == null ? '' : String(msg);
  t.appendChild(icon);
  t.appendChild(text);
  wrap.appendChild(t);
  setTimeout(() => {
    t.classList.add('hide');
    setTimeout(() => t.remove(), 300);
  }, duration);
}

/* Nutrition navigation: two modes and native Today disclosures.
   Only UI state enters history; URLs and all business handlers stay canonical. */
let _nutritionNavigation = { mode: 'today', tools: {} };
const _nutritionTools = ['diary', 'history', 'water'];
const _nutritionLoadedTools = new Set();

function nutritionNavigationState(raw) {
  const tools = {};
  _nutritionTools.forEach(name => { tools[name] = Boolean(raw && raw.tools && raw.tools[name] === true); });
  return { mode: raw && raw.mode === 'plan' ? 'plan' : 'today', tools };
}

function applyNutritionNavigation(raw, refreshToday = false, focusId = null) {
  const next = nutritionNavigationState(raw);
  const previous = _nutritionNavigation;
  _nutritionNavigation = next;
  ['today', 'plan'].forEach(name => {
    const selected = next.mode === name;
    const tab = document.getElementById('nutrition-tab-' + name);
    const panel = document.getElementById('panel-' + name);
    tab.classList.toggle('active', selected);
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    panel.classList.toggle('active', selected);
    panel.hidden = !selected;
  });
  _nutritionTools.forEach(name => {
    document.getElementById('nutrition-tool-' + name).open = next.tools[name];
    if (next.mode === 'today' && next.tools[name] &&
        (!previous.tools[name] || !_nutritionLoadedTools.has(name))) {
      _nutritionLoadedTools.add(name);
      if (name === 'diary') loadDiary();
      if (name === 'history') loadMealHistory();
    }
  });
  if (refreshToday && next.mode === 'today') { loadTodayData(); loadQuickAddSection(); }
  if (focusId) document.getElementById(focusId)?.focus();
}

function rememberNutritionNavigation() {
  history.pushState({ ...history.state, nutritionNavigation: _nutritionNavigation }, '', location.href);
}

// Preserve the published function and all actual in-page entry intents.
function switchTab(name, btn) {
  if (!['today', 'plan', ..._nutritionTools].includes(name)) return;
  const next = nutritionNavigationState(_nutritionNavigation);
  next.mode = name === 'plan' ? 'plan' : 'today';
  if (_nutritionTools.includes(name)) next.tools[name] = true;
  const changed = next.mode !== _nutritionNavigation.mode ||
    _nutritionTools.some(tool => next.tools[tool] !== _nutritionNavigation.tools[tool]);
  applyNutritionNavigation(next, name === 'today', 'nutrition-tab-' + name);
  if (changed) rememberNutritionNavigation();
}

function initNutritionNavigation() {
  const initial = nutritionNavigationState(history.state?.nutritionNavigation);
  applyNutritionNavigation(initial);
  history.replaceState({ ...history.state, nutritionNavigation: initial }, '', location.href);
  _nutritionTools.forEach(name => {
    const detail = document.getElementById('nutrition-tool-' + name);
    detail.addEventListener('toggle', function () {
      if (detail.open === _nutritionNavigation.tools[name]) return;
      const next = nutritionNavigationState(_nutritionNavigation);
      next.tools[name] = detail.open;
      applyNutritionNavigation(next);
      rememberNutritionNavigation();
    });
  });
  document.querySelector('.tab-bar').addEventListener('keydown', function (event) {
    if (!event.target.matches('[role="tab"]')) return;
    let name;
    if (event.key === 'Home') name = 'today';
    else if (event.key === 'End') name = 'plan';
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight')
      name = _nutritionNavigation.mode === 'today' ? 'plan' : 'today';
    else return;
    event.preventDefault();
    switchTab(name);
  });
  window.addEventListener('popstate', function (event) {
    const next = nutritionNavigationState(event.state?.nutritionNavigation);
    const changed = _nutritionTools.find(name => next.tools[name] !== _nutritionNavigation.tools[name]);
    const focus = next.mode === 'today' && changed ? changed : next.mode;
    applyNutritionNavigation(next, next.mode !== _nutritionNavigation.mode, 'nutrition-tab-' + focus);
  });
}

/* ── OVERLAY A11Y: Esc ile kapat (açılışta odak: _focusFirstVisible) ── */
/* Escape closes the top-most surface through its OWN close function, so a
   cancelled method hands focus back exactly as its Cancel button does. */
var _ESCAPE_CLOSERS = [
  ['plan-replace-modal', function () { cancelPlanReplace(); }],
  ['photo-modal', function () { closePhotoConfirm(); }],
  ['serving-modal', function () { closeServingModal(); }],
  ['water-modal', function () { closeWater(); }],
  ['manual-sheet', function () { closeManualSheet(); }],
];
document.addEventListener('keydown', function (e) {
  if (e.key !== 'Escape') return;
  var scan = document.getElementById('scan-overlay');
  if (scan && scan.classList.contains('open')) { closeScanOverlay(); return; }
  var chooser = document.getElementById('log-sheet');
  for (var i = 0; i < _ESCAPE_CLOSERS.length; i++) {
    var el = document.getElementById(_ESCAPE_CLOSERS[i][0]);
    if (el && el.classList.contains('open')) { _ESCAPE_CLOSERS[i][1](); return; }
  }
  if (chooser && chooser.classList.contains('open')) dismissLogSheet();
});

/* ── data-action köprüleri (CSP: satır-içi on* yerine) ──
   Tıklanan öğe (eski `this`) bazı eski çağrılarda ortada/başta argümandı ya da
   this.value iletiliyordu; delegasyon öğeyi sona koyduğu için bu ince
   sarmalayıcılar argüman sırasını ve değer okumayı korur. */
function fxGoToPlanTab() { switchTab('plan', document.querySelector('[data-tab-name="plan"]')); }
function fxSelectFood(el) { selectFood(JSON.parse(el.dataset.f)); }
function fxAddDiaryFood(el) { addDiaryFood(el.dataset.meal, JSON.parse(el.dataset.f)); }
function fxDiaryFoodSearch(el) { diaryFoodSearch(el, el.dataset.meal); }
function fxUpdateDiaryServing(el) { updateDiaryServing(el.dataset.itemId, el.value, el.dataset.foodId); }
function fxUpdateDiaryServingQty(el) { updateDiaryServingQty(el.dataset.itemId, el.value, el.dataset.foodId); }
function fxUpdateDiaryServingQtyOnly(el) { updateDiaryServingQtyOnly(el.dataset.itemId, el.value); }
function fxUpdateDiaryGrams(el) { updateDiaryGrams(el.dataset.itemId, el.value); }

/* ── DAILY SUMMARY (NUTR-PR3) ──
   One canonical read (`/meal-log/today`) carries both facts this card shows:
   the consumed totals (MealLog) and the target projection (`nutrition_targets`,
   `targets: null` when none is configured). This code only PRESENTS them:

     intake  loading | confirmed | stale (a refresh failed after a confirmed
             read — the last confirmed values stay, labelled) | unavailable
     target  pending | known | absent (authority published none) |
             unavailable (read failed, or its target was unusable)

   UNKNOWN is never drawn as 0: before a read proves a value the card shows
   "—", and "remaining" exists only when BOTH sides are confirmed. Remaining is
   the one piece of arithmetic here — target minus intake, on the rounded
   numbers the card shows, so the three figures always agree. The server's own
   `remaining` is clamped at zero and cannot say "over target", so it is not
   used for kcal. No macro arithmetic, no percentages, no scores. */
const MACRO_KEYS = ['protein', 'karb', 'yag'];

function isNum(v) { return typeof v === 'number' && Number.isFinite(v); }

/* A usable read has numeric totals and a meals list; anything else is invalid
   and presented as unavailable, never coerced to zeros. */
function validTodayPayload(d) {
  if (!d || typeof d !== 'object' || !Array.isArray(d.meals)) return false;
  const t = d.totals;
  return !!t && ['kalori', ...MACRO_KEYS].every(k => isNum(t[k]));
}

/* → {state:'absent'} | {state:'known', kalori, macros} | {state:'unavailable'} */
function readTarget(d) {
  if (d.targets === null) return { state: 'absent' };
  const t = d.targets;
  if (!t || typeof t !== 'object' || !isNum(t.kalori) || !(t.kalori > 0)) return { state: 'unavailable' };
  const macros = {};
  MACRO_KEYS.forEach(k => { macros[k] = isNum(t[k]) && t[k] > 0 ? t[k] : null; });
  return { state: 'known', kalori: t.kalori, macros };
}

function setIntakeStatus(key) {
  const box = document.getElementById('nut-intake-status');
  document.getElementById('nut-intake-status-text').textContent = key ? __t(key) : '';
  document.getElementById('nut-intake-retry').hidden = !key;
  box.classList.toggle('is-empty', !key);
}

function renderDaySummary(totals, target) {
  const hero = document.getElementById('nut-day');
  hero.dataset.intakeState = 'confirmed';
  hero.dataset.targetState = target.state;
  hero.setAttribute('aria-busy', 'false');
  setIntakeStatus(null);

  const eaten = Math.round(totals.kalori);
  document.getElementById('nut-intake').textContent = eaten;
  document.getElementById('nut-target').textContent = target.state === 'known' ? Math.round(target.kalori) : '—';

  const remaining = document.getElementById('nut-remaining');
  const fill = document.getElementById('bar-kcal');
  if (target.state === 'known') {
    const goal = Math.round(target.kalori);
    const left = goal - eaten;
    remaining.textContent = left >= 0
      ? __t('nutrition.remaining_left', { n: left })
      : __t('nutrition.remaining_over', { n: -left });
    remaining.hidden = false;
    fill.style.width = (Math.min(eaten / goal, 1) * 100) + '%';
    fill.classList.toggle('is-over', left < 0);
  } else {
    remaining.textContent = '';
    remaining.hidden = true;
    fill.style.width = '0%';
  }

  MACRO_KEYS.forEach(k => {
    const val = Math.round(totals[k]);
    document.getElementById('macro-' + k).textContent = val;
    const goal = target.state === 'known' ? target.macros[k] : null;
    const targetEl = document.getElementById('macro-' + k + '-target');
    const bar = document.getElementById('bar-' + k);
    if (goal) {
      targetEl.textContent = '/ ' + Math.round(goal);
      targetEl.hidden = false;
      bar.style.width = (Math.min(val / goal, 1) * 100) + '%';
      bar.parentElement.hidden = false;
    } else {
      targetEl.textContent = '';
      targetEl.hidden = true;
      bar.style.width = '0%';
      bar.parentElement.hidden = true;
    }
  });
  renderPlanTarget(target);
}

/* The read failed. With nothing confirmed yet the whole card is unknown; after
   a confirmed read the values stay but are labelled as not refreshed (PR2's
   post-commit contract keeps them — they are the last truth we have). */
function renderDaySummaryFailure() {
  const hero = document.getElementById('nut-day');
  hero.setAttribute('aria-busy', 'false');
  if (_todayConfirmed) {
    hero.dataset.intakeState = 'stale';
    setIntakeStatus('nutrition.intake_stale');
    return;
  }
  hero.dataset.intakeState = 'unavailable';
  hero.dataset.targetState = 'unavailable';
  document.getElementById('nut-intake').textContent = '—';
  document.getElementById('nut-target').textContent = '—';
  const remaining = document.getElementById('nut-remaining');
  remaining.textContent = '';
  remaining.hidden = true;
  MACRO_KEYS.forEach(k => {
    document.getElementById('macro-' + k).textContent = '—';
    document.getElementById('macro-' + k + '-target').hidden = true;
    document.getElementById('bar-' + k).parentElement.hidden = true;
  });
  setIntakeStatus('nutrition.meals_unavailable');
  renderPlanTarget({ state: 'unavailable' });
}

/* ── MEAL TYPE SELECTOR ── */
function selectMealType(type, el) {
  selectedMealType = type;
  document.querySelectorAll('.meal-type-opt').forEach(o => o.classList.remove('selected'));
  el.classList.add('selected');
}

/* ── LOAD TODAY DATA ── */
let _todayReadGeneration = 0;
let _todayRefreshPending = false;
let _todayConfirmed = false;   // a canonical read has been rendered at least once
async function loadTodayData(notifyFailure = false) {
  const generation = ++_todayReadGeneration;
  if (notifyFailure) _todayRefreshPending = true;
  try {
    const todayRes = await fetch('/meal-log/today');
    if (!todayRes.ok) throw new Error('Today read failed');
    const today = await todayRes.json();
    if (generation !== _todayReadGeneration) return;
    if (!validTodayPayload(today)) throw new Error('Today read invalid');
    renderDaySummary(today.totals, readTarget(today));
    renderTimeline(today.meals);
    _todayConfirmed = true;
    _todayRefreshPending = false;
    // NUTR-PR6: a Next step that asked to retry the ledger is re-read once the
    // ledger is readable again (single flight; no read otherwise).
    if (_nextKind === 'retry') loadDayView();
  } catch (e) {
    console.error('loadTodayData', e);
    if (generation !== _todayReadGeneration) return;
    renderDaySummaryFailure();
    renderTimelineFailure();
    if (_todayRefreshPending)
      showToast(__t('route.macros_unavailable'), 'error');
  }
}

function retryTodayData() { loadTodayData(); }

/* ── NUTR-PR6 NEXT STEP ──
   `next_action` is SERVER-OWNED (app/services/nutrition_day_view.py). This
   script never derives it from totals, targets, water or the plan: it reads
   `GET /nutrition-day-view` once at load (and on an explicit retry only —
   never on a tab switch or redraw, never by polling) and maps an ALLOWLISTED
   kind to an action that already exists. The server's label_key must equal the
   allowlist's; any other kind/key/shape is shown as "no step", never run.
     log_food   → openLogSheet()  (the PR4 chooser; opening it writes nothing)
     set_target → /setup?yeniden=1 (the onboarding form, the one target writer;
                  a constant link in the template, never a URL from JSON)
     retry      → re-read the day view + Today's ledger (the failed read)     */
const NEXT_ACTIONS = Object.freeze({
  log_food: Object.freeze({ label: 'nutrition.next.log_food', lead: 'nutrition.next.log_food_lead' }),
  set_target: Object.freeze({ label: 'nutrition.next.set_target', lead: 'nutrition.next.set_target_lead' }),
  retry: Object.freeze({ label: 'nutrition.next.retry', lead: 'nutrition.next.retry_lead' }),
});
let _dayViewSeq = 0;           // every read takes a ticket; a superseded answer is dropped
let _dayViewInFlight = null;   // single flight: a double retry is ONE request
let _nextKind = null;          // the allowlisted kind currently on screen, or null

function allowedNextAction(view) {
  const na = view && typeof view === 'object' ? view.next_action : null;
  if (!na || typeof na !== 'object' || na.state !== 'available') return null;
  if (typeof na.kind !== 'string' || !Object.prototype.hasOwnProperty.call(NEXT_ACTIONS, na.kind)) return null;
  return NEXT_ACTIONS[na.kind].label === na.label_key ? na.kind : null;
}

function renderNextStep(state, kind) {
  const box = document.getElementById('nut-next');
  const lead = document.getElementById('nut-next-lead');
  const button = document.getElementById('nut-next-action');
  const link = document.getElementById('nut-next-link');
  // Focus inside the section (a retried control, or the heading that held it
  // while loading) follows the section's new control; nothing else moves it.
  const hadFocus = box.contains(document.activeElement);
  _nextKind = kind;
  box.dataset.nextState = state;
  box.dataset.nextKind = kind || '';
  box.setAttribute('aria-busy', state === 'loading' ? 'true' : 'false');
  let leadKey = 'nutrition.next.none';
  if (state === 'loading') leadKey = 'nutrition.next.loading';
  else if (state === 'unavailable') leadKey = 'nutrition.next.unavailable';
  else if (kind) leadKey = NEXT_ACTIONS[kind].lead;
  lead.textContent = __t(leadKey);
  link.hidden = kind !== 'set_target';
  const buttonKey = state === 'unavailable' ? 'nutrition.next.retry'
    : (kind && kind !== 'set_target' ? NEXT_ACTIONS[kind].label : null);
  button.hidden = !buttonKey;
  button.textContent = buttonKey ? __t(buttonKey) : '';
  if (kind === 'log_food') {
    button.setAttribute('aria-haspopup', 'dialog');
    button.setAttribute('aria-controls', 'log-sheet');
  } else {
    button.removeAttribute('aria-haspopup');
    button.removeAttribute('aria-controls');
  }
  if (hadFocus) {
    const into = !link.hidden ? link : (!button.hidden ? button : document.getElementById('nut-next-title'));
    into.focus();
  }
}

function loadDayView() {
  if (_dayViewInFlight) return _dayViewInFlight;
  const seq = ++_dayViewSeq;
  renderNextStep('loading', null);
  const read = (async () => {
    let view = null;
    let ok = false;
    try {
      const res = await fetch('/nutrition-day-view', { headers: { Accept: 'application/json' } });
      if (res.ok) { view = await res.json(); ok = !!view && typeof view === 'object'; }
    } catch (e) { ok = false; }
    if (seq !== _dayViewSeq) return;
    if (!ok) { renderNextStep('unavailable', null); return; }
    const kind = allowedNextAction(view);
    renderNextStep(kind ? 'available' : 'empty', kind);
  })();
  _dayViewInFlight = read;
  read.finally(() => { if (_dayViewInFlight === read) _dayViewInFlight = null; });
  return read;
}

/* The one data-action on the Next step button. It re-checks the allowlist at
   click time, so nothing but the three known actions can ever run. */
function runNextAction(el) {
  const box = document.getElementById('nut-next');
  if (box.dataset.nextState === 'unavailable') { retryDayView(); return; }
  if (!_nextKind || !Object.prototype.hasOwnProperty.call(NEXT_ACTIONS, _nextKind)) return;
  if (_nextKind === 'log_food') openLogSheet();
  else if (_nextKind === 'retry') { retryDayView(); loadTodayData(); }
}

function retryDayView() { return loadDayView(); }

/* ── MEAL TIMELINE ── */
var _SLOT_ICONS = {
  breakfast: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8h1a4 4 0 0 1 0 8h-1"/><path d="M2 8h16v9a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4V8z"/><line x1="6" y1="1" x2="6" y2="4"/><line x1="10" y1="1" x2="10" y2="4"/><line x1="14" y1="1" x2="14" y2="4"/></svg>',
  lunch: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3c4.4 0 8 2.7 8 6v2H4V9c0-3.3 3.6-6 8-6z"/><path d="M4 11h16v2a6 6 0 0 1-6 6h-4a6 6 0 0 1-6-6v-2z"/><path d="M8 19v2M16 19v2"/></svg>',
  dinner: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 2v7c0 1.1.9 2 2 2h.5V22M6 2v9M9 2v9M9 2v7c0 1.1-.9 2-2 2"/><path d="M18 2c-1.7 0-3 2-3 5.5S16 13 18 13s3-2 3-5.5S19.7 2 18 2zM18 13v9"/></svg>',
  snack: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3c.6 2.2 2.2 3.4 2.2 3.4S12.8 7.6 12 9"/><path d="M12 22c-4.2 0-7-3.8-7-8.2C5 9.2 8 6 12 6s7 3.2 7 7.8C19 18.2 16.2 22 12 22z"/></svg>'
};
var SLOTS = [
  { key: 'Kahvaltı', icon: _SLOT_ICONS.breakfast },
  { key: 'Öğle',     icon: _SLOT_ICONS.lunch },
  { key: 'Akşam',    icon: _SLOT_ICONS.dinner },
  { key: 'Ara Öğün', icon: _SLOT_ICONS.snack },
];

function fmtTime(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleTimeString(_EN ? 'en-GB' : 'tr-TR',
      { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Istanbul' });
  } catch (e) { return ''; }
}

var _MEAL_PLACEHOLDER_SVG = '<svg viewBox="0 0 24 24"><path d="M3 2v7c0 1.1.9 2 2 2h.5V22M6 2v9M9 2v9M9 2v7c0 1.1-.9 2-2 2"/><path d="M18 2c-1.7 0-3 2-3 5.5S16 13 18 13s3-2 3-5.5S19.7 2 18 2zM18 13v9"/></svg>';

function mealCardHTML(m) {
  var img = m.photo_url
    ? '<img class="mc-img" src="' + esc(m.photo_url) + '" alt="">'
    : '<div class="mc-img">' + _MEAL_PLACEHOLDER_SVG + '</div>';
  var time = fmtTime(m.created_at);
  /* F1/N9: the correction action, and only for a row the server published an
     identity + revision for. /meal-log/today publishes those; history does not,
     so a past-day card cannot render one. */
  var del = (m.entry_token && m.revision)
    ? '<button class="mc-del" data-action="deleteMeal" data-args=\'["' +
        esc(m.entry_token) + '","' + esc(m.revision) + '",' +
        (m.has_photo ? 'true' : 'false') + ']\' aria-label="' +
        __t('nutrition.delete_meal') + '" title="' + __t('nutrition.delete_meal') + '">' +
        '<svg viewBox="0 0 24 24"><path d="M3 6h18"/><path d="M8 6V4h8v2"/>' +
        '<path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/></svg>' +
      '</button>'
    : '';
  return '<div class="meal-card">' + img +
    '<div class="mc-body"><div class="mc-title">' + esc(m.yemekler) + '</div>' +
      '<div class="mc-macros">' +
        '<span>' + Math.round(m.kalori || 0) + ' kcal</span>' +
        '<span>' + MA.p + ' <strong>' + Math.round(m.protein || 0) + '</strong></span>' +
        '<span>' + MA.k + ' <strong>' + Math.round(m.karb || 0) + '</strong></span>' +
        '<span>' + MA.y + ' <strong>' + Math.round(m.yag || 0) + '</strong></span>' +
      '</div>' +
      (time ? '<div class="mc-time">' + time + '</div>' : '') +
    '</div>' +
    '<div class="mc-side">' +
      '<button class="mc-edit" data-action="quickEditMeal" data-args=\'["' + esc(m.ogun) + '"]\' aria-label="' + __t('nutrition.quick_edit') + '">' +
        '<svg viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>' +
      '</button>' + del +
    '</div></div>';
}

/* ── MEAL CORRECTION (Sprint 13 PR4 — F1/N9) ──
   The one correction primitive the web has: a current-day HARD DELETE, issued
   against the opaque identity + revision the server published. Deletion is
   LOSSY and irreversible — the confirmation says so, and names the stored photo
   when the row owns one. There is no undo, so none is promised. */
var _mealDeleteInFlight = false;

async function deleteMeal(entryToken, revision, hasPhoto, el) {
  if (_mealDeleteInFlight) return;
  var message = __t('nutrition.delete_meal_confirm');
  if (hasPhoto) message += ' ' + __t('nutrition.delete_meal_photo_note');
  if (!window.confirm(message)) return;

  _mealDeleteInFlight = true;
  if (el) el.disabled = true;
  try {
    var res = await fetch('/meal-log/entry/' + encodeURIComponent(entryToken), {
      method: 'DELETE',
      headers: { 'If-Match': '"' + revision + '"' }
    });
    if (res.status === 204) {
      showToast(__t('nutrition.delete_meal_done'), 'success');
    } else if (res.status === 503) {
      /* The ledger correction COMMITTED; what remains pending is only the
         release of the stored photo, and the server holds that intent
         durably. Reporting a failed delete here would be false: the entry is
         gone, and the canonical re-read below proves it. The server converges
         on the photo by itself (a retry, or the operator drain), so this is a
         warning rather than an error the user has to act on. */
      showToast(__t('nutrition.delete_meal_photo_pending'), 'warning');
    } else if (res.status === 404 || res.status === 412) {
      /* Someone or something else moved first. Say so plainly; the canonical
         re-read below decides what is actually there now. */
      showToast(__t('nutrition.delete_meal_stale'), 'warning');
    } else {
      showToast(__t('nutrition.delete_meal_failed'), 'error');
    }
  } catch (e) {
    /* The request may still have been applied. Never retry a destructive call
       with a revision we can no longer trust — re-read instead. */
    showToast(__t('nutrition.delete_meal_failed'), 'error');
  } finally {
    _mealDeleteInFlight = false;
    if (el) el.disabled = false;
    /* Success, refusal or ambiguity: canonical server state is the only
       authority on what is left and what the day now adds up to. The browser
       never subtracts macros of its own. */
    loadTodayData();
  }
}

/* The ledger: MealLog rows only, grouped into the four canonical slots in the
   order the server returned them. A slot with nothing logged is one line — its
   name and the existing contextual "add to this meal" action. */
function renderTimeline(meals) {
  var box = document.getElementById('meal-timeline');
  if (!box) return;
  var bySlot = { 'Kahvaltı': [], 'Öğle': [], 'Akşam': [], 'Ara Öğün': [] };
  (meals || []).forEach(function (m) {
    (bySlot[m.ogun] || bySlot['Ara Öğün']).push(m);
  });
  var note = (meals || []).length ? '' :
    '<p class="nut-ledger-note">' + esc(__t('nutrition.empty_today_title')) + '</p>';
  box.innerHTML = note + SLOTS.map(function (slot) {
    var items = bySlot[slot.key] || [];
    var add = '<button type="button" class="slot-empty" data-action="logManualSlot" data-args=\'["' + esc(slot.key) +
          '"]\'>+ ' + __t('nutrition.add_to_meal') + '</button>';
    var kcal = items.reduce(function (a, m) { return a + (m.kalori || 0); }, 0);
    var head = '<div class="slot-head"><span class="slot-ic" aria-hidden="true">' + (slot.icon || '') +
      '</span><span class="slot-name">' + esc(mealLabel(slot.key)) + '</span>' +
      (items.length ? '<span class="slot-kcal">' + Math.round(kcal) + ' kcal</span>' : add) + '</div>';
    return '<div class="meal-slot' + (items.length ? '' : ' is-empty') + '">' + head +
      items.map(mealCardHTML).join('') + '</div>';
  }).join('');
  box.dataset.ledgerState = (meals || []).length ? 'available' : 'empty';
  box.setAttribute('aria-busy', 'false');
  var count = document.getElementById('nut-meal-count');
  count.textContent = __t('nutrition.meal_count', { n: (meals || []).length });
  count.hidden = false;
}

/* A failed ledger read. Before any confirmed read the list is UNKNOWN — never
   "no meals". After one, the confirmed rows stay (the summary above says they
   were not refreshed) so correction/delete keep working on what is known. */
function renderTimelineFailure() {
  var box = document.getElementById('meal-timeline');
  if (!box) return;
  box.setAttribute('aria-busy', 'false');
  if (_todayConfirmed) return;
  box.dataset.ledgerState = 'unavailable';
  box.innerHTML = '<p class="nut-ledger-note">' + esc(__t('nutrition.meals_unavailable')) + '</p>';
  document.getElementById('nut-meal-count').hidden = true;
}

/* Öğün tipini programatik seç (quick edit / boş slot). */
function selectMealTypeByValue(ogun) {
  selectedMealType = ogun;
  document.querySelectorAll('#meal-type-grid .meal-type-opt').forEach(function (o) {
    o.classList.toggle('selected',
      o.getAttribute('data-args') === '["' + ogun + '"]');
  });
}

/* Quick edit / boş slota ekle → manuel giriş sayfasını açık öğünle aç. */
function quickEditMeal(ogun)  { selectMealTypeByValue(ogun); openManualSheet(); }
function logManualSlot(ogun)  { selectMealTypeByValue(ogun); openManualSheet(); }

/* ── LOG FOOD CHOOSER (NUTR-PR4) ──
   ONE front door (#log-food-btn), several methods. The chooser is local UI
   only: opening, closing and re-opening it issue no request and add no
   listener, and choosing a method only HANDS OFF to the workflow that already
   owns it. None of these functions writes anything — every consumed record
   still comes from that workflow's own explicit, confirmed log:

     Search food  logManual      → search sheet → serving → POST /meal-log
     Scan barcode logScanBarcode → /api/food/barcode (discovery) → serving → POST /meal-log
     Scan menu    logMenuScan    → Coach widget scanner (analysis, not a log)
     Quick add    logQuickAdd    → the existing "From your plan" rows
     Build meal   logBuildMeal   → the meal builder (staging) → its "Log this meal"
     Photo        logTakePhoto   → photo + note → POST /meal-log on confirm

   Focus is deterministic: into the chooser on open; back to "Log food" when
   the chooser, or a method surface it launched, is dismissed. */
var _logSheetOpener = null;

function _logFoodButton() { return document.getElementById('log-food-btn'); }

/* Where focus goes when a surface closes without handing it on: the element
   that opened that surface if it is still on the page, else "Log food". Only
   when focus would otherwise be lost (on <body> or inside the closed
   surface) — a surface that already moved focus somewhere real is left alone. */
function _returnFocus(surface, opener) {
  var active = document.activeElement;
  var lost = !active || active === document.body || active.offsetParent === null ||
             (surface && surface.contains(active));
  if (!lost) return;
  var back = opener && document.contains(opener) && opener.offsetParent !== null
    ? opener : _logFoodButton();
  if (back) back.focus();
}

/* The first control a keyboard user can actually reach inside `el`. */
function _focusFirstVisible(el) {
  if (!el) return;
  var all = el.querySelectorAll('input, select, textarea, button, [tabindex]:not([tabindex="-1"])');
  for (var i = 0; i < all.length; i++) {
    if (all[i].offsetParent !== null && !all[i].disabled) {
      try { all[i].focus({ preventScroll: true }); } catch (e) { all[i].focus(); }
      return;
    }
  }
}

/* The Quick add option states the plan's truth from what the page already
   holds (`#quick-add-section[data-plan-state]`) — no read of its own, and
   "no active plan" is never shown for a plan that failed to load. */
var _QUICK_ADD_SUB = {
  available: 'nutrition.log_quick_add_sub',
  none: 'nutrition.log_quick_add_none',
  unavailable: 'nutrition.plan_unavailable',
  loading: 'nutrition.log_quick_add_loading'
};
function _syncQuickAddOption() {
  var state = document.getElementById('quick-add-section').dataset.planState;
  if (!_QUICK_ADD_SUB[state]) state = 'loading';
  var opt = document.querySelector('#log-sheet [data-method="quick-add"]');
  opt.dataset.planState = state;
  document.getElementById('lso-quick-add-sub').textContent = __t(_QUICK_ADD_SUB[state]);
}

function openLogSheet() {
  var s = document.getElementById('log-sheet');
  if (s.classList.contains('open')) return;
  _logSheetOpener = document.activeElement;
  _syncQuickAddOption();
  s.classList.add('open');
  _logFoodButton().setAttribute('aria-expanded', 'true');
  _focusFirstVisible(s.querySelector('.log-sheet-grid'));
}
function closeLogSheet() {
  document.getElementById('log-sheet').classList.remove('open');
  _logFoodButton().setAttribute('aria-expanded', 'false');
}
function dismissLogSheet() {
  var wasOpen = document.getElementById('log-sheet').classList.contains('open');
  closeLogSheet();
  var back = _logSheetOpener && document.contains(_logSheetOpener)
    ? _logSheetOpener : _logFoodButton();
  _logSheetOpener = null;
  if (wasOpen && back) back.focus();
}

/* aria-modal is a promise: while the chooser is open Tab cycles inside it.
   One listener for the page's life, installed here once, never per open. */
document.addEventListener('keydown', function (e) {
  if (e.key !== 'Tab') return;
  var s = document.getElementById('log-sheet');
  if (!s || !s.classList.contains('open')) return;
  var items = Array.prototype.filter.call(
    s.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'),
    function (el) { return !el.disabled && el.offsetParent !== null; });
  if (!items.length) return;
  var first = items[0], last = items[items.length - 1];
  var active = document.activeElement;
  if (!s.contains(active)) { e.preventDefault(); first.focus(); }
  else if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
});

document.addEventListener('keydown', function (e) {
  if (e.key !== 'Tab') return;
  var m = document.getElementById('plan-replace-modal');
  if (!m || !m.classList.contains('open')) return;
  var items = Array.prototype.filter.call(m.querySelectorAll('button'),
    function (el) { return !el.disabled && el.offsetParent !== null; });
  if (!items.length) return;
  var first = items[0], last = items[items.length - 1];
  var active = document.activeElement;
  if (!m.contains(active)) { e.preventDefault(); first.focus(); }
  else if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
});

/* ── SEARCH FOOD SHEET ── */
var _manualOpener = null;
function openManualSheet()  {
  var fromChooser = document.getElementById('log-sheet').classList.contains('open');
  _manualOpener = fromChooser ? _logFoodButton() : document.activeElement;
  closeLogSheet();
  var s = document.getElementById('manual-sheet');
  s.classList.add('open');
  var inp = document.getElementById('food-search-input');
  if (inp) { try { inp.focus({ preventScroll: true }); } catch (e) { inp.focus(); } }
}
function closeManualSheet() {
  var s = document.getElementById('manual-sheet');
  var wasOpen = s.classList.contains('open');
  s.classList.remove('open');
  if (wasOpen) _returnFocus(s, _manualOpener);
  _manualOpener = null;
}

/* ── SCAN MENU (the Coach widget's scanner, reused as is) ──
   Menu analysis produces suggestions, not intake: nothing is logged here. If
   the scanner is not on the page the chooser says so and every other method
   stays usable. */
function logMenuScan() {
  closeLogSheet();
  if (window.CW && typeof window.CW.startScan === 'function') {
    window.CW.startScan();               // #cw-scan overlay'ini kendisi açar
  } else {
    showToast(__t('nutrition.menu_unavailable'), 'error');
    _returnFocus(null, null);
  }
}

/* ── QUICK ADD → the existing "From your plan" rows ──
   Navigation only. The planned-row state machine (pending · unconfirmed ·
   logged, its page-life locks and the one quickAddMeal writer) stays the
   ONLY way a planned meal is logged; this just takes the user there and
   focuses the first thing they can act on — a planned row, "Try again" for a
   failed plan read, or the no-plan link — else the section title. */
function logQuickAdd() {
  closeLogSheet();
  var section = document.getElementById('quick-add-section');
  var target = section.querySelector('#quick-add-cards button:not(:disabled)') ||
               document.getElementById('nut-planned-title');
  try { section.scrollIntoView({ block: 'start', behavior: 'smooth' }); } catch (e) { section.scrollIntoView(); }
  try { target.focus({ preventScroll: true }); } catch (e) { target.focus(); }
}

/* ── BUILD MEAL → the existing meal builder (CustomMeal staging) ──
   Opens the builder disclosure through the normal navigation path, so its
   staging read runs once, lazily, exactly as when the disclosure itself is
   opened. Adding foods there is staging; only its "Log this meal" commit
   creates a consumed record. */
function logBuildMeal() {
  closeLogSheet();
  switchTab('diary');
  var title = document.getElementById('diary-builder-title');
  try { title.scrollIntoView({ block: 'start', behavior: 'smooth' }); } catch (e) { title.scrollIntoView(); }
  try { title.focus({ preventScroll: true }); } catch (e) { title.focus(); }
}

/* ── SEARCH FOOD / PHOTO ── */
function logManual()    { openManualSheet(); }
function logTakePhoto() {
  closeLogSheet();
  // Focus must not be lost while the system file picker is up (a cancelled
  // picker never tells the page): park it on "Log food" first.
  _returnFocus(null, null);
  document.getElementById('photo-input').click();
}

/* ── TAKE PHOTO FLOW ── */
var _photoDataUrl = null, _photoMealType = 'Kahvaltı';

function _readFileAsDataURL(file) {
  return new Promise(function (res, rej) {
    var r = new FileReader();
    r.onload = function () { res(r.result); };
    r.onerror = rej;
    r.readAsDataURL(file);
  });
}

async function onPhotoPicked(el) {
  var file = el.files && el.files[0];
  if (!file) return;
  try {
    _photoDataUrl = await _readFileAsDataURL(file);
  } catch (e) {
    showToast(__t('nutrition.photo_read_error'), 'error');
    return;
  }
  el.value = '';                         // aynı dosyayı tekrar seçebilmek için sıfırla
  openPhotoConfirm(_photoDataUrl);
}

function openPhotoConfirm(dataUrl) {
  document.getElementById('photo-preview').src = dataUrl;
  // Öğünü günün saatine göre öner
  var h = new Date().getHours();
  var suggested = h < 11 ? 'Kahvaltı' : h < 16 ? 'Öğle' : h < 22 ? 'Akşam' : 'Ara Öğün';
  selectPhotoMealType(suggested);
  document.getElementById('photo-note-input').value = '';
  var modal = document.getElementById('photo-modal');
  modal.classList.add('open');
  _focusFirstVisible(modal);
}
function closePhotoConfirm() {
  var modal = document.getElementById('photo-modal');
  var wasOpen = modal.classList.contains('open');
  modal.classList.remove('open');
  _photoDataUrl = null;
  if (wasOpen) _returnFocus(modal, null);
}

function selectPhotoMealType(ogun) {
  _photoMealType = ogun;
  document.querySelectorAll('#photo-meal-type-grid .meal-type-opt').forEach(function (o) {
    o.classList.toggle('selected', o.getAttribute('data-args') === '["' + ogun + '"]');
  });
}

async function submitPhotoMeal() {
  if (!_photoDataUrl) return;
  var note = document.getElementById('photo-note-input').value.trim();
  var btn = document.getElementById('photo-confirm-btn');
  btn.disabled = true;
  var loading = document.getElementById('loading');
  loading.classList.add('active');
  try {
    var idempotencyHeaders = mealWriteHeaders();
    var res = await fetch('/meal-log', {
      method: 'POST', headers: idempotencyHeaders,
      body: JSON.stringify({
        ogun: _photoMealType,
        yemekler: note || mealLabel(_photoMealType),
        image: _photoDataUrl,
      })
    });
    var d = await res.json();
    if (d.error) { showToast(d.error, 'error'); return; }
    showToast(__t('nutrition.meal_saved'), 'success');
    if (window.fxActivation) fxActivation('meal');
    if (d.quest_awarded) showToast('+' + d.quest_awarded.xp + ' XP!', 'success');
    closePhotoConfirm();
    loadTodayData();
  } catch (e) {
    showToast(__t('nutrition.conn_error_prefix') + e.message, 'error');
  } finally {
    btn.disabled = false;
    loading.classList.remove('active');
  }
}

/* ── BARCODE SCAN ──
   Okuma: tarayıcı BarcodeDetector'ı (Chrome/Android); desteklenmiyorsa yalnız
   manuel numara girişi. Çözme: /api/food/barcode → FatSecret → porsiyon modalı. */
var _scanStream = null, _scanRAF = null, _scanBusy = false;

function _suggestOgunByHour() {
  var h = new Date().getHours();
  return h < 11 ? 'Kahvaltı' : h < 16 ? 'Öğle' : h < 22 ? 'Akşam' : 'Ara Öğün';
}

function logScanBarcode() { closeLogSheet(); openScanOverlay(); }

function openScanOverlay() {
  var ov = document.getElementById('scan-overlay');
  ov.classList.remove('manual-only');
  ov.classList.add('open');
  document.getElementById('barcode-manual-input').value = '';
  if ('BarcodeDetector' in window && navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
    startBarcodeScan();
  } else {
    showManualBarcodeOnly();
  }
}

function showManualBarcodeOnly() {
  document.getElementById('scan-overlay').classList.add('manual-only');
  var hint = document.getElementById('scan-hint');
  if (hint) hint.textContent = '';
  var inp = document.getElementById('barcode-manual-input');
  if (inp) inp.focus();
}

async function startBarcodeScan() {
  var video = document.getElementById('scan-video');
  try {
    _scanStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
  } catch (e) {
    showManualBarcodeOnly();
    return;
  }
  video.srcObject = _scanStream;
  try { await video.play(); } catch (e) { /* autoplay engeli — kullanıcı etkileşimi zaten var */ }
  var det;
  try {
    det = new window.BarcodeDetector({ formats: ['ean_13', 'ean_8', 'upc_a', 'upc_e'] });
  } catch (e) {
    showManualBarcodeOnly();
    return;
  }
  var tick = async function () {
    var ov = document.getElementById('scan-overlay');
    if (!ov || !ov.classList.contains('open')) return;
    try {
      var codes = await det.detect(video);
      if (codes && codes.length && codes[0].rawValue) {
        resolveBarcode(codes[0].rawValue);
        return;
      }
    } catch (e) { /* geçici algılama hatası — döngü sürer */ }
    _scanRAF = requestAnimationFrame(tick);
  };
  _scanRAF = requestAnimationFrame(tick);
}

function stopBarcodeScan() {
  if (_scanRAF) { cancelAnimationFrame(_scanRAF); _scanRAF = null; }
  if (_scanStream) { _scanStream.getTracks().forEach(function (t) { t.stop(); }); _scanStream = null; }
  var video = document.getElementById('scan-video');
  if (video) video.srcObject = null;
}

function closeScanOverlay() {
  stopBarcodeScan();
  var ov = document.getElementById('scan-overlay');
  ov.classList.remove('open');
  _returnFocus(ov, null);
}

function onBarcodeManual() {
  var code = (document.getElementById('barcode-manual-input').value || '').trim();
  if (code) resolveBarcode(code);
}

async function resolveBarcode(code) {
  if (_scanBusy) return;
  _scanBusy = true;
  stopBarcodeScan();
  closeScanOverlay();
  showToast(__t('nutrition.barcode_looking'), 'info');
  try {
    var res = await fetch('/api/food/barcode?code=' + encodeURIComponent(code));
    if (res.status === 404) { showToast(__t('nutrition.barcode_not_found'), 'error'); return; }
    if (!res.ok) { showToast(__t('nutrition.barcode_error'), 'error'); return; }
    var d = await res.json();
    openMealLogServing(
      { food_id: d.food_id, name: d.name || __t('nutrition.log_barcode'), brand: d.brand, servings: d.servings },
      _suggestOgunByHour()
    );
  } catch (e) {
    showToast(__t('nutrition.barcode_error'), 'error');
  } finally {
    _scanBusy = false;
  }
}

/* ── LOG MEAL ── */
/* Sprint 13 PR3 (F4/F5). Seçilen bir besin ARTIK tek başına bir yazma komutudur.
   Eskiden çok-besinli hızlı kayıt `per_100g` değerlerini tarayıcıda TOPLAYIP
   `override_macros` olarak gönderiyordu — yani her besin sessizce "100 g"
   sayılıyordu ve kalıcı makro otoritesi tarayıcıdaydı. Artık:

     - sağlayıcı kimliği olan besin → `provider_food` (kimlik + porsiyon + adet);
       sunucu porsiyon gerçeğini yeniden çeker ve ölçekler,
     - sağlayıcı kimliği OLMAYAN besin (statik tablo/LLM yedeği; yeniden
       çekilecek sağlayıcı gerçeği YOKTUR) → kullanıcının seçtiği gramajla ELLE
       komut; kullanıcı-otoriter ve tipli sınırlı kalır.

   Kayıt SIRALIdır ve her besin KENDİ idempotency anahtarını taşır: kısmi
   başarıda yazılanlar listeden düşer, kalanlar AYNI anahtarlarla yeniden
   denenebilir → sunucu replay eder, ikinci satır ve ikinci XP oluşmaz. */
function postSelectedFood(entry, ogun, idempotencyKey) {
  const headers = {
    'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey };
  const body = entry.food_id
    ? {
        ogun: ogun,
        provider_food: {
          provider: 'fatsecret',
          food_id: entry.food_id,
          serving_id: entry.serving_id,
          quantity: entry.quantity,
          discovery_source: entry.discovery_source || 'search',
        },
      }
    : {
        ogun: ogun,
        yemekler: entry.label || entry.name,
        override_macros: entry.manual,
      };
  return fetch('/meal-log', {
    method: 'POST', headers: headers, body: JSON.stringify(body) });
}

/* One log per action. The loading overlay stops a second click, but not a
   second Enter on the still-focused button — and each call mints a fresh
   idempotency key, so a second call would be a second meal. */
let _mealLogInFlight = false;
async function logMeal() {
  if (_mealLogInFlight) return;
  _mealLogInFlight = true;
  try { await submitMealLog(); } finally { _mealLogInFlight = false; }
}

async function submitMealLog() {
  const input = document.getElementById('meal-input');
  const loading = document.getElementById('loading');

  if (selectedFoods.length > 0) {
    if (!_selectedBatchKey) _selectedBatchKey = newIdempotencyKey();
    loading.classList.add('active');
    let written = 0, failure = null;
    try {
      for (let i = 0; i < selectedFoods.length; i++) {
        const entry = selectedFoods[i];
        const res = await postSelectedFood(
          entry, selectedMealType, _selectedBatchKey + '-' + entry.slot);
        const d = await res.json();
        if (d.error) { failure = d.error; break; }
        written++;
        if (d.quest_awarded && written === 1)
          showToast('+' + d.quest_awarded.xp + ' XP!', 'success');
      }
    } catch (e) {
      failure = __t('nutrition.conn_error_prefix') + e.message;
    } finally {
      selectedFoods = selectedFoods.slice(written);
      renderSelectedFoods();
      loading.classList.remove('active');
    }
    if (failure) { showToast(failure, 'error'); loadTodayData(); return; }
    _selectedBatchKey = null;
    input.value = '';
    showToast(__t('nutrition.meal_saved'), 'success');
    closeManualSheet();
    loadTodayData();
    return;
  }

  const yemekler = input.value.trim();
  if (!yemekler) { showToast(__t('nutrition.write_or_search'), 'error'); return; }

  loading.classList.add('active');
  try {
    const idempotencyHeaders = mealWriteHeaders();
    const res = await fetch('/meal-log', {
      method: 'POST',
      headers: idempotencyHeaders,
      body: JSON.stringify({ ogun: selectedMealType, yemekler })
    });
    const d = await res.json();
    if (d.error) { showToast(d.error, 'error'); return; }
    input.value = '';
    showToast(__t('nutrition.meal_saved'), 'success');
    // Funnel/aktivasyon: ilk öğün kaydı.
    if (window.fxTrackOnce) fxTrackOnce('first_meal_logged');
    if (window.fxActivation) fxActivation('meal');
    if (d.quest_awarded) showToast('+' + d.quest_awarded.xp + ' XP!', 'success');
    closeManualSheet();
    loadTodayData();
  } catch (e) {
    showToast(__t('nutrition.conn_error_prefix') + e.message, 'error');
  } finally {
    loading.classList.remove('active');
  }
}

/* ── AI REVIEW ── */
async function getReview() {
  const btn = document.getElementById('review-btn');
  btn.textContent = __t('nutrition.evaluating');
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
    showToast(__t('nutrition.eval_failed'), 'error');
  } finally {
    btn.textContent = __t('nutrition.eod_review');
    btn.disabled = false;
  }
}

/* ── MEAL HISTORY ── */
let _historyReadGeneration = 0;
let _historyRefreshPending = false;
async function loadMealHistory(notifyFailure = false) {
  const generation = ++_historyReadGeneration;
  if (notifyFailure) _historyRefreshPending = true;
  try {
    const res = await fetch('/meal-log/history');
    if (!res.ok) throw new Error('History read failed');
    const data = await res.json();
    if (generation !== _historyReadGeneration) return;

    // Weekly chart (last 7 days)
    renderWeeklyChart(data.slice(0, 7).reverse());

    // History list
    const list = document.getElementById('history-list');
    if (!data.length) {
      list.innerHTML = `<div class="empty-state"><div class="empty-icon"><svg viewBox="0 0 24 24"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg></div><div class="empty-title">${__t('nutrition.no_records')}</div></div>`;
      _historyRefreshPending = false;
      return;
    }
    list.innerHTML = data.map(day => `
      <div class="history-day">
        <div class="history-day-hdr">
          <div class="history-date">${day.tarih}</div>
          <div class="history-totals">
            <span class="history-total">${__t('nutrition.calories_label')} <span>${Math.round(day.totals.kalori)}</span></span>
            <span class="history-total">${MA.p}: <span>${Math.round(day.totals.protein)}g</span></span>
            <span class="history-total">${MA.k}: <span>${Math.round(day.totals.karb)}g</span></span>
            <span class="history-total">${MA.y}: <span>${Math.round(day.totals.yag)}g</span></span>
          </div>
        </div>
        ${day.meals.map(m => `
          <div class="history-meal">
            <div class="history-meal-type">${mealLabel(m.ogun)} · ${Math.round(m.kalori)} kcal</div>
            <div class="history-meal-foods">${esc(m.yemekler)}</div>
          </div>`).join('')}
      </div>`).join('');
    _historyRefreshPending = false;
  } catch (e) {
    console.error('loadMealHistory', e);
    if (_historyRefreshPending && generation === _historyReadGeneration)
      showToast(__t('route.macros_unavailable'), 'error');
  }
}

function renderWeeklyChart(days) {
  const chart = document.getElementById('weekly-chart');
  if (!days.length) { chart.innerHTML = '<div style="flex:1;text-align:center;color:var(--text-3);font-size:13px;align-self:center;">' + __t('nutrition.no_data') + '</div>'; return; }
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

/* ── NUTRITION PLAN (NUTR-PR5) ──
   Plan is planning and review; Today is where food is logged. Every fact on
   this surface belongs to an authority this code only presents:

     target        the `/meal-log/today` projection Today already read —
                   renderPlanTarget is fed by the Today read, no read of its own
     current plan  NutritionPlan, through the ONE shared /nutrition-plan/active
                   read (getActivePlan, also used by "From your plan")
     option        POST /nutrition-plan output: a PROPOSAL, never the current plan
     replacement   POST /nutrition-plan/save, the only thing that changes the plan

   Current-plan states (`#plan-current[data-plan-state]`): loading · active ·
   absent (`exists:false`) · unavailable (the read failed — never "no plan") ·
   invalid (a stored document this page cannot draw safely).

   A proposal becomes the current plan only after the save answers AND a
   canonical re-read draws it: nothing is marked active optimistically. Generate
   and save are single-flight. Choosing an option while a plan exists (or might)
   is confirmed first, because the save route deletes the stored plan. A refused
   save (4xx) changed nothing and may be retried. A save whose answer does not
   say what happened (5xx, network, unreadable) is NEVER re-sent: it gets at
   most ONE canonical re-read, which proves the new plan, proves the old plan
   (retry allowed), or leaves it "couldn't confirm" (these options stay
   disabled; a reload or a new generation starts over). */
const FOODS = {
  protein_hayvansal: ['Tavuk Göğsü','Yumurta','Ton Balığı','Kırmızı Et','Yoğurt','Somon','Hindi'],
  protein_bitkisel:  ['Mercimek','Nohut','Tofu','Kinoa','Edamame','Fasulye'],
  karbonhidrat:      ['Yulaf Ezmesi','Pirinç','Bulgur','Tatlı Patates','Tam Buğday Ekmeği','Muz','Elma','Makarna'],
  yag:               ['Zeytinyağı','Avokado','Badem','Ceviz','Fındık','Fıstık Ezmesi']
};
const selected    = { proteins: new Set(), carbs: new Set(), fats: new Set() };
const customFoods = [];

/* A food choice is a real toggle button (keyboard + screen reader state). */
function createFoodChip(name, category) {
  const el = document.createElement('button');
  el.type = 'button';
  el.className = 'chip';
  el.setAttribute('aria-pressed', 'false');
  el.innerHTML = `<span class="chip-dot" aria-hidden="true"></span>${esc(foodLabel(name))}`;
  el.addEventListener('click', () => {
    const isSelected = el.classList.toggle('selected');
    el.setAttribute('aria-pressed', String(isSelected));
    if (isSelected) selected[category].add(name);
    else            selected[category].delete(name);
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
  tag.innerHTML = `<span>${esc(val)}</span><button type="button" class="custom-tag-remove" data-action="removeCustomFood" aria-label="${esc(__t('nutrition.plan.remove_food', { food: val }))}">×</button>`;
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
  const input = document.getElementById('custom-input');
  if (input) input.focus();
}

/* The four canonical meal slots of a plan document (keys are the contract). */
function planMealSlots() {
  return [
    { key: 'kahvalti', label: __t('nutrition.meal_breakfast'), icon: _SLOT_ICONS.breakfast },
    { key: 'ogle',     label: __t('nutrition.meal_lunch'),     icon: _SLOT_ICONS.lunch },
    { key: 'aksam',    label: __t('nutrition.meal_dinner'),    icon: _SLOT_ICONS.dinner },
    { key: 'ara_ogun', label: __t('nutrition.meal_snack'),     icon: _SLOT_ICONS.snack }
  ];
}
function isMealObject(ml) { return !!ml && typeof ml === 'object' && !Array.isArray(ml); }
/* Drawable = an object with at least one meal slot. Anything else that the
   server calls a plan is shown as `invalid`, never as "no plan". */
function drawablePlan(plan) {
  return !!plan && typeof plan === 'object' && !Array.isArray(plan) &&
    planMealSlots().some(m => isMealObject(plan[m.key]));
}
/* Rows persisted before the schema existed may hold one string, not a list. */
function planItems(ml) {
  if (Array.isArray(ml.yemekler)) return ml.yemekler.filter(y => typeof y === 'string' || typeof y === 'number');
  if (typeof ml.yemekler === 'string') return [ml.yemekler];
  return [];
}
function planName(plan, i) {
  if (plan && typeof plan.isim === 'string' && plan.isim) return plan.isim;
  return i == null ? __t('nutrition.plan.unnamed') : __t('nutrition.plan.option_n', { n: i + 1 });
}
/* The same payload fingerprint "From your plan" scopes its row locks with. */
function planKeyOf(d) { return JSON.stringify([d.plan && typeof d.plan === 'object' ? d.plan : {}, d.score, d.created_at]); }
/* Two plan documents say the same thing (the save route re-builds the
   document and may turn "420" into 420, so raw JSON is not comparable). */
function planDocumentKey(p) {
  if (!p || typeof p !== 'object') return null;
  const num = v => ((typeof v === 'number' || (typeof v === 'string' && v.trim() !== '')) && Number.isFinite(Number(v))) ? Number(v) : null;
  const out = { isim: typeof p.isim === 'string' ? p.isim : null };
  planMealSlots().forEach(m => {
    const ml = p[m.key];
    out[m.key] = isMealObject(ml) ? {
      yemekler: Array.isArray(ml.yemekler) ? ml.yemekler.map(String) : [],
      kalori: num(ml.kalori), protein: num(ml.protein), karb: num(ml.karb), yag: num(ml.yag)
    } : null;
  });
  ['toplam_kalori', 'toplam_protein', 'toplam_karb', 'toplam_yag'].forEach(k => { out[k] = num(p[k]); });
  return JSON.stringify(out);
}
function samePlanDocument(a, b) {
  const ka = planDocumentKey(a);
  return ka !== null && ka === planDocumentKey(b);
}

/* ── TARGET (read-only mirror of Today's canonical read) ── */
function renderPlanTarget(target) {
  const box = document.getElementById('plan-target');
  const value = document.getElementById('plan-target-value');
  const macros = document.getElementById('plan-target-macros');
  const note = document.getElementById('plan-target-note');
  box.dataset.targetState = target.state;
  macros.hidden = true; macros.textContent = '';
  note.hidden = true; note.textContent = '';
  if (target.state === 'known') {
    value.textContent = __t('nutrition.plan.target_value', { n: Math.round(target.kalori) });
    const m = target.macros;
    if (m.protein && m.karb && m.yag) {
      macros.textContent = __t('nutrition.plan.target_macros',
        { p: Math.round(m.protein), c: Math.round(m.karb), f: Math.round(m.yag) });
      macros.hidden = false;
    }
    note.textContent = __t('nutrition.plan.target_source');
    note.hidden = false;
  } else if (target.state === 'absent') {
    value.textContent = __t('nutrition.target_absent');
    note.textContent = __t('nutrition.plan.target_absent_hint');
    note.hidden = false;
  } else {
    value.textContent = __t('nutrition.target_unavailable');   // never 0
  }
}

/* ── CURRENT PLAN ── */
let _planShown = { state: 'loading', key: null, name: null, plan: null };
let _planOptions = null;        // { plans, score } — the last generated proposals
let _planGenInFlight = false;
let _planSaveInFlight = false;
let _planSaveLocked = false;    // an unconfirmable save: these options stay disabled
let _planReplaceIndex = null;
let _planReplaceOpener = null;

function setCurrentPlanState(state) {
  const section = document.getElementById('plan-current');
  section.dataset.planState = state;
  section.setAttribute('aria-busy', 'false');
}

function planCta(kind) {
  if (kind === 'retry')
    return `<button type="button" class="btn-ghost nut-retry" data-action="retryActivePlan">${esc(__t('nutrition.try_again'))}</button>`;
  if (kind === 'create')
    return `<button type="button" class="btn-volt plan-cta" id="plan-create-btn" data-action="openPlanBuilder" aria-controls="plan-builder" aria-expanded="false">${esc(__t('nutrition.plan.create'))}</button>`;
  return `<button type="button" class="btn-ghost plan-cta" id="plan-replace-btn" data-action="openPlanBuilder" aria-controls="plan-builder" aria-expanded="false">${esc(__t('nutrition.plan.replace'))}</button>`;
}

function renderPlanStateBlock(textKey, hintKey, kind) {
  document.getElementById('active-plan-detail').innerHTML = `
    <p class="plan-state-text">${esc(__t(textKey))}</p>
    <p class="plan-note">${esc(__t(hintKey))}</p>
    <div class="plan-actions">${planCta(kind)}</div>`;
}

function renderCurrentPlan(d) {
  if (!d.exists) {
    _planShown = { state: 'absent', key: 'absent', name: null, plan: null };
    setCurrentPlanState('absent');
    renderPlanStateBlock('nutrition.plan.absent', 'nutrition.plan.absent_hint', 'create');
  } else if (!drawablePlan(d.plan)) {
    _planShown = { state: 'invalid', key: planKeyOf(d), name: null, plan: null };
    setCurrentPlanState('invalid');
    renderPlanStateBlock('nutrition.plan.invalid', 'nutrition.plan.invalid_hint', 'replace');
  } else {
    _planShown = { state: 'active', key: planKeyOf(d), name: planName(d.plan), plan: d.plan };
    setCurrentPlanState('active');
    renderActivePlanDetail(d.plan, d.score, d.created_at);
  }
  syncPlanBuilder();
  return _planShown;
}

function renderCurrentPlanUnavailable() {
  _planShown = { state: 'unavailable', key: null, name: null, plan: null };
  setCurrentPlanState('unavailable');
  renderPlanStateBlock('nutrition.plan_unavailable', 'nutrition.plan.unavailable_hint', 'retry');
  syncPlanBuilder();
  return _planShown;
}

/* Draws what the shared read says. An answer that belongs to a read that has
   since been replaced (after a save, or a retry) is dropped: the newer read
   is awaited instead, so an old plan can never overwrite a newer one. */
async function loadActivePlan(force = false) {
  const section = document.getElementById('plan-current');
  section.setAttribute('aria-busy', 'true');
  let d, epoch;
  const superseded = () => {
    if (_activePlanCache) return loadActivePlan();
    section.setAttribute('aria-busy', String(_planShown.state === 'loading'));
    return _planShown;
  };
  try {
    const read = getActivePlan(force);
    epoch = _activePlanEpoch;
    d = await read;
  } catch (e) {
    if (epoch !== _activePlanEpoch) return superseded();
    return renderCurrentPlanUnavailable();
  }
  if (epoch !== _activePlanEpoch) return superseded();
  return renderCurrentPlan(d);
}

function retryActivePlan() {
  document.getElementById('plan-current-status').textContent = '';
  loadActivePlan(true);
}

function renderActivePlanDetail(plan, score, createdAt) {
  const meals = planMealSlots();
  const mealsHtml = meals.map(m => {
    const ml = plan[m.key];
    if (!isMealObject(ml)) return '';
    const items = planItems(ml).map(y => `<li>${esc(y)}</li>`).join('');
    return `
      <li class="apd-meal" data-planned="true">
        <div class="apd-meal-hdr">
          <span class="apd-meal-icon" aria-hidden="true">${m.icon}</span>
          <h5 class="apd-meal-name">${m.label}</h5>
          <span class="apd-badge">${esc(__t('nutrition.planned'))}</span>
          <span class="apd-meal-kcal">${fmtNum(ml.kalori)} kcal</span>
        </div>
        <ul class="apd-meal-list">${items}</ul>
      </li>`;
  }).join('');

  const created = typeof createdAt === 'string' && createdAt
    ? `<p class="apd-sub">${esc(__t('nutrition.plan.created', { date: createdAt }))}</p>` : '';
  const hasTotals = ['toplam_kalori', 'toplam_protein', 'toplam_karb', 'toplam_yag']
    .some(k => fmtNum(plan[k]) !== '—');
  const totals = hasTotals ? `
    <h4 class="plan-block-sub">${esc(__t('nutrition.plan.totals'))}</h4>
    <div class="apd-macro-grid">
      <div class="apd-macro-item"><div class="apd-macro-val">${fmtNum(plan.toplam_kalori)}</div><div class="apd-macro-lbl">kcal</div></div>
      <div class="apd-macro-item"><div class="apd-macro-val">${fmtNum(plan.toplam_protein)}g</div><div class="apd-macro-lbl">${__t('nutrition.macro_protein')}</div></div>
      <div class="apd-macro-item"><div class="apd-macro-val">${fmtNum(plan.toplam_karb)}g</div><div class="apd-macro-lbl">${__t('nutrition.carb_short')}</div></div>
      <div class="apd-macro-item"><div class="apd-macro-val">${fmtNum(plan.toplam_yag)}g</div><div class="apd-macro-lbl">${__t('nutrition.macro_fat')}</div></div>
    </div>` : '';
  // The stored score is the generator's average rating of the chosen FOODS
  // (micronutrients, bioavailability, gluten). It is secondary metadata, not
  // a verdict on the plan, so it is small, neutral text — no colour, no label.
  const rating = fmtNum(score);
  const ratingNote = rating === '—' ? '' : `
    <p class="plan-note plan-rating">${esc(__t('nutrition.plan.rating', { n: rating }))} · ${esc(__t('nutrition.plan.rating_hint'))}</p>`;

  document.getElementById('active-plan-detail').innerHTML = `
    <div class="apd-header">
      <p class="apd-title">${esc(plan.isim || __t('nutrition.plan.unnamed'))}</p>
      ${created}
    </div>
    ${totals}
    <h4 class="plan-block-sub">${esc(__t('nutrition.plan.planned_meals'))}</h4>
    <ul class="apd-meals">${mealsHtml}</ul>
    <p class="plan-note">${esc(__t('nutrition.plan.planned_note'))}</p>
    ${ratingNote}
    <div class="plan-actions">${planCta('replace')}</div>`;
}

/* ── CREATE / REPLACE WORKFLOW ── */
function setPlanGenStatus(key, vars) {
  document.getElementById('plan-gen-status').textContent = key ? __t(key, vars) : '';
}

/* One place decides what the builder says and which controls may act. */
function syncPlanBuilder() {
  const builder = document.getElementById('plan-builder');
  const creating = _planShown.state === 'absent';
  document.getElementById('plan-builder-title').textContent =
    __t(creating ? 'nutrition.plan.builder_create' : 'nutrition.plan.builder_replace');
  document.getElementById('plan-builder-lead').textContent =
    __t(creating ? 'nutrition.plan.builder_lead' : 'nutrition.plan.builder_lead_replace');
  document.querySelectorAll('#plan-current .plan-cta').forEach(b => {
    b.hidden = !builder.hidden;
    b.setAttribute('aria-expanded', String(!builder.hidden));
  });
  const busy = _planGenInFlight || _planSaveInFlight;
  document.querySelectorAll('#plans-grid .btn-select-plan').forEach(b => {
    b.disabled = busy || _planSaveLocked;
  });
  document.getElementById('plan-builder-cancel').disabled = _planSaveInFlight;
}

function openPlanBuilder() {
  const builder = document.getElementById('plan-builder');
  document.getElementById('plan-current-status').textContent = '';
  builder.hidden = false;
  syncPlanBuilder();
  const title = document.getElementById('plan-builder-title');
  try { title.focus({ preventScroll: true }); } catch (e) { title.focus(); }
  try { title.scrollIntoView({ block: 'start' }); } catch (e) {}
}

function closePlanBuilder(returnFocus = true) {
  const builder = document.getElementById('plan-builder');
  if (builder.hidden || _planSaveInFlight) return;
  builder.hidden = true;
  syncPlanBuilder();
  if (!returnFocus) return;
  const back = document.querySelector('#plan-current .plan-cta') ||
               document.getElementById('plan-current-title');
  try { back.focus({ preventScroll: true }); } catch (e) { back.focus(); }
}

/* Explicit action only; exactly one request however often it is pressed. It
   never touches the current plan: success and failure both leave it drawn. */
async function generatePlan() {
  if (_planGenInFlight || _planSaveInFlight) return;
  if (!selected.proteins.size) { showToast(__t('nutrition.need_protein'), 'error'); return; }
  if (!selected.carbs.size)    { showToast(__t('nutrition.need_carb'), 'error'); return; }
  if (!selected.fats.size)     { showToast(__t('nutrition.need_fat'), 'error'); return; }

  const builder = document.getElementById('plan-builder');
  const btn = document.getElementById('plan-btn');
  _planGenInFlight = true;
  builder.dataset.genState = 'loading';
  btn.disabled = true;
  btn.setAttribute('aria-busy', 'true');
  btn.textContent = __t('nutrition.plan.generating');
  setPlanGenStatus('nutrition.plan.generating');
  syncPlanBuilder();

  let res = null, data = null;
  try {
    res = await fetch('/nutrition-plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        proteins:     [...selected.proteins],
        carbs:        [...selected.carbs],
        fats:         [...selected.fats],
        custom_foods: customFoods
      })
    });
    data = await res.json().catch(() => null);
  } catch (e) { res = null; }

  _planGenInFlight = false;
  btn.disabled = false;
  btn.removeAttribute('aria-busy');
  btn.textContent = __t('nutrition.plan.generate');
  const plans = res && res.ok && data && !data.error && Array.isArray(data.planlar)
    ? data.planlar.filter(p => p && typeof p === 'object' && !Array.isArray(p)) : [];
  if (!plans.length) {
    // Earlier options (if any) stay as they were; the current plan was never touched.
    builder.dataset.genState = 'failed';
    setPlanGenStatus('nutrition.plan.gen_failed');
    if (res && res.status >= 400 && res.status < 500 && data && typeof data.error === 'string')
      showToast(data.error, 'error');
    syncPlanBuilder();
    return;
  }
  _planOptions = { plans, score: data.overall_score };
  _planSaveLocked = false;
  builder.dataset.genState = 'ready';
  builder.dataset.saveState = 'idle';
  renderPlans({ planlar: plans, overall_score: data.overall_score });
  setPlanGenStatus('nutrition.plan.options_ready', { n: plans.length });
}

/* Generated options: proposals, each with its own "Use this plan". None of
   them is, or is ever labelled, the current plan. */
function renderPlans(data) {
  document.getElementById('plan-results').hidden = false;
  const rating = fmtNum(data.overall_score);
  document.getElementById('score-banner-wrap').textContent = rating === '—' ? ''
    : __t('nutrition.plan.rating', { n: rating }) + ' · ' + __t('nutrition.plan.rating_hint');

  document.getElementById('plans-grid').innerHTML = data.planlar.map((plan, i) => {
    const mealsHtml = planMealSlots().map(m => {
      const ml = plan[m.key];
      if (!isMealObject(ml)) return '';
      return `
        <div class="plan-meal-sec">
          <p class="plan-meal-title">${m.label} · ${fmtNum(ml.kalori)} kcal</p>
          <ul class="plan-meal-items">${planItems(ml).map(y => `<li>${esc(y)}</li>`).join('')}</ul>
        </div>`;
    }).join('');
    return `
      <article class="plan-card" id="plan-card-${i}" role="listitem" aria-labelledby="plan-card-name-${i}">
        <div class="plan-card-hdr">
          <div class="plan-card-id">
            <span class="plan-badge">${esc(__t('nutrition.plan.option_badge'))}</span>
            <h5 class="plan-card-name" id="plan-card-name-${i}">${esc(planName(plan, i))}</h5>
          </div>
          <div class="plan-card-kcal">${fmtNum(plan.toplam_kalori)} kcal</div>
        </div>
        <div class="plan-card-body">
          ${mealsHtml}
          <div class="plan-macro-grid">
            <div class="plan-macro-item"><div class="plan-macro-val">${fmtNum(plan.toplam_protein)}g</div><div class="plan-macro-lbl">${__t('nutrition.macro_protein')}</div></div>
            <div class="plan-macro-item"><div class="plan-macro-val">${fmtNum(plan.toplam_karb)}g</div><div class="plan-macro-lbl">${__t('nutrition.carb_short')}</div></div>
            <div class="plan-macro-item"><div class="plan-macro-val">${fmtNum(plan.toplam_yag)}g</div><div class="plan-macro-lbl">${__t('nutrition.macro_fat')}</div></div>
          </div>
          <button class="btn-select-plan" id="sel-btn-${i}" type="button" data-action="selectPlan" data-args="[${i}]">${esc(__t('nutrition.plan.use'))}</button>
        </div>
      </article>`;
  }).join('');
  syncPlanBuilder();
}

/* "Use this plan". With a confirmed absence there is nothing to replace; in
   every other state (a plan, a plan we cannot draw, or a state we could not
   read) the replacement is confirmed first. */
function selectPlan(i, btn) {
  if (_planSaveInFlight || _planGenInFlight || _planSaveLocked) return;
  if (!_planOptions || !_planOptions.plans[i]) return;
  if (_planShown.state === 'absent') return savePlanOption(i);
  openPlanReplace(i, btn);
}

function openPlanReplace(i, opener) {
  _planReplaceIndex = i;
  _planReplaceOpener = opener && opener.nodeType === 1 ? opener : document.activeElement;
  const next = planName(_planOptions.plans[i], i);
  let body;
  if (_planShown.state === 'active')
    body = __t('nutrition.plan.confirm_body', { current: _planShown.name, next });
  else if (_planShown.state === 'invalid')
    body = __t('nutrition.plan.confirm_body_unnamed', { next });
  else
    body = __t('nutrition.plan.confirm_body_unknown', { next });
  document.getElementById('plan-replace-body').textContent = body;
  document.getElementById('plan-replace-confirm').disabled = false;
  document.getElementById('plan-replace-modal').classList.add('open');
  // The non-destructive choice takes focus first.
  document.getElementById('plan-replace-cancel').focus();
}

function cancelPlanReplace() {
  const modal = document.getElementById('plan-replace-modal');
  const wasOpen = modal.classList.contains('open');
  modal.classList.remove('open');
  const back = _planReplaceOpener;
  _planReplaceIndex = null;
  _planReplaceOpener = null;
  if (wasOpen && back && document.contains(back) && !back.disabled) back.focus();
}

function confirmPlanReplace() {
  const i = _planReplaceIndex;
  if (i === null || _planSaveInFlight) return;
  document.getElementById('plan-replace-confirm').disabled = true;
  document.getElementById('plan-replace-modal').classList.remove('open');
  _planReplaceIndex = null;
  _planReplaceOpener = null;
  savePlanOption(i);
}

/* The ONE canonical replacement. */
async function savePlanOption(i) {
  if (_planSaveInFlight || _planSaveLocked || !_planOptions || !_planOptions.plans[i]) return;
  const sent = _planOptions.plans[i];
  const replacing = _planShown.state !== 'absent';
  const before = ['active', 'absent', 'invalid'].includes(_planShown.state) ? _planShown.key : null;
  const builder = document.getElementById('plan-builder');
  _planSaveInFlight = true;
  builder.dataset.saveState = 'saving';
  setPlanGenStatus('nutrition.plan.saving');
  syncPlanBuilder();
  const title = document.getElementById('plan-builder-title');
  try { title.focus({ preventScroll: true }); } catch (e) { title.focus(); }

  let res = null, body = null;
  try {
    res = await fetch('/nutrition-plan/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan: sent, score: _planOptions.score })
    });
    body = await res.json().catch(() => null);
  } catch (e) { res = null; }
  const ok = Boolean(res && res.ok && body && !body.error);
  const refused = Boolean(res && res.status >= 400 && res.status < 500);

  if (refused) {
    // Definite rejection: the route validates before it deletes, so the
    // current plan was never touched. The option may be chosen again.
    _planSaveInFlight = false;
    builder.dataset.saveState = 'rejected';
    setPlanGenStatus('nutrition.plan.save_rejected');
    showToast((body && typeof body.error === 'string' && body.error) || __t('nutrition.plan.save_rejected'), 'error');
    syncPlanBuilder();
    return;
  }

  // Confirmed, or unknown: ONE canonical re-read, shared by Plan and Today.
  invalidateActivePlan();
  const reread = loadActivePlan();
  loadQuickAddSection();
  const shown = await reread;
  _planSaveInFlight = false;

  if (ok || (shown.state === 'active' && samePlanDocument(sent, shown.plan))) {
    finishPlanSave();
    return;
  }
  if (shown.state !== 'unavailable' && before !== null && shown.key === before) {
    // The canonical read proves nothing changed: a retry is safe.
    builder.dataset.saveState = 'failed';
    setPlanGenStatus(replacing ? 'nutrition.plan.save_not_replaced' : 'nutrition.plan.save_not_saved');
    showToast(__t(replacing ? 'nutrition.plan.save_not_replaced' : 'nutrition.plan.save_not_saved'), 'error');
    syncPlanBuilder();
    return;
  }
  // Neither outcome can be proven. Never re-send; these options stay disabled.
  _planSaveLocked = true;
  builder.dataset.saveState = 'unconfirmed';
  setPlanGenStatus(replacing ? 'nutrition.plan.save_unconfirmed' : 'nutrition.plan.save_unconfirmed_new');
  showToast(__t(replacing ? 'nutrition.plan.save_unconfirmed' : 'nutrition.plan.save_unconfirmed_new'), 'warning');
  syncPlanBuilder();
}

function finishPlanSave() {
  _planOptions = null;
  _planSaveLocked = false;
  const builder = document.getElementById('plan-builder');
  document.getElementById('plans-grid').innerHTML = '';
  document.getElementById('plan-results').hidden = true;
  document.getElementById('score-banner-wrap').textContent = '';
  builder.dataset.genState = 'idle';
  builder.dataset.saveState = 'idle';
  setPlanGenStatus(null);
  closePlanBuilder(false);
  document.getElementById('plan-current-status').textContent = __t('nutrition.plan.saved');
  const title = document.getElementById('plan-current-title');
  try { title.focus({ preventScroll: true }); } catch (e) { title.focus(); }
}

/* ── AKTİF PLAN CACHE ──
   loadActivePlan() ve loadQuickAddSection() açılışta arka arkaya çağrılıyordu;
   ikisi de /nutrition-plan/active'i çekince istek iki kez gidiyordu. In-flight
   promise'i paylaşarak tek isteğe indir; plan değişince invalidateActivePlan().
   `_activePlanEpoch` names the current read: a consumer whose read was
   replaced (invalidation or a forced retry) drops its answer, so an older
   response can never overwrite a newer canonical state. */
let _activePlanCache = null;
let _activePlanEpoch = 0;
function getActivePlan(force = false) {
  if (force || !_activePlanCache) {
    _activePlanEpoch++;
    // A failed read rejects (and is not cached) — it is never `exists:false`.
    const read = fetch('/nutrition-plan/active')
      .then(r => { if (!r.ok) throw new Error('Plan read failed'); return r.json(); })
      .then(d => { if (!d || typeof d.exists !== 'boolean') throw new Error('Plan read invalid'); return d; })
      .catch(err => { if (_activePlanCache === read) _activePlanCache = null; throw err; });
    _activePlanCache = read;
  }
  return _activePlanCache;
}
function invalidateActivePlan() { _activePlanCache = null; _activePlanEpoch++; }

/* ── FROM YOUR PLAN (planned shortcuts) ──
   The existing `/api/quick-add-meal` shortcut, presented as what it is: a
   PLANNED meal until that write confirms, and only then "Logged". States:
   loading · available · no plan (`exists:false`) · unavailable (read failed).
   Deferred, NOT fixed here: the endpoint's idempotency replay is user-wide and
   runs before plan/meal validation, so nothing on this surface claims
   exactly-once. It only avoids a double tap and never re-sends an ambiguous
   write by itself. A row whose write was sent stays locked for the rest of
   this page's life, re-renders included: "Not confirmed" after an ambiguous
   answer, "Logged" after a confirmed one. Only a reload rebuilds it from the
   server, so a redraw never re-offers a write that was already sent.
   The locks are presentation containment, not consumption authority (MealLog
   is): they belong to the plan they were taken on and are dropped when a
   different active plan is drawn, so an old "Logged" never disables a new
   plan's meal. No plan id is published, so the plan is named by its payload. */
const _plannedWriteLocks = new Map(); // meal key → 'pending' | 'unconfirmed' | 'logged'
let _plannedLocksPlan = null;         // the drawn plan those locks belong to

const _PLANNED_ROW_COPY = {           // badge, action; pending keeps Planned / Log
  unconfirmed: ['nutrition.unconfirmed_state', 'nutrition.check_logged_meals'],
  logged:      ['nutrition.logged_state', 'nutrition.logged_state']
};

function lockPlannedRow(row, state) {
  row.disabled = true;
  row.dataset.writeState = state;
  row.classList.toggle('qab-done', state === 'logged');
  const copy = _PLANNED_ROW_COPY[state];
  if (!copy) return;
  row.querySelector('.qab-badge').textContent = __t(copy[0]);
  row.querySelector('.qab-action').textContent = __t(copy[1]);
}

function setPlanState(state) {
  document.getElementById('quick-add-section').dataset.planState = state;
}

async function loadQuickAddSection(force = false) {
  const container = document.getElementById('quick-add-cards');
  let d, epoch;
  try {
    const read = getActivePlan(force);
    epoch = _activePlanEpoch;
    d = await read;
  } catch (e) {
    if (epoch !== _activePlanEpoch) return _activePlanCache ? loadQuickAddSection() : undefined;
    setPlanState('unavailable');
    container.innerHTML = `
      <div class="nut-planned-state">
        <p class="nut-planned-note">${esc(__t('nutrition.plan_unavailable'))}</p>
        <button type="button" class="btn-ghost nut-retry" data-action="retryPlanShortcuts">${esc(__t('nutrition.try_again'))}</button>
      </div>`;
    return;
  }
  // A newer read replaced this one (e.g. a confirmed plan save): draw that one.
  if (epoch !== _activePlanEpoch) return _activePlanCache ? loadQuickAddSection() : undefined;

  if (!d.exists) {
    setPlanState('none');
    container.innerHTML = `
      <button type="button" class="qab-no-plan" data-action="fxGoToPlanTab">
        ${__t('nutrition.no_active_plan')}
      </button>`;
    return;
  }

  const MEALS = [
    { key: 'kahvalti', label: __t('nutrition.meal_breakfast'), icon: _SLOT_ICONS.breakfast },
    { key: 'ogle',     label: __t('nutrition.meal_lunch'),     icon: _SLOT_ICONS.lunch },
    { key: 'aksam',    label: __t('nutrition.meal_dinner'),    icon: _SLOT_ICONS.dinner },
    { key: 'ara_ogun', label: __t('nutrition.meal_snack'),     icon: _SLOT_ICONS.snack }
  ];

  setPlanState('available');
  const plan = d.plan && typeof d.plan === 'object' ? d.plan : {};
  const planKey = JSON.stringify([plan, d.score, d.created_at]);
  if (planKey !== _plannedLocksPlan) { _plannedWriteLocks.clear(); _plannedLocksPlan = planKey; }
  container.innerHTML = MEALS.map(m => {
    const ml  = plan[m.key];
    if (!ml) return '';
    const sub = `${fmtNum(ml.kalori)} kcal · ${fmtNum(ml.protein)}g ${__t('nutrition.unit_protein')} · ${fmtNum(ml.karb)}g ${__t('nutrition.unit_carb')}`;
    return `
      <button class="qab" id="qab-${m.key}" data-planned="true"
        data-action="quickAddMeal" data-args='["${m.key}","${m.label}"]' type="button">
        <span class="qab-icon" aria-hidden="true">${m.icon}</span>
        <div class="qab-info">
          <span class="qab-badge">${esc(__t('nutrition.planned'))}</span>
          <div class="qab-title">${m.label} — ${esc(plan.isim || __t('nutrition.active_plan_name'))}</div>
          <div class="qab-sub">${sub}</div>
        </div>
        <span class="qab-action">${esc(__t('nutrition.log_planned'))}</span>
      </button>`;
  }).join('');
  _plannedWriteLocks.forEach((state, key) => {
    const row = document.getElementById('qab-' + key);
    if (row) lockPlannedRow(row, state);
  });
}

function retryPlanShortcuts() { loadQuickAddSection(true); }

async function quickAddMeal(mealKey, mealLabel, btn) {
  if (btn.classList.contains('qab-done') || btn.disabled || _plannedWriteLocks.has(mealKey)) return;
  const planKey = _plannedLocksPlan;
  _plannedWriteLocks.set(mealKey, 'pending');
  lockPlannedRow(btn, 'pending');
  btn.setAttribute('aria-busy', 'true');
  let res = null, d = null;
  try {
    res = await fetch('/api/quick-add-meal', {
      method:  'POST',
      headers: mealWriteHeaders(),
      body:    JSON.stringify({ meal_key: mealKey })
    });
    d = await res.json().catch(() => null);
  } catch (e) { res = null; }
  btn.removeAttribute('aria-busy');
  const ok = Boolean(res && res.ok && d && !d.error);
  const refused = Boolean(res && res.status >= 400 && res.status < 500);
  if (planKey !== _plannedLocksPlan) {
    // A different plan was drawn while this write was in flight: its row and
    // lock are gone. Report the answer and re-read the ledger, never lock the
    // new plan's row with the old plan's outcome.
    if (ok) showToast(`${mealLabel} ${__t('nutrition.added_suffix')}`, 'success');
    else if (refused) showToast((d && d.error) || __t('nutrition.add_error'), 'error');
    else showToast(__t('nutrition.quick_add_uncertain'), 'warning');
    loadTodayData();
    return;
  }
  if (!btn.isConnected) btn = document.getElementById('qab-' + mealKey) || btn;

  if (ok) {
    // Confirmed write: only now does this planned meal read "Logged", and it
    // keeps reading so (locked) through every redraw until a reload.
    _plannedWriteLocks.set(mealKey, 'logged');
    lockPlannedRow(btn, 'logged');
    showToast(`${mealLabel} ${__t('nutrition.added_suffix')}`, 'success');
    if (d.quest_awarded) showToast('+' + d.quest_awarded.xp + ' XP!', 'success');
    loadTodayData(); // canonical refresh: summary + ledger
    return;
  }
  if (refused) {
    // Refused: nothing was written, so the row may be tried again.
    _plannedWriteLocks.delete(mealKey);
    delete btn.dataset.writeState;
    btn.disabled = false;
    showToast((d && d.error) || __t('nutrition.add_error'), 'error');
    return;
  }
  // 5xx, network or unreadable reply: the write may have committed. Neither
  // "Logged" nor "failed" is known, so the row stays locked and says so until
  // a reload; the one ledger re-read below refreshes the day but never decides
  // this row. Nothing is re-sent, by us or by a second tap.
  _plannedWriteLocks.set(mealKey, 'unconfirmed');
  lockPlannedRow(btn, 'unconfirmed');
  showToast(__t('nutrition.quick_add_uncertain'), 'warning');
  loadTodayData();
}


/* ── SU TAKİBİ (Water tracking) — WaterLog (/water) is the only authority ──
   "Su Takibi" bardak widget'ı ile "Bugün" Hızlı Ekle butonu aynı durumu paylaşır.
   The page shows only what the server CONFIRMED:
   - a failed read is UNKNOWN — never 0 and never a browser cache;
   - a sent write is not a saved write: controls lock and nothing is announced
     until POST /water answers. Its body is the committed count, so it IS the
     confirmation — no second read;
   - an ambiguous answer (5xx / network / unreadable body) gets exactly ONE
     reconciliation GET; if that fails too the state is "unconfirmed" and the
     write is never re-sent automatically;
   - every request takes a waterSeq ticket and a superseded answer is dropped,
     so an older response can never overwrite newer truth. */
const WATER_GOAL_N = 8;
let waterConfirmed = null;   // last server-confirmed count; null = never confirmed
let waterState = 'loading';  // loading | confirmed | saving | unavailable | unconfirmed
let waterSeq = 0;
let waterShown = null;       // count currently drawn (bump animation only)

/* → {kind:'ok', count} | {kind:'rejected'} (4xx: refused, nothing written)
     | {kind:'unknown'} (5xx, network, unreadable: may have been written) */
async function requestWater(init) {
  let res;
  try { res = await fetch('/water', init); } catch (e) { return { kind: 'unknown' }; }
  if (res.status >= 400 && res.status < 500) return { kind: 'rejected' };
  if (!res.ok) return { kind: 'unknown' };
  let d = null;
  try { d = await res.json(); } catch (e) {}
  const n = d && d.count;
  if (!Number.isInteger(n) || n < 0 || n > WATER_GOAL_N) return { kind: 'unknown' };
  return { kind: 'ok', count: n };
}

function confirmWater(n) {
  waterConfirmed = n;
  waterState = 'confirmed';
}

async function loadWater() {
  const seq = ++waterSeq;
  waterState = 'loading';
  renderWater(false);
  const r = await requestWater();
  if (seq !== waterSeq) return;
  if (r.kind === 'ok') confirmWater(r.count);
  else waterState = 'unavailable';
  renderWater(false);
}

/* Set the day's total. Resolves to the count when the server committed exactly
   the requested total, otherwise null (nothing to announce). */
async function saveWaterCount(next) {
  if (waterState !== 'confirmed' || next === waterConfirmed) return null;
  const seq = ++waterSeq;
  waterState = 'saving';
  renderWater(false);
  const r = await requestWater({
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ count: next })
  });
  if (seq !== waterSeq) return null;
  if (r.kind === 'ok') {
    confirmWater(r.count);
    renderWater();
    return r.count === next ? next : null;
  }
  if (r.kind === 'rejected') {
    waterState = 'confirmed';
    renderWater(false);
    showToast(__t('nutrition.water_save_failed'), 'error');
    return null;
  }
  const check = await requestWater();
  if (seq !== waterSeq) return null;
  if (check.kind !== 'ok') {
    waterState = 'unconfirmed';
    renderWater(false);
    return null;
  }
  confirmWater(check.count);
  renderWater();
  if (check.count === next) return next;
  showToast(__t('nutrition.water_save_failed'), 'error');
  return null;
}

function announceWater(prev, count) {
  if (count === null || count <= prev) return;
  if (count >= WATER_GOAL_N) showToast(__t('nutrition.water_goal_reached'), 'success');
  else showToast(__t('nutrition.cup_drunk', { n: count }), 'info');
}

function retryWater() {
  if (waterState === 'unavailable' || waterState === 'unconfirmed') loadWater();
}

/* Bardak widget'ını ("Su Takibi" sekmesi) + Hızlı Ekle altyazısını çiz */
function renderWater(animate) {
  const known = waterState === 'confirmed' || waterState === 'saving';
  const count = known ? waterConfirmed : 0;
  const locked = waterState !== 'confirmed';

  const card = document.querySelector('.water-card');
  if (card) {
    card.dataset.waterState = waterState;
    card.setAttribute('aria-busy', String(waterState === 'loading' || waterState === 'saving'));
  }
  const sub = document.getElementById('qab-water-sub');
  if (sub) {
    sub.textContent = known ? __t('nutrition.water_progress', { n: count, goal: WATER_GOAL_N })
      : __t(waterState === 'loading' ? 'nutrition.water_loading' : 'nutrition.water_progress_unknown');
  }
  const qab = document.getElementById('qab-water');
  if (qab) qab.disabled = locked;

  const numEl = document.getElementById('water-num');
  if (numEl) {
    numEl.textContent = known ? count : '—';
    if (animate !== false && known && waterShown !== null && count > waterShown) {
      numEl.classList.remove('bump'); void numEl.offsetWidth; numEl.classList.add('bump');
      setTimeout(() => numEl.classList.remove('bump'), 220);
    }
  }
  waterShown = known ? count : null;
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
    btn.disabled = locked || count >= WATER_GOAL_N;
    if (known && count >= WATER_GOAL_N) {
      btn.innerHTML = __t('nutrition.water_goal_btn');
    } else {
      btn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> ' + __t('nutrition.add_cup');
    }
  }

  const status = document.getElementById('water-status');
  if (status) {
    const key = { loading: 'nutrition.water_loading', saving: 'nutrition.water_saving',
                  unavailable: 'nutrition.water_unavailable',
                  unconfirmed: 'nutrition.water_unconfirmed' }[waterState];
    status.textContent = key ? __t(key) : '';
  }
  const lastKnown = document.getElementById('water-last-known');
  if (lastKnown) {
    lastKnown.textContent = !known && waterConfirmed !== null
      ? __t('nutrition.water_last_confirmed', { n: waterConfirmed, goal: WATER_GOAL_N }) : '';
  }
  const retry = document.getElementById('water-retry');
  if (retry) retry.hidden = !(waterState === 'unavailable' || waterState === 'unconfirmed');
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
      if (waterState !== 'confirmed') return;
      const cur = waterConfirmed;
      saveWaterCount(i < cur ? i : i + 1).then(n => announceWater(cur, n));
    });
    c.appendChild(g);
  }
}

/* "Bardak Ekle" butonu ("Su Takibi" sekmesi) */
function addWater() {
  if (waterState !== 'confirmed' || waterConfirmed >= WATER_GOAL_N) return;
  const cur = waterConfirmed;
  saveWaterCount(cur + 1).then(n => announceWater(cur, n));
}

/* "Hızlı Ekle" su butonu ("Bugün" sekmesi) */
async function quickAddWater(btn) {
  if (waterState !== 'confirmed') return;
  if (waterConfirmed >= WATER_GOAL_N) { showToast(__t('nutrition.water_goal_reached'), 'success'); return; }
  const cur = waterConfirmed;
  const n = await saveWaterCount(cur + 1);
  if (n === null) return;
  announceWater(cur, n);
  btn.querySelector('.qab-plus').style.display = 'none';
  btn.querySelector('.qab-check').style.display = '';
  setTimeout(() => {
    btn.querySelector('.qab-plus').style.display = '';
    btn.querySelector('.qab-check').style.display = 'none';
  }, 1500);
}

function initWaterButton() {
  buildWaterGlasses();
  return loadWater();
}

/* ── WATER MODAL ── */
function openWater()  { document.getElementById('water-modal').classList.add('open'); }
function closeWater() { document.getElementById('water-modal').classList.remove('open'); }
function logWater() {
  const ml = document.getElementById('water-amount').value;
  closeWater();
  showToast(__t('nutrition.water_logged_ml', { ml: ml }), 'success');
}

/* ── FOOD AUTOCOMPLETE ── */
let acTimeout = null;
let selectedFoods = [];
let acController = null;
/* Bir "kayıt" partisinin sabit anahtar kökü + besin-başına kararlı son ek.
   Kısmi başarıdan sonraki yeniden denemede AYNI anahtarlar gider → sunucu
   replay eder (ikinci satır/ikinci XP yok). Parti ancak tamamı yazıldığında
   sıfırlanır. */
let _selectedBatchKey = null;
let _selectedSeq = 0;

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
    // A refusal (503 capacity, 429) is not "no results": say so, never "not found".
    if (!res.ok) {
      dropdown.innerHTML = '<div class="autocomplete-item" style="color:var(--text-3);cursor:default;">' + esc(data.error || __t('error.ai_busy')) + '</div>';
      dropdown.style.display = 'block';
      return;
    }
    if (!data.results.length) {
      dropdown.innerHTML = '<div class="autocomplete-item" style="color:var(--text-3);cursor:default;">' + __t('nutrition.no_result_freetext') + '</div>';
      dropdown.style.display = 'block';
      return;
    }
    dropdown.innerHTML = data.results.map(f => {
      const fj = JSON.stringify(f).replace(/&/g,'&amp;').replace(/'/g,'&#39;').replace(/"/g,'&quot;');
      return `<div class="autocomplete-item" data-action="fxSelectFood" data-f="${fj}">
        <div class="ac-name">${esc(f.name)}${f.brand ? ' <span class="ac-brand">(' + esc(f.brand) + ')</span>' : ''}</div>
        <div class="ac-macros"><strong>${Math.round(f.macros.calories)}</strong> kcal · ${MA.p}: ${Math.round(f.macros.protein)}g · ${MA.k}: ${Math.round(f.macros.carbs)}g · ${MA.y}: ${Math.round(f.macros.fat)}g${f.serving ? ' · ' + esc(f.serving) : ''}</div>
      </div>`;
    }).join('');
    dropdown.style.display = 'block';
  } catch (e) {
    if (e.name !== 'AbortError') dropdown.style.display = 'none';
  }
}

/* F4: arama sonucu ARTIK doğrudan listeye girmez. Bir besin ancak AÇIK bir
   porsiyon/adet (ya da gramaj) seçildikten sonra kayıt komutuna dönüşebilir;
   bunun için MEVCUT porsiyon modali 'select' modunda yeniden kullanılır — yeni
   bir UX sistemi kurulmaz. */
function selectFood(food) {
  document.getElementById('food-search-input').value = '';
  document.getElementById('food-autocomplete-dropdown').style.display = 'none';
  openSelectServing(food);
}

/* Porsiyon/adet (veya gramaj) SEÇİLDİKTEN sonra çağrılır. Sağlayıcı kimliği
   varsa komut kimlik taşır (makro sunucuda yeniden hesaplanır); yoksa
   kullanıcının seçtiği gramajla elle komut olur. Önizleme yalnızca gösterimdir. */
function addSelectedFood(food, serving, quantity, grams) {
  const entry = { name: food.name, slot: String(_selectedSeq++) };
  if (food.food_id && serving) {
    entry.food_id = food.food_id;
    entry.serving_id = serving.serving_id;
    entry.quantity = quantity;
    entry.discovery_source = food.discovery_source || 'search';
    entry.preview = {
      kalori: (serving.calories || 0) * quantity,
      protein: (serving.protein || 0) * quantity,
      karb: (serving.carbs || 0) * quantity,
      yag: (serving.fat || 0) * quantity,
    };
  } else {
    const base = food.per_100g || {};
    const scale = grams / 100;
    entry.label = food.name + ' (' + Math.round(grams) + 'g)';
    entry.manual = {
      kalori: (base.calories || 0) * scale,
      protein: (base.protein || 0) * scale,
      karb: (base.carbs || 0) * scale,
      yag: (base.fat || 0) * scale,
    };
    entry.preview = entry.manual;
    // Sıfır-makro satırı kanonik deftere YAZILMAZ (meallog/social ile aynı
    // koruma): ne sağlayıcı kimliği ne de ölçülebilir bir makro varsa ekleme.
    const total = entry.manual.kalori + entry.manual.protein
      + entry.manual.karb + entry.manual.yag;
    if (!(total > 0)) { showToast(__t('nutrition.add_error'), 'error'); return; }
  }
  selectedFoods.push(entry);
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
        <div style="font-size:13px;color:var(--text);">${esc(f.label || f.name)}</div>
        <div style="font-size:11px;color:var(--text-3);">${Math.round(f.preview.kalori)} kcal · ${MA.p}:${Math.round(f.preview.protein)}g ${MA.k}:${Math.round(f.preview.karb)}g ${MA.y}:${Math.round(f.preview.yag)}g</div>
      </div>
    </div>`).join('');
  // Yalnızca ÖNİZLEME toplamı — kalıcı otorite sunucudadır (F4/F5).
  const t = selectedFoods.reduce((acc, f) => ({
    cal: acc.cal + (f.preview.kalori || 0), p: acc.p + (f.preview.protein || 0),
    k: acc.k + (f.preview.karb || 0), y: acc.y + (f.preview.yag || 0)
  }), {cal:0, p:0, k:0, y:0});
  totals.innerHTML = __t('nutrition.total_label') + ' <strong style="color:var(--color-primary);">' + Math.round(t.cal) + '</strong> kcal · ' + MA.p + ': ' + Math.round(t.p) + 'g · ' + MA.k + ': ' + Math.round(t.k) + 'g · ' + MA.y + ': ' + Math.round(t.y) + 'g';
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
/* Same four meal slots as the timeline above, so they take the same icons —
   the diary tab used to render full-colour emoji next to the timeline's
   stroked SVGs, which read as two different products on one page. */
const DIARY_MEALS = [
  { key: 'Kahvaltı', icon: _SLOT_ICONS.breakfast },
  { key: 'Öğle',     icon: _SLOT_ICONS.lunch },
  { key: 'Akşam',    icon: _SLOT_ICONS.dinner },
  { key: 'Ara Öğün', icon: _SLOT_ICONS.snack }
];

/* NUTR-PR4: the builder's staging read has its own state on #diary-meals
   (loading · available · unavailable). A failed or unreadable read is
   UNAVAILABLE with a retry — never four empty meals, which would claim the
   user has nothing staged. Staged items live on the server (CustomMeal), so
   a failed read or leaving the builder discards nothing. */
let _diaryReadGeneration = 0;
function validDiaryPayload(d) {
  return !!d && typeof d === 'object' && Array.isArray(d.meals) &&
    !!d.totals && typeof d.totals === 'object';
}

async function loadDiary() {
  const box = document.getElementById('diary-meals');
  const generation = ++_diaryReadGeneration;
  if (box.dataset.diaryState !== 'available') {
    box.dataset.diaryState = 'loading';
    box.setAttribute('aria-busy', 'true');
  }
  try {
    const res = await fetch('/api/diary/today');
    if (!res.ok) throw new Error('Diary read failed');
    const data = await res.json();
    if (generation !== _diaryReadGeneration) return;
    if (!validDiaryPayload(data)) throw new Error('Diary read invalid');
    renderDiary(data);
    box.dataset.diaryState = 'available';
  } catch (e) {
    console.error('loadDiary', e);
    if (generation !== _diaryReadGeneration) return;
    renderDiaryFailure();
  } finally {
    if (generation === _diaryReadGeneration) box.setAttribute('aria-busy', 'false');
  }
}

function renderDiaryFailure() {
  const box = document.getElementById('diary-meals');
  box.dataset.diaryState = 'unavailable';
  box.innerHTML = `
    <div class="nut-planned-state diary-unavailable" role="status">
      <p class="nut-planned-note">${esc(__t('nutrition.diary_unavailable'))}</p>
      <button type="button" class="btn-ghost nut-retry" data-action="loadDiary">${esc(__t('nutrition.try_again'))}</button>
    </div>`;
  document.getElementById('diary-grand-total').style.display = 'none';
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
          <span class="diary-unit">g</span>`;
      }
      return `<div class="diary-food-row" data-item-id="${item.id}">
        <div class="diary-food-info">
          <div class="diary-food-name">${esc(item.food_name)}</div>
          <div class="diary-food-macros">${Math.round(item.calories)} kcal · ${MA.p}:${Math.round(item.protein)}g ${MA.k}:${Math.round(item.carbs)}g ${MA.y}:${Math.round(item.fat)}g</div>
        </div>
        ${unitHtml}
        ${!isLogged ? '<button class="sf-remove" data-action="deleteDiaryItem" data-args="[' + item.id + ']">✕</button>' : ''}
      </div>`;
    }).join('');

    return `
      <div class="card diary-meal-card" data-meal-name="${dm.key}" data-meal-id="${mealId}">
        <div class="diary-meal-hdr">
          <div class="diary-meal-title">
            <span class="dm-icon" aria-hidden="true">${dm.icon}</span>
            <span class="diary-meal-name">${mealLabel(dm.key)}</span>
          </div>
          <span class="diary-meal-kcal">${Math.round(totals.calories)} kcal</span>
        </div>
        <div class="diary-items-list">${itemsHtml}</div>
        ${!isLogged ? `
        <div class="diary-search-wrap">
          <input class="fc-input diary-food-search" placeholder="${__t('nutrition.search_short')}"
            data-action-input="fxDiaryFoodSearch" data-meal="${dm.key}" autocomplete="off">
          <div class="autocomplete-dropdown diary-ac" style="display:none;"></div>
        </div>
        <button class="btn-volt w-full diary-log-btn" data-action="logDiaryMeal" data-args='["${dm.key}"]'>${__t('nutrition.log_this_meal')}</button>
        ` : `
        <div class="diary-logged">${__t('nutrition.logged')}</div>
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
      if (!res.ok) {
        dropdown.innerHTML = '<div class="autocomplete-item" style="color:var(--text-3);">' + esc(data.error || __t('error.ai_busy')) + '</div>';
        dropdown.style.display = 'block';
        return;
      }
      if (!data.results.length) {
        dropdown.innerHTML = '<div class="autocomplete-item" style="color:var(--text-3);">' + __t('nutrition.no_result') + '</div>';
        dropdown.style.display = 'block';
        return;
      }
      dropdown.innerHTML = data.results.map(f => {
        const fj = JSON.stringify(f).replace(/&/g,'&amp;').replace(/'/g,'&#39;').replace(/"/g,'&quot;');
        return `<div class="autocomplete-item" data-action="fxAddDiaryFood" data-meal="${mealName}" data-f="${fj}">
          <div class="ac-name">${esc(f.name)}</div>
          <div class="ac-macros"><strong>${Math.round(f.per_100g.calories)}</strong> kcal/100g · ${MA.p}:${Math.round(f.per_100g.protein)}g · ${MA.k}:${Math.round(f.per_100g.carbs)}g · ${MA.y}:${Math.round(f.per_100g.fat)}g${f.serving && f.is_per_serving ? ' · ' + esc(f.serving) : ''}</div>
        </div>`;
      }).join('');
      dropdown.style.display = 'block';
    } catch (e) {
      if (e.name !== 'AbortError') dropdown.style.display = 'none';
    }
  }, 350);
}

/* ── SERVING MODAL STATE ──
   İki mod: 'diary' (öğün oluşturucuya ekle — varsayılan) ve 'meallog' (barkod
   → bugünkü zaman çizelgesine doğrudan kaydet). Aynı modal DOM'u yeniden kullanılır. */
let _smFood = null;
let _smMealName = null;
let _smServings = null;
let _smMode = 'diary';
let _smLogOgun = 'Kahvaltı';
let _smOpener = null;

/* Modal alanlarını başlangıç durumuna getir (gram modu görünür). */
function _smResetFields(food) {
  document.getElementById('sm-food-name').textContent = food.name || '';
  document.getElementById('sm-brand').textContent = food.brand || '';
  document.getElementById('sm-serving-row').style.display = 'none';
  document.getElementById('sm-qty-row').style.display = 'none';
  document.getElementById('sm-gram-row').style.display = 'block';
  document.getElementById('sm-gram-input').value = 100;
  document.getElementById('sm-qty-input').value = 1;
  document.getElementById('sm-confirm-btn').disabled = false;
  var modal = document.getElementById('serving-modal');
  _smOpener = document.activeElement;
  modal.classList.add('open');
  updateSmPreview();
  _focusFirstVisible(modal);
}

/* Porsiyon listesini modale uygula (fetch veya barkod ile hazır gelen). */
function _smApplyServings(servings) {
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
    const is100 = s => s.serving_description === '100 g' || s.serving_description === '100g';
    let preferred = servings.findIndex(s => !s.is_bulk && !is100(s));
    if (preferred < 0) preferred = servings.findIndex(is100);
    if (preferred >= 0) select.selectedIndex = preferred;
    document.getElementById('sm-serving-row').style.display = 'block';
    document.getElementById('sm-qty-row').style.display = 'block';
    document.getElementById('sm-gram-row').style.display = 'none';
  }
  updateSmPreview();
}

function openServingModal(mealName, food) {
  _smMode = 'diary';
  _smFood = food;
  _smMealName = mealName;
  _smServings = null;

  const searchInput = document.querySelector('[data-meal-name="' + mealName + '"] .diary-food-search');
  if (searchInput) { searchInput.value = ''; searchInput.nextElementSibling.style.display = 'none'; }

  _smResetFields(food);
  // The builder re-renders after an add, so its search field cannot be the
  // stable return point: the builder title is.
  _smOpener = document.getElementById('diary-builder-title');

  const lookupKey = food.food_id || food.name;
  if (!lookupKey) {
    document.getElementById('sm-loading').style.display = 'none';
    return;
  }
  document.getElementById('sm-loading').style.display = 'flex';
  fetchServings(lookupKey).then(_smApplyServings);
}

/* Barkod akışı: çözülen besin + hazır porsiyonlarla modali 'meallog' modunda aç. */
function openMealLogServing(food, ogun) {
  _smMode = 'meallog';
  _smFood = food;
  _smMealName = null;
  _smLogOgun = ogun || _smLogOgun;
  _smServings = null;
  _smResetFields(food);
  if (food.servings && food.servings.length) {
    _smApplyServings(food.servings);
  } else if (food.food_id || food.name) {
    document.getElementById('sm-loading').style.display = 'flex';
    fetchServings(food.food_id || food.name).then(_smApplyServings);
  } else {
    document.getElementById('sm-loading').style.display = 'none';
  }
}

/* Çok-besinli hızlı kayıt akışı: aynı modalı 'select' modunda aç. Onaylandığında
   defter YAZILMAZ — yalnızca seçilen porsiyon/adet (veya gramaj) `selectedFoods`
   listesine AÇIK bir komut olarak eklenir (F4). */
function openSelectServing(food) {
  _smMode = 'select';
  _smFood = food;
  _smMealName = null;
  _smServings = null;
  _smResetFields(food);
  const lookupKey = food.food_id || food.name;
  if (!lookupKey) {
    document.getElementById('sm-loading').style.display = 'none';
    return;
  }
  document.getElementById('sm-loading').style.display = 'flex';
  fetchServings(lookupKey).then(_smApplyServings);
}

function closeServingModal() {
  var modal = document.getElementById('serving-modal');
  var wasOpen = modal.classList.contains('open');
  modal.classList.remove('open');
  // Back to whatever opened it (the search field for Search food, the
  // builder title for Build meal); a barcode lookup falls back to Log food.
  if (wasOpen) _returnFocus(modal, _smOpener);
  _smOpener = null;
  _smFood = null; _smMealName = null; _smServings = null; _smMode = 'diary';
}

/* Modaldeki mevcut porsiyon seçimi (porsiyon nesnesi + adet) veya null. */
function _smSelectedServing() {
  if (!_smServings) return null;
  const select = document.getElementById('sm-serving-select');
  const srv = _smServings.find(s => s.serving_id === select.value);
  if (!srv) return null;
  const qty = parseFloat(document.getElementById('sm-qty-input').value) || 1;
  return { serving: srv, quantity: qty };
}

function _smSelectedGrams() {
  return parseFloat(document.getElementById('sm-gram-input').value) || 100;
}

/* Modaldeki mevcut seçimden makroları hesapla (porsiyon×adet veya gram).
   Gram modu yalnızca per_100g varsa hesaplanır (barkod besininde olmayabilir). */
function _smCurrentMacros() {
  let cal = 0, pro = 0, carb = 0, fat = 0;
  if (_smServings) {
    const select = document.getElementById('sm-serving-select');
    const srv = _smServings.find(s => s.serving_id === select.value);
    const qty = parseFloat(document.getElementById('sm-qty-input').value) || 1;
    if (srv) {
      cal = srv.calories * qty; pro = srv.protein * qty;
      carb = srv.carbs * qty; fat = srv.fat * qty;
    }
  } else if (_smFood && _smFood.per_100g) {
    const grams = parseFloat(document.getElementById('sm-gram-input').value) || 100;
    const p = _smFood.per_100g;
    const scale = grams / 100;
    cal = p.calories * scale; pro = p.protein * scale;
    carb = p.carbs * scale; fat = p.fat * scale;
  }
  return { kalori: cal, protein: pro, karb: carb, yag: fat };
}

function updateSmPreview() {
  const m = _smCurrentMacros();
  document.getElementById('sm-cal').textContent = Math.round(m.kalori);
  document.getElementById('sm-pro').textContent = Math.round(m.protein) + 'g';
  document.getElementById('sm-carb').textContent = Math.round(m.karb) + 'g';
  document.getElementById('sm-fat').textContent = Math.round(m.yag) + 'g';
}

/* Barkod/porsiyon modalinden kanonik deftere yazma (F5/F16). Tarayıcının
   hesapladığı `_smCurrentMacros()` YALNIZCA önizlemedir ve GÖNDERİLMEZ: sunucuya
   besin + porsiyon KİMLİĞİ ve adet gider, makro sağlayıcı gerçeğinden orada
   yeniden hesaplanır. Porsiyon kimliği çözülemiyorsa (sağlayıcı porsiyon
   döndürmedi) KAYIT YAPILMAZ — gram-modu önizlemesinden uydurma bir sağlayıcı
   satırı yazmak, kapatılan tam da o güven sınırıdır. */
async function logProviderFoodToLedger(food, ogun) {
  const picked = _smSelectedServing();
  if (!food || !food.food_id || !picked) {
    showToast(__t('nutrition.add_error'), 'error');
    return;
  }
  try {
    const res = await fetch('/meal-log', {
      method: 'POST', headers: mealWriteHeaders(),
      body: JSON.stringify({
        ogun: ogun,
        provider_food: {
          provider: 'fatsecret',
          food_id: food.food_id,
          serving_id: picked.serving.serving_id,
          quantity: picked.quantity,
          discovery_source: food.discovery_source || 'barcode',
        },
      })
    });
    const d = await res.json();
    if (d.error) { showToast(d.error, 'error'); return; }
    showToast(__t('nutrition.meal_saved'), 'success');
    if (d.quest_awarded) showToast('+' + d.quest_awarded.xp + ' XP!', 'success');
    closeServingModal();
    loadTodayData();
  } catch (e) {
    showToast(__t('nutrition.add_error'), 'error');
  }
}

async function confirmServingModal() {
  if (!_smFood) return;
  const btn = document.getElementById('sm-confirm-btn');
  btn.disabled = true;
  btn.textContent = __t('nutrition.adding');

  // ── 'select' modu (çok-besinli hızlı kayıt) → yalnızca listeye ekle ──
  if (_smMode === 'select') {
    const picked = _smSelectedServing();
    addSelectedFood(_smFood, picked && picked.serving,
                    picked ? picked.quantity : 0, _smSelectedGrams());
    closeServingModal();
    btn.disabled = false;
    btn.textContent = __t('nutrition.add');
    return;
  }

  // ── 'meallog' modu (barkod) → doğrudan bugünkü kanonik deftere yaz ──
  if (_smMode === 'meallog') {
    await logProviderFoodToLedger(_smFood, _smLogOgun);
    btn.disabled = false;
    btn.textContent = __t('nutrition.add');
    return;
  }

  // ── 'diary' modu (öğün oluşturucu) ──
  if (!_smMealName) { btn.disabled = false; btn.textContent = __t('nutrition.add'); return; }

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
        serving_quantity: qty,
      };
    }
  }
  if (!body) {
    const grams = parseFloat(document.getElementById('sm-gram-input').value) || 100;
    body = {
      food_name: _smFood.name, grams: grams,
      per_100g: _smFood.per_100g,
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
  } catch (e) { showToast(__t('nutrition.add_error'), 'error'); }
  btn.disabled = false;
  btn.textContent = __t('nutrition.add');
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
  } catch (e) { showToast(__t('nutrition.update_error'), 'error'); }
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
        serving_quantity: qty,
      })
    });
    loadDiary();
  } catch (e) { showToast(__t('nutrition.update_error'), 'error'); }
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
      serving_quantity: qty,
    } : { serving_quantity: qty };
    await fetch('/api/diary/item/' + itemId, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    loadDiary();
  } catch (e) { showToast(__t('nutrition.update_error'), 'error'); }
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
  } catch (e) { showToast(__t('nutrition.update_error'), 'error'); }
}

async function deleteDiaryItem(itemId) {
  try {
    await fetch('/api/diary/item/' + itemId, { method: 'DELETE' });
    loadDiary();
  } catch (e) { showToast(__t('nutrition.delete_error'), 'error'); }
}

async function logDiaryMeal(mealName) {
  const card = document.querySelector('[data-meal-name="' + mealName + '"]');
  const mealId = card.dataset.mealId;
  if (!mealId) { showToast(__t('nutrition.add_food_first'), 'error'); return; }
  try {
    const res = await fetch('/api/diary/meal/' + mealId + '/log', { method: 'POST' });
    const d = await res.json();
    if (d.error) { showToast(d.error, 'error'); return; }
    if (!res.ok) { showToast(__t('nutrition.save_error'), 'error'); return; }
    showToast(__t('nutrition.meal_saved_named', { meal: mealLabel(mealName) }), 'success');
    if (window.fxTrackOnce) fxTrackOnce('first_meal_logged');
    if (window.fxActivation) fxActivation('meal');
    if (d.quest_awarded) showToast('+' + d.quest_awarded.xp + ' XP!', 'success');
    loadDiary();
    // Diary is inline in Today: no tab transition will refresh its parent.
    // Re-read the ledger; staging totals are not canonical consumed totals.
    loadTodayData(true);
    if (_nutritionNavigation.mode === 'today' &&
        document.getElementById('nutrition-tool-history').open) {
      loadMealHistory(true);
    } else {
      // Hidden History stays lazy, including an open disclosure under Plan.
      _nutritionLoadedTools.delete('history');
      ++_historyReadGeneration;
      _historyRefreshPending = true;
    }
  } catch (e) { showToast(__t('nutrition.save_error'), 'error'); }
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
initNutritionNavigation();
populateFoods();
loadTodayData();
loadQuickAddSection();
loadActivePlan();
initWaterButton();
loadDayView();
