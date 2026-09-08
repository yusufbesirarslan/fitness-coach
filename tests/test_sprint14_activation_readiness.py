"""Structural gates on the workout-session activation runbook (Sprint 14 PR5).

A runbook is executed by a human under pressure, from a document that nobody
runs. The failure this file exists to prevent is a runbook that goes stale
silently: it names a metric that was renamed, an event the code no longer emits,
a route that moved, or a rollback command that no longer works — and the first
person to discover any of that is an operator mid-incident.

Every assertion below ties a claim in `docs/WORKOUT_SESSION_ACTIVATION.md` to
the shipped code that must still support it. None of these tests exercise the
feature; they check that the document and the build still agree.

They also gate the two things PR5 must never do: change a flag default, or
encode a production activation.
"""
import re
from pathlib import Path

import pytest

from app import feature_flags
from app.feature_flags import ROLLOUT_FLAGS
from app.services.workout_session import metrics as lifecycle_metrics


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNBOOK_PATH = REPO_ROOT / "docs" / "WORKOUT_SESSION_ACTIVATION.md"
READINESS_PATH = (REPO_ROOT / "docs" / "superpowers" / "specs"
                  / "2026-09-07-sprint14-pr5-workout-session-activation-readiness.md")
FLAGS_DOC_PATH = REPO_ROOT / "docs" / "FEATURE_FLAGS.md"
ROLLOUT_DOC_PATH = REPO_ROOT / "docs" / "ROLLOUT.md"
MIGRATIONS_DIR = REPO_ROOT / "migrations" / "versions"

SESSIONS_FLAG = "FITX_WORKOUT_SESSIONS_ENABLED"
METRICS_FLAG = "RUNTIME_METRICS_ENABLED"

RUNBOOK = RUNBOOK_PATH.read_text(encoding="utf-8")
EVIDENCE_CLASSES = (
    "VERIFIED LIVE IN STAGING",
    "VERIFIED BY CI",
    "VERIFIED BY STATIC/STRUCTURAL TEST",
    "NOT EXERCISED",
    "BLOCKED",
)
READINESS = READINESS_PATH.read_text(encoding="utf-8")


def _flag(key):
    for record in ROLLOUT_FLAGS:
        if record.key == key:
            return record
    raise AssertionError(f"{key} is not a registered rollout flag")


# ── The documents exist and are reachable from the canonical docs ──────────
def test_the_runbook_is_linked_from_both_canonical_flag_documents():
    """An unreferenced runbook is a runbook nobody finds during an incident."""
    for path in (FLAGS_DOC_PATH, ROLLOUT_DOC_PATH):
        text = path.read_text(encoding="utf-8")
        assert "WORKOUT_SESSION_ACTIVATION.md" in text, (
            f"{path.name} does not link the activation runbook")


def test_the_readiness_record_and_the_runbook_reference_each_other():
    assert "WORKOUT_SESSION_ACTIVATION.md" in READINESS
    assert "sprint14-pr5-workout-session-activation-readiness" in RUNBOOK


# ── Defaults must not move ─────────────────────────────────────────────────
def test_the_workout_session_flag_default_is_still_off():
    """PR5 is readiness work. A readiness PR that enables the thing it is
    assessing has stopped being readiness work."""
    assert _flag(SESSIONS_FLAG).default is False


def test_the_runtime_metrics_default_is_still_off():
    from app import config

    assert config.RUNTIME_METRICS_ENABLED is False, (
        "the repository default for RUNTIME_METRICS_ENABLED must stay OFF; "
        "a staging operator turns it on at run time, not by shipping a default")


def test_no_rollout_flag_ships_enabled():
    enabled = [f.key for f in ROLLOUT_FLAGS if f.default is not False]
    assert enabled == []


def test_the_flag_lifecycle_is_a_registered_state_and_not_an_invented_one():
    """PR5 must not mint a lifecycle value to describe its own outcome."""
    record = _flag(SESSIONS_FLAG)
    assert record.lifecycle in feature_flags.LIFECYCLE_STATES
    assert record.lifecycle == feature_flags.LIFECYCLE_STAGING_ONLY, (
        "no staging exercise has been performed, so the flag cannot have "
        "advanced past staging_only")


