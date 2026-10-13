#!/usr/bin/python3 -I
"""Read-only check that nginx access logs cannot record query strings (PRIV-LOG-01).

    python3 -I scripts/axisai_nginx_log_privacy.py [--catalog-only] <successful-complete-nginx-T-dump>

The deployment input is the stdout of a successful, complete ``nginx -T``,
checked on the host that produced it. File sections form a catalog which is
recursively expanded at each include site. Absolute canonical paths and
absolute ``*``/``?`` globs are supported, with glob(3) semantics (a wildcard
never matches a leading dot; results sorted bytewise). Relative, missing,
cyclic, duplicate, unreachable or out-of-order file sections and unmodelled
glob syntax fail closed; includes in a standalone site file also fail closed.
Standalone texts without includes are accepted for unit/source checks. Never
concatenate site files manually.

Catalog provenance cannot be proven from the text: a comment inside a file
may look exactly like nginx's section marker, and a dump cut at a section
boundary is self-consistent. So by default every section must equal the file
on this host (plus the newline nginx appends) and every include glob must
match the same files on disk. ``--catalog-only`` skips that for off-host
analysis and reports ``provenance=catalog-only``, which is not deployment
evidence. Neither mode proves the RUNNING configuration: ``nginx -T`` reads
the files on disk, which a running master adopts only on reload.

The exact reference log format and maps are required once. Every HTTP server
must declare its own access_log (off or axisai_access), and every descendant
log must also be off or axisai_access. This conservatively audits all locations,
including nested/named locations and internal-redirect destinations, regardless
of reachability. The distribution HTTP log may remain because all servers
shadow it. All included directives are checked at their original contexts.

nginx compares variable names, named captures and ``access_log`` format names
case-insensitively, and reads every word through its escape rules
(``\\'`` ``\\"`` ``\\\\`` in unquoted words too), so all identities here are the
nginx-stored values, canonical in ASCII lower case:
``$AXISAI_LOG_PATH`` is ``$axisai_log_path`` and ``log_format AXISAI_ACCESS``
competes with ``axisai_access``. The maps' outputs, builtin inputs and named
capture dependencies are protected against other writers/captures.
Unsupported consumers of these variables, embedded Lua/JS/Perl and dynamic
module loading fail closed pending a separate module audit. Unrelated stock
``set`` directives remain allowed.

Exit codes: 0 ok, 1 violation (details on stderr), 2 usage, unparsable input or
unverifiable provenance. The one stdout line is
``result=ok|violation servers=N access_logs=N warnings=N provenance=P``.
Warnings are informational only: their single class is a query-bearing
``log_format`` that no server can use (every server must log with
``axisai_access`` or ``off``). Anything that can affect what a server logs
is a violation.
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
MARKER = re.compile(rb"^# configuration file ([^\n]+):\r?$", re.MULTILINE)
VARIABLE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
# PCRE named groups: (?<name>...), (?P<name>...) and (?'name'...).
NAMED_CAPTURE = re.compile(r"\(\?(?:P?<([A-Za-z_][A-Za-z0-9_]*)>|'([A-Za-z_][A-Za-z0-9_]*)')")
# Variables that carry the query string, the raw Referer (which carries the
# previous page's query string) or credentials. `$request` is the full request
# line; `$request_method`/`$request_time`/`$request_id` are not matched.
FORBIDDEN_VARIABLE = re.compile(
    r"\$\{?(?:(?:request|request_uri|args|query_string|is_args|request_body|remote_user"
    r"|http_referer|http_authorization|http_cookie|http_proxy_authorization)(?![A-Za-z0-9_])"
    r"|(?:arg|cookie)_[A-Za-z0-9_]+)", re.IGNORECASE)
NON_HTTP_CONTEXTS = {"stream", "mail"}

EXIT_OK, EXIT_VIOLATION, EXIT_USAGE = 0, 1, 2


def _ident(name: str) -> str:
    """nginx identifier identity: ngx_strncasecmp / ngx_strcasecmp (ASCII)."""
    return name.lower()


def _nginx_word(raw: bytes) -> str:
    """A word's value as ngx_conf_read_token stores it (ngx_conf_file.c), for
    quoted and unquoted words alike: ``\\"`` ``\\'`` ``\\\\`` drop the
    backslash, ``\\t`` ``\\r`` ``\\n`` become control characters, and any other
    backslash is kept together with the byte after it."""
    out, i = bytearray(), 0
    while i < len(raw):
        if raw[i:i + 1] == b"\\" and i + 1 < len(raw):
            nxt = raw[i + 1:i + 2]
            if nxt in (b'"', b"'", b"\\"):
                out += nxt
                i += 2
                continue
            if nxt in (b"t", b"r", b"n"):
                out += {b"t": b"\t", b"r": b"\r", b"n": b"\n"}[nxt]
                i += 2
                continue
        out += raw[i:i + 1]
        i += 1
    return out.decode("utf-8")


def _parse(data: bytes):
    """sw.parse_nginx with every word re-read from its source bytes as nginx
    stores it. The shared tokenizer (also the route-control parser) keeps
    unquoted words raw and drops the backslash of every escape in quoted
    words, so ``(?\\'AXISAI_LOG_PATH\\'.+)`` would hide a named capture.
    nginx keeps ``${`` inside a word where the tokenizer opens a block: that
    boundary is not modelled and fails closed."""
    tokens = sw.tokenize(data)
    for word, brace in zip(tokens, tokens[1:]):
        if (not word.quoted and not brace.quoted and brace.value == "{"
                and word.end == brace.start and word.value.endswith("$")):
            raise sw.NginxSyntaxError("unquoted ${ continues an nginx word: not modelled")

    def value(tok):
        raw = data[tok.start + 1:tok.end - 1] if tok.quoted else data[tok.start:tok.end]
        return sw.Token(_nginx_word(raw), tok.start, tok.end, tok.quoted)

    def rebuild(nodes):
        return [sw.Directive(value(d.name), [value(a) for a in d.args],
                             None if d.block is None else rebuild(d.block)) for d in nodes]
    return rebuild(sw.parse_nginx(data))


def _variables(value: str) -> set:
    return {"$" + _ident(v) for v in VARIABLE.findall(value)}


def _captures(value: str) -> set:
    return {_ident(a or b) for a, b in NAMED_CAPTURE.findall(value)}


def _signature(directive) -> Tuple:
    """Parsed shape of a directive: name, argument values and nested block."""
    return (directive.name.value, tuple(a.value for a in directive.args),
            None if directive.block is None else tuple(_signature(d) for d in directive.block))


def _definitions(tree) -> Tuple[List, List]:
    nodes = list(sw._walk(tree))
    formats = [d for d, _ in nodes if d.name.value == "log_format" and d.args
               and _ident(d.args[0].value) == FORMAT]
    maps = [d for d, _ in nodes if d.name.value == "map" and len(d.args) == 2
            and _ident(d.args[1].value) in MAPS]
    return formats, maps


def _format_text(directive) -> str:
    return "".join(a.value for a in directive.args[1:] if not a.value.startswith("escape="))


def _glob_component(name: str, pattern: str) -> bool:
    """One path component under glob(3) with flags 0 (ngx_open_glob)."""
    if name.startswith(".") and not pattern.startswith("."):
        return False    # a leading period must be matched explicitly
    return fnmatch.fnmatchcase(name, pattern)


def _glob(pattern: str, candidates) -> List[str]:
    parts = pattern.split("/")
    return sorted((f for f in candidates if len(f.split("/")) == len(parts)
                   and all(_glob_component(a, b) for a, b in zip(f.split("/"), parts))),
                  key=os.fsencode)


def _host_glob(pattern: str) -> List[str]:
    """The same matcher applied to this host's directories."""
    paths = ["/"]
    for part in pattern.split("/")[1:]:
        found = []
        for base in paths:
            if not any(c in part for c in "*?"):
                if os.path.exists(os.path.join(base, part)):
                    found.append(os.path.join(base, part))
                continue
            try:
                names = os.listdir(base)
            except OSError:
                continue
            found.extend(os.path.join(base, n) for n in names if _glob_component(n, part))
        paths = found
    return sorted(paths, key=os.fsencode)


def _verify_host(files, globs) -> None:
    for path, content in files.items():
        try:
            with open(path, "rb") as handle:
                disk = handle.read()
        except OSError as exc:
            raise ValueError(f"catalog section {path} is not a readable host file ({exc.strerror})")
        # nginx -T writes each file's exact bytes followed by one newline.
        if content != disk + b"\n":
            raise ValueError(f"catalog section {path} differs from the host file: "
                             "not an authentic complete nginx -T of this host")
    for pattern, matches in globs:
        if _host_glob(pattern) != matches:
            raise ValueError(f"include {pattern} matches different host files than the catalog: "
                             "truncated or foreign nginx -T")


def _effective_tree(data, verify_host=False):
    """Expand nginx -T's file catalog at each include site, never flatten it.

    Only absolute includes are supported: a dump does not record nginx's
    conf-prefix for relative paths. Empty absolute globs are valid nginx
    includes. The catalog must come from a successful, complete nginx -T;
    sections must appear in the order nginx first opens each file. Host
    files are only compared (``verify_host``), never used to fill holes.
    """
    sections = list(MARKER.finditer(data))
    if not sections:
        tree = _parse(data)
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
        files[path] = data[match.end() + 1:end]   # after the marker line's newline
    visited, globs = [], []

    def expand(path, stack):
        if path in stack or len(stack) > 64:
            raise ValueError("cyclic or excessive include depth")
        if path not in visited:
            visited.append(path)

        def directives(nodes):
            result = []
            for d in nodes:
                if d.name.value == "include":
                    if d.block is not None or len(d.args) != 1:
                        raise ValueError("ambiguous include directive")
                    pattern = d.args[0].value
                    if not pattern.startswith("/") or "$" in pattern or os.path.normpath(pattern) != pattern:
                        raise ValueError("unsupported include path (absolute canonical paths required)")
                    if "[" in pattern or "\\" in pattern:
                        raise ValueError("unsupported include glob: bracket/escape syntax is not modelled")
                    if any(c in pattern for c in "*?"):
                        # glob(3): never crosses "/", sorted, may be empty.
                        matches = _glob(pattern, files)
                        globs.append((pattern, matches))
                    elif pattern in files:
                        matches = [pattern]
                    else:
                        raise ValueError("missing included file in nginx -T catalog")
                    for included in matches:
                        result.extend(expand(included, stack + (path,)))
                else:
                    result.append(sw.Directive(d.name, d.args,
                                               directives(d.block) if d.block is not None else None))
            return result
        return directives(_parse(files[path]))

    tree = expand(next(iter(files)), ())
    if set(visited) != set(files):
        raise ValueError("unreachable file sections: include context cannot be established")
    if visited != list(files):
        raise ValueError("nginx -T section order differs from the include order: "
                         "not an authentic complete dump")
    if verify_host:
        _verify_host(files, globs)
    return tree


def _variable_violations(tree, ref_maps):
    # The map input variables are nginx builtins; named captures are writable
    # shared variables too. Only the exact reviewed maps may define them.
    approved = {_signature(d) for d in ref_maps}
    map_values = [value for d, _ in sw._walk(ref_maps)
                  for value in [d.name.value] + [a.value for a in d.args]]
    protected = set().union(*(_variables(v) | {"$" + c for c in _captures(v)} for v in map_values))
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
            found = set().union(*map(_variables, values)) & protected
            named = set().union(*map(_captures, values)) & {v[1:] for v in protected}
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


def check(data: bytes, reference: bytes, verify_host: bool = False) -> Tuple[List[str], List[str], int, int]:
    """Returns (violations, warnings, servers, access_logs)."""
    tree = _effective_tree(data, verify_host)
    ref_formats, ref_maps = _definitions(_parse(reference))
    if len(ref_formats) != 1 or len(ref_maps) != len(MAPS):
        raise ValueError("reference nginx.conf lacks the axisai_access definitions")
    violations: List[str] = _variable_violations(tree, ref_maps)
    warnings: List[str] = []

    formats, maps = _definitions(tree)
    if [_signature(d) for d in formats] != [_signature(ref_formats[0])]:
        violations.append(f"log_format {FORMAT} must be defined exactly once, equal to the reference")
    ref_by_var = {d.args[1].value: _signature(d) for d in ref_maps}
    for var in MAPS:
        found = [_signature(d) for d in maps if _ident(d.args[1].value) == var]
        if found != [ref_by_var[var]]:
            violations.append(f"map for {var} must be defined exactly once, equal to the reference")
    if FORBIDDEN_VARIABLE.search(_format_text(ref_formats[0])):
        violations.append(f"reference log_format {FORMAT} contains a forbidden variable")
    names = {}
    for d, parents in sw._walk(tree):
        if d.name.value != "log_format" or not d.args:
            continue
        name = _ident(d.args[0].value)
        if not NON_HTTP_CONTEXTS & {p.name.value for p in parents}:
            names[name] = names.get(name, 0) + 1
        if FORBIDDEN_VARIABLE.search(_format_text(d)):
            (violations if name == FORMAT else warnings).append(
                f"log_format {d.args[0].value} contains a query-bearing or credential variable")
    # access_log resolves format names case-insensitively (first match wins)
    # while nginx only refuses exact-case duplicates.
    violations.extend(f"log_format {name} is defined more than once (names are case-insensitive)"
                      for name, count in names.items() if count > 1 and name != FORMAT)

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
            if "server" in names and (len(args) < 2 or _ident(args[1]) != FORMAT):
                violations.append(f"access_log {' '.join(args)} inside a server does not use {FORMAT}")
    if not servers:
        violations.append("no HTTP servers: effective configuration is incomplete")
    return violations, warnings, servers, access_logs


def main(argv: Sequence[str], reference_path: Optional[str] = None,
         stdin=None, out=print, err=None) -> int:
    err = err or (lambda line: print(line, file=sys.stderr))
    argv = list(argv)
    catalog_only = argv[:1] == ["--catalog-only"]
    if catalog_only:
        argv.pop(0)
    if len(argv) != 1 or argv[0].startswith("--"):
        err("usage: axisai_nginx_log_privacy.py [--catalog-only] <nginx-T-dump | ->")
        return EXIT_USAGE
    try:
        if argv[0] == "-":
            data = (stdin or sys.stdin.buffer).read()
        else:
            with open(argv[0], "rb") as handle:
                data = handle.read()
        with open(reference_path or REFERENCE, "rb") as handle:
            reference = handle.read()
        violations, warnings, servers, access_logs = check(data, reference, verify_host=not catalog_only)
    except (OSError, ValueError, UnicodeDecodeError, sw.NginxSyntaxError) as exc:
        err(f"error: {type(exc).__name__}: {exc}")
        return EXIT_USAGE
    for line in warnings:
        err(f"warning: {line}")
    for line in violations:
        err(f"violation: {line}")
    result = "violation" if violations else "ok"
    provenance = ("standalone" if not MARKER.search(data)
                  else "catalog-only" if catalog_only else "host-verified")
    out(f"result={result} servers={servers} access_logs={access_logs} "
        f"warnings={len(warnings)} provenance={provenance}")
    return EXIT_VIOLATION if violations else EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
