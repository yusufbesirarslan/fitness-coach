"""PRIV-LOG-01: nginx access logs must not record query strings (static proof).

Before: neither the repository template nor the audited live site
(tests/fixtures/r6_nginx/fitx.live-2026-10-08.conf) declares an access_log, so
every server inherits the distribution's http-level
``access_log /var/log/nginx/access.log;`` whose built-in ``combined`` format
logs ``$request``: the whole request line, query string included. A mobile
food search (``?q=``), a barcode lookup (``?code=``) and the backend's hairpin
FatSecret call (``?search_expression=``) all landed in that file next to the
client IP, and the CloudWatch agent ships it to /axisai/nginx/access.

The real-nginx before/after proof is tests/test_priv_log_01_nginx_integration.py
(CI job r6-nginx-integration). This module needs no nginx binary.
"""
import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import axisai_nginx_bootstrap as boot  # noqa: E402
import axisai_nginx_log_privacy as privacy  # noqa: E402

from priv_log_support import (  # noqa: E402
    ACCESS_LOG_LINE, LIVE_FIXTURE, TEMPLATE, access_log_block, patch_site, source_catalog)

sw = boot.sw
LIVE = LIVE_FIXTURE.read_bytes()
MIGRATED = boot.transform(LIVE, sw.INCLUDE_PATH, 5000)   # production since R6-02B


def run_check(data):
    return privacy.check(source_catalog(data), TEMPLATE)


def _format_directive(tree):
    return next(d for d, _ in sw._walk(tree) if d.name.value == "log_format"
                and d.args[0].value == privacy.FORMAT)


def _map_entries(tree, variable):
    """The map's regex entries in source order, and its default."""
    block = next(d for d, _ in sw._walk(tree) if d.name.value == "map"
                 and d.args[1].value == variable).block
    entries = [(d.name.value, d.args[0].value) for d in block]
    regexes = []
    for key, value in entries:
        if key.startswith("~"):
            flags, pattern = (re.IGNORECASE, key[2:]) if key.startswith("~*") else (0, key[1:])
            regexes.append((re.compile(pattern.replace("(?<", "(?P<"), flags), value))
    return regexes, dict(entries)["default"]


def nginx_map(variable, value):
    """The map's first-match semantics (regexes in order, ~* caseless),
    emulated with the reviewed regexes; a ``$name`` value is that capture."""
    regexes, default = _map_entries(sw.parse_nginx(TEMPLATE), variable)
    for regex, out in regexes:
        match = regex.search(value)
        if match:
            return match.group(out[1:]) if out.startswith("$") else out
    return default


# --- before: the leak exists in every audited configuration ------------------------------------

@pytest.mark.parametrize("site", [LIVE, MIGRATED], ids=["live-2026-10-08", "live-r6-migrated"])
def test_live_site_inherits_the_query_bearing_combined_format(site):
    violations, _warnings, servers, access_logs = run_check(site)
    assert (servers, access_logs) == (2, 0)
    inherits = [v for v in violations if "inherits the http-level format" in v]
    assert len(inherits) == 2   # the TLS server AND Certbot's 80->443 redirect server
    assert any("log_format axisai_access" in v for v in violations)


# --- after: the reviewed definitions ----------------------------------------------------------------

def test_template_passes_and_every_server_declares_the_query_free_log():
    assert run_check(TEMPLATE) == ([], [], 1, 1)


def test_query_free_format_keeps_operational_fields_and_no_forbidden_variable():
    text = privacy._format_text(_format_directive(sw.parse_nginx(TEMPLATE)))
    assert not privacy.FORBIDDEN_VARIABLE.search(text)
    for variable in ("$remote_addr", "$time_local", "$request_method", "$axisai_log_path",
                     "$server_protocol", "$status", "$body_bytes_sent", "$http_user_agent",
                     "$request_time", "$upstream_connect_time", "$upstream_header_time",
                     "$upstream_response_time", "$upstream_status", "$upstream_addr",
                     "$limit_req_status", "$request_id", "$host"):
        assert variable in text, variable
    # combined-compatible prefix: a parser for `combined` still finds every field
    assert text.startswith('$remote_addr - - [$time_local] "$request_method $axisai_log_path '
                           '$server_protocol" $status $body_bytes_sent "$axisai_log_referer" '
                           '"$http_user_agent" ')


