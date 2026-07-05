# Phase 3 Handoff — AxisAI V2 Ana Panel (AI Command Center)

Date: 2026-07-05
Branch: `feat/phase2-app-shell` (Phase 3 çalışması bu branch üzerinde devam etti)
Spec: `docs/superpowers/specs/2026-07-05-phase3-home-dashboard-design.md`
Plan: `docs/superpowers/plans/2026-07-05-phase3-home-dashboard.md`
Docs: `docs/design-system.md` → dashboard.css artık token-uyumlu (aşağıda)
Önceki faz: `docs/archive/handoff-2026-07-05-phase2-app-shell.md`

## Completed Work

Ana panel (`/`) baştan sona **"AI Command Center"** olarak yeniden tasarlandı.
`templates/index.html` ve `static/dashboard.css` tamamen yeniden yazıldı; backend
uçlarına ve iş mantığına DOKUNULMADI (tüm veri aynı endpoint'lerden geliyor).
Panel `.dash-grid` içinde 8 bölümden oluşuyor (spec sırası):

- **1 · Kimlik (`.dash-id`):** avatar, selamlama (`setGreeting` → `#id-hello`),
  kullanıcı adı, XP çipi ve Streak çipi (`.id-chip.xp` / `.id-chip.streak`),
  rütbe/level meta satırı.
- **2 · Hero (`.hero`):** "Bugünkü Hedef", kalan kalori ve dairesel ilerleme
  halkası (`updateCalRing(consumed, target)`, r=52 → çevre 326.73). Aktivite
  kalorisi kaldırıldığı için ring artık 2 argümanlı (activity=0).
- **3 · AI Sıradaki Aksiyon (`.next`) — panelin en kritik bölümü:** istemci
  tarafı **deterministik kural motoru** (`computeNextAction` → `renderNextAction`
  → `doNextAction`). Günün saatine göre öncelik: kahvaltı / öğle / akşam yemeği,
  su iç, antrenmana başla, hepsi tamam. LLM ÇAĞRISI YOK (aşağıdaki karar #1).
- **4 · Hızlı İşlemler (`.qa-grid`):** Öğün Ekle, Barkod Tara (devre dışı —
  "yakında"), Menü Tarayıcı (koç widget'ını açar), Antrenmana Başla. Eski
  bağımsız hızlı-ekle FAB bu grid'e katlandı.
- **5 · Beslenme Özeti (`.nutri-grid`):** Protein / Karbonhidrat / Yağ / Su için
  animasyonlu halkalar (`components.css` `.ring-*`, r=26 → çevre 163.36).
  Makro hedefleri nutrition.js formülüyle: protein=hedef·0.30/4, karb=·0.40/4,
  yağ=·0.30/9.
- **6 · Kilo (`.wt`):** mini Chart.js sparkline (haftalık trend + delta),
  kilo güncelleme formu (`doUpdateWeight` → `/update-weight`).
- **7 · Başarımlar (`.ach`):** XP, rozet amblemi, streak, görevler linki;
  streak bonus aktifken `.ach-xp.bonus-active`.
- **8 · AI İpucu (`.tip`):** günlük öneri karuseli (TIPS_TR/TIPS_EN, dot +
  ilerleme çubuğu, otomatik geçiş, `visibilitychange`'de duraklar).

`dashboard.css` eski `--volt`/sabit-gri paletten **kanonik token'lara** taşındı
(hiç `--volt`/hex/rgba kalmadı) ve `components.css` bileşenlerini (`.card`,
`.ring-*`, `.pbar-*`, `.sec-label`, `.stat-card`) yeniden kullanıyor. ≥768px'te
bento düzeni: `.dash-id/.hero/.next/.tip` 2 kolon, diğerleri 1 kolon.

## Files Modified

- Yeniden yazılan: `templates/index.html`, `static/dashboard.css`.
- Değişen: `locales/tr.json` + `locales/en.json` (33 yeni `index.*` anahtarı;
  `index.hero_goal`, `index.next_action_label`, `index.quick_actions`,
  `index.nutrition_summary`, `index.achievements`, `index.qa_*`,
  `index.macro_*`, `index.na_*`, `index.tip_*` vb.).
- Test: `tests/test_i18n.py::test_dashboard_renders_localized` (assertion'lar yeni
  markup'a göre: "Quick Actions" + "Nutrition Summary" var, "Hızlı İşlemler" yok;
  "Sports Physiology" kontrolü korundu).
- Docs: `docs/design-system.md` (dashboard.css gri-palet TODO'su ✅ ile
  kapatıldı, token-uyumu notu eklendi), `docs/handoff.md` (bu dosya),
  `docs/superpowers/specs/2026-07-05-phase3-home-dashboard-design.md` (spec),
  `docs/superpowers/plans/2026-07-05-phase3-home-dashboard.md` (plan).

## Components Created or Refactored

- Yeni panel sınıfları: `.dash-grid`, `.dash-id/.id-avatar/.id-hello/.id-name/
  .id-meta/.id-chip(.xp/.streak)`, `.hero/.hero-ring/.hero-num/.hero-cap/
  .hero-body/.hero-goal/.hero-sub`, `.next/.next-icon/.next-body/.next-title/
  .next-sub/.next-cta`, `.qa-grid/.qa-tile(.disabled)/.qa-ic/.qa-lbl/.qa-soon`,
  `.nutri-grid/.nutri-cell/.nutri-val/.nutri-lbl`, `.wt/.wt-big/.wt-delta/
  .wt-spark/.wt-form/.wt-input/.wt-btn/.wt-meta`, `.ach/.ach-emblem/.ach-title/
  .ach-xp/.ach-streak/.ach-link`, `.tip/.tip-icon/.tip-text/.tip-src/.tip-nav/
  .tip-btn/.tip-dots/.tip-dot(.on)/.tip-progress/.tip-pbar/.tip-slide`.
- Yeniden kullanılan (yeni kopya YOK): `components.css` `.card`, `.ring-wrap/
  .ring-svg/.ring-track/.ring-fill/.ring-label`, `.pbar-track/.pbar-fill`,
  `.sec-label`, `.stat-card`, `.badge`, `.avatar`.
- Kaldırılan: aktivite/adım kartı, bağımsız hızlı-ekle FAB, `updateCalRing`'in
  3. (aktivite) argümanı, eski `--volt`/gri panel stilleri.

## Architectural Decisions

1. **AI Sıradaki Aksiyon istemci tarafı kural motoru:** LLM çağrısı yok. CLAUDE.md
   AI route'larının senkron/thread-bloklayıcı olduğunu (tek worker/8 thread)
   uyarıyor; ana panel her açılışta çalıştığı için deterministik, saat-tabanlı
   bir kural motoru (`computeNextAction`) seçildi. Ucuz, anında, offline-güvenli.
2. **Barkod Tara = "yakında" (devre dışı kutu):** barkod tarayıcı backend'i yok;
   tile `.qa-tile.disabled` + `.qa-soon` rozetiyle görünür ama tıklanamaz.
3. **Menü Tarayıcı = global koç widget'ı:** `openMenuScan` → `window.CW.toggle()`
   + `CW.startScan()`; widget yoksa `/nutrition`'a fallback.
4. **Adım/aktivite kartı kaldırıldı:** yalnızca placeholder'dı (ileride cihaz
   sağlık verisinden otomatik gelecek). "Adımları tamamla" AI aksiyonundan da
   düştü; ring'de aktivite kalorisi 0 kabul edilir.
5. **FAB → Hızlı İşlemler:** bağımsız yüzen buton grid'e katlandı (tek IA,
   daha az yüzen öğe). Kullanıcı kararı.
6. **CSS kanonik token'lar üzerine yeniden yazıldı:** `--volt`/hex/rgba yok;
   `components.css` ring/bar/card'ları yeniden kullanılıyor (DRY).
7. **Backend değişmedi:** `/last-session`, `/meal-log/today`, `/water`,
   `/workout/status`, `/checkin-history`, `/update-weight`,
   `/leaderboard/reward-check|reward-dismiss` aynen korundu.

## Verification Done

- `python -m pytest -q` → **1088 passed, 0 failed** (762s; Phase 2 bazı 1088 ile
  aynı — panel yeniden yazımı test sayısını değiştirmedi, mevcut regresyonlar
  yeşil kaldı).
- Design-system token guard'ları geçiyor; `dashboard.css`'te `--volt`/hex/rgba
  kalmadı (doğrulandı).
- App-shell smoke'u (`tests/test_app_shell.py`) yeşil — yeni panel kabuğa
  (global-header + action-bar) düzgün oturuyor.
- i18n testi: EN katalog (`Quick Actions`/`Nutrition Summary`) var, TR karşılığı
  (`Hızlı İşlemler`) render'da yok → lokalizasyon doğru.
- **Görsel tarayıcı kontrolü YAPILAMADI** (Chrome eklentisi bu oturumda da bağlı
  değil — Phase 1/2 ile aynı kısıt). Deploy öncesi göz kontrolü önerilir.

## Quality Metrics Review

- **Responsiveness: iyi.** Mobil tek kolon; ≥768px bento (hero/kimlik/aksiyon/
  ipucu 2 kolon, hızlı-işlem/beslenme/kilo/başarım eşleşerek 1 kolon). Ring'ler
  ve sparkline `max-width:100%`. Zayıflık: çok geniş masaüstünde (≥1280) panel
  `--content-max` ile ortalı; ekstra kolon denenmedi.
- **Accessibility: iyi yönde.** Anlamsal başlıklar, `.sec-label`, dokunma
  hedefleri ≥44px, devre dışı tile `aria-disabled`. Zayıflık: kontrast/axe
  taraması hâlâ yapılmadı (Phase 1'den devir); ring değerlerinin ekran
  okuyucu metni tarayıcıda doğrulanmalı.
- **Visual consistency: çok iyi.** Panel artık tamamen kanonik token'larda ve
  paylaşılan `components.css` bileşenlerinde — Phase 2'de not düşülen
  dashboard.css palet sapması KAPANDI.
- **Code maintainability: iyi.** Tek şablon + tek CSS; ad-hoc ring/bar kopyası
  yok, hepsi components.css'ten. Kural motoru saf fonksiyon (`computeNextAction`)
  — test edilebilir.
- **Reusability: iyi.** Ring/bar/card/badge bileşenleri yeniden kullanıldı; yeni
  `.qa-*`, `.nutri-*`, `.tip-*` sınıfları panel-özel ama tutarlı adlandırmada.
- **Performance: iyi.** AI aksiyonu istemci tarafı (ek AI thread yok); mevcut
  endpoint'ler dışında yeni network yok; Chart.js zaten yükleniyordu (SRI'lı).
- **UX clarity: iyi.** "Sıradaki en önemli iş" en üstte ve tek; hızlı işlemler
  4'lü grid; ipucu karuseli pasif bilgi. Risk: kural motoru öncelikleri gerçek
  kullanımda ayarlanmalı (ör. su hedefi eşiği).

## Known Issues

- Piksel düzeyinde tarayıcı doğrulaması yok (yukarıda). Özellikle mobil 390px'te
  ring hizası ve bento kırılımı deploy öncesi gözle kontrol edilmeli.
- **Barkod Tara** devre dışı — backend gelene kadar "yakında". Etkinleştirmek
  için tarayıcı endpoint'i + `qa-tile.disabled` kaldırılması gerekir.
- **Adım/aktivite** kaldırıldı — cihaz sağlık entegrasyonu geldiğinde geri
  eklenecek (aktivite kalorisi ring'e o zaman katılır).
- AI Sıradaki Aksiyon **deterministik/istemci tarafı** — gerçek LLM önerisi
  değil (bilinçli tradeoff, karar #1). İleride hafif bir günlük özet çağrısıyla
  zenginleştirilebilir ama ana-panel-açılışında senkron AI'dan kaçınılmalı.
- `.superpowers/sdd/*` ve `AGENTS.md` depo kökünde takip dışı scratch —
  commit'lenmedi, dokunulmadı (Phase 2'den devam).

## Remaining Tasks / Next Recommended Steps

1. **Deploy öncesi görsel kontrol** (mobil 390px, tablet 768px, masaüstü 1280px):
   hero ring, AI aksiyon kartı, hızlı işlemler grid'i, beslenme halkaları,
   kilo sparkline, ipucu karuseli.
2. **Barkod tarama backend'i** eklenince "yakında" tile'ını etkinleştir.
3. **Kural motoru öncelikleri** gerçek kullanım verisiyle kalibre et (yemek/su/
   antrenman eşikleri).
4. Kontrast/axe taraması (Phase 1'den devir — panel dahil).
5. Diğer sayfa içerikleri (nutrition/training/progress) hâlâ eski grid'leriyle;
   panelle aynı 8-pt + components.css ritmine oturt.
6. `coach_widget.css` lime/gri kalıntıları (Phase 1'den devir) — panel bitti,
   sırada widget.
