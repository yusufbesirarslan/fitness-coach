# Pump Check Sharing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Pump Check sharing to a separate `/feed` page, friend-only chat shares, and a personal Pump Check Gallery while preserving the current AxisAI UI language and workout completion semantics.

**Architecture:** `PumpCheck` remains the canonical record for workout completion, gallery, feed visibility, and friend shares. Feed and gallery routes authorize access before generating presigned image URLs. Likes/comments use separate tables with denormalized counts on `PumpCheck`; friend shares use existing `Message` rows with `message_type="pump_check"`.

**Tech Stack:** Flask, SQLAlchemy, Flask-Login, Alembic, Jinja templates, vanilla JavaScript, pytest, SQLite test database, PostgreSQL production migrations, existing S3 helper.

## Global Constraints

- Do not change the overall design language, color palette, spacing system, typography, or modal style.
- `/feed` is a completely separate page and is not placed inside `/friends`.
- Replace the current Club tab in the bottom navigation with Feed.
- Move Club/leaderboard into the hamburger drawer as `/leaderboard`.
- `/friends` remains dedicated to friend list, requests, search, and management.
- Preserve one successful Pump Check per user per Istanbul day for workout completion and XP.
- Existing historical Pump Checks must backfill to `private` so old photos are not published.
- Stored Pump Check images remain private S3 objects; only authorized routes generate presigned URLs.
- Code identifiers are English; UI copy goes through `locales/en.json` and `locales/tr.json`.
- All state-changing fetch routes remain CSRF-protected POST/DELETE routes.
- Use test-first implementation: write a failing test, run it, implement the smallest passing code, run it again.

---

## File Structure

- Modify `app/models.py`: extend `PumpCheck`; add `PumpCheckLike` and `PumpCheckComment`.
- Create `migrations/versions/f2a3b4c5d6e7_pump_check_sharing.py`: PostgreSQL migration for new columns/tables.
- Create `app/services/pump_checks.py`: friend lookup, visibility authorization, serialization, counts, and image URL helpers.
- Modify `app/blueprints/training.py`: accept `visibility` and `shared_friend_ids`; create friend-share messages.
- Modify `app/blueprints/social.py`: add `/feed`, `/feed/data`, friend selector, like/comment endpoints.
- Modify `app/blueprints/profile.py`: add gallery page/data/delete routes.
- Modify `templates/training.html`: add share selector, friend selector, compression/progress/success UX.
- Create `templates/feed.html`: social timeline.
- Create `templates/pump_check_gallery.html`: personal gallery and detail modal.
- Modify `templates/chat.html`: render `message_type="pump_check"` cards.
- Modify `templates/edit_profile.html`: link to Pump Check Gallery from profile.
- Modify `templates/_actionbar.html`: replace Club tab with Feed.
- Modify `templates/_nav.html`: add Feed and Club/Leaderboard drawer links.
- Modify templates with hardcoded nav/action bars (`templates/index.html`, `templates/training.html`, `templates/nutrition.html`, `templates/friends.html`, `templates/leaderboard.html`, `templates/quests.html`, `templates/progress.html`, `templates/manage_stack.html`) enough to keep bottom nav consistent.
- Modify `locales/en.json` and `locales/tr.json`: feed, gallery, pump share, and navigation labels.
- Create `tests/test_pump_check_sharing.py`: sharing, feed, chat, gallery, like/comment authorization.
- Modify `tests/test_training_routes.py`: assert default visibility and idempotency still work.

---

### Task 1: Data Model, Migration, and Pump Check Service Helpers

**Files:**
- Modify: `app/models.py`
- Create: `app/services/pump_checks.py`
- Create: `migrations/versions/f2a3b4c5d6e7_pump_check_sharing.py`
- Test: `tests/test_pump_check_sharing.py`

**Interfaces:**
- Produces: `PumpCheck.visibility: str`, `PumpCheck.shared_friend_ids: list[int]`, `PumpCheck.likes_count: int`, `PumpCheck.comments_count: int`
- Produces: `PumpCheckLike`, `PumpCheckComment`
- Produces: `get_friend_ids(user_id: int) -> set[int]`
- Produces: `can_view_pump_check(user_id: int, check: PumpCheck) -> bool`
- Produces: `pump_check_image_url(check: PumpCheck, viewer_id: int, expires_in: int = 3600) -> str | None`
- Produces: `serialize_pump_check_card(check: PumpCheck, viewer_id: int, include_viewer_state: bool = True) -> dict`

- [ ] **Step 1: Write failing service/model tests**

Add `tests/test_pump_check_sharing.py`:

```python
from datetime import datetime, timedelta

from app.extensions import db
from app.models import Friendship, PumpCheck, PumpCheckComment, PumpCheckLike
from app.services.pump_checks import can_view_pump_check, get_friend_ids, serialize_pump_check_card


def _friend(a, b):
    db.session.add(Friendship(sender_id=a.id, receiver_id=b.id, status="accepted"))
    db.session.commit()


def test_pump_check_defaults_are_private_safe(auth_user):
    check = PumpCheck(user_id=auth_user.id, image_key="pump-checks/1/2026/07/x.jpg", valid=True)
    db.session.add(check)
    db.session.commit()

    assert check.visibility == "private"
    assert check.shared_friend_ids == []
    assert check.likes_count == 0
    assert check.comments_count == 0


def test_get_friend_ids_returns_accepted_friend_ids(make_user):
    user = make_user("owner")
    friend = make_user("friend")
    pending = make_user("pending")
    db.session.add(Friendship(sender_id=user.id, receiver_id=friend.id, status="accepted"))
    db.session.add(Friendship(sender_id=pending.id, receiver_id=user.id, status="pending"))
    db.session.commit()

    assert get_friend_ids(user.id) == {friend.id}


def test_can_view_pump_check_enforces_visibility(make_user):
    owner = make_user("owner")
    friend = make_user("friend")
    selected = make_user("selected")
    stranger = make_user("stranger")
    _friend(owner, friend)
    _friend(owner, selected)

    feed = PumpCheck(user_id=owner.id, visibility="feed", valid=True)
    friends = PumpCheck(user_id=owner.id, visibility="friends", shared_friend_ids=[selected.id], valid=True)
    private = PumpCheck(user_id=owner.id, visibility="private", valid=True)
    db.session.add_all([feed, friends, private])
    db.session.commit()

    assert can_view_pump_check(owner.id, feed) is True
    assert can_view_pump_check(friend.id, feed) is True
    assert can_view_pump_check(stranger.id, feed) is False
    assert can_view_pump_check(selected.id, friends) is True
    assert can_view_pump_check(friend.id, friends) is False
    assert can_view_pump_check(owner.id, private) is True
    assert can_view_pump_check(friend.id, private) is False


def test_serialize_pump_check_card_exposes_requested_fields(make_user):
    owner = make_user("owner", full_name="Owner Person")
    check = PumpCheck(
        user_id=owner.id,
        image_key=None,
        location_type="Gym",
        description="Upper body session.",
        visibility="feed",
        likes_count=2,
        comments_count=3,
        created_at=datetime.utcnow() - timedelta(hours=2),
        valid=True,
    )
    db.session.add(check)
    db.session.add(PumpCheckLike(pump_check_id=1, user_id=owner.id))
    db.session.add(PumpCheckComment(pump_check_id=1, user_id=owner.id, body="Nice"))
    db.session.commit()

    data = serialize_pump_check_card(check, owner.id)

    assert data["id"] == check.id
    assert data["username"] == "owner"
    assert data["imageUrl"] is None
    assert data["environment"] == "Gym"
    assert data["description"] == "Upper body session."
    assert data["likesCount"] == 2
    assert data["commentsCount"] == 3
    assert data["visibility"] == "feed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m pytest tests/test_pump_check_sharing.py -v
```

Expected: FAIL because `PumpCheckLike`, `PumpCheckComment`, and `app.services.pump_checks` do not exist, and `PumpCheck` does not have the new fields.

- [ ] **Step 3: Implement models**

In `app/models.py`, update `PumpCheck` and add models after it:

```python
    visibility    = db.Column(db.String(20), nullable=False, default="private", server_default="private", index=True)
    shared_friend_ids = db.Column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=list)
    likes_count   = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    comments_count = db.Column(db.Integer, nullable=False, default=0, server_default="0")
```

