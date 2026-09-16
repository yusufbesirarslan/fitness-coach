# AxisAI Design System (v1 — Phase 1)

Tek kanonik stil kaynağı. Bu doküman **her token'ı ve bileşeni** tanımlar;
yeni UI kodu yazan herkes (insan veya ajan) önce burayı okumalı.

## Mimari

```
templates/_head.html          ← her sayfada, sayfa CSS'lerinden ÖNCE:
 ├─ Google Fonts (Inter + Bebas Neue)
 ├─ static/tokens.css         ← 1) TÜM design token'ları (tek kaynak)
 └─ static/components.css     ← 2) yeniden kullanılabilir bileşenler
sayfa CSS'i                   ← 3) theme.css | style.css (+ nav/dashboard/coach_widget)
sayfa <style nonce> bloğu     ← 4) sayfaya özel kurallar (en son, eşitlikte kazanır)
```

- **Kaskad sözleşmesi:** sayfa CSS'i ve inline blok, bileşen kurallarını eşit
  özgüllükte *bilinçli olarak* geçersiz kılabilir (yükleme sırası korunmalı).
- **tokens.css** yalnızca custom property tanımlar; hiçbir görsel kural içermez.
- **components.css** yalnızca bileşen sınıfları içerir; sayfa/feature deseni
  (`.meal-log-card`, `.plan-card`…) `theme.css`'te kalır.
- `style.css` yalnızca auth/onboarding sayfalarında yüklenir (landing, login,
  register, setup, verify) ve canlı light/dark anahtarı taşır.

### İsimlendirme politikası

- **Yeni kod yalnızca kanonik isimleri kullanır:** `--color-*`, `--space-*`,
  `--radius-*`, `--text-*`, `--weight-*`, `--leading-*`, `--duration-*`,
  `--ease-*`, `--elevation-*`, `--icon-*`, `--z-*`, `--overlay-*`.
- **Legacy alias'lar** (`--volt*`, `--accent*`, `--bg2`, `--s1..--s10`,
  `--r-*`, `--t-*`…) yalnızca mevcut kodun kırılmaması için yaşar; tokens.css
  sonundaki alias bloğunda kanonik token'lara bağlıdır. Yeni kodda KULLANMA.
  Fazlar ilerledikçe kullanım azaltılıp kaldırılacaklar.

### Tema (Light/Dark hazırlığı)

Semantik token'lar `:root`'ta dark değerleriyle tanımlı;
`[data-theme="light"]` bloğu yalnızca semantik katmanı override eder.
Alias'lar semantik token'lara baktığı için otomatik döner. Bugün tüm sayfalar
`<html data-theme="dark">` basar; light tema auth sayfalarındaki toggle ile
canlıdır (`.theme-toggle`, static/actions.js). Yeni renk eklerken **iki temaya
da** değer ver.

## Token Referansı (static/tokens.css)

### Renk — semantik (yeni kodun kullanacağı katman)

