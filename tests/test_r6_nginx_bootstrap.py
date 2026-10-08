"""R6-02A: deterministic R6-02B nginx bootstrap (classifier, transform, apply).

The classifier and transform are pure and run everywhere. ``apply`` runs for
real against a temporary /etc tree on Linux, with nginx, the Certbot parser
gate and the public health probe injected. The real-nginx behaviour proof is
tests/test_r6_nginx_integration.py.
"""
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import axisai_nginx_bootstrap as boot  # noqa: E402

sw = boot.sw   # the exact helper module the bootstrap loads and installs

LIVE = (ROOT / "tests" / "fixtures" / "r6_nginx" / "fitx.live-2026-10-08.conf").read_bytes()
LIVE_SHA = "dba2117019971ea0d3f7dde124c6a575591bfc31e298773f3f735ab2e5e5486a"
INC = sw.INCLUDE_PATH
LEGACY_PROXY = b"        proxy_pass http://127.0.0.1:5000;"
UPSTREAM = f"upstream axisai_web {{\n    include {INC};\n}}\n".encode()

linux_only = pytest.mark.skipif(sys.platform == "win32" or not hasattr(os, "geteuid"),
                                reason="apply runs as a Linux root command")


def classify(data):
    return sw.classify_site(data, INC, 5000).kind


# --- fixture integrity --------------------------------------------------------------

def test_fixture_is_the_exact_live_site_audited_2026_10_08():
    import hashlib
    assert hashlib.sha256(LIVE).hexdigest() == LIVE_SHA
    assert b"# managed by Certbot" in LIVE and b'proxy_set_header Connection "upgrade";' in LIVE


# --- classification ---------------------------------------------------------------------

def test_live_site_is_legacy_direct():
    assert classify(LIVE) == sw.TOPOLOGY_LEGACY


def test_repository_template_is_migrated():
    assert classify((ROOT / "nginx.conf").read_bytes()) == sw.TOPOLOGY_MIGRATED


PARTIAL = {
    "upstream-only": LIVE.replace(b"limit_req_zone $binary", UPSTREAM + b"limit_req_zone $binary"),
    "proxy-only": LIVE.replace(b"http://127.0.0.1:5000", b"http://axisai_web"),
    "upstream-extra-server": boot.transform(LIVE, INC, 5000).replace(
        f"include {INC};".encode(), f"include {INC};\n    server 127.0.0.1:5001;".encode()),
    "upstream-wrong-include": boot.transform(LIVE, INC, 5000).replace(
        f"include {INC};".encode(), b"include /tmp/other.conf;"),
    "two-upstreams": boot.transform(LIVE, INC, 5000).replace(b"limit_req_zone $binary", UPSTREAM + b"limit_req_zone $binary"),
    "include-elsewhere": boot.transform(LIVE, INC, 5000).replace(
        b"server_tokens off;", f"server_tokens off;\n    include {INC};".encode()),
    "migrated-plus-legacy-location": boot.transform(LIVE, INC, 5000).replace(
        b"    location / {", b"    location /old { proxy_pass http://127.0.0.1:5000; }\n    location / {"),
    "upstream-in-wrong-proxy": boot.transform(LIVE, INC, 5000).replace(
        b"proxy_pass http://fatsecret_proxy/rest/server.api;", b"proxy_pass http://axisai_web;"),
}
UNKNOWN = {
    "two-legacy-proxies": LIVE.replace(
        b"    location / {", b"    location /api/ { proxy_pass http://127.0.0.1:5000; }\n    location / {"),
    "legacy-with-uri": LIVE.replace(b"http://127.0.0.1:5000;", b"http://127.0.0.1:5000/;"),
    "legacy-in-other-location": LIVE.replace(b"    location / {", b"    location /app {"),
    "legacy-via-variable": LIVE.replace(b"proxy_pass http://127.0.0.1:5000;",
                                         b"set $b 127.0.0.1:5000; proxy_pass http://$b;"),
    "unbalanced": LIVE + b"\n}\n",
    "unterminated": LIVE + b"\nserver {\n",
    "no-backend": LIVE.replace(b"proxy_pass http://127.0.0.1:5000;", b"return 503;"),
    "only-commented": LIVE.replace(b"proxy_pass http://127.0.0.1:5000;", b"# proxy_pass http://127.0.0.1:5000;"),
    "quoted-backend": LIVE.replace(b"http://127.0.0.1:5000;", b'"http://127.0.0.1:5000";'),
}


