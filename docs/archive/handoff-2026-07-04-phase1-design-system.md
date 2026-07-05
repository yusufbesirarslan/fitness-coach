# Phase 1 Handoff — AxisAI V2 Design System

Date: 2026-07-04
Branch: `feat/phase1-design-system` (base: `origin/main` @ 90b36cd)
Plan: `docs/superpowers/plans/2026-07-04-phase1-design-system.md`
Docs: `docs/design-system.md` (token + bileşen referansı — Phase 2 bunu okumalı)

## Completed Work

- **`static/tokens.css` (yeni):** tek kanonik token kaynağı — renk
  (primitives → semantik → legacy alias), tipografi (Inter tabanlı skala,
  ağırlık, satır yüksekliği, tracking), 8-pt boşluk skalası, radius, kenarlık
  kalınlıkları, elevation/gölge, opaklık, ikon boyutları, hareket
  (süre + easing), z-index skalası, layout sabitleri, breakpoint referansları
  ve `[data-theme="light"]` override bloğu (light/dark hazırlığı).
- **`static/components.css` (yeni):** 16 bileşenlik kütüphane — Button,
  Input(+field), Card, **Modal (yeni)**, **Bottom Sheet (yeni)**,
  **Badge (yeni)**, Chip, **Avatar (yeni)**, Progress Ring, Progress Bar,
  Navigation Item (tabs), FAB, Empty State, Loading Skeleton (+varyantlar),
  Stat Card, Section Header + Toast, Loading Overlay, Divider, keyframe'ler,
  tabular-nums, focus-visible ve reduced-motion kuralları.
