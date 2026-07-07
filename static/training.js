/* ── i18n (PR6) ── görünen metin EN olur; backend'e giden KANONIK değerler
   (OPTIONS val, sakatlık adı, gün adı, goal/level/score key) Türkçe KALIR →
   training-plan / injury_constraints eşleşmesi bozulmaz. window.t bazı yerlerdeki
   yerel `t` ile çakışmasın diye __t aliası. */
var __t = (window.t) || function (k, v) { return k; };
var _EN = (window.LOCALE === 'en');
/* Sakatlık: görünen etiket EN, değer (backend'e giden) TR kalır. */
var INJURY_LABELS_EN = { 'Hiçbiri':'None','Menisküs':'Meniscus','Diz':'Knee','Kifoz':'Kyphosis','Skolyoz':'Scoliosis','Bel fıtığı':'Herniated disc','Bel ağrısı':'Lower-back pain','Omuz':'Shoulder','Bilek':'Wrist','Dirsek':'Elbow','Ayak bileği':'Ankle','Kalça':'Hip','Boyun':'Neck' };
function injuryLabel(v) { return (_EN && INJURY_LABELS_EN[v]) ? INJURY_LABELS_EN[v] : v; }
/* Gün adı: backend plan değeri (gun) TR KALIR — getTodayTurkish() ile eşleşir ve
   plan kanonik anahtarıdır; yalnızca GÖRÜNEN ad EN'e çevrilir. */
