# FitX Flask uygulaması — production imajı.
# Python 3.11-slim tabanlı, gunicorn ile 5000 portundan servis eder.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Önce sadece requirements'i kopyala — bağımlılık katmanı kod değişiminde
# yeniden derlenmez (Docker layer cache). psycopg2-binary ve Pillow manylinux
# wheel'leri kendi C kütüphanelerini taşıdığı için ek apt paketi gerekmiyor.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Uygulama kodu
COPY . .

EXPOSE 5000

# Tek worker: starter.py içindeki in-process self-heal / haftalik reset mantığı
# (bkz. weekly-reset) tek instance varsayar; birden çok worker bunu mükerrer
# çalıştırır. AI/ağ çağrıları I/O-bound olduğu için eşzamanlılığı thread ile
# veriyoruz. timeout 300 = uzun süren AI Coach isteklerini kesmesin (nginx
# proxy_read_timeout 300 ile uyumlu).
CMD ["gunicorn", "--bind", "0.0.0.0:5000", \
     "--workers", "1", "--threads", "8", \
     "--timeout", "300", "--graceful-timeout", "30", \
     "--access-logfile", "-", "--error-logfile", "-", \
     "starter:app"]
