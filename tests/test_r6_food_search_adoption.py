"""Pure source delta checks; fixtures are not production preimages."""
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import axisai_nginx_bootstrap as boot
import r6_food_search_adoption as adoption


def fixture():
    live = (ROOT / "tests/fixtures/r6_nginx/fitx.live-2026-10-08.conf").read_bytes()
    return boot.transform(live, boot.sw.INCLUDE_PATH, 5000)


def test_delta_preserves_every_byte_outside_two_allowed_ranges():
    before = fixture()
    tree = boot.sw.parse_nginx(before)
    tls, http = [d for d in tree if d.name.value == "server"]
    loc = next(d for d in tls.block if d.name.value == "location" and len(d.args) == 2)
    after = adoption._transform(before)
    expected = (before[:loc.name.start] + adoption.DENIAL
                + before[adoption._end(before, loc):http.block[0].name.start]
                + adoption.HTTP_GUARD + before[http.block[0].name.start:])
    assert after == expected
    assert b"proxy_pass http://axisai_web;" in after
    assert b"proxy_pass http://fatsecret_proxy/rest/server.api;" not in after
    assert after.count(b"return 301 https://$host$request_uri;") == 1


def test_cli_refuses_unaccepted_preimage_without_disclosing_configuration():
    result = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/r6_food_search_adoption.py")],
                            input=fixture(), capture_output=True, timeout=10)
    assert result.returncode == 1
    assert result.stdout == b""
    assert result.stderr == b"R6_ADOPTION_REFUSED\n"
    assert adoption.SITE_SHA256 == "f83269eb587148474ef255583794e382ce5104ad11e9528e2184a41a828a9096"
    assert hashlib.sha256(fixture()).hexdigest() != adoption.SITE_SHA256


@pytest.mark.parametrize("old,new", [
    (b"if ($host =", b"if ($http_host ="),
    (b"return 404; # managed", b"return 302 /other; # managed"),
    (b"proxy_pass http://fatsecret_proxy/rest/server.api;", b"return 404;"),
])
def test_unsupported_control_flow_refused(old, new):
    with pytest.raises(ValueError):
        adoption._transform(fixture().replace(old, new))
