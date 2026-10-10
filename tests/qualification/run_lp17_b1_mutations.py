"""Run isolated LP17-B1 semantic mutations locally; restore sources after each run.

Usage: python tests/qualification/run_lp17_b1_mutations.py
Only run in the dedicated LP17-B1 worktree, without concurrent tests.
"""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
TEST = "tests/test_lp17_b1_today_guidance_api.py"
ROUTE = "app/blueprints/mobile_today_guidance.py"
MUTATIONS = (
    ("rollout_gate_removed", ROUTE,
     "        if not _guidance_enabled():\n            abort(404)",
     "        if False:\n            abort(404)",
     "test_flag_off_is_an_absent_route_before_authentication"),
    ("rollout_gate_inside_auth", ROUTE,
     "@_rollout_gated\n@require_mobile_auth\n",
     "@require_mobile_auth\n@_rollout_gated\n",
     "test_flag_off_is_an_absent_route_before_authentication"),
    ("owner_from_query_string", ROUTE,
     "owner_id = g.mobile_user.id",
     'owner_id = int(__import__("flask").request.args.get("user_id", g.mobile_user.id))',
     "test_spoofed_owner_date_and_timezone_inputs_are_ignored"),
    ("strict_failure_as_success", ROUTE,
     '"Today guidance is temporarily unavailable.", 503, True)',
     '"Today guidance is temporarily unavailable.", 200, True)',
     "test_strict_failures_are_a_typed_retryable_503_without_detail"),
    ("flag_default_on", "app/feature_flags.py",
     'key="FITX_MOBILE_TODAY_GUIDANCE_ENABLED",\n'
     '        capability="LP17-B1 read-only Bearer GET /api/v1/today/guidance over the '
     'LP17 Today Guidance read model; OFF is an absent route.",\n'
     '        owner=_OWNER,\n        default=False,',
     'key="FITX_MOBILE_TODAY_GUIDANCE_ENABLED",\n'
     '        capability="LP17-B1 read-only Bearer GET /api/v1/today/guidance over the '
     'LP17 Today Guidance read model; OFF is an absent route.",\n'
     '        owner=_OWNER,\n        default=True,',
     "test_flag_is_a_default_off_registry_record_depending_on_native_auth"),
)


def main():
    results = []
    for name, relative, before, after, test in MUTATIONS:
        path = ROOT / relative
        original = path.read_text()
        if original.count(before) != 1:
            raise RuntimeError(f"mutation must apply exactly once: {name}")
        try:
            path.write_text(original.replace(before, after))
            run = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                 f"{TEST}::{test}"],
                cwd=ROOT, capture_output=True, text=True)
            killed = run.returncode == 1 and " failed" in run.stdout
            results.append({"mutation": name, "test": test, "killed": killed})
            if not killed:
                print(run.stdout + run.stderr)
        finally:
            path.write_text(original)
    print(json.dumps(results, indent=2))
    return 0 if all(item["killed"] for item in results) else 1


if __name__ == "__main__":
    sys.exit(main())
