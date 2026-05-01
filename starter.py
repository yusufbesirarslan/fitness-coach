from flask import Flask, request, jsonify, render_template , redirect , url_for , session , Response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager , UserMixin , login_user , logout_user , login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from groq import Groq
import os
import json
from dotenv import load_dotenv
load_dotenv()
app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///chatbot.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-123")
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login" # Giriş zaten yapılıysa yönlendirme
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200),nullable=False)
    created_at = db.Column(db.DateTime , default=datetime.utcnow)

    def set_password(self,password):
        self.password_hash = generate_password_hash(password)

    def check_password(self , password):
        return check_password_hash(self.password_hash, password)
    
    def __repr__(self):
        return f"<User {self.username}>"
    
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


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

@app.route("/")
@login_required
def home():
    return render_template("index.html", username=current_user.username)

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

@app.route("/ask", methods=["POST"])
@login_required
def ask_coach():
    data     = request.get_json()
    question = data.get("question", "")

    if not question.strip():
        return jsonify({"error": "Bir soru yaz."}), 400

    # Kullanıcı bağlamını topla
    last_session = UserSession.query.filter_by(user_id=current_user.id)\
        .order_by(UserSession.created_at.desc()).first()

    last_checkin = WeeklyCheckIn.query.filter_by(user_id=current_user.id)\
        .order_by(WeeklyCheckIn.created_at.desc()).first()

    context = "Kullanıcı hakkında bilgi yok."
    if last_session:
        context = (
            f"Kullanıcı: {current_user.username}, "
            f"Kilo: {last_session.weight}kg, Boy: {last_session.height}cm, "
            f"Yaş: {last_session.age}, Hedef: {last_session.goal}, "
            f"Seviye: {last_session.fitness_level}, "
            f"Kalori hedefi: {round(last_session.target_calories)} kcal"
        )

    checkin_context = ""
    if last_checkin:
        checkin_context = (
            f"\nSon check-in: Kilo {last_checkin.weight}kg, "
            f"Yorgunluk {last_checkin.fatigue}/5, "
            f"Uyku {last_checkin.uyku_kalitesi}/5, "
            f"Beslenme uyumu {last_checkin.beslenme_uyumu}/5"
        )

    prompt = f"""Sen bir kişisel fitness ve beslenme koçusun. Türkçe yaz.
Kullanıcıya "sen" diye hitap et.

{context}{checkin_context}

Kullanıcının sorusu: {question}

Kısa, net ve spesifik cevap ver. Kullanıcının verileriyle bağlantılı tavsiyeler sun.
Emin olmadığın tıbbi konularda doktora danışmasını öner."""

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "Sen bir fitness koçusun. Türkçe, samimi, kısa ve net konuş."},
                {"role": "user",   "content": prompt}
            ],
            max_tokens=500,
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
    return render_template("nutrition.html", username=current_user.username)
    
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

SADECE bu gıdaları kullanarak 3 FARKLI günlük beslenme planı oluştur.
Her plan {round(target_calories)} kcal civarında olsun (±100 kcal tolerans).
Her plan kahvaltı, öğle, akşam ve ara öğün içersin.
Her öğünde miktar belirt (gram veya adet olarak).

Yanıtını SADECE şu JSON formatında ver, başka hiçbir şey yazma:
{{
  "planlar": [
    {{
      "isim": "Plan A",
      "kahvalti": {{"yemekler": ["yemek - miktar"], "kalori": 0, "protein": 0, "karb": 0, "yag": 0}},
      "ogle": {{"yemekler": ["yemek - miktar"], "kalori": 0, "protein": 0, "karb": 0, "yag": 0}},
      "aksam": {{"yemekler": ["yemek - miktar"], "kalori": 0, "protein": 0, "karb": 0, "yag": 0}},
      "ara_ogun": {{"yemekler": ["yemek - miktar"], "kalori": 0, "protein": 0, "karb": 0, "yag": 0}},
      "toplam_kalori": 0,
      "toplam_protein": 0,
      "toplam_karb": 0,
      "toplam_yag": 0
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

        # JSON parse
        raw = raw.replace("```json", "").replace("```", "").strip()
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
    return render_template("training.html", username=current_user.username)

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

    ekipman_labels = {
        "ev"          : "ev ortamı (vücut ağırlığı egzersizleri)",
        "spor_salonu" : "spor salonu (tam ekipman, barbell, dambıl, makineler)",
        "minimal"     : "minimal ekipman (dambıl ve direnç bandı)"
    }

    odak_labels = {
        "tum_vucut" : "tüm vücut",
        "ust_vucut" : "üst vücut",
        "alt_vucut" : "alt vücut",
        "core"      : "karın ve core bölgesi"
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

    prompt = (
        f"Sen deneyimli bir kişisel antrenörsün. Türkçe yaz, İngilizce kelime kullanma.\n"
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
        f"\n"
        f"{kardiyo_text}\n"
        f"\n"
        f"Haftanın 7 günü için antrenman programı oluştur.\n"
        f"Antrenman günleri, kardiyo günleri ve dinlenme günlerini dengeli dağıt.\n"
        f"Her egzersiz için set sayısı, tekrar sayısı ve dinlenme süresi belirt.\n"
        f"Egzersizler seçilen ekipmana uygun olsun.\n"
        f"Tahmini kalori yakımını her gün için belirt.\n"
        f"\n"
        f"SADECE şu JSON formatında yanıt ver, başka hiçbir şey yazma:\n"
        f'{{"program": ['
        f'{{"gun": "Pazartesi", "tip": "antrenman", "odak": "Göğüs ve Triceps", "sure_dk": {sure}, "tahmini_kalori": 0, '
        f'"egzersizler": [{{"isim": "Bench Press", "set": 4, "tekrar": "8-10", "dinlenme": "90 sn", "not": "örnek"}}]}}, '
        f'{{"gun": "Salı", "tip": "kardiyo", "odak": "Kardiyo", "sure_dk": {kardiyo_sure}, "tahmini_kalori": 0, '
        f'"egzersizler": [{{"isim": "Koşu", "set": 1, "tekrar": "{kardiyo_sure} dk", "dinlenme": "-", "not": "örnek"}}]}}, '
        f'{{"gun": "Çarşamba", "tip": "dinlenme", "odak": "Aktif Toparlanma", "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []}}], '
        f'"haftalik_ozet": {{"toplam_antrenman_gun": {gun_sayisi}, "toplam_tahmini_kalori": 0, '
        f'"yogunluk_skoru": 0, "denge_skoru": 0, "uygunluk_skoru": 0}}}}'
    )

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "Sen bir kişisel antrenörsün. SADECE geçerli JSON döndür, başka hiçbir şey yazma."},
                {"role": "user",   "content": prompt}
            ],
            max_tokens=3000,
            temperature=0.3
        )

        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
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
    return render_template("progress.html", username=current_user.username)
    
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
        "weight"          : s.weight,
        "height"          : s.height,
        "age"             : s.age,
        "gender"          : s.gender,
        "goal"            : s.goal,
        "fitness_level"   : s.fitness_level,
        "current_activity": s.current_activity,
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
    return jsonify({"message" : f"Hoş geldin {user.username}!"})

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404

@app.errorhandler(500)
def server_error(e):
    return render_template("500.html"), 500

with app.app_context():
    db.create_all()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)