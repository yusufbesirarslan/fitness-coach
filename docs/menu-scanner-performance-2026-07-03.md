# Menü Tarayıcı Performans Denetimi ve Optimizasyonu — 2026-07-03

Menü tarama hattının (`/api/proxy/scan-menu` → istemci → `/api/menu/analyze`)
uçtan uca izlenmesiyle bulunan darboğazlar ve uygulanan düzeltmeler. Tüm
değişiklikler API sözleşmesini korur (yalnızca ekleme alanlar); 1009 test yeşil.

## Denetim: darboğazlar (kanıt → maliyet)

| # | Darboğaz | Yer | Maliyet |
|---|----------|-----|---------|
| 1 | Alt-sayfa taraması SERİ + sayfa arası bilinçli `time.sleep(0.5–1.5s)` | `menu.py` scan route | 6 sayfada yalnız uykular 2.5–7.5 s; seri fetch ile en kötü ~40 s |
| 2 | Aynı HTML'in 3 kez BeautifulSoup parse edilmesi (framework_state, ana soup, full-body fallback) | `menu.py` + `menu_extract.py` | 3 MB'a varan sayfalarda saniyeler mertebesinde saf CPU |
| 3 | fw_state **zaten None iken** boş çıkarımda birebir aynı girdiyle İKİNCİ ağır LLM çağrısı (temperature=0 → aynı sonuç) | `menu.py` analyze | En pahalı Bedrock/Sonnet çağrısının sebepsiz 2×'i (~10–40 s) |
| 4 | FatSecret çözümlemesi öğe başına 1–2 HTTP araması, 80 öğeye kadar SERİ | `fatsecret.py:_lookup_macros_fatsecret` | En kötü ~160 ardışık istek × 5 s timeout |
| 5 | Porsiyon-ağırlığı LLM'i ile eksik-öğe makro LLM'i (bağımsız kümeler) art arda bekleniyor | `menu.py` analyze | İki ağır LLM turu: a+b yerine max(a,b) mümkün |
| 6 | Çıkarım sonucu kullanıcıdan bağımsız olduğu hâlde her analiz isteğinde yeniden LLM'e gidiliyor | `menu.py` analyze | Aynı restoranı tarayan her kullanıcı tam bedeli ödüyor |
| 7 | Makro önbelleği yalnızca süreç-içi dict (tek worker, restart'ta sıfır) | `foodcache.py` | Her deploy tüm birikimi siler; çok-worker'a ölçeklenmez |

Sorunsuz bulunanlar: tarama sonucu Redis önbelleği (6 s TTL), FatSecret bağlantı
havuzu + token önbelleği, LLM makro batch'lerinin 4-worker paralelliği, kesik-JSON
kurtarma, SSRF savunması, İngilizce'ye toplu çeviri (tek çağrı).

## Uygulanan değişiklikler

1. **Paralel alt-sayfa taraması** (`menu.py`): 6 alt sayfa `ThreadPoolExecutor`
   (≤4 worker) ile eşzamanlı; uykular kaldırıldı. Bölümler ve `crawl_errors`
   link-keşif sırasına göre deterministik birleşir.
2. **Tek HTML parse** (`menu.py`, `menu_extract.py`): ana soup bir kez kurulur;
   `_extract_framework_state(html, soup=...)` onu yeniden kullanır; full-body
   fallback aynı ağaçtan `noscript/svg` düşürerek üretilir.
3. **Gereksiz çıkarım tekrarının kaldırılması** (`menu.py`): fw_state'siz yeniden
   deneme yalnızca ilk deneme fw_state İLE yapılmışken çalışır.
4. **Paralel FatSecret çözümlemesi** (`fatsecret.py`): öğe başına arama gövdesi
   `_lookup_one`'a çekildi; ≤6 worker, `ex.map` girdi sırasını korur, öğe-başına
   hata izolasyonu aynen sürer. `social.py` çağrısı da otomatik yararlanır.
5. **Bağımsız LLM aşamalarının bindirilmesi** (`menu.py`): porsiyon-ağırlığı
   tahmini ile eksik-öğe makro tahmini eşzamanlı koşar (kümeler ayrık).
6. **Çıkarım önbelleği** (`menu.py`, `menu:extract:v1:*`): kategorize yemek
   listesi `sha256(menu_text, fw_state, headings, menu_source)` anahtarıyla
   Redis'te 6 saat tutulur; boş sonuç önbelleğe yazılmaz. Kullanıcıya özgü
   hedef/kalan/skor her istekte yeniden hesaplanır.
7. **foodcache Redis L2** (`foodcache.py`, `food:macros:v1:*`): çözülen makrolar
   yazma-geçişli olarak Redis'e de konur (vars. 3 gün, `FOODCACHE_REDIS_TTL`);
   L1 miss'leri tek `MGET` ile okunur. Redis yoksa/düşerse davranış birebir eski
   süreç-içi önbellek — tüm Redis hataları yutulur.
8. **Güven skoru** (`menu.py`): her öğeye ekleme `confidence` (0–1) ve
   `macro_source` alanı — `fatsecret_serving` 0.9 · `cache` 0.8 ·
   `fatsecret_scaled`/`llm_stated_grams` 0.7 · `llm` 0.6 ·
   `fatsecret_scaled_fallback` 0.45 · `none` 0.0; bant-kırpması güveni ≤0.5'e
   indirir. Mevcut istemciler bilmediği alanları yok sayar.

## Ölçülen / beklenen kazanımlar

Canlı doğrulama (gerçek site, BigChefs menüsü, bu ortamdan):

- İlk tarama: **17.5 s**, 5 alt sayfa paralel (loglarda tamamlanma sırası
  3→4→5→2→6), 375 bölüm, `crawl_errors: null`. Eski akışta aynı tarama
  uykular + seri fetch ile kabaca **25–40 s** olurdu.
- Tekrar tarama: **24 ms** (Redis HIT — mevcut davranış, korunuyor).
- Analyze, LLM anahtarı yokken yapısal 422 döner (500 değil); fw_state'siz
  istekte loglarda tek çıkarım denemesi (yeniden deneme satırı 0 kez).

Beklenen (prod, anahtarlarla):

- `analyze` FatSecret aşaması: 80 öğede seri ~80–160 istek → ~6× kısalır.
- İki LLM aşamasının bindirilmesi: tipik 2 ağır turdan ~1 tur tasarrufu.
- Aynı menünün ikinci analizi: çıkarım LLM'i tamamen atlanır; makrolar da
  Redis L2'den gelirse FatSecret+LLM tümüyle atlanır (log: "All items served
  from cache").
- fw_state'siz başarısız çıkarımda Bedrock maliyeti yarıya iner.

## Sonraki adımlar (bilinçli olarak bu kapsamda YAPILMADI)

- **Koç yemek araması N+1 LLM'i**: `_food_search_fatsecret` sonuç başına
  `_estimate_serving_weights_llm([tek ad])` çağırıyor (8 sonuçta 8 LLM çağrısı).
  Menü hattı değil; ayrı iş olarak toplu çağrıya çevrilmeli.
- **Genişletilmiş besinler** (lif/şeker/doymuş yağ/sodyum): FatSecret arama
  açıklaması yalnız kal/P/K/Y taşıyor; alanları eklemek öğe başına `food.get`
  (HTTP 2×) ya da LLM şeması genişletmesi ister — maliyet/tutarlılık kararı
  ürün tarafında verilmeli.
- **AI uçlarını ayrı worker/queue'ya taşıma**: tek gunicorn worker × 8 thread
  hâlâ senkron AI çağrılarıyla dolabilir (CLAUDE.md operasyon notu). Bu değişiklik
  istek başına süreyi düşürerek riski azaltır ama mimari çözüm değildir.
- **Scan→analyze el sıkışması**: 40 kB body_text istemciye gidip aynen geri
  geliyor; tarama yanıtına bir `scan_id` ekleyip analyze'ın Redis'ten okuması
  bant genişliğini düşürür (geriye uyumlu eklenebilir).
