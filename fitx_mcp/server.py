"""
FitX MCP Server — provides database tools (read + write) for the AI Coach.

Usage:
    python -m fitx_mcp          (stdio transport, for Claude Desktop / SDK)
    python -m fitx_mcp --http   (streamable-http transport, for Flask integration)

Env vars:
    DATABASE_URL  — PostgreSQL connection string (required)
"""

import os
import json
import time
import logging
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from contextlib import contextmanager

# L4: print() yerine logger — stdout'a yazmak logging/Sentry altyapısını atlıyordu
# (app tarafında zaten düzeltilmiş anti-pattern, bkz. ai_nutrition.py).
logger = logging.getLogger("fitx_mcp.server")

# Sabit uygulama saat dilimi (app.timeutil ile aynı): MCP sunucusu standalone
# çalışabildiği için tüm app paketini çekmemek adına burada inline tutulur.
_APP_TZ = ZoneInfo("Europe/Istanbul")
_UTC = ZoneInfo("UTC")


def _utc_day_bounds(d=None):
    """Verilen (veya bugünkü) Istanbul gününün [başlangıç, bitiş) sınırlarını
    NAIVE UTC datetime olarak döndür (app.timeutil.utc_day_bounds ile aynı).

    workout_log.created_at, datetime.utcnow() (naive UTC) ile yazılır ve `tarih`
    kolonu yoktur; 'Istanbul günü' aralığını bununla doğru karşılaştırmak için
    `created_at::date = CURRENT_DATE` (UTC günü) yerine bu sınırlar kullanılır —
    aksi halde 00:00–03:00 Istanbul arası toplamlar yanlış güne düşer (C2 sınıfı).
    `d` parametresi haftalık rapor gibi geçmiş gün sınırlarını da hesaplar.
    """
    if d is None:
        d = _app_today()
    start_local = datetime(d.year, d.month, d.day, tzinfo=_APP_TZ)
    start_utc = start_local.astimezone(_UTC).replace(tzinfo=None)
    end_utc = (start_local + timedelta(days=1)).astimezone(_UTC).replace(tzinfo=None)
    return start_utc, end_utc

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# N6: MealLog'a yazan HER yol makroları kanonik clamp'ten geçirmeli (DB CHECK
# yalnızca >100000 kcal kaba taşmayı yakalar, "3000 kcal çöpü"nü değil).
# nutrition_pipeline saf modüldür (Flask/DB importu yok) — standalone MCP
# sunucusuna güvenle çekilir. Doğrudan script çalıştırmada (`python
# fitx_mcp/server.py`) repo kökü sys.path'te olmaz (yalnızca `-m fitx_mcp`'de
# olur); kökü ekleyerek her iki giriş yolunda da import edilebilsin.
import sys as _sys
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)
from app.services.nutrition_pipeline import clamp_serving_macros
from app.services import coach_context_queries as _coach_context_queries
from app.services.coach_context_queries import (
    _app_today,
    _day_key,
    _get_db_url,
    _level,
    _title,
    get_conn,
)

load_dotenv()

# B1/D4: complete_workout'un yazdığı sentetik tamamlama satırı (volume=0)
# gerçek egzersiz değildir; tüm workout_log toplulaştırmaları onu dışlar.
# app/models.py:WORKOUT_COMPLETION_MARKER ile birebir aynı tutulmalı
# (standalone kalmak için app paketi import edilmez; eşitlik testte sabitlenir).
WORKOUT_COMPLETION_MARKER = "Antrenman tamamlandı (Pump Check)"

mcp = FastMCP(
    "FitX Coach Tools",
    instructions=(
        "Bu araçlar FitX fitness uygulamasının veritabanına erişim sağlar. "
        "Okuma araçları ile kullanıcı verilerini sorgula, yazma araçları ile "
        "antrenman ve beslenme kayıtlarını logla."
    ),
)

@contextmanager
def get_write_conn():
    db_url = _get_db_url()
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    dsn = db_url.replace("postgresql://", "postgres://", 1)
    conn = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# TOOL 1: Fitness Summary

