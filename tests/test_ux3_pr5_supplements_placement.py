"""UX-3 PR5: Supplements belongs to Plan → Nutrition, and owns exactly one editor.

The load-bearing invariant is ONE editable cabinet. These tests are written to
fail when a future change grows a second one, so they inspect the shipped
templates and scripts for the mutation endpoints themselves rather than watching
a single button id. Placement is checked through rendered responses and the
production navigation resolver, not by reading source strings out of context.
"""
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from sqlalchemy import event

from app import nav

ROOT = Path(__file__).resolve().parents[1]

# The canonical Supplement mutation authority (app/blueprints/supplements.py).
# A surface that names any of these is asking to write the cabinet.
MUTATION_ENDPOINTS = ("/supplement/add", "/supplement/edit", "/supplement/delete")

# The ONE surface allowed to carry them. Adding a file here is a deliberate act
# that must be argued in review; that is exactly the friction this list buys.
CANONICAL_EDITOR = "templates/manage_stack.html"


def _login_user(client, make_user, login, username="ux3pr5", language="en"):
    user = make_user(username, profile_complete=True, language=language)
    login(username)
    return user


def _add_supplements(user_id, *specs):
    """Seed the cabinet directly; the write PATH is exercised separately."""
    from app.extensions import db
    from app.models import Supplement

    created = []
    for spec in specs:
        row = Supplement(
            user_id=user_id,
            product_name=spec.get("product_name", "Whey"),
            brand=spec.get("brand", "Brand"),
            category=spec.get("category", "Protein"),
            status=spec.get("status", "Active"),
            is_public=spec.get("is_public", True),
            price_paid=spec.get("price_paid"),
            review_text=spec.get("review_text"),
            rating_effect=spec.get("rating_effect"),
        )
        db.session.add(row)
        created.append(row)
    db.session.commit()
    return created


def _rendered_body(html):
    """Page HTML with every <script> block removed.

    `templates/_head.html` injects the WHOLE locale catalog as
    `window.I18N = {...}`, so `"some translated sentence" in html` is true on
    EVERY page whether or not the page renders it — an assertion like that
    proves nothing. Strip the scripts and assert against what a user can read.
    """
    return re.sub(r"(?is)<script[^>]*>.*?</script>", "", html)


class _AncestryParser(HTMLParser):
    """Record the open-tag stack above the element carrying `attr=value`."""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, attr, value):
        super().__init__(convert_charrefs=True)
        self._attr, self._value = attr, value
        self._stack = []
        self.found = None

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            return
        pairs = dict(attrs)
        if self.found is None and pairs.get(self._attr) == self._value:
            self.found = list(self._stack)
        self._stack.append((tag, pairs.get(self._attr)))

    def handle_endtag(self, tag):
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                return


def _ancestors_of(html, attr, value):
    parser = _AncestryParser(attr, value)
    parser.feed(html)
    return parser.found


def _element_html(html, opening_marker):
    """The rendered markup of one element, from its marker to its close.

    Bounded by the next `</section>`, which is correct for the leaf-most
    `<section>`s this module inspects and, unlike an open-ended slice, cannot
    silently widen to the rest of the document if the marker moves.
    """
    after = html.split(opening_marker, 1)[1]
    return after.split("</section>", 1)[0]


def _shipped_frontend_sources():
    """Every template and browser script this app actually serves."""
    for pattern in ("templates/**/*.html", "static/**/*.js"):
        for path in ROOT.glob(pattern):
            if path.is_file():
                yield path.relative_to(ROOT).as_posix(), path.read_text(
                    encoding="utf-8", errors="replace",
                )


# ─────────────────────────────────────────────────────────────────────────────
# 1. ONE EDITABLE CABINET — the P1 invariant
# ─────────────────────────────────────────────────────────────────────────────

def test_only_the_canonical_cabinet_template_names_supplement_mutations():
    """Plan, Nutrition, Profile — or anything else — must not gain a second editor.

    This fails for a future developer who adds a `/supplement/add|edit|delete`
    form, fetch, or action handler anywhere but the canonical cabinet.
    """
    offenders = {
        name: [e for e in MUTATION_ENDPOINTS if e in body]
        for name, body in _shipped_frontend_sources()
        if any(e in body for e in MUTATION_ENDPOINTS)
    }

    assert set(offenders) == {CANONICAL_EDITOR}, offenders
    assert set(offenders[CANONICAL_EDITOR]) == set(MUTATION_ENDPOINTS)


