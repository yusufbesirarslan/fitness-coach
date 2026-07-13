"""Integration tests for the request hooks (app/hooks.py).

Üç güvenlik/oyunlaştırma katmanını sabitler:
- Origin/Referer tabanlı CSRF koruması (cookie-auth POST'ların saldırı yüzeyi)
- Per-request nonce'lu CSP başlığı (satır-içi script enjeksiyonuna karşı)
- update_streak: gün-sınırı mantığı + 7/14/30/60/100 milestone XP ödülleri

    python -m pytest tests/test_hooks.py -v
"""
from datetime import date, timedelta

from app.extensions import db
from app.models import Activity, User
from app.blueprints import auth as auth_bp
from app.services import cognito_service
from app.services.cognito_service import CognitoServiceError


def _csp_directives(response):
    header = response.headers.get("Content-Security-Policy", "")
    return {d.split(" ", 1)[0]: d for d in (p.strip() for p in header.split(";")) if d}


# ---------------------------------------------------------------------------
# CSRF — state-changing istekler Origin/Referer doğrulamasından geçmeli.
# ---------------------------------------------------------------------------

def test_csrf_post_without_origin_or_referer_rejected(raw_client):
    response = raw_client.post("/login", json={"username": "x", "password": "y"})
    assert response.status_code == 403


def test_csrf_cross_origin_post_rejected(raw_client):
    response = raw_client.post("/login", json={"username": "x", "password": "y"},
                               headers={"Origin": "http://evil.example"})
    assert response.status_code == 403


def test_csrf_cross_site_referer_rejected(raw_client):
    response = raw_client.post("/login", json={"username": "x", "password": "y"},
                               headers={"Referer": "http://evil.example/fake-form"})
    assert response.status_code == 403


def _with_token(raw_client, token="tok-abc"):
    """Oturuma synchronizer token tohumla ve eşleşen başlığı döndür."""
    with raw_client.session_transaction() as sess:
        sess["_csrf_token"] = token
    return {"X-CSRFToken": token}


def _reject_cognito_login(monkeypatch):
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", True)
    monkeypatch.setattr(
        cognito_service,
        "authenticate",
        lambda username, password: (_ for _ in ()).throw(
            CognitoServiceError("bad credentials", "NotAuthorizedException")),
    )


def test_csrf_same_origin_post_passes(raw_client, monkeypatch):
    # Aynı origin + geçerli token → CSRF kapısını geçer; yanlış şifre 401'i view'dan gelir.
    _reject_cognito_login(monkeypatch)
    headers = {"Origin": "http://localhost", **_with_token(raw_client)}
    response = raw_client.post("/login", json={"username": "x", "password": "y"},
                               headers=headers)
    assert response.status_code == 401


def test_csrf_same_origin_referer_passes(raw_client, monkeypatch):
    _reject_cognito_login(monkeypatch)
    headers = {"Referer": "http://localhost/login", **_with_token(raw_client)}
    response = raw_client.post("/login", json={"username": "x", "password": "y"},
                               headers=headers)
    assert response.status_code == 401


def test_csrf_valid_origin_but_missing_token_rejected(raw_client):
    # Layer 2: Origin doğru olsa bile token yoksa reddedilir (defense-in-depth).
    response = raw_client.post("/login", json={"username": "x", "password": "y"},
                               headers={"Origin": "http://localhost"})
    assert response.status_code == 403


def test_csrf_valid_origin_but_wrong_token_rejected(raw_client):
    _with_token(raw_client, token="gercek-token")
    response = raw_client.post("/login", json={"username": "x", "password": "y"},
                               headers={"Origin": "http://localhost",
                                        "X-CSRFToken": "yanlis-token"})
    assert response.status_code == 403


def test_csrf_token_injected_into_page(client):
    # Token şablona <meta name="csrf-token"> olarak sızdırılmalı (frontend okur).
    html = client.get("/login").get_data(as_text=True)
    assert 'name="csrf-token"' in html


def test_csrf_does_not_apply_to_get(raw_client):
    assert raw_client.get("/login").status_code == 200


