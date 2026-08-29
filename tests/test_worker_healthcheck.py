"""Worker sağlık sözleşmesi (scripts/worker_healthcheck.py).

Düzeltilen arıza: web ve worker AYNI imajı kullanır, worker compose'da kendi
`healthcheck:`ine sahip DEĞİLDİ ve imaj düzeyindeki web healthcheck'ini
(`http://127.0.0.1:5000/health`) miras alıyordu. worker `python worker.py` koşar
ve 5000'de hiçbir şey dinlemez → her probe "Connection refused". Prod kanıtı
(2026-08-29): status=running, restarts=0, exit=0, OOMKilled=false, PID 1 =
`python worker.py`, RQ state=idle, heartbeat TTL=116/120 — ama health=unhealthy
ve failingstreak=988 (konteyner ömrü boyunca TEK bir probe bile geçmemiş).

    python -m pytest tests/test_worker_healthcheck.py -v
"""
import subprocess
import sys
from pathlib import Path

import pytest

from app import jobs
from scripts import worker_healthcheck as hc

HOSTNAME = "worker-container-1"
RQ_KEY = "rq:worker:d1cda1fda98b49ca845e39807f77c6c1"


class _FakeRedis:
    """Probe'un kullandığı dar Redis yüzeyi (decode_responses=True gibi str döner)."""

    def __init__(self, keys=(), members=(), hashes=None):
        self.keys = set(keys)
        self.members = set(members)
        self.hashes = hashes or {}

    def exists(self, key):
        return 1 if key in self.keys else 0

    def smembers(self, key):
        return set(self.members) if key == hc.RQ_WORKERS_SET else set()

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)


def _healthy_redis():
    """Sağlıklı prod durumu: heartbeat taze + BU konteynerin RQ worker'ı canlı."""
    return _FakeRedis(
        keys={hc.WORKER_HEARTBEAT_KEY, RQ_KEY},
        members={RQ_KEY},
        hashes={RQ_KEY: {"hostname": HOSTNAME, "state": "idle"}},
    )


@pytest.fixture
def container(monkeypatch, tmp_path):
    """PID 1 = `python worker.py` olan, hostname'i bilinen bir konteyner."""
    cmdline = tmp_path / "cmdline"
    cmdline.write_bytes(b"python\x00worker.py\x00")
    monkeypatch.setattr(hc, "PID1_CMDLINE", str(cmdline))
    monkeypatch.setattr(hc.socket, "gethostname", lambda: HOSTNAME)

    def use(redis_conn):
        monkeypatch.setattr(hc, "connect", lambda: redis_conn)

    return use


# ── (1) Süreç canlılığı ─────────────────────────────────────────────────────

def test_process_alive_true_for_worker_entrypoint(tmp_path):
    p = tmp_path / "cmdline"
    p.write_bytes(b"python\x00worker.py\x00")
    assert hc.worker_process_alive(str(p)) is True


def test_process_alive_false_for_a_different_main_process(tmp_path):
    # compose `command:` sağlık sözleşmesi gözden geçirilmeden değişirse
    # SESSİZCE geçmesin.
    p = tmp_path / "cmdline"
    p.write_bytes(b"gunicorn\x00--config\x00gunicorn.conf.py\x00starter:app\x00")
    assert hc.worker_process_alive(str(p)) is False


def test_process_alive_false_when_proc_unreadable(tmp_path):
    assert hc.worker_process_alive(str(tmp_path / "yok")) is False


# ── (2) Heartbeat tazeliği ──────────────────────────────────────────────────

def test_reports_healthy_for_a_fresh_heartbeat(container):
    container(_healthy_redis())
    healthy, reason = hc.check()
    assert healthy is True, reason
    assert hc.main() == 0


def test_reports_unhealthy_for_a_missing_or_expired_heartbeat(container):
    # Bayat = anahtarın TTL'i dolmuş = EXISTS 0. Ayrı zaman damgası yok.
    conn = _healthy_redis()
    conn.keys.discard(hc.WORKER_HEARTBEAT_KEY)
    container(conn)
    healthy, reason = hc.check()
    assert healthy is False
    assert "heartbeat" in reason
    assert hc.main() == 1


def test_expired_heartbeat_flips_a_healthy_worker_to_unhealthy(container):
    conn = _healthy_redis()
    container(conn)
    assert hc.main() == 0
    conn.keys.discard(hc.WORKER_HEARTBEAT_KEY)  # TTL doldu
    assert hc.main() == 1


# ── (3) RQ iş döngüsü canlılığı ─────────────────────────────────────────────

def test_wedged_rq_loop_is_unhealthy_even_while_the_heartbeat_thread_lives(container):
    # ASIL SEBEP bu yarının var olması: heartbeat'i worker.py'de BAĞIMSIZ bir
    # daemon thread yazar. RQ ana döngüsü kilitlenirse thread yazmaya devam eder;
    # yalnızca heartbeat'e bakan bir kontrol, kuyruk birikirken "sağlıklı" derdi.
    conn = _healthy_redis()
    conn.keys.discard(RQ_KEY)          # RQ'nun kendi anahtarının TTL'i doldu
    container(conn)
    healthy, reason = hc.check()
    assert healthy is False
    assert "RQ" in reason
    assert hc.main() == 1


