"""Production runtime SLI'ları (CloudWatch) — HTTP, kapasite, DB/Redis.

`ai_metrics.py` AI turlarını olay-başına yollar; bu modül İSTEK YOLUNDAKİ
sinyalleri toplar. Fark önemlidir: `put_metric_data` bir AĞ çağrısıdır ve her
istekte satır-içi çağrılırsa tek worker/8 thread'lik sunum kapasitesine istek
başına bir round-trip eklerdi (tam olarak ölçmeye çalıştığımız şeyi bozardı).

Bu yüzden burada kayıt SÜREÇ-İÇİ TAMPONA yazılır (kilit altında bir dict
güncellemesi, mikrosaniyeler) ve ayrı bir daemon thread tamponu
RUNTIME_METRICS_FLUSH_SECONDS'ta bir boşaltır. Gecikmeler kova(bucket)
histogramı olarak tutulup CloudWatch'a `Values`/`Counts` ile gider — böylece
p50/p95/p99 sunucuda hesaplanabilir (StatisticSets yüzdelik veremez).

Varsayılan KAPALI (`RUNTIME_METRICS_ENABLED=0`, ai_metrics ile aynı gerekçe:
EC2 instance role'üne `cloudwatch:PutMetricData` izni gerekir). Kapalıyken,
boto3 yokken ya da HERHANGİ bir hatada tam no-op — metrik yolu asıl isteği
ASLA bloklamaz, yavaşlatmaz ya da düşürmez (s3_helper/ai_metrics boto3-guard
deseni).

Etiket (dimension) kardinalitesi BİLEREK sınırlıdır: yalnızca sabit kümeli
değerler (blueprint adı, status sınıfı, istemci sınıfı, sağlayıcı, sonuç).
Kullanıcı kimliği, ham path, token, e-posta ya da başka PII metrik boyutu
OLARAK KULLANILMAZ (docs/OBSERVABILITY.md "Yasak alanlar").
"""
import logging
import threading
import time

from app.config import (RUNTIME_METRICS_ENABLED, RUNTIME_METRICS_FLUSH_SECONDS,
                        RUNTIME_METRICS_NAMESPACE, BEDROCK_REGION)

_log = logging.getLogger(__name__)

try:
    import boto3
    _BOTO3_AVAILABLE = True
except Exception:  # pragma: no cover - boto3 opsiyonel (lazy-guard)
    boto3 = None
    _BOTO3_AVAILABLE = False

# CloudWatch sınırları: çağrı başına ≤20 datum, datum başına ≤150 farklı değer.
_MAX_DATA_PER_CALL = 20
_MAX_VALUES_PER_DATUM = 150

# Gecikme kovaları (ms). Kova sayısı SABİT → `Values` dizisi 150 sınırının
# çok altında kalır ve p50/p95/p99 anlamlı çözünürlükte çıkar.
_LATENCY_BUCKETS_MS = (
    5, 10, 25, 50, 100, 250, 500, 1000, 2000, 3000, 5000,
    10000, 20000, 30000, 60000, 120000, 300000,
)

_client = None
_lock = threading.Lock()
_counters = {}   # (name, dims_key) -> float
_timings = {}    # (name, dims_key) -> {bucket_ms: count}
_gauges = {}     # (name, dims_key) -> float (son yazan kazanır)
_flusher = None
_samplers = []   # her flush'tan ÖNCE çağrılan seviye-örnekleyicileri
_shutdown_hooked = False


def is_enabled():
    """Metrik yayını etkin mi? Bayrak açık VE boto3 mevcut."""
    return RUNTIME_METRICS_ENABLED and _BOTO3_AVAILABLE


def _get_client():
    """CloudWatch client'ını tembel oluştur (EC2 IAM Instance Profile).
    Açık connect/read timeout: flush thread'i asla süresiz asılı kalmasın."""
    global _client
    if _client is None:
        from botocore.config import Config as _BotoConfig
        _client = boto3.client(
            "cloudwatch", region_name=BEDROCK_REGION,
            config=_BotoConfig(connect_timeout=2, read_timeout=3,
                               retries={"max_attempts": 1}),
        )
    return _client