@pytest.mark.parametrize("variable", [
    "$request", "${request}", "$request_uri", "$args", "$query_string", "$is_args", "$arg_q",
    "$arg_search_expression", "$http_referer", "$http_authorization", "$http_cookie",
    "$cookie_session", "$remote_user", "$request_body"])
def test_forbidden_variables_are_detected(variable):
    assert privacy.FORBIDDEN_VARIABLE.search(f"x {variable} y")


@pytest.mark.parametrize("variable", [
    "$request_method", "$request_time", "$request_id", "$request_length", "$uri",
    "$upstream_response_time", "$axisai_log_path", "$axisai_log_referer", "$http_user_agent"])
def test_operational_variables_are_allowed(variable):
    assert not privacy.FORBIDDEN_VARIABLE.search(f"x {variable} y")


@pytest.mark.parametrize("request_uri, logged", [
    ("/api/v1/nutrition/foods/search?q=PRIVLOG01%20tavuk", "/api/v1/nutrition/foods/search"),
    ("/api/v1/nutrition/foods/barcode?code=8690000000017", "/api/v1/nutrition/foods/barcode"),
    ("/fatsecret/rest/server.api?method=foods.search&search_expression=PRIVLOG01",
     "/fatsecret/rest/server.api"),
    ("/reset-password?token=PRIVLOG01", "/reset-password"),
    ("/api/v1/nutrition/foods/search?", "/api/v1/nutrition/foods/search"),
    ("/api/v1/today", "/api/v1/today"),
    ("/path%3Fnot-a-query", "/path%3Fnot-a-query"),
    ("?q=PRIVLOG01", "-"),          # no path at all: nothing of the query survives
    ("", "-"),                      # unparsable request line
])
def test_path_map_drops_the_query_string(request_uri, logged):
    assert nginx_map("$axisai_log_path", request_uri) == logged


# --- FatSecret detail segment (#547 c7240107959 F2, source only) ---------------------------------

FATSECRET_DETAIL = "/api/v1/nutrition/foods/fatsecret/:food_id/servings"
DETAIL_SEGMENTS = ("api", "v1", "nutrition", "foods", "fatsecret")


def _encoded_or_literal(char):
    """One prefix character, literal or percent-encoded (either case)."""
    if char == "/":
        return "(?:/|%2f)"
    if char.isdigit():
        return f"(?:{char}|%3{char})"
    code = ord(char)
    return f"(?:{char}|%[{(code >> 4) - 2}{code >> 4}]{code & 15:x})"


def _reviewed_detail_pattern():
    slash = "(?:/|%2f)+"
    return ("~*^" + slash + slash.join("".join(map(_encoded_or_literal, segment))
                                        for segment in DETAIL_SEGMENTS) + "(?:/|%2f)")


def test_detail_mask_is_the_reviewed_derivation_and_precedes_the_generic_capture():
    block = next(d for d, _ in sw._walk(sw.parse_nginx(TEMPLATE)) if d.name.value == "map"
                 and d.args[1].value == "$axisai_log_path").block
    assert [(d.name.value, d.args[0].value) for d in block] == [
        ("default", "-"),
        (_reviewed_detail_pattern(), FATSECRET_DETAIL),
        ("~^(?<axisai_lp>[^?]+)", "$axisai_lp"),
    ]


