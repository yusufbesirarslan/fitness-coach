"""Sprint 4 WS6 — AI CloudWatch metrikleri (app/services/ai_metrics.py).

Hermetik: gerçek CloudWatch yok. Varsayılan KAPALI (AI_METRICS_ENABLED=0) → tam
no-op; açıkken sahte bir boto3 client'a put_metric_data çağrıldığı, ≤20'lik
parçalara bölündüğü ve tüm hataların yutulduğu doğrulanır.

    python -m pytest tests/test_ai_metrics.py -v
"""
import pytest

from app.services import ai_metrics


class _FakeCloudWatch:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def put_metric_data(self, Namespace=None, MetricData=None):
        if self.fail:
            raise RuntimeError("cloudwatch down")
        self.calls.append({"Namespace": Namespace, "MetricData": MetricData})


@pytest.fixture
def enabled_cw(monkeypatch):
    """Metrikleri aç + sahte CloudWatch client enjekte et."""
    cw = _FakeCloudWatch()
    monkeypatch.setattr(ai_metrics, "AI_METRICS_ENABLED", True)
    monkeypatch.setattr(ai_metrics, "_BOTO3_AVAILABLE", True)
    monkeypatch.setattr(ai_metrics, "_get_client", lambda: cw)
    return cw


# ── Kapalı = no-op ──────────────────────────────────────────────────────────

def test_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(ai_metrics, "AI_METRICS_ENABLED", False)
    called = {"n": 0}
    monkeypatch.setattr(ai_metrics, "_get_client",
                        lambda: called.__setitem__("n", called["n"] + 1))
    ai_metrics.put_metric("X", 1)
    ai_metrics.increment("Y")
    ai_metrics.record_tokens(10, 20)
    assert called["n"] == 0  # client hiç kurulmadı


def test_no_boto3_is_noop(monkeypatch):
    monkeypatch.setattr(ai_metrics, "AI_METRICS_ENABLED", True)
    monkeypatch.setattr(ai_metrics, "_BOTO3_AVAILABLE", False)
    assert ai_metrics.is_enabled() is False
    ai_metrics.put_metric("X", 1)  # patlamamalı


# ── Açık = yazar ────────────────────────────────────────────────────────────

def test_put_metric_sends_one_datum(enabled_cw):
    from app.config import AI_METRICS_NAMESPACE
    ai_metrics.put_metric("AILatency", 123.4, unit="Milliseconds",
                          dimensions={"provider": "bedrock"})
    assert len(enabled_cw.calls) == 1
    call = enabled_cw.calls[0]
    assert call["Namespace"] == AI_METRICS_NAMESPACE
    datum = call["MetricData"][0]
    assert datum["MetricName"] == "AILatency"
    assert datum["Value"] == 123.4
    assert datum["Unit"] == "Milliseconds"
    assert datum["Dimensions"] == [{"Name": "provider", "Value": "bedrock"}]


def test_increment_is_count(enabled_cw):
    ai_metrics.increment("AIErrors", dimensions={"mode": "stream"})
    datum = enabled_cw.calls[0]["MetricData"][0]
    assert datum["MetricName"] == "AIErrors"
    assert datum["Value"] == 1.0
    assert datum["Unit"] == "Count"


def test_record_tokens_emits_three_metrics(enabled_cw):
    ai_metrics.record_tokens(100, 40, dimensions={"mode": "stream"})
    names = {d["MetricName"]: d["Value"]
             for c in enabled_cw.calls for d in c["MetricData"]}
    assert names == {"PromptTokens": 100.0, "CompletionTokens": 40.0,
                     "TotalTokens": 140.0}


def test_batches_chunked_to_20(enabled_cw):
    metrics = [{"name": f"M{i}", "value": i} for i in range(45)]
    ai_metrics.put_metrics(metrics)
    # 45 datum → 20 + 20 + 5 = 3 put_metric_data çağrısı.
    assert [len(c["MetricData"]) for c in enabled_cw.calls] == [20, 20, 5]


def test_swallows_cloudwatch_error(monkeypatch):
    cw = _FakeCloudWatch(fail=True)
    monkeypatch.setattr(ai_metrics, "AI_METRICS_ENABLED", True)
    monkeypatch.setattr(ai_metrics, "_BOTO3_AVAILABLE", True)
    monkeypatch.setattr(ai_metrics, "_get_client", lambda: cw)
    ai_metrics.put_metric("X", 1)  # patlamamalı — hata yutulur


def test_empty_metrics_noop(enabled_cw):
    ai_metrics.put_metrics([])
    assert enabled_cw.calls == []
