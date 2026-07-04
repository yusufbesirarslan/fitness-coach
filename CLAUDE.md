# Fitness Coach (FitX)

Flask + SQLAlchemy + Bedrock/Claude Sonnet (primary heavy AI path) + OpenAI
(fallback/light paths, `gpt-4o-mini`) + FatSecret API proxy + Cognito OIDC +
S3 avatar/meal image storage + Redis.
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
- app/services/ — ai, ai_coach, ai_nutrition, calculations, fatsecret, foodcache, gamification, premium, referral, cognito/cognito_idp, avatars, injury_constraints, menu_extract/fetch/ocr, training_generation/, validators
- fitx_mcp/ — MCP sunucusu (AI Coach DB araçları). DİKKAT: araçlar user_id'yi parametre alır, kendi yetkilendirmesi YOKTUR — yalnızca stdio/in-process kullan; HTTP taşıması FITX_MCP_ALLOW_HTTP=1 + loopback arkasındadır, asla public proxy'e koyma
- nutrition_pipeline.py, analytics_engine.py — deterministik makro değerlendirme / nudge motoru
- s3_helper.py — S3 görsel yükleme + pre-signed URL (EC2 IAM Instance Profile ile auth; AWS anahtarı hardcode YOK)
- migrations/ — Alembic (Flask-Migrate) şema geçmişi
- templates/ + static/ — Türkçe UI (index, nutrition, training, progress, setup, friends, chat, quests, leaderboard, ...)
- Dockerfile / docker-compose.yml — web (gunicorn, tek worker/8 thread) + redis; Postgres artık compose içinde değil, prod'da external RDS/DATABASE_URL ile gelir; servisler loopback'e bağlı
- nginx.conf — host reverse proxy: / → Flask:5000, /fatsecret/rest/server.api → loopback proxy (127.0.0.1:3000, aynı EC2; Bearer token tel üzerinde açıkta kalmasın)
- tests/ — pytest (menü çıkarımı, makro alaka, nutrition pipeline)
- .env — SECRET_KEY, DATABASE_URL, FATSECRET_*, OPENAI_API_KEY, OPENAI_MODEL, BEDROCK_*, COGNITO_*, AWS_REGION, S3_BUCKET_NAME, REDIS_URL (commit etme)
  - Örnek için .env.example'a bak. OpenAI anahtarı .env'den okunur, asla hardcode edilmez.
  - Opsiyonel güvenlik/freemium/gözlem anahtarları (hepsinin makul varsayılanı var):
    - `AI_PLAN_QUOTA_ENABLED` (vars. 1) — non-premium'a haftada 1 AI plan üretimi (app/services/premium.py). 0 = kota kapalı.
    - `LOGIN_FAIL_CLOSED` (vars. 1) — Redis erişilemezse login 503 (brute-force throttle güvenilir değilken). 0 = eski fail-open.
      Bilinçli tradeoff (A5): Redis availability == login availability. Redis tek konteynerdir;
      diğer her şey degrade eder (session=cookie, leaderboard→Postgres, foodcache→L1,
      limiter→in-memory), yalnızca login 503 olur. /health `limiter_storage` alanından izle.
    - `AI_MAX_CONCURRENCY` (vars. 5) + `AI_GATE_WAIT_SECONDS` (vars. 10) — ağır AI route'larında
      eşzamanlılık tavanı (app/services/ai_gate.py); dolunca 503 + Retry-After. Thread rezervi
      /health ve ucuz route'ları AI yükünden korur (A1).
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
PendingAction, PumpCheck, Friendship, Message, Activity, Supplement, DailyQuest,
UserQuestProgress, WeeklyWinner, WeeklyResetLog, WaterLog, WorkoutLog,
DailyActivity, CustomMeal, CustomMealItem

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
- Deploy: push to main → .github/workflows/deploy.yml (AWS SSM) EC2'de compose'u
  yeniden kurar, /health 200 gate'inden geçirmezse önceki commit'e rollback eder.
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
