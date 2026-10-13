#!/usr/bin/python3 -I
"""Read-only check that nginx access logs cannot record query strings (PRIV-LOG-01).

    sudo nginx -T 2>/dev/null | python3 -I scripts/axisai_nginx_log_privacy.py -
    python3 -I scripts/axisai_nginx_log_privacy.py nginx.conf

The input is one nginx configuration text: a single site file, or the full
``nginx -T`` dump (every file concatenated). Nothing is written, reloaded or
executed. The repository ``nginx.conf`` is the reference for the reviewed
query-free definitions (``log_format axisai_access`` and the two path/referer
maps); the input must carry them byte-for-byte in parsed form, so a host edit
cannot quietly widen the format.

Rules, all of which must hold:

1. ``log_format axisai_access`` and the ``$axisai_log_path`` /
   ``$axisai_log_referer`` maps are present exactly once and equal the reference.
2. Every ``access_log`` inside a ``server`` block (server, location or ``if``
   level) is ``off`` or names ``axisai_access``.
3. Every HTTP ``server`` block declares its own server-level ``access_log``.
   nginx inherits ``access_log`` only when a level declares none, so a server
   without one falls back to the distribution's http-level line, whose default
   ``combined`` format logs ``$request`` (the full request line with the query
   string). That http-level line is therefore tolerated only because rule 3
   leaves no server inheriting it.
4. No ``log_format`` used by rule 2 contains a query-bearing or credential
   variable (also checked for any other format, as a warning).

Exit codes: 0 ok, 1 violation (details on stderr), 2 usage or unparsable input.
The one stdout line is ``result=ok|violation servers=N access_logs=N``.
"""
import importlib.util
import os
import re
import sys
from typing import List, Optional, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_SPEC = importlib.util.spec_from_file_location(
    "axisai_switch_web_slot", os.path.join(_HERE, "axisai_switch_web_slot.py"))
sw = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sw)

REFERENCE = os.path.join(os.path.dirname(_HERE), "nginx.conf")
FORMAT = "axisai_access"
MAPS = ("$axisai_log_path", "$axisai_log_referer")
# Variables that carry the query string, the raw Referer (which carries the
# previous page's query string) or credentials. `$request` is the full request
# line; `$request_method`/`$request_time`/`$request_id` are not matched.
FORBIDDEN_VARIABLE = re.compile(
    r"\$\{?(?:(?:request|request_uri|args|query_string|is_args|request_body|remote_user"
    r"|http_referer|http_authorization|http_cookie|http_proxy_authorization)(?![A-Za-z0-9_])"
    r"|(?:arg|cookie)_[A-Za-z0-9_]+)")
NON_HTTP_CONTEXTS = {"stream", "mail"}

EXIT_OK, EXIT_VIOLATION, EXIT_USAGE = 0, 1, 2


def _signature(directive) -> Tuple:
    """Parsed shape of a directive: name, argument values and nested block."""
    return (directive.name.value, tuple(a.value for a in directive.args),
            None if directive.block is None else tuple(_signature(d) for d in directive.block))


def _definitions(tree) -> Tuple[List, List]:
    nodes = list(sw._walk(tree))
    formats = [d for d, _ in nodes if d.name.value == "log_format" and d.args
               and d.args[0].value == FORMAT]
    maps = [d for d, _ in nodes if d.name.value == "map" and len(d.args) == 2
            and d.args[1].value in MAPS]
    return formats, maps


def _format_text(directive) -> str:
    return "".join(a.value for a in directive.args[1:] if not a.value.startswith("escape="))


def check(data: bytes, reference: bytes) -> Tuple[List[str], List[str], int, int]:
    """Returns (violations, warnings, servers, access_logs)."""
    tree = sw.parse_nginx(data)
    ref_formats, ref_maps = _definitions(sw.parse_nginx(reference))
    if len(ref_formats) != 1 or len(ref_maps) != len(MAPS):
        raise ValueError("reference nginx.conf lacks the axisai_access definitions")
    violations: List[str] = []
    warnings: List[str] = []

    formats, maps = _definitions(tree)
    if [_signature(d) for d in formats] != [_signature(ref_formats[0])]:
        violations.append(f"log_format {FORMAT} must be defined exactly once, equal to the reference")
    ref_by_var = {d.args[1].value: _signature(d) for d in ref_maps}
    for var in MAPS:
        found = [_signature(d) for d in maps if d.args[1].value == var]
        if found != [ref_by_var[var]]:
            violations.append(f"map for {var} must be defined exactly once, equal to the reference")
    if FORBIDDEN_VARIABLE.search(_format_text(ref_formats[0])):
        violations.append(f"reference log_format {FORMAT} contains a forbidden variable")
    for d, _ in sw._walk(tree):
        if d.name.value == "log_format" and d.args and FORBIDDEN_VARIABLE.search(_format_text(d)):
            (violations if d.args[0].value == FORMAT else warnings).append(
                f"log_format {d.args[0].value} contains a query-bearing or credential variable")

    servers = access_logs = 0
    for d, parents in sw._walk(tree):
        names = [p.name.value for p in parents]
        if NON_HTTP_CONTEXTS & set(names):
            continue
        if d.name.value == "server" and d.block is not None:
            servers += 1
            if not any(c.name.value == "access_log" for c in d.block):
                listen = next((" ".join(a.value for a in c.args) for c in d.block
                               if c.name.value == "listen"), "?")
                violations.append(f"server (listen {listen}) has no server-level access_log and "
                                  "inherits the http-level format")
        if d.name.value == "access_log":
            access_logs += 1
            args = [a.value for a in d.args]
            if args == ["off"]:
                continue
            if "server" in names and (len(args) < 2 or args[1] != FORMAT):
                violations.append(f"access_log {' '.join(args)} inside a server does not use {FORMAT}")
    return violations, warnings, servers, access_logs


def main(argv: Sequence[str], reference_path: Optional[str] = None,
         stdin=None, out=print, err=None) -> int:
    err = err or (lambda line: print(line, file=sys.stderr))
    if len(argv) != 1:
        err("usage: axisai_nginx_log_privacy.py <nginx-config-file | ->")
        return EXIT_USAGE
    try:
        if argv[0] == "-":
            data = (stdin or sys.stdin.buffer).read()
        else:
            with open(argv[0], "rb") as handle:
                data = handle.read()
        with open(reference_path or REFERENCE, "rb") as handle:
            reference = handle.read()
        violations, warnings, servers, access_logs = check(data, reference)
    except (OSError, ValueError, UnicodeDecodeError, sw.NginxSyntaxError) as exc:
        err(f"error: {type(exc).__name__}: {exc}")
        return EXIT_USAGE
    for line in warnings:
        err(f"warning: {line}")
    for line in violations:
        err(f"violation: {line}")
    result = "violation" if violations else "ok"
    out(f"result={result} servers={servers} access_logs={access_logs}")
    return EXIT_VIOLATION if violations else EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
