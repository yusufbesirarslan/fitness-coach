"""Durable production app-log shipping (Phase 2 P2-A).

`/axisai/app` used to tail ONE hard-coded container log path and went silent
after the first redeploy. The contract pinned here:

  * web/worker json logs carry their compose service name; redis does not;
  * the agent entry follows a glob by SERVICE IDENTITY (never a container id),
    with publish_multi_logs so no container starves another;
  * the include filter admits web/worker lines and rejects redis and
    unlabelled lines (checked against real json-file line shapes);
  * Phase 1 metric pinning is preserved in the same file;
  * the app no longer prints each request line twice under gunicorn.

    python -m pytest tests/test_app_log_shipping.py -v
"""
import json
import logging
import re
from pathlib import Path

import pytest
import yaml

AGENT_CONFIG = Path("deploy/cloudwatch-agent/file_config.json")
SERVICE_LABEL = "com.docker.compose.service"


@pytest.fixture(scope="module")
def services():
    return yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))["services"]


@pytest.fixture(scope="module")
def agent():
    return json.loads(AGENT_CONFIG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def app_entry(agent):
    entries = [e for e in agent["logs"]["logs_collected"]["files"]["collect_list"]
               if e["log_group_name"] == "/axisai/app"]
    assert len(entries) == 1
    return entries[0]


def _json_file_line(message, service=None):
    """A line exactly as Docker's json-file driver writes it."""
    line = {"log": message + "\n", "stream": "stderr"}
    if service is not None:
        line["attrs"] = {SERVICE_LABEL: service}
    line["time"] = "2026-09-23T07:20:00.519796033Z"
    return json.dumps(line, separators=(",", ":"))


def _admitted(entry, line):
    for rule in entry.get("filters", []):
        matched = re.search(rule["expression"], line) is not None
        if rule["type"] == "include" and not matched:
            return False
        if rule["type"] == "exclude" and matched:
            return False
    return True


@pytest.mark.parametrize("service", ["web", "worker"])
def test_app_services_label_their_json_logs(services, service):
    options = services[service]["logging"]["options"]
    assert options.get("labels") == SERVICE_LABEL


def test_redis_is_not_labelled_so_it_can_never_be_shipped(services):
    assert "labels" not in services["redis"]["logging"]["options"]


def test_app_entry_follows_service_identity_not_a_container_id(app_entry):
    path = app_entry["file_path"]
    assert path == "/var/lib/docker/containers/*/*-json.log"
    assert not re.search(r"[0-9a-f]{12,}", path), "container id pinned again"


def test_app_entry_publishes_every_matching_file(app_entry):
    # Without this the agent follows only the newest-modified match, and
    # redis (writing every minute) would starve web and worker.
    assert app_entry.get("publish_multi_logs") is True


def test_app_entry_never_deletes_docker_logs(app_entry):
    assert app_entry.get("auto_removal") in (None, False)


@pytest.mark.parametrize("service", ["web", "worker"])
def test_filter_admits_app_service_lines(app_entry, service):
    line = _json_file_line("[INFO] request id=abc method=GET path=/ status=200", service)
    assert _admitted(app_entry, line)


@pytest.mark.parametrize("line", [
    _json_file_line("1:M 23 Sep 2026 06:00:23.398 * Background saving started", "redis"),
    _json_file_line("1:M 23 Sep 2026 06:00:23.398 * DB saved on disk"),
    _json_file_line("unlabelled container output"),
    _json_file_line('spoof "com.docker.compose.service":"web" inside the message', "redis"),
])
def test_filter_rejects_everything_else(app_entry, line):
    assert not _admitted(app_entry, line)


def test_app_group_retention_is_bounded(app_entry):
    assert app_entry["retention_in_days"] == 30


def test_phase1_metric_pinning_is_preserved(agent):
    metrics = agent["metrics"]
    assert metrics["aggregation_dimensions"] == [["InstanceId"]]
    collected = metrics["metrics_collected"]
    assert collected["diskio"]["resources"] == ["nvme0n1"]
    assert collected["net"]["resources"] == ["ens5"]
    assert collected["disk"]["resources"] == ["/"]


def test_request_line_is_not_duplicated_under_gunicorn():
    from app import create_app

    gunicorn_logger = logging.getLogger("gunicorn.error")
    root = logging.getLogger()
    records = {"gunicorn": [], "root": []}

    class _Capture(logging.Handler):
        def __init__(self, bucket):
            super().__init__()
            self.bucket = bucket

        def emit(self, record):
            records[self.bucket].append(record.getMessage())

    gunicorn_handler = _Capture("gunicorn")
    root_handler = _Capture("root")
    gunicorn_logger.addHandler(gunicorn_handler)
    root.addHandler(root_handler)
    try:
        app = create_app()
        app.logger.info("request id=probe")
    finally:
        gunicorn_logger.removeHandler(gunicorn_handler)
        root.removeHandler(root_handler)

    assert records["gunicorn"].count("request id=probe") == 1
    assert "request id=probe" not in records["root"]
