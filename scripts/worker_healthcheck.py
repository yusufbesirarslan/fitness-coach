#!/usr/bin/env python
"""Worker konteyneri için canlılık kontrolü (Docker healthcheck).

NEDEN AYRI BİR KONTROL: web ve worker AYNI Dockerfile'dan üretilir, dolayısıyla
worker imaj düzeyindeki `HEALTHCHECK`'i — `http://127.0.0.1:5000/health` — miras
alır. Ama worker `python worker.py` koşar ve HİÇBİR HTTP sunucusu açmaz; 5000
portunda dinleyen yoktur. Sonuç kalıcı bir YANLIŞ-NEGATİF'tir: worker RQ tarafında
tamamen sağlıklıyken Docker onu ilelebet `unhealthy` işaretler (prod'da 2026-08-29
itibarıyla failingstreak=988, restarts=0, exit=0 — tek bir probe bile hiç
geçmemiş). Bu, gerçek bir worker arızasını görünmez kılar: gürültü alarmı bastırır.

SÖZLEŞME — worker ÜÇ koşul BİRLİKTE sağlanırsa sağlıklıdır:

  1. Konteynerin ana süreci worker giriş noktasıdır (PID 1 = worker.py).
  2. Redis destekli worker heartbeat'i TAZE'dir (worker.py'nin yazdığı anahtar).
  3. BU konteynerin RQ worker'ı RQ'nun KENDİ kayıt defterinde canlıdır.

(3) neden gerekli: (2)'yi yazan şey worker.py'deki BAĞIMSIZ bir daemon thread'dir
(worker.py `_start_heartbeat`), `worker.work()` döngüsünün kendisi değil. RQ ana
döngüsü kilitlenir de thread yaşamaya devam ederse, yalnızca (2)'ye bakan bir
kontrol kuyruk sonsuza dek birikirken "sağlıklı" derdi. RQ ise kendi worker
anahtarının TTL'ini İŞ DÖNGÜSÜNÜN İÇİNDEN tazeler; o anahtarın canlılığı gerçek
döngü canlılığıdır. Ayrıca (2)'nin anahtarı GLOBAL'dir (konteyner başına değil):
`--scale worker=2` altında ölü bir worker, yaşayan worker'ın heartbeat'ini okuyup
sağlıklı görünürdü. RQ kayıt defterindeki `hostname` alanı konteyner hostname'idir,
yani (3) BU konteyneri ayırt eder.

YENİ BİR PROTOKOL İCAT EDİLMEZ: hem (2) hem (3) üretimde ZATEN yazılan anahtarları
OKUR — sırasıyla app/jobs.record_worker_heartbeat() ve RQ'nun kendi kayıt defteri.

NEDEN `app.jobs.worker_alive()` ÇAĞRILMIYOR: `from app.jobs import ...` `app`
paketini import eder, yani app/__init__.py → app.extensions → openai, alembic,
SQLAlchemy, flask-limiter... ölçüldü: 23.0s ve 2109 modül. İmaj
PYTHONDONTWRITEBYTECODE=1 ile koşar, yani bu maliyet HER probe'da yeniden ödenir
ve healthcheck timeout'u 10s'dir. Yani "mevcut yardımcıyı çağır" en temiz görünen
seçenek, düzelttiğimiz arızayı (kalıcı unhealthy) BAŞKA bir sebeple geri
getirirdi — üstelik worker'ın 512m cgroup'unda 30 saniyede bir ikinci bir tam
import grafiği doğurarak. Bunun yerine tek bir Redis EXISTS yapılır ve anahtar
adları burada sabit olarak tutulur; tests/test_worker_healthcheck.py bu sabitlerin
app.jobs ile AYNI kaldığını pinler (sürüklenme testte patlar, prod'da değil).

Çıkış kodu: 0 sağlıklı, 1 sağlıksız. Docker healthcheck sözleşmesi budur.
"""
import os
import socket
import sys

# Konteynerde `python worker.py` PID 1'dir (compose `command:` doğrudan exec
# edilir, araya shell girmez).
WORKER_ENTRYPOINT = "worker.py"
PID1_CMDLINE = "/proc/1/cmdline"

