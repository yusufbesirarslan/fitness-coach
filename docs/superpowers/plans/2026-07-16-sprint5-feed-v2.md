# Sprint 5 PR 2 — Feed V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the PumpCheck-only feed into a modern social fitness feed — reposts (plain + quote), milestone cards, per-item likes/comments with delete, per-viewer moderation (hide/report), external share, cursor-based infinite scroll — without touching the existing PumpCheck like/comment/gallery/chat-share paths.

**Architecture:** A new `FeedItem` table holds ONLY new content (repost/quote); the feed is assembled at query time in a new `app/services/feed.py` that merges three keyset sources — feed-visible PumpChecks, FeedItems, and Activity milestones — from `friends ∪ self`, ranks chronologically (the algorithmic seam), and serializes per-kind. `FeedItem.ref_id` is a polymorphic seam (no FK); dangling refs render as an "unavailable" stub. Plain reposts surface the ORIGINAL pump check's engagement (+ a denormalized `PumpCheck.reposts_count`); quote reposts are their own likeable/commentable entity via `FeedItemLike`/`FeedItemComment`. Every serialized item carries `engagement.target = {type, id}` so the frontend picks like/comment endpoints generically. Moderation is per-viewer polymorphic (`FeedHide`/`FeedReport` over `pump_check|feed_item|activity`); report auto-hides.

**Tech Stack:** Flask, SQLAlchemy, Flask-Migrate (Alembic), pytest (hermetic in-memory SQLite), vanilla JS (data-action delegation, `window.t` i18n), CSP-nonced inline scripts.

## Global Constraints

- Türkçe UI, İngilizce kod. Canonical backend values stay English slugs (`item_type`, `ref_type`, `reason`, `ntype`, `target_type`); only display text is translated. Every new user-visible string → BOTH `locales/tr.json` AND `locales/en.json` (flat dotted keys). CI test `test_locale_key_parity_tr_en` blocks on drift.
- Migrations are additive / expand-only and **re-runnable** (fresh-DB boot re-runs them after `create_all`): gate every `create_table` with `sa.inspect(op.get_bind()).has_table(...)` and every `add_column` with a column-existence check. `down_revision` = current head `dd44ee55ff66`. Bump the hardcoded head in `tests/test_migration_graph.py`.
- Every new `user_id`-bearing model MUST be added to `_user_child_models()` in `app/cli.py` (children before parents, FK-safe order) or `tests/test_cascade_delete.py` fails.
- Routes: `@bp.route` → `@require_auth` → optional `@limiter.limit(CONST, key_func=_user_or_ip_key)`. Domain-key JSON (no success envelope); errors `{"error": t("...")}` with 400/403/404/429; `request.get_json(silent=True) or {}`; ownership via query-scoped filters. All state-changing routes are POST/DELETE (CSRF hook only guards write methods).
- Queries are user-scoped; never widen a pump check's audience via repost (friends-only/private are NOT repostable). `can_view_pump_check` is the single visibility authority.
- `notify(...)` and all feed writes are session-add-only pre-commit (atomic with the triggering action); NEVER touch Redis pre-commit. Notifications never raise.
- New page templates include `_head.html`; inline `<script>` carries `nonce="{{ csp_nonce }}"`; NO JS-injected `<style>` (progress bars via `style` attr are OK — CSP `style-src-attr 'unsafe-inline'`); per-page CSS in `static/*.css`.
- Run the suite with `pytest -m "not load"` (hermetic). Commit messages short (Turkish, matching repo style).

## Reference — verified current-code anchors (do NOT re-derive)

- `app/models.py`: `PumpCheck` @350 (has `likes_count`/`comments_count`/`visibility`/`shared_friend_ids`), `PumpCheckLike` @382 (`uq_pump_check_like_user`), `PumpCheckComment` @396 (`body` String(500) NOT NULL), `Friendship` @407, `Activity` @435 (`activity_type` String(30), `content` String(300), `timestamp`), `Notification` @704.
- `app/services/pump_checks.py`: `can_view_pump_check(user_id, check)` @16, `serialize_pump_check_card(check, viewer_id, include_viewer_state=True, liked_pump_check_ids=None, image_visibility_preauthorized=False)` @73 (re-exports `get_friend_ids`).
- `app/services/friends.py`: `get_friend_ids(user_id)`, `are_friends(a, b)`.
- `app/services/notifications.py`: `notify(user_id, ntype, actor_id=None, target_type=None, target_id=None, payload=None)`.
- `app/blueprints/social.py`: `feed_data` @124 (offset `{posts,hasMore,nextPage}` — feed.html is sole consumer), `_visible_pump_check_or_403` @166, `pump_check_like` @179, `pump_check_unlike` @202, `pump_check_comments` @221 (asc, no pagination/delete yet), `pump_check_comment_create` @243. Imports at @24. `notify` already imported.
- `app/blueprints/notifications.py`: keyset pagination + route conventions to mirror.
- `templates/feed.html`: single-kind card renderer + comments bottom-sheet; `esc()` XSS helper; `__t` i18n; scroll-based `loadFeed()` using `/feed/data?page=`.
- `app/cli.py`: `_user_child_models()` @84 (list to extend), `_purge_user` @115.
- `app/config.py`: rate-limit consts pattern (e.g. `FRIEND_REQUEST_RATELIMIT`), `_user_or_ip_key`.
- ACTIVITY_ICONS live in `app/blueprints/gamification.py` (milestone icons) — confirm at task time.

---

## File Structure

- **Create** `app/services/feed.py` — cursor codec, `get_feed_page` (collect/rank/serialize), `MILESTONE_ACTIVITY_TYPES`, hide/report helpers, `_visible_feed_item_or_403` support.
- **Create** `docs/FEED.md` — spine, milestones, cursor, ranking seam, engagement-target contract, moderation, share decision + repost-privacy rule.
- **Create** `tests/test_feed_v2.py`, `tests/test_repost_routes.py`, `tests/test_feed_moderation.py`.
- **Modify** `app/models.py` — 5 new models + `PumpCheck.reposts_count`.
- **Modify** `app/services/pump_checks.py` — additive `repostsCount` + `reposted_ref_ids` param on `serialize_pump_check_card`.
- **Modify** `app/blueprints/social.py` — new `/feed/data` shape; repost/quote/delete; feed-item like/comment/delete; pump-check comment pagination+delete; moderation routes; new rate-limit consts wiring.
- **Modify** `app/config.py` — `FEED_WRITE_RATELIMIT`, `FEED_REPORT_RATELIMIT`, `COMMENT_WRITE_RATELIMIT`.
- **Modify** `app/cli.py` — registry + `_purge_user` for new models + FeedItems referencing purged pump checks.
- **Modify** `app/blueprints/profile.py` — gallery delete also deletes referencing FeedItems + children.
- **Modify** `templates/feed.html` + `static/feed.css` — per-kind renderers, cursor loadFeed, optimistic UI, ⋯ menu, share, comments delete/load-older.
- **Modify** `templates/pump_check_gallery.html` — repostsCount + share on own cards.
- **Modify** `locales/tr.json` + `locales/en.json` — feed/notif/moderation keys.
- **Modify** `tests/test_migration_graph.py` — head bump.
- **Modify** `tests/test_social_routes.py` — comment-delete matrix + pagination.
- **Modify** `CLAUDE.md` — models, feed service, routes, docs pointer.

---

## Task 1: Models + migration + cascade registry

**Files:**
- Modify: `app/models.py` (after `PumpCheckComment` @404, and add `reposts_count` to `PumpCheck` @350)
- Modify: `app/cli.py:84-100` (`_user_child_models`) + `_purge_user`
- Create: `migrations/versions/<rev>_add_feed_v2.py`
- Modify: `tests/test_migration_graph.py:31`
- Test: `tests/test_feed_v2.py` (model-creation sanity only in this task)

