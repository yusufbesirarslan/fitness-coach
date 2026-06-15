"""
FitX Analytics Engine — proactive nudges and insights.

Called by the AI Coach to inject smart notifications
into the conversation context. All model classes are
passed in from the caller to avoid circular imports.
"""

from datetime import datetime, date, timedelta


def get_nudges(user, db, models, prev_last_login=None):
    """
    Return a list of proactive nudge strings for the given user.

    Args:
        user: Current User object
        db: SQLAlchemy db instance
        models: dict with keys 'WorkoutLog', 'MealLog', 'UserSession'
        prev_last_login: kullanıcının bu istekten ÖNCEKI last_login değeri. Verilmezse
            user.last_login kullanılır. update_streak before_request hook'u last_login'i
            "bugün" yaptığından, seri-riski dürtüsü aksi halde asla tetiklenmezdi.
    """
    nudges = []
    now = datetime.utcnow()
    today = date.today()

    _check_missing_logs(user, db, models, now, nudges)
    _check_streak_at_risk(user, today, nudges, prev_last_login)
    _check_protein_goal(user, db, models, today, nudges)
    _check_weekly_report_day(today, nudges)

    return nudges


def _check_missing_logs(user, db, models, now, nudges):
    cutoff = now - timedelta(hours=48)
    WorkoutLog = models["WorkoutLog"]
    MealLog = models["MealLog"]

    recent_workout = WorkoutLog.query.filter(
        WorkoutLog.user_id == user.id,
        WorkoutLog.created_at >= cutoff,
    ).first()

    recent_nutrition = MealLog.query.filter(
        MealLog.user_id == user.id,
        MealLog.created_at >= cutoff,
    ).first()

    if not recent_workout and not recent_nutrition:
        nudges.append(
            "NUDGE_MISSING_LOGS: 48 saatten fazladır antrenman veya beslenme kaydı yok. "
            "Kullanıcıyı nazikçe ve destekleyici bir şekilde loglama yapmaya teşvik et."
        )
    elif not recent_workout:
        nudges.append(
            "NUDGE_NO_WORKOUT: Son 48 saatte antrenman kaydı yok. "
            "Hafifçe hatırlat, baskı yapma."
        )
    elif not recent_nutrition:
        nudges.append(
            "NUDGE_NO_NUTRITION: Son 48 saatte beslenme kaydı yok. "
            "Ne yediğini loglamasını öner."
        )


def _check_streak_at_risk(user, today, nudges, prev_last_login=None):
    streak = user.streak_count or 0
    last_login = prev_last_login if prev_last_login is not None else user.last_login

    if streak >= 5 and last_login and last_login < today:
        nudges.append(
            f"NUDGE_STREAK_RISK: Kullanıcının {streak} günlük serisi var ve bugün henüz aktif değil. "
            f"'Seriyi kırma!' tarzı motive edici bir mesaj ver."
        )


def _check_protein_goal(user, db, models, today, nudges):
    UserSession = models["UserSession"]
    MealLog = models["MealLog"]

    sess = UserSession.query.filter_by(user_id=user.id)\
        .order_by(UserSession.created_at.desc()).first()
    if not sess or not sess.target_calories:
        return

    weekly_protein_goal = sess.target_calories * 0.3 / 4 * 7

    week_start = today - timedelta(days=today.weekday())
    total = db.session.query(
        db.func.coalesce(db.func.sum(MealLog.protein), 0)
    ).filter(
        MealLog.user_id == user.id,
        MealLog.created_at >= datetime.combine(week_start, datetime.min.time()),
    ).scalar()

    if weekly_protein_goal > 0 and total >= weekly_protein_goal * 0.9:
        nudges.append(
            f"NUDGE_PROTEIN_GOAL: Haftalık protein hedefinin %90'ına ulaştı! "
            f"({round(total, 1)}g / {round(weekly_protein_goal, 1)}g). Tebrik et!"
        )


def _check_weekly_report_day(today, nudges):
    if today.weekday() in (0, 6):
        nudges.append(
            "NUDGE_WEEKLY_REPORT: Bugün haftalık rapor günü. "
            "generate_weekly_report aracını kullanarak haftalık performans özetini sun."
        )
