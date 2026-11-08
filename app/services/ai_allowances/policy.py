"""Pure allowance policy: feature taxonomy, validated quantities and periods.

Stdlib + ``app.timeutil`` only. The source installer selects the approved launch
policy; there is no environment/client policy channel. A missing policy, scope
or feature is reported as such and never read as "unlimited" or as zero.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from types import MappingProxyType

from app.timeutil import APP_TZ

# ── Feature taxonomy ───────────────────────────────────────────────────────
# One visible unit = one logical successful user action of that feature.
SUPPORTED_FEATURES = (
    "coach",                 # native Coach + web /ask, /ask/stream, /chat
    "training_generation",   # native + web training plan generation
    "nutrition_generation",  # native + web nutrition plan generation
    "pump_check",            # Pump Check analysis
    "qr_menu",               # LP15-C menu analysis
)

DISPOSITION_FAMILY = "FAMILY"        # same operation on another transport
DISPOSITION_UNDECIDED = "UNDECIDED"  # needs an owner decision; not metered here
DISPOSITION_EXEMPT = "EXEMPT"        # must never consume a user unit


@dataclass(frozen=True)
class FeatureExtension:
    disposition: str
    family: str | None
    note: str


# Extension points. Only FAMILY entries resolve; everything else is refused
# rather than silently mapped (enforcement wiring belongs to BE-03B).
EXTENSIONS = MappingProxyType({
    "web_coach": FeatureExtension(DISPOSITION_FAMILY, "coach",
                                  "web /ask, /ask/stream and /chat already share the chat bucket"),
    "web_training_generation": FeatureExtension(DISPOSITION_FAMILY, "training_generation",
                                                "web /training-plan shares the training bucket"),
    "web_nutrition_generation": FeatureExtension(DISPOSITION_FAMILY, "nutrition_generation",
                                                 "web /nutrition-plan shares the nutrition bucket"),
    "training_replacement": FeatureExtension(DISPOSITION_UNDECIDED, None,
                                             "candidate family training_generation; owner decision D-3"),
    "pump_comparison": FeatureExtension(DISPOSITION_UNDECIDED, None, "owner decision D-2"),
    "legacy_web_ai": FeatureExtension(DISPOSITION_UNDECIDED, None,
                                      "check-in feedback, plan comment, social, food AI; owner decision D-6"),
    "workout_photo_proof": FeatureExtension(DISPOSITION_EXEMPT, None,
                                            "core workout completion; never metered"),
    "coach_summary": FeatureExtension(DISPOSITION_EXEMPT, None,
                                      "background memory summary; spend guard only"),
})


class UnsupportedFeature(ValueError):
    """The name is not a supported feature or a decided family alias."""


def canonical_feature(name):
    if not isinstance(name, str):
        raise UnsupportedFeature(type(name).__name__)
    if name in SUPPORTED_FEATURES:
        return name
    extension = EXTENSIONS.get(name)
    if extension is not None and extension.disposition == DISPOSITION_FAMILY:
        return extension.family
    raise UnsupportedFeature(name if isinstance(name, str) else type(name).__name__)


# ── Scopes and periods ─────────────────────────────────────────────────────
SCOPE_STARTER = "STARTER"
SCOPE_PREMIUM = "PREMIUM"
# A finite, campaign-anchored AI allowance scope. It is never a subscription
# entitlement: it cannot make Premium effective or stand in for Apple evidence.
SCOPE_LAUNCH_FREE = "LAUNCH_FREE"
SCOPES = (SCOPE_STARTER, SCOPE_PREMIUM, SCOPE_LAUNCH_FREE)

PERIOD_NEVER = "NEVER"
PERIOD_WEEKLY = "WEEKLY"
PERIOD_MONTHLY = "MONTHLY"
# Campaign windows. The scope's own period; the wire ``reset`` token is
# CAMPAIGN_WINDOW while another window follows and CAMPAIGN_END in the last.
PERIOD_CAMPAIGN = "CAMPAIGN_WINDOW"
RESET_CAMPAIGN_END = "CAMPAIGN_END"
CAMPAIGN_WINDOW_DAYS = 30
MAX_CAMPAIGN_WINDOWS = 12

MAX_UNITS = 10_000
MAX_BILLED_RELEASES = 1_000
_VERSION = re.compile(r"[A-Za-z0-9._-]{1,32}")


class PolicyError(ValueError):
    """A constructed policy is malformed; refuse it rather than guess."""


def _count(value, low, high, what):
    if type(value) is not int or not low <= value <= high:
        raise PolicyError(f"{what} must be an integer in [{low}, {high}]")
    return value


@dataclass(frozen=True)
class ScopePolicy:
    period: str
    allowances: MappingProxyType
    # Historical settlement cap. Usable-result policies refund known unusable
    # outcomes independently; original-action physical and operational budgets
    # still bound every paid attempt.
    max_billed_releases: int

    def __init__(self, period, allowances, max_billed_releases):
        if period not in (PERIOD_NEVER, PERIOD_WEEKLY, PERIOD_MONTHLY, PERIOD_CAMPAIGN):
            raise PolicyError("unknown period")
        if not isinstance(allowances, dict) or not allowances:
            raise PolicyError("allowances must be a non-empty dict")
        clean = {}
        for feature, units in allowances.items():
            if feature not in SUPPORTED_FEATURES:
                raise PolicyError("allowance for an unsupported feature")
            clean[feature] = _count(units, 1, MAX_UNITS, "allowance")
        object.__setattr__(self, "period", period)
        object.__setattr__(self, "allowances", MappingProxyType(clean))
        object.__setattr__(self, "max_billed_releases",
                           _count(max_billed_releases, 0, MAX_BILLED_RELEASES, "max_billed_releases"))


@dataclass(frozen=True)
class CampaignPolicy:
    """A finite campaign: fixed 30-day windows anchored at ``starts_at``.

    Window ``n`` (1-based) is ``[start + 30(n-1) days, start + 30n days)`` at
    00:00 Europe/Istanbul, start-inclusive and end-exclusive. Nothing exists
    before the start or at/after the end. Source-only, like every policy.
    """
    version: str
    starts_at: datetime
    windows: int
    allowances: ScopePolicy

    def __post_init__(self):
        if not isinstance(self.version, str) or not _VERSION.fullmatch(self.version):
            raise PolicyError("version must match [A-Za-z0-9._-]{1,32}")
        if not isinstance(self.starts_at, datetime) or self.starts_at.tzinfo is None:
            raise PolicyError("campaign needs an aware start instant")
        if self.starts_at.astimezone(APP_TZ).timetz().replace(tzinfo=None) != time(0):
            raise PolicyError("campaign starts at 00:00 Europe/Istanbul")
        _count(self.windows, 1, MAX_CAMPAIGN_WINDOWS, "windows")
        if not isinstance(self.allowances, ScopePolicy) or self.allowances.period != PERIOD_CAMPAIGN:
            raise PolicyError("campaign allowances renew per campaign window")

    def boundary(self, n):
        """Start of window ``n + 1`` (``boundary(0)`` is the campaign start), aware UTC."""
        day = self.starts_at.astimezone(APP_TZ).date() + timedelta(days=CAMPAIGN_WINDOW_DAYS * n)
        return datetime.combine(day, time(0), tzinfo=APP_TZ).astimezone(timezone.utc)

    @property
    def ends_at(self):
        return self.boundary(self.windows)


@dataclass(frozen=True)
class AllowancePolicy:
    version: str
    starter: ScopePolicy | None = None
    # Accounts created at/after this instant are Starter-eligible. Launch V1
    # includes every valid creation timestamp; NULL is never auto-granted.
    starter_eligible_from: datetime | None = None
    premium: ScopePolicy | None = None
    # Customer settlement is independent of provider billing. Historical
    # policies retain their original release caps; the launch policy refunds
    # only a reconciled absence of a usable result.
    refund_unusable_results: bool = False
    # Optional finite campaign scope. Its buckets carry the campaign's version.
    launch_free: CampaignPolicy | None = None

    def __post_init__(self):
        if type(self.refund_unusable_results) is not bool:
            raise PolicyError("refund_unusable_results must be Boolean")
        if not isinstance(self.version, str) or not _VERSION.fullmatch(self.version):
            raise PolicyError("version must match [A-Za-z0-9._-]{1,32}")
        if self.starter is not None:
            if not isinstance(self.starter, ScopePolicy) or self.starter.period != PERIOD_NEVER:
                raise PolicyError("Starter never resets")
            if (not isinstance(self.starter_eligible_from, datetime)
                    or self.starter_eligible_from.tzinfo is None):
                raise PolicyError("Starter needs an aware eligibility instant")
        elif self.starter_eligible_from is not None:
            raise PolicyError("eligibility without a Starter scope")
        if self.premium is not None and (not isinstance(self.premium, ScopePolicy)
                                         or self.premium.period not in (PERIOD_WEEKLY, PERIOD_MONTHLY)):
            raise PolicyError("Premium allowances renew weekly or monthly")
        if self.launch_free is not None:
            if not isinstance(self.launch_free, CampaignPolicy):
                raise PolicyError("Launch Free is a campaign policy")
            if self.launch_free.version == self.version:
                raise PolicyError("campaign buckets carry their own policy version")

    def scope(self, name):
        if name == SCOPE_STARTER:
            return self.starter
        if name == SCOPE_PREMIUM:
            return self.premium
        if name == SCOPE_LAUNCH_FREE:
            return self.launch_free.allowances if self.launch_free is not None else None
        return None

    def bucket_version(self, name):
        """The policy version a bucket of this scope is granted under."""
        if name == SCOPE_LAUNCH_FREE:
            return self.launch_free.version if self.launch_free is not None else None
        return self.version


def starter_eligible(policy, account_created_at):
    """Eligibility is a pure function of the account's own creation instant.

    ``User.created_at`` is naive UTC; NULL (legacy rows) is never eligible.
    """
    if policy is None or policy.starter is None or account_created_at is None:
        return False
    created = (account_created_at.replace(tzinfo=timezone.utc)
               if account_created_at.tzinfo is None else account_created_at)
    return created >= policy.starter_eligible_from


def period_key(period, now):
    """Bucket key for ``now``; Istanbul calendar, matching the legacy quota week."""
    if period == PERIOD_NEVER:
        return "NEVER"
    local = now.astimezone(APP_TZ).date()
    if period == PERIOD_WEEKLY:
        year, week, _ = local.isocalendar()
        return f"{year}-W{week:02d}"
    if period == PERIOD_MONTHLY:
        return f"{local.year}-{local.month:02d}"
    raise PolicyError("unknown period")


def resets_at(period, now):
    """Next reset instant (aware UTC), or ``None`` when the balance never resets."""
    if period == PERIOD_NEVER:
        return None
    local = now.astimezone(APP_TZ).date()
    if period == PERIOD_WEEKLY:
        start = local + timedelta(days=7 - local.weekday())
    elif period == PERIOD_MONTHLY:
        start = (local.replace(year=local.year + 1, month=1, day=1) if local.month == 12
                 else local.replace(month=local.month + 1, day=1))
    else:
        raise PolicyError("unknown period")
    return datetime(start.year, start.month, start.day, tzinfo=APP_TZ).astimezone(timezone.utc)


# ── Campaign windows (LAUNCH_FREE) ─────────────────────────────────────────
def campaign_window(campaign, now):
    """``(n, start, end)`` of the window containing ``now``, or ``None``.

    ``None`` before the start, at/after the end, or without a campaign. A pure
    function of the source campaign and the server clock; never a request value.
    """
    if campaign is None:
        return None
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise PolicyError("campaign windows need an aware instant")
    now = now.astimezone(timezone.utc)
    if now < campaign.boundary(0) or now >= campaign.ends_at:
        return None
    for n in range(1, campaign.windows + 1):
        end = campaign.boundary(n)
        if now < end:
            return n, campaign.boundary(n - 1), end
    return None  # unreachable: now < ends_at


def campaign_period_key(campaign, now):
    """``LF1``..``LFn`` for an active window, else ``None`` (no grant)."""
    window = campaign_window(campaign, now)
    return None if window is None else f"LF{window[0]}"


def campaign_reset(campaign, now):
    """``(reset, resets_at)`` for an active window, else ``(None, None)``.

    The final window advertises no further campaign replenishment.
    """
    window = campaign_window(campaign, now)
    if window is None:
        return None, None
    n, _, end = window
    if n < campaign.windows:
        return PERIOD_CAMPAIGN, end
    return RESET_CAMPAIGN_END, None