@pytest.mark.parametrize("name", sorted(PARTIAL))
def test_partial_topologies_are_refused(name):
    assert classify(PARTIAL[name]) == sw.TOPOLOGY_PARTIAL
    with pytest.raises(boot.BootError) as exc:
        boot.transform(PARTIAL[name], INC, 5000)
    assert exc.value.code == boot.EXIT_UNEXPECTED_TOPOLOGY


@pytest.mark.parametrize("name", sorted(UNKNOWN))
def test_unknown_topologies_are_refused(name):
    assert classify(UNKNOWN[name]) == sw.TOPOLOGY_UNKNOWN
    with pytest.raises(boot.BootError):
        boot.transform(UNKNOWN[name], INC, 5000)


def test_comments_and_quoted_strings_do_not_confuse_the_classifier():
    noisy = LIVE.replace(b"limit_req_zone $binary", b"# upstream axisai_web { include x; }\nlimit_req_zone $binary")
    noisy = noisy.replace(b"server_tokens off;", b'server_tokens off; # proxy_pass http://127.0.0.1:5000;')
    assert classify(noisy) == sw.TOPOLOGY_LEGACY
    assert classify(boot.transform(noisy, INC, 5000)) == sw.TOPOLOGY_MIGRATED


# --- transform ----------------------------------------------------------------------------------

def test_transform_changes_only_the_routing_lines():
    new = boot.transform(LIVE, INC, 5000)
    assert classify(new) == sw.TOPOLOGY_MIGRATED
    old_lines, new_lines = LIVE.split(b"\n"), new.split(b"\n")
    inserted = boot.upstream_block(INC).split(b"\n")[:-1]
    index = new_lines.index(b"upstream axisai_web {") - 2
    assert new_lines[index:index + len(inserted)] == inserted
    rest = new_lines[:index] + new_lines[index + len(inserted):]
    assert len(rest) == len(old_lines)
    diffs = [(a, b) for a, b in zip(old_lines, rest) if a != b]
    assert diffs == [(LEGACY_PROXY, b"        proxy_pass http://axisai_web;")]
    # every Certbot / TLS / header / timeout / fatsecret byte survives
    for marker in (b"ssl_certificate", b"options-ssl-nginx.conf", b"ssl_dhparam", b"return 301",
                   b'Connection "upgrade"', b"proxy_buffering off;", b"proxy_read_timeout    300s;",
                   b"client_max_body_size 25m;", b"fatsecret_proxy", b"limit_req zone=perip burst=60 nodelay;"):
        assert new.count(marker) == LIVE.count(marker) > 0


def test_transform_is_exactly_reversible():
    new = boot.transform(LIVE, INC, 5000)
    assert new.replace(boot.upstream_block(INC), b"", 1).replace(
        b"proxy_pass http://axisai_web;", b"proxy_pass http://127.0.0.1:5000;") == LIVE


def test_transform_is_deterministic_and_never_reapplied():
    new = boot.transform(LIVE, INC, 5000)
    assert boot.transform(LIVE, INC, 5000) == new
    with pytest.raises(boot.BootError, match="migrated"):
        boot.transform(new, INC, 5000)


def test_minimal_change_verifier_rejects_extra_edits():
    new = boot.transform(LIVE, INC, 5000)
    with pytest.raises(boot.BootError):
        boot.verify_minimal_change(LIVE, new.replace(b"server_tokens off;", b"server_tokens on;"),
                                   INC, LEGACY_PROXY, b"http://127.0.0.1:5000")
    with pytest.raises(boot.BootError):
        boot.verify_minimal_change(LIVE, new + b"\n# extra\n", INC, LEGACY_PROXY, b"http://127.0.0.1:5000")
    # a pure deletion adds nothing unexpected: only the removed-lines check catches it
    with pytest.raises(boot.BootError, match="removed"):
        boot.verify_minimal_change(LIVE, new.replace(b"    server_tokens off;\n", b"", 1),
                                   INC, LEGACY_PROXY, b"http://127.0.0.1:5000")


def test_template_and_transform_agree_on_the_routing_shape():
    template = (ROOT / "nginx.conf").read_bytes().replace(b"\r\n", b"\n")
    assert b"upstream axisai_web {\n    include /etc/nginx/axisai/active-web-upstream.conf;\n}" in template
    assert template.count(b"proxy_pass http://axisai_web;") == 1
    assert b"proxy_pass http://127.0.0.1:5000" not in template


