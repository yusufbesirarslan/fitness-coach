"""Immutable in-image bounded pass; no standalone scheduling or migrations."""
import json
import os
from pathlib import Path
import re
import sys


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    check = len(args) == 3 and args[2] == '--check'
    if (len(args) not in (2, 3) or (len(args) == 3 and not check) or not re.fullmatch(r'[0-9a-f]{40}', args[0])
            or not args[1].isdigit() or not 25 <= int(args[1]) <= 200):
        return 1
    if (Path('/app/BUILD_REVISION').read_text().strip() != args[0]
            or os.environ.get('APP_REVISION') != args[0]
            or os.environ.get('FITX_STARTUP_MODE') != 'read-only'
            or os.environ.get('FITX_SKIP_DB_INIT') != '0'):
        return 1
    sys.path.insert(0, '/app')
    from starter import app
    from app.services import apple_reconciliation
    with app.app_context():
        if not (app.config.get('APPLE_NOTIFICATIONS_ENABLED') is True
                or app.config.get('APPLE_SUBSCRIPTIONS_ENABLED') is True):
            if check:
                print('APPLE_RELEASE_PASS=READY')
                return 0
            return 1
        adapter = apple_reconciliation._adapter()
        health = apple_reconciliation.health(adapter)
        if any(int(args[1]) < max(25, (state['relevant_rows'] + 9) // 10)
               for state in health.values()):
            return 1
        if check:
            print('APPLE_RELEASE_PASS=READY')
            return 0
        counts = apple_reconciliation.run(limit=int(args[1]), sweep_limit=2 * int(args[1]))
        # An unavailable provider/storage or terminal failure must be visible to
        # systemd. Durable lease/backoff/quarantine recovery remains unchanged.
        if any(key not in {'enqueued', 'purged', 'processed', 'APPLIED', 'SUPERSEDED'}
               and value for key, value in counts.items()):
            return 1
        print('APPLE_RELEASE_HEALTH=' + json.dumps(apple_reconciliation.health(adapter), sort_keys=True))
    print('APPLE_RELEASE_PASS=OK')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('APPLE_RELEASE_PASS=FAILED')
        raise SystemExit(1)
