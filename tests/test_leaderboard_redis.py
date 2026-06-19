"""Leaderboard'un REDIS hızlı yolu (app/services/gamification.py).

Mevcut test_gamification_routes.py Redis'siz ortamda yalnızca Postgres yolunu
sınar. Burada minimal bir in-memory sorted-set sahte'siyle (fakeredis bağımlılığı
YOK) Redis yolu sabitlenir: global ZREVRANGE sıralaması, friends ZMSCORE birleşmesi,
top-listede olmayan kullanıcının ZREVRANK ile rütbesi, Redis hatasında Postgres'e
şeffaf düşüş ve commit-sonrası (after_commit) Redis senkronizasyonu.

    python -m pytest tests/test_leaderboard_redis.py -v
"""
import app.blueprints.gamification as gam_bp
import app.services.gamification as gam
from app.config import LB_ALLTIME_KEY, LB_WEEKLY_KEY
from app.extensions import db
from app.models import Friendship


# ---------------------------------------------------------------------------
# Minimal in-memory Redis sahtesi — yalnızca leaderboard'un kullandığı komutlar.
# ---------------------------------------------------------------------------

class FakeRedis:
    def __init__(self):
        self.zsets = {}        # key -> {member(str): score(float)}
        self.fail_zrevrange = False

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(
            {str(m): float(s) for m, s in mapping.items()})

    def _ordered(self, key):
        # Skora göre azalan; eşitlikte üye-id'sine göre artan (deterministik).
        return sorted(self.zsets.get(key, {}).items(),
                      key=lambda kv: (-kv[1], int(kv[0])))

    def zrevrange(self, key, start, end):
        if self.fail_zrevrange:
            raise RuntimeError("redis down")
        items = self._ordered(key)
        sliced = items[start:] if end == -1 else items[start:end + 1]
        return [m for m, _ in sliced]

    def zmscore(self, key, members):
        z = self.zsets.get(key, {})
        return [z.get(str(m)) for m in members]

    def zrevrank(self, key, member):
        order = [m for m, _ in self._ordered(key)]
        member = str(member)
        return order.index(member) if member in order else None

    def zcard(self, key):
        return len(self.zsets.get(key, {}))

    def delete(self, *keys):
        for k in keys:
            self.zsets.pop(k, None)

    def ping(self):
        return True

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, r):
        self.r = r
        self.ops = []

    def delete(self, *keys):
        self.ops.append(("delete", keys))
        return self

    def zadd(self, key, mapping):
        self.ops.append(("zadd", (key, mapping)))
        return self

    def execute(self):
        for name, args in self.ops:
            getattr(self.r, name)(*args)
        self.ops = []


def _use_fake_redis(monkeypatch, fake):
    """redis_client'ı HEM blueprint HEM servis modülünde sahteye çevir (ikisi de
    import zamanında kendi referansını tutar)."""
    monkeypatch.setattr(gam_bp, "redis_client", fake)
    monkeypatch.setattr(gam, "redis_client", fake)


# ---------------------------------------------------------------------------
# Global kapsam — ZREVRANGE sıralaması
# ---------------------------------------------------------------------------

def test_global_leaderboard_redis_order(client, auth_user, make_users_bulk, monkeypatch):
    # Postgres rank_points sırası artan (u0<u1<u2) → Postgres yolu u2,u1,u0 verirdi.
    u0, u1, u2 = make_users_bulk(3, prefix="u", rank_points=0)
    u0.rank_points, u1.rank_points, u2.rank_points = 10, 20, 30
    db.session.commit()
    fake = FakeRedis()
    # Redis skorlarını TERS koy (u0>u1>u2) → Redis yolu kullanılıyorsa sıra TERSİNE
    # döner. Bu, Postgres yolundan kesin olarak ayırt eder. (testuser, istek-içi
    # streak hook'uyla gerçek skoruna yeniden senkronlanır; en sonda kalır.)
    fake.zadd(LB_ALLTIME_KEY, {u0.id: 300, u1.id: 200, u2.id: 100})
    _use_fake_redis(monkeypatch, fake)

    body = client.get("/leaderboard/data").get_json()
    names = [e["username"] for e in body["entries"]]
    assert names[:3] == ["u0", "u1", "u2"]  # Redis (ters) sırası — Postgres'in TAM TERSİ
    assert body["in_list"] is True           # testuser de listede (kendi gerçek skoruyla)
    assert body["me"]["username"] == "testuser"


def test_global_weekly_timeframe_reads_weekly_key(client, auth_user, make_users_bulk, monkeypatch):
    (other,) = make_users_bulk(1, prefix="w", rank_points=0, weekly_xp=0)
    fake = FakeRedis()
    # all-time ve weekly setleri farklı sıralar → weekly istendiğinde weekly key okunmalı.
    fake.zadd(LB_ALLTIME_KEY, {auth_user.id: 999, other.id: 1})
    fake.zadd(LB_WEEKLY_KEY, {auth_user.id: 1, other.id: 999})
    _use_fake_redis(monkeypatch, fake)

    body = client.get("/leaderboard/data?timeframe=weekly").get_json()
    assert body["timeframe"] == "weekly"
    assert body["entries"][0]["username"] == other.username  # weekly key sırası


