"""Üretim runtime SLI'ları (app/services/runtime_metrics.py).

Hermetik: gerçek CloudWatch yok, flush thread'i başlatılmaz (testler `flush()`'ı
doğrudan çağırır). Doğrulanan sözleşme:

  1. Varsayılan KAPALI → kayıt de flush da TAM no-op (tampon bile dolmaz).
  2. Kayıt AĞ'a çıkmaz — put_metric_data yalnızca flush()'ta çağrılır.
  3. Gecikmeler `Values`/`Counts` kova histogramı olarak gider (yüzdelik için).
  4. ≤20 datum parçalama uygulanır.
  5. CloudWatch hatası yutulur ve tampon yine boşaltılır (kesintide bellek şişmez).
  6. Boyutlar sabit kümeli kalır; ham path/kullanıcı kimliği metriklere sızmaz.

    python -m pytest tests/test_runtime_metrics.py -v
"""
import pytest

from app.services import runtime_metrics


class _FakeCloudWatch:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def put_metric_data(self, Namespace=None, MetricData=None):
        if self.fail:
            raise RuntimeError("cloudwatch down")
        self.calls.append({"Namespace": Namespace, "MetricData": MetricData})


@pytest.fixture(autouse=True)
def _clean_buffer():
    runtime_metrics.reset_for_test()
    yield
    runtime_metrics.reset_for_test()


@pytest.fixture
def enabled_cw(monkeypatch):
    """Metrikleri aç + sahte client enjekte et + flush thread'ini devre dışı bırak."""
    cw = _FakeCloudWatch()
    monkeypatch.setattr(runtime_metrics, "RUNTIME_METRICS_ENABLED", True)
    monkeypatch.setattr(runtime_metrics, "_BOTO3_AVAILABLE", True)
    monkeypatch.setattr(runtime_metrics, "_get_client", lambda: cw)
    monkeypatch.setattr(runtime_metrics, "_ensure_flusher", lambda: None)
    return cw


# ── 1. Kapalı = tam no-op ──────────────────────────────────────────────────

def test_disabled_records_nothing(monkeypatch):
    monkeypatch.setattr(runtime_metrics, "RUNTIME_METRICS_ENABLED", False)
    monkeypatch.setattr(
        runtime_metrics, "_get_client",
        lambda: pytest.fail("kapalıyken client kurulmamalı"))
    runtime_metrics.increment("X")
    runtime_metrics.record_latency("Y", 12.0)
    runtime_metrics.set_gauge("Z", 3)
    assert runtime_metrics.flush() == 0
    # Tampon dolmamalı: kapalı bir kurulum sınırsız bellek biriktirmemeli.
    assert runtime_metrics._counters == {}
    assert runtime_metrics._timings == {}
    assert runtime_metrics._gauges == {}


def test_disabled_without_boto3(monkeypatch):
    monkeypatch.setattr(runtime_metrics, "RUNTIME_METRICS_ENABLED", True)
    monkeypatch.setattr(runtime_metrics, "_BOTO3_AVAILABLE", False)
    assert runtime_metrics.is_enabled() is False
    runtime_metrics.increment("X")  # patlamamalı
    assert runtime_metrics.flush() == 0


# ── 2. Kayıt ağ'a çıkmaz ───────────────────────────────────────────────────

def test_recording_does_not_call_cloudwatch(enabled_cw):
    for _ in range(50):
        runtime_metrics.increment("HttpRequests", dimensions={"Client": "web"})
        runtime_metrics.record_latency("HttpLatency", 42.0)
    assert enabled_cw.calls == []  # tek bir round-trip bile yok
    runtime_metrics.flush()
    assert len(enabled_cw.calls) == 1


def test_counters_aggregate_before_flush(enabled_cw):
    for _ in range(7):
        runtime_metrics.increment("HttpRequests", dimensions={"Client": "web"})
    runtime_metrics.flush()
    data = enabled_cw.calls[0]["MetricData"]
    assert len(data) == 1  # 7 istek → TEK datum
    assert data[0]["MetricName"] == "HttpRequests"
    assert data[0]["Value"] == 7.0
    assert data[0]["Dimensions"] == [{"Name": "Client", "Value": "web"}]


# ── 3. Gecikme histogramı ──────────────────────────────────────────────────