# ── The runbook must name what the code actually emits ─────────────────────
def test_the_runbook_names_the_real_lifecycle_metric():
    assert lifecycle_metrics.METRIC_NAME in RUNBOOK, (
        f"the runbook must name {lifecycle_metrics.METRIC_NAME!r}; an operator "
        "cannot query a metric the document renamed")


def test_the_runbook_lists_every_lifecycle_event_and_invents_none():
    """Both directions matter. A missing event leaves an operator unable to
    recognise a signal; an invented one sends them looking for a metric value
    that will never arrive."""
    for event in lifecycle_metrics.EVENTS:
        assert re.search(rf"\b{re.escape(event)}\b", RUNBOOK), (
            f"lifecycle event {event!r} is emitted but absent from the runbook")

    # Every word the runbook presents as a lifecycle Event value must be real.
    quoted_events = set(re.findall(
        r"`Event`?\s*[=∈]\s*`?([a-z_]+)`?", RUNBOOK))
    assert quoted_events <= lifecycle_metrics.EVENTS | {"Event"}, (
        f"the runbook names lifecycle events the code never emits: "
        f"{quoted_events - lifecycle_metrics.EVENTS}")


def test_the_runbook_names_the_single_event_dimension():
    assert "`Event`" in RUNBOOK
    assert "cardinality" in RUNBOOK.lower()


def test_the_runbook_names_the_real_metric_namespace():
    from app.config import RUNTIME_METRICS_NAMESPACE

    assert RUNTIME_METRICS_NAMESPACE in RUNBOOK


# ── The runbook must name the routes that actually exist ───────────────────
def test_the_runbook_lists_every_browser_session_route(app):
    """Derived from the running URL map, not from a hand-kept list."""
    browser_rules = {
        str(rule) for rule in app.url_map.iter_rules()
        if str(rule).startswith("/workout/session")
    }
    assert browser_rules, "no browser session routes are registered at all"
    for rule in browser_rules:
        # `/workout/session/<public_id>/checkpoint` is written with its
        # placeholder in the runbook, exactly as Flask renders it.
        assert rule in RUNBOOK, f"{rule} is registered but absent from the runbook"


def test_the_runbook_names_the_native_transport_prefix():
    assert "/api/v1/training/workout-sessions" in RUNBOOK


def test_the_runbook_records_that_the_native_half_needs_mobile_auth():
    """The native routes live inside the MOBILE_AUTH_ENABLED blueprint, so on a
    host where that is OFF this flag opens the browser half only. An operator
    who does not know that will record cases E-K as failures."""
    assert "MOBILE_AUTH_ENABLED" in RUNBOOK


# ── The runbook must name the real refusal vocabulary ──────────────────────
def test_the_runbook_publishes_the_browser_refusal_codes_the_build_renders():
    from app.blueprints.training import _SESSION_ERROR_RENDERING

    for code, status in _SESSION_ERROR_RENDERING.values():
        assert f"`{code}`" in RUNBOOK, (
            f"browser refusal code {code!r} is rendered but undocumented")
        assert str(status) in RUNBOOK, (
            f"HTTP status {status} for {code!r} is absent from the runbook")


def test_the_runbook_publishes_the_canonical_public_codes():
    from app.services.workout_session import errors

    published = {
        value.public_code for name, value in vars(errors).items()
        if isinstance(value, type) and getattr(value, "public_code", None)
    }
    documented = {code for code in published if code in RUNBOOK}
    # Not every canonical class surfaces on the exercised paths; the ones the
    # matrix can actually produce must be documented.
    required = {
        "TRAINING_SESSION_NOT_FOUND",
        "TRAINING_SESSION_INVALID_REVISION",
        "TRAINING_SESSION_REVISION_CONFLICT",
        "TRAINING_SESSION_IDEMPOTENCY_CONFLICT",
        "TRAINING_SESSION_TERMINAL",
        "TRAINING_SESSION_STALE",
        "TRAINING_SESSION_UNAVAILABLE",
    }
    assert required <= published, "the required codes are no longer canonical"
    assert required <= documented, (
        f"undocumented native codes: {sorted(required - documented)}")


