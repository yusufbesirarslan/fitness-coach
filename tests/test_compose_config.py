"""docker-compose.yml sözleşmesi (M2).

Docker'ın varsayılan json-file log sürücüsünün max-size'ı YOKTUR. gunicorn
--access-logfile - ve observability.log_request istek BAŞINA satır yazar → log
sınırsız büyür. Tek EC2 host'unda sabit EBS diskiyle bu, eninde sonunda diski
doldurur ve web + redis + deploy'u BİRLİKTE düşürür; mem_limit disk için hiçbir
şey yapmaz. Rotasyon sözleşmesini teste bağla.

    python -m pytest tests/test_compose_config.py -v
"""
import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

COMPOSE = Path("docker-compose.yml")


@pytest.fixture(scope="module")
def services():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def test_compose_parses_and_has_expected_services(services):
    # worker (Sprint 4 WS8): AI iş kuyruğunu tüketen ayrı süreç (aynı imaj).
    assert set(services) == {"web", "redis", "worker"}


@pytest.mark.parametrize("service", ["web", "redis", "worker"])
def test_log_rotation_configured(services, service):
    logging = services[service].get("logging")
    assert logging, f"{service}: logging bloğu YOK → sınırsız log → dolu disk"
    assert logging["driver"] == "json-file"
    options = logging["options"]
    assert options.get("max-size"), f"{service}: max-size YOK"
    assert options.get("max-file"), f"{service}: max-file YOK"


@pytest.mark.parametrize("service", ["web", "redis", "worker"])
def test_ports_bound_to_loopback_only(services, service):
    # Regresyon kapısı: servisler internete AÇILMAMALI (host nginx tek giriş).
    # worker hiç port yayınlamaz (yalnızca Redis'e giden istemci) → döngü boş, geçer.
    for mapping in services[service].get("ports", []):
        assert str(mapping).startswith("127.0.0.1:"), \
            f"{service}: {mapping} loopback'e bağlı değil"


@pytest.mark.parametrize("service", ["web", "redis", "worker"])
def test_memory_limit_set(services, service):
    assert services[service].get("mem_limit")


# --- Production image immutability (deploy hardening PR1, finding 8) ---------
#
# `redis:alpine` is a MUTABLE tag: the same string resolves to different bytes
# over time, so `docker compose build/up` on the production host can silently
# introduce an unreviewed third-party image between two deploys of the exact
# same application SHA. Every externally sourced production image must name an
# explicit version AND pin the immutable content digest.

_IMMUTABLE_IMAGE_RE = re.compile(r"[^:@\s]+:[^@\s]+@sha256:[0-9a-f]{64}")


def test_every_external_production_image_is_versioned_and_digest_pinned(services):
    violations = {
        name: service["image"]
        for name, service in services.items()
        if service.get("image") is not None
        and not _IMMUTABLE_IMAGE_RE.fullmatch(service["image"])
    }
    assert violations == {}


def test_redis_pin_matches_the_major_production_actually_runs(services):
    # Pinning must not silently jump a major -- in EITHER direction. The
    # retired guard demanded 7.4.11 while the host had been serving
    # `redis:alpine`, which has resolved to Redis 8 for a long time; the
    # persisted volume therefore holds an RDB written in format 14. Redis 7.4
    # refuses that file outright -- "Can't handle RDB format version 14",
    # confirmed by running 7.4.11 against a read-only copy of the production
    # volume -- so the downgrade was a guaranteed crash loop on the first
    # deploy after it landed, not a theoretical risk.
    #
    # Anchored on the major rather than the exact patch so a security bump
    # inside 8.x is a one-line change, while a major move stays deliberate.
    image = services["redis"]["image"]
    assert image.startswith("redis:8."), image
    assert not image.startswith("redis:7."), image


def test_only_redis_is_an_external_production_image(services):
    # web/worker build the exact local context; nothing else may appear as a
    # third-party image without passing the guard above.
    assert {
        name for name, service in services.items() if service.get("image") is not None
    } == {"redis"}


# --- Worker health contract (worker unhealthy false-negative fix) ------------
#
# web ve worker AYNI Dockerfile'dan üretilir. Dockerfile imaj düzeyinde bir
# HEALTHCHECK tanımlar: `http://127.0.0.1:5000/health`. Bu web için doğrudur —
# gunicorn orada dinler. worker ise `python worker.py` koşar ve HİÇBİR HTTP
# sunucusu açmaz, dolayısıyla miras alınan probe konteyner ömrü boyunca
# "Connection refused" ile düşer. Prod kanıtı (2026-08-29): worker
# status=running, restarts=0, exit=0, OOMKilled=false, RQ state=idle, heartbeat
# TTL=116/120 — ama health=unhealthy, failingstreak=988. Kalıcı bir yanlış
# negatif, gerçek bir worker arızasını gürültünün içinde görünmez kılar.