# ---------------------------------------------------------------------------
# CSP — başlık her yanıtta, nonce per-request, 'unsafe-inline' script'te yok.
# ---------------------------------------------------------------------------

def test_csp_header_present_and_locked_down(client):
    directives = _csp_directives(client.get("/health"))
    assert directives["default-src"] == "default-src 'self'"
    assert "'nonce-" in directives["script-src"]
    assert "'unsafe-inline'" not in directives["script-src"]
    # Satır-içi nitelik-handler'ları (onclick=...) tamamen yasak — tüm on*
    # işleyicileri data-action delegasyonuna (static/actions.js) taşındı.
    assert directives["script-src-attr"] == "script-src-attr 'none'"
    assert directives["frame-ancestors"] == "frame-ancestors 'none'"
    assert directives["object-src"] == "object-src 'none'"
    # style-src-elem: <style> blokları nonce ister, 'unsafe-inline' YOK — XSS ile
    # enjekte edilen <style> çalışmaz. Dinamik style="" nitelikleri style-src-attr
    # üzerinden 'unsafe-inline' ile yaşamaya devam eder (progress-bar genişlikleri).
    assert "'nonce-" in directives["style-src-elem"]
    assert "'unsafe-inline'" not in directives["style-src-elem"]
    assert directives["style-src-attr"] == "style-src-attr 'unsafe-inline'"


def test_csp_inline_style_blocks_match_header_nonce_when_present(client):
    # Sayfa inline <style> bloğu kullanıyorsa başlıktaki style-src-elem nonce'u ile
    # imzalanmalı; yalnızca harici CSS kullanan sayfalarda inline style bloğu olmaz.
    response = client.get("/login")
    html = response.get_data(as_text=True)
    style_elem = _csp_directives(response)["style-src-elem"]
    nonce = style_elem.split("'nonce-")[1].split("'")[0]
    assert nonce
    if "<style" in html:
        assert f'<style nonce="{nonce}"' in html
    else:
        assert 'rel="stylesheet"' in html


def test_csp_nonce_changes_per_request(client):
    first = _csp_directives(client.get("/health"))["script-src"]
    second = _csp_directives(client.get("/health"))["script-src"]
    assert first != second


def test_csp_nonce_in_header_matches_template(client):
    # Şablondaki <script nonce="..."> başlıktaki nonce ile imzalanmalı,
    # yoksa tarayıcı script'i bloklar (CLAUDE.md kuralı).
    response = client.get("/login")
    script_src = _csp_directives(response)["script-src"]
    nonce = script_src.split("'nonce-")[1].split("'")[0]
    assert nonce and f'nonce="{nonce}"' in response.get_data(as_text=True)


def test_csp_jsdelivr_pinned_to_exact_files(client):
    # SEC1: jsdelivr artık geniş host (cdn.jsdelivr.net) DEĞİL — yalnızca SRI ile
    # sabitlenmiş tam dosyalar script-src'de listeli. Kök/keyfi-yol yükleme kapalı.
    tokens = _csp_directives(client.get("/health"))["script-src"].split()
    assert "https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js" in tokens
    assert "https://cdn.jsdelivr.net/npm/html5-qrcode@2.3.8/html5-qrcode.min.js" in tokens
    assert "https://cdn.jsdelivr.net" not in tokens  # geniş kök host kaynağı YOK


def test_chart_js_tag_carries_sri(client, make_user, login):
    # SEC1: dashboard'daki chart.js <script> etiketi sabit sürüm + integrity +
    # crossorigin taşımalı (tarayıcı bütünlüğü doğrulasın).
    make_user("sriuser", profile_complete=True)
    login("sriuser")
    body = client.get("/").get_data(as_text=True)
    assert 'src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"' in body
    assert 'integrity="sha384-' in body and 'crossorigin="anonymous"' in body


# ---------------------------------------------------------------------------
# update_streak — gün-sınırı mantığı ve milestone ödülleri.
# ---------------------------------------------------------------------------

def _fresh(user_id):
    db.session.expire_all()
    return db.session.get(User, user_id)