```python
class PumpCheckLike(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    pump_check_id = db.Column(db.Integer, db.ForeignKey("pump_check.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id       = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        db.UniqueConstraint("pump_check_id", "user_id", name="uq_pump_check_like_user"),
    )

    pump_check = db.relationship("PumpCheck", backref=db.backref("likes", passive_deletes=True))
    user = db.relationship("User", backref=db.backref("pump_check_likes", passive_deletes=True))


class PumpCheckComment(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    pump_check_id = db.Column(db.Integer, db.ForeignKey("pump_check.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id       = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    body          = db.Column(db.String(500), nullable=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    pump_check = db.relationship("PumpCheck", backref=db.backref("comments", passive_deletes=True))
    user = db.relationship("User", backref=db.backref("pump_check_comments", passive_deletes=True))
```

- [ ] **Step 4: Implement service helpers**

Create `app/services/pump_checks.py`:

```python
import s3_helper

from app.extensions import db
from app.models import Friendship, PumpCheckLike
from app.timeutil import display_dt


def get_friend_ids(user_id):
    rows = Friendship.query.filter(
        Friendship.status == "accepted",
        db.or_(Friendship.sender_id == user_id, Friendship.receiver_id == user_id),
    ).all()
    ids = set()
    for row in rows:
        ids.add(row.receiver_id if row.sender_id == user_id else row.sender_id)
    return ids


def can_view_pump_check(user_id, check):
    if check.user_id == user_id:
        return True
    visibility = check.visibility or "private"
    if visibility == "feed":
        return user_id in get_friend_ids(check.user_id)
    if visibility == "friends":
        return user_id in set(check.shared_friend_ids or [])
    return False


def pump_check_image_url(check, viewer_id, expires_in=3600):
    if not check.image_key or not can_view_pump_check(viewer_id, check):
        return None
    return s3_helper.generate_presigned_url(
        check.image_key,
        expires_in=expires_in,
        expected_user_id=check.user_id,
    )


def sharing_status(check):
    visibility = check.visibility or "private"
    if visibility == "feed":
        return "Shared to Feed"
    if visibility == "friends":
        return "Shared to Friends"
    return "Private"


def serialize_pump_check_card(check, viewer_id, include_viewer_state=True):
    user = check.user
    liked = False
    if include_viewer_state:
        liked = PumpCheckLike.query.filter_by(
            pump_check_id=check.id,
            user_id=viewer_id,
        ).first() is not None
    return {
        "id": check.id,
        "userId": check.user_id,
        "username": user.username if user else "",
        "userAvatar": user.avatar_src if user else None,
        "timePosted": display_dt(check.created_at, "%d.%m.%Y %H:%M"),
        "createdAt": check.created_at.isoformat() if check.created_at else None,
        "imageUrl": pump_check_image_url(check, viewer_id),
        "workoutScore": None,
        "environment": check.location_type or "",
        "description": check.description or "",
        "visibility": check.visibility or "private",
        "sharingStatus": sharing_status(check),
        "sharedFriendIds": check.shared_friend_ids or [],
        "likesCount": check.likes_count or 0,
        "commentsCount": check.comments_count or 0,
        "likedByMe": liked,
    }
```

- [ ] **Step 5: Add migration**

Create `migrations/versions/f2a3b4c5d6e7_pump_check_sharing.py`:

```python
"""Pump Check sharing fields and interactions.

Revision ID: f2a3b4c5d6e7
Revises: ab12cd34ef56
Create Date: 2026-07-02
"""
from alembic import op


revision = "f2a3b4c5d6e7"
down_revision = "ab12cd34ef56"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE pump_check ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) NOT NULL DEFAULT 'private'")
    op.execute("ALTER TABLE pump_check ADD COLUMN IF NOT EXISTS shared_friend_ids JSONB NOT NULL DEFAULT '[]'::jsonb")
    op.execute("ALTER TABLE pump_check ADD COLUMN IF NOT EXISTS likes_count INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE pump_check ADD COLUMN IF NOT EXISTS comments_count INTEGER NOT NULL DEFAULT 0")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pump_check_visibility ON pump_check (visibility)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS pump_check_like (
            id SERIAL PRIMARY KEY,
            pump_check_id INTEGER NOT NULL REFERENCES pump_check(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
            created_at TIMESTAMP WITHOUT TIME ZONE,
            CONSTRAINT uq_pump_check_like_user UNIQUE (pump_check_id, user_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_pump_check_like_pump_check_id ON pump_check_like (pump_check_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pump_check_like_user_id ON pump_check_like (user_id)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS pump_check_comment (
            id SERIAL PRIMARY KEY,
            pump_check_id INTEGER NOT NULL REFERENCES pump_check(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
            body VARCHAR(500) NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_pump_check_comment_pump_check_id ON pump_check_comment (pump_check_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pump_check_comment_user_id ON pump_check_comment (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pump_check_comment_created_at ON pump_check_comment (created_at)")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP TABLE IF EXISTS pump_check_comment")
    op.execute("DROP TABLE IF EXISTS pump_check_like")
    op.execute("DROP INDEX IF EXISTS ix_pump_check_visibility")
    op.execute("ALTER TABLE pump_check DROP COLUMN IF EXISTS comments_count")
    op.execute("ALTER TABLE pump_check DROP COLUMN IF EXISTS likes_count")
    op.execute("ALTER TABLE pump_check DROP COLUMN IF EXISTS shared_friend_ids")
    op.execute("ALTER TABLE pump_check DROP COLUMN IF EXISTS visibility")
```

- [ ] **Step 6: Run tests to verify Task 1 passes**

Run:

```bash
python -m pytest tests/test_pump_check_sharing.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

```bash
git add app/models.py app/services/pump_checks.py migrations/versions/f2a3b4c5d6e7_pump_check_sharing.py tests/test_pump_check_sharing.py
git commit -m "Add pump check sharing models"
```

---

### Task 2: Workout Completion Sharing and Friend Selector API

**Files:**
- Modify: `app/blueprints/training.py`
- Modify: `app/blueprints/social.py`
- Modify: `tests/test_pump_check_sharing.py`
- Modify: `tests/test_training_routes.py`

**Interfaces:**
- Consumes: `get_friend_ids(user_id) -> set[int]`
- Produces: `/workout/complete` support for `visibility` and `shared_friend_ids`
- Produces: `/friends/select-list?q=...`
- Produces: `Message.message_type == "pump_check"` rows for selected friends

- [ ] **Step 1: Add failing tests for completion visibility and friend selector**

Append to `tests/test_pump_check_sharing.py`:

```python
import json

from app.blueprints import training as training_bp
from app.models import Message, TrainingPlan
from tests.test_validators import _image_data_url


def _ready_for_workout(client, user):
    client.post("/training-plan/save", json={"plan": {"v": 1}, "score": 8.0})


def test_workout_complete_defaults_to_feed_visibility(client, auth_user, monkeypatch):
    _ready_for_workout(client, auth_user)
    monkeypatch.setattr(training_bp, "validate_pump_check", lambda *a: {"valid": True, "fallback": False})

    res = client.post("/workout/complete", json={"image": _image_data_url("JPEG"), "location_type": "Gym"})

    assert res.status_code == 200
    check = PumpCheck.query.filter_by(user_id=auth_user.id).one()
    assert check.visibility == "feed"
    assert check.shared_friend_ids == []


def test_workout_complete_rejects_friends_visibility_without_recipients(client, auth_user, monkeypatch):
    _ready_for_workout(client, auth_user)
    monkeypatch.setattr(training_bp, "validate_pump_check", lambda *a: {"valid": True, "fallback": False})

    res = client.post("/workout/complete", json={
        "image": _image_data_url("JPEG"),
        "visibility": "friends",
        "shared_friend_ids": [],
    })

    assert res.status_code == 400
    assert PumpCheck.query.count() == 0


def test_workout_complete_sends_pump_check_messages_to_selected_friends(client, auth_user, make_user, monkeypatch):
    friend = make_user("friend")
    other = make_user("other")
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=friend.id, status="accepted"))
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=other.id, status="accepted"))
    db.session.commit()
    _ready_for_workout(client, auth_user)
    monkeypatch.setattr(training_bp, "validate_pump_check", lambda *a: {"valid": True, "fallback": False})

    res = client.post("/workout/complete", json={
        "image": _image_data_url("JPEG"),
        "location_type": "Gym",
        "description": "Push day",
        "visibility": "friends",
        "shared_friend_ids": [friend.id],
    })

    assert res.status_code == 200
    check = PumpCheck.query.filter_by(user_id=auth_user.id).one()
    assert check.visibility == "friends"
    assert check.shared_friend_ids == [friend.id]
    msg = Message.query.filter_by(sender_id=auth_user.id, receiver_id=friend.id, message_type="pump_check").one()
    payload = json.loads(msg.body)
    assert payload["pump_check_id"] == check.id
    assert payload["environment"] == "Gym"
    assert Message.query.filter_by(receiver_id=other.id, message_type="pump_check").count() == 0


