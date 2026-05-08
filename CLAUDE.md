# Fitness Coach

Flask + SQLAlchemy + Groq API. Railway'de deploy.

## Yapı
- starter.py — tüm backend (routes, modeller, AI fonksiyonları)
- templates/ — index, nutrition, training, progress, setup, login, register, 404
- static/style.css — ortak CSS
- .env — GROQ_API_KEY, SECRET_KEY (commit etme)

## Veritabanı
SQLite (chatbot.db). Modeller: User, UserSession, WeeklyLog, WeeklyCheckIn, MealLog, NutritionPlan, TrainingPlan

## Kurallar
- Kısa commit mesajları yaz
- Türkçe UI, İngilizce kod
- Test: flask run ile local test et