def test_the_runbook_names_the_two_response_headers_a_client_branches_on():
    assert "Session-Resolution" in RUNBOOK
    assert "Idempotency-Replayed" in RUNBOOK


def test_the_runbook_names_the_two_required_checkpoint_headers():
    assert "If-Match" in RUNBOOK
    assert "Idempotency-Key" in RUNBOOK


# ── Rollback must stay documented, and stay a flag flip ────────────────────
def test_the_documented_rollback_command_is_present_and_is_the_flag_off_value():
    assert f"{SESSIONS_FLAG}=0" in RUNBOOK, (
        "the canonical rollback (set the flag to 0) must appear verbatim")


def test_the_runbook_forbids_the_destructive_rollback_steps():
    """Rollback safety is the claim that no user data is touched. If the
    runbook stops saying so, the claim has quietly become unverified."""
    lowered = RUNBOOK.lower()
    assert "downgrade" in lowered
    assert "delete `workoutsession` rows" in lowered


def test_the_registry_rollback_still_describes_a_flag_flip():
    rollback = _flag(SESSIONS_FLAG).rollback
    assert f"{SESSIONS_FLAG}=0" in rollback
    assert "not rolled back" in rollback.lower()


def test_the_runbook_requires_post_rollback_inertness_verification():
    for token in ("contract_version=1", "404", "not deleted"):
        assert token in RUNBOOK, (
            f"post-rollback inertness evidence must mention {token!r}")


# ── No production activation may be encoded ────────────────────────────────
def test_the_runbook_encodes_no_activation_command_outside_a_staging_guard():
    """Every `FITX_WORKOUT_SESSIONS_ENABLED=1` instruction must be reachable
    only after the staging assertion. The guard name is the marker."""
    assert "fitx_assert_staging" in RUNBOOK
    activation_lines = [
        line for line in RUNBOOK.splitlines()
        if f"{SESSIONS_FLAG}=1" in line
    ]
    assert activation_lines, "the runbook must show how to activate in staging"
    for line in activation_lines:
        assert "staging" in line.lower() or line.lstrip().startswith("#"), (
            f"activation instruction without a staging qualifier: {line!r}")


def test_the_runbook_states_that_it_does_not_authorize_production():
    lowered = RUNBOOK.lower()
    assert "does not authorize production activation" in lowered
    assert "ready for activation" in lowered or "≠ activated" in RUNBOOK


def test_the_runbook_commits_no_hostname_and_uses_placeholders():
    """A committed hostname is how a staging runbook silently becomes a
    production one."""
    assert "<STAGING_HOST>" in RUNBOOK
    assert "<PRODUCTION_HOST>" in RUNBOOK
    # A bare domain literal (anything.tld) has no business in this document.
    # Loopback and the placeholders are the only hosts it may name.
    for match in re.findall(r"https?://([^\s/'\"`)]+)", RUNBOOK):
        host = match.split(":")[0]
        assert host in {"127.0.0.1", "localhost"}, (
            f"the runbook names a concrete host {host!r}; use a placeholder")


def test_no_secret_shaped_material_is_committed_in_either_document():
    forbidden = ("AKIA", "aws_secret_access_key", "BEGIN PRIVATE KEY",
                 "password=", "SECRET_KEY=", "Bearer ey")
    for name, text in (("runbook", RUNBOOK), ("readiness", READINESS)):
        for token in forbidden:
            assert token not in text, f"{name} contains {token!r}"


# ── Migration prerequisites must remain true ───────────────────────────────
def _revision_ids():
    ids = set()
    for path in MIGRATIONS_DIR.glob("*.py"):
        match = re.search(r"^revision\s*=\s*[\"'](.+?)[\"']",
                          path.read_text(encoding="utf-8"), re.M)
        if match:
            ids.add(match.group(1))
    return ids