def test_friend_select_list_recent_contacts_first(client, auth_user, make_user):
    old_friend = make_user("alpha")
    recent_friend = make_user("zeta")
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=old_friend.id, status="accepted"))
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=recent_friend.id, status="accepted"))
    db.session.commit()
    db.session.add(Message(sender_id=recent_friend.id, receiver_id=auth_user.id, body="hi"))
    db.session.commit()

    body = client.get("/friends/select-list").get_json()

    assert [row["id"] for row in body["friends"]] == [recent_friend.id, old_friend.id]
```

Modify `tests/test_training_routes.py::test_complete_awards_xp_and_records_pump_check` to include:

```python
    assert check.visibility == "feed"
    assert check.shared_friend_ids == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m pytest tests/test_pump_check_sharing.py tests/test_training_routes.py::test_complete_awards_xp_and_records_pump_check -v
```

Expected: FAIL because `/workout/complete` ignores visibility, `Message` is not imported/created, and `/friends/select-list` does not exist.

- [ ] **Step 3: Implement `/workout/complete` sharing**

In `app/blueprints/training.py`, import:

```python
from app.models import Message, User
from app.services.pump_checks import get_friend_ids
```

Add helpers near `complete_workout`:

```python
def _parse_pump_visibility(data):
    visibility = (data.get("visibility") or "feed").strip().lower()
    if visibility not in {"feed", "friends", "private"}:
        return None, [], t("pump.visibility_invalid")
    raw_ids = data.get("shared_friend_ids") or []
    try:
        selected_ids = list(dict.fromkeys(int(x) for x in raw_ids))
    except (TypeError, ValueError):
        return None, [], t("pump.friend_ids_invalid")
    if visibility == "friends" and not selected_ids:
        return None, [], t("pump.friend_required")
    if visibility != "friends":
        selected_ids = []
    return visibility, selected_ids, None
```

Inside `complete_workout`, after description parsing and before AI validation:

```python
    visibility, selected_friend_ids, visibility_error = _parse_pump_visibility(data)
    if visibility_error:
        return jsonify({"error": visibility_error}), 400
    if visibility == "friends":
        accepted_ids = get_friend_ids(current_user.id)
        if any(fid not in accepted_ids for fid in selected_friend_ids):
            return jsonify({"error": t("pump.friend_ids_invalid")}), 400
```

Change the PumpCheck creation to store the object:

```python
    pump_check = PumpCheck(
        user_id=current_user.id, image_key=pump_image_key,
        location_type=location_type, description=description,
        valid=True, fallback=check.get("fallback", False),
        date_key=app_today().isoformat(),
        visibility=visibility,
        shared_friend_ids=selected_friend_ids,
    )
    db.session.add(pump_check)
    db.session.flush()
```

Before commit, add friend messages:

```python
    if visibility == "friends":
        payload = json.dumps({
            "pump_check_id": pump_check.id,
            "image_key": pump_image_key,
            "environment": location_type,
            "description": description,
            "created_at": pump_check.created_at.isoformat() if pump_check.created_at else None,
        }, ensure_ascii=False)
        for friend_id in selected_friend_ids:
            db.session.add(Message(
                sender_id=current_user.id,
                receiver_id=friend_id,
                body=payload,
                message_type="pump_check",
            ))
```

Add response fields:

```python
        "pump_check_id": pump_check.id,
        "visibility": visibility,
        "shared_friend_ids": selected_friend_ids,
```

- [ ] **Step 4: Implement friend selector route**

In `app/blueprints/social.py`, add route:

```python
@bp.route("/friends/select-list")
@login_required
def friends_select_list():
    q = (request.args.get("q") or "").strip().lower()
    accepted = Friendship.query.filter(
        Friendship.status == "accepted",
        db.or_(Friendship.sender_id == current_user.id, Friendship.receiver_id == current_user.id)
    ).all()
    friend_ids = [f.receiver_id if f.sender_id == current_user.id else f.sender_id for f in accepted]
    if not friend_ids:
        return jsonify({"friends": []})

    latest_rows = Message.query.filter(
        db.or_(
            db.and_(Message.sender_id == current_user.id, Message.receiver_id.in_(friend_ids)),
            db.and_(Message.receiver_id == current_user.id, Message.sender_id.in_(friend_ids)),
        )
    ).order_by(Message.timestamp.desc()).all()
    recent_rank = {}
    for msg in latest_rows:
        other_id = msg.receiver_id if msg.sender_id == current_user.id else msg.sender_id
        recent_rank.setdefault(other_id, len(recent_rank))

    users = User.query.filter(User.id.in_(friend_ids)).all()
    if q:
        users = [u for u in users if q in (u.username or "").lower() or q in (u.full_name or "").lower()]
    users.sort(key=lambda u: (recent_rank.get(u.id, 10_000), (u.username or "").lower()))
    return jsonify({"friends": [{
        "id": u.id,
        "username": u.username,
        "full_name": u.full_name or u.username,
        "profile_picture": u.avatar_src,
        "recent": u.id in recent_rank,
    } for u in users]})
```

Add `User` to the existing social imports.

- [ ] **Step 5: Add locale keys used by backend errors**

Add to `locales/en.json`:

```json
"pump.visibility_invalid": "Choose where to share this Pump Check.",
"pump.friend_ids_invalid": "Select valid friends to share with.",
"pump.friend_required": "Select at least one friend."
```

Add to `locales/tr.json`:

```json
"pump.visibility_invalid": "Bu Pump Check'i nerede paylasacagini sec.",
"pump.friend_ids_invalid": "Paylasmak icin gecerli arkadaslar sec.",
"pump.friend_required": "En az bir arkadas sec."
```

- [ ] **Step 6: Run tests to verify Task 2 passes**

Run:

```bash
python -m pytest tests/test_pump_check_sharing.py tests/test_training_routes.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

```bash
git add app/blueprints/training.py app/blueprints/social.py locales/en.json locales/tr.json tests/test_pump_check_sharing.py tests/test_training_routes.py
git commit -m "Add pump check workout sharing"
```

---

### Task 3: Feed Page, Feed API, Likes, and Comments

**Files:**
- Modify: `app/blueprints/social.py`
- Create: `templates/feed.html`
- Modify: `tests/test_pump_check_sharing.py`
- Modify: `locales/en.json`
- Modify: `locales/tr.json`

**Interfaces:**
- Consumes: `can_view_pump_check`, `serialize_pump_check_card`, `get_friend_ids`
- Produces: `/feed`, `/feed/data`, `/pump-check/<id>/like`, `/pump-check/<id>/comments`

- [ ] **Step 1: Add failing feed and interaction tests**

Append:

```python
def test_feed_data_shows_current_user_and_friend_feed_posts(client, auth_user, make_user):
    friend = make_user("friend")
    stranger = make_user("stranger")
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=friend.id, status="accepted"))
    db.session.add(PumpCheck(user_id=auth_user.id, visibility="feed", location_type="Gym", description="Mine", valid=True))
    db.session.add(PumpCheck(user_id=friend.id, visibility="feed", location_type="Home", description="Friend", valid=True))
    db.session.add(PumpCheck(user_id=stranger.id, visibility="feed", location_type="Gym", description="Nope", valid=True))
    db.session.commit()

    body = client.get("/feed/data").get_json()

    descriptions = [post["description"] for post in body["posts"]]
    assert descriptions == ["Friend", "Mine"]
    assert "Nope" not in descriptions


def test_feed_page_renders(client, auth_user):
    assert client.get("/feed").status_code == 200


def test_like_create_and_delete_updates_count(client, auth_user, make_user):
    friend = make_user("friend")
    db.session.add(Friendship(sender_id=friend.id, receiver_id=auth_user.id, status="accepted"))
    check = PumpCheck(user_id=friend.id, visibility="feed", valid=True)
    db.session.add(check)
    db.session.commit()

    assert client.post(f"/pump-check/{check.id}/like").get_json()["likesCount"] == 1
    assert client.post(f"/pump-check/{check.id}/like").get_json()["likesCount"] == 1
    assert client.delete(f"/pump-check/{check.id}/like").get_json()["likesCount"] == 0


def test_comment_requires_visibility_and_updates_count(client, auth_user, make_user):
    stranger = make_user("stranger")
    check = PumpCheck(user_id=stranger.id, visibility="feed", valid=True)
    db.session.add(check)
    db.session.commit()

    denied = client.post(f"/pump-check/{check.id}/comments", json={"body": "Nice"})
    assert denied.status_code == 403

    db.session.add(Friendship(sender_id=stranger.id, receiver_id=auth_user.id, status="accepted"))
    db.session.commit()
    ok = client.post(f"/pump-check/{check.id}/comments", json={"body": "Nice work"})
    assert ok.status_code == 200
    assert ok.get_json()["commentsCount"] == 1
    comments = client.get(f"/pump-check/{check.id}/comments").get_json()["comments"]
    assert comments[0]["body"] == "Nice work"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m pytest tests/test_pump_check_sharing.py -v
```

