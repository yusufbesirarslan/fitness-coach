"""Adversarial delta tests; catalog bytes model a successful complete nginx -T."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import axisai_nginx_log_privacy as privacy  # noqa: E402
from priv_log_support import TEMPLATE, source_catalog  # noqa: E402


def catalog(site, **files):
    dump = source_catalog(site)
    for name, content in files.items():
        dump += f'# configuration file /etc/nginx/{name}.conf:\n{content}\n'.encode()
    return dump


@pytest.mark.parametrize('scope', [
    'include /etc/nginx/outer.conf;',
    'server { access_log off; include /etc/nginx/outer.conf; }',
    'server { access_log off; location / { include /etc/nginx/outer.conf; } }',
    'server { access_log off; location / { location /nested { include /etc/nginx/outer.conf; } } }',
    'server { access_log off; error_page 404 = @fallback; location @fallback { include /etc/nginx/outer.conf; } }',
])
def test_nested_include_unsafe_log_rejected_at_all_scopes(scope):
    # HTTP-level unsafe logging is shadowed by explicit server logs. At HTTP
    # scope the included server must itself inherit/introduce an unsafe log.
    inner = ('server { access_log /tmp/leak combined; }' if scope.startswith('include')
             else 'access_log /tmp/leak combined;')
    dump = catalog(TEMPLATE + scope.encode(), outer='include /etc/nginx/inner.conf;', inner=inner)
    assert any('does not use axisai_access' in v for v in privacy.check(dump, TEMPLATE)[0])


def test_included_server_level_safe_log_and_repeated_include():
    site = TEMPLATE + b'server { include /etc/nginx/safe.conf; location / { include /etc/nginx/safe.conf; } }'
    assert privacy.check(catalog(site, safe='access_log /tmp/safe axisai_access;'), TEMPLATE)[0] == []


@pytest.mark.parametrize('scope', [
    'server { access_log off; set $axisai_log_path $request_uri; }',
    'server { access_log off; location / { location /nested { set $axisai_log_referer $http_referer; } } }',
    'server { access_log off; location / { include /etc/nginx/writer.conf; } }',
    'server { access_log off; set $axisai_lp $request_uri; }',
    'server { access_log off; if ($request_uri ~ "(?<axisai_lr>.+)") { return 200; } }',
    'map $request_uri $axisai_lp { default $request_uri; }',
    'server { access_log off; location ~ "(?<axisai_log_path>.+)" { return 200; } }',
    'server { access_log off; if ($request_uri ~ "(?P<axisai_log_referer>.+)") { return 200; } }',
    "server { access_log off; if ($request_uri ~ \"(?'axisai_lp'.+)\") { return 200; } }",
    'custom_writer $axisai_log_path;',
    'server { access_log off; rewrite_by_lua_block { dynamic_code; } }',
])
def test_protected_variable_writers_and_captures_rejected(scope):
    files = {'writer': 'set $axisai_log_path $request_uri;'} if 'include' in scope else {}
    violations = privacy.check(catalog(TEMPLATE + scope.encode(), **files), TEMPLATE)[0]
    assert any('write' in v or 'interference' in v for v in violations)


def test_unrelated_set_and_safe_reads_remain_allowed():
    site = TEMPLATE + b'server { access_log off; set $unrelated value; add_header X-Path $axisai_log_path; }'
    assert privacy.check(catalog(site), TEMPLATE)[0] == []


@pytest.mark.parametrize('dump', [
    b'http { include /etc/nginx/missing.conf; }',
    b'# configuration file /etc/nginx/nginx.conf:\nhttp { include relative.conf; }',
    b'# configuration file /etc/nginx/nginx.conf:\nhttp { include /etc/nginx/missing.conf; }',
    b'# configuration file /etc/nginx/nginx.conf:\ninclude /etc/nginx/nginx.conf;',
    b'# configuration file /etc/nginx/nginx.conf:\nevents {}\n# configuration file /etc/nginx/orphan.conf:\naccess_log off;',
    b'# configuration file /etc/nginx/nginx.conf:\nevents {}\n# configuration file /etc/nginx/nginx.conf:\nevents {}',
])
def test_ambiguous_or_incomplete_include_context_fails_closed(dump):
    with pytest.raises(ValueError):
        privacy.check(dump, TEMPLATE)


def test_absolute_glob_expands_without_crossing_directory_boundaries():
    dump = catalog(TEMPLATE + b'include /etc/nginx/extra*.conf;', extra1='server { access_log off; }',
                   extra2='server { access_log /tmp/leak combined; }')
    assert privacy.check(dump, TEMPLATE)[0]
