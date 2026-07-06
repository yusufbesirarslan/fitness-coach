
/* ── CONSTANTS ── */
const RING_CIRC = 301.6; // 2π × 48
let targetCalories = 2000;
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
/* Skor etiketi: backend TR döndürür → görünen etiket. */
var SCORE_LABELS_EN = { 'İyi': 'Good', 'Orta': 'Fair', 'Kötü': 'Poor' };
function mealLabel(v)  { return (_EN && MEAL_LABELS_EN[v])  ? MEAL_LABELS_EN[v]  : v; }
function foodLabel(v)  { return (_EN && FOOD_LABELS_EN[v])  ? FOOD_LABELS_EN[v]  : v; }
function scoreLabel(v) { return (_EN && SCORE_LABELS_EN[v]) ? SCORE_LABELS_EN[v] : v; }

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
  if (isBulk) label += ' ⚠ ' + __t('nutrition.full_recipe');
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
  ring.style.stroke = eaten > target * 1.05 ? '#FF4D4D' : '#3D8BFF';
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

    // Render meal timeline
    renderTimeline(today.meals || []);

  } catch (e) {
    console.error('loadTodayData', e);
  }
}

/* ── AI SCORE (client-side, deterministic) ──
   Öğünün kendi makrolarından 0-100 + harf notu üretir. Ağ YOK, offline-güvenli
   (Faz 3 kural motoruyla aynı felsefe). */
function mealScore(m) {
  var kcal = Math.max(+m.kalori || 0, 1);
  var pK = (+m.protein || 0) * 4, cK = (+m.karb || 0) * 4, fK = (+m.yag || 0) * 9;
  var sum = Math.max(pK + cK + fK, 1);
  var pShare = pK / sum, cShare = cK / sum, fShare = fK / sum;
  // 1) Protein yoğunluğu (0-40): ideal protein payı >= 0.30
  var pScore = Math.min(pShare / 0.30, 1) * 40;
  // 2) Makro denge (0-35): aşırı yağ (>0.40) veya karb (>0.60) payını cezalandır
  var balance = 35;
  if (fShare > 0.40) balance -= (fShare - 0.40) * 60;
  if (cShare > 0.60) balance -= (cShare - 0.60) * 50;
  balance = Math.max(0, balance);
  // 3) Kalori makullüğü (0-25): 900 kcal'e kadar tam, sonra azalır
  var cal = 25;
  if (kcal > 900) cal -= Math.min((kcal - 900) / 30, 25);
  cal = Math.max(0, cal);
  var value = Math.round(pScore + balance + cal);
  var grade, tone;
  if (value >= 75)      { grade = 'A'; tone = 'success'; }
  else if (value >= 55) { grade = 'B'; tone = 'success'; }
  else if (value >= 40) { grade = 'C'; tone = 'warning'; }
  else                  { grade = 'D'; tone = 'danger';  }
  return { value: value, grade: grade, tone: tone };
}

/* ── MEAL TIMELINE ── */
var SLOTS = [
  { key: 'Kahvaltı', emoji: '🍳' },
  { key: 'Öğle',     emoji: '🥗' },
  { key: 'Akşam',    emoji: '🍽️' },
  { key: 'Ara Öğün', emoji: '🥜' },
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
  var s = mealScore(m);
  var img = m.photo_url
    ? '<img class="mc-img" src="' + esc(m.photo_url) + '" alt="">'
    : '<div class="mc-img">' + _MEAL_PLACEHOLDER_SVG + '</div>';
  var time = fmtTime(m.created_at);
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
      '<span class="badge badge-' + s.tone + '" title="' + __t('nutrition.ai_score') + ' ' + s.value + '/100">' + s.grade + '</span>' +
      '<button class="mc-edit" data-action="quickEditMeal" data-args=\'["' + esc(m.ogun) + '"]\' aria-label="' + __t('nutrition.quick_edit') + '">' +
        '<svg viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>' +
      '</button>' +
    '</div></div>';
}

