"""PRIV-LOG-01 live proof on a disposable, unprivileged nginx (CI job r6-nginx-integration).

Runs only with FITX_R6_NGINX_IT=1, on the R6 harness: the exact audited live
Certbot site (tests/fixtures/r6_nginx), migrated as production is since R6-02B,
under an http{} block whose ``access_log`` uses the built-in ``combined``
format exactly like the distribution nginx.conf on the host. The same synthetic
requests go through the site before and after the reviewed edit
(tests/priv_log_support.patch_site); only synthetic canaries are used.
"""
import http.client
import os
import re
from urllib.parse import quote

import pytest

from test_r6_nginx_integration import (  # noqa: F401  (module skips itself without FITX_R6_NGINX_IT=1)
    HOST, LIVE, _ctx, backends, boot, nginx, request, sw, wait_for)
import axisai_nginx_log_privacy as privacy  # noqa: E402  (scripts/ is on sys.path via the harness)
from priv_log_support import TEMPLATE, patch_site  # noqa: E402

MIGRATED = boot.transform(LIVE, sw.INCLUDE_PATH, 5000)
SEARCH = "PRIVLOG01-search tavuk göğsü"
PROVIDER = "PRIVLOG01-provider yulaf"
BARCODE = "8690000000017"
TOKEN = "PRIVLOG01-token"
REFERER = "https://axisaiapp.com/nutrition?PRIVLOG01-referer"
LINE = re.compile(
    r'^(?P<ip>\S+) - - \[[^\]]+\] "(?P<method>\S+) (?P<path>\S+) (?P<proto>\S+)" '
    r'(?P<status>\d{3}) (?P<bytes>\d+) "(?P<referer>[^"]*)" "(?P<ua>[^"]*)" '
    r'rt=(?P<rt>[\d.]+) uct=(?P<uct>\S+) uht=(?P<uht>\S+) urt=(?P<urt>\S+) '
    r'us=(?P<us>\S+) ua=(?P<upstream>\S+) lr=(?P<lr>\S+) rid=(?P<rid>[0-9a-f]{32}) host=(?P<host>\S+)$')


def runnable(ngx, site):
    """The site's real log path, pointed at the harness's own access log file."""
    return site.replace(b"/var/log/nginx/access.log", str(ngx.tmp / "var" / "access.log").encode())


def access_log(ngx):
    path = ngx.tmp / "var" / "access.log"
    return path.read_bytes() if path.exists() else b""


def exercise(ngx):
    """One food search, one barcode lookup, the backend's hairpin FatSecret
    call and a plain-HTTP search caught by Certbot's redirect server."""
    seen = []
    for path, headers in (
        ("/api/v1/nutrition/foods/search?q=" + quote(SEARCH),
         {"Authorization": "Bearer " + TOKEN, "Referer": REFERER}),
        ("/api/v1/nutrition/foods/barcode?code=" + BARCODE, {"Authorization": "Bearer " + TOKEN}),
        ("/fatsecret/rest/server.api?method=foods.search&search_expression=" + quote(PROVIDER)
         + "&format=json", {"Authorization": "Bearer " + TOKEN}),
    ):
        status, hdrs, body = request(ngx, path=path, headers=headers)
        seen.append((path, status, hdrs.get("x-backend"), body))
    status, hdrs, _ = request(ngx, path="/api/v1/nutrition/foods/search?q=" + quote(SEARCH), tls=False)
    seen.append(("http", status, hdrs.get("location"), None))
    return seen


def run_site(ngx, site):
    ngx.write_site(runnable(ngx, site))
    dump = ngx.dump()
    offset = len(access_log(ngx))
    ngx.start()
    seen = exercise(ngx)
    ngx.stop()
    return seen, access_log(ngx)[offset:].decode("utf-8", "replace"), dump


