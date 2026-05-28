from flask import Flask, request, jsonify, render_template , redirect , url_for , session , Response, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager , UserMixin , login_user , logout_user , login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
import boto3
from botocore.exceptions import ClientError
import os
import json
import click
import re
import threading
import time
import requests as http_requests_lib
from dotenv import load_dotenv
load_dotenv()
app = Flask(__name__)
_BOOT_TS = int(time.time())  # cache-bust static assets on each deploy

# ── FatSecret API (inlined to avoid psycopg2 dependency from fitx_mcp.server) ──

FATSECRET_TOKEN_URL = "https://oauth.fatsecret.com/connect/token"
FATSECRET_API_URL = "https://platform.fatsecret.com/rest/server.api"

def _fs_get(url, **kwargs):
    """GET request to FatSecret API."""
    kwargs.setdefault("timeout", 10)
    return http_requests_lib.get(url, **kwargs)


def _fs_post(url, **kwargs):
    """POST request to FatSecret API."""
    kwargs.setdefault("timeout", 10)
    return http_requests_lib.post(url, **kwargs)


_fs_token_lock = threading.Lock()
_fs_token_cache = {"token": None, "expires_at": 0}


def _get_fatsecret_token() -> str:
    with _fs_token_lock:
        if _fs_token_cache["token"] and time.time() < _fs_token_cache["expires_at"] - 60:
            return _fs_token_cache["token"]
    client_id = os.environ.get("FATSECRET_CLIENT_ID", "")
    client_secret = os.environ.get("FATSECRET_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError("FATSECRET_CLIENT_ID / FATSECRET_CLIENT_SECRET not set")
    resp = _fs_post(
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


def _parse_fatsecret_desc(desc: str) -> dict | None:
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
        _num_pat = re.compile(r"(\d+(?:[.,]\d+)?)")
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
                    parts[key] = 0.0
    except Exception:
        return None
    return parts if len(parts) > 1 else None


def _food_get_servings(food_id):
    try:
        token = _get_fatsecret_token()
        app.logger.info("_food_get_servings: got token for food_id=%s", food_id)
    except Exception as e:
        app.logger.error("_food_get_servings: token failed: %s", e)
        return None

    servings_raw = None
    for method in ("food.get.v4", "food.get.v2", "food.get"):
        try:
            resp = _fs_get(FATSECRET_API_URL, params={
                "method": method,
                "food_id": food_id,
                "format": "json",
            }, headers={"Authorization": f"Bearer {token}"}, timeout=5)
            data = resp.json()
            app.logger.info("_food_get_servings %s status=%s keys=%s",
                            method, resp.status_code, list(data.keys())[:5])
        except Exception as e:
            app.logger.warning("_food_get_servings %s failed: %s", method, e)
            continue

        if "error" in data:
            app.logger.warning("_food_get_servings %s error: %s", method, data["error"])
            continue

        try:
            servings_raw = data["food"]["servings"]["serving"]
            if isinstance(servings_raw, dict):
                servings_raw = [servings_raw]
            app.logger.info("_food_get_servings %s OK: %d servings", method, len(servings_raw))
            break
        except (KeyError, TypeError):
            app.logger.warning("_food_get_servings %s: no servings in response keys=%s",
                               method, list(data.get("food", {}).keys()) if "food" in data else list(data.keys()))
            continue

    if not servings_raw:
        return None

    results = []
    for s in servings_raw:
        try:
            metric_amt = float(s.get("metric_serving_amount", 0))
            if metric_amt == 0:
                app.logger.warning("_food_get_servings food_id=%s: serving '%s' has no metric_serving_amount",
                                   food_id, s.get("serving_description", "?"))
            results.append({
                "serving_id": str(s.get("serving_id", "")),
                "serving_description": s.get("serving_description", ""),
                "metric_serving_amount": metric_amt,
                "metric_serving_unit": s.get("metric_serving_unit", "g"),
                "calories": float(s.get("calories", 0)),
                "protein": float(s.get("protein", 0)),
                "carbs": float(s.get("carbohydrate", 0)),
                "fat": float(s.get("fat", 0)),
            })
        except (ValueError, TypeError):
            continue

    if not results:
        return None

    # Inject a synthetic "100 g" serving if none exists
    has_100g = any(abs(r["metric_serving_amount"] - 100) < 0.5 for r in results)
    if not has_100g:
        donor = max((r for r in results if r["metric_serving_amount"] > 0),
                    key=lambda r: r["metric_serving_amount"], default=None)
        if donor:
            scale = 100.0 / donor["metric_serving_amount"]
            results.append({
                "serving_id": "100g_calc",
                "serving_description": "100 g",
                "metric_serving_amount": 100.0,
                "metric_serving_unit": "g",
                "calories": round(donor["calories"] * scale, 1),
                "protein": round(donor["protein"] * scale, 1),
                "carbs": round(donor["carbs"] * scale, 1),
                "fat": round(donor["fat"] * scale, 1),
            })
        else:
            app.logger.warning("_food_get_servings food_id=%s: cannot derive 100g — all servings lack metric_serving_amount", food_id)

    return results


database_url = os.environ.get("DATABASE_URL", "sqlite:///chatbot.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-123")
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login" # Giriş zaten yapılıysa yönlendirme
bedrock_runtime = boto3.client(
    'bedrock-runtime',
    region_name=os.getenv('AWS_REGION', 'us-east-1'),
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
)
BEDROCK_MODEL_ID = "us.anthropic.claude-sonnet-4-6"


def _bedrock_chat(messages, system_prompt=None, max_tokens=1024, temperature=0.7):
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if temperature is not None:
        body["temperature"] = temperature
    if system_prompt:
        body["system"] = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"}
            }
        ]
    response = bedrock_runtime.invoke_model(
        body=json.dumps(body),
        modelId=BEDROCK_MODEL_ID,
        contentType='application/json',
        accept='application/json',
    )
    result = json.loads(response['body'].read())
    return result['content'][0]['text']

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

class User(UserMixin, db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Profil bilgileri
    weight           = db.Column(db.Float)
    height           = db.Column(db.Float)
    age              = db.Column(db.Integer)
    gender           = db.Column(db.String(10))
    goal             = db.Column(db.String(50))
    fitness_level    = db.Column(db.String(20))
    current_activity = db.Column(db.String(20))
    profile_complete = db.Column(db.Boolean, default=False)

    profile_picture  = db.Column(db.Text, nullable=True)
    full_name        = db.Column(db.String(150), nullable=True)
    target_weight    = db.Column(db.Float, nullable=True)
    goal_type        = db.Column(db.String(10), nullable=True)
    streak_count     = db.Column(db.Integer, default=0, server_default='0')
    rank_points      = db.Column(db.Integer, default=0, server_default='0')
    last_login       = db.Column(db.Date, nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.username}>"


class UserSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    name = db.Column(db.String(100))
    age = db.Column(db.Integer)
    gender = db.Column(db.String(10))
    weight = db.Column(db.Float)
    height = db.Column(db.Float)
    goal = db.Column(db.String(50))
    fitness_level = db.Column(db.String(20))
    current_activity = db.Column(db.String(20))
    bmr = db.Column(db.Float)
    tdee = db.Column(db.Float)
    target_calories = db.Column(db.Float)
    training_plan = db.Column(db.Text)
    nutrition_plan = db.Column(db.Text)
    coach_reply = db.Column(db.Text)
    created_at = db.Column(db.DateTime , default= datetime.utcnow)

    def __repr__(self):
        return f"<UserSession {self.name} - {self.created_at}>"
    
class WeeklyLog(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    weight     = db.Column(db.Float, nullable=False)
    note       = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<WeeklyLog {self.user_id} - {self.weight}kg - {self.created_at}>"
    
@app.route("/setup", methods=["GET", "POST"])
@login_required
def setup():
    if request.method == "GET":
        return render_template("setup.html", username=current_user.username)

    data = request.get_json()

    required = ["weight", "height", "age", "gender", "goal", "fitness_level", "current_activity"]
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"{field} alanı eksik"}), 400

    try:
        current_user.weight           = float(data["weight"])
        current_user.height           = float(data["height"])
        current_user.age              = int(data["age"])
        current_user.gender           = data["gender"]
        current_user.goal             = data["goal"]
        current_user.fitness_level    = data["fitness_level"]
        current_user.current_activity = data["current_activity"]
        current_user.profile_complete = True
        current_user.goal_type = "loss" if data["goal"] == "kilo verme" else "gain"
        if data.get("target_weight"):
            try:
                current_user.target_weight = float(data["target_weight"])
            except (ValueError, TypeError):
                pass
        db.session.commit()
    except ValueError:
        return jsonify({"error": "Kilo, boy ve yaş sayısal olmalıdır"}), 400

    # İlk oturumu oluştur
    bmr             = calculate_bmr(current_user.weight, current_user.height, current_user.age, current_user.gender)
    tdee            = calculate_tdee(bmr, current_user.current_activity)
    target_calories = calculate_target(tdee, current_user.goal)
    training_plan   = generate_training_plan(current_user.goal, current_user.fitness_level)
    nutrition_plan  = generate_nutrition_plan(current_user.goal, target_calories)

    session_entry = UserSession(
        name=current_user.username,
        age=current_user.age, gender=current_user.gender,
        weight=current_user.weight, height=current_user.height,
        goal=current_user.goal, fitness_level=current_user.fitness_level,
        current_activity=current_user.current_activity,
        bmr=bmr, tdee=tdee, target_calories=target_calories,
        training_plan=training_plan, nutrition_plan=nutrition_plan,
        coach_reply="", user_id=current_user.id
    )
    db.session.add(session_entry)
    db.session.commit()

    return jsonify({
        "message": "Profil kaydedildi.",
        "bmr": round(bmr),
        "tdee": round(tdee),
        "target_calories": round(target_calories)
    })

class WeeklyCheckIn(db.Model):
    id                = db.Column(db.Integer, primary_key=True)
    user_id           = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    weight            = db.Column(db.Float, nullable=False)
    yogunluk          = db.Column(db.Integer)      # 1-5 arası
    fatigue           = db.Column(db.Integer)       # 1-5 arası
    progressive_overload = db.Column(db.String(20)) # "evet", "hayir", "kismen"
    uyku_kalitesi     = db.Column(db.Integer)       # 1-5 arası
    beslenme_uyumu    = db.Column(db.Integer)       # 1-5 arası
    note              = db.Column(db.Text)
    coach_feedback    = db.Column(db.Text)
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<WeeklyCheckIn {self.user_id} - {self.created_at}>"
    