@mcp.tool()
def get_user_fitness_summary(user_id: int) -> str:
    """Kullanıcının fitness özetini döndürür: seviye, unvan, seri, XP kazanımları ve temel istatistikler."""
    return _coach_context_queries.get_user_fitness_summary(user_id)


# TOOL 2: Workout History

@mcp.tool()
def get_user_workout_history(user_id: int, days: int = 7) -> str:
    """Kullanıcının son X gündeki antrenman planlarını ve tamamlanan antrenmanları döndürür."""
    return _coach_context_queries.get_user_workout_history(user_id, days)


# TOOL 3: Supplement Stack

@mcp.tool()
def get_user_supplement_stack(user_id: int) -> str:
    """Kullanıcının supplement stack'ini döndürür: aktif ürünler, puanlar, yorumlar."""
    return _coach_context_queries.get_user_supplement_stack(user_id)


# TOOL 4: Friend Activities

@mcp.tool()
def get_friend_activities(user_id: int) -> str:
    """Kullanıcının arkadaşlarının son aktivitelerini döndürür (sosyal bağlam için)."""
    return _coach_context_queries.get_friend_activities(user_id)


# TOOL 5: Nutrition Log

@mcp.tool()
def get_user_nutrition_log(user_id: int, days: int = 3) -> str:
    """Kullanıcının son X gündeki beslenme kayıtlarını döndürür: öğünler, kalori, makrolar."""
    return _coach_context_queries.get_user_nutrition_log(user_id, days)


# ── FatSecret API — OAuth2 Token Cache ─────────────────────────

_fs_token_lock = threading.Lock()
_fs_token_cache = {"token": None, "expires_at": 0}

FATSECRET_TOKEN_URL = "https://oauth.fatsecret.com/connect/token"
# Güvensiz varsayılan YOK: ayarlanmazsa boş kalır. Eski hardcoded
# "http://<public-ip>:3000" varsayılanı OAuth bearer token'ını düz metin HTTP
# üzerinde sızdırıyordu (app/config.py ile aynı sorun). MCP sunucusu standalone
# çalışabildiği için TLS zorunluluğu burada da uygulanır (_enforce_fatsecret_tls).
FATSECRET_BASE_URL = os.environ.get("FATSECRET_BASE_URL", "")
FATSECRET_API_URL = f"{FATSECRET_BASE_URL}/rest/server.api"


def _enforce_fatsecret_tls() -> None:
    """app/config.py:_enforce_fatsecret_tls'in MCP karşılığı. Bearer token
    Authorization header'ında gittiğinden düz metin HTTP (loopback dışı) kabul
    edilmez. Ayarlanmamış/güvensiz URL'de FatSecret çağrısını reddet."""
    from urllib.parse import urlparse as _urlparse
    if not FATSECRET_BASE_URL:
        raise RuntimeError(
            "FATSECRET_BASE_URL ayarlı değil — FatSecret entegrasyonu çalışmaz. "
            "https:// bir endpoint'e işaret ettir."
        )
    p = _urlparse(FATSECRET_BASE_URL)
    host = (p.hostname or "").lower()
    is_local = host in ("localhost", "127.0.0.1", "::1")
    if p.scheme == "https" or is_local:
        return
    if os.environ.get("FATSECRET_ALLOW_INSECURE") == "1":
        return
    raise RuntimeError(
        "FATSECRET_BASE_URL must use https:// — the FatSecret OAuth token is sent "
        "in the Authorization header and would otherwise travel in cleartext. "
        "Point FATSECRET_BASE_URL at an https:// endpoint."
    )