var DAY_LABELS_EN = { 'Pazartesi':'Monday','Salı':'Tuesday','Çarşamba':'Wednesday','Perşembe':'Thursday','Cuma':'Friday','Cumartesi':'Saturday','Pazar':'Sunday' };
function dayLabel(v) { return (_EN && DAY_LABELS_EN[v]) ? DAY_LABELS_EN[v] : v; }
let quickAddOpen = false;
function toggleQuickAdd() {
    quickAddOpen = !quickAddOpen;
    document.getElementById('quick-add-btn').classList.toggle('open', quickAddOpen);
    document.getElementById('quick-add-actions').classList.toggle('open', quickAddOpen);
}
// Eski bileşik satır-içi onclick'lerin (toggleQuickAdd();...) data-action karşılığı.
function fxOpenSetupForm() {
    toggleQuickAdd();
    const f = document.getElementById('setup-form');
    if (f) { f.style.display = 'block'; f.scrollIntoView({ behavior: 'smooth' }); }
}
function fxTriggerFinish() {
    toggleQuickAdd();
    const b = document.getElementById('finish-workout-btn');
    if (b) b.click();
}
document.addEventListener('click', e => {
    if (quickAddOpen && !e.target.closest('.quick-add-wrap')) {
        quickAddOpen = false;
        document.getElementById('quick-add-btn').classList.remove('open');
        document.getElementById('quick-add-actions').classList.remove('open');
    }
});
    // ── OPTIONS DATA ──
    const OPTIONS = {
        gun: [
            { val: 3, label: "3 Gün",  sub: "Başlangıç dostu", en: "3 Days", es: "Beginner-friendly" },
            { val: 4, label: "4 Gün",  sub: "Dengeli split",   en: "4 Days", es: "Balanced split" },
            { val: 5, label: "5 Gün",  sub: "Yoğun program",   en: "5 Days", es: "Intense program" },
            { val: 6, label: "6 Gün",  sub: "İleri seviye",    en: "6 Days", es: "Advanced" }
        ],
        ekipman: [
            { val: "spor_salonu", label: "Spor Salonu", sub: "Tam ekipman",   en: "Gym",     es: "Full equipment" },
            { val: "ev",          label: "Ev",          sub: "Vücut ağırlığı", en: "Home",    es: "Bodyweight" },
            { val: "minimal",     label: "Minimal",     sub: "Dambıl + bant",  en: "Minimal", es: "Dumbbells + band" }
        ],
        odak: [
            { val: "tum_vucut", label: "Tüm Vücut",   sub: "Dengeli gelişim",     en: "Full Body",    es: "Balanced development" },
            { val: "ust_vucut", label: "Üst Vücut",   sub: "Göğüs, sırt, omuz",   en: "Upper Body",   es: "Chest, back, shoulders" },
            { val: "sirt",      label: "Sırt Odaklı", sub: "Lat, trapez, rhomboid", en: "Back-Focused", es: "Lats, traps, rhomboids" },
            { val: "alt_vucut", label: "Alt Vücut",   sub: "Bacak, kalça",        en: "Lower Body",   es: "Legs, glutes" },
            { val: "core",      label: "Core",         sub: "Karın, bel",          en: "Core",         es: "Abs, lower back" }
        ],
        sure: [
            { val: 30, label: "30 Dakika", sub: "Kısa ve yoğun", en: "30 Minutes", es: "Short and intense" },
            { val: 45, label: "45 Dakika", sub: "Standart",      en: "45 Minutes", es: "Standard" },
            { val: 60, label: "60 Dakika", sub: "Kapsamlı",      en: "60 Minutes", es: "Comprehensive" },
            { val: 90, label: "90 Dakika", sub: "İleri seviye",  en: "90 Minutes", es: "Advanced" }
        ]
    };
    const TARZI_OPTIONS = [
        { val:"genel",        label:"Genel Fitness",  sub:"Karışık antrenman", en:"General Fitness", es:"Mixed training" },
        { val:"crossfit",     label:"CrossFit",       sub:"WOD, fonksiyonel",  en:"CrossFit",        es:"WOD, functional" },
        { val:"calisthenics", label:"Kalistenik",     sub:"Vücut ağırlığı",    en:"Calisthenics",    es:"Bodyweight" },
        { val:"powerlifting", label:"Powerlifting",   sub:"Güç odaklı",        en:"Powerlifting",    es:"Strength-focused" },
        { val:"bodybuilding", label:"Bodybuilding",   sub:"Kas izolasyonu",    en:"Bodybuilding",    es:"Muscle isolation" },
        { val:"fonksiyonel",  label:"Fonksiyonel",    sub:"Mobilite odaklı",   en:"Functional",      es:"Mobility-focused" }
    ];
    const HEDEF_OPTIONS = [
        { val:"genel",       label:"Genel Sağlık",  sub:"Dengeli gelişim",  en:"General Health", es:"Balanced development" },
        { val:"guc",         label:"Güç",            sub:"Maksimum kuvvet",  en:"Strength",       es:"Maximum force" },
        { val:"kondisyon",   label:"Kondisyon",      sub:"Dayanıklılık",     en:"Conditioning",   es:"Endurance" },
        { val:"kas_kutlesi", label:"Kas Kütlesi",   sub:"Hipertrofi",       en:"Muscle Mass",    es:"Hypertrophy" },
        { val:"yag_yakimi",  label:"Yağ Yakımı",    sub:"Rekomposizyon",    en:"Fat Loss",       es:"Recomposition" },
        { val:"esneklik",    label:"Esneklik",       sub:"Mobilite",         en:"Flexibility",    es:"Mobility" }
    ];
    const KARDIYO_OPTIONS = {
        tip: [
            { val:"yok",       label:"Kardiyo Yok",  sub:"Sadece ağırlık",      en:"No Cardio",  es:"Weights only" },
            { val:"kosu",      label:"Koşu",          sub:"Dış mekan / bant",    en:"Running",    es:"Outdoor / treadmill" },
            { val:"bisiklet",  label:"Bisiklet",      sub:"Sabit veya dış mekan", en:"Cycling",    es:"Stationary or outdoor" },
            { val:"yuzme",     label:"Yüzme",         sub:"Havuz",               en:"Swimming",   es:"Pool" },
            { val:"ip_atlama", label:"İp Atlama",     sub:"Ev / salon",          en:"Jump Rope",  es:"Home / gym" },
            { val:"yuruyus",   label:"Yürüyüş",      sub:"Tempolu yürüyüş",     en:"Walking",    es:"Brisk walking" },
            { val:"karisik",   label:"Karışık",       sub:"Farklı türler",       en:"Mixed",      es:"Various types" }
        ],
        gun:  [
            { val:2, label:"2 Gün", sub:"Hafif",     en:"2 Days", es:"Light" },
            { val:3, label:"3 Gün", sub:"Orta",      en:"3 Days", es:"Moderate" },
            { val:4, label:"4 Gün", sub:"Yoğun",     en:"4 Days", es:"Intense" },
            { val:5, label:"5 Gün", sub:"Çok yoğun", en:"5 Days", es:"Very intense" }
        ],
        sure: [
            { val:15, label:"15 dk", sub:"Kısa HIIT", en:"15 min", es:"Short HIIT" },
            { val:20, label:"20 dk", sub:"Standart",  en:"20 min", es:"Standard" },
            { val:30, label:"30 dk", sub:"Orta",      en:"30 min", es:"Moderate" },
            { val:45, label:"45 dk", sub:"Uzun",      en:"45 min", es:"Long" }
        ],
        yogunluk: [
            { val:"dusuk",   label:"Düşük (LISS)", sub:"Yağ yakımı odaklı", en:"Low (LISS)",  es:"Fat-loss focused" },
            { val:"orta",    label:"Orta",          sub:"Genel kondisyon",   en:"Moderate",    es:"General conditioning" },
            { val:"yuksek",  label:"Yüksek (HIIT)", sub:"Kısa ve yoğun",     en:"High (HIIT)", es:"Short and intense" },
            { val:"karisik", label:"Karışık",       sub:"Gün gün değişen",   en:"Mixed",       es:"Varies day to day" }
        ]
    };

    // ── STATE ──
    const selections = {
        gun_sayisi: 3, ekipman: "spor_salonu", odak: "tum_vucut", sure: 45,
        kardiyo_tipi: "yok", kardiyo_gun: 0, kardiyo_sure: 20,
        kardiyo_yogunluk: "orta", antrenman_tarzi: "genel", odak_hedef: "genel",
        injuries: (window.__TRAINING && window.__TRAINING.injuries) || ""   // kayıtlı sakatlık (varsa) ile ön-doldurulur
    };
    let currentPlan = null, currentScore = null;

    // ── TOAST ──
    function showToast(msg, type = 'info') {
        const icons = { success: '✓', error: '✗', info: 'ℹ' };
        const wrap = document.getElementById('toast-wrap');
        const t = document.createElement('div');
        t.className = `toast toast-${type}`;
        t.innerHTML = `<span class="toast-icon">${icons[type]||'ℹ'}</span><span>${msg}</span>`;
        wrap.appendChild(t);
        setTimeout(() => { t.classList.add('hide'); setTimeout(() => t.remove(), 280); }, 3200);
    }

    // ── OPTION CHIP BUILDER ──
    function createOptionChip(item, gridId, key, defaultVal) {
        const chip = document.createElement("div");
        chip.className = "option-chip" + (item.val === defaultVal ? " selected" : "");
        chip.innerHTML = `
            <div class="option-chip-dot"></div>
            <div>
                <div class="option-chip-text">${_EN ? (item.en || item.label) : item.label}</div>
                <div class="option-chip-sub">${_EN ? (item.es || item.sub) : item.sub}</div>
            </div>
        `;
        chip.addEventListener("click", () => {
            document.querySelectorAll(`#${gridId} .option-chip`).forEach(c => c.classList.remove("selected"));
            chip.classList.add("selected");
            selections[key] = item.val;
        });
        return chip;
    }

    function populateOptions() {
        OPTIONS.gun.forEach(o     => document.getElementById("gun-grid").appendChild(createOptionChip(o, "gun-grid", "gun_sayisi", 3)));
        OPTIONS.ekipman.forEach(o => document.getElementById("ekipman-grid").appendChild(createOptionChip(o, "ekipman-grid", "ekipman", "spor_salonu")));
        OPTIONS.odak.forEach(o    => document.getElementById("odak-grid").appendChild(createOptionChip(o, "odak-grid", "odak", "tum_vucut")));
        OPTIONS.sure.forEach(o    => document.getElementById("sure-grid").appendChild(createOptionChip(o, "sure-grid", "sure", 45)));

        KARDIYO_OPTIONS.tip.forEach(o => {
            const chip = createOptionChip(o, "kardiyo-tip-grid", "kardiyo_tipi", "yok");
            chip.addEventListener("click", () => {
                const show = selections.kardiyo_tipi !== "yok";
                document.getElementById("kardiyo-details").style.display = show ? "block" : "none";
                if (!show) selections.kardiyo_gun = 0;
            });
            document.getElementById("kardiyo-tip-grid").appendChild(chip);
        });
        KARDIYO_OPTIONS.gun.forEach(o      => document.getElementById("kardiyo-gun-grid").appendChild(createOptionChip(o, "kardiyo-gun-grid", "kardiyo_gun", 2)));
        KARDIYO_OPTIONS.sure.forEach(o     => document.getElementById("kardiyo-sure-grid").appendChild(createOptionChip(o, "kardiyo-sure-grid", "kardiyo_sure", 20)));
        KARDIYO_OPTIONS.yogunluk.forEach(o => document.getElementById("kardiyo-yogunluk-grid").appendChild(createOptionChip(o, "kardiyo-yogunluk-grid", "kardiyo_yogunluk", "orta")));
        TARZI_OPTIONS.forEach(o  => document.getElementById("tarzi-grid").appendChild(createOptionChip(o, "tarzi-grid", "antrenman_tarzi", "genel")));
        HEDEF_OPTIONS.forEach(o  => document.getElementById("hedef-grid").appendChild(createOptionChip(o, "hedef-grid", "odak_hedef", "genel")));
    }

    // ── SAKATLIK SEÇİCİ (çoklu-seçim) ──
    // Yaygın durumlar için hızlı chip'ler + serbest metin. TEK doğruluk kaynağı
    // serbest-metin alanıdır (#injury-other); chip'ler yalnızca onu düzenler. Bu metin
    // /training-plan'a gider; backend (app/services/injury_constraints) klinik
    // kontrendikasyona çevirir. "Hiçbiri" literal gönderilir → kayıtlı sakatlığı
    // temizleyebilmek için (boş string backend'de kayıtlı veriyi SİLMEZ).
    const COMMON_INJURIES = ["Menisküs", "Diz", "Kifoz", "Skolyoz", "Bel fıtığı",
        "Bel ağrısı", "Omuz", "Bilek", "Dirsek", "Ayak bileği", "Kalça", "Boyun"];
    const NONE_LABEL = "Hiçbiri";

    function injuryTokens() {
        return (document.getElementById("injury-other").value || "")
            .split(",").map(s => s.trim()).filter(Boolean);
    }
    function syncInjuryChips() {
        const lc  = injuryTokens().map(t => t.toLowerCase());
        const val = (document.getElementById("injury-other").value || "").trim().toLowerCase();
        document.querySelectorAll("#injury-grid .option-chip").forEach(chip => {
            const label = chip.dataset.label;
            const selected = label === NONE_LABEL
                ? (val === "" || val === NONE_LABEL.toLowerCase())
                : lc.includes(label.toLowerCase());
            chip.classList.toggle("selected", selected);
        });
    }
    function setInjuryValue(text) {
        const input = document.getElementById("injury-other");
        input.value = text;
        selections.injuries = text.trim();
        syncInjuryChips();
    }
    function makeInjuryChip(label) {
        const chip = document.createElement("div");
        chip.className = "option-chip";
        chip.dataset.label = label;
        chip.innerHTML = `<div class="option-chip-dot"></div><div><div class="option-chip-text">${injuryLabel(label)}</div></div>`;
        chip.addEventListener("click", () => {
            if (label === NONE_LABEL) { setInjuryValue(NONE_LABEL); return; }
            // 'Hiçbiri' token'ını at, tıklanan etiketi aç/kapat.
            let tokens = injuryTokens().filter(t => t.toLowerCase() !== NONE_LABEL.toLowerCase());
            const idx = tokens.findIndex(t => t.toLowerCase() === label.toLowerCase());
            if (idx >= 0) tokens.splice(idx, 1); else tokens.push(label);
            setInjuryValue(tokens.join(", "));
        });
        return chip;
    }
    function setupInjuryPicker() {
        const grid = document.getElementById("injury-grid");
        grid.appendChild(makeInjuryChip(NONE_LABEL));
        COMMON_INJURIES.forEach(l => grid.appendChild(makeInjuryChip(l)));
        const input = document.getElementById("injury-other");
        input.addEventListener("input", () => {
            selections.injuries = input.value.trim();
            syncInjuryChips();
        });
        input.value = selections.injuries || "";   // sunucudan gelen kayıtlı değer
        syncInjuryChips();
    }

    // ── LOAD INFO ──
    async function loadInfo() {
        try {
            const res  = await fetch("/last-session");
            const data = await res.json();
            if (data.exists) {
                const goalLabels  = { "kilo verme": __t('training.goal_loss'), "kas kazanma": __t('training.goal_gain') };
                const levelLabels = { "beginner": __t('training.level_beginner'), "intermediate": __t('training.level_intermediate'), "advanced": __t('training.level_advanced') };
                document.getElementById("info-goal").textContent  = goalLabels[data.goal]  || data.goal;
                document.getElementById("info-level").textContent = levelLabels[data.fitness_level] || data.fitness_level;
                document.getElementById("info-tdee").textContent  = Math.round(data.target_calories || 0) + " kcal";
            }
        } catch(e) {}
    }

    // ── DAY DETECTION ──
    function getTodayTurkish() {
        const days = ['Pazar','Pazartesi','Salı','Çarşamba','Perşembe','Cuma','Cumartesi'];
        return days[new Date().getDay()];
    }

    // ── ACTIVE PLAN ──
    let activePlan = null;

    async function loadActivePlan() {
        try {
            const res  = await fetch("/training-plan/active");
            const data = await res.json();
            if (!data.exists) return;

            activePlan = data.plan; // array of day objects

            // Score color & label
            const score = parseFloat(data.score) || 0;
            const scoreColor = score >= 8 ? '#3D8BFF' : score >= 6 ? '#FFB020' : '#FF4D4D';
            const scoreLabel = score >= 8 ? __t('training.score_excellent') : score >= 6 ? __t('training.score_good') : __t('training.score_fair');

            document.getElementById('apv-score').textContent  = score;
            document.getElementById('apv-score').style.color  = scoreColor;
            document.getElementById('apv-meta').textContent   =
                __t('training.created_on', { date: data.created_at, label: scoreLabel });

            // Today's Workout Hero + this-week strip + weekly stats
            renderHero(activePlan, false);
            renderWeekStrip(activePlan);
            renderWeekStats(activePlan);

            // Switch views
            document.getElementById('active-plan-view').style.display = 'block';
            document.getElementById('setup-form').style.display        = 'none';
        } catch(e) {}
    }

    // Plan text (exercise names, notes, day focus) originates from the AI model;
    // escape it before interpolating into innerHTML so it can't inject markup.
    function esc(s) {
        const d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML;
    }

    function todayDay() {
        const name = getTodayTurkish();
        return (activePlan || []).find(g => g.gun === name) || null;
    }

    // Progress ring: r=48 (matches the shared .ring-* markup in components.css /
    // static/nutrition.js's updateRing), circumference = 2πr. The session is
    // ephemeral (no partial state persists across reloads) so pct is only ever
    // 0 (not done) or 100 (done) — renderHero() decides which to pass.
    const WH_RING_R = 48;
    const WH_RING_C = 2 * Math.PI * WH_RING_R;
    function updateHeroRing(pct) {
        const el = document.getElementById('wh-ring');
        if (el) el.setAttribute('data-pct', pct);  // ring-fill stroke set by shared ring helper
        const fill = document.getElementById('wh-ring-fill');
        if (fill) {
            fill.style.strokeDasharray  = WH_RING_C;
            fill.style.strokeDashoffset = WH_RING_C * (1 - (pct || 0) / 100);
        }
    }

    function renderHero(program, completed) {
        const hero = document.getElementById('workout-hero');
        const day = todayDay();
        const isRest = !day || day.tip === 'dinlenme';
        hero.classList.toggle('is-rest', isRest);
        hero.classList.toggle('is-done', !!completed);
        const focusEl   = document.getElementById('wh-focus');
        const metaEl    = document.getElementById('wh-meta');
        const cta       = document.getElementById('wh-cta');
        const ringLabel = document.getElementById('wh-ring-label');
        if (isRest) {
            focusEl.textContent = __t('training.rest_day');
            metaEl.textContent  = __t('training.active_recovery');
            cta.innerHTML = '';
            if (ringLabel) ringLabel.innerHTML =
                '<div style="font-size:22px;line-height:1;">😴</div>';
        } else {
            const exs = day.egzersizler || [];
            focusEl.textContent = (day.odak || exs[0] && exs[0].isim || __t('training.workout')).toUpperCase();
            metaEl.innerHTML =
                '<span>' + exs.length + ' ' + __t('training.exercises') + '</span>' +
                '<span>' + (day.sure_dk || 0) + ' ' + __t('training.min') + '</span>' +
                '<span>~' + (day.tahmini_kalori || 0) + ' kcal</span>';
            if (completed) {
                cta.innerHTML = '<span class="wh-done-badge badge badge-success">✓ ' +
                    __t('training.workout_done_label') + '</span>';
            } else {
                cta.innerHTML = '<button class="btn-volt w-full" data-action="startWorkout">' +
                    __t('training.start_workout') + '</button>';
            }
            if (ringLabel) ringLabel.innerHTML =
                '<div style="font-family:var(--font-display);font-size:20px;color:var(--color-primary);line-height:1;">' +
                exs.length + '</div>' +
                '<div style="font-size:9px;color:var(--color-text-3);font-weight:600;margin-top:2px;">' +
                __t('training.exercises') + '</div>';
        }
        // progress ring: 0% until a session runs (session is ephemeral)
        updateHeroRing(completed ? 100 : 0);
    }

    function renderWeekStrip(program) {
        const strip = document.getElementById('week-strip');
        const todayName = getTodayTurkish();
        strip.innerHTML = (program || []).map(gun => {
            const isRest = gun.tip === 'dinlenme', isCardio = gun.tip === 'kardiyo';
            const cls = 'week-chip' + (gun.gun === todayName ? ' is-today' : '') +
                (isRest ? ' is-rest' : '') + (isCardio ? ' is-cardio' : '');
            const focus = isRest ? __t('training.off') :
                esc(gun.odak || (gun.egzersizler && gun.egzersizler[0] && gun.egzersizler[0].isim) || '');
            return '<div class="' + cls + '" data-action="previewDay" data-args=\'["' + esc(gun.gun) + '"]\'>' +
                '<div class="wc-day">' + esc(dayLabel(gun.gun)).slice(0, 3) + '</div>' +
                '<div class="wc-focus">' + focus + '</div></div>';
        }).join('');
    }

    function renderWeekStats(program) {
        const days = (program || []).filter(g => g.tip !== 'dinlenme').length;
        const kcal = (program || []).reduce((a, g) => a + (g.tahmini_kalori || 0), 0);
        const mins = (program || []).reduce((a, g) => a + (g.sure_dk || 0), 0);
        document.getElementById('wstats').innerHTML =
            statCard(days, __t('training.workout_day')) +
            statCard(kcal, __t('training.weekly_cal')) +
            statCard(mins, __t('training.total_min'));
    }

    function statCard(v, label) {
        return '<div class="stat-card"><div class="stat-value">' + v +
            '</div><div class="stat-label">' + label + '</div></div>';
    }

    function previewDay(gunName) {
        const day = (activePlan || []).find(g => g.gun === gunName);
        if (!day || day.tip === 'dinlenme') return;
        openDayPreview(day);   // read-only sheet listing exercises (Task 4 reuses .sheet)
    }

    // ── WORKOUT SESSION — ephemeral, in-memory only. No localStorage, no network.
    //    A future Workout Log backend plugs in at the prProvider seam (Task 5). ──
    var _session = null;        // { startedAt, day, exercises:[{isim,tekrar,dinlenme,not,sets:[{weightKg,reps,done,isPR}]}] }
    var _pendingStats = null;   // stats snapshot handed to the celebration after Pump Check

    function defaultReps(tekrar) {
        var m = String(tekrar || '').match(/\d+/g);
        return m && m.length ? parseInt(m[m.length - 1], 10) : null;   // "8-12" -> 12
    }

    function buildSession(day) {
        return {
            startedAt: Date.now(),
            day: day,
            exercises: (day.egzersizler || []).map(function (ex) {
                var n = Math.max(1, parseInt(ex.set, 10) || 1);
                var sets = [];
                for (var i = 0; i < n; i++) {
                    sets.push({ weightKg: null, reps: defaultReps(ex.tekrar), done: false, isPR: false });
                }
                return { isim: ex.isim, tekrar: ex.tekrar, dinlenme: ex.dinlenme,
                         not: ex.not || '', sets: sets };
            }),
        };
    }

    function computeSessionStats(session) {
        var vol = 0, done = 0, total = 0, prs = 0, exDone = 0;
        (session.exercises || []).forEach(function (ex) {
            var any = false;
            ex.sets.forEach(function (st) {
                total++;
                if (st.done) {
                    done++; any = true;
                    vol += (Number(st.weightKg) || 0) * (Number(st.reps) || 0);
                    if (st.isPR) prs++;
                }
            });
            if (any) exDone++;
        });
        return { totalVolume: Math.round(vol), setsDone: done, totalSets: total,
                 prCount: prs, exercisesDone: exDone,
                 elapsedMin: Math.max(0, Math.round((Date.now() - session.startedAt) / 60000)) };
    }

    function startWorkout() {
        var day = todayDay();
        if (!day || day.tip === 'dinlenme') return;
        openSession(day);
    }

    function openSession(day) {
        _session = buildSession(day);
        document.getElementById('sv-title').textContent =
            (day.odak || __t('training.session'));
        renderSession();
        var v = document.getElementById('session-view');
        v.classList.add('open');
        document.body.style.overflow = 'hidden';
    }

    function closeSession() {
        document.getElementById('session-view').classList.remove('open');
        document.body.style.overflow = '';
        stopRestTimer();            // Task 5 (safe no-op until defined)
        _session = null;            // discard ephemeral state
    }

    function renderSession() {
        if (!_session) return;
        var body = document.getElementById('sv-body');
        body.innerHTML = _session.exercises.map(function (ex, ei) {
            var rows = ex.sets.map(function (st, si) {
                return '<div class="set-row' + (st.done ? ' is-done' : '') +
                    (st.isPR ? ' is-pr' : '') + '" data-ex="' + ei + '" data-set="' + si + '">' +
                  '<div class="set-idx">' + (si + 1) + '</div>' +
                  '<input class="set-input" type="number" inputmode="decimal" min="0" step="0.5" ' +
                    'placeholder="kg" data-field="weight" value="' + (st.weightKg == null ? '' : st.weightKg) + '">' +
                  '<input class="set-input" type="number" inputmode="numeric" min="0" step="1" ' +
                    'placeholder="reps" data-field="reps" value="' + (st.reps == null ? '' : st.reps) + '">' +
                  '<button class="set-check" data-field="done" aria-label="' + __t('training.set_done') + '">✓' +
                    '<span class="pr-badge badge badge-warning">PR</span></button>' +
                '</div>';
            }).join('');
            return '<div class="exercise-card"><div class="ec-head">' +
                '<span class="ec-name">' + esc(ex.isim) + '</span>' +
                '<span class="ec-prescribed">' + ex.sets.length + '×' + esc(ex.tekrar) +
                  ' · ' + esc(ex.dinlenme) + '</span></div>' +
                (ex.not ? '<div class="ec-note">' + esc(ex.not) + '</div>' : '') +
                '<div class="set-list">' +
                  '<div class="set-row set-head"><div class="set-idx"></div>' +
                  '<div class="set-col-label">' + __t('training.weight') + '</div>' +
                  '<div class="set-col-label">' + __t('training.reps') + '</div><div></div></div>' +
                  rows + '</div></div>';
        }).join('');
        updateSessionProgress();
    }

    function updateSessionProgress() {
        var s = computeSessionStats(_session);
        document.getElementById('sv-count').textContent = s.setsDone + '/' + s.totalSets;
        var pct = s.totalSets ? (s.setsDone / s.totalSets) * 100 : 0;
        document.getElementById('sv-progress-bar').style.width = pct + '%';
    }

    function finishSession() {
        if (!_session) return;
        _pendingStats = computeSessionStats(_session);
        document.getElementById('session-view').classList.remove('open');
        stopRestTimer();
        openPumpCheck();            // existing flow; on success → showCelebration (Task 6)
    }

    // Scoped delegated listener — binds once on #sv-body; updates `_session` in place.
    (function initSession() {
        var body = document.getElementById('sv-body');
        if (!body) return;
        body.addEventListener('input', function (e) {
            var row = e.target.closest('.set-row'); if (!row || !_session) return;
            var ex = _session.exercises[+row.dataset.ex]; var st = ex && ex.sets[+row.dataset.set];
            if (!st) return;
            var field = e.target.dataset.field;
            if (field === 'weight') { st.weightKg = e.target.value === '' ? null : parseFloat(e.target.value);
                st.isPR = evaluatePR(ex.isim, st.weightKg); refreshPRFlags(ex, +row.dataset.ex); }  // Task 5
            else if (field === 'reps') { st.reps = e.target.value === '' ? null : parseInt(e.target.value, 10); }
        });
        body.addEventListener('click', function (e) {
            var btn = e.target.closest('[data-field="done"]'); if (!btn || !_session) return;
            var row = btn.closest('.set-row');
            var ex = _session.exercises[+row.dataset.ex]; var st = ex.sets[+row.dataset.set];
            st.done = !st.done;
            row.classList.toggle('is-done', st.done);
            updateSessionProgress();
            if (st.done) startRestTimer(parseRestSeconds(ex.dinlenme));   // Task 5
        });
    })();

    // Temporary stubs — PR detection + rest timer land in Task 5. Kept here so the
    // session player runs green standalone; Task 5 REPLACES these with real bodies.
    function evaluatePR() { return false; }
    function refreshPRFlags() {}
    function parseRestSeconds() { return 60; }
    function startRestTimer() {}
    function stopRestTimer() {}

    // ── DAY PREVIEW (read-only, non-today days) ──
    function openDayPreview(day) {
        document.getElementById('dp-title').textContent = dayLabel(day.gun) + ' — ' + (day.odak || '');
        document.getElementById('dp-body').innerHTML = (day.egzersizler || []).map(function (e) {
            return '<div class="exercise-card"><div class="ec-head"><span class="ec-name">' +
                esc(e.isim) + '</span><span class="ec-prescribed">' + e.set + '×' + esc(e.tekrar) +
                ' · ' + esc(e.dinlenme) + '</span></div>' +
                (e.not ? '<div class="ec-note">' + esc(e.not) + '</div>' : '') + '</div>';
        }).join('');
        document.getElementById('day-preview').classList.add('open');
    }
    function closeDayPreview() { document.getElementById('day-preview').classList.remove('open'); }

    function getTodayKey() {
        const d = new Date();
        return 'fitx_workout_completed_' + d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
    }

    function markWorkoutCompleted() {
        const btn = document.getElementById('finish-workout-btn');
        if (!btn) return;
        btn.disabled = true;
        btn.textContent = __t('training.workout_done');
        btn.classList.add('completed');
    }

    async function checkWorkoutCompleted() {
        // hızlı yol: yerel önbellek (anlık boyama)
        try {
            if (localStorage.getItem(getTodayKey()) === 'true') renderHero(activePlan, true);
        } catch (e) {}
        // doğru yol: sunucudan (cihazlar arası senkron)
        try {
            const res = await fetch('/workout/status');
            if (!res.ok) return;
            const data = await res.json();
            if (data.completed) {
                renderHero(activePlan, true);
                try { localStorage.setItem(getTodayKey(), 'true'); } catch (e) {}
            }
        } catch (e) {}
    }

    // "Antrenmanı Tamamla" artık doğrudan tamamlamaz; önce Pump Check modalını açar.
    function finishWorkout() {
        openPumpCheck();
    }

    // ── PUMP CHECK MODAL ──
    let pumpImageData = null;  // seçilen fotoğrafın base64 data-URL'i
    let pumpVisibility = 'feed';
    let pumpSelectedFriends = new Map();

    function escapeHTML(str) {
        const d = document.createElement('div');
        d.textContent = str == null ? '' : String(str);
        return d.innerHTML;
    }

    function openPumpCheck() {
        const modal = document.getElementById('pump-check-modal');
        if (!modal) return;
        // durumu sıfırla
        pumpImageData = null;
        document.getElementById('pump-dropzone').classList.remove('has-image');
        document.getElementById('pump-file-input').value = '';
        pumpVisibility = 'feed';
        pumpSelectedFriends = new Map();
        setPumpShare('feed');
        renderPumpSelectedFriends();
        document.getElementById('pump-friend-search').value = '';
        document.getElementById('pump-progress').hidden = true;
        document.getElementById('pump-progress-bar').style.width = '0%';
        clearPumpError();
        const submit = document.getElementById('pump-submit');
        submit.disabled = false;
        submit.classList.remove('loading');
        submit.textContent = __t('training.complete_workout');
        modal.classList.add('active');
        document.body.style.overflow = 'hidden';
        setTimeout(() => document.getElementById('pump-dropzone').focus(), 50);
    }

    function closePumpCheck() {
        const modal = document.getElementById('pump-check-modal');
        if (!modal) return;
        modal.classList.remove('active');
        document.body.style.overflow = '';
        const btn = document.getElementById('finish-workout-btn');
        if (btn && !btn.classList.contains('completed')) btn.focus();
    }

    function showPumpError(msg) {
        const el = document.getElementById('pump-error');
        el.textContent = msg;
        el.classList.add('visible');
    }
    function clearPumpError() {
        const el = document.getElementById('pump-error');
        el.textContent = '';
        el.classList.remove('visible');
    }

    function setPumpShare(value) {
        pumpVisibility = value;
        document.querySelectorAll('.pump-share-option').forEach(btn => {
            const active = btn.dataset.share === value;
            btn.classList.toggle('active', active);
            btn.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
        document.getElementById('pump-friend-picker').hidden = value !== 'friends';
        if (value === 'friends') loadPumpFriends();
    }

    function renderPumpSelectedFriends() {
        const wrap = document.getElementById('pump-selected-friends');
        wrap.innerHTML = Array.from(pumpSelectedFriends.values()).map(f =>
            '<span class="pump-chip">@' + escapeHTML(f.username) + ' <button type="button" data-remove-friend="' + f.id + '" aria-label="' + __t('pump.remove_friend') + '">&times;</button></span>'
        ).join('');
    }

    async function loadPumpFriends() {
        const q = document.getElementById('pump-friend-search').value.trim();
        try {
            const res = await fetch('/friends/select-list?q=' + encodeURIComponent(q));
            const data = await res.json();
            document.getElementById('pump-friend-results').innerHTML = data.friends.map(f =>
                '<button type="button" class="pump-friend-row" data-friend-id="' + f.id + '" data-username="' + escapeHTML(f.username).replace(/"/g, '&quot;') + '"><span>@' + escapeHTML(f.username) + '</span></button>'
            ).join('') || '<div class="pump-dropzone-hint" style="padding:12px;">' + __t('pump.no_friends') + '</div>';
        } catch (err) {
            document.getElementById('pump-friend-results').innerHTML = '<div class="pump-dropzone-hint" style="padding:12px;">' + __t('pump.no_friends') + '</div>';
        }
    }

    function setPumpProgress(percent) {
        document.getElementById('pump-progress').hidden = false;
        document.getElementById('pump-progress-bar').style.width = percent + '%';
    }

    function handlePumpFile(file) {
        if (!file) return;
        if (!file.type || !file.type.startsWith('image/')) { showPumpError(__t('training.pump_pick_image')); return; }
        if (file.size > 5 * 1024 * 1024) { showPumpError(__t('training.photo_too_big')); return; }
        const reader = new FileReader();
        reader.onload = function(ev) {
            pumpImageData = ev.target.result;
            document.getElementById('pump-preview-img').src = pumpImageData;
            document.getElementById('pump-dropzone').classList.add('has-image');
            clearPumpError();
        };
        reader.readAsDataURL(file);
    }

    async function submitPumpCheck() {
        if (!pumpImageData) { showPumpError(__t('training.pump_upload_first')); return; }
        if (pumpVisibility === 'friends' && pumpSelectedFriends.size === 0) {
            showPumpError(__t('pump.friend_required'));
            return;
        }
        clearPumpError();
        const submit = document.getElementById('pump-submit');
        submit.disabled = true;
        submit.classList.add('loading');
        submit.innerHTML = '<span class="pump-busy"><span class="pump-spinner"></span>' + __t('training.verifying') + '</span>';
        setPumpProgress(35);
        const payload = {
            image: pumpImageData,
            location_type: document.getElementById('pump-location').value,
            description: document.getElementById('pump-desc').value.trim(),
            visibility: pumpVisibility,
            shared_friend_ids: Array.from(pumpSelectedFriends.keys())
        };
        try {
            const res = await fetch('/workout/complete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            setPumpProgress(75);
            const data = await res.json().catch(() => ({}));
            // Sunucu "zaten tamamlandı" yapısal kodunu (ör. başka cihazda yapıldı)
            // dönerse bunu hata değil, tamamlanmış kabul et. Dile bağımlı metin
            // yerine code alanına bak (i18n: error metni artık çevriliyor).
            const already = !res.ok && data.code === 'already_completed';
            if (res.ok || already) {
                setPumpProgress(100);
                if (res.ok) showToast(data.message, 'success');
                markWorkoutCompleted();
                // localStorage bazı mobil tarayıcılarda hata fırlatabilir; ayrı koru.
                try { localStorage.setItem(getTodayKey(), 'true'); } catch (e) {}
                closePumpCheck();
            } else {
                // 422 = doğrulama eşleşmedi, 400 = eksik/biçim hatası → modalda göster, yeniden dene.
                showPumpError(data.error || __t('training.verify_failed'));
                document.getElementById('pump-progress').hidden = true;
                document.getElementById('pump-progress-bar').style.width = '0%';
                submit.disabled = false;
                submit.classList.remove('loading');
                submit.textContent = __t('training.complete_workout');
            }
        } catch (err) {
            showPumpError(__t('training.conn_error_retry'));
            document.getElementById('pump-progress').hidden = true;
            document.getElementById('pump-progress-bar').style.width = '0%';
            submit.disabled = false;
            submit.classList.remove('loading');
            submit.textContent = __t('training.complete_workout');
        }
    }

    function resetPlan() {
        document.getElementById('active-plan-view').style.display = 'none';
        document.getElementById('setup-form').style.display        = 'block';
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    // ── GENERATE PLAN ──
    async function generatePlan() {
        const btn     = document.getElementById("submit-btn");
        const loading = document.getElementById("loading");
        btn.classList.add("loading");
        btn.textContent = __t('training.preparing');
        loading.classList.add("active");

        try {
            const res  = await fetch("/training-plan", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(selections)
            });
            const data = await res.json();
            if (data.error) { showToast(data.error, 'error'); return; }
            currentPlan  = data.program;
            currentScore = data.overall_score;
            renderResults(data);
        } catch (err) {
            showToast(__t('training.error_prefix') + err.message, 'error');
        } finally {
            btn.classList.remove("loading");
            btn.textContent = __t('training.create_program');
            loading.classList.remove("active");
        }
    }

    function renderResults(data) {
        document.getElementById("results").style.display = "block";
        const saveBtn = document.getElementById("save-btn");
        saveBtn.classList.remove("saved");
        saveBtn.textContent = __t('training.save_program');

        // Score
        const scoreColors = { "İyi": "#3D8BFF", "Orta": "#FFB020", "Kötü": "#FF4D4D" };
        const color = scoreColors[data.score_label] || "#9A9A9A";
        const ozet  = data.haftalik_ozet || {};

        document.getElementById("score-banner").innerHTML = `
            <div class="score-val" style="color:${color}">${data.overall_score}</div>
            <div class="score-info">
                <div class="score-label-big" style="color:${color}">${(_EN ? ({'İyi':'Good','Orta':'Fair','Kötü':'Poor'}[data.score_label] || data.score_label) : data.score_label)} ${__t('training.program_word')}</div>
                <div class="score-desc">${__t('training.score_desc')}</div>
                <div class="score-bars">
                    <div class="score-bar-row">
                        <span class="score-bar-label">${__t('training.bar_intensity')}</span>
                        <div class="score-bar-track"><div class="score-bar-fill" style="width:${(ozet.yogunluk_skoru||7)*10}%;background:${color}"></div></div>
                        <span class="score-bar-val">${ozet.yogunluk_skoru||7}/10</span>
                    </div>
                    <div class="score-bar-row">
                        <span class="score-bar-label">${__t('training.bar_balance')}</span>
                        <div class="score-bar-track"><div class="score-bar-fill" style="width:${(ozet.denge_skoru||7)*10}%;background:${color}"></div></div>
                        <span class="score-bar-val">${ozet.denge_skoru||7}/10</span>
                    </div>
                    <div class="score-bar-row">
                        <span class="score-bar-label">${__t('training.bar_fit')}</span>
                        <div class="score-bar-track"><div class="score-bar-fill" style="width:${(ozet.uygunluk_skoru||7)*10}%;background:${color}"></div></div>
                        <span class="score-bar-val">${ozet.uygunluk_skoru||7}/10</span>
                    </div>
                </div>
            </div>
        `;

        // Weekly grid
        const grid = document.getElementById("weekly-grid");
        grid.innerHTML = "";
        const todaySetup = getTodayTurkish();
        data.program.forEach((gun) => {
            const card    = document.createElement("div");
            const isRest  = gun.tip === "dinlenme";
            const isCardio = gun.tip === "kardiyo";
            const isToday  = gun.gun === todaySetup;
            card.className = "day-card" + (isRest ? " rest" : "") + (isCardio ? " kardiyo" : "") + (isToday ? " today" : " not-today");
            const exercises = gun.egzersizler || [];
            const preview   = exercises.slice(0, 3).map(e => `<div class="day-ex">${esc(e.isim)}</div>`).join("");
            const more      = exercises.length > 3 ? `<div class="day-more">+${exercises.length - 3} ${__t('training.more')}</div>` : "";
            const odakText = gun.odak || (exercises[0] && exercises[0].isim) || '';
            card.innerHTML = `
                <div class="day-card-header">
                    ${isToday ? '<div class="today-badge">' + __t('training.today_badge') + '</div>' : ''}
                    <div class="day-name">${esc(dayLabel(gun.gun))}</div>
                    <div class="day-odak">${isRest ? 'Off Day' : esc(odakText)}</div>
                </div>
                <div class="day-card-body">
                    ${!isRest ? `
                        <div class="day-stat"><span>${__t('training.duration')}</span><span>${gun.sure_dk} dk</span></div>
                        <div class="day-stat"><span>${__t('training.calories')}</span><span>~${gun.tahmini_kalori} kcal</span></div>
                        <div class="day-exercises">${preview}${more}</div>
                    ` : `<div style="font-size:13px;color:var(--text-3);margin-top:8px">${__t('training.rest_day')}</div>`}
                </div>
            `;
            if (!isRest) card.addEventListener("click", () => showDetail(gun));
            grid.appendChild(card);
        });

        // Summary
        const toplam_kalori = data.program.reduce((a, g) => a + (g.tahmini_kalori || 0), 0);
        document.getElementById("weekly-summary").innerHTML = `
            <div class="summary-card">
                <div class="summary-val">${ozet.toplam_antrenman_gun || selections.gun_sayisi}</div>
                <div class="summary-label">${__t('training.workout_day')}</div>
            </div>
            <div class="summary-card">
                <div class="summary-val">${toplam_kalori}</div>
                <div class="summary-label">${__t('training.weekly_cal')}</div>
            </div>
            <div class="summary-card">
                <div class="summary-val">${data.program.filter(g => g.tip !== "dinlenme").length * selections.sure}</div>
                <div class="summary-label">${__t('training.total_min')}</div>
            </div>
        `;

        setTimeout(() => document.getElementById("results").scrollIntoView({ behavior:"smooth", block:"start" }), 200);
    }

    function showDetail(gun) {
        document.getElementById("detail-title").textContent = dayLabel(gun.gun) + " — " + gun.odak;
        document.getElementById("detail-sub").textContent   = `${gun.sure_dk} ${__t('training.minutes')} · ~${gun.tahmini_kalori} kcal`;
        document.getElementById("detail-exercises").innerHTML = (gun.egzersizler || []).map(e => `
            <tr>
                <td>${esc(e.isim)}${e.not ? `<div class="ex-not">${esc(e.not)}</div>` : ""}</td>
                <td>${e.set}</td>
                <td>${e.tekrar}</td>
                <td>${e.dinlenme}</td>
            </tr>
        `).join("");
        const panel = document.getElementById("detail-panel");
        panel.classList.add("visible");
        panel.scrollIntoView({ behavior:"smooth", block:"nearest" });
    }

    function closeDetail() {
        document.getElementById("detail-panel").classList.remove("visible");
    }

    async function savePlan() {
        const btn = document.getElementById("save-btn");
        if (!currentPlan) return;
        try {
            const res = await fetch("/training-plan/save", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ plan: currentPlan, score: currentScore })
            });
            await res.json();
            btn.textContent = __t('training.saved');
            btn.classList.add("saved");
            showToast(__t('training.program_saved'), 'success');
            // Reload and switch to active plan view
            await loadActivePlan();
        } catch (err) {
            showToast(__t('training.save_error_prefix') + err.message, 'error');
        }
    }

    // ── Pump Check modal olay bağlantıları ──
    (function initPumpCheck() {
        const modal = document.getElementById('pump-check-modal');
        if (!modal) return;
        const dz = document.getElementById('pump-dropzone');
        const fileInput = document.getElementById('pump-file-input');
        dz.addEventListener('click', () => fileInput.click());
        dz.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); }
        });
        fileInput.addEventListener('change', (e) => handlePumpFile(e.target.files[0]));
        document.getElementById('pump-close').addEventListener('click', closePumpCheck);
        document.getElementById('pump-cancel').addEventListener('click', closePumpCheck);
        document.querySelectorAll('.pump-share-option').forEach(btn => {
            btn.addEventListener('click', () => setPumpShare(btn.dataset.share));
        });
        document.getElementById('pump-friend-search').addEventListener('input', () => {
            clearTimeout(window.__pumpFriendTimer);
            window.__pumpFriendTimer = setTimeout(loadPumpFriends, 200);
        });
        document.getElementById('pump-friend-results').addEventListener('click', (e) => {
            const row = e.target.closest('[data-friend-id]');
            if (!row) return;
            pumpSelectedFriends.set(Number(row.dataset.friendId), { id: Number(row.dataset.friendId), username: row.dataset.username });
            renderPumpSelectedFriends();
        });
        document.getElementById('pump-selected-friends').addEventListener('click', (e) => {
            const btn = e.target.closest('[data-remove-friend]');
            if (!btn) return;
            pumpSelectedFriends.delete(Number(btn.dataset.removeFriend));
            renderPumpSelectedFriends();
        });
        modal.addEventListener('click', (e) => { if (e.target === modal) closePumpCheck(); });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && modal.classList.contains('active')) closePumpCheck();
        });
    })();

    populateOptions();
    setupInjuryPicker();
    loadInfo();
    loadActivePlan().then(() => checkWorkoutCompleted());