class NutritionPlan(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    plan_data  = db.Column(db.Text, nullable=False)  # JSON olarak sakla
    score      = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<NutritionPlan {self.user_id} - {self.created_at}>"
    
class TrainingPlan(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    plan_data  = db.Column(db.Text, nullable=False)
    score      = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<TrainingPlan {self.user_id} - {self.created_at}>"
    
class MealLog(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    ogun       = db.Column(db.String(100), nullable=False)
    yemekler   = db.Column(db.Text, nullable=False)
    kalori     = db.Column(db.Float)
    protein    = db.Column(db.Float)
    karb       = db.Column(db.Float)
    yag        = db.Column(db.Float)
    tarih      = db.Column(db.String(10))
    source     = db.Column(db.String(20), default="manual")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<MealLog {self.user_id} - {self.ogun} - {self.created_at}>"

class Friendship(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    sender_id   = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status      = db.Column(db.String(10), default="pending")
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("sender_id", "receiver_id", name="uq_friendship"),
    )

    sender   = db.relationship("User", foreign_keys=[sender_id], backref="sent_requests")
    receiver = db.relationship("User", foreign_keys=[receiver_id], backref="received_requests")

class Message(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    sender_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    receiver_id  = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    body         = db.Column(db.Text, nullable=False)
    timestamp    = db.Column(db.DateTime, default=datetime.utcnow)
    is_read      = db.Column(db.Boolean, default=False)
    message_type = db.Column(db.String(50), default="text")

    sender   = db.relationship("User", foreign_keys=[sender_id], backref="sent_messages")
    receiver = db.relationship("User", foreign_keys=[receiver_id], backref="received_messages")


class Activity(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    activity_type = db.Column(db.String(30), nullable=False)
    content       = db.Column(db.String(300), nullable=False)
    timestamp     = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    user = db.relationship("User", backref="activities")

class ActivityClap(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    activity_id = db.Column(db.Integer, db.ForeignKey("activity.id"), nullable=False)
    user_id     = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("activity_id", "user_id", name="uq_activity_clap"),
    )

    activity = db.relationship("Activity", backref="claps")
    user     = db.relationship("User", backref="given_claps")


class Supplement(db.Model):
    id                   = db.Column(db.Integer, primary_key=True)
    user_id              = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    product_name         = db.Column(db.String(150), nullable=False)
    brand                = db.Column(db.String(100), nullable=False)
    category             = db.Column(db.String(30), nullable=False, default="Other")
    status               = db.Column(db.String(15), nullable=False, default="Active")
    rating_effect        = db.Column(db.Integer, nullable=True)
    rating_taste         = db.Column(db.Integer, nullable=True)
    rating_digestion     = db.Column(db.Integer, nullable=True)
    rating_price         = db.Column(db.Integer, nullable=True)
    review_text          = db.Column(db.Text, nullable=True)
    price_paid           = db.Column(db.Float, nullable=True)
    is_public            = db.Column(db.Boolean, default=True)
    created_at           = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="supplements")

SUPPLEMENT_CATEGORIES = ["Protein", "Amino Acid", "Pre-Workout", "Vitamin/Health", "Creatine", "Other"]
SUPPLEMENT_STATUSES   = ["Active", "Low Stock", "Finished"]
CATEGORY_ICONS = {
    "Protein": "\U0001f964", "Amino Acid": "\U0001f4a7", "Pre-Workout": "⚡",
    "Vitamin/Health": "\U0001f48a", "Creatine": "\U0001f4aa", "Other": "\U0001f4e6",
}


class DailyQuest(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    title         = db.Column(db.String(120), nullable=False)
    description   = db.Column(db.Text)
    points_reward = db.Column(db.Integer, nullable=False, default=10)
    quest_type    = db.Column(db.String(50), nullable=False)
    is_active     = db.Column(db.Boolean, default=True)


class UserQuestProgress(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    quest_id     = db.Column(db.Integer, db.ForeignKey("daily_quest.id"), nullable=False)
    completed_at = db.Column(db.DateTime, default=datetime.utcnow)
    date_key     = db.Column(db.String(10), nullable=False, default=lambda: date.today().isoformat())
    is_claimed   = db.Column(db.Boolean, default=False)

    __table_args__ = (
        db.UniqueConstraint("user_id", "quest_id", "date_key", name="uq_user_quest_day"),
    )

    user  = db.relationship("User", backref="quest_progress")
    quest = db.relationship("DailyQuest", backref="progress_entries")


class WorkoutLog(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    exercise_name = db.Column(db.String(120), nullable=False)
    sets          = db.Column(db.Integer, nullable=False)
    reps          = db.Column(db.Integer, nullable=False)
    weight_kg     = db.Column(db.Float, nullable=False, default=0)
    volume        = db.Column(db.Float, nullable=False, default=0)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    user = db.relationship("User", backref="workout_logs")


class UserDailyNutrition(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    food_item  = db.Column(db.String(200), nullable=False)
    calories   = db.Column(db.Float, nullable=False, default=0)
    protein    = db.Column(db.Float, nullable=False, default=0)
    carbs      = db.Column(db.Float, nullable=False, default=0)
    fat        = db.Column(db.Float, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    user = db.relationship("User", backref="daily_nutrition")


class DailyActivity(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    steps           = db.Column(db.Integer, nullable=False, default=0)
    intensity       = db.Column(db.String(20), nullable=False, default="moderate")
    calories_burned = db.Column(db.Float, nullable=False, default=0)
    distance_km     = db.Column(db.Float, nullable=False, default=0)
    duration_min    = db.Column(db.Float, nullable=False, default=0)
    date_key        = db.Column(db.String(10), nullable=False)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    user = db.relationship("User", backref="daily_activities")

    __table_args__ = (
        db.UniqueConstraint("user_id", "date_key", "intensity", name="uq_daily_activity"),
    )


class CustomMeal(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    meal_name  = db.Column(db.String(50), nullable=False)
    date_key   = db.Column(db.String(10), nullable=False)
    is_logged  = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user  = db.relationship("User", backref="custom_meals")
    items = db.relationship("CustomMealItem", backref="meal",
                            cascade="all, delete-orphan",
                            order_by="CustomMealItem.id")

    __table_args__ = (
        db.UniqueConstraint("user_id", "meal_name", "date_key",
                            name="uq_custom_meal_day"),
    )


class CustomMealItem(db.Model):
    id               = db.Column(db.Integer, primary_key=True)
    custom_meal_id   = db.Column(db.Integer, db.ForeignKey("custom_meal.id"), nullable=False, index=True)
    food_name        = db.Column(db.String(200), nullable=False)
    grams            = db.Column(db.Float, nullable=False)
    calories         = db.Column(db.Float, nullable=False, default=0)
    protein          = db.Column(db.Float, nullable=False, default=0)
    carbs            = db.Column(db.Float, nullable=False, default=0)
    fat              = db.Column(db.Float, nullable=False, default=0)
    fatsecret_food_id = db.Column(db.String(50), nullable=True)
    per_100g_calories = db.Column(db.Float, nullable=True)
    per_100g_protein  = db.Column(db.Float, nullable=True)
    per_100g_carbs    = db.Column(db.Float, nullable=True)
    per_100g_fat      = db.Column(db.Float, nullable=True)
    serving_id          = db.Column(db.String(50), nullable=True)
    serving_description = db.Column(db.String(200), nullable=True)
    serving_quantity    = db.Column(db.Float, nullable=True)


def award_xp(user_id, amount):
    user = User.query.get(user_id)
    if user:
        user.rank_points = (user.rank_points or 0) + amount
        return user.rank_points
    return None

def get_level(xp):
    return 1 + (xp or 0) // 500

def get_title(level):
    if level <= 5:
        return "Fitness Yolcusu"
    elif level <= 10:
        return "Demir Bükücü"
    elif level <= 20:
        return "Kas Mimarı"
    elif level <= 50:
        return "FitX Efsanesi"
    else:
        return "Antrenman Tanrısı"

ACTIVITY_ICONS = {
    "workout_completed": "⚡",
    "new_supplement": "\U0001f48a",
    "streak_milestone": "\U0001f525",
    "new_friend": "\U0001f91d",
    "level_up": "\U0001f31f",
    "quest_completed": "\U0001f3af",
}

def log_activity(user_id, activity_type, content):
    act = Activity(user_id=user_id, activity_type=activity_type, content=content)
    db.session.add(act)

def get_rank_title(points):
    return get_title(get_level(points))


@app.before_request
def update_streak():
    if current_user.is_authenticated:
        today = date.today()
        if current_user.last_login != today:
            if current_user.last_login == today - timedelta(days=1):
                current_user.streak_count = (current_user.streak_count or 0) + 1
            else:
                current_user.streak_count = 1
            current_user.last_login = today
            streak = current_user.streak_count
            if streak in (7, 14, 30, 60, 100):
                log_activity(current_user.id, "streak_milestone",
                             f"{streak} günlük seri yakalanadı!")
                award_xp(current_user.id, streak * 2)
            db.session.commit()

@app.context_processor
def inject_rank():
    base = {"_v": _BOOT_TS}
    if current_user.is_authenticated:
        xp = current_user.rank_points or 0
        level = get_level(xp)
        title = get_title(level)
        xp_in_level = xp % 500
        return {**base,
            "rank_points": xp, "rank_title": title,
            "user_xp": xp, "user_level": level, "user_title": title,
            "xp_in_level": xp_in_level, "xp_for_next": 500,
        }
    return {**base,
        "rank_points": 0, "rank_title": "Fitness Yolcusu",
        "user_xp": 0, "user_level": 1, "user_title": "Fitness Yolcusu",
        "xp_in_level": 0, "xp_for_next": 500,
    }


def get_today_progress(user_id):
    today_key = date.today().isoformat()
    return UserQuestProgress.query.filter_by(
        user_id=user_id, date_key=today_key
    ).all()


def complete_quest_for_user(user_id, quest_type):
    quest = DailyQuest.query.filter_by(quest_type=quest_type, is_active=True).first()
    if not quest:
        return None
    today_key = date.today().isoformat()
    existing = UserQuestProgress.query.filter_by(
        user_id=user_id, quest_id=quest.id, date_key=today_key
    ).first()
    if existing:
        return None
    try:
        progress = UserQuestProgress(
            user_id=user_id, quest_id=quest.id,
            date_key=today_key, is_claimed=True
        )
        db.session.add(progress)
        new_total = award_xp(user_id, quest.points_reward)
        log_activity(user_id, "quest_completed", f"'{quest.title}' görevini tamamladı")
        db.session.commit()
        level = get_level(new_total)
        return {
            "awarded": True,
            "xp": quest.points_reward,
            "new_total": new_total,
            "quest_title": quest.title,
            "level": level,
            "title": get_title(level)
        }
    except Exception:
        db.session.rollback()
        return None


@app.route("/nutrition-plan/save", methods=["POST"])
@login_required
def save_nutrition_plan():
    data = request.get_json()
    plan = data.get("plan")
    score = data.get("score")

    if not plan:
        return jsonify({"error": "Plan verisi eksik"}), 400

    # Eski planı sil, yenisini kaydet
    NutritionPlan.query.filter_by(user_id=current_user.id).delete()

    new_plan = NutritionPlan(
        user_id   = current_user.id,
        plan_data = json.dumps(plan, ensure_ascii=False),
        score     = score
    )
    db.session.add(new_plan)
    db.session.commit()

    return jsonify({"message": "Plan kaydedildi."})

@app.route("/nutrition-plan/active")
@login_required
def get_active_nutrition_plan():
    plan = NutritionPlan.query.filter_by(user_id=current_user.id)\
        .order_by(NutritionPlan.created_at.desc())\
        .first()

    if not plan:
        return jsonify({"exists": False})

    return jsonify({
        "exists"    : True,
        "plan"      : json.loads(plan.plan_data),
        "score"     : plan.score,
        "created_at": plan.created_at.strftime("%d.%m.%Y")
    })

@app.route("/api/quick-add-meal", methods=["POST"])
@login_required
def quick_add_meal():
    data     = request.get_json()
    meal_key = data.get("meal_key", "")

    MEAL_LABELS = {
        "kahvalti": "Kahvaltı",
        "ogle":     "Öğle",
        "aksam":    "Akşam",
        "ara_ogun": "Ara Öğün"
    }
    if meal_key not in MEAL_LABELS:
        return jsonify({"error": "Geçersiz öğün anahtarı."}), 400

    plan_record = NutritionPlan.query.filter_by(user_id=current_user.id)\
        .order_by(NutritionPlan.created_at.desc()).first()

    if not plan_record:
        return jsonify({"error": "Aktif beslenme planı bulunamadı."}), 404

    plan = json.loads(plan_record.plan_data)
    meal = plan.get(meal_key)

    if not meal:
        return jsonify({"error": "Bu öğün planda tanımlı değil."}), 404

    yemekler = ", ".join(meal.get("yemekler", []))
    today    = datetime.utcnow().strftime("%d.%m")

    entry = MealLog(
        user_id  = current_user.id,
        ogun     = MEAL_LABELS[meal_key],
        yemekler = yemekler,
        kalori   = round(float(meal.get("kalori",  0)), 1),
        protein  = round(float(meal.get("protein", 0)), 1),
        karb     = round(float(meal.get("karb",    0)), 1),
        yag      = round(float(meal.get("yag",     0)), 1),
        tarih    = today
    )
    entry.source = "ai_plan"
    db.session.add(entry)
    db.session.commit()

    quest_result = complete_quest_for_user(current_user.id, "meal_logged")
    response = {
        "message": f"{MEAL_LABELS[meal_key]} planından eklendi.",
        "nutrients": {
            "kalori":  entry.kalori,
            "protein": entry.protein,
            "karb":    entry.karb,
            "yag":     entry.yag
        }
    }
    if quest_result:
        response["quest_awarded"] = quest_result
    return jsonify(response)


def _food_search_fatsecret(q):
    """Try FatSecret API. Returns list of results or None on failure."""
    try:
        token = _get_fatsecret_token()
    except Exception:
        return None

    try:
        resp = _fs_get(FATSECRET_API_URL, params={
            "method": "foods.search",
            "search_expression": q,
            "format": "json",
            "max_results": 8,
        }, headers={"Authorization": f"Bearer {token}"}, timeout=5)
        data = resp.json()
    except Exception:
        return None

    if "error" in data:
        return None

    foods = data.get("foods", {}).get("food", [])
    if isinstance(foods, dict):
        foods = [foods]
    if not foods:
        return None

    results = []
    for f in foods:
        desc = f.get("food_description", "")
        parsed = _parse_fatsecret_desc(desc)
        if not parsed:
            continue
        cal_val = parsed.get("calories") or parsed.get("cal") or parsed.get("energy") or 0
        if not cal_val:
            continue
        macros = {
            "calories": float(cal_val),
            "protein": float(parsed.get("protein", 0)),
            "carbs": float(parsed.get("carbs", parsed.get("carbohydrate", parsed.get("carb", 0)))),
            "fat": float(parsed.get("fat", parsed.get("total fat", 0))),
        }
        serving_text = parsed.get("serving", "")
        is_serving = _is_per_serving(serving_text)

        per_100g = macros
        if is_serving:
            est = _estimate_serving_weights_llm([f.get("food_name", q)])
            weight_g = est.get(f.get("food_name", q), 150.0)
            if weight_g and weight_g > 0:
                scale = 100.0 / weight_g
                per_100g = {
                    "calories": round(macros["calories"] * scale, 1),
                    "protein": round(macros["protein"] * scale, 1),
                    "carbs": round(macros["carbs"] * scale, 1),
                    "fat": round(macros["fat"] * scale, 1),
                }

        name = f.get("food_name", "")
        fid = f.get("food_id", "")
        _cache_macros({name: per_100g})
        if fid:
            _food_id_cache[name.lower()] = fid
        results.append({
            "name": name,
            "brand": f.get("brand_name", ""),
            "food_id": fid,
            "serving": serving_text,
            "is_per_serving": is_serving,
            "macros": macros,
            "per_100g": per_100g
        })
    return results if results else None


_STATIC_FOODS = {
    "tavuk": [
        {"name": "Tavuk Göğsü (Haşlanmış)", "calories": 165, "protein": 31.0, "carbs": 0.0, "fat": 3.6},
        {"name": "Tavuk But (Pişmiş)", "calories": 209, "protein": 26.0, "carbs": 0.0, "fat": 10.9},
        {"name": "Tavuk Kanat (Izgara)", "calories": 203, "protein": 30.5, "carbs": 0.0, "fat": 8.1},
        {"name": "Tavuk Köfte", "calories": 180, "protein": 18.0, "carbs": 8.0, "fat": 8.5},
    ],
    "yumurta": [
        {"name": "Yumurta (Haşlanmış)", "calories": 155, "protein": 13.0, "carbs": 1.1, "fat": 11.0},
        {"name": "Yumurta (Çırpılmış)", "calories": 149, "protein": 10.0, "carbs": 1.6, "fat": 11.2},
        {"name": "Yumurta Beyazı", "calories": 52, "protein": 11.0, "carbs": 0.7, "fat": 0.2},
    ],
    "pirinç": [
        {"name": "Pirinç Pilavı", "calories": 130, "protein": 2.7, "carbs": 28.0, "fat": 0.3},
        {"name": "Bulgur Pilavı", "calories": 83, "protein": 3.1, "carbs": 18.6, "fat": 0.2},
    ],
    "pilav": [
        {"name": "Pirinç Pilavı", "calories": 130, "protein": 2.7, "carbs": 28.0, "fat": 0.3},
        {"name": "Bulgur Pilavı", "calories": 83, "protein": 3.1, "carbs": 18.6, "fat": 0.2},
    ],
    "ekmek": [
        {"name": "Beyaz Ekmek", "calories": 265, "protein": 9.0, "carbs": 49.0, "fat": 3.2},
        {"name": "Tam Buğday Ekmeği", "calories": 247, "protein": 13.0, "carbs": 41.0, "fat": 3.4},
    ],
    "süt": [
        {"name": "Tam Yağlı Süt", "calories": 61, "protein": 3.2, "carbs": 4.8, "fat": 3.3},
        {"name": "Yarım Yağlı Süt", "calories": 46, "protein": 3.4, "carbs": 4.7, "fat": 1.6},
    ],
    "peynir": [
        {"name": "Beyaz Peynir", "calories": 264, "protein": 17.0, "carbs": 1.0, "fat": 21.0},
        {"name": "Kaşar Peyniri", "calories": 340, "protein": 25.0, "carbs": 1.5, "fat": 26.0},
        {"name": "Lor Peyniri", "calories": 98, "protein": 11.0, "carbs": 3.4, "fat": 4.3},
    ],
    "makarna": [
        {"name": "Makarna (Haşlanmış)", "calories": 131, "protein": 5.0, "carbs": 25.0, "fat": 1.1},
        {"name": "Tam Buğday Makarna", "calories": 124, "protein": 5.3, "carbs": 24.0, "fat": 0.5},
    ],
    "et": [
        {"name": "Dana Kıyma (Pişmiş)", "calories": 250, "protein": 26.0, "carbs": 0.0, "fat": 15.0},
        {"name": "Kuzu Eti (Pişmiş)", "calories": 294, "protein": 25.0, "carbs": 0.0, "fat": 21.0},
        {"name": "Dana Biftek (Izgara)", "calories": 271, "protein": 26.0, "carbs": 0.0, "fat": 18.0},
    ],
    "balık": [
        {"name": "Somon (Izgara)", "calories": 208, "protein": 20.0, "carbs": 0.0, "fat": 13.0},
        {"name": "Levrek (Fırında)", "calories": 124, "protein": 24.0, "carbs": 0.0, "fat": 2.6},
        {"name": "Hamsi (Tava)", "calories": 210, "protein": 20.0, "carbs": 3.0, "fat": 13.0},
    ],
    "yoğurt": [
        {"name": "Tam Yağlı Yoğurt", "calories": 63, "protein": 3.5, "carbs": 4.7, "fat": 3.3},
        {"name": "Süzme Yoğurt", "calories": 66, "protein": 10.0, "carbs": 3.6, "fat": 0.7},
    ],
    "muz": [
        {"name": "Muz", "calories": 89, "protein": 1.1, "carbs": 23.0, "fat": 0.3},
    ],
    "elma": [
        {"name": "Elma", "calories": 52, "protein": 0.3, "carbs": 14.0, "fat": 0.2},
    ],
    "salata": [
        {"name": "Çoban Salatası", "calories": 25, "protein": 1.0, "carbs": 4.0, "fat": 0.5},
        {"name": "Mevsim Salatası", "calories": 20, "protein": 1.2, "carbs": 3.5, "fat": 0.3},
    ],
    "çorba": [
        {"name": "Mercimek Çorbası", "calories": 56, "protein": 3.5, "carbs": 9.0, "fat": 0.5},
        {"name": "Tavuk Çorbası", "calories": 36, "protein": 2.5, "carbs": 4.0, "fat": 1.0},
        {"name": "Domates Çorbası", "calories": 30, "protein": 1.0, "carbs": 5.5, "fat": 0.5},
    ],
    "mercimek": [
        {"name": "Mercimek (Haşlanmış)", "calories": 116, "protein": 9.0, "carbs": 20.0, "fat": 0.4},
        {"name": "Mercimek Çorbası", "calories": 56, "protein": 3.5, "carbs": 9.0, "fat": 0.5},
    ],
    "nohut": [
        {"name": "Nohut (Haşlanmış)", "calories": 164, "protein": 8.9, "carbs": 27.0, "fat": 2.6},
    ],
    "patates": [
        {"name": "Patates (Haşlanmış)", "calories": 87, "protein": 1.9, "carbs": 20.0, "fat": 0.1},
        {"name": "Patates Kızartması", "calories": 312, "protein": 3.4, "carbs": 41.0, "fat": 15.0},
    ],
    "avokado": [
        {"name": "Avokado", "calories": 160, "protein": 2.0, "carbs": 9.0, "fat": 15.0},
    ],
    "badem": [
        {"name": "Badem", "calories": 579, "protein": 21.0, "carbs": 22.0, "fat": 50.0},
    ],
    "fıstık": [
        {"name": "Yer Fıstığı", "calories": 567, "protein": 26.0, "carbs": 16.0, "fat": 49.0},
        {"name": "Antep Fıstığı", "calories": 560, "protein": 20.0, "carbs": 28.0, "fat": 45.0},
    ],
    "ceviz": [
        {"name": "Ceviz", "calories": 654, "protein": 15.0, "carbs": 14.0, "fat": 65.0},
    ],
    "zeytin": [
        {"name": "Siyah Zeytin", "calories": 115, "protein": 0.8, "carbs": 6.0, "fat": 11.0},
        {"name": "Yeşil Zeytin", "calories": 145, "protein": 1.0, "carbs": 3.8, "fat": 15.0},
    ],
    "bal": [
        {"name": "Bal", "calories": 304, "protein": 0.3, "carbs": 82.0, "fat": 0.0},
    ],
    "protein": [
        {"name": "Whey Protein Tozu", "calories": 375, "protein": 75.0, "carbs": 10.0, "fat": 3.0},
        {"name": "Kazein Protein Tozu", "calories": 360, "protein": 70.0, "carbs": 12.0, "fat": 2.0},
    ],
}


def _food_search_static(q):
    """Last-resort fallback: match against built-in food database."""
    q_lower = q.lower().strip()
    matches = []
    for key, foods in _STATIC_FOODS.items():
        if q_lower in key or key in q_lower:
            matches.extend(foods)
    if not matches:
        return []
    results = []
    for item in matches[:8]:
        per_100g = {
            "calories": item["calories"], "protein": item["protein"],
            "carbs": item["carbs"], "fat": item["fat"],
        }
        _cache_macros({item["name"]: per_100g})
        results.append({
            "name": item["name"], "brand": "", "food_id": "",
            "serving": "Per 100g", "is_per_serving": False,
            "macros": per_100g, "per_100g": per_100g
        })
    return results


def _food_search_llm(q):
    """Fallback: use LLM to estimate nutrition for common foods."""
    prompt = (
        f"Kullanıcı '{q}' araması yaptı. Bu aramayla eşleşen 5 yaygın besini listele.\n"
        "Her biri için 100 gram başına makro değerlerini ver.\n"
        "SADECE JSON döndür, başka metin yazma. Format:\n"
        '[{{"name":"Besin adı","calories":123,"protein":10.5,"carbs":5.2,"fat":3.1}}]'
    )
    try:
        text = _bedrock_chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=600,
        ).strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        items = json.loads(text)
        results = []
        for item in items:
            per_100g = {
                "calories": float(item.get("calories", 0)),
                "protein": float(item.get("protein", 0)),
                "carbs": float(item.get("carbs", 0)),
                "fat": float(item.get("fat", 0)),
            }
            name = item.get("name", q)
            _cache_macros({name: per_100g})
            results.append({
                "name": name,
                "brand": "",
                "food_id": "",
                "serving": "Per 100g",
                "is_per_serving": False,
                "macros": per_100g,
                "per_100g": per_100g
            })
        return results
    except Exception:
        return []


@app.route("/api/food/search")
@login_required
def food_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"results": []})

    cached = _macro_cache.get(q)
    if cached:
        cached_fid = _food_id_cache.get(q.lower(), "")
        app.logger.info("food_search cache hit for '%s', food_id=%s", q, cached_fid or "(none)")
        return jsonify({"results": [{
            "name": q, "brand": "",
            "food_id": cached_fid,
            "serving": "", "is_per_serving": False,
            "macros": cached, "per_100g": cached
        }]})

    results = _food_search_fatsecret(q)
    if not results:
        results = _food_search_llm(q)
    if not results:
        results = _food_search_static(q)

    return jsonify({"results": results})



@app.route("/api/server-ip")
@login_required
def server_ip():
    """Show Railway's outbound IP and test FatSecret connectivity."""
    result = {}
    try:
        r = http_requests_lib.get("https://api.ipify.org", timeout=5)
        result["ip"] = r.text.strip()
    except Exception as e:
        result["ip"] = None
        result["ip_error"] = str(e)
    try:
        token = _get_fatsecret_token()
        result["fatsecret_token"] = True
        resp = _fs_get(FATSECRET_API_URL, params={
            "method": "foods.search", "search_expression": "egg",
            "format": "json", "max_results": 1,
        }, headers={"Authorization": f"Bearer {token}"}, timeout=5)
        data = resp.json()
        if "error" in data:
            result["fatsecret_search"] = False
            result["fatsecret_error"] = data["error"]
        else:
            foods = data.get("foods", {}).get("food", [])
            if isinstance(foods, dict):
                foods = [foods]
            result["fatsecret_search"] = True
            result["food_id"] = foods[0].get("food_id") if foods else None
    except Exception as e:
        result["fatsecret_token"] = False
        result["fatsecret_error"] = str(e)
    return jsonify(result)


@app.route("/api/food/<food_id>/servings")
@login_required
def food_servings(food_id):
    app.logger.info("food_servings called with food_id=%s", food_id)
    servings = _food_get_servings(food_id)
    if servings:
        app.logger.info("Servings OK for food_id=%s: %d options, first=%s",
                        food_id, len(servings), servings[0].get("serving_description", "?"))
        return jsonify({"servings": servings})
    else:
        app.logger.warning("No servings for food_id=%s", food_id)
        return jsonify({"servings": [], "debug": "no_servings_returned"})


@app.route("/api/food/servings-by-name")
@login_required
def food_servings_by_name():
    name = request.args.get("name", "").strip()
    if len(name) < 2:
        return jsonify({"servings": [], "food_id": ""})

    # Check food_id cache first (populated by previous FatSecret searches)
    cached_fid = _food_id_cache.get(name.lower())
    if cached_fid:
        app.logger.info("servings-by-name: cache hit food_id=%s for '%s'", cached_fid, name)
        servings = _food_get_servings(cached_fid)
        if servings:
            return jsonify({"servings": servings, "food_id": cached_fid})

    # Try FatSecret search with the original name and common translations
    search_terms = [name]
    # Add English translations for common Turkish food names
    _TR_TO_EN = {
        "yumurta": "egg", "tavuk": "chicken", "pirinc": "rice", "pilav": "rice pilaf",
        "ekmek": "bread", "sut": "milk", "süt": "milk", "peynir": "cheese",
        "yogurt": "yogurt", "yoğurt": "yogurt", "bal": "honey", "tereyagi": "butter",
        "tereyağı": "butter", "makarna": "pasta", "salata": "salad",
        "domates": "tomato", "patates": "potato", "havuc": "carrot", "havuç": "carrot",
        "elma": "apple", "muz": "banana", "portakal": "orange", "balik": "fish",
        "balık": "fish", "ton baligi": "tuna", "somon": "salmon",
        "brokoli": "broccoli", "ispanak": "spinach", "fasulye": "beans",
        "nohut": "chickpea", "mercimek": "lentil", "ceviz": "walnut",
        "badem": "almond", "findik": "hazelnut", "fındık": "hazelnut",
        "zeytin": "olive", "zeytinyagi": "olive oil", "zeytinyağı": "olive oil",
        "avokado": "avocado", "kayisi": "apricot", "kayısı": "apricot",
        "cilek": "strawberry", "çilek": "strawberry", "karpuz": "watermelon",
    }
    en = _TR_TO_EN.get(name.lower())
    if en and en.lower() != name.lower():
        search_terms.append(en)

    try:
        token = _get_fatsecret_token()
        for term in search_terms:
            resp = _fs_get(FATSECRET_API_URL, params={
                "method": "foods.search",
                "search_expression": term,
                "format": "json",
                "max_results": 1,
            }, headers={"Authorization": f"Bearer {token}"}, timeout=5)
            data = resp.json()
            if "error" in data:
                app.logger.warning("servings-by-name search '%s' error: %s", term, data["error"])
                continue
            foods = data.get("foods", {}).get("food", [])
            if isinstance(foods, dict):
                foods = [foods]
            if foods:
                fid = foods[0].get("food_id", "")
                app.logger.info("servings-by-name: found food_id=%s for search '%s'", fid, term)
                if fid:
                    _food_id_cache[name.lower()] = fid
                    servings = _food_get_servings(fid)
                    if servings:
                        return jsonify({"servings": servings, "food_id": fid})
                    app.logger.warning("servings-by-name: food.get returned no servings for id=%s", fid)
            else:
                app.logger.info("servings-by-name: no results for search '%s'", term)
    except Exception as e:
        app.logger.warning("servings-by-name failed for '%s': %s", name, e)
    return jsonify({"servings": [], "food_id": ""})


# ── DIARY BUILDER API ──

@app.route("/api/diary/meal", methods=["POST"])
@login_required
def diary_create_meal():
    data = request.get_json()
    meal_name = data.get("meal_name", "").strip()
    date_key = data.get("date_key", date.today().isoformat())

    valid_meals = ("Kahvaltı", "Öğle", "Akşam", "Ara Öğün")
    if meal_name not in valid_meals:
        return jsonify({"error": "Geçersiz öğün adı"}), 400

    existing = CustomMeal.query.filter_by(
        user_id=current_user.id, meal_name=meal_name, date_key=date_key
    ).first()
    if existing:
        return jsonify({"meal_id": existing.id, "exists": True})

    meal = CustomMeal(user_id=current_user.id, meal_name=meal_name, date_key=date_key)
    db.session.add(meal)
    db.session.commit()
    return jsonify({"meal_id": meal.id, "exists": False})


@app.route("/api/diary/meal/<int:meal_id>/item", methods=["POST"])
@login_required
def diary_add_item(meal_id):
    meal = CustomMeal.query.get(meal_id)
    if not meal or meal.user_id != current_user.id:
        return jsonify({"error": "Öğün bulunamadı"}), 404
    if meal.is_logged:
        return jsonify({"error": "Bu öğün zaten kaydedilmiş"}), 400

    data = request.get_json()
    food_name = data.get("food_name", "").strip()
    food_id = data.get("fatsecret_food_id", "")

    if not food_name:
        return jsonify({"error": "Besin adı gerekli"}), 400

    srv_id = data.get("serving_id")
    if srv_id:
        qty = float(data.get("serving_quantity", 1))
        srv_cal = float(data.get("serving_calories", 0))
        srv_pro = float(data.get("serving_protein", 0))
        srv_carb = float(data.get("serving_carbs", 0))
        srv_fat = float(data.get("serving_fat", 0))
        metric_amt = float(data.get("metric_serving_amount", 0))
        grams = round(metric_amt * qty, 1) if metric_amt else 0
        p100_cal = round(srv_cal / metric_amt * 100, 2) if metric_amt else 0
        p100_pro = round(srv_pro / metric_amt * 100, 2) if metric_amt else 0
        p100_carb = round(srv_carb / metric_amt * 100, 2) if metric_amt else 0
        p100_fat = round(srv_fat / metric_amt * 100, 2) if metric_amt else 0
        item = CustomMealItem(
            custom_meal_id=meal_id,
            food_name=food_name,
            grams=grams,
            calories=round(srv_cal * qty, 1),
            protein=round(srv_pro * qty, 1),
            carbs=round(srv_carb * qty, 1),
            fat=round(srv_fat * qty, 1),
            fatsecret_food_id=food_id or None,
            per_100g_calories=p100_cal,
            per_100g_protein=p100_pro,
            per_100g_carbs=p100_carb,
            per_100g_fat=p100_fat,
            serving_id=str(srv_id),
            serving_description=data.get("serving_description", ""),
            serving_quantity=qty,
        )
    else:
        grams = float(data.get("grams", 100))
        per_100g = data.get("per_100g", {})
        scale = grams / 100.0
        p100_cal = float(per_100g.get("calories", 0))
        p100_pro = float(per_100g.get("protein", 0))
        p100_carb = float(per_100g.get("carbs", 0))
        p100_fat = float(per_100g.get("fat", 0))
        item = CustomMealItem(
            custom_meal_id=meal_id,
            food_name=food_name,
            grams=grams,
            calories=round(p100_cal * scale, 1),
            protein=round(p100_pro * scale, 1),
            carbs=round(p100_carb * scale, 1),
            fat=round(p100_fat * scale, 1),
            fatsecret_food_id=food_id or None,
            per_100g_calories=p100_cal,
            per_100g_protein=p100_pro,
            per_100g_carbs=p100_carb,
            per_100g_fat=p100_fat,
        )

    db.session.add(item)
    db.session.commit()
    return jsonify({
        "item_id": item.id,
        "calories": item.calories,
        "protein": item.protein,
        "carbs": item.carbs,
        "fat": item.fat
    })


@app.route("/api/diary/item/<int:item_id>", methods=["PATCH"])
@login_required
def diary_update_item(item_id):
    item = CustomMealItem.query.get(item_id)
    if not item or item.meal.user_id != current_user.id:
        return jsonify({"error": "Besin bulunamadı"}), 404
    if item.meal.is_logged:
        return jsonify({"error": "Bu öğün zaten kaydedilmiş"}), 400

    data = request.get_json()
    srv_id = data.get("serving_id")

    if srv_id:
        qty = float(data.get("serving_quantity", 1))
        srv_cal = float(data.get("serving_calories", 0))
        srv_pro = float(data.get("serving_protein", 0))
        srv_carb = float(data.get("serving_carbs", 0))
        srv_fat = float(data.get("serving_fat", 0))
        metric_amt = float(data.get("metric_serving_amount", 0))
        item.serving_id = str(srv_id)
        item.serving_description = data.get("serving_description", "")
        item.serving_quantity = qty
        item.grams = round(metric_amt * qty, 1) if metric_amt else 0
        item.calories = round(srv_cal * qty, 1)
        item.protein = round(srv_pro * qty, 1)
        item.carbs = round(srv_carb * qty, 1)
        item.fat = round(srv_fat * qty, 1)
        if metric_amt:
            item.per_100g_calories = round(srv_cal / metric_amt * 100, 2)
            item.per_100g_protein = round(srv_pro / metric_amt * 100, 2)
            item.per_100g_carbs = round(srv_carb / metric_amt * 100, 2)
            item.per_100g_fat = round(srv_fat / metric_amt * 100, 2)
    elif "serving_quantity" in data and item.serving_id:
        qty = float(data["serving_quantity"])
        old_qty = item.serving_quantity or 1
        factor = qty / old_qty
        item.serving_quantity = qty
        item.grams = round(item.grams * factor, 1)
        item.calories = round(item.calories * factor, 1)
        item.protein = round(item.protein * factor, 1)
        item.carbs = round(item.carbs * factor, 1)
        item.fat = round(item.fat * factor, 1)
    else:
        grams = float(data.get("grams", item.grams))
        scale = grams / 100.0
        item.grams = grams
        item.calories = round((item.per_100g_calories or 0) * scale, 1)
        item.protein = round((item.per_100g_protein or 0) * scale, 1)
        item.carbs = round((item.per_100g_carbs or 0) * scale, 1)
        item.fat = round((item.per_100g_fat or 0) * scale, 1)

    db.session.commit()
    return jsonify({
        "item_id": item.id,
        "grams": item.grams,
        "calories": item.calories,
        "protein": item.protein,
        "carbs": item.carbs,
        "fat": item.fat
    })


@app.route("/api/diary/item/<int:item_id>", methods=["DELETE"])
@login_required
def diary_delete_item(item_id):
    item = CustomMealItem.query.get(item_id)
    if not item or item.meal.user_id != current_user.id:
        return jsonify({"error": "Besin bulunamadı"}), 404
    if item.meal.is_logged:
        return jsonify({"error": "Bu öğün zaten kaydedilmiş"}), 400
    db.session.delete(item)
    db.session.commit()
    return jsonify({"deleted": True})


@app.route("/api/diary/meal/<int:meal_id>/log", methods=["POST"])
@login_required
def diary_log_meal(meal_id):
    meal = CustomMeal.query.get(meal_id)
    if not meal or meal.user_id != current_user.id:
        return jsonify({"error": "Öğün bulunamadı"}), 404
    if meal.is_logged:
        return jsonify({"error": "Bu öğün zaten kaydedilmiş"}), 400
    if not meal.items:
        return jsonify({"error": "Öğüne en az bir besin ekle"}), 400

    total_cal = sum(i.calories for i in meal.items)
    total_pro = sum(i.protein for i in meal.items)
    total_karb = sum(i.carbs for i in meal.items)
    total_fat = sum(i.fat for i in meal.items)

    def _item_label(i):
        if i.serving_description and i.serving_quantity:
            qty = int(i.serving_quantity) if i.serving_quantity == int(i.serving_quantity) else i.serving_quantity
            return f"{i.food_name} ({qty}x {i.serving_description})"
        return f"{i.food_name} ({int(i.grams)}g)"
    yemekler = ", ".join(_item_label(i) for i in meal.items)
    today = datetime.utcnow().strftime("%d.%m")

    entry = MealLog(
        user_id=current_user.id,
        ogun=meal.meal_name,
        yemekler=yemekler,
        kalori=round(total_cal, 1),
        protein=round(total_pro, 1),
        karb=round(total_karb, 1),
        yag=round(total_fat, 1),
        tarih=today,
        source="diary"
    )
    db.session.add(entry)
    meal.is_logged = True
    db.session.commit()

    quest_result = complete_quest_for_user(current_user.id, "meal_logged")
    response = {
        "message": f"{meal.meal_name} kaydedildi.",
        "nutrients": {
            "kalori": entry.kalori,
            "protein": entry.protein,
            "karb": entry.karb,
            "yag": entry.yag
        }
    }
    if quest_result:
        response["quest_awarded"] = quest_result
    return jsonify(response)


@app.route("/api/diary/today")
@login_required
def diary_today():
    today_key = date.today().isoformat()
    meals = CustomMeal.query.filter_by(
        user_id=current_user.id, date_key=today_key
    ).order_by(CustomMeal.id).all()

    result = []
    grand_total = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}

    for m in meals:
        items = []
        meal_total = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}
        for i in m.items:
            items.append({
                "id": i.id,
                "food_name": i.food_name,
                "grams": i.grams,
                "calories": i.calories,
                "protein": i.protein,
                "carbs": i.carbs,
                "fat": i.fat,
                "per_100g": {
                    "calories": i.per_100g_calories,
                    "protein": i.per_100g_protein,
                    "carbs": i.per_100g_carbs,
                    "fat": i.per_100g_fat
                },
                "serving_id": i.serving_id,
                "serving_description": i.serving_description,
                "serving_quantity": i.serving_quantity,
                "fatsecret_food_id": i.fatsecret_food_id,
            })
            meal_total["calories"] += i.calories
            meal_total["protein"] += i.protein
            meal_total["carbs"] += i.carbs
            meal_total["fat"] += i.fat

        result.append({
            "id": m.id,
            "meal_name": m.meal_name,
            "is_logged": m.is_logged,
            "items": items,
            "totals": meal_total
        })

        for k in grand_total:
            grand_total[k] += meal_total[k]

    return jsonify({"meals": result, "totals": grand_total})


@app.route("/log", methods=["POST"])
@login_required
def log_progress():
    data = request.get_json()
    weight = data.get("weight")
    note = data.get("note", "")

    if not weight:
        return jsonify({"error" : "Kilo zorunludur"}), 400
    
    try:
        weight = float(weight)
    except ValueError:
        return jsonify({"error" : "Kilo sayısal olmalıdır"}), 400
    
    entry = WeeklyLog(
        user_id = current_user.id,
        weight=weight,
        note=note
    )
    db.session.add(entry)
    db.session.commit()

    # Önceki kayıtlarla karşılaştırma
    previous = WeeklyLog.query.filter_by(user_id=current_user.id)\
    .order_by(WeeklyLog.created_at.desc())\
    .offset(1).first()

    message = f"{weight} kg kaydedildi."
    if previous:
        diff = round(weight - previous.weight , 1)
        if diff < 0:
            message += f"Geçen kayda göre {abs(diff)} kg verdin. 🔥"
        elif diff > 0:
            message = f"Geçen kayda göre {abs(diff)} kg aldın."
        else:
            message += "Geçen kayıtla aynı kilo. Tutarlısın."
    return jsonify({"message" : message})

@app.route("/progress")
@login_required
def progress():
    logs = WeeklyLog.query.filter_by(user_id=current_user.id)\
    .order_by(WeeklyLog.created_at.asc())\
    .all()

    result = []
    for log in logs:
        result.append({
            "tarih" : log.created_at.strftime("%d.%m"),
            "kilo" : log.weight,
            "not" : log.note
        })
    return Response(
            json.dumps(result, ensure_ascii=False),
            mimetype="application/json"
        )

def generate_checkin_feedback(name, weight, prev_weight, days_passed,
                               goal, yogunluk, fatigue, overload,
                               uyku, beslenme, note):

    yogunluk_labels = {1:"çok düşük",2:"düşük",3:"orta",4:"yüksek",5:"çok yüksek"}
    fatigue_labels  = {1:"hiç yorgun değil",2:"biraz yorgun",3:"normal",4:"yorgun",5:"çok yorgun"}
    overload_labels = {"evet":"ağırlık/tekrar artırdı","hayir":"artıramadı","kismen":"kısmen artırdı"}
    uyku_labels     = {1:"çok kötü",2:"kötü",3:"orta",4:"iyi",5:"çok iyi"}
    beslenme_labels = {1:"hiç uyamadı",2:"zayıf",3:"orta",4:"iyi",5:"tam uyum"}

    progress = "İlk check-in, geçmiş veri yok."
    if prev_weight and days_passed:
        diff = round(weight - prev_weight, 1)
        if goal.lower() == "kas kazanma":
            if diff > 0:
                progress = f"{days_passed} günde {abs(diff)} kg aldı — kas kazanma hedefinde olumlu."
            elif diff < 0:
                progress = f"{days_passed} günde {abs(diff)} kg verdi — kas kazanma hedefinde bu istenmeyen."
            else:
                progress = f"{days_passed} günde kilo değişmedi — kalori artışı gerekebilir."
        else:
            if diff < 0:
                progress = f"{days_passed} günde {abs(diff)} kg verdi — kilo verme hedefinde başarılı."
            elif diff > 0:
                progress = f"{days_passed} günde {abs(diff)} kg aldı — kilo verme hedefinde istenmeyen."
            else:
                progress = f"{days_passed} günde kilo değişmedi — plato olabilir."

    prompt = f"""Sen bir kişisel fitness koçusun. Türkçe yaz, İngilizce kullanma.
Kullanıcıya "sen" diye hitap et.

Haftalık check-in verileri:
- İsim: {name}
- Güncel kilo: {weight} kg
- Hedef: {goal}
- İlerleme: {progress}
- Antrenman yoğunluğu: {yogunluk_labels.get(yogunluk, 'orta')}
- Yorgunluk durumu: {fatigue_labels.get(fatigue, 'normal')}
- Progressive overload: {overload_labels.get(overload, 'kısmen')}
- Uyku kalitesi: {uyku_labels.get(uyku, 'orta')}
- Beslenme uyumu: {beslenme_labels.get(beslenme, 'orta')}
- Kullanıcının notu: {note if note else 'Yok'}

Bu verilere dayanarak kısa ve spesifik bir koçluk geri bildirimi yaz.
Maksimum 4-5 cümle. İyi giden şeyleri vurgula, eksik olanlar için somut öneri ver.
Fatigue yüksekse dinlenme öner, progressive overload yapamadıysa nasıl yapabileceğini anlat.
Uyku kötüyse bunun etkisini açıkla."""

    try:
        return _bedrock_chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="Sen bir fitness koçusun. Kısa, spesifik, motive edici Türkçe konuş.",
            max_tokens=400,
            temperature=0.7,
        )
    except (ClientError, Exception) as e:
        return f"Geri bildirim alınamadı: {str(e)}"
    
@app.route("/checkin", methods=["POST"])
@login_required
def checkin():
    data = request.get_json()

    weight = data.get("weight")
    if not weight:
        return jsonify({"error": "Kilo zorunludur"}), 400

    try:
        weight = float(weight)
    except ValueError:
        return jsonify({"error": "Kilo sayısal olmalıdır"}), 400

    yogunluk    = int(data.get("yogunluk", 3))
    fatigue     = int(data.get("fatigue", 3))
    overload    = data.get("progressive_overload", "kismen")
    uyku        = int(data.get("uyku_kalitesi", 3))
    beslenme    = int(data.get("beslenme_uyumu", 3))
    note        = data.get("note", "")

    # Önceki check-in'i al
    previous = WeeklyCheckIn.query.filter_by(user_id=current_user.id)\
        .order_by(WeeklyCheckIn.created_at.desc())\
        .first()

    # Son oturum bilgilerini al
    last_session = UserSession.query.filter_by(user_id=current_user.id)\
        .order_by(UserSession.created_at.desc())\
        .first()

    goal = last_session.goal if last_session else "genel sağlık"

    # İlerleme hesapla
    prev_weight = previous.weight if previous else None
    days_passed = None
    if previous:
        days_passed = (datetime.utcnow() - previous.created_at).days

    # AI koç geri bildirimi
    coach_feedback = generate_checkin_feedback(
        current_user.username, weight, prev_weight, days_passed,
        goal, yogunluk, fatigue, overload, uyku, beslenme, note
    )

    entry = WeeklyCheckIn(
        user_id=current_user.id,
        weight=weight,
        yogunluk=yogunluk,
        fatigue=fatigue,
        progressive_overload=overload,
        uyku_kalitesi=uyku,
        beslenme_uyumu=beslenme,
        note=note,
        coach_feedback=coach_feedback
    )
    db.session.add(entry)
    db.session.commit()

    return jsonify({
        "message": "Check-in kaydedildi.",
        "coach_feedback": coach_feedback
    })

@app.route("/checkin-history")
@login_required
def checkin_history():
    checkins = WeeklyCheckIn.query.filter_by(user_id=current_user.id)\
        .order_by(WeeklyCheckIn.created_at.asc())\
        .all()

    result = []
    for c in checkins:
        result.append({
            "tarih"     : c.created_at.strftime("%d.%m"),
            "kilo"      : c.weight,
            "yogunluk"  : c.yogunluk,
            "fatigue"   : c.fatigue,
            "overload"  : c.progressive_overload,
            "uyku"      : c.uyku_kalitesi,
            "beslenme"  : c.beslenme_uyumu,
            "feedback"  : c.coach_feedback
        })

    return Response(
        json.dumps(result, ensure_ascii=False),
        mimetype="application/json"
    )

@app.route("/meal-log", methods=["POST"])
@login_required
def log_meal():
    data = request.get_json()
    ogun     = data.get("ogun", "")
    yemekler = data.get("yemekler", "")

    if not ogun or not yemekler:
        return jsonify({"error": "Öğün ve yemekler zorunludur"}), 400

    _FITNESS_DICT = {
        r'(?i)\b(\d+)\s*(?:ölçek|scoop)\s*(?:whey|protein\s*tozu|protein\s*powder)':
            lambda m: (f"{m.group(1)} ölçek whey protein tozu ({int(m.group(1))*30}g)", None),
        r'(?i)\b(\d+)\s*(?:ölçek|scoop)\s*kreatin':
            lambda m: (f"{m.group(1)} ölçek kreatin ({int(m.group(1))*5}g)", None),
        r'(?i)\b(\d+)\s*(?:ölçek|scoop)\s*(?:kazein|casein)':
            lambda m: (f"{m.group(1)} ölçek kazein protein ({int(m.group(1))*33}g)", None),
        r'(?i)\b(\d+)\s*(?:adet\s+)?pirinç\s*patlağı':
            lambda m: (f"{m.group(1)} adet pirinç patlağı ({int(m.group(1))*8}g)", None),
        r'(?i)\bprotein\s*bar[ıi]?\b':
            lambda m: ("1 protein bar (60g)", None),
        r'(?i)\b(\d+)\s*(?:kaşık|tbsp)\s*fıstık\s*ezmesi':
            lambda m: (f"{m.group(1)} yemek kaşığı fıstık ezmesi ({int(m.group(1))*15}g)", None),
        r'(?i)\b(\d+)\s*(?:kaşık|tbsp)\s*(?:bal|honey)':
            lambda m: (f"{m.group(1)} yemek kaşığı bal ({int(m.group(1))*21}g)", None),
        r'(?i)\bbcaa\b':
            lambda m: ("1 ölçek BCAA (7g)", None),
    }
    import re as _re
    normalized_yemekler = yemekler
    for pattern, handler in _FITNESS_DICT.items():
        match = _re.search(pattern, normalized_yemekler)
        if match:
            replacement, _ = handler(match)
            normalized_yemekler = _re.sub(pattern, replacement, normalized_yemekler, count=1)
    if normalized_yemekler != yemekler:
        print(f"[MEAL] Normalized: '{yemekler}' → '{normalized_yemekler}'")
    yemekler_for_prompt = normalized_yemekler

    override = data.get("override_macros")
    if override:
        nutrients = {
            "kalori": round(float(override.get("kalori", 0)), 1),
            "protein": round(float(override.get("protein", 0)), 1),
            "karb": round(float(override.get("karb", 0)), 1),
            "yag": round(float(override.get("yag", 0)), 1),
        }
        today = datetime.utcnow().strftime("%d.%m")
        entry = MealLog(
            user_id=current_user.id, ogun=ogun, yemekler=yemekler,
            kalori=nutrients["kalori"], protein=nutrients["protein"],
            karb=nutrients["karb"], yag=nutrients["yag"], tarih=today
        )
        db.session.add(entry)
        db.session.commit()
        return jsonify({"message": f"{ogun} kaydedildi.", "nutrients": nutrients})

    prompt = (
        f"Sen bir beslenme uzmanısın. Aşağıdaki yemeklerin GERÇEK toplam besin değerlerini hesapla.\n"
        f"Miktar belirtilmişse kullan, belirtilmemişse standart porsiyon kabul et.\n\n"
        f"Referans değerler:\n"
        f"- Tavuk göğsü (100g): 165 kcal, 31g protein, 0g karb, 3.6g yağ\n"
        f"- Yumurta (1 adet, 60g): 90 kcal, 7g protein, 0.6g karb, 6.3g yağ\n"
        f"- Pirinç pişmiş (100g): 130 kcal, 2.7g protein, 28g karb, 0.3g yağ\n"
        f"- Yulaf ezmesi (100g): 389 kcal, 17g protein, 66g karb, 7g yağ\n"
        f"- Beyaz peynir (100g): 264 kcal, 17g protein, 0.5g karb, 21g yağ\n"
        f"- Tam buğday ekmeği (1 dilim, 30g): 74 kcal, 4g protein, 12g karb, 1g yağ\n"
        f"- Ekmek (1 dilim, 30g): 80 kcal, 2.5g protein, 15g karb, 1g yağ\n"
        f"- Zeytinyağı (1 yemek kaşığı, 14ml): 119 kcal, 0g protein, 0g karb, 14g yağ\n"
        f"- Muz (1 orta, 120g): 105 kcal, 1.3g protein, 27g karb, 0.4g yağ\n"
        f"- Fıstık ezmesi (15g): 94 kcal, 4g protein, 3g karb, 8g yağ\n"
        f"- Makarna pişmiş (100g): 158 kcal, 5.8g protein, 31g karb, 0.9g yağ\n"
        f"- Patates pişmiş (100g): 87 kcal, 1.9g protein, 20g karb, 0.1g yağ\n"
        f"- Kırmızı et (100g): 250 kcal, 26g protein, 0g karb, 17g yağ\n"
        f"- Ton balığı (100g): 132 kcal, 28g protein, 0g karb, 1.3g yağ\n"
        f"- Süt (1 bardak, 240ml): 150 kcal, 8g protein, 12g karb, 8g yağ\n"
        f"- Yoğurt (100g): 61 kcal, 10g protein, 3.6g karb, 0.4g yağ\n"
        f"- Peynir (kaşar, 100g): 350 kcal, 25g protein, 1.5g karb, 27g yağ\n"
        f"- Salatalık (100g): 16 kcal, 0.7g protein, 3.6g karb, 0.1g yağ\n"
        f"- Domates (100g): 18 kcal, 0.9g protein, 3.9g karb, 0.2g yağ\n\n"
        f"Spor takviyeleri ve fitness besinleri:\n"
        f"- Whey protein tozu (1 ölçek, 30g): 120 kcal, 24g protein, 3g karb, 1.5g yağ\n"
        f"- Kazein protein (1 ölçek, 33g): 120 kcal, 24g protein, 3g karb, 1g yağ\n"
        f"- Kreatin monohidrat (1 ölçek, 5g): 0 kcal, 0g protein, 0g karb, 0g yağ\n"
        f"- BCAA (1 ölçek, 7g): 0 kcal, 0g protein, 0g karb, 0g yağ\n"
        f"- Protein bar (1 adet, 60g): 220 kcal, 20g protein, 22g karb, 8g yağ\n"
        f"- Pirinç patlağı (1 adet, 8g): 28 kcal, 0.7g protein, 6g karb, 0.2g yağ\n"
        f"- Fıstık ezmesi (1 yemek kaşığı, 15g): 94 kcal, 4g protein, 3g karb, 8g yağ\n"
        f"- Bal (1 yemek kaşığı, 21g): 64 kcal, 0g protein, 17g karb, 0g yağ\n\n"
        f"Kullanıcının yediği: {yemekler_for_prompt}\n\n"
        f"Her besini ayrı hesapla ve topla. Sonucu SADECE aşağıdaki JSON formatında döndür.\n"
        f"Değerler gerçek sayı olmalı (0 değil), ondalık olabilir:\n"
        f'{{"kalori": 520, "protein": 38, "karb": 45, "yag": 14}}'
    )

    nutrients = {"kalori": 0, "protein": 0, "karb": 0, "yag": 0}
    raw = ""

    try:
        raw = _bedrock_chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="SADECE JSON döndür. Açıklama yapma, markdown kullanma, sadece düz JSON objesi. Tüm değerler sayı olmalı.",
            max_tokens=150,
            temperature=0.0,
        ).strip()
        raw = raw.replace("```json", "").replace("```", "").strip()

        # Bazen AI fazladan metin ekliyor, sadece { } arasını al
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            raw = raw[start:end]
        
        nutrients = json.loads(raw)
        for key in ("kalori", "protein", "karb", "yag"):
            try:
                nutrients[key] = round(float(nutrients.get(key, 0)), 1)
            except (TypeError, ValueError):
                nutrients[key] = 0
    except Exception as e:
        print(f"MEAL LOG ERROR: {e}")
        print(f"RAW: {raw}")

    today = datetime.utcnow().strftime("%d.%m")

    entry = MealLog(
        user_id  = current_user.id,
        ogun     = ogun,
        yemekler = yemekler,
        kalori   = nutrients.get("kalori", 0),
        protein  = nutrients.get("protein", 0),
        karb     = nutrients.get("karb", 0),
        yag      = nutrients.get("yag", 0),
        tarih    = today
    )
    db.session.add(entry)
    db.session.commit()

    quest_result = complete_quest_for_user(current_user.id, "meal_logged")
    response = {
        "message": f"{ogun} kaydedildi.",
        "nutrients": nutrients,
        "raw_debug": raw
    }
    if quest_result:
        response["quest_awarded"] = quest_result
    return jsonify(response)

@app.route("/meal-log/today")
@login_required
def today_meals():
    today = datetime.utcnow().strftime("%d.%m")
    meals = MealLog.query.filter_by(user_id=current_user.id, tarih=today)\
        .order_by(MealLog.created_at.asc()).all()

    result = []
    totals = {"kalori": 0, "protein": 0, "karb": 0, "yag": 0}
    for m in meals:
        result.append({
            "ogun": m.ogun,
            "yemekler": m.yemekler,
            "kalori": m.kalori,
            "protein": m.protein,
            "karb": m.karb,
            "yag": m.yag,
            "source": getattr(m, "source", "manual") or "manual"
        })
        totals["kalori"]  += m.kalori or 0
        totals["protein"] += m.protein or 0
        totals["karb"]    += m.karb or 0
        totals["yag"]     += m.yag or 0

    return jsonify({"meals": result, "totals": totals, "tarih": today})

@app.route("/meal-log/history")
@login_required
def meal_history():
    meals = MealLog.query.filter_by(user_id=current_user.id)\
        .order_by(MealLog.created_at.desc()).limit(50).all()

    days = {}
    for m in meals:
        if m.tarih not in days:
            days[m.tarih] = {"meals": [], "totals": {"kalori": 0, "protein": 0, "karb": 0, "yag": 0}}
        days[m.tarih]["meals"].append({
            "ogun": m.ogun,
            "yemekler": m.yemekler,
            "kalori": m.kalori,
            "protein": m.protein,
            "karb": m.karb,
            "yag": m.yag
        })
        days[m.tarih]["totals"]["kalori"]  += m.kalori or 0
        days[m.tarih]["totals"]["protein"] += m.protein or 0
        days[m.tarih]["totals"]["karb"]    += m.karb or 0
        days[m.tarih]["totals"]["yag"]     += m.yag or 0

    result = [{"tarih": k, **v} for k, v in days.items()]
    return Response(json.dumps(result, ensure_ascii=False), mimetype="application/json")

@app.route("/meal-log/review", methods=["POST"])
@login_required
def review_meals():
    today = datetime.utcnow().strftime("%d.%m")
    meals = MealLog.query.filter_by(user_id=current_user.id, tarih=today).all()

    if not meals:
        return jsonify({"error": "Bugün kayıtlı öğün yok."}), 400

    last_session = UserSession.query.filter_by(user_id=current_user.id)\
        .order_by(UserSession.created_at.desc()).first()

    target = last_session.target_calories if last_session else 2000
    goal   = last_session.goal if last_session else "genel sağlık"

    meals_text = ""
    total_cal = 0
    for m in meals:
        meals_text += f"- {m.ogun}: {m.yemekler} ({m.kalori} kcal, P:{m.protein}g K:{m.karb}g Y:{m.yag}g)\n"
        total_cal += m.kalori or 0

    prompt = (
        f"Sen bir beslenme koçusun. Türkçe yaz, İngilizce kullanma.\n"
        f"Kullanıcıya 'sen' diye hitap et.\n\n"
        f"Kullanıcının hedefi: {goal}\n"
        f"Günlük kalori hedefi: {round(target)} kcal\n"
        f"Bugün toplam: {round(total_cal)} kcal\n\n"
        f"Bugün yedikleri:\n{meals_text}\n"
        f"Bu günü değerlendir:\n"
        f"- Kalori hedefine ulaştı mı?\n"
        f"- Makro dağılımı dengeli mi?\n"
        f"- Biyoyararlanım açısından nasıl?\n"
        f"- Gluten içeriği yüksek mi?\n"
        f"- Değiştirilmesi gereken bir şey var mı?\n"
        f"Kısa ve spesifik ol, 4-5 cümle yeterli."
    )

    try:
        review = _bedrock_chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="Sen bir beslenme koçusun. Kısa, spesifik, Türkçe konuş.",
            max_tokens=400,
            temperature=0.7,
        )
    except Exception as e:
        review = f"Değerlendirme alınamadı: {str(e)}"

    return jsonify({"review": review, "total_calories": round(total_cal), "target": round(target)})

@app.route("/menu-assistant")
@login_required
def menu_assistant():
    return render_template("menu_assistant.html",
        username=current_user.username,
        profile_picture=current_user.profile_picture,
    )


def _validate_menu_url(url):
    from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
    import re as _re

    url = url.strip()
    if not url:
        return None, None, "URL gerekli."

    if not _re.match(r'^https?://', url, _re.IGNORECASE):
        if _re.match(r'^[a-zA-Z0-9]', url):
            url = "https://" + url
        else:
            return None, None, "Geçersiz URL formatı."

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None, None, "Yalnızca HTTP/HTTPS desteklenir."
    if not parsed.hostname:
        return None, None, "Geçersiz URL."
    blocked = ("127.0.0.1", "localhost", "0.0.0.0", "169.254.169.254", "[::1]")
    if parsed.hostname.lower() in blocked or parsed.hostname.startswith("10.") or parsed.hostname.startswith("192.168."):
        return None, None, "İç ağ adresleri engellendi."

    tracking_params = {"utm_source", "utm_medium", "utm_campaign", "utm_term",
                       "utm_content", "fbclid", "gclid", "ref", "source"}
    qs = parse_qs(parsed.query, keep_blank_values=False)
    cleaned_qs = {k: v for k, v in qs.items() if k.lower() not in tracking_params}
    clean_query = urlencode(cleaned_qs, doseq=True) if cleaned_qs else ""
    clean_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path,
                            parsed.params, clean_query, ""))
    return urlparse(clean_url), clean_url, None


_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]