**Interfaces:**
- Produces: `FeedItem(user_id, item_type, ref_type, ref_id, body, likes_count, comments_count, created_at)` + `uq_feed_item_user_ref`; `FeedItemLike(feed_item_id, user_id, created_at)` + `uq_feed_item_like_user`; `FeedItemComment(feed_item_id, user_id, body, created_at)`; `FeedHide(user_id, target_type, target_id, created_at)` + `uq_feed_hide_target`; `FeedReport(user_id, target_type, target_id, reason, note, status, created_at)` + `uq_feed_report_target`; `PumpCheck.reposts_count` (Integer NOT NULL server_default "0").

- [ ] **Step 1: Add models to `app/models.py`.** Add `reposts_count` to `PumpCheck` right after `comments_count` (@362):

```python
    reposts_count = db.Column(db.Integer, nullable=False, default=0, server_default="0")
```

Append after `PumpCheckComment` (@404):

```python
class FeedItem(db.Model):
    # Feed V2 omurgası (Sprint 5 PR2): SADECE yeni içerik türleri (repost/quote).
    # ref_id polimorfik tohum — FK YOK (ref_type ayırır); askıda kalan ref →
    # "içerik yok" stub. PumpCheck/Activity feed'e sorgu-zamanında katılır.
    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    item_type     = db.Column(db.String(20), nullable=False, index=True)   # 'repost' | 'quote'
    ref_type      = db.Column(db.String(20), nullable=False, default="pump_check", server_default="pump_check")
    ref_id        = db.Column(db.Integer, nullable=False, index=True)       # FK YOK — polimorfik
    body          = db.Column(db.String(500), nullable=True)                # quote metni
    likes_count   = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    comments_count = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    created_at    = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        db.UniqueConstraint("user_id", "item_type", "ref_type", "ref_id", name="uq_feed_item_user_ref"),
    )

    user = db.relationship("User", backref=db.backref("feed_items", passive_deletes=True))


class FeedItemLike(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    feed_item_id = db.Column(db.Integer, db.ForeignKey("feed_item.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id      = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        db.UniqueConstraint("feed_item_id", "user_id", name="uq_feed_item_like_user"),
    )

    feed_item = db.relationship("FeedItem", backref=db.backref("likes", passive_deletes=True))
    user = db.relationship("User", backref=db.backref("feed_item_likes", passive_deletes=True))


class FeedItemComment(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    feed_item_id = db.Column(db.Integer, db.ForeignKey("feed_item.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id      = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    body         = db.Column(db.String(500), nullable=False)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    feed_item = db.relationship("FeedItem", backref=db.backref("comments", passive_deletes=True))
    user = db.relationship("User", backref=db.backref("feed_item_comments", passive_deletes=True))


class FeedHide(db.Model):
    # Görüntüleyen-başı gizleme (polimorfik): pump_check | feed_item | activity.
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    target_type = db.Column(db.String(20), nullable=False)
    target_id   = db.Column(db.Integer, nullable=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id", "target_type", "target_id", name="uq_feed_hide_target"),
    )

    user = db.relationship("User", backref=db.backref("feed_hides", passive_deletes=True))


class FeedReport(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)   # şikayetçi
    target_type = db.Column(db.String(20), nullable=False)
    target_id   = db.Column(db.Integer, nullable=False)
    reason      = db.Column(db.String(30), nullable=False)   # 'spam' | 'inappropriate' | 'other'
    note        = db.Column(db.String(300), nullable=True)
    status      = db.Column(db.String(15), nullable=False, default="open", server_default="open")
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id", "target_type", "target_id", name="uq_feed_report_target"),
    )

    user = db.relationship("User", backref=db.backref("feed_reports", passive_deletes=True))
```

- [ ] **Step 2: Register in `_user_child_models()` (`app/cli.py`).** Add to the import block and the returned tuple. Order: children (likes/comments) before `FeedItem`; `FeedHide`/`FeedReport` anywhere before `User`. Add `FeedHide, FeedItem, FeedItemComment, FeedItemLike, FeedReport` to the import; append to the tuple in FK-safe order — put `FeedItemLike, FeedItemComment` before `FeedItem`, and `FeedHide, FeedReport` after:

```python
        FeedItemLike, FeedItemComment, FeedItem, FeedHide, FeedReport, Notification,
```

(insert immediately before `Notification,` in the returned tuple; add the same names to the `from app.models import (...)` list, keeping alphabetical grouping loose.)

- [ ] **Step 3: Extend `_purge_user`.** After the existing child-loop delete, add explicit cleanup of FeedItems that *reference* the purged user's pump checks (their `ref_id` points at rows about to vanish) and the children of those FeedItems. Read `_purge_user` first (@115) to match its transaction style; add before the final user delete:

```python
        # Purged kullanıcının pump check'lerine ATIFTA BULUNAN başkalarının repost'ları
        # (ref_id FK'sız) — askıda kalmasın diye açıkça sil (önce çocukları).
        from app.models import PumpCheck, FeedItem, FeedItemLike, FeedItemComment
        orphan_ref_ids = [pc_id for (pc_id,) in db.session.query(PumpCheck.id).filter(PumpCheck.user_id == user.id).all()]
        if orphan_ref_ids:
            orphan_items = db.session.query(FeedItem.id).filter(
                FeedItem.ref_type == "pump_check", FeedItem.ref_id.in_(orphan_ref_ids)
            ).all()
            orphan_item_ids = [i for (i,) in orphan_items]
            if orphan_item_ids:
                FeedItemLike.query.filter(FeedItemLike.feed_item_id.in_(orphan_item_ids)).delete(synchronize_session=False)
                FeedItemComment.query.filter(FeedItemComment.feed_item_id.in_(orphan_item_ids)).delete(synchronize_session=False)
                FeedItem.query.filter(FeedItem.id.in_(orphan_item_ids)).delete(synchronize_session=False)
```

(The user's OWN FeedItems/likes/comments/hides/reports are deleted by the registry loop already; this block only covers cross-user reposts of the purged user's content.)

- [ ] **Step 4: Generate the migration.** Run:

```bash
FITX_SKIP_DB_INIT=1 flask --app starter db migrate -m "add feed v2"
```

Expected: a new file `migrations/versions/<rev>_add_feed_v2.py` with `create_table` for 5 tables + `add_column` for `pump_check.reposts_count`.

- [ ] **Step 5: Hand-edit the migration to be re-runnable.** Set `down_revision = "dd44ee55ff66"`. Wrap the body so it is idempotent (fresh-DB boot re-runs it post-`create_all`). Replace `upgrade()`:

```python
def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("feed_item"):
        op.create_table(
            "feed_item",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("item_type", sa.String(length=20), nullable=False),
            sa.Column("ref_type", sa.String(length=20), server_default="pump_check", nullable=False),
            sa.Column("ref_id", sa.Integer(), nullable=False),
            sa.Column("body", sa.String(length=500), nullable=True),
            sa.Column("likes_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("comments_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "item_type", "ref_type", "ref_id", name="uq_feed_item_user_ref"),
        )
        op.create_index("ix_feed_item_user_id", "feed_item", ["user_id"])
        op.create_index("ix_feed_item_item_type", "feed_item", ["item_type"])
        op.create_index("ix_feed_item_ref_id", "feed_item", ["ref_id"])
        op.create_index("ix_feed_item_created_at", "feed_item", ["created_at"])

    if not insp.has_table("feed_item_like"):
        op.create_table(
            "feed_item_like",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("feed_item_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["feed_item_id"], ["feed_item.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("feed_item_id", "user_id", name="uq_feed_item_like_user"),
        )
        op.create_index("ix_feed_item_like_feed_item_id", "feed_item_like", ["feed_item_id"])
        op.create_index("ix_feed_item_like_user_id", "feed_item_like", ["user_id"])
        op.create_index("ix_feed_item_like_created_at", "feed_item_like", ["created_at"])

    if not insp.has_table("feed_item_comment"):
        op.create_table(
            "feed_item_comment",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("feed_item_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("body", sa.String(length=500), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["feed_item_id"], ["feed_item.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_feed_item_comment_feed_item_id", "feed_item_comment", ["feed_item_id"])
        op.create_index("ix_feed_item_comment_user_id", "feed_item_comment", ["user_id"])
        op.create_index("ix_feed_item_comment_created_at", "feed_item_comment", ["created_at"])

    if not insp.has_table("feed_hide"):
        op.create_table(
            "feed_hide",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("target_type", sa.String(length=20), nullable=False),
            sa.Column("target_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "target_type", "target_id", name="uq_feed_hide_target"),
        )
        op.create_index("ix_feed_hide_user_id", "feed_hide", ["user_id"])

    if not insp.has_table("feed_report"):
        op.create_table(
            "feed_report",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("target_type", sa.String(length=20), nullable=False),
            sa.Column("target_id", sa.Integer(), nullable=False),
            sa.Column("reason", sa.String(length=30), nullable=False),
            sa.Column("note", sa.String(length=300), nullable=True),
            sa.Column("status", sa.String(length=15), server_default="open", nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "target_type", "target_id", name="uq_feed_report_target"),
        )
        op.create_index("ix_feed_report_user_id", "feed_report", ["user_id"])

    cols = {c["name"] for c in insp.get_columns("pump_check")}
    if "reposts_count" not in cols:
        op.add_column("pump_check", sa.Column("reposts_count", sa.Integer(), server_default="0", nullable=False))
```

