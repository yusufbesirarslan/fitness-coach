"""C1 (2026-07-04 triage): with_for_update kilitleri identity-map bayatlığına
karşı etkisizdi. Kilitli SELECT ENTITY döndürünce SQLAlchemy, PK'si session'da
zaten yüklü ve süresi dolmamış örneği verir ve taze SELECT'lenen kolon
değerlerini ATAR; oku-değiştir-yaz bayat bellek değeriyle yapılıp eşzamanlı
artışı sessizce ezerdi (XP / streak / haftalık kota). Düzeltme: kilitli okuma
KOLON sorgusuyla yapılır (identity map'i atlar).

Testler bayatlığı deterministik kurar: örnek identity map'e yüklenir, sonra
"eşzamanlı isteğin commit'lediği" değişiklik Core UPDATE ile (ORM örneğine
dokunmadan) simüle edilir. Düzeltilmiş kod kilit altında taze değeri görmeli;
eski kod bayat değerle üzerine yazardı.

    python -m pytest tests/test_concurrency_staleness.py -v
"""
from datetime import timedelta

import sqlalchemy as sa

from app.extensions import db
from app.models import User
from app.timeutil import app_today


def _concurrent_commit_simulation(user_id, **values):
    """Başka bir thread'in commit'lediği satır durumunu simüle et: Core UPDATE
    DB satırını değiştirir ama identity map'teki örneğe DOKUNMAZ — gerçek
    yarıştaki 'DB ileride, bellek geride' durumunun birebir eşi."""
    db.session.execute(sa.update(User).where(User.id == user_id).values(**values))


def test_award_xp_reads_fresh_points_under_lock(app, make_user):
    from app.services.gamification import award_xp

    user = make_user("xpfresh", rank_points=0, weekly_xp=0)
    assert user.rank_points == 0  # identity map'te yüklü, süresi dolmamış

    # Eşzamanlı istek 50 XP verdi (DB=50, bellek hâlâ 0 görür).
    _concurrent_commit_simulation(user.id, rank_points=50, weekly_xp=50)

    new_total = award_xp(user.id, 10)
    db.session.commit()

    # Eski kod bayat 0'dan 10 yazar, eşzamanlı 50'yi kaybederdi.
    assert new_total == 60
    db.session.expire_all()
    fresh = db.session.get(User, user.id)
    assert fresh.rank_points == 60
    assert fresh.weekly_xp == 60


def test_update_streak_sees_concurrent_daily_update(app, make_user):
    from flask_login import login_user
    from app.hooks import update_streak

    today = app_today()
    yesterday = today - timedelta(days=1)
    user = make_user("streakfresh", last_login=yesterday, streak_count=6,
                     rank_points=0, weekly_xp=0)

    with app.test_request_context("/"):
        login_user(user)
        assert user.last_login == yesterday  # bellekte bayat kalacak değer
        # Paralel istek günü zaten işledi: streak 6→7, 7-gün milestone XP verildi.
        _concurrent_commit_simulation(user.id, last_login=today,
                                      streak_count=7, rank_points=14, weekly_xp=14)
        update_streak()

    db.session.expire_all()
    fresh = db.session.get(User, user.id)
    # Eski kod bayat last_login=dün ile streak'i 8'e çıkarır ve milestone
    # XP'sini İKİNCİ kez verirdi.
    assert fresh.streak_count == 7
    assert fresh.rank_points == 14
    assert fresh.weekly_xp == 14


def test_reserve_ai_quota_reads_fresh_metadata_under_lock(app, make_user):
    from app.services.premium import reserve_ai_quota, _week_key

    user = make_user("quotafresh")
    assert user.user_metadata is None  # identity map'te yüklü (boş)

    # Eşzamanlı üretim kotayı 1'e çekti (bellekteki örnek hâlâ boş metadata görür).
    wk = _week_key()
    _concurrent_commit_simulation(
        user.id, user_metadata={"ai_plan_quota": {"week": wk, "nutrition": 1}})

    assert reserve_ai_quota(user, "nutrition", 3) is True

    db.session.expire_all()
    fresh = db.session.get(User, user.id)
    # Eski kod bayat boş metadata'dan used=1 yazar, kota fiilen 2×'e çıkardı.
    assert fresh.user_metadata["ai_plan_quota"]["nutrition"] == 2