def _dims_key(dimensions):
    """dict → deterministik, hashlenebilir boyut anahtarı."""
    if not dimensions:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in dimensions.items()))


def _bucket(millis):
    """Gecikmeyi sabit merdivendeki en küçük üst kovaya yuvarla."""
    for edge in _LATENCY_BUCKETS_MS:
        if millis <= edge:
            return edge
    return _LATENCY_BUCKETS_MS[-1]


def _ensure_flusher():
    """Süreçte TEK flush thread'i başlat (idempotent, daemon)."""
    global _flusher
    if _flusher is not None and _flusher.is_alive():
        return
    with _lock:
        if _flusher is not None and _flusher.is_alive():
            return
        thread = threading.Thread(
            target=_flush_loop, name="fitx-runtime-metrics", daemon=True)
        thread.start()
        _flusher = thread


def register_sampler(fn):
    """Her flush'tan ÖNCE çağrılacak bir seviye-örnekleyici kaydet (idempotent).

    Sayaç/gecikme metrikleri istek yolundan yazılır; SEVİYE metrikleri (thread
    rezervi, havuz kullanımı) ise yazacak bir "olay" olmadığı için bir
    örnekleyiciye ihtiyaç duyar. Bunları `/health?deep=1` yolundan yazmak
    yetmiyordu: konteyner probe'u SIĞ /health'i çağırır, deep yalnızca deploy
    sırasında birkaç kez vurulur → gauge'lar veri üretmez ve alarm sürekli
    INSUFFICIENT_DATA'da kalır. Zaten periyodik olan flush thread'i doğru sahip.

    Örnekleyici AĞ'a çıkmamalıdır (flush thread'i bir sonraki pencereyi
    geciktirmemeli); yalnızca süreç-içi seviye okur. Hatası YUTULUR.
    """
    with _lock:
        if fn not in _samplers:
            _samplers.append(fn)


def _run_samplers():
    if not is_enabled():
        return  # kapalıyken seviye örneklemeye gerek yok (tam no-op yolu)
    for sampler in list(_samplers):
        try:
            sampler()
        except Exception as e:
            _log.warning("[RUNTIME METRICS] sampler basarisiz: %s: %s",
                         type(e).__name__, e)


def _flush_loop():  # pragma: no cover - zamanlayıcı döngüsü
    while True:
        time.sleep(max(5, RUNTIME_METRICS_FLUSH_SECONDS))
        try:
            _run_samplers()
            flush()
        except Exception as e:
            _log.warning("[RUNTIME METRICS] flush basarisiz: %s: %s",
                         type(e).__name__, e)


def flush_on_shutdown():
    """Süreç kapanırken tamponu SON bir kez boşalt.

    `_flush_loop` bir daemon thread'dir: kapanışta pencere ortasında öldürülür ve
    o pencere (varsayılan 60 sn) kaybolur. Kaybedilen pencere tam olarak
    yeniden başlatmadan ÖNCEKİ penceredir — yani deploy'u/rollback'i tetikleyen
    hata ve doygunluk artışının kendisi. İstemcinin zaten sıkı zaman aşımları
    var (connect 2 sn / read 3 sn / tek deneme), bu yüzden kapanış sınırlıdır.
    """
    if not is_enabled():
        return  # kapalıyken örnekleyicileri de çalıştırmaya gerek yok
    try:
        _run_samplers()
        flush()
    except Exception:
        pass


def install_shutdown_flush():
    """`atexit` üzerinden son flush'ı bir kez kur (idempotent)."""
    global _shutdown_hooked
    with _lock:
        if _shutdown_hooked:
            return
        _shutdown_hooked = True
    import atexit
    atexit.register(flush_on_shutdown)


