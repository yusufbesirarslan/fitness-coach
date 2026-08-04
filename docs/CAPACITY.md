# Kapasite, aşırı yük ve kurtarma (Hardening PR4)

Bu belge FitX'in **desteklenen üretim topolojisini**, thread/izin/havuz
aritmetiğini, yol-başına aşırı-yük davranışını ve geri alma yordamını tek yerde
toplar. PR4 yeni bir eşzamanlılık mekanizması KURMAZ: PR #199 ile gelen
mimariyi tamamlar, ölçülebilir kılar ve boot'ta doğrular.

İlgili: `docs/OBSERVABILITY.md` (metrik altyapısı), `docs/ROLLOUT.md` (bayrak
rollout'u), `docs/AUTH_CONTRACT.md` (kimlik sınırı).

---

## 1. Desteklenen topoloji

| Bileşen | Değer | Kaynak |
|---|---|---|
| Gunicorn worker | **1** | `gunicorn.conf.py` (`FITX_WEB_WORKERS`) |
| Gunicorn thread | **8** | `gunicorn.conf.py` (`FITX_WEB_THREADS`) |
| Paylaşılan bloklayıcı izin (`_ai_slots`) | 4 | `AI_MAX_CONCURRENCY` |
| Model izni (`_model_slots`, iç içe) | 4 | `AI_MODEL_MAX_CONCURRENCY` |
| Scrape izni (`_scrape_slots`) | 2 | `SCRAPE_MAX_CONCURRENCY` |
| DB havuzu | 8 + 4 taşma | `DB_POOL_SIZE`, `DB_POOL_MAX_OVERFLOW` |
| DB checkout beklemesi | 10 sn | `DB_POOL_TIMEOUT_SECONDS` |

**Tek worker BİR TASARIM KARARIDIR, tesadüf değil.** Kapılar, in-memory cache ve
limiter yedeği süreç-içidir; ikinci bir worker her sınırı worker-başına uygular ve
rezerv garantisini sessizce ikiye böler. `enforce_gate_invariants` bu yüzden
`FITX_WEB_WORKERS != 1` durumunda üretimde boot'u DURDURUR (dev'de uyarır).
Worker sayısını artırmak istiyorsan doğru çözüm AI uçlarını ayrı bir
worker/kuyruğa taşımaktır — bayrağı çevirmek değil.

## 2. Kapasite formülü

```
bloklayıcı tavan = AI_MAX_CONCURRENCY
                 + SCRAPE_MAX_CONCURRENCY
                 + max(0, AI_MODEL_MAX_CONCURRENCY - AI_MAX_CONCURRENCY)

rezerv           = FITX_WEB_THREADS - bloklayıcı tavan
şart             = rezerv >= 2                       (THREAD_RESERVE_MIN)
şart             = DB_POOL_SIZE + DB_POOL_MAX_OVERFLOW >= FITX_WEB_THREADS
```

Varsayılanlarla: `4 + 2 + 0 = 6`, rezerv `8 - 6 = 2` ✓; havuz `8 + 4 = 12 ≥ 8` ✓.

**Model fazlalığı neden sayılır?** Model izinleri bugün her zaman bir route
kapısının ya da `blocking_concurrency_slot`'un İÇİNDE alınır, yani ek thread
talebi yaratmazlar. Ama `AI_MODEL_MAX_CONCURRENCY` AYRI bir düğmedir: dış kapıdan
büyük bırakılırsa, iç içe geçme ileride herhangi bir yerde bozulduğu anda fazlalık
kadar EK thread park edilebilir. Invariant bu yüzden bugünkü çağrı grafiğine
değil, yapılandırmanın kendisine bakar.