@pytest.mark.parametrize("template", [
    "templates/plan.html",
    "templates/nutrition.html",
    "templates/edit_profile.html",
])
def test_plan_nutrition_and_profile_carry_no_supplement_write_affordance(template):
    """Not just the URLs: no form target, and no cabinet action handler either."""
    body = (ROOT / template).read_text(encoding="utf-8")

    assert "supplement" not in body.lower() or "/supplements" in body
    for endpoint in MUTATION_ENDPOINTS:
        assert endpoint not in body
    for handler in ("addSupplement", "quickStatus", "deleteSupplement"):
        assert handler not in body, handler
    assert not re.search(r'<form[^>]*action=["\'][^"\']*supplement', body, re.I)


def test_rendered_plan_nutrition_and_profile_expose_only_navigation(
    app, client, make_user, login,
):
    """Proved on the RESPONSE, so a template partial cannot smuggle one in."""
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    user = _login_user(client, make_user, login, "ux3pr5-nowrite")
    _add_supplements(user.id, {"product_name": "Creatine", "category": "Creatine"})

    for path in ("/training", "/nutrition", "/edit-profile"):
        html = client.get(path).get_data(as_text=True)
        assert 'href="/supplements"' in html, path
        for endpoint in MUTATION_ENDPOINTS:
            assert endpoint not in html, (path, endpoint)


def test_supplement_writes_reject_a_get_so_a_link_can_never_mutate(client, auth_user):
    """The only surfaces that link out use GET; the authority refuses GET."""
    from app.extensions import db
    from app.models import Supplement

    row = Supplement(user_id=auth_user.id, product_name="X", brand="B",
                     category="Protein", status="Active")
    db.session.add(row)
    db.session.commit()

    assert client.get("/supplement/add").status_code == 405
    assert client.get(f"/supplement/edit/{row.id}").status_code == 405
    assert client.get(f"/supplement/delete/{row.id}").status_code == 405


# ─────────────────────────────────────────────────────────────────────────────
# 2. ROUTE + NAVIGATION CONTRACT
# ─────────────────────────────────────────────────────────────────────────────

def test_supplements_route_identity_and_write_paths_are_unchanged(app):
    adapter = app.url_map.bind("")

    assert adapter.match("/supplements", method="GET")[0] == (
        "supplements.supplements_page")
    assert adapter.match("/supplement/add", method="POST")[0] == (
        "supplements.supplement_add")
    assert adapter.match("/supplement/edit/7", method="POST")[0] == (
        "supplements.supplement_edit")
    assert adapter.match("/supplement/delete/7", method="POST")[0] == (
        "supplements.supplement_delete")

    # PR5 explicitly introduces no alias, nesting, or migration target.
    paths = {rule.rule for rule in app.url_map.iter_rules()}
    for invented in ("/plan", "/plan/supplements", "/nutrition/supplements",
                     "/profile/supplements", "/supplements/manage-v2"):
        assert invented not in paths, invented


def test_supplements_is_not_a_global_destination_and_keeps_plan_active():
    primary = {d["id"] for d in nav.primary_destinations()}
    secondary = {d["id"] for d in nav.secondary_destinations()}

    assert len(nav.primary_destinations()) == 4
    assert primary == {"today", "plan", "coach", "progress"}
    assert "supplements" not in primary and "supplements" not in secondary
    assert nav.resolve_active("supplements") == "plan"
    assert nav.resolve_active("nutrition") == "plan"


def test_supplements_page_renders_plan_as_the_active_primary_destination(
    app, client, make_user, login,
):
    _login_user(client, make_user, login, "ux3pr5-active")

    html = client.get("/supplements").get_data(as_text=True)

    assert 'class="hn-link active" aria-current="page">Plan</a>' in html
    assert html.count('[data-nav-id="supplements"]') == 0
    # Header tab + mobile bar are the two renderings of the same active mark.
    assert html.count('data-nav-id="plan"') == 2


# ─────────────────────────────────────────────────────────────────────────────
# 3. PLACEMENT — Plan → Nutrition → Supplements
# ─────────────────────────────────────────────────────────────────────────────

