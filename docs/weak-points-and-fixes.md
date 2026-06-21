# FitX — Weak Points & Needed Fixes

_Hazırlanma: 2026-06-21 · Kapsam: 2026-06-14 → 06-21 haftası incelemesi + açık backlog_

Bu doküman; haftalık kod incelemesinde çıkan zayıf noktaları, gereken
düzeltmeleri ve önerileri **öncelik sırasına** göre toplar. Her madde:
**ne / neden / nerede / nasıl** + öneri içerir. İlgili açık backlog için
[`triage-remaining-fixes.md`](triage-remaining-fixes.md)'e de bakın.

Genel durum: hafta sağlam geçti — 835 test geçiyor, %91 kapsama, güvenlik
bilinçli değişiklikler (Cognito auth, korumalı Bedrock tool-use koçu). Asıl
risk **gönderilen kodda değil, operasyonel tarafta**: prod hata izleme yok ve
artık pahalı olan AI uçlarında sunucu-tarafı kota yok.

---

## 🔴 Kritik (önce bunlar)

### 1. Sunucu-tarafı premium kapısı (AI kota zorlaması)
- **Ne:** `is_premium` / "haftada 1 AI plan" yalnızca UI-tavsiyesi; AI üretim
  rotalarında (plan/koç uçları) sunucu-tarafı kota **yok**.
- **Neden kritik:** Bedrock artık devrede → kotasız uç = doğrudan maliyet-suistimali
  açığı. Bir kullanıcı plan/koç uçlarını sınırsız çağırabilir.
- **Nerede:** `app/blueprints/pages.py:59-65` ve AI plan/koç endpoint'leri
  (`app/blueprints/training.py`, `app/blueprints/coach.py`).
- **Öneri:** AI üretim uçlarına decorator/route-içi kontrol; premium olmayan
  kullanıcılar için haftalık kotayı Istanbul haftasına göre (`app/timeutil`)
  say. Aşımda 402/429 + Türkçe mesaj. İlk sürümde fail-closed.

### 2. Gözlemlenebilirlik — hata izleme / yapısal log
- **Ne:** Sentry/OTel yok; yapısal istek logu yok.
- **Neden kritik:** Bu hafta iki yüksek-etki alanlı özellik (auth + DB'ye yazan
  LLM) prod'a indi. Şu an prod hataları kör noktada — en büyük boşluk.
