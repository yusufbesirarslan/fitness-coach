"""PRIV-LOG-01-HF01: checker/nginx identifier and catalog semantics agree.

The R2 independent review showed configurations the checker accepted while a
real nginx wrote synthetic query/Referer canaries to the access log, because
nginx compares variable names, named captures and log-format references
case-insensitively (ngx_http_variables.c ngx_strncasecmp; access_log lookup
ngx_strcasecmp in ngx_http_log_module.c) and glob(3) never lets a wildcard
match a leading dot. The real-nginx counterpart of every case below is
tests/test_priv_log_01_hf01_nginx.py. Catalog bytes model a successful
``nginx -T``: "# configuration file <path>:\\n" + exact file bytes + "\\n",
in the order nginx first opens each file.
"""
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import axisai_nginx_log_privacy as privacy  # noqa: E402
from priv_log_support import TEMPLATE, source_catalog  # noqa: E402

LOCATION = b"    location / {\n"


def violations(site, **files):
    dump = source_catalog(site)
    for name, content in files.items():
        dump += f"# configuration file /etc/nginx/{name}.conf:\n{content}\n".encode()
    return privacy.check(dump, TEMPLATE)[0]


def in_location(directive):
    return TEMPLATE.replace(LOCATION, LOCATION + b"        " + directive + b"\n", 1)


def at_http(directive, before_block=False):
    anchor = b"# BEGIN axisai access log (PRIV-LOG-01)\n" if before_block else b"server {\n"
    return TEMPLATE.replace(anchor, directive + b"\n" + anchor, 1)


# --- CV-01..CV-05: every reproduced case-variant bypass is rejected -------------------------------

@pytest.mark.parametrize("site, files", [
    # CV-01 protected path variable reassigned, directly and from an included location file
    (in_location(b"set $AXISAI_LOG_PATH $REQUEST_URI;"), {}),
    (in_location(b"set $AXISAI_LP $REQUEST_URI;"), {}),
    (in_location(b"include /etc/nginx/snippet.conf;"), {"snippet": "set $AXISAI_LOG_PATH $REQUEST_URI;"}),
    # CV-02 Referer variable reassigned
    (in_location(b"set $Axisai_Log_Referer $Http_Referer;"), {}),
    # CV-03 named captures that overwrite a protected map output or map capture
    (in_location(b'if ($REQUEST_URI ~ "(?<AXISAI_LOG_PATH>.+)") { }'), {}),
    (in_location(b"if ($request_uri ~ \"(?P<Axisai_Lp>.+)\") { }"), {}),
    (in_location(b"if ($request_uri ~ \"(?'AXISAI_LR'.+)\") { }"), {}),
    (at_http(b'server { access_log off; location ~ "^(?<AXISAI_LOG_PATH>/s.*)$" { return 200; } }'), {}),
    # CV-04 competing (case-variant) map output, before or after the reviewed maps
    (at_http(b"map $REQUEST_URI $AXISAI_LOG_PATH { default $REQUEST_URI; }"), {}),
    (at_http(b"map $http_referer $Axisai_Log_Referer { default $http_referer; }", before_block=True), {}),
    (at_http(b"geo $AXISAI_LOG_PATH { default x; }"), {}),
], ids=["cv01-set-upper", "cv01-set-map-capture", "cv01-included-location", "cv02-referer-mixed",
        "cv03-if-capture-output", "cv03-P-capture-map-capture", "cv03-quoted-capture",
        "cv03-location-capture", "cv04-map-after", "cv04-map-before", "cv04-geo"])
def test_case_variant_writers_and_captures_are_rejected(site, files):
    found = violations(site, **files)
    assert found, "case-variant interference must not pass"
    assert any("protected log variables" in v or "exactly once" in v for v in found), found


@pytest.mark.parametrize("directive", [
    b"log_format AXISAI_ACCESS '$remote_addr \"$request\" \"$http_referer\"';",   # earlier: wins the lookup
    b"log_format Axisai_Access '$remote_addr \"$request_method $axisai_log_path $server_protocol\"';",
], ids=["cv05-unsafe-shadow", "cv05-second-definition"])
def test_case_variant_log_format_collision_is_a_violation_not_a_warning(directive):
    # CV-05: access_log resolves format names case-insensitively, first match
    # wins, while the duplicate check is case-sensitive, so nginx -t accepts it.
    site = at_http(directive, before_block=True)
    v, w, _s, _a = privacy.check(source_catalog(site), TEMPLATE)
    assert any("log_format axisai_access must be defined exactly once" in x for x in v), v
    assert not any("axisai_access" in x.lower() for x in w), w


