# FitX Flask uygulaması — production imajı.
# Python 3.11-slim tabanlı, gunicorn ile 5000 portundan servis eder.
# Base image @sha256 ile sabitlendi (3.2): tag float ediyordu ve aksi halde
# tamamen pinlenmiş requirements'a rağmen build tekrarlanabilir değildi. Bu
# çok-mimarili (multi-arch) index digest'tir; güncellerken yeni digest'i
# `docker buildx imagetools inspect python:3.11-slim` ile al.
FROM python:3.11-slim@sha256:b27df5841f3355e9473f9a516d38a6783b6c8dfeacaf2d14a240f443b368ddb6

# FITX_WEB_THREADS gunicorn thread sayısıyla eş tutulmalı — ai_gate boot
# denetimi AI+scrape kapılarının thread rezervini bu değere göre doğrular (I1).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    FITX_WEB_WORKERS=1 \
    FITX_WEB_THREADS=8

WORKDIR /app

# Önce sadece requirements'i kopyala — bağımlılık katmanı kod değişiminde
# yeniden derlenmez (Docker layer cache). psycopg2-binary ve Pillow manylinux
# wheel'leri kendi C kütüphanelerini taşıdığı için ek apt paketi gerekmiyor.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Uygulama kodu
COPY . .

# Root'tan düş: uygulamayı yetkisiz bir kullanıcı olarak çalıştır (savunma
# derinliği — bir kod-çalıştırma hatası konteyner içinde root olmasın).
# gunicorn 5000 portuna (>1024) bağlandığı için ayrıcalık gerekmez.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
ARG BUILD_REVISION
RUN case "$BUILD_REVISION" in \
      *[!0-9a-f]* | "" ) echo "BUILD_REVISION must be lowercase 40-hex" >&2; exit 64 ;; \
    esac && test "${#BUILD_REVISION}" -eq 40 && \
    printf '%s\n' "$BUILD_REVISION" > /app/BUILD_REVISION && \
    chown root:root /app/BUILD_REVISION && chmod 0444 /app/BUILD_REVISION
USER appuser

EXPOSE 5000

# Güvenli varsayılan 1 worker / 8 thread'dir. Worker sayısı yalnızca in-memory
# cache/limiter fallback varsayımları kaldırıldığında ve worker başına AI/scrape
# eşzamanlılık bütçelerinin toplam kapasiteye etkisi hesaba katıldığında artırılmalı.
# timeout 300 = uzun süren AI Coach isteklerini kesmesin (nginx proxy_read_timeout
# 300 ile uyumlu).

# Sağlık kontrolü: /health 200 dönmezse konteyner "unhealthy" işaretlenir.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=4).status==200 else 1)"

CMD ["gunicorn", "--config", "gunicorn.conf.py", "starter:app"]