Expected: FAIL because feed and interaction routes do not exist.

- [ ] **Step 3: Implement social routes**

In `app/blueprints/social.py`, import:

```python
from app.models import PumpCheck, PumpCheckComment, PumpCheckLike
from app.services.pump_checks import can_view_pump_check, get_friend_ids, serialize_pump_check_card
```

Add:

```python
@bp.route("/feed")
@login_required
def feed_page():
    return render_template("feed.html", username=current_user.username, profile_picture=current_user.avatar_src)


@bp.route("/feed/data")
@login_required
def feed_data():
    page = max(int(request.args.get("page", 1) or 1), 1)
    per_page = min(max(int(request.args.get("per_page", 10) or 10), 1), 30)
    visible_user_ids = get_friend_ids(current_user.id) | {current_user.id}
    query = PumpCheck.query.filter(
        PumpCheck.visibility == "feed",
        PumpCheck.user_id.in_(visible_user_ids),
    ).order_by(PumpCheck.created_at.desc(), PumpCheck.id.desc())
    rows = query.offset((page - 1) * per_page).limit(per_page + 1).all()
    posts = rows[:per_page]
    return jsonify({
        "posts": [serialize_pump_check_card(row, current_user.id) for row in posts],
        "hasMore": len(rows) > per_page,
        "nextPage": page + 1 if len(rows) > per_page else None,
    })


def _visible_pump_check_or_403(check_id):
    check = db.session.get(PumpCheck, check_id)
    if not check:
        return None, (jsonify({"error": t("pump.not_found")}), 404)
    if not can_view_pump_check(current_user.id, check):
        return None, (jsonify({"error": t("route.not_friends")}), 403)
    return check, None


@bp.route("/pump-check/<int:check_id>/like", methods=["POST"])
@login_required
def pump_check_like(check_id):
    check, error = _visible_pump_check_or_403(check_id)
    if error:
        return error
    existing = PumpCheckLike.query.filter_by(pump_check_id=check.id, user_id=current_user.id).first()
    if not existing:
        db.session.add(PumpCheckLike(pump_check_id=check.id, user_id=current_user.id))
        check.likes_count = (check.likes_count or 0) + 1
        db.session.commit()
    return jsonify({"liked": True, "likesCount": check.likes_count or 0})


@bp.route("/pump-check/<int:check_id>/like", methods=["DELETE"])
@login_required
def pump_check_unlike(check_id):
    check, error = _visible_pump_check_or_403(check_id)
    if error:
        return error
    existing = PumpCheckLike.query.filter_by(pump_check_id=check.id, user_id=current_user.id).first()
    if existing:
        db.session.delete(existing)
        check.likes_count = max((check.likes_count or 0) - 1, 0)
        db.session.commit()
    return jsonify({"liked": False, "likesCount": check.likes_count or 0})


@bp.route("/pump-check/<int:check_id>/comments")
@login_required
def pump_check_comments(check_id):
    check, error = _visible_pump_check_or_403(check_id)
    if error:
        return error
    rows = PumpCheckComment.query.filter_by(pump_check_id=check.id).order_by(PumpCheckComment.created_at.asc()).all()
    return jsonify({"comments": [{
        "id": row.id,
        "username": row.user.username,
        "userAvatar": row.user.avatar_src,
        "body": row.body,
        "createdAt": display_dt(row.created_at, "%d.%m.%Y %H:%M"),
    } for row in rows]})


@bp.route("/pump-check/<int:check_id>/comments", methods=["POST"])
@login_required
def pump_check_comment_create(check_id):
    check, error = _visible_pump_check_or_403(check_id)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"error": t("route.message_empty")}), 400
    if len(body) > 500:
        return jsonify({"error": t("route.message_too_long")}), 400
    comment = PumpCheckComment(pump_check_id=check.id, user_id=current_user.id, body=body)
    db.session.add(comment)
    check.comments_count = (check.comments_count or 0) + 1
    db.session.commit()
    return jsonify({"id": comment.id, "commentsCount": check.comments_count or 0})
```

- [ ] **Step 4: Create Feed template**

Create `templates/feed.html` with existing shell and focused JS:

```html
<!DOCTYPE html>
<html lang="{{ locale }}" data-theme="dark">
<head>
    {% include "_head.html" %}
    <title>{{ t('feed.page_title') }}</title>
    <link rel="stylesheet" href="/static/theme.css">
    <link rel="stylesheet" href="/static/nav.css">
    <style nonce="{{ csp_nonce }}">
    .feed-wrap { max-width: 620px; margin: 0 auto; display:flex; flex-direction:column; gap:14px; }
    .feed-card { background:var(--surface-2); border:1px solid var(--border); border-radius:var(--r-md); overflow:hidden; }
    .feed-head { display:flex; align-items:center; gap:10px; padding:14px 16px; }
    .feed-avatar { width:40px; height:40px; border-radius:50%; background:linear-gradient(135deg,#3D8BFF,#1E6FE0); color:#121212; display:flex; align-items:center; justify-content:center; font-family:var(--font-display); overflow:hidden; }
    .feed-avatar img { width:100%; height:100%; object-fit:cover; }
    .feed-user { font-weight:700; color:var(--text); font-size:14px; }
    .feed-time { color:var(--text-3); font-size:12px; }
    .feed-img { width:100%; max-height:520px; object-fit:cover; display:block; background:rgba(255,255,255,0.03); }
    .feed-body { padding:14px 16px 16px; display:flex; flex-direction:column; gap:12px; }
    .feed-label { font-size:10px; font-weight:700; letter-spacing:.14em; text-transform:uppercase; color:var(--text-3); margin-bottom:3px; }
    .feed-value { font-size:14px; color:var(--text); line-height:1.45; }
    .feed-actions { display:flex; gap:14px; color:var(--text-2); font-size:13px; align-items:center; }
    .feed-action { background:none; border:0; color:inherit; cursor:pointer; font:inherit; padding:0; }
    .feed-action.liked { color:var(--volt); }
    .feed-skeleton { height:360px; border-radius:var(--r-md); background:linear-gradient(90deg,rgba(255,255,255,.03),rgba(255,255,255,.07),rgba(255,255,255,.03)); animation:pulse 1.2s infinite; }
    .feed-empty { background:var(--surface-2); border:1px solid var(--border); border-radius:var(--r-md); padding:32px; text-align:center; color:var(--text-3); }
    @keyframes pulse { 0%{opacity:.6} 50%{opacity:1} 100%{opacity:.6} }
    </style>
</head>
{% set nav_active = 'feed' %}
<body class="page-body">
<div class="toast-wrap" id="toast-wrap"></div>
{% include "_nav.html" %}
<main class="main-content">
    <div class="page-hdr">
        <h1>{{ t('feed.h1_a') }}<br><span>{{ t('feed.h1_b') }}</span></h1>
        <p>{{ t('feed.page_sub') }}</p>
    </div>
    <div class="feed-wrap" id="feed-list">
        <div class="feed-skeleton"></div>
        <div class="feed-skeleton"></div>
    </div>
</main>
{% include "_actionbar.html" %}
<script nonce="{{ csp_nonce }}">
var __t = window.t || function(k){ return k; };
let feedPage = 1;
let feedLoading = false;
let feedHasMore = true;
const feedList = document.getElementById('feed-list');
function esc(v){ const d=document.createElement('div'); d.textContent=v == null ? '' : String(v); return d.innerHTML; }
function avatar(post){ return post.userAvatar ? '<div class="feed-avatar"><img src="'+esc(post.userAvatar).replace(/"/g,'&quot;')+'" alt=""></div>' : '<div class="feed-avatar">'+esc((post.username||'U')[0].toUpperCase())+'</div>'; }
function card(post){
  return '<article class="feed-card" data-id="'+post.id+'"><div class="feed-head">'+avatar(post)+'<div><div class="feed-user">'+esc(post.username)+'</div><div class="feed-time">'+esc(post.timePosted)+'</div></div></div>'+
    (post.imageUrl ? '<img class="feed-img" src="'+esc(post.imageUrl).replace(/"/g,'&quot;')+'" loading="lazy" decoding="async" alt="">' : '')+
    '<div class="feed-body"><div><div class="feed-label">'+__t('feed.environment')+'</div><div class="feed-value">'+esc(post.environment)+'</div></div>'+
    '<div><div class="feed-label">'+__t('feed.description')+'</div><div class="feed-value">'+esc(post.description)+'</div></div>'+
    '<div class="feed-actions"><button class="feed-action '+(post.likedByMe?'liked':'')+'" data-like="'+post.id+'">'+post.likesCount+' '+__t('feed.likes')+'</button><button class="feed-action" data-comments="'+post.id+'">'+__t('feed.comments_count',{n:post.commentsCount})+'</button></div></div></article>';
}
async function loadFeed(){
  if(feedLoading || !feedHasMore) return;
  feedLoading = true;
  const res = await fetch('/feed/data?page=' + feedPage);
  const data = await res.json();
  if(feedPage === 1) feedList.innerHTML = '';
  if(!data.posts.length && feedPage === 1) feedList.innerHTML = '<div class="feed-empty">'+__t('feed.empty')+'</div>';
  feedList.insertAdjacentHTML('beforeend', data.posts.map(card).join(''));
  feedHasMore = data.hasMore;
  feedPage = data.nextPage || feedPage;
  feedLoading = false;
}
feedList.addEventListener('click', async (e) => {
  const like = e.target.closest('[data-like]');
  if(!like) return;
  const id = like.dataset.like;
  const liked = like.classList.contains('liked');
  const res = await fetch('/pump-check/' + id + '/like', { method: liked ? 'DELETE' : 'POST' });
  const data = await res.json();
  if(res.ok){ like.classList.toggle('liked', data.liked); like.textContent = data.likesCount + ' ' + __t('feed.likes'); }
});
window.addEventListener('scroll', () => { if(window.innerHeight + window.scrollY > document.body.offsetHeight - 500) loadFeed(); });
loadFeed();
</script>
<script src="/static/nav.js"></script>
<script src="/static/actions.js"></script>
</body>
</html>
```

