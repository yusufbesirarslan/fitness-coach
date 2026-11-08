"""AXISAI-LAUNCH-FREE-BE-01 mutation qualification (SQLite suites).

Each mutant must make the Launch Free / allowance contract tests fail.
Mutates files in place and restores them; run on a clean checkout:
    python -B tests/qualification/run_launch_free_be01_mutations.py
"""
import pathlib, subprocess, sys
ROOT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parents[2]
PY = sys.argv[2] if len(sys.argv) > 2 else sys.executable
TESTS = ["tests/test_launch_free_allowance.py", "tests/test_ai_allowances_contract.py"]
MUTANTS = [
 ("apple flag back in require_ready", "app/services/ai_allowances/execution.py",
  '"AI_ALLOWANCE_LEDGER_ENABLED", "AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED",\n    )',
  '"AI_ALLOWANCE_LEDGER_ENABLED", "AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED", "APPLE_SUBSCRIPTIONS_ENABLED",\n    )'),
 ("apple-off gate ignores campaign", "app/services/ai_allowances/execution.py",
  'policy.launch_free is None\n            or set(', 'False\n            and set('),
 ("campaign skipped in authorize", "app/services/ai_allowances/service.py",
  "    campaign_bucket = _launch_free(owner, policy, feature, now)\n", "    campaign_bucket = None\n"),
 ("dispatch accepts DISABLED without campaign", "app/services/ai_allowances/service.py",
  'subscription.state == "DISABLED" and policy.launch_free is not None', 'subscription.state == "DISABLED"'),
 ("final window advertises reset", "app/services/ai_allowances/policy.py",
  "    if n < campaign.windows:\n        return PERIOD_CAMPAIGN, end", "    if True:\n        return PERIOD_CAMPAIGN, end"),
 ("start exclusive", "app/services/ai_allowances/policy.py",
  "if now < campaign.boundary(0) or", "if now <= campaign.boundary(0) or"),
 ("window end inclusive", "app/services/ai_allowances/policy.py",
  "        if now < end:", "        if now <= end:"),
 ("settlement uses base version", "app/services/ai_allowances/service.py",
  "bucket.policy_version == policy.bucket_version(bucket.scope))", "bucket.policy_version == policy.version)"),
 ("exhaustion falls through to starter", "app/services/ai_allowances/service.py",
  "    if campaign_bucket is not None:\n        return campaign_bucket, P.SCOPE_LAUNCH_FREE\n",
  "    if campaign_bucket is not None and campaign_bucket.used + campaign_bucket.reserved < campaign_bucket.granted:\n        return campaign_bucket, P.SCOPE_LAUNCH_FREE\n"),
 ("a9c1 verifier rejects widened", "migrations/versions/a9c1d2e3f4b5_ai_allowance_ledger.py",
  "                or _later_check(checks.get(c.name), c.name, bind.dialect.name))", "                or False)"),
 ("migration skips widening", "migrations/versions/d8f1a3c5e7b9_ai_allowance_launch_free_scope.py",
  "    if _current(bind) == NARROW:\n        _replace(WIDE)\n    if _current(bind) != WIDE:", "    if False:"),
 ("null created_at gets units", "app/services/ai_allowances/service.py",
  "    if owner.created_at is None or feature not", "    if feature not"),
 ("view keeps STARTER access in window", "app/services/ai_allowances/service.py",
  'elif campaign_key is not None and owner.created_at is not None:', 'elif False:'),
]
results = []
for name, rel, old, new in MUTANTS:
    path = ROOT / rel; original = path.read_text()
    assert original.count(old) == 1, (name, original.count(old))
    path.write_text(original.replace(old, new))
    try:
        r = subprocess.run([PY, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *TESTS],
                           cwd=ROOT, capture_output=True, text=True)
        killed = r.returncode != 0
    finally:
        path.write_text(original)
    results.append((name, killed)); print(("KILLED  " if killed else "SURVIVED"), name, flush=True)
r = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", *TESTS], cwd=ROOT, capture_output=True, text=True)
print("BASELINE", "PASS" if r.returncode == 0 else "FAIL", r.stdout.strip().splitlines()[-1])
sys.exit(0 if all(k for _, k in results) and r.returncode == 0 else 1)
