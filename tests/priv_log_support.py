"""PRIV-LOG-01 test support: the reviewed nginx access-log edit, applied to a site.

The live site is Certbot-owned and is never copied from the repository, so the
change reaches a host as two hand edits (deploy/nginx/README.md "Access log"):

1. the block between the BEGIN/END markers in the repository nginx.conf,
   pasted at http level before the first server block;
2. ``access_log /var/log/nginx/access.log axisai_access;`` as the first line of
   EVERY top-level server block (the Certbot 80->443 redirect block included).

``patch_site`` performs exactly those two edits so the tests exercise the same
bytes an operator is told to produce.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "nginx.conf").read_bytes()
BEGIN = b"# BEGIN axisai access log (PRIV-LOG-01)\n"
END = b"# END axisai access log (PRIV-LOG-01)\n"
ACCESS_LOG_LINE = b"    access_log /var/log/nginx/access.log axisai_access;\n"
LIVE_FIXTURE = ROOT / "tests" / "fixtures" / "r6_nginx" / "fitx.live-2026-10-08.conf"


def access_log_block(template: bytes = TEMPLATE) -> bytes:
    start = template.index(BEGIN)
    end = template.index(END, start) + len(END)
    return template[start:end]


def patch_site(site: bytes, template: bytes = TEMPLATE) -> bytes:
    assert BEGIN not in site, "site already carries the PRIV-LOG-01 block"
    servers = list(re.finditer(rb"^server \{\n", site, flags=re.MULTILINE))
    assert servers, "no top-level server block"
    out = bytearray(site)
    for match in reversed(servers):
        out[match.end():match.end()] = ACCESS_LOG_LINE
    first = servers[0].start()
    out[first:first] = access_log_block(template) + b"\n"
    return bytes(out)


def source_catalog(site):
    """Explicit test-only catalog for the source fixture's external includes.

    Production checks require actual nginx -T; these TLS options are modeled
    as empty and the route include is the known fixture legacy endpoint.
    """
    files = {
        "/etc/nginx/axisai/active-web-upstream.conf": b"server 127.0.0.1:5000;\n",
        "/etc/letsencrypt/options-ssl-nginx.conf": b"ssl_session_timeout 1d;\n",
    }
    if site.startswith(b"# configuration file "):
        dump = site
    else:
        dump = (b"# configuration file /etc/nginx/nginx.conf:\nevents {} http { include /etc/nginx/site.conf; }\n"
                b"# configuration file /etc/nginx/site.conf:\n" + site + b"\n")
    for path, content in files.items():
        if path.encode() in site:
            dump += f"# configuration file {path}:\n".encode() + content
    return dump