| Token | Dark | Light | Kullanım |
|---|---|---|---|
| `--color-primary` | `#3D8BFF` | `#1A66D0` | Marka vurgusu, CTA, aktif durum |
| `--color-primary-strong` | `#1E6FE0` | `#1550B8` | Hover/pressed primary |
| `--color-primary-soft` | `rgba(61,139,255,.09)` | `rgba(26,102,208,.10)` | Seçili zemin, banner |
| `--color-primary-glow` | `rgba(61,139,255,.30)` | `rgba(26,102,208,.30)` | Vurgu kenarlığı |
| `--color-on-primary` | `#121212` | `#FFFFFF` | Primary dolgu üstü metin/ikon |
| `--color-bg` | `#121212` | `#F5F5F0` | Sayfa zemini |
| `--color-surface-1` | `#1A1A1A` | `#EEEDE8` | Alçak yüzey (input zemini dark'ta) |
| `--color-surface-2` | `#1E1E1E` | `#FFFFFF` | Kart yüzeyi |
| `--color-surface-3` | `#252525` | `#E5E4DF` | Yükseltilmiş/iç yüzey |
| `--color-text-1..4` | `#F4F4F4 #A6A6A6 #909090 #4D4D4D` | `#1A1A1A #5C5C5C #676767 #A3A3A3` | Metin hiyerarşisi (text-3 AA normal text on card surfaces) |
| `--color-success(-soft)` | `#00C48C` | aynı | Başarı |
| `--color-warning(-soft)` | `#FFB020` | `#C89800` | Uyarı |
| `--color-danger(-soft)` | `#FF4D4D` | `#DD4444` | Hata/yıkıcı eylem |
| `--color-info(-soft)` | `#007BFF` | `#2A70C0` | Bilgi/ikincil mavi |
| `--color-border-1` | `rgba(255,255,255,.07)` | `rgba(0,0,0,.10)` | Hairline kenarlık |
| `--color-border-2` | `rgba(255,255,255,.13)` | `rgba(0,0,0,.18)` | Hover/vurgu kenarlığı |
| `--color-border-solid` | `#242424` | `#D5D4CF` | Auth sayfalarının katı kenarlığı (mirası) |
| `--overlay-2/-3/-4/-6/-10` | beyaz %2–10 | siyah %2–10 | Hover/active zeminleri |

Primitives (`--gray-*`, `--blue-*`, `--red-*`…) yalnızca tokens.css içinde
semantik token tanımlamak içindir; sayfa kodunda kullanma (tek istisna:
renkli dolgu üstünde tema'dan bağımsız koyu metin → `var(--gray-950)`).

### Tipografi

- Aileler: `--font-sans` = **Inter** (sistem yedekleri) · `--font-display` =
  **Bebas Neue** · `--font-body` = `var(--font-sans)`.
  Inter ağırlıkları 300–800 yüklü (_head.html Google Fonts).
- Boyutlar: `--text-2xs..--text-3xl` → 10 / 11 / 12 / 13 / 14 / 15 / 17 / 20 / 24 px;
  akışkan display: `--text-display-sm/md/lg` → clamp 28-36 / 32-44 / 38-52 px.
- **Metrik ölçeği (PR2)**: `--text-metric-sm/md/lg` → clamp 22-26 / 28-34 /
  34-44 px. Veri-öncelikli yüzeylerde BİRİNCİL sayı/sonuç bu üç rolden birini
  seçer — sayfa tek-seferlik `36px`/`20px` değeri UYDURMAZ:
  `lg` = yüzeyin tek hakim sayısı (`.stat-value`), `md` = bölüm düzeyi başlık
  metrik (`.ps-state`, `.wh-focus`, `.hero-num`), `sm` = kart düzeyi metrik
  (`.wc-value`, `.wt-big`, `.ach-title`).
  Hiyerarşi merdiveni: `--text-display-*` > `--text-metric-*` > bölüm başlığı >
  gövde > metadata.
- Ağırlıklar: `--weight-light..--weight-extrabold` → 300–800.
- Satır yüksekliği: `--leading-none/headline/tight/snug/normal/relaxed` →
  1 / 1.1 / 1.2 / 1.4 / 1.6 / 1.75. `--leading-headline` sayfa başlığı içindir:
  skala 1 → 1.2 arasında boştu, başlık ya kendi alt uzantılarına oturuyor ya da
  gövde metni gibi okunuyordu.
- Harf aralığı: `--tracking-tight/wide/wider/widest/label` →
  -.01 / .04 / .08 / .12 / .16 em (uppercase mikro etiketler `--tracking-label`).
  `--tracking-tight` YALNIZCA büyük punto optik düzeltmesidir (başlık); gövde
  veya mikro etikette KULLANMA.

### Boşluk (8-pt grid)

`--space-1..--space-20` → 4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 80 px.
4 px yarım adım serbest; grid dışı tarihsel değerler (13/14/18 px) Phase 2+'ta
grid'e çekilecek.

### Radius / Kenarlık / Gölge / Opaklık / İkon

- Radius: `--radius-xs/sm/md/lg/xl/full` → 4 / 8 / 12 / 16 / 24 / 9999 px
  (badge · chip · buton-input · kart · sheet · pill-avatar).
- Kenarlık kalınlığı: `--border-w-1/2/3` → 1 / 1.5 / 2 px.
- Elevation: `--elevation-1/2/3` (yumuşak siyah gölgeler; light'ta hafifler),
  `--shadow-primary` (mavi vurgu).
- Opaklık: `--opacity-disabled` .65 · `--opacity-muted` .7 · `--opacity-faint` .85.
- İkon: `--icon-xs/sm/md/lg/xl` → 14 / 16 / 18 / 22 / 26 px (stroke ~1.8).

### Odak (tek dil — WEB-UX4-PR2)

Ürünün **TEK** odak dili 2 px solid primary halkadır. İki sözdizimsel rolü var
ve ikisi de aynı görünür:

- `--focus-ring` → **box-shadow DEĞERİ** (`0 0 0 2px var(--color-primary)`).
  `box-shadow: var(--focus-ring);` biçiminde tüketilir.
- `outline` gereken yerde (overflow kırpması riski olan kaplar) doğrudan
  `outline: 2px solid var(--color-primary); outline-offset: 2px;` yazılır.

**`outline: var(--focus-ring)` YASAKTIR.** `--focus-ring` bir box-shadow
değeridir; `outline` içinde kullanıldığında computed-value anında geçersiz olur,
`var()` parse-time denetimini atlattığı için kural sessizce `outline: none`'a
düşer ve kaskadın bir önceki kazananına da DÖNMEZ. Bu, beş kontrolün (meal
edit/delete, quick-add, log FAB, slot-empty) odağını tamamen silmişti.
Bir regresyon testi bu kullanımı repo genelinde yasaklar.

Kontrast: halka `--color-bg` / `--color-surface-1/2/3` karşısında dark temada
4.62–5.65 : 1, light temada 4.28–5.45 : 1 ölçer (WCAG 2.2 SC 1.4.11 eşiği 3 : 1).
Yeni bir odak token'ı EKLEME — tek token, tek dil.

### Hareket

- Süre: `--duration-fast/base/slow/slower` → 140 / 200 / 280 / 320 ms.
- Easing: `--ease-standard` (genel), `--ease-out-expo` (giriş/spring hissi),
  `--ease-out-quint` (drawer/sheet), `--ease-bounce` (oyunsu geri tepme).
- Kullanım: `transition: color var(--duration-fast) var(--ease-standard);`
- `prefers-reduced-motion` kuralları components.css'te (app) ve style.css'te
  (auth) — yeni dekoratif animasyonları oraya ekle.

### Z-index

`--z-header` 100 · `--z-drawer-backdrop` 199 · `--z-drawer` 200 · `--z-fab` 200
· `--z-overlay` 300 (modal/sheet/loading) · `--z-toast` 400. Yeni katman
eklemeden önce bu skalaya oturt. (`--z-drawer*` ve `--drawer-w` Phase 2'de
çekmece kaldırılınca kullanım dışı kaldı; token'lar miras olarak duruyor.)

### Layout & Breakpoint'ler

`--content-max` 1280 px · `--header-h` 56 · `--action-bar-h` 68 · `--drawer-w` 280
· `--fab-btn` 56 · `--fab-protrude` 22 · `--fab-size` = `var(--fab-btn)` (legacy alias).
- **Yüzen FAB rayı (PR2)**: `--fab-btn` 56 · `--fab-rail-inset` 15 (≥1024px'te
  36) · `--fab-rail-h` = ikisinin toplamı. Koç FAB'ı (`#cw-root`) ve beslenme
  log FAB'ı konumlarını bu inset'ten alır ve boyutlarını `--fab-btn`'den
  türetir; `.page-body` (nav.css) sayfa dibinde `--fab-rail-h` kadar yer
  AYIRIR — yoksa sayfanın son satırı butonun altında kalır. Yeni yüzen kontrol
  eklerken üçünü de bu token'lardan türet.

| Breakpoint | Değer | Kullanım |
|---|---|---|
| `--bp-sm` | 520px | dar telefon kırılımları |
| `--bp-md` | 640px | telefon → tablet |
| `--bp-lg` | 768px | tablet |
| `--bp-xl` | 1024px | masaüstü |
| `--bp-2xl` | 1280px | içerik tavanı |

⚠️ CSS değişkenleri `@media` sorgusunda ÇALIŞMAZ — media query yazarken bu
tabloyla senkron kal (tarihsel 900px sorguları Phase 2'de 1024'e çekilecek).

## Bileşen Kataloğu (static/components.css)

Mevcut sınıf adları kamu API'sidir — yeniden adlandırma yok. Kullanım örnekleri:

| Bileşen | Sınıflar | Örnek |
|---|---|---|
| Button | `.btn-volt` (primary), `.btn-ghost` (ikincil), `.btn-danger` (yıkıcı), mod: `.w-full`, `.loading` | `<button class="btn-volt">KAYDET</button>` |
| Input | `.fc-input` (+ `.field` > `.field-label`) | `<div class="field"><label class="field-label">AD</label><input class="fc-input"></div>` |
| Card | `.card` (kendi `--space-5` padding'ini TAŞIR), hover için ek `.card-hover`, padding'i kendi yöneten kartlar için `.card-flush` | `<div class="card card-hover">…</div>` |
| Modal | `.modal-backdrop` > `.modal` > `.modal-header/-title/-close/-body/-footer` | `AxisModal.open(backdrop)` / `AxisModal.close(backdrop)` — aşağıya bak |
| Bottom Sheet | `.sheet-backdrop.open` > `.sheet` > `.sheet-handle` + `.sheet-title` | mobilde alttan, ≥768px ortalanır |
| Badge | `.badge` + `.badge-primary/-success/-warning/-danger/-neutral` | `<span class="badge badge-success">AKTİF</span>` |
| Chip | `.chip(.selected)` > `.chip-dot` | seçilebilir filtre/besin etiketi |
| Avatar | `.avatar` + `.avatar-sm/-md/-lg/-xl` (28/34/48/64) | `<div class="avatar avatar-md">YA</div>` veya içine `<img>` |
| Progress Ring | `.ring-wrap` > `.ring-svg` (`.ring-track` + `.ring-fill`) + `.ring-label` | SVG dairesel ilerleme |
| Progress Bar | `.pbar-track` > `.pbar-fill` | genişlik JS ile |
| Navigation Item | `.tab-bar` > `.tab-btn(.active)`, panel: `.tab-panel(.active)` | sayfa içi sekmeler (uygulama kabuğu gezinmesi nav.css'te — aşağıdaki bölüm) |
| FAB | `.quick-add-wrap` > `.quick-add-btn(.open)` + `.quick-add-actions` > `.fab-row` > `.fab-sub` + `.fab-lbl` | global hızlı ekleme |
| Icon Tile | `.icon-tile` (+ `.icon-tile-sm`, `.icon-tile-accent`, `.icon-tile-soft`) > `<svg>`; metin hizasındaki ikon için `.icon-inline` | arayüz ikonunun TEK kutusu — emoji KULLANMA |
| Empty State | `.empty-state` > `.empty-icon` + `.empty-title` + `.empty-sub` | boş liste durumu |
| Loading Skeleton | `.skeleton` (+ `.skeleton-text`, `.skeleton-circle`) | boyutu yerinde ver |
| Stat Card | `.stat-card` > `.stat-label` + `.stat-value` + `.stat-unit` | metrik kartı |
| Section Header | `.sec-label` (çizgili mikro başlık), `.cat-label` | bölüm ayırıcı |
| Toast | `.toast-wrap` > `.toast.toast-success/-error/-info` (+ `.hide`) | JS üretir |
| Loading Overlay | `.loading-overlay.active` > `.loading-spinner` + `.loading-text` | tam ekran bekleme |
| Divider | `.fc-divider` > `.fc-divider-line` + `.fc-divider-lbl` | etiketli ayraç |

Yenileri (Modal, Sheet, Badge, Avatar) Phase 1'de tanımlandı; sayfalar henüz
ad-hoc kopyalarını kullanıyor — sayfa fazlarında bunlara geçirilecek.

### Kanonik buton ailesi (WEB-UX4-PR2)

Üç rütbe, **TEK** geometri. Ortak sözleşme: `min-height: 44px` (deponun mevcut
dokunma hedefi standardı), `--radius-md`, `--space-3`/`--space-6` iç boşluk,
AÇIK `font-family` ve tam `hover · pressed · focus-visible · disabled ·
loading` kapsaması.

| Sınıf | Rütbe | Dolgu | Yazı yüzü |
|---|---|---|---|
| `.btn-volt` | BİRİNCİL | dolu `--color-primary` | `--font-display` (Bebas) |
| `.btn-ghost` | İKİNCİL | şeffaf + hairline kenarlık | `--font-body` (Inter) |
| `.btn-danger` | YIKICI | şeffaf + `--color-danger` kenarlık ve metin | `--font-body` (Inter) |

- **`font-family` zorunludur.** `<button>` sayfa yazı tipini MİRAS ALMAZ —
  Chromium UA sayfası form kontrollerine `font: 400 13.333px Arial` basar.
  `.btn-ghost` bu yüzden `<button>` iken Arial, `<a>` iken Inter render
  ediyordu. `components.css` artık `button, input, select, textarea, optgroup`
  için `font-family: inherit` normalizasyonu yapar; yalnızca AİLE normalize
  edilir, boyut/ağırlık bileşende kalır.
- **`.btn-danger` "renk değişmiş primary" DEĞİLDİR.** Dolu kırmızı zemin
  `--white` metni 3.27 : 1'e düşürüyordu; anahatlı muamele hem rütbeyi ayırır
  hem 4.69–5.73 : 1 ölçer. Hover bir FILL değil `inset` halkadır — dolgu,
  metnin kendi zeminini boyayıp kontrastı 4.07 : 1'e düşürürdü.

### Modal/Sheet davranışı (`static/modal.js` — WEB-UX4-PR2)

CSS primitive'i Phase 1'den beri vardı ama DAVRANIŞI yoktu; ürün bu yüzden 21
ayrı `role="dialog"` yüzeyi üretti (1 odak tuzağı, 3 odak iadesi, 2 `inert`).
`modal.js` YALNIZCA mevcut primitive'i tamamlar — bileşen çerçevesi değildir:
kayıt defteri, otomatik bağlama, MutationObserver veya polling YOKTUR.

```js
AxisModal.open(backdropEl, {
  dismissible: true,      // false → Escape kapatmaz (yıkıcı onaylar)
  initialFocus: '#name',  // Element | seçici | yoksa ilk odaklanabilir
  onClose: fn
});
AxisModal.close(backdropEl);
AxisModal.isOpen(backdropEl);
```

Garantiler: açılışta odak içeri · `Tab`/`Shift+Tab` sarmalama · politikaya
saygılı `Escape` · `inert` ile arka plan yalıtımı · açan öğeye odak iadesi
(öğe hâlâ kullanılabilirse; değilse `main`'e) · iç içe diyaloglarda LIFO
temizlik · kapanıştan sonra bağlı handler BIRAKMAZ.

PR2 hiçbir ürün diyaloğunu taşımaz; her yolculuk PR'ı kendi diyaloglarını
taşır. Sözleşme `tests/test_ux4_pr2_foundations_browser.py` ile korunur.

## Uygulama Kabuğu (static/nav.css — Phase 2)

Tüm kimlikli sayfalar iki ortak parçayı include eder; sayfada satır-içi nav
markup'ı YASAK (regresyon: `tests/test_app_shell.py`):

```jinja
{% set nav_active = 'home' %}   {# home | nutrition | training | progress | profile #}
{% include "_nav.html" %}       {# üst başlık: marka + masaüstü sekmeleri + avatar #}
...sayfa içeriği (<main class="main-content">)...
{% include "_actionbar.html" %} {# alt sekme çubuğu (5 sekme, <1024px) #}
```

- **Sekmeler:** Ana Sayfa `/` · Beslenme `/nutrition` · Antrenman `/training`
  · İlerleme `/progress-page` · Profil `/edit-profile`.
- **İkincil sayfalar** (friends, chat, feed, leaderboard, quests, supplements,
  premium, pump-check-gallery) `nav_active = 'profile'` işaretler ve Profil
  sayfasındaki **hub**'dan erişilir (`.hub` > `.hub-section-label` +
  `.hub-card` > `.hub-link` / `.hub-row`; dil anahtarı `.hub-lang(-opt)`,
  çıkış `.hub-link-danger`, premium `.hub-link-premium`).
- **Kırılım davranışı:** <1024px alt çubuk (`.action-bar` > `.ab-tab`,
  safe-area `env(safe-area-inset-bottom)` — `_head.html` viewport'u
  `viewport-fit=cover`); ≥1024px alt çubuk gizlenir, başlıkta yatay sekmeler
  görünür (`.header-nav` > `.hn-link`).
- **Sınıflar kamu API'sidir:** `.global-header .header-brand .header-avatar
  .header-nav .hn-link .action-bar .ab-tab .hub-*` — yeniden adlandırma yok.
- **Sayfa geçişi:** `.main-content` `page-enter` animasyonuyla girer
  (reduced-motion'da kapalı). Alt boşluk tek yerden gelir: `.page-body`
  (nav.css) sabit çubuk + safe-area payını verir; sayfalar kendi
  padding-bottom hack'ini EKLEMEZ.
- **Çekmece (drawer) kaldırıldı** (v3 → v4): `static/nav.js` silindi, kabuk
  JS'siz çalışır. Aktif sekme `aria-current="page"` taşır.

### Koç hedefi (WEB-UX4-PR3)

Coach dört birincil hedeften biridir, bu yüzden kanonik `/coach` sayfasında
sohbet **sayfa içeriğidir** — üstüne yüzen bir destek penceresi değil. Aynı
widget, aynı `#cw-root`, aynı composer/stream/`/coach/history` hidrasyonu:
`static/coach_widget.js` TEK Coach uygulaması olmayı sürdürür. Değişen tek şey
sunumdur ve host'un iki bildirimsel kancasıyla açılır (launcher'ın `data-coach-
launcher` deseninin aynısı; kancasız her host eskisi gibi yüzer):

| Kanca | Kim koyar | Ne yapar |
| --- | --- | --- |
| `data-coach-mount` | `coach_v2.html` (`<main>` içindeki sütun) | Tek ağacın NEREYE eklendiği — `<body>`nin dibi yerine içerik sütunu. |
| `data-coach-destination` | `coach_v2.html` `<body>` | NASIL sunulduğu: pencere hep açık, `inert` değil, `toggle()` no-op, `#cw-header` gizli. |

- **Sayfa akışı:** `body.page-body[data-coach-destination]` viewport'un TAM
  kendisidir (`height: 100vh; height: 100dvh; overflow: hidden`) ve dikey flex
  kolondur; taşma `#cw-msgs`'e aittir. Sayfanın kendisi kaydırılsaydı composer
  telefonda katlamanın altına inerdi. dvh'siz tarayıcı ikinci bildirimi düşürür
  ve 100vh'de kalır — kap yine vardır.
- **Katman:** `#cw-root` → `--z-fab`, `#cw-notify` → `--z-toast`. Ham 9998/9999
  gitti; Coach artık toast'ın ÜSTÜNE çıkamaz. Hedef modunda `#cw-root`
  `position: static` olur, yani katmana hiç girmez.
- **`#cw-notify` DAİMA `<body>`ye eklenir**, mount'a asla: `position: fixed` bir
  düğüm, dönüştürülmüş bir atanın (`.main-content` `page-enter`) içindeyken o
  ataya göre konumlanır ve animasyon boyunca rayından kayardı.
- **Klavye:** DOM sırası = sekme sırası. Composer artık satırın BAŞINDA
  (`#cw-input` → `#cw-qr` → `#cw-send`); hedefte launcher/kapat yok, yani
  görünmez odak durağı da yok. Pozitif `tabindex` hiçbir yerde yok.
- **Tipografi/renk:** gövde `--text-base`/`--weight-regular`, composer
  `--text-xl` (17 px — iOS'un 16 px odak-zoom eşiğinin üstündeki EN KÜÇÜK
  mevcut token; yeni token EKLENMEDİ). Ham hex kalmadı.
- **Geri alma:** `UIUX_COACH_PAGE_V2_ENABLED=0` → `templates/coach.html` +
  `.coach-page*` (nav.css) birebir eskisi gibi render eder. Bayrak adı,
  varsayılanı ve okuma yolu DEĞİŞMEDİ.
- **Regresyon:** `tests/test_ux4_pr3_coach_contract.py` (kaynak sözleşmesi) +
  `tests/test_ux4_pr3_coach_destination_browser.py` (ölçülmüş geometri, gerçek
  Tab yürüyüşü, 320–1366 × TR/EN taşma matrisi) + `tests/test_coach_page_v2.py`.

### Plan ilk kurulum (WEB-UX4-PR4)

Aktif planı olmayan kullanıcı Plan'a geldiğinde ilk gördüğü şey artık sekiz
soruluk bir form değil, **koçluk yapılan bir ilk adımdır**: sonuç → temel
tercihler → isteğe bağlı ayar → güvenlik notu → TEK eylem. Yalnızca sunum
değişti; `plan_manage` iş akışı, 11 anahtarlı tercih yükü, varsayılanlar,
`POST /training-plan` → öneri → `GET /training/bootstrap` tazelik okuması →
`POST /training-plan/save` → kanonik yenileme zinciri ve `management_baseline`
birebir aynıdır (`static/training_plan_management.js` DEĞİŞMEDİ).

| Katman | Ne | Neden |
| --- | --- | --- |
| Sonuç | `plan.create.outcome` + `plan.create.defaults_hint`, ilk alandan ÖNCE | Kullanıcı neyin kurulacağını, kaydetmeden önce inceleyeceğini ve varsayılanların yeterli olduğunu parametreye dokunmadan öğrenir. |
| Temel | Hedef, ekipman, haftalık gün, süre | Bir haftayı şekillendiren dört cevap. ≥360 px'te kısa etiketli gün/süre çifti yan yana; uzun etiketli hedef/ekipman telefonda tam satır (kapalı `<select>` metni kırpılmaz). ≥641 px'te hepsi ikişerli. |
| İsteğe bağlı | Yerel `<details class="plan-refine">`: tarz, odak bölge, kardiyo + kombinasyon uyarısı | Kapalıyken alanlar sekme sırasında YOK ama değerlerini korur ve gönderimde okunur; açıp kapamak yükü değiştirmez. Sihirbaz/adım makinesi/taslak depolama YOK. |
| Güvenlik | `data-plan-field="injuries"` (maxlength 200, boş) | Açıklamanın DIŞINDA, her zaman görünür. |
| Eylem | `.plan-manage-action` içinde TEK `[data-plan-manage-generate]` + `[data-plan-manage-msg]` | `position: sticky`; alt gezinmenin (`--action-bar-h`) ve safe-area'nın üstünde kalır, form kaydıkça erişilebilir, sonra yerine oturur — takip eden öneri panelinin üstünde ASLA asılı kalmaz. Durum/hata satırı eylemle aynı blokta olduğundan tıklamanın cevabı hiçbir zaman eylemin altında kalmaz. |

- **Kapsam:** her kural `[data-manage-state="create"]` ya da yalnızca create
  dalının render ettiği sınıflara bağlıdır. Yeniden oluşturma (regenerate)
  formu, sırası ve durum satırı UX-3 PR3'teki gibi düz kalır; aktif plan,
  read_error, Beslenme ve Takviyeler piksel olarak değişmedi (390/1366 belge
  yükseklikleri birebir aynı).
- **Odak:** odağı alan alan `scroll-margin-bottom` ile yapışkan eylemin ve alt
  gezinmenin üstüne kaydırılır. Pozitif `tabindex` yok; özet (`summary`)
  `outline: 2px solid var(--color-primary)` taşır.
- **F-14:** Plan hedefinin `<h1>`'i ve `<title>`'ı gezinmenin kendi etiketini
  (`nav.plan`) kullanır. Antrenman/Beslenme/Takviyeler alt-alan başlıkları
  olarak kalır. `plan.title` ve `plan.create.intro` tek tüketicileri gittiği
  için katalogdan çıkarıldı (TR/EN eşliği korunur).
- **Bilinen, BİLEREK çözülmeyen tutarsızlık:** dokunulmamış Plan V2 formu
  `sure=30` gönderir (süre `<select>`'inde seçili seçenek yok → ilk seçenek),
  oysa `plan_training_manage.js` `DEFAULTS`, legacy Training, sunucu
  `TrainingPreferences` ve tercih sözleşmesi yedeği 45 der. 30 → 45 üretici
  girdisini değiştirir; bu yüzden PR4 davranışı KORUR ve testle karakterize
  eder. Ayrı, dar bir takip işi olarak kayıtlıdır.
- **Geri alma:** `git revert`. `UIUX_PLAN_V2_ENABLED` daha geniş Plan geri alma
  seçicisi olarak kalır; bu PR hiçbir bayrağa dokunmaz.
- **Regresyon:** `tests/test_ux4_pr4_plan_first_run_contract.py` (render
  sözleşmesi, yük/otorite/depolama/CSS kapıları) +
  `tests/test_ux4_pr4_plan_first_run_browser.py` (gerçek yük, ağ sırası, ilk
  ekran geometrisi, gerçek Tab yürüyüşü, yapışkan eylem, 320–1366 × TR/EN
  taşma matrisi, yeniden oluşturma regresyonu).

## Genişletme Rehberi

1. **Yeni token:** tokens.css'te doğru bölüme kanonik adla ekle; light değeri
   varsa `[data-theme="light"]` bloğuna da ekle; bu dokümandaki tabloyu güncelle.
   Legacy alias EKLEME.
2. **Yeni bileşen:** components.css'e yalnızca token tüketen kurallar yaz;
   `tests/test_design_system.py`'deki selektör listesine ekle; katalog tablosunu
   güncelle. Sayfaya özgü desense components.css'e değil theme.css'e/sayfaya koy.
3. **CSP:** şablona satır-içi `<style>`/`<script>` eklerken
   `nonce="{{ csp_nonce }}"` zorunlu (app/hooks.py); harici stil yalnızca
   fonts.googleapis.com.
4. **Regresyon korumaları:** `tests/test_design_system.py` — _head.html
   kablosu, token sözleşmesi, bileşen envanteri.

## Bilinen sapmalar / Phase 3+ TODO

- `.global-header`/`.action-bar` zeminleri yarı saydam rgba literalleridir
  (blur zemini için token yok) — bilinçli istisna, nav.css'te not düşüldü.
- ✅ `dashboard.css` Phase 3'te tamamen kanonik token'lara taşındı (ara-gri
  palet kaldırıldı); ana sayfa kabuğu artık `.card`/`.ring-*`/`.pbar-*`/
  `.avatar`/`.badge`/`.sec-label` bileşenlerini yeniden kullanır.
- ✅ `coach_widget.css`'in kendi ara-gri paleti (`#808088`, `#505058`, `#2A2A2A`…)
  WEB-UX4-PR3'te kaldırıldı: stylesheet'te ham hex KALMADI, her değer
  `--color-text-*`/`--surface-*`/`--color-border-*`'ten gelir.
- ✅ Eski lime kalıntıları (avatar gradyanı ve `#cw-send:hover`) WEB-UX4-PR3'te
  kanonik `--color-primary` / `--color-primary-strong` çiftine taşındı;
  `--color-chat-avatar-accent` ve `--color-chat-send-hover` token'ları
  `tokens.css`'ten SİLİNDİ (referanssız bırakılmadı). Bir regresyon testi
  `static/**/*.css` içinde iki hex'i de ve iki token adını da reddeder.
- Auth sayfaları katı `--color-border-solid` kenarlık kullanır; hairline'a
  birleştirme Phase 3+.
- Chart.js konfiglerindeki renk/font literalleri (progress/index) JS içinde —
  tokenizasyon dışı bırakıldı.
- Grid dışı tarihsel boşluk/boyut değerleri (7/9/13/14/18 px) görsel değişiklik
  yasağı nedeniyle korundu.
