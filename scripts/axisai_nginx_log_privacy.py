#!/usr/bin/python3 -I
"""Read-only check that nginx access logs cannot record query strings (PRIV-LOG-01).

    python3 -I scripts/axisai_nginx_log_privacy.py <successful-complete-nginx-T-dump>

The deployment input is the stdout of a successful, complete ``nginx -T``.
File sections form a catalog which is recursively expanded at each include
site. Absolute canonical paths and absolute globs are supported. Relative,
missing, cyclic, duplicate or unreachable file sections fail closed; includes
in a standalone site file also fail closed. Standalone texts without includes
are accepted for unit/source checks. Never concatenate site files manually.

The exact reference log format and maps are required once. Every HTTP server
must declare its own access_log (off or axisai_access), and every descendant
log must also be off or axisai_access. This conservatively audits all locations,
including nested/named locations and internal-redirect destinations, regardless
of reachability. The distribution HTTP log may remain because all servers
shadow it. All included directives are checked at their original contexts.

The maps' outputs, builtin inputs and named capture dependencies are protected
against other writers/captures. Unsupported consumers of these variables,
embedded Lua/JS/Perl and dynamic module loading fail closed pending a separate
module audit. Unrelated stock ``set`` directives remain allowed.

Exit codes: 0 ok, 1 violation (details on stderr), 2 usage or unparsable input.
The one stdout line is ``result=ok|violation servers=N access_logs=N``.
"""
import fnmatch
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


def _effective_tree(data):
    """Expand nginx -T's file catalog at each include site, never flatten it.

    Only absolute includes are supported: a dump does not record nginx's
    conf-prefix for relative paths. Empty absolute globs are valid nginx
    includes. The catalog must come from a successful, complete nginx -T.
    No host filesystem reads are used to fill holes in a captured snapshot.
    """
    marker = re.compile(rb"^# configuration file ([^\n]+):\r?$", re.MULTILINE)
    sections = list(marker.finditer(data))
    if not sections:
        tree = sw.parse_nginx(data)
        if any(d.name.value == "include" for d, _ in sw._walk(tree)):
            raise ValueError("unresolved include: supply the complete successful nginx -T dump")
        return tree
    if data[:sections[0].start()].strip():
        raise ValueError("unexpected text before nginx -T catalog")
    files = {}
    for index, match in enumerate(sections):
        path = match[1].decode()
        if not path.startswith("/") or os.path.normpath(path) != path or path in files:
            raise ValueError("ambiguous nginx -T file catalog")
        end = sections[index + 1].start() if index + 1 < len(sections) else len(data)
        files[path] = data[match.end():end]
    visited = set()

    def expand(path, stack):
        if path in stack or len(stack) > 64:
            raise ValueError("cyclic or excessive include depth")
        visited.add(path)

        def directives(nodes):
            result = []
            for d in nodes:
                if d.name.value == "include":
                    if d.block is not None or len(d.args) != 1:
                        raise ValueError("ambiguous include directive")
                    pattern = d.args[0].value
                    if not pattern.startswith("/") or "$" in pattern or os.path.normpath(pattern) != pattern:
                        raise ValueError("unsupported include path (absolute canonical paths required)")
                    # nginx glob does not cross directory separators.
                    matches = sorted(f for f in files if len(f.split("/")) == len(pattern.split("/"))
                                     and all(fnmatch.fnmatchcase(a, b) for a, b in
                                             zip(f.split("/"), pattern.split("/"))))
                    if not matches and not any(c in pattern for c in "*?["):
                        raise ValueError("missing included file in nginx -T catalog")
                    for included in matches:
                        result.extend(expand(included, stack + (path,)))
                else:
                    result.append(sw.Directive(d.name, d.args,
                                               directives(d.block) if d.block is not None else None))
            return result
        return directives(sw.parse_nginx(files[path]))

    tree = expand(next(iter(files)), ())
    if visited != set(files):
        raise ValueError("unreachable file sections: include context cannot be established")
    return tree


def _variable_violations(tree, ref_maps):
    # The map input variables are nginx builtins; named captures are writable
    # shared variables too. Only the exact reviewed maps may define them.
    approved = {_signature(d) for d in ref_maps}
    variable = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
    map_values = [value for d, _ in sw._walk(ref_maps)
                  for value in [d.name.value] + [a.value for a in d.args]]
    captures = {capture for value in map_values for capture in
                re.findall(r"\(\?(?:<|P<)([A-Za-z_][A-Za-z0-9_]*)>", value)}
    protected = ({"$" + v for value in map_values for v in variable.findall(value)}
                 | {"$" + c for c in captures})
    # These stock directives only read variables. Everything else mentioning
    # a protected variable is an unsupported writer and fails closed.
    readers = {"log_format", "proxy_set_header", "add_header", "return", "rewrite",
               "if", "proxy_pass", "fastcgi_param", "uwsgi_param", "scgi_param"}
    violations = []

    def visit(nodes):
        for d in nodes:
            name = d.name.value
            if _signature(d) in approved:
                continue
            values = [d.name.value] + [a.value for a in d.args]
            found = {"$" + v for value in values for v in variable.findall(value)} & protected
            named = {c for value in values for c in
                     re.findall(r"\(\?(?:<|P<|\')([A-Za-z_][A-Za-z0-9_]*)(?:>|\')", value)} & {v[1:] for v in protected}
            if named or (found and name not in readers):
                violations.append(f"unsupported write or capture interference in {name} for protected log variables")
            # Embedded code / dynamically loaded modules can write variables
            # without a literal variable argument. They need a separate audit.
            if name == "load_module" or re.search(r"(?:lua|js|perl)", name):
                violations.append(f"unsupported dynamic variable writer: {name}")
            if d.block is not None:
                visit(d.block)
    visit(tree)
    return violations


def check(data: bytes, reference: bytes) -> Tuple[List[str], List[str], int, int]:
    """Returns (violations, warnings, servers, access_logs)."""
    tree = _effective_tree(data)
    ref_formats, ref_maps = _definitions(sw.parse_nginx(reference))
    if len(ref_formats) != 1 or len(ref_maps) != len(MAPS):
        raise ValueError("reference nginx.conf lacks the axisai_access definitions")
    violations: List[str] = _variable_violations(tree, ref_maps)
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
    if not servers:
        violations.append("no HTTP servers: effective configuration is incomplete")
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