def test_query_text_leaves_the_access_log_and_nothing_else_changes(nginx):
    ngx = nginx
    ngx.write_include(ngx.ports["legacy"])

    seen_before, log_before, dump_before = run_site(ngx, MIGRATED)
    # synthetic leak reproduction on the audited live site: every query reaches the log
    for canary in (quote(SEARCH), quote(PROVIDER), "code=" + BARCODE, "PRIVLOG01-referer"):
        assert canary in log_before, canary
    assert privacy.check(dump_before, TEMPLATE)[0]                 # checker flags it too

    seen_after, log_after, dump_after = run_site(ngx, patch_site(MIGRATED))
    assert privacy.check(dump_after, TEMPLATE)[0] == []

    # responses, routing, forwarded query and auth headers are unchanged
    assert seen_after == seen_before
    assert [s[1:3] for s in seen_after] == [
        (200, "legacy"), (200, "legacy"), (200, "fatsecret"), (301, f"https://{HOST}" + seen_after[0][0])]

    for forbidden in ("PRIVLOG01", BARCODE, "search_expression", "q=", "code=", "Bearer", "?"):
        assert forbidden not in log_after, forbidden
    lines = [LINE.match(line) for line in log_after.splitlines()]
    assert len(lines) == 4 and all(lines), log_after
    search, barcode, provider, redirect = (m.groupdict() for m in lines)
    legacy, fatsecret = f"127.0.0.1:{ngx.ports['legacy']}", f"127.0.0.1:{ngx.ports['fatsecret']}"
    assert (search["method"], search["path"], search["proto"], search["status"]) == (
        "GET", "/api/v1/nutrition/foods/search", "HTTP/1.1", "200")
    assert (search["us"], search["upstream"], search["lr"], search["host"], search["ip"]) == (
        "200", legacy, "PASSED", HOST, "127.0.0.1")
    assert search["referer"] == "https://axisaiapp.com/nutrition"
    assert float(search["rt"]) >= 0 and search["urt"] != "-" and search["uct"] != "-"
    assert (barcode["path"], barcode["status"], barcode["upstream"]) == (
        "/api/v1/nutrition/foods/barcode", "200", legacy)
    assert (provider["path"], provider["status"], provider["us"], provider["upstream"], provider["lr"]) == (
        "/fatsecret/rest/server.api", "200", "200", fatsecret, "-")
    assert (redirect["path"], redirect["status"], redirect["us"]) == (
        "/api/v1/nutrition/foods/search", "301", "-")
    assert len({m["rid"] for m in (search, barcode, provider, redirect)}) == 4


def test_rate_limiting_still_applies_and_rejections_stay_observable(nginx):
    ngx = nginx
    ngx.write_include(ngx.ports["legacy"])
    ngx.write_site(runnable(ngx, patch_site(MIGRATED)))
    ngx.start()
    err_offset = ngx.err.stat().st_size if ngx.err.exists() else 0
    offset = len(access_log(ngx))
    path = "/api/v1/nutrition/foods/search?q=" + quote("PRIVLOG01-burst")
    statuses = []
    conn = http.client.HTTPSConnection("127.0.0.1", ngx.ports["https"], context=_ctx(), timeout=15)
    try:
        while statuses.count(503) < 5 and len(statuses) < 1000:
            conn.request("GET", path, headers={"Host": HOST})
            resp = conn.getresponse()
            resp.read()
            statuses.append(resp.status)
    finally:
        conn.close()
    ngx.stop()
    assert statuses.count(503) >= 5 and statuses[0] == 200, statuses[:5]

    log = access_log(ngx)[offset:].decode()
    assert "PRIVLOG01" not in log and "q=" not in log
    rows = [LINE.match(line).groupdict() for line in log.splitlines()]
    assert len(rows) == len(statuses)
    rejected = [r for r in rows if r["lr"] == "REJECTED"]
    assert len(rejected) == statuses.count(503)
    assert {(r["status"], r["us"], r["path"]) for r in rejected} == {
        ("503", "-", "/api/v1/nutrition/foods/search")}
    # RESIDUAL R1 (deploy/nginx/README.md "Access log"): nginx appends the raw
    # request line to every error-log entry and that cannot be configured, so a
    # rejected search still writes its query to error.log. Pinned so the
    # documentation stays true; if this starts failing, update the README.
    assert quote("PRIVLOG01-burst") in ngx.err.read_bytes()[err_offset:].decode("utf-8", "replace")


@pytest.mark.skipif(os.environ.get("FITX_R6_CERTBOT_GATE") != "1", reason="needs certbot-nginx (CI job)")
def test_certbot_parser_sees_identical_vhosts_after_the_access_log_edit(tmp_path):
    root = tmp_path / "nginx"
    (root / "sites-available").mkdir(parents=True)
    (root / "sites-enabled").mkdir()
    (root / "nginx.conf").write_text(f"events {{}}\nhttp {{\n    include {root}/sites-enabled/*;\n}}\n")
    site = root / "sites-available" / "fitx"
    site.write_bytes(MIGRATED)
    (root / "sites-enabled" / "default").symlink_to(site)
    (tmp_path / "backup").mkdir()
    bcfg = boot.BootConfig(sw=sw.Config(site_path=str(site)), sites_enabled=str(root / "sites-enabled"),
                           nginx_root=str(root), backup_dir=str(tmp_path / "backup"))
    boot.certbot_gate(patch_site(MIGRATED), bcfg)    # renewals parse the edited site identically
    assert any(ssl_on and HOST in names for names, ssl_on, _a, _f in boot._vhost_signature(str(root)))