def test_streak_increments_on_consecutive_day(client, make_user, login):
    user = make_user("alice", last_login=date.today() - timedelta(days=1), streak_count=3)
    login("alice")
    client.get("/health")
    user = _fresh(user.id)
    assert user.streak_count == 4
    assert user.last_login == date.today()


def test_streak_resets_after_gap(client, make_user, login):
    user = make_user("bob", last_login=date.today() - timedelta(days=3), streak_count=10)
    login("bob")
    client.get("/health")
    assert _fresh(user.id).streak_count == 1


def test_streak_first_login_starts_at_one(client, make_user, login):
    user = make_user("carol")  # last_login=None
    login("carol")
    client.get("/health")
    assert _fresh(user.id).streak_count == 1


def test_streak_same_day_counts_once(client, make_user, login):
    user = make_user("dave", last_login=date.today() - timedelta(days=1), streak_count=1)
    login("dave")
    client.get("/health")
    client.get("/health")
    assert _fresh(user.id).streak_count == 2


def test_streak_milestone_awards_xp_and_activity(client, make_user, login):
    user = make_user("eve", last_login=date.today() - timedelta(days=1),
                     streak_count=6, rank_points=0, weekly_xp=0)
    login("eve")
    client.get("/health")
    user = _fresh(user.id)
    assert user.streak_count == 7
    assert user.rank_points == 14  # streak * 2
    assert user.weekly_xp == 14
    milestone = Activity.query.filter_by(user_id=user.id, activity_type="streak_milestone").first()
    assert milestone is not None


def test_streak_non_milestone_awards_nothing(client, make_user, login):
    user = make_user("frank", last_login=date.today() - timedelta(days=1),
                     streak_count=3, rank_points=0)
    login("frank")
    client.get("/health")
    assert _fresh(user.id).rank_points == 0


# ---------------------------------------------------------------------------
# Hata sayfaları
# ---------------------------------------------------------------------------

