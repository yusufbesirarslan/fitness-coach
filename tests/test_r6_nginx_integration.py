"""R6-02A live proof on a disposable, unprivileged nginx (CI job r6-nginx-integration).

Runs only with FITX_R6_NGINX_IT=1. Everything lives under pytest's tmp_path:
a private nginx prefix laid out like /etc/nginx, a self-signed certificate and
three trivial loopback HTTP backends standing in for legacy / blue / green.
The system nginx, /etc and production are never touched.

The REAL helper (scripts/axisai_switch_web_slot.py) and bootstrap run with a
test Config whose paths point into tmp_path and whose nginx commands address
the private instance (``nginx -p <prefix> -c ... -t`` / ``-s reload``). Their
production argv (/usr/sbin/nginx -t -q, systemctl reload nginx) is pinned by
tests/test_r6_nginx_route_control.py.

The Certbot site under test is the exact live site audited on 2026-10-08
(tests/fixtures/r6_nginx). To run it unprivileged, one test-only rewrite is
applied identically to the before AND after configuration: listen ports,
certificate/dhparam paths and backend ports. Everything else is the real text.

    FITX_R6_NGINX_IT=1 FITX_R6_NGINX_BIN=/usr/sbin/nginx \
        python -m pytest tests/test_r6_nginx_integration.py -v
"""
import dataclasses
import difflib
import hashlib
import http.client
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

