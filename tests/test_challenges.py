# Challenge servisi testleri (Sprint 5 PR3). Hermetik: in-memory SQLite (FK ON),
# app-context aktif. Gerçek User satırları oluşturulur (FK zorunlu).
from datetime import datetime

import pytest

from app.extensions import db
from app.models import (
    Activity, Challenge, DailyQuest, Notification, User, UserBadge,
    UserChallengeProgress,
)


def _mkuser(app, **fields):
    n = User.query.count() + 1
    u = User(username="u%d" % n, email="u%d@t.co" % n)
    u.cognito_sub = "sub-u%d" % n
    for k, v in fields.items():
        setattr(u, k, v)
    db.session.add(u)
    db.session.commit()
    return u


def _seed_challenge(app, **kw):
    defaults = dict(code="weekly_workouts", title="Haftalık Antrenman",
                    description="3 antrenman", category="workouts",
                    metric="workout_logged", target_value=3, xp_reward=150,
                    badge_code=None, challenge_type="global", period_type="weekly",
                    is_active=True)
    defaults.update(kw)
    c = Challenge(**defaults)
    db.session.add(c)
    db.session.commit()
    return c


# ── Task 1: model sanity ──────────────────────────────────────────────────
def test_models_persist_and_unique(app):
    u = _mkuser(app)
    c = _seed_challenge(app)
    db.session.add(UserChallengeProgress(user_id=u.id, challenge_id=c.id,
                                         period_key="2026-W29", progress=2))
    db.session.commit()
    assert UserChallengeProgress.query.filter_by(user_id=u.id).one().progress == 2

    db.session.add(UserBadge(user_id=u.id, badge_code="pump_week",
                             source="challenge:weekly_pump:2026-W29"))
    db.session.commit()
    assert UserBadge.query.filter_by(user_id=u.id).count() == 1


# ── Task 2: badges ────────────────────────────────────────────────────────
def test_award_badge_dedup_and_unknown(app):
    from app.services import badges
    u = _mkuser(app)
    assert badges.award_badge(u.id, None) is None
    assert badges.award_badge(u.id, "not_a_real_badge") is None
    b = badges.award_badge(u.id, "pump_week", source="challenge:weekly_pump:2026-W29")
    db.session.commit()
    assert b is not None
    assert badges.award_badge(u.id, "pump_week") is None      # duplicate → None
    db.session.commit()
    assert UserBadge.query.filter_by(user_id=u.id, badge_code="pump_week").count() == 1
    codes = {x["code"] for x in badges.badges_for(u.id)}
    assert "pump_week" in codes


# ── Task 3: period math ───────────────────────────────────────────────────
def test_current_challenge_week_boundaries():
    from app.services.challenges import current_challenge_week
    # Istanbul-naive datetimes (same convention as _last_completed_week_key tests)
    assert current_challenge_week(datetime(2026, 6, 10, 12, 0)) == "2026-W24"   # hafta ortası → bu hafta
    assert current_challenge_week(datetime(2026, 6, 14, 23, 58)) == "2026-W24"  # kapanıştan 1 dk önce → hâlâ bu hafta
    assert current_challenge_week(datetime(2026, 6, 14, 23, 59)) == "2026-W25"  # tam kapanış → yeni hafta
    assert current_challenge_week(datetime(2026, 6, 15, 0, 0)) == "2026-W25"    # Pazartesi 00:00


def test_period_end_utc_is_sunday_2359_istanbul():
    from app.services.challenges import period_end_utc
    from app.timeutil import to_app_tz
    end = period_end_utc(datetime(2026, 6, 10, 12, 0))  # mid-week
    local = to_app_tz(end)   # naive UTC → Istanbul
    assert local.isoweekday() == 7 and local.hour == 23 and local.minute == 59


# ── Task 4: record_event + completion ─────────────────────────────────────
def test_record_event_increments_and_accumulates(app):
    from app.services import challenges
    u = _mkuser(app)
    _seed_challenge(app, code="weekly_workouts", metric="workout_logged", target_value=3, xp_reward=150)
    challenges.record_event(u.id, "workout_logged"); db.session.commit()
    challenges.record_event(u.id, "workout_logged", amount=1); db.session.commit()
    row = UserChallengeProgress.query.filter_by(user_id=u.id).one()
    assert row.progress == 2 and row.completed_at is None


def test_record_event_completes_exactly_once(app):
    from app.services import challenges
    u = _mkuser(app)
    c = _seed_challenge(app, code="weekly_pump", metric="pump_check_created",
                        target_value=1, xp_reward=100, badge_code="pump_week")
    before = User.query.get(u.id).rank_points or 0
    challenges.record_event(u.id, "pump_check_created"); db.session.commit()
    challenges.record_event(u.id, "pump_check_created"); db.session.commit()  # 2nd event, already complete
    row = UserChallengeProgress.query.filter_by(user_id=u.id, challenge_id=c.id).one()
    assert row.completed_at is not None
    assert (User.query.get(u.id).rank_points or 0) == before + 100      # XP once
    assert UserBadge.query.filter_by(user_id=u.id, badge_code="pump_week").count() == 1


def test_challenge_complete_notifies_each_week(app, monkeypatch):
    # Aynı challenge FARKLI haftalarda tamamlanınca ayrı bildirim üretmeli — ilk
    # bildirim OKUNMAMIŞ kalsa bile. Kimlik target_id=row.id (haftaya özgü progress
    # satırı) olduğundan hafta tekrarları dedup'lanmaz.
    from app.services import challenges
    u = _mkuser(app)
    _seed_challenge(app, code="weekly_pump", metric="pump_check_created",
                    target_value=1, xp_reward=100)
    monkeypatch.setattr(challenges, "current_challenge_week", lambda now=None: "2026-W01")
    challenges.record_event(u.id, "pump_check_created"); db.session.commit()
    monkeypatch.setattr(challenges, "current_challenge_week", lambda now=None: "2026-W02")
    challenges.record_event(u.id, "pump_check_created"); db.session.commit()

    notifs = Notification.query.filter_by(user_id=u.id, ntype="challenge_complete").all()
    assert len(notifs) == 2                                    # haftalar arası dedup YOK
    assert {n.payload["week"] for n in notifs} == {"2026-W01", "2026-W02"}
    assert len({n.target_id for n in notifs}) == 2             # haftaya özgü tekil kimlik


def test_featured_requires_opt_in(app):
    from app.services import challenges
    u = _mkuser(app)
    _seed_challenge(app, code="featured_grind", metric="workout_logged",
                    target_value=2, challenge_type="featured")
    challenges.record_event(u.id, "workout_logged"); db.session.commit()
    assert UserChallengeProgress.query.filter_by(user_id=u.id).count() == 0  # no auto-create for featured


def test_record_event_never_commits(app, monkeypatch):
    # record_event çağıranla aynı transaction'da çalışmalı → HİÇ commit etmemeli
    # (atomiklik sözleşmesi). commit'i casusla: prod-Postgres savepoint semantiği
    # SQLite'ta birebir taklit edilemediği için rollback yerine doğrudan "commit
    # çağrıldı mı?" kontrol edilir (dialect-bağımsız + record_event'in geniş
    # except'i AssertionError'ı yutar, bu yüzden istisna değil sayaç kullanılır).
    from app.services import challenges
    u = _mkuser(app)
    _seed_challenge(app, code="weekly_workouts", metric="workout_logged", target_value=3)
    commits = []
    monkeypatch.setattr(db.session, "commit", lambda *a, **k: commits.append(1))
    challenges.record_event(u.id, "workout_logged")
    assert commits == []


# ── Task 5: join + board + seed ───────────────────────────────────────────
def test_join_featured_only(app):
    from app.services import challenges
    u = _mkuser(app)
    g = _seed_challenge(app, code="weekly_workouts", challenge_type="global")
    f = _seed_challenge(app, code="featured_grind", challenge_type="featured")
    assert challenges.join_featured(u.id, g.id) is None            # global not joinable
    row = challenges.join_featured(u.id, f.id); db.session.commit()
    assert row is not None and row.opted_in is True


def test_challenge_board_ordering(app):
    from app.services import challenges
    c = _seed_challenge(app, code="weekly_workouts", target_value=5)
    us = [_mkuser(app) for _ in range(3)]
    pk = challenges.current_challenge_week()
    for u, prog in zip(us, (2, 5, 5)):
        r = UserChallengeProgress(user_id=u.id, challenge_id=c.id, period_key=pk, progress=prog)
        db.session.add(r)
    db.session.commit()
    board = challenges.challenge_board(c.id, pk, "global", us[0].id)
    # progress desc → the two 5s ahead of the 2; tie broken by user_id asc
    assert [e["progress"] for e in board["entries"]] == [5, 5, 2]


def test_seed_challenges_idempotent(app):
    from app.services import challenges
    challenges.seed_challenges()
    n = Challenge.query.count()
    challenges.seed_challenges()
    assert Challenge.query.count() == n and n >= 8


# ── Task 6: gamification wiring ───────────────────────────────────────────
def test_challenge_reward_xp_does_not_feed_xp_challenge(app):
    from app.services import challenges
    u = _mkuser(app)
    _seed_challenge(app, code="weekly_pump", metric="pump_check_created", target_value=1,
                    xp_reward=200, badge_code="pump_week")
    _seed_challenge(app, code="weekly_xp", metric="xp_earned", target_value=100, xp_reward=150)
    challenges.record_event(u.id, "pump_check_created"); db.session.commit()
    xp_row = (UserChallengeProgress.query
              .join(Challenge).filter(Challenge.code == "weekly_xp").first())
    assert xp_row is None or xp_row.progress == 0     # reward XP excluded


def test_quest_event_funnels_to_challenge(app):
    from app.services.gamification import _claim_quest
    u = _mkuser(app)
    db.session.add(DailyQuest(title="Öğün", description="x", points_reward=20,
                              quest_type="meal_logged", is_active=True))
    _seed_challenge(app, code="weekly_meals", metric="meal_logged", target_value=10, xp_reward=100)
    db.session.commit()
    _claim_quest(u.id, "meal_logged"); db.session.commit()
    row = (UserChallengeProgress.query.join(Challenge)
           .filter(Challenge.code == "weekly_meals").one())
    assert row.progress == 1


# ── Regression: transaction poisoning (#2, triage 2026-07-17) ──────────────
def test_record_event_db_error_does_not_poison_caller_transaction(app, monkeypatch):
    """Bir challenge işlenirken geçici DB hatası çıksa bile record_event çağıranın
    transaction'ını POISON'lamaz: hata yalnızca o challenge'ın savepoint'ini geri
    alır, çağıranın bekleyen yazısı ve sonraki commit sağ kalır. Regresyon olursa
    update_streak'in çıplak commit'i PendingRollbackError → normal gezinmede 500."""
    from app.services import challenges
    u = _mkuser(app)
    _seed_challenge(app, code="weekly_workouts", metric="workout_logged",
                    target_value=1, xp_reward=150)

    # Tamamlama yolunda gerçekçi bir DB arızasını taklit et: flush'ta IntegrityError
    # (deadlock / kilit-zaman-aşımı gibi oturumu "rollback gerekli" durumuna sokar).
    def _poison(*a, **k):
        db.session.add(UserBadge(user_id=None, badge_code=None))  # NOT NULL ihlali
        db.session.flush()
    monkeypatch.setattr(challenges, "_try_complete", _poison)

    # update_streak'in record_event'ten ÖNCE sahneye koyduğu yazıyı taklit et.
    u.streak_count = 5
    challenges.record_event(u.id, "workout_logged")   # iç hata yutulur, patlamaz

    # Fix ile oturum kullanılabilir kalır → çıplak commit patlamaz, streak kalıcı olur.
    db.session.commit()
    assert db.session.get(User, u.id).streak_count == 5
    # Başarısız challenge'ın ilerlemesi savepoint ile geri alındı (tamamlanmadı).
    row = UserChallengeProgress.query.filter_by(user_id=u.id).first()
    assert row is None or row.completed_at is None