@pytest.mark.parametrize("request_uri", [
    "/api/v1/nutrition/foods/fatsecret/35755/servings",
    "/api/v1/nutrition/foods/fatsecret/35755/servings?q=PRIVLOG01",
    "/api/v1/nutrition/foods/fatsecret/john%20doe/servings",
    "/api/v1/nutrition/foods/fatsecret/a%40b.co/servings",
    "/api/v1/nutrition/foods/fatsecret/%D9%A3%D9%A5/servings",
    "/api/v1/nutrition/foods/fatsecret/" + "9" * 200 + "/servings",
    "/api/v1/nutrition/foods/fatsecret/x",
    "/api/v1/nutrition/foods/fatsecret/",
    "/api/v1/nutrition/foods/fatsecret//servings",
    "/API/V1/Nutrition/Foods/FatSecret/PRIVLOG01/servings",
    "//api//v1/nutrition/foods/fatsecret/PRIVLOG01/servings",
    "///api/v1///nutrition//foods////fatsecret//PRIVLOG01/servings",
    "/api/v1/nutrition/foods/%66atsecret/PRIVLOG01/servings",
    "/api/v1/nutrition/foods/%46AT%53ECRET/PRIVLOG01/servings",
    "/%61%70%69/%76%31/%6e%75%74%72%69%74%69%6f%6e/%66%6f%6f%64%73/%66%61%74%73%65%63%72%65%74/"
    "PRIVLOG01/servings",
    "/api%2fv1%2Fnutrition/foods/fatsecret%2fPRIVLOG01/servings",
    "%2fapi/v1/nutrition/foods/fatsecret/PRIVLOG01/servings",
])
def test_fatsecret_detail_segment_is_masked(request_uri):
    assert nginx_map("$axisai_log_path", request_uri) == FATSECRET_DETAIL


@pytest.mark.parametrize("request_uri, logged", [
    ("/api/v1/nutrition/foods/search?q=anything", "/api/v1/nutrition/foods/search"),
    ("/api/v1/nutrition/foods/barcode?code=8690000000017", "/api/v1/nutrition/foods/barcode"),
    ("/api/v1/nutrition/diary/today", "/api/v1/nutrition/diary/today"),
    ("/api/v1/nutrition/logs", "/api/v1/nutrition/logs"),
    ("/api/v1/nutrition/foods/fatsecret", "/api/v1/nutrition/foods/fatsecret"),
    ("/api/v1/nutrition/foods/fatsecretx/1/servings", "/api/v1/nutrition/foods/fatsecretx/1/servings"),
    ("/api/v1/nutrition/foods/fatsecre/1", "/api/v1/nutrition/foods/fatsecre/1"),
    ("/api/v2/nutrition/foods/fatsecret/1/servings", "/api/v2/nutrition/foods/fatsecret/1/servings"),
    ("/x/api/v1/nutrition/foods/fatsecret/1/servings", "/x/api/v1/nutrition/foods/fatsecret/1/servings"),
    ("/fatsecret/rest/server.api?food_id=1", "/fatsecret/rest/server.api"),
    ("/api/food/35755/servings", "/api/food/35755/servings"),       # browser route, unchanged
    ("/api/v1/nutrition/foods/%2566atsecret/1/servings",          # double-encoded: not routed
     "/api/v1/nutrition/foods/%2566atsecret/1/servings"),
])
def test_other_routes_keep_their_existing_logged_path(request_uri, logged):
    assert nginx_map("$axisai_log_path", request_uri) == logged


def _wsgi_path(request_uri):
    """What gunicorn hands Flask: the percent-decoded path (PATH_INFO)."""
    from urllib.parse import unquote
    return unquote(request_uri.split("?", 1)[0])


@pytest.mark.parametrize("raw", [
    "/api/v1/nutrition/foods/%66atsecret/x/servings",
    "/%61pi/v%31/nutrition/foods/fatsecret/x/servings",
    "/api%2Fv1/nutrition/foods/fatsecret/x/servings",
    "/api/v1/nutrition/foods/fatsecre%74/x/servings",
])
def test_every_encoded_spelling_that_decodes_to_the_route_is_masked(raw):
    assert _wsgi_path(raw) == "/api/v1/nutrition/foods/fatsecret/x/servings"
    assert nginx_map("$axisai_log_path", raw) == FATSECRET_DETAIL


@pytest.mark.parametrize("referer, logged", [
    ("https://axisaiapp.com/nutrition?q=PRIVLOG01#frag", "https://axisaiapp.com/nutrition"),
    ("https://axisaiapp.com/nutrition#q=PRIVLOG01", "https://axisaiapp.com/nutrition"),
    ("https://axisaiapp.com/", "https://axisaiapp.com/"),
    ("", "-"),
    ("?PRIVLOG01", "-"),
])
def test_referer_map_drops_query_and_fragment(referer, logged):
    assert nginx_map("$axisai_log_referer", referer) == logged


# --- the operator edit on the real live site -----------------------------------------------------