def test_plan_nests_supplements_inside_the_nutrition_placement(
    app, client, make_user, login,
):
    """Supplements is a CHILD of Nutrition on Plan, never a peer of Training."""
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    user = _login_user(client, make_user, login, "ux3pr5-plan")
    _add_supplements(user.id, {"product_name": "A"}, {"product_name": "B"})

    html = client.get("/training").get_data(as_text=True)

    # Nesting is an ANCESTRY claim, so parse it. A substring slice would only
    # prove the supplements block comes after the nutrition one, which is also
    # true of a sibling section — exactly the arrangement PR5 must forbid.
    ancestors = _ancestors_of(html, "data-plan-domain", "supplements")
    assert ancestors is not None, "supplements block not rendered"
    assert ("section", "nutrition") in ancestors, ancestors
    assert ("section", "training") not in ancestors, ancestors

    supplements = _element_html(html, '<section class="plan-domain-child"')
    assert 'data-plan-domain="supplements"' in supplements
    assert "2 supplements saved." in supplements
    assert supplements.count('href="/supplements"') == 1

    # Bounded facts only — no cabinet detail reaches Plan.
    for leak in ("rating", "price", "review", "is_public", "brand"):
        assert leak not in supplements.lower(), leak


def test_nutrition_exposes_supplements_as_a_child_domain_not_a_sixth_tab(
    app, client, make_user, login,
):
    _login_user(client, make_user, login, "ux3pr5-nutrition")

    html = client.get("/nutrition").get_data(as_text=True)

    # The five daily workflow tabs are unchanged — Supplements is not among them.
    assert html.count('role="tab"') == 5
    assert html.count('role="tabpanel"') == 5
    tabs = html.split('class="tab-bar" role="tablist"', 1)[1].split("</div>", 1)[0]
    for label in ("Today", "Diary", "Nutrition Plan", "History", "Water"):
        assert label in tabs, label
    assert 'data-tab-name="supplements"' not in html
    assert 'id="panel-supplements"' not in html

    # It IS reachable, from its own landmark outside the tablist.
    child = html.split('class="nutrition-child-domain"', 1)[1].split("</nav>", 1)[0]
    assert 'href="/supplements"' in child
    assert "Supplement cabinet" in child
    assert "Part of Nutrition" in child
    assert 'class="tab-btn' not in child

    tablist = html.split('class="tab-bar" role="tablist"', 1)[1].split("</div>", 1)[0]
    assert "/supplements" not in tablist


def test_supplements_page_states_its_parent_chain_without_a_second_shell(
    app, client, make_user, login,
):
    _login_user(client, make_user, login, "ux3pr5-context")

    html = client.get("/supplements").get_data(as_text=True)
    context = html.split('class="stack-parent-context"', 1)[1].split("</nav>", 1)[0]

    assert 'href="/training"' in context
    assert 'href="/nutrition"' in context
    assert 'aria-current="page"' in context
    assert context.index("Plan") < context.index("Nutrition") < context.index(
        "Supplements")

    # Local orientation only: no duplicated Plan shell or Nutrition tablist.
    assert 'role="tablist"' not in html
    assert "plan-domain" not in html
    assert html.count("<h1") == 1


def test_profile_is_a_contextual_projection_not_the_supplements_home(
    app, client, make_user, login,
):
    user = _login_user(client, make_user, login, "ux3pr5-profile")
    _add_supplements(user.id, {"product_name": "Magnesium",
                               "category": "Vitamin/Health"})

    html = client.get("/edit-profile").get_data(as_text=True)
    body = _rendered_body(html)

    assert "You manage it in Plan → Nutrition → Supplements." in body
    assert "Open supplement cabinet" in body
    # Ownership-flavoured copy that made Account look like the cabinet is gone.
    assert "Edit Your Stack" not in body
    assert "Add Your First Supplement" not in body
    # The projection itself survives — PR5 removes ambiguity, not the feature.
    assert "Magnesium" in body
    assert html.count('href="/supplements"') == 1


def test_profile_without_supplements_links_out_instead_of_offering_a_form(
    app, client, make_user, login,
):
    _login_user(client, make_user, login, "ux3pr5-profile-empty")

    html = client.get("/edit-profile").get_data(as_text=True)
    body = _rendered_body(html)

    # Jinja escapes the apostrophe; assert on the stable half of the sentence.
    assert "added any supplements yet." in body
    assert "Open supplement cabinet" in body
    assert html.count('href="/supplements"') == 1
    assert 'id="add-btn"' not in html
    assert 'id="f-name"' not in html


# ─────────────────────────────────────────────────────────────────────────────
# 4. STATE MATRIX — zero / one / many / private / statuses / read failure
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("count,expected", [
    (0, "No supplements saved yet."),
    (1, "1 supplements saved."),
    (4, "4 supplements saved."),
])
def test_plan_summary_counts_are_bounded_and_truthful(
    app, client, make_user, login, count, expected,
):
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    user = _login_user(client, make_user, login, f"ux3pr5-count-{count}")
    _add_supplements(user.id, *[{"product_name": f"S{i}"} for i in range(count)])

    html = client.get("/training").get_data(as_text=True)
    section = html.split('data-plan-domain="supplements"', 1)[1].split(
        "</section>", 1)[0]

    assert expected in section
    assert f'data-domain-state="{"available" if count else "empty"}"' in html


def test_supplement_read_failure_is_never_rendered_as_an_empty_cabinet(
    app, make_user, monkeypatch,
):
    """`unavailable` and `empty` must stay two different statements."""
    from app.services import plan_facts as pf
    from app.plan_presenter import build_plan_view

    user = make_user("ux3pr5-readfail", profile_complete=True)

    class _Boom:
        def filter_by(self, **_kw):
            raise RuntimeError("supplement read down")

    monkeypatch.setattr(pf.Supplement, "query", _Boom())
    facts = pf.gather_plan_facts(user.id)

    assert facts.supplements_state == "unavailable"
    assert facts.supplements_count is None
    assert build_plan_view(facts).supplements_state == "unavailable"

    # And the template must not fall through to the empty copy for it.
    from app.i18n import reload_catalog
    catalog = reload_catalog()["en"]
    assert catalog["plan.supplements.unavailable"] != catalog["plan.supplements.empty"]

    body = (ROOT / "templates" / "plan.html").read_text(encoding="utf-8")
    assert "plan.supplements.unavailable" in body
    assert body.index("plan.supplements.unavailable") < body.index(
        "plan.supplements.count")


def test_supplement_read_failure_leaves_training_and_nutrition_usable(
    app, client, make_user, login, monkeypatch,
):
    from app.services import plan_facts as pf

    app.config["UIUX_PLAN_V2_ENABLED"] = True
    user = _login_user(client, make_user, login, "ux3pr5-isolation")
    from app.extensions import db
    from app.models import TrainingPlan, UserSession
    db.session.add(UserSession(user_id=user.id, target_calories=2100))
    db.session.add(TrainingPlan(user_id=user.id, plan_data=json.dumps(
        [{"gun": "Pazartesi", "tip": "dinlenme", "egzersizler": []}])))
    db.session.commit()

    class _Boom:
        def filter_by(self, **_kw):
            raise RuntimeError("supplement read down")

    monkeypatch.setattr(pf.Supplement, "query", _Boom())

    response = client.get("/training")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Supplement count is temporarily unavailable." in _rendered_body(html)
    assert "No supplements saved yet." not in _rendered_body(html)
    assert 'data-plan-domain="training"' in html
    assert "2100" in html                      # Nutrition summary survived
    assert 'href="/supplements"' in html       # placement still truthful

    # Nutrition's own page never asks about supplements, so it cannot degrade.
    assert client.get("/nutrition").status_code == 200


@pytest.mark.parametrize("status", ["Active", "Low Stock", "Finished"])
def test_status_values_are_preserved_and_only_localized_at_the_surface(
    app, client, make_user, login, status,
):
    from app.extensions import db
    from app.models import Supplement

    user = _login_user(client, make_user, login, f"ux3pr5-st-{status.replace(' ', '')}")
    row, = _add_supplements(user.id, {"product_name": "S", "status": status})
    row_id = row.id

    html = client.get("/supplements").get_data(as_text=True)

    # The stored enum-like value is untouched; only presentation is localized.
    assert db.session.get(Supplement, row_id).status == status

    # Status is not conveyed by colour alone — the badge carries text.
    badge = html.split('class="status-badge', 1)[1].split("</span>", 1)[0]
    assert badge.split(">", 1)[1].strip()

    # The row's controls are the cabinet's own, and they name the canonical
    # values rather than links that would navigate somewhere to mutate.
    actions = html.split('class="supp-actions"', 1)[1].split("</div>", 1)[0]
    for value in ("Active", "Low Stock", "Finished"):
        assert f"""data-args='[{row_id},"{value}"]'""" in actions, value
    assert f"""data-action="deleteSupplement" data-args='[{row_id}]'""" in actions
    assert "<a " not in actions


def test_private_supplements_stay_editable_and_keep_their_visibility(
    app, client, make_user, login,
):
    from app.extensions import db
    from app.models import Supplement

    user = _login_user(client, make_user, login, "ux3pr5-private")
    row, = _add_supplements(user.id, {"product_name": "Private ZMA",
                                      "is_public": False})
    row_id = row.id

    assert "Private ZMA" in client.get("/supplements").get_data(as_text=True)

    # An edit that does not mention visibility must not silently republish it.
    assert client.post(f"/supplement/edit/{row_id}",
                       json={"status": "Low Stock"}).status_code == 200
    refreshed = db.session.get(Supplement, row_id)
    assert refreshed.status == "Low Stock"
    assert refreshed.is_public is False

    # And the flag itself is still persisted, in BOTH directions, by the one
    # authority — PR5 changes placement, never what `is_public` means.
    assert client.post(f"/supplement/edit/{row_id}",
                       json={"is_public": True}).status_code == 200
    assert db.session.get(Supplement, row_id).is_public is True
    assert client.post(f"/supplement/edit/{row_id}",
                       json={"is_public": False}).status_code == 200
    assert db.session.get(Supplement, row_id).is_public is False

    # A newly added supplement keeps the visibility it was created with.
    created = client.post("/supplement/add", json={
        "product_name": "Quiet", "brand": "B", "is_public": False})
    assert created.status_code == 200
    assert db.session.get(Supplement, created.get_json()["id"]).is_public is False


def test_plan_and_nutrition_never_publish_supplement_identity_or_price(
    app, client, make_user, login,
):
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    user = _login_user(client, make_user, login, "ux3pr5-privacy")
    row, = _add_supplements(user.id, {
        "product_name": "SecretStack", "price_paid": 1234.5,
        "review_text": "PRIVATE-REVIEW-TEXT", "is_public": False,
    })
    row_id = row.id

    for path in ("/training", "/nutrition"):
        html = client.get(path).get_data(as_text=True)
        assert "SecretStack" not in html, path
        assert "PRIVATE-REVIEW-TEXT" not in html, path
        assert "1234" not in html, path
        assert f"/supplement/edit/{row_id}" not in html, path


# ─────────────────────────────────────────────────────────────────────────────
# 5. MUTATION AUTHORITY + CONCURRENCY REGRESSION
# ─────────────────────────────────────────────────────────────────────────────

def test_canonical_add_edit_delete_still_run_through_the_one_authority(
    client, auth_user,
):
    from app.extensions import db
    from app.models import Supplement

    added = client.post("/supplement/add", json={
        "product_name": "Whey", "brand": "ON", "category": "Protein",
        "status": "Active", "rating_effect": 5, "price_paid": "750",
        "review_text": "solid", "is_public": False,
    })
    assert added.status_code == 200
    sid = added.get_json()["id"]

    row = db.session.get(Supplement, sid)
    assert (row.user_id, row.category, row.status) == (auth_user.id, "Protein", "Active")
    assert row.rating_effect == 5 and row.is_public is False

    assert client.post(f"/supplement/edit/{sid}",
                       json={"status": "Finished", "category": "Creatine"}).status_code == 200
    row = db.session.get(Supplement, sid)
    assert (row.status, row.category) == ("Finished", "Creatine")

    assert client.post(f"/supplement/delete/{sid}").status_code == 200
    assert db.session.get(Supplement, sid) is None


def test_first_supplement_bonus_is_still_serialized_to_exactly_one_award(
    app, client, auth_user,
):
    """PR5 adds entry points to this workflow, so the XP decision is re-proved."""
    from app.extensions import db
    from app.models import Supplement, User

    locked = []
    original = db.session.query

    def spy(*args, **kwargs):
        query = original(*args, **kwargs)
        if args and args[0] is User.id:
            real_with_for_update = query.with_for_update

            def record(*a, **k):
                locked.append(True)
                return real_with_for_update(*a, **k)

            query.with_for_update = record
        return query

    db.session.query = spy
    try:
        before = db.session.get(User, auth_user.id).rank_points or 0
        assert client.post("/supplement/add", json={
            "product_name": "First", "brand": "B"}).status_code == 200
        first_xp = db.session.get(User, auth_user.id).rank_points or 0
        assert client.post("/supplement/add", json={
            "product_name": "Second", "brand": "B"}).status_code == 200
        second_xp = db.session.get(User, auth_user.id).rank_points or 0
    finally:
        db.session.query = original

    assert first_xp - before == 25          # first supplement bonus, once
    assert second_xp == first_xp            # and never again
    assert Supplement.query.filter_by(user_id=auth_user.id).count() == 2
    # The decision is still taken under the owner-row lock, not a bare count.
    assert len(locked) == 2


def test_cabinet_reads_and_writes_stay_owner_scoped(client, make_user, login):
    from app.extensions import db
    from app.models import Supplement

    intruder = make_user("ux3pr5-intruder", profile_complete=True)
    victim = make_user("ux3pr5-victim", profile_complete=True)
    row = Supplement(user_id=victim.id, product_name="VictimStack", brand="B",
                     category="Protein", status="Active")
    db.session.add(row)
    db.session.commit()
    row_id = row.id
    login("ux3pr5-intruder")

    assert "VictimStack" not in client.get("/supplements").get_data(as_text=True)
    assert "VictimStack" not in client.get("/edit-profile").get_data(as_text=True)
    assert client.post(f"/supplement/edit/{row_id}",
                       json={"status": "Finished"}).status_code == 404
    assert client.post(f"/supplement/delete/{row_id}").status_code == 404
    assert db.session.get(Supplement, row_id).status == "Active"
    assert intruder.id != victim.id


def test_supplement_writes_require_authentication(client):
    for path in ("/supplement/add", "/supplement/edit/1", "/supplement/delete/1"):
        assert client.post(path, json={}).status_code in (302, 401), path
    assert client.get("/supplements").status_code in (302, 401)


# ─────────────────────────────────────────────────────────────────────────────
# 6. QUERY BUDGET
# ─────────────────────────────────────────────────────────────────────────────

def _selects(app, client, path):
    from app.extensions import db

    statements = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        text = " ".join(statement.lower().split())
        if text.startswith("select"):
            statements.append(text)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        response = client.get(path)
    finally:
        event.remove(db.engine, "before_cursor_execute", record)
    return response, statements


@pytest.mark.parametrize("path,expected_supplement_selects", [
    ("/nutrition", 0),      # placement is a link; it costs nothing
    ("/supplements", 1),    # the canonical bounded cabinet list
    ("/edit-profile", 1),   # the projection that already existed
    ("/training", 1),       # the bounded COUNT PR4 introduced
])
def test_placement_adds_no_supplement_query_anywhere(
    app, client, make_user, login, path, expected_supplement_selects,
):
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    user = _login_user(
        client, make_user, login, f"ux3pr5-budget-{path.strip('/')}")
    _add_supplements(user.id, *[{"product_name": f"S{i}"} for i in range(3)])

    response, statements = _selects(app, client, path)
    supplement_reads = [s for s in statements if "from supplement" in s]

    assert response.status_code == 200
    assert len(supplement_reads) == expected_supplement_selects, supplement_reads
    # No N+1: never one query per row.
    assert len(supplement_reads) < 3


@pytest.mark.parametrize("sessions_enabled,expected", [(False, 7), (True, 9)])
def test_plan_fact_gathering_budget_is_unchanged_by_pr5(
    app, make_user, sessions_enabled, expected,
):
    from app.extensions import db
    from app.models import TrainingPlan
    from app.services.plan_facts import gather_plan_facts

    app.config["UIUX_PLAN_V2_ENABLED"] = True
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = sessions_enabled
    user = make_user(f"ux3pr5-planbudget-{int(sessions_enabled)}",
                     profile_complete=True)
    user_id = user.id
    db.session.add(TrainingPlan(user_id=user_id, plan_data=json.dumps(
        [{"gun": "Pazartesi", "tip": "dinlenme", "egzersizler": []}])))
    db.session.commit()

    statements = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        text = " ".join(statement.lower().split())
        if text.startswith("select"):
            statements.append(text)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        facts = gather_plan_facts(user_id, sessions_enabled=sessions_enabled)
    finally:
        event.remove(db.engine, "before_cursor_execute", record)

    assert facts.read_ok is True
    assert len(statements) == expected, statements
    assert sum("from supplement" in s for s in statements) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 7. FLAG, LOCALE AND PR3/PR4 REGRESSION
# ─────────────────────────────────────────────────────────────────────────────