def _fetch_page(url, timeout=10):
    import requests as http_req
    import random
    from urllib.parse import urlparse

    ua = random.choice(_USER_AGENTS)
    parsed = urlparse(url)
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer": f"{parsed.scheme}://{parsed.hostname}/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
    }

    session = http_req.Session()
    session.headers.update(headers)

    resp = session.get(url, timeout=timeout, allow_redirects=True)

    if resp.status_code == 403 or (resp.status_code == 200 and len(resp.text) < 500):
        alt_ua = random.choice([u for u in _USER_AGENTS if u != ua] or _USER_AGENTS)
        session.headers["User-Agent"] = alt_ua
        import time
        time.sleep(random.uniform(0.3, 0.8))
        resp = session.get(url, timeout=timeout, allow_redirects=True)

    resp.raise_for_status()
    return resp


def _extract_framework_state(html_text):
    import re
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text, "html.parser")
    script_tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if script_tag and script_tag.string:
        try:
            state = json.loads(script_tag.string)
            return state, "next"
        except (json.JSONDecodeError, ValueError):
            pass

    patterns = [
        (r'window\.__NEXT_DATA__\s*=\s*({.+})\s*;?\s*</script>', "next"),
        (r'window\.__NUXT__\s*=\s*({.+})\s*;?\s*</script>', "nuxt"),
        (r'window\.__DATA__\s*=\s*({.+})\s*;?\s*</script>', "data"),
        (r'window\.__INITIAL_STATE__\s*=\s*({.+})\s*;?\s*</script>', "state"),
    ]
    for pattern, framework in patterns:
        match = re.search(pattern, html_text, re.DOTALL)
        if match:
            raw = match.group(1)
            depth = 0
            end_idx = 0
            for i, ch in enumerate(raw):
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        end_idx = i + 1
                        break
            if end_idx > 0:
                try:
                    state = json.loads(raw[:end_idx])
                    return state, framework
                except (json.JSONDecodeError, ValueError):
                    continue
    return None, None


def _discover_menu_links(soup, base_parsed):
    from urllib.parse import urljoin, urlparse
    menu_keywords = (
        "menu", "yemek", "yiyecek", "kahvalt", "icecek", "tatl", "salata",
        "pizza", "burger", "makarna", "et", "tavuk", "balik", "corba",
        "aperatif", "ara-sicak", "meze", "soguk", "sicak", "izgara",
        "food", "dish", "breakfast", "lunch", "dinner", "drinks",
    )
    base_origin = f"{base_parsed.scheme}://{base_parsed.hostname}"
    found = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
            continue
        full_url = urljoin(base_origin + base_parsed.path, href)
        link_parsed = urlparse(full_url)
        if link_parsed.hostname != base_parsed.hostname:
            continue
        path_lower = link_parsed.path.lower()
        if any(kw in path_lower for kw in menu_keywords):
            clean = f"{link_parsed.scheme}://{link_parsed.hostname}{link_parsed.path}"
            if clean not in found and clean != f"{base_parsed.scheme}://{base_parsed.hostname}{base_parsed.path}":
                found.add(clean)
    return list(found)[:10]


def _extract_page_sections(html_text, soup_clean):
    from bs4 import BeautifulSoup
    import re as _re

    sections = []

    jsonld_scripts = soup_clean.find_all("script", {"type": "application/ld+json"})
    for script in jsonld_scripts:
        if script.string:
            try:
                ld = json.loads(script.string)
                items = ld if isinstance(ld, list) else [ld]
                for item in items:
                    if item.get("@type") in ("Menu", "MenuSection", "Restaurant"):
                        menu_sec = item.get("hasMenuSection", [])
                        if not isinstance(menu_sec, list):
                            menu_sec = [menu_sec]
                        for sec in menu_sec:
                            cat = sec.get("name", "Genel")
                            menu_items = sec.get("hasMenuItem", [])
                            if not isinstance(menu_items, list):
                                menu_items = [menu_items]
                            names = [mi.get("name", "") for mi in menu_items if mi.get("name")]
                            if names:
                                sections.append({"category": cat, "text": "\n".join(names)})
            except (json.JSONDecodeError, TypeError):
                pass
    if sections:
        return sections

    current_heading = "Genel"
    current_items = []

    for el in soup_clean.find_all(["h1", "h2", "h3", "h4", "h5", "p", "li", "span", "div", "td"]):
        text = el.get_text(strip=True)
        if not text or len(text) < 2:
            continue
        if el.name in ("h1", "h2", "h3", "h4", "h5"):
            if current_items:
                sections.append({"category": current_heading, "text": "\n".join(current_items)})
                current_items = []
            current_heading = text
        else:
            if len(text) > 3 and len(text) < 500:
                current_items.append(text)

    if current_items:
        sections.append({"category": current_heading, "text": "\n".join(current_items)})

    if not sections:
        menu_containers = soup_clean.find_all(
            ["div", "section", "ul"],
            class_=_re.compile(r'menu|food|dish|product|item|category', _re.IGNORECASE)
        )
        for container in menu_containers:
            items = []
            for el in container.find_all(["h4", "h3", "h2", "p", "li", "span", "div"]):
                text = el.get_text(strip=True)
                if text and 3 < len(text) < 500:
                    items.append(text)
            if items:
                sections.append({"category": "Genel", "text": "\n".join(items)})

    if not sections:
        container = soup_clean.find("main") or soup_clean.find("article") or soup_clean.find("body")
        if container:
            fallback_items = []
            for el in container.find_all(["h4", "h3", "h2", "p", "li", "span"]):
                text = el.get_text(strip=True)
                if text and 3 < len(text) < 500:
                    fallback_items.append(text)
            if fallback_items:
                sections.append({"category": "Genel", "text": "\n".join(fallback_items)})

    return sections


_FOOD_KEYWORDS = {
    "kahvalt", "salata", "corba", "çorba", "pizza", "burger", "makarna", "tavuk",
    "et ", "balık", "tost", "pilav", "izgara", "tatli", "tatlı", "içecek", "icecek",
    "kahve", "çay", "smoothie", "meze", "kebab", "köfte", "lahmacun", "pide",
    "breakfast", "salad", "soup", "pasta", "chicken", "steak", "grill", "dessert",
    "drink", "sandwich", "appetizer", "main course", "starter",
}


def _content_has_food_items(text, threshold=3):
    text_lower = text.lower()
    return sum(1 for kw in _FOOD_KEYWORDS if kw in text_lower) >= threshold


def _try_wordpress_api(base_parsed, raw_html):
    import requests as http_req
    from bs4 import BeautifulSoup

    is_wp = "wp-content" in raw_html or "wp-json" in raw_html or "wordpress" in raw_html.lower()
    if not is_wp:
        return None, []

    origin = f"{base_parsed.scheme}://{base_parsed.netloc}"
    path_parts = [p for p in base_parsed.path.strip("/").split("/") if p]

    slugs_to_try = []
    if path_parts:
        slugs_to_try.append(path_parts[-1])
    slugs_to_try.extend(["menu", "yemek", "yiyecekler", "icecekler", "food", "foods"])

    seen_slugs = set()
    unique_slugs = []
    for s in slugs_to_try:
        if s not in seen_slugs:
            seen_slugs.add(s)
            unique_slugs.append(s)
    slugs_to_try = unique_slugs

    print(f"[SCRAPER] WordPress detected — trying REST API for slugs: {slugs_to_try}")

    all_sections = []
    best_title = None

    for slug in slugs_to_try:
        try:
            api_url = f"{origin}/wp-json/wp/v2/pages?slug={slug}"
            api_resp = http_req.get(api_url, timeout=8, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            })
            if api_resp.status_code != 200:
                continue
            pages = api_resp.json()
            if not pages:
                continue

            content_html = pages[0].get("content", {}).get("rendered", "")
            title = pages[0].get("title", {}).get("rendered", "")
            if not content_html:
                continue

            soup = BeautifulSoup(content_html, "html.parser")
            text = soup.get_text(separator="\n", strip=True)

            if len(text) > 100 and _content_has_food_items(text):
                print(f"[SCRAPER] WP API hit for slug '{slug}': {len(text)} chars with food content")
                sections = _extract_page_sections(content_html, soup)
                all_sections.extend(sections)
                if not best_title:
                    best_title = title
                continue

            discovered_slugs = set()
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
                    continue
                if href.startswith("/") or href.startswith(origin):
                    full = href if href.startswith("http") else origin + href
                    link_parsed = http_req.utils.urlparse(full)
                    parts = [p for p in link_parsed.path.strip("/").split("/") if p]
                    if parts and parts[-1] not in seen_slugs:
                        discovered_slugs.add(parts[-1])

            if not best_title:
                best_title = title

            for link_slug in list(discovered_slugs)[:6]:
                seen_slugs.add(link_slug)
                sub_api = f"{origin}/wp-json/wp/v2/pages?slug={link_slug}"
                try:
                    sub_resp = http_req.get(sub_api, timeout=6, headers={
                        "User-Agent": "Mozilla/5.0", "Accept": "application/json"})
                    if sub_resp.status_code != 200:
                        continue
                    sub_pages = sub_resp.json()
                    if not sub_pages:
                        continue
                    sub_html = sub_pages[0].get("content", {}).get("rendered", "")
                    sub_soup = BeautifulSoup(sub_html, "html.parser")
                    sub_text = sub_soup.get_text(separator="\n", strip=True)
                    if len(sub_text) > 200 and _content_has_food_items(sub_text):
                        print(f"[SCRAPER] WP API sub-page '{link_slug}': {len(sub_text)} chars with food content")
                        sub_sections = _extract_page_sections(sub_html, sub_soup)
                        all_sections.extend(sub_sections)
                except Exception as e:
                    print(f"[SCRAPER] WP API sub-page '{link_slug}' failed: {type(e).__name__}: {e}")
                    continue

        except Exception as e:
            print(f"[SCRAPER] WP API attempt for '{slug}' failed: {type(e).__name__}: {e}")
            continue

    if all_sections:
        print(f"[SCRAPER] WP API total: {len(all_sections)} sections recovered")
        return best_title, all_sections

    return None, []


# ── Google Drive Interceptor ──────────────────────────────────

def _is_google_drive_url(url):
    return bool(url) and ("drive.google.com" in url or "docs.google.com" in url)


def _extract_drive_file_id(url):
    import re
    patterns = [
        r'/file/d/([a-zA-Z0-9_-]+)',
        r'/document/d/([a-zA-Z0-9_-]+)',
        r'/spreadsheets/d/([a-zA-Z0-9_-]+)',
        r'/presentation/d/([a-zA-Z0-9_-]+)',
        r'[?&]id=([a-zA-Z0-9_-]+)',
        r'/open\?id=([a-zA-Z0-9_-]+)',
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def _get_drive_direct_url(url, file_id):
    if "docs.google.com/document" in url:
        return f"https://docs.google.com/document/d/{file_id}/export?format=txt", "doc"
    if "docs.google.com/spreadsheets" in url:
        return f"https://docs.google.com/spreadsheets/d/{file_id}/export?format=csv", "sheet"
    return f"https://drive.google.com/uc?export=download&id={file_id}", "file"


def _sanitize_menu_text(text):
    import re as _re
    if not text:
        return ""
    replacements = {
        'Ä±': 'ı', 'Ä': 'ğ', 'Ã¼': 'ü',
        'Ã¶': 'ö', 'Ã§': 'ç', 'Å': 'ş',
        'Ä°': 'İ', 'Ä': 'Ğ', 'Ã': 'Ü',
        'Ã': 'Ö', 'Ã': 'Ç', 'Å': 'Ş',
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    text = _re.sub(r'[^\S\n]+', ' ', text)
    text = _re.sub(r' {2,}', ' ', text)
    text = _re.sub(r'(\n\s*){3,}', '\n\n', text)
    lines = []
    for line in text.split('\n'):
        stripped = line.strip()
        if stripped:
            lines.append(stripped)
        elif lines and lines[-1] != '':
            lines.append('')
    return '\n'.join(lines).strip()


def _extract_text_from_pdf(pdf_bytes):
    import pdfplumber
    import io

    try:
        pdf = pdfplumber.open(io.BytesIO(pdf_bytes))
    except Exception as e:
        err_str = str(e).lower()
        if "password" in err_str or "encrypt" in err_str:
            raise ValueError("PDF_ENCRYPTED")
        raise ValueError(f"PDF_CORRUPT: {type(e).__name__}")

    text_parts = []
    scanned_pages = []

    try:
        for idx, page in enumerate(pdf.pages[:20]):
            page_text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""

            tables = page.extract_tables() or []
            table_text_parts = []
            for table in tables:
                for row in table:
                    cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                    if cells:
                        table_text_parts.append(" | ".join(cells))
            table_text = "\n".join(table_text_parts)

            combined = (page_text + "\n" + table_text).strip()

            if len(combined.replace(" ", "").replace("\n", "")) < 10:
                scanned_pages.append(idx)
            else:
                text_parts.append(combined)
    finally:
        pdf.close()

    text_result = "\n\n".join(text_parts)

    if scanned_pages and len(text_result.strip()) < 50:
        print(f"[PDF] Scanned PDF detected: {len(scanned_pages)} pages with no text, forwarding to Vision OCR")
        ocr_text = _extract_pdf_pages_via_vision(pdf_bytes, scanned_pages[:5])
        if ocr_text:
            text_result = (text_result + "\n\n" + ocr_text).strip() if text_result.strip() else ocr_text
    elif scanned_pages:
        print(f"[PDF] {len(scanned_pages)} scanned pages skipped (text pages had sufficient content)")

    return _sanitize_menu_text(text_result)


def _extract_pdf_pages_via_vision(pdf_bytes, page_indices):
    import io
    try:
        import pdfplumber
        pdf = pdfplumber.open(io.BytesIO(pdf_bytes))
    except Exception:
        return ""

    results = []
    try:
        for idx in page_indices:
            if idx >= len(pdf.pages):
                continue
            page = pdf.pages[idx]
            try:
                img = page.to_image(resolution=200)
                img_buffer = io.BytesIO()
                img.original.save(img_buffer, format="PNG")
                img_bytes = img_buffer.getvalue()
                print(f"[PDF→OCR] Page {idx + 1}: rendered {len(img_bytes)} bytes")
                text = _extract_text_from_image(img_bytes, "image/png")
                if text:
                    results.append(f"[Sayfa {idx + 1}]\n{text}")
            except Exception as e:
                print(f"[PDF→OCR] Page {idx + 1} render failed: {type(e).__name__}: {e}")
                continue
    finally:
        pdf.close()

    return "\n\n".join(results)


def _compress_image_for_vision(image_bytes, max_bytes=1_500_000):
    from PIL import Image
    import io

    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception:
        return image_bytes, "image/jpeg"

    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    max_dim = 1600
    if max(img.size) > max_dim:
        ratio = max_dim / max(img.size)
        img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)

    buf = io.BytesIO()
    quality = 85
    img.save(buf, format="JPEG", quality=quality)
    while buf.tell() > max_bytes and quality > 30:
        quality -= 15
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)

    print(f"[VISION] Compressed image: {len(image_bytes)} -> {buf.tell()} bytes (q={quality}, {img.size[0]}x{img.size[1]})")
    return buf.getvalue(), "image/jpeg"


def _extract_text_from_image(image_bytes, content_type="image/jpeg"):
    import base64

    if len(image_bytes) > 1_500_000:
        image_bytes, content_type = _compress_image_for_vision(image_bytes)

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    mime = content_type.split(";")[0].strip()
    if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        mime = "image/jpeg"

    vision_system = (
        "Sen bir restoran menüsü OCR asistanısın. Görseldeki TÜM yemek ve içecek isimlerini, "
        "açıklamalarını ve fiyatlarını eksiksiz oku. Hiçbir öğeyi atlama veya özetleme. "
        "Menüdeki kategori başlıklarını koru (örn: Kahvaltılar, Salatalar, Ana Yemekler). "
        "Her yemeği ayrı satırda yaz. Türkçe karakterleri doğru kullan (ı, ş, ğ, ç, ö, ü)."
    )
    vision_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4000,
        "temperature": 0.0,
        "system": [{"type": "text", "text": vision_system, "cache_control": {"type": "ephemeral"}}],
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": (
                    "Bu restoran menüsü görselindeki TÜM yemek ve içecek isimlerini satır satır oku. "
                    "Kategori başlıklarını koru. Hiçbir öğeyi atlama, özetleme veya yorum ekleme. "
                    "Sadece menüde yazanları aynen oku."
                )},
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
            ]}
        ],
    }
    try:
        resp = bedrock_runtime.invoke_model(
            body=json.dumps(vision_body),
            modelId=BEDROCK_MODEL_ID,
            contentType='application/json',
            accept='application/json',
        )
        vision_result = json.loads(resp['body'].read())
        result = vision_result['content'][0]['text'].strip()
        print(f"[VISION OCR] Extracted {len(result)} chars")
        return result
    except Exception as e:
        print(f"[VISION OCR] Failed: {type(e).__name__}: {e}")
        return ""


def _process_google_drive_url(url):
    import requests as http_req

    file_id = _extract_drive_file_id(url)
    if not file_id:
        return None, "Google Drive bağlantısından dosya kimliği çıkarılamadı."

    direct_url, url_type = _get_drive_direct_url(url, file_id)
    print(f"[DRIVE] Detected type={url_type}, file_id={file_id}, direct_url={direct_url}")

    _DRIVE_MAX_BYTES = 50 * 1024 * 1024

    try:
        resp = http_req.get(direct_url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        }, allow_redirects=True, stream=True)
    except http_req.exceptions.Timeout:
        return None, "Google Drive dosyası indirilemedi — zaman aşımı."
    except http_req.exceptions.RequestException as e:
        return None, f"Google Drive bağlantı hatası: {type(e).__name__}"

    if resp.status_code in (403, 401):
        return None, json.dumps({
            "success": False,
            "error": "GOOGLE_DRIVE_LINK_RESTRICTED",
            "message": "Bu menü dosyası gizli olarak ayarlanmış. Lütfen Drive üzerinden dosya iznini 'Bağlantıya sahip olan herkes görüntüleyebilir' olarak değiştirip tekrar deneyin."
        })
    if resp.status_code == 404:
        return None, "Google Drive dosyası bulunamadı. Lütfen bağlantıyı kontrol edin."
    if resp.status_code != 200:
        return None, f"Google Drive hatası: HTTP {resp.status_code}"

    content_length = resp.headers.get("Content-Length")
    if content_length and int(content_length) > _DRIVE_MAX_BYTES:
        resp.close()
        size_mb = int(content_length) // (1024 * 1024)
        return None, f"Dosya çok büyük ({size_mb}MB). Maksimum 50MB desteklenir. Lütfen dosyayı küçültüp tekrar deneyin."

    content_type = resp.headers.get("Content-Type", "").lower()
    chunks = []
    downloaded = 0
    for chunk in resp.iter_content(chunk_size=256 * 1024):
        downloaded += len(chunk)
        if downloaded > _DRIVE_MAX_BYTES:
            resp.close()
            return None, "Dosya çok büyük (maks 50MB). Lütfen dosyayı küçültüp tekrar deneyin."
        chunks.append(chunk)
    file_bytes = b"".join(chunks)

    print(f"[DRIVE] Downloaded {len(file_bytes)} bytes, Content-Type: {content_type}")

    preview_lower = file_bytes[:5000].lower()
    is_drive_confirm = ("text/html" in content_type and
                        (b"virus scan" in preview_lower or b"download anyway" in preview_lower
                         or b"uc-download-link" in preview_lower or b"confirm=" in preview_lower))
    if is_drive_confirm:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(file_bytes, "html.parser")
        confirm_link = soup.find("a", {"id": "uc-download-link"})
        if confirm_link and confirm_link.get("href"):
            confirm_url = "https://drive.google.com" + confirm_link["href"]
            print(f"[DRIVE] Virus scan confirmation redirect: {confirm_url}")
            try:
                resp2 = http_req.get(confirm_url, timeout=15, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                }, allow_redirects=True, cookies=resp.cookies)
                if resp2.status_code == 200:
                    file_bytes = resp2.content
                    content_type = resp2.headers.get("Content-Type", "").lower()
                    print(f"[DRIVE] Confirmed download: {len(file_bytes)} bytes, Content-Type: {content_type}")
            except Exception as e:
                print(f"[DRIVE] Confirm download failed: {e}")

    if "application/pdf" in content_type or file_bytes[:5] == b"%PDF-":
        print(f"[DRIVE] Dispatching to PDF extractor")
        try:
            text = _extract_text_from_pdf(file_bytes)
            if text and len(text.strip()) > 20:
                text = _sanitize_menu_text(text)
                print(f"[DRIVE] PDF extracted: {len(text)} chars")
                return {"title": "Google Drive PDF Menü", "body_text": text, "headings": [], "source_url": url, "menu_source": "google_drive"}, None
            return None, "PDF dosyasından menü metni çıkarılamadı."
        except ValueError as ve:
            msg = str(ve)
            if msg == "PDF_ENCRYPTED":
                return None, "Bu PDF şifre korumalıdır. Lütfen şifresiz bir PDF yükleyin."
            if msg == "PDF_CORRUPT":
                return None, "PDF dosyası bozuk veya okunamıyor. Lütfen farklı bir dosya deneyin."
            return None, f"PDF işlenirken hata: {msg}"
        except Exception as e:
            print(f"[DRIVE] PDF extraction failed: {type(e).__name__}: {e}")
            return None, f"PDF işlenirken hata: {type(e).__name__}"

    if "text/plain" in content_type or "text/csv" in content_type or url_type in ("doc", "sheet"):
        print(f"[DRIVE] Dispatching as plain text")
        try:
            text = file_bytes.decode("utf-8", errors="replace")
        except Exception:
            text = file_bytes.decode("latin-1", errors="replace")
        if text and len(text.strip()) > 20:
            text = _sanitize_menu_text(text)
            print(f"[DRIVE] Text extracted: {len(text)} chars")
            title = "Google Drive Doküman Menü" if url_type == "doc" else "Google Drive Menü"
            return {"title": title, "body_text": text, "headings": [], "source_url": url, "menu_source": "google_drive"}, None
        return None, "Dosyadan menü metni çıkarılamadı."

    if any(t in content_type for t in ("image/jpeg", "image/png", "image/webp", "image/gif")):
        print(f"[DRIVE] Dispatching to Vision OCR")
        if len(file_bytes) > 10 * 1024 * 1024:
            return None, "Görsel dosya çok büyük (maks 10MB)."
        text = _extract_text_from_image(file_bytes, content_type)
        if text and len(text.strip()) > 20:
            text = _sanitize_menu_text(text)
            print(f"[DRIVE] Vision OCR extracted: {len(text)} chars")
            return {"title": "Google Drive Görsel Menü", "body_text": text, "headings": [], "source_url": url, "menu_source": "google_drive"}, None
        return None, "Görselden menü metni okunamadı."

    if "text/html" in content_type:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(file_bytes, "html.parser")
        for tag in soup(["script", "style", "link", "meta"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        if text and len(text.strip()) > 20:
            text = _sanitize_menu_text(text)
            print(f"[DRIVE] HTML fallback extracted: {len(text)} chars")
            return {"title": "Google Drive Menü", "body_text": text, "headings": [], "source_url": url, "menu_source": "google_drive"}, None

    return None, f"Desteklenmeyen dosya tipi: {content_type.split(';')[0]}"


@app.route("/api/proxy/scan-menu", methods=["POST"])
@login_required
def proxy_scan_menu():
    import requests as http_req
    from bs4 import BeautifulSoup
    from urllib.parse import urlparse

    data = request.get_json()
    url = (data or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "URL gerekli."}), 400

    base_parsed, clean_url, err = _validate_menu_url(url)
    if err:
        return jsonify({"error": err}), 400
    url = clean_url

    if _is_google_drive_url(url):
        print(f"[DRIVE] Intercepted Google Drive URL: {url}")
        drive_result, drive_err = _process_google_drive_url(url)
        if drive_err:
            try:
                err_payload = json.loads(drive_err)
                return jsonify(err_payload), 403
            except (json.JSONDecodeError, TypeError):
                return jsonify({"error": drive_err}), 422
        return jsonify(drive_result)

    try:
        resp = _fetch_page(url)
    except http_req.exceptions.Timeout:
        return jsonify({"error": "Zaman aşımı — site yanıt vermedi."}), 504
    except http_req.exceptions.RequestException as e:
        return jsonify({"error": f"Bağlantı hatası: {type(e).__name__}"}), 502

    content_type = resp.headers.get("Content-Type", "")
    if "html" not in content_type and "text" not in content_type:
        return jsonify({"error": "Desteklenmeyen içerik tipi."}), 415

    raw_html = resp.text
    print(f"[SCRAPER] Page 1 (main) — {url} — HTTP {resp.status_code} — {len(raw_html)} bytes")
    framework_state, fw_type = _extract_framework_state(raw_html)

    soup = BeautifulSoup(raw_html, "html.parser")
    sub_links = _discover_menu_links(soup, base_parsed)
    print(f"[SCRAPER] Discovered {len(sub_links)} sub-links, crawling {min(len(sub_links), 6)}: {sub_links[:6]}")

    for tag in soup(["script", "style", "iframe", "object", "embed", "link", "meta"]):
        tag.decompose()

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    sections = _extract_page_sections(raw_html, soup)

    import time, random
    crawl_errors = []
    for idx, sub_url in enumerate(sub_links[:6]):
        if idx > 0:
            time.sleep(random.uniform(0.5, 1.5))
        try:
            sub_resp = _fetch_page(sub_url, timeout=6)
            print(f"[SCRAPER] Page {idx+2}/{len(sub_links[:6])+1} — {sub_url} — HTTP {sub_resp.status_code}")
            sub_soup = BeautifulSoup(sub_resp.text, "html.parser")
            for tag in sub_soup(["script", "style", "iframe", "object", "embed", "link", "meta"]):
                tag.decompose()
            sub_sections = _extract_page_sections(sub_resp.text, sub_soup)
            print(f"[SCRAPER]   → Extracted {len(sub_sections)} section(s): {[s['category'] for s in sub_sections]}")
            sections.extend(sub_sections)
        except Exception as e:
            status = getattr(getattr(e, 'response', None), 'status_code', 'N/A')
            print(f"[SCRAPER] Page {idx+2} FAILED — {sub_url} — Status: {status} — {type(e).__name__}: {e}")
            crawl_errors.append({"url": sub_url, "error": f"{type(e).__name__}: {status}"})

    all_text_parts = []
    for sec in sections:
        all_text_parts.append(f"[{sec['category']}]\n{sec['text']}")
    body_text = "\n\n".join(all_text_parts)

    content_quality_ok = _content_has_food_items(body_text) if body_text else False

    if not content_quality_ok:
        print(f"[SCRAPER] Content quality low (no food keywords) — trying WordPress API fallback")
        wp_title, wp_sections = _try_wordpress_api(base_parsed, raw_html)
        if wp_sections:
            sections = wp_sections
            if wp_title:
                title = wp_title
            all_text_parts = [f"[{sec['category']}]\n{sec['text']}" for sec in sections]
            body_text = "\n\n".join(all_text_parts)
            print(f"[SCRAPER] WordPress API recovered {len(sections)} sections, {len(body_text)} chars")

    if not body_text or len(body_text.strip()) < 20:
        fallback_soup = BeautifulSoup(raw_html, "html.parser")
        for tag in fallback_soup(["script", "style", "iframe", "object", "embed", "link", "meta", "noscript", "svg"]):
            tag.decompose()
        body_text = fallback_soup.get_text(separator=" ", strip=True)
        print(f"[SCRAPER] Section extraction empty — used full-body fallback: {len(body_text)} chars")

    if not body_text or len(body_text.strip()) < 20:
        return jsonify({"error": "Menü içeriği şu anda korumalı veya okunamıyor. Lütfen linki kontrol edip tekrar deneyiniz."}), 422

    import re as _re
    body_text = _re.sub(r'\s{3,}', '  ', body_text)
    body_text = _re.sub(r'(\n\s*){3,}', '\n\n', body_text)

    headings = [sec["category"] for sec in sections if sec["category"] != "Genel"]
    unique_headings = list(dict.fromkeys(headings))[:40]

    print(f"[SCRAPER] Total sections: {len(sections)} — Unique categories: {len(unique_headings)} — Categories: {unique_headings}")
    print(f"[SCRAPER] Raw body_text length: {len(body_text)} chars")

    if len(body_text) > 18000:
        body_text = body_text[:18000]
        print(f"[SCRAPER] Truncated body_text to 18000 chars")

    result = {
        "title": title,
        "headings": unique_headings,
        "body_text": body_text,
        "source_url": url,
        "menu_source": "web_scraper",
        "sub_pages_crawled": len(sub_links[:6]),
        "total_sections": len(sections),
        "crawl_errors": crawl_errors if crawl_errors else None,
    }

    if framework_state:
        fw_str = json.dumps(framework_state, ensure_ascii=False)
        if len(fw_str) > 15000:
            fw_str = fw_str[:15000]
        result["framework_state"] = fw_str
        result["framework_type"] = fw_type

    return jsonify(result)


def _extract_categorized_items(raw_text, fw_state=None, headings=None, menu_source=None):
    menu_input = raw_text[:10000]
    if fw_state:
        menu_input = fw_state[:6000] + "\n\n" + raw_text[:6000]

    heading_hint = ""
    if headings:
        heading_hint = f"\n\nTespit edilen kategori başlıkları: {', '.join(headings)}\nBu kategorilerin HEPSİ için yemek bul. Hiçbirini atlama."

    doc_hint = ""
    if menu_source == "google_drive":
        doc_hint = ("\n\nDİKKAT: Bu metin bir PDF/görsel/doküman kaynağından çıkarılmıştır. "
                    "Tablo formatları, OCR hataları veya düzensiz boşluklar olabilir. "
                    "Satır satır dikkatlice oku, her yemek öğesini ayır. "
                    "Fiyat sütunlarını ve tablo başlıklarını (adet, fiyat, TL, ₺) yoksay.")

    prompt = f"""Aşağıdaki restoran menü metninden yemekleri KATEGORİLERİYLE çıkar.
Pazarlama metinlerini, açıklamaları, fiyatları YOKSAY. Sadece yemek/içecek adlarını al.
Her kategori altında en fazla 10 yemek olsun. Toplam en fazla 50 yemek.
Kategorileri menüdeki başlıklardan al (örn: Kahvaltılar, Salatalar, Izgara & Etler, Makarnalar, Burgerler, İçecekler, Tatlılar).
Eğer kategori bulamazsan "Genel" kullan.{heading_hint}{doc_hint}

Menü metni:
{menu_input}

SADECE aşağıdaki JSON formatında yanıt ver, başka hiçbir şey yazma:
{{"categories": {{"Kategori Adı": ["yemek1", "yemek2"], "Başka Kategori": ["yemek3"]}}}}"""

    try:
        raw = _bedrock_chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="SADECE JSON döndür. Açıklama yapma, markdown kullanma. Menüdeki TÜM kategorileri dahil et, hiçbirini atlama.",
            temperature=0.0,
            max_tokens=2500,
        ).strip()
        print(f"[EXTRACT] LLM raw response length: {len(raw)} chars")
        raw = raw.replace("```json", "").replace("```", "").strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start < 0 or end <= start:
            print(f"[EXTRACT] ERROR: No valid JSON braces found in LLM response: {raw[:200]}")
            return {}
        try:
            parsed = json.loads(raw[start:end])
        except json.JSONDecodeError as je:
            print(f"[EXTRACT] JSON parse failed: {je} — Raw snippet: {raw[start:start+300]}")
            return {}
        cats = parsed.get("categories", parsed)
        if isinstance(cats, dict):
            result = {k: v for k, v in cats.items() if isinstance(v, list)}
            print(f"[EXTRACT] LLM returned {len(result)} categories: {list(result.keys())} — Total items: {sum(len(v) for v in result.values())}")
            return result
        print(f"[EXTRACT] ERROR: Unexpected parsed structure type: {type(cats).__name__}")
    except json.JSONDecodeError as je:
        print(f"[EXTRACT] JSON ERROR: {je}")
    except Exception as e:
        print(f"[EXTRACT] ERROR: {type(e).__name__}: {e}")
    return {}


def _is_per_serving(serving_text):
    if not serving_text:
        return False
    s = serving_text.lower()
    per_100_patterns = ("per 100g", "per 100 g", "100g başına", "100 gram")
    if any(p in s for p in per_100_patterns):
        return False
    serving_patterns = ("per 1 serving", "per serving", "1 serving", "1 portion",
                        "1 plate", "1 porsiyon", "1 tabak", "per 1 cup", "per 1 bowl")
    return any(p in s for p in serving_patterns)


def _turkish_ablative_suffix(name):
    name = (name or "").strip()
    if not name:
        return "'dan"
    unvoiced = set("çÇfFhHkKpPsSşŞtT")
    back_vowels = set("aAıIoOuU")
    front_vowels = set("eEiİöÖüÜ")
    last_vowel_is_back = True
    for ch in reversed(name):
        if ch in back_vowels:
            last_vowel_is_back = True
            break
        elif ch in front_vowels:
            last_vowel_is_back = False
            break
    last_char = name[-1]
    if last_char in unvoiced:
        return "'tan" if last_vowel_is_back else "'ten"
    return "'dan" if last_vowel_is_back else "'den"


def _parse_suggestion_items(body_text):
    try:
        raw = _bedrock_chat(
            messages=[{"role": "user", "content": f"Aşağıdaki öğün önerisinden her bir yiyecek öğesini (miktar dahil) çıkar ve JSON listesi olarak döndür:\n\n\"{body_text}\"\n\nÖrnek çıktı: [\"200g tavuk göğsü\", \"1 kase pilav\", \"yeşil salata\"]"}],
            system_prompt="Kullanıcının yemek önerisinden yiyecek öğelerini çıkar. SADECE JSON array döndür, başka hiçbir şey yazma.",
            temperature=0.0,
            max_tokens=500,
        ).strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            items = json.loads(raw[start:end])
            if isinstance(items, list) and items:
                return [str(i).strip() for i in items if str(i).strip()]
    except Exception as e:
        print(f"[SUGGESTION] Failed to parse suggestion items: {type(e).__name__}: {e}")
    return []


def _lookup_macros_fatsecret(items, token):
    per_serving = {}
    per_100g = {}
    for name in items:
        try:
            fs_resp = _fs_get(FATSECRET_API_URL, params={
                "method": "foods.search",
                "search_expression": name,
                "format": "json",
                "max_results": 5,
            }, headers={"Authorization": f"Bearer {token}"}, timeout=5)
            fs_data = fs_resp.json()
            foods = fs_data.get("foods", {}).get("food", [])
            if isinstance(foods, dict):
                foods = [foods]
            if not foods:
                print(f"[MACRO ENGINE] FatSecret returned 0 results for '{name}'")
                continue

            found_serving = False
            baseline_100g = None

            for food in foods:
                desc_raw = food.get("food_description", "")
                parsed = _parse_fatsecret_desc(desc_raw)
                if not parsed:
                    continue
                cal_val = parsed.get("calories") or parsed.get("cal") or parsed.get("energy") or 0
                if not cal_val:
                    continue
                macros = {
                    "calories": float(cal_val),
                    "protein": float(parsed.get("protein", 0)),
                    "carbs": float(parsed.get("carbs", parsed.get("carbohydrate", parsed.get("carb", 0)))),
                    "fat": float(parsed.get("fat", parsed.get("total fat", 0))),
                }
                serving_text = parsed.get("serving", "")
                if _is_per_serving(serving_text):
                    per_serving[name] = macros
                    found_serving = True
                    print(f"[MACRO ENGINE] FatSecret per-serving match: '{name}' → Cal={macros['calories']}, P={macros['protein']}, C={macros['carbs']}, F={macros['fat']}")
                    break
                if baseline_100g is None:
                    baseline_100g = macros

            if not found_serving and baseline_100g:
                per_100g[name] = baseline_100g
                print(f"[MACRO ENGINE] FatSecret per-100g baseline: '{name}' → Cal={baseline_100g['calories']}/100g")

        except Exception as e:
            print(f"[MACRO ENGINE] FatSecret lookup failed for '{name}': {type(e).__name__}: {e}")
            continue
    print(f"[MACRO ENGINE] FatSecret totals: {len(per_serving)} per-serving, {len(per_100g)} per-100g, {len(items) - len(per_serving) - len(per_100g)} missed")
    return per_serving, per_100g


