"""Run isolated LP17 semantic mutations locally; restore sources after each run.

Usage: python tests/qualification/run_lp17_mutations.py
Only run in the dedicated LP17 worktree, without concurrent tests.
"""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
TEST = "tests/test_lp17_today_guidance_read_model.py"
MUTATIONS = (
    ("unknown_water_to_zero", "app/services/today_guidance_projection.py",
     '"state": "unavailable", "amount": None',
     '"state": "unavailable", "amount": 0',
     "test_secondary_total_failure_is_unknown"),
    ("accept_mixed_server_days", "app/services/today_guidance_read_model.py",
     'if training["date"] != day or (nutrition is not None and nutrition["day"] != day):',
     'if False:',
     "test_midnight_between_sources_requires_reread"),
    ("invent_global_training_priority", "app/services/today_guidance_projection.py",
     '"primary": None', '"primary": decision.primary_kind or "create_plan"',
     "test_checkin_changes_and_account_isolation"),
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
                [sys.executable, "-m", "pytest", "-q", f"{TEST}::{test}"],
                cwd=ROOT, capture_output=True, text=True)
            killed = run.returncode == 1 and "1 failed" in run.stdout
            results.append({"mutation": name, "test": test, "killed": killed})
            if not killed:
                print(run.stdout + run.stderr)
        finally:
            path.write_text(original)
    print(json.dumps(results, indent=2))
    return 0 if all(item["killed"] for item in results) else 1


if __name__ == "__main__":
    sys.exit(main())
