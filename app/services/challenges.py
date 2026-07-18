# Meydan okuma servisi (Sprint 5 PR3). record_event tek huni; period_key hesaplı
# (ISO hafta, Sunday-23:59-Istanbul sınırı, _last_completed_week_key ile aynı).
# record_event COMMIT ETMEZ (çağıran commit eder — _claim_quest sözleşmesi).
import logging
from datetime import datetime, timedelta

from app.extensions import db
from app.models import Challenge, User, UserChallengeProgress
from app.timeutil import UTC, app_now

log = logging.getLogger(__name__)


# ── Periyot matematiği ────────────────────────────────────────────────────
def _week_bounds(now):
    """Bu takvim haftasının Pazartesi 00:00 ve Pazar 23:59'unu döndür (now'un tz'i korunur)."""
    monday = (now - timedelta(days=now.isoweekday() - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    sunday_2359 = monday + timedelta(days=6, hours=23, minutes=59)
    return monday, sunday_2359


def current_challenge_week(now=None):
    """AKTİF (devam eden) haftanın ISO anahtarı. _last_completed_week_key'in tersi:
    Pazar 23:59'dan ÖNCE bu ISO hafta; tam/sonra bir sonraki hafta."""
    now = now or app_now()
    monday, sunday_2359 = _week_bounds(now)
    ref = (monday + timedelta(days=7)) if now >= sunday_2359 else now
    y, w, _ = ref.isocalendar()
    return "%d-W%02d" % (y, w)


def period_end_utc(now=None):
    """Bu periyodun bitişi = yaklaşan Pazar 23:59 Istanbul → NAIVE UTC döndür
    (leaderboard/challenges countdown istemciye ISO UTC olarak verir)."""
    now = now or app_now()
    monday, sunday_2359 = _week_bounds(now)
    end_local = (monday + timedelta(days=13, hours=23, minutes=59)) if now >= sunday_2359 else sunday_2359
    if end_local.tzinfo is None:
        from app.timeutil import APP_TZ
        end_local = end_local.replace(tzinfo=APP_TZ)
    return end_local.astimezone(UTC).replace(tzinfo=None)


# ── Olay huni + tamamlama ─────────────────────────────────────────────────
def _get_or_create_global_row(user_id, challenge_id, period_key):
    """Global challenge için satırı getir ya da yarış-güvenli oluştur (commit etmez)."""
    row = UserChallengeProgress.query.filter_by(
        user_id=user_id, challenge_id=challenge_id, period_key=period_key).first()
    if row is not None:
        return row
    try:
        with db.session.begin_nested():
            row = UserChallengeProgress(user_id=user_id, challenge_id=challenge_id,
                                        period_key=period_key, progress=0)
            db.session.add(row)
        return row
    except Exception:
        # Eşzamanlı istek aynı satırı yazdı (uq_user_challenge_period) — yeniden oku.
        return UserChallengeProgress.query.filter_by(
            user_id=user_id, challenge_id=challenge_id, period_key=period_key).first()


def record_event(user_id, event_type, amount=1):
    """Bir gamification olayını tüm eşleşen aktif challenge'lara işle. COMMIT ETMEZ.
    Global → get-or-create + ilerlet; featured → yalnızca mevcut opted_in satır.
    Tamamlanınca (guarded UPDATE, tam-bir-kez) XP + rozet + bildirim + feed aktivitesi.

    Her challenge KENDİ savepoint'inde işlenir: geçici bir DB hatası (deadlock /
    kilit zaman aşımı — 8 thread User/challenge satırlarında FOR UPDATE alırken
    gerçekçi) yalnızca o savepoint'i geri alır, çağıranın DIŞ transaction'ını
    KULLANILABİLİR bırakır. Böylece "challenge ilerlemesi ana eylemi ASLA kırmaz"
    sözü gerçekten tutulur; aksi halde yutulan hata (rollback'siz) oturumu
    poison'lar ve çağıranın (ör. update_streak) çıplak commit'i
    PendingRollbackError → normal gezinmede 500 verirdi (triage 2026-07-17 #2)."""
    try:
        matched = Challenge.query.filter_by(metric=event_type, is_active=True).all()
        if not matched:
            return
        period_key = current_challenge_week()
    except Exception:
        # Katalog/periyot okuması patlarsa: yut ve çık. Burada ROLLBACK ETME —
        # çağıranın bekleyen yazısını (streak / WorkoutLog vb.) geri almak ana
        # eylemi kırardı; oturum poison olduysa çağıranın korumalı commit'i
        # (bkz. update_streak) 500'ü önler.
        log.warning("record_event katalog okuması başarısız (yutuldu): user=%s event=%s",
                    user_id, event_type, exc_info=True)
        return
    for ch in matched:
        # Dış değişiklikler (streak/last_login/WorkoutLog...) yukarıdaki katalog
        # sorgusunun autoflush'ı ile savepoint AÇILMADAN ÖNCE dış transaction'a
        # yazıldı → savepoint rollback'i onları GERİ ALMAZ, yalnızca bu challenge'ın
        # yazısını alır.
        try:
            with db.session.begin_nested():
                if ch.challenge_type == "featured":
                    row = UserChallengeProgress.query.filter_by(
                        user_id=user_id, challenge_id=ch.id, period_key=period_key,
                        opted_in=True).first()
                else:
                    row = _get_or_create_global_row(user_id, ch.id, period_key)
                if row is None or row.completed_at is not None:
                    continue
                # Atomik ilerleme (kayıp güncelleme yok — kolon UPDATE).
                UserChallengeProgress.query.filter_by(id=row.id).update(
                    {UserChallengeProgress.progress: UserChallengeProgress.progress + amount},
                    synchronize_session=False)
                db.session.refresh(row)
                if row.progress >= ch.target_value:
                    _try_complete(user_id, ch, row, period_key)
        except Exception:
            # Savepoint geri alındı → oturum kullanılabilir kalır; bu challenge
            # atlanır, kalan challenge'lar işlenmeye devam eder.
            log.warning("record_event başarısız (yutuldu): user=%s event=%s challenge=%s",
                        user_id, event_type, getattr(ch, "code", "?"), exc_info=True)


def _try_complete(user_id, ch, row, period_key):
    """Guarded completion — WHERE completed_at IS NULL kazanan tek satır ödülü verir."""
    from app.services.badges import award_badge
    from app.services.gamification import award_xp, log_activity
    from app.services.notifications import notify

    now = datetime.utcnow()
    won = UserChallengeProgress.query.filter(
        UserChallengeProgress.id == row.id,
        UserChallengeProgress.completed_at.is_(None),
    ).update({UserChallengeProgress.completed_at: now}, synchronize_session=False)
    if not won:
        return
    award_xp(user_id, ch.xp_reward, count_challenge_xp=False)
    if ch.badge_code:
        award_badge(user_id, ch.badge_code,
                    source="challenge:%s:%s" % (ch.code, period_key))
    # Hedef = bu tamamlamanın progress satırı (row.id): (user, challenge, period_key)
    # başına tekil (uq_user_challenge_period). Böylece AYNI challenge'ın farklı
    # haftalardaki tamamlamaları okunmamış-dedup'ta ÇAKIŞMAZ. Aynı hafta/aynı
    # challenge zaten korumalı UPDATE ile tam-bir-kez tamamlanır (gerçek tekrar yok).
    # payload week/code'u taşımaya devam eder (istemci metni).
    notify(user_id, "challenge_complete", actor_id=None,
           target_type="challenge", target_id=row.id,
           payload={"code": ch.code, "xp": ch.xp_reward,
                    "badge": ch.badge_code, "week": period_key})
    log_activity(user_id, "challenge_completed",
                 "'%s' meydan okumasını tamamladı!" % ch.title)


# ── Katıl + katalog seed + leaderboard ────────────────────────────────────
CHALLENGE_SEED = [
    # global (auto-participate)
    dict(code="weekly_workouts", title="Haftalık Antrenman", description="Bu hafta 3 antrenman tamamla",
         category="workouts", metric="workout_logged", target_value=3, xp_reward=150, badge_code=None,
         challenge_type="global"),
    dict(code="weekly_meals", title="Beslenme Takibi", description="Bu hafta 10 öğün kaydet",
         category="nutrition", metric="meal_logged", target_value=10, xp_reward=100, badge_code=None,
         challenge_type="global"),
    dict(code="weekly_water", title="Su Kahramanı", description="Bu hafta 5 gün su takibini gir",
         category="hydration", metric="water_logged", target_value=5, xp_reward=75, badge_code=None,
         challenge_type="global"),
    dict(code="weekly_pump", title="Pump Check Serisi", description="Bu hafta 3 Pump Check paylaş",
         category="pump_check", metric="pump_check_created", target_value=3, xp_reward=100,
         badge_code="pump_week", challenge_type="global"),
    dict(code="weekly_active", title="Aktif Hafta", description="Bu hafta 5 gün aktif ol",
         category="active_days", metric="active_day", target_value=5, xp_reward=100,
         badge_code="active_week", challenge_type="global"),
    dict(code="weekly_xp", title="XP Avcısı", description="Bu hafta 500 XP kazan",
         category="xp", metric="xp_earned", target_value=500, xp_reward=150, badge_code=None,
         challenge_type="global"),
    # featured (opt-in)
    dict(code="featured_pump_perfect", title="Kusursuz Pump", description="Bu hafta 5 Pump Check paylaş",
         category="pump_check", metric="pump_check_created", target_value=5, xp_reward=300,
         badge_code="pump_perfect", challenge_type="featured"),
    dict(code="featured_grind", title="Grind Modu", description="Bu hafta 5 antrenman tamamla",
         category="workouts", metric="workout_logged", target_value=5, xp_reward=250,
         badge_code="grinder", challenge_type="featured"),
]


def seed_challenges():
    """Katalog seed'i — code'a göre idempotent (DailyQuest deseni). Commit eder."""
    for spec in CHALLENGE_SEED:
        if not Challenge.query.filter_by(code=spec["code"]).first():
            db.session.add(Challenge(period_type="weekly", is_active=True, **spec))
    db.session.commit()


def join_featured(user_id, challenge_id):
    """Featured challenge'a katıl (opted_in satır). Global → None. COMMIT ETMEZ."""
    ch = Challenge.query.filter_by(id=challenge_id, is_active=True,
                                   challenge_type="featured").first()
    if ch is None:
        return None
    period_key = current_challenge_week()
    row = UserChallengeProgress.query.filter_by(
        user_id=user_id, challenge_id=ch.id, period_key=period_key).first()
    if row is not None:
        row.opted_in = True
        return row
    try:
        with db.session.begin_nested():
            row = UserChallengeProgress(user_id=user_id, challenge_id=ch.id,
                                        period_key=period_key, progress=0, opted_in=True)
            db.session.add(row)
        return row
    except Exception:
        return UserChallengeProgress.query.filter_by(
            user_id=user_id, challenge_id=ch.id, period_key=period_key).first()


def _board_entry(u, row, rank):
    return {"rank": rank, "user_id": u.id, "username": u.username,
            "full_name": u.full_name or u.username, "profile_picture": u.avatar_src,
            "progress": row.progress if row else 0,
            "completed": bool(row and row.completed_at)}


def challenge_board(challenge_id, period_key, scope, viewer_id):
    """Challenge sıralaması (progress desc, completed_at asc nulls-last, user_id asc).
    Top 50 + kapsam dışıysa 'me' satırı. Redis yok; Postgres/SQLite ORDER BY."""
    from app.services.friends import get_friend_ids

    q = (db.session.query(UserChallengeProgress, User)
         .join(User, User.id == UserChallengeProgress.user_id)
         .filter(UserChallengeProgress.challenge_id == challenge_id,
                 UserChallengeProgress.period_key == period_key))
    if scope == "friends":
        ids = get_friend_ids(viewer_id) | {viewer_id}
        q = q.filter(UserChallengeProgress.user_id.in_(ids))
    # nulls-last: tamamlanmış satırlar (completed_at dolu) en erken tamamlanmaya göre,
    # sonra devam edenler progress desc. completed_first=1 (null) satırları sona iter.
    completed_first = db.case((UserChallengeProgress.completed_at.is_(None), 1), else_=0)
    rows = q.order_by(UserChallengeProgress.progress.desc(),
                      completed_first.asc(),
                      UserChallengeProgress.completed_at.asc(),
                      User.id.asc()).limit(50).all()
    entries = [_board_entry(u, r, i + 1) for i, (r, u) in enumerate(rows)]
    in_list = any(e["user_id"] == viewer_id for e in entries)
    me = next((e for e in entries if e["user_id"] == viewer_id), None)
    if not in_list:
        my = (db.session.query(UserChallengeProgress, User)
              .join(User, User.id == UserChallengeProgress.user_id)
              .filter(UserChallengeProgress.challenge_id == challenge_id,
                      UserChallengeProgress.period_key == period_key,
                      UserChallengeProgress.user_id == viewer_id).first())
        if my is not None:
            me = _board_entry(my[1], my[0], None)
        else:
            u = db.session.get(User, viewer_id)
            me = _board_entry(u, None, None) if u else None
    return {"entries": entries, "me": me, "in_list": in_list}
