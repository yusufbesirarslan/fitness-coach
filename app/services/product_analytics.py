"""Unwired review foundation. No SDK, endpoint, database, logging or IO.

Only an explicitly driven worker may call drain_one. The application imports
none of this module. Real consent serialization, durable deduplication and
erasure are release gates, documented in docs/product-analytics-v1/README.md.
"""
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import MappingProxyType
from uuid import UUID

PRODUCT_ANALYTICS_ENABLED = False  # source-only hold; no environment override
_CATALOG = json.loads((Path(__file__).resolve().parents[2] /
                      'docs/product-analytics-v1/catalog.json').read_text())
MAX_PENDING = 100
MAX_SEEN = 1024
TTL = timedelta(hours=24)


def _uuid4(value):
    return (type(value) is str and str(UUID(value)) == value
            and UUID(value).version == 4)


@dataclass(frozen=True)
class Grant:
    """Analytics authority output, never synthesized from AI/health permission."""
    subject: str
    grant_id: str
    purpose: str = 'product_analytics'
    version: str = '1'

    def valid(self):
        return (_uuid4(self.subject) and _uuid4(self.grant_id)
                and self.purpose == 'product_analytics' and self.version == '1')


@dataclass(frozen=True)
class Event:
    event_id: str
    action_id: str
    name: str
    source: str
    occurred_at: datetime
    properties: object

    @classmethod
    def parse(cls, data, *, source, now):
        keys = {'schema_version', 'event_id', 'action_id', 'name', 'source',
                'occurred_at', 'properties'}
        if type(data) is not dict or set(data) != keys:
            raise ValueError('invalid envelope')
        if type(data['schema_version']) is not int or data['schema_version'] != 1:
            raise ValueError('invalid version')
        spec = _CATALOG['events'].get(data['name'])
        if not spec or spec['source'] != source or data['source'] != source:
            raise ValueError('invalid authority')
        if not _uuid4(data['event_id']) or not _uuid4(data['action_id']):
            raise ValueError('invalid identity')
        props = data['properties']
        if (type(props) is not dict or set(props) != set(spec['properties'])
                or any(type(v) is not str or v not in spec['properties'][k]
                       for k, v in props.items())):
            raise ValueError('invalid properties')
        if type(data['occurred_at']) is not str or len(data['occurred_at']) > 32:
            raise ValueError('invalid time')
        occurred = datetime.fromisoformat(data['occurred_at'].replace('Z', '+00:00'))
        if (occurred.utcoffset() != timedelta(0)
                or not now - TTL <= occurred <= now + timedelta(minutes=5)):
            raise ValueError('invalid time')
        return cls(data['event_id'], data['action_id'], data['name'], source,
                   occurred, MappingProxyType(dict(props)))


class ReviewBuffer:
    """Single-worker in-memory prototype; no persistence/reliable delivery claim.

    current_grant is an independent analytics authority port. sink receives
    validated Events and a random subject only, never auth context. Test seams
    do not constitute privacy clearance. Default construction cannot collect.
    """
    def __init__(self, *, current_grant=lambda: None, sink=lambda *_: None,
                 clock=lambda: datetime.now(timezone.utc), enabled=PRODUCT_ANALYTICS_ENABLED):
        self.enabled = enabled
        self._authority, self._sink, self._clock = current_grant, sink, clock
        self._grant = None
        self._pending = OrderedDict()
        self._seen = {}

    def clear(self):
        self._grant = None
        self._pending.clear()
        self._seen.clear()

    def _admit(self):
        try:
            grant = self._authority() if self.enabled else None
            if type(grant) is not Grant or not grant.valid():
                self.clear()
                return None
            if grant != self._grant:
                self.clear()
                self._grant = grant
            return grant
        except Exception:
            self.clear()
            return None

    def record(self, data, *, trusted_source):
        """Non-IO hot path. trusted_source belongs to adapter, never HTTP input."""
        try:
            if self._admit() is None:
                return False
            now = self._clock()
            self._prune(now)
            event = Event.parse(data, source=trusted_source, now=now)
            if (event.event_id in self._seen or len(self._pending) >= MAX_PENDING
                    or len(self._seen) >= MAX_SEEN):
                return False
            self._seen[event.event_id] = now
            self._pending[event.event_id] = (event, 0, now)
            return True
        except Exception:
            return False  # never log the rejected payload or error

    def _prune(self, now):
        self._seen = {key: at for key, at in self._seen.items() if now - at <= TTL}
        self._pending = OrderedDict((key, value) for key, value in self._pending.items()
                                    if now - value[0].occurred_at <= TTL)

    def drain_one(self):
        """Explicit review-worker tick. Sink must have its own bounded timeout.

        No thread/timer is started. Production withdrawal must serialize the
        authorization check and provider dispatch; this prototype is unwired.
        """
        try:
            grant = self._admit()
            if grant is None:
                return False
            now = self._clock()
            self._prune(now)
            candidate = next(((key, value) for key, value in self._pending.items()
                              if value[2] <= now), None)
            if candidate is None:
                return False
            key, (event, attempts, _) = candidate
            try:
                self._sink(event, grant.subject)
            except Exception:
                attempts += 1
                if attempts >= 3:
                    self._pending.pop(key, None)
                else:
                    self._pending[key] = (event, attempts,
                                          now + timedelta(seconds=10 * 3 ** (attempts - 1)))
                return False
            self._pending.pop(key, None)
            return True
        except Exception:
            return False