def test_unknown_path_renders_404(client):
    response = client.get("/boyle-bir-sayfa-yok")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Haftalık rollover throttle — Redis varsa fleet-geneli, yoksa süreç-içi (#17).
# ---------------------------------------------------------------------------

class _ThrottleRedis:
    """NX+EX SET semantiği taşıyan minimal sahte Redis (throttle testleri için)."""
    def __init__(self, fail=False):
        self.store = {}
        self.fail = fail

    def set(self, key, val, nx=False, ex=None):
        if self.fail:
            raise RuntimeError("redis down")
        if nx and key in self.store:
            return None
        self.store[key] = val
        return True


def test_rollover_throttle_redis_shared_across_workers(monkeypatch):
    # Redis varsa throttle FLEET-GENELİ: iki ayrı çağrı (iki worker'ı temsil eder)
    # aynı NX anahtarını paylaşır → yalnızca biri kontrol çalıştırır.
    from app import hooks
    from datetime import datetime
    monkeypatch.setattr("app.extensions.redis_client", _ThrottleRedis())
    now = datetime.utcnow()
    assert hooks._rollover_throttle_passed(now) is True
    assert hooks._rollover_throttle_passed(now) is False


def test_rollover_throttle_falls_back_to_in_memory_when_no_redis(monkeypatch):
    from app import hooks
    from app.services import gamification
    from datetime import datetime
    monkeypatch.setattr("app.extensions.redis_client", None)
    gamification._last_rollover_check[0] = None
    now = datetime.utcnow()
    assert hooks._rollover_throttle_passed(now) is True    # ilk: kontrol et
    assert hooks._rollover_throttle_passed(now) is False   # 5 dk içinde: atla


def test_rollover_throttle_redis_error_falls_back_to_in_memory(monkeypatch):
    from app import hooks
    from app.services import gamification
    from datetime import datetime
    monkeypatch.setattr("app.extensions.redis_client", _ThrottleRedis(fail=True))
    gamification._last_rollover_check[0] = None
    now = datetime.utcnow()
    assert hooks._rollover_throttle_passed(now) is True    # Redis patladı → süreç-içi yedek


def test_weekly_rollover_receives_istanbul_now(app, monkeypatch):
    # `app` fixture: hook artık rollover'dan sonra oturum süpürmesi de yapıyor
    # (M1) ve bu DB'ye dokunur — before_request olarak zaten HER ZAMAN app
    # context'inde çalışır, test de öyle koşmalı.
    from datetime import datetime
    from app import hooks
    from app.timeutil import APP_TZ

    istanbul_now = datetime(2026, 7, 6, 1, 30, tzinfo=APP_TZ)
    captured = []

    monkeypatch.setattr(hooks, "_rollover_throttle_passed", lambda now: True)
    monkeypatch.setattr(hooks, "app_now", lambda: istanbul_now, raising=False)
    monkeypatch.setattr(hooks, "run_weekly_rollover", lambda now: captured.append(now))

    hooks.maybe_weekly_rollover()

    assert captured == [istanbul_now]


# ---------------------------------------------------------------------------
# M1 — süresi geçmiş Cognito oturumlarının süpürülmesi ZAMANLANMIŞ olmalı.
#
# purge_expired'ın tek çağıranı weekly-reset CLI'ıydı ve o da yalnızca bir EC2
# host cron'unun VAR OLDUĞUNU VARSAYIYORDU; repo'da/compose'da/deploy'da o cron'u
# kuran hiçbir şey yok. Cron eksikse tablo sınırsız büyür ve süresi geçmiş
# ŞİFRELİ refresh token'lar saklanmaya devam eder.
# ---------------------------------------------------------------------------

def test_rollover_hook_also_purges_expired_sessions(app, monkeypatch):
    from app import hooks

    calls = []
    monkeypatch.setattr(hooks, "_rollover_throttle_passed", lambda now: True)
    monkeypatch.setattr(hooks, "_purge_throttle_passed", lambda now: True)
    monkeypatch.setattr(hooks, "run_weekly_rollover", lambda now: None)

    from app.services import session_store
    monkeypatch.setattr(session_store, "purge_expired", lambda: calls.append(1))

    hooks.maybe_weekly_rollover()
    assert calls == [1]


def test_purge_skipped_while_throttle_held(app, monkeypatch):
    from app import hooks

    calls = []
    monkeypatch.setattr(hooks, "_rollover_throttle_passed", lambda now: True)
    monkeypatch.setattr(hooks, "_purge_throttle_passed", lambda now: False)
    monkeypatch.setattr(hooks, "run_weekly_rollover", lambda now: None)

    from app.services import session_store
    monkeypatch.setattr(session_store, "purge_expired", lambda: calls.append(1))

    hooks.maybe_weekly_rollover()
    assert calls == []


def test_purge_runs_even_when_rollover_raises(app, monkeypatch):
    """Süpürme rollover'a BAĞLI olmamalı: rollover patlasa da oturum satırları
    süpürülmeye devam etmeli (ayrı sorumluluk, ayrı throttle)."""
    from app import hooks

    calls = []
    monkeypatch.setattr(hooks, "_rollover_throttle_passed", lambda now: True)
    monkeypatch.setattr(hooks, "_purge_throttle_passed", lambda now: True)

    def boom(now):
        raise RuntimeError("rollover patladı")

    monkeypatch.setattr(hooks, "run_weekly_rollover", boom)

    from app.services import session_store
    monkeypatch.setattr(session_store, "purge_expired", lambda: calls.append(1))

    hooks.maybe_weekly_rollover()
    assert calls == [1]


def test_purge_throttle_uses_redis_nx_lock_fleet_wide(app, monkeypatch):
    """Redis varsa süpürme fleet-genelinde günde bir kez çalışmalı."""
    from datetime import datetime
    from app import hooks
    import app.extensions as ext

    seen = {}

    class _FakeRedis:
        def set(self, key, value, nx=False, ex=None):
            seen["key"], seen["nx"], seen["ex"] = key, nx, ex
            return True if key not in seen.get("held", ()) else None

    monkeypatch.setattr(ext, "redis_client", _FakeRedis())
    assert hooks._purge_throttle_passed(datetime.utcnow()) is True
    assert seen["key"] == "fitx:session_purge"
    assert seen["nx"] is True
    assert seen["ex"] == 86400
