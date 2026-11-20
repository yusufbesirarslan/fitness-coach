"""Post/watch integration for an already authorized R6 production read."""
import argparse
import json
import re
from datetime import datetime, timezone

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


def collect(ops, approved, expected_digest, challenge):
    if not coach.DIGEST.fullmatch(expected_digest) or not coach.NONCE.fullmatch(challenge):
        raise ValueError("R6_COACH_ACCEPTANCE_BINDING_INVALID")
    result = ops.coach_release_proof(approved, 90)
    if result["digest"] != expected_digest:
        raise ValueError("R6_COACH_DEPLOYED_BOOTS_CHANGED")
    return {**result, "observed_at": datetime.now(timezone.utc).isoformat(),
            "observation": coach.boot_digest(challenge)}


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
        ops = HostOps(deploy_dir=args.deploy_dir, context_dir=".", work_dir=".",
                      main_project=args.main_project, deploy_sha=args.revision, previous_commit="")
        print(json.dumps(collect(ops, approved, args.digest, args.challenge), sort_keys=True))
    except Exception:
        raise SystemExit("R6_COACH_LIVE_ACCEPTANCE_UNPROVEN") from None


if __name__ == "__main__":
    main()
