"""Run the F01/D1 selection against a task-owned disposable local PostgreSQL."""
import os
from pathlib import Path
import subprocess
import sys
import pgserver

root = Path(__file__).resolve().parents[2]
server = pgserver.get_server(Path('/private/tmp/axisai-f01-d1-20261110-pg'), cleanup_mode='stop')
env = dict(os.environ, FITX_PG_CONCURRENCY_TEST='1', PG_TEST_DATABASE_URL=server.get_uri(),
           AWS_PROFILE='axisai-staging', AWS_REGION='eu-central-1', AWS_EC2_METADATA_DISABLED='true',
           RUNTIME_METRICS_ENABLED='0')
result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-m', 'pg_concurrency',
    '-W', 'error::pytest.PytestUnraisableExceptionWarning', *sys.argv[1:]], cwd=root, env=env)
raise SystemExit(result.returncode)
