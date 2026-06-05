# Fitness Coach

Flask + SQLAlchemy + AWS Bedrock (Claude, boto3) + FatSecret API proxy.
Deploy: tek AWS EC2 üzerinde Docker Compose (önceden Railway'deydi).

## Yapı
- starter.py — tüm backend (routes, modeller, AI fonksiyonları)
- templates/ — index, nutrition, training, progress, setup, login, register, 404
- static/style.css — ortak CSS
- Dockerfile / docker-compose.yml — web (gunicorn) + db (postgres:15-alpine)
- nginx.conf — host reverse proxy: / → Flask:5000, /fatsecret/ → statik IP proxy
- .env — SECRET_KEY, DATABASE_URL, POSTGRES_*, FATSECRET_*, AWS_REGION (commit etme)
  - Örnek için .env.example'a bak. AWS access key YOK; EC2 IAM Instance Profile sağlar.

## Veritabanı
Lokal: SQLite (chatbot.db). Prod/Docker: PostgreSQL (DATABASE_URL ile).
Tablolar import anında db.create_all() ile oluşur.
Modeller: User, UserSession, WeeklyLog, WeeklyCheckIn, MealLog, NutritionPlan, TrainingPlan

## Kurallar
- Kısa commit mesajları yaz
- Türkçe UI, İngilizce kod
- Test: flask run ile local test et