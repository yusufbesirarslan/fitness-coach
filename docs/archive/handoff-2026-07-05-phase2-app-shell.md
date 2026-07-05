# Phase 2 Handoff — AxisAI V2 Uygulama Kabuğu (Navigation & Layout)

Date: 2026-07-05
Branch: `feat/phase2-app-shell` (base: `feat/phase1-design-system` @ f0dec19)
Plan: `docs/superpowers/plans/2026-07-05-phase2-app-shell.md`
Docs: `docs/design-system.md` → "Uygulama Kabuğu (Phase 2)" bölümü (Phase 3 bunu okumalı)
Önceki faz: `docs/archive/handoff-2026-07-04-phase1-design-system.md`

## Completed Work

- **Çekmece (sidebar drawer) kaldırıldı.** Hamburger + overlay çekmece ve
  `static/nav.js` (drawer/swipe JS) silindi; kabuk artık JS'siz çalışır.
- **5 sekmeli alt gezinme:** `_actionbar.html` yeniden yazıldı — Ana Sayfa `/`,
  Beslenme `/nutrition`, Antrenman `/training`, İlerleme `/progress-page`,
  Profil `/edit-profile`. Aktif sekme `aria-current="page"` taşır.
- **Üst başlık (v4):** `_nav.html` yalnızca başlık — marka (artık `/`'a link),
  ≥1024px'te yatay sekmeler (`.header-nav` > `.hn-link`), sağda avatar.
  <1024px'te alt çubuk, ≥1024px'te başlık sekmeleri: masaüstü/tablet uyumu.
- **Tekilleştirme:** 8 sayfadaki (index, nutrition, training, progress, quests,
  friends, leaderboard, manage_stack) satır-içi header+drawer+action-bar
  kopyaları (441 satır) silindi; 13 kimlikli sayfanın tamamı iki ortak
  partial'ı include ediyor (`nav_active` sözleşmesi).
- **Profil hub'ı:** çekmecenin tüm hedefleri Profil sayfasına taşındı —
  Keşfet: Arkadaşlarım, Feed, Kulüp, Görevler, Supplement Dolabı, Premium;
  Ayarlar: dil anahtarı (TR/EN, `setLang`) + Çıkış. Rütbe ünvanı
  (`user_title · Level n`) avatar bloğuna eklendi (çekmece başlığından miras).
  İkincil sayfalar `nav_active='profile'` ile Profil sekmesini vurgular.
- **Kabuk yerleşimi:** `.page-body` sabit çubuk + `env(safe-area-inset-bottom)`
  payını tek yerden verir; `theme.css` `.main-content` padding'leri 8-pt
  token'lara çekildi (16px mobil / 24px ≥768px yanlar); sayfa bazlı
  padding-bottom hack'leri kaldırıldı (edit_profile). Viewport
  `viewport-fit=cover` (çentikli cihazlarda safe-area artık gerçekten çalışır).
- **Sayfa geçişi:** `.main-content` `page-enter` animasyonu
  (reduced-motion'da devre dışı).
- **nav.css v4:** drawer kuralları silindi (~%45 küçüldü), kalan + yeni tüm
  kurallar kanonik token'larla; hub bileşen stilleri eklendi.
- **Regresyon korumaları:** `tests/test_app_shell.py` (7 test: partial
  kablosu, drawer/nav.js yokluğu, 5 sekme + aktif işaret, ikincil→profil,
  viewport-fit, 12 route'luk kimlikli kabuk smoke'u, hub envanteri).

## Files Modified

- Silinen: `static/nav.js`.
- Yeniden yazılan: `templates/_nav.html`, `templates/_actionbar.html`,
  `static/nav.css`.
- Değişen: `templates/_head.html` (viewport), `static/theme.css`
  (.main-content), `static/components.css` (focus listesi drawer→hub),
  `templates/edit_profile.html` (hub + avatar-rank + back-btn kaldırıldı),
  13 kimlikli sayfa şablonu (include + nav_active + nav.js tag'i),
  `locales/{tr,en}.json` (`nav.section_settings`).
- Test: `tests/test_app_shell.py` (yeni),
  `tests/test_pump_check_sharing.py` (2 nav testi yeni kabuğa yazıldı),
  `tests/test_i18n.py` (drawer testi → profil sayfası).

## Components Created or Refactored

- Yeni kabuk sınıfları: `.header-nav`, `.hn-link(.active)`, `.hub`,
  `.hub-section-label`, `.hub-card`, `.hub-link(-premium/-danger)`,
  `.hub-row`, `.hub-chevron`, `.hub-lang(-opt)`.
- Yeniden tasarlanan: `.global-header` (drawer-trigger'sız, safe-area yan
  padding), `.action-bar`/`.ab-tab` (5 sekme, 10px etiket, ≥1024 gizli),
  `.header-avatar` (36px, img kuralı CSS'e taşındı), `.page-body`.
- Kaldırılan: `.drawer-*` ailesi (backdrop, xp, lang, logout…),
  `.drawer-trigger`, eski `.main-content` margin override'ı.

## Architectural Decisions

1. **Önce tekilleştir, sonra tasarla:** Task 1 satır-içi kopyaları mevcut
   partial'lara indirger (görsel değişiklik yok), Task 2 tek noktadan yeniden
   tasarlar. Her commit çalışır durumda.
2. **Çekmece yerine hub:** ikincil gezinme ayrı bir overlay yüzeyi yerine
   Profil sekmesinin içeriği oldu — mobil desenle uyumlu, JS'siz, tek IA.
3. **Masaüstü = başlık sekmeleri:** ≥1024px'te alt çubuk gizlenir; aynı 5
   hedef `.header-nav`'da. Ek bir masaüstü sidebar'ı bilinçli yapılmadı.
4. **Alt boşluk tek kaynak:** sabit çubuk payını yalnızca `.page-body` verir;
   sayfalar kendi safe-area/padding hack'ini taşımaz.
5. **`nav_active` sözleşmesi:** `home | nutrition | training | progress |
   profile`; ikincil sayfalar `profile` işaretler.
6. **Kanonik token politikası** nav.css'te tam uygulandı; yarı saydam
   header/bar zeminleri bilinçli rgba literal (token yok, not düşüldü).

## Verification Done

- `python -m pytest -q` → **1088 passed, 0 failed** (Phase 1 bazı 1081 + yeni
  kabuk testleri; 3 eski nav testi yeni kabuğa göre yeniden yazıldı).
- Kimlikli kabuk smoke'u (test client): /, nutrition, training,
  progress-page, quests, friends, feed, leaderboard, supplements, premium,
  edit-profile, pump-check-gallery → 200 + global-header + action-bar,
  fx-drawer yok (`test_all_app_pages_render_shared_shell`).
- Görsel tarayıcı kontrolü YAPILAMADI (Chrome eklentisi bu oturumda da bağlı
  değil — Phase 1'deki kısıtın aynısı). Deploy öncesi hızlı göz kontrolü
  önerilir: alt çubuk (mobil), başlık sekmeleri (masaüstü), profil hub'ı.

## Quality Metrics Review

- **Responsiveness: iyi.** <1024 alt çubuk / ≥1024 başlık sekmeleri; safe-area
  yatay+dikey (`viewport-fit=cover` + `env()`); içerik `--content-max` ile
  ortalı. Zayıflık: sayfa İÇERİKLERİ hâlâ eski grid'leriyle (sayfa fazlarında).
- **Accessibility: iyi yönde.** `aria-current="page"`, `aria-label`'lı nav'lar,
  44-48px dokunma hedefleri, focus-visible halkaları, reduced-motion'lı geçiş.
  Zayıflık: kontrast denetimi (axe) hâlâ yapılmadı; alt sekme etiketi 10px
  uppercase — WCAG bakımından ikon+`aria-current` ile destekli ama tarayıcıda
  doğrulanmalı.
- **Visual consistency: iyi.** Tek kabuk, tek boşluk kaynağı, tokenlı nav.css.
  Zayıflık: dashboard/coach_widget yerel palet sapmaları sürüyor (Phase 3+).
- **Code maintainability: çok iyi.** −441 satır şablon kopyası, −78 satır JS;
  kabuk 3 dosyada (2 partial + nav.css) ve test korumalı.
- **Reusability: iyi.** Hub sınıfları genel amaçlı (ayar listeleri için de
  kullanılabilir); kabuk sınıfları kamu API'si olarak dokümante.
- **Performance: iyi yönde.** 1 JS isteği ve ~80 satır CSS azaldı; kabuk
  JS'siz. DM Sans hâlâ font URL'inde (Phase 1 TODO'su, sayfa inline'ları
  bitince çıkarılacak).
- **UX clarity: iyi yönde.** 5 sekmeli standart mobil IA; ikincil hedefler tek
  yerde (Profil). Risk: Feed/Kulüp eski alt çubuktan derine taşındı — kullanım
  verisine göre Home'a kısayol eklenebilir.

## Known Issues

- Piksel düzeyinde tarayıcı doğrulaması yok (yukarıda). Özellikle: masaüstünde
  `.header-nav`'ın dar 1024-1100px aralığında marka+sekme+avatar sığışması.
- `chat.html`'in sohbet yerleşimi kabuk padding'leriyle çalışıyor ama chat'e
  özel yükseklik hesapları (chat-wrap) sayfa fazında gözden geçirilmeli.
- Eski `nav.menu_open`/`nav.menu_nav` anahtarlarından `nav.menu_open` artık
  kullanılmıyor (menu_nav hub aria-label'ı oldu); sözlükte zararsız duruyor.
- `--z-drawer*`/`--drawer-w` token'ları kullanım dışı (miras olarak duruyor).
- `.superpowers/sdd/*` ve `AGENTS.md` depo kökünde takip dışı scratch —
  commit'lenmedi, dokunulmadı.

## Remaining Tasks / Next Recommended Steps

1. **Deploy öncesi görsel kontrol** (mobil 390px, tablet 768px, masaüstü
   1280px): alt çubuk, başlık sekmeleri, profil hub'ı, chat sayfası.
2. **Phase 3 (sayfa içerikleri):** sayfa `page-hdr`/grid'lerini kabuk
   ritmine (8-pt, `--content-max`) oturt; ad-hoc modal/sheet/avatar/badge
   kopyalarını components.css bileşenlerine geçir.
3. Kontrast/axe taraması (Phase 1'den devir).
4. dashboard.css + coach_widget.css gri paletlerini semantik token'lara çek;
   coach widget lime kalıntılarını temizle (Phase 1'den devir).
5. 900px media query'lerini 1024'e, kalan grid dışı boşlukları 8-pt'e çek.
6. Feed/Kulüp erişim yolunu kullanımla doğrula; gerekirse Home'a giriş kartı.
7. Inline literaller bitince DM Sans'ı fonts URL'inden çıkar (Phase 1 devri).