def test_case_variant_reference_to_the_unique_reviewed_format_is_accepted():
    # With exactly one axisai_access identity, any spelling resolves to it.
    site = TEMPLATE.replace(b"access.log axisai_access;", b"access.log AXISAI_ACCESS;")
    assert violations(site) == []


def test_case_variant_collision_between_other_http_formats_is_rejected():
    site = at_http(b"log_format diag '$status';\nlog_format DIAG '$status $remote_addr';")
    assert any("log_format diag" in v for v in violations(site))


def test_forbidden_variables_are_detected_in_any_case():
    for variable in ("$REQUEST", "$Request_Uri", "${ARGS}", "$Http_Referer", "$ARG_q", "$Cookie_x"):
        assert privacy.FORBIDDEN_VARIABLE.search(f"x {variable} y"), variable
    for variable in ("$REQUEST_METHOD", "$Request_Time", "$HTTP_USER_AGENT"):
        assert not privacy.FORBIDDEN_VARIABLE.search(f"x {variable} y"), variable
    site = at_http(b"log_format legacy '$REMOTE_ADDR \"$REQUEST\"';")
    _v, w, _s, _a = privacy.check(source_catalog(site), TEMPLATE)
    assert w == ["log_format legacy contains a query-bearing or credential variable"]


# --- CFG-06: unrelated legitimate variables (any case) still pass ---------------------------------

@pytest.mark.parametrize("directive", [
    b"set $Unrelated_Value $HOST;",
    b"set $axisai_log_path_extra value;",          # a different identifier, not a prefix match
    b"add_header X-Path $AXISAI_LOG_PATH;",         # a reader of the protected value
    b'if ($Request_Method = POST) { set $post 1; }',
    b'if ($uri ~ "^/(?<section>[a-z]+)") { set $sec $section; }',
], ids=["set-unrelated", "set-longer-name", "reader", "if-method", "unrelated-capture"])
def test_unrelated_and_read_only_uses_remain_allowed(directive):
    assert violations(in_location(directive)) == []


# --- CFG-01: include globs follow glob(3), never matching a leading dot ---------------------------

def _glob_site():
    # The server's only access_log comes from a glob include; a dotfile with a
    # safe log is in the catalog because it is included literally elsewhere.
    return (TEMPLATE.replace(b"    access_log /var/log/nginx/access.log axisai_access;\n",
                             b"    include /etc/nginx/conf.d/*.conf;\n")
            + b"include /etc/nginx/conf.d/.hidden.conf;\n")


def test_wildcards_do_not_match_dotfiles_like_glob3():
    dump = source_catalog(_glob_site()) + (
        b"# configuration file /etc/nginx/conf.d/.hidden.conf:\n"
        b"access_log /var/log/nginx/access.log axisai_access;\n\n")
    found = privacy.check(dump, TEMPLATE)[0]
    assert any("has no server-level access_log" in v for v in found), found


@pytest.mark.parametrize("pattern, name, expected", [
    ("*.conf", "a.conf", True), ("*.conf", ".a.conf", False), ("?a.conf", ".a.conf", False),
    (".*.conf", ".a.conf", True), (".hidden.conf", ".hidden.conf", True), ("*", "default", True),
    ("*", ".default", False),
])
def test_glob_component_semantics(pattern, name, expected):
    assert privacy._glob_component(name, pattern) is expected


@pytest.mark.parametrize("pattern", ["/etc/nginx/conf.d/[ab].conf", "/etc/nginx/conf.d/\\*.conf"])
def test_unmodelled_glob_syntax_fails_closed(pattern):
    with pytest.raises(ValueError, match="unsupported include glob"):
        privacy.check(source_catalog(TEMPLATE + f"include {pattern};\n".encode()), TEMPLATE)


# --- CFG-02/03: catalog provenance --------------------------------------------------------------

def _forged_site():
    return TEMPLATE.replace(b"    access_log /var/log/nginx/access.log axisai_access;\n",
                            b"    include /etc/nginx/fake/*.conf;\n").replace(
        b"server {\n", b"include /etc/nginx/forge.conf;\nserver {\n", 1)


FORGE = (b"# configuration file /etc/nginx/forge.conf:\n# x\n"
         b"# configuration file /etc/nginx/fake/a.conf:\n"
         b"access_log /var/log/nginx/access.log axisai_access;\n\n")


def test_section_order_must_equal_nginx_open_order():
    dump = (b"# configuration file /etc/nginx/nginx.conf:\nhttp { include /etc/nginx/a.conf; "
            b"include /etc/nginx/b.conf; }\n\n"
            b"# configuration file /etc/nginx/b.conf:\n\n"
            b"# configuration file /etc/nginx/a.conf:\n\n")
    with pytest.raises(ValueError, match="section order"):
        privacy.check(dump, TEMPLATE)


