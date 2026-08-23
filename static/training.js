/* ── i18n (PR6) ── görünen metin EN olur; backend'e giden KANONIK değerler
   (OPTIONS val, sakatlık adı, gün adı, goal/level/score key) Türkçe KALIR →
   training-plan / injury_constraints eşleşmesi bozulmaz. window.t bazı yerlerdeki
   yerel `t` ile çakışmasın diye __t aliası. */
var __t = (window.t) || function (k, v) { return k; };
var _EN = (window.LOCALE === 'en');
/* Sakatlık: görünen etiket EN, değer (backend'e giden) TR kalır. */
var INJURY_LABELS_EN = { 'Hiçbiri':'None','Menisküs':'Meniscus','Diz':'Knee','Kifoz':'Kyphosis','Skolyoz':'Scoliosis','Bel fıtığı':'Herniated disc','Bel ağrısı':'Lower-back pain','Omuz':'Shoulder','Bilek':'Wrist','Dirsek':'Elbow','Ayak bileği':'Ankle','Kalça':'Hip','Boyun':'Neck' };
function injuryLabel(v) { return (_EN && INJURY_LABELS_EN[v]) ? INJURY_LABELS_EN[v] : v; }
/* Gün adı backend'den gelir; yalnızca görünen etiket EN'e çevrilir. */
var DAY_LABELS_EN = { 'Pazartesi':'Monday','Salı':'Tuesday','Çarşamba':'Wednesday','Perşembe':'Thursday','Cuma':'Friday','Cumartesi':'Saturday','Pazar':'Sunday' };
function dayLabel(v) { return (_EN && DAY_LABELS_EN[v]) ? DAY_LABELS_EN[v] : v; }
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
    // currentContextToken: the server-signed equipment context from generate,
    // held in memory beside the candidate ONLY to hand back on save. Never
    // parsed, displayed, edited, stored, or put in a URL — it is opaque here.
    let currentPlan = null, currentScore = null, currentContextToken = null;

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
        chip.className = "tw-chip" + (item.val === defaultVal ? " selected" : "");
        chip.innerHTML = `
            <div class="tw-chip-dot"></div>
            <div>
                <div class="tw-chip-text">${_EN ? (item.en || item.label) : item.label}</div>
                <div class="tw-chip-sub">${_EN ? (item.es || item.sub) : item.sub}</div>
            </div>
        `;
        chip.addEventListener("click", () => {
            document.querySelectorAll(`#${gridId} .tw-chip`).forEach(c => c.classList.remove("selected"));
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
        document.querySelectorAll("#injury-grid .tw-chip").forEach(chip => {
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
        chip.className = "tw-chip";
        chip.dataset.label = label;
        chip.innerHTML = `<div class="tw-chip-dot"></div><div><div class="tw-chip-text">${injuryLabel(label)}</div></div>`;
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

    // ── ACTIVE PLAN ──
    let activePlan = null;
    let activeTodayPlan = null;
    let currentWorkoutState = null;
    let workoutStateClient = null;

    function applyTrainingSnapshot(snapshot) {
            const data = snapshot.plan || { exists: false };
            currentWorkoutState = snapshot.workout && snapshot.workout.state;
            activeTodayPlan = snapshot.today_plan || null;
            if (!data.exists) {
                activePlan = null;
                document.getElementById('active-plan-view').style.display = 'none';
                document.getElementById('setup-form').style.display = 'block';
                return;
            }

            activePlan = Array.isArray(data.plan) ? data.plan : data.plan.program;

            // Score color & label
            const score = parseFloat(data.score) || 0;
            const scoreColor = score >= 8 ? '#3D8BFF' : score >= 6 ? '#FFB020' : '#FF4D4D';
            const scoreLabel = score >= 8 ? __t('training.score_excellent') : score >= 6 ? __t('training.score_good') : __t('training.score_fair');

            document.getElementById('apv-score').textContent  = score;
            document.getElementById('apv-score').style.color  = scoreColor;
            document.getElementById('apv-meta').textContent   =
                __t('training.created_on', { date: data.created_at, label: scoreLabel });

            // Today's Workout Hero + this-week strip + weekly stats
            renderHero(activePlan, !!(snapshot.workout && snapshot.workout.completed));
            renderWeekStrip(activePlan);
            renderWeekStats(activePlan);

            // Switch views
            document.getElementById('active-plan-view').style.display = 'block';
            document.getElementById('setup-form').style.display        = 'none';
    }

    function renderTrainingBlocked() {
        activePlan = null;
        currentWorkoutState = null;
        activeTodayPlan = null;
        const activePlanView = document.getElementById('active-plan-view');
        if (activePlanView) activePlanView.style.display = 'block';
        const setupForm = document.getElementById('setup-form');
        if (setupForm) setupForm.style.display = 'none';
        const cta = document.getElementById('wh-cta');
        if (cta) cta.innerHTML = '<span class="badge badge-warning">' +
            (_EN ? 'Workout state unavailable' : 'Antrenman durumu alınamadı') + '</span>';
    }

    // Plan text (exercise names, notes, day focus) originates from the AI model;
    // escape it before interpolating into innerHTML so it can't inject markup.
    function esc(s) {
        const d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML;
    }

    function todayDay() {
        return activeTodayPlan;
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
        const isRest = currentWorkoutState &&
            currentWorkoutState.schedule_state === 'rest_day';
        const blocked = !currentWorkoutState || currentWorkoutState.action === 'blocked';
        hero.classList.toggle('is-rest', isRest);
        hero.classList.toggle('is-done', !!completed);
        const focusEl   = document.getElementById('wh-focus');
        const metaEl    = document.getElementById('wh-meta');
        const cta       = document.getElementById('wh-cta');
        const ringLabel = document.getElementById('wh-ring-label');
        if (blocked) {
            focusEl.textContent = _EN ? 'WORKOUT UNAVAILABLE' : 'ANTRENMAN KULLANILAMIYOR';
            metaEl.textContent = _EN ? 'Refresh to try again.' : 'Yenile ve tekrar dene.';
            cta.innerHTML = currentWorkoutState && currentWorkoutState.session &&
                currentWorkoutState.session.status === 'active'
                ? '<button class="btn-ghost w-full" data-action="abandonWorkout">' +
                  (_EN ? 'Abandon workout' : 'Antrenmanı bırak') + '</button>' : '';
        } else if (isRest) {
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
            } else if (currentWorkoutState.action === 'start' ||
                       currentWorkoutState.action === 'resume') {
                cta.innerHTML = '<button class="btn-volt w-full" data-action="startWorkout">' +
                    (currentWorkoutState.action === 'resume'
                        ? (_EN ? 'Continue workout' : 'Antrenmana devam et')
                        : __t('training.start_workout')) + '</button>';
            } else {
                cta.innerHTML = '';
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
        const todayName = activeTodayPlan && activeTodayPlan.gun;
        strip.innerHTML = (program || []).map(gun => {
            const isRest = gun.tip === 'dinlenme', isCardio = gun.tip === 'kardiyo';
            const cls = 'week-chip' + (gun.gun === todayName ? ' is-today' : '') +
                (isRest ? ' is-rest' : '') + (isCardio ? ' is-cardio' : '');
            const focusRaw = isRest ? __t('training.off') :
                (gun.odak || (gun.egzersizler && gun.egzersizler[0] && gun.egzersizler[0].isim) || '');
            const focus = esc(focusRaw);
            const focusTitle = focus.replace(/"/g, '&quot;');
            return '<div class="' + cls + '" data-action="previewDay" data-args=\'["' + esc(gun.gun) + '"]\' title="' + focusTitle + '">' +
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

    function previewDay(gunName, el) {
        const day = (activePlan || []).find(g => g.gun === gunName);
        if (!day || day.tip === 'dinlenme') return;
        openDayPreview(day, el);   // read-only sheet listing exercises (Task 4 reuses .sheet)
    }

    // ── WORKOUT SESSION — set/rep interaction stays ephemeral in page memory;
    //    lifecycle transitions are owned by the canonical server controller. ──
    var _session = null;        // { startedAt, day, exercises:[{isim,tekrar,dinlenme,not,sets:[{weightKg,reps,done,isPR}]}] }
    var _pendingStats = null;   // stats snapshot handed to the celebration after Pump Check
    var _sessionTrigger = null;     // element that opened #session-view (focus returns here on close)
    var _dayPreviewTrigger = null;  // element that opened #day-preview

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

    async function startWorkout(el) {
        var day = todayDay();
        if (!day || day.tip === 'dinlenme') return;
        if (currentWorkoutState && currentWorkoutState.contract_version === 2) {
            var action = currentWorkoutState.action;
            var session = currentWorkoutState.session;
            var url = action === 'resume' && session
                ? '/workout/session/' + encodeURIComponent(session.public_id) + '/resume'
                : '/workout/session/start';
            if (action !== 'start' && action !== 'resume') return;
            var result = await workoutStateClient.mutate(url, { method: 'POST' });
            if (!result || !result.ok) return;
        }
        openSession(day, el);
    }

    async function abandonWorkout() {
        var session = currentWorkoutState && currentWorkoutState.session;
        if (!workoutStateClient || !session || session.status !== 'active') return;
        closeSession();
        await workoutStateClient.mutate(
            '/workout/session/' + encodeURIComponent(session.public_id) + '/abandon',
            { method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ reason: 'user_abandoned' }) }
        );
    }

    function openSession(day, trigger) {
        _session = buildSession(day);
        var persistedSession = currentWorkoutState && currentWorkoutState.session;
        document.getElementById('sv-abandon').hidden = !(
            currentWorkoutState && currentWorkoutState.contract_version === 2 &&
            persistedSession && persistedSession.status === 'active');
        document.getElementById('sv-title').textContent =
            (day.odak || __t('training.session'));
        renderSession();
        var v = document.getElementById('session-view');
        v.classList.add('open');
        document.body.style.overflow = 'hidden';
        _sessionTrigger = trigger || document.activeElement;
        setTimeout(function () { _focusFirstIn(v); }, 50);
    }

    function closeSession() {
        document.getElementById('session-view').classList.remove('open');
        document.body.style.overflow = '';
        stopRestTimer();            // Task 5 (safe no-op until defined)
        _session = null;            // discard ephemeral state
        _restoreFocus(_sessionTrigger);
        _sessionTrigger = null;
    }

    // Session with zero exercises can't happen in practice (the plan validator
    // guarantees >=1 exercise per non-rest day) — defensive .empty-state guard.
    function renderSession() {
        if (!_session) return;
        var body = document.getElementById('sv-body');
        if (!_session.exercises.length) {
            body.innerHTML = '<div class="empty-state"><div class="empty-icon">' +
                '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M9 12h6"/></svg></div>' +
                '<div class="empty-title">' +
                (_EN ? 'No exercises in this session.' : 'Bu seansta egzersiz yok.') +
                '</div></div>';
            updateSessionProgress();
            return;
        }
        body.innerHTML = _session.exercises.map(function (ex, ei) {
            var rows = ex.sets.map(function (st, si) {
                return '<div class="set-row' + (st.done ? ' is-done' : '') +
                    (st.isPR ? ' is-pr' : '') + '" data-ex="' + ei + '" data-set="' + si + '">' +
                  '<div class="set-idx">' + (si + 1) + '</div>' +
                  '<input class="set-input" type="number" inputmode="decimal" min="0" step="0.5" ' +
                    'aria-label="' + __t('training.weight') + ' — ' + __t('training.set_done') + ' ' + (si + 1) + '" ' +
                    'placeholder="kg" data-field="weight" value="' + (st.weightKg == null ? '' : st.weightKg) + '">' +
                  '<input class="set-input" type="number" inputmode="numeric" min="0" step="1" ' +
                    'aria-label="' + __t('training.reps') + ' — ' + __t('training.set_done') + ' ' + (si + 1) + '" ' +
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
            refreshPRFlags(ex, +row.dataset.ex);   // recompute in-session top-set ★ (only done sets count)
            if (st.done) startRestTimer(parseRestSeconds(ex.dinlenme));   // Task 5
        });
    })();

    // ── REST TIMER ──
    var _rest = { id: null, remaining: 0 };

    function parseRestSeconds(dinlenme) {
        var s = String(dinlenme || '').toLowerCase();
        var nums = s.match(/\d+/g);
        var n = nums && nums.length ? parseInt(nums[nums.length - 1], 10) : 60;   // "60-90 sn" -> 90
        if (s.indexOf('dk') >= 0 || s.indexOf('min') >= 0) n *= 60;
        return Math.max(5, Math.min(n, 600));
    }

    function _fmtRest(sec) {
        var m = Math.floor(sec / 60), s = sec % 60;
        return m + ':' + (s < 10 ? '0' : '') + s;
    }

    function startRestTimer(sec) {
        stopRestTimer();
        _rest.remaining = sec;
        var el = document.getElementById('rest-timer');
        document.getElementById('rt-time').textContent = _fmtRest(sec);
        el.classList.add('open');
        _rest.id = setInterval(function () {
            _rest.remaining -= 1;
            if (_rest.remaining <= 0) { stopRestTimer();
                if (navigator.vibrate) { try { navigator.vibrate(120); } catch (e) {} }
                return; }
            document.getElementById('rt-time').textContent = _fmtRest(_rest.remaining);
        }, 1000);
    }
    function stopRestTimer() {
        if (_rest.id) { clearInterval(_rest.id); _rest.id = null; }
        var el = document.getElementById('rest-timer');
        if (el) el.classList.remove('open');
    }
    function addRest(delta) {
        if (!_rest.id) return;
        _rest.remaining = Math.max(1, Math.min(_rest.remaining + delta, 600));
        document.getElementById('rt-time').textContent = _fmtRest(_rest.remaining);
    }
    function skipRest() { stopRestTimer(); }

    // ── PR provider seam. No persistence this phase → returns null (no false PRs).
    //    WORKOUT-PERSIST-HOOK: swap NullPrProvider for a backend-backed provider
    //    (e.g. GET /workout/history/best?exercise=) when Workout History ships. ──
    var prProvider = { getBest: function (exerciseName) { return null; } };  // {weightKg}|null

    function evaluatePR(exerciseName, weightKg) {
        var w = Number(weightKg) || 0;
        if (w <= 0) return false;
        var best = prProvider.getBest(exerciseName);
        return best && typeof best.weightKg === 'number' ? w > best.weightKg : false;
    }

    // In-session "top set": heaviest done set of this exercise gets a ★.
    function sessionTopSetIndex(ex) {
        var best = -1, idx = -1;
        ex.sets.forEach(function (st, i) {
            var w = st.done ? (Number(st.weightKg) || 0) : -1;
            if (w > best) { best = w; idx = (best > 0 ? i : -1); }
        });
        return idx;
    }

    function refreshPRFlags(ex, exIdx) {
        var top = sessionTopSetIndex(ex);
        var rows = document.querySelectorAll('#sv-body .set-row[data-ex="' + exIdx + '"][data-set]');
        ex.sets.forEach(function (st, i) {
            var row = rows[i]; if (!row) return;
            row.classList.toggle('is-pr', !!st.isPR);
            row.classList.toggle('top-set', i === top);
        });
    }

    // ── EXERCISE CARD (read-only) — shared by the day-preview sheet (below) and
    //    the generated-plan preview (renderResults, further down). ──
    function exerciseCardHTML(e) {
        return '<div class="exercise-card"><div class="ec-head"><span class="ec-name">' +
            esc(e.isim) + '</span><span class="ec-prescribed">' + esc(e.set) + '×' + esc(e.tekrar) +
            ' · ' + esc(e.dinlenme) + '</span></div>' +
            (e.not ? '<div class="ec-note">' + esc(e.not) + '</div>' : '') + '</div>';
    }

    // ── DAY PREVIEW (read-only, non-today days) ──
    function openDayPreview(day, trigger) {
        document.getElementById('dp-title').textContent = dayLabel(day.gun) + ' — ' + (day.odak || '');
        document.getElementById('dp-body').innerHTML = (day.egzersizler || []).map(exerciseCardHTML).join('');
        document.getElementById('day-preview').classList.add('open');
        _dayPreviewTrigger = trigger || document.activeElement;
        setTimeout(function () { _focusFirstIn(document.querySelector('#day-preview .sheet')); }, 50);
    }
    function closeDayPreview() {
        document.getElementById('day-preview').classList.remove('open');
        _restoreFocus(_dayPreviewTrigger);
        _dayPreviewTrigger = null;
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
        cropImg = null;
        const cropEl = document.getElementById('pump-crop');
        if (cropEl) cropEl.hidden = true;
        pumpVisibility = 'feed';
        pumpSelectedFriends = new Map();
        setPumpShare('feed');
        renderPumpSelectedFriends();
        syncPumpLocationOther();  // "Diğer:" seçili değilse özel konum inputunu gizle
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

    // "Diğer:" seçilince serbest-metin konum inputunu göster; aksi halde gizle+temizle.
    function syncPumpLocationOther() {
        const sel = document.getElementById('pump-location');
        const other = document.getElementById('pump-location-other');
        if (!sel || !other) return;
        const isOther = sel.value === 'Diğer:';
        other.hidden = !isOther;
        if (!isOther) other.value = '';
    }

    function handlePumpFile(file) {
        if (!file) return;
        if (!file.type || !file.type.startsWith('image/')) { showPumpError(__t('training.pump_pick_image')); return; }
        if (file.size > 5 * 1024 * 1024) { showPumpError(__t('training.photo_too_big')); return; }
        const reader = new FileReader();
        reader.onload = function(ev) {
            clearPumpError();
            openPumpCrop(ev.target.result);  // önce çerçeveyi ayarlat (Frame Adjustment)
        };
        reader.readAsDataURL(file);
    }

    // ── FRAME ADJUSTMENT (kırpma/kaydırma/yakınlaştırma) ──
    // 4:5 dikey çerçeve; kaydır + zoom slider. Harici kütüphane YOK (CSP), saf canvas.
    let cropImg = null;      // yüklenen Image
    let cropScale = 1;       // zoom (1 = kapsayan/cover ölçek)
    let cropCoverS = 1;      // görseli çerçeveyi kaplayacak minimum ölçek
    let cropOx = 0, cropOy = 0;   // görselin çerçeve içindeki sol-üst ofseti (CSS px)
    let cropCw = 300, cropCh = 375, cropDpr = 1;
    let cropDragging = false, cropLastX = 0, cropLastY = 0;

    function openPumpCrop(dataUrl) {
        const img = new Image();
        img.onload = function() {
            if (!img.naturalWidth || !img.naturalHeight) { showPumpError(__t('training.pump_pick_image')); return; }
            cropImg = img;
            cropCw = Math.min((window.innerWidth || 360) - 96, 300);
            cropCh = Math.round(cropCw * 1.25);  // 4:5 dikey
            cropDpr = window.devicePixelRatio || 1;
            const canvas = document.getElementById('pump-crop-canvas');
            canvas.style.width = cropCw + 'px';
            canvas.style.height = cropCh + 'px';
            canvas.width = Math.round(cropCw * cropDpr);
            canvas.height = Math.round(cropCh * cropDpr);
            cropCoverS = Math.max(cropCw / img.naturalWidth, cropCh / img.naturalHeight);
            cropScale = 1;
            document.getElementById('pump-crop-zoom').value = '1';
            const eff = cropCoverS * cropScale;
            cropOx = (cropCw - img.naturalWidth * eff) / 2;
            cropOy = (cropCh - img.naturalHeight * eff) / 2;
            drawPumpCrop();
            document.getElementById('pump-crop').hidden = false;
        };
        img.onerror = function() { showPumpError(__t('training.pump_pick_image')); };
        img.src = dataUrl;
    }

    function clampPumpCrop() {
        const eff = cropCoverS * cropScale;
        const dw = cropImg.naturalWidth * eff;
        const dh = cropImg.naturalHeight * eff;
        cropOx = Math.min(0, Math.max(cropCw - dw, cropOx));
        cropOy = Math.min(0, Math.max(cropCh - dh, cropOy));
    }

    function drawPumpCrop() {
        if (!cropImg) return;
        const canvas = document.getElementById('pump-crop-canvas');
        const ctx = canvas.getContext('2d');
        ctx.setTransform(cropDpr, 0, 0, cropDpr, 0, 0);
        ctx.clearRect(0, 0, cropCw, cropCh);
        const eff = cropCoverS * cropScale;
        ctx.drawImage(cropImg, cropOx, cropOy, cropImg.naturalWidth * eff, cropImg.naturalHeight * eff);
    }

    function cropPointerDown(e) {
        if (!cropImg) return;
        cropDragging = true;
        cropLastX = e.clientX; cropLastY = e.clientY;
        try { e.currentTarget.setPointerCapture(e.pointerId); } catch (err) {}
    }
    function cropPointerMove(e) {
        if (!cropDragging) return;
        cropOx += (e.clientX - cropLastX);
        cropOy += (e.clientY - cropLastY);
        cropLastX = e.clientX; cropLastY = e.clientY;
        clampPumpCrop();
        drawPumpCrop();
    }
    function cropPointerUp() { cropDragging = false; }

    function cropZoomChange(e) {
        if (!cropImg) return;
        const z1 = parseFloat(e.target.value) || 1;
        const eff0 = cropCoverS * cropScale;
        const eff1 = cropCoverS * z1;
        // çerçeve MERKEZİNİ sabit tut (yakınlaştırma merkezden olsun)
        const imgX = (cropCw / 2 - cropOx) / eff0;
        const imgY = (cropCh / 2 - cropOy) / eff0;
        cropScale = z1;
        cropOx = cropCw / 2 - imgX * eff1;
        cropOy = cropCh / 2 - imgY * eff1;
        clampPumpCrop();
        drawPumpCrop();
    }

    function confirmPumpCrop() {
        if (!cropImg) return;
        const OUTW = 1080, OUTH = 1350;  // 4:5, uzun kenar 1350 (≤1440)
        const sf = OUTW / cropCw;
        const out = document.createElement('canvas');
        out.width = OUTW; out.height = OUTH;
        const octx = out.getContext('2d');
        octx.fillStyle = '#000';
        octx.fillRect(0, 0, OUTW, OUTH);
        const eff = cropCoverS * cropScale;
        octx.drawImage(cropImg,
            cropOx * sf, cropOy * sf,
            cropImg.naturalWidth * eff * sf, cropImg.naturalHeight * eff * sf);
        pumpImageData = out.toDataURL('image/jpeg', 0.9);
        document.getElementById('pump-preview-img').src = pumpImageData;
        document.getElementById('pump-dropzone').classList.add('has-image');
        document.getElementById('pump-crop').hidden = true;
        cropImg = null;
    }

    function cancelPumpCrop() {
        document.getElementById('pump-crop').hidden = true;
        cropImg = null;
        // önizleme yoksa dropzone boş kalır; dosya inputunu sıfırla ki aynı foto tekrar seçilebilsin
        document.getElementById('pump-file-input').value = '';
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
        const locSel = document.getElementById('pump-location');
        const locOther = document.getElementById('pump-location-other');
        let locationType = locSel.value;
        if (locSel.value === 'Diğer:') {
            locationType = (locOther && locOther.value.trim()) || 'Diğer';
        }
        const payload = {
            image: pumpImageData,
            location_type: locationType,
            description: document.getElementById('pump-desc').value.trim(),
            visibility: pumpVisibility,
            shared_friend_ids: Array.from(pumpSelectedFriends.keys())
        };
        if (currentWorkoutState && currentWorkoutState.contract_version === 2 &&
            workoutStateClient.getSessionId()) {
            payload.session_id = workoutStateClient.getSessionId();
        }
        try {
            const mutation = await workoutStateClient.mutate('/workout/complete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            setPumpProgress(75);
            const data = mutation ? mutation.body : {};
            // Sunucu "zaten tamamlandı" yapısal kodunu (ör. başka cihazda yapıldı)
            // dönerse bunu hata değil, tamamlanmış kabul et. Dile bağımlı metin
            // yerine code alanına bak (i18n: error metni artık çevriliyor).
            const already = mutation && !mutation.ok && data.code === 'already_completed';
            if ((mutation && mutation.ok) || already) {
                setPumpProgress(100);
                if (mutation.ok) showToast(data.message, 'success');
                closePumpCheck();
                showCelebration(mutation.ok ? data : null, _pendingStats);
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

    // ── XP CELEBRATION — shown after a successful Pump Check; discards the
    //    ephemeral session snapshot once the user dismisses it (Task 6). ──
    function animateXP(el, target) {
        var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (reduce || !target) { el.textContent = target || 0; return; }
        var start = null, dur = 900;
        function frame(ts) {
            if (start == null) start = ts;
            var p = Math.min((ts - start) / dur, 1);
            var eased = 0.5 - Math.cos(p * Math.PI) / 2;         // easeInOutSine
            el.textContent = Math.round(target * eased);
            if (p < 1) requestAnimationFrame(frame);
        }
        requestAnimationFrame(frame);
    }

    function showCelebration(xpResp, stats) {
        var xp = (xpResp && xpResp.points_awarded) || 0;
        document.getElementById('cel-level').textContent =
            xpResp && xpResp.title ? xpResp.title + (xpResp.level ? ' · Lv ' + xpResp.level : '') : '';
        document.getElementById('cel-summary').innerHTML = stats ? (
            statCard(stats.totalVolume + ' kg', __t('training.volume')) +
            statCard(stats.setsDone + '/' + stats.totalSets, __t('training.sets')) +
            statCard(stats.exercisesDone, __t('training.exercises')) +
            statCard(stats.elapsedMin + ' ' + __t('training.min'), __t('training.duration'))
        ) : '';
        document.getElementById('celebration').classList.add('open');
        document.body.style.overflow = 'hidden';
        animateXP(document.getElementById('cel-xp'), xp);
        // No single "trigger" opened this overlay (it follows an async Pump Check
        // success, not a direct click) — focus the Done button; on close, focus
        // naturally falls back to <body> once the overlay is hidden, which is fine.
        setTimeout(function () { _focusFirstIn(document.getElementById('celebration')); }, 50);
    }

    function closeCelebration() {
        document.getElementById('celebration').classList.remove('open');
        document.body.style.overflow = '';
        _session = null; _pendingStats = null;      // discard ephemeral state
        workoutStateClient.refresh('celebration_closed');
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
            if (selections.kardiyo_tipi !== "yok"
                    && (Number(selections.gun_sayisi) + Number(selections.kardiyo_gun)) > 7) {
                showToast(__t('plan.contract.conflicting_preferences'), 'error');
                return;
            }
            const res  = await fetch("/training-plan", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(selections)
            });
            const data = await res.json();
            if (data.error) { showToast(data.error, 'error'); return; }
            currentPlan  = data.program;
            currentScore = data.overall_score;
            currentContextToken = data.exercise_context_token;
            renderResults(data);
        } catch (err) {
            showToast(__t('training.error_prefix') + err.message, 'error');
        } finally {
            btn.classList.remove("loading");
            btn.textContent = __t('training.create_program');
            loading.classList.remove("active");
        }
    }

    // Score-label → canonical token color var. data.score_label is a backend
    // canonical TR value ('İyi'/'Orta'/'Kötü'); only used to pick a CSS var, never
    // rendered raw when _EN (see labelMapEN below) — no hex/rgba needed.
    function scoreColorVar(label) {
        if (label === "Orta") return "var(--color-warning)";
        if (label === "Kötü") return "var(--color-danger)";
        return "var(--color-primary)";
    }
    function scoreClass(label) {
        if (label === "Orta") return " is-warning";
        if (label === "Kötü") return " is-danger";
        return "";
    }
    function scoreBarRow(label, val, colorVar) {
        return '<div style="display:flex;align-items:center;gap:var(--space-3);font-size:var(--text-sm);color:var(--color-text-2);">' +
            '<span style="min-width:90px;">' + esc(label) + '</span>' +
            '<div class="pbar-track" style="flex:1;"><div class="pbar-fill" style="width:' + (val * 10) + '%;background:' + colorVar + ';"></div></div>' +
            '<span style="min-width:32px;text-align:right;">' + esc(val) + '/10</span>' +
        '</div>';
    }

    function renderResults(data) {
        document.getElementById("results").style.display = "block";
        const saveBtn = document.getElementById("save-btn");
        saveBtn.classList.remove("saved");
        saveBtn.textContent = __t('training.save_program');

        const ozet = data.haftalik_ozet || {};
        const labelMapEN = { 'İyi': 'Good', 'Orta': 'Fair', 'Kötü': 'Poor' };
        const scoreLabelText = _EN ? (labelMapEN[data.score_label] || data.score_label) : data.score_label;
        const colorVar = scoreColorVar(data.score_label);
        const cls = scoreClass(data.score_label);

        // Score card: big readout + intensity/balance/fit bars
        document.getElementById("score-banner").innerHTML = `
            <div style="display:flex;align-items:center;gap:var(--space-4);flex-wrap:wrap;">
                <div class="tw-score${cls}">${esc(data.overall_score)}</div>
                <div style="flex:1;min-width:180px;">
                    <div class="tw-score${cls}" style="font-size:var(--text-2xl);letter-spacing:2px;">${esc(scoreLabelText)} ${__t('training.program_word')}</div>
                    <div style="font-size:var(--text-sm);color:var(--color-text-2);margin-top:var(--space-1);font-weight:var(--weight-light);">${__t('training.score_desc')}</div>
                </div>
            </div>
            <div style="display:flex;flex-direction:column;gap:var(--space-2);margin-top:var(--space-4);">
                ${scoreBarRow(__t('training.bar_intensity'), ozet.yogunluk_skoru || 7, colorVar)}
                ${scoreBarRow(__t('training.bar_balance'), ozet.denge_skoru || 7, colorVar)}
                ${scoreBarRow(__t('training.bar_fit'), ozet.uygunluk_skoru || 7, colorVar)}
            </div>
        `;

        // Weekly stats (reuse the same statCard() helper as the active-plan hero)
        const toplamKalori = data.program.reduce((a, g) => a + (g.tahmini_kalori || 0), 0);
        const workoutDays  = data.program.filter(g => g.tip !== "dinlenme").length;
        document.getElementById("weekly-summary").innerHTML =
            statCard(ozet.toplam_antrenman_gun || selections.gun_sayisi, __t('training.workout_day')) +
            statCard(toplamKalori, __t('training.weekly_cal')) +
            statCard(workoutDays * selections.sure, __t('training.total_min'));

        // Per-day exercise groups (read-only exercise-card list; rest days skipped)
        document.getElementById("weekly-grid").innerHTML = data.program
            .filter(gun => gun.tip !== "dinlenme")
            .map(gun => {
                const odakText = gun.odak ? " — " + esc(gun.odak) : "";
                const cards = (gun.egzersizler || []).map(exerciseCardHTML).join("");
                return '<div class="tw-day-group"><div class="sec-label">' + esc(dayLabel(gun.gun)) + odakText + '</div>' + cards + '</div>';
            }).join("");

        setTimeout(() => document.getElementById("results").scrollIntoView({ behavior:"smooth", block:"start" }), 200);
    }

    async function savePlan() {
        const btn = document.getElementById("save-btn");
        if (!currentPlan) return;
        try {
            const result = await workoutStateClient.mutate("/training-plan/save", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    plan: currentPlan,
                    score: currentScore,
                    exercise_context_token: currentContextToken
                })
            });
            if (!result || !result.ok) {
                const detail = result && result.body && result.body.error;
                throw new Error(detail || "request_failed");
            }
            btn.textContent = __t('training.saved');
            btn.classList.add("saved");
            showToast(__t('training.program_saved'), 'success');
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
        document.getElementById('pump-location').addEventListener('change', syncPumpLocationOther);
        // Frame Adjustment kontrolleri
        const cropCanvas = document.getElementById('pump-crop-canvas');
        cropCanvas.addEventListener('pointerdown', cropPointerDown);
        cropCanvas.addEventListener('pointermove', cropPointerMove);
        cropCanvas.addEventListener('pointerup', cropPointerUp);
        cropCanvas.addEventListener('pointercancel', cropPointerUp);
        document.getElementById('pump-crop-zoom').addEventListener('input', cropZoomChange);
        document.getElementById('pump-crop-confirm').addEventListener('click', confirmPumpCrop);
        document.getElementById('pump-crop-cancel').addEventListener('click', cancelPumpCrop);
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
    })();

    // ── OVERLAY A11Y: Esc-to-close (pump/session/celebration/day-preview) +
    //    focus-into-overlay on open + return-focus-to-trigger on close.
    //    Mirrors the pattern already used in static/nutrition.js (Task 9). ──
    function _focusFirstIn(container) {
        if (!container) return;
        var f = container.querySelector(
            'input, select, textarea, button, [tabindex]:not([tabindex="-1"])');
        if (f) { try { f.focus({ preventScroll: true }); } catch (e) { f.focus(); } }
        else if (container.hasAttribute('tabindex')) {
            try { container.focus({ preventScroll: true }); } catch (e) { container.focus(); }
        }
    }
    function _restoreFocus(trigger) {
        if (trigger && typeof trigger.focus === 'function' && document.body.contains(trigger)) {
            try { trigger.focus({ preventScroll: true }); } catch (e) { trigger.focus(); }
        }
    }

    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape') return;
        var pump = document.getElementById('pump-check-modal');
        if (pump && pump.classList.contains('active')) { closePumpCheck(); return; }
        var session = document.getElementById('session-view');
        if (session && session.classList.contains('open')) { closeSession(); return; }
        var cel = document.getElementById('celebration');
        if (cel && cel.classList.contains('open')) { closeCelebration(); return; }
        var dp = document.getElementById('day-preview');
        if (dp && dp.classList.contains('open')) { closeDayPreview(); return; }
    });

    // Lightweight focus-trap: Tab cycles within #session-view while it's open.
    (function initSessionFocusTrap() {
        var container = document.getElementById('session-view');
        if (!container) return;
        container.addEventListener('keydown', function (e) {
            if (e.key !== 'Tab' || !container.classList.contains('open')) return;
            var focusables = Array.prototype.filter.call(
                container.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'),
                function (el) { return el.offsetParent !== null; }
            );
            if (!focusables.length) return;
            var first = focusables[0], last = focusables[focusables.length - 1];
            if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
            else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        });
    })();

    populateOptions();
    setupInjuryPicker();
    loadInfo();

    function initWorkoutStateClient() {
        if (workoutStateClient) return workoutStateClient;
        workoutStateClient = window.FitXWorkoutStateClient.createWorkoutStateClient({
            fetchImpl: window.fetch.bind(window),
            onSnapshot: applyTrainingSnapshot,
            onBlocked: renderTrainingBlocked,
            documentRef: document,
            addEventListener: window.addEventListener.bind(window),
            removeEventListener: window.removeEventListener.bind(window)
        });
        workoutStateClient.refresh('load');
        return workoutStateClient;
    }

    function destroyWorkoutStateClient() {
        if (!workoutStateClient) return;
        const ownedClient = workoutStateClient;
        workoutStateClient = null;
        ownedClient.destroy();
    }

    initWorkoutStateClient();
    window.addEventListener('pagehide', destroyWorkoutStateClient);
    window.addEventListener('pageshow', function (event) {
        if (event.persisted) initWorkoutStateClient();
    });
