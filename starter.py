from flask import Flask, request, jsonify, render_template , redirect , url_for , session , Response, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager , UserMixin , login_user , logout_user , login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
from groq import Groq
import os
import json
import click
from dotenv import load_dotenv
load_dotenv()
app = Flask(__name__)
database_url = os.environ.get("DATABASE_URL", "sqlite:///chatbot.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-123")
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login" # Giriş zaten yapılıysa yönlendirme
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

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

    profile_picture  = db.Column(db.String(500), nullable=True)
    full_name        = db.Column(db.String(150), nullable=True)
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
    ogun       = db.Column(db.String(20), nullable=False)  # kahvalti, ogle, aksam, ara
    yemekler   = db.Column(db.Text, nullable=False)
    kalori     = db.Column(db.Float)
    protein    = db.Column(db.Float)
    karb       = db.Column(db.Float)
    yag        = db.Column(db.Float)
    tarih      = db.Column(db.String(10))  # "01.05" formatında
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
    message_type = db.Column(db.String(20), default="text")

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
    if current_user.is_authenticated:
        xp = current_user.rank_points or 0
        level = get_level(xp)
        title = get_title(level)
        xp_in_level = xp % 500
        return {
            "rank_points": xp, "rank_title": title,
            "user_xp": xp, "user_level": level, "user_title": title,
            "xp_in_level": xp_in_level, "xp_for_next": 500,
        }
    return {
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
        return
    today_key = date.today().isoformat()
    existing = UserQuestProgress.query.filter_by(
        user_id=user_id, quest_id=quest.id, date_key=today_key
    ).first()
    if not existing:
        progress = UserQuestProgress(user_id=user_id, quest_id=quest.id, date_key=today_key)
        db.session.add(progress)
        db.session.commit()


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
    db.session.add(entry)
    db.session.commit()

    return jsonify({
        "message": f"{MEAL_LABELS[meal_key]} planından eklendi.",
        "nutrients": {
            "kalori":  entry.kalori,
            "protein": entry.protein,
            "karb":    entry.karb,
            "yag":     entry.yag
        }
    })

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
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "Sen bir fitness koçusun. Kısa, spesifik, motive edici Türkçe konuş."},
                {"role": "user",   "content": prompt}
            ],
            max_tokens=400,
            temperature=0.7
        )
        return response.choices[0].message.content
    except Exception as e:
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
        f"Kullanıcının yediği: {yemekler}\n\n"
        f"Her besini ayrı hesapla ve topla. Sonucu SADECE aşağıdaki JSON formatında döndür.\n"
        f"Değerler gerçek sayı olmalı (0 değil), ondalık olabilir:\n"
        f'{{"kalori": 520, "protein": 38, "karb": 45, "yag": 14}}'
    )

    nutrients = {"kalori": 0, "protein": 0, "karb": 0, "yag": 0}
    raw = ""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "SADECE JSON döndür. Açıklama yapma, markdown kullanma, sadece düz JSON objesi. Tüm değerler sayı olmalı."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=150,
            temperature=0.1
        )
        raw = response.choices[0].message.content.strip()
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

    return jsonify({
        "message": f"{ogun} kaydedildi.",
        "nutrients": nutrients,
        "raw_debug": raw
    })

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
            "yag": m.yag
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
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "Sen bir beslenme koçusun. Kısa, spesifik, Türkçe konuş."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=400,
            temperature=0.7
        )
        review = response.choices[0].message.content
    except Exception as e:
        review = f"Değerlendirme alınamadı: {str(e)}"

    return jsonify({"review": review, "total_calories": round(total_cal), "target": round(target)})

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
            streak_count=current_user.streak_count or 0,
            supplements=supps,
            icons=CATEGORY_ICONS,
        )

    data = request.get_json()

    new_username = (data.get("username") or "").strip()
    new_full_name = (data.get("full_name") or "").strip()
    new_profile_picture = (data.get("profile_picture") or "").strip()
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

    if len(new_profile_picture) > 500:
        return jsonify({"error": "Profil fotoğrafı URL'si çok uzun."}), 400

    valid_goals = ["kilo verme", "kas kazanma", ""]
    if new_goal not in valid_goals:
        return jsonify({"error": "Geçersiz hedef seçimi."}), 400

    current_user.username = new_username
    current_user.full_name = new_full_name if new_full_name else None
    current_user.profile_picture = new_profile_picture if new_profile_picture else None
    if new_goal:
        current_user.goal = new_goal

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
    complete_quest_for_user(current_user.id, "suggestion_sent")
    return jsonify({"message": "Gönderildi.", "id": msg.id,
                    "timestamp": msg.timestamp.strftime("%H:%M"),
                    "message_type": msg_type})

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
    complete_quest_for_user(current_user.id, "suggestion_sent")
    return jsonify({"message": "Öneri gönderildi!", "id": msg.id})

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
    else:
        msg.message_type = msg.message_type + "_declined"

    db.session.commit()
    return jsonify({"message": "Kabul edildi!" if action == "accept" else "Reddedildi.",
                    "new_type": msg.message_type})

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
    complete_quest_for_user(current_user.id, "supplement_added")

    return jsonify({"message": "Supplement eklendi!", "id": supp.id})

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