def _estimate_serving_weights_llm(items):
    if not items:
        return {}
    print(f"[MACRO ENGINE] Estimating serving weights for {len(items)} per-100g items")
    items_str = "\n".join(f"- {name}" for name in items)
    prompt = f"""Sen bir restoran şefi ve beslenme uzmanısın. Aşağıdaki yemeklerin Türkiye'de standart bir restoranda servis edilen 1 PORSİYONUNUN ortalama ağırlığını GRAM cinsinden tahmin et.

Kurallar:
- Sadece tabakta servis edilen yemeğin ağırlığını ver (tabak hariç)
- Garnitür, pilav, salata gibi yan ürünler dahil
- Et yemekleri: sadece et 150-200g, garnitürle 300-400g
- Salatalar: 250-350g
- Çorbalar: 250-300ml (≈ gram)
- Makarnalar: 300-400g
- Hamburger: 250-350g
- Izgara balık: 200-300g (garnitürle 350-450g)

Yemekler:
{items_str}

SADECE aşağıdaki JSON formatında yanıt ver:
{{{", ".join(f'"{name}": GRAM_SAYISI' for name in items[:3])}{"..." if len(items) > 3 else ""}}}"""

    try:
        raw = _bedrock_chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="SADECE JSON döndür. Her yemek için farklı, gerçekçi gram değerleri ver. Sayıları integer olarak yaz.",
            temperature=0.0,
            max_tokens=1000,
        ).strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(raw[start:end])
            llm_lower = {k.strip().lower(): k for k in parsed.keys()}
            results = {}
            for name in items:
                grams = parsed.get(name)
                if grams is None:
                    llm_key = llm_lower.get(name.strip().lower())
                    if llm_key:
                        grams = parsed[llm_key]
                if isinstance(grams, (int, float)) and 50 <= grams <= 1500:
                    results[name] = float(grams)
                else:
                    results[name] = 150.0
                    print(f"[MACRO ENGINE] Serving weight fallback 150g for '{name}' (raw={grams})")
            print(f"[MACRO ENGINE] Serving weights resolved: {results}")
            return results
    except Exception as e:
        print(f"[MACRO ENGINE] LLM SERVING WEIGHT ERROR: {type(e).__name__}: {e}")
    return {n: 150.0 for n in items}


_macro_cache = {}
_food_id_cache = {}   # name → fatsecret food_id
_MACRO_CACHE_MAX = 500


def _get_cached_macros(item_names):
    hits = {}
    misses = []
    for name in item_names:
        cached = _macro_cache.get(name)
        if cached is not None:
            hits[name] = cached
        else:
            misses.append(name)
    return hits, misses


def _cache_macros(macro_map):
    for name, macros in macro_map.items():
        if macros.get("calories", 0) > 0:
            if len(_macro_cache) >= _MACRO_CACHE_MAX:
                oldest = next(iter(_macro_cache))
                del _macro_cache[oldest]
            _macro_cache[name] = macros


def _repair_truncated_json(raw_json):
    import re as _re
    depth = 0
    last_valid = 0
    in_string = False
    escape_next = False
    for i, ch in enumerate(raw_json):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                last_valid = i + 1
                break
    if depth == 0 and last_valid > 0:
        return raw_json[:last_valid]
    trimmed = raw_json.rstrip()
    trimmed = _re.sub(r',\s*$', '', trimmed)
    trimmed = _re.sub(r'"[^"]*$', '', trimmed)
    trimmed = _re.sub(r',\s*$', '', trimmed)
    trimmed += '}' * depth
    return trimmed


def _estimate_macros_llm_batch(batch_items):
    if not batch_items:
        return {}
    items_str = "\n".join(f"- {name}" for name in batch_items)
    prompt = f"""Sen bir beslenme uzmanısın. Aşağıdaki restoran yemeklerinin 1 PORSİYON (standart restoran servisi) için TAHMİNİ besin değerlerini hesapla.

ÖNEMLİ: Değerler 100 gram için DEĞİL, 1 tam porsiyon (tabaktaki yemeğin tamamı) için olmalı.
Referans porsiyon ağırlıkları: Et yemekleri garnitürle ~350g, salatalar ~300g, çorbalar ~280g, makarnalar ~350g.
Her yemek için gerçekçi değerler ver. Hiçbir yemeğe aynı değerleri verme, her biri farklı olmalı.

ÖNEMLİ: JSON anahtarları olarak yemek isimlerini AYNEN aşağıdaki listeden kopyala, hiçbir harfi değiştirme:
{items_str}

SADECE aşağıdaki JSON formatında yanıt ver:
{{{", ".join(f'"{name}": {{"calories": X, "protein": Y, "carbs": Z, "fat": W}}' for name in batch_items[:3])}{"..." if len(batch_items) > 3 else ""}}}

Tüm {len(batch_items)} yemek için değer ver. Sadece JSON döndür, başka bir şey yazma."""

    max_tok = min(300 + len(batch_items) * 65, 4000)
    try:
        raw = _bedrock_chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="SADECE JSON döndür. Her yemek için 1 TAM PORSİYON (100g değil!) besin değerleri hesapla. Her yemeğe farklı, gerçekçi makro değerleri ver. JSON anahtarlarını kullanıcının verdiği isimlerle BİREBİR AYNI yaz.",
            temperature=0.0,
            max_tokens=max_tok,
        ).strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        start = raw.find("{")
        if start < 0:
            print(f"[MACRO ENGINE] LLM response has no JSON braces: {raw[:200]}")
            return {}

        json_str = raw[start:]
        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError:
            repaired = _repair_truncated_json(json_str)
            try:
                parsed = json.loads(repaired)
                print(f"[MACRO ENGINE] Repaired truncated JSON: {len(json_str)} → {len(repaired)} chars")
            except json.JSONDecodeError as je:
                print(f"[MACRO ENGINE] JSON repair failed: {je} — raw[{start}:{start+200}]: {raw[start:start+200]}")
                return {}

        print(f"[MACRO ENGINE] LLM batch returned {len(parsed)} keys")

        import re as _re
        _num_pat = _re.compile(r"(\d+(?:[.,]\d+)?)")
        def _safe_float(v):
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                match = _num_pat.search(v.replace(",", "."))
                if match:
                    return float(match.group(1))
            return 0.0

        llm_key_map = {k.strip().lower(): k for k in parsed.keys()}
        results = {}
        for name in batch_items:
            m = None
            if name in parsed and isinstance(parsed[name], dict):
                m = parsed[name]
            else:
                llm_key = llm_key_map.get(name.strip().lower())
                if llm_key and isinstance(parsed.get(llm_key), dict):
                    m = parsed[llm_key]

            if m:
                macros = {
                    "calories": _safe_float(m.get("calories", 0)),
                    "protein": _safe_float(m.get("protein", 0)),
                    "carbs": _safe_float(m.get("carbs", 0)),
                    "fat": _safe_float(m.get("fat", 0)),
                }
                if macros["calories"] > 0:
                    results[name] = macros

        return results
    except Exception as e:
        import traceback
        print(f"[MACRO ENGINE] LLM BATCH ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()
    return {}


_LLM_MACRO_BATCH_SIZE = 15


def _estimate_macros_llm(items):
    if not items:
        return {}
    print(f"[MACRO ENGINE] LLM fallback for {len(items)} items (batch size {_LLM_MACRO_BATCH_SIZE}): {items[:5]}{'...' if len(items)>5 else ''}")
    all_results = {}
    for i in range(0, len(items), _LLM_MACRO_BATCH_SIZE):
        batch = items[i:i + _LLM_MACRO_BATCH_SIZE]
        print(f"[MACRO ENGINE] Processing batch {i // _LLM_MACRO_BATCH_SIZE + 1}/{(len(items) - 1) // _LLM_MACRO_BATCH_SIZE + 1} ({len(batch)} items)")
        batch_results = _estimate_macros_llm_batch(batch)
        all_results.update(batch_results)
    print(f"[MACRO ENGINE] LLM total resolved: {len(all_results)}/{len(items)} items with non-zero macros")
    return all_results


def _score_item(macros, remaining):
    import math
    rem_cal = max(remaining["calories"], 1)
    rem_pro = max(remaining["protein"], 1)
    rem_fat = max(remaining["fat"], 1)
    rem_carb = max(remaining["carbs"], 1)

    ideal_cal = rem_cal * 0.40
    ideal_pro = rem_pro * 0.40
    ideal_fat = rem_fat * 0.35
    ideal_carb = rem_carb * 0.40

    w_cal, w_pro, w_fat, w_carb = 0.25, 0.35, 0.20, 0.20

    err_cal = ((macros["calories"] - ideal_cal) / rem_cal) ** 2
    err_pro = ((macros["protein"] - ideal_pro) / rem_pro) ** 2
    err_fat = ((macros["fat"] - ideal_fat) / rem_fat) ** 2
    err_carb = ((macros["carbs"] - ideal_carb) / rem_carb) ** 2

    wmse = w_cal * err_cal + w_pro * err_pro + w_fat * err_fat + w_carb * err_carb
    base_score = max(0.0, 100.0 * math.exp(-3.5 * wmse))

    penalty = 0.0
    cal_ratio = macros["calories"] / rem_cal
    fat_ratio = macros["fat"] / rem_fat
    carb_ratio = macros["carbs"] / rem_carb

    if cal_ratio > 0.70:
        penalty += (cal_ratio - 0.70) * 40
    if fat_ratio > 0.60:
        penalty += (fat_ratio - 0.60) * 30
    if carb_ratio > 0.75:
        penalty += (carb_ratio - 0.75) * 20

    pro_ratio = macros["protein"] / rem_pro
    bonus = 0.0
    if 0.25 <= pro_ratio <= 0.50:
        bonus += 10.0 * (1.0 - abs(pro_ratio - 0.375) / 0.125)

    score = max(0.0, min(100.0, base_score - penalty + bonus))

    warnings = []
    if cal_ratio > 0.80:
        warnings.append("Günlük kalori limitinin %80'ini aşıyor")
    if fat_ratio > 0.80:
        warnings.append("Günlük yağ limitinin %80'ini aşıyor")
    if carb_ratio > 0.85:
        warnings.append("Karbonhidrat limitine yakın")

    reason_parts = []
    if pro_ratio >= 0.25:
        reason_parts.append(f"Kalan {remaining['protein']:.0f}g protein hedefinle uyumlu")
    if cal_ratio <= 0.50:
        reason_parts.append("Kalori bütçesine uygun")
    if fat_ratio <= 0.40:
        reason_parts.append("Düşük yağ")
    if not reason_parts:
        reason_parts.append(f"{macros['calories']:.0f} kcal · {macros['protein']:.0f}g protein")

    return round(score, 4), warnings, " · ".join(reason_parts)


@app.route("/api/menu/analyze", methods=["POST"])
@login_required
def analyze_menu():
    data = request.get_json()
    raw_text = (data or {}).get("menu_text", "").strip()
    fw_state = (data or {}).get("framework_state")
    menu_source = (data or {}).get("menu_source", "web_scraper")

    if not raw_text or len(raw_text.replace(" ", "").replace("\n", "")) < 15:
        return jsonify({
            "success": False,
            "error": "EMPTY_MENU_TEXT",
            "message": "Menü dökümanından anlamlı bir metin çıkarılamadı. Lütfen dosyanın taranabilir ve okunabilir bir menü içerdiğinden emin olun.",
            "items": [], "categories": {},
        }), 400

    sess = UserSession.query.filter_by(user_id=current_user.id)\
        .order_by(UserSession.created_at.desc()).first()
    if not sess or not sess.target_calories:
        return jsonify({"error": "Profil verileri eksik."}), 400

    today_str = datetime.utcnow().strftime("%d.%m")
    meals = MealLog.query.filter_by(user_id=current_user.id, tarih=today_str).all()
    consumed = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}
    for m in meals:
        consumed["calories"] += m.kalori or 0
        consumed["protein"]  += m.protein or 0
        consumed["carbs"]    += m.karb or 0
        consumed["fat"]      += m.yag or 0

    target_cal = sess.target_calories
    goal = sess.goal or ""
    protein_target = target_cal * (0.30 if goal == "kas kazanma" else 0.25) / 4
    fat_target = target_cal * 0.25 / 9
    carb_target = target_cal * (0.45 if goal == "kas kazanma" else 0.50) / 4

    remaining = {
        "calories": max(target_cal - consumed["calories"], 0),
        "protein": max(protein_target - consumed["protein"], 0),
        "carbs": max(carb_target - consumed["carbs"], 0),
        "fat": max(fat_target - consumed["fat"], 0),
    }

    headings_hint = (data or {}).get("headings")
    try:
        categorized = _extract_categorized_items(raw_text, fw_state, headings=headings_hint, menu_source=menu_source)
    except Exception as e:
        print(f"[ANALYZE] Extraction crashed: {type(e).__name__}: {e}")
        categorized = {}

    if not categorized:
        print(f"[ANALYZE] First extraction returned empty — retrying without framework_state")
        try:
            categorized = _extract_categorized_items(raw_text, None, headings=headings_hint, menu_source=menu_source)
        except Exception as e:
            print(f"[ANALYZE] Retry extraction crashed: {type(e).__name__}: {e}")
            categorized = {}

    if not categorized:
        print(f"[ANALYZE] FAILED: No food items extracted. raw_text length={len(raw_text)}, "
              f"has_food_keywords={_content_has_food_items(raw_text)}, "
              f"first 300 chars: {raw_text[:300]}")
        return jsonify({"success": False, "error": "OUTPUT_PARSING_FAILED",
                        "message": "Menü metni işlenirken bir hata oluştu. Lütfen tekrar deneyin.",
                        "items": [], "categories": {}}), 200

    all_items = []
    for cat, items in categorized.items():
        for name in items:
            if isinstance(name, str) and name.strip():
                all_items.append((cat, name.strip()))

    if not all_items:
        return jsonify({"success": False, "error": "OUTPUT_PARSING_FAILED",
                        "message": "Menü metni işlenirken bir hata oluştu. Lütfen tekrar deneyin.",
                        "items": [], "categories": {}}), 200

    MAX_MENU_ITEMS = 50
    item_names = list(dict.fromkeys(name for _, name in all_items))
    if len(item_names) > MAX_MENU_ITEMS:
        print(f"[MACRO ENGINE] Capping items from {len(item_names)} to {MAX_MENU_ITEMS}")
        item_names = item_names[:MAX_MENU_ITEMS]
        kept = set(item_names)
        all_items = [(cat, name) for cat, name in all_items if name in kept]
    print(f"[MACRO ENGINE] Starting macro pipeline for {len(item_names)} unique items")

    cached_hits, uncached_names = _get_cached_macros(item_names)
    if cached_hits:
        print(f"[MACRO ENGINE] Cache hit: {len(cached_hits)}/{len(item_names)} items from cache")

    macro_map = dict(cached_hits)
    per_100g_items = {}
    lookup_names = uncached_names
    if not lookup_names:
        print(f"[MACRO ENGINE] All {len(item_names)} items served from cache — skipping FatSecret + LLM")
    else:
        try:
            token = _get_fatsecret_token()
            print(f"[MACRO ENGINE] FatSecret token acquired")
            per_serving, per_100g_items = _lookup_macros_fatsecret(lookup_names, token)
            macro_map.update(per_serving)
        except Exception as e:
            print(f"[MACRO ENGINE] FatSecret FAILED — uncached items will use LLM fallback: {type(e).__name__}: {e}")

    if per_100g_items:
        serving_weights = _estimate_serving_weights_llm(list(per_100g_items.keys()))
        for name, base_macros in per_100g_items.items():
            grams = serving_weights.get(name, 150.0)
            scale = grams / 100.0
            scaled = {
                "calories": round(base_macros["calories"] * scale, 1),
                "protein": round(base_macros["protein"] * scale, 1),
                "carbs": round(base_macros["carbs"] * scale, 1),
                "fat": round(base_macros["fat"] * scale, 1),
            }
            macro_map[name] = scaled
            print(f"[MACRO ENGINE] Scaled per-100g→serving: '{name}' × {scale:.1f} → Cal={scaled['calories']}")

    missing = [n for n in lookup_names if n not in macro_map]
    print(f"[MACRO ENGINE] After FatSecret: {len(macro_map)} resolved, {len(missing)} missing → LLM fallback")
    if missing:
        llm_macros = _estimate_macros_llm(missing)
        macro_map.update(llm_macros)

    _cache_macros(macro_map)

    final_resolved = sum(1 for n in item_names if n in macro_map and macro_map[n].get("calories", 0) > 0)
    final_zero = len(item_names) - final_resolved
    print(f"[MACRO ENGINE] Final pipeline result: {final_resolved}/{len(item_names)} items have non-zero macros"
          + (f" — WARNING: {final_zero} items still at 0" if final_zero else ""))

    categories_result = {}
    all_scored = []

    for cat, name in all_items:
        macros = macro_map.get(name)
        has_macros = macros is not None and macros.get("calories", 0) > 0

        if not has_macros:
            macros = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}
            print(f"[MACRO ENGINE] ZERO-MACRO ITEM: '{name}' — no data from FatSecret or LLM")

        if has_macros:
            score, warnings, reason = _score_item(macros, remaining)
        else:
            score, warnings, reason = 0, [], "Besin değerleri hesaplanamadı"

        item_obj = {
            "name": name,
            "macros": {k: int(round(v)) for k, v in macros.items()},
            "score": score,
            "warnings": warnings,
            "reason": reason,
        }

        if cat not in categories_result:
            categories_result[cat] = []
        categories_result[cat].append(item_obj)
        if has_macros:
            all_scored.append(item_obj)

    for cat in categories_result:
        categories_result[cat].sort(key=lambda x: (-x["score"], x["name"]))

    all_scored.sort(key=lambda x: (-x["score"], x["name"]))
    coach_picks = all_scored[:3]

    print(f"[DEBUG] Total unique categories found: {len(categories_result.keys())} — Categories: {list(categories_result.keys())}")
    print(f"[DEBUG] Total items in payload: {sum(len(v) for v in categories_result.values())} — Scored items: {len(all_scored)}")
    print(f"[ALGORITHM DEBUG] Top 3 Raw Scores: {[(item['name'], item['score']) for item in coach_picks]}")

    source_label = {"google_drive": "Google Drive", "web_scraper": "Web Scraper"}.get(menu_source, menu_source)

    return jsonify({
        "success": True,
        "menu_source": source_label,
        "coach_picks": coach_picks,
        "categories": categories_result,
        "items": [item for items in categories_result.values() for item in items],
        "remaining": {k: int(round(v)) for k, v in remaining.items()},
        "target": {
            "calories": int(round(target_cal)),
            "protein": int(round(protein_target)),
            "carbs": int(round(carb_target)),
            "fat": int(round(fat_target)),
        },
        "consumed": {k: int(round(v)) for k, v in consumed.items()},
    })


@app.route("/")
@login_required
def home():
    if not current_user.profile_complete:
        return redirect(url_for("setup"))
    return render_template("index.html",
        username=current_user.username,
        profile_picture=current_user.profile_picture,
        streak_count=current_user.streak_count or 0,
    )

@app.route("/edit-profile", methods=["GET", "POST"])
@login_required
def edit_profile():
    if request.method == "GET":
        supps = Supplement.query.filter_by(user_id=current_user.id)\
            .filter(Supplement.status.in_(["Active", "Low Stock"]))\
            .order_by(Supplement.created_at.desc()).all()
        return render_template("edit_profile.html",
            username=current_user.username,
            full_name=current_user.full_name or "",
            profile_picture=current_user.profile_picture or "",
            goal=current_user.goal or "",
            target_weight=current_user.target_weight,
            streak_count=current_user.streak_count or 0,
            supplements=supps,
            icons=CATEGORY_ICONS,
        )

    data = request.get_json()

    new_username = (data.get("username") or "").strip()
    new_full_name = (data.get("full_name") or "").strip()
    new_goal = (data.get("goal") or "").strip()

    if not new_username or len(new_username) < 3:
        return jsonify({"error": "Kullanıcı adı en az 3 karakter olmalıdır."}), 400

    if len(new_username) > 80:
        return jsonify({"error": "Kullanıcı adı en fazla 80 karakter olabilir."}), 400

    if new_username != current_user.username:
        if User.query.filter_by(username=new_username).first():
            return jsonify({"error": "Bu kullanıcı adı zaten alınmış."}), 400

    if len(new_full_name) > 150:
        return jsonify({"error": "Ad soyad en fazla 150 karakter olabilir."}), 400

    if "profile_picture" in data:
        new_profile_picture = (data.get("profile_picture") or "").strip()
        if len(new_profile_picture) > 500_000:
            return jsonify({"error": "Profil fotoğrafı çok büyük (maks 2MB)."}), 400
        current_user.profile_picture = new_profile_picture if new_profile_picture else None

    valid_goals = ["kilo verme", "kas kazanma", ""]
    if new_goal not in valid_goals:
        return jsonify({"error": "Geçersiz hedef seçimi."}), 400

    current_user.username = new_username
    current_user.full_name = new_full_name if new_full_name else None
    if new_goal:
        current_user.goal = new_goal
        current_user.goal_type = "loss" if new_goal == "kilo verme" else "gain"

    new_target_weight = data.get("target_weight")
    if new_target_weight is not None:
        try:
            current_user.target_weight = float(new_target_weight) if new_target_weight else None
        except (ValueError, TypeError):
            pass

    db.session.commit()
    return jsonify({"message": "Profil başarıyla güncellendi!"})

# ── FRIENDSHIP ROUTES ──

def are_friends(user_a_id, user_b_id):
    return Friendship.query.filter(
        Friendship.status == "accepted",
        db.or_(
            db.and_(Friendship.sender_id == user_a_id, Friendship.receiver_id == user_b_id),
            db.and_(Friendship.sender_id == user_b_id, Friendship.receiver_id == user_a_id),
        )
    ).first() is not None

@app.route("/friends")
@login_required
def friends_page():
    return render_template("friends.html",
        username=current_user.username,
        profile_picture=current_user.profile_picture)

@app.route("/friends/list")
@login_required
def friends_list():
    accepted = Friendship.query.filter(
        Friendship.status == "accepted",
        db.or_(Friendship.sender_id == current_user.id, Friendship.receiver_id == current_user.id)
    ).all()
    friends = []
    for f in accepted:
        friend = f.receiver if f.sender_id == current_user.id else f.sender
        xp = friend.rank_points or 0
        lvl = get_level(xp)
        friends.append({"id": friend.id, "username": friend.username,
                        "full_name": friend.full_name or friend.username,
                        "profile_picture": friend.profile_picture,
                        "rank_title": get_title(lvl),
                        "level": lvl})

    pending_in = Friendship.query.filter_by(receiver_id=current_user.id, status="pending").all()
    incoming = [{"request_id": p.id, "username": p.sender.username,
                 "full_name": p.sender.full_name or p.sender.username,
                 "profile_picture": p.sender.profile_picture} for p in pending_in]

    pending_out = Friendship.query.filter_by(sender_id=current_user.id, status="pending").all()
    outgoing = [{"request_id": p.id, "username": p.receiver.username} for p in pending_out]

    return jsonify({"friends": friends, "incoming": incoming, "outgoing": outgoing})

@app.route("/friends/search")
@login_required
def friends_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"users": []})
    users = User.query.filter(
        User.username.ilike(f"%{q}%"),
        User.id != current_user.id
    ).limit(10).all()
    results = []
    for u in users:
        existing = Friendship.query.filter(
            db.or_(
                db.and_(Friendship.sender_id == current_user.id, Friendship.receiver_id == u.id),
                db.and_(Friendship.sender_id == u.id, Friendship.receiver_id == current_user.id),
            )
        ).first()
        status = existing.status if existing else None
        results.append({"username": u.username, "full_name": u.full_name or u.username,
                        "profile_picture": u.profile_picture, "status": status})
    return jsonify({"users": results})

@app.route("/friend/request/<username>", methods=["POST"])
@login_required
def friend_request(username):
    target = User.query.filter_by(username=username).first()
    if not target:
        return jsonify({"error": "Kullanıcı bulunamadı."}), 404
    if target.id == current_user.id:
        return jsonify({"error": "Kendinize istek gönderemezsiniz."}), 400

    existing = Friendship.query.filter(
        db.or_(
            db.and_(Friendship.sender_id == current_user.id, Friendship.receiver_id == target.id),
            db.and_(Friendship.sender_id == target.id, Friendship.receiver_id == current_user.id),
        )
    ).first()
    if existing:
        if existing.status == "accepted":
            return jsonify({"error": "Zaten arkadaşsınız."}), 400
        if existing.status == "pending":
            return jsonify({"error": "Zaten bekleyen bir istek var."}), 400
        if existing.status == "rejected":
            existing.status = "pending"
            existing.sender_id = current_user.id
            existing.receiver_id = target.id
            existing.created_at = datetime.utcnow()
            db.session.commit()
            return jsonify({"message": f"{username} kullanıcısına istek gönderildi."})

    friendship = Friendship(sender_id=current_user.id, receiver_id=target.id)
    db.session.add(friendship)
    db.session.commit()
    return jsonify({"message": f"{username} kullanıcısına istek gönderildi."})

@app.route("/friend/accept/<int:request_id>", methods=["POST"])
@login_required
def friend_accept(request_id):
    fr = Friendship.query.get_or_404(request_id)
    if fr.receiver_id != current_user.id:
        return jsonify({"error": "Bu isteği kabul etme yetkiniz yok."}), 403
    if fr.status != "pending":
        return jsonify({"error": "Bu istek zaten işlenmiş."}), 400
    fr.status = "accepted"
    award_xp(fr.sender_id, 50)
    award_xp(fr.receiver_id, 50)
    log_activity(fr.sender_id, "new_friend", f"{fr.receiver.username} ile arkadaş oldu")
    log_activity(fr.receiver_id, "new_friend", f"{fr.sender.username} ile arkadaş oldu")
    db.session.commit()
    return jsonify({"message": f"{fr.sender.username} ile artık arkadaşsınız! +50 XP!", "points_awarded": 50})

@app.route("/friend/reject/<int:request_id>", methods=["POST"])
@login_required
def friend_reject(request_id):
    fr = Friendship.query.get_or_404(request_id)
    if fr.receiver_id != current_user.id:
        return jsonify({"error": "Bu isteği reddetme yetkiniz yok."}), 403
    if fr.status != "pending":
        return jsonify({"error": "Bu istek zaten işlenmiş."}), 400
    fr.status = "rejected"
    db.session.commit()
    return jsonify({"message": "İstek reddedildi."})

# ── CHAT ROUTES ──

@app.route("/chat/<username>")
@login_required
def chat_page(username):
    other = User.query.filter_by(username=username).first_or_404()
    if not are_friends(current_user.id, other.id):
        return redirect(url_for("friends_page"))
    other_xp = other.rank_points or 0
    other_lvl = get_level(other_xp)
    return render_template("chat.html",
        username=current_user.username,
        profile_picture=current_user.profile_picture,
        other_username=other.username,
        other_full_name=other.full_name or other.username,
        other_profile_picture=other.profile_picture,
        other_rank_title=get_title(other_lvl),
        other_level=other_lvl)

@app.route("/chat/<username>/messages")
@login_required
def chat_messages(username):
    other = User.query.filter_by(username=username).first_or_404()
    if not are_friends(current_user.id, other.id):
        return jsonify({"error": "Arkadaş değilsiniz."}), 403

    Message.query.filter_by(sender_id=other.id, receiver_id=current_user.id, is_read=False)\
        .update({"is_read": True})
    db.session.commit()

    messages = Message.query.filter(
        db.or_(
            db.and_(Message.sender_id == current_user.id, Message.receiver_id == other.id),
            db.and_(Message.sender_id == other.id, Message.receiver_id == current_user.id),
        )
    ).order_by(Message.timestamp.asc()).all()

    return jsonify({"messages": [
        {"id": m.id, "sender": m.sender.username, "body": m.body,
         "timestamp": m.timestamp.strftime("%H:%M"), "is_mine": m.sender_id == current_user.id,
         "message_type": m.message_type or "text"}
        for m in messages
    ]})