def test_plan_v2_off_keeps_legacy_training_and_a_stable_cabinet(
    app, client, make_user, login,
):
    app.config["UIUX_PLAN_V2_ENABLED"] = False
    _login_user(client, make_user, login, "ux3pr5-flagoff")

    legacy = client.get("/training")
    assert legacy.status_code == 200
    legacy_html = legacy.get_data(as_text=True)
    assert "data-plan-v2" not in legacy_html
    assert "/static/training.js" in legacy_html

    cabinet = client.get("/supplements")
    assert cabinet.status_code == 200
    assert 'class="stack-parent-context"' in cabinet.get_data(as_text=True)
    assert 'class="nutrition-child-domain"' in client.get(
        "/nutrition").get_data(as_text=True)


def test_pr5_locale_keys_exist_and_are_translated_in_both_catalogs():
    from app.i18n import reload_catalog

    catalog = reload_catalog()
    keys = {
        "supplements.parent.aria",
        "supplements.parent.plan",
        "supplements.parent.nutrition",
        "supplements.parent.current",
        "nutrition.child.aria",
        "nutrition.child.kicker",
        "nutrition.child.supplements",
        "plan.supplements.empty",
        "plan.supplements.unavailable",
        "editprofile.stack_context",
        "editprofile.stack_manage",
        "manage_stack.delete_confirm",
    }
    for locale in ("tr", "en"):
        assert keys <= catalog[locale].keys(), locale
        assert all(catalog[locale][key] != key for key in keys)
        assert all(catalog[locale][key].strip() for key in keys)

    # EN and TR must actually differ where the copy is prose, not a proper noun.
    for key in ("nutrition.child.kicker", "plan.supplements.empty",
                "editprofile.stack_manage"):
        assert catalog["tr"][key] != catalog["en"][key], key

    # Keys PR5 orphaned are gone from BOTH catalogs, not just one.
    for retired in ("editprofile.edit_stack", "editprofile.add_first_supp",
                    "plan.supplements.summary"):
        assert retired not in catalog["tr"], retired
        assert retired not in catalog["en"], retired


def test_supplements_page_localizes_without_hardcoded_strings_in_its_script(
    app, client, make_user, login,
):
    """The delete confirmation used to be a hardcoded Turkish literal.

    The catalog is injected into every page, so the proof is in the page's own
    inline script: it must ASK for the key, and must not carry the literal.
    """
    source = (ROOT / "templates" / "manage_stack.html").read_text(encoding="utf-8")
    assert "__t('manage_stack.delete_confirm')" in source
    assert "Bu supplement silinsin mi?" not in source

    _login_user(client, make_user, login, "ux3pr5-locale-en", language="en")
    english = client.get("/supplements").get_data(as_text=True)
    assert '"manage_stack.delete_confirm": "Delete this supplement?"' in english
    # The parent chain is rendered copy, not just catalog data.
    assert "Nutrition" in _rendered_body(english)

    _login_user(client, make_user, login, "ux3pr5-locale-tr", language="tr")
    turkish = _rendered_body(client.get("/supplements").get_data(as_text=True))
    assert "Beslenme" in turkish and "Takviyeler" in turkish


def test_pr4_nutrition_placement_survives_pr5(app, client, make_user, login):
    """PR5 must not disturb the Plan → Nutrition summary PR4 established."""
    from app.extensions import db
    from app.models import MealLog, NutritionPlan, UserSession
    from app.timeutil import app_today

    app.config["UIUX_PLAN_V2_ENABLED"] = True
    user = _login_user(client, make_user, login, "ux3pr5-pr4")
    db.session.add(UserSession(user_id=user.id, target_calories=2000))
    db.session.add(MealLog(user_id=user.id, ogun="Kahvaltı", yemekler="Yulaf",
                           kalori=625, tarih=app_today().isoformat()))
    db.session.add(NutritionPlan(user_id=user.id,
                                 plan_data=json.dumps({"isim": "Plan A"})))
    db.session.commit()

    html = client.get("/training").get_data(as_text=True)
    nutrition = html.split('data-plan-domain="nutrition"', 1)[1]

    assert "625" in nutrition and "2000" in nutrition
    assert "Nutrition plan saved" in nutrition
    assert nutrition.count('href="/nutrition"') == 1
    assert "/meal-log/today" not in html
    assert "/static/nutrition.js" not in html

    nutrition_html = client.get("/nutrition").get_data(as_text=True)
    assert 'class="nutrition-parent-context"' in nutrition_html
    assert nutrition_html.count('role="tab"') == 5