@pytest.mark.parametrize("revision", ["a994f9bed783", "f5a6b7c8d9e0"])
def test_the_prerequisite_migrations_named_by_the_runbook_still_exist(revision):
    assert revision in _revision_ids(), (
        f"the runbook names migration {revision} as an activation "
        "prerequisite, but it is not in migrations/versions")
    assert revision in RUNBOOK
    assert revision in _flag(SESSIONS_FLAG).prerequisites[0] + "".join(
        _flag(SESSIONS_FLAG).prerequisites), (
        f"{revision} is no longer named in the flag's own prerequisites")


def test_pr5_adds_no_migration_and_the_head_the_runbook_names_is_current():
    """S14-9: the sprint adds no migration, and the runbook's stated head must
    be the actual single head — an operator verifies `alembic heads` against
    this document."""
    import ast as _ast

    revisions, downs = {}, set()
    for path in MIGRATIONS_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        rev = re.search(r"^revision\s*=\s*[\"'](.+?)[\"']", text, re.M)
        if rev:
            revisions[rev.group(1)] = path.name
        down = re.search(r"^down_revision\s*=\s*(.+)$", text, re.M)
        if down:
            try:
                value = _ast.literal_eval(down.group(1).strip())
            except (ValueError, SyntaxError):
                value = None
            if isinstance(value, str):
                downs.add(value)
            elif isinstance(value, (tuple, list)):
                downs.update(value)

    heads = sorted(set(revisions) - downs)
    assert heads == ["f5a6b7c8d9e0"], f"expected one head, found {heads}"


# ── The readiness record must stay honest ──────────────────────────────────
def test_the_readiness_record_does_not_claim_sprint_14_is_closed():
    """The specific historical falsehood this sprint must not commit.

    The record is allowed to *prepare* the closure wording — that is what
    state-aware documentation looks like — but the status line it publishes
    must say OPEN, and it must say so unconditionally somewhere a reader
    cannot miss.
    """
    assert "SPRINT 14 STATUS: OPEN" in READINESS
    assert "Sprint 14 is not closed by this document." in READINESS

    # Any closure phrasing must be conditional. A bare declarative one is the
    # failure. Every occurrence has to sit under the "Upon ... may be
    # considered closed" framing. Whitespace is normalized first: the framing
    # is prose and wraps across lines.
    flat = " ".join(READINESS.upper().split())
    for match in re.finditer(r"SPRINT 14 CLOSED", flat):
        preceding = flat[max(0, match.start() - 400):match.start()]
        assert "MAY BE CONSIDERED CLOSED" in preceding, (
            "an unconditional 'Sprint 14 closed' claim appears in the "
            "readiness record")


def test_the_runbook_defines_the_five_distinct_evidence_classes():
    """Collapsing these into "verified" is how a CI proof and a live staging
    proof become indistinguishable in a closure argument."""
    for label in EVIDENCE_CLASSES:
        assert label in RUNBOOK, f"evidence class {label!r} was dropped"


def test_the_readiness_verdict_matches_the_live_evidence_it_actually_holds():
    """A consistency gate that stays correct after a staging exercise runs.

    The record may only stop reporting BLOCKED once it carries live staging
    evidence, and it may only claim live staging evidence while the exercise
    has genuinely happened. One of the two states must hold.
    """
    claims_live = "VERIFIED LIVE IN STAGING" in READINESS.replace(
        "VERIFIED LIVE IN STAGING             observed", "")
    reports_blocked = "PR5 BLOCKED — STAGING EVIDENCE REQUIRED" in READINESS

    assert claims_live or reports_blocked, (
        "the readiness record neither claims live staging evidence nor "
        "reports the blocker — it has to do one or the other")
    if reports_blocked:
        assert "SPRINT 14 STATUS: OPEN" in READINESS, (
            "a blocked PR5 cannot coexist with a closed sprint")


def test_the_readiness_record_adjudicates_every_p2():
    for index in range(1, 9):
        assert f"P2-{index}" in READINESS, (
            f"P2-{index} carries no activation-risk ruling")


def test_the_flag_prerequisites_still_require_a_staging_exercise():
    """PR5 may record evidence. It may not delete the prerequisite it failed
    to satisfy."""
    prerequisites = " ".join(_flag(SESSIONS_FLAG).prerequisites).lower()
    assert "staging exercise" in prerequisites
    assert METRICS_FLAG.lower() in prerequisites