def test_latency_is_bucketed_into_values_counts(enabled_cw):
    for millis in (3, 4, 900, 950, 100000):
        runtime_metrics.record_latency("HttpLatency", millis)
    runtime_metrics.flush()
    datum = enabled_cw.calls[0]["MetricData"][0]
    assert datum["Unit"] == "Milliseconds"
    # Yüzdelik hesabı `Values`/`Counts` ister; StatisticValues yüzdelik veremez.
    assert set(datum) >= {"Values", "Counts"}
    assert dict(zip(datum["Values"], datum["Counts"])) == {
        5.0: 2.0,      # 3ms + 4ms → 5ms kovası
        1000.0: 2.0,   # 900ms + 950ms → 1000ms kovası
        120000.0: 1.0,
    }


def test_latency_above_ladder_lands_in_top_bucket(enabled_cw):
    runtime_metrics.record_latency("HttpLatency", 10 ** 9)
    runtime_metrics.flush()
    datum = enabled_cw.calls[0]["MetricData"][0]
    assert datum["Values"] == [float(runtime_metrics._LATENCY_BUCKETS_MS[-1])]


def test_gauge_keeps_last_value(enabled_cw):
    runtime_metrics.set_gauge("DbPoolCheckedOut", 3)
    runtime_metrics.set_gauge("DbPoolCheckedOut", 9)
    runtime_metrics.flush()
    datum = enabled_cw.calls[0]["MetricData"][0]
    assert datum["Value"] == 9.0


# ── 4. Parçalama ───────────────────────────────────────────────────────────

def test_chunks_at_twenty_data_per_call(enabled_cw):
    for i in range(45):
        runtime_metrics.increment("C", dimensions={"Slot": str(i)})
    runtime_metrics.flush()
    assert [len(c["MetricData"]) for c in enabled_cw.calls] == [20, 20, 5]


# ── 5. Arıza yutulur, tampon yine boşalır ──────────────────────────────────

def test_cloudwatch_failure_is_swallowed_and_buffer_drained(monkeypatch):
    cw = _FakeCloudWatch(fail=True)
    monkeypatch.setattr(runtime_metrics, "RUNTIME_METRICS_ENABLED", True)
    monkeypatch.setattr(runtime_metrics, "_BOTO3_AVAILABLE", True)
    monkeypatch.setattr(runtime_metrics, "_get_client", lambda: cw)
    monkeypatch.setattr(runtime_metrics, "_ensure_flusher", lambda: None)
    runtime_metrics.increment("X")
    assert runtime_metrics.flush() == 0  # patlamaz
    # Kritik: kesinti sırasında tampon BÜYÜMEZ — bir pencere kaybedilir, bellek değil.
    assert runtime_metrics._counters == {}


def test_record_never_raises_on_internal_failure(monkeypatch):
    monkeypatch.setattr(runtime_metrics, "RUNTIME_METRICS_ENABLED", True)
    monkeypatch.setattr(runtime_metrics, "_BOTO3_AVAILABLE", True)
    monkeypatch.setattr(runtime_metrics, "_ensure_flusher", lambda: None)

    def boom(_):
        raise RuntimeError("bozuk boyut")

    monkeypatch.setattr(runtime_metrics, "_dims_key", boom)
    runtime_metrics.increment("X")           # yükseltmemeli
    runtime_metrics.record_latency("Y", 1)   # yükseltmemeli
    runtime_metrics.set_gauge("Z", 1)        # yükseltmemeli


# ── 6. Boyut güvenliği (PII / kardinalite) ─────────────────────────────────

def test_request_metric_dimensions_are_bounded(app, client, monkeypatch):
    """HTTP emitter yalnızca sabit kümeli boyutlar yazmalı — ham path ya da
    kullanıcı kimliği ASLA metrik boyutu olmamalı (docs/OBSERVABILITY.md)."""
    monkeypatch.setattr(runtime_metrics, "RUNTIME_METRICS_ENABLED", True)
    monkeypatch.setattr(runtime_metrics, "_BOTO3_AVAILABLE", True)
    monkeypatch.setattr(runtime_metrics, "_ensure_flusher", lambda: None)
    client.get("/login")
    keys = {name for _, dims in runtime_metrics._counters for name, _ in dims}
    keys |= {name for _, dims in runtime_metrics._timings for name, _ in dims}
    assert keys and keys <= {"Blueprint", "Status", "Client"}
    values = {value for _, dims in runtime_metrics._counters for _, value in dims}
    assert "/login" not in values  # ham path boyut DEĞİL


def test_client_class_is_derived_from_blueprint_not_headers(app):
    """İstemci sınıfı sunucu-tarafı olgudan gelmeli; sahte başlık onu değiştirememeli."""
    from app.observability import client_class
    with app.test_request_context("/", headers={"X-Client": "mobile",
                                                "User-Agent": "AxisAI-Mobile"}):
        assert client_class() == "web"