- [ ] **Step 5: Add feed locale keys**

Add English:

```json
"nav.feed": "Feed",
"feed.page_title": "Feed - AxisAI",
"feed.h1_a": "FRIENDS",
"feed.h1_b": "FEED",
"feed.page_sub": "Pump Check posts from your training circle.",
"feed.environment": "Environment:",
"feed.description": "Description:",
"feed.likes": "Likes",
"feed.comments_count": "{n} Comments",
"feed.empty": "No Pump Check posts yet. Complete a workout or add friends."
```

Add Turkish:

```json
"nav.feed": "Feed",
"feed.page_title": "Feed - AxisAI",
"feed.h1_a": "ARKADAS",
"feed.h1_b": "FEED",
"feed.page_sub": "Antrenman cevrenden Pump Check paylasimlari.",
"feed.environment": "Ortam:",
"feed.description": "Aciklama:",
"feed.likes": "Begeni",
"feed.comments_count": "{n} Yorum",
"feed.empty": "Henuz Pump Check paylasimi yok. Antrenman tamamla veya arkadas ekle."
```

- [ ] **Step 6: Run tests to verify Task 3 passes**

Run:

```bash
python -m pytest tests/test_pump_check_sharing.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

```bash
git add app/blueprints/social.py templates/feed.html locales/en.json locales/tr.json tests/test_pump_check_sharing.py
git commit -m "Add pump check feed"
```

---

### Task 4: Friend-Only Pump Check Chat Cards

**Files:**
- Modify: `app/blueprints/social.py`
- Modify: `templates/chat.html`
- Modify: `tests/test_pump_check_sharing.py`
- Modify: `locales/en.json`
- Modify: `locales/tr.json`

**Interfaces:**
- Consumes: existing `Message.message_type == "pump_check"` JSON body
- Produces: chat message JSON with `pump_check` payload for authorized recipients
- Produces: template rendering for Pump Check message cards

- [ ] **Step 1: Add failing chat message API test**

Append:

```python
def test_chat_messages_include_authorized_pump_check_payload(client, auth_user, make_user):
    friend = make_user("friend")
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=friend.id, status="accepted"))
    check = PumpCheck(user_id=auth_user.id, visibility="friends", shared_friend_ids=[friend.id],
                      location_type="Gym", description="Shared only", valid=True)
    db.session.add(check)
    db.session.commit()
    db.session.add(Message(sender_id=auth_user.id, receiver_id=friend.id, message_type="pump_check",
                           body=json.dumps({"pump_check_id": check.id})))
    db.session.commit()

    client.post("/logout")
    client.post("/login", json={"username": "friend", "password": "Sifre123"})
    body = client.get("/chat/testuser/messages").get_json()

    msg = body["messages"][0]
    assert msg["message_type"] == "pump_check"
    assert msg["pump_check"]["description"] == "Shared only"
    assert msg["pump_check"]["environment"] == "Gym"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python -m pytest tests/test_pump_check_sharing.py::test_chat_messages_include_authorized_pump_check_payload -v
```

Expected: FAIL because `chat_messages` does not attach `pump_check`.

- [ ] **Step 3: Extend `chat_messages` serialization**

In `app/blueprints/social.py`, add imports:

```python
import json
from app.services.pump_checks import serialize_pump_check_card
from app.models import PumpCheck
```

Replace the list comprehension in `chat_messages` with:

```python
    payloads = []
    for m in messages:
        item = {"id": m.id, "sender": m.sender.username, "body": m.body,
                "timestamp": display_dt(m.timestamp, "%H:%M"), "is_mine": m.sender_id == current_user.id,
                "message_type": m.message_type or "text"}
        if (m.message_type or "") == "pump_check":
            item["pump_check"] = None
            try:
                raw = json.loads(m.body or "{}")
                check = db.session.get(PumpCheck, int(raw.get("pump_check_id")))
                if check and can_view_pump_check(current_user.id, check):
                    item["pump_check"] = serialize_pump_check_card(check, current_user.id)
            except (TypeError, ValueError, json.JSONDecodeError):
                item["pump_check"] = None
        payloads.append(item)

    return jsonify({"messages": payloads})
```

- [ ] **Step 4: Update chat template renderer**

In `templates/chat.html`, add CSS:

```css
    .msg-pump-card {
        background: var(--surface-2); border: 1px solid rgba(61,139,255,0.2);
        border-radius: 14px; overflow: hidden; max-width: 260px;
    }
    .msg-pump-card img { width: 100%; max-height: 220px; object-fit: cover; display: block; }
    .msg-pump-body { padding: 12px; display: flex; flex-direction: column; gap: 8px; }
    .msg-pump-title { font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--volt); }
    .msg-pump-label { font-size: 10px; font-weight: 700; letter-spacing: .12em; color: var(--text-3); text-transform: uppercase; }
    .msg-pump-value { font-size: 13px; color: var(--text); line-height: 1.4; }
    .msg-pump-missing { padding: 12px; color: var(--text-3); font-size: 13px; }
