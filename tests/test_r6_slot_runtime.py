"""R6-01B: two-slot web runtime foundation (no traffic switching).

Static Compose contract, a real `docker compose config` render (skipped when
the Compose CLI is absent), the pure capacity gate on synthetic /proc/meminfo,
the /health limiter budget, and slot lifecycle decisions against a scripted
fake Docker. Nothing here starts a container or depends on this machine's RAM.

    python -m pytest tests/test_r6_slot_runtime.py -v
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts import web_slot_runtime as rt

BASE = Path("docker-compose.web-slot.yml")
MAIN = Path("docker-compose.yml")
OVERLAYS = {slot: Path(f"deploy/compose/web-slot-{slot}.yml") for slot in rt.SLOT_PORTS}
AGENT = Path("deploy/cloudwatch-agent/file_config.json")
REV_A = "a" * 40
REV_B = "b" * 40
MiB = 1024


def _yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ── Slot Compose contract (static) ──────────────────────────────────────────

def test_slot_base_owns_only_the_web_service_and_nothing_persistent():
    doc = _yaml(BASE)
    assert set(doc["services"]) == {"web"}
    assert "volumes" not in doc                      # no redis_data ownership
    web = doc["services"]["web"]
    for forbidden in ("build", "container_name", "depends_on", "volumes",
                      "ports", "healthcheck", "command", "user"):
        assert forbidden not in web, forbidden
    assert "name" not in doc                          # identity only from overlay


def test_slot_image_is_an_exact_revision_tag_never_built_or_pulled():
    web = _yaml(BASE)["services"]["web"]
    assert web["image"].startswith("axisai-web:${AXISAI_WEB_SLOT_REVISION:?")
    assert ":latest" not in web["image"]
    assert web["pull_policy"] == "never"


def test_candidate_startup_mode_is_a_literal_that_outranks_env_file():
    web = _yaml(BASE)["services"]["web"]
    assert web["env_file"] == [".env"]
    assert web["environment"]["FITX_STARTUP_MODE"] == "read-only"
    assert "FITX_SKIP_DB_INIT" not in web["environment"]
    assert web["environment"]["APP_REVISION"].startswith(
        "${AXISAI_WEB_SLOT_REVISION:?")


def test_memory_ceiling_is_a_bounded_literal_mirrored_by_the_admission_gate():
    web = _yaml(BASE)["services"]["web"]
    assert web["mem_limit"] == f"{rt.SLOT_MEM_LIMIT_MIB}m"   # not .env-tunable
    main_web = _yaml(MAIN)["services"]["web"]
    assert main_web["mem_limit"] == "${WEB_MEM_LIMIT:-1g}"  # legacy unchanged
    assert 384 <= rt.SLOT_MEM_LIMIT_MIB <= 768


def test_stop_grace_exceeds_gunicorn_graceful_timeout_plus_flush_bound():
    web = _yaml(BASE)["services"]["web"]
    assert web["stop_grace_period"] == f"{rt.STOP_GRACE_SECONDS}s"
    assert web["stop_signal"] == "SIGTERM"
    gunicorn = Path("gunicorn.conf.py").read_text(encoding="utf-8")
    graceful = int(re.search(r"^graceful_timeout = (\d+)$", gunicorn, re.M).group(1))
    timeout = int(re.search(r"^timeout = (\d+)$", gunicorn, re.M).group(1))
    assert graceful == rt.GUNICORN_GRACEFUL_TIMEOUT_SECONDS == 30
    assert timeout == 300                             # untouched by R6-01B
    assert rt.STOP_GRACE_SECONDS >= graceful + rt.SHUTDOWN_FLUSH_BOUND_SECONDS + 10


def test_logging_rotation_preserved_and_service_label_kept():
    web = _yaml(BASE)["services"]["web"]
    main = _yaml(MAIN)["services"]["web"]["logging"]
    assert web["logging"]["driver"] == main["driver"] == "json-file"
    for key in ("max-size", "max-file"):
        assert web["logging"]["options"][key] == main["options"][key]
    labels = web["logging"]["options"]["labels"].split(",")
    assert labels[0] == "com.docker.compose.service"
    assert "com.docker.compose.project" in labels


def test_shared_network_is_external_validated_input_never_slot_owned():
    doc = _yaml(BASE)
    assert doc["services"]["web"]["networks"] == ["shared"]
    shared = doc["networks"]["shared"]
    assert shared["external"] is True
    assert shared["name"].startswith("${AXISAI_SHARED_NETWORK:?")
    assert set(shared) == {"name", "external"}        # no driver/ipam: not created here


@pytest.mark.parametrize("slot", sorted(rt.SLOT_PORTS))
def test_overlay_sets_only_project_identity_and_one_loopback_port(slot):
    doc = _yaml(OVERLAYS[slot])
    assert set(doc) == {"name", "services"}
    assert doc["name"] == f"axisai-web-{slot}" == rt.slot_project(slot)
    assert set(doc["services"]) == {"web"}
    assert doc["services"]["web"] == {
        "ports": [f"127.0.0.1:{rt.SLOT_PORTS[slot]}:5000"]}


def test_slot_ports_are_distinct_and_avoid_every_main_project_port():
    assert sorted(rt.SLOT_PORTS.values()) == [5001, 5002]
    main_ports = {int(p.split(":")[1]) for s in _yaml(MAIN)["services"].values()
                  for p in s.get("ports", [])}
    assert main_ports == {5000, 6379}
    assert not main_ports & set(rt.SLOT_PORTS.values())


def test_slot_projects_cannot_collide_with_the_main_project_or_each_other():
    # `--remove-orphans` only considers containers whose project label equals
    # the invoking project, so distinct project names are the isolation.
    assert "name" not in _yaml(MAIN)
    names = {_yaml(p)["name"] for p in OVERLAYS.values()}
    assert names == {"axisai-web-blue", "axisai-web-green"}
    assert rt.DEFAULT_MAIN_PROJECT not in names
    with pytest.raises(rt.InvalidInput):
        rt.validate_main_project("axisai-web-blue")


def test_internal_port_and_image_healthcheck_stay_container_local_5000():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "http://127.0.0.1:5000/health" in dockerfile
    assert 'bind = "0.0.0.0:5000"' in Path("gunicorn.conf.py").read_text(encoding="utf-8")
    assert rt.INTERNAL_PORT == 5000


def test_env_file_and_secrets_are_not_baked_into_the_image():
    ignored = Path(".dockerignore").read_text(encoding="utf-8").split()
    assert ".env" in ignored
    assert "COPY .env" not in Path("Dockerfile").read_text(encoding="utf-8")


# ── CloudWatch log shipping for both slots ──────────────────────────────────

def _app_filter():
    agent = json.loads(AGENT.read_text(encoding="utf-8"))
    (entry,) = [e for e in agent["logs"]["logs_collected"]["files"]["collect_list"]
                if e["log_group_name"] == "/axisai/app"]
    (rule,) = entry["filters"]
    assert rule["type"] == "include"
    return rule["expression"]


@pytest.mark.parametrize("slot", sorted(rt.SLOT_PORTS))
def test_both_slots_json_log_lines_match_the_existing_agent_filter(slot):
    # Docker writes the configured labels into attrs in label-list order.
    line = json.dumps({"log": "{\"event\":\"request\"}\n", "stream": "stderr",
                       "attrs": {"com.docker.compose.service": "web",
                                 "com.docker.compose.project": rt.slot_project(slot)},
                       "time": "2026-10-08T07:20:00.519796033Z"},
                      separators=(",", ":"))
    assert re.search(_app_filter(), line)


def test_a_renamed_slot_service_would_be_silently_dropped():
    line = json.dumps({"attrs": {"com.docker.compose.service": "web-blue"}},
                      separators=(",", ":"))
    assert not re.search(_app_filter(), line)


# ── Pure validation & capacity gate ─────────────────────────────────────────

@pytest.mark.parametrize("value", ["", "A" * 40, "a" * 39, "a" * 41, "g" * 40,
                                   None, " " + "a" * 40, "a" * 40 + "\n"])
def test_revision_must_be_exact_lowercase_40_hex(value):
    with pytest.raises(rt.InvalidInput):
        rt.validate_revision(value)


@pytest.mark.parametrize("value", ["", "Blue", "red", "blue ", None, "legacy"])
def test_slot_identity_fails_closed(value):
    with pytest.raises(rt.InvalidInput):
        rt.validate_slot(value)


def test_shared_network_derives_from_a_validated_main_project():
    assert rt.shared_network("fitness-coach") == "fitness-coach_default"
    for bad in ("", "Fitness", "x" * 64, "a/b", "a b", "-a"):
        with pytest.raises(rt.InvalidInput):
            rt.shared_network(bad)


def _meminfo(available_kib):
    return ("MemTotal:        1950720 kB\nMemFree:          374784 kB\n"
            f"MemAvailable:    {available_kib} kB\nBuffers:           2048 kB\n")


def test_admission_formula_matches_the_documented_budget():
    assert rt.required_available_kib(256) == (640 + 32 + 256) * MiB == 928 * MiB


@pytest.mark.parametrize("available_mib, running, allowed, reason", [
    (1088, 1, True, "ok"),                               # worst observed prod
    (1300, 0, True, "ok"),
    (927, 1, False, "insufficient_mem_available"),
    (500, 1, False, "insufficient_mem_available"),
    (4000, 2, False, "too_many_running_web_containers"),  # active+candidate exist
])
def test_admission_decisions(available_mib, running, allowed, reason):
    decision = rt.decide_admission(available_mib * MiB, running, 256)
    assert (decision.allowed, decision.reason) == (allowed, reason)


def test_admission_exact_boundary_admits_and_one_kib_below_refuses():
    required = rt.required_available_kib(256)
    assert rt.decide_admission(required, 1, 256).allowed is True
    assert rt.decide_admission(required - 1, 1, 256).allowed is False


@pytest.mark.parametrize("text", [
    "MemTotal: 1 kB\n",                                    # missing
    "MemAvailable: 12 kB\nMemAvailable: 12 kB\n",          # ambiguous
    "MemAvailable: -5 kB\n", "MemAvailable: 12 MB\n",
    "MemAvailable: lots kB\n", "MemAvailable:\n", "",
])
def test_malformed_meminfo_refuses(text):
    with pytest.raises(rt.Refused):
        rt.parse_mem_available_kib(text)


def test_meminfo_parses_the_kernel_format():
    assert rt.parse_mem_available_kib(_meminfo(1114112)) == 1114112


@pytest.mark.parametrize("raw", ["", " ", "0", "-1", "191", "769", "1e3",
                                 "256MiB", "garbage", "99999999"])
def test_host_reserve_override_cannot_disable_the_gate(raw):
    with pytest.raises(rt.InvalidInput):
        rt.parse_host_reserve_mib(raw)


def test_host_reserve_defaults_and_accepts_in_bounds_values():
    assert rt.parse_host_reserve_mib(None) == 256
    assert rt.parse_host_reserve_mib("192") == 192
    assert rt.parse_host_reserve_mib("768") == 768


@pytest.mark.parametrize("count", [-1, True, "1", None])
def test_invalid_running_count_refuses(count):
    with pytest.raises(rt.Refused):
        rt.decide_admission(2_000_000, count, 256)


def test_two_slot_worst_case_ceilings_need_the_gate_not_just_limits():
    # Configured worst case does not fit physically; safety comes from the
    # ceiling AND the MemAvailable gate, never from the ceilings alone.
    worst = 2 * rt.SLOT_MEM_LIMIT_MIB + 512 + 256
    assert worst > 1905
    # With the candidate at its full ceiling, the documented worst observed
    # host still keeps the protected reserve.
    assert 1088 - rt.SLOT_MEM_LIMIT_MIB - rt.RUNTIME_OVERHEAD_MIB >= 256


# ── /health limiter budget ──────────────────────────────────────────────────

def test_health_probe_budget_stays_well_under_the_default_limit():
    config = Path("app/config.py").read_text(encoding="utf-8")
    default = re.search(r'DEFAULT_RATELIMIT = os.getenv\("DEFAULT_RATELIMIT", '
                        r'"(\d+) per hour"\)', config)
    limit = int(default.group(1))
    assert limit == 600
    interval = int(re.search(r"HEALTHCHECK --interval=(\d+)s",
                             Path("Dockerfile").read_text(encoding="utf-8")).group(1))
    docker_checks = rt.MAX_RUNNING_WEB_CONTAINERS * 3600 // interval   # 240
    # Every anonymous probe from inside a container is keyed ip:127.0.0.1 on
    # endpoint `health` in the SHARED Redis, so all web containers share it.
    legacy_deploy_probes = 2 * 30            # production_deploy.sh candidate + rollback
    slot_verify_probes = 4 * rt.DEEP_PROBE_ATTEMPTS
    worst = docker_checks + legacy_deploy_probes + slot_verify_probes
    assert worst == 324
    assert worst <= 0.6 * limit              # >= 40 % headroom


# ── Lifecycle against a scripted fake Docker ────────────────────────────────

def _container(slot="blue", revision=REV_A, status="running", health="healthy",
               mode="read-only", host_ip="127.0.0.1", port=None, project=None,
               service="web"):
    return {
        "Id": f"{slot}{revision[:6]}" + "0" * 52,
        "Name": f"/axisai-web-{slot}-web-1",
        "State": {"Status": status, "Health": {"Status": health}},
        "Config": {"Image": f"axisai-web:{revision}",
                   "Env": [f"APP_REVISION={revision}", f"FITX_STARTUP_MODE={mode}",
                           "SECRET_KEY=never-printed"],
                   "Labels": {"com.docker.compose.project": project or f"axisai-web-{slot}",
                              "com.docker.compose.service": service}},
        "HostConfig": {"PortBindings": {"5000/tcp": [
            {"HostIp": host_ip, "HostPort": str(port or rt.SLOT_PORTS[slot])}]}},
    }


def _rendered(slot, revision, network="fitness-coach_default"):
    return {
        "name": f"axisai-web-{slot}",
        "networks": {"shared": {"name": network, "external": True, "ipam": {}}},
        "services": {"web": {
            "image": f"axisai-web:{revision}", "pull_policy": "never",
            "mem_limit": str(rt.SLOT_MEM_LIMIT_MIB * MiB * MiB),
            "stop_grace_period": "45s",
            "environment": {"FITX_STARTUP_MODE": "read-only", "APP_REVISION": revision},
            "logging": {"driver": "json-file", "options": {
                "labels": "com.docker.compose.service,com.docker.compose.project"}},
            "networks": {"shared": None},
            "ports": [{"mode": "ingress", "host_ip": "127.0.0.1", "target": 5000,
                       "published": str(rt.SLOT_PORTS[slot]), "protocol": "tcp"}],
        }},
    }


class FakeDocker:
    def __init__(self, *, containers=None, network_labels=None, redis_ids="r1",
                 running_web="w1", image_present=True, baked=REV_A,
                 published="", rendered=None, deep=None):
        self.containers = containers or {}
        self.network_labels = network_labels if network_labels is not None else {
            "com.docker.compose.project": "fitness-coach",
            "com.docker.compose.network": "default"}
        self.redis_ids = redis_ids
        self.running_web = running_web
        self.image_present = image_present
        self.baked = baked
        self.published = published
        self.rendered = rendered
        self.deep = deep or {"http": 200, "status": "ok", "revision": REV_A}
        self.calls = []

    def __call__(self, args, timeout, env=None):
        self.calls.append((list(args), env))
        a = args[1:]
        out = lambda s="", rc=0: SimpleNamespace(returncode=rc, stdout=s, stderr="")
        if a[0] == "compose":
            verb = a[a.index("-f", a.index("-f") + 1) + 2]
            project = a[a.index("-p") + 1]
            slot = project.rsplit("-", 1)[1]
            if verb == "config":
                return out(json.dumps(self.rendered or _rendered(
                    slot, env["AXISAI_WEB_SLOT_REVISION"])))
            return out()
        if a[0] == "network":
            if self.network_labels is False:
                return out(rc=1)
            return out(json.dumps(self.network_labels))
        if a[0] == "ps":
            filters = [a[i + 1] for i, x in enumerate(a) if x == "--filter"]
            if any(f.startswith("label=com.docker.compose.service=redis") for f in filters):
                return out(self.redis_ids)
            if "label=com.docker.compose.service=web" in filters:
                return out(self.running_web)
            if any(f.startswith("publish=") for f in filters):
                return out(self.published)
            project = next(f.split("=", 2)[2] for f in filters
                           if f.startswith("label=com.docker.compose.project="))
            slot = project.rsplit("-", 1)[1]
            return out("\n".join(c["Id"] for c in self.containers.get(slot, [])))
        if a[0] == "inspect":
            every = [c for cs in self.containers.values() for c in cs]
            return out(json.dumps([c for c in every if c["Id"] in a[1:]]))
        if a[0] == "image":
            return out("sha256:x", 0 if self.image_present else 1)
        if a[0] == "run":
            return out(self.baked + "\n")
        if a[0] == "exec":
            if a[2] == "cat":
                return out(self.baked + "\n")
            return out(json.dumps(self.deep) + "\n")
        raise AssertionError(f"unexpected docker call {args}")

    def verbs(self):
        return [c[0][1] if c[0][1] != "compose" else
                "compose " + c[0][c[0].index("-f", c[0].index("-f") + 1) + 2]
                for c in self.calls]


@pytest.fixture
def deploy_dir(tmp_path):
    (tmp_path / "deploy/compose").mkdir(parents=True)
    shutil.copy(BASE, tmp_path / BASE.name)
    for path in OVERLAYS.values():
        shutil.copy(path, tmp_path / path)
    return tmp_path


def _runtime(deploy_dir, fake, available_mib=1200, bindable=True, http=(200, b'{"status":"ok"}')):
    return rt.SlotRuntime(
        deploy_dir, "fitness-coach", run=fake,
        read_meminfo=lambda: _meminfo(available_mib * MiB),
        port_bindable=lambda port: bindable,
        http_get=lambda url, timeout: http,
        sleep=lambda seconds: None,
        environ={"PATH": "/usr/bin", "COMPOSE_PROJECT_NAME": "fitness-coach",
                 "COMPOSE_FILE": "docker-compose.yml"})


def test_start_on_an_empty_slot_runs_up_without_deps_build_pull_or_recreate(deploy_dir):
    fake = FakeDocker()
    result = _runtime(deploy_dir, fake).start("green", REV_A, 256)
    assert result["action"] == "started"
    (up_args, up_env), = [c for c in fake.calls if "up" in c[0]]
    assert up_args[-8:] == ["up", "-d", "--no-build", "--pull", "never",
                            "--no-deps", "--no-recreate", "web"]
    assert up_args[up_args.index("-p") + 1] == "axisai-web-green"
    assert "--remove-orphans" not in up_args
    assert not any(k.startswith("COMPOSE_") for k in up_env)  # no project hijack
    assert up_env["AXISAI_WEB_SLOT_REVISION"] == REV_A
    assert up_env["AXISAI_SHARED_NETWORK"] == "fitness-coach_default"


def test_slot_commands_never_name_worker_or_redis(deploy_dir):
    fake = FakeDocker()
    runtime = _runtime(deploy_dir, fake)
    runtime.start("blue", REV_A, 256)
    for args, _ in fake.calls:
        if args[1] == "compose":
            assert "worker" not in args and "redis" not in args
            assert "-v" not in args and "--volumes" not in args


@pytest.mark.parametrize("health", ["healthy", "starting"])
def test_already_running_same_revision_is_an_idempotent_noop(deploy_dir, health):
    fake = FakeDocker(containers={"green": [_container("green", health=health)]},
                      running_web="w1 w2")
    result = _runtime(deploy_dir, fake).start("green", REV_A, 256)
    assert result["action"] == "already-running"
    assert not any("up" in c[0] for c in fake.calls)


@pytest.mark.parametrize("container", [
    _container("green", revision=REV_B),              # other revision
    _container("green", status="exited"),             # stopped
    _container("green", health="unhealthy"),
    _container("green", mode="self-migrating"),       # not a read-only candidate
    _container("green", host_ip="0.0.0.0"),           # exposed binding
])
def test_ambiguous_existing_candidate_state_fails_closed_without_touching_it(
        deploy_dir, container):
    fake = FakeDocker(containers={"green": [container]})
    with pytest.raises(rt.Refused, match="never overwritten"):
        _runtime(deploy_dir, fake).start("green", REV_A, 256)
    assert not any(v in ("compose up", "compose down") for v in fake.verbs())


def test_two_containers_in_one_slot_project_fail_closed(deploy_dir):
    fake = FakeDocker(containers={"green": [_container("green"), _container("green", REV_B)]})
    with pytest.raises(rt.Refused):
        _runtime(deploy_dir, fake).start("green", REV_A, 256)


def test_insufficient_memory_refuses_before_anything_starts(deploy_dir):
    fake = FakeDocker()
    with pytest.raises(rt.Refused, match="insufficient_mem_available"):
        _runtime(deploy_dir, fake, available_mib=900).start("green", REV_A, 256)
    assert "compose up" not in fake.verbs()


def test_a_third_running_web_container_is_refused(deploy_dir):
    fake = FakeDocker(running_web="legacy blue")
    with pytest.raises(rt.Refused, match="too_many_running_web_containers"):
        _runtime(deploy_dir, fake, available_mib=4000).start("green", REV_A, 256)
    assert "compose up" not in fake.verbs()


def test_occupied_candidate_port_fails_closed_without_killing_the_listener(deploy_dir):
    fake = FakeDocker()
    with pytest.raises(rt.Refused, match="already in use"):
        _runtime(deploy_dir, fake, bindable=False).start("green", REV_A, 256)
    fake = FakeDocker(published="someone")
    with pytest.raises(rt.Refused, match="already in use"):
        _runtime(deploy_dir, fake).start("green", REV_A, 256)
    assert not any(v in ("kill", "stop", "rm", "compose up") for v in fake.verbs())


def test_active_slot_port_does_not_block_the_other_slot(deploy_dir):
    probed = []
    fake = FakeDocker(containers={"blue": [_container("blue")]})
    runtime = _runtime(deploy_dir, fake)
    runtime._port_bindable = lambda port: probed.append(port) or port != 5001
    assert runtime.start("green", REV_A, 256)["action"] == "started"
    assert probed == [5002]


@pytest.mark.parametrize("labels", [
    False,                                                  # network missing
    {},                                                     # unlabelled
    {"com.docker.compose.project": "other", "com.docker.compose.network": "default"},
    {"com.docker.compose.project": "fitness-coach", "com.docker.compose.network": "x"},
])
def test_missing_or_foreign_shared_network_fails_closed(deploy_dir, labels):
    fake = FakeDocker(network_labels=labels)
    with pytest.raises(rt.Refused):
        _runtime(deploy_dir, fake).start("green", REV_A, 256)
    assert not any(v.startswith("network create") or v == "compose up"
                   for v in fake.verbs())


@pytest.mark.parametrize("redis_ids", ["", "r1 r2"])
def test_shared_network_without_exactly_one_healthy_redis_fails_closed(deploy_dir, redis_ids):
    fake = FakeDocker(redis_ids=redis_ids)
    with pytest.raises(rt.Refused, match="redis"):
        _runtime(deploy_dir, fake).start("green", REV_A, 256)


def test_missing_image_or_mismatched_baked_revision_fails_closed(deploy_dir):
    with pytest.raises(rt.Refused, match="not present"):
        _runtime(deploy_dir, FakeDocker(image_present=False)).start("green", REV_A, 256)
    with pytest.raises(rt.Refused, match="different BUILD_REVISION"):
        _runtime(deploy_dir, FakeDocker(baked=REV_B)).start("green", REV_A, 256)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(name="fitness-coach"),
    lambda d: d["services"].update(redis={"image": "redis"}),
    lambda d: d["services"].update(worker={"image": "x"}),
    lambda d: d["services"]["web"].update(mem_limit="1073741824"),
    lambda d: d["services"]["web"].update(stop_grace_period="10s"),
    lambda d: d["services"]["web"]["environment"].update(FITX_STARTUP_MODE="self-migrating"),
    lambda d: d["services"]["web"]["environment"].update(FITX_SKIP_DB_INIT="1"),
    lambda d: d["services"]["web"]["environment"].update(APP_REVISION="b" * 40),
    lambda d: d["services"]["web"]["ports"][0].update(host_ip="0.0.0.0"),
    lambda d: d["services"]["web"]["ports"][0].update(published="5000"),
    lambda d: d["services"]["web"]["ports"].append(dict(d["services"]["web"]["ports"][0])),
    lambda d: d["services"]["web"].update(build={"context": "."}),
    lambda d: d["services"]["web"].update(volumes=["redis_data:/data"]),
    lambda d: d["services"]["web"].update(pull_policy="always"),
    lambda d: d["networks"]["shared"].update(external=False),
    lambda d: d["networks"]["shared"].update(name="other_default"),
    lambda d: d["services"]["web"]["logging"]["options"].update(labels="x"),
])
def test_rendered_model_drift_is_refused(mutate):
    doc = _rendered("green", REV_A)
    mutate(doc)
    with pytest.raises(rt.Refused):
        rt.validate_rendered(doc, "green", REV_A, "fitness-coach_default")


def test_render_redacts_merged_env_file_values_even_when_validation_fails(deploy_dir):
    leaky = _rendered("green", REV_A)
    leaky["services"]["web"]["environment"].update(SECRET_KEY="never-printed",
                                                   DATABASE_URL="postgresql://x:pw@h/db")
    runtime = _runtime(deploy_dir, FakeDocker(rendered=leaky))
    doc = runtime.render("green", REV_A)
    assert "never-printed" not in json.dumps(doc) and "pw@h" not in json.dumps(doc)
    broken = _rendered("green", REV_A)
    broken["services"]["web"]["environment"].update(SECRET_KEY="never-printed",
                                                    FITX_SKIP_DB_INIT="1")
    with pytest.raises(rt.Refused) as refused:
        _runtime(deploy_dir, FakeDocker(rendered=broken)).render("green", REV_A)
    assert "never-printed" not in str(refused.value)


def test_verify_requires_health_baked_revision_deep_revision_and_host_port(deploy_dir):
    fake = FakeDocker(containers={"green": [_container("green")]})
    result = _runtime(deploy_dir, fake).verify("green", REV_A)
    assert result["action"] == "verified"
    assert result["deep_health_revision"] == REV_A
    assert result["host_health"] == "http://127.0.0.1:5002/health"


def test_docker_healthy_alone_is_not_readiness(deploy_dir):
    wrong_deep = FakeDocker(containers={"green": [_container("green")]},
                            deep={"http": 200, "status": "ok", "revision": REV_B})
    with pytest.raises(rt.VerificationFailed, match="deep health"):
        _runtime(deploy_dir, wrong_deep).verify("green", REV_A)
    assert sum(1 for v in wrong_deep.verbs() if v == "exec") == 1 + rt.DEEP_PROBE_ATTEMPTS
    baked = FakeDocker(containers={"green": [_container("green")]}, baked=REV_B)
    with pytest.raises(rt.VerificationFailed, match="BUILD_REVISION"):
        _runtime(deploy_dir, baked).verify("green", REV_A)
    host_down = FakeDocker(containers={"green": [_container("green")]})
    with pytest.raises(rt.VerificationFailed, match="loopback"):
        _runtime(deploy_dir, host_down, http=(503, b"{}")).verify("green", REV_A)


def test_verify_waits_a_bounded_time_for_docker_health(deploy_dir):
    fake = FakeDocker(containers={"green": [_container("green", health="starting")]})
    with pytest.raises(rt.VerificationFailed, match="never became healthy"):
        _runtime(deploy_dir, fake).verify("green", REV_A)
    assert sum(1 for v in fake.verbs() if v == "inspect") == rt.HEALTH_WAIT_POLLS
    assert "exec" not in fake.verbs()                 # no HTTP probe while unhealthy


def test_remove_only_touches_the_named_slot_at_the_expected_revision(deploy_dir):
    fake = FakeDocker(containers={"green": [_container("green")],
                                  "blue": [_container("blue", REV_B)]})
    runtime = _runtime(deploy_dir, fake)
    with pytest.raises(rt.Refused):
        runtime.remove("blue", REV_A)                  # wrong revision: refused
    assert runtime.remove("green", REV_A)["action"] == "removed"
    (down,) = [c[0] for c in fake.calls if "down" in c[0]]
    assert down[down.index("-p") + 1] == "axisai-web-green"
    assert down[-3:] == ["down", "--timeout", "45"]
    for flag in ("-v", "--volumes", "--rmi", "--remove-orphans"):
        assert flag not in down


def test_remove_of_an_absent_slot_is_a_noop(deploy_dir):
    fake = FakeDocker()
    assert _runtime(deploy_dir, fake).remove("green", REV_A) == {"action": "absent"}
    assert "compose down" not in fake.verbs()


def test_inspect_never_reports_container_environment_secrets(deploy_dir):
    fake = FakeDocker(containers={"green": [_container("green")]})
    report = json.dumps(_runtime(deploy_dir, fake).inspect("green"))
    assert "never-printed" not in report and "SECRET_KEY" not in report


def test_cli_rejects_invalid_input_with_exit_2(capsys):
    assert rt.main(["--deploy-dir", str(Path.cwd().resolve()), "render",
                    "--slot", "purple", "--revision", REV_A]) == 2
    assert rt.main(["--deploy-dir", str(Path.cwd().resolve()), "admission",
                    "--host-reserve-mib", "0"]) == 2


# ── Real Compose render (no container started) ──────────────────────────────

def _compose_cli():
    exe = shutil.which("docker")
    if not exe:
        return None
    probe = subprocess.run([exe, "compose", "version"], capture_output=True, text=True)
    return exe if probe.returncode == 0 else None


@pytest.mark.parametrize("slot", sorted(rt.SLOT_PORTS))
def test_real_compose_render_passes_the_contract_and_ignores_env_file_values(
        slot, deploy_dir):
    if not _compose_cli():
        pytest.skip("docker compose CLI unavailable")
    # A hostile .env must not change identity, mode, revision or network.
    (deploy_dir / ".env").write_text(
        "FITX_STARTUP_MODE=self-migrating\nAXISAI_WEB_SLOT_REVISION=" + "c" * 40 +
        "\nAXISAI_SHARED_NETWORK=evil_default\nCOMPOSE_PROJECT_NAME=fitness-coach\n"
        "SECRET_KEY=never-rendered\n", encoding="utf-8")
    runtime = rt.SlotRuntime(deploy_dir, "fitness-coach")
    doc = runtime.render(slot, REV_A)
    assert doc["name"] == f"axisai-web-{slot}"
    # Whatever the Compose version merges from env_file, nothing beyond the
    # slot-owned keys ever leaves the helper.
    assert "never-rendered" not in json.dumps(doc)
    assert doc["services"]["web"]["environment"] == {
        "FITX_STARTUP_MODE": "read-only", "APP_REVISION": REV_A}


def test_real_compose_render_without_revision_refuses(deploy_dir):
    exe = _compose_cli()
    if not exe:
        pytest.skip("docker compose CLI unavailable")
    (deploy_dir / ".env").write_text("", encoding="utf-8")
    proc = subprocess.run(
        [exe, "compose", "-f", BASE.name, "-f", str(OVERLAYS["blue"]), "config"],
        cwd=deploy_dir, capture_output=True, text=True, timeout=60,
        env={**{k: v for k, v in os.environ.items()
                if not k.startswith(("COMPOSE_", "AXISAI_"))},
             "AXISAI_SHARED_NETWORK": "fitness-coach_default"})
    assert proc.returncode != 0
    assert "AXISAI_WEB_SLOT_REVISION" in proc.stderr