@app.route("/chat/<username>/send", methods=["POST"])
@login_required
def chat_send(username):
    other = User.query.filter_by(username=username).first_or_404()
    if not are_friends(current_user.id, other.id):
        return jsonify({"error": "Arkadaş değilsiniz."}), 403

    data = request.get_json()
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"error": "Mesaj boş olamaz."}), 400
    if len(body) > 2000:
        return jsonify({"error": "Mesaj çok uzun."}), 400

    msg_type = data.get("message_type", "text")
    if msg_type not in ("text", "suggestion_meal", "suggestion_workout"):
        msg_type = "text"

    msg = Message(sender_id=current_user.id, receiver_id=other.id,
                  body=body, message_type=msg_type)
    db.session.add(msg)
    db.session.commit()
    quest_result = complete_quest_for_user(current_user.id, "suggestion_sent")
    response = {"message": "Gönderildi.", "id": msg.id,
                "timestamp": msg.timestamp.strftime("%H:%M"),
                "message_type": msg_type}
    if quest_result:
        response["quest_awarded"] = quest_result
    return jsonify(response)

# ── SUGGESTION ROUTES ──

@app.route("/suggest/<username>", methods=["POST"])
@login_required
def send_suggestion(username):
    other = User.query.filter_by(username=username).first_or_404()
    if not are_friends(current_user.id, other.id):
        return jsonify({"error": "Arkadaş değilsiniz."}), 403

    data = request.get_json()
    stype = data.get("type")
    body = (data.get("body") or "").strip()
    if stype not in ("suggestion_meal", "suggestion_workout"):
        return jsonify({"error": "Geçersiz öneri tipi."}), 400
    if not body:
        return jsonify({"error": "Öneri boş olamaz."}), 400

    msg = Message(sender_id=current_user.id, receiver_id=other.id,
                  body=body, message_type=stype)
    db.session.add(msg)
    db.session.commit()
    quest_result = complete_quest_for_user(current_user.id, "suggestion_sent")
    response = {"message": "Öneri gönderildi!", "id": msg.id}
    if quest_result:
        response["quest_awarded"] = quest_result
    return jsonify(response)

@app.route("/suggest/respond/<int:msg_id>", methods=["POST"])
@login_required
def respond_suggestion(msg_id):
    msg = Message.query.get_or_404(msg_id)
    if msg.receiver_id != current_user.id:
        return jsonify({"error": "Bu öneri size ait değil."}), 403
    if msg.message_type not in ("suggestion_meal", "suggestion_workout"):
        return jsonify({"error": "Bu bir öneri mesajı değil."}), 400

    data = request.get_json()
    action = data.get("action")
    if action not in ("accept", "decline"):
        return jsonify({"error": "Geçersiz işlem."}), 400

    if action == "accept":
        msg.message_type = msg.message_type + "_accepted"
        reply = Message(sender_id=current_user.id, receiver_id=msg.sender_id,
                        body=f"✅ Önerini kabul ettim: {msg.body[:100]}",
                        message_type="text")
        db.session.add(reply)

        nutrients = None
        if "meal" in msg.message_type:
            nutrients = _process_meal_suggestion_accept(msg)
    else:
        msg.message_type = msg.message_type + "_declined"

    db.session.commit()

    resp = {"message": "Kabul edildi!" if action == "accept" else "Reddedildi.",
            "new_type": msg.message_type}
    if action == "accept" and nutrients:
        resp["nutrients"] = nutrients
        resp["message"] = f"Kabul edildi! {int(nutrients['kalori'])} kcal eklendi"
    return jsonify(resp)


def _process_meal_suggestion_accept(msg):
    sender = User.query.get(msg.sender_id)
    sender_name = sender.full_name or sender.username if sender else "Arkadaş"
    suffix = _turkish_ablative_suffix(sender_name)
    ogun_title = f"{sender_name}{suffix} alınan öneri"

    items = _parse_suggestion_items(msg.body)
    if not items:
        print(f"[SUGGESTION] Could not parse items from: {msg.body[:100]}")
        entry = MealLog(
            user_id=current_user.id, ogun=ogun_title,
            yemekler=msg.body[:200], kalori=0, protein=0, karb=0, yag=0,
            tarih=datetime.utcnow().strftime("%d.%m")
        )
        db.session.add(entry)
        return None

    total = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}
    try:
        cached_hits, uncached = _get_cached_macros(items)
        macro_map = dict(cached_hits)
        per_100g_items = {}

        if uncached:
            try:
                token = _get_fatsecret_token()
                per_serving, per_100g_items = _lookup_macros_fatsecret(uncached, token)
                macro_map.update(per_serving)
            except Exception as e:
                print(f"[SUGGESTION] FatSecret failed, using LLM fallback: {e}")

            if per_100g_items:
                weights = _estimate_serving_weights_llm(list(per_100g_items.keys()))
                for name, base in per_100g_items.items():
                    scale = weights.get(name, 150.0) / 100.0
                    macro_map[name] = {
                        "calories": round(base["calories"] * scale, 1),
                        "protein": round(base["protein"] * scale, 1),
                        "carbs": round(base["carbs"] * scale, 1),
                        "fat": round(base["fat"] * scale, 1),
                    }

            missing = [n for n in uncached if n not in macro_map]
            if missing:
                llm_macros = _estimate_macros_llm(missing)
                macro_map.update(llm_macros)

            _cache_macros(macro_map)

        for item in items:
            m = macro_map.get(item, {})
            total["calories"] += m.get("calories", 0)
            total["protein"] += m.get("protein", 0)
            total["carbs"] += m.get("carbs", 0)
            total["fat"] += m.get("fat", 0)

    except Exception as e:
        print(f"[SUGGESTION] Macro lookup pipeline failed: {type(e).__name__}: {e}")
        try:
            llm_macros = _estimate_macros_llm(items)
            for item in items:
                m = llm_macros.get(item, {})
                total["calories"] += m.get("calories", 0)
                total["protein"] += m.get("protein", 0)
                total["carbs"] += m.get("carbs", 0)
                total["fat"] += m.get("fat", 0)
        except Exception:
            pass

    entry = MealLog(
        user_id=current_user.id, ogun=ogun_title,
        yemekler=", ".join(items),
        kalori=round(total["calories"], 1),
        protein=round(total["protein"], 1),
        karb=round(total["carbs"], 1),
        yag=round(total["fat"], 1),
        tarih=datetime.utcnow().strftime("%d.%m")
    )
    db.session.add(entry)
    print(f"[SUGGESTION] Logged meal: {ogun_title} → {total['calories']:.0f} kcal")
    return {"kalori": entry.kalori, "protein": entry.protein, "karb": entry.karb, "yag": entry.yag}

# ── FEED ROUTES ──

@app.route("/feed")
@login_required
def feed_page():
    return render_template("feed.html",
        username=current_user.username,
        profile_picture=current_user.profile_picture)

@app.route("/feed/data")
@login_required
def feed_data():
    accepted = Friendship.query.filter(
        Friendship.status == "accepted",
        db.or_(Friendship.sender_id == current_user.id,
               Friendship.receiver_id == current_user.id)
    ).all()
    friend_ids = set()
    for f in accepted:
        friend_ids.add(f.receiver_id if f.sender_id == current_user.id else f.sender_id)

    if not friend_ids:
        return jsonify({"activities": [], "empty": True})

    page = request.args.get("page", 1, type=int)
    per_page = 20
    activities = Activity.query.filter(Activity.user_id.in_(friend_ids))\
        .order_by(Activity.timestamp.desc())\
        .offset((page - 1) * per_page).limit(per_page).all()

    result = []
    for a in activities:
        clap_count = ActivityClap.query.filter_by(activity_id=a.id).count()
        has_clapped = ActivityClap.query.filter_by(
            activity_id=a.id, user_id=current_user.id).first() is not None
        result.append({
            "id": a.id,
            "username": a.user.username,
            "full_name": a.user.full_name or a.user.username,
            "profile_picture": a.user.profile_picture,
            "activity_type": a.activity_type,
            "content": a.content,
            "icon": ACTIVITY_ICONS.get(a.activity_type, "📌"),
            "timestamp": a.timestamp.strftime("%d.%m.%Y %H:%M"),
            "clap_count": clap_count,
            "has_clapped": has_clapped,
        })

    return jsonify({"activities": result, "empty": len(result) == 0})

@app.route("/feed/clap/<int:activity_id>", methods=["POST"])
@login_required
def feed_clap(activity_id):
    activity = Activity.query.get_or_404(activity_id)
    existing = ActivityClap.query.filter_by(
        activity_id=activity_id, user_id=current_user.id).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        count = ActivityClap.query.filter_by(activity_id=activity_id).count()
        return jsonify({"clapped": False, "count": count})

    clap = ActivityClap(activity_id=activity_id, user_id=current_user.id)
    db.session.add(clap)
    db.session.commit()
    count = ActivityClap.query.filter_by(activity_id=activity_id).count()
    return jsonify({"clapped": True, "count": count})

# ── SUPPLEMENT ROUTES ──

@app.route("/supplements")
@login_required
def supplements_page():
    supps = Supplement.query.filter_by(user_id=current_user.id)\
        .order_by(Supplement.created_at.desc()).all()
    return render_template("manage_stack.html",
        username=current_user.username,
        profile_picture=current_user.profile_picture,
        supplements=supps,
        categories=SUPPLEMENT_CATEGORIES,
        statuses=SUPPLEMENT_STATUSES,
        icons=CATEGORY_ICONS)

@app.route("/supplement/add", methods=["POST"])
@login_required
def supplement_add():
    data = request.get_json()
    name = (data.get("product_name") or "").strip()
    brand = (data.get("brand") or "").strip()
    if not name or not brand:
        return jsonify({"error": "Ürün adı ve marka zorunludur."}), 400

    category = data.get("category", "Other")
    if category not in SUPPLEMENT_CATEGORIES:
        category = "Other"
    status = data.get("status", "Active")
    if status not in SUPPLEMENT_STATUSES:
        status = "Active"

    def parse_rating(val):
        if val is None or val == "" or val == 0:
            return None
        try:
            v = int(val)
            return v if 1 <= v <= 5 else None
        except (ValueError, TypeError):
            return None

    supp = Supplement(
        user_id=current_user.id,
        product_name=name, brand=brand,
        category=category, status=status,
        rating_effect=parse_rating(data.get("rating_effect")),
        rating_taste=parse_rating(data.get("rating_taste")),
        rating_digestion=parse_rating(data.get("rating_digestion")),
        rating_price=parse_rating(data.get("rating_price")),
        review_text=(data.get("review_text") or "").strip() or None,
        price_paid=float(data["price_paid"]) if data.get("price_paid") else None,
        is_public=data.get("is_public", True),
    )
    db.session.add(supp)

    first_entry = Supplement.query.filter_by(user_id=current_user.id).count() == 0
    db.session.commit()

    if first_entry or Supplement.query.filter_by(user_id=current_user.id).count() == 1:
        award_xp(current_user.id, 25)
        db.session.commit()

    log_activity(current_user.id, "new_supplement", f"{name} ({category}) stack'ine eklendi")
    quest_result = complete_quest_for_user(current_user.id, "supplement_added")

    response = {"message": "Supplement eklendi!", "id": supp.id}
    if quest_result:
        response["quest_awarded"] = quest_result
    return jsonify(response)

@app.route("/supplement/edit/<int:sid>", methods=["POST"])
@login_required
def supplement_edit(sid):
    supp = Supplement.query.get_or_404(sid)
    if supp.user_id != current_user.id:
        return jsonify({"error": "Yetkiniz yok."}), 403

    data = request.get_json()

    if "product_name" in data:
        name = (data["product_name"] or "").strip()
        if name:
            supp.product_name = name
    if "brand" in data:
        brand = (data["brand"] or "").strip()
        if brand:
            supp.brand = brand
    if "category" in data and data["category"] in SUPPLEMENT_CATEGORIES:
        supp.category = data["category"]
    if "status" in data and data["status"] in SUPPLEMENT_STATUSES:
        supp.status = data["status"]

    def parse_rating(val):
        if val is None or val == "" or val == 0:
            return None
        try:
            v = int(val)
            return v if 1 <= v <= 5 else None
        except (ValueError, TypeError):
            return None

    for field in ["rating_effect", "rating_taste", "rating_digestion", "rating_price"]:
        if field in data:
            setattr(supp, field, parse_rating(data[field]))
    if "review_text" in data:
        supp.review_text = (data["review_text"] or "").strip() or None
    if "price_paid" in data:
        supp.price_paid = float(data["price_paid"]) if data["price_paid"] else None
    if "is_public" in data:
        supp.is_public = bool(data["is_public"])

    db.session.commit()
    return jsonify({"message": "Supplement güncellendi!"})

@app.route("/supplement/delete/<int:sid>", methods=["POST"])
@login_required
def supplement_delete(sid):
    supp = Supplement.query.get_or_404(sid)
    if supp.user_id != current_user.id:
        return jsonify({"error": "Yetkiniz yok."}), 403
    db.session.delete(supp)
    db.session.commit()
    return jsonify({"message": "Supplement silindi."})


@app.route("/update-weight", methods=["POST"])
@login_required
def update_weight():
    data = request.get_json()
    weight = data.get("weight")

    if not weight:
        return jsonify({"error": "Kilo zorunludur"}), 400

    try:
        weight = float(weight)
    except ValueError:
        return jsonify({"error": "Kilo sayısal olmalıdır"}), 400

    current_user.weight = weight

    bmr             = calculate_bmr(weight, current_user.height, current_user.age, current_user.gender)
    tdee            = calculate_tdee(bmr, current_user.current_activity)
    target_calories = calculate_target(tdee, current_user.goal)

    last_sess = UserSession.query.filter_by(user_id=current_user.id)\
        .order_by(UserSession.created_at.desc()).first()
    if last_sess:
        last_sess.weight          = weight
        last_sess.bmr             = bmr
        last_sess.tdee            = tdee
        last_sess.target_calories = target_calories

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_checkin = WeeklyCheckIn.query.filter(
        WeeklyCheckIn.user_id == current_user.id,
        WeeklyCheckIn.created_at >= today_start
    ).first()
    if today_checkin:
        today_checkin.weight = weight
    else:
        db.session.add(WeeklyCheckIn(user_id=current_user.id, weight=weight))

    db.session.commit()

    return jsonify({
        "bmr": round(bmr),
        "tdee": round(tdee),
        "target_calories": round(target_calories)
    })


@app.route("/api/activity/log", methods=["POST"])
@login_required
def log_daily_activity():
    data = request.get_json()
    steps = int(data.get("steps", 0))
    intensity = data.get("intensity", "moderate")

    if intensity not in MET_CONFIG:
        return jsonify({"error": "Geçersiz yoğunluk"}), 400
    if steps <= 0:
        return jsonify({"error": "Adım sayısı pozitif olmalı"}), 400

    weight = current_user.weight or 70
    height = current_user.height or 170
    calories, distance, duration = calculate_activity_calories(steps, intensity, weight, height)

    today_key = date.today().isoformat()
    existing = DailyActivity.query.filter_by(
        user_id=current_user.id, date_key=today_key, intensity=intensity
    ).first()

    if existing:
        existing.steps = steps
        existing.calories_burned = calories
        existing.distance_km = distance
        existing.duration_min = duration
    else:
        db.session.add(DailyActivity(
            user_id=current_user.id, steps=steps, intensity=intensity,
            calories_burned=calories, distance_km=distance,
            duration_min=duration, date_key=today_key
        ))

    db.session.commit()
    return jsonify({
        "message": f"{steps} adım kaydedildi.",
        "calories_burned": calories,
        "distance_km": distance,
        "duration_min": duration
    })


@app.route("/api/activity/today")
@login_required
def today_activity():
    today_key = date.today().isoformat()
    activities = DailyActivity.query.filter_by(
        user_id=current_user.id, date_key=today_key
    ).all()

    total_calories = sum(a.calories_burned or 0 for a in activities)
    total_steps = sum(a.steps or 0 for a in activities)
    total_distance = sum(a.distance_km or 0 for a in activities)

    entries = [{
        "intensity": a.intensity, "steps": a.steps,
        "calories_burned": a.calories_burned,
        "distance_km": a.distance_km, "duration_min": a.duration_min,
    } for a in activities]

    return jsonify({
        "total_calories": round(total_calories, 1),
        "total_steps": total_steps,
        "total_distance": round(total_distance, 2),
        "entries": entries
    })


@app.route("/chat", methods=["POST"])
@login_required
def chat():
    data = request.get_json()

    required_fields = ["weight", "height", "age",
                       "gender", "goal", "fitness_level", "current_activity"]
    for field in required_fields:
        if not data.get(field):
            return jsonify({"reply": f"{field} alanı eksik"}), 400

    try:
        weight = float(data["weight"])
        height = float(data["height"])
        age    = int(data["age"])
    except ValueError:
        return jsonify({"reply": "Kilo, boy ve yaş sayısal olmalıdır."}), 400

    name             = current_user.username  # formdan değil, oturumdan al
    gender           = data["gender"]
    goal             = data["goal"]
    level            = data["fitness_level"]
    current_activity = data["current_activity"]
    user_message     = data.get("message", "")

    # Kullanıcının önceki kaydını çek
    previous_session = UserSession.query.filter_by(user_id=current_user.id)\
        .order_by(UserSession.created_at.desc())\
        .first()

    # Önceki veriler ve geçen süre
    previous_weight  = None
    previous_date    = None
    days_passed      = None

    if previous_session:
        previous_weight = previous_session.weight
        previous_date   = previous_session.created_at
        days_passed     = (datetime.utcnow() - previous_date).days

    bmr             = calculate_bmr(weight, height, age, gender)
    tdee            = calculate_tdee(bmr, current_activity)
    target_calories = calculate_target(tdee, goal)
    training_plan   = generate_training_plan(goal, level)
    nutrition_plan  = generate_nutrition_plan(goal, target_calories)
    coach_reply     = generate_coach_reply(
                          name, age, gender, weight, height,
                          goal, level, current_activity,
                          bmr, tdee, target_calories,
                          training_plan, nutrition_plan,
                          user_message,
                          previous_weight, days_passed
                      )
    
    new_session = UserSession(
        name=name, age=age, gender=gender,
        weight=weight, height=height,
        goal=goal, fitness_level=level,
        current_activity=current_activity,
        bmr=bmr, tdee=tdee,
        target_calories=target_calories,
        training_plan=training_plan,
        nutrition_plan=nutrition_plan,
        coach_reply=coach_reply,
        user_id=current_user.id
    )
    db.session.add(new_session)
    db.session.commit()

    # Karşılaştırma verisi frontend'e gönder
    comparison = None
    if previous_weight and days_passed is not None:
        diff = round(weight - previous_weight, 1)
        comparison = {
            "previous_weight" : previous_weight,
            "days_passed"     : days_passed,
            "weight_diff"     : diff
        }

    return jsonify({
        "bmr"            : round(bmr),
        "tdee"           : round(tdee),
        "target_calories": round(target_calories),
        "training_plan"  : training_plan,
        "nutrition_plan" : nutrition_plan,
        "coach_reply"    : coach_reply,
        "comparison"     : comparison
    })

COACH_SYSTEM_PROMPT = """Sen FitX uygulamasının profesyonel, son derece motive edici AI Fitness & Yaşam Koçusun. Kullanıcının veritabanına HEM okuma HEM yazma erişimin var.

VERİ KAYNAKLARIN:
- Beslenme verileri FatSecret API'den geliyor (besin öğeleri, porsiyon ölçekleri — orta/büyük yumurta gibi — ve metrikler).
- Günlük aktivite verileri Apple Health (HealthKit) ve Android Health Connect'ten senkronize ediliyor (adım sayısı, kalori yakımı).
- Bu verileri analiz ederek kişiye özel, veri odaklı önerilerde bulun.

TEMEL GÖREV:
- Kullanıcı antrenman veya yemek bahsettiğinde HEMEN tespit et.
- "yaptım", "yedim", "çalıştım", "içtim" gibi ifadeler = loglama niyeti.
- Loglama niyeti tespit ettiğinde veriyi çıkar ve ONAY İSTE (asla direkt kaydetme).
- Onay formatı: "📋 Tespit ettim: [detaylar]. Kayıt edeyim mi?"
- Beslenme soruları için FatSecret verisini kullan, gerçek makro değerleri ver.
- Trendleri, eksik logları ve başarıları proaktif olarak belirt.
- Haftalık rapor günlerinde (Pazartesi/Pazar) otomatik rapor sun.

RESTORAN MENÜ ANALİZİ:
- Restoran menülerini analiz ederken metabolik hassasiyet ve kullanıcının aktif hedeflerini her şeyin üstünde tut.
- Objektif ol, restoran pazarlama abartılarını filtrele.
- Sporcuyu masadaki en akıllı taktiksel yemeğe yönlendir.
- Kalan günlük makro bütçesine göre somut önerilerde bulun.
- Porsiyon boyutları ve gizli kaloriler konusunda uyar.

YANIT FORMATI:
- Yanıtlarını modern UI'a uygun yaz: taranabilir kısa bloklar, madde listeleri, net başlıklar.
- Uzun paragraflar yerine aksiyon odaklı kısa maddeler kullan.

KURALLAR:
- Türkçe yaz, kullanıcıya "sen" diye hitap et.
- Kısa, net, samimi ve veri odaklı konuş.
- Genel geçer tavsiye VERME — her yanıt kullanıcının verisine dayansın.
- Emin olmadığın tıbbi konularda doktora yönlendir. Tıbbi teşhis veya reçete VERME.
- Kas kazanma hedefinde kilo artışı OLUMLU, kilo vermede azalış OLUMLU.
- Tonu: elit, destekleyici, veri odaklı."""

NUTRITION_KEYWORDS = [
    "kalori", "kaç kalori", "protein", "karb", "karbonhidrat", "yağ", "makro",
    "besin", "beslenme", "yemek", "yiyecek", "içecek", "meyve", "sebze",
    "tavuk", "pirinç", "yumurta", "süt", "ekmek", "pilav", "makarna", "salata",
    "et", "balık", "peynir", "yoğurt", "çikolata", "muz", "elma",
    "calories", "chicken", "rice", "egg", "carbs", "fat",
    "gram", "100g", "200g", "porsiyon", "tabak",
]

LOGGING_WORKOUT_KEYWORDS = [
    "yaptım", "çalıştım", "kaldırdım", "press", "squat", "curl",
    "deadlift", "bench", "set", "tekrar", "rep", "antrenman yaptım",
    "egzersiz yaptım", "ağırlık", "dambıl", "barbell",
]

LOGGING_NUTRITION_KEYWORDS = [
    "yedim", "içtim", "atıştırdım", "kahvaltı yaptım", "öğle yedim",
    "akşam yedim", "ara öğün", "tükettim", "bir porsiyon",
]

CONFIRM_KEYWORDS = [
    "evet", "yes", "onayla", "kaydet", "tamam", "olur", "yap",
    "log", "do it", "confirm", "uygun", "kesinlikle", "tabii",
]

DENY_KEYWORDS = [
    "hayır", "no", "iptal", "cancel", "vazgeç", "yapma", "değil",
]


def _detect_intent(question: str) -> str:
    q = question.lower().strip()
    if any(kw in q for kw in CONFIRM_KEYWORDS) and len(q.split()) <= 5:
        return "confirm"
    if any(kw in q for kw in DENY_KEYWORDS) and len(q.split()) <= 8:
        return "deny"
    if any(kw in q for kw in LOGGING_WORKOUT_KEYWORDS):
        return "log_workout"
    if any(kw in q for kw in LOGGING_NUTRITION_KEYWORDS):
        return "log_nutrition"
    return "general"


def _is_nutrition_question(question: str) -> bool:
    q = question.lower()
    return any(kw in q for kw in NUTRITION_KEYWORDS)


def _fetch_coach_context(user_id, question=""):
    from fitx_mcp.server import (
        get_user_fitness_summary,
        get_user_workout_history,
        get_user_supplement_stack,
        get_friend_activities,
        get_user_nutrition_log,
        search_nutrition_data,
        generate_weekly_report,
    )
    parts = []
    try:
        parts.append(f"[FITNESS ÖZETİ]\n{get_user_fitness_summary(user_id)}")
    except Exception:
        parts.append("[FITNESS ÖZETİ] Veri alınamadı.")
    try:
        parts.append(f"[ANTRENMAN GEÇMİŞİ (7 gün)]\n{get_user_workout_history(user_id, 7)}")
    except Exception:
        pass
    try:
        parts.append(f"[SUPPLEMENT STACK]\n{get_user_supplement_stack(user_id)}")
    except Exception:
        pass
    try:
        parts.append(f"[BESLENME LOGU (3 gün)]\n{get_user_nutrition_log(user_id, 3)}")
    except Exception:
        pass
    try:
        parts.append(f"[ARKADAŞ AKTİVİTELERİ]\n{get_friend_activities(user_id)}")
    except Exception:
        pass

    if question and _is_nutrition_question(question):
        try:
            nutrition_result = search_nutrition_data(question)
            parts.append(f"[FATSECRET BESİN VERİSİ]\n{nutrition_result}")
        except Exception:
            parts.append("[FATSECRET BESİN VERİSİ] Veri alınamadı.")

    from analytics_engine import get_nudges
    try:
        models = {
            "WorkoutLog": WorkoutLog,
            "UserDailyNutrition": UserDailyNutrition,
            "UserSession": UserSession,
        }
        nudges = get_nudges(User.query.get(user_id), db, models)
        if nudges:
            parts.append("[PROAKTİF BİLDİRİMLER]\n" + "\n".join(nudges))
    except Exception:
        pass

    return "\n\n".join(parts)


def _extract_with_llm(question, intent_type):
    """Use a focused LLM call to extract structured data from natural language."""
    if intent_type == "log_workout":
        extraction_prompt = (
            "Aşağıdaki mesajdan antrenman verisini JSON olarak çıkar. "
            'Format: {"exercise": "...", "sets": N, "reps": N, "weight_kg": N}\n'
            "Eğer bir değer belirtilmemişse makul bir varsayılan kullan (sets=3, reps=10, weight_kg=0).\n"
            "SADECE JSON döndür, başka bir şey yazma.\n\n"
            f"Mesaj: {question}"
        )
    else:
        extraction_prompt = (
            "Aşağıdaki mesajdan beslenme verisini JSON olarak çıkar. "
            'Format: {"food_item": "...", "calories": N, "protein": N, "carbs": N, "fat": N}\n'
            "Eğer makro değerleri belirtilmemişse 0 yaz — sistem FatSecret'tan alacak.\n"
            "SADECE JSON döndür, başka bir şey yazma.\n\n"
            f"Mesaj: {question}"
        )
    try:
        raw = _bedrock_chat(
            messages=[{"role": "user", "content": extraction_prompt}],
            max_tokens=150,
            temperature=0.1,
        ).strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
    except Exception:
        pass
    return None