def test_another_containers_rq_worker_does_not_count(container):
    # heartbeat anahtarı GLOBAL'dir. `--scale worker=2` altında ölü bir worker,
    # yaşayan worker'ın heartbeat'ini okuyup sağlıklı görünmemeli.
    conn = _healthy_redis()
    conn.hashes[RQ_KEY] = {"hostname": "baska-konteyner"}
    container(conn)
    assert hc.main() == 1


def test_stale_registry_membership_without_a_live_key_does_not_count(container):
    # `rq:workers` bir küme ve bayat üye barındırabilir; otorite worker
    # anahtarının KENDİSİDİR.
    conn = _FakeRedis(
        keys={hc.WORKER_HEARTBEAT_KEY},
        members={RQ_KEY},
        hashes={RQ_KEY: {"hostname": HOSTNAME}},
    )
    container(conn)
    assert hc.main() == 1


def test_empty_registry_is_unhealthy(container):
    container(_FakeRedis(keys={hc.WORKER_HEARTBEAT_KEY}))
    assert hc.main() == 1


# ── Redis yokluğu / hataları ────────────────────────────────────────────────

def test_absent_redis_is_unhealthy_not_fail_open(container, monkeypatch):
    # app.jobs.worker_alive() Redis yoksa None döner (UYGULAMA tarafında bilinçli
    # fail-open: worker'ın yokluğu isteği bozmaz, işler satır-içine düşer).
    # Worker'ın KENDİ sağlığında bu doğru değildir — worker.py de exit 1 verir.
    monkeypatch.setenv("REDIS_URL", "")
    assert hc.connect() is None
    assert hc.main() == 1


def test_redis_error_is_unhealthy(container):
    class _BoomRedis:
        def exists(self, *a, **k):
            raise RuntimeError("down")

        def smembers(self, *a, **k):
            raise RuntimeError("down")

    container(_BoomRedis())
    assert hc.main() == 1


def test_healthcheck_never_raises_on_unexpected_failure(monkeypatch):
    # Docker healthcheck sözleşmesi bir ÇIKIŞ KODUDUR; traceback ile ölmek
    # yorumlanamaz bir sağlık durumu üretir.
    monkeypatch.setattr(hc, "check", lambda: (_ for _ in ()).throw(ValueError("boom")))
    assert hc.main() == 1


# ── Her yarı GEREKLİ ────────────────────────────────────────────────────────

def test_fresh_redis_state_alone_is_not_enough(container, monkeypatch, tmp_path):
    container(_healthy_redis())
    p = tmp_path / "cmdline"
    p.write_bytes(b"sleep\x00infinity\x00")
    monkeypatch.setattr(hc, "PID1_CMDLINE", str(p))
    assert hc.main() == 1


# ── Protokol paylaşımı ve probe maliyeti ────────────────────────────────────

def test_reuses_the_existing_heartbeat_key_rather_than_a_second_protocol():
    # Probe app.jobs'u IMPORT ETMEZ (aşağıdaki maliyet testine bak), bu yüzden
    # anahtar adı burada sabittir. Sürüklenme prod'da değil BURADA patlamalı.
    assert hc.WORKER_HEARTBEAT_KEY == jobs.WORKER_HEARTBEAT_KEY


def test_probe_never_writes_the_heartbeat_it_reads():
    import ast
    import inspect

    called = {
        node.func.attr
        for node in ast.walk(ast.parse(inspect.getsource(hc)))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "setex" not in called
    assert "set" not in called


def test_probe_does_not_import_the_flask_application():
    """Probe'un maliyet sözleşmesi — bu testin varlık sebebi ÖLÇÜLMÜŞ bir arıza.

    `from app.jobs import worker_alive` `app` paketini çeker: app/__init__.py →
    app.extensions → openai, alembic, SQLAlchemy, flask-limiter. Ölçüldü: 23.0s
    ve 2109 modül. İmaj PYTHONDONTWRITEBYTECODE=1 ile koşar (önbellek yok) ve
    compose timeout'u 10s'dir → her probe timeout'a düşer ve konteyner yine
    kalıcı `unhealthy` olurdu; yani düzeltilen arıza BAŞKA bir sebeple geri
    gelirdi. Ayrıca 512m cgroup'ta 30 saniyede bir ikinci bir tam import
    grafiği doğardı.
    """
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; import scripts.worker_healthcheck; "
         "print(any(m == 'app' or m.startswith('app.') for m in sys.modules)); "
         "print(len(sys.modules))"],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    imported_app, module_count = proc.stdout.split()
    assert imported_app == "False", "probe Flask uygulamasini import ediyor"
    assert int(module_count) < 400, module_count
