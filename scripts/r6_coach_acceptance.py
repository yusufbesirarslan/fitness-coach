"""Post/watch integration for an already authorized R6 production read."""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from scripts import r6_coach_attestation as coach
from scripts.r6_recovery_acceptance import EVENT


def deployment_digest(log, revision, slot):
    """Extract only from the exact successful protected deploy job log."""
    events = list(EVENT.finditer(log))
    names = [event[1] for event in events]
    required = ("adult-registration-open", "release-committed", "coach-containment-proven",
                "release-complete")
    if any(names.count(name) != 1 for name in required):
        raise ValueError("R6_COACH_DEPLOYMENT_PROOF_MISSING_OR_DUPLICATE")
    opened, committed, proof, complete = (events[names.index(name)] for name in required)
    match = re.fullmatch(
        rf"digest=({coach.DIGEST.pattern}) version={coach.VERSION} "
        r"web_flag=false web_guard=false worker_flag=false worker_guard=false", proof[2] or "")
    if (not match or not opened.start() < committed.start() < proof.start() < complete.start()
            or opened[2] != f"revision={revision}"
            or committed[2] != f"revision={revision} route={slot}"
            or complete[2] != f"route={slot} revision={revision}"
            or {"release-failed", "commit-receipt-unproven", "retirement-incomplete",
                "capability-activation-incomplete"}.intersection(names)):
        raise ValueError("R6_COACH_DEPLOYMENT_PROOF_UNPROVEN")
    return match[1]


PROJECT = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")
GIT_SECONDS = 30


def source_checkout(deploy_dir, main_project, revision, sources, *, run=subprocess.run):
    """The canonical absolute checkout this reader runs from, at the approved commit.

    HostOps runs ``<context>/scripts/web_slot_runtime.py --deploy-dir <context>``
    and the slot runtime accepts only an absolute directory. For a post/watch
    read the verified checkout is that context, as in the engine's own
    ``--deploy-dir`` rule and the slot runtime's default. Refuse every other
    directory instead of resolving one. ``sources`` are the files of the loaded
    reader modules; each must be ``<deploy_dir>/scripts/<name>``.
    """
    # realpath(x) == x: absolute, normalized (no ".", "..", repeated or
    # trailing slash) and free of symlinks, so it names one directory.
    if (not isinstance(deploy_dir, str) or os.path.realpath(deploy_dir) != deploy_dir
            or not isinstance(main_project, str) or not PROJECT.fullmatch(main_project)
            or main_project.startswith("axisai-web-")
            or PurePosixPath(deploy_dir).name != main_project
            or not sources
            or any(Path(source).resolve().parent != Path(deploy_dir, "scripts")
                   for source in sources)):
        raise ValueError("R6_COACH_SOURCE_CHECKOUT_INVALID")
    # --no-optional-locks: a stat-dirty index is never refreshed or written.
    git = ["git", "--no-optional-locks", "-C", deploy_dir]
    head = run([*git, "rev-parse", "--verify", "HEAD^{commit}"], capture_output=True,
               text=True, timeout=GIT_SECONDS, check=False)
    status = run([*git, "status", "--porcelain=v1", "--untracked-files=all", "--", "scripts"],
                 capture_output=True, text=True, timeout=GIT_SECONDS, check=False)
    if (head.returncode != 0 or head.stdout.strip() != revision
            or status.returncode != 0 or status.stdout != ""):
        raise ValueError("R6_COACH_SOURCE_CHECKOUT_UNPROVEN")
    return deploy_dir


def collect(ops, approved, expected_digest, challenge):
    if not coach.DIGEST.fullmatch(expected_digest) or not coach.NONCE.fullmatch(challenge):
        raise ValueError("R6_COACH_ACCEPTANCE_BINDING_INVALID")
    result = ops.coach_release_proof(approved, 90)
    if result["digest"] != expected_digest:
        raise ValueError("R6_COACH_DEPLOYED_BOOTS_CHANGED")
    return {**result, "observed_at": datetime.now(timezone.utc).isoformat(),
            "observation": coach.boot_digest(challenge)}


def verify_observation(report, approved, expected_digest, challenge, started, ended):
    """Outer one-shot checker gate for the exact authorized host invocation."""
    if (not isinstance(report, dict)
            or set(report) != {"binding", "web", "worker", "digest", "observed_at", "observation"}
            or report["binding"] != approved or report["digest"] != expected_digest
            or not coach.NONCE.fullmatch(challenge)
            or report["observation"] != coach.boot_digest(challenge)):
        raise ValueError("R6_COACH_OBSERVATION_BINDING_INVALID")
    observed = datetime.fromisoformat(report["observed_at"])
    if observed.utcoffset() != timezone.utc.utcoffset(None) or not started <= observed <= ended:
        raise ValueError("R6_COACH_OBSERVATION_TIME_INVALID")
    for role in ("web", "worker"):
        actor = report[role]
        if not isinstance(actor, dict) or set(actor) != coach.FIELDS | {"container", "started"}:
            raise ValueError("R6_COACH_OBSERVATION_ACTOR_INVALID")
        coach.validate_process({key: actor[key] for key in coach.FIELDS},
                               role=role, revision=approved["revision"])
        if (not coach.DIGEST.fullmatch(actor["container"])
                or not coach.STARTED.fullmatch(actor["started"])
                or actor["heads"] != approved["heads"]):
            raise ValueError("R6_COACH_OBSERVATION_ACTOR_INVALID")
    if coach.fingerprint(approved, report["web"], report["worker"]) != expected_digest:
        raise ValueError("R6_COACH_OBSERVATION_DIGEST_INVALID")


def main():
    from scripts.r6_deploy_transaction import HostOps

    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("deploy-dir", "main-project", "revision", "tree", "image", "slot",
                "digest", "challenge"):
        parser.add_argument(f"--{arg}", required=True)
    parser.add_argument("--head", required=True, action="append")
    args = parser.parse_args()
    try:
        approved = coach.binding(args.revision, args.tree, args.image, args.slot, sorted(args.head))
        checkout = source_checkout(
            args.deploy_dir, args.main_project, args.revision,
            (__file__, coach.__file__, sys.modules["scripts.r6_recovery_acceptance"].__file__,
             sys.modules[HostOps.__module__].__file__))
        # Read-only: no work-directory operation is reachable from collect().
        ops = HostOps(deploy_dir=checkout, context_dir=checkout, work_dir=checkout,
                      main_project=args.main_project, deploy_sha=args.revision, previous_commit="")
        print(json.dumps(collect(ops, approved, args.digest, args.challenge), sort_keys=True))
    except Exception:
        raise SystemExit("R6_COACH_LIVE_ACCEPTANCE_UNPROVEN") from None


if __name__ == "__main__":
    main()