def _execute_pending_action(user_id):
    """Execute the pending coach action stored in session. Returns result dict or None."""
    from fitx_mcp.server import log_workout_entry, log_nutrition_entry, search_nutrition_data

    pending = session.get("pending_coach_action")
    if not pending:
        return None

    session.pop("pending_coach_action", None)

    action_type = pending.get("type")
    data = pending.get("data", {})

    if action_type == "log_workout":
        result_str = log_workout_entry(
            user_id,
            data.get("exercise", ""),
            data.get("sets", 3),
            data.get("reps", 10),
            data.get("weight_kg", 0),
        )
        result = json.loads(result_str)
        if result.get("success"):
            award_xp(user_id, 15)
            log_activity(user_id, "workout_completed",
                         f"{data.get('exercise')} — {data.get('sets')}x{data.get('reps')} @ {data.get('weight_kg')}kg")
            db.session.commit()
        return result

    elif action_type == "log_nutrition":
        cal = data.get("calories", 0)
        pro = data.get("protein", 0)
        carb = data.get("carbs", 0)
        fat = data.get("fat", 0)

        if cal == 0 and pro == 0:
            try:
                fs_result = json.loads(search_nutrition_data(data.get("food_item", "")))
                results = fs_result.get("results", [])
                if results and results[0].get("per_serving"):
                    ps = results[0]["per_serving"]
                    cal = ps.get("calories", 0)
                    pro = ps.get("protein", 0)
                    carb = ps.get("carbs", 0)
                    fat = ps.get("fat", 0)
            except Exception:
                pass

        result_str = log_nutrition_entry(user_id, data.get("food_item", ""), cal, pro, carb, fat)
        result = json.loads(result_str)
        if result.get("success"):
            award_xp(user_id, 10)
            log_activity(user_id, "nutrition_logged",
                         f"{data.get('food_item')} — {round(cal)} kcal")
            db.session.commit()
        return result

    return None


@app.route("/ask", methods=["POST"])
@login_required
def ask_coach():
    data     = request.get_json()
    question = data.get("question", "")
    history  = data.get("history", [])

    if not question.strip():
        return jsonify({"error": "Bir soru yaz."}), 400

    intent = _detect_intent(question)

    if intent == "confirm":
        result = _execute_pending_action(current_user.id)
        if result and result.get("success"):
            if result.get("today_total_volume") is not None:
                msg = (
                    f"Kaydedildi! {result['exercise']} — "
                    f"{result['sets']}x{result['reps']} @ {result['weight_kg']}kg "
                    f"(Volüm: {result['volume']}kg)\n"
                    f"Bugünkü toplam volüm: {result['today_total_volume']}kg "
                    f"({result['today_entry_count']} kayıt) +15 XP!"
                )
            else:
                t = result.get("today_totals", {})
                msg = (
                    f"Kaydedildi! {result['food_item']} — "
                    f"{result['calories']} kcal | "
                    f"P: {result['protein']}g | K: {result['carbs']}g | Y: {result['fat']}g\n"
                    f"Bugünkü toplam: {t.get('calories', 0)} kcal | "
                    f"P: {t.get('protein', 0)}g ({t.get('entry_count', 0)} kayıt) +10 XP!"
                )
            return jsonify({"answer": msg})
        elif result and result.get("error"):
            return jsonify({"answer": f"Hata: {result['error']}"})
        else:
            return jsonify({"answer": "Onaylanacak bir kayıt bulunamadı. Ne kaydetmemi istersin?"})

    if intent == "deny":
        session.pop("pending_coach_action", None)
        return jsonify({"answer": "Tamam, iptal ettim. Düzeltme yapmak istersen söyle!"})

    if intent in ("log_workout", "log_nutrition"):
        extracted = _extract_with_llm(question, intent)
        if extracted:
            session["pending_coach_action"] = {"type": intent, "data": extracted}

            if intent == "log_workout":
                ex = extracted.get("exercise", "?")
                s = extracted.get("sets", 3)
                r = extracted.get("reps", 10)
                w = extracted.get("weight_kg", 0)
                vol = s * r * w
                preview = (
                    f"📋 Tespit ettim: **{ex}** — {s} set x {r} tekrar @ {w}kg "
                    f"(Toplam volüm: {vol}kg)\n\nKayıt edeyim mi?"
                )
            else:
                food = extracted.get("food_item", "?")
                cal = extracted.get("calories", 0)
                pro = extracted.get("protein", 0)

                if cal == 0 and pro == 0:
                    from fitx_mcp.server import search_nutrition_data
                    try:
                        fs = json.loads(search_nutrition_data(food))
                        results = fs.get("results", [])
                        if results and results[0].get("per_serving"):
                            ps = results[0]["per_serving"]
                            extracted["calories"] = ps.get("calories", 0)
                            extracted["protein"] = ps.get("protein", 0)
                            extracted["carbs"] = ps.get("carbs", 0)
                            extracted["fat"] = ps.get("fat", 0)
                            session["pending_coach_action"]["data"] = extracted
                    except Exception:
                        pass

                cal = extracted.get("calories", 0)
                pro = extracted.get("protein", 0)
                carb = extracted.get("carbs", 0)
                fat = extracted.get("fat", 0)
                preview = (
                    f"📋 Tespit ettim: **{food}** — {cal} kcal | "
                    f"P: {pro}g | K: {carb}g | Y: {fat}g\n\nKayıt edeyim mi?"
                )
            return jsonify({"answer": preview})

    context = _fetch_coach_context(current_user.id, question)

    messages = []
    for h in history[-6:]:
        role = "user" if h.get("role") == "user" else "assistant"
        messages.append({"role": role, "content": h.get("text", "")[:500]})
    messages.append({"role": "user", "content": f"{context}\n\nKullanıcının sorusu: {question}"})

    try:
        answer = _bedrock_chat(
            messages=messages,
            system_prompt=COACH_SYSTEM_PROMPT,
            max_tokens=700,
            temperature=0.7,
        )
        return jsonify({"answer": answer})
    except (ClientError, Exception) as e:
        return jsonify({"error": f"Yanıt alınamadı: {str(e)}"}), 500

# ── HESAPLAMALAR ──────────────────────────────────────────

def calculate_bmr(weight, height, age, gender):
    if gender.lower() == "male":
        return 10 * weight + 6.25 * height - 5 * age + 5
    return 10 * weight + 6.25 * height - 5 * age - 161

def calculate_tdee(bmr, current_activity):
    # BUG DÜZELTMESİ: "aktif" yerine "active" — frontend ile eşleşmeli
    multipliers = {
        "sedentary" : 1.2,
        "active"    : 1.55,
        "very_active": 1.75
    }
    return bmr * multipliers.get(current_activity.lower(), 1.2)

def calculate_target(tdee, goal):
    if goal.lower() == "kilo verme":
        return tdee - 400
    elif goal.lower() == "kas kazanma":
        return tdee + 300
    return tdee

MET_CONFIG = {
    "light":    {"met": 2.0, "speed_kmh": 3.0},
    "moderate": {"met": 3.5, "speed_kmh": 4.5},
    "brisk":    {"met": 4.3, "speed_kmh": 5.5},
    "fast":     {"met": 5.0, "speed_kmh": 6.5},
}

def calculate_activity_calories(steps, intensity, weight_kg, height_cm):
    config = MET_CONFIG.get(intensity, MET_CONFIG["moderate"])
    stride_cm = height_cm * 0.414
    distance_km = steps * stride_cm / 100_000
    duration_hours = distance_km / config["speed_kmh"] if config["speed_kmh"] > 0 else 0
    calories = config["met"] * weight_kg * duration_hours
    return round(calories, 1), round(distance_km, 2), round(duration_hours * 60, 1)

def generate_training_plan(goal, level):
    plans = {
        ("kilo verme",  "beginner")    : "Haftada 3 gün 30 dk yürüyüş + 2 gün hafif kardiyo",
        ("kilo verme",  "intermediate"): "Haftada 4-5 gün koşu + direnç antrenmanı",
        ("kilo verme",  "advanced")    : "Haftada 5 gün yoğun kardiyo + full body antrenman",
        ("kas kazanma", "beginner")    : "Haftada 3 gün temel ağırlık + vücut ağırlığı egzersizleri",
        ("kas kazanma", "intermediate"): "Haftada 4 gün hipertrofi odaklı split",
        ("kas kazanma", "advanced")    : "Haftada 5 gün split program",
    }
    return plans.get((goal.lower(), level.lower()), "Standart plan")

def generate_nutrition_plan(goal, target_calories):
    if goal.lower() == "kilo verme":
        return f"Günlük {target_calories:.0f} kcal — yüksek protein, düşük işlenmiş karbonhidrat"
    elif goal.lower() == "kas kazanma":
        return f"Günlük {target_calories:.0f} kcal — kalori fazlası, protein ağırlıklı"
    return "Standart beslenme planı"


# ── AI KOÇ YORUMU ─────────────────────────────────────────

def generate_coach_reply(name, age, gender, weight, height,
                         goal, level, current_activity,
                         bmr, tdee, target_calories,
                         training_plan, nutrition_plan,
                         user_message,
                         previous_weight=None, days_passed=None):

    activity_labels = {
        "sedentary"  : "hareketsiz (masa başı iş)",
        "active"     : "aktif (haftada 3-5 gün)",
        "very_active": "çok aktif (haftada 5-6 gün)"
    }
    activity_text = activity_labels.get(current_activity, current_activity)

    # Önceki ilerleme metni
    diff = 0
    sure = "0 gün"
    progress_text = "İlk kayıt — geçmiş veri yok."
    if previous_weight and days_passed is not None:
        diff = round(weight - previous_weight, 1)
        sure = f"{days_passed} gün"
        if days_passed >= 7:
            sure = f"{days_passed // 7} hafta {days_passed % 7} gün"

    if goal.lower() == "kas kazanma":
        # Kas kazanmada kilo artışı OLUMLU
        if diff > 0:
            progress_text = (
                f"Kullanıcı {sure} önce {previous_weight} kg'dı, "
                f"şimdi {weight} kg — {abs(diff)} kg almış. "
                f"KAS KAZANMA hedefinde bu bir BAŞARI. Tebrik et. "
                f"Kalori fazlası doğru çalışıyor, devam etmesini söyle."
            )
        elif diff < 0:
            progress_text = (
                f"Kullanıcı {sure} önce {previous_weight} kg'dı, "
                f"şimdi {weight} kg — {abs(diff)} kg vermiş. "
                f"KAS KAZANMA hedefinde bu istenmeyen bir durum. "
                f"Kalori alımını artırması gerektiğini vurgula. "
                f"Kalori açığı değil, kalori fazlası hedefleniyor."
            )
        else:
            progress_text = (
                f"Kullanıcı {sure} önce de {previous_weight} kg'dı, kilo değişmemiş. "
                f"KAS KAZANMA hedefinde kilo artışı bekleniyor. "
                f"Kalori alımını biraz artırmasını öner."
            )
    else:
        # Kilo verme hedefinde
        if diff < 0:
            progress_text = (
                f"Kullanıcı {sure} önce {previous_weight} kg'dı, "
                f"şimdi {weight} kg — {abs(diff)} kg vermiş. "
                f"KİLO VERME hedefinde bu bir BAŞARI. Tebrik et. "
                f"Verme hızını değerlendir: {sure}de {abs(diff)} kg."
            )
        elif diff > 0:
            progress_text = (
                f"Kullanıcı {sure} önce {previous_weight} kg'dı, "
                f"şimdi {weight} kg — {abs(diff)} kg almış. "
                f"KİLO VERME hedefinde bu istenmeyen bir durum. "
                f"Nazikçe değin, kalori takibine odaklanmasını söyle."
            )
        else:
            progress_text = (
                f"Kullanıcı {sure} önce de {previous_weight} kg'dı, kilo değişmemiş. "
                f"KİLO VERME hedefinde plato olabilir. "
                f"Kalori açığını gözden geçirmesini öner."
            )
    prompt = f"""Sen deneyimli, samimi ve motive edici bir kişisel fitness koçusun.
Şeker boyamıyorsun ama insanı kırmıyorsun da. Türkçe konuşuyorsun.
Kullanıcıya "sen" diye hitap et, hiçbir zaman "siz" kullanma.
Yanıtında tek bir İngilizce kelime bile kullanma, tamamen Türkçe yaz.
Tavsiyelerin bu kişinin spesifik verilerine dayansın — genel geçer şeyler söyleme.
"ÇOK ÖNEMLİ: Kas kazanma hedefinde kilo artışı OLUMLUDUR, bunu asla negatif algılama. "
"Kilo verme hedefinde kilo azalışı OLUMLUDUR. "
"Hedefe göre değerlendirme yap, genel kalıplarla düşünme."

Kullanıcı bilgileri:
- İsim: {name}, Yaş: {age}, Cinsiyet: {gender}
- Güncel kilo: {weight}kg, Boy: {height}cm
- BMR: {round(bmr)} kcal, TDEE: {round(tdee)} kcal
- Hedef kalori: {round(target_calories)} kcal/gün
- Hedef: {goal}
- Fitness seviyesi: {level}
- Günlük aktivite: {activity_text}
- Kullanıcının sorusu/mesajı: {user_message if user_message else 'Yok'}

İlerleme durumu:
{progress_text}

Aşağıdaki formatta kişiye özel koçluk yorumu yaz:

GENEL DEĞERLENDİRME:
(2-3 cümle — mevcut durumu ve ilerlemeyi değerlendir, sayıları kullan)

EN KRİTİK ADIM:
(şu an odaklanması gereken tek şey, spesifik ve ölçülebilir olsun)

BU HAFTA YAP:
1. (spesifik görev — gün, süre, miktar belirt)
2. (spesifik görev — gün, süre, miktar belirt)
3. (spesifik görev — gün, süre, miktar belirt)

MOTİVASYON:
(bu kişinin durumuna özel, güçlü bir cümle)"""

    try:
        return _bedrock_chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="Sen bir fitness koçusun. Türkçe, samimi, spesifik ve motive edici konuş. Sayılar ve süreler kullan.",
            max_tokens=700,
            temperature=0.7,
        )
    except (ClientError, Exception) as e:
        return f"Koç yorumu şu an alınamıyor. Hata: {str(e)}"
    
@app.route("/nutrition")
@login_required
def nutrition():
    return render_template("nutrition.html", username=current_user.username, profile_picture=current_user.profile_picture)
    
@app.route("/nutrition-plan", methods=["POST"])
@login_required
def nutrition_plan_generate():
    data = request.get_json()
    FOOD_DATABASE = {
    "protein": {
        "hayvansal": [
            {"isim": "Tavuk Göğsü", "kalori": 165, "protein": 31, "karb": 0, "yag": 3.6, "mikro_skor": 8, "biyoyararlanim": 9, "gluten": 10},
            {"isim": "Yumurta", "kalori": 155, "protein": 13, "karb": 1.1, "yag": 11, "mikro_skor": 9, "biyoyararlanim": 9, "gluten": 10},
            {"isim": "Ton Balığı", "kalori": 132, "protein": 28, "karb": 0, "yag": 1.3, "mikro_skor": 8, "biyoyararlanim": 9, "gluten": 10},
            {"isim": "Kırmızı Et", "kalori": 250, "protein": 26, "karb": 0, "yag": 17, "mikro_skor": 8, "biyoyararlanim": 8, "gluten": 10},
            {"isim": "Yoğurt", "kalori": 61, "protein": 10, "karb": 3.6, "yag": 0.4, "mikro_skor": 7, "biyoyararlanim": 8, "gluten": 10},
            {"isim": "Somon", "kalori": 208, "protein": 20, "karb": 0, "yag": 13, "mikro_skor": 9, "biyoyararlanim": 9, "gluten": 10},
        ],
        "bitkisel": [
            {"isim": "Mercimek", "kalori": 116, "protein": 9, "karb": 20, "yag": 0.4, "mikro_skor": 8, "biyoyararlanim": 6, "gluten": 10},
            {"isim": "Nohut", "kalori": 164, "protein": 9, "karb": 27, "yag": 2.6, "mikro_skor": 7, "biyoyararlanim": 6, "gluten": 10},
            {"isim": "Tofu", "kalori": 76, "protein": 8, "karb": 1.9, "yag": 4.2, "mikro_skor": 7, "biyoyararlanim": 7, "gluten": 10},
            {"isim": "Kinoa", "kalori": 120, "protein": 4.4, "karb": 21, "yag": 1.9, "mikro_skor": 8, "biyoyararlanim": 7, "gluten": 10},
            {"isim": "Edamame", "kalori": 121, "protein": 11, "karb": 8.9, "yag": 5.2, "mikro_skor": 8, "biyoyararlanim": 7, "gluten": 10},
        ]
    },
    "karbonhidrat": [
        {"isim": "Yulaf Ezmesi", "kalori": 389, "protein": 17, "karb": 66, "yag": 7, "mikro_skor": 9, "biyoyararlanim": 8, "gluten": 6},
        {"isim": "Pirinç", "kalori": 130, "protein": 2.7, "karb": 28, "yag": 0.3, "mikro_skor": 6, "biyoyararlanim": 8, "gluten": 10},
        {"isim": "Bulgur", "kalori": 83, "protein": 3, "karb": 18, "yag": 0.2, "mikro_skor": 7, "biyoyararlanim": 7, "gluten": 3},
        {"isim": "Tatlı Patates", "kalori": 86, "protein": 1.6, "karb": 20, "yag": 0.1, "mikro_skor": 9, "biyoyararlanim": 8, "gluten": 10},
        {"isim": "Tam Buğday Ekmeği", "kalori": 247, "protein": 13, "karb": 41, "yag": 3.4, "mikro_skor": 6, "biyoyararlanim": 6, "gluten": 2},
        {"isim": "Muz", "kalori": 89, "protein": 1.1, "karb": 23, "yag": 0.3, "mikro_skor": 7, "biyoyararlanim": 9, "gluten": 10},
        {"isim": "Elma", "kalori": 52, "protein": 0.3, "karb": 14, "yag": 0.2, "mikro_skor": 7, "biyoyararlanim": 8, "gluten": 10},
    ],
    "yag": [
        {"isim": "Zeytinyağı", "kalori": 884, "protein": 0, "karb": 0, "yag": 100, "mikro_skor": 9, "biyoyararlanim": 9, "gluten": 10},
        {"isim": "Avokado", "kalori": 160, "protein": 2, "karb": 9, "yag": 15, "mikro_skor": 9, "biyoyararlanim": 9, "gluten": 10},
        {"isim": "Badem", "kalori": 579, "protein": 21, "karb": 22, "yag": 50, "mikro_skor": 8, "biyoyararlanim": 7, "gluten": 10},
        {"isim": "Ceviz", "kalori": 654, "protein": 15, "karb": 14, "yag": 65, "mikro_skor": 9, "biyoyararlanim": 7, "gluten": 10},
        {"isim": "Fındık", "kalori": 628, "protein": 15, "karb": 17, "yag": 61, "mikro_skor": 8, "biyoyararlanim": 7, "gluten": 10},
    ]
}
    # Kullanıcının son oturumundan kalori hedefini al
    last = UserSession.query.filter_by(user_id=current_user.id)\
        .order_by(UserSession.created_at.desc())\
        .first()

    if not last:
        return jsonify({"error": "Önce ana sayfadan planını oluştur."}), 400

    target_calories = last.target_calories
    goal            = last.goal

    # Seçilen gıdalar
    selected_proteins = data.get("proteins", [])
    selected_carbs    = data.get("carbs", [])
    selected_fats     = data.get("fats", [])
    custom_foods      = data.get("custom_foods", [])

    if not selected_proteins or not selected_carbs or not selected_fats:
        return jsonify({"error": "Her kategoriden en az bir gıda seç."}), 400

    # Seçilen gıdaların ortalama skorunu hesapla
    def avg_score(foods):
        if not foods:
            return 0
        all_foods = []
        for category in FOOD_DATABASE.values():
            if isinstance(category, dict):
                for items in category.values():
                    all_foods.extend(items)
            else:
                all_foods.extend(category)
        selected = [f for f in all_foods if f["isim"] in foods]
        if not selected:
            return 0
        mikro  = sum(f["mikro_skor"] for f in selected) / len(selected)
        biyo   = sum(f["biyoyararlanim"] for f in selected) / len(selected)
        gluten = sum(f["gluten"] for f in selected) / len(selected)
        return round((mikro + biyo + gluten) / 3, 1)

    overall_score = avg_score(selected_proteins + selected_carbs + selected_fats)

    if overall_score >= 8:
        score_label = "İyi"
        score_color = "green"
    elif overall_score >= 6:
        score_label = "Orta"
        score_color = "orange"
    else:
        score_label = "Kötü"
        score_color = "red"

    # AI'a gönder
    custom_text = ""
    if custom_foods:
        custom_text = f"\nKullanıcının eklediği özel gıdalar: {', '.join(custom_foods)}"

    prompt = f"""Sen bir beslenme uzmanısın. Türkçe yaz, İngilizce kelime kullanma.

Kullanıcı bilgileri:
- Günlük hedef kalori: {round(target_calories)} kcal
- Hedef: {goal}

Kullanıcının tercih ettiği gıdalar:
- Protein kaynakları: {', '.join(selected_proteins)}
- Karbonhidrat kaynakları: {', '.join(selected_carbs)}
- Yağ kaynakları: {', '.join(selected_fats)}
{custom_text}

SADECE bu gıdaları kullanarak 3 FARKLI günlük beslenme planı oluştur (Plan A, Plan B, Plan C).
Her plan tam olarak {round(target_calories)} kcal civarında olsun (±100 kcal tolerans).
Her plan kahvaltı, öğle, akşam ve ara öğün içersin.
Her öğünde gram veya adet olarak miktar belirt.
Tüm kalori ve makro değerlerini gerçek sayı olarak hesapla ve yaz.

Yanıtını SADECE şu JSON formatında ver, başka hiçbir şey yazma.
Örnek format (değerler örnek, gerçek değerleri hesapla):
{{
  "planlar": [
    {{
      "isim": "Plan A",
      "kahvalti": {{"yemekler": ["Yumurta - 3 adet", "Tam buğday ekmeği - 2 dilim"], "kalori": 420, "protein": 28, "karb": 35, "yag": 18}},
      "ogle": {{"yemekler": ["Tavuk göğsü - 150g", "Pirinç - 100g"], "kalori": 380, "protein": 48, "karb": 28, "yag": 5}},
      "aksam": {{"yemekler": ["Kırmızı et - 120g", "Tatlı patates - 150g"], "kalori": 450, "protein": 38, "karb": 30, "yag": 20}},
      "ara_ogun": {{"yemekler": ["Yoğurt - 200g", "Muz - 1 adet"], "kalori": 227, "protein": 22, "karb": 30, "yag": 1}},
      "toplam_kalori": 1477,
      "toplam_protein": 136,
      "toplam_karb": 123,
      "toplam_yag": 44
    }},
    {{
      "isim": "Plan B",
      "kahvalti": {{"yemekler": ["yemek - miktar"], "kalori": 400, "protein": 25, "karb": 40, "yag": 15}},
      "ogle": {{"yemekler": ["yemek - miktar"], "kalori": 450, "protein": 40, "karb": 35, "yag": 10}},
      "aksam": {{"yemekler": ["yemek - miktar"], "kalori": 500, "protein": 42, "karb": 38, "yag": 18}},
      "ara_ogun": {{"yemekler": ["yemek - miktar"], "kalori": 200, "protein": 15, "karb": 20, "yag": 6}},
      "toplam_kalori": 1550,
      "toplam_protein": 122,
      "toplam_karb": 133,
      "toplam_yag": 49
    }},
    {{
      "isim": "Plan C",
      "kahvalti": {{"yemekler": ["yemek - miktar"], "kalori": 380, "protein": 22, "karb": 42, "yag": 12}},
      "ogle": {{"yemekler": ["yemek - miktar"], "kalori": 430, "protein": 38, "karb": 40, "yag": 12}},
      "aksam": {{"yemekler": ["yemek - miktar"], "kalori": 520, "protein": 44, "karb": 35, "yag": 22}},
      "ara_ogun": {{"yemekler": ["yemek - miktar"], "kalori": 210, "protein": 18, "karb": 22, "yag": 5}},
      "toplam_kalori": 1540,
      "toplam_protein": 122,
      "toplam_karb": 139,
      "toplam_yag": 51
    }}
  ]
}}"""

    try:
        raw = _bedrock_chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="Sen bir beslenme uzmanısın. SADECE geçerli JSON döndür, başka hiçbir şey yazma.",
            max_tokens=2000,
            temperature=0.3,
        ).strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            raw = raw[start:end]
        plans = json.loads(raw)

        return jsonify({
            "planlar"      : plans["planlar"],
            "overall_score": overall_score,
            "score_label"  : score_label,
            "score_color"  : score_color,
            "target_calories": round(target_calories)
        })

    except json.JSONDecodeError:
        return jsonify({"error": "Plan oluşturulamadı, tekrar dene."}), 500
    except Exception as e:
        return jsonify({"error": f"Hata: {str(e)}"}), 500

@app.route("/training")
@login_required
def training():
    return render_template("training.html", username=current_user.username, profile_picture=current_user.profile_picture)