And make `downgrade()` best-effort (drop column + tables in reverse). Confirm the generated `revision` id, then update the head test.

- [ ] **Step 6: Bump migration-graph head test.** In `tests/test_migration_graph.py:31`:

```python
    assert heads == ["<new-rev-id>"]
```

- [ ] **Step 7: Write the model sanity test** in `tests/test_feed_v2.py`:

```python
from app.extensions import db
from app.models import FeedItem, PumpCheck


def test_feed_item_and_reposts_count_persist(auth_user, app):
    with app.app_context():
        pc = PumpCheck(user_id=auth_user.id, visibility="feed", date_key="2026-07-16")
        db.session.add(pc)
        db.session.commit()
        item = FeedItem(user_id=auth_user.id, item_type="repost", ref_type="pump_check", ref_id=pc.id)
        db.session.add(item)
        PumpCheck.query.filter_by(id=pc.id).update({PumpCheck.reposts_count: PumpCheck.reposts_count + 1})
        db.session.commit()
        assert db.session.get(PumpCheck, pc.id).reposts_count == 1
        assert db.session.get(FeedItem, item.id).item_type == "repost"
```

(Check `tests/conftest.py` for the exact `auth_user`/`app` fixture names; PR1 tests use `auth_user`. Adjust if the app-context fixture differs.)

- [ ] **Step 8: Run tests.**

```bash
pytest tests/test_feed_v2.py tests/test_migration_graph.py tests/test_cascade_delete.py -v -m "not load"
```

Expected: PASS (cascade test now sees the new models in the registry; migration head matches).

- [ ] **Step 9: Commit.**

```bash
git add app/models.py app/cli.py migrations/versions/ tests/test_feed_v2.py tests/test_migration_graph.py
git commit -m "feat(feed): feed v2 modelleri + migration (Sprint 5 PR2)"
```

---

## Task 2: Feed service — cursor codec + `serialize_pump_check_card` additive fields

**Files:**
- Create: `app/services/feed.py`
- Modify: `app/services/pump_checks.py:73-111` (`serialize_pump_check_card`)
- Test: `tests/test_feed_v2.py`

**Interfaces:**
- Consumes: `get_friend_ids`, `can_view_pump_check`, `serialize_pump_check_card`, models from Task 1.
- Produces: `encode_cursor(created_at, source, id) -> str`; `decode_cursor(str) -> (datetime, str, int) | None`; `MILESTONE_ACTIVITY_TYPES`; `SOURCE_RANK`; `get_feed_page(viewer_id, cursor=None, limit=10) -> {"items": [...], "hasMore": bool, "nextCursor": str|None}`; serialized item shape `{kind, id, createdAt, engagement:{target:{type,id}}|None, ...}`.

- [ ] **Step 1: Additive `serialize_pump_check_card` change.** Add `reposted_ref_ids=None` param (default) and two output keys. Modify the signature (@73) and the returned dict:

```python
def serialize_pump_check_card(
    check,
    viewer_id,
    include_viewer_state=True,
    liked_pump_check_ids=None,
    image_visibility_preauthorized=False,
    reposted_ref_ids=None,
):
    ...
    return {
        ...
        "likedByMe": liked,
        "repostsCount": check.reposts_count or 0,
        "repostedByMe": (reposted_ref_ids is not None and check.id in reposted_ref_ids),
    }
```

Chat consumers pass no `reposted_ref_ids` → `repostedByMe` is `False`, `repostsCount` reflects the column. Additive; no caller breaks.

- [ ] **Step 2: Write failing cursor codec test** in `tests/test_feed_v2.py`:

```python
from datetime import datetime
from app.services import feed as feed_svc


def test_cursor_roundtrip_and_garbage():
    dt = datetime(2026, 7, 16, 12, 30, 0)
    cur = feed_svc.encode_cursor(dt, "pump_check", 42)
    got = feed_svc.decode_cursor(cur)
    assert got == (dt, "pump_check", 42)
    assert feed_svc.decode_cursor("!!!not-base64!!!") is None
    assert feed_svc.decode_cursor("") is None
    assert feed_svc.decode_cursor(None) is None
```

- [ ] **Step 3: Run — expect ImportError/fail.**

```bash
pytest tests/test_feed_v2.py::test_cursor_roundtrip_and_garbage -v
```

- [ ] **Step 4: Create `app/services/feed.py` with codec + collect/rank/serialize.** Full file:

