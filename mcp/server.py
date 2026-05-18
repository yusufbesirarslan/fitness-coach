"""
FitX MCP Server — provides read-only database tools for the AI Coach.

Usage:
    python -m mcp.server          (stdio transport, for Claude Desktop / SDK)
    python -m mcp.server --http   (streamable-http transport, for Flask integration)

Env vars:
    DATABASE_URL  — PostgreSQL connection string (required)
"""

import os
import json
import time
import threading
from datetime import datetime, date, timedelta
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

mcp = FastMCP(
    "FitX Coach Tools",
    instructions=(
        "Bu araçlar FitX fitness uygulamasının veritabanına salt-okunur erişim sağlar. "
        "Kullanıcının fitness verileri, antrenman geçmişi, supplement stack'i ve "
        "arkadaş aktiviteleri hakkında bilgi almak için kullan."
    ),
)

def _get_db_url():
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


@contextmanager
def get_conn():
    db_url = _get_db_url()
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    dsn = db_url.replace("postgresql://", "postgres://", 1)
    conn = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    conn.set_session(readonly=True, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def _level(xp: int) -> int:
    return 1 + (xp or 0) // 500


def _title(level: int) -> str:
    if level <= 5:
        return "Fitness Yolcusu"
    if level <= 10:
        return "Demir Bükücü"
    if level <= 20:
        return "Kas Mimarı"
    if level <= 50:
        return "FitX Efsanesi"
    return "Antrenman Tanrısı"


# ── TOOL 1: Fitness Summary ──────────────────────────────────────

@mcp.tool()
def get_user_fitness_summary(user_id: int) -> str:
    """Kullanıcının fitness özetini döndürür: seviye, unvan, seri, XP kazanımları ve temel istatistikler."""
    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute(
            'SELECT username, weight, height, age, gender, goal, '
            'fitness_level, current_activity, streak_count, rank_points, last_login '
            'FROM "user" WHERE id = %s',
            (user_id,),
        )
        user = cur.fetchone()
        if not user:
            return json.dumps({"error": "Kullanıcı bulunamadı"}, ensure_ascii=False)

        xp = user["rank_points"] or 0
        level = _level(xp)

        cur.execute(
            "SELECT weight, target_calories, bmr, tdee, goal, fitness_level, created_at "
            "FROM user_session WHERE user_id = %s ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        )
        session = cur.fetchone()

        cur.execute(
            "SELECT weight, created_at FROM weekly_check_in "
            "WHERE user_id = %s ORDER BY created_at DESC LIMIT 5",
            (user_id,),
        )
        checkins = cur.fetchall()

        cur.execute(
            "SELECT SUM(kalori) as total_cal, COUNT(*) as meal_count "
            "FROM meal_log WHERE user_id = %s AND tarih = %s",
            (user_id, date.today().strftime("%d.%m")),
        )
        today_meals = cur.fetchone()

        result = {
            "username": user["username"],
            "level": level,
            "title": _title(level),
            "xp": xp,
            "xp_for_next_level": 500 - (xp % 500),
            "streak_days": user["streak_count"] or 0,
            "last_login": str(user["last_login"]) if user["last_login"] else None,
            "goal": user["goal"],
            "fitness_level": user["fitness_level"],
            "current_weight": user["weight"],
            "height": user["height"],
            "age": user["age"],
            "gender": user["gender"],
        }

        if session:
            result["target_calories"] = round(session["target_calories"] or 0)
            result["bmr"] = round(session["bmr"] or 0)
            result["tdee"] = round(session["tdee"] or 0)

        if today_meals and today_meals["total_cal"]:
            result["today_calories_consumed"] = round(today_meals["total_cal"])
            result["today_meal_count"] = today_meals["meal_count"]

        if checkins:
            result["recent_checkins"] = [
                {"weight": c["weight"], "date": str(c["created_at"].date())}
                for c in checkins
            ]

        return json.dumps(result, ensure_ascii=False, default=str)


# ── TOOL 2: Workout History ─────────────────────────────────────

@mcp.tool()
def get_user_workout_history(user_id: int, days: int = 7) -> str:
    """Kullanıcının son X gündeki antrenman planlarını ve tamamlanan antrenmanları döndürür."""
    days = min(max(days, 1), 90)
    cutoff = datetime.utcnow() - timedelta(days=days)

    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute('SELECT id FROM "user" WHERE id = %s', (user_id,))
        if not cur.fetchone():
            return json.dumps({"error": "Kullanıcı bulunamadı"}, ensure_ascii=False)

        cur.execute(
            "SELECT plan_data, score, created_at FROM training_plan "
            "WHERE user_id = %s AND created_at >= %s "
            "ORDER BY created_at DESC",
            (user_id, cutoff),
        )
        plans = cur.fetchall()

        cur.execute(
            "SELECT uqp.date_key, dq.title FROM user_quest_progress uqp "
            "JOIN daily_quest dq ON dq.id = uqp.quest_id "
            "WHERE uqp.user_id = %s AND dq.quest_type = 'workout_logged' "
            "AND uqp.date_key >= %s "
            "ORDER BY uqp.date_key DESC",
            (user_id, cutoff.date().isoformat()),
        )
        completed = cur.fetchall()

        result = {
            "period_days": days,
            "training_plans": [],
            "completed_workouts": [],
            "total_workouts_completed": len(completed),
        }

        for p in plans:
            try:
                plan_parsed = json.loads(p["plan_data"]) if isinstance(p["plan_data"], str) else p["plan_data"]
            except (json.JSONDecodeError, TypeError):
                plan_parsed = p["plan_data"]
            result["training_plans"].append({
                "plan": plan_parsed,
                "score": p["score"],
                "created_at": str(p["created_at"].date()),
            })

        for c in completed:
            result["completed_workouts"].append({
                "date": c["date_key"],
                "quest": c["title"],
            })

        return json.dumps(result, ensure_ascii=False, default=str)


# ── TOOL 3: Supplement Stack ────────────────────────────────────

@mcp.tool()
def get_user_supplement_stack(user_id: int) -> str:
    """Kullanıcının supplement stack'ini döndürür: aktif ürünler, puanlar, yorumlar."""
    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute('SELECT id FROM "user" WHERE id = %s', (user_id,))
        if not cur.fetchone():
            return json.dumps({"error": "Kullanıcı bulunamadı"}, ensure_ascii=False)

        cur.execute(
            "SELECT product_name, brand, category, status, "
            "rating_effect, rating_taste, rating_digestion, rating_price, "
            "review_text, price_paid, created_at "
            "FROM supplement WHERE user_id = %s "
            "ORDER BY created_at DESC",
            (user_id,),
        )
        supps = cur.fetchall()

        result = {
            "total_supplements": len(supps),
            "active_count": sum(1 for s in supps if s["status"] == "Active"),
            "supplements": [],
        }

        for s in supps:
            result["supplements"].append({
                "product_name": s["product_name"],
                "brand": s["brand"],
                "category": s["category"],
                "status": s["status"],
                "ratings": {
                    "effect": s["rating_effect"],
                    "taste": s["rating_taste"],
                    "digestion": s["rating_digestion"],
                    "price": s["rating_price"],
                },
                "review": s["review_text"],
                "price_paid": s["price_paid"],
                "added": str(s["created_at"].date()),
            })

        return json.dumps(result, ensure_ascii=False, default=str)


# ── TOOL 4: Friend Activities ───────────────────────────────────

@mcp.tool()
def get_friend_activities(user_id: int) -> str:
    """Kullanıcının arkadaşlarının son aktivitelerini döndürür (sosyal bağlam için)."""
    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute('SELECT id FROM "user" WHERE id = %s', (user_id,))
        if not cur.fetchone():
            return json.dumps({"error": "Kullanıcı bulunamadı"}, ensure_ascii=False)

        cur.execute(
            "SELECT CASE WHEN sender_id = %s THEN receiver_id ELSE sender_id END AS friend_id "
            "FROM friendship WHERE status = 'accepted' "
            "AND (sender_id = %s OR receiver_id = %s)",
            (user_id, user_id, user_id),
        )
        friend_ids = [r["friend_id"] for r in cur.fetchall()]

        if not friend_ids:
            return json.dumps({
                "friends_count": 0,
                "activities": [],
                "message": "Henüz arkadaş yok.",
            }, ensure_ascii=False)

        cur.execute(
            'SELECT a.activity_type, a.content, a.timestamp, u.username, u.full_name '
            'FROM activity a JOIN "user" u ON u.id = a.user_id '
            "WHERE a.user_id = ANY(%s) "
            "ORDER BY a.timestamp DESC LIMIT 20",
            (friend_ids,),
        )
        activities = cur.fetchall()

        result = {
            "friends_count": len(friend_ids),
            "activities": [
                {
                    "username": a["username"],
                    "name": a["full_name"] or a["username"],
                    "type": a["activity_type"],
                    "content": a["content"],
                    "time": str(a["timestamp"]),
                }
                for a in activities
            ],
        }

        return json.dumps(result, ensure_ascii=False, default=str)


# ── TOOL 5: Nutrition Log ───────────────────────────────────────

@mcp.tool()
def get_user_nutrition_log(user_id: int, days: int = 3) -> str:
    """Kullanıcının son X gündeki beslenme kayıtlarını döndürür: öğünler, kalori, makrolar."""
    days = min(max(days, 1), 30)

    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute('SELECT id FROM "user" WHERE id = %s', (user_id,))
        if not cur.fetchone():
            return json.dumps({"error": "Kullanıcı bulunamadı"}, ensure_ascii=False)

        cutoff = datetime.utcnow() - timedelta(days=days)
        cur.execute(
            "SELECT ogun, yemekler, kalori, protein, karb, yag, tarih, created_at "
            "FROM meal_log WHERE user_id = %s AND created_at >= %s "
            "ORDER BY created_at DESC",
            (user_id, cutoff),
        )
        meals = cur.fetchall()

        cur.execute(
            "SELECT plan_data, score, created_at FROM nutrition_plan "
            "WHERE user_id = %s ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        )
        plan = cur.fetchone()

        by_date = {}
        for m in meals:
            d = m["tarih"] or str(m["created_at"].date())
            if d not in by_date:
                by_date[d] = {"meals": [], "total_cal": 0, "total_protein": 0, "total_carb": 0, "total_fat": 0}
            by_date[d]["meals"].append({
                "meal_type": m["ogun"],
                "foods": m["yemekler"],
                "calories": m["kalori"],
                "protein": m["protein"],
                "carbs": m["karb"],
                "fat": m["yag"],
            })
            by_date[d]["total_cal"] += m["kalori"] or 0
            by_date[d]["total_protein"] += m["protein"] or 0
            by_date[d]["total_carb"] += m["karb"] or 0
            by_date[d]["total_fat"] += m["yag"] or 0

        result = {
            "period_days": days,
            "total_meals_logged": len(meals),
            "daily_logs": by_date,
        }

        if plan:
            try:
                result["active_plan"] = json.loads(plan["plan_data"]) if isinstance(plan["plan_data"], str) else plan["plan_data"]
            except (json.JSONDecodeError, TypeError):
                result["active_plan"] = plan["plan_data"]
            result["plan_score"] = plan["score"]

        return json.dumps(result, ensure_ascii=False, default=str)


# ── FatSecret API — OAuth2 Token Cache ─────────────────────────

_fs_token_lock = threading.Lock()
_fs_token_cache = {"token": None, "expires_at": 0}

FATSECRET_TOKEN_URL = "https://oauth.fatsecret.com/connect/token"
FATSECRET_API_URL = "https://platform.fatsecret.com/rest/server.api"


def _get_fatsecret_token() -> str:
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
    data = resp.json()

    with _fs_token_lock:
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

        for item in macros.split("|"):
            item = item.strip()
            if ":" not in item:
                continue
            key, val = item.split(":", 1)
            key = key.strip().lower()
            val = val.strip().replace("kcal", "").replace("g", "").strip()
            try:
                parts[key] = float(val)
            except ValueError:
                parts[key] = val
    except Exception:
        return None
    return parts if len(parts) > 1 else None


# ── Entry point ─────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http", host="127.0.0.1", port=8100)
    else:
        mcp.run(transport="stdio")