function renderTimeline(meals) {
  var box = document.getElementById('meal-timeline');
  if (!box) return;
  var bySlot = { 'Kahvaltı': [], 'Öğle': [], 'Akşam': [], 'Ara Öğün': [] };
  (meals || []).forEach(function (m) {
    (bySlot[m.ogun] || bySlot['Ara Öğün']).push(m);
  });
  box.innerHTML = SLOTS.map(function (slot) {
    var items = bySlot[slot.key] || [];
    var kcal = items.reduce(function (a, m) { return a + (m.kalori || 0); }, 0);
    var head = '<div class="slot-head"><span class="slot-emoji">' + slot.emoji +
      '</span><span class="slot-name">' + esc(mealLabel(slot.key)) + '</span>' +
      '<span class="slot-kcal">' + Math.round(kcal) + ' kcal</span></div>';
    var body = items.length
      ? items.map(mealCardHTML).join('')
      : '<div class="slot-empty" data-action="logManualSlot" data-args=\'["' + esc(slot.key) +
          '"]\'>+ ' + __t('nutrition.add_to_meal') + '</div>';
    return '<div class="meal-slot">' + head + body + '</div>';
  }).join('');
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

/* ── LOG BOTTOM SHEET (FAB) ── */
function openLogSheet()  { document.getElementById('log-sheet').classList.add('open'); }
function closeLogSheet() { document.getElementById('log-sheet').classList.remove('open'); }

/* ── MANUAL ENTRY SHEET ── */
function openManualSheet()  { closeLogSheet(); document.getElementById('manual-sheet').classList.add('open'); }
function closeManualSheet() { document.getElementById('manual-sheet').classList.remove('open'); }

/* ── VOICE PLACEHOLDER SHEET (mobil uygulamada) ──
   Web MVP'de sesli giriş YOK; bileşen mimarisi native iOS/Android STT için hazır.
   NATIVE-VOICE-HOOK: native STT metnini şuraya bağla:
     selectMealTypeByValue(<algılanan öğün>); openManualSheet();
     document.getElementById('meal-input').value = <transkript>;
   Böylece UI/UX değişmeden native ses kaydı takılabilir. */
function logVoice()        { closeLogSheet(); document.getElementById('voice-sheet').classList.add('open'); }
function closeVoiceSheet() { document.getElementById('voice-sheet').classList.remove('open'); }

/* ── MENU SCANNER (mevcut koç widget'ını yeniden kullan) ── */
function logMenuScan() {
  closeLogSheet();
  if (window.CW && typeof window.CW.startScan === 'function') {
    window.CW.startScan();               // #cw-scan overlay'ini kendisi açar
  } else {
    showToast(__t('nutrition.menu_unavailable'), 'error');
  }
}

/* ── MANUAL / TAKE PHOTO FAB OPTIONS ── */
function logManual()    { openManualSheet(); }
function logTakePhoto() { closeLogSheet(); document.getElementById('photo-input').click(); }

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
  document.getElementById('photo-modal').classList.add('open');
}
function closePhotoConfirm() {
  document.getElementById('photo-modal').classList.remove('open');
  _photoDataUrl = null;
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
    var res = await fetch('/meal-log', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
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
    if (d.quest_awarded) showToast('\u{1F3AF} +' + d.quest_awarded.xp + ' XP!', 'success');
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
  document.getElementById('scan-overlay').classList.remove('open');
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
      showToast(__t('nutrition.meal_saved'), 'success');
      if (d.quest_awarded) showToast('\u{1F3AF} +' + d.quest_awarded.xp + ' XP!', 'success');
      closeManualSheet();
      loadTodayData();
    } catch (e) {
      showToast(__t('nutrition.conn_error_prefix') + e.message, 'error');
    } finally {
      loading.classList.remove('active');
    }
    return;
  }

  const yemekler = input.value.trim();
  if (!yemekler) { showToast(__t('nutrition.write_or_search'), 'error'); return; }

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
    showToast(__t('nutrition.meal_saved'), 'success');
    // Funnel/aktivasyon: ilk öğün kaydı.
    if (window.fxTrackOnce) fxTrackOnce('first_meal_logged');
    if (window.fxActivation) fxActivation('meal');
    if (d.quest_awarded) showToast('\u{1F3AF} +' + d.quest_awarded.xp + ' XP!', 'success');
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
async function loadMealHistory() {
  try {
    const res = await fetch('/meal-log/history');
    const data = await res.json();

    // Weekly chart (last 7 days)
    renderWeeklyChart(data.slice(0, 7).reverse());

    // History list
    const list = document.getElementById('history-list');
    if (!data.length) {
      list.innerHTML = `<div class="empty-state"><div class="empty-icon"><svg viewBox="0 0 24 24"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg></div><div class="empty-title">${__t('nutrition.no_records')}</div></div>`;
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
  } catch (e) {
    console.error('loadMealHistory', e);
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
  el.innerHTML = `<span class="chip-dot"></span>${esc(foodLabel(name))}`;
  el.addEventListener('click', () => {
    const isSelected = el.classList.toggle('selected');
    if (isSelected) selected[category].add(name);
    else            selected[category].delete(name);
    el.querySelector('.chip-dot').style.background = isSelected ? '#3D8BFF' : '';
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
  if (!selected.proteins.size) { showToast(__t('nutrition.need_protein'), 'error'); return; }
  if (!selected.carbs.size)    { showToast(__t('nutrition.need_carb'), 'error'); return; }
  if (!selected.fats.size)     { showToast(__t('nutrition.need_fat'), 'error'); return; }

  const btn = document.getElementById('plan-btn');
  const loading = document.getElementById('loading');
  btn.classList.add('loading');
  btn.textContent = __t('nutrition.preparing');
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
    showToast(__t('nutrition.plan_failed_prefix') + e.message, 'error');
  } finally {
    btn.classList.remove('loading');
    btn.textContent = __t('nutrition.plan_create');
    loading.classList.remove('active');
  }
}

function renderPlans(data) {
  document.getElementById('plan-results').style.display = 'block';

  // Score banner
  const scoreColors = { 'İyi': '#3D8BFF', 'Orta': '#FFB020', 'Kötü': '#FF4D4D' };
  const color = scoreColors[data.score_label] || '#9A9A9A';
  document.getElementById('score-banner-wrap').innerHTML = `
    <div class="score-banner" style="margin-bottom:24px;">
      <div class="score-big" style="color:${color};">${data.overall_score}</div>
      <div>
        <div class="score-label" style="color:${color};">${scoreLabel(data.score_label)} ${__t('nutrition.plan_word')}</div>
        <div class="score-desc">${__t('nutrition.score_desc')}</div>
      </div>
    </div>`;

  // Plan cards
  const grid = document.getElementById('plans-grid');
  grid.innerHTML = '';
  data.planlar.forEach((plan, i) => {
    const meals = [
      { key:'kahvalti',  label: __t('nutrition.meal_breakfast') },
      { key:'ogle',      label: __t('nutrition.meal_lunch')     },
      { key:'aksam',     label: __t('nutrition.meal_dinner')    },
      { key:'ara_ogun',  label: __t('nutrition.meal_snack')     }
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
          <div class="plan-macro-item"><div class="plan-macro-val">${plan.toplam_protein ?? '—'}g</div><div class="plan-macro-lbl">${__t('nutrition.macro_protein')}</div></div>
          <div class="plan-macro-item"><div class="plan-macro-val">${plan.toplam_karb ?? '—'}g</div><div class="plan-macro-lbl">${__t('nutrition.carb_short')}</div></div>
          <div class="plan-macro-item"><div class="plan-macro-val">${plan.toplam_yag ?? '—'}g</div><div class="plan-macro-lbl">${__t('nutrition.macro_fat')}</div></div>
        </div>
        <button class="btn-select-plan" id="sel-btn-${i}"
          data-action="selectPlan" data-args="${JSON.stringify([i, plan, data.overall_score]).replace(/"/g,'&quot;')}">
          ${__t('nutrition.select_plan')}
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
    document.querySelectorAll('.btn-select-plan').forEach(b => { b.textContent = __t('nutrition.select_plan'); b.classList.remove('chosen'); });
    document.getElementById(`plan-card-${i}`).classList.add('chosen');
    const btn = document.getElementById(`sel-btn-${i}`);
    btn.textContent = __t('nutrition.active_plan');
    btn.classList.add('chosen');
    showToast(__t('nutrition.plan_saved'), 'success');
  } catch (e) {
    showToast(__t('nutrition.save_error_prefix') + e.message, 'error');
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
    { key: 'kahvalti', label: __t('nutrition.meal_breakfast'), icon: '🍳' },
    { key: 'ogle',     label: __t('nutrition.meal_lunch'),     icon: '🥗' },
    { key: 'aksam',    label: __t('nutrition.meal_dinner'),    icon: '🍽️' },
    { key: 'ara_ogun', label: __t('nutrition.meal_snack'),     icon: '🥜' }
  ];

  const mealsHtml = meals.map(m => {
    const ml = plan[m.key];
    if (!ml) return '';
    const items = (ml.yemekler || []).map(y => `<li>${esc(y)}</li>`).join('');
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
        <div class="apd-title">${esc(plan.isim || __t('nutrition.active_plan_name'))}</div>
        <div class="apd-sub">${createdAt} · ${__t('nutrition.score_text')} ${score}/10</div>
      </div>
      <button class="btn-ghost" data-action="resetPlan">${__t('nutrition.new_plan')}</button>
    </div>

    <div class="apd-macro-grid">
      <div class="apd-macro-item">
        <div class="apd-macro-val">${plan.toplam_kalori ?? '—'}</div>
        <div class="apd-macro-lbl">kcal</div>
      </div>
      <div class="apd-macro-item">
        <div class="apd-macro-val">${plan.toplam_protein ?? '—'}g</div>
        <div class="apd-macro-lbl">${__t('nutrition.macro_protein')}</div>
      </div>
      <div class="apd-macro-item">
        <div class="apd-macro-val">${plan.toplam_karb ?? '—'}g</div>
        <div class="apd-macro-lbl">${__t('nutrition.carb_short')}</div>
      </div>
      <div class="apd-macro-item">
        <div class="apd-macro-val">${plan.toplam_yag ?? '—'}g</div>
        <div class="apd-macro-lbl">${__t('nutrition.macro_fat')}</div>
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
          ${__t('nutrition.no_active_plan')}
        </div>`;
      return;
    }

    const MEALS = [
      { key: 'kahvalti', label: __t('nutrition.meal_breakfast'), icon: '🍳' },
      { key: 'ogle',     label: __t('nutrition.meal_lunch'),     icon: '🥗' },
      { key: 'aksam',    label: __t('nutrition.meal_dinner'),    icon: '🍽️' },
      { key: 'ara_ogun', label: __t('nutrition.meal_snack'),     icon: '🥜' }
    ];

    container.innerHTML = MEALS.map(m => {
      const ml  = d.plan[m.key];
      if (!ml) return '';
      const sub = `${ml.kalori ?? '—'} kcal · ${ml.protein ?? '—'}g ${__t('nutrition.unit_protein')} · ${ml.karb ?? '—'}g ${__t('nutrition.unit_carb')}`;
      return `
        <button class="qab" id="qab-${m.key}"
          data-action="quickAddMeal" data-args='["${m.key}","${m.label}"]' type="button">
          <span class="qab-icon">${m.icon}</span>
          <div class="qab-info">
            <div class="qab-title">${m.label} — ${esc(d.plan.isim || 'Aktif Plan')}</div>
            <div class="qab-sub">${sub}</div>
          </div>
          <svg class="qab-check" viewBox="0 0 24 24" fill="none"
               stroke="#3D8BFF" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round">
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

    showToast(`${mealLabel} ${__t('nutrition.added_suffix')}`, 'success');
    loadTodayData(); // live-refresh calorie ring + macro bars

  } catch (e) {
    showToast(__t('nutrition.add_failed_prefix') + e.message, 'error');
    btn.disabled = false;
    btn.style.opacity = '1';
  }
}


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
  if (sub) sub.textContent = __t('nutrition.water_progress', { n: n, goal: WATER_GOAL_N });
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
      btn.innerHTML = __t('nutrition.water_goal_btn');
    } else {
      btn.disabled = false;
      btn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg> ' + __t('nutrition.add_cup');
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
        if (next === WATER_GOAL_N) showToast(__t('nutrition.water_goal_reached'), 'success');
        else showToast(__t('nutrition.cup_drunk', { n: next }), 'info');
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
  if (next === WATER_GOAL_N) showToast(__t('nutrition.water_goal_reached'), 'success');
  else showToast(__t('nutrition.cup_drunk', { n: next }), 'info');
}

/* "Hızlı Ekle" su butonu ("Bugün" sekmesi) */
async function quickAddWater(btn) {
  if (waterCount >= WATER_GOAL_N) { showToast(__t('nutrition.water_goal_reached') + ' 🎉', 'success'); return; }
  const next = waterCount + 1;
  saveWaterCount(next);
  renderWater(next);
  btn.querySelector('.qab-plus').style.display = 'none';
  btn.querySelector('.qab-check').style.display = '';
  if (next >= WATER_GOAL_N) showToast(__t('nutrition.water_goal_reached') + ' 🎉', 'success');
  else showToast(__t('nutrition.cup_drunk', { n: next }) + ' 💧', 'info');
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
  showToast(__t('nutrition.water_logged_ml', { ml: ml }), 'success');
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
        <div style="font-size:11px;color:var(--text-3);">${Math.round(f.per_100g.calories)} kcal · ${MA.p}:${Math.round(f.per_100g.protein)}g ${MA.k}:${Math.round(f.per_100g.carbs)}g ${MA.y}:${Math.round(f.per_100g.fat)}g</div>
      </div>
    </div>`).join('');
  const t = selectedFoods.reduce((acc, f) => ({
    cal: acc.cal + (f.per_100g.calories || 0), p: acc.p + (f.per_100g.protein || 0),
    k: acc.k + (f.per_100g.carbs || 0), y: acc.y + (f.per_100g.fat || 0)
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
          <div style="font-size:11px;color:var(--text-3);">${Math.round(item.calories)} kcal · ${MA.p}:${Math.round(item.protein)}g ${MA.k}:${Math.round(item.carbs)}g ${MA.y}:${Math.round(item.fat)}g</div>
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
            <span style="font-family:'Bebas Neue';font-size:18px;letter-spacing:1.5px;color:var(--text);">${mealLabel(dm.key)}</span>
          </div>
          <span style="font-family:'Bebas Neue';font-size:16px;color:var(--color-primary);">${Math.round(totals.calories)} kcal</span>
        </div>
        <div class="diary-items-list">${itemsHtml}</div>
        ${!isLogged ? `
        <div style="position:relative;margin-top:10px;">
          <input class="fc-input diary-food-search" placeholder="${__t('nutrition.search_short')}"
            data-action-input="fxDiaryFoodSearch" data-meal="${dm.key}" autocomplete="off">
          <div class="autocomplete-dropdown diary-ac" style="display:none;"></div>
        </div>
        <button class="btn-volt w-full" style="margin-top:10px;" data-action="logDiaryMeal" data-args='["${dm.key}"]'>${__t('nutrition.log_this_meal')}</button>
        ` : `
        <div style="text-align:center;padding:8px;color:#00C48C;font-size:12px;font-weight:600;letter-spacing:0.1em;">${__t('nutrition.logged')}</div>
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
  document.getElementById('serving-modal').classList.add('open');
  updateSmPreview();
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

function closeServingModal() {
  document.getElementById('serving-modal').classList.remove('open');
  _smFood = null; _smMealName = null; _smServings = null; _smMode = 'diary';
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

async function confirmServingModal() {
  if (!_smFood) return;
  const btn = document.getElementById('sm-confirm-btn');
  btn.disabled = true;
  btn.textContent = __t('nutrition.adding');

  // ── 'meallog' modu (barkod) → doğrudan bugünkü kanonik deftere yaz ──
  if (_smMode === 'meallog') {
    const macros = _smCurrentMacros();
    try {
      const res = await fetch('/meal-log', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ogun: _smLogOgun,
          yemekler: _smFood.name || __t('nutrition.log_barcode'),
          override_macros: {
            kalori: macros.kalori, protein: macros.protein,
            karb: macros.karb, yag: macros.yag,
          },
        })
      });
      const d = await res.json();
      if (d.error) { showToast(d.error, 'error'); return; }
      showToast(__t('nutrition.meal_saved'), 'success');
      if (d.quest_awarded) showToast('\u{1F3AF} +' + d.quest_awarded.xp + ' XP!', 'success');
      closeServingModal();
      loadTodayData();
    } catch (e) {
      showToast(__t('nutrition.add_error'), 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = __t('nutrition.add');
    }
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
    showToast(__t('nutrition.meal_saved_named', { meal: mealLabel(mealName) }), 'success');
    if (window.fxTrackOnce) fxTrackOnce('first_meal_logged');
    if (window.fxActivation) fxActivation('meal');
    if (d.quest_awarded) showToast('\u{1F3AF} +' + d.quest_awarded.xp + ' XP!', 'success');
    loadDiary();
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
populateFoods();
loadTodayData();
loadQuickAddSection();
loadActivePlan();
initWaterButton();