```python
# Feed V2 servisi (Sprint 5 PR2). Feed'i sorgu-zamanında ÜÇ keyset kaynağından
# birleştirir: feed-görünür PumpCheck'ler, FeedItem'lar (repost/quote), Activity
# kilometre taşları — hepsi (arkadaşlar ∪ kendisi) kapsamından. Kronolojik sıralama
# (algoritmik dikiş: _rank) + kind-başı serileştirme. Görüntüleyen-başı gizli
# satırlar düşülür (FeedHide; FeedReport auto-hide zaten FeedHide yazar).
import base64
import logging

from app.extensions import db
from app.models import (
    Activity, FeedHide, FeedItem, FeedItemLike, PumpCheck, PumpCheckLike,
)
from app.services.friends import get_friend_ids
from app.services.pump_checks import can_view_pump_check, serialize_pump_check_card
from app.timeutil import display_dt

log = logging.getLogger(__name__)

# Feed'de gösterilen kilometre taşı activity türleri (allowlist; gürültü hariç).
# PR3'te 'challenge_completed' eklenecek.
MILESTONE_ACTIVITY_TYPES = ("level_up", "streak_milestone", "new_friend")

# Eşit created_at'te kaynak-arası deterministik kopmak için sabit sıra (DESC).
SOURCE_RANK = {"pump_check": 3, "feed_item": 2, "activity": 1}

# Milestone ikonları (gamification.ACTIVITY_ICONS ile hizalı; feed-yerel kopya).
MILESTONE_ICONS = {
    "level_up": "⭐",
    "streak_milestone": "🔥",
    "new_friend": "🤝",
}


def encode_cursor(created_at, source, item_id):
    raw = "%s|%s|%s" % (created_at.isoformat(), source, int(item_id))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor):
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        iso, source, item_id = raw.split("|")
        from datetime import datetime
        return (datetime.fromisoformat(iso), source, int(item_id))
    except Exception:
        return None


def _keyset_filter(created_at_col, id_col, cursor, source):
    """Bu kaynak için '(created_at, source_rank, id) < cursor' DESC-sonrası filtresi."""
    if cursor is None:
        return None
    ct, _c_source, cid = cursor
    c_rank = SOURCE_RANK.get(_c_source, 0)
    s_rank = SOURCE_RANK.get(source, 0)
    if s_rank < c_rank:
        return db.or_(created_at_col < ct, created_at_col == ct)
    if s_rank == c_rank:
        return db.or_(created_at_col < ct, db.and_(created_at_col == ct, id_col < cid))
    return created_at_col < ct


def _sort_key(candidate):
    # candidate: (created_at, source, id, obj) — DESC sıralama için negatif yok;
    # reverse=True ile en yeni önce.
    ca, source, cid, _obj = candidate
    return (ca, SOURCE_RANK.get(source, 0), cid)


def get_feed_page(viewer_id, cursor=None, limit=10):
    limit = max(1, min(int(limit or 10), 30))
    decoded = decode_cursor(cursor)
    visible_ids = get_friend_ids(viewer_id) | {viewer_id}

    # 1) COLLECT — her kaynaktan limit+1, keyset filtreli.
    candidates = []  # (created_at, source, id, obj)

    pc_q = PumpCheck.query.filter(
        PumpCheck.visibility == "feed", PumpCheck.user_id.in_(visible_ids),
    )
    f = _keyset_filter(PumpCheck.created_at, PumpCheck.id, decoded, "pump_check")
    if f is not None:
        pc_q = pc_q.filter(f)
    for pc in pc_q.order_by(PumpCheck.created_at.desc(), PumpCheck.id.desc()).limit(limit + 1).all():
        candidates.append((pc.created_at, "pump_check", pc.id, pc))

    fi_q = FeedItem.query.filter(FeedItem.user_id.in_(visible_ids))
    f = _keyset_filter(FeedItem.created_at, FeedItem.id, decoded, "feed_item")
    if f is not None:
        fi_q = fi_q.filter(f)
    for fi in fi_q.order_by(FeedItem.created_at.desc(), FeedItem.id.desc()).limit(limit + 1).all():
        candidates.append((fi.created_at, "feed_item", fi.id, fi))

    act_q = Activity.query.filter(
        Activity.user_id.in_(visible_ids),
        Activity.activity_type.in_(MILESTONE_ACTIVITY_TYPES),
    )
    f = _keyset_filter(Activity.timestamp, Activity.id, decoded, "activity")
    if f is not None:
        act_q = act_q.filter(f)
    for act in act_q.order_by(Activity.timestamp.desc(), Activity.id.desc()).limit(limit + 1).all():
        candidates.append((act.timestamp, "activity", act.id, act))

    # 2) hidden düş (görüntüleyen-başı).
    hidden = {
        (h.target_type, h.target_id)
        for h in FeedHide.query.filter(FeedHide.user_id == viewer_id).all()
    }
    if hidden:
        candidates = [c for c in candidates if (c[1], c[2]) not in hidden]

    # 3) RANK — kronolojik (algoritmik dikiş). En yeni önce.
    candidates.sort(key=_sort_key, reverse=True)
    page = candidates[:limit]
    has_more = len(candidates) > limit

    # 4) batch görüntüleyen-durumu.
    pc_ids = [c[2] for c in page if c[1] == "pump_check"]
    repost_items = [c[3] for c in page if c[1] == "feed_item" and c[3].item_type == "repost"]
    quote_items = [c[3] for c in page if c[1] == "feed_item" and c[3].item_type == "quote"]
    original_ref_ids = [fi.ref_id for fi in repost_items if fi.ref_type == "pump_check"]
    all_pc_ids = set(pc_ids) | set(original_ref_ids)

    liked_pc = set()
    if all_pc_ids:
        liked_pc = {r for (r,) in db.session.query(PumpCheckLike.pump_check_id).filter(
            PumpCheckLike.user_id == viewer_id, PumpCheckLike.pump_check_id.in_(all_pc_ids)).all()}
    liked_fi = set()
    fi_ids = [c[2] for c in page if c[1] == "feed_item"]
    if fi_ids:
        liked_fi = {r for (r,) in db.session.query(FeedItemLike.feed_item_id).filter(
            FeedItemLike.user_id == viewer_id, FeedItemLike.feed_item_id.in_(fi_ids)).all()}
    my_reposts = set()
    if all_pc_ids:
        my_reposts = {r for (r,) in db.session.query(FeedItem.ref_id).filter(
            FeedItem.user_id == viewer_id, FeedItem.item_type == "repost",
            FeedItem.ref_type == "pump_check", FeedItem.ref_id.in_(all_pc_ids)).all()}
    originals = {}
    if original_ref_ids:
        for pc in PumpCheck.query.options(db.joinedload(PumpCheck.user)).filter(PumpCheck.id.in_(original_ref_ids)).all():
            originals[pc.id] = pc

    # 5) SERIALIZE — kind-başı registry.
    items = []
    for ca, source, cid, obj in page:
        if source == "pump_check":
            items.append(_serialize_pump_check(obj, viewer_id, liked_pc, my_reposts))
        elif source == "feed_item":
            if obj.item_type == "repost":
                items.append(_serialize_repost(obj, viewer_id, originals, liked_pc, my_reposts))
            else:
                items.append(_serialize_quote(obj, viewer_id, liked_fi, originals))
        else:
            items.append(_serialize_milestone(obj))

    next_cursor = None
    if has_more and page:
        ca, source, cid, _obj = page[-1]
        next_cursor = encode_cursor(ca, source, cid)
    return {"items": items, "hasMore": has_more, "nextCursor": next_cursor}


def _serialize_pump_check(pc, viewer_id, liked_pc, my_reposts):
    card = serialize_pump_check_card(
        pc, viewer_id, liked_pump_check_ids=liked_pc,
        image_visibility_preauthorized=True, reposted_ref_ids=my_reposts,
    )
    card["kind"] = "pump_check"
    card["engagement"] = {"target": {"type": "pump_check", "id": pc.id}}
    return card


def _serialize_repost(fi, viewer_id, originals, liked_pc, my_reposts):
    base = {
        "kind": "repost",
        "id": fi.id,
        "createdAt": fi.created_at.isoformat() if fi.created_at else None,
        "timePosted": display_dt(fi.created_at, "%d.%m.%Y %H:%M"),
        "reposter": {
            "userId": fi.user_id,
            "username": fi.user.username if fi.user else "",
            "userAvatar": fi.user.avatar_src if fi.user else None,
        },
    }
    original = originals.get(fi.ref_id) if fi.ref_type == "pump_check" else None
    if original is not None and can_view_pump_check(viewer_id, original):
        card = serialize_pump_check_card(
            original, viewer_id, liked_pump_check_ids=liked_pc,
            image_visibility_preauthorized=True, reposted_ref_ids=my_reposts,
        )
        base["original"] = card
        base["engagement"] = {"target": {"type": "pump_check", "id": original.id}}
        base["unavailable"] = False
    else:
        base["original"] = None
        base["engagement"] = None
        base["unavailable"] = True
    return base


def _serialize_quote(fi, viewer_id, liked_fi, originals):
    original = None
    ref = originals.get(fi.ref_id) if fi.ref_type == "pump_check" else None
    if ref is not None and can_view_pump_check(viewer_id, ref):
        original = serialize_pump_check_card(ref, viewer_id, image_visibility_preauthorized=True)
    return {
        "kind": "quote",
        "id": fi.id,
        "createdAt": fi.created_at.isoformat() if fi.created_at else None,
        "timePosted": display_dt(fi.created_at, "%d.%m.%Y %H:%M"),
        "author": {
            "userId": fi.user_id,
            "username": fi.user.username if fi.user else "",
            "userAvatar": fi.user.avatar_src if fi.user else None,
        },
        "body": fi.body or "",
        "likesCount": fi.likes_count or 0,
        "commentsCount": fi.comments_count or 0,
        "likedByMe": fi.id in liked_fi,
        "original": original,
        "engagement": {"target": {"type": "feed_item", "id": fi.id}},
    }


def _serialize_milestone(act):
    return {
        "kind": "milestone",
        "id": act.id,
        "createdAt": act.timestamp.isoformat() if act.timestamp else None,
        "timePosted": display_dt(act.timestamp, "%d.%m.%Y %H:%M"),
        "userId": act.user_id,
        "activityType": act.activity_type,
        "icon": MILESTONE_ICONS.get(act.activity_type, "🏅"),
        "content": act.content,
        "engagement": None,
    }
```

Note: `_serialize_quote` loads its original only if in `originals`; extend the batch loader to also gather quote `ref_id`s. Update the batch block: build `original_ref_ids` from BOTH repost and quote items (`for fi in repost_items + quote_items if fi.ref_type == "pump_check"`).

