"""AxisAI UX-1 PR2 — kanonik gezinme sözleşmesi (app/nav.py) testleri.

Saf birim testleri: Flask uygulaması/DB gerektirmez. Sözleşmenin Product IA
dört-hedefli üretim modelini kodladığını doğrular.
"""
import pytest

from app import nav


def test_primary_order_is_today_plan_coach_progress():
    assert [d["id"] for d in nav.primary_destinations()] == [
        "today", "plan", "coach", "progress",
    ]


def test_primary_paths_are_canonical_repo_routes():
    paths = {d["id"]: d["path"] for d in nav.primary_destinations()}
    assert paths == {
        "today": "/",
        "plan": "/training",
        "coach": "/coach",
        "progress": "/progress-page",
    }


def test_primary_labels_are_i18n_keys_not_translated_text():
    for d in nav.primary_destinations():
        assert "." in d["label_key"]
        assert d["label_key"].startswith(("nav.", "gallery."))


def test_no_duplicate_destination_ids():
    ids = [d["id"] for d in nav.primary_destinations()]
    ids += [d["id"] for d in nav.secondary_destinations()]
    assert len(ids) == len(set(ids))


def test_nutrition_is_not_a_primary_destination():
    primary = {d["id"] for d in nav.primary_destinations()}
    secondary = {d["id"] for d in nav.secondary_destinations()}
    assert "nutrition" not in primary
    assert "nutrition" not in secondary


def test_profile_is_not_a_primary_destination():
    primary = {d["id"] for d in nav.primary_destinations()}
    assert "profile" not in primary


def test_community_destinations_are_secondary_not_primary():
    primary = {d["id"] for d in nav.primary_destinations()}
    secondary = {d["id"] for d in nav.secondary_destinations()}
    for cid in ("feed", "friends", "leaderboard", "quests", "challenges"):
        assert cid not in primary, cid
        assert cid in secondary, cid
    assert "gallery" not in primary
    assert "gallery" not in secondary


def test_utility_destinations_excluded_from_primary_tier():
    primary = {d["id"] for d in nav.primary_destinations()}
    by_id = {d["id"]: d for d in nav.secondary_destinations()}
    for uid in ("profile", "notifications", "logout"):
        assert uid not in primary, uid
        assert by_id[uid]["tier"] == "utility", uid


def test_resolve_active_maps_pages_and_domain_children_to_primary():
    r = nav.resolve_active
    assert r("today") == "today"
    assert r("home") == "today"
    assert r("plan") == "plan"
    assert r("training") == "plan"
    assert r("nutrition") == "plan"
    assert r("supplements") == "plan"
    assert r("coach") == "coach"
    assert r("progress") == "progress"
    assert r("gallery") == "progress"


def test_resolve_active_none_for_utility_community_account_and_unknown():
    r = nav.resolve_active
    assert r("profile") is None
    assert r("notifications") is None
    assert r("friends") is None
    assert r("feed") is None
    assert r("leaderboard") is None
    assert r("quests") is None
    assert r("challenges") is None
    assert r("premium") is None
    assert r(None) is None
    assert r("does-not-exist") is None


def test_contract_rows_are_read_only():
    with pytest.raises(TypeError):
        nav.primary_destinations()[0]["id"] = "hacked"


def test_every_row_has_required_presentational_keys():
    required = {"id", "label_key", "path", "icon", "tier", "active_when"}
    for d in list(nav.primary_destinations()) + list(nav.secondary_destinations()):
        assert required <= set(d.keys()), d["id"]
        assert d["path"].startswith("/"), d["id"]