def _get_fatsecret_token() -> str:
    _enforce_fatsecret_tls()
    # M6: kilit, getirme dahil TÜM kritik bölümü kapsar (app-side fatsecret.py ile
    # aynı). Eski sürümde kilit yalnızca önbellek OKUMASINI sarıyor, ağ çağrısı
    # kilitsiz yapılıp yazma ayrı kilitte oluyordu → iki eşzamanlı çağrı ikisi de
    # token çekerek thundering-herd yapabiliyordu. Token getirme seyrek olduğundan
    # kilidi ağ çağrısı boyunca tutmak sorun değil.
    with _fs_token_lock:
        if _fs_token_cache["token"] and time.time() < _fs_token_cache["expires_at"] - 60:
            return _fs_token_cache["token"]

        client_id = os.environ.get("FATSECRET_CLIENT_ID", "")
        client_secret = os.environ.get("FATSECRET_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            raise RuntimeError("FATSECRET_CLIENT_ID / FATSECRET_CLIENT_SECRET not set")

        resp = requests.post(
            FATSECRET_TOKEN_URL,
            data={"grant_type": "client_credentials", "scope": "basic"},
            auth=(client_id, client_secret),
            timeout=10,
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError as e:
            # Hata sayfası/HTML döndüyse json() patlar; önbelleği bozma, anlamlı hata ver.
            raise RuntimeError(f"FatSecret token yanıtı JSON değil: {e}") from e
        # M6: OAuth hata yükü ({"error": "..."}) veya eksik access_token: ham
        # data["access_token"] KeyError yerine açık hata at; önbelleği ASLA boş/
        # yanlış token'la doldurma (app-side ile aynı doğrulama).
        if not isinstance(data, dict) or data.get("error") or not data.get("access_token"):
            err = data.get("error") if isinstance(data, dict) else data
            raise RuntimeError(f"FatSecret token alınamadı: {err}")

        _fs_token_cache["token"] = data["access_token"]
        _fs_token_cache["expires_at"] = time.time() + data.get("expires_in", 86400)
        return data["access_token"]


# ── TOOL 6: Nutrition Data Search (FatSecret) ─────────────────

@mcp.tool()
def search_nutrition_data(query: str) -> str:
    """FatSecret API ile besin verisi arar. Kalori, protein, karb, yağ bilgisi döndürür. Örnek: '200g grilled chicken'."""
    if not query.strip():
        return json.dumps({"error": "Arama sorgusu boş olamaz."}, ensure_ascii=False)

    try:
        token = _get_fatsecret_token()
    except Exception as e:
        return json.dumps({"error": f"FatSecret auth hatası: {e}"}, ensure_ascii=False)

    try:
        resp = requests.get(
            FATSECRET_API_URL,
            params={
                "method": "foods.search",
                "search_expression": query,
                "format": "json",
                "max_results": 5,
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return json.dumps({"error": f"FatSecret API hatası: {e}"}, ensure_ascii=False)

    foods_wrapper = data.get("foods", {})
    food_list = foods_wrapper.get("food", [])
    if not food_list:
        return json.dumps({
            "query": query,
            "results": [],
            "message": "Sonuç bulunamadı.",
        }, ensure_ascii=False)

    if isinstance(food_list, dict):
        food_list = [food_list]

    results = []
    for f in food_list:
        desc = f.get("food_description", "")
        entry = {
            "name": f.get("food_name", ""),
            "type": f.get("food_type", ""),
            "brand": f.get("brand_name", ""),
            "description": desc,
        }
        parsed = _parse_fatsecret_desc(desc)
        if parsed:
            entry["per_serving"] = parsed
        results.append(entry)

    return json.dumps({
        "query": query,
        "results": results,
    }, ensure_ascii=False, default=str)


def _parse_fatsecret_desc(desc: str) -> dict | None:
    """Parse FatSecret's 'Per 100g - Calories: 165kcal | Fat: 3.57g | Carbs: 0.00g | Protein: 31.02g' format."""
    import re as _re
    if not desc:
        return None
    parts = {}
    try:
        segments = desc.split(" - ", 1)
        if len(segments) == 2:
            parts["serving"] = segments[0].strip()
            macros = segments[1]
        else:
            macros = desc

        _num_pat = _re.compile(r"(\d+(?:[.,]\d+)?)")
        for item in macros.split("|"):
            item = item.strip()
            if ":" not in item:
                continue
            key, val = item.split(":", 1)
            key = key.strip().lower()
            num_match = _num_pat.search(val)
            if num_match:
                num_str = num_match.group(1).replace(",", ".")
                try:
                    parts[key] = float(num_str)
                except ValueError:
                    logger.warning("[FATSECRET PARSE] Failed to convert '%s' from key='%s', val='%s'", num_str, key, val.strip())
                    parts[key] = 0.0
            else:
                logger.warning("[FATSECRET PARSE] No number found in key='%s', val='%s'", key, val.strip())
    except Exception:
        logger.warning("[FATSECRET PARSE] Exception parsing desc — desc='%s'", desc[:200], exc_info=True)
        return None
    return parts if len(parts) > 1 else None


# ── TOOL 7: Log Workout Entry (WRITE) ──────────────────────────

@mcp.tool()
def log_workout_entry(user_id: int, exercise_name: str, sets: int, reps: int, weight_kg: float) -> str:
    """Kullanıcının antrenman kaydını veritabanına yazar. Onay alındıktan sonra çağrılmalı."""
    if sets <= 0 or reps <= 0 or weight_kg < 0:
        return json.dumps({"error": "Geçersiz değerler: set, tekrar > 0, ağırlık >= 0 olmalı."}, ensure_ascii=False)
    if not exercise_name.strip():
        return json.dumps({"error": "Egzersiz adı boş olamaz."}, ensure_ascii=False)

    volume = sets * reps * weight_kg

    with get_write_conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id FROM "user" WHERE id = %s', (user_id,))
        if not cur.fetchone():
            return json.dumps({"error": "Kullanıcı bulunamadı"}, ensure_ascii=False)

        cur.execute(
            "INSERT INTO workout_log (user_id, exercise_name, sets, reps, weight_kg, volume, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (user_id, exercise_name.strip(), sets, reps, weight_kg, volume, datetime.utcnow()),
        )
        row = cur.fetchone()

        # C2 sınıfı: günlük toplamı Istanbul gün sınırlarıyla topla. Eski
        # `created_at::date = CURRENT_DATE` UTC gününe bakıyordu; 00:00–03:00
        # Istanbul arası "bugün" yanlış çıkıyordu. workout_log'da tarih kolonu yok,
        # bu yüzden naive-UTC created_at'i UTC gün-sınırlarıyla filtreliyoruz.
        day_start, day_end = _utc_day_bounds()
        cur.execute(
            "SELECT COALESCE(SUM(volume), 0) as total_volume, COUNT(*) as entry_count "
            "FROM workout_log WHERE user_id = %s AND exercise_name <> %s "
            "AND created_at >= %s AND created_at < %s",
            (user_id, WORKOUT_COMPLETION_MARKER, day_start, day_end),
        )
        today = cur.fetchone()

    return json.dumps({
        "success": True,
        "id": row["id"],
        "exercise": exercise_name.strip(),
        "sets": sets,
        "reps": reps,
        "weight_kg": weight_kg,
        "volume": volume,
        "today_total_volume": round(today["total_volume"]),
        "today_entry_count": today["entry_count"],
    }, ensure_ascii=False)


# ── TOOL 8: Log Nutrition Entry (WRITE) ────────────────────────

@mcp.tool()
def log_nutrition_entry(user_id: int, food_item: str, calories: float, protein: float, carbs: float, fat: float) -> str:
    """Kullanıcının beslenme kaydını veritabanına yazar. Makro değerleri FatSecret'tan alınabilir."""
    if not food_item.strip():
        return json.dumps({"error": "Yiyecek adı boş olamaz."}, ensure_ascii=False)
    if calories < 0 or protein < 0 or carbs < 0 or fat < 0:
        return json.dumps({"error": "Makro değerleri negatif olamaz."}, ensure_ascii=False)

    # N6: in-app koç yolu (ai_coach.py) clamp'liyor; bu standalone MCP yazma yolu da
    # kanonik tavanlara oranla kısmalı ki bir LLM saçmalığı (9999 kcal) deftere sızmasın.
    calories, protein, carbs, fat = clamp_serving_macros(calories, protein, carbs, fat)

    with get_write_conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT id FROM "user" WHERE id = %s', (user_id,))
        if not cur.fetchone():
            return json.dumps({"error": "Kullanıcı bulunamadı"}, ensure_ascii=False)

        cur.execute(
            "INSERT INTO meal_log (user_id, ogun, yemekler, kalori, protein, karb, yag, tarih, source, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (user_id, "AI Koç", food_item.strip(), calories, protein, carbs, fat,
             _day_key(), "coach", datetime.utcnow()),
        )
        row = cur.fetchone()

        # C2: günlük toplamı Istanbul gün anahtarıyla (tarih) topla — INSERT'teki
        # tarih=_day_key() ile aynı. Eski `created_at::date = CURRENT_DATE` UTC
        # gününe bakıyordu; 00:00–03:00 Istanbul arası toplamlar yanlış çıkıyordu.
        cur.execute(
            "SELECT COALESCE(SUM(kalori), 0) as total_cal, "
            "COALESCE(SUM(protein), 0) as total_protein, "
            "COALESCE(SUM(karb), 0) as total_carbs, "
            "COALESCE(SUM(yag), 0) as total_fat, "
            "COUNT(*) as entry_count "
            "FROM meal_log WHERE user_id = %s AND tarih = %s",
            (user_id, _day_key()),
        )
        today = cur.fetchone()

    return json.dumps({
        "success": True,
        "id": row["id"],
        "food_item": food_item.strip(),
        "calories": calories,
        "protein": protein,
        "carbs": carbs,
        "fat": fat,
        "today_totals": {
            "calories": round(today["total_cal"]),
            "protein": round(today["total_protein"], 1),
            "carbs": round(today["total_carbs"], 1),
            "fat": round(today["total_fat"], 1),
            "entry_count": today["entry_count"],
        },
    }, ensure_ascii=False)


# ── TOOL 9: Weekly Performance Report ──────────────────────────

@mcp.tool()
def generate_weekly_report(user_id: int) -> str:
    """Kullanıcının haftalık performans raporunu oluşturur: bu hafta vs geçen hafta karşılaştırması."""
    today = _app_today()
    this_week_start = today - timedelta(days=today.weekday())
    last_week_start = this_week_start - timedelta(days=7)
    # C2 sınıfı: hafta sınırlarını UTC olarak hesapla (created_at naive UTC).
    # `created_at::date` UTC gününe bakıyordu → gece geç saatlerde (Istanbul)
    # yazılan kayıtlar yanlış haftaya kayıyordu. this_week_start_utc aynı zamanda
    # geçen haftanın (dışlayıcı) üst sınırıdır.
    this_week_start_utc, _ = _utc_day_bounds(this_week_start)
    last_week_start_utc, _ = _utc_day_bounds(last_week_start)

    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute('SELECT id, goal FROM "user" WHERE id = %s', (user_id,))
        user = cur.fetchone()
        if not user:
            return json.dumps({"error": "Kullanıcı bulunamadı"}, ensure_ascii=False)

        cur.execute(
            "SELECT exercise_name, SUM(volume) as total_vol, SUM(sets) as total_sets, "
            "COUNT(*) as entries, MAX(weight_kg) as max_weight "
            "FROM workout_log WHERE user_id = %s AND exercise_name <> %s "
            "AND created_at >= %s "
            "GROUP BY exercise_name ORDER BY total_vol DESC",
            (user_id, WORKOUT_COMPLETION_MARKER, this_week_start_utc),
        )
        this_week_workouts = cur.fetchall()

        cur.execute(
            "SELECT COALESCE(SUM(volume), 0) as vol FROM workout_log "
            "WHERE user_id = %s AND exercise_name <> %s "
            "AND created_at >= %s AND created_at < %s",
            (user_id, WORKOUT_COMPLETION_MARKER, last_week_start_utc, this_week_start_utc),
        )
        last_week_vol = cur.fetchone()["vol"]

        cur.execute(
            "SELECT COALESCE(SUM(kalori), 0) as cal, COALESCE(SUM(protein), 0) as pro, "
            "COALESCE(SUM(karb), 0) as carb, COALESCE(SUM(yag), 0) as fat, COUNT(*) as entries "
            "FROM meal_log WHERE user_id = %s AND created_at >= %s",
            (user_id, this_week_start_utc),
        )
        this_week_nutrition = cur.fetchone()

        cur.execute(
            "SELECT COALESCE(SUM(kalori), 0) as cal, COALESCE(SUM(protein), 0) as pro "
            "FROM meal_log "
            "WHERE user_id = %s AND created_at >= %s AND created_at < %s",
            (user_id, last_week_start_utc, this_week_start_utc),
        )
        last_week_nutrition = cur.fetchone()

    this_week_total_vol = sum(w["total_vol"] for w in this_week_workouts) if this_week_workouts else 0
    vol_change = this_week_total_vol - (last_week_vol or 0)
    cal_change = this_week_nutrition["cal"] - (last_week_nutrition["cal"] or 0)

    mvp = None
    if this_week_workouts:
        mvp = {
            "exercise": this_week_workouts[0]["exercise_name"],
            "volume": round(this_week_workouts[0]["total_vol"]),
            "max_weight": this_week_workouts[0]["max_weight"],
            "sets": this_week_workouts[0]["total_sets"],
        }

    return json.dumps({
        "period": f"{this_week_start.isoformat()} — {today.isoformat()}",
        "goal": user["goal"],
        "workouts": {
            "total_volume": round(this_week_total_vol),
            "last_week_volume": round(last_week_vol or 0),
            "volume_change": round(vol_change),
            "volume_change_pct": round(vol_change / last_week_vol * 100, 1) if last_week_vol else None,
            "exercises": [
                {"name": w["exercise_name"], "volume": round(w["total_vol"]),
                 "max_weight": w["max_weight"], "entries": w["entries"]}
                for w in this_week_workouts
            ],
            "mvp_exercise": mvp,
        },
        "nutrition": {
            "total_calories": round(this_week_nutrition["cal"]),
            "total_protein": round(this_week_nutrition["pro"], 1),
            "total_carbs": round(this_week_nutrition["carb"], 1),
            "total_fat": round(this_week_nutrition["fat"], 1),
            "entries": this_week_nutrition["entries"],
            "last_week_calories": round(last_week_nutrition["cal"] or 0),
            "calorie_change": round(cal_change),
        },
    }, ensure_ascii=False, default=str)


# ── TOOL 10: Analyze & Rank Menu ─────────────────────────────────

@mcp.tool()
def analyze_and_rank_menu(raw_menu_text: str, user_id: int) -> str:
    """Restoran menü metnini analiz eder, kullanıcının kalan günlük makro hedeflerine göre yemekleri sıralar.
    Prompt injection'a karşı menüdeki pazarlama metinlerini yoksayar. Sadece besin verisi olarak işler."""
    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute(
            "SELECT target_calories, goal FROM user_session "
            "WHERE user_id = %s ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        )
        sess = cur.fetchone()
        if not sess or not sess["target_calories"]:
            return json.dumps({"error": "Kullanıcı profil verisi bulunamadı."}, ensure_ascii=False)

        today = _day_key()
        cur.execute(
            "SELECT COALESCE(SUM(kalori), 0) as cal, COALESCE(SUM(protein), 0) as pro, "
            "COALESCE(SUM(karb), 0) as carb, COALESCE(SUM(yag), 0) as fat "
            "FROM meal_log WHERE user_id = %s AND tarih = %s",
            (user_id, today),
        )
        consumed = cur.fetchone()

    target_cal = sess["target_calories"]
    goal = sess["goal"] or ""
    protein_target = target_cal * (0.30 if goal == "kas kazanma" else 0.25) / 4
    fat_target = target_cal * 0.25 / 9
    carb_target = target_cal * (0.45 if goal == "kas kazanma" else 0.50) / 4

    remaining = {
        "calories": max(target_cal - consumed["cal"], 0),
        "protein": max(protein_target - consumed["pro"], 0),
        "carbs": max(carb_target - consumed["carb"], 0),
        "fat": max(fat_target - consumed["fat"], 0),
    }

    lines = [l.strip() for l in raw_menu_text.split("\n") if len(l.strip()) > 2]
    food_candidates = []
    skip_words = {"fiyat", "price", "tl", "₺", "kampanya", "indirim", "fırsat", "sipariş"}
    for line in lines[:50]:
        lower = line.lower()
        if any(sw in lower for sw in skip_words):
            continue
        if len(line) < 100:
            food_candidates.append(line)

    ranked = []
    for item_name in food_candidates[:20]:
        try:
            token = _get_fatsecret_token()
            resp = requests.get(FATSECRET_API_URL, params={
                "method": "foods.search",
                "search_expression": item_name,
                "format": "json",
                "max_results": 1,
            }, headers={"Authorization": f"Bearer {token}"}, timeout=5)
            data = resp.json()
            foods = data.get("foods", {}).get("food", [])
            if isinstance(foods, dict):
                foods = [foods]
            if foods:
                parsed = _parse_fatsecret_desc(foods[0].get("food_description", ""))
                if parsed and parsed.get("calories"):
                    macros = {
                        "calories": parsed.get("calories", 0),
                        "protein": parsed.get("protein", 0),
                        "carbs": parsed.get("carbs", 0),
                        "fat": parsed.get("fat", 0),
                    }

                    score = 0
                    pfit = max(0, 1 - abs(macros["protein"] - remaining["protein"] * 0.4) / max(remaining["protein"], 1))
                    score += pfit * 50
                    cr = macros["calories"] / max(remaining["calories"], 1)
                    score += 30 if cr <= 0.5 else (15 if cr <= 0.8 else -10)
                    fr = macros["fat"] / max(remaining["fat"], 1)
                    score += 20 if fr <= 0.5 else (5 if fr <= 0.8 else -15)

                    warnings = []
                    if macros["fat"] > remaining["fat"] * 0.8:
                        warnings.append("Günlük yağ limitinin %80'ini aşıyor")
                    if macros["calories"] > remaining["calories"] * 0.8:
                        warnings.append("Günlük kalori limitinin %80'ini aşıyor")

                    ranked.append({
                        "name": item_name,
                        "macros": {k: round(v, 1) for k, v in macros.items()},
                        "score": round(score, 1),
                        "warnings": warnings,
                    })
        except Exception:
            continue

    ranked.sort(key=lambda x: x["score"], reverse=True)

    return json.dumps({
        "ranked_items": ranked[:10],
        "remaining_macros": {k: round(v, 1) for k, v in remaining.items()},
        "analysis": f"{len(ranked)} yemek analiz edildi, protein ve kalori uyumuna göre sıralandı.",
    }, ensure_ascii=False, default=str)


# ── Entry point ─────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if "--http" in sys.argv:
        # Aynı kapı fitx_mcp/__main__.py'de de var; bu blok doğrudan
        # `python fitx_mcp/server.py --http` çalıştırılırsa onu atlamasın.
        # Araçlar user_id'yi parametre alır ve kendi yetkilendirmesi yoktur —
        # HTTP taşıması yalnızca açık opt-in + loopback ile açılır.
        if os.environ.get("FITX_MCP_ALLOW_HTTP") != "1":
            sys.exit(
                "MCP HTTP taşıması başlatılmıyor: kimliksiz, çapraz-kullanıcı "
                "veritabanı erişimi açığa çıkarır. Riski anlıyorsan (yalnızca "
                "loopback) FITX_MCP_ALLOW_HTTP=1 ayarla."
            )
        mcp.run(transport="streamable-http", host="127.0.0.1", port=8100)
    else:
        mcp.run(transport="stdio")