- **Nerede:** Uygulama genel; `app/__init__.py` boot.
- **Öneri:** Sentry ekle (DSN env'den, hardcode YOK), istek-bazlı yapısal log
  (request id, user id, latency). Bedrock/OpenAI çağrı süre+maliyet metriği
  zaten loglanıyor ([AI] sağlayıcı logu) — bunu yapısal alana taşı.

---

## 🟡 Orta (yakında)

### 3. CSRF synchronizer token (defense-in-depth)
- **Ne:** CSRF savunması yalnızca Origin/Referer + `SameSite=Lax`.
- **Neden:** Her yeni form/`fetch()` ile yüzey büyüyor; senkronizör token
  ikinci katman sağlar.
- **Nerede:** `app/hooks.py:61-95` (`_csrf_protect`).
- **Öneri:** Flask-WTF `CSRFProtect` ya da `inject_csp_nonce` benzeri context
  processor ile elle token; meta tag + header üzerinden JS'e aç. **Dikkat:**
  login `session.clear()` yaptığı için token'ı login *sonrası* yeniden ver.
  Ayrıca hiçbir durum-değiştiren rotanın GET ile erişilebilir olmadığını denetle.

### 4. Rate-limiter fail-open politikası
- **Ne:** Redis düştüğünde limiter sessizce in-memory'ye düşüyor → dağıtık
  login brute-force throttle zayıflıyor. Şu an yalnızca boot'ta uyarılıyor.
- **Nerede:** `app/extensions.py:28-33`, `warn_if_limiter_degraded`.
- **Öneri:** Politikayı netleştir. Öneri: yalnızca login throttle için
  fail-closed (auth'ta daha sıkı limit / 503), diğer rotalar fail-open kalsın
  (Redis blip'inde tüm login'leri kilitleme).

### 5. `nutrition.py` god-module + `ai_coach.py` şişkinliği
- **Ne:** `nutrition.py` 429 statement'lık tek modül (kendi backlog'umuzda da
  işaretli), `ai_coach.py` ~1.200 satır. Değişiklik-riski yoğunlaşma noktaları.
- **Öneri:** `nutrition.py`'yi ilgiye göre böl (log/öğün, plan, makro). `ai_coach.py`'de
  tool tanımları/loop'u ayrı modüle çıkar. Davranış değişmeden, küçük adımlarla.

### 6. Legacy `UserDailyNutrition` modeli kaldırılmalı
- **Ne:** MealLog kanonik defter oldu; `UserDailyNutrition` artık yazılmıyor ama
  model + tablo duruyor.
- **Öneri:** Kullanılmadığını doğrula, model + tabloyu migration ile kaldır.

### 7. MCP `get_today_volume` UTC gün-anahtarı
- **Ne:** `workout_log` UTC `CURRENT_DATE` ile toplanırsa 00:00–03:00 Istanbul
  aralığında yanlış (C2 ile aynı sınıf). Koç tarafı 6329642'de düzeltildi;
  MCP server'ı denetle.
- **Nerede:** `fitx_mcp/server.py:567` ve `created_at::date` aralıkları
  (661/669/677/685).
- **Öneri:** `app/timeutil.utc_day_bounds` mantığını yansıt; `created_at >= :start
  AND created_at < :end` kullan.

---

## 🟢 Düşük / hijyen

### 8. IDOR kontrolü substring sezgisel
- **Ne:** S3 sahiplik kontrolü `f"/{user_id}/" in key` — mevcut anahtar düzeniyle
  doğru ama konvansiyona bağlı; gelecekte anahtar şeması değişirse sessizce zayıflar.
- **Nerede:** `s3_helper.py:131,153`.
- **Öneri:** Yazma anında anahtar formatını assert et / yorum ekle; ileride
  yapısal prefix doğrulamasına geç.

### 9. SQLAlchemy legacy `Query.get()`
- **Ne:** Kod genelinde `Model.query.get(id)` — 315 deprecation uyarısı.
- **Öneri:** `db.session.get(Model, id)`'ye geç. Acil değil ama 2.x yükseltmesinde
  sorun çıkarır.

### 10. `/friends/search` kullanıcı adı enumerasyonu
- **Ne:** Throttle'sız arama; kullanıcı adları yarı-public, düşük şiddet.
- **Öneri:** Rate limit ekle.

### 11. `calculate_bmr` None-guard'ları
- **Ne:** `gender` / `goal` için savunmacı varsayılan yok.
- **Öneri:** Defansif varsayılan ekle.

### 12. Doküman hijyeni
- **Ne:** `docs/` altında 10 dosya + kökte `TRIAGE.md` & `TRIAGE_FIXES.md`;
  kök ile `docs/triage-*` arasında tekrar var.
- **Öneri:** Kök `TRIAGE*.md`'leri `docs/` altında topla; aşılmış raporları sil.

### 13. Bağımlılık denetimi
- **Ne:** Tüm `==` pinleri iyi; ama Pillow/pdfplumber (güvenilmeyen yükleme
  parser'ları) için sürekli güvenlik takibi yok.
- **Öneri:** Dependabot/güvenlik taraması ekle.

---

## Süreç önerileri

- **Yüksek-etki özellikleri için staging/canary:** Cognito + Bedrock gibi geniş
  yüzeyli işler tek haftada prod'a inerken birim testleri konfig/maliyet
  sürprizlerini yakalamaz. Bir staging doğrulama adımı ekle.
- **Test disiplini sürsün:** %91 kapsama çok iyi; yeni uçlarda (özellikle AI
  kota kapısı) regresyon testini PR şartı yap.
- **Prompt-injection farkındalığı:** Scrape edilen menüler staging→confirm akışı
  + sunucu-enjekte `user_id` ile sınırlanıyor; bu akış değişirse tekrar denetle.

---

## Öncelik özeti

| # | Madde | Öncelik | Tahmini boyut |
|---|-------|---------|----------------|
| 1 | Sunucu-tarafı AI kota kapısı | 🔴 | Orta |
| 2 | Sentry/OTel + yapısal log | 🔴 | Orta |
| 3 | CSRF synchronizer token | 🟡 | Büyük (her form/fetch) |
| 4 | Limiter fail-open politikası | 🟡 | Küçük |
| 5 | nutrition.py / ai_coach.py bölme | 🟡 | Büyük |
| 6 | Legacy UserDailyNutrition kaldır | 🟡 | Küçük |
| 7 | MCP get_today_volume UTC fix | 🟡 | Küçük |
| 8–13 | IDOR/Query.get/enumerasyon/BMR/doc/deps | 🟢 | Küçük |
