"""Progress page render smoke test (Phase 5 Task 11): confirms the redesigned
shell renders its structural anchors (heatmap, insights, tabs, panels,
check-in sheet, static assets) and that no legacy `--volt` token leaks into
the rendered HTML (canonical-tokens-only guard, mirrors other page checks)."""


def test_progress_page_renders_structural_anchors(app, client, make_user, login):
    make_user("proguiuser")
    login("proguiuser")

    r = client.get("/progress-page")
    assert r.status_code == 200
    html = r.get_data(as_text=True)

    assert 'id="heatmap-grid"' in html
    assert 'id="insight-row"' in html
    assert 'data-action="switchTab"' in html
    for name in ("weight", "nutrition", "workout", "achievements"):
        assert f'id="tab-{name}"' in html
    assert 'id="checkin-sheet"' in html
    assert "/static/progress.js" in html
    assert "/static/progress.css" in html
    assert "--volt" not in html
