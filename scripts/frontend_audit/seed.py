"""Deterministic, synthetic seed data for local frontend auditing."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from .app import load_scenario_clocks


def _naive_utc(value: str) -> datetime:
    from app.timeutil import UTC

    return datetime.fromisoformat(value).astimezone(UTC).replace(tzinfo=None)


def _training_program(rest_day: bool = False) -> list[dict]:
    monday = (
        {"gun": "Pazartesi", "tip": "dinlenme", "odak": "Aktif dinlenme", "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []}
        if rest_day else
        {"gun": "Pazartesi", "tip": "kuvvet", "odak": "Üst Vücut", "sure_dk": 50, "tahmini_kalori": 360, "egzersizler": [{"isim": "Bench Press", "set": 4, "tekrar": "8"}, {"isim": "Row", "set": 4, "tekrar": "10"}]}
    )
    return [
        monday,
        {"gun": "Salı", "tip": "dinlenme", "odak": "Dinlenme", "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []},
        {"gun": "Çarşamba", "tip": "kuvvet", "odak": "Alt Vücut", "sure_dk": 55, "tahmini_kalori": 410, "egzersizler": [{"isim": "Squat", "set": 4, "tekrar": "8"}]},
        {"gun": "Perşembe", "tip": "dinlenme", "odak": "Dinlenme", "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []},
        {"gun": "Cuma", "tip": "kuvvet", "odak": "Tüm Vücut", "sure_dk": 45, "tahmini_kalori": 330, "egzersizler": [{"isim": "Deadlift", "set": 3, "tekrar": "5"}]},
        {"gun": "Cumartesi", "tip": "kardiyo", "odak": "Kardiyo", "sure_dk": 30, "tahmini_kalori": 280, "egzersizler": [{"isim": "Koşu", "set": 1, "tekrar": "30 dk"}]},
        {"gun": "Pazar", "tip": "dinlenme", "odak": "Dinlenme", "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []},
    ]


def seed_all(app) -> dict[str, int]:
    from app.extensions import db
    from app.models import (
        CoachConversation, CoachMessage, DailyActivity, Friendship, MealLog,
        Notification, NutritionPlan, PumpCheck, Supplement, TrainingPlan, User,
        UserWearableConnection, WeeklyLog, WorkoutLog,
    )
    from app.timeutil import audit_clock

    scenarios = load_scenario_clocks()
    scenario_ids = [scenario["id"] for scenario in scenarios.values()]
    default_now = datetime.fromisoformat(scenarios["active-workout"]["fixed_current_datetime"])

    with app.app_context(), audit_clock(default_now):
        db.drop_all()
        db.create_all()
        users = {}
        for index, scenario_id in enumerate(scenario_ids):
            active = scenario_id not in {"anonymous", "new-empty", "partial"}
            user = User(
                username=f"audit-{scenario_id}",
                email=f"audit-{scenario_id}@example.invalid",
                cognito_sub=f"audit-sub-{scenario_id}",
                full_name=f"Audit {scenario_id.replace('-', ' ').title()}",
                profile_complete=active,
                weight=72.0 if active else None,
                height=178.0 if active else None,
                age=30 if active else None,
                gender="male" if active else None,
                goal="muscle_gain" if active else None,
                fitness_level="intermediate" if active else None,
                current_activity="moderate" if active else None,
                target_weight=76.0 if active else None,
                streak_count=index if active else 0,
                rank_points=1200 + index * 25 if active else 0,
                weekly_xp=180 + index * 10 if active else 0,
                last_login=datetime(2026, 7, 20).date() if active else None,
                user_metadata={"injuries": [], "equipment_available": ["gym"]} if active else {},
            )
            db.session.add(user)
            users[scenario_id] = user

        friend = User(
            username="audit-friend",
            email="audit-friend@example.invalid",
            cognito_sub="audit-sub-friend",
            full_name="Audit Friend",
            profile_complete=True,
            weight=80.0,
            height=183.0,
            age=31,
            gender="male",
            goal="general_fitness",
            fitness_level="intermediate",
            current_activity="moderate",
        )
        db.session.add(friend)
        db.session.flush()

        for scenario_id in ("active-workout", "completed-workout", "progress-history", "coach-history", "wearable-connected", "wearable-disconnected"):
            user = users[scenario_id]
            db.session.add(TrainingPlan(
                user_id=user.id,
                plan_data=json.dumps(_training_program(), ensure_ascii=False),
                score=8.7,
                created_at=_naive_utc("2026-07-19T14:00:00+03:00"),
            ))
            db.session.add(NutritionPlan(
                user_id=user.id,
                plan_data=json.dumps({"hedef_kalori": 2450, "protein": 165, "karb": 280, "yag": 75}, ensure_ascii=False),
                score=8.4,
                created_at=_naive_utc("2026-07-19T14:05:00+03:00"),
            ))
            db.session.add(MealLog(
                user_id=user.id, ogun="Kahvaltı", yemekler="Yulaf, yoğurt, muz",
                kalori=520, protein=32, karb=68, yag=14, tarih="2026-07-20",
                source="manual", created_at=_naive_utc("2026-07-20T08:00:00+03:00"),
            ))
            db.session.add(DailyActivity(
                user_id=user.id, steps=7420, intensity="moderate", calories_burned=430,
                distance_km=5.6, duration_min=58, date_key="2026-07-20",
                created_at=_naive_utc("2026-07-20T09:00:00+03:00"),
            ))

        rest_user = users["active-rest-day"]
        db.session.add(TrainingPlan(
            user_id=rest_user.id,
            plan_data=json.dumps(_training_program(rest_day=True), ensure_ascii=False),
            score=8.2,
            created_at=_naive_utc("2026-07-19T14:00:00+03:00"),
        ))

        completed = users["completed-workout"]
        db.session.add(PumpCheck(
            user_id=completed.id, location_type="gym", description="Audit workout",
            workout_score=8.5, visibility="private", shared_friend_ids=[],
            date_key="2026-07-20", created_at=_naive_utc("2026-07-20T18:30:00+03:00"),
        ))
        db.session.add(WorkoutLog(
            user_id=completed.id, exercise_name="Bench Press", sets=4, reps=8,
            weight_kg=70, volume=2240, created_at=_naive_utc("2026-07-20T18:00:00+03:00"),
        ))

        progress_user = users["progress-history"]
        for offset, weight in enumerate((74.2, 73.8, 73.4, 73.0)):
            db.session.add(WeeklyLog(
                user_id=progress_user.id, weight=weight, note="Audit history",
                created_at=_naive_utc((datetime.fromisoformat("2026-06-29T09:00:00+03:00") + timedelta(days=offset * 7)).isoformat()),
            ))

        coach_user = users["coach-history"]
        conversation = CoachConversation(
            user_id=coach_user.id,
            started_at=_naive_utc("2026-07-20T08:30:00+03:00"),
        )
        db.session.add(conversation)
        db.session.flush()
        db.session.add_all([
            CoachMessage(conversation_id=conversation.id, role="user", content="Bugünkü antrenmanım nedir?", token_estimate=8, created_at=_naive_utc("2026-07-20T08:31:00+03:00")),
            CoachMessage(conversation_id=conversation.id, role="assistant", content="Bugün üst vücut antrenmanın var.", token_estimate=10, created_at=_naive_utc("2026-07-20T08:31:10+03:00")),
        ])

        wearable_user = users["wearable-connected"]
        db.session.add(UserWearableConnection(
            user_id=wearable_user.id, provider="whoop", access_token_encrypted="audit-never-decrypted",
            status="connected", external_user_id="audit-whoop-user",
            last_sync_at=_naive_utc("2026-07-20T08:45:00+03:00"),
        ))

        active = users["active-workout"]
        db.session.add(Friendship(sender_id=active.id, receiver_id=friend.id, status="accepted"))
        db.session.add(Supplement(
            user_id=active.id, product_name="Audit Creatine", brand="Example Labs",
            category="Performance", status="Active", rating_effect=5,
            rating_taste=4, rating_digestion=5, rating_price=4,
            review_text="Synthetic audit fixture", price_paid=499.0, is_public=False,
        ))
        db.session.add(Notification(
            user_id=active.id, actor_id=friend.id, ntype="friend_accept",
            target_type="friendship", payload={"username": friend.username},
            is_read=False, created_at=_naive_utc("2026-07-20T08:50:00+03:00"),
        ))
        db.session.commit()

        return {
            "users": User.query.count(),
            "training_plans": TrainingPlan.query.count(),
            "meal_logs": MealLog.query.count(),
            "workout_logs": WorkoutLog.query.count(),
            "coach_messages": CoachMessage.query.count(),
            "wearable_connections": UserWearableConnection.query.count(),
        }