if os.environ.get("FITX_R6_NGINX_IT") != "1":
    pytest.skip("set FITX_R6_NGINX_IT=1 (disposable nginx; CI job r6-nginx-integration)",
                allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import axisai_nginx_bootstrap as boot  # noqa: E402

sw = boot.sw
NGINX = os.environ.get("FITX_R6_NGINX_BIN", "/usr/sbin/nginx")
LIVE = (ROOT / "tests" / "fixtures" / "r6_nginx" / "fitx.live-2026-10-08.conf").read_bytes()
HOST = "fitx-chatbot.duckdns.org"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- trivial backends -------------------------------------------------------------

class Backend:
    """Loopback HTTP server that echoes exactly what nginx forwarded and can
    stream a slow response (to hold a connection open across a reload)."""

    def __init__(self, name):
        self.name = name
        backend = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                if self.path.startswith("/slow"):
                    chunks, delay = 12, 0.4
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("X-Backend", backend.name)
                    self.send_header("Content-Length", str(chunks * 8))
                    self.end_headers()
                    for i in range(chunks):
                        self.wfile.write(f"{backend.name[:1]}{i:06d}\n".encode())
                        self.wfile.flush()
                        time.sleep(delay)
                    return
                payload = json.dumps({
                    "backend": backend.name, "method": self.command, "uri": self.path,
                    "version": self.request_version,
                    "headers": sorted([k.lower(), v] for k, v in self.headers.items()),
                    "body_len": len(body), "body_sha": hashlib.sha256(body).hexdigest(),
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("X-Backend", backend.name)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def backends():
    made = {name: Backend(name) for name in ("legacy", "blue", "green", "fatsecret")}
    yield made
    for b in made.values():
        b.close()


# --- disposable nginx ---------------------------------------------------------------

def make_cert(directory):
    key, cert = directory / "privkey.pem", directory / "fullchain.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", f"/CN={HOST}", "-keyout", str(key), "-out", str(cert)],
                   check=True, capture_output=True, timeout=60)
    return cert, key


def runnable_site(site, ports, cert, key, options, include_path):
    """The single test-only rewrite, applied identically before and after."""
    out = site
    for old, new in (
        (b"listen 443 ssl; # managed by Certbot", f"listen 127.0.0.1:{ports['https']} ssl; # managed by Certbot".encode()),
        (b"listen [::]:443 ssl ipv6only=on; # managed by Certbot", b"# (ipv6 listen removed for the disposable test)"),
        (b"listen 80;", f"listen 127.0.0.1:{ports['http']};".encode()),
        (b"listen [::]:80;", b"# (ipv6 listen removed for the disposable test)"),
        (f"/etc/letsencrypt/live/{HOST}/fullchain.pem".encode(), str(cert).encode()),
        (f"/etc/letsencrypt/live/{HOST}/privkey.pem".encode(), str(key).encode()),
        (b"include /etc/letsencrypt/options-ssl-nginx.conf;", f"include {options};".encode()),
        (b"ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;", b"# (dhparam omitted for the disposable test)"),
        (b"127.0.0.1:5000", f"127.0.0.1:{ports['legacy']}".encode()),
        (b"127.0.0.1:3000", f"127.0.0.1:{ports['fatsecret']}".encode()),
        (sw.INCLUDE_PATH.encode(), str(include_path).encode()),
    ):
        assert old in out or old in (sw.INCLUDE_PATH.encode(), b"127.0.0.1:5000"), old
        out = out.replace(old, new)
    return out


class Nginx:
    def __init__(self, tmp, backends):
        self.root = tmp / "etc" / "nginx"
        for d in (tmp / "etc", self.root, self.root / "sites-available", self.root / "sites-enabled",
                  self.root / "axisai", self.root / "extra", tmp / "etc" / "axisai", tmp / "run",
                  tmp / "var", tmp / "tls"):
            d.mkdir(parents=True, exist_ok=True)
            d.chmod(0o755)
        tmp.chmod(0o755)
        self.tmp = tmp
        self.ports = {"https": free_port(), "http": free_port(),
                      **{name: b.port for name, b in backends.items()}}
        self.cert, self.key = make_cert(tmp / "tls")
        self.options = tmp / "tls" / "options-ssl-nginx.conf"
        self.options.write_text("ssl_session_cache shared:le_nginx_SSL:1m;\nssl_protocols TLSv1.2 TLSv1.3;\n")
        self.include = self.root / "axisai" / "active-web-upstream.conf"
        self.site = self.root / "sites-available" / "fitx"
        self.conf = self.root / "nginx.conf"
        self.err = tmp / "var" / "error.log"
        v = tmp / "var"
        self.conf.write_text(
            f"pid {tmp}/run/nginx.pid;\nerror_log {self.err} info;\nworker_processes 2;\n"
            "events { worker_connections 128; }\n"
            "http {\n"
            f"    access_log {v}/access.log;\n"
            f"    client_body_temp_path {v}/cbt; proxy_temp_path {v}/pt; fastcgi_temp_path {v}/ft;\n"
            f"    uwsgi_temp_path {v}/ut; scgi_temp_path {v}/st;\n"
            f"    include {self.root}/extra/*.conf;\n"
            f"    include {self.root}/sites-enabled/*;\n"
            "}\n")
        (self.root / "sites-enabled" / "default").symlink_to(self.site)
        self.base = (NGINX, "-p", str(self.root), "-c", str(self.conf), "-e", str(self.err))
        self.started = False

    def write_site(self, data):
        self.site.write_bytes(runnable_site(data, self.ports, self.cert, self.key, self.options, self.include))
        self.site.chmod(0o644)

    def write_include(self, port):
        self.include.write_bytes(sw.render_include(port))
        self.include.chmod(0o644)

    def write_mapping(self, path):
        path.write_text("".join(f"{s} 127.0.0.1:{self.ports[s]}\n" for s in sw.STATES))
        path.chmod(0o644)

    @property
    def test_argv(self):
        return self.base + ("-t", "-q")

    @property
    def reload_argv(self):
        return self.base + ("-s", "reload")

    def start(self):
        subprocess.run(self.test_argv, check=True, capture_output=True, timeout=30)
        subprocess.run(self.base, check=True, capture_output=True, timeout=30)
        self.started = True
        wait_for(lambda: self.master_pid() is not None, "nginx master")

    def stop(self):
        if self.started:
            pid = self.master_pid()
            subprocess.run(self.base + ("-s", "quit"), capture_output=True, timeout=30)
            if pid:
                wait_for(lambda: not Path(f"/proc/{pid}").exists(), "nginx exit", timeout=15)
            self.started = False

    def master_pid(self):
        try:
            return int((self.tmp / "run" / "nginx.pid").read_text().strip())
        except (OSError, ValueError):
            return None

    def workers(self):
        master = self.master_pid()
        out = set()
        for entry in Path("/proc").iterdir():
            if entry.name.isdigit():
                try:
                    fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                except OSError:
                    continue
                if int(fields[1]) == master:
                    out.add(int(entry.name))
        return out

    def dump(self):
        return subprocess.run(self.base + ("-T",), check=True, capture_output=True, timeout=30).stdout

    def sw_config(self):
        mapping = self.tmp / "etc" / "axisai" / "web-slots.conf"
        self.write_mapping(mapping)
        return sw.Config(mapping_path=str(mapping), include_path=str(self.include), site_path=str(self.site),
                         lock_path=str(self.tmp / "run" / "route.lock"),
                         pending_path=str(self.tmp / "run" / "route.pending"),
                         nginx_test=self.test_argv, nginx_reload=self.reload_argv,
                         trusted_uid=os.geteuid(), trust_root=str(self.tmp))


@pytest.fixture
def nginx(tmp_path, backends):
    if not shutil.which(NGINX) and not Path(NGINX).exists():
        pytest.fail(f"nginx binary {NGINX} not found (FITX_R6_NGINX_IT=1 requires it)")
    instance = Nginx(tmp_path, backends)
    yield instance
    instance.stop()


def wait_for(predicate, what, timeout=10.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {what}")


def _ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def request(ngx, method="GET", path="/api/v1/health-probe", body=None, headers=None, tls=True):
    if tls:
        conn = http.client.HTTPSConnection("127.0.0.1", ngx.ports["https"], context=_ctx(), timeout=15)
    else:
        conn = http.client.HTTPConnection("127.0.0.1", ngx.ports["http"], timeout=15)
    try:
        conn.request(method, path, body=body, headers={"Host": HOST, **(headers or {})})
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
    finally:
        conn.close()


def backend_of(ngx):
    status, headers, _ = request(ngx)
    assert status == 200, status
    return headers["x-backend"]


def switch(cfg, target):
    out, err = [], []
    code = sw.main(["switch", target], cfg, sw.Deps(out=out.append, err=err.append))
    return code, dict(kv.split("=", 1) for kv in out[0].split(" ")), err


def status(cfg):
    out = []
    code = sw.main(["status"], cfg, sw.Deps(out=out.append, err=lambda s: None))
    return code, dict(kv.split("=", 1) for kv in out[0].split(" "))


@pytest.fixture
def routed(nginx):
    """The disposable nginx running the MIGRATED live site, include -> legacy."""
    nginx.write_site(boot.transform(LIVE, sw.INCLUDE_PATH, 5000))
    nginx.write_include(nginx.ports["legacy"])
    cfg = nginx.sw_config()
    nginx.start()
    return nginx, cfg


# --- 13. real routing switch -----------------------------------------------------------------

def test_real_nginx_switch_routes_new_traffic_and_rolls_back_to_legacy(routed):
    ngx, cfg = routed
    assert status(cfg) == (0, {"op": "status", "state": "legacy",
                               "backend": f"127.0.0.1:{ngx.ports['legacy']}", "result": "ok"})
    assert backend_of(ngx) == "legacy"

    code, fields, err = switch(cfg, "blue")
    assert (code, fields["from"], fields["to"], fields["result"]) == (0, "legacy", "blue", "switched"), err
    wait_for(lambda: backend_of(ngx) == "blue", "new traffic on blue")
    assert {backend_of(ngx) for _ in range(10)} == {"blue"}
    assert status(cfg)[1]["state"] == "blue"

    # first-cutover rollback: back to the legacy backend through the same primitive
    code, fields, err = switch(cfg, "legacy")
    assert (code, fields["from"], fields["result"]) == (0, "blue", "switched"), err
    wait_for(lambda: backend_of(ngx) == "legacy", "new traffic back on legacy")
    assert {backend_of(ngx) for _ in range(10)} == {"legacy"}

    for target in ("green", "blue", "green"):
        code, fields, err = switch(cfg, target)
        assert code == 0, err
        wait_for(lambda: backend_of(ngx) == target, f"traffic on {target}")
    assert status(cfg)[1]["state"] == "green"
    assert switch(cfg, "green")[1]["result"] == "already-active"


# --- 14. graceful reload ----------------------------------------------------------------------------

def test_in_flight_response_survives_the_switch_while_new_traffic_moves(routed):
    ngx, cfg = routed
    result = {}
    first_chunk = threading.Event()

    def slow_client():
        conn = http.client.HTTPSConnection("127.0.0.1", ngx.ports["https"], context=_ctx(), timeout=30)
        conn.request("GET", "/slow", headers={"Host": HOST})
        resp = conn.getresponse()
        result["backend"] = resp.getheader("X-Backend")
        chunks = [resp.read(8)]
        first_chunk.set()
        while True:
            piece = resp.read(8)
            if not piece:
                break
            chunks.append(piece)
        result["status"] = resp.status
        result["body"] = b"".join(chunks)
        result["done_at"] = time.monotonic()
        conn.close()

    thread = threading.Thread(target=slow_client)
    thread.start()
    assert first_chunk.wait(10), "slow response never started"
    old_workers = ngx.workers()
    assert old_workers

    code, fields, err = switch(cfg, "blue")
    switched_at = time.monotonic()
    assert (code, fields["result"]) == (0, "switched"), err
    wait_for(lambda: backend_of(ngx) == "blue", "new connection on blue")
    new_seen_at = time.monotonic()
    assert {backend_of(ngx) for _ in range(5)} == {"blue"}
    # the old generation is still draining the in-flight response
    assert thread.is_alive(), "slow response finished before the switch could be observed"
    assert old_workers & ngx.workers(), "old workers were not kept alive for the in-flight request"

    thread.join(30)
    assert not thread.is_alive()
    expected = b"".join(f"l{i:06d}\n".encode() for i in range(12))
    assert (result["status"], result["backend"], result["body"]) == (200, "legacy", expected)
    assert result["done_at"] > new_seen_at > switched_at
    # once drained, the old workers exit and only the new generation remains
    wait_for(lambda: not (old_workers & ngx.workers()), "old workers to exit", timeout=15)
    assert ngx.workers()


# --- current-config preflight on a real nginx ---------------------------------------------------

def test_invalid_live_config_blocks_the_switch(routed):
    ngx, cfg = routed
    bad = ngx.root / "extra" / "broken.conf"
    bad.write_text("this_is_not_a_directive on;\n")
    inode = os.stat(ngx.include).st_ino
    code, fields, _err = switch(cfg, "blue")
    assert (code, fields["result"], fields["changed"]) == (sw.EXIT_CURRENT_INVALID, "current-nginx-invalid", "no")
    assert os.stat(ngx.include).st_ino == inode
    assert ngx.include.read_bytes() == sw.render_include(ngx.ports["legacy"])
    assert backend_of(ngx) == "legacy"
    bad.unlink()
    assert switch(cfg, "blue")[0] == 0


# --- behaviour-preserving bootstrap on the real Certbot site -------------------------------------

REPRESENTATIVE = [
    ("GET", "/api/v1/nutrition/diary/today?date=2026-10-08&q=a%2Fb%20c", None,
     {"Authorization": "Bearer test-token", "Accept": "application/json"}),
    ("POST", "/api/v1/workouts/log", json.dumps({"sets": list(range(2000))}).encode(),
     {"Content-Type": "application/json"}),
    ("PUT", "/api/v1/profile", b"x" * (3 * 1024 * 1024), {"Content-Type": "application/octet-stream"}),
    ("GET", "/api/v1/coach/stream", None, {"Upgrade": "websocket", "Connection": "Upgrade"}),
    ("GET", "/dashboard", None, {"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "http"}),
    ("DELETE", "/api/v1/meals/123", None, {}),
    ("GET", "/fatsecret/rest/server.api?method=foods.search", None, {}),
    ("GET", "/fatsecret/other", None, {}),
]
RESPONSE_HEADERS = ("strict-transport-security", "x-frame-options", "x-content-type-options",
                    "referrer-policy", "content-type", "location", "server")


def observe(ngx):
    seen = []
    for method, path, body, headers in REPRESENTATIVE:
        st, hdrs, data = request(ngx, method, path, body, headers)
        seen.append((method, path, st, {k: hdrs.get(k) for k in RESPONSE_HEADERS},
                     json.loads(data) if hdrs.get("content-type") == "application/json" else data))
    try:
        st = request(ngx, "POST", "/api/v1/upload", b"y" * (26 * 1024 * 1024))[0]
    except (ConnectionError, http.client.HTTPException) as exc:   # nginx may cut the upload short
        st = type(exc).__name__
    seen.append(("POST", "oversize", st, None, None))
    st, hdrs, _ = request(ngx, tls=False, path="/api/v1/x")
    seen.append(("GET", "http", st, hdrs.get("location"), None))
    return seen


def test_bootstrap_transformation_is_behaviour_preserving(nginx):
    ngx = nginx
    ngx.write_include(ngx.ports["legacy"])
    ngx.write_site(LIVE)
    ngx.start()
    before = observe(ngx)
    dump_before = ngx.dump()
    ngx.stop()

    ngx.write_site(boot.transform(LIVE, sw.INCLUDE_PATH, 5000))
    ngx.start()
    after = observe(ngx)
    dump_after = ngx.dump()

    assert after == before
    by_path = {entry[1]: entry for entry in before}
    api = by_path["/api/v1/nutrition/diary/today?date=2026-10-08&q=a%2Fb%20c"][4]
    headers = dict(api["headers"])
    assert api["backend"] == "legacy" and api["uri"] == "/api/v1/nutrition/diary/today?date=2026-10-08&q=a%2Fb%20c"
    assert (headers["host"], headers["x-real-ip"], headers["x-forwarded-for"], headers["x-forwarded-proto"]) == (
        HOST, "127.0.0.1", "127.0.0.1", "https")
    assert headers["connection"] == "upgrade" and api["version"] == "HTTP/1.1"   # live hard-coded value kept
    assert headers["authorization"] == "Bearer test-token"
    ws = dict(by_path["/api/v1/coach/stream"][4]["headers"])
    assert ws["upgrade"] == "websocket"
    xff = dict(by_path["/dashboard"][4]["headers"])
    assert (xff["x-forwarded-for"], xff["x-forwarded-proto"]) == ("203.0.113.7, 127.0.0.1", "https")
    put = by_path["/api/v1/profile"][4]
    assert (put["body_len"], put["body_sha"]) == (3 * 1024 * 1024, hashlib.sha256(b"x" * (3 * 1024 * 1024)).hexdigest())
    assert by_path["/fatsecret/rest/server.api?method=foods.search"][4]["backend"] == "fatsecret"
    assert by_path["/fatsecret/other"][4]["backend"] == "legacy"
    assert by_path["oversize"][2] in (413, "ConnectionResetError", "BrokenPipeError")   # client_max_body_size 25m
    assert by_path["http"][2:4] == (301, f"https://{HOST}/api/v1/x")

    # the effective configuration differs only by the routing indirection
    removed, added = [], []
    a, b = dump_before.decode().splitlines(), dump_after.decode().splitlines()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        removed += a[i1:i2] if tag in ("replace", "delete") else []
        added += b[j1:j2] if tag in ("replace", "insert") else []
    assert removed == [f"        proxy_pass http://127.0.0.1:{ngx.ports['legacy']};"]
    allowed = set(boot.upstream_block(str(ngx.include)).decode().splitlines()) | {
        "        proxy_pass http://axisai_web;", f"# configuration file {ngx.include}:",
        f"server 127.0.0.1:{ngx.ports['legacy']};", ""}
    assert set(added) <= allowed, set(added) - allowed


def test_streaming_is_unbuffered_before_and_after(nginx):
    """proxy_buffering off survives: the first bytes of a slow response reach
    the client long before the backend finishes."""
    ngx = nginx
    ngx.write_include(ngx.ports["legacy"])
    for site in (LIVE, boot.transform(LIVE, sw.INCLUDE_PATH, 5000)):
        ngx.write_site(site)
        ngx.start()
        conn = http.client.HTTPSConnection("127.0.0.1", ngx.ports["https"], context=_ctx(), timeout=30)
        started = time.monotonic()
        conn.request("GET", "/slow", headers={"Host": HOST})
        resp = conn.getresponse()
        assert resp.read(8) == b"l000000\n"
        first = time.monotonic() - started
        resp.read()
        total = time.monotonic() - started
        conn.close()
        assert first < 1.5 and total > 4.0, (first, total)
        ngx.stop()


# --- end-to-end R6-02B rehearsal: bootstrap apply on the live site, then switch ----------------

def test_bootstrap_apply_then_switch_on_real_nginx(nginx, tmp_path):
    ngx = nginx
    ngx.write_site(LIVE)                       # legacy-direct, include not referenced
    ngx.start()
    assert backend_of(ngx) == "legacy"
    cfg = ngx.sw_config()
    mapping_text = Path(cfg.mapping_path).read_bytes()
    os.unlink(cfg.mapping_path)                # bootstrap installs it

    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "deploy" / "nginx").mkdir(parents=True)
    (repo / "scripts" / "axisai_switch_web_slot.py").write_bytes((ROOT / "scripts/axisai_switch_web_slot.py").read_bytes())
    (repo / "deploy/nginx/web-slots.conf").write_bytes(mapping_text)        # test ports
    (repo / "deploy/nginx/active-web-upstream.conf").write_bytes(sw.render_include(ngx.ports["legacy"]))
    (tmp_path / "sbin").mkdir(mode=0o755)
    artifacts = (("scripts/axisai_switch_web_slot.py", str(tmp_path / "sbin" / "axisai-switch-web-slot"), 0o755),
                 ("deploy/nginx/web-slots.conf", cfg.mapping_path, 0o644),
                 ("deploy/nginx/active-web-upstream.conf", cfg.include_path, 0o644))
    (tmp_path / "root").mkdir(mode=0o700)
    bcfg = boot.BootConfig(sw=dataclasses.replace(cfg), repo_dir=str(repo), artifacts=artifacts,
                           sites_enabled=str(ngx.root / "sites-enabled"), nginx_root=str(ngx.root),
                           backup_dir=str(tmp_path / "root" / "axisai-r6-02b"), health_url="unused")
    # The site the bootstrap sees must reference the private include path.
    assert sw.classify_site(ngx.site.read_bytes(), cfg.include_path, ngx.ports["legacy"]).kind == sw.TOPOLOGY_LEGACY

    probes = []

    def probe(_url):
        st, hdrs, _ = request(ngx, path="/health")
        probes.append(hdrs.get("x-backend"))
        return st

    gate = boot.certbot_gate if os.environ.get("FITX_R6_CERTBOT_GATE") == "1" else (lambda c, b: None)
    deps = boot.BootDeps(probe=probe, gate=gate, sleep=time.sleep)
    code, fields, notes = boot.run(["apply"], bcfg, deps)
    assert code == 0, (fields, notes)
    assert fields["result"] == "migrated" and fields["state"] == "legacy"
    assert probes == ["legacy"] * boot.HEALTH_SAMPLES
    assert Path(fields["backup"]).read_bytes() != ngx.site.read_bytes()
    assert sw.classify_site(ngx.site.read_bytes(), cfg.include_path, ngx.ports["legacy"]).kind == sw.TOPOLOGY_MIGRATED

    assert boot.run(["apply"], bcfg, deps)[1]["result"] == "already-migrated"
    assert status(cfg)[1]["state"] == "legacy"
    assert switch(cfg, "blue")[0] == 0
    wait_for(lambda: backend_of(ngx) == "blue", "traffic on blue after bootstrap")
    assert switch(cfg, "legacy")[0] == 0
    wait_for(lambda: backend_of(ngx) == "legacy", "traffic back on legacy")


@pytest.mark.skipif(os.environ.get("FITX_R6_CERTBOT_GATE") != "1", reason="needs certbot-nginx (CI job)")
def test_certbot_parser_sees_identical_vhosts_after_transformation(tmp_path):
    root = tmp_path / "nginx"
    (root / "sites-available").mkdir(parents=True)
    (root / "sites-enabled").mkdir()
    (root / "nginx.conf").write_text(f"events {{}}\nhttp {{\n    include {root}/sites-enabled/*;\n}}\n")
    site = root / "sites-available" / "fitx"
    site.write_bytes(LIVE)
    (root / "sites-enabled" / "default").symlink_to(site)
    (tmp_path / "backup").mkdir()
    bcfg = boot.BootConfig(sw=sw.Config(site_path=str(site)), sites_enabled=str(root / "sites-enabled"),
                           nginx_root=str(root), backup_dir=str(tmp_path / "backup"))
    candidate = boot.transform(LIVE, sw.INCLUDE_PATH, 5000)
    boot.certbot_gate(candidate, bcfg)            # identical vhosts: passes
    sig = boot._vhost_signature(str(root))
    assert any(ssl_on and HOST in names for names, ssl_on, _addrs, _f in sig)
    broken = candidate.replace(b"listen 443 ssl; # managed by Certbot", b"listen 8443; # changed")
    with pytest.raises(boot.BootError) as exc:
        boot.certbot_gate(broken, bcfg)
    assert exc.value.result == "certbot-gate-failed"
