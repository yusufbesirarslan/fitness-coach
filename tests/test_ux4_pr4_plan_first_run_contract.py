"""WEB-UX4-PR4 — the Plan first run is coached, not configured.

Discovery F-07 measured a no-plan Plan page that opened with configuration and
put its only action at the very bottom; F-14 found the Plan destination naming
itself "Your Training Plan" — a child of the destination the user clicked.

These tests read the SERVER RENDER (scripts stripped, because `_head.html` ships
the whole locale catalogue inside `window.I18N` and any copy assertion against
raw HTML is vacuous). The browser suite proves geometry, focus and network.

What this PR must never move is asserted alongside what it changes: the same
eleven-key preference contract, the same single generate control, the same one
save call site, no client-side draft storage, and a regeneration surface that
renders exactly as it did before.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from app.extensions import db
from app.i18n import t
from app.models import TrainingPlan, User

ROOT = Path(__file__).resolve().parents[1]

# Canonical option vocabularies as rendered on the pre-PR4 baseline (2e5ebe5).
# A field or option that disappears — collapsed, reordered, or otherwise — is a
# generator-input regression, not a presentation change.
FIELD_OPTIONS = {
    "gun_sayisi": ["3", "4", "5", "6"],
    "antrenman_tarzi": ["genel", "crossfit", "calisthenics", "powerlifting",
                        "bodybuilding", "fonksiyonel"],
    "odak_hedef": ["genel", "guc", "kondisyon", "kas_kutlesi", "yag_yakimi", "esneklik"],
    "ekipman": ["spor_salonu", "ev", "minimal"],
    "odak": ["tum_vucut", "ust_vucut", "sirt", "alt_vucut", "core"],
    "sure": ["30", "45", "60", "90"],
    "kardiyo_tipi": ["yok", "kosu", "bisiklet", "yuzme", "ip_atlama", "yuruyus", "karisik"],
}
# The regenerate surface is not PR4's to redesign: its fields keep this order.
BASELINE_FIELD_ORDER = ["gun_sayisi", "antrenman_tarzi", "odak_hedef", "ekipman",
                        "odak", "sure", "kardiyo_tipi", "injuries"]
ESSENTIAL = ["odak_hedef", "ekipman", "gun_sayisi", "sure"]
OPTIONAL = ["antrenman_tarzi", "odak", "kardiyo_tipi"]


def _rendered_body(html):
    return re.sub(r"(?is)<script[^>]*>.*?</script>", "", html)


def _page(app, client, user, *, language="tr", plan=False):
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    with app.app_context():
        row = db.session.get(User, user.id)
        row.profile_complete = True
        row.language = language
        if plan:
            from test_sprint11_training_generation_output import _week
            db.session.add(TrainingPlan(user_id=user.id, score=6.0,
                                        plan_data=json.dumps(_week(), ensure_ascii=False)))
        db.session.commit()
    html = client.get("/training").get_data(as_text=True)
    return BeautifulSoup(_rendered_body(html), "html.parser"), html


@pytest.fixture(autouse=True)
def _flag_reset(app):
    yield
    app.config["UIUX_PLAN_V2_ENABLED"] = False


# ══════════════════════════════════════════════════════════════════════════
# F-14 — the destination the user clicked names itself
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("language", ["tr", "en"])
@pytest.mark.parametrize("plan", [False, True], ids=["no_plan", "active_plan"])
def test_plan_heading_is_the_primary_navigation_label(app, client, auth_user, language, plan):
    soup, _ = _page(app, client, auth_user, language=language, plan=plan)
    with app.test_request_context():
        destination = t("nav.plan", locale=language)
    nav_labels = {a.get_text(strip=True) for a in soup.select('a[href="/training"]')
                  if a.get_text(strip=True)}
    assert nav_labels == {destination}, nav_labels
    h1 = soup.find_all("h1")
    assert len(h1) == 1
    assert h1[0].get_text(strip=True) == destination


@pytest.mark.parametrize("language", ["tr", "en"])
def test_document_title_names_the_same_destination(app, client, auth_user, language):
    soup, _ = _page(app, client, auth_user, language=language)
    with app.test_request_context():
        destination = t("nav.plan", locale=language)
    assert soup.title.get_text(strip=True) == "AxisAI — " + destination


@pytest.mark.parametrize("language", ["tr", "en"])
def test_training_remains_a_child_domain_heading(app, client, auth_user, language):
    """F-14 renames the DESTINATION only; Training keeps its own name inside it."""
    soup, _ = _page(app, client, auth_user, language=language)
    with app.test_request_context():
        assert soup.select_one("#plan-training-label").get_text(strip=True) == t(
            "plan.domain.training", locale=language)


# ══════════════════════════════════════════════════════════════════════════
# F-07 — orientation → essentials → optional refinement → one action
# ══════════════════════════════════════════════════════════════════════════

def _create(soup):
    root = soup.select_one('[data-plan-manage][data-manage-state="create"]')
    assert root is not None
    return root


def _in_details(node):
    return node.find_parent("details") is not None


@pytest.mark.parametrize("language", ["tr", "en"])
def test_the_outcome_is_stated_before_any_preference(app, client, auth_user, language):
    soup, _ = _page(app, client, auth_user, language=language)
    root = _create(soup)
    with app.test_request_context():
        outcome = t("plan.create.outcome", locale=language)
        defaults = t("plan.create.defaults_hint", locale=language)
    text_nodes = [n for n in root.find_all(string=True) if n.strip()]
    first_field = root.select_one("[data-plan-field]")
    before_first_field = []
    for node in root.descendants:
        if node is first_field:
            break
        if isinstance(node, str) and node.strip():
            before_first_field.append(node.strip())
    joined = " ".join(before_first_field)
    assert outcome in joined, "no stated outcome precedes the first preference"
    assert defaults in joined, "the user is not told the defaults are enough"
    assert text_nodes  # the section renders copy at all


def test_essential_preferences_are_prominent_and_optional_ones_are_disclosed(
        app, client, auth_user):
    soup, _ = _page(app, client, auth_user)
    root = _create(soup)
    fields = {el["data-plan-field"]: el for el in root.select("[data-plan-field]")}
    for name in ESSENTIAL + ["injuries"]:
        assert not _in_details(fields[name]), f"{name} must be visible on first paint"
    disclosures = root.find_all("details")
    assert len(disclosures) == 1, "one native disclosure, not a wizard"
    details = disclosures[0]
    assert not details.has_attr("open"), "optional refinement starts collapsed"
    assert details.find("summary") is not None
    for name in OPTIONAL:
        assert fields[name].find_parent("details") is details, f"{name} is not optional refinement"
    # Essentials come first in reading order; injury stays outside the disclosure.
    order = [el["data-plan-field"] for el in root.select("[data-plan-field]")]
    assert order[:4] == ESSENTIAL, order


def test_the_injury_field_is_unchanged_and_never_buried(app, client, auth_user):
    soup, _ = _page(app, client, auth_user)
    injury = _create(soup).select('[data-plan-field="injuries"]')
    assert len(injury) == 1
    node = injury[0]
    assert node.name == "input" and node.get("type") == "text"
    assert node.get("maxlength") == "200"
    assert not node.has_attr("value"), "the injury field must not be prefilled"
    assert not _in_details(node)
    label = _create(soup).select_one(f'label[for="{node["id"]}"]')
    assert label is not None and label.get_text(strip=True)


@pytest.mark.parametrize("state", ["create", "regenerate"])
def test_every_field_and_option_is_still_rendered_exactly_once(app, client, auth_user, state):
    soup, _ = _page(app, client, auth_user, plan=(state == "regenerate"))
    root = soup.select_one(f'[data-plan-manage][data-manage-state="{state}"]')
    fields = root.select("[data-plan-field]")
    assert sorted(el["data-plan-field"] for el in fields) == sorted(BASELINE_FIELD_ORDER)
    for el in fields:
        name = el["data-plan-field"]
        if name == "injuries":
            continue
        assert [o["value"] for o in el.find_all("option")] == FIELD_OPTIONS[name], name
        # No option is preselected: the untouched value stays the first option,
        # exactly as on the baseline (see the payload characterization test).
        assert not any(o.has_attr("selected") for o in el.find_all("option")), name
    ints = sorted(el["data-plan-field"] for el in fields if el.get("data-plan-type") == "int")
    assert ints == ["gun_sayisi", "sure"]


def test_there_is_exactly_one_generate_control_and_one_status_region(app, client, auth_user):
    soup, _ = _page(app, client, auth_user)
    assert len(soup.select("[data-plan-manage-generate]")) == 1
    assert len(soup.select('[data-action="planManageGenerate"]')) == 1
    assert len(soup.select("[data-plan-manage-msg]")) == 1
    assert len(soup.select("[data-plan-manage]")) == 1
    # The status region sits with the action, so a sticky action can never
    # hide the answer to the click that produced it.
    action = soup.select_one("[data-plan-manage-generate]")
    msg = soup.select_one("[data-plan-manage-msg]")
    assert action.parent is msg.parent


def test_the_proposal_contract_is_unchanged(app, client, auth_user):
    soup, _ = _page(app, client, auth_user)
    root = _create(soup)
    for hook in ("data-plan-manage-proposal", "data-plan-manage-proposal-title",
                 "data-plan-manage-days", "data-plan-manage-confirm"):
        assert len(root.select(f"[{hook}]")) == 1, hook
    proposal = root.select_one("[data-plan-manage-proposal]")
    assert proposal.has_attr("hidden")
    assert proposal.select_one(".plan-proposal-note") is not None


def test_the_management_baseline_is_handed_over_unchanged(app, client, auth_user):
    _, html = _page(app, client, auth_user)
    blocks = re.findall(
        r'<script type="application/json" data-plan-manage-bootstrap>(.*?)</script>', html, re.S)
    assert len(blocks) == 1
    # The exact server statement a no-plan render made before PR4.
    assert json.loads(blocks[0]) == {"present": False}


def test_no_active_plan_is_not_announced_three_times(app, client, auth_user):
    """The header status is the canonical statement; the Training head does not
    repeat it in a second chip above a heading that says it a third way."""
    soup, _ = _page(app, client, auth_user)
    training = soup.select_one('[data-plan-domain="training"]')
    assert training.select_one(".plan-domain-state") is None
    assert soup.select_one(".plan-status--no_active_plan") is not None


# ══════════════════════════════════════════════════════════════════════════
# Regeneration is shared by the macro and is NOT redesigned
# ══════════════════════════════════════════════════════════════════════════

def test_regeneration_renders_its_pre_pr4_flat_form(app, client, auth_user):
    soup, _ = _page(app, client, auth_user, plan=True)
    root = soup.select_one('[data-plan-manage][data-manage-state="regenerate"]')
    assert root.find("details") is None
    order = [el["data-plan-field"] for el in root.select("[data-plan-field]")]
    assert order == BASELINE_FIELD_ORDER
    panel = root.select_one("[data-plan-manage-panel]")
    assert panel.has_attr("hidden")
    assert root.select_one("[data-plan-manage-open]") is not None
    # Status region is still the first thing inside the panel, as before.
    first = next(child for child in panel.children if getattr(child, "name", None))
    assert first.has_attr("data-plan-manage-msg")
    # The regeneration copy is untouched and the create-only copy is absent.
    body = root.get_text(" ", strip=True)
    with app.test_request_context():
        assert t("plan.manage.intro", locale="tr") in body
        assert t("plan.create.capability_note", locale="tr") in body
        assert t("plan.create.outcome", locale="tr") not in body
    assert soup.select_one("[data-plan-replace-confirm]") is not None


# ══════════════════════════════════════════════════════════════════════════
# Authority, storage and style guards scoped to the PR4 surface
# ══════════════════════════════════════════════════════════════════════════

PR4_SURFACE = [ROOT / "templates" / "plan.html", ROOT / "static" / "plan_training_manage.js",
               ROOT / "static" / "plan.css"]


def _code(path):
    """Source with comments removed: a comment NAMING a forbidden API (plan.html
    documents that legacy `localStorage` completion is gone) is not a use of it."""
    source = path.read_text(encoding="utf-8")
    source = re.sub(r"(?s)\{#.*?#\}", "", source)
    source = re.sub(r"(?s)/\*.*?\*/", "", source)
    return re.sub(r"(?m)^\s*//.*$", "", source)


def test_the_plan_create_path_keeps_no_client_draft_state():
    forbidden = re.compile(
        r"localStorage|sessionStorage|indexedDB|document\.cookie|history\.(pushState|replaceState)"
        r"|history\.state|URLSearchParams|location\.search|location\.hash")
    for path in PR4_SURFACE + [ROOT / "static" / "training_plan_management.js"]:
        hits = [m.group(0) for m in forbidden.finditer(_code(path))]
        assert not hits, f"{path.name} holds client-side state: {hits}"


def test_there_is_exactly_one_save_call_site():
    owners = []
    for base in (ROOT / "static", ROOT / "templates"):
        for path in base.rglob("*"):
            if path.suffix in {".js", ".html"} and "/training-plan/save" in path.read_text(
                    encoding="utf-8", errors="ignore"):
                owners.append(path.name)
    assert owners == ["training_plan_management.js"], owners


def test_the_plan_template_implements_no_request_of_its_own():
    source = (ROOT / "templates" / "plan.html").read_text(encoding="utf-8")
    inline = re.findall(r"(?is)<script(?![^>]*\bsrc=)(?![^>]*application/json)[^>]*>(.*?)</script>",
                        source)
    assert not [s for s in inline if s.strip()], "plan.html gained an inline script"
    assert "fetch(" not in source and "XMLHttpRequest" not in source


def test_the_renderer_binds_one_generate_handler():
    source = (ROOT / "static" / "plan_training_manage.js").read_text(encoding="utf-8")
    assert source.count("window.planManageGenerate =") == 1
    assert source.count("manager.generate(") == 1
    assert source.count("manager.replace(") == 1


def test_first_run_styles_consume_the_system_instead_of_minting_one():
    css = re.sub(r"(?s)/\*.*?\*/", "", (ROOT / "static" / "plan.css").read_text(encoding="utf-8"))
    assert "!important" not in css
    assert "nth-child" not in css and "nth-of-type" not in css
    assert not re.search(r"(?m)^\s*--[a-z0-9-]+\s*:", css), "plan.css defines a custom property"
    for value in re.findall(r"z-index\s*:\s*([^;]+);", css):
        assert value.strip().startswith("var(--z-"), value
    assert "@import" not in css and "font-family: '" not in css