- **Inter tipografisi:** `_head.html` Google Fonts'a Inter 300–800 eklendi;
  `--font-body` artık Inter (DM Sans fallback olarak yüklü kalıyor).
  `--font-display` Bebas Neue olarak korundu (başlık redesign'ı sonraki fazlar).
- **Global kablo:** `_head.html` tokens.css + components.css'i tüm sayfalarda
  sayfa CSS'lerinden önce yükler (kaskad sözleşmesi korunur).
- **Hardcoded değer süpürmesi:** theme.css, style.css, nav.css, dashboard.css,
  coach_widget.css + 18 şablonun satır-içi `<style>` bloğu — birebir eşleşen
  renk/font/easing/boşluk literalleri token'lara çevrildi (≈300+ değişim).
- **Tekilleştirme:** theme.css'in `:root` bloğu ve taşınan bileşen bölümleri
  silindi; style.css'in tema blokları tokens.css'e devredildi; ölü kod
  kaldırıldı (`.mobile-bottom-nav`, style.css'in kullanılmayan `.stat-card`'ı,
  yinelenen `@keyframes spin`).
- **Regresyon korumaları:** `tests/test_design_system.py` (asset kablosu,
  token sözleşmesi, bileşen envanteri).

## Files Modified

- Yeni: `static/tokens.css`, `static/components.css`, `docs/design-system.md`,
  `tests/test_design_system.py`, plan dosyası.
- Değişen: `templates/_head.html`, `static/{theme,style,nav,dashboard,coach_widget}.css`,
  şablonlar: index, nutrition, training, progress, chat, friends, feed,
  leaderboard, quests, manage_stack, edit_profile, premium, landing, login,
  register, setup, verify (`pump_check_gallery` değişmedi — zaten token'lıydı;
  404/500 bilinçli dokunulmadı — bağımsız mini sayfalar).
- `docs/handoff.md` yeniden yazıldı (önceki pump-check handoff'u
  `docs/archive/handoff-2026-07-03-pump-check-sharing.md`).

## Components Created or Refactored

- Yeni tanım: Modal, Bottom Sheet, Badge (+5 varyant), Avatar (4 boyut),
  `.btn-danger`, `.field/.field-label`, `.skeleton-text/.skeleton-circle`.
- Konsolide (theme.css → components.css, sınıf adları değişmeden): btn-volt,
  btn-ghost, fc-input, card, chip, tab-bar/tab-btn/tab-panel, ring-*, pbar-*,
  quick-add-*/fab-*, empty-*, skeleton, sec-label/cat-label, toast-*,
  loading-overlay, fc-divider.
- Kanonikleşen: Stat Card (style.css'teki ölü kopya silindi, components.css'te
  token'lı sürüm).

## Architectural Decisions

1. **Zero-visual-change disiplini:** görünüm birebir korunur; tek istisna
   gövde fontunun Inter'e geçmesi (faz gereksinimiydi). Yalnızca birebir değer
   eşleşmeleri token'a çevrildi; token karşılığı olmayan yerel paletler
   (dashboard/coach_widget grileri) bilinçli bırakıldı.
2. **Alias stratejisi:** yüzlerce `--volt*`/`--accent*` referansı kırılmasın
   diye legacy isimler tokens.css'te kanonik token'lara bağlandı. Yeni kod
   yalnızca kanonik isim kullanır (politika docs/design-system.md'de).
3. **Kaskad sözleşmesi:** tokens → components → sayfa CSS → sayfa inline;
   sayfa kuralları eşit özgüllükte kazanmaya devam eder (taşımalar güvenli).
4. **Tema hazırlığı:** semantik katman `[data-theme]` ile temalanır; auth
   sayfalarındaki canlı light toggle davranışı birebir korundu (`--input-bg`
   ve `--border` için tarihsel istisnalar tokens.css/style.css'te açıklandı).
5. **Navigation Item kapsamı:** sekmeler (tab-bar) bileşenleşti; drawer ve
   action-bar öğeleri Phase 2'nin (navigasyon fazı) alanı olduğundan nav.css'te
   token'lanarak bırakıldı.

## Verification Done

- `python -m pytest -q` → **1081 passed, 0 failed** (temiz baz: 1078 + 3 yeni).
- Selektör envanteri diff'i (origin/main vs yeni dosyalar): kayıp kural yok
  (yalnızca bilinçli silinen ölü kod + taşınan `:root`).
- Kimlikli sunucu smoke'u (scratch SQLite): /, nutrition, training, progress,
  quests, friends, feed, leaderboard, edit-profile, supplements, premium,
  pump-check-gallery, setup, login → hepsi 200 + tokens/components kablolu;
  7 CSS dosyası 200.

## Quality Metrics Review

- **Responsiveness: iyi.** Tüm kırılımlar korundu; display boyutları için
  clamp() token'ları eklendi. Zayıflık: tarihsel 900px sorguları breakpoint
  skalasının dışında (Phase 2'de 1024'e çekilmeli).
- **Accessibility: orta-iyi.** focus-visible halkaları, reduced-motion ve
  tabular-nums korunup merkezileştirildi; `--focus-ring` token'ı eklendi.
  Zayıflık: kontrast denetimi yapılmadı (özellikle dashboard'ın #505058
  ara-grileri); Phase 2'de axe/kontrast taraması önerilir.
- **Visual consistency: iyi (dark).** Tek token kaynağı + bileşen kütüphanesi.
  Zayıflık: dashboard/coach_widget yerel gri paletleri ve coach widget'taki
  lime kalıntıları (`#99cc00`, `#d6ff1a`) tutarsız — bilinçli ertelendi.
- **Code maintainability: iyi.** theme.css 27→~11 KB'a indi; token/bileşen/
  sayfa katmanları ayrıştı; her şey dokümante.
- **Reusability: iyi.** 16 bileşen tek dosyada, tüm sayfalara yüklü; Modal/
  Sheet/Badge/Avatar hazır ama sayfalar henüz ad-hoc kopyalarında (adaptasyon
  sayfa fazlarında).
- **Performance: nötr-iyi.** +2 küçük CSS isteği (`?v=` cache-bust'lı) karşılığı
  yinelenen kurallar silindi; Inter eklendi ama DM Sans da geçiş boyunca
  yükleniyor → font transferi arttı. DM Sans, inline literaller tamamen
  temizlenince fonts URL'inden çıkarılmalı (Phase 2-3).
- **UX clarity: değişmedi (kasıtlı).** Bu faz görsel yeniden tasarım yapmadı.

## Known Issues

- **Görsel doğrulama sunucu-taraflıdır:** Chrome eklentisi bu oturumda bağlı
  değildi; piksel düzeyinde tarayıcı kontrolü yapılamadı. Deploy öncesi hızlı
  bir göz kontrolü önerilir (özellikle login, index, training).
- Inter geçişi metin metriklerini bir tık değiştirir (DM Sans'a çok yakın ama
  birebir değil) — beklenen ve istenen değişiklik.
- Chart.js konfiglerindeki `'DM Sans'`/renk literalleri JS içinde kaldı
  (progress, index) — grafikler hâlâ DM Sans çizer.
- 404/500 sayfaları tasarım sistemine bağlı değil (bağımsız, _head.html
  içermiyor).
- `.superpowers/sdd/*` ve `AGENTS.md` depo kökünde takip dışı scratch —
  commit'lenmedi, dokunulmadı.

## Remaining Tasks / Next Recommended Steps

1. **Phase 2 (Navigation & Layout):** `docs/design-system.md` + bu dosyayı
   okuyarak başla; nav.css zaten token'lı, yapısal değişiklik oraya.
2. Sayfalardaki ad-hoc modal/sheet/avatar/badge kopyalarını components.css
   bileşenlerine geçir (sayfa fazlarında).
3. dashboard.css + coach_widget.css ara-gri paletini semantik token'lara
   normalize et; coach widget lime kalıntılarını temizle.
4. 900px media query'lerini 1024'e, grid dışı boşlukları 8-pt'e çek.
5. Legacy alias kullanımını kademeli azalt (yeni kod kanonik isim kullanır).
6. Inline literaller bittiğinde DM Sans'ı fonts URL'inden çıkar.