def test_patched_live_site_passes_and_keeps_the_r6_route_contract():
    patched = patch_site(MIGRATED)
    assert run_check(patched) == ([], [], 2, 2)
    # R6 route control still classifies the site as migrated: the switch helper keeps working
    topology = sw.classify_site(patched, sw.INCLUDE_PATH, 5000)
    assert topology.kind == sw.TOPOLOGY_MIGRATED
    # the edit only adds lines: the block and one access_log per server, nothing removed
    before, after = MIGRATED.decode().splitlines(), patched.decode().splitlines()
    added = list(after)
    for line in before:
        added.remove(line)
    assert sorted(added) == sorted(access_log_block().decode().splitlines() + [""]
                                   + [ACCESS_LOG_LINE.decode().rstrip("\n")] * 2)
    # routing, auth headers and rate limiting are byte-identical
    routing = re.compile(r"^\s*(proxy_|limit_req|location|upstream|listen|return|if |ssl_|include)")
    assert [l for l in before if routing.match(l)] == [l for l in after if routing.match(l)]


def test_patching_is_also_correct_on_the_pre_migration_site():
    assert run_check(patch_site(LIVE)) == ([], [], 2, 2)


# --- every way a later edit could reintroduce the query string fails ------------------------------

def _mutate(old, new, data=None):
    data = data if data is not None else patch_site(MIGRATED)
    assert old in data, old
    return data.replace(old, new, 1)


@pytest.mark.parametrize("old, new, expected", [
    (ACCESS_LOG_LINE, b"", "has no server-level access_log"),
    (ACCESS_LOG_LINE, b"    access_log /var/log/nginx/access.log;\n", "does not use axisai_access"),
    (ACCESS_LOG_LINE, b"    access_log /var/log/nginx/access.log combined;\n", "does not use axisai_access"),
    (b"\"$request_method $axisai_log_path $server_protocol\"", b"\"$request\"",
     "log_format axisai_access must be defined exactly once"),
    (b"host=$host'", b"host=$host q=$args'", "log_format axisai_access must be defined exactly once"),
    (b"[^?]+", b".+", "map for $axisai_log_path"),
    (b"[^?#]+", b".+", "map for $axisai_log_referer"),
    (b"    location / {\n", b"    location / {\n        access_log /var/log/nginx/access.log combined;\n",
     "does not use axisai_access"),
])
def test_reintroducing_a_query_bearing_log_is_a_violation(old, new, expected):
    violations = run_check(_mutate(old, new))[0]
    assert any(expected in v for v in violations), violations


def test_extra_server_needs_its_own_access_log_and_off_is_accepted():
    proxy = (b"server {\n    listen 127.0.0.1:3000;\n    location / {\n"
             b"        proxy_pass https://platform.fatsecret.com;\n    }\n}\n")
    violations = run_check(patch_site(MIGRATED) + proxy)[0]
    assert violations == ["server (listen 127.0.0.1:3000) has no server-level access_log and "
                          "inherits the http-level format"]
    off = proxy.replace(b"listen 127.0.0.1:3000;\n", b"listen 127.0.0.1:3000;\n    access_log off;\n")
    assert run_check(patch_site(MIGRATED) + off)[0] == []


def nginx_T(*files):
    """Real nginx -T framing: marker, exact file bytes, one newline; open order."""
    return b"".join(b"# configuration file " + path + b":\n" + content + b"\n" for path, content in files)


def test_full_nginx_T_style_dump_tolerates_the_distribution_http_line():
    dump = nginx_T(
        (b"/etc/nginx/nginx.conf",
         b"events {}\nhttp {\n    access_log /var/log/nginx/access.log;\n"
         b"    log_format legacy '$remote_addr \"$request\"';\n"
         b"    include /etc/nginx/sites-enabled/*;\n}\n"
         b"stream {\n    server {\n        listen 9000;\n    }\n}\n"),
        (b"/etc/nginx/sites-enabled/default", patch_site(MIGRATED)),
        (b"/etc/nginx/axisai/active-web-upstream.conf", b"server 127.0.0.1:5000;\n"),
        (b"/etc/letsencrypt/options-ssl-nginx.conf", b"ssl_session_timeout 1d;\n"),
        (b"/etc/nginx/sites-enabled/fatsecret-proxy",
         b"server {\n    listen 127.0.0.1:3000;\n    access_log off;\n}\n"))
    violations, warnings, servers, access_logs = privacy.check(dump, TEMPLATE)
    assert violations == [] and servers == 3 and access_logs == 4
    assert warnings == ["log_format legacy contains a query-bearing or credential variable"]