WORKER_HEALTHCHECK_SCRIPT = Path("scripts/worker_healthcheck.py")


def test_worker_does_not_inherit_the_web_only_http_healthcheck(services):
    healthcheck = services["worker"].get("healthcheck")
    assert healthcheck, (
        "worker'ın kendi healthcheck'i YOK → imajdaki web HTTP probe'unu miras "
        "alır → kalıcı unhealthy"
    )
    probe = " ".join(healthcheck["test"])
    assert "5000" not in probe, probe
    assert "/health" not in probe, probe
    assert "urllib" not in probe, probe


def test_worker_healthcheck_runs_the_shipped_worker_health_module(services):
    probe = services["worker"]["healthcheck"]["test"]
    assert probe[0] == "CMD", "CMD-SHELL gereksiz — araya shell sokma"
    # `-m` ŞART. Script'i YOL ile çağırmak sys.path[0]'i /app/scripts yapar,
    # /app'i DEĞİL — `import app.jobs` o zaman ModuleNotFoundError verir ve probe
    # her seferinde exit 1 döner. Yani düzeltilen yanlış-negatif, sessizce
    # kendini tekrar ederdi. Bunu sözleşmeye bağla.
    assert probe[1:] == ["python", "-m", "scripts.worker_healthcheck"], probe
    assert WORKER_HEALTHCHECK_SCRIPT.is_file(), "probe var olmayan bir modülü çağırıyor"


def test_worker_healthcheck_module_resolves_when_run_the_way_docker_runs_it():
    # Yukarıdaki argv'yi GERÇEKTEN çalıştır: probe'un import zinciri konteynerde
    # çözülüyor mu? Docker WORKDIR /app'ten koşar; burada depo kökünden koşuyoruz.
    # Ortam hermetik tutulur (REDIS_URL="" → heartbeat okunamaz → exit 1);
    # kanıtlanan şey ÇIKIŞ KODU değil, modülün import edilip ÇALIŞMASIDIR.
    env = dict(os.environ)
    env.update({
        "REDIS_URL": "",
        "DATABASE_URL": "sqlite://",
        "SECRET_KEY": "test-secret-key",
        "OPENAI_API_KEY": "test-key-not-used",
        "FATSECRET_BASE_URL": "https://fatsecret.invalid",
        "FITX_SKIP_DB_INIT": "1",
        "PYTHONPATH": "",
    })
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.worker_healthcheck"],
        cwd=str(Path.cwd()), env=env, capture_output=True, text=True, timeout=120,
    )
    combined = proc.stdout + proc.stderr
    assert "ModuleNotFoundError" not in combined, combined
    assert "Traceback" not in combined, combined
    # Modül koştu ve sağlık sözleşmesini uyguladı (bu ortamda: sağlıksız).
    assert proc.returncode == 1, combined