```

Add JS:

```javascript
function renderPumpCheckCard(m) {
    const p = m.pump_check;
    if (!p) return '<div class="msg-pump-card"><div class="msg-pump-missing">'+__t('chat.pump_unavailable')+'</div></div>';
    return '<div class="msg-pump-card">' +
        (p.imageUrl ? '<img src="'+escapeHTML(p.imageUrl).replace(/"/g,'&quot;')+'" loading="lazy" decoding="async" alt="">' : '') +
        '<div class="msg-pump-body"><div class="msg-pump-title">'+__t('chat.pump_title')+'</div>' +
        '<div><div class="msg-pump-label">'+__t('feed.environment')+'</div><div class="msg-pump-value">'+escapeHTML(p.environment)+'</div></div>' +
        '<div><div class="msg-pump-label">'+__t('feed.description')+'</div><div class="msg-pump-value">'+escapeHTML(p.description)+'</div></div></div></div>';
}
```

In `renderMessages`, before suggestion rendering:

```javascript
        if (m.message_type === 'pump_check') {
            row.innerHTML = '<div>' + renderPumpCheckCard(m) +
                '<div class="msg-time">' + m.timestamp + '</div></div>';
        } else if (isSuggestion) {
```

- [ ] **Step 5: Add chat locale keys**

Add English:

```json
"chat.pump_title": "Pump Check",
"chat.pump_unavailable": "This Pump Check is no longer available."
```

Add Turkish:

```json
"chat.pump_title": "Pump Check",
"chat.pump_unavailable": "Bu Pump Check artik goruntulenemiyor."
```

- [ ] **Step 6: Run tests**

Run:

```bash
python -m pytest tests/test_pump_check_sharing.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add app/blueprints/social.py templates/chat.html locales/en.json locales/tr.json tests/test_pump_check_sharing.py
git commit -m "Render pump check chat cards"
```

---

### Task 5: Pump Check Gallery Page and Owner Delete

**Files:**
- Modify: `app/blueprints/profile.py`
- Create: `templates/pump_check_gallery.html`
- Modify: `templates/edit_profile.html`
- Modify: `tests/test_pump_check_sharing.py`
- Modify: `locales/en.json`
- Modify: `locales/tr.json`

**Interfaces:**
- Consumes: `serialize_pump_check_card`
- Produces: `/pump-check-gallery`, `/pump-check-gallery/data`, `DELETE /pump-check-gallery/<id>`

- [ ] **Step 1: Add failing gallery tests**

Append:

```python
def test_gallery_lists_only_current_user_pump_checks(client, auth_user, make_user):
    other = make_user("other")
    mine_feed = PumpCheck(user_id=auth_user.id, visibility="feed", description="Feed", valid=True)
    mine_private = PumpCheck(user_id=auth_user.id, visibility="private", description="Private", valid=True)
    not_mine = PumpCheck(user_id=other.id, visibility="feed", description="Other", valid=True)
    db.session.add_all([mine_feed, mine_private, not_mine])
    db.session.commit()

    body = client.get("/pump-check-gallery/data").get_json()

    assert [item["description"] for item in body["items"]] == ["Private", "Feed"]


def test_gallery_page_renders(client, auth_user):
    assert client.get("/pump-check-gallery").status_code == 200


def test_gallery_delete_is_owner_only(client, auth_user, make_user):
    other = make_user("other")
    mine = PumpCheck(user_id=auth_user.id, visibility="feed", valid=True)
    not_mine = PumpCheck(user_id=other.id, visibility="feed", valid=True)
    db.session.add_all([mine, not_mine])
    db.session.commit()

    assert client.delete(f"/pump-check-gallery/{not_mine.id}").status_code == 404
    assert client.delete(f"/pump-check-gallery/{mine.id}").status_code == 200
    assert db.session.get(PumpCheck, mine.id) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m pytest tests/test_pump_check_sharing.py::test_gallery_lists_only_current_user_pump_checks tests/test_pump_check_sharing.py::test_gallery_page_renders tests/test_pump_check_sharing.py::test_gallery_delete_is_owner_only -v
```

Expected: FAIL because gallery routes and template do not exist.

- [ ] **Step 3: Implement gallery routes**

In `app/blueprints/profile.py`, import:

```python
from app.models import PumpCheck
from app.services.pump_checks import serialize_pump_check_card
```

Add:

```python
@bp.route("/pump-check-gallery")
@login_required
def pump_check_gallery():
    return render_template("pump_check_gallery.html",
        username=current_user.username,
        profile_picture=current_user.avatar_src)


@bp.route("/pump-check-gallery/data")
@login_required
def pump_check_gallery_data():
    page = max(int(request.args.get("page", 1) or 1), 1)
    per_page = min(max(int(request.args.get("per_page", 18) or 18), 1), 36)
    rows = PumpCheck.query.filter_by(user_id=current_user.id)\
        .order_by(PumpCheck.created_at.desc(), PumpCheck.id.desc())\
        .offset((page - 1) * per_page).limit(per_page + 1).all()
    items = rows[:per_page]
    return jsonify({
        "items": [serialize_pump_check_card(row, current_user.id) for row in items],
        "hasMore": len(rows) > per_page,
        "nextPage": page + 1 if len(rows) > per_page else None,
    })


@bp.route("/pump-check-gallery/<int:check_id>", methods=["DELETE"])
@login_required
def pump_check_gallery_delete(check_id):
    check = PumpCheck.query.filter_by(id=check_id, user_id=current_user.id).first_or_404()
    db.session.delete(check)
    db.session.commit()
    return jsonify({"ok": True})
```

- [ ] **Step 4: Create gallery template**

Create `templates/pump_check_gallery.html`:

```html
<!DOCTYPE html>
<html lang="{{ locale }}" data-theme="dark">
<head>
    {% include "_head.html" %}
    <title>{{ t('gallery.page_title') }}</title>
    <link rel="stylesheet" href="/static/theme.css">
    <link rel="stylesheet" href="/static/nav.css">
    <style nonce="{{ csp_nonce }}">
    .gallery-wrap { max-width: 880px; margin: 0 auto; }
    .gallery-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:10px; }
    .gallery-item { background:var(--surface-2); border:1px solid var(--border); border-radius:var(--r-md); overflow:hidden; cursor:pointer; text-align:left; padding:0; color:var(--text); }
    .gallery-item img { width:100%; aspect-ratio:1/1; object-fit:cover; display:block; background:rgba(255,255,255,.03); }
    .gallery-meta { padding:10px; }
    .gallery-date { color:var(--text-3); font-size:11px; }
    .gallery-desc { font-size:13px; margin-top:4px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .gallery-modal { position:fixed; inset:0; z-index:1000; display:none; align-items:center; justify-content:center; background:rgba(0,0,0,.78); padding:20px; }
    .gallery-modal.show { display:flex; animation:fade-in .18s ease; }
    .gallery-detail { width:min(920px,100%); max-height:92vh; overflow:auto; background:var(--surface-2); border:1px solid var(--border); border-radius:var(--r-lg); box-shadow:var(--shadow-lg); }
    .gallery-detail img { width:100%; max-height:70vh; object-fit:contain; background:#050505; display:block; }
    .gallery-detail-body { padding:18px; display:flex; flex-direction:column; gap:12px; }
    .gallery-actions { display:flex; justify-content:space-between; gap:10px; margin-top:4px; }
    .gallery-empty { background:var(--surface-2); border:1px solid var(--border); border-radius:var(--r-md); padding:32px; text-align:center; color:var(--text-3); }
    </style>
</head>
{% set nav_active = 'profile' %}
<body class="page-body">
<div class="toast-wrap" id="toast-wrap"></div>
{% include "_nav.html" %}
<main class="main-content">
    <div class="page-hdr">
        <h1>{{ t('gallery.h1_a') }}<br><span>{{ t('gallery.h1_b') }}</span></h1>
        <p>{{ t('gallery.page_sub') }}</p>
    </div>
    <div class="gallery-wrap"><div class="gallery-grid" id="gallery-grid"></div></div>
</main>
{% include "_actionbar.html" %}
<div class="gallery-modal" id="gallery-modal" role="dialog" aria-modal="true">
    <div class="gallery-detail" id="gallery-detail"></div>
</div>
<script nonce="{{ csp_nonce }}">
var __t = window.t || function(k){ return k; };
const grid = document.getElementById('gallery-grid');
const modal = document.getElementById('gallery-modal');
const detail = document.getElementById('gallery-detail');
let galleryItems = [];
function esc(v){ const d=document.createElement('div'); d.textContent=v == null ? '' : String(v); return d.innerHTML; }
function itemHTML(item){ return '<button class="gallery-item" data-id="'+item.id+'">'+(item.imageUrl?'<img src="'+esc(item.imageUrl).replace(/"/g,'&quot;')+'" loading="lazy" decoding="async" alt="">':'')+'<div class="gallery-meta"><div class="gallery-date">'+esc(item.timePosted)+'</div><div class="gallery-desc">'+esc(item.description)+'</div></div></button>'; }
async function loadGallery(){ const res=await fetch('/pump-check-gallery/data'); const data=await res.json(); galleryItems=data.items; grid.innerHTML=data.items.length?data.items.map(itemHTML).join(''):'<div class="gallery-empty">'+__t('gallery.empty')+'</div>'; }
function openDetail(item){ detail.innerHTML=(item.imageUrl?'<img src="'+esc(item.imageUrl).replace(/"/g,'&quot;')+'" alt="">':'')+'<div class="gallery-detail-body"><div><b>'+__t('feed.environment')+'</b><br>'+esc(item.environment)+'</div><div><b>'+__t('feed.description')+'</b><br>'+esc(item.description)+'</div><div>'+esc(item.timePosted)+'</div><div>'+esc(item.sharingStatus)+'</div><div class="gallery-actions"><button class="btn-ghost" data-close-gallery>'+__t('common.close')+'</button><button class="btn-ghost" data-delete-gallery="'+item.id+'">'+__t('gallery.delete')+'</button></div></div>'; modal.classList.add('show'); }
grid.addEventListener('click', e => { const btn=e.target.closest('[data-id]'); if(!btn)return; const item=galleryItems.find(x=>String(x.id)===btn.dataset.id); if(item) openDetail(item); });
modal.addEventListener('click', async e => { if(e.target===modal || e.target.closest('[data-close-gallery]')) modal.classList.remove('show'); const del=e.target.closest('[data-delete-gallery]'); if(del && confirm(__t('gallery.delete_confirm'))){ const res=await fetch('/pump-check-gallery/'+del.dataset.deleteGallery,{method:'DELETE'}); if(res.ok){ modal.classList.remove('show'); loadGallery(); } } });
loadGallery();
</script>
<script src="/static/nav.js"></script>
<script src="/static/actions.js"></script>
</body>
</html>
```

- [ ] **Step 5: Link gallery from profile**

In `templates/edit_profile.html`, after the stats row, add:

```html
        <div class="form-section" style="margin-bottom:24px;">
            <div class="form-section-title">{{ t('gallery.profile_link_title') }}</div>
            <p style="font-size:13px;color:var(--text-2);margin:0 0 14px;font-weight:300;">{{ t('gallery.profile_link_desc') }}</p>
            <a href="/pump-check-gallery" class="stack-link">{{ t('gallery.open') }}</a>
        </div>
```

- [ ] **Step 6: Add gallery locale keys**

Add English:

```json
"gallery.page_title": "Pump Check Gallery - AxisAI",
"gallery.h1_a": "PUMP CHECK",
"gallery.h1_b": "GALLERY",
"gallery.page_sub": "Your saved workout photos and sharing history.",
"gallery.empty": "No Pump Checks saved yet.",
"gallery.delete": "Delete",
"gallery.delete_confirm": "Delete this Pump Check?",
"gallery.profile_link_title": "PUMP CHECK GALLERY",
"gallery.profile_link_desc": "View your saved workout photos.",
"gallery.open": "Open Gallery"
```

Add Turkish equivalents:

```json
"gallery.page_title": "Pump Check Galerisi - AxisAI",
"gallery.h1_a": "PUMP CHECK",
"gallery.h1_b": "GALERI",
"gallery.page_sub": "Kaydedilen antrenman fotograflarin ve paylasim gecmisin.",
"gallery.empty": "Henuz kaydedilmis Pump Check yok.",
"gallery.delete": "Sil",
"gallery.delete_confirm": "Bu Pump Check silinsin mi?",
"gallery.profile_link_title": "PUMP CHECK GALERISI",
"gallery.profile_link_desc": "Kaydedilen antrenman fotograflarini goruntule.",
"gallery.open": "Galeriyi Ac"
```

- [ ] **Step 7: Run tests**

Run:

```bash
python -m pytest tests/test_pump_check_sharing.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 5**

```bash
git add app/blueprints/profile.py templates/pump_check_gallery.html templates/edit_profile.html locales/en.json locales/tr.json tests/test_pump_check_sharing.py
git commit -m "Add pump check gallery"
```

---

### Task 6: Pump Modal UI, Navigation, and Full Regression

**Files:**
- Modify: `templates/training.html`
- Modify: `templates/_actionbar.html`
- Modify: `templates/_nav.html`
- Modify: hardcoded nav templates listed in File Structure
- Modify: `locales/en.json`
- Modify: `locales/tr.json`
- Test: `tests/test_pump_check_sharing.py`
- Test: existing template smoke tests

**Interfaces:**
- Consumes: `/friends/select-list`
- Consumes: `/workout/complete` with `visibility` and `shared_friend_ids`
- Produces: Pump Check modal share selector and friend selector
- Produces: bottom nav Feed and drawer Club link

- [ ] **Step 1: Add failing navigation smoke tests**

Append:

```python
def test_bottom_nav_has_feed_not_club(client, auth_user):
    html = client.get("/").get_data(as_text=True)
    assert 'href="/feed"' in html
    assert 'href="/leaderboard" class="ab-tab' not in html


def test_drawer_contains_club_link(client, auth_user):
    html = client.get("/feed").get_data(as_text=True)
    assert 'href="/leaderboard"' in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m pytest tests/test_pump_check_sharing.py::test_bottom_nav_has_feed_not_club tests/test_pump_check_sharing.py::test_drawer_contains_club_link -v
```

Expected: FAIL because bottom nav still points to `/leaderboard` and feed template may not yet include final drawer link.

- [ ] **Step 3: Update shared action bar**

In `templates/_actionbar.html`, replace the Club tab with:

```html
    <a href="/feed" class="ab-tab{% if nav_active == 'feed' %} active{% endif %}">
        <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="7" y1="8" x2="17" y2="8"/><line x1="7" y1="12" x2="17" y2="12"/><line x1="7" y1="16" x2="13" y2="16"/></svg>
        <span>{{ t('nav.feed') }}</span>
    </a>
```

- [ ] **Step 4: Update shared drawer**

In `templates/_nav.html`, add Feed near Friends:

```html
        <a href="/feed" class="drawer-link{% if nav_active == 'feed' %} active{% endif %}"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="7" y1="8" x2="17" y2="8"/><line x1="7" y1="12" x2="17" y2="12"/><line x1="7" y1="16" x2="13" y2="16"/></svg>{{ t('nav.feed') }}</a>
```

Add Club/leaderboard in the drawer:

```html
        <a href="/leaderboard" class="drawer-link{% if nav_active == 'club' %} active{% endif %}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 21h8"/><path d="M12 17v4"/><path d="M7 4h10v4a5 5 0 0 1-10 0V4z"/><path d="M5 6H3a3 3 0 0 0 3 3"/><path d="M19 6h2a3 3 0 0 1-3 3"/></svg>{{ t('nav.club') }}</a>
```

- [ ] **Step 5: Update hardcoded bottom nav instances**

For templates that still contain inline `<nav class="action-bar">`, replace the last tab href `/leaderboard` with `/feed`, active state with the current page where applicable, icon with the Feed icon from Step 3, and label with `{{ t('nav.feed') }}`. Keep `/leaderboard` page itself accessible from drawer only.

Run this search after edits:

```bash
rg -n "href=\"/leaderboard\" class=\"ab-tab|t\\('nav.club'\\).*ab-tab|<span>\\{\\{ t\\('nav.club'\\)" templates
```

Expected: no bottom-nav Club matches remain.

- [ ] **Step 6: Update Pump Check modal markup**

In `templates/training.html`, after the description field, add:

```html
        <div class="pump-field">
            <div class="pump-label">{{ t('pump.share_to') }}</div>
            <div class="pump-share-toggle" role="radiogroup" aria-label="{{ t('pump.share_to') }}">
                <button type="button" class="pump-share-option active" data-share="feed" aria-pressed="true">{{ t('pump.share_feed') }}</button>
                <button type="button" class="pump-share-option" data-share="friends" aria-pressed="false">{{ t('pump.share_friends') }}</button>
            </div>
        </div>
        <div class="pump-field pump-friend-picker" id="pump-friend-picker" hidden>
            <label class="pump-label" for="pump-friend-search">{{ t('pump.select_friends') }}</label>
            <input type="text" id="pump-friend-search" class="fc-input" placeholder="{{ t('pump.search_friends') }}" autocomplete="off">
            <div class="pump-selected-friends" id="pump-selected-friends"></div>
            <div class="pump-friend-results" id="pump-friend-results"></div>
        </div>
        <div class="pump-progress" id="pump-progress" hidden><div id="pump-progress-bar"></div></div>
```

Add CSS using existing variables:

```css
        .pump-share-toggle { display:flex; gap:8px; background:rgba(255,255,255,0.03); border:1px solid var(--border); border-radius:var(--r-md); padding:5px; }
        .pump-share-option { flex:1; height:38px; border:0; border-radius:var(--r-sm); background:transparent; color:var(--text-3); font-family:'DM Sans',sans-serif; font-weight:700; cursor:pointer; transition:all var(--t-fast); }
        .pump-share-option.active { background:var(--volt-dim); color:var(--volt); border:1px solid rgba(61,139,255,0.22); }
        .pump-friend-picker[hidden] { display:none; }
        .pump-selected-friends { display:flex; flex-wrap:wrap; gap:6px; }
        .pump-chip { border:1px solid rgba(61,139,255,.24); color:var(--volt); background:var(--volt-dim); border-radius:var(--r-full); padding:5px 8px; font-size:12px; display:inline-flex; gap:6px; align-items:center; }
        .pump-chip button { border:0; background:none; color:inherit; cursor:pointer; }
        .pump-friend-results { max-height:180px; overflow:auto; border:1px solid var(--border); border-radius:var(--r-md); background:rgba(255,255,255,0.02); }
        .pump-friend-row { width:100%; border:0; background:none; color:var(--text); display:flex; align-items:center; gap:10px; padding:10px 12px; cursor:pointer; text-align:left; }
        .pump-friend-row:hover { background:rgba(61,139,255,0.08); }
        .pump-progress { height:4px; background:rgba(255,255,255,0.06); border-radius:var(--r-full); overflow:hidden; }
        .pump-progress div { height:100%; width:0; background:var(--volt); transition:width .25s ease; }
```

- [ ] **Step 7: Update Pump Check modal JavaScript**

In `templates/training.html`, add state:

```javascript
    let pumpVisibility = 'feed';
    let pumpSelectedFriends = new Map();
```

In `openPumpCheck()`, reset:

```javascript
        pumpVisibility = 'feed';
        pumpSelectedFriends = new Map();
        setPumpShare('feed');
        renderPumpSelectedFriends();
        document.getElementById('pump-progress').hidden = true;
        document.getElementById('pump-progress-bar').style.width = '0%';
```

Add functions:

```javascript
    function setPumpShare(value) {
        pumpVisibility = value;
        document.querySelectorAll('.pump-share-option').forEach(btn => {
            const active = btn.dataset.share === value;
            btn.classList.toggle('active', active);
            btn.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
        document.getElementById('pump-friend-picker').hidden = value !== 'friends';
        if (value === 'friends') loadPumpFriends();
    }
    function renderPumpSelectedFriends() {
        const wrap = document.getElementById('pump-selected-friends');
        wrap.innerHTML = Array.from(pumpSelectedFriends.values()).map(f =>
            '<span class="pump-chip">@'+escapeHTML(f.username)+' <button type="button" data-remove-friend="'+f.id+'" aria-label="'+__t('pump.remove_friend')+'">&times;</button></span>'
        ).join('');
    }
    async function loadPumpFriends() {
        const q = document.getElementById('pump-friend-search').value.trim();
        const res = await fetch('/friends/select-list?q=' + encodeURIComponent(q));
        const data = await res.json();
        document.getElementById('pump-friend-results').innerHTML = data.friends.map(f =>
            '<button type="button" class="pump-friend-row" data-friend-id="'+f.id+'" data-username="'+escapeHTML(f.username).replace(/"/g,'&quot;')+'"><span>@'+escapeHTML(f.username)+'</span></button>'
        ).join('') || '<div class="pump-dropzone-hint" style="padding:12px;">'+__t('pump.no_friends')+'</div>';
    }
    function setPumpProgress(percent) {
        document.getElementById('pump-progress').hidden = false;
        document.getElementById('pump-progress-bar').style.width = percent + '%';
    }
```

If `escapeHTML` does not exist in `training.html`, add the standard helper:

```javascript
    function escapeHTML(str) {
        const d = document.createElement('div');
        d.textContent = str == null ? '' : String(str);
        return d.innerHTML;
    }
```

Update payload in `submitPumpCheck()`:

```javascript
            visibility: pumpVisibility,
            shared_friend_ids: Array.from(pumpSelectedFriends.keys())
```

Before submitting:

```javascript
        if (pumpVisibility === 'friends' && pumpSelectedFriends.size === 0) {
            showPumpError(__t('pump.friend_required'));
            return;
        }
        setPumpProgress(35);
```

After fetch starts and succeeds:

```javascript
            setPumpProgress(75);
```

On success before close:

```javascript
                setPumpProgress(100);
```

Add event listeners in `initPumpCheck()`:

```javascript
        document.querySelectorAll('.pump-share-option').forEach(btn => {
            btn.addEventListener('click', () => setPumpShare(btn.dataset.share));
        });
        document.getElementById('pump-friend-search').addEventListener('input', () => {
            clearTimeout(window.__pumpFriendTimer);
            window.__pumpFriendTimer = setTimeout(loadPumpFriends, 200);
        });
        document.getElementById('pump-friend-results').addEventListener('click', e => {
            const row = e.target.closest('[data-friend-id]');
            if (!row) return;
            pumpSelectedFriends.set(Number(row.dataset.friendId), { id: Number(row.dataset.friendId), username: row.dataset.username });
            renderPumpSelectedFriends();
        });
        document.getElementById('pump-selected-friends').addEventListener('click', e => {
            const btn = e.target.closest('[data-remove-friend]');
            if (!btn) return;
            pumpSelectedFriends.delete(Number(btn.dataset.removeFriend));
            renderPumpSelectedFriends();
        });
```

- [ ] **Step 8: Add pump modal locale keys**

Add English:

```json
"pump.share_to": "SHARE TO",
"pump.share_feed": "Feed",
"pump.share_friends": "Friends",
"pump.select_friends": "Select friends",
"pump.search_friends": "Search friends...",
"pump.remove_friend": "Remove friend",
"pump.no_friends": "No friends found"
```

Add Turkish:

```json
"pump.share_to": "PAYLAS",
"pump.share_feed": "Feed",
"pump.share_friends": "Arkadaslar",
"pump.select_friends": "Arkadas sec",
"pump.search_friends": "Arkadas ara...",
"pump.remove_friend": "Arkadasi kaldir",
"pump.no_friends": "Arkadas bulunamadi"
```

- [ ] **Step 9: Run focused tests**

Run:

```bash
python -m pytest tests/test_pump_check_sharing.py tests/test_training_routes.py -v
```

Expected: PASS.

- [ ] **Step 10: Run full regression**

Run:

```bash
python -m pytest -v
```

Expected: PASS. If unrelated existing tests fail, record exact failing tests and determine whether this feature caused them before changing unrelated code.

- [ ] **Step 11: Commit Task 6**

```bash
git add templates/training.html templates/_actionbar.html templates/_nav.html templates/index.html templates/nutrition.html templates/friends.html templates/leaderboard.html templates/quests.html templates/progress.html templates/manage_stack.html locales/en.json locales/tr.json tests/test_pump_check_sharing.py
git commit -m "Polish pump check sharing UI"
```

---

## Final Verification

- [ ] Run `python -m pytest -v`.
- [ ] Run `git status --short` and confirm only intended files are changed.
- [ ] Start the Flask app if practical with `python start_dev.py` or the repo's usual local command.
- [ ] Manually verify `/training`, `/feed`, `/friends`, `/chat/<friend>`, `/edit-profile`, and `/pump-check-gallery`.
- [ ] Confirm bottom nav shows Feed and drawer shows Club.
- [ ] Confirm no friend-only Pump Check appears in `/feed`.
- [ ] Confirm deleting a gallery item removes it from owner gallery/feed responses.

## Self-Review Notes

- Spec coverage: schema, workout flow, feed, friends share, gallery, navigation, UX, privacy, and tests are each mapped to at least one task.
- Placeholder scan: no TBD/TODO/fill-in placeholders are intentionally left; implementation snippets use concrete file paths, route names, and expected commands.
- Type consistency: new service helpers and model fields are named consistently across tasks.
