"""Sprint 4 WS6 — AI gözlemlenebilirlik (CloudWatch metrikleri).

CloudWatch `FitX/AI` namespace'ine gecikme / token / önbellek-isabet / retry /
hata sayaçları yollar. **Varsayılan KAPALI** (`AI_METRICS_ENABLED=0`): EC2 instance
role'üne `cloudwatch:PutMetricData` izni verildikten SONRA açılır. Kapalıyken,
boto3 yokken ya da herhangi bir hatada TAM no-op — metrik yazımı hiçbir zaman
asıl AI akışını bloklamaz ya da düşürmez (s3_helper boto3-guard deseni).

Kimlik bilgisi EC2 IAM Instance Profile'dan gelir; kodda/.env'de anahtar YOK.
put_metric_data çağrı başına ≤20 datum kabul eder → gönderimden önce parçalanır.
"""
import logging

from app.config import AI_METRICS_ENABLED, AI_METRICS_NAMESPACE, BEDROCK_REGION

_log = logging.getLogger(__name__)

try:
    import boto3
    _BOTO3_AVAILABLE = True
except Exception:  # pragma: no cover - boto3 opsiyonel (lazy-guard)
    boto3 = None
    _BOTO3_AVAILABLE = False

_client = None


def is_enabled():
    """Metrik yayını etkin mi? Bayrak açık VE boto3 mevcut."""
    return AI_METRICS_ENABLED and _BOTO3_AVAILABLE


def _get_client():
    """CloudWatch client'ını tembel oluştur + önbelleğe al (IAM Instance Profile).
    Açık connect/read timeout: metrik yazımı istek thread'ini asla uzun bloklamasın."""
    global _client
    if _client is None:
        from botocore.config import Config as _BotoConfig
        _client = boto3.client(
            "cloudwatch", region_name=BEDROCK_REGION,
            config=_BotoConfig(connect_timeout=2, read_timeout=3,
                               retries={"max_attempts": 1}),
        )
    return _client


def _dimensions(dimensions):
    """dict → CloudWatch Dimensions listesi ([{Name, Value}])."""
    return [{"Name": str(k), "Value": str(v)} for k, v in (dimensions or {}).items()]


def put_metric(name, value, unit="None", dimensions=None):
    """Tek bir metrik datum'u yolla. Kapalı / boto3 yok / hata → sessiz no-op."""
    put_metrics([{"name": name, "value": value, "unit": unit,
                  "dimensions": dimensions}])


def put_metrics(metrics):
    """Birden çok datum'u ≤20'lik parçalar hâlinde yolla. Kapalı / hata → no-op.

    metrics: [{"name", "value", "unit"?, "dimensions"?}] listesi."""
    if not is_enabled() or not metrics:
        return
    try:
        client = _get_client()
        data = [{
            "MetricName": m["name"],
            "Value": float(m.get("value", 0)),
            "Unit": m.get("unit", "None"),
            "Dimensions": _dimensions(m.get("dimensions")),
        } for m in metrics]
        for i in range(0, len(data), 20):  # put_metric_data: ≤20 datum/çağrı
            client.put_metric_data(Namespace=AI_METRICS_NAMESPACE,
                                   MetricData=data[i:i + 20])
    except Exception as e:
        _log.warning("[AI METRICS] put_metric_data basarisiz: %s: %s",
                     type(e).__name__, e)


# ── Kısayollar (çağrı noktalarında niyet açık olsun) ───────────────────────

def increment(name, dimensions=None):
    """Bir sayacı 1 artır (Count birimi)."""
    put_metric(name, 1, unit="Count", dimensions=dimensions)


def record_latency(name, millis, dimensions=None):
    """Milisaniye cinsinden gecikme kaydet."""
    put_metric(name, millis, unit="Milliseconds", dimensions=dimensions)


def record_tokens(prompt_tokens=0, completion_tokens=0, dimensions=None):
    """Prompt/completion/total token sayaçlarını tek çağrıda yolla."""
    p = int(prompt_tokens or 0)
    c = int(completion_tokens or 0)
    put_metrics([
        {"name": "PromptTokens", "value": p, "unit": "Count", "dimensions": dimensions},
        {"name": "CompletionTokens", "value": c, "unit": "Count", "dimensions": dimensions},
        {"name": "TotalTokens", "value": p + c, "unit": "Count", "dimensions": dimensions},
    ])
