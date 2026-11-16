#!/usr/bin/env python3
"""Prepare the one hash-bound site delta; never install, test or reload nginx.

Private configuration is stdin/stdout. Redirect stdout to an owner-private
candidate file, never a terminal/CI log. No arbitrary preimage override exists.
Loaded acceptance and installation belong to the existing R6 release procedure.
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import axisai_switch_web_slot as sw

SITE_SHA256 = "f83269eb587148474ef255583794e382ce5104ad11e9528e2184a41a828a9096"
PATH = "/fatsecret/rest/server.api"
DENIAL = b'''location = /fatsecret/rest/server.api {
        add_header Cache-Control "no-store" always;
        return 404;
    }'''
# Server rewrite phase precedes both Certbot's conditional 301 and final 404.
# Empty header values are not emitted. Thus unrelated 301/404 headers stay as-is.
HTTP_GUARD = b'''
    # V1 Food Search denial precedes Certbot's existing host redirect.
    set $axisai_food_search_cache "";
    add_header Cache-Control $axisai_food_search_cache always;
    if ($uri = /fatsecret/rest/server.api) {
        set $axisai_food_search_cache "no-store";
        return 404;
    }
'''


def _args(directive):
    return [token.value for token in directive.args]


def _end(data, directive):
    """End of a complete block, using the existing token grammar, not regex."""
    depth = 0
    for token in sw.tokenize(data[directive.name.start:]):
        if not token.quoted and token.value == "{":
            depth += 1
        elif not token.quoted and token.value == "}":
            depth -= 1
            if depth == 0:
                return directive.name.start + token.end
    raise ValueError("block")


def _transform(site):
    """Pure byte-range edit. Unpinned entry is private and used only by fixtures."""
    tree = sw.parse_nginx(site)
    servers = [d for d in tree if d.name.value == "server"]
    if len(servers) != 2:
        raise ValueError("servers")
    tls, http = servers
    locations = [d for d in tls.block if d.name.value == "location"
                 and _args(d) == ["=", PATH]]
    if len(locations) != 1:
        raise ValueError("location")
    location = locations[0]
    if [d.name.value for d in location.block] != ["proxy_pass"] + ["proxy_set_header"] * 4:
        raise ValueError("handler")
    if _args(location.block[0]) != ["http://fatsecret_proxy/rest/server.api"]:
        raise ValueError("upstream")
    if [d.name.value for d in http.block] != ["if", "listen", "listen", "server_name", "return"]:
        raise ValueError("http")
    host_if = http.block[0]
    names = _args(http.block[3])
    if (len(names) != 1 or _args(host_if) != ["($host", "=", names[0] + ")"]
            or len(host_if.block) != 1 or host_if.block[0].name.value != "return"
            or _args(host_if.block[0]) != ["301", "https://$host$request_uri"]
            or _args(http.block[-1]) != ["404"]):
        raise ValueError("redirect")
    # Both insertions preserve all other bytes, including names, certificates,
    # listeners, upstream selection, rate limits, and the Certbot redirect.
    result = (site[:location.name.start] + DENIAL
              + site[_end(site, location):host_if.name.start] + HTTP_GUARD
              + site[host_if.name.start:])
    sw.parse_nginx(result)
    return result


def prepare(site):
    if hashlib.sha256(site).hexdigest() != SITE_SHA256:
        raise ValueError("preimage")
    return _transform(site)


def main():
    if len(sys.argv) != 1:
        print("R6_ADOPTION_REFUSED", file=sys.stderr)
        return 1
    try:
        site = sys.stdin.buffer.read(65537)
        if len(site) > 65536:
            raise ValueError("size")
        candidate = prepare(site)
    except (ValueError, UnicodeError):
        print("R6_ADOPTION_REFUSED", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(candidate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
