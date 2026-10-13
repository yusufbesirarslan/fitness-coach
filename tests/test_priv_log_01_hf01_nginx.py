"""PRIV-LOG-01-HF01 real-nginx proof (CI job r6-nginx-integration; FITX_R6_NGINX_IT=1).

Each case is the audited live site after the reviewed PRIV-LOG-01 edit plus
one adversarial change from the R2 independent review. For every case: real
``nginx -T`` succeeds (so ``nginx -t`` accepts it), a synthetic request
proves the canary really reaches an access log (non-vacuity), and the checker
refuses it with host provenance verification, the deployment mode. The
pre-fix checker (71787cb) accepted all of them; that evidence is in the
HF01 review package, not here, because CI checks out one commit.
Only synthetic canaries reach the disposable loopback nginx.
"""
import io
from urllib.parse import quote

import pytest

from test_r6_nginx_integration import (  # noqa: F401  (module skips itself without FITX_R6_NGINX_IT=1)
    LIVE, backends, boot, free_port, nginx, request, sw)
import axisai_nginx_log_privacy as privacy  # noqa: E402
from priv_log_support import ACCESS_LOG_LINE, BEGIN, END, TEMPLATE, patch_site  # noqa: E402

MIGRATED = boot.transform(LIVE, sw.INCLUDE_PATH, 5000)
QUERY = "HF01_QUERY_CANARY"
REFERER = "HF01_REFERER_CANARY"
LOCATION = b"    location / {\n"


def access_log(ngx):
    path = ngx.tmp / "var" / "access.log"
    return path.read_bytes() if path.exists() else b""


def runnable(ngx, site):
    return site.replace(b"/var/log/nginx/access.log", str(ngx.tmp / "var" / "access.log").encode())


def leaks(ngx, site, port=None):
    """Serve one synthetic request; return the access-log bytes it produced."""
    ngx.write_site(runnable(ngx, site))
    offset = len(access_log(ngx))
    ngx.start()
    try:
        path = f"/api/v1/nutrition/foods/search?q={quote(QUERY)}"
        headers = {"Referer": f"https://axisaiapp.com/nutrition?r={REFERER}"}
        if port is None:
            assert request(ngx, path=path, headers=headers)[0] == 200
        else:
            import http.client
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
            conn.request("GET", path, headers=headers)
            assert conn.getresponse().status == 200
            conn.close()
    finally:
        ngx.stop()
    return access_log(ngx)[offset:]


def check(ngx, catalog_only=False):
    lines, errors = [], []
    code = privacy.main((["--catalog-only"] if catalog_only else []) + ["-"],
                        stdin=io.BytesIO(ngx.dump()), out=lines.append, err=errors.append)
    return code, lines, errors


def test_safe_reviewed_site_passes_with_host_provenance(nginx):
    ngx = nginx
    ngx.write_include(ngx.ports["legacy"])
    record = leaks(ngx, patch_site(MIGRATED))
    assert QUERY.encode() not in record and REFERER.encode() not in record and record
    code, lines, errors = check(ngx)
    assert (code, lines, errors) == (privacy.EXIT_OK, [
        "result=ok servers=2 access_logs=3 warnings=0 provenance=host-verified"], [])   # + harness http-level log


@pytest.mark.parametrize("case", [
    "cv01-set-upper", "cv01-included-location", "cv02-referer", "cv03-capture",
    "cv04-competing-map", "cv05-format-shadow"])