# --- apply (Linux, temporary /etc) -----------------------------------------------------------

STAGE_A_MAPPING = (b"# AxisAI web slot -> loopback backend mapping (R6-01 / P6).\n"
                   b"blue 127.0.0.1:5001\ngreen 127.0.0.1:5002\n")


class Runner:
    def __init__(self, test=0, reload=0):
        self.calls = []
        self.script = {"test": test, "reload": reload}

    def __call__(self, argv):
        kind = "test" if tuple(argv) == ("nginx", "-t") else "reload"
        self.calls.append(kind)
        out = self.script[kind]
        if isinstance(out, list):
            out = out.pop(0) if out else 0
        return out, ""


def _mk(path, mode=0o755):
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(mode)
    return path


@pytest.fixture
def host(tmp_path):
    tmp_path.chmod(0o755)
    etc_nginx = _mk(tmp_path / "etc" / "nginx")
    avail = _mk(etc_nginx / "sites-available")
    enabled = _mk(etc_nginx / "sites-enabled")
    _mk(tmp_path / "etc" / "axisai")
    _mk(etc_nginx / "axisai")
    _mk(tmp_path / "usr" / "local" / "sbin")
    _mk(tmp_path / "run")
    _mk(tmp_path / "root", 0o700)
    site = avail / "fitx"
    site.write_bytes(LIVE)
    site.chmod(0o644)
    (enabled / "default").symlink_to(site)
    (avail / "fatsecret-proxy").write_text("server { listen 127.0.0.1:8080; }\n")
    (enabled / "fatsecret-proxy").symlink_to(avail / "fatsecret-proxy")
    include = str(etc_nginx / "axisai" / "active-web-upstream.conf")
    cfg = sw.Config(
        mapping_path=str(tmp_path / "etc" / "axisai" / "web-slots.conf"), include_path=include,
        site_path=str(site), lock_path=str(tmp_path / "run" / "lock"),
        pending_path=str(tmp_path / "run" / "pending"), nginx_test=("nginx", "-t"),
        nginx_reload=("nginx", "-s", "reload"), trusted_uid=os.geteuid(), trust_root=str(tmp_path))
    # the repository artifacts, with the include path the tmp site references
    repo = _mk(tmp_path / "repo")
    _mk(repo / "scripts")
    _mk(repo / "deploy" / "nginx")
    for rel in ("scripts/axisai_switch_web_slot.py", "deploy/nginx/web-slots.conf",
                "deploy/nginx/active-web-upstream.conf"):
        (repo / rel).write_bytes((ROOT / rel).read_bytes())
    artifacts = (
        ("scripts/axisai_switch_web_slot.py", str(tmp_path / "usr/local/sbin/axisai-switch-web-slot"), 0o755),
        ("deploy/nginx/web-slots.conf", cfg.mapping_path, 0o644),
        ("deploy/nginx/active-web-upstream.conf", cfg.include_path, 0o644),
    )
    bcfg = boot.BootConfig(sw=cfg, repo_dir=str(repo), artifacts=artifacts, sites_enabled=str(enabled),
                           nginx_root=str(etc_nginx), backup_dir=str(tmp_path / "root" / "axisai-r6-02b"),
                           health_url="https://example.invalid/health")
    return bcfg


def apply(bcfg, *, runner=None, probe=200, gate=None, op="apply"):
    runner = runner or Runner()
    probes = []

    def do_probe(url):
        probes.append(url)
        return probe.pop(0) if isinstance(probe, list) else probe

    deps = boot.BootDeps(run=runner, probe=do_probe, gate=gate or (lambda c, b: None),
                         sleep=lambda s: None, stamp=lambda: "20261008T000000Z")
    code, fields, notes = boot.run([op], bcfg, deps)
    return code, fields, runner, probes, notes


