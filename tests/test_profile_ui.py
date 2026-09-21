"""Profile page render tests (Phase 5 · Surface 3): structural anchors of the
redesigned shell, the Membership card in both is_premium states, the edit sheet,
the test-pinned hub destinations, and the canonical-tokens-only guard."""

import re

from app.extensions import db
from app.models import Supplement


def _html(client):
    r = client.get("/edit-profile")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_profile_structural_anchors(client, auth_user):
    html = _html(client)
    # hero + XP + membership + edit sheet
    assert 'class="pf-hero"' in html
    assert 'class="pbar-track"' in html
    assert 'class="pf-membership' in html
    assert 'id="edit-sheet"' in html
    assert 'role="dialog"' in html
    assert 'data-action="openEditSheet"' in html
    assert '<button type="button" class="pf-avatar"' in html
    assert html.count('<button type="button" class="pf-choice') == 4
    assert not re.search(r'<div class="pf-choice(?:\s|\")', html)
    # seçili durum yardımcı teknolojiye açık: her seçim düğmesi aria-pressed taşır
    choices = re.findall(r'<button type="button" class="(?:pf-choice|hub-lang-opt)[^>]*>', html)
    assert len(choices) == 6
    assert all(re.search(r'aria-pressed="(?:true|false)"', c) for c in choices)
    assert sum('aria-pressed="true"' in c for c in choices if 'setLang' in c) == 2
    assert not re.search(r'<button type="button" class="pf-choice[^>]*>\s*<div', html)
    # sheet form still carries the i18n-test-pinned pieces
    assert '["kilo verme"]' in html
    # static assets + no legacy token leak
    assert "/static/profile.js" in html
    assert "/static/profile.css" in html
    assert "--volt" not in html


def test_account_prioritizes_identity_and_has_one_page_primary(client, auth_user):
    html = _html(client)
    page = html.split('<!-- ── EDIT PROFILE SHEET', 1)[0]

    edit = re.search(r'<button[^>]*data-action="openEditSheet"[^>]*>', page)
    assert edit, "identity edit action is missing"
    assert "btn-volt" in edit.group(0)
    assert page.count("btn-volt") == 1
    for section in ("identity", "preferences", "subscription", "security"):
        assert f'data-account-section="{section}"' in page
    assert page.count("<h1") == 1
    assert page.count("<h2") >= 4


def test_account_presentation_uses_system_icons_and_localized_status(client, auth_user):
    db.session.add(Supplement(
        user_id=auth_user.id,
        product_name="Whey",
        brand="AxisAI",
        category="Protein",
        status="Active",
        rating_effect=4,
    ))
    db.session.commit()
    client.post("/set-language", json={"lang": "tr"})

    html = _html(client)
    visible = html.split("</head>", 1)[1]
    assert not re.search(r"[🔥🏃💪🇹🇷🇬🇧★☆📦]", visible)
    assert ">Aktif<" in visible
    assert ">Active<" not in visible
    assert 'aria-label="4 / 5"' in visible


def test_profile_script_url_is_versioned_so_avatar_fixes_reach_existing_clients(client, auth_user):
    html = _html(client)
    assert '/static/profile.js?v=' in html


def test_profile_hub_destinations_preserved(client, auth_user):
    html = _html(client)
    for href in ("/friends", "/feed", "/leaderboard", "/quests",
                 "/challenges", "/logout"):
        assert f'href="{href}" class="hub-link' in html, href
    assert 'href="/pump-check-gallery" class="hub-link' not in html
    assert 'href="/supplements" class="hub-link' not in html
    assert 'href="/premium" class="hub-link' not in html
    assert 'data-action="setLang"' in html
    assert 'href="/premium"' in html
    assert 'href="/supplements"' in html


def test_membership_free_shows_upgrade(client, auth_user):
    # fresh users are not premium: the membership card renders the upgrade CTA
    # (the exact inverse of the premium test below). We assert on the CTA markup,
    # not on resolved copy — _head.html dumps the whole i18n catalog into
    # window.I18N on every page, so every key *name* is present regardless.
    html = _html(client)
    assert 'class="btn-ghost pf-upgrade"' in html    # contextual commercial CTA
    assert 'href="/premium"' in html
    assert 'data-ga-event="premium_nav_click"' in html


def test_membership_premium_shows_badge_no_cta(client, auth_user):
    auth_user.is_premium = True
    db.session.commit()
    html = _html(client)
    assert 'class="badge badge-success' in html      # premium badge present
    assert 'class="btn-ghost pf-upgrade"' not in html
