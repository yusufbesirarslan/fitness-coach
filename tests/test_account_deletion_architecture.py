"""One account-deletion authority, one thin transport (LP-11 gate).

Source-level guards that fail if the deletion lifecycle leaks into the route,
if a second module starts deleting Cognito identities or purging accounts, or
if the route starts reading anything but the verified principal.

    python -m pytest tests/test_account_deletion_architecture.py -v
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = "app/services/account_deletion.py"
ROUTE = "app/blueprints/mobile_account_deletion.py"
CLI = "app/cli.py"


def _tree(relative):
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"))


def _python_sources():
    for base in ("app", "fitx_mcp"):
        for path in sorted((ROOT / base).rglob("*.py")):
            yield path.relative_to(ROOT).as_posix()
    for path in sorted(ROOT.glob("*.py")):
        yield path.relative_to(ROOT).as_posix()


def _names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
            names.add(node.module or "")
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


def _referencing(name, exclude=()):
    return {path for path in _python_sources()
            if path not in exclude and name in _names(_tree(path))}


def test_only_the_service_deletes_provider_identities():
    assert _referencing(
        "delete_user", exclude={"app/services/cognito_service.py"}) == {SERVICE}


def test_only_the_service_and_the_operator_cli_purge_accounts():
    assert _referencing("_purge_user") == {SERVICE, CLI}


def test_exactly_one_transport_calls_the_lifecycle():
    callers = {path for path in _python_sources()
               if path != SERVICE and "account_deletion.delete_account("
               in (ROOT / path).read_text(encoding="utf-8")}
    assert callers == {ROUTE}


def test_route_is_transport_only():
    """No provider, storage, model or purge knowledge in the adapter, and the
    only request state it reads is whether a body/query was sent (to refuse)."""
    names = _names(_tree(ROUTE))
    for forbidden in ("cognito_service", "s3_helper", "session_store",
                      "_purge_user", "User", "MobileAuthSession", "models",
                      "get_json", "form", "values", "headers", "view_args"):
        assert forbidden not in names, forbidden
    source = (ROOT / ROUTE).read_text(encoding="utf-8")
    assert "request.args or request.get_data(cache=True)" in source
    assert ("account_deletion.delete_account(\n"
            "            g.mobile_user, g.mobile_session, g.mobile_claims)"
            in source)


def test_service_has_no_request_or_presentation_dependency():
    flask_imports = {
        alias.name for node in ast.walk(_tree(SERVICE))
        if isinstance(node, ast.ImportFrom) and node.module == "flask"
        for alias in node.names}
    assert flask_imports == {"current_app"}
    names = _names(_tree(SERVICE))
    for forbidden in ("request", "jsonify", "abort", "make_response"):
        assert forbidden not in names, forbidden


# -- Anti-resurrection (deleted_identity) ---------------------------------------
DELETED_IDENTITY = "app/services/deleted_identity.py"
MOBILE_LOGIN = "app/services/mobile_auth.py"
WEB_LOGIN = "app/blueprints/auth.py"
REGISTRATION = "app/services/account_registration.py"
# The shared guard, or the web login's one-line wrapper around it (which
# rolls its pending write back and keeps the web answer).
GUARD_CALLS = ("deleted_identity.refuse_if_deleted", "_identity_deleted")


def _subject_writes(function):
    """Line numbers where `function` gives a local row a provider subject:
    `User(..., cognito_sub=...)` or `<row>.cognito_sub = ...`."""
    lines = []
    for node in ast.walk(function):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "User"
                and any(k.arg == "cognito_sub" for k in node.keywords)):
            lines.append(node.lineno)
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Attribute) and t.attr == "cognito_sub"
                   for t in targets):
                lines.append(node.lineno)
    return lines


def _calls(function, dotted):
    return sorted(node.lineno for node in ast.walk(function)
                  if isinstance(node, ast.Call)
                  and ast.unparse(node.func) == dotted)


def test_only_the_deletion_lifecycle_records_tombstones():
    assert _referencing("DeletedIdentityTombstone",
                        exclude={"app/models.py"}) == {DELETED_IDENTITY}
    recorders = {path for path in _python_sources()
                 if path != DELETED_IDENTITY and "deleted_identity.record("
                 in (ROOT / path).read_text(encoding="utf-8")}
    assert recorders == {SERVICE}


def test_the_tombstone_is_written_inside_the_purge_transaction():
    """Both exits of step 4 that end in success record the tombstone before
    their commit, and the purge path records it before `_purge_user`."""
    (purge,) = [node for node in ast.walk(_tree(SERVICE))
                if isinstance(node, ast.FunctionDef)
                and node.name == "_purge_if_released"]
    records = _calls(purge, "deleted_identity.record")
    purges = _calls(purge, "_purge_user")
    commits = _calls(purge, "db.session.commit")
    assert len(records) == 2 and len(purges) == 1 and len(commits) == 2
    assert records[0] < commits[0] and records[1] < purges[0] < commits[1]


def test_every_path_that_binds_a_provider_subject_refuses_a_deleted_one():
    """A local row gains a Cognito subject in exactly these functions. A NEW
    one fails here first. Every one of them checks the tombstone AFTER each
    write (the order the race argument needs: deleted_identity module doc).
    Registration is no exception: its subject was minted by SignUp in the
    same request, but that request can stall while the same person confirms,
    signs in and deletes the account — its late INSERT must be refused too."""
    found = {}
    for path in _python_sources():
        for node in ast.walk(_tree(path)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                writes = _subject_writes(node)
                if writes:
                    found[(path, node.name)] = (writes, sorted(
                        line for guard in GUARD_CALLS
                        for line in _calls(node, guard)))
    assert set(found) == {
        (MOBILE_LOGIN, "_resolve_user"),
        (WEB_LOGIN, "_reconcile_local_user"),
        (REGISTRATION, "register_account"),
    }
    for key, (writes, checks) in found.items():
        assert checks, key
        for write in writes:
            assert any(check > write for check in checks), (key, write)


def test_the_web_wrapper_is_the_shared_guard():
    (wrapper,) = [node for node in ast.walk(_tree(WEB_LOGIN))
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "_identity_deleted"]
    assert _calls(wrapper, "deleted_identity.refuse_if_deleted")
    assert _calls(wrapper, "db.session.rollback")