@app.route("/training-plan", methods=["POST"])
@login_required
def training_plan_generate():
    data = request.get_json()

    last = UserSession.query.filter_by(user_id=current_user.id)\
        .order_by(UserSession.created_at.desc())\
        .first()

    if not last:
        return jsonify({"error": "Önce ana sayfadan planını oluştur."}), 400

    goal             = last.goal
    fitness_level    = last.fitness_level
    current_activity = last.current_activity
    tdee             = last.tdee

    gun_sayisi       = data.get("gun_sayisi", 3)
    ekipman          = data.get("ekipman", "spor_salonu")
    odak             = data.get("odak", "tum_vucut")
    sure             = data.get("sure", 45)
    kardiyo_tipi     = data.get("kardiyo_tipi", "yok")
    kardiyo_gun      = data.get("kardiyo_gun", 0)
    kardiyo_sure     = data.get("kardiyo_sure", 20)
    kardiyo_yogunluk = data.get("kardiyo_yogunluk", "orta")
    antrenman_tarzi  = data.get("antrenman_tarzi", "genel")
    odak_hedef       = data.get("odak_hedef", "genel")

    ekipman_labels = {
        "ev"          : "ev ortamı (vücut ağırlığı egzersizleri)",
        "spor_salonu" : "spor salonu (tam ekipman, barbell, dambıl, makineler)",
        "minimal"     : "minimal ekipman (dambıl ve direnç bandı)"
    }

    odak_labels = {
        "tum_vucut" : "tüm vücut",
        "ust_vucut" : "üst vücut (göğüs, sırt, omuz, kol)",
        "alt_vucut" : "alt vücut (bacak, kalça, baldır)",
        "core"      : "karın ve core bölgesi",
        "sirt"      : "sırt odaklı (latissimus, trapez, rhomboid, arka zincir — sırta ekstra hacim ver)"
    }

    kardiyo_labels = {
        "kosu"     : "koşu",
        "bisiklet" : "bisiklet",
        "yuzme"    : "yüzme",
        "ip_atlama": "ip atlama",
        "yuruyus"  : "tempolu yürüyüş",
        "karisik"  : "karışık (koşu, bisiklet, ip atlama kombinasyonu)",
        "yok"      : "kardiyo yok"
    }

    yogunluk_labels_kardiyo = {
        "dusuk"  : "düşük yoğunluk (LISS — konuşabilecek tempoda)",
        "orta"   : "orta yoğunluk (konuşması biraz zor)",
        "yuksek" : "yüksek yoğunluk (HIIT — interval)",
        "karisik": "karışık (bazı günler LISS, bazı günler HIIT)"
    }
    tarzi_labels = {
        "genel"        : "genel fitness (karışık antrenman)",
        "crossfit"     : "CrossFit tarzı (fonksiyonel, yüksek yoğunluk, WOD yapısı)",
        "calisthenics" : "kalistenik (vücut ağırlığı, beceri odaklı — muscle up, handstand)",
        "powerlifting" : "powerlifting (squat, bench, deadlift odaklı, düşük tekrar yüksek ağırlık)",
        "bodybuilding" : "vücut geliştirme (hipertrofi odaklı, split program, kas izolasyonu)",
        "fonksiyonel"  : "fonksiyonel antrenman (günlük hareket kalıpları, mobilite)"
    }

    hedef_labels = {
        "genel"      : "genel fitness ve sağlık",
        "guc"        : "maksimum güç artışı",
        "kondisyon"  : "kardiyovasküler kondisyon ve dayanıklılık",
        "kas_kutlesi": "kas kütlesi ve hipertrofi",
        "yag_yakimi" : "yağ yakımı ve vücut rekomposizyonu",
        "esneklik"   : "esneklik ve mobilite"
    }

    # Kardiyo metni
    if kardiyo_tipi != "yok" and kardiyo_gun > 0:
        kardiyo_text = (
            f"Kardiyo tercihleri:\n"
            f"- Kardiyo türü: {kardiyo_labels.get(kardiyo_tipi, kardiyo_tipi)}\n"
            f"- Haftada {kardiyo_gun} gün kardiyo\n"
            f"- Her kardiyo seansı: {kardiyo_sure} dakika\n"
            f"- Kardiyo yoğunluğu: {yogunluk_labels_kardiyo.get(kardiyo_yogunluk, kardiyo_yogunluk)}\n"
            f"\n"
            f"Kardiyo günlerini ağırlık antrenmanı günleriyle akıllıca birleştir veya ayrı günlere koy.\n"
            f"HIIT'i ağırlık günüyle aynı güne koyma — LISS ise aynı gün olabilir.\n"
            f"Her kardiyo seansı için tahmini kalori yakımını belirt.\n"
            f"Kardiyo günlerinin tipini 'kardiyo' olarak işaretle."
        )
    else:
        kardiyo_text = "Kardiyo istemiyor — sadece ağırlık antrenmanı planla."

    # Odak bölgeye ve gün sayısına göre split yapısı öner
    odak_raw = odak  # "sirt", "tum_vucut" vb.

    split_rehber_map = {
        ("sirt", 3): "Push-Pull-Legs: Gün1=SIRT+Biceps (en az 6 egzersiz), Gün2=Göğüs+Triceps+Omuz, Gün3=Bacak+Core",
        ("sirt", 4): "Gün1=SIRT+Biceps (en az 6 egzersiz), Gün2=Göğüs+Triceps, Gün3=Bacak, Gün4=Omuz+Core+Ek Sırt (Face Pull, Shrug)",
        ("sirt", 5): "Gün1=SIRT (6-7 egzersiz, sadece sırt), Gün2=Göğüs+Triceps, Gün3=Bacak, Gün4=Omuz+Ek Sırt, Gün5=Biceps+Triceps+Core",
        ("sirt", 6): "Gün1=SIRT+Biceps, Gün2=Göğüs+Triceps, Gün3=Bacak, Gün4=SIRT+Omuz (ikinci sırt günü), Gün5=Göğüs+Triceps, Gün6=Bacak+Core",
        ("tum_vucut", 3): "Gün1=Full Body A (squat+bench+row), Gün2=Full Body B (deadlift+press+pulldown), Gün3=Full Body C (lunge+dips+curl)",
        ("tum_vucut", 4): "Upper A (Göğüs+Sırt) / Lower A (Bacak ön) / Upper B (Omuz+Kol) / Lower B (Bacak arka+Core)",
        ("tum_vucut", 5): "Push / Pull (Sırt+Biceps) / Bacak / Upper (Göğüs+Omuz) / Core+Ek",
        ("tum_vucut", 6): "PPL tekrar: Push/Pull/Legs/Push/Pull/Legs",
        ("ust_vucut", 3): "Gün1=Göğüs+Triceps, Gün2=SIRT+Biceps (en az 5 egzersiz), Gün3=Omuz+Core",
        ("ust_vucut", 4): "Gün1=Göğüs+Triceps, Gün2=SIRT+Biceps, Gün3=Omuz+Core, Gün4=Göğüs+Sırt (compound odak)",
        ("ust_vucut", 5): "Gün1=Göğüs, Gün2=SIRT (tek başına 6 egzersiz), Gün3=Omuz, Gün4=Biceps+Triceps, Gün5=Full Upper",
        ("ust_vucut", 6): "Gün1=Göğüs+Triceps, Gün2=SIRT+Biceps, Gün3=Omuz, Gün4=Göğüs+Sırt, Gün5=Kol, Gün6=Full Upper",
        ("alt_vucut", 3): "Gün1=Bacak ön (Quadriceps), Gün2=Bacak arka (Hamstring+Glute), Gün3=Bacak+Baldır+Core",
        ("alt_vucut", 4): "Gün1=Squat odaklı, Gün2=Hip Hinge (deadlift), Gün3=Unilateral (lunge, split squat), Gün4=Glute+Core",
        ("alt_vucut", 5): "Gün1=Quad, Gün2=Posterior chain, Gün3=Güç (squat+deadlift), Gün4=Tek bacak, Gün5=Hipertrofi+Baldır",
        ("core", 3): "Gün1=Core compound (plank, rollout, hanging raise), Gün2=Core rotasyon+oblik, Gün3=Core stabilite+esneklik",
        ("core", 4): "Her güne core bloku ekle, 4. gün full core+mobilite",
        ("core", 5): "Her güne core ekle + 2 hafif kardiyo günü",
    }

    split_rehber = split_rehber_map.get(
        (odak_raw, gun_sayisi),
        f"Haftada {gun_sayisi} günlük dengeli split: her büyük kas grubu (sırt, göğüs, bacak, omuz) en az bir kez çalışılsın. Sırt mutlaka ayrı bir günde yer alsın."
    )

    # Kas grubu bazında geniş egzersiz referans listesi
    egzersiz_referans = """
SPOR SALONU EGZERSİZ REHBERİ (kas grubu başına çeşitli seçenekler):

SIRT (her programda bu egzersizlerden EN AZ 4-5 farklısını kullan):
  Yatay çekiş (compound): Barbell Bent Over Row, Dumbbell Row (tek kol), T-Bar Row, Cable Seated Row, Yatay Makine Çekiş
  Dikey çekiş (compound): Lat Pulldown (geniş tutuş), Lat Pulldown (dar tutuş), Pull-Up (barfiks), Chin-Up, Assisted Pull-Up
  İzolasyon/detail: Face Pull, Straight-Arm Pulldown, Cable Pullover, Rack Pull, Shrug (trapez), Rear Delt Fly
  Deadlift ailesi: Conventional Deadlift, Romanian Deadlift, Sumo Deadlift
  Sırt kalınlık: Meadows Row, Pendlay Row, Chest-Supported Row

GÖĞÜS: Bench Press (düz/eğimli/negatif), Dumbbell Fly, Cable Crossover, Pec Deck, Dips (göğüs), Push-Up, Incline Dumbbell Press

OMUZ: Overhead Press (barbell/dumbbell), Arnold Press, Lateral Raise, Front Raise, Rear Delt Fly, Face Pull, Upright Row, Shrug

BİCEPS: Barbell Curl, Dumbbell Curl, Hammer Curl, Incline Dumbbell Curl, Concentration Curl, Cable Curl, Preacher Curl

TRİCEPS: Triceps Pushdown (ip/düz), Skull Crusher, Close-Grip Bench Press, Overhead Triceps Extension, Dips (triceps), Kickback

BACAK ÖN (quadriceps): Squat, Leg Press, Hack Squat, Leg Extension, Bulgarian Split Squat, Lunge, Step-Up

BACAK ARKA (hamstring/glute): Romanian Deadlift, Leg Curl (yatay/oturarak), Hip Thrust, Sumo Squat, Good Morning, Cable Kickback

BALDUR: Standing Calf Raise, Seated Calf Raise, Donkey Calf Raise, Leg Press Calf Raise

KARIN (core): Crunch, Cable Crunch, Leg Raise, Plank, Russian Twist, Ab Rollout, Hanging Knee Raise, Oblique Crunch

EV / MİNİMAL EKİPMAN (barfiks, dambıl, direnç bandı):
  Sırt: Pull-Up, Chin-Up, Band Bent Over Row, Dumbbell Row, Band Pulldown, Superman Hold
  Göğüs: Push-Up (geniş/dar/eğimli), Dumbbell Press, Dumbbell Fly
  Bacak: Squat, Lunge, Glute Bridge, Romanian Deadlift (dambıl), Step-Up
"""

    prompt = (
        f"Sen 10+ yıllık deneyimli bir kişisel antrenörsün. Türkçe yaz, İngilizce egzersiz isimlerini kullanabilirsin.\n"
        f"\n"
        f"Kullanıcı bilgileri:\n"
        f"- Hedef: {goal}\n"
        f"- Fitness seviyesi: {fitness_level}\n"
        f"- Günlük aktivite: {current_activity}\n"
        f"- TDEE: {round(tdee)} kcal\n"
        f"\n"
        f"Antrenman tercihleri:\n"
        f"- Haftada {gun_sayisi} gün antrenman yapabilir\n"
        f"- Ekipman: {ekipman_labels.get(ekipman, ekipman)}\n"
        f"- Odak bölge: {odak_labels.get(odak, odak)}\n"
        f"- Antrenman süresi: {sure} dakika\n"
        f"- Antrenman tarzı: {tarzi_labels.get(antrenman_tarzi, antrenman_tarzi)}\n"
        f"- Öncelikli hedef: {hedef_labels.get(odak_hedef, odak_hedef)}\n"
        f"\n"
        f"{kardiyo_text}\n"
        f"\n"
        f"{egzersiz_referans}\n"
        f"\n"
        f"ÖNERİLEN SPLIT YAPISI ({gun_sayisi} gün için):\n"
        f"{split_rehber}\n"
        f"\n"
        f"PROGRAM KURALLARI:\n"
        f"1. Haftanın tam 7 günü için plan yap (Pazartesi'den Pazar'a).\n"
        f"2. Yukarıdaki split yapısına uy — sırt günü MUTLAKA programda yer alsın.\n"
        f"3. Sırt günü için EGZERSİZ REHBERİ'ndeki listeden EN AZ 5 FARKLI egzersiz seç:\n"
        f"   - En az 1 yatay çekiş (Row ailesi)\n"
        f"   - En az 1 dikey çekiş (Pulldown/Pull-up ailesi)\n"
        f"   - En az 1 izolasyon (Face Pull, Pullover, Shrug vb.)\n"
        f"   - Sırt günü 'odak' alanına 'Sırt ve Biceps' veya 'Sırt (Lat + Kalın Sırt)' yaz.\n"
        f"4. Aynı kas grubunu ard arda iki güne koyma.\n"
        f"5. Her egzersiz için gerçekçi set, tekrar ve dinlenme süresi yaz.\n"
        f"6. Her günün tahmini kalori yakımını gerçekçi hesapla (200-600 kcal arası).\n"
        f"7. 'not' alanına egzersiz için kısa teknik ipucu yaz.\n"
        f"8. Toplam antrenman günü tam olarak {gun_sayisi} olsun.\n"
        f"\n"
        f"SADECE geçerli JSON döndür, başka hiçbir şey yazma. Format:\n"
        f'{{"program": ['
        f'{{"gun": "Pazartesi", "tip": "antrenman", "odak": "Sırt ve Biceps", "sure_dk": {sure}, "tahmini_kalori": 380, '
        f'"egzersizler": ['
        f'{{"isim": "Bent Over Row", "set": 4, "tekrar": "8-10", "dinlenme": "90 sn", "not": "sırt düz tut, kürek kemiklerini sıkıştır"}}, '
        f'{{"isim": "Lat Pulldown", "set": 4, "tekrar": "10-12", "dinlenme": "75 sn", "not": "tam uzanım, tam çekiş"}}, '
        f'{{"isim": "Cable Seated Row", "set": 3, "tekrar": "10-12", "dinlenme": "75 sn", "not": "dirsekleri gövdeye yak"}}, '
        f'{{"isim": "Face Pull", "set": 3, "tekrar": "15-20", "dinlenme": "60 sn", "not": "omuz sağlığı için kritik"}}, '
        f'{{"isim": "Barbell Curl", "set": 3, "tekrar": "10-12", "dinlenme": "60 sn", "not": "sallanma yapma"}}]'
        f'}}, '
        f'{{"gun": "Salı", "tip": "dinlenme", "odak": "Aktif Toparlanma", "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []}}, '
        f'{{"gun": "Çarşamba", "tip": "antrenman", "odak": "Göğüs ve Triceps", "sure_dk": {sure}, "tahmini_kalori": 350, '
        f'"egzersizler": [{{"isim": "Bench Press", "set": 4, "tekrar": "8-10", "dinlenme": "90 sn", "not": "kürek kemiklerini bankaya bas"}}]}}], '
        f'"haftalik_ozet": {{"toplam_antrenman_gun": {gun_sayisi}, "toplam_tahmini_kalori": 1800, '
        f'"yogunluk_skoru": 8, "denge_skoru": 8, "uygunluk_skoru": 9}}}}'
    )

    try:
        raw = _bedrock_chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="Sen deneyimli bir kişisel antrenörsün. SADECE geçerli JSON döndür, başka hiçbir şey yazma. Markdown, açıklama veya yorum ekleme.",
            max_tokens=4000,
            temperature=0.4,
        ).strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            raw = raw[start:end]
        plan = json.loads(raw)

        ozet     = plan.get("haftalik_ozet", {})
        yogunluk = ozet.get("yogunluk_skoru", 0)
        denge    = ozet.get("denge_skoru", 0)
        uygunluk = ozet.get("uygunluk_skoru", 0)

        # AI 0 döndürürse default 7 kullan
        if yogunluk == 0:
            yogunluk = 7
        if denge == 0:
            denge = 7
        if uygunluk == 0:
            uygunluk = 7

        overall  = round((yogunluk + denge + uygunluk) / 3, 1)

        if overall >= 8:
            score_label = "İyi"
        elif overall >= 6:
            score_label = "Orta"
        else:
            score_label = "Kötü"

        return jsonify({
            "program"      : plan["program"],
            "haftalik_ozet": ozet,
            "overall_score": overall,
            "score_label"  : score_label
        })

    except json.JSONDecodeError:
        return jsonify({"error": "Plan oluşturulamadı, tekrar dene."}), 500
    except Exception as e:
        return jsonify({"error": f"Hata: {str(e)}"}), 500
    
@app.route("/training-plan/save", methods=["POST"])
@login_required
def save_training_plan():
    data  = request.get_json()
    plan  = data.get("plan")
    score = data.get("score")

    if not plan:
        return jsonify({"error": "Plan verisi eksik"}), 400

    TrainingPlan.query.filter_by(user_id=current_user.id).delete()

    new_plan = TrainingPlan(
        user_id   = current_user.id,
        plan_data = json.dumps(plan, ensure_ascii=False), 
        score     = score
    )
    db.session.add(new_plan)
    db.session.commit()

    return jsonify({"message": "Antrenman planı kaydedildi."})

@app.route("/workout/complete", methods=["POST"])
@login_required
def complete_workout():
    plan = TrainingPlan.query.filter_by(user_id=current_user.id)\
        .order_by(TrainingPlan.created_at.desc()).first()
    if not plan:
        return jsonify({"error": "Aktif antrenman planın yok."}), 400

    today_key = date.today().isoformat()
    quest = DailyQuest.query.filter_by(quest_type="workout_logged", is_active=True).first()
    if quest:
        existing = UserQuestProgress.query.filter_by(
            user_id=current_user.id, quest_id=quest.id, date_key=today_key
        ).first()
        if existing:
            return jsonify({"error": "Bugünkü antrenmanını zaten tamamladın!"}), 400

    quest_result = complete_quest_for_user(current_user.id, "workout_logged")
    new_total = award_xp(current_user.id, 10)
    log_activity(current_user.id, "workout_completed", "Bugünkü antrenmanını tamamladı")
    db.session.commit()

    total_xp = 10 + (quest_result["xp"] if quest_result else 0)
    level = get_level(new_total)
    response = {
        "message": f"Bugünkü antrenmanı tamamladın! +{total_xp} XP!",
        "points_awarded": total_xp,
        "new_total": new_total,
        "level": level,
        "title": get_title(level)
    }
    if quest_result:
        response["quest_awarded"] = quest_result
    return jsonify(response)

@app.route("/training-plan/active")
@login_required
def get_active_training_plan():
    plan = TrainingPlan.query.filter_by(user_id=current_user.id)\
        .order_by(TrainingPlan.created_at.desc())\
        .first()

    if not plan:
        return jsonify({"exists": False})

    return jsonify({
        "exists"    : True,
        "plan"      : json.loads(plan.plan_data),
        "score"     : plan.score,
        "created_at": plan.created_at.strftime("%d.%m.%Y")
    })

@app.route("/progress-page")
@login_required  
def progress_page():
    return render_template("progress.html", username=current_user.username, profile_picture=current_user.profile_picture)
    
@app.route("/history")
@login_required
def history():
    sessions = UserSession.query.filter_by(user_id = current_user.id)\
    .order_by(UserSession.created_at.desc())\
    .limit(5).all()

    result = []
    for s in sessions:
        result.append({
            "tarih" : s.created_at.strftime("%d.%m.%Y %H:%M"),
            "kilo" : s.weight,
            "hedef_kalori" : s.target_calories,
            "coach_reply" : s.coach_reply
        })
    return Response(
        json.dumps(result, ensure_ascii=False),
        mimetype="application/json"
    )
@app.route("/last-session")
@login_required
def last_session():
    s = UserSession.query.filter_by(user_id=current_user.id)\
        .order_by(UserSession.created_at.desc())\
        .first()

    if not s:
        return jsonify({"exists": False})

    return jsonify({
        "exists"          : True,
        "weight"          : current_user.weight or s.weight,
        "height"          : s.height,
        "age"             : s.age,
        "gender"          : s.gender,
        "goal"            : s.goal,
        "fitness_level"   : s.fitness_level,
        "current_activity": s.current_activity,
        "bmr"             : s.bmr,
        "tdee"            : s.tdee,
        "target_calories" : s.target_calories,
        "target_weight"   : current_user.target_weight,
        "goal_type"       : current_user.goal_type,
        "tarih"           : s.created_at.strftime("%d.%m.%Y")
    })

@app.route("/dashboard-nudges")
@login_required
def dashboard_nudges():
    from analytics_engine import get_nudges
    nudge_translations = {
        "NUDGE_MISSING_LOGS": "Son 48 saatte antrenman veya beslenme kaydın yok. Bugün hedeflerine bir adım daha yaklaş!",
        "NUDGE_NO_WORKOUT": "Son 48 saatte antrenman kaydı görünmüyor. Kısa bir antrenman bile fark yaratır.",
        "NUDGE_NO_NUTRITION": "Beslenme kaydını güncellemeyi unutma — veriler koçunun sana daha iyi yardım etmesini sağlar.",
        "NUDGE_STREAK_RISK": "Serin risk altında! Bugün giriş yaparak kesintisiz serinizi koruyun.",
        "NUDGE_PROTEIN_GOAL": "Haftalık protein hedefinin %90'ına ulaştın — harika gidiyorsun!",
        "NUDGE_WEEKLY_REPORT": "Bugün haftalık rapor günü. Koçundan performans özetini iste!",
    }
    try:
        models = {
            "WorkoutLog": WorkoutLog,
            "UserDailyNutrition": UserDailyNutrition,
            "UserSession": UserSession,
        }
        raw_nudges = get_nudges(User.query.get(current_user.id), db, models)
        cleaned = []
        for n in (raw_nudges or []):
            key = n.split(":")[0].strip() if ":" in n else ""
            cleaned.append(nudge_translations.get(key, n))
        return jsonify({"nudges": cleaned})
    except Exception:
        return jsonify({"nudges": []})

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")
    data = request.get_json()
    username = data.get("username")
    email = data.get("email")
    password = data.get("password")

    if not username or not email or not password:
        return jsonify({"error" : "Tüm alanlar zorunludur"}), 400
    
    if len(password) < 6:
        return jsonify({"error" : "Şifre en az 6 karakter olmalıdır."}) , 400
    
    if len(username) < 3:
        return jsonify({"error" : "Kullanıcı adı en az 3 karakter olmalıdır."}) , 400
    
    # Kullanıcı check
    if User.query.filter_by(username=username).first():
        return jsonify({"error" : "Bu kullanıcı adı alınmış."}), 400
    
    if User.query.filter_by(email = email).first():
        return jsonify({"error" : "Bu email zaten kayıtlı."}) , 400
    
    user = User(username = username , email = email)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    return jsonify({"message" : f"Hoş geldin {username}, hesabın oluşturuldu!"})

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")

    user = User.query.filter_by(username = username).first()

    if not user or not user.check_password(password):
        return jsonify({"error": "Kullanıcı adı veya şifre hatalı"}) , 401
    
    login_user(user)
    quest_result = complete_quest_for_user(user.id, "login")
    response = {"message": f"Hoş geldin {user.username}!"}
    if quest_result:
        response["quest_awarded"] = quest_result
    return jsonify(response)

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/quests")
@login_required
def quests():
    all_quests = DailyQuest.query.filter_by(is_active=True).all()
    today_progress = get_today_progress(current_user.id)
    progress_map = {p.quest_id: p for p in today_progress}

    quest_data = []
    for q in all_quests:
        prog = progress_map.get(q.id)
        status = "claimed" if prog else "pending"
        quest_data.append({"quest": q, "status": status})

    return render_template("quests.html",
        username=current_user.username,
        profile_picture=current_user.profile_picture,
        quest_data=quest_data
    )


@app.route("/quests/claim/<int:quest_id>", methods=["POST"])
@login_required
def claim_quest(quest_id):
    xp = current_user.rank_points or 0
    level = get_level(xp)
    return jsonify({
        "message": "Ödül zaten alındı ✓",
        "new_total": xp,
        "level": level,
        "title": get_title(level)
    })


@app.cli.command("seed-quests")
def seed_quests():
    """Insert default daily quests into the database."""
    defaults = [
        {"title": "Daily Login", "description": "Bugün uygulamaya giriş yap", "points_reward": 10, "quest_type": "login"},
        {"title": "Log a Workout", "description": "Bir antrenman planı oluştur veya kaydet", "points_reward": 50, "quest_type": "workout_logged"},
        {"title": "Help a Friend", "description": "Bir arkadaşına mesaj gönder", "points_reward": 30, "quest_type": "suggestion_sent"},
        {"title": "Log a Meal", "description": "Bugün bir öğün kaydet", "points_reward": 20, "quest_type": "meal_logged"},
    ]
    for q in defaults:
        existing = DailyQuest.query.filter_by(quest_type=q["quest_type"]).first()
        if not existing:
            db.session.add(DailyQuest(**q))
    db.session.commit()
    click.echo("Default quests seeded successfully.")


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404

@app.errorhandler(500)
def server_error(e):
    return "Internal Server Error", 500

with app.app_context():
    db.create_all()
    migrations = [
        'ALTER TABLE "user" ADD COLUMN last_login DATE',
        'ALTER TABLE supplement RENAME COLUMN rating_effectiveness TO rating_effect',
        'ALTER TABLE supplement ADD COLUMN rating_digestion INTEGER',
        'ALTER TABLE message ADD COLUMN message_type VARCHAR(20) DEFAULT \'text\'',
        'ALTER TABLE "user" ADD COLUMN target_weight FLOAT',
        'ALTER TABLE "user" ADD COLUMN goal_type VARCHAR(10)',
        'ALTER TABLE "user" ALTER COLUMN profile_picture TYPE TEXT',
        'ALTER TABLE message ALTER COLUMN message_type TYPE VARCHAR(50)',
        'ALTER TABLE meal_log ALTER COLUMN ogun TYPE VARCHAR(100)',
        'ALTER TABLE meal_log ADD COLUMN source VARCHAR(20) DEFAULT \'manual\'',
        'UPDATE user_quest_progress SET is_claimed = true WHERE is_claimed = false',
        'ALTER TABLE custom_meal_item ADD COLUMN serving_id VARCHAR(50)',
        'ALTER TABLE custom_meal_item ADD COLUMN serving_description VARCHAR(200)',
        'ALTER TABLE custom_meal_item ADD COLUMN serving_quantity FLOAT',
    ]
    for sql in migrations:
        try:
            db.session.execute(db.text(sql))
            db.session.commit()
        except Exception:
            db.session.rollback()

    # PL/pgSQL trigger for PostgreSQL activity calorie auto-calculation
    try:
        db.session.execute(db.text("""
            CREATE OR REPLACE FUNCTION calc_activity_calories()
            RETURNS TRIGGER AS $$
            DECLARE
                w FLOAT; h FLOAT; stride FLOAT; dist FLOAT; dur FLOAT;
                met_val FLOAT; spd FLOAT;
            BEGIN
                SELECT weight, height INTO w, h FROM "user" WHERE id = NEW.user_id;
                w := COALESCE(w, 70); h := COALESCE(h, 170);
                met_val := CASE NEW.intensity
                    WHEN 'light' THEN 2.0 WHEN 'moderate' THEN 3.5
                    WHEN 'brisk' THEN 4.3 WHEN 'fast' THEN 5.0 ELSE 3.5 END;
                spd := CASE NEW.intensity
                    WHEN 'light' THEN 3.0 WHEN 'moderate' THEN 4.5
                    WHEN 'brisk' THEN 5.5 WHEN 'fast' THEN 6.5 ELSE 4.5 END;
                stride := h * 0.414;
                dist := NEW.steps * stride / 100000.0;
                dur := dist / spd;
                NEW.calories_burned := ROUND((met_val * w * dur)::numeric, 1);
                NEW.distance_km := ROUND(dist::numeric, 2);
                NEW.duration_min := ROUND((dur * 60)::numeric, 1);
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """))
        db.session.execute(db.text("""
            DROP TRIGGER IF EXISTS trg_calc_activity ON daily_activity;
            CREATE TRIGGER trg_calc_activity
            BEFORE INSERT OR UPDATE ON daily_activity
            FOR EACH ROW EXECUTE FUNCTION calc_activity_calories();
        """))
        db.session.commit()
    except Exception:
        db.session.rollback()
    if DailyQuest.query.count() == 0:
        for q in [
            DailyQuest(title="Daily Login", description="Bugün uygulamaya giriş yap", points_reward=10, quest_type="login"),
            DailyQuest(title="Log a Workout", description="Bir antrenman planı oluştur veya kaydet", points_reward=50, quest_type="workout_logged"),
            DailyQuest(title="Help a Friend", description="Bir arkadaşına mesaj gönder", points_reward=30, quest_type="suggestion_sent"),
        ]:
            db.session.add(q)
        db.session.commit()
    if not DailyQuest.query.filter_by(quest_type="supplement_added").first():
        db.session.add(DailyQuest(
            title="Update Your Stack",
            description="Supplement stack'ine yeni bir ürün ekle",
            points_reward=25, quest_type="supplement_added"
        ))
        db.session.commit()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)