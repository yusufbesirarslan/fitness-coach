"""All-origin parser and generation delta, without host nginx or production."""
import hashlib
import json

import pytest

from scripts import r6_ingress_admission as ingress


@pytest.fixture
def config(monkeypatch):
    files = {
        "/etc/nginx/nginx.conf": b"""events {} http {
            upstream axisai_web { include /etc/nginx/axisai/active-web-upstream.conf; }
            upstream fatsecret_proxy { server 127.0.0.1:3000; }
            include /etc/nginx/sites-enabled/fitx;
        }""",
        "/etc/nginx/axisai/active-web-upstream.conf": b"server 127.0.0.1:5001;\n",
        "/etc/nginx/sites-enabled/fitx": b"""
            server { listen 80; return 301 https://$host$request_uri; }
            server { listen 443 ssl; listen [::]:443 ssl;
                     location / { proxy_pass http://axisai_web; } }
            server { listen 127.0.0.1:3000;
                     location / { proxy_pass https://platform.fatsecret.com; } }
        """,
    }

    def dump(argv, seconds=10):
        assert argv == ["/usr/sbin/nginx", "-T", "-q"]
        return b"".join(b"# configuration file " + k.encode() + b":\n" + v + b"\n"
                        for k, v in files.items())

    monkeypatch.setattr(ingress, "run", dump)
    monkeypatch.setattr(ingress, "trusted_file", lambda p: (
        files[p], hashlib.sha256(files[p]).hexdigest()))
    monkeypatch.setattr(ingress.glob, "glob", lambda p: [p] if p in files else [])
    monkeypatch.setattr(ingress.os.path, "lexists", lambda p: False)
    return files


def test_all_origin_binding_allows_only_canonical_route_delta(config):
    before, ports = ingress.configuration("blue")
    assert ports == {80, 443, 3000}
    config["/etc/nginx/axisai/active-web-upstream.conf"] = b"server 127.0.0.1:5002;\n"
    assert ingress.configuration("green")[0] == before
    with pytest.raises(ingress.Hold):
        ingress.configuration("blue")


@pytest.mark.parametrize("extra", [
    b"server { listen 443; location /auth/register { proxy_pass http://127.0.0.1:5000; } }",
    b"server { listen 8443; location / { proxy_pass http://axisai_web; } }",
    b"server { listen 443 quic; location / { proxy_pass http://axisai_web; } }",
    b"server { listen 443; location / { proxy_pass http://foreign; } }",
    b"server { listen 443; location / { fastcgi_pass 127.0.0.1:9000; } }",
    b"server { listen 443; location / { content_by_lua 'return'; } }",
    b"server { listen 443; return 302 https://other.example/register; }",
])
def test_unqualified_direct_origin_or_handler_refuses(config, extra):
    config["/etc/nginx/sites-enabled/fitx"] += extra
    with pytest.raises(ingress.Hold):
        ingress.configuration("blue")


def test_pending_route_is_never_loaded_generation_proof(config, monkeypatch):
    monkeypatch.setattr(ingress.os.path, "lexists", lambda p: True)
    with pytest.raises(ingress.Hold, match="PENDING_ROUTE"):
        ingress.configuration("blue")


def test_other_config_changes_do_not_hide_behind_route_exclusion(config):
    before = ingress.configuration("blue")[0]
    config["/etc/nginx/sites-enabled/fitx"] += b"\n# unexplained change\n"
    assert ingress.configuration("blue")[0] != before


def test_readonly_snapshot_does_not_reload(config, monkeypatch, capsys):
    monkeypatch.setattr(ingress.os, "geteuid", lambda: 0)
    monkeypatch.setattr(ingress.sys, "argv", ["proof", "blue", "any"])
    monkeypatch.setattr(ingress, "nginx_processes", lambda: (
        {"pid": 1, "start_ticks": 10}, [{"pid": 2, "start_ticks": 20}]))
    monkeypatch.setattr(ingress, "socket_coverage", lambda *a: None)
    assert ingress.main() == 0
    assert json.loads(capsys.readouterr().out)["workers"] == [[2, 20]]