# ── PR3 remediation: no_autoflush + tamamlama atomikliği (2026-07-18) ──────
def test_record_event_no_match_does_not_flush_caller_writes(app):
    """Item 1 (no_autoflush): eşleşen challenge YOKKEN record_event çağıranın
    bekleyen INSERT'ini FLUSH ETMEZ (salt-katalog okuması no_autoflush + no-match
    erken çıkış). Gelecekteki bir çağıran, kendi commit'inde ele alacağı bekleyen
    bir yazı tutuyorsa record_event onu erken flush edip session'ı poison'lamaz.
    (Taze pending nesne → session.new üyeliği kesin bir oracle'dır.)"""
    from app.services import challenges
    u = _mkuser(app)
    act = Activity(user_id=u.id, activity_type="pending_probe", content="x")
    db.session.add(act)                      # çağıranın bekleyen (flush'lanmamış) INSERT'i
    assert act in db.session.new
    # 'workout_logged' metriğine seed'li challenge yok → eşleşme yok, erken çıkış.
    challenges.record_event(u.id, "workout_logged")
    assert act in db.session.new             # record_event flush ETMEDİ → hâlâ bekliyor
    db.session.commit()                      # çağıran kendi işini yazar; poison yok → commit patlamaz
    assert Activity.query.filter_by(activity_type="pending_probe").count() == 1


def test_challenge_completion_atomic_on_reward_failure(app, monkeypatch):
    """Item 2: ödül adımlarından biri (feed aktivitesi) patlarsa tamamlama TÜMÜYLE
    geri alınır — XP verilip rozet/bildirim bırakılmış "kısmi başarı" YOK. Gerçek
    record_event yolu üzerinden (iç hata yutulur, ana eylem kırılmaz)."""
    from app.services import challenges, gamification
    u = _mkuser(app)
    c = _seed_challenge(app, code="weekly_pump", metric="pump_check_created",
                        target_value=1, xp_reward=100, badge_code="pump_week")
    before_xp = User.query.get(u.id).rank_points or 0

    def _boom(*a, **k):
        raise RuntimeError("feed aktivite servisi düştü")
    monkeypatch.setattr(gamification, "log_activity", _boom)   # tamamlamanın son ödül adımı

    challenges.record_event(u.id, "pump_check_created")        # iç hata yutulur, patlamaz
    db.session.commit()

    row = UserChallengeProgress.query.filter_by(user_id=u.id, challenge_id=c.id).first()
    assert row is None or row.completed_at is None                              # tamamlanmadı
    assert (User.query.get(u.id).rank_points or 0) == before_xp                 # XP geri alındı
    assert UserBadge.query.filter_by(user_id=u.id, badge_code="pump_week").count() == 0
    assert Notification.query.filter_by(user_id=u.id, ntype="challenge_complete").count() == 0
    assert Activity.query.filter_by(user_id=u.id, activity_type="challenge_completed").count() == 0


def test_try_complete_atomic_without_outer_savepoint(app, monkeypatch):
    """Item 2: _try_complete KENDİ begin_nested'iyle atomik — record_event'in
    sarmalayan savepoint'i OLMASA da (gelecekteki doğrudan çağıranlar) bir ödül
    adımı patlarsa completed_at dahil her şey geri alınır ve session KULLANILABİLİR
    kalır. Inner savepoint kaldırılırsa bu test kırılır (completed_at set kalır)."""
    from app.services import challenges, gamification
    u = _mkuser(app)
    c = _seed_challenge(app, code="weekly_pump", metric="pump_check_created",
                        target_value=1, xp_reward=100, badge_code="pump_week")
    pk = challenges.current_challenge_week()
    row = UserChallengeProgress(user_id=u.id, challenge_id=c.id, period_key=pk, progress=1)
    db.session.add(row)
    db.session.commit()
    before_xp = User.query.get(u.id).rank_points or 0

    def _boom(*a, **k):
        raise RuntimeError("feed down")
    monkeypatch.setattr(gamification, "log_activity", _boom)

    with pytest.raises(RuntimeError):
        challenges._try_complete(u.id, c, row, pk)   # sarmalayan savepoint YOK — doğrudan çağrı

    # begin_nested geri alındı → session kullanılabilir; tamamlama/ödül YOK.
    fresh = db.session.get(UserChallengeProgress, row.id)
    assert fresh.completed_at is None
    assert (User.query.get(u.id).rank_points or 0) == before_xp
    assert UserBadge.query.filter_by(user_id=u.id, badge_code="pump_week").count() == 0
    assert Notification.query.filter_by(user_id=u.id, ntype="challenge_complete").count() == 0