# app/jobs/__init__.py ile AYNI olmak ZORUNDA — testte pinlenir.
WORKER_HEARTBEAT_KEY = "fitx:worker:alive"
# RQ 2.10.0 kayıt defteri (requirements.txt'te pinli).
RQ_WORKERS_SET = "rq:workers"

# Probe'un toplam bütçesi 10s; Redis askıda kalırsa Docker'ın probe'u öldürmesini
# beklemek yerine kendimiz hızlı ve yorumlanabilir biçimde düşelim.
REDIS_TIMEOUT_SECONDS = 3


def worker_process_alive(cmdline_path=None):
    """PID 1 worker giriş noktası mı? Healthcheck yalnızca konteyner ayaktayken
    koşar, yani bu "doğru süreci mi ölçüyorum" doğrulamasıdır: compose `command:`
    sağlık sözleşmesi gözden geçirilmeden değişirse sessizce geçmesin."""
    # Modül sabitine ÇAĞRI ANINDA bak (varsayılan argümana bağlama): sabit
    # testlerde monkeypatch'lenir, erken bağlanırsa yama etkisiz kalır.
    path = PID1_CMDLINE if cmdline_path is None else cmdline_path
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return False
    # /proc/<pid>/cmdline argümanları NUL ile ayırır ve NUL ile biter.
    argv = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
    return any(arg.endswith(WORKER_ENTRYPOINT) for arg in argv)


def connect():
    """REDIS_URL'den decode_responses=True bağlantı. URL yoksa None.

    Redis'siz bir worker kuyruk tüketemez — worker.py de bu durumda exit 1 verir —
    dolayısıyla worker'ın KENDİ sağlığında "Redis yok" SAĞLIKSIZ demektir.
    (app.jobs.worker_alive() burada None döndürüp fail-open olur; bu, isteği bozmama
    amaçlı UYGULAMA tarafı bir karardır, worker'ın kendi sağlığı için değil.)"""
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    import redis
    return redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=REDIS_TIMEOUT_SECONDS,
        socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
    )


def heartbeat_fresh(conn):
    """worker.py'nin yazdığı heartbeat anahtarı duruyor mu? Tazelik anahtarın
    TTL'i ile ifade edilir — ayrı bir zaman damgası karşılaştırması YOK."""
    return bool(conn.exists(WORKER_HEARTBEAT_KEY))


def rq_worker_registered(conn, hostname=None):
    """BU konteynerin RQ worker'ı kayıt defterinde CANLI mı?

    `rq:workers` bir küme ve bayat üyeler barındırabilir; otorite worker
    anahtarının KENDİSİNİN var olmasıdır (TTL'i RQ iş döngüsünden tazelenir).
    Konteyner hostname'i ile eşleştirmek `--scale worker=2` altında bu konteyneri
    ayırt eder."""
    hostname = socket.gethostname() if hostname is None else hostname
    for key in conn.smembers(RQ_WORKERS_SET) or ():
        if conn.exists(key) and conn.hget(key, "hostname") == hostname:
            return True
    return False


def check():
    """(healthy: bool, reason: str) döndür. Üç yarı da KISA DEVRE YAPMADAN
    değerlendirilir: sebep birleşimi arızayı tek bakışta okunur kılar."""
    reasons = []
    if not worker_process_alive():
        reasons.append("PID 1 worker giris noktasi degil (beklenen: %s)"
                       % WORKER_ENTRYPOINT)
    conn = connect()
    if conn is None:
        reasons.append("REDIS_URL yok — worker kuyruk tuketemez")
    else:
        if not heartbeat_fresh(conn):
            reasons.append("worker heartbeat yok ya da bayat (%s)"
                           % WORKER_HEARTBEAT_KEY)
        if not rq_worker_registered(conn):
            reasons.append("bu konteynerin RQ worker'i kayit defterinde canli degil")
    if reasons:
        return False, "; ".join(reasons)
    return True, "worker sureci ayakta, heartbeat taze, RQ worker'i kayitli"


def main():
    try:
        healthy, reason = check()
    except Exception as e:  # Redis/import hatası da sağlıksızdır — sessiz geçme.
        print("worker healthcheck hatasi: %s: %s" % (type(e).__name__, e),
              file=sys.stderr)
        return 1
    print(reason, file=sys.stdout if healthy else sys.stderr)
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