COACH_SYSTEM_PROMPT = """Sen FitX Proaktif Koçusun. Kullanıcının veritabanına HEM okuma HEM yazma erişimin var.

TEMEL GÖREV:
- Kullanıcı antrenman veya yemek bahsettiğinde HEMEN tespit et.
- "yaptım", "yedim", "çalıştım", "içtim" gibi ifadeler = loglama niyeti.
- Loglama niyeti tespit ettiğinde veriyi çıkar ve ONAY İSTE (asla direkt kaydetme).
- Onay formatı: "📋 Tespit ettim: [detaylar]. Kayıt edeyim mi?"
- Beslenme soruları için FatSecret verisini kullan, gerçek makro değerleri ver.
- Trendleri, eksik logları ve başarıları proaktif olarak belirt.
- Haftalık rapor günlerinde (Pazartesi/Pazar) otomatik rapor sun.

KURALLAR:
- Türkçe yaz, kullanıcıya "sen" diye hitap et.
- Kısa, net, samimi ve veri odaklı konuş.
- Genel geçer tavsiye VERME — her yanıt kullanıcının verisine dayansın.
- Emin olmadığın tıbbi konularda doktora yönlendir.
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
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": extraction_prompt}],
            max_tokens=150,
            temperature=0.1,
        )
        raw = resp.choices[0].message.content.strip()
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

    messages = [{"role": "system", "content": COACH_SYSTEM_PROMPT}]
    for h in history[-6:]:
        role = "user" if h.get("role") == "user" else "assistant"
        messages.append({"role": role, "content": h.get("text", "")[:500]})
    messages.append({"role": "user", "content": f"{context}\n\nKullanıcının sorusu: {question}"})

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
            max_tokens=700,
            temperature=0.7
        )
        return jsonify({"answer": response.choices[0].message.content})
    except Exception as e:
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
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Sen bir fitness koçusun. Türkçe, samimi, spesifik ve motive edici konuş. Sayılar ve süreler kullan."},
                {"role": "user",   "content": prompt}
            ],
            max_tokens=700,
            temperature=0.7
        )
        return response.choices[0].message.content
    except Exception as e:
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
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Sen bir beslenme uzmanısın. SADECE geçerli JSON döndür, başka hiçbir şey yazma."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=2000,
            temperature=0.3  # düşük — tutarlı JSON için
        )

        raw = response.choices[0].message.content.strip()
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
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Sen deneyimli bir kişisel antrenörsün. SADECE geçerli JSON döndür, başka hiçbir şey yazma. Markdown, açıklama veya yorum ekleme."},
                {"role": "user",   "content": prompt}
            ],
            max_tokens=4000,
            temperature=0.4
        )

        raw = response.choices[0].message.content.strip()
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

    complete_quest_for_user(current_user.id, "workout_logged")
    new_total = award_xp(current_user.id, 10)
    log_activity(current_user.id, "workout_completed", "Bugünkü antrenmanını tamamladı")
    db.session.commit()

    level = get_level(new_total)
    return jsonify({
        "message": "Bugünkü antrenmanı tamamladın! +10 XP!",
        "points_awarded": 10,
        "new_total": new_total,
        "level": level,
        "title": get_title(level)
    })

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
        "tarih"           : s.created_at.strftime("%d.%m.%Y")
    })

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
    complete_quest_for_user(user.id, "login")
    return jsonify({"message" : f"Hoş geldin {user.username}!"})

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
        if prog and prog.is_claimed:
            status = "claimed"
        elif prog:
            status = "completed"
        else:
            status = "pending"
        quest_data.append({"quest": q, "status": status})

    return render_template("quests.html",
        username=current_user.username,
        profile_picture=current_user.profile_picture,
        quest_data=quest_data
    )


@app.route("/quests/claim/<int:quest_id>", methods=["POST"])
@login_required
def claim_quest(quest_id):
    quest = DailyQuest.query.get(quest_id)
    if not quest:
        return jsonify({"error": "Görev bulunamadı"}), 404

    today_key = date.today().isoformat()
    progress = UserQuestProgress.query.filter_by(
        user_id=current_user.id, quest_id=quest_id, date_key=today_key
    ).first()

    if not progress:
        return jsonify({"error": "Bu görevi henüz tamamlamadın"}), 403

    if progress.is_claimed:
        return jsonify({"error": "Bu ödülü zaten aldın"}), 400

    progress.is_claimed = True
    new_total = award_xp(current_user.id, quest.points_reward)
    log_activity(current_user.id, "quest_completed", f"'{quest.title}' görevini tamamladı")
    db.session.commit()

    level = get_level(new_total)
    return jsonify({
        "message": f"+{quest.points_reward} XP!",
        "new_total": new_total,
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
    ]
    for sql in migrations:
        try:
            db.session.execute(db.text(sql))
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