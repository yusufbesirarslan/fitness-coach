"""Canonical Istanbul-day hydration (``WaterLog``) — one authority, two transports.

``WaterLog`` holds ONE absolute count of glasses per (user, Istanbul day)
(``uq_user_water_day``). The browser's ``GET/POST /water`` and the native
``GET/PUT /api/v1/nutrition/hydration`` (NUTR-PR7) read and write that same
row; nothing here is a second store.

* :func:`read_today` — the bounded read (one SELECT).
* :func:`set_today_count` — the native ABSOLUTE desired-state write under a
  precondition. The owner row is locked first (the repository's per-owner
  serialization idiom — ``supplements``/``premium``/``plan_owner_lock``) so the
  "no row yet" case is serialized too, then the day row, then the caller's
  ``check`` runs against the CURRENT count under those locks. Nothing is written
  unless it passes.
* :func:`claim_water_funnel_for_today` — the once-per-day ``water_logged``
  quest claim (moved verbatim from ``training.py``; the browser route keeps
  calling it under its old module name).
"""
from app.extensions import db
from app.models import User, WaterLog
from app.services.gamification import complete_quest_for_user


UNIT = "glass"
# The canonical bound the browser route already enforces (it clamps; the
# native transport refuses instead of silently rewriting a request).
MAX_GLASSES = 8


def read_today(user_id, day_key):
    """``(row_exists, count)`` for the owner's day; raises on storage failure."""
    row = (WaterLog.query.filter_by(user_id=user_id, date_key=day_key)
           .with_entities(WaterLog.count).first())
    if row is None:
        return False, 0
    return True, row[0]


def set_today_count(user_id, day_key, count, check):
    """Set the day's ABSOLUTE count; ``check(current_count)`` raises to refuse.

    Returns the committed count. A refused check leaves the row untouched.
    """
    db.session.query(User.id).filter_by(id=user_id).with_for_update().first()
    row = (WaterLog.query.filter_by(user_id=user_id, date_key=day_key)
           .populate_existing().with_for_update().first())
    current = row.count if row is not None else 0
    try:
        check(current)
    except Exception:
        db.session.rollback()
        raise
    if row is None:
        row = WaterLog(user_id=user_id, date_key=day_key, count=count)
        db.session.add(row)
    else:
        row.count = count
    db.session.commit()
    return count


def award_water_logged(user_id, day_key, count):
    """The day's ``water_logged`` funnel — the browser route's exact rule."""
    if count > 0 and claim_water_funnel_for_today(user_id, day_key):
        return complete_quest_for_user(user_id, "water_logged")
    return None


def claim_water_funnel_for_today(user_id, today_key):
    """Günün su hunisini (water_logged) TEK sefer talep et. True = bu istek aldı.

    Huni GÜN-anlamlıdır: weekly_water challenge'ı "5 GÜN" sayar ve
    challenges.record_event'in per-day dedup'ı YOKTUR — dedup çağıranın
    sorumluluğu (active_day deseni). Eski kapı `count > 0 and prev_count == 0`
    kullanıcının kendi SIFIRLAYABİLDİĞİ sayıya bakıyordu: 5 → 0 → 5 toggle'ı
    geçişi yeniden silahlandırıp huniyi aynı gün defalarca ateşliyordu, yani
    "5 gün" challenge'ı tek öğleden sonrada tamamlanabiliyordu
    (triage 2026-08-07 #1 / 2026-08-14 #1).

    Kalıcı gün-başı işaret (WaterLog.quest_fired) toggle'a dayanıklıdır; koşullu
    UPDATE ... WHERE quest_fired = false, uq_user_water_day sayesinde satırın
    kendisi gün-başına tek olduğu için eşzamanlı iki isteğin ikisinin birden
    ateşlemesini de engeller (DB-düzeyi tek iddia; ayrı kilit gerekmez).

    COMMIT ETMEZ: iddia ile ödülü (DailyQuest + XP + challenge) TEK transaction'da
    olmalı; çağıran hemen ardından `complete_quest_for_user` ile commit eder. Eskiden
    iddia kendi transaction'ında commit ediliyordu — ödül commit'i patlarsa
    `quest_fired` True kalıp retry'ı kalıcı bastırıyor ve günün su XP'si kayboluyordu
    (triage 2026-09-30 #5). Şimdi ödül commit'i patlarsa iddia da geri alınır ve bir
    sonraki su güncellemesi yeniden dener. Satır kilidi (UPDATE) commit'e dek tutulur,
    yani eşzamanlı ikinci iddia bekler ve `quest_fired` dolu görür → yine tek ateşleme.
    """
    claimed = (db.session.query(WaterLog)
               .filter(WaterLog.user_id == user_id,
                       WaterLog.date_key == today_key,
                       WaterLog.quest_fired.is_(False))
               .update({WaterLog.quest_fired: True}, synchronize_session=False))
    return bool(claimed)
