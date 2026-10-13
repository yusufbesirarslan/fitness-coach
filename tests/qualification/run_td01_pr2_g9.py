"""Isolated synthetic G9 fixture; never connects to an environment database."""
import argparse
import json
import platform
import statistics
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import tests.conftest  # establishes hermetic provider/auth configuration
import os
from urllib.parse import urlparse
pg_url = os.environ.get('PG_TEST_DATABASE_URL', '')
if os.environ.get('FITX_PG_CONCURRENCY_TEST') != '1' or urlparse(pg_url).hostname not in {'127.0.0.1', 'localhost'}:
    raise SystemExit('G9 requires opt-in and a disposable local PG_TEST_DATABASE_URL')
os.environ['DATABASE_URL'] = pg_url
from app import create_app
from app.extensions import db
from app.models import User, WorkoutSession
from app.services.exercise_performance_history import queries
from tests.exercise_history_support import ANCHOR, utc_noon, checkpoint_json
from sqlalchemy import event, text

parser = argparse.ArgumentParser()
parser.add_argument('mode', choices=['seed', 'measure'])
parser.add_argument('rows', type=int, choices=[1000, 4000])
parser.add_argument('--output-dir', type=Path, required=True)
args = parser.parse_args()
app = create_app()
OUT = args.output_dir
OUT.mkdir(parents=True, exist_ok=True)
with app.app_context():
    if args.mode == 'seed':
        db.drop_all()
        db.create_all()
        owners = [User(username=f'g9-{i}', email=f'g9-{i}@example.invalid', cognito_sub=f'g9-{i}') for i in range(21)]
        db.session.add_all(owners)
        db.session.commit()
        ids = [o.id for o in owners]
        exercise_ids = ['ex_barbell_back_squat', 'ex_barbell_bench_press', 'ex_barbell_row', 'ex_push_up', 'ex_barbell_deadlift', 'ex_outdoor_run']
        batch = []
        sizes = []
        for i in range(args.rows):
            on = ANCHOR - timedelta(days=i)
            checkpoint = checkpoint_json([(ex, [(s, True, 5 + ((i+s+j) % 16), float(20 + ((i+j+s) % 200))) for s in range(5)]) for j, ex in enumerate(exercise_ids)])
            sizes.append(len(checkpoint.encode()))
            # Other owners are spread uniformly through the target's lifetime rows.
            owner_ids = ids if i % (args.rows // 1000) == 0 else ids[:1]
            for owner in owner_ids:
                stamp = utc_noon(on)
                batch.append(dict(public_id=f'g9-{owner}-{i}', user_id=owner, status='completed', workout_date=on.isoformat(), source='unscheduled', started_at=stamp-timedelta(hours=1), last_activity_at=stamp, completed_at=stamp, checkpoint_revision=3, checkpoint_data=checkpoint))
            if len(batch) >= 1000:
                db.session.execute(WorkoutSession.__table__.insert(), batch)
                batch = []
        if batch:
            db.session.execute(WorkoutSession.__table__.insert(), batch)
        for owner in ids:
            for status in ('active', 'abandoned'):
                db.session.add(WorkoutSession(public_id=f'g9-{owner}-{status}', user_id=owner, status=status, workout_date=ANCHOR.isoformat(), source='unscheduled'))
        db.session.commit()
        db.session.execute(text('ANALYZE'))
        db.session.commit()
        (OUT / f'g9-{args.rows}-fixture.json').write_text(json.dumps(dict(target_rows=args.rows, other_owners=20, rows_per_other_owner=1000, active=21, abandoned=21, checkpoint_bytes=[min(sizes),max(sizes)], analyze=True, anchor=ANCHOR.isoformat()), indent=2))
    else:
        # Intercept the exact service statement without running it before the cold pass.
        captured = []
        class Captured(Exception):
            pass
        def intercept(conn, cursor, statement, parameters, context, many):
            captured.append((statement, parameters))
            raise Captured()
        event.listen(db.engine, 'before_cursor_execute', intercept)
        try:
            queries.load_candidates(1, ANCHOR)
        except Captured:
            pass
        finally:
            event.remove(db.engine, 'before_cursor_execute', intercept)
        db.session.rollback()
        statement, parameters = captured[0]
        runs = []
        with db.engine.connect() as conn:
            version = conn.execute(text('SELECT version()')).scalar()
            for i in range(6):
                plan = conn.exec_driver_sql('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + statement, parameters).scalar()[0]
                runs.append(dict(cache='first after PostgreSQL restart; OS cache not cleared' if i == 0 else 'warm', **plan))
        warm_ms = statistics.median(run['Execution Time'] for run in runs[1:])
        warm_buffers = statistics.median(run['Plan'].get('Shared Hit Blocks',0)+run['Plan'].get('Shared Read Blocks',0) for run in runs[1:])
        result = dict(version=version, host=platform.platform(), machine=platform.machine(), python=platform.python_version(), statement=statement, parameters=parameters, fixture=json.loads((OUT / f'g9-{args.rows}-fixture.json').read_text()), warm_median_ms=warm_ms, warm_buffers=warm_buffers, runs=runs)
        (OUT / f'g9-{args.rows}.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(dict(rows=args.rows, warm_ms=warm_ms, buffers=warm_buffers, plan_nodes=runs[-1]['Plan'])))