def test_global_me_outside_top_gets_rank_via_zrevrank(client, auth_user, make_users_bulk, monkeypatch):
    a, b = make_users_bulk(2, prefix="top", rank_points=0)
    fake = FakeRedis()
    fake.zadd(LB_ALLTIME_KEY, {a.id: 300, b.id: 200, auth_user.id: 100})
    _use_fake_redis(monkeypatch, fake)
    # Top listeyi 2'ye düşür → testuser (en düşük) listede olmaz; rütbe ZREVRANK'tan.
    monkeypatch.setattr(gam, "LEADERBOARD_TOP_N", 2)

    body = client.get("/leaderboard/data").get_json()
    assert [e["username"] for e in body["entries"]] == [a.username, b.username]
    assert body["in_list"] is False
    assert body["me"]["rank"] == 3  # ZREVRANK index 2 → rütbe 3


# ---------------------------------------------------------------------------
# Friends kapsamı — ZMSCORE birleşmesi
# ---------------------------------------------------------------------------

def test_friends_leaderboard_uses_zmscore(client, auth_user, make_users_bulk, monkeypatch):
    friend, stranger = make_users_bulk(2, prefix="f", rank_points=0)
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=friend.id,
                              status="accepted"))
    db.session.commit()
    fake = FakeRedis()
    fake.zadd(LB_ALLTIME_KEY, {friend.id: 500, auth_user.id: 100, stranger.id: 999})
    _use_fake_redis(monkeypatch, fake)

    body = client.get("/leaderboard/data?scope=friends").get_json()
    names = [e["username"] for e in body["entries"]]
    assert names == [friend.username, "testuser"]  # yabancı (stranger) listede DEĞİL
    assert body["no_friends"] is False


def test_friends_missing_redis_score_defaults_to_zero(client, auth_user, make_users_bulk, monkeypatch):
    friend, = make_users_bulk(1, prefix="fz", rank_points=0)
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=friend.id,
                              status="accepted"))
    db.session.commit()
    fake = FakeRedis()
    # Yalnızca arkadaşın skoru var; testuser sette YOK → ZMSCORE None → 0 sayılır.
    fake.zadd(LB_ALLTIME_KEY, {friend.id: 250})
    _use_fake_redis(monkeypatch, fake)

    body = client.get("/leaderboard/data?scope=friends").get_json()
    assert body["entries"][0]["username"] == friend.username  # skorlu önce
    assert body["entries"][-1]["username"] == "testuser"      # skorsuz (0) sonda


# ---------------------------------------------------------------------------
# Redis hatasında Postgres'e şeffaf düşüş (blueprint except yolu)
# ---------------------------------------------------------------------------

def test_route_falls_back_to_postgres_when_redis_raises(client, auth_user, make_users_bulk, monkeypatch):
    make_users_bulk(1, prefix="pg", rank_points=400)
    fake = FakeRedis()
    fake.fail_zrevrange = True  # Redis yolu patlasın
    _use_fake_redis(monkeypatch, fake)

    resp = client.get("/leaderboard/data")
    assert resp.status_code == 200
    body = resp.get_json()
    # Postgres yolu (rank_points sıralaması) çalıştı: en yüksek XP başta.
    assert body["entries"][0]["username"] == "pg0"
    assert body["in_list"] is True  # testuser Postgres'ten listede


# ---------------------------------------------------------------------------
# Yazma yolu — lb_sync_user / lb_rebuild / commit-sonrası flush
# ---------------------------------------------------------------------------

def test_lb_sync_user_writes_both_sets(app, make_user, monkeypatch):
    user = make_user("syncer", rank_points=700, weekly_xp=120, streak_count=4)
    fake = FakeRedis()
    monkeypatch.setattr(gam, "redis_client", fake)

    gam.lb_sync_user(user)
    expected_all = gam._lb_score(700, 4)
    expected_week = gam._lb_score(120, 4)
    assert fake.zmscore(LB_ALLTIME_KEY, [user.id]) == [float(expected_all)]
    assert fake.zmscore(LB_WEEKLY_KEY, [user.id]) == [float(expected_week)]


def test_lb_rebuild_loads_all_users_from_postgres(app, make_users_bulk, monkeypatch):
    u1, u2 = make_users_bulk(2, prefix="rb", rank_points=300)
    fake = FakeRedis()
    monkeypatch.setattr(gam, "redis_client", fake)

    gam.lb_rebuild()
    score = float(gam._lb_score(300, 0))
    assert fake.zmscore(LB_ALLTIME_KEY, [u1.id, u2.id]) == [score, score]


def test_award_xp_syncs_redis_after_commit(app, make_user, monkeypatch):
    user = make_user("awardee", rank_points=0, weekly_xp=0)
    fake = FakeRedis()
    monkeypatch.setattr(gam, "redis_client", fake)

    gam.award_xp(user.id, 50)
    assert fake.zmscore(LB_ALLTIME_KEY, [user.id]) == [None]  # commit ÖNCESI yazılmaz
    db.session.commit()  # after_commit → _flush_lb_dirty → lb_sync_user
    assert fake.zmscore(LB_ALLTIME_KEY, [user.id]) == [float(gam._lb_score(50, 0))]


def test_rollback_drops_dirty_no_redis_write(app, make_user, monkeypatch):
    user = make_user("roller", rank_points=0)
    fake = FakeRedis()
    monkeypatch.setattr(gam, "redis_client", fake)

    gam.award_xp(user.id, 50)
    db.session.rollback()  # after_rollback → _drop_lb_dirty
    db.session.commit()    # işaret düştü → Redis'e yazılmaz
    assert fake.zmscore(LB_ALLTIME_KEY, [user.id]) == [None]