- [ ] **Step 5: Run codec + a collect smoke test.**

```bash
pytest tests/test_feed_v2.py::test_cursor_roundtrip_and_garbage -v
```

Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add app/services/feed.py app/services/pump_checks.py tests/test_feed_v2.py
git commit -m "feat(feed): feed servisi (keyset merge + kind serileştirme)"
```

---

## Task 3: `/feed/data` cursor shape + feed-merge/visibility tests

**Files:**
- Modify: `app/blueprints/social.py:124-163` (`feed_data`)
- Test: `tests/test_feed_v2.py`

**Interfaces:**
- Consumes: `get_feed_page` (Task 2).
- Produces: `GET /feed/data?cursor=&per_page=` → `{items, hasMore, nextCursor}`.

- [ ] **Step 1: Write failing route tests** (merge ordering, cursor no-dup/no-skip, milestone allowlist, visibility leakage) in `tests/test_feed_v2.py`. Use the friend-fixture pattern from `tests/test_social_routes.py`. Example core cases:

```python
def test_feed_merges_and_paginates(client, auth_user, make_user, login, app):
    # auth_user + friend post feed pump checks + a repost + a milestone;
    # a stranger's feed post must NOT appear. Two-page cursor walk yields
    # each id exactly once (no dup, no skip).
    ...

def test_feed_hides_non_friend_and_restricted(client, auth_user, make_user, login, app):
    # friends-only / private pump checks absent; stranger milestone absent;
    # repost-of-restricted → unavailable stub for non-mutual viewer.
    ...
```

(Write the full bodies mirroring `test_social_routes.py` helpers — `_feed_check(owner_id)` inserts a `PumpCheck(visibility="feed")`; assert on `data["items"]` kinds/ids.)

- [ ] **Step 2: Run — expect fail (old shape returns `posts`).**

- [ ] **Step 3: Rewrite `feed_data`:**

```python
@bp.route("/feed/data")
@require_auth
def feed_data():
    cursor = request.args.get("cursor") or None
    try:
        per_page = min(max(int(request.args.get("per_page", 10) or 10), 1), 30)
    except (TypeError, ValueError):
        per_page = 10
    from app.services.feed import get_feed_page
    return jsonify(get_feed_page(current_user.id, cursor=cursor, limit=per_page))
```

- [ ] **Step 4: Run the new tests.**

```bash
pytest tests/test_feed_v2.py -v -m "not load"
```

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add app/blueprints/social.py tests/test_feed_v2.py
git commit -m "feat(feed): /feed/data cursor tabanlı yeni şema"
```

---

## Task 4: Repost / quote / delete routes

**Files:**
- Modify: `app/config.py` (rate-limit consts), `app/blueprints/social.py` (new routes + imports)
- Test: `tests/test_repost_routes.py`

**Interfaces:**
- Consumes: models, `can_view_pump_check`, `notify`, `_visible_pump_check_or_403`.
- Produces: `POST /feed/repost`, `DELETE /feed/item/<int:item_id>`.

- [ ] **Step 1: Add config consts** to `app/config.py` (mirror `FRIEND_REQUEST_RATELIMIT` block):

```python
    FEED_WRITE_RATELIMIT = os.environ.get("FEED_WRITE_RATELIMIT", "60 per hour")
    FEED_REPORT_RATELIMIT = os.environ.get("FEED_REPORT_RATELIMIT", "20 per day")
    COMMENT_WRITE_RATELIMIT = os.environ.get("COMMENT_WRITE_RATELIMIT", "120 per hour")
```

- [ ] **Step 2: Write failing tests** in `tests/test_repost_routes.py`: happy repost (counts +1, notify to owner), self-repost (no notify), duplicate repost → 400 `already_reposted`, friends-only/private original → 403 (no audience widening), invisible (stranger) → 403, quote requires body ≤500, owner delete decrements floor-0 + removes children, non-owner delete → 404.

- [ ] **Step 3: Run — expect 404 (routes absent).**

- [ ] **Step 4: Add imports + routes** to `app/blueprints/social.py`. Extend the model import to include `FeedItem, FeedItemLike, FeedItemComment`; add `current_app` import if not present. Insert after `pump_check_comment_create` (@264):

```python
_REPOST_REF_TYPES = {"pump_check"}


@bp.route("/feed/repost", methods=["POST"])
@require_auth
@limiter.limit(lambda: current_app.config["FEED_WRITE_RATELIMIT"], key_func=_user_or_ip_key)
def feed_repost():
    data = request.get_json(silent=True) or {}
    ref_type = (data.get("ref_type") or "pump_check").strip()
    mode = (data.get("mode") or "repost").strip()
    if ref_type not in _REPOST_REF_TYPES or mode not in ("repost", "quote"):
        return jsonify({"error": t("feed.invalid_request")}), 400
    try:
        ref_id = int(data.get("ref_id"))
    except (TypeError, ValueError):
        return jsonify({"error": t("feed.invalid_request")}), 400

    original = db.session.get(PumpCheck, ref_id)
    if original is None:
        return jsonify({"error": t("pump.not_found")}), 404
    # Yalnızca feed-görünür VE görülebilir içerik repost edilebilir — friends-only/
    # private repost KİTLE GENİŞLETİR, engelle.
    if original.visibility != "feed" or not can_view_pump_check(current_user.id, original):
        return jsonify({"error": t("route.not_friends")}), 403

    body = None
    if mode == "quote":
        body = (data.get("body") or "").strip()
        if not body:
            return jsonify({"error": t("route.message_empty")}), 400
        if len(body) > 500:
            return jsonify({"error": t("route.message_too_long")}), 400

    item = FeedItem(user_id=current_user.id, item_type=mode, ref_type=ref_type, ref_id=ref_id, body=body)
    db.session.add(item)
    PumpCheck.query.filter_by(id=ref_id).update({
        PumpCheck.reposts_count: PumpCheck.reposts_count + 1,
    }, synchronize_session=False)
    notify(original.user_id, "quote_repost" if mode == "quote" else "repost",
           actor_id=current_user.id, target_type="feed_item", target_id=None,
           payload={"pumpCheckId": ref_id})
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": t("feed.already_reposted")}), 400
    fresh = db.session.get(PumpCheck, ref_id)
    return jsonify({"itemId": item.id, "repostsCount": fresh.reposts_count or 0})


@bp.route("/feed/item/<int:item_id>", methods=["DELETE"])
@require_auth
def feed_item_delete(item_id):
    item = FeedItem.query.filter_by(id=item_id, user_id=current_user.id).first_or_404()
    ref_id = item.ref_id if item.ref_type == "pump_check" else None
    FeedItemLike.query.filter_by(feed_item_id=item.id).delete(synchronize_session=False)
    FeedItemComment.query.filter_by(feed_item_id=item.id).delete(synchronize_session=False)
    if item.item_type == "repost" and ref_id is not None:
        PumpCheck.query.filter_by(id=ref_id).update({
            PumpCheck.reposts_count: db.case(
                (PumpCheck.reposts_count > 0, PumpCheck.reposts_count - 1), else_=0,
            ),
        }, synchronize_session=False)
    db.session.delete(item)
    db.session.commit()
    reposts = 0
    if ref_id is not None:
        fresh = db.session.get(PumpCheck, ref_id)
        reposts = fresh.reposts_count if fresh else 0
    return jsonify({"ok": True, "repostsCount": reposts})
```

Note the `notify` target_id for repost is set post-commit isn't possible (notify is pre-commit and needs the item id). Since `item.id` is unknown pre-commit, pass `target_type="feed_item", target_id=None` with `payload={"pumpCheckId": ref_id}` — the notification links to the pump check via payload; the frontend routes repost/quote notifications to `/feed`. (Confirm `limiter`, `current_app`, `_user_or_ip_key`, `IntegrityError` are imported in social.py — add any missing.)

- [ ] **Step 5: Run tests.**

