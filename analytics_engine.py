"""
FitX Analytics Engine — proactive nudges and insights.

Called by the AI Coach to inject smart notifications
into the conversation context. All model classes are
passed in from the caller to avoid circular imports.
"""

from datetime import datetime, date, timedelta


def get_nudges(user, db, models):
    """
    Return a list of proactive nudge strings for the given user.

    Args:
        user: Current User object
        db: SQLAlchemy db instance
        models: dict with keys 'WorkoutLog', 'UserDailyNutrition', 'UserSession'
    """
    nudges = []
    now = datetime.utcnow()
    today = date.today()

    _check_missing_logs(user, db, models, now, nudges)
    _check_protein_goal(user, db, models, today, nudges)
    _check_weekly_report_day(today, nudges)

    return nudges


def _check_missing_logs(user, db, models, now, nudges):
    cutoff = now - timedelta(hours=48)
    WorkoutLog = models["WorkoutLog"]
    UserDailyNutrition = models["UserDailyNutrition"]

    recent_workout = WorkoutLog.query.filter(
        WorkoutLog.user_id == user.id,
        WorkoutLog.created_at >= cutoff,
    ).first()

    recent_nutrition = UserDailyNutrition.query.filter(
        UserDailyNutrition.user_id == user.id,
        UserDailyNutrition.created_at >= cutoff,
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


def _check_protein_goal(user, db, models, today, nudges):
    UserSession = models["UserSession"]
    UserDailyNutrition = models["UserDailyNutrition"]

    sess = UserSession.query.filter_by(user_id=user.id)\
        .order_by(UserSession.created_at.desc()).first()
    if not sess or not sess.target_calories:
        return

    weekly_protein_goal = sess.target_calories * 0.3 / 4 * 7

    week_start = today - timedelta(days=today.weekday())
    total = db.session.query(
        db.func.coalesce(db.func.sum(UserDailyNutrition.protein), 0)
    ).filter(
        UserDailyNutrition.user_id == user.id,
        UserDailyNutrition.created_at >= datetime.combine(week_start, datetime.min.time()),
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