def _dockerignore_patterns():
    return [
        line.strip()
        for line in Path(".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _dockerignore_excludes(pattern, posix_path):
    """`.dockerignore` eşleşmesi — `PurePath.match` DEĞİL.

    `PurePath.match` SAĞDAN eşleşir ve `**` onda özyinelemeli değildir, oysa
    `.dockerignore` desenleri build bağlamının KÖKÜNE çapalıdır ve `**`
    özyinelemelidir. Fark sessiz bir delik açardı: `**/scripts` eklenirse
    `scripts/` imaja hiç girmez, probe her koşuda `ModuleNotFoundError` verir —
    yani kalıcı unhealthy geri gelir — ve `match` tabanlı bir kapı YEŞİL kalırdı.
    """
    if pattern.startswith("!"):
        return False  # negasyon yeniden DAHİL eder, dışlamaz
    body = pattern.strip("/")
    if body.startswith("**/"):
        body = body[3:]
    parts = posix_path.split("/")
    # Bir dizin deseni altındaki her şeyi dışlar → yol öneklerini de sına.
    candidates = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    return any(
        fnmatch.fnmatch(candidate, body) or fnmatch.fnmatch(candidate.split("/")[-1], body)
        for candidate in candidates
    )


def test_the_dockerignore_matcher_actually_matches():
    # Yukarıdaki yardımcı yanlışsa asıl kapı BOŞUNA geçer. Gerçekten dışlanan
    # yolları tanıdığını ve dışlanmayanı tanımadığını önce burada kanıtla.
    patterns = _dockerignore_patterns()
    for excluded in ("tests/test_compose_config.py", "README.md", "Dockerfile",
                     "app/__pycache__/x.pyc", ".env"):
        assert any(_dockerignore_excludes(p, excluded) for p in patterns), excluded
    for kept in ("app/jobs/__init__.py", "worker.py", "requirements.txt"):
        assert not any(_dockerignore_excludes(p, kept) for p in patterns), kept
    # Ve varsayımsal bir desen gerçekten yakalanır:
    assert _dockerignore_excludes("**/scripts", WORKER_HEALTHCHECK_SCRIPT.as_posix())
    assert _dockerignore_excludes("scripts/", WORKER_HEALTHCHECK_SCRIPT.as_posix())


def test_worker_healthcheck_script_is_actually_shipped_into_the_image():
    # .dockerignore `tests/` ve `*.md`'yi imajdan çıkarır. Sağlık script'i de bir
    # gün oraya girerse probe SESSİZCE çalışamaz hale gelir — tam olarak
    # düzelttiğimiz arıza sınıfı (sağlık sinyalinin sessizce anlamsızlaşması).
    target = WORKER_HEALTHCHECK_SCRIPT.as_posix()
    offenders = [p for p in _dockerignore_patterns() if _dockerignore_excludes(p, target)]
    assert offenders == [], offenders


def _seconds(duration):
    assert duration.endswith("s"), duration
    return int(duration[:-1])


def _default_heartbeat_ttl():
    """app/jobs'taki VARSAYILAN TTL — ortamdan OKUMA.

    `WORKER_HEARTBEAT_TTL` bir operator düğmesidir ve `app.jobs` onu import
    zamanında okur. İçe aktarılan sabiti kullanmak testi çalıştıran makinenin
    (ya da bir depo-kökü .env'inin) ortamına bağlardı: `=240` bu testi kod
    değişmeden kırar. Daha önemlisi TERSİ: compose'daki interval/retries sabit
    LİTERALLERDİR, yani prod .env'inde TTL'i büyütmek buradaki bütçeyi sessizce
    aşar ve içe aktarılan sabite bakan bir test bunu ASLA göremez. Kaynaktaki
    varsayılana çapala."""
    source = Path("app/jobs/__init__.py").read_text(encoding="utf-8")
    match = re.search(
        r'WORKER_HEARTBEAT_TTL\s*=\s*int\(\s*os\.getenv\(\s*"WORKER_HEARTBEAT_TTL"\s*,\s*"(\d+)"\s*\)\s*\)',
        source,
    )
    assert match, "app/jobs varsayilan heartbeat TTL'i bulunamadi"
    return int(match.group(1))


def test_worker_healthcheck_windows_are_consistent_with_the_heartbeat_ttl(services):
    # Bu pencereler keyfi değil: heartbeat anahtarının TTL'inden türer.
    ttl = _default_heartbeat_ttl()
    healthcheck = services["worker"]["healthcheck"]
    interval = _seconds(healthcheck["interval"])
    timeout = _seconds(healthcheck["timeout"])
    retries = healthcheck["retries"]
    start_period = _seconds(healthcheck["start_period"])

    # worker.py heartbeat'i TTL/2'de bir tazeler → SAĞLIKLI bir worker'da anahtar
    # asla kaybolmaz. Kaçırılan tek bir tazelemeyi retries değil, TTL'in KENDİSİ
    # soğurur; retries yalnızca anahtar süresi DOLDUKTAN sonra pay ekler.
    assert max(ttl // 2, 5) < ttl

    # Probe bir sonraki probe'dan önce bitmeli — üst üste binen probe yığılmasın.
    assert timeout < interval, (timeout, interval)

    # Gerçekten bayat bir worker makul sürede yakalanmalı: anahtarın dolması +
    # ardışık başarısız probe'lar. Sınırsız olmasın.
    detection = ttl + interval * retries
    assert detection <= 300, detection

    # Boot: import + Redis bağlantısı + ilk heartbeat yazımı start_period içinde
    # bitmeli, yoksa worker daha ayağa kalkarken unhealthy işaretlenir.
    assert start_period >= 30, start_period


def test_web_still_inherits_the_dockerfile_http_healthcheck(services):
    # Regresyon kapısı: worker'ı düzeltmek web'in HTTP sağlık sözleşmesini
    # BOZMAMALI. web compose'da override etmez → imajdakini miras alır.
    assert "healthcheck" not in services["web"]
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "HEALTHCHECK" in dockerfile
    assert "http://127.0.0.1:5000/health" in dockerfile


def test_redis_healthcheck_unchanged(services):
    # Aynı regresyon kapısı, redis için.
    probe = services["redis"]["healthcheck"]["test"]
    assert probe == ["CMD-SHELL",
                     'REDISCLI_AUTH="$$REDIS_PASSWORD" redis-cli ping | grep -q PONG']


def test_worker_command_and_queue_semantics_unchanged(services):
    # Sağlık sözleşmesi düzeltmesi worker'ın ÇALIŞMA davranışını değiştirmez.
    assert services["worker"]["command"] == "python worker.py"
    assert services["worker"]["restart"] == "unless-stopped"
    assert services["worker"]["depends_on"] == {
        "redis": {"condition": "service_healthy"}
    }
