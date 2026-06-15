# Fitness Coach (FitX)

Flask + SQLAlchemy + OpenAI (gpt-4o-mini) + FatSecret API proxy.
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
- app/services/ — ai, ai_coach, ai_nutrition, calculations, fatsecret, foodcache, gamification, menu_extract/fetch/ocr, validators
- fitx_mcp/ — MCP sunucusu (AI Coach DB araçları). DİKKAT: araçlar user_id'yi parametre alır, kendi yetkilendirmesi YOKTUR — yalnızca stdio/in-process kullan; HTTP taşıması FITX_MCP_ALLOW_HTTP=1 + loopback arkasındadır, asla public proxy'e koyma
- nutrition_pipeline.py, analytics_engine.py — deterministik makro değerlendirme / nudge motoru
- s3_helper.py — S3 görsel yükleme + pre-signed URL (EC2 IAM Instance Profile ile auth; AWS anahtarı hardcode YOK)
- migrations/ — Alembic (Flask-Migrate) şema geçmişi
- templates/ + static/ — Türkçe UI (index, nutrition, training, progress, setup, friends, chat, quests, leaderboard, ...)
- Dockerfile / docker-compose.yml — web (gunicorn, tek worker) + db (postgres) + redis; hepsi loopback'e bağlı
- nginx.conf — host reverse proxy: / → Flask:5000, /fatsecret/rest/server.api → statik IP proxy
- tests/ — pytest (menü çıkarımı, makro alaka, nutrition pipeline)
- .env — SECRET_KEY, DATABASE_URL, POSTGRES_*, FATSECRET_*, OPENAI_API_KEY, OPENAI_MODEL, AWS_REGION, S3_BUCKET_NAME, REDIS_URL (commit etme)
  - Örnek için .env.example'a bak. OpenAI anahtarı .env'den okunur, asla hardcode edilmez.

## Veritabanı
Lokal: SQLite (instance/chatbot.db). Prod/Docker: PostgreSQL (DATABASE_URL ile).
Beslenme TEK kanonik defterde tutulur: MealLog (UI + AI koç + menü; UserDailyNutrition
artık YAZILMIYOR, eski veriler MealLog'a taşındı). MealLog.tarih ISO 'YYYY-MM-DD'
(Istanbul); gün anahtarları için app/timeutil kullan.
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
UserQuestProgress, WeeklyWinner, WeeklyResetLog, WaterLog, WorkoutLog, UserDailyNutrition,
DailyActivity, CustomMeal, CustomMealItem

## Kurallar
- Kısa commit mesajları yaz
- Türkçe UI, İngilizce kod
- Test: `pytest` + `flask run` ile local test et
- CSP: başlık Flask'ta üretilir (app/hooks.py). Şablona yeni satır-içi <script> eklerken
  MUTLAKA `<script nonce="{{ csp_nonce }}">` yaz, yoksa tarayıcı bloklar. Dış script
  yalnızca cdn.jsdelivr.net ve *.googletagmanager.com'dan (GA) yüklenebilir.
- Deploy: push to main → .github/workflows/deploy.yml (AWS SSM) EC2'de compose'u
  yeniden kurar ve host nginx'teki eski CSP add_header satırını otomatik temizler.
- Sorgular daima current_user.id'ye scope'lanır; ID ile yüklenen kayıtlarda sahiplik kontrolü zorunlu