```bash
pytest tests/test_repost_routes.py -v -m "not load"
```

Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add app/config.py app/blueprints/social.py tests/test_repost_routes.py
git commit -m "feat(feed): repost/quote + silme uçları"
```

---

## Task 5: Feed-item like/comment + pump-check comment pagination & delete

**Files:**
- Modify: `app/blueprints/social.py` (feed-item like/comment routes; pump-check comment pagination + delete + limiter)
- Test: `tests/test_repost_routes.py` (quote like/comment), `tests/test_social_routes.py` (comment-delete matrix + pagination)

**Interfaces:**
- Produces: `_visible_feed_item_or_403(item_id)`; `POST/DELETE /feed/item/<id>/like`; `GET/POST /feed/item/<id>/comments`; `DELETE /feed/item/<id>/comments/<cid>`; `DELETE /pump-check/<id>/comments/<cid>`; `GET /pump-check/<id>/comments?before_id=&limit=` (now paginated + `canDelete`).

- [ ] **Step 1: Add `_visible_feed_item_or_403` + like pair.** A quote/repost FeedItem is visible if the reposter is self or an accepted friend (mirrors feed collection scope):

```python
def _visible_feed_item_or_403(item_id):
    item = db.session.get(FeedItem, item_id)
    if not item:
        return None, (jsonify({"error": t("feed.not_found")}), 404)
    if item.user_id != current_user.id and item.user_id not in get_friend_ids(current_user.id):
        return None, (jsonify({"error": t("route.not_friends")}), 403)
    return item, None


@bp.route("/feed/item/<int:item_id>/like", methods=["POST"])
@require_auth
def feed_item_like(item_id):
    item, error = _visible_feed_item_or_403(item_id)
    if error:
        return error
    existing = FeedItemLike.query.filter_by(feed_item_id=item.id, user_id=current_user.id).first()
    if not existing:
        FeedItem.query.filter_by(id=item.id).update({
            FeedItem.likes_count: FeedItem.likes_count + 1}, synchronize_session=False)
        db.session.add(FeedItemLike(feed_item_id=item.id, user_id=current_user.id))
        notify(item.user_id, "feed_like", actor_id=current_user.id,
               target_type="feed_item", target_id=item.id)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
    fresh = db.session.get(FeedItem, item.id)
    return jsonify({"liked": True, "likesCount": fresh.likes_count or 0})


@bp.route("/feed/item/<int:item_id>/like", methods=["DELETE"])
@require_auth
def feed_item_unlike(item_id):
    item, error = _visible_feed_item_or_403(item_id)
    if error:
        return error
    deleted = FeedItemLike.query.filter_by(feed_item_id=item.id, user_id=current_user.id).delete(synchronize_session=False)
    if deleted:
        FeedItem.query.filter_by(id=item.id).update({
            FeedItem.likes_count: db.case(
                (FeedItem.likes_count > 0, FeedItem.likes_count - 1), else_=0),
        }, synchronize_session=False)
        db.session.commit()
    fresh = db.session.get(FeedItem, item.id)
    return jsonify({"liked": False, "likesCount": fresh.likes_count or 0})
```

- [ ] **Step 2: Add feed-item comment list/create/delete** (mirror pump-check; create notifies `feed_comment`):

```python
@bp.route("/feed/item/<int:item_id>/comments")
@require_auth
def feed_item_comments(item_id):
    item, error = _visible_feed_item_or_403(item_id)
    if error:
        return error
    return _serialize_comment_page(
        FeedItemComment, FeedItemComment.feed_item_id == item.id, item.user_id)


@bp.route("/feed/item/<int:item_id>/comments", methods=["POST"])
@require_auth
@limiter.limit(lambda: current_app.config["COMMENT_WRITE_RATELIMIT"], key_func=_user_or_ip_key)
def feed_item_comment_create(item_id):
    item, error = _visible_feed_item_or_403(item_id)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"error": t("route.message_empty")}), 400
    if len(body) > 500:
        return jsonify({"error": t("route.message_too_long")}), 400
    comment = FeedItemComment(feed_item_id=item.id, user_id=current_user.id, body=body)
    db.session.add(comment)
    FeedItem.query.filter_by(id=item.id).update({
        FeedItem.comments_count: FeedItem.comments_count + 1}, synchronize_session=False)
    notify(item.user_id, "feed_comment", actor_id=current_user.id,
           target_type="feed_item", target_id=item.id)
    db.session.commit()
    fresh = db.session.get(FeedItem, item.id)
    return jsonify({"id": comment.id, "commentsCount": fresh.comments_count or 0})


@bp.route("/feed/item/<int:item_id>/comments/<int:comment_id>", methods=["DELETE"])
@require_auth
def feed_item_comment_delete(item_id, comment_id):
    item, error = _visible_feed_item_or_403(item_id)
    if error:
        return error
    comment = FeedItemComment.query.filter_by(id=comment_id, feed_item_id=item.id).first_or_404()
    if comment.user_id != current_user.id and item.user_id != current_user.id:
        return jsonify({"error": t("route.forbidden")}), 403
    db.session.delete(comment)
    FeedItem.query.filter_by(id=item.id).update({
        FeedItem.comments_count: db.case(
            (FeedItem.comments_count > 0, FeedItem.comments_count - 1), else_=0),
    }, synchronize_session=False)
    db.session.commit()
    fresh = db.session.get(FeedItem, item.id)
    return jsonify({"ok": True, "commentsCount": fresh.comments_count or 0})
```

- [ ] **Step 3: Add shared comment-page helper + paginate/delete pump-check comments.** Add helper and rewrite `pump_check_comments` (@221) to keyset-paginate with `canDelete`, add pump-check comment delete, and add limiter to `pump_check_comment_create`:

```python
def _serialize_comment_page(model, scope_filter, post_owner_id):
    try:
        limit = min(max(int(request.args.get("limit", 20) or 20), 1), 50)
    except (TypeError, ValueError):
        limit = 20
    try:
        before_id = int(request.args.get("before_id", 0) or 0)
    except (TypeError, ValueError):
        before_id = 0
    q = model.query.options(selectinload(model.user)).filter(scope_filter)
    if before_id > 0:
        q = q.filter(model.id < before_id)
    rows = q.order_by(model.id.desc()).limit(limit + 1).all()
    page = rows[:limit]
    has_more = len(rows) > limit
    return jsonify({
        "comments": [{
            "id": r.id,
            "username": r.user.username,
            "userAvatar": r.user.avatar_src,
            "body": r.body,
            "createdAt": display_dt(r.created_at, "%d.%m.%Y %H:%M"),
            "canDelete": (r.user_id == current_user.id or post_owner_id == current_user.id),
        } for r in page],
        "hasMore": has_more,
        "nextBeforeId": page[-1].id if has_more and page else None,
    })
```

Rewrite `pump_check_comments` body to `return _serialize_comment_page(PumpCheckComment, PumpCheckComment.pump_check_id == check.id, check.user_id)`. Add `@limiter.limit(lambda: current_app.config["COMMENT_WRITE_RATELIMIT"], key_func=_user_or_ip_key)` to `pump_check_comment_create`. Add pump-check comment delete:

```python
@bp.route("/pump-check/<int:check_id>/comments/<int:comment_id>", methods=["DELETE"])
@require_auth
def pump_check_comment_delete(check_id, comment_id):
    check, error = _visible_pump_check_or_403(check_id)
    if error:
        return error
    comment = PumpCheckComment.query.filter_by(id=comment_id, pump_check_id=check.id).first_or_404()
    if comment.user_id != current_user.id and check.user_id != current_user.id:
        return jsonify({"error": t("route.forbidden")}), 403
    db.session.delete(comment)
    PumpCheck.query.filter_by(id=check.id).update({
        PumpCheck.comments_count: db.case(
            (PumpCheck.comments_count > 0, PumpCheck.comments_count - 1), else_=0),
    }, synchronize_session=False)
    db.session.commit()
    fresh = _reload_pump_check(check.id)
    return jsonify({"ok": True, "commentsCount": fresh.comments_count or 0})
