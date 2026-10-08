"""R6-01B live proof: two web slots on one Docker host (disposable, CI only).

Runs only with FITX_R6_DOCKER_IT=1 on a disposable Docker host (the
`r6-slot-docker-integration` CI job). It builds the exact checked-out revision
the way the deploy path does (git archive + BUILD_REVISION), then reproduces
the production shape with the REAL docker-compose.yml:

    main project  (stands in for `fitness-coach`): legacy web + worker + redis,
                  plus a disposable postgres standing in for RDS
    slot projects axisai-web-blue / axisai-web-green, driven only through
                  scripts/web_slot_runtime.py

No production host, AWS service, provider or real account is touched.

    FITX_R6_DOCKER_IT=1 python -m pytest tests/test_r6_slot_docker_integration.py -v
"""
import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

if os.environ.get("FITX_R6_DOCKER_IT") != "1":
    pytest.skip("set FITX_R6_DOCKER_IT=1 on a disposable Docker host",
                allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "web_slot_runtime.py"
MAIN = "r6bit-main"
NETWORK = f"{MAIN}_default"
OTHER_REV = "0" * 40
(CW_FILTER,) = [
    entry["filters"][0]["expression"] for entry in json.loads(
        (ROOT / "deploy/cloudwatch-agent/file_config.json").read_text())[
        "logs"]["logs_collected"]["files"]["collect_list"]
    if entry["log_group_name"] == "/axisai/app"]


def sh(*args, check=True, timeout=600, cwd=None, env=None):
    proc = subprocess.run(list(args), capture_output=True, text=True,
                          timeout=timeout, cwd=cwd, env=env)
    if check and proc.returncode != 0:
        raise AssertionError(f"{args} failed rc={proc.returncode}\n"
                             f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    return proc


def helper(deploy, *args, main=MAIN):
    proc = sh(sys.executable, str(HELPER), "--deploy-dir", str(deploy),
              "--main-project", main, *args, check=False, timeout=900)
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise AssertionError(f"helper output not JSON: {proc.stdout}\n{proc.stderr}")
    return proc.returncode, payload


def inspect(name_or_id):
    return json.loads(sh("docker", "inspect", name_or_id).stdout)[0]


def slot_container(slot):
    ids = sh("docker", "ps", "-aq", "--filter",
             f"label=com.docker.compose.project=axisai-web-{slot}").stdout.split()
    assert len(ids) == 1, ids
    return inspect(ids[0])


def http(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def wait_healthy(name, seconds=300):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = inspect(name)["State"]
        if (state.get("Health") or {}).get("Status") == "healthy":
            return
        assert state["Status"] == "running", (name, state)
        time.sleep(3)
    raise AssertionError(f"{name} never became healthy:\n"
                         + sh("docker", "logs", "--tail", "80", name, check=False).stderr)


def schema_fingerprint():
    return sh("docker", "exec", f"{MAIN}-pg-1", "psql", "-U", "postgres", "-d",
              "fitx_it", "-Atc",
              "SELECT (SELECT string_agg(version_num, ',') FROM alembic_version) || '|' || "
              "(SELECT md5(string_agg(table_name || '.' || column_name || ':' || data_type, ',' "
              "ORDER BY table_name, column_name)) FROM information_schema.columns "
              "WHERE table_schema = 'public')").stdout.strip()


def json_log_lines(container):
    path = container["LogPath"]
    return sh("sudo", "-n", "head", "-c", "200000", path).stdout.splitlines()


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    revision = sh("git", "rev-parse", "HEAD", cwd=ROOT).stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{40}", revision)
    work = tmp_path_factory.mktemp("r6b")
    context = work / "context"
    context.mkdir()
    archive = work / "context.tar"
    sh("git", "archive", "--format=tar", revision, "-o", str(archive), cwd=ROOT)
    with tarfile.open(archive) as tar:
        tar.extractall(context, filter="data")
    image = f"axisai-web:{revision}"
    sh("docker", "build", "--build-arg", f"BUILD_REVISION={revision}", "-t", image,
       str(context), timeout=1200)

    deploy = work / "deploy"
    (deploy / "deploy/compose").mkdir(parents=True)
    main_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert main_text.count("    build: .\n") == 2
    (deploy / "docker-compose.yml").write_text(main_text.replace(
        "    build: .\n", f"    image: {image}\n    pull_policy: never\n"), encoding="utf-8")
    (deploy / "docker-compose.it-db.yml").write_text(
        "services:\n"
        "  pg:\n"
        "    image: postgres:16-alpine\n"
        "    environment: {POSTGRES_PASSWORD: itpw, POSTGRES_DB: fitx_it}\n"
        "    healthcheck:\n"
        "      test: [\"CMD-SHELL\", \"pg_isready -U postgres\"]\n"
        "      interval: 2s\n      timeout: 3s\n      retries: 30\n"
        "  web:\n    depends_on: {pg: {condition: service_healthy}}\n"
        "  worker:\n    depends_on: {pg: {condition: service_healthy}}\n",
        encoding="utf-8")
    shutil.copy(ROOT / "docker-compose.web-slot.yml", deploy)
    for slot in ("blue", "green"):
        shutil.copy(ROOT / f"deploy/compose/web-slot-{slot}.yml", deploy / "deploy/compose")
    key = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    (deploy / ".env").write_text("\n".join([
        "SECRET_KEY=r6b-ci-only-secret",
        "DATABASE_URL=postgresql://postgres:itpw@pg:5432/fitx_it",
        "REDIS_PASSWORD=r6bciredis",
        "REDIS_URL=redis://:r6bciredis@redis:6379/0",
        "OPENAI_API_KEY=sk-ci-unused",
        "BEDROCK_ENABLED=0", "S3_BUCKET_NAME=", "COGNITO_USER_POOL_ID=",
        "COGNITO_APP_CLIENT_ID=", "RESEND_API_KEY=", "LOGIN_FAIL_CLOSED=1",
        "MOBILE_AUTH_ENABLED=1",
        "MOBILE_AUTH_DERIVATION_KEYRING='{\"ci-v1\":\"" + key + "\"}'",
        "MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION=ci-v1", ""]), encoding="utf-8")

    main = ["docker", "compose", "-p", MAIN, "--project-directory", str(deploy),
            "-f", str(deploy / "docker-compose.yml"),
            "-f", str(deploy / "docker-compose.it-db.yml")]
    try:
        sh(*main, "up", "-d", timeout=600)
        wait_healthy(f"{MAIN}-web-1")
        wait_healthy("fitx-redis")
        yield {"revision": revision, "deploy": deploy, "main": main, "image": image}
    finally:
        for slot in ("blue", "green"):
            sh("docker", "compose", "-p", f"axisai-web-{slot}", "down", "--timeout", "5",
               check=False)
        sh(*main, "down", "-v", "--timeout", "5", check=False)
        sh("docker", "rmi", "-f", image, f"axisai-web:{OTHER_REV}", check=False)


def test_two_slot_runtime_end_to_end(stack):
    rev, deploy, main = stack["revision"], stack["deploy"], stack["main"]
    redis_before = inspect("fitx-redis")
    worker_before = inspect(f"{MAIN}-worker-1")
    legacy = inspect(f"{MAIN}-web-1")
    schema_before = schema_fingerprint()

    # ── failure cases first: each refused, nothing started ────────────────
    code, out = helper(deploy, "preflight", "--slot", "blue", "--revision", rev,
                       main="r6bit-absent")
    assert code == 3 and "does not exist" in out["detail"], out
    sh("docker", "tag", stack["image"], f"axisai-web:{OTHER_REV}")
    code, out = helper(deploy, "start", "--slot", "blue", "--revision", OTHER_REV)
    assert code == 3 and "different BUILD_REVISION" in out["detail"], out
    squatter = socket.socket()
    squatter.bind(("127.0.0.1", 5001))
    squatter.listen(1)
    try:
        code, out = helper(deploy, "start", "--slot", "blue", "--revision", rev)
        assert code == 3 and "already in use" in out["detail"], out
    finally:
        squatter.close()
    code, out = helper(deploy, "start", "--slot", "blue", "--revision", rev,
                       "--host-reserve-mib", "0")
    assert code == 2, out
    assert sh("docker", "ps", "-aq", "--filter",
              "label=com.docker.compose.project=axisai-web-blue").stdout.strip() == ""

    # ── candidate blue next to the serving legacy web ─────────────────────
    code, out = helper(deploy, "start", "--slot", "blue", "--revision", rev)
    assert code == 0 and out["action"] == "started", out
    assert out["admission"]["running_web_containers"] == 1
    code, out = helper(deploy, "verify", "--slot", "blue", "--revision", rev)
    assert code == 0 and out["deep_health_revision"] == rev, out
    code, out = helper(deploy, "start", "--slot", "blue", "--revision", rev)
    assert code == 0 and out["action"] == "already-running", out       # idempotent

    # A third web (legacy + blue + green) is outside the capacity contract.
    code, out = helper(deploy, "start", "--slot", "green", "--revision", rev)
    assert code == 3 and "too_many_running_web_containers" in out["detail"], out

    # R6-03 will retire the legacy web; emulate that, then run BOTH slots.
    sh(*main, "stop", "web")
    code, out = helper(deploy, "start", "--slot", "green", "--revision", rev)
    assert code == 0 and out["action"] == "started", out
    code, out = helper(deploy, "verify", "--slot", "green", "--revision", rev)
    assert code == 0, out

    blue, green = slot_container("blue"), slot_container("green")
    for slot, container, port in (("blue", blue, 5001), ("green", green, 5002)):
        labels = container["Config"]["Labels"]
        assert labels["com.docker.compose.service"] == "web"
        assert labels["com.docker.compose.project"] == f"axisai-web-{slot}"
        assert container["State"]["Health"]["Status"] == "healthy"
        assert container["HostConfig"]["PortBindings"] == {
            "5000/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(port)}]}
        assert container["HostConfig"]["Memory"] == 640 * 1024 * 1024
        assert container["Config"]["StopTimeout"] == 45
        env = dict(e.split("=", 1) for e in container["Config"]["Env"])
        assert env["FITX_STARTUP_MODE"] == "read-only"
        assert env["APP_REVISION"] == rev
        assert list(container["NetworkSettings"]["Networks"]) == [NETWORK]
        status, body = http(f"http://127.0.0.1:{port}/health")
        # limiter_storage "redis" = this slot reached the main project's redis.
        assert (status, body["status"], body["db"], body["limiter_storage"]) == (
            200, "ok", "ok", "redis")
        assert sh("docker", "exec", container["Id"], "cat",
                  "/app/BUILD_REVISION").stdout.strip() == rev
        # Representative mobile API through the slot's own loopback port.
        status, body = http(f"http://127.0.0.1:{port}/api/v1/account/me")
        assert status == 401 and body["error"]["code"] == "AUTH_SESSION_EXPIRED"
        status, body = http(f"http://127.0.0.1:{port}/api/v1/account/me",
                            {"Authorization": "Bearer r6b-not-a-credential"})
        assert status == 401 and body["error"]["code"] == "AUTH_SESSION_EXPIRED"
        # Slot logs keep the service identity the CloudWatch agent ships.
        lines = json_log_lines(container)
        assert lines and all(re.search(CW_FILTER, line) for line in lines)
        assert all(f'"com.docker.compose.project":"axisai-web-{slot}"' in line
                   for line in lines)
    assert blue["Name"] != green["Name"]

    # Slot boots changed no schema; Redis and worker were never recreated.
    assert schema_fingerprint() == schema_before
    for before in (redis_before, worker_before):
        now = inspect(before["Id"])
        assert now["State"]["StartedAt"] == before["State"]["StartedAt"]
        assert now["State"]["Status"] == "running"

    # ── --remove-orphans isolation, both directions ───────────────────────
    sh(*main, "up", "-d", "--remove-orphans", "--no-deps", "worker")
    for slot in ("blue", "green"):
        assert slot_container(slot)["State"]["Status"] == "running"
    assert inspect(redis_before["Id"])["State"]["StartedAt"] == \
        redis_before["State"]["StartedAt"]
    sh("docker", "compose", "-p", "axisai-web-green", "--project-directory", str(deploy),
       "-f", str(deploy / "docker-compose.web-slot.yml"),
       "-f", str(deploy / "deploy/compose/web-slot-green.yml"),
       "up", "-d", "--no-build", "--pull", "never", "--no-deps", "--no-recreate",
       "--remove-orphans", "web",
       env={**os.environ, "AXISAI_WEB_SLOT_REVISION": rev,
            "AXISAI_SHARED_NETWORK": NETWORK})
    assert slot_container("blue")["State"]["Status"] == "running"
    assert inspect(redis_before["Id"])["State"]["Status"] == "running"
    assert inspect(f"{MAIN}-worker-1")["State"]["Status"] == "running"
    assert inspect(legacy["Id"])["State"]["Status"] == "exited"   # untouched (stopped above)

    # ── removing the candidate leaves the active slot, Redis, worker ──────
    code, out = helper(deploy, "remove", "--slot", "green", "--expected-revision", OTHER_REV)
    assert code == 3, out                                          # wrong revision refused
    code, out = helper(deploy, "remove", "--slot", "green", "--expected-revision", rev)
    assert code == 0 and out["action"] == "removed", out
    assert sh("docker", "ps", "-aq", "--filter",
              "label=com.docker.compose.project=axisai-web-green").stdout.strip() == ""
    assert http("http://127.0.0.1:5001/health")[0] == 200
    assert slot_container("blue")["State"]["Health"]["Status"] == "healthy"
    assert sh("docker", "network", "inspect", NETWORK, check=False).returncode == 0
    assert inspect(redis_before["Id"])["State"]["StartedAt"] == \
        redis_before["State"]["StartedAt"]
    assert inspect(f"{MAIN}-worker-1")["State"]["Status"] == "running"
    volumes = sh("docker", "volume", "ls", "-q").stdout.split()
    assert f"{MAIN}_redis_data" in volumes


def test_stop_grace_lets_gunicorn_drain_instead_of_sigkill(stack):
    """Docker must send SIGTERM and wait; gunicorn exits on its own (code 0),
    well inside the 45 s grace, rather than being SIGKILLed (137)."""
    container = slot_container("blue")
    started = time.monotonic()
    sh("docker", "stop", container["Id"], timeout=120)
    elapsed = time.monotonic() - started
    state = inspect(container["Id"])["State"]
    assert state["ExitCode"] != 137 and not state["OOMKilled"], state
    assert elapsed < 45