def test_forged_marker_is_indistinguishable_in_text_and_is_caught_on_the_host(tmp_path):
    # Text alone: the forged section is self-consistent, even in nginx's open
    # order, so a catalog-only check cannot refuse it (documented limitation).
    assert privacy.check(source_catalog(_forged_site()) + FORGE, TEMPLATE)[0] == []
    # On the host the real forge.conf holds the "marker" as a comment and the
    # phantom file does not exist: nginx actually logs with the http-level
    # combined format here (tests/test_priv_log_01_hf01_nginx.py).
    root = tmp_path / "etc" / "nginx"
    dump = (source_catalog(_forged_site()) + FORGE).replace(b"/etc/nginx/", f"{root}/".encode())
    _materialize(source_catalog(_forged_site()).replace(b"/etc/nginx/", f"{root}/".encode()))
    forge = dump[dump.index(b"forge.conf:\n") + len(b"forge.conf:\n"):]
    (root / "forge.conf").write_bytes(forge[:-1])
    with pytest.raises(ValueError, match="host"):
        privacy.check(dump, TEMPLATE, verify_host=True)


def _materialize(dump):
    """Write a catalog's sections to disk exactly as nginx read them."""
    sections = list(privacy.MARKER.finditer(dump))
    for i, m in enumerate(sections):
        end = sections[i + 1].start() if i + 1 < len(sections) else len(dump)
        content = dump[m.end() + 1:end]
        assert content.endswith(b"\n")
        path = Path(m[1].decode())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content[:-1])


def _host_dump(tmp_path, site):
    root = tmp_path / "etc" / "nginx"
    dump = source_catalog(site).replace(b"/etc/nginx/", f"{root}/".encode()).replace(
        b"/etc/letsencrypt/", f"{tmp_path}/le/".encode())
    _materialize(dump)
    return dump, root


def test_host_verification_accepts_an_authentic_catalog(tmp_path):
    dump, _root = _host_dump(tmp_path, TEMPLATE)
    assert privacy.check(dump, TEMPLATE, verify_host=True) == ([], [], 1, 1)


def test_truncated_dump_is_refused_on_the_host(tmp_path):
    # A dump cut at a section boundary hides a glob-included unsafe server;
    # the text is self-consistent, so only the host comparison can see it.
    site = TEMPLATE + b"include /etc/nginx/sites/*.conf;\n"
    dump, root = _host_dump(tmp_path, site)
    (root / "sites").mkdir()
    (root / "sites" / "zz.conf").write_text("server { listen 9; }\n")
    assert privacy.check(dump, TEMPLATE)[0] == []                       # text-only: no claim possible
    with pytest.raises(ValueError, match="host"):
        privacy.check(dump, TEMPLATE, verify_host=True)
    full = dump + f"# configuration file {root}/sites/zz.conf:\nserver {{ listen 9; }}\n\n".encode()
    assert any("has no server-level access_log" in v for v in privacy.check(full, TEMPLATE, verify_host=True)[0])


def test_mid_file_truncation_is_refused_on_the_host(tmp_path):
    dump, _root = _host_dump(tmp_path, TEMPLATE + b"# trailing comment\n")
    cut = dump.replace(b"# trailing comment\n", b"", 1)
    with pytest.raises(ValueError, match="host"):
        privacy.check(cut, TEMPLATE, verify_host=True)


# --- CFG-04: CLI contract -----------------------------------------------------------------------

def test_cli_defaults_to_host_verification_and_reports_provenance_and_warnings(tmp_path):
    lines, errors = [], []
    catalog = source_catalog(TEMPLATE)
    assert privacy.main(["-"], stdin=io.BytesIO(catalog), out=lines.append,
                        err=errors.append) == privacy.EXIT_USAGE      # /etc/nginx is not this catalog
    assert lines == [] and any("host" in e for e in errors)
    lines.clear()
    assert privacy.main(["--catalog-only", "-"], stdin=io.BytesIO(catalog), out=lines.append,
                        err=errors.append) == privacy.EXIT_OK
    assert lines == ["result=ok servers=1 access_logs=1 warnings=0 provenance=catalog-only"]
    dump, _root = _host_dump(tmp_path, at_http(b"log_format legacy '$request';"))
    lines.clear(), errors.clear()
    assert privacy.main(["-"], stdin=io.BytesIO(dump), out=lines.append, err=errors.append) == privacy.EXIT_OK
    assert lines == ["result=ok servers=1 access_logs=1 warnings=1 provenance=host-verified"]
    assert errors == ["warning: log_format legacy contains a query-bearing or credential variable"]