# ── Kayıt yüzeyi (istek yolundan çağrılır; ASLA ağ'a çıkmaz) ───────────────

def increment(name, dimensions=None, value=1):
    """Bir sayacı artır. Kapalıysa no-op; hata ASLA yükselmez."""
    if not is_enabled():
        return
    try:
        key = (name, _dims_key(dimensions))
        with _lock:
            _counters[key] = _counters.get(key, 0.0) + float(value)
        _ensure_flusher()
    except Exception:
        pass


def record_latency(name, millis, dimensions=None):
    """Gecikmeyi kova histogramına ekle. Kapalıysa no-op."""
    if not is_enabled():
        return
    try:
        key = (name, _dims_key(dimensions))
        bucket = _bucket(float(millis))
        with _lock:
            hist = _timings.setdefault(key, {})
            hist[bucket] = hist.get(bucket, 0) + 1
        _ensure_flusher()
    except Exception:
        pass


def set_gauge(name, value, dimensions=None):
    """Anlık bir seviye kaydet (havuz kullanımı, aktif istek vb.)."""
    if not is_enabled():
        return
    try:
        key = (name, _dims_key(dimensions))
        with _lock:
            _gauges[key] = float(value)
        _ensure_flusher()
    except Exception:
        pass


# ── Boşaltma ───────────────────────────────────────────────────────────────

def _drain():
    """Tamponu atomik olarak al ve sıfırla (flush ağ'a çıkarken kayıt sürsün)."""
    with _lock:
        counters, timings, gauges = _counters.copy(), _timings.copy(), _gauges.copy()
        _counters.clear()
        _timings.clear()
        _gauges.clear()
    return counters, timings, gauges


def _datum(name, dims_key, **payload):
    return {
        "MetricName": name,
        "Dimensions": [{"Name": k, "Value": v} for k, v in dims_key],
        **payload,
    }


def build_metric_data(counters, timings, gauges):
    """Tamponları CloudWatch MetricData listesine çevir (saf; testlenebilir)."""
    data = []
    for (name, dims_key), total in counters.items():
        data.append(_datum(name, dims_key, Value=total, Unit="Count"))
    for (name, dims_key), value in gauges.items():
        data.append(_datum(name, dims_key, Value=value, Unit="None"))
    for (name, dims_key), hist in timings.items():
        # En yoğun kovaları koru: 150 sınırı bu merdivende zaten aşılamaz ama
        # sınır kodda AÇIK dursun ki merdiven büyürse sessizce bozulmasın.
        items = sorted(hist.items())[:_MAX_VALUES_PER_DATUM]
        if not items:
            continue
        data.append(_datum(
            name, dims_key,
            Values=[float(b) for b, _ in items],
            Counts=[float(c) for _, c in items],
            Unit="Milliseconds",
        ))
    return data


def flush():
    """Tamponu CloudWatch'a yolla. Kapalı / boto3 yok / hata → no-op."""
    if not is_enabled():
        return 0
    counters, timings, gauges = _drain()
    data = build_metric_data(counters, timings, gauges)
    if not data:
        return 0
    try:
        client = _get_client()
        for i in range(0, len(data), _MAX_DATA_PER_CALL):
            client.put_metric_data(Namespace=RUNTIME_METRICS_NAMESPACE,
                                   MetricData=data[i:i + _MAX_DATA_PER_CALL])
    except Exception as e:
        # Tampon zaten boşaltıldı: bir kesinti sonsuz büyüyen bir kuyruk
        # bırakmaz (bellek sınırlı kalır), yalnızca o pencere kaybolur.
        _log.warning("[RUNTIME METRICS] put_metric_data basarisiz: %s: %s",
                     type(e).__name__, e)
        return 0
    return len(data)


def reset_for_test():
    """Testler arası tampon temizliği (üretim yolunda çağrılmaz)."""
    with _lock:
        _counters.clear()
        _timings.clear()
        _gauges.clear()
        _samplers.clear()
