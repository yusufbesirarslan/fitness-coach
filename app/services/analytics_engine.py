"""
FitX Analytics Engine — proactive nudges and insights.

Called by the AI Coach to inject smart notifications
into the conversation context. All model classes are
passed in from the caller to avoid circular imports.

Nudge'lar modele verilen DİREKTİFLERdir (kullanıcıya doğrudan gösterilmez);
yine de İngilizce kullanıcılarda modelin doğal İngilizce üretebilmesi için
nudge metinleri de dile (language='tr'|'en') göre verilir.
"""

from datetime import datetime, timedelta

from app.services.training_history import fetch_workout_entries
from app.timeutil import app_date_of, app_today


def get_nudges(user, db, models, prev_last_login=None, language="tr"):
    """
    Return a list of proactive nudge strings for the given user.

    Args:
        user: Current User object
        db: SQLAlchemy db instance
        models: dict with keys 'WorkoutLog', 'MealLog', 'UserSession'
        prev_last_login: kullanıcının bu istekten ÖNCEKI last_login değeri. Verilmezse
            user.last_login kullanılır. update_streak before_request hook'u last_login'i
            "bugün" yaptığından, seri-riski dürtüsü aksi halde asla tetiklenmezdi.
        language: 'tr' | 'en' — nudge direktiflerinin dili.
    """
    nudges = []
    now = datetime.utcnow()
    today = app_today()
    en = language == "en"

    _check_missing_logs(user, db, models, now, nudges, en)
    _check_streak_at_risk(user, today, nudges, prev_last_login, en)
    _check_protein_goal(user, db, models, today, nudges, en)
    _check_weekly_report_day(today, nudges, en)
    _check_recovery_signals(user, db, models, nudges, en)
    _check_overload_stall(user, db, models, nudges, en)
    _check_hydration(user, db, models, today, nudges, en)

    return nudges


def _check_missing_logs(user, db, models, now, nudges, en=False):
    cutoff = now - timedelta(hours=48)
    MealLog = models["MealLog"]

    workout_entries = fetch_workout_entries(
        user.id,
        app_date_of(cutoff),
        app_today(),
        include_markers=True,
    )
    recent_workout = next(
        (entry for entry in workout_entries if entry.created_at >= cutoff),
        None,
    )

    recent_nutrition = MealLog.query.filter(
        MealLog.user_id == user.id,
        MealLog.created_at >= cutoff,
    ).first()

    if not recent_workout and not recent_nutrition:
        nudges.append(
            "NUDGE_MISSING_LOGS: No workout or nutrition log for over 48 hours. "
            "Gently and supportively encourage the user to start logging again."
            if en else
            "NUDGE_MISSING_LOGS: 48 saatten fazladır antrenman veya beslenme kaydı yok. "
            "Kullanıcıyı nazikçe ve destekleyici bir şekilde loglama yapmaya teşvik et."
        )
    elif not recent_workout:
        nudges.append(
            "NUDGE_NO_WORKOUT: No workout log in the last 48 hours. "
            "Remind gently, without pressure."
            if en else
            "NUDGE_NO_WORKOUT: Son 48 saatte antrenman kaydı yok. "
            "Hafifçe hatırlat, baskı yapma."
        )
    elif not recent_nutrition:
        nudges.append(
            "NUDGE_NO_NUTRITION: No nutrition log in the last 48 hours. "
            "Suggest logging what they eat."
            if en else
            "NUDGE_NO_NUTRITION: Son 48 saatte beslenme kaydı yok. "
            "Ne yediğini loglamasını öner."
        )


def _check_streak_at_risk(user, today, nudges, prev_last_login=None, en=False):
    streak = user.streak_count or 0
    last_login = prev_last_login if prev_last_login is not None else user.last_login

    if streak >= 5 and last_login and last_login < today:
        nudges.append(
            f"NUDGE_STREAK_RISK: The user has a {streak}-day streak and hasn't been active today yet. "
            f"Give a motivating 'Don't break the streak!' style message."
            if en else
            f"NUDGE_STREAK_RISK: Kullanıcının {streak} günlük serisi var ve bugün henüz aktif değil. "
            f"'Seriyi kırma!' tarzı motive edici bir mesaj ver."
        )


def _check_protein_goal(user, db, models, today, nudges, en=False):
    UserSession = models["UserSession"]
    MealLog = models["MealLog"]

    sess = UserSession.query.filter_by(user_id=user.id)\
        .order_by(UserSession.created_at.desc()).first()
    if not sess or not sess.target_calories:
        return

    # Protein hedefi yüzdesi koç/menü ile tutarlı: kas kazanmada %30, aksi halde %25
    # (eski sabit %30, diğer hesaplarla çelişiyordu — F8).
    protein_pct = 0.30 if (sess.goal or "") == "kas kazanma" else 0.25
    weekly_protein_goal = sess.target_calories * protein_pct / 4 * 7

    week_start = today - timedelta(days=today.weekday())
    # Kanonik gün anahtarı MealLog.tarih (Istanbul ISO) ile sorgula — created_at
    # (naive UTC) kullanmak gün dönümü yakınındaki öğünleri yanlış haftaya
    # büküyordu; uygulamanın geri kalanı (_today_nutrition_totals, analyze_menu)
    # zaten tarih üzerinden gidiyor (B2/A7).
    total = db.session.query(
        db.func.coalesce(db.func.sum(MealLog.protein), 0)
    ).filter(
        MealLog.user_id == user.id,
        MealLog.tarih >= week_start.isoformat(),
        MealLog.tarih <= today.isoformat(),
    ).scalar()

    if weekly_protein_goal > 0 and total >= weekly_protein_goal * 0.9:
        nudges.append(
            f"NUDGE_PROTEIN_GOAL: Reached 90% of the weekly protein goal! "
            f"({round(total, 1)}g / {round(weekly_protein_goal, 1)}g). Congratulate!"
            if en else
            f"NUDGE_PROTEIN_GOAL: Haftalık protein hedefinin %90'ına ulaştı! "
            f"({round(total, 1)}g / {round(weekly_protein_goal, 1)}g). Tebrik et!"
        )


