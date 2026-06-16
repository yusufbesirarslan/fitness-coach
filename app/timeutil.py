"""Uygulama saat dilimi yardımcıları — tek "bugün/şimdi" kaynağı.

FitX kullanıcıları Türkiye'de; "gün" sınırı (öğün günü, seri, görev, su) bu
yüzden sabit Europe/Istanbul saatine göre hesaplanır. UTC ve sunucu-yerel
date.today() karışımı, gün dönümünde tutarsızlıklara yol açıyordu.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

APP_TZ = ZoneInfo("Europe/Istanbul")
UTC = ZoneInfo("UTC")


def app_now():
    """Şu anki zaman, uygulama saat diliminde (tz-aware)."""
    return datetime.now(APP_TZ)


def app_today():
    """Bugünün tarihi, uygulama saat diliminde."""
    return app_now().date()


def day_key(dt=None):
    """Bir an için ISO gün anahtarı ('YYYY-MM-DD'), uygulama saat diliminde.

    dt verilmezse şu an kullanılır. tz-aware datetime APP_TZ'ye çevrilir; naive
    datetime APP_TZ kabul edilir. Yıl içerdiği için eski yıl-içermeyen '%d.%m'
    anahtarının ay/yıl çakışmalarını giderir (F4).
    """
    if dt is None:
        dt = app_now()
    elif dt.tzinfo is not None:
        dt = dt.astimezone(APP_TZ)
    return dt.date().isoformat()


def app_date_of(dt):
    """Bir datetime'ın Istanbul gün tarihini (date) döndür.

    created_at sütunları datetime.utcnow() ile NAIVE UTC yazıldığından, tz bilgisi
    yoksa UTC varsayılır (day_key'in "naive=APP_TZ" kuralından bilinçli olarak
    farklı). Geçen-gün hesapları bu fonksiyondan geçmeli; aksi halde UTC günü geç
    saatte (Istanbul sabahı) yazılan kayıt bir gün kayar.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(APP_TZ).date()


def utc_day_bounds(d=None):
    """Verilen (veya bugünkü) Istanbul gününün [başlangıç, bitiş) sınırlarını
    NAIVE UTC datetime olarak döndür.

    created_at sütunları datetime.utcnow() (naive UTC) ile yazıldığı için,
    "Istanbul günü" aralığını bunlarla doğru karşılaştırmak isteyen sorgular
    bu sınırları kullanır.
    """
    if d is None:
        d = app_today()
    start_local = datetime(d.year, d.month, d.day, tzinfo=APP_TZ)
    start_utc = start_local.astimezone(UTC).replace(tzinfo=None)
    end_utc = (start_local + timedelta(days=1)).astimezone(UTC).replace(tzinfo=None)
    return start_utc, end_utc


def display_ddmm(value):
    """ISO 'YYYY-MM-DD' (veya date) → 'DD.MM' gösterim etiketi (UI grafik/geçmiş
    etiketleri eski biçimi korusun; anahtar ISO kalır)."""
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            return value
    return value.strftime("%d.%m")
