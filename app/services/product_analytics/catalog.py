"""The closed v2 event catalog. Any new event or property is a catalog change."""
import json
from pathlib import Path

_PATH = Path(__file__).resolve().parents[3] / "docs/product-analytics-v2/catalog.json"
CATALOG = json.loads(_PATH.read_text(encoding="utf-8"))
EVENTS = CATALOG["events"]
NOTICE_VERSION = CATALOG["notice_version"]
ENVELOPE = CATALOG["client_envelope"]
WIRE = CATALOG["vendor_wire_allowlist"]
SERVER_EVENTS = frozenset(n for n, s in EVENTS.items() if s["authority"] == "server")
CLIENT_EVENTS = frozenset(n for n, s in EVENTS.items() if s["authority"] == "client")


def valid_properties(name, properties, authority):
    """Exact key set, string values from the closed enum only."""
    spec = EVENTS.get(name)
    if spec is None or spec["authority"] != authority:
        return False
    if type(properties) is not dict or set(properties) != set(spec["properties"]):
        return False
    return all(type(value) is str and value in spec["properties"][key]
               for key, value in properties.items())