```

Note: the comment-list shape changes from a bare list to `{comments, hasMore, nextBeforeId}` — the frontend (Task 8) consumes the new shape; confirm no other consumer of `/pump-check/<id>/comments` exists (grep).

- [ ] **Step 4: Write the comment-delete matrix + pagination tests** in `tests/test_social_routes.py` (author deletes / post-owner deletes / third friend 403 / non-visible 403 / double-delete 404 / floor-0 count) and quote like/comment counters + dedup + floor-0 in `tests/test_repost_routes.py`.

- [ ] **Step 5: Run.**

```bash
pytest tests/test_repost_routes.py tests/test_social_routes.py -v -m "not load"
```

Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add app/blueprints/social.py tests/
git commit -m "feat(feed): feed-item like/comment + yorum silme & sayfalama"
```

---

## Task 6: Moderation — hide / unhide / report

**Files:**
- Modify: `app/blueprints/social.py` (3 routes)
- Test: `tests/test_feed_moderation.py`

**Interfaces:**
- Produces: `POST /feed/hide`, `POST /feed/unhide`, `POST /feed/report`. `target_type ∈ {pump_check, feed_item, activity}`.

- [ ] **Step 1: Write failing tests** in `tests/test_feed_moderation.py`: hide idempotent (2nd → still ok, one row), hide-own-content → 400, unhide, report dedup → 400, report auto-hides (row appears in FeedHide → item absent from `/feed/data`), invalid type/reason → 400, no cross-user leak (A's hide doesn't hide for B).

- [ ] **Step 2: Run — expect 404.**

