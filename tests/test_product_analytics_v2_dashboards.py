"""Dashboard definitions reference only catalog events and properties."""
import json
import re
from pathlib import Path

from app.services.product_analytics import catalog

DOC = json.loads((Path(__file__).resolve().parents[1]
                  / "docs/product-analytics-v2/dashboards.json").read_text(encoding="utf-8"))


def test_nine_panels_are_specified_with_exact_definitions():
    ids = [panel["id"] for panel in DOC["panels"]]
    assert {panel_id[:2] for panel_id in ids} == {f"P{n}" for n in range(1, 10)}
    for panel in DOC["panels"]:
        for key in ("numerator", "denominator", "cohort", "maturity", "limitations"):
            assert panel[key], (panel["id"], key)
        source = panel["insight"]["query"]["source"]
        assert source["kind"] == "HogQLQuery" and source["query"].startswith(("SELECT", "WITH"))


def test_queries_use_only_catalog_names_property_values_and_no_identity():
    properties = {key: set(values) for spec in catalog.EVENTS.values()
                  for key, values in spec["properties"].items()}
    merged = {}
    for key, values in ((k, v) for spec in catalog.EVENTS.values()
                        for k, v in spec["properties"].items()):
        merged.setdefault(key, set()).update(values)
    for panel in DOC["panels"]:
        query = panel["insight"]["query"]["source"]["query"]
        for name in re.findall(r"'(axis_v2_[a-z_]+)'", query):
            assert name in catalog.EVENTS, (panel["id"], name)
        for key in re.findall(r"properties\.([a-z_$]+)", query):
            assert key in properties, (panel["id"], key)
        for key, value in re.findall(r"properties\.([a-z_]+)\s*=\s*'([a-z_]+)'", query):
            assert value in merged[key], (panel["id"], key, value)
        for banned in ("person", "$ip", "$geoip", "email", "$set"):
            assert banned not in query, (panel["id"], banned)