def tree_snapshot(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = Path(dirpath) / name
            if not path.is_symlink():
                out[str(path)] = (path.read_bytes(), path.stat().st_ino)
    return out


@linux_only
def test_check_is_read_only_and_reports_topology(host):
    before = tree_snapshot(Path(host.sw.trust_root))
    code, fields, runner, probes, _ = apply(host, op="check")
    assert code == 0 and fields["topology"] == "legacy-direct" and fields["site_sha256"] == LIVE_SHA
    assert fields["axisai-switch-web-slot"] == "absent"
    assert runner.calls == [] and probes == []
    snap = tree_snapshot(Path(host.sw.trust_root))
    snap.pop(host.sw.lock_path, None)
    assert snap == before


@linux_only
def test_apply_migrates_with_backup_exact_artifacts_and_verification(host):
    code, fields, runner, probes, notes = apply(host)
    assert code == 0, (fields, notes)
    assert fields["result"] == "migrated" and fields["state"] == "legacy"
    assert runner.calls == ["test", "test", "reload"]
    assert len(probes) == boot.HEALTH_SAMPLES
    site = Path(host.sw.site_path).read_bytes()
    assert site == boot.transform(LIVE, host.sw.include_path, 5000)
    assert stat.S_IMODE(os.stat(host.sw.site_path).st_mode) == 0o644
    backup = Path(fields["backup"])
    assert backup.read_bytes() == LIVE and stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert (Path(str(backup) + ".sha256").read_text().split()[0]) == LIVE_SHA
    for rel, dest, mode in host.artifacts:
        assert Path(dest).read_bytes() == (ROOT / rel).read_bytes()
        assert stat.S_IMODE(os.stat(dest).st_mode) == mode
    assert sw.read_route_state(host.sw).state == "legacy"


@linux_only
def test_apply_is_idempotent_on_a_migrated_site(host):
    assert apply(host)[0] == 0
    before = tree_snapshot(Path(host.sw.trust_root))
    code, fields, runner, probes, _ = apply(host)
    assert (code, fields["result"], fields["state"]) == (0, "already-migrated", "legacy")
    assert runner.calls == [] and probes == []
    after = tree_snapshot(Path(host.sw.trust_root))
    after.pop(host.sw.lock_path, None)
    before.pop(host.sw.lock_path, None)
    assert after == before


@linux_only
def test_already_migrated_with_artifact_drift_fails_closed(host):
    assert apply(host)[0] == 0
    Path(host.sw.mapping_path).write_bytes(STAGE_A_MAPPING)
    code, fields, runner, _probes, _ = apply(host)
    assert (code, fields["result"], runner.calls) == (boot.EXIT_UNTRUSTED, "already-migrated-drift", [])
    assert Path(host.sw.mapping_path).read_bytes() == STAGE_A_MAPPING


@linux_only
def test_stale_stage_a_files_are_replaced_from_the_repository(host):
    Path(host.sw.mapping_path).write_bytes(STAGE_A_MAPPING)
    os.chmod(host.sw.mapping_path, 0o644)
    helper = host.artifacts[0][1]
    Path(helper).write_bytes(b"#!/usr/bin/python3 -I\n# R6-01 stage A\n")
    os.chmod(helper, 0o755)
    code, fields, _runner, _probes, _ = apply(host, op="check")
    assert fields["web-slots.conf"] == "differs" and fields["axisai-switch-web-slot"] == "differs"
    code, fields, _runner, _probes, notes = apply(host)
    assert code == 0, notes
    assert Path(host.sw.mapping_path).read_bytes() == (ROOT / "deploy/nginx/web-slots.conf").read_bytes()
    assert Path(helper).read_bytes() == (ROOT / "scripts/axisai_switch_web_slot.py").read_bytes()


@linux_only
@pytest.mark.parametrize("mutate", sorted(PARTIAL) + sorted(UNKNOWN))
def test_unexpected_topology_changes_nothing(host, mutate):
    data = {**PARTIAL, **UNKNOWN}[mutate].replace(INC.encode(), host.sw.include_path.encode())
    Path(host.sw.site_path).write_bytes(data)
    before = tree_snapshot(Path(host.sw.trust_root))
    code, fields, runner, probes, _ = apply(host)
    assert (code, fields["result"]) == (boot.EXIT_UNEXPECTED_TOPOLOGY, "unexpected-topology")
    assert runner.calls == [] and probes == []
    after = tree_snapshot(Path(host.sw.trust_root))
    after.pop(host.sw.lock_path, None)
    assert after == before


@linux_only
@pytest.mark.parametrize("links", [0, 2])
def test_site_must_be_enabled_exactly_once(host, links):
    enabled = Path(host.sites_enabled)
    if links == 0:
        (enabled / "default").unlink()
    else:
        (enabled / "second").symlink_to(host.sw.site_path)
    code, fields, runner, _probes, _ = apply(host)
    assert (code, runner.calls) == (boot.EXIT_UNEXPECTED_TOPOLOGY, [])
    assert Path(host.sw.site_path).read_bytes() == LIVE


@linux_only
def test_invalid_live_config_blocks_bootstrap(host):
    code, fields, runner, _probes, _ = apply(host, runner=Runner(test=1))
    assert (code, fields["result"], runner.calls) == (boot.EXIT_CURRENT_INVALID, "current-nginx-invalid", ["test"])
    assert Path(host.sw.site_path).read_bytes() == LIVE
    assert not os.path.exists(host.backup_dir)


@linux_only
def test_certbot_gate_failure_changes_nothing(host):
    def gate(candidate, bcfg):
        raise boot.BootError(boot.EXIT_CERTBOT_GATE, "certbot-gate-failed", "vhosts differ")

    code, fields, runner, _probes, _ = apply(host, gate=gate)
    assert (code, fields["result"], runner.calls) == (boot.EXIT_CERTBOT_GATE, "certbot-gate-failed", ["test"])
    assert Path(host.sw.site_path).read_bytes() == LIVE
    assert not os.path.exists(host.sw.mapping_path)


@linux_only
def test_candidate_rejection_restores_exact_site_without_reload(host):
    code, fields, runner, _probes, _ = apply(host, runner=Runner(test=[0, 1, 0]))
    assert (code, fields["result"], fields["restored"]) == (boot.EXIT_CANDIDATE_INVALID, "candidate-nginx-invalid", "yes")
    assert runner.calls == ["test", "test", "test"]
    assert Path(host.sw.site_path).read_bytes() == LIVE


@linux_only
def test_reload_failure_restores_and_reloads_known_good(host):
    code, fields, runner, _probes, _ = apply(host, runner=Runner(reload=[1, 0]))
    assert (code, fields["known_good_reload"]) == (boot.EXIT_RELOAD_FAILED, "yes")
    assert runner.calls == ["test", "test", "reload", "test", "reload"]
    assert Path(host.sw.site_path).read_bytes() == LIVE


@linux_only
def test_failed_public_health_rolls_back(host):
    code, fields, runner, probes, _ = apply(host, probe=[200, 200, 502, 200, 200])
    assert (code, fields["result"], fields["health"]) == (boot.EXIT_VERIFY_FAILED, "verify-failed", "200,200,502,200,200")
    assert runner.calls == ["test", "test", "reload", "test", "reload"]
    assert Path(host.sw.site_path).read_bytes() == LIVE


@linux_only
def test_failed_rollback_reports_manual_action(host):
    code, fields, _runner, _probes, notes = apply(host, runner=Runner(reload=[1, 1]))
    assert code == boot.EXIT_RESTORE_FAILED and fields["known_good_reload"] == "no"
    assert any("MANUAL ACTION" in n and fields["backup"] in n for n in notes)
    assert Path(fields["backup"]).read_bytes() == LIVE


@linux_only
def test_bootstrap_requires_root_and_exact_operation(host):
    for argv in ([], ["apply", "x"], ["APPLY"], ["rollback"], ["--site=/tmp/x"]):
        code, fields, _notes = boot.run(argv, host, boot.BootDeps(run=Runner()))
        assert (code, fields["result"]) == (boot.EXIT_USAGE, "usage")
    import dataclasses
    other = dataclasses.replace(host, sw=dataclasses.replace(host.sw, trusted_uid=os.geteuid() + 1))
    code, fields, _notes = boot.run(["apply"], other, boot.BootDeps(run=Runner()))
    assert code == boot.EXIT_NOT_ROOT
    assert Path(host.sw.site_path).read_bytes() == LIVE


def test_bootstrap_production_constants():
    cfg = boot.BootConfig()
    assert cfg.sw == sw.Config()
    assert [(a[1], a[2]) for a in cfg.artifacts] == [
        ("/usr/local/sbin/axisai-switch-web-slot", 0o755),
        ("/etc/axisai/web-slots.conf", 0o644),
        ("/etc/nginx/axisai/active-web-upstream.conf", 0o644)]
    assert (cfg.sites_enabled, cfg.nginx_root, cfg.backup_dir) == (
        "/etc/nginx/sites-enabled", "/etc/nginx", "/root/axisai-r6-02b")
    assert cfg.health_url == "https://fitx-chatbot.duckdns.org/health"
    assert cfg.repo_dir == str(ROOT)
