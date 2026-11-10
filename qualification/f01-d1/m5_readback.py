"""Later authorized read-only RQ observation; never enqueues or executes a job.

Use a dedicated Redis read-only credential through F01_QUAL_REDIS_URL. This
script does not prove the serving revision or zero traffic; attach those
operator receipts separately. No arguments, results or user payloads are output.
"""
import argparse
from datetime import datetime, timezone
import json
import os
import time

from redis import Redis
from rq import Queue
from rq.registry import FinishedJobRegistry

TARGET = "app.jobs.tasks.retry_health_erasures"
PREFIX = "f01-erasure-retry-"
MARKER = "axisai.f01.erasure-retry.v1"
FIELDS = ("description", "origin", "status", "enqueued_at", "started_at", "ended_at")


def observe(connection, queue_name):
    queue = Queue(queue_name, connection=connection)
    registry = FinishedJobRegistry(queue=queue)
    ids = sorted(set(queue.get_job_ids()) | set(registry.get_job_ids(cleanup=False)))
    rows = []
    for job_id in ids:
        job_id = job_id.decode() if isinstance(job_id, bytes) else job_id
        if not job_id.startswith(PREFIX):
            continue  # never fetch even metadata of unrelated jobs
        values = connection.hmget("rq:job:" + job_id, FIELDS)
        value = {key: (raw.decode() if isinstance(raw, bytes) else raw)
                 for key, raw in zip(FIELDS, values)}
        if value["description"] != MARKER or value["origin"] != queue_name:
            continue
        rows.append(dict(id=job_id, function=TARGET, status=value["status"],
            enqueued_at=value["enqueued_at"], started_at=value["started_at"], ended_at=value["ended_at"]))
    return rows


def qualifying_rows(rows, start, end):
    result = []
    for row in rows:
        if row["status"] != "finished":
            continue
        try:
            times = [datetime.fromisoformat(row[key])
                     for key in ("enqueued_at", "started_at", "ended_at")]
            if (all(moment.tzinfo is not None for moment in times)
                    and start <= times[0] <= times[1] <= times[2] <= end):
                result.append(row)
        except (ValueError, TypeError):
            continue
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", default="fitx:ai")
    args = parser.parse_args()
    connection = Redis.from_url(os.environ["F01_QUAL_REDIS_URL"],
                               socket_connect_timeout=3, socket_timeout=5)
    start = datetime.now(timezone.utc)
    deadline = time.monotonic() + 3600
    seen = {}
    while True:
        for row in observe(connection, args.queue):
            seen[row["id"]] = row
        if time.monotonic() >= deadline:
            break
        time.sleep(min(60, max(0, deadline - time.monotonic())))
    end = datetime.now(timezone.utc)
    qualifying = qualifying_rows(seen.values(), start, end)
    print(json.dumps(dict(started_at=start.isoformat(), ended_at=end.isoformat(),
        queue=args.queue, enqueued_and_finished_in_window=qualifying,
        registry_check="PASS" if qualifying else "FAIL",
        serving_revision="REQUIRES_SEPARATE_RECEIPT", zero_health_traffic="REQUIRES_SEPARATE_RECEIPT"), indent=2))
    return 0 if qualifying else 1


if __name__ == "__main__":
    try:
        outcome = main()
    except Exception as error:
        print(json.dumps(dict(registry_check="ERROR", error_type=type(error).__name__)))
        outcome = 2
    raise SystemExit(outcome)