def test_documented_fatsecret_proxy_block_disables_its_access_log():
    doc = (ROOT / "deploy" / "fatsecret-proxy.md").read_text()
    block = doc.split("```", 2)[1]
    assert "listen 127.0.0.1:3000;" in block and "access_log off;" in block


def test_cloudwatch_agent_still_ships_the_same_access_log_file():
    config = json.loads((ROOT / "deploy" / "cloudwatch-agent" / "file_config.json").read_text())
    files = {c["file_path"]: c for c in config["logs"]["logs_collected"]["files"]["collect_list"]}
    assert files["/var/log/nginx/access.log"]["log_group_name"] == "/axisai/nginx/access"
    assert b"access_log /var/log/nginx/access.log axisai_access;" in TEMPLATE


# --- the CLI ------------------------------------------------------------------------------------------

def test_cli_exit_codes_and_single_result_line(tmp_path):
    # Off-host synthetic catalogs: host provenance is covered in test_priv_log_01_hf01.py.
    lines, errors = [], []
    assert privacy.main(["--catalog-only", "-"], stdin=io.BytesIO(source_catalog(patch_site(MIGRATED))),
                        out=lines.append, err=errors.append) == privacy.EXIT_OK
    assert lines == ["result=ok servers=2 access_logs=2 warnings=0 provenance=catalog-only"] and errors == []
    lines.clear()
    assert privacy.main(["--catalog-only", "-"], stdin=io.BytesIO(source_catalog(MIGRATED)), out=lines.append,
                        err=errors.append) == privacy.EXIT_VIOLATION
    assert lines == ["result=violation servers=2 access_logs=0 warnings=0 provenance=catalog-only"] and errors
    assert privacy.main([], out=lines.append, err=errors.append) == privacy.EXIT_USAGE
    assert privacy.main(["-"], stdin=io.BytesIO(b"server {"), out=lines.append,
                        err=errors.append) == privacy.EXIT_USAGE
    assert privacy.main([str(tmp_path / "missing")], out=lines.append,
                        err=errors.append) == privacy.EXIT_USAGE


def test_cli_fails_closed_on_template_with_unresolved_includes():
    proc = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "axisai_nginx_log_privacy.py"),
                           str(ROOT / "nginx.conf")], capture_output=True, text=True, timeout=30)
    assert proc.returncode == privacy.EXIT_USAGE and "unresolved include" in proc.stderr


def _spellings(char):
    """Every spelling of one prefix character that gunicorn decodes back to it
    (literal, upper case, percent-encoded with either hex case)."""
    code = f"{ord(char):02x}"
    out = {char, char.upper(), "%" + code, "%" + code.upper()}
    if char.isalpha():
        upper = f"{ord(char.upper()):02x}"
        out |= {"%" + upper, "%" + upper.upper()}
    return sorted(out)


def test_each_prefix_character_in_every_spelling_is_masked():
    prefix = "/api/v1/nutrition/foods/fatsecret/"
    checked = 0
    for index, char in enumerate(prefix):
        for spelling in _spellings(char):
            uri = prefix[:index] + spelling + prefix[index + 1:] + "PRIVLOG01/servings"
            assert nginx_map("$axisai_log_path", uri) == FATSECRET_DETAIL, uri
            checked += 1
    assert checked == 138   # 35 characters, every spelling


def test_random_mixed_spellings_are_masked():
    import random
    rng = random.Random(547)
    prefix = "/api/v1/nutrition/foods/fatsecret/"
    for _ in range(500):
        uri = "".join(rng.choice(_spellings(c)) * (rng.randint(1, 3) if c == "/" else 1)
                      for c in prefix) + "PRIVLOG01/servings?q=PRIVLOG01"
        assert nginx_map("$axisai_log_path", uri) == FATSECRET_DETAIL, uri
