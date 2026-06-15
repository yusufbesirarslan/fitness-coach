"""Tests for the app timezone helpers (app/timeutil.py).

Gün sınırının sabit Europe/Istanbul'a göre hesaplandığını ve gün anahtarının
ISO (yıl dahil) olduğunu sabitler — F4 (yıl-içermeyen çakışma) ve F5 (UTC vs
yerel karışımı) regresyonlarını engeller.

    python -m pytest tests/test_timeutil.py -v
"""
from datetime import date, datetime

from app.timeutil import APP_TZ, UTC, day_key, display_ddmm, utc_day_bounds


def test_day_key_is_iso_and_istanbul_aware():
    # UTC 20:30 = Istanbul 23:30 aynı gün; UTC 21:30 = Istanbul 00:30 ertesi gün.
    assert day_key(datetime(2026, 6, 15, 20, 30, tzinfo=UTC)) == "2026-06-15"
    assert day_key(datetime(2026, 6, 15, 21, 30, tzinfo=UTC)) == "2026-06-16"


def test_meals_at_2330_and_0030_fall_on_different_days():
    # Istanbul 23:30 (15.) ve 00:30 (16.) → farklı gün anahtarları (F5).
    a = datetime(2026, 6, 15, 23, 30, tzinfo=APP_TZ)
    b = datetime(2026, 6, 16, 0, 30, tzinfo=APP_TZ)
    assert day_key(a) == "2026-06-15"
    assert day_key(b) == "2026-06-16"
    assert day_key(a) != day_key(b)


def test_no_yearless_collision_across_years():
    # Aynı gün/ay, farklı yıl → eski '%d.%m' çakışırdı; ISO çakışmaz (F4).
    assert day_key(datetime(2025, 6, 15, 12, tzinfo=APP_TZ)) \
        != day_key(datetime(2026, 6, 15, 12, tzinfo=APP_TZ))


def test_utc_day_bounds_for_istanbul_day():
    # Istanbul 2026-06-15 [00:00, 24:00) = UTC [2026-06-14 21:00, 2026-06-15 21:00).
    start, end = utc_day_bounds(date(2026, 6, 15))
    assert start == datetime(2026, 6, 14, 21, 0)
    assert end == datetime(2026, 6, 15, 21, 0)


def test_display_ddmm_keeps_ui_format():
    assert display_ddmm("2026-06-15") == "15.06"
    assert display_ddmm(date(2026, 6, 15)) == "15.06"
    assert display_ddmm("bozuk") == "bozuk"  # parse edilemezse aynen döner