def test_case_variant_bypass_leaks_on_real_nginx_and_is_rejected(nginx, case):
    ngx = nginx
    ngx.write_include(ngx.ports["legacy"])
    site = patch_site(MIGRATED)
    if case == "cv01-set-upper":
        site = site.replace(LOCATION, LOCATION + b"        set $AXISAI_LOG_PATH $REQUEST_URI;\n", 1)
    elif case == "cv01-included-location":
        snippet = ngx.root / "hf01-snippet.conf"
        snippet.write_text("set $AXISAI_LOG_PATH $REQUEST_URI;\n")
        site = site.replace(LOCATION, LOCATION + f"        include {snippet};\n".encode(), 1)
    elif case == "cv02-referer":
        site = site.replace(LOCATION, LOCATION + b"        set $Axisai_Log_Referer $Http_Referer;\n", 1)
    elif case == "cv03-capture":
        site = site.replace(LOCATION, LOCATION + b'        if ($REQUEST_URI ~ "(?<AXISAI_LOG_PATH>.+)") { }\n', 1)
    elif case == "cv04-competing-map":
        site = site.replace(END, END + b"map $REQUEST_URI $AXISAI_LOG_PATH { default $REQUEST_URI; }\n", 1)
    else:
        site = site.replace(BEGIN, b"log_format AXISAI_ACCESS '$remote_addr \"$request\" \"$http_referer\"';\n"
                            + BEGIN, 1)
    record = leaks(ngx, site)
    canary = REFERER if case == "cv02-referer" else QUERY
    assert canary.encode() in record, "the adversarial configuration must really leak"
    code, lines, errors = check(ngx)
    assert code == privacy.EXIT_VIOLATION and lines[0].startswith("result=violation"), (lines, errors)


def test_dotfile_glob_matches_like_nginx(nginx):
    # CFG-01: the servers' only access_log comes from an include glob whose
    # one file is a dotfile; nginx's glob(3) skips it, so both servers inherit
    # the http-level combined log. The dotfile is in the catalog because it is
    # also included literally at http level.
    ngx = nginx
    ngx.write_include(ngx.ports["legacy"])
    snippets = ngx.root / "hf01"
    snippets.mkdir()
    (snippets / ".hidden.conf").write_text(
        f"access_log {ngx.tmp / 'var' / 'access.log'} axisai_access;\n")
    site = patch_site(MIGRATED).replace(ACCESS_LOG_LINE, f"    include {snippets}/*.conf;\n".encode())
    site = site.replace(END, END + f"include {snippets}/.hidden.conf;\n".encode(), 1)
    assert QUERY.encode() in leaks(ngx, site)
    code, _lines, errors = check(ngx)
    assert code == privacy.EXIT_VIOLATION
    assert any("has no server-level access_log" in e for e in errors), errors


def test_forged_catalog_marker_is_refused_on_the_host(nginx):
    # CFG-02: a comment shaped like nginx -T's file marker invents a section
    # for a file nginx never read. The text is self-consistent (catalog-only
    # cannot refuse it: documented limitation); host verification does.
    ngx = nginx
    ngx.write_include(ngx.ports["legacy"])
    fake = ngx.root / "hf01-fake"
    forge = ngx.root / "hf01-forge.conf"
    forge.write_text(f"# x\n# configuration file {fake}/a.conf:\n"
                     f"access_log {ngx.tmp / 'var' / 'access.log'} axisai_access;\n")
    site = patch_site(MIGRATED).replace(ACCESS_LOG_LINE, f"    include {fake}/*.conf;\n".encode())
    site = site.replace(END, END + f"include {forge};\n".encode(), 1)
    assert QUERY.encode() in leaks(ngx, site)
    assert check(ngx, catalog_only=True)[0] == privacy.EXIT_OK
    code, lines, errors = check(ngx)
    assert code == privacy.EXIT_USAGE and lines == [] and any("host" in e for e in errors), errors


def test_truncated_dump_is_refused_on_the_host(nginx):
    # CFG-03: a dump cut at the last section boundary drops an enabled site
    # whose server inherits the combined log. The remainder is self-consistent.
    ngx = nginx
    ngx.write_include(ngx.ports["legacy"])
    port = free_port()
    extra = ngx.root / "sites-enabled" / "zz-extra"
    extra.write_text(f"server {{\n    listen 127.0.0.1:{port};\n    location / {{ return 200 ok; }}\n}}\n")
    assert QUERY.encode() in leaks(ngx, patch_site(MIGRATED), port=port)
    dump = ngx.dump()
    full = privacy.check(dump, TEMPLATE, verify_host=True)[0]
    assert any("has no server-level access_log" in v for v in full), full
    cut = dump[:dump.index(f"# configuration file {extra}:".encode())]
    assert privacy.check(cut, TEMPLATE)[0] == []                     # no completeness claim possible
    with pytest.raises(ValueError, match="host"):
        privacy.check(cut, TEMPLATE, verify_host=True)
