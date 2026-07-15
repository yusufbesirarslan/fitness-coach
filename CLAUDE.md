# Fitness Coach (FitX)

Flask + SQLAlchemy + Bedrock/Claude Sonnet (primary heavy AI path) + OpenAI
(fallback/light paths, `gpt-4o-mini`) + FatSecret API proxy + Cognito (native
backend API — Hosted UI/OIDC KAPALI) + S3 avatar/meal image storage + Redis.
Deploy: tek AWS EC2 üzerinde Docker Compose (önceden Railway'deydi).

## Yapı
- starter.py — ince giriş noktası (`gunicorn starter:app`); asıl uygulama app/ paketinde
- app/__init__.py — application factory (create_app), blueprint kaydı
- app/config.py — env/config yükleme, güvenlik ayarları (cookie, SECRET_KEY, TLS zorunluluğu)
- app/hooks.py — CSRF koruması, CSP başlığı + per-request nonce, streak/rollover hook'ları
- app/extensions.py — db, migrate, login_manager, limiter, redis_client, openai_client
- app/models.py — tüm SQLAlchemy modelleri
- app/db_init.py — boot'ta create_all + bekleyen Alembic migration'ları otomatik uygular + idempotent legacy ALTER'lar (FITX_SKIP_DB_INIT=1 ile atlanır)
- app/cli.py — flask CLI komutları (seed-quests, weekly-reset)
- app/timeutil.py — TEK gün/saat kaynağı: sabit Europe/Istanbul (app_today/day_key/utc_day_bounds). Tüm gün anahtarları buradan; doğrudan date.today()/utcnow().strftime("%d.%m") KULLANMA
- app/blueprints/ — auth, profile, nutrition, food, menu, training, tracking, social, gamification, supplements, coach
- app/services/ — ai, ai_coach, ai_nutrition, calculations, fatsecret, foodcache, gamification, premium, referral, cognito_service (native cognito-idp sarmalayıcı; eski cognito.py/cognito_idp.py Sprint 3'te SİLİNDİ — Hosted UI/OIDC yok), cognito_jwt (uygulamadaki TEK JWT doğrulayıcı; cognito_service ona delege eder), session_store (Fernet ile şifreli sunucu-tarafı Cognito token deposu), avatars, injury_constraints, menu_extract/fetch/ocr, training_generation/, validators, email_service (merkezi Resend e-posta altyapısı — SDK'ya yalnızca bu modül dokunur; RESEND_API_KEY yoksa no-op), email_templates (markalı auth e-posta şablonları — saf stdlib, Lambda kopyasıyla bayt-eş tutulur: tests/test_email_templates_sync.py)
- AI yanıt hattı (Sprint 4 WS3, modüler aşamalar): ai_pipeline (orkestratör; kanonik bileşim) → context_builder (koç bağlam blokları + FRIEND_DATA fence) → memory_manager (kalıcı konuşma hafızası + kısa-dönem geçmiş sınırlama + estimate_tokens) → prompt_builder (system/messages montajı; BEDROCK_PROMPT_CACHE parametreyle gelir) → sağlayıcı döngüleri (ai_coach'ta kaldı: araçlar, staging→commit, Bedrock/OpenAI) → moderation (girdi kapısı MAX_QUESTION_CHARS + çıktı kancası) → response_formatter (hata-yedeği kararı, COACH_FALLBACKS). ai_coach eski adları (_fetch_coach_context, _sanitize_client_history, _COACH_FALLBACKS...) re-export eder — mevcut import yolları ve test monkeypatch yüzeyi değişmedi
- Kalıcı koç hafızası (Sprint 4 WS1): CoachConversation + CoachMessage tabloları; /ask her turu DB'ye yazar (hata-yedeği yanıtlar HARİÇ — B16), bağlam penceresi en yeni mesajlardan geriye AI_CONTEXT_TOKEN_BUDGET dolana dek kurulur, özetlenmemiş kuyruk AI_SUMMARY_TRIGGER_TOKENS'ı aşınca eski turlar tek hafif LLM çağrısıyla conversation.summary'ye katlanır (mesajlar SİLİNMEZ, pencere dışı kalır). `GET /coach/history` geçmişi, `POST /coach/conversation/reset` aktif konuşmayı ARŞİVLER. Hafıza katmanının HER adımı arızaya dayanıklıdır: çökerse eski client-history yoluna düşülür, sohbet kırılmaz. Kapatma anahtarı: AI_MEMORY_ENABLED=0
- Akışlı koç yanıtı (Sprint 4 WS2): `POST /ask/stream` bloklayıcı /ask'in yanında durur (SSE çerçeveleri: meta → delta* → done | error). app/services/ai_stream.py gerçek Bedrock akışlı araç döngüsünü (bedrock_client.messages.stream()) koşar; ai_pipeline.stream_answer sarar — done'da turu kalıcılaştırır (B16: hata-yedeği HARİÇ), istemci akış ortasında koparsa (GeneratorExit) kısmi yanıtı interrupted=True kaydeder. Sağlayıcı geçişi B-kuralına tabi: Bedrock→OpenAI yalnızca İLK delta istemciye gitmeden VE hiç araç yan etkisi olmadan; sonrasında akış-ortası hata dostça i18n `error` çerçevesi döndürür (sağlayıcı istisna metni ASLA sızmaz). ai_stream_concurrency_gate slotu yanıt KAPANANA dek tutar (call_on_close) — normal kapı view döndüğünde bırakır, akışta bu ilk token'dan öncedir, yani stream'leri hiç sınırlamazdı. Frontend (static/coach_widget.js): fetch POST + ReadableStream SSE okuyucu (EventSource X-CSRFToken gönderemez), AbortController ile Durdur, Yeniden üret, marked+DOMPurify markdown (jsdelivr pinned + SRI), açılışta GET /coach/history hidrasyonu
- AI önbellek/kurtarma/kısıt katmanı (Sprint 4 WS5/WS9/WS7): app/services/ai_cache.py jenerik Redis önbelleği (cache_get/cache_set, anahtar `ai:cache:<feature>:v1:<sha256>`, JSON değer, özellik-başı TTL, foodcache deseni: Redis yoksa no-op, hatalar yutulur). Deterministik TR→EN besin-adı normalizasyonuna bağlı (ai_nutrition._normalize_food_query_en + batch); yalnızca BAŞARILI sonuç önbelleklenir. app/services/ai_recovery.py kurtarma merdiveni (_heavy_chat'e bağlı): geçici hata (TransientAIError — _claude_chat/_openai_chat rate-limit/timeout/bağlantı için fırlatır, RuntimeError alt sınıfı) → jitter'lı retry; kalıcı hatada Bedrock→OpenAI; İKİSİ de düşerse son-iyi (last-good) Redis yanıtı (içerik-hash anahtarlı — iki kullanıcı yalnızca birebir aynı girdide paylaşır, sızıntı yok); o da yoksa mevcut dostça RuntimeError. Bedrock SDK max_retries 2→1 (BEDROCK_MAX_RETRIES) — katmanların çarpımını önler. WS7: `/ask*` üzerinde AI_BURST_RATELIMIT="5 per minute" (AI_RATELIMIT üstüne) + kullanıcı-başı arıza soğuması (AI_FAILURE_THRESHOLD ardışık arıza → Redis NX anahtar AI_FAILURE_COOLDOWN_SECONDS boyunca 429+Retry-After, KOTADAN ÖNCE; ilk başarıda sayaç sıfırlanır). Hepsi varsayılan AÇIK ve Redis'siz zarifçe no-op
- Arka-plan işler + gözlemlenebilirlik (Sprint 4 WS8/WS6): app/jobs/ — RQ iş kuyruğu. get_queue() None-güvenli (rq/Redis yoksa None), enqueue_or_run(func,...) worker varsa kuyruğa atar yoksa SATIR-İÇİ çalıştırır (worker OPSİYONEL — yoksa işler inline'a düşer, uygulama çalışır). worker.py entrypoint (Linux Worker / Windows SimpleWorker), compose 'worker' servisi, heartbeat daemon thread. İlk bağlı task: summarize_conversation (PR2'nin inline özetlemesi artık ai_pipeline._memory_stage'de enqueue). Ölü-mektup: `flask rq-failed`/`rq-requeue`. /health?deep=1 worker alanı BİLGİLENDİRİCİ (gating değil). rq==2.10.0. WS6: app/services/ai_metrics.py CloudWatch FitX/AI metrikleri (AITurn/AIErrors/*Tokens/SummarizeJob), AI_METRICS_ENABLED VARSAYILAN 0 (cloudwatch:PutMetricData izni gerekir), kapalı/boto3 yokken tam no-op. İzleme: sunucu-üretimi 16-hane request_id → logfmt satırı + /ask/stream SSE meta + Sentry tag. Docs: docs/{AI_ARCHITECTURE,MEMORY,STREAMING,OBSERVABILITY,RATE_LIMITING,DEPLOYMENT}.md. Yük testleri tests/load/ (@pytest.mark.load, pytest.ini `-m "not load"` ile varsayılan atlanır; `-m load` ile koş)
- app/prompts/ — TÜM LLM istem şablonları (Sprint 4 WS4): system (koç sistem promptu + dil direktifi), goals (plan-koçluğu), nutrition (besin arama/EN normalizasyon/menü çıkarımı/porsiyon-makro/öğün toplamı + PORTION_SANITY_RULE), workout (training_generation'a delege), progress (haftalık check-in). Yeni LLM istemi eklerken şablonu BURAYA koy, servise gömme; modüller saf string üretir (DB/Flask/istemci import etmez)
- infra/cognito-email-sender/ — Cognito CustomEmailSender Lambda + KMS (SAM): doğrulama/sıfırlama kod e-postalarını Resend üzerinden markalı gönderir; havuza bağlama runbook'u README'sinde (mimari: docs/auth-emails.md)
- fitx_mcp/ — MCP sunucusu (AI Coach DB araçları). DİKKAT: araçlar user_id'yi parametre alır, kendi yetkilendirmesi YOKTUR — yalnızca stdio/in-process kullan; HTTP taşıması FITX_MCP_ALLOW_HTTP=1 + loopback arkasındadır, asla public proxy'e koyma
- nutrition_pipeline.py, analytics_engine.py — deterministik makro değerlendirme / nudge motoru
- s3_helper.py — S3 görsel yükleme + pre-signed URL (EC2 IAM Instance Profile ile auth; AWS anahtarı hardcode YOK)
- migrations/ — Alembic (Flask-Migrate) şema geçmişi
- templates/ + static/ — Türkçe UI (index, nutrition, training, progress, setup, friends, chat, quests, leaderboard, ...)
- Dockerfile / docker-compose.yml — web (gunicorn, tek worker/8 thread) + redis; Postgres artık compose içinde değil, prod'da external RDS/DATABASE_URL ile gelir; servisler loopback'e bağlı
- nginx.conf — host reverse proxy: / → Flask:5000, /fatsecret/rest/server.api → loopback proxy (127.0.0.1:3000 — ayrı süreç DEĞİL, host nginx'in kendi server bloğu; süpervizyon = nginx systemd; ayrıntı/geçmiş: deploy/fatsecret-proxy.md — deploy.yml dinleyiciyi kontrol eder, /health?deep=1 raporlar)
- tests/ — pytest (menü çıkarımı, makro alaka, nutrition pipeline)
- .env — SECRET_KEY, DATABASE_URL, FATSECRET_*, OPENAI_API_KEY, OPENAI_MODEL, BEDROCK_*, COGNITO_*, AWS_REGION, S3_BUCKET_NAME, REDIS_URL, RESEND_API_KEY, EMAIL_FROM_NAME/EMAIL_FROM_ADDRESS/EMAIL_REPLY_TO (commit etme)
  - Örnek için .env.example'a bak. OpenAI anahtarı .env'den okunur, asla hardcode edilmez.
  - Opsiyonel güvenlik/freemium/gözlem anahtarları (hepsinin makul varsayılanı var):
    - `AI_PLAN_QUOTA_ENABLED` (vars. 1) — non-premium'a haftada 1 AI plan üretimi (app/services/premium.py). 0 = kota kapalı.
    - `LOGIN_FAIL_CLOSED` (vars. 1) — Redis erişilemezse login 503 (brute-force throttle güvenilir değilken). 0 = eski fail-open.
      Bilinçli tradeoff (A5): Redis availability == login availability. Redis tek konteynerdir;
      diğer her şey degrade eder (session=cookie, leaderboard→Postgres, foodcache→L1,
      limiter→in-memory), yalnızca login 503 olur. `/health?deep=1` login offline'ken 503
      döner ve deploy gate bunu kullanır (I2); sığ /health liveness için yeşil kalır.
    - `AI_MAX_CONCURRENCY` (vars. 4) + `SCRAPE_MAX_CONCURRENCY` (vars. 2) +
      `AI_MODEL_MAX_CONCURRENCY` (vars. AI_MAX_CONCURRENCY) +
      `AI_GATE_WAIT_SECONDS` (vars. 0) — ağır AI/scrape route'larında eşzamanlılık
      tavanı (app/services/ai_gate.py); dolunca 503 + Retry-After. İki kapının toplamı
      `FITX_WEB_THREADS`'in (vars. 8, gunicorn --threads ile eş) en az 2 altında
      kalmalı; ihlal boot'ta loglanır. Thread rezervi /health ve ucuz route'ları
      AI yükünden korur (A1/I1).
    - `SENTRY_DSN` (yoksa kapalı) + opsiyonel `SENTRY_ENVIRONMENT`, `SENTRY_TRACES_SAMPLE_RATE` — hata izleme (app/observability.py). DSN yoksa no-op.
  - Operasyon notu: web konteyneri hâlâ tek gunicorn worker + 8 thread çalışır.
    Coach/menu/plan AI çağrıları senkron ve bloklayıcıdır; 8 uzun AI isteği tüm
    uygulama thread'lerini doldurabilir. Worker sayısını artırmadan önce in-memory
    cache/limiter fallback varsayımlarını kaldır veya AI uçlarını ayrı worker/queue'ya taşı.

## Veritabanı
Lokal: SQLite (instance/chatbot.db). Prod/Docker: PostgreSQL (DATABASE_URL ile).
Beslenme TEK kanonik defterde tutulur: MealLog (UI + AI koç + menü). Eski
UserDailyNutrition verisi MealLog'a taşındı ve tablo düşürüldü (migration
f6a7b8c9d0e1). MealLog.tarih ISO 'YYYY-MM-DD' (Istanbul); gün anahtarları için
app/timeutil kullan.
Tablolar boot'ta db.create_all() ile oluşur (app/db_init.py); ayrıca Alembic baseline migration mevcut.
Şema değişikliği akışı (yeni değişiklikler için tercih edilen yol):
1. Modeli app/models.py'de değiştir
2. `FITX_SKIP_DB_INIT=1 flask --app starter db migrate -m "açıklama"`
3. Üretilen dosyayı gözden geçir ve commit'le — uygulama boot'ta bekleyen
   migration'ları OTOMATİK uygular (app/db_init.py); alembic_version tablosu
   olmayan eski DB'ler de boot'ta otomatik `stamp` ile zincire alınır.
   Manuel `flask db upgrade` / `stamp head` yalnızca lokal işler için gerekir.
Modeller: User, UserSession, WeeklyLog, WeeklyCheckIn, NutritionPlan, TrainingPlan, MealLog,
PendingAction, CoachConversation, CoachMessage, PumpCheck, Friendship, Message, Activity,
Supplement, DailyQuest, UserQuestProgress, WeeklyWinner, WeeklyResetLog, WaterLog, WorkoutLog,
DailyActivity, CustomMeal, CustomMealItem
Yeni user-child model eklerken app/cli.py `_user_child_models` listesine de ekle
(tests/test_cascade_delete.py introspeksiyonla doğrular).
DİKKAT (taze DB boot yolu): app/db_init.py önce db.create_all() çalıştırır, sonra
aa11bb22cc33'ü damgalayıp head'e upgrade eder — yani o revision'dan SONRAKİ her
migration create_all'ın zaten kurduğu şemaya karşı da koşar. Tablo yaratan yeni
migration'lar bu yüzden TEKRAR-ÇALIŞTIRILABİLİR olmalı (bkz. cc33dd44ee55:
`sa.inspect(op.get_bind()).has_table(...)` kapısı).

## Kurallar
- Kısa commit mesajları yaz
- Türkçe UI, İngilizce kod
- Test: `pytest` + `flask run` ile local test et
- CSP: başlık Flask'ta üretilir (app/hooks.py). Şablona yeni satır-içi <script> eklerken
  MUTLAKA `<script nonce="{{ csp_nonce }}">` yaz, yoksa tarayıcı bloklar. Dış script
  yalnızca cdn.jsdelivr.net ve *.googletagmanager.com'dan (GA) yüklenebilir.
- CSRF: tüm POST/PUT/PATCH/DELETE iki katmandan geçer (app/hooks.py `_csrf_protect`):
  Origin/Referer + per-session synchronizer token. Token `<meta name="csrf-token">`
  (_head.html, `csrf_token` context processor) ile verilir; static/csrf.js `window.fetch`'i
  sararak durum-değiştiren aynı-origin fetch'lere `X-CSRFToken` başlığını OTOMATİK ekler.
  Yeni fetch çağrısı ek iş istemez; AMA: (1) yeni sayfa `_head.html`'i include etmeli,
  (2) durum-değiştiren bir istek fetch DIŞINDA (XHR/sendBeacon) yapılıyorsa X-CSRFToken
  başlığını elle ekle, (3) state-changing route'u GET olarak açma (kapı yalnızca yazma
  metodlarında çalışır).
- Deploy: push to main → **ci.yml (pytest + şema-drift) YEŞİL olursa** →
  .github/workflows/deploy.yml (`workflow_run` ile CI'a kapılı) AWS SSM ile EC2'de
  compose'u yeniden kurar, /health?deep=1 gate'inden geçirmezse önceki commit'e
  rollback eder. CI kırmızıysa deploy HİÇ başlamaz (H3).
  NOT: main branch koruması ayrıca GitHub ayarlarından açılmalıdır — buradaki gate
  DEPLOY kapısıdır, branch koruması MERGE kapısıdır.
  Host nginx'te eski `add_header Content-Security-Policy` satırı kalırsa deploy
  canlı config'i sed ile değiştirmez; fail-fast yapar.
  DİKKAT (A2): rollback yalnızca KODU geri alır — boot'ta otomatik uygulanan DB
  migration'ları geri ALMAZ. Migration'ları expand/contract (geriye uyumlu) yaz:
  kolon/tablo DÜŞÜRME veya RENAME, eski kod en az bir başarılı deploy boyunca onsuz
  çalışabiliyorsa gönderilir. Yıkıcı migration kaçınılmazsa: önce RDS snapshot al,
  `FITX_DB_AUTO_UPGRADE=0` ile boot-upgrade'i kapat ve migration'ı ayrı tek seferlik
  `flask db upgrade` adımı olarak çalıştır. Boot'ta migration hatası artık FATAL'dir
  (app/db_init.py; kaçış: FITX_DB_UPGRADE_FAIL_OPEN=1) — health gate rollback yapar.
- Sorgular daima current_user.id'ye scope'lanır; ID ile yüklenen kayıtlarda sahiplik kontrolü zorunlu
