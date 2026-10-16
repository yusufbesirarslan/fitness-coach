"""Pure allowance policy: feature taxonomy, validated quantities and periods.

Stdlib + ``app.timeutil`` only. There is deliberately NO production default and
NO environment channel: ``current_app.config["AI_ALLOWANCE_POLICY"]`` is
``None`` unless something constructs an ``AllowancePolicy`` explicitly (today:
tests only). A missing policy, scope or feature is reported as such and is
never read as "unlimited" or as zero.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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

PERIOD_NEVER = "NEVER"
PERIOD_WEEKLY = "WEEKLY"
PERIOD_MONTHLY = "MONTHLY"

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
    # Provider-billed failures are released at most this many times per bucket;
    # after that a billed failure consumes its unit (no unlimited free retries).
    max_billed_releases: int

    def __init__(self, period, allowances, max_billed_releases):
        if period not in (PERIOD_NEVER, PERIOD_WEEKLY, PERIOD_MONTHLY):
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
class AllowancePolicy:
    version: str
    starter: ScopePolicy | None = None
    # Accounts created at/after this instant are Starter-eligible. Existing
    # accounts are NOT backfilled in this provisional test policy.
    # FOUNDER_DECISION_REQUIRED before activation; see docs/AI_ALLOWANCE_LEDGER.md.
    starter_eligible_from: datetime | None = None
    premium: ScopePolicy | None = None

    def __post_init__(self):
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
                                         or self.premium.period == PERIOD_NEVER):
            raise PolicyError("Premium allowances renew weekly or monthly")

    def scope(self, name):
        return self.starter if name == SCOPE_STARTER else self.premium


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
