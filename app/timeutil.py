"""Uygulama saat dilimi yardımcıları — tek "bugün/şimdi" kaynağı.

FitX kullanıcıları Türkiye'de; "gün" sınırı (öğün günü, seri, görev, su) bu
yüzden sabit Europe/Istanbul saatine göre hesaplanır. UTC ve sunucu-yerel
date.today() karışımı, gün dönümünde tutarsızlıklara yol açıyordu.

Faz A'da pasif (inert) eklenir; Faz C'de tüm gün-anahtarı mantığı buraya bağlanır.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

APP_TZ = ZoneInfo("Europe/Istanbul")


def app_now():
    """Şu anki zaman, uygulama saat diliminde (tz-aware)."""
    return datetime.now(APP_TZ)


def app_today():
    """Bugünün tarihi, uygulama saat diliminde."""
    return app_now().date()


def day_key(dt=None):
    """Bir an için ISO gün anahtarı ('YYYY-MM-DD'), uygulama saat diliminde.

    dt verilmezse şu an kullanılır. tz-aware bir datetime verilirse APP_TZ'ye
    çevrilir; naive datetime'lar APP_TZ kabul edilir.
    """
    if dt is None:
        dt = app_now()
    elif dt.tzinfo is not None:
        dt = dt.astimezone(APP_TZ)
    return dt.date().isoformat()