**Rezerv neden 2?** `/health` (Docker HEALTHCHECK, 30 sn'de bir) ve deploy'un
`/health?deep=1` kapısı asla AI yüküne kuyruklanmamalıdır. Kuyruklanırsa
konteyner restart-loop'una girer veya deploy sağlıklı bir sürümü **sahte
rollback** ile geri alır — yani yük, kod arızası gibi görünür.

**Havuz neden thread sayısını karşılamalı?** Bir istek thread'i işini yaparken bir
DB bağlantısı tutar. Havuz thread sayısının altındaysa kapılar sağlayıcı
beklemesini sınırlasa bile rezerv edilen thread'ler `/health`'i yine servis
edemez — rezerv kâğıt üzerinde kalırdı. PR4 öncesi havuz AÇIKÇA
ayarlanmıyordu ve SQLAlchemy varsayılanları yürürlükteydi (`pool_size=5`,
`max_overflow=10`, `pool_timeout=30`): 5 < 8 thread → normal yükte bile bağlantı
churn'ü, ve 30 sn'lik SESSİZ checkout beklemesi hiçbir istek deadline'ına bağlı
değildi.

## 3. Bloklayıcı çağrı envanteri

Kanıta dayalı; her satır gerçek koda karşı doğrulanmıştır.

| Yol | Kapı | Alım beklemesi | Sağlayıcı timeout | Aşırı yük yanıtı | Tx/kilit I/O'yu kapsıyor mu |
|---|---|---|---|---|---|
| `/ask`, `/ask/stream`, plan/menü/analiz route'ları | `ai_concurrency_gate` / `ai_stream_concurrency_gate` (`_ai_slots`) | `AI_GATE_WAIT_SECONDS` (0) | `min(30, kalan bütçe)` / `min(BEDROCK_CALL_TIMEOUT_SECONDS, kalan)` | 503 + `Retry-After: 15` | Hayır |
| Bedrock/OpenAI model çağrıları | `model_concurrency_slot` (`_model_slots`, iç içe) | `min(kapı beklemesi, deadline)` | yukarıdaki ile aynı | `BlockingConcurrencyLimit` → route sözleşmesi | Hayır |
| `food_search` (FatSecret + LLM normalizasyon) | `blocking_concurrency_slot` | 0 | 5–10 sn | Boş sonuç (degrade) | Hayır |
| `respond_suggestion` (social) | `blocking_concurrency_slot` | 0 | 5–10 sn | Degrade | Hayır |
| Mobil `/api/v1/auth/login` | `blocking_concurrency_slot` | 0 | Cognito 5+10 sn, 2 deneme | `AUTH_TEMPORARILY_UNAVAILABLE` | Hayır |
| Mobil `/api/v1/auth/refresh` | `blocking_concurrency_slot` | 0 | aynı | aynı | **Hayır** — iki fazlı: ağ ÖNCE, kilit SONRA |
| Menü scrape (`proxy_scan_menu`) | `scrape_concurrency_gate` (`_scrape_slots`) | `SCRAPE_GATE_WAIT_SECONDS` (10) | 10 sn/sayfa | 503 + `Retry-After: 15` | Hayır |
| Giyilebilir `callback` / `sync` / `whoop` | **`blocking_concurrency_slot` (PR4'te eklendi)** | 0 | 10 sn × ≤3 ardışık | 503 + `Retry-After: 15` | Hayır — izin yalnız ağ'ı sarar |
| JWKS anahtar çözümü (mobil token doğrulama) | Kapı yok — **PR4'te tek-uçuş + soğuma** | — | 5 sn | Kesin `invalid_key` | Hayır |

### Bilerek kapıya alınMAYANlar (ve neden)

- **`/api/food/barcode*` (FatSecret barkod)** — `BarcodeFoodCache` DB tablosu
  önünde durur, yani istekler ÇOĞUNLUKLA ağ'a hiç çıkmaz; çağrı 5–10 sn ile
  sınırlı ve `FOOD_SEARCH_RATELIMIT` altında. Bu yolu kapıya almak, AI
  doygunluğunda önbellekten servis edilebilecek istekleri de reddederdi —
  "sağlayıcı kodunun yanında duruyor diye hızlı yerel işi kapıya alma" kuralının
  ihlali olurdu.
- **`menu_ocr` OpenAI vision çağrısı** — çağıran route'lar zaten `_ai_slots` veya
  `_scrape_slots` tutar, yani THREAD rezervi korunur. Ama bu çağrı
  `model_concurrency_slot` KULLANMAZ: model tavanı bu yolu saymaz, `AiProviderCalls`
  metriğinde görünmez. Bilinen sınır (§7).
- **Resend e-posta ve S3 yükleme** — kayıt/doğrulama ve avatar/öğün görseli
  yollarında senkron; sınırlı timeout'lu ve düşük hacimli. Kapıya alınmaları
  gerekçelendirilmiş bir kanıt bekler (§7).

## 4. Timeout / deadline hiyerarşisi

```
istek bütçesi (koç turu: TEK mutlak deadline, PR #199)
  └─ kapı alımı           timeout = min(GATE_WAIT, kalan bütçe)
       └─ model izni alımı timeout = min(GATE_WAIT, kalan bütçe)
            └─ sağlayıcı   timeout = min(sağlayıcı tavanı, kalan bütçe)
```

Kurallar (PR #199'da uygulandı, PR4'te test edildi):

- Kapıda geçen süre AYNI bütçeden düşer — bekleme "bedava" değildir.
- Deadline dolduktan sonra yeni bir sağlayıcı çağrısı ya da yedek YOL BAŞLAMAZ.
- Sağlayıcıya geçirilen timeout kalan bütçeden BÜYÜK OLAMAZ.
- İzinler `finally` içinde bırakılır; akışta `call_on_close` ile (istemci
  koparsa da) bırakılır ve tam bir kez bırakılır.

## 5. Aşırı yük davranışı

| Durum | Yanıt | Ayırt edilebilir mi |
|---|---|---|
| Kapı dolu (doygunluk) | 503 + `Retry-After: 15`, `GateRejections` sayacı | Evet — `HttpOverload` (503) `HttpServerErrors`'tan (500) AYRI |
| Sağlayıcı timeout | Kurtarma merdiveni → yedek → dostça hata | Evet — `AiProviderCalls{Outcome=timeout}` |
| Sağlayıcı erişilemez | aynı | Evet — `Outcome=connection` |
| İstemci akışı kopardı | Kısmi yanıt kaydedilir | Evet — `Outcome=cancelled` (arıza SAYILMAZ) |
| Uygulama kusuru | 500 | Evet — `HttpServerErrors` |

Yanıt gövdeleri sağlayıcı içini SIZDIRMAZ (sağlayıcı adı, URL, istisna metni,
token). `tests/test_capacity_invariants.py` bunu sabitler.

## 6. Metrikler ve önerilen alarmlar

`RUNTIME_METRICS_ENABLED=1` iken, `FitX/Runtime` namespace'inde. Seviye
metrikleri PR4'te **flush thread'inden** örneklenir — `/health?deep=1`
yolundan DEĞİL. Sebep: konteyner probe'u SIĞ `/health`'i çağırır ve deep uç
yalnızca deploy sırasında birkaç kez vurulur, dolayısıyla gauge'lar veri
üretmiyor ve alarmlar sürekli `INSUFFICIENT_DATA`'da kalıyordu.

| Metrik | Anlam | Önerilen alarm |
|---|---|---|
| `ThreadReserve` | Ölçülen boş thread payı | `< 2` 2 ardışık periyot → rollout'u DURDUR |
| `AiSlotsActive` | Tutulan paylaşılan izin | `>= AI_MAX_CONCURRENCY` sürekli → kapasite incele |
| `AiModelSlotsActive` | Tutulan model izni | bilgilendirici |
| `ScrapeSlotsActive` | Tutulan scrape izni | bilgilendirici |
| `DbPoolCheckedOut` / `DbPoolOverflow` / `DbPoolSize` | Havuz kullanımı | `CheckedOut >= Size` sürekli → havuzu büyüt |
| `GateRejections{Gate}` | Kapı reddi | ani artış → yük atma başladı |
| `HttpOverload` | 503 (kasıtlı yük atma) | 500'lerden AYRI alarmla |
| `AiProviderCalls{Provider,Outcome}` | Sağlayıcı sonucu | `Outcome=timeout/connection` artışı |

`ThreadReserve` **PR4'ten önce hiç yayınlanmıyordu**, oysa
`app/feature_flags.py` `MOBILE_AUTH_ENABLED` kaydı ve `docs/ROLLOUT.md` onu
rollout'u durdurma sinyali olarak adlandırıyordu — belgelenmiş ama var olmayan
bir abort kapısı. Artık gerçek.

**Boyut disiplini:** kapasite gauge'ları boyut TAŞIMAZ (süreç-geneli tekil
sinyaller). Kullanıcı kimliği, token, e-posta, ham prompt ya da ham path hiçbir
metrik boyutunda YER ALMAZ. Enstrümantasyon arızası yutulur: bir örnekleyicinin
patlaması ne flush döngüsünü ne de herhangi bir isteği etkiler.

**Kapanış flush'ı:** `atexit` + gunicorn `worker_exit` son pencereyi boşaltır.
Öncesinde daemon flush thread'i kapanışta pencere ortasında ölüyordu ve
kaybedilen pencere tam olarak yeniden başlatmadan ÖNCEKİ pencereydi — yani
deploy'u/rollback'i tetikleyen doygunluk artışının kendisi.

## 7. Bilinen sınırlar

1. **`menu_ocr` vision çağrısı model tavanının dışındadır.** Thread rezervi
   korunur (çağıran route kapı tutar) ama `AI_MODEL_MAX_CONCURRENCY` bu yolu
   saymaz ve çağrı `AiProviderCalls`'ta görünmez. Gerçek eşzamanlı sağlayıcı
   işlemi tavanı bu nedenle `AI_MAX + SCRAPE_MAX = 6`'dır, `AI_MODEL_MAX = 4`
   değil.
2. **`DbUp` / `RedisUp` / `LoginUp` hâlâ yalnızca `/health?deep=1` yolunda
   yazılır.** Bunlar gerçek bağımlılık probe'u gerektirir; flush thread'inden
   çağrılsalardı metrik yolu AĞ'a çıkmış olurdu (modülün temel tasarım kuralının
   ihlali). Bu gauge'lara alarm kuracaksan zamanlanmış bir deep-health poll'ü
   ÖN KOŞULDUR.
3. **JWKS soğuma penceresi.** Gerçek bir anahtar rotasyonu bir tazelemeden hemen
   sonra olursa yeni `kid` pencere boyunca (varsayılan 60 sn) reddedilebilir.
   Cognito rotasyon sırasında eski anahtarla imzalamayı sürdürür; sınırsız thread
   park etmeye kıyasla bilinçli takas.
4. **Barkod / e-posta / S3 yolları kapısızdır** (§3). Sınırlı timeout + önbellek
   + rate-limit ile yaşanabilir kabul edildi; hacim büyürse yeniden değerlendir.
5. **Kapılar süreç-içidir.** Yatay ölçekte (birden çok konteyner) her örnek kendi
   tavanını uygular; toplam sağlayıcı eşzamanlılığı örnek sayısıyla çarpılır.

## 8. Geri alma

Bu PR yeni bir özellik bayrağı EKLEMEZ ve hiçbir üretim ayarını AÇMAZ. Geri
alma yolları:

| Değişiklik | Geri alma |
|---|---|
| DB havuz boyutu | `.env`: `DB_POOL_SIZE` / `DB_POOL_MAX_OVERFLOW` / `DB_POOL_TIMEOUT_SECONDS` (eski örtük davranış: `5` / `10` / `30`) |
| JWKS soğuma penceresi | `.env`: `JWKS_FORCED_REFRESH_COOLDOWN_SECONDS=0` (eski koşulsuz tazeleme) |
| Giyilebilir kapıları | Kod geri alması gerektirir (bayrak yok) — tek commit |
| Kapasite gauge'ları | `RUNTIME_METRICS_ENABLED=0` (tüm runtime metrikleri no-op) |
| Tümü | `git revert` + normal deploy; şema/migration değişikliği YOK |

Boot invariant'ı desteklenmeyen bir birleşimde artık FAIL-CLOSED'dır: hatalı bir
`.env` kapasite değeri konteyneri boot'ta durdurur ve deploy'un health gate'i
önceki commit'e döner. Bu BİLİNÇLİDİR — sessizce rezervsiz çalışmaktansa gürültülü
düşmek yeğdir.
