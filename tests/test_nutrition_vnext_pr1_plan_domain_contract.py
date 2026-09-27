"""PR1: the Plan overview presents Training and Nutrition as peers."""

from html.parser import HTMLParser
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


class PlanStructure(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.domains = {}
        self.headings = {}
        self.overview = None
        self.detail = None
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if "plan-domain-overview" in classes:
            self.overview = tuple(self.stack)
        if "plan-training-detail" in classes:
            self.detail = tuple(self.stack)
        if attrs.get("data-plan-domain"):
            self.domains[attrs["data-plan-domain"]] = tuple(self.stack)
        if attrs.get("id", "").startswith("plan-") and tag in {"h1", "h2", "h3"}:
            self.headings[attrs["id"]] = tag
        if tag == "a":
            self.links.append((attrs.get("href"), tuple(self.stack)))
        if tag not in self.VOID:
            self.stack.append((tag, attrs.get("class", ""), attrs.get("data-plan-domain")))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return


@pytest.mark.parametrize("language", ["en", "tr"])
def test_plan_domains_are_siblings_before_training_detail(
    client, make_user, login, language,
):
    name = f"pr1-{language}"
    make_user(name, profile_complete=True, language=language)
    login(name)
    html = client.get("/training").get_data(as_text=True)
    tree = PlanStructure()
    tree.feed(html)

    training = tree.domains["training"]
    nutrition = tree.domains["nutrition"]
    supplements = tree.domains["supplements"]
    assert training == nutrition
    assert any("plan-domain-overview" in node[1].split() for node in training)
    assert not any(node[2] == "training" for node in nutrition)
    assert any(node[2] == "nutrition" for node in supplements)
    assert tree.headings["plan-training-label"] == tree.headings["plan-nutrition-label"] == "h2"
    assert tree.headings["plan-supplements-label"] == "h3"
    assert html.index('data-plan-domain="nutrition"') < html.index('class="plan-training-detail"')
    assert sum(href == "/nutrition" for href, _ in tree.links) == 1
    assert sum(href == "/supplements" for href, _ in tree.links) == 1
    assert sum(href == "#plan-training-detail" for href, _ in tree.links) == 1
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    training_section = soup.select_one('[data-plan-domain="training"]')
    assert training_section.select_one("#plan-workout-error") is not None


def test_desktop_overview_does_not_restore_narrow_nutrition_rail():
    css = (ROOT / "static/plan.css").read_text(encoding="utf-8")
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css
    assert "grid-template-columns: minmax(0, 1fr) 320px" not in css
    assert "plan-training-detail" in css


def test_plan_copy_describes_user_state_not_architecture():
    import json

    for language in ("en", "tr"):
        catalog = json.loads((ROOT / f"locales/{language}.json").read_text(encoding="utf-8"))
        template = (ROOT / "templates/plan.html").read_text(encoding="utf-8")
        assert "plan.domain.primary" not in template
        assert "plan.domain.child" not in template
        assert "plan.domain_state.available" not in template
        assert "plan.status.active_plan" not in template
        assert catalog["plan.training.active"]
        assert catalog["plan.training.unavailable"]


def test_hierarchy_contract_rejects_controlled_regressions(client, make_user, login):
    """Each mutation damages one semantic relationship in rendered markup."""
    from bs4 import BeautifulSoup

    make_user("pr1-mutations", profile_complete=True, language="en")
    login("pr1-mutations")
    original = client.get("/training").get_data(as_text=True)

    def is_valid(markup):
        soup = BeautifulSoup(markup, "html.parser")
        overview = soup.select_one(".plan-domain-overview")
        training = overview.select_one('[data-plan-domain="training"]')
        nutrition = overview.select_one('[data-plan-domain="nutrition"]')
        supplements = soup.select_one('[data-plan-domain="supplements"]')
        return all((
            training.parent is overview,
            nutrition.parent is overview,
            supplements.find_parent(attrs={"data-plan-domain": "nutrition"}) is nutrition,
            training.select_one("#plan-training-label").name == "h2",
            nutrition.select_one("#plan-nutrition-label").name == "h2",
            supplements.select_one("#plan-supplements-label").name == "h3",
            len(nutrition.select('a[href="/nutrition"]')) == 1,
        ))

    assert is_valid(original)

    def mutate(change):
        soup = BeautifulSoup(original, "html.parser")
        overview = soup.select_one(".plan-domain-overview")
        training = overview.select_one('[data-plan-domain="training"]')
        nutrition = overview.select_one('[data-plan-domain="nutrition"]')
        supplements = nutrition.select_one('[data-plan-domain="supplements"]')
        change(overview, training, nutrition, supplements)
        return str(soup)

    # Nutrition nested under Training; heading demoted; Supplements promoted;
    # Nutrition CTA removed. All four must be rejected by the same contract.
    changes = (
        lambda o, t, n, s: t.append(n.extract()),
        lambda o, t, n, s: setattr(n.select_one("#plan-nutrition-label"), "name", "h3"),
        lambda o, t, n, s: o.append(s.extract()),
        lambda o, t, n, s: n.select_one('a[href="/nutrition"]').decompose(),
    )
    for change in changes:
        assert not is_valid(mutate(change))


def test_layout_and_failure_contract_reject_in_memory_mutations():
    css = (ROOT / "static/plan.css").read_text(encoding="utf-8")
    equal = "grid-template-columns: repeat(2, minmax(0, 1fr))"
    rail = "grid-template-columns: minmax(0, 1fr) 320px"
    layout_valid = lambda source: equal in source and rail not in source
    assert layout_valid(css)
    mutated_css = css.replace(equal, rail)
    assert not layout_valid(mutated_css)

    template = (ROOT / "templates/plan.html").read_text(encoding="utf-8")
    unavailable = "t('plan.nutrition.intake_unavailable')"
    failure_valid = lambda source: unavailable in source
    assert failure_valid(template)
    mutated_template = template.replace(unavailable, "t('plan.nutrition.intake_only', consumed=0)")
    assert not failure_valid(mutated_template)