- [ ] **Step 3: Add routes** (helper to check ownership of a target so users can't hide their own):

```python
_MOD_TARGET_TYPES = {"pump_check", "feed_item", "activity"}
_REPORT_REASONS = {"spam", "inappropriate", "other"}


def _target_owner_id(target_type, target_id):
    if target_type == "pump_check":
        row = db.session.get(PumpCheck, target_id)
    elif target_type == "feed_item":
        row = db.session.get(FeedItem, target_id)
    elif target_type == "activity":
        row = db.session.get(Activity, target_id)
    else:
        row = None
    return row.user_id if row is not None else None


def _parse_mod_target():
    data = request.get_json(silent=True) or {}
    ttype = (data.get("target_type") or "").strip()
    try:
        tid = int(data.get("target_id"))
    except (TypeError, ValueError):
        return None, None, data
    if ttype not in _MOD_TARGET_TYPES:
        return None, None, data
    return ttype, tid, data


@bp.route("/feed/hide", methods=["POST"])
@require_auth
def feed_hide():
    ttype, tid, _ = _parse_mod_target()
    if ttype is None:
        return jsonify({"error": t("feed.invalid_request")}), 400
    if _target_owner_id(ttype, tid) == current_user.id:
        return jsonify({"error": t("feed.cannot_hide_own")}), 400
    if not FeedHide.query.filter_by(user_id=current_user.id, target_type=ttype, target_id=tid).first():
        db.session.add(FeedHide(user_id=current_user.id, target_type=ttype, target_id=tid))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
    return jsonify({"ok": True})


@bp.route("/feed/unhide", methods=["POST"])
@require_auth
def feed_unhide():
    ttype, tid, _ = _parse_mod_target()
    if ttype is None:
        return jsonify({"error": t("feed.invalid_request")}), 400
    FeedHide.query.filter_by(user_id=current_user.id, target_type=ttype, target_id=tid).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/feed/report", methods=["POST"])
@require_auth
@limiter.limit(lambda: current_app.config["FEED_REPORT_RATELIMIT"], key_func=_user_or_ip_key)
def feed_report():
    ttype, tid, data = _parse_mod_target()
    if ttype is None:
        return jsonify({"error": t("feed.invalid_request")}), 400
    reason = (data.get("reason") or "").strip()
    if reason not in _REPORT_REASONS:
        return jsonify({"error": t("feed.invalid_request")}), 400
    note = (data.get("note") or "").strip()[:300] or None
    if FeedReport.query.filter_by(user_id=current_user.id, target_type=ttype, target_id=tid).first():
        return jsonify({"error": t("feed.already_reported")}), 400
    db.session.add(FeedReport(user_id=current_user.id, target_type=ttype, target_id=tid, reason=reason, note=note))
    # Auto-hide: şikayet edilen içerik şikayetçiden gizlenir.
    if not FeedHide.query.filter_by(user_id=current_user.id, target_type=ttype, target_id=tid).first():
        db.session.add(FeedHide(user_id=current_user.id, target_type=ttype, target_id=tid))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": t("feed.already_reported")}), 400
    return jsonify({"ok": True})
```

Add `FeedHide, FeedReport, Activity` to imports as needed.

- [ ] **Step 4: Run.**

```bash
pytest tests/test_feed_moderation.py -v -m "not load"
```

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add app/blueprints/social.py tests/test_feed_moderation.py
git commit -m "feat(feed): moderasyon (gizle/şikayet)"
```

---

## Task 7: Gallery-delete cascade + cross-PR purge test

**Files:**
- Modify: `app/blueprints/profile.py` (pump-check gallery delete)
- Test: `tests/test_feed_v2.py` (or `tests/test_cascade_delete.py`)

**Interfaces:**
- Consumes: `FeedItem`, `FeedItemLike`, `FeedItemComment`.

- [ ] **Step 1: Locate gallery delete.** `grep -n "def pump_check_gallery_delete\|pump-check.*DELETE\|gallery" app/blueprints/profile.py`. Read the handler.

- [ ] **Step 2: Write failing test:** deleting a pump check that has been reposted also removes the referencing FeedItems + their likes/comments (no dangling ref left).

- [ ] **Step 3: In the delete handler**, before deleting the `PumpCheck`, add:

```python
        from app.models import FeedItem, FeedItemLike, FeedItemComment
        ref_items = [i for (i,) in db.session.query(FeedItem.id).filter(
            FeedItem.ref_type == "pump_check", FeedItem.ref_id == check.id).all()]
        if ref_items:
            FeedItemLike.query.filter(FeedItemLike.feed_item_id.in_(ref_items)).delete(synchronize_session=False)
            FeedItemComment.query.filter(FeedItemComment.feed_item_id.in_(ref_items)).delete(synchronize_session=False)
            FeedItem.query.filter(FeedItem.id.in_(ref_items)).delete(synchronize_session=False)
```

- [ ] **Step 4: Run** the test + `tests/test_cascade_delete.py`.

- [ ] **Step 5: Commit.**

```bash
git add app/blueprints/profile.py tests/
git commit -m "feat(feed): galeri silmede repost referanslarını temizle"
```

---

## Task 8: i18n keys (both locales)

**Files:**
- Modify: `locales/tr.json`, `locales/en.json`
- Test: `tests/` locale parity test (existing)

**Interfaces:** none.

- [ ] **Step 1: Add keys to BOTH locales** (canonical values stay English; only display text differs). Keys:
  - `feed.repost`, `feed.quote`, `feed.reposted_by` (`{username}`), `feed.reposts_count` (`{n}`), `feed.share`, `feed.share_text` (`{username}`), `feed.copied`, `feed.unavailable`, `feed.hide`, `feed.unhide`, `feed.report`, `feed.report_title`, `feed.report_spam`, `feed.report_inappropriate`, `feed.report_other`, `feed.report_sent`, `feed.hidden`, `feed.menu`, `feed.delete_repost`, `feed.quote_placeholder`, `feed.quote_post`, `feed.invalid_request`, `feed.not_found`, `feed.already_reposted`, `feed.already_reported`, `feed.cannot_hide_own`, `feed.comment_delete`, `feed.comments_load_older`.
  - `notif.repost` (`{username}`), `notif.quote_repost` (`{username}`), `notif.feed_like` (`{username}`), `notif.feed_comment` (`{username}`).
  - `route.forbidden` (if not already present — grep first).

TR examples: `"notif.repost": "{username} gönderini yeniden paylaştı"`, `"feed.reposted_by": "{username} yeniden paylaştı"`, `"feed.share_text": "{username} kullanıcısının Pump Check'ine göz at!"`. EN mirrors.

- [ ] **Step 2: Run parity + count.**

```bash
pytest tests/ -k "locale_key_parity" -v
```

Expected: PASS (tr/en counts equal).

- [ ] **Step 3: Commit.**

```bash
git add locales/tr.json locales/en.json
git commit -m "feat(feed): feed/bildirim/moderasyon i18n anahtarları"
```

---

## Task 9: Frontend — feed.html rework + feed.css + gallery share

**Files:**
- Modify: `templates/feed.html`, `static/feed.css`, `templates/pump_check_gallery.html`
- Test: manual smoke (`FLASK_DEBUG=1 flask run`)

**Interfaces:** Consumes `/feed/data` (cursor), `/feed/repost`, `/feed/item/<id>/(like|comments)`, `/pump-check/<id>/comments`, moderation routes; `engagement.target` decides like/comment endpoints generically.

- [ ] **Step 1: Rework the feed script** in `templates/feed.html`. Replace the `loadFeed`/`card` block with a per-kind dispatcher and cursor pagination. Key pieces (write in the nonce'd `<script>`):

```javascript
let feedCursor = null, feedLoading = false, feedHasMore = true;
function engTarget(item){ return item.engagement && item.engagement.target; }
function pumpCard(p, opts){ /* existing card markup + reposts button + ⋯ menu + share (own) */ }
function renderItem(item){
  if(item.kind === 'pump_check') return pumpCard(item, {});
  if(item.kind === 'repost') return item.unavailable
      ? unavailableCard(item)
      : repostWrap(item, pumpCard(item.original, {viaRepost:true}));
  if(item.kind === 'quote') return quoteCard(item);
  if(item.kind === 'milestone') return milestoneCard(item);
  return '';
}
async function loadFeed(){
  if(feedLoading || !feedHasMore) return;
  feedLoading = true;
  try {
    const q = feedCursor ? ('?cursor=' + encodeURIComponent(feedCursor)) : '';
    const res = await fetch('/feed/data' + q);
    const data = await res.json();
    if(feedCursor === null) feedList.innerHTML = '';
    if(!data.items.length && feedCursor === null){
      feedList.innerHTML = '<div class="empty-state"><div class="empty-icon">📸</div><div class="empty-desc">'+__t('feed.empty')+'</div></div>';
    } else {
      feedList.insertAdjacentHTML('beforeend', data.items.map(renderItem).join(''));
    }
    feedHasMore = data.hasMore;
    feedCursor = data.nextCursor;
  } finally { feedLoading = false; }
}
```

Implement `pumpCard`/`repostWrap`/`quoteCard`/`milestoneCard`/`unavailableCard` mirroring the existing `card()` markup + `esc()`; the like button uses `engagement.target` (`data-like-type`/`data-like-id`); the comment button opens the sheet with the target; the ⋯ menu (own reposts get Delete; any card gets Hide/Report). Optimistic like/repost/comment with revert on non-OK.

- [ ] **Step 2: Generic like handler** dispatches on target type:

```javascript
async function toggleLike(el){
  const type = el.dataset.likeType, id = el.dataset.likeId;
  const base = type === 'feed_item' ? '/feed/item/' + id + '/like' : '/pump-check/' + id + '/like';
  const liked = el.classList.contains('liked');
  el.classList.toggle('liked', !liked);  // optimistic
  try {
    const res = await fetch(base, { method: liked ? 'DELETE' : 'POST' });
    const data = await res.json();
    if(res.ok){ el.classList.toggle('liked', data.liked); el.querySelector('.like-count').textContent = data.likesCount; }
    else { el.classList.toggle('liked', liked); }
  } catch(e){ el.classList.toggle('liked', liked); }
}
```

- [ ] **Step 3: Repost + share + moderation handlers** using `data-action` (register globals `window.repostItem`, `window.quoteItem`, `window.hideItem`, `window.reportItem`, `window.deleteRepost`, `window.shareCheck`). Share:

```javascript
window.shareCheck = async function(username){
  const text = __t('feed.share_text', {username: username});
  const url = window.location.origin + '/feed';
  if(navigator.share){ try { await navigator.share({text: text, url: url}); return; } catch(e){ if(e.name === 'AbortError') return; } }
  try { await navigator.clipboard.writeText(text + ' ' + url); toast(__t('feed.copied')); } catch(e){}
};
```

- [ ] **Step 4: Comments sheet** — switch fetch to target-aware endpoint (`/feed/item/<id>/comments` vs `/pump-check/<id>/comments`), consume `{comments, hasMore, nextBeforeId}` with a "load older" control, render `canDelete` rows with a delete button, POST create to the matching endpoint.

- [ ] **Step 5: `static/feed.css` additions** — `.repost-banner`, `.quote-card`, `.milestone-card`, `.feed-menu`, `.feed-unavailable`, share/report button styles. No JS-injected `<style>`.

- [ ] **Step 6: `templates/pump_check_gallery.html`** — show `repostsCount` on cards and a Share button on own cards (reuse `window.shareCheck`). Confirm gallery serializer includes `repostsCount` (it does after Task 2).

- [ ] **Step 7: Manual smoke.** `FLASK_DEBUG=1 flask run`; from two accounts: repost/quote a friend's feed pump check → counts move, optimistic UI, notification bell increments; hide/report removes it; comment + delete; share button copies/shares.

- [ ] **Step 8: Commit.**

```bash
git add templates/feed.html static/feed.css templates/pump_check_gallery.html
git commit -m "feat(feed): feed v2 arayüzü (repost/quote/milestone, paylaş, moderasyon)"
```

---

## Task 10: Cross-cutting tests + docs

**Files:**
- Modify/Create: `tests/test_feed_v2.py` (purge/cascade rounding out), `docs/FEED.md`, `CLAUDE.md`
- Test: full suite

- [ ] **Step 1: Purge/cascade coverage** — extend `tests/test_feed_v2.py`: purging a user removes their reposts, their reposts-of-others, hides, reports; and purging a *reposted* user removes cross-user reposts of their content (no orphans). Mirror `tests/test_cascade_delete.py` style.

- [ ] **Step 2: Write `docs/FEED.md`** — spine (FeedItem-only-for-new-content), milestone allowlist, cursor codec + keyset merge + ranking seam (`_rank` identity sort), engagement-target contract, moderation (per-viewer hide, report auto-hides), repost privacy rule (only `visibility=="feed"` repostable), external share decision (Web Share + clipboard; NO tokenized public page in V1 — documented future work), purge rules.

- [ ] **Step 3: Update `CLAUDE.md`** — add FeedItem/FeedItemLike/FeedItemComment/FeedHide/FeedReport to model list; note `app/services/feed.py`; note new `/feed/*` routes; docs pointer.

- [ ] **Step 4: Run the FULL suite.**

```bash
pytest -m "not load"
```

Expected: all green (PR1 baseline was 1668; expect a higher count, 0 failures).

- [ ] **Step 5: Commit.**

```bash
git add tests/ docs/FEED.md CLAUDE.md
git commit -m "test+docs(feed): feed v2 kapsam + dokümanlar"
```

---

## Verification (end-to-end)

1. `pytest -m "not load"` — full suite green (hermetic).
2. Schema-drift guard in CI re-confirms the generated migration matches models (no un-migrated model edits remain).
3. Manual smoke per Task 9 Step 7.
4. Push `feat/sprint5-feed-v2`, open PR against `main`; **stop for user review/merge before starting PR 3** (Challenges), which is gated on PR 2 merging.

## Self-review notes (spec coverage)

- WS1 Feed (ranking seam, pagination, metadata, empty state, permission boundaries) → Tasks 2,3,9. WS2 Like/comment (delete, pagination, counts, dedup) → Tasks 5. WS3 Notifications (repost/quote/feed_like/feed_comment triggers) → Tasks 4,5,8. WS4 Pump Check (reposts_count, gallery share) → Tasks 1,9. WS moderation (hide/report) → Task 6. Purge/cascade → Tasks 1,7,10. Docs → Task 10.
- Repost privacy (no audience widening): enforced in Task 4 (`visibility=="feed"` AND `can_view`).
- All new user_id models in registry (Task 1); migration re-runnable + head test bumped (Task 1); i18n both locales + parity (Task 8).
