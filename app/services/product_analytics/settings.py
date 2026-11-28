"""Vendor and retention configuration, validated strictly and fail-closed.

Nothing here has a production default. Missing or malformed vendor values
mean "no vendor transport" (dispatch and vendor erasure do not run; rows stay
in AxisAI's database under the hard outbox ceiling). Missing retention values
mean the corresponding optional purge does not run. Retention values are
APPROVAL-PENDING proposals (docs/product-analytics-v2/RETENTION.md); this
module only enforces their technical bounds.

Destinations are code constants (``posthog_transport``). Configuration only
selects the region, and ``eu`` is the only accepted value.
"""
import re
from dataclasses import dataclass
from datetime import timedelta

REGION = "PRODUCT_ANALYTICS_POSTHOG_REGION"
PROJECT_TOKEN = "PRODUCT_ANALYTICS_POSTHOG_PROJECT_TOKEN"
PROJECT_ID = "PRODUCT_ANALYTICS_POSTHOG_PROJECT_ID"
PERSONAL_API_KEY = "PRODUCT_ANALYTICS_POSTHOG_PERSONAL_API_KEY"
CONNECT_TIMEOUT = "PRODUCT_ANALYTICS_HTTP_CONNECT_TIMEOUT_SECONDS"
READ_TIMEOUT = "PRODUCT_ANALYTICS_HTTP_READ_TIMEOUT_SECONDS"
OUTBOX_MAX_AGE = "PRODUCT_ANALYTICS_OUTBOX_MAX_AGE_HOURS"
RECEIPT_RETENTION = "PRODUCT_ANALYTICS_ERASURE_RECEIPT_RETENTION_DAYS"
ERASURE_OVERDUE = "PRODUCT_ANALYTICS_ERASURE_OVERDUE_DAYS"

#: Absolute ceiling for an undispatched outbox row, independent of config.
OUTBOX_HARD_CEILING = timedelta(hours=48)
DEFAULT_CONNECT_TIMEOUT = 3.0
DEFAULT_READ_TIMEOUT = 10.0
_TOKEN = re.compile(r"phc_[A-Za-z0-9]{20,80}")
_PERSONAL = re.compile(r"phx_[A-Za-z0-9]{20,100}")
_PROJECT = re.compile(r"[1-9][0-9]{0,11}")


@dataclass(frozen=True)
class VendorSettings:
    project_token: str
    project_id: str | None
    personal_api_key: str | None
    connect_timeout: float
    read_timeout: float

    @property
    def can_erase(self):
        return self.project_id is not None and self.personal_api_key is not None

    def __repr__(self):  # never render secrets
        return (f"VendorSettings(region=eu, erasure={'yes' if self.can_erase else 'no'}, "
                f"timeouts=({self.connect_timeout},{self.read_timeout}))")


@dataclass(frozen=True)
class RetentionPolicy:
    outbox_max_age: timedelta
    receipt_retention: timedelta | None
    erasure_overdue: timedelta | None


def _text(config, key):
    value = config.get(key)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _bounded(config, key, low, high, default=None, cast=float):
    raw = config.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    try:
        value = cast(raw)
    except (TypeError, ValueError):
        return None
    if isinstance(value, bool) or not (low <= value <= high):
        return None
    return value


def vendor_settings(config):
    """Complete, valid capture settings or None (fail closed)."""
    if _text(config, REGION) != "eu":
        return None
    token = _text(config, PROJECT_TOKEN)
    if token is None or not _TOKEN.fullmatch(token):
        return None
    connect = _bounded(config, CONNECT_TIMEOUT, 0.5, 5.0, DEFAULT_CONNECT_TIMEOUT)
    read = _bounded(config, READ_TIMEOUT, 1.0, 15.0, DEFAULT_READ_TIMEOUT)
    if connect is None or read is None:
        return None
    project = _text(config, PROJECT_ID)
    personal = _text(config, PERSONAL_API_KEY)
    if not (project and _PROJECT.fullmatch(project) and personal and _PERSONAL.fullmatch(personal)):
        project = personal = None   # capture may run; vendor erasure may not
    return VendorSettings(token, project, personal, connect, read)


def retention_policy(config):
    hours = _bounded(config, OUTBOX_MAX_AGE, 1, 48, cast=int)
    receipt = _bounded(config, RECEIPT_RETENTION, 1, 400, cast=int)
    overdue = _bounded(config, ERASURE_OVERDUE, 1, 90, cast=int)
    return RetentionPolicy(
        outbox_max_age=timedelta(hours=hours) if hours else OUTBOX_HARD_CEILING,
        receipt_retention=timedelta(days=receipt) if receipt else None,
        erasure_overdue=timedelta(days=overdue) if overdue else None)