def _check_weekly_report_day(today, nudges, en=False):
    # Yalnızca Pazartesi (weekday()==0). Eskiden (0, 6) idi: Pazar VE Pazartesi
    # ikisinde de tetiklenip dürtü haftada iki kez çıkıyordu. Haftalık sıfırlama
    # sınırı Pazar 23:59 Istanbul olduğundan rapor günü yeni haftanın ilk günü
    # Pazartesi'dir (1.3).
    if today.weekday() == 0:
        nudges.append(
            "NUDGE_WEEKLY_REPORT: Today is weekly report day. "
            "Use the generate_weekly_report tool to present the weekly performance summary."
            if en else
            "NUDGE_WEEKLY_REPORT: Bugün haftalık rapor günü. "
            "generate_weekly_report aracını kullanarak haftalık performans özetini sun."
        )


def _latest_checkin(user, models):
    """En güncel GERÇEK WeeklyCheckIn (yoksa None). Model dict'te yoksa (eski
    çağıran) sessizce None — yeni dürtüler bu durumda devre dışı kalır.

    B2: /update-weight yalnız weight taşıyan sparse satır yazar (yogunluk=NULL);
    filtresiz sorguda bu satır gerçek check-in'i gölgeleyip toparlanma/duraksama
    sinyallerini NULL'a karşı değerlendirtiyordu. Uygulamanın geri kalanı gibi
    (tracking.py) yalnız yogunluk taşıyan satırlara bak."""
    WeeklyCheckIn = models.get("WeeklyCheckIn")
    if WeeklyCheckIn is None:
        return None
    return WeeklyCheckIn.query.filter_by(user_id=user.id)\
        .filter(WeeklyCheckIn.yogunluk.isnot(None))\
        .order_by(WeeklyCheckIn.created_at.desc()).first()


def _check_recovery_signals(user, db, models, nudges, en=False):
    """Son check-in'de uyku kalitesi düşük (<=2) VEYA yorgunluk yüksek (>=4) ise
    toparlanma/deload öner. Aşırı antrenmanı erken yakalar."""
    ci = _latest_checkin(user, models)
    if not ci:
        return
    poor_sleep = ci.uyku_kalitesi is not None and ci.uyku_kalitesi <= 2
    high_fatigue = ci.fatigue is not None and ci.fatigue >= 4
    if poor_sleep or high_fatigue:
        nudges.append(
            f"NUDGE_RECOVERY: In the last check-in sleep quality was {ci.uyku_kalitesi}/5, "
            f"fatigue {ci.fatigue}/5. Suggest lowering training volume/intensity and "
            f"prioritizing recovery and sleep."
            if en else
            f"NUDGE_RECOVERY: Son check-in'de uyku kalitesi {ci.uyku_kalitesi}/5, "
            f"yorgunluk {ci.fatigue}/5. Antrenman hacmini/şiddetini düşürmeyi, "
            f"toparlanma ve uykuyu önceliklendirmeyi öner."
        )


def _check_overload_stall(user, db, models, nudges, en=False):
    """Son check-in'de progresif yüklenme 'hayir' ise program ayarı öner."""
    ci = _latest_checkin(user, models)
    if not ci:
        return
    if (ci.progressive_overload or "").strip().lower() == "hayir":
        nudges.append(
            "NUDGE_OVERLOAD_STALL: Progressive overload has stalled (last check-in: no). "
            "Suggest a small load/rep increase, an added set, or a technique/tempo focus."
            if en else
            "NUDGE_OVERLOAD_STALL: Progresif yüklenme duraksamış (son check-in: hayır). "
            "Küçük bir yük/tekrar artışı, set ekleme veya teknik/tempo odağı öner."
        )


def _check_hydration(user, db, models, today, nudges, en=False):
    """Son 7 günde ortalama günlük su (bardak) eşiğin altındaysa nazikçe hatırlat.
    Bir bardak ≈ 240 ml; eşik 6 bardak ≈ 1.5 L. Hiç kayıt yoksa sessiz — loglamama
    durumunu zaten MISSING_LOGS yakalar, burada yanlış pozitif üretme."""
    WaterLog = models.get("WaterLog")
    if WaterLog is None:
        return
    lo = (today - timedelta(days=6)).isoformat()
    hi = today.isoformat()
    rows = WaterLog.query.filter(
        WaterLog.user_id == user.id,
        WaterLog.date_key >= lo, WaterLog.date_key <= hi).all()
    if len(rows) < 3:
        return
    days = len(rows)
    avg_cups = sum(r.count or 0 for r in rows) / days
    if avg_cups < 6:
        nudges.append(
            f"NUDGE_LOW_HYDRATION: Across {days} tracked days average daily water is ~{round(avg_cups, 1)} "
            f"cups (≈{round(avg_cups * 240)} ml) — low. Gently suggest increasing water intake."
            if en else
            f"NUDGE_LOW_HYDRATION: {days} kayıtlı günde ortalama günlük su ~{round(avg_cups, 1)} "
            f"bardak (≈{round(avg_cups * 240)} ml) — düşük. Su alımını artırmayı nazikçe öner."
        )
