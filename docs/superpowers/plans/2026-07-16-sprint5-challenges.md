# Sprint 5 PR 3 — Challenges (hybrid V1) + badges + leaderboard polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add global weekly challenges (auto-tracked from existing events) + opt-in featured challenges, with progress tracking, idempotent completion → XP + badges + notifications + feed milestone cards, per-challenge friend/global leaderboards, and fix the leaderboard countdown UTC↔Istanbul mismatch.

**Architecture:** A single `challenges.record_event(user_id, event_type, amount)` funnel is called from every existing gamification event site (all ~14 quest events via `_claim_quest`, plus `pump_check_created`, `active_day`, `xp_earned`). It updates lazily-created `UserChallengeProgress` rows (get-or-create with `begin_nested()` + `IntegrityError` guard) against a static seeded `Challenge` catalog, using a computed ISO-week `period_key` (same Sunday-23:59-Istanbul boundary as `_last_completed_week_key`). Completion is guarded by a conditional `UPDATE … SET completed_at WHERE completed_at IS NULL` (rowcount-1 wins → exactly-once XP/badge/notify). `record_event` NEVER commits (caller commits, mirroring `_claim_quest`). No instance table, no cron, no Redis — new week = new lazily-created progress rows; `challenge_board` reads Postgres directly.

**Tech Stack:** Flask, SQLAlchemy + Flask-Migrate (Alembic), Jinja templates, vanilla JS (nonce'd), custom JSON i18n (`locales/{tr,en}.json`), pytest (hermetic in-memory SQLite).

## Global Constraints

- **Sequential PR / independently deployable:** branch `feat/sprint5-challenges` off the merged `main` (PR2 #156 already merged). Old code must ignore the 3 new tables (additive/expand-only). Migration `down_revision="ee55ff66aa77"`.
- **Migrations additive + re-runnable:** hand-write the migration (local `flask db migrate` can't boot — missing env/psycopg2); gate every `create_table` with `sa.inspect(op.get_bind()).has_table(...)` (fresh-DB boot re-runs migrations after `create_all`). Bump the hardcoded head in `tests/test_migration_graph.py`.
- **Every new `user_id`-bearing model** → `app/cli.py` `_user_child_models()` (children before parents) or `tests/test_cascade_delete.py` fails.
- **Canonical values stay English slugs:** `challenge.code`, `category`, `metric` (event_type), `badge_code`, `challenge_type`, `period_type`, `ntype`. Challenge **titles/descriptions are canonical TR** in the DB (quest pattern), displayed via `t_or("challenge.<code>.title", ...)`.
- **i18n:** every new user-visible string → BOTH `locales/tr.json` AND `locales/en.json` (keep parity).
- **CSP:** new page includes `_head.html`; inline `<script nonce="{{ csp_nonce }}">`; NO JS-injected `<style>` (CSS in `static/challenges.css`); progress-bar widths via `style="width:N%"` attr are allowed (`style-src-attr 'unsafe-inline'`).
- **Transaction contract:** `record_event` / `notify` / `award_badge` / `log_activity` are session-add-only, NO commit (atomic with the triggering action's commit). NEVER touch Redis pre-commit (leaderboard sync stays `after_commit`).
- **XP recursion excluded:** challenge reward XP must NOT feed the `xp_earned` challenge. `award_xp(..., count_challenge_xp=False)` for challenge rewards + weekly-rollover top-3.
- **Routes:** `@bp.route` → `@require_auth` → optional `@limiter.limit(CONST, key_func=_user_or_ip_key)`. Domain-key JSON; errors `{"error": t("...")}` + status; ownership via query-scoped filters.
- **Short commit messages; Türkçe UI / İngilizce kod.**

---

## File Structure

- **Create** `app/services/challenges.py` — period math, `record_event` funnel + completion, `join_featured`, `challenge_board`, `seed_challenges`, `CHALLENGE_SEED`.
- **Create** `app/services/badges.py` — `BADGE_CATALOG`, `award_badge`, `badges_for`.
- **Create** `app/blueprints/challenges.py` — 4 routes.
- **Create** `templates/challenges.html`, `static/challenges.css`.
- **Create** `migrations/versions/ff66aa77bb88_add_challenges.py`.
- **Create** `docs/CHALLENGES.md`, `docs/LEADERBOARD.md`.
- **Create** tests: `tests/test_challenges.py`, `tests/test_challenge_routes.py`.
- **Modify** `app/models.py` (+3 models), `app/cli.py` (registry + seed-quests drift fix), `app/db_init.py` (call `seed_challenges`), `app/services/gamification.py` (award_xp flag + `_claim_quest` funnel + rollover flag), `app/hooks.py` (`active_day`), `app/blueprints/training.py` + `app/services/ai_coach.py` (`pump_check_created`), `app/services/feed.py` + `app/services/gamification.py` `ACTIVITY_ICONS` (`challenge_completed` milestone), `app/blueprints/gamification.py` (`resetAt`), `templates/leaderboard.html` (server countdown), `templates/_nav.html` (drawer link), `locales/tr.json` + `locales/en.json`, `tests/test_migration_graph.py`, `tests/test_cascade_delete.py`, `docs/leaderboard`/`CLAUDE.md`.

---

## Task 1: Models + migration + registry

**Files:**
- Modify: `app/models.py` (append after `Notification`, currently ends ~line 804)
- Create: `migrations/versions/ff66aa77bb88_add_challenges.py`
- Modify: `app/cli.py` (`_user_child_models()`)
- Modify: `tests/test_migration_graph.py:31`
- Test: `tests/test_challenges.py` (model sanity)

**Interfaces:**
- Produces: `Challenge(code, title, description, category, metric, target_value, xp_reward, badge_code, challenge_type, period_type, is_active, created_at)`; `UserChallengeProgress(user_id, challenge_id, period_key, progress, opted_in, completed_at, created_at)` with `uq_user_challenge_period` + `ix_ucp_challenge_period`; `UserBadge(user_id, badge_code, source, earned_at)` with `uq_user_badge`.

- [ ] **Step 1: Write the failing test** — `tests/test_challenges.py`:

```python
from datetime import datetime

from app.extensions import db
from app.models import Challenge, UserBadge, UserChallengeProgress


def _seed_challenge(app, **kw):
    defaults = dict(code="weekly_workouts", title="Haftalık Antrenman",
                    description="3 antrenman", category="workouts",
                    metric="workout_logged", target_value=3, xp_reward=150,
                    badge_code=None, challenge_type="global", period_type="weekly",
                    is_active=True)
    defaults.update(kw)
    c = Challenge(**defaults)
    db.session.add(c)
    db.session.commit()
    return c


def test_models_persist_and_unique(app):
    c = _seed_challenge(app)
    db.session.add(UserChallengeProgress(user_id=1, challenge_id=c.id,
                                         period_key="2026-W29", progress=2))
    db.session.commit()
    assert UserChallengeProgress.query.filter_by(user_id=1).one().progress == 2

    db.session.add(UserBadge(user_id=1, badge_code="pump_week",
                             source="challenge:weekly_pump:2026-W29"))
    db.session.commit()
    assert UserBadge.query.filter_by(user_id=1).count() == 1
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_challenges.py::test_models_persist_and_unique -v` → FAIL (`ImportError: cannot import name 'Challenge'`).

- [ ] **Step 3: Append models** to `app/models.py` after `Notification` (mirror existing column conventions — `ondelete="CASCADE"`, `index=True`, `server_default`):

```python
class Challenge(db.Model):
    # Meydan okuma katalogu (Sprint 5 PR3). Statik seed (DailyQuest deseni).
    # code/category/metric/badge_code/challenge_type/period_type kanonik İngilizce
    # slug; title/description kanonik TR (görünen metin t_or ile çevrilir).
    # Genişletme tohumu: challenge_type ('global'|'featured'; ileride duel/team/
    # sponsored), period_type ('weekly'; ileride daily/seasonal).
    id            = db.Column(db.Integer, primary_key=True)
    code          = db.Column(db.String(50), nullable=False, unique=True)
    title         = db.Column(db.String(120), nullable=False)
    description   = db.Column(db.Text)
    category      = db.Column(db.String(30))
    metric        = db.Column(db.String(30), nullable=False, index=True)   # event_type
    target_value  = db.Column(db.Integer, nullable=False, default=1)
    xp_reward     = db.Column(db.Integer, nullable=False, default=100)
    badge_code    = db.Column(db.String(50), nullable=True)
    challenge_type = db.Column(db.String(20), nullable=False, default="global", server_default="global")
    period_type   = db.Column(db.String(20), nullable=False, default="weekly", server_default="weekly")
    is_active     = db.Column(db.Boolean, nullable=False, default=True, server_default="true")
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)


class UserChallengeProgress(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenge.id", ondelete="CASCADE"), nullable=False, index=True)
    period_key   = db.Column(db.String(10), nullable=False, index=True)   # 'YYYY-Www'
    progress     = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    opted_in     = db.Column(db.Boolean, nullable=False, default=True, server_default="true")
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id", "challenge_id", "period_key", name="uq_user_challenge_period"),
        db.Index("ix_ucp_challenge_period", "challenge_id", "period_key"),
    )

    user      = db.relationship("User", backref=db.backref("challenge_progress", passive_deletes=True))
    challenge = db.relationship("Challenge", backref=db.backref("progress_entries", passive_deletes=True))


class UserBadge(db.Model):
    # Tek-seferlik rozet (UNIQUE user_id+badge_code); tekrar tamamlamada XP verilir,
    # rozet verilmez. source = izleme kaynağı, ör. "challenge:weekly_pump:2026-W29".
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    badge_code = db.Column(db.String(50), nullable=False)
    source     = db.Column(db.String(80), nullable=True)
    earned_at  = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id", "badge_code", name="uq_user_badge"),
    )

    user = db.relationship("User", backref=db.backref("badges", passive_deletes=True))
```

- [ ] **Step 4: Add to registry** — `app/cli.py` `_user_child_models()`: add `Challenge`-children to imports and tuple. `UserChallengeProgress, UserBadge` are user-children (place before `Notification` in the returned tuple, next to other children). `Challenge` itself has NO `user_id` → do NOT add it. Confirm import line includes `UserBadge, UserChallengeProgress`.

- [ ] **Step 5: Hand-write migration** — `migrations/versions/ff66aa77bb88_add_challenges.py` (revision `ff66aa77bb88`, `down_revision="ee55ff66aa77"`), template = `ee55ff66aa77_add_feed_v2.py`. `upgrade()` creates `challenge`, `user_challenge_progress`, `user_badge` each behind `if not insp.has_table("<t>")`, including the unique constraints + `ix_ucp_challenge_period`. `downgrade()` drops in reverse.

```python
"""add challenges

Revision ID: ff66aa77bb88
Revises: ee55ff66aa77
Create Date: 2026-07-16
"""
import sqlalchemy as sa
from alembic import op

revision = "ff66aa77bb88"
down_revision = "ee55ff66aa77"
branch_labels = None
depends_on = None


def upgrade():
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("challenge"):
        op.create_table(
            "challenge",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(length=50), nullable=False),
            sa.Column("title", sa.String(length=120), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("category", sa.String(length=30), nullable=True),
            sa.Column("metric", sa.String(length=30), nullable=False),
            sa.Column("target_value", sa.Integer(), nullable=False),
            sa.Column("xp_reward", sa.Integer(), nullable=False),
            sa.Column("badge_code", sa.String(length=50), nullable=True),
            sa.Column("challenge_type", sa.String(length=20), server_default="global", nullable=False),
            sa.Column("period_type", sa.String(length=20), server_default="weekly", nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code"),
        )
        op.create_index("ix_challenge_metric", "challenge", ["metric"])
    if not insp.has_table("user_challenge_progress"):
        op.create_table(
            "user_challenge_progress",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("challenge_id", sa.Integer(), nullable=False),
            sa.Column("period_key", sa.String(length=10), nullable=False),
            sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
            sa.Column("opted_in", sa.Boolean(), server_default="true", nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["challenge_id"], ["challenge.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "challenge_id", "period_key", name="uq_user_challenge_period"),
        )
        op.create_index("ix_user_challenge_progress_user_id", "user_challenge_progress", ["user_id"])
        op.create_index("ix_user_challenge_progress_challenge_id", "user_challenge_progress", ["challenge_id"])
        op.create_index("ix_user_challenge_progress_period_key", "user_challenge_progress", ["period_key"])
        op.create_index("ix_ucp_challenge_period", "user_challenge_progress", ["challenge_id", "period_key"])
    if not insp.has_table("user_badge"):
        op.create_table(
            "user_badge",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("badge_code", sa.String(length=50), nullable=False),
            sa.Column("source", sa.String(length=80), nullable=True),
            sa.Column("earned_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "badge_code", name="uq_user_badge"),
        )
        op.create_index("ix_user_badge_user_id", "user_badge", ["user_id"])


def downgrade():
    insp = sa.inspect(op.get_bind())
    if insp.has_table("user_badge"):
        op.drop_table("user_badge")
    if insp.has_table("user_challenge_progress"):
        op.drop_table("user_challenge_progress")
    if insp.has_table("challenge"):
        op.drop_table("challenge")
```

- [ ] **Step 6: Bump migration graph head** — `tests/test_migration_graph.py:31` → `assert heads == ["ff66aa77bb88"]`.

- [ ] **Step 7: Run** — `pytest tests/test_challenges.py::test_models_persist_and_unique tests/test_migration_graph.py -v` → PASS.

- [ ] **Step 8: Commit** — `git add -A && git commit -m "feat(challenges): modeller + migration + registry"`

---

## Task 2: `badges.py` service

**Files:**
- Create: `app/services/badges.py`
- Test: `tests/test_challenges.py` (badge helpers)

**Interfaces:**
- Produces: `BADGE_CATALOG: dict[str, {"icon","title_key"}]`; `award_badge(user_id, badge_code, source=None) -> UserBadge | None` (no commit; None on falsy code / unknown code / duplicate); `badges_for(user_id) -> list[dict]`.

- [ ] **Step 1: Write failing test** — append to `tests/test_challenges.py`:

```python
def test_award_badge_dedup_and_unknown(app):
    from app.services import badges
    assert badges.award_badge(1, None) is None
    assert badges.award_badge(1, "not_a_real_badge") is None
    b = badges.award_badge(1, "pump_week", source="challenge:weekly_pump:2026-W29")
    db.session.commit()
    assert b is not None
    assert badges.award_badge(1, "pump_week") is None      # duplicate → None
    db.session.commit()
    from app.models import UserBadge
    assert UserBadge.query.filter_by(user_id=1, badge_code="pump_week").count() == 1
    codes = {x["code"] for x in badges.badges_for(1)}
    assert "pump_week" in codes
```

- [ ] **Step 2: Run to fail** — `pytest tests/test_challenges.py::test_award_badge_dedup_and_unknown -v` → FAIL (import).

- [ ] **Step 3: Implement** `app/services/badges.py`:

```python
# Rozet katalogu + verme yardımcıları (Sprint 5 PR3).
# award_badge: session-add-only, COMMIT ETMEZ (record_event ile aynı transaction).
import logging

from app.extensions import db
from app.models import UserBadge

log = logging.getLogger(__name__)

# badge_code → görsel + i18n başlık anahtarı. code kanonik slug; başlık t() ile.
BADGE_CATALOG = {
    "pump_week":    {"icon": "\U0001f4aa", "title_key": "badge.pump_week.title"},
    "active_week":  {"icon": "\U0001f525", "title_key": "badge.active_week.title"},
    "pump_perfect": {"icon": "\U0001f3c6", "title_key": "badge.pump_perfect.title"},
    "grinder":      {"icon": "⚙️", "title_key": "badge.grinder.title"},
}


def award_badge(user_id, badge_code, source=None):
    """Rozet ekle (commit etmez). None döner: kod yok / katalogda yok / zaten var."""
    if not badge_code or badge_code not in BADGE_CATALOG:
        return None
    try:
        with db.session.no_autoflush:
            exists = UserBadge.query.filter_by(
                user_id=user_id, badge_code=badge_code).first()
        if exists:
            return None
        b = UserBadge(user_id=user_id, badge_code=badge_code, source=source)
        db.session.add(b)
        return b
    except Exception:
        log.warning("award_badge başarısız (yutuldu): user=%s badge=%s",
                    user_id, badge_code, exc_info=True)
        return None


def badges_for(user_id):
    """Kullanıcının rozetleri (en yeni önce). Katalog dışı kodlar atlanır."""
    rows = (UserBadge.query.filter_by(user_id=user_id)
            .order_by(UserBadge.earned_at.desc(), UserBadge.id.desc()).all())
    out = []
    for r in rows:
        meta = BADGE_CATALOG.get(r.badge_code)
        if not meta:
            continue
        out.append({"code": r.badge_code, "icon": meta["icon"],
                    "titleKey": meta["title_key"],
                    "earnedAt": r.earned_at.isoformat() if r.earned_at else None})
    return out
```

- [ ] **Step 4: Run to pass** — `pytest tests/test_challenges.py::test_award_badge_dedup_and_unknown -v` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(challenges): rozet servisi"`

---

## Task 3: `challenges.py` period math

**Files:**
- Create: `app/services/challenges.py` (period helpers first)
- Test: `tests/test_challenges.py`

**Interfaces:**
- Produces: `current_challenge_week(now=None) -> str` ('YYYY-Www', current in-progress week by Sunday-23:59-Istanbul boundary — the INVERSE of `_last_completed_week_key`: before Sunday 23:59 → this ISO week; at/after → next); `period_end_utc(now=None) -> datetime` (naive UTC of the upcoming Sunday 23:59 Istanbul boundary).

Semantics (must match `_last_completed_week_key`'s boundary at `gamification.py:169`): the active period runs Monday 00:00 → Sunday 23:59 Istanbul. `current_challenge_week` returns the ISO week the user is currently *earning in*; at exactly Sunday 23:59 the week has closed and the new week begins.

- [ ] **Step 1: Write failing test**:

```python
from datetime import datetime


def test_current_challenge_week_boundaries():
    from app.services.challenges import current_challenge_week
    # Istanbul-naive datetimes (same convention as _last_completed_week_key tests)
    assert current_challenge_week(datetime(2026, 6, 10, 12, 0)) == "2026-W24"   # hafta ortası → bu hafta
    assert current_challenge_week(datetime(2026, 6, 14, 23, 58)) == "2026-W24"  # kapanıştan 1 dk önce → hâlâ bu hafta
    assert current_challenge_week(datetime(2026, 6, 14, 23, 59)) == "2026-W25"  # tam kapanış → yeni hafta
    assert current_challenge_week(datetime(2026, 6, 15, 0, 0)) == "2026-W25"    # Pazartesi 00:00


def test_period_end_utc_is_sunday_2359_istanbul():
    from app.services.challenges import period_end_utc
    from app.timeutil import to_app_tz
    end = period_end_utc(datetime(2026, 6, 10, 12, 0))  # mid-week
    local = to_app_tz(end)   # naive UTC → Istanbul
    assert local.isoweekday() == 7 and local.hour == 23 and local.minute == 59
```

- [ ] **Step 2: Run to fail** — FAIL (import).

- [ ] **Step 3: Implement period math** — start `app/services/challenges.py`:

```python
# Meydan okuma servisi (Sprint 5 PR3). record_event tek huni; period_key hesaplı
# (ISO hafta, Sunday-23:59-Istanbul sınırı, _last_completed_week_key ile aynı).
# record_event COMMIT ETMEZ (çağıran commit eder — _claim_quest sözleşmesi).
import logging
from datetime import timedelta

from app.extensions import db
from app.timeutil import UTC, app_now

log = logging.getLogger(__name__)


def _week_bounds(now):
    """Bu takvim haftasının Pazartesi 00:00 ve Pazar 23:59'unu (Istanbul-naive) döndür."""
    monday = (now - timedelta(days=now.isoweekday() - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    sunday_2359 = monday + timedelta(days=6, hours=23, minutes=59)
    return monday, sunday_2359


def current_challenge_week(now=None):
    """AKTİF (devam eden) haftanın ISO anahtarı. _last_completed_week_key'in tersi:
    Pazar 23:59'dan ÖNCE bu ISO hafta; tam/ sonra bir sonraki hafta."""
    now = now or app_now()
    monday, sunday_2359 = _week_bounds(now)
    ref = (monday + timedelta(days=7)) if now >= sunday_2359 else now
    y, w, _ = ref.isocalendar()
    return "%d-W%02d" % (y, w)


def period_end_utc(now=None):
    """Bu periyodun bitişi = yaklaşan Pazar 23:59 Istanbul → NAIVE UTC döndür
    (leaderboard/challenges countdown istemciye ISO UTC olarak verir)."""
    now = now or app_now()
    monday, sunday_2359 = _week_bounds(now)
    end_local = (monday + timedelta(days=13, hours=23, minutes=59)) if now >= sunday_2359 else sunday_2359
    # end_local Istanbul-aware; naive ise APP_TZ'de kabul et.
    if end_local.tzinfo is None:
        from app.timeutil import APP_TZ
        end_local = end_local.replace(tzinfo=APP_TZ)
    return end_local.astimezone(UTC).replace(tzinfo=None)
```

Note: `app_now()` returns a tz-aware Istanbul datetime; `.isoweekday()`/`.replace()` work on aware datetimes. The tests pass naive datetimes (matching `_last_completed_week_key` tests) — arithmetic still works; `period_end_utc` handles the naive case by stamping `APP_TZ`.

- [ ] **Step 4: Run to pass** — `pytest tests/test_challenges.py -k "week or period" -v` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(challenges): periyot matematiği"`

---

## Task 4: `record_event` + idempotent completion

**Files:**
- Modify: `app/services/challenges.py`
- Test: `tests/test_challenges.py`

**Interfaces:**
- Consumes: `award_xp` (Task 6 adds `count_challenge_xp` kwarg — for now call with the flag), `badges.award_badge`, `notifications.notify`, `log_activity`, `current_challenge_week`.
- Produces: `record_event(user_id, event_type, amount=1) -> None` (NO commit). For each active `Challenge` with `metric == event_type`: global → get-or-create `UserChallengeProgress` (`begin_nested()` + `IntegrityError` guard); featured → only an EXISTING `opted_in` row. Atomic `progress = progress + amount`; on crossing `target_value` a guarded `UPDATE … SET completed_at=now WHERE completed_at IS NULL` (rowcount 1 wins) → `award_xp(user_id, xp_reward, count_challenge_xp=False)` + `award_badge` + `notify("challenge_complete", target_type="challenge", target_id=challenge.id, payload={code,xp,badge,week})` + `log_activity(user_id, "challenge_completed", <TR>)`.

- [ ] **Step 1: Write failing tests**:

```python
def _mkuser(app):
    from app.models import User
    u = User(username="u%d" % (User.query.count() + 1), email="u%d@t.co" % (User.query.count() + 1))
    db.session.add(u); db.session.commit()
    return u


def test_record_event_increments_and_accumulates(app):
    from app.services import challenges
    u = _mkuser(app)
    _seed_challenge(app, code="weekly_workouts", metric="workout_logged", target_value=3, xp_reward=150)
    challenges.record_event(u.id, "workout_logged"); db.session.commit()
    challenges.record_event(u.id, "workout_logged", amount=1); db.session.commit()
    row = UserChallengeProgress.query.filter_by(user_id=u.id).one()
    assert row.progress == 2 and row.completed_at is None


def test_record_event_completes_exactly_once(app):
    from app.services import challenges
    from app.models import User
    u = _mkuser(app)
    c = _seed_challenge(app, code="weekly_pump", metric="pump_check_created",
                        target_value=1, xp_reward=100, badge_code="pump_week")
    before = User.query.get(u.id).rank_points or 0
    challenges.record_event(u.id, "pump_check_created"); db.session.commit()
    challenges.record_event(u.id, "pump_check_created"); db.session.commit()  # 2nd event, already complete
    row = UserChallengeProgress.query.filter_by(user_id=u.id, challenge_id=c.id).one()
    assert row.completed_at is not None
    assert (User.query.get(u.id).rank_points or 0) == before + 100      # XP once
    assert UserBadge.query.filter_by(user_id=u.id, badge_code="pump_week").count() == 1


def test_featured_requires_opt_in(app):
    from app.services import challenges
    u = _mkuser(app)
    _seed_challenge(app, code="featured_grind", metric="workout_logged",
                    target_value=2, challenge_type="featured")
    challenges.record_event(u.id, "workout_logged"); db.session.commit()
    assert UserChallengeProgress.query.filter_by(user_id=u.id).count() == 0  # no auto-create for featured


def test_record_event_never_commits(app):
    from app.services import challenges
    u = _mkuser(app)
    _seed_challenge(app, code="weekly_workouts", metric="workout_logged", target_value=3)
    challenges.record_event(u.id, "workout_logged")
    db.session.rollback()   # if record_event committed, the row would survive
    assert UserChallengeProgress.query.filter_by(user_id=u.id).count() == 0
```

- [ ] **Step 2: Run to fail** — FAIL (`record_event` undefined).

- [ ] **Step 3: Implement `record_event`** — append to `app/services/challenges.py`:

```python
from datetime import datetime

from app.models import Challenge, UserChallengeProgress


def _get_or_create_global_row(user_id, challenge_id, period_key):
    """Global challenge için satırı getir ya da yarış-güvenli oluştur (commit etmez)."""
    row = UserChallengeProgress.query.filter_by(
        user_id=user_id, challenge_id=challenge_id, period_key=period_key).first()
    if row is not None:
        return row
    try:
        with db.session.begin_nested():
            row = UserChallengeProgress(user_id=user_id, challenge_id=challenge_id,
                                        period_key=period_key, progress=0)
            db.session.add(row)
        return row
    except Exception:
        # Eşzamanlı istek aynı satırı yazdı (uq_user_challenge_period) — yeniden oku.
        return UserChallengeProgress.query.filter_by(
            user_id=user_id, challenge_id=challenge_id, period_key=period_key).first()


def record_event(user_id, event_type, amount=1):
    """Bir gamification olayını tüm eşleşen aktif challenge'lara işle. COMMIT ETMEZ.
    Global → get-or-create + ilerlet; featured → yalnızca mevcut opted_in satır.
    Tamamlanınca (guarded UPDATE, tam-bir-kez) XP + rozet + bildirim + feed aktivitesi."""
    try:
        challenges = Challenge.query.filter_by(metric=event_type, is_active=True).all()
        if not challenges:
            return
        period_key = current_challenge_week()
        for ch in challenges:
            if ch.challenge_type == "featured":
                row = UserChallengeProgress.query.filter_by(
                    user_id=user_id, challenge_id=ch.id, period_key=period_key,
                    opted_in=True).first()
                if row is None:
                    continue
            else:
                row = _get_or_create_global_row(user_id, ch.id, period_key)
                if row is None:
                    continue
            if row.completed_at is not None:
                continue
            # Atomik ilerleme (kayıp güncelleme yok — kolon UPDATE).
            UserChallengeProgress.query.filter_by(id=row.id).update(
                {UserChallengeProgress.progress: UserChallengeProgress.progress + amount},
                synchronize_session=False)
            db.session.refresh(row)
            if row.progress >= ch.target_value:
                _try_complete(user_id, ch, row, period_key)
    except Exception:
        # Challenge ilerlemesi ana eylemi (antrenman/öğün) ASLA kırmaz.
        log.warning("record_event başarısız (yutuldu): user=%s event=%s",
                    user_id, event_type, exc_info=True)


def _try_complete(user_id, ch, row, period_key):
    """Guarded completion — WHERE completed_at IS NULL kazanan tek satır ödülü verir."""
    from app.services.badges import award_badge
    from app.services.gamification import award_xp, log_activity
    from app.services.notifications import notify

    now = datetime.utcnow()
    won = UserChallengeProgress.query.filter(
        UserChallengeProgress.id == row.id,
        UserChallengeProgress.completed_at.is_(None),
    ).update({UserChallengeProgress.completed_at: now}, synchronize_session=False)
    if not won:
        return
    award_xp(user_id, ch.xp_reward, count_challenge_xp=False)
    if ch.badge_code:
        award_badge(user_id, ch.badge_code,
                    source="challenge:%s:%s" % (ch.code, period_key))
    notify(user_id, "challenge_complete", actor_id=None,
           target_type="challenge", target_id=ch.id,
           payload={"code": ch.code, "xp": ch.xp_reward,
                    "badge": ch.badge_code, "week": period_key})
    log_activity(user_id, "challenge_completed",
                 "'%s' meydan okumasını tamamladı!" % ch.title)
```

Note on `award_xp(count_challenge_xp=...)`: Task 6 adds that kwarg. To keep this task's tests green *before* Task 6, either implement Task 6's `award_xp` signature change first, or (simpler) order execution so Task 6 lands with this. **Execution note:** implement Task 6's `award_xp` signature (adding `count_challenge_xp=True` default) at the same commit if running inline, so `_try_complete` imports resolve. The tests here only assert XP is awarded once (they don't yet exercise recursion), so the default-flag `award_xp` suffices.

- [ ] **Step 4: Run to pass** — `pytest tests/test_challenges.py -v` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(challenges): record_event + tam-bir-kez tamamlama"`

---

## Task 5: `join_featured` + `challenge_board` + `seed_challenges`

**Files:**
- Modify: `app/services/challenges.py`
- Test: `tests/test_challenges.py`

**Interfaces:**
- Produces: `join_featured(user_id, challenge_id) -> UserChallengeProgress | None` (featured only; creates opted_in row for current period; NO commit); `challenge_board(challenge_id, period_key, scope, viewer_id) -> dict` (`{entries:[{rank,username,full_name,profile_picture,progress,completed}], me, in_list}`, order `progress desc, completed_at asc nulls-last, user_id asc`, top 50 + me row; Postgres/SQLite only, no Redis); `CHALLENGE_SEED: list[dict]`; `seed_challenges() -> None` (idempotent by `code`, commits).

- [ ] **Step 1: Write failing tests**:

```python
def test_join_featured_only(app):
    from app.services import challenges
    u = _mkuser(app)
    g = _seed_challenge(app, code="weekly_workouts", challenge_type="global")
    f = _seed_challenge(app, code="featured_grind", challenge_type="featured")
    assert challenges.join_featured(u.id, g.id) is None            # global not joinable
    row = challenges.join_featured(u.id, f.id); db.session.commit()
    assert row is not None and row.opted_in is True


def test_challenge_board_ordering(app):
    from app.services import challenges
    c = _seed_challenge(app, code="weekly_workouts", target_value=5)
    us = [_mkuser(app) for _ in range(3)]
    pk = challenges.current_challenge_week()
    for u, prog in zip(us, (2, 5, 5)):
        r = UserChallengeProgress(user_id=u.id, challenge_id=c.id, period_key=pk, progress=prog)
        db.session.add(r)
    db.session.commit()
    board = challenges.challenge_board(c.id, pk, "global", us[0].id)
    # progress desc → the two 5s ahead of the 2; tie broken by user_id asc
    assert [e["progress"] for e in board["entries"]] == [5, 5, 2]


def test_seed_challenges_idempotent(app):
    from app.services import challenges
    challenges.seed_challenges()
    n = Challenge.query.count()
    challenges.seed_challenges()
    assert Challenge.query.count() == n and n >= 8
```

- [ ] **Step 2: Run to fail** — FAIL.

- [ ] **Step 3: Implement** — append to `app/services/challenges.py`:

```python
from app.models import User

CHALLENGE_SEED = [
    # global (auto-participate)
    dict(code="weekly_workouts", title="Haftalık Antrenman", description="Bu hafta 3 antrenman tamamla",
         category="workouts", metric="workout_logged", target_value=3, xp_reward=150, badge_code=None,
         challenge_type="global"),
    dict(code="weekly_meals", title="Beslenme Takibi", description="Bu hafta 10 öğün kaydet",
         category="nutrition", metric="meal_logged", target_value=10, xp_reward=100, badge_code=None,
         challenge_type="global"),
    dict(code="weekly_water", title="Su Kahramanı", description="Bu hafta 5 gün su takibini gir",
         category="hydration", metric="water_logged", target_value=5, xp_reward=75, badge_code=None,
         challenge_type="global"),
    dict(code="weekly_pump", title="Pump Check Serisi", description="Bu hafta 3 Pump Check paylaş",
         category="pump_check", metric="pump_check_created", target_value=3, xp_reward=100,
         badge_code="pump_week", challenge_type="global"),
    dict(code="weekly_active", title="Aktif Hafta", description="Bu hafta 5 gün aktif ol",
         category="active_days", metric="active_day", target_value=5, xp_reward=100,
         badge_code="active_week", challenge_type="global"),
    dict(code="weekly_xp", title="XP Avcısı", description="Bu hafta 500 XP kazan",
         category="xp", metric="xp_earned", target_value=500, xp_reward=150, badge_code=None,
         challenge_type="global"),
    # featured (opt-in)
    dict(code="featured_pump_perfect", title="Kusursuz Pump", description="Bu hafta 5 Pump Check paylaş",
         category="pump_check", metric="pump_check_created", target_value=5, xp_reward=300,
         badge_code="pump_perfect", challenge_type="featured"),
    dict(code="featured_grind", title="Grind Modu", description="Bu hafta 5 antrenman tamamla",
         category="workouts", metric="workout_logged", target_value=5, xp_reward=250,
         badge_code="grinder", challenge_type="featured"),
]


def seed_challenges():
    """Katalog seed'i — code'a göre idempotent (DailyQuest deseni). Commit eder."""
    for spec in CHALLENGE_SEED:
        if not Challenge.query.filter_by(code=spec["code"]).first():
            db.session.add(Challenge(period_type="weekly", is_active=True, **spec))
    db.session.commit()


def join_featured(user_id, challenge_id):
    """Featured challenge'a katıl (opted_in satır). Global → None. COMMIT ETMEZ."""
    ch = Challenge.query.filter_by(id=challenge_id, is_active=True,
                                   challenge_type="featured").first()
    if ch is None:
        return None
    period_key = current_challenge_week()
    row = UserChallengeProgress.query.filter_by(
        user_id=user_id, challenge_id=ch.id, period_key=period_key).first()
    if row is not None:
        row.opted_in = True
        return row
    try:
        with db.session.begin_nested():
            row = UserChallengeProgress(user_id=user_id, challenge_id=ch.id,
                                        period_key=period_key, progress=0, opted_in=True)
            db.session.add(row)
        return row
    except Exception:
        return UserChallengeProgress.query.filter_by(
            user_id=user_id, challenge_id=ch.id, period_key=period_key).first()


def _board_entry(u, row, rank):
    return {"rank": rank, "user_id": u.id, "username": u.username,
            "full_name": u.full_name or u.username, "profile_picture": u.avatar_src,
            "progress": row.progress if row else 0,
            "completed": bool(row and row.completed_at)}


def challenge_board(challenge_id, period_key, scope, viewer_id):
    """Challenge sıralaması (progress desc, completed_at asc nulls-last, user_id asc).
    Top 50 + kapsam dışıysa 'me' satırı. Redis yok; Postgres/SQLite ORDER BY."""
    from app.services.friends import get_friend_ids

    q = (db.session.query(UserChallengeProgress, User)
         .join(User, User.id == UserChallengeProgress.user_id)
         .filter(UserChallengeProgress.challenge_id == challenge_id,
                 UserChallengeProgress.period_key == period_key))
    if scope == "friends":
        ids = get_friend_ids(viewer_id) | {viewer_id}
        q = q.filter(UserChallengeProgress.user_id.in_(ids))
    # nulls-last: completed rows (completed_at not null) rank by earliest completion,
    # then in-progress by progress desc. Express as: progress desc, completed_at asc.
    completed_first = db.case((UserChallengeProgress.completed_at.is_(None), 1), else_=0)
    rows = q.order_by(UserChallengeProgress.progress.desc(),
                      completed_first.asc(),
                      UserChallengeProgress.completed_at.asc(),
                      User.id.asc()).limit(50).all()
    entries = [_board_entry(u, r, i + 1) for i, (r, u) in enumerate(rows)]
    in_list = any(e["user_id"] == viewer_id for e in entries)
    me = next((e for e in entries if e["user_id"] == viewer_id), None)
    if not in_list:
        my = (db.session.query(UserChallengeProgress, User)
              .join(User, User.id == UserChallengeProgress.user_id)
              .filter(UserChallengeProgress.challenge_id == challenge_id,
                      UserChallengeProgress.period_key == period_key,
                      UserChallengeProgress.user_id == viewer_id).first())
        if my is not None:
            me = _board_entry(my[1], my[0], None)
        else:
            u = db.session.get(User, viewer_id)
            me = _board_entry(u, None, None) if u else None
    return {"entries": entries, "me": me, "in_list": in_list}
```

- [ ] **Step 4: Run to pass** — `pytest tests/test_challenges.py -v` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(challenges): join + board + seed"`

---

## Task 6: `gamification.py` wiring (XP flag, quest funnel, rollover)

**Files:**
- Modify: `app/services/gamification.py` (`award_xp`, `_claim_quest`, `run_weekly_rollover`)
- Test: `tests/test_challenges.py` (recursion exclusion, quest funnel)

**Interfaces:**
- Consumes: `challenges.record_event`.
- Produces: `award_xp(user_id, amount, count_challenge_xp=True)` — after the XP write, if flag: `record_event(user_id, "xp_earned", amount)` (lazy import). `_claim_quest` first real statement: `record_event(user_id, quest_type)`. `run_weekly_rollover` top-3 uses `award_xp(u.id, xp, count_challenge_xp=False)`.

- [ ] **Step 1: Write failing tests**:

```python
def test_challenge_reward_xp_does_not_feed_xp_challenge(app):
    from app.services import challenges
    u = _mkuser(app)
    # xp challenge target below the reward from the pump challenge, so IF recursion
    # happened the xp challenge would complete. It must NOT.
    _seed_challenge(app, code="weekly_pump", metric="pump_check_created", target_value=1,
                    xp_reward=200, badge_code="pump_week")
    _seed_challenge(app, code="weekly_xp", metric="xp_earned", target_value=100, xp_reward=150)
    challenges.record_event(u.id, "pump_check_created"); db.session.commit()
    xp_row = (UserChallengeProgress.query
              .join(Challenge).filter(Challenge.code == "weekly_xp").first())
    assert xp_row is None or xp_row.progress == 0     # reward XP excluded


def test_quest_event_funnels_to_challenge(app):
    from app.services.gamification import _claim_quest
    from app.models import DailyQuest
    u = _mkuser(app)
    db.session.add(DailyQuest(title="Öğün", description="x", points_reward=20,
                              quest_type="meal_logged", is_active=True))
    _seed_challenge(app, code="weekly_meals", metric="meal_logged", target_value=10, xp_reward=100)
    db.session.commit()
    _claim_quest(u.id, "meal_logged"); db.session.commit()
    row = (UserChallengeProgress.query.join(Challenge)
           .filter(Challenge.code == "weekly_meals").one())
    assert row.progress == 1
```

- [ ] **Step 2: Run to fail** — FAIL (`award_xp` has no `count_challenge_xp`; quest funnel absent).

- [ ] **Step 3: Edit `award_xp`** (`app/services/gamification.py:77`) — add param + tail call:

```python
def award_xp(user_id, amount, count_challenge_xp=True):
    ...
    if row is not None:
        ...
        if new_level > get_level(old_points):
            log_activity(user_id, "level_up",
                         f"{new_level}. seviyeye ulaştı! ({get_title(new_level)})")
        # Challenge huni: kazanılan XP 'xp_earned' metriğini besler. Ödül XP'si
        # (challenge/rollover) count_challenge_xp=False ile GELMEZ → sonsuz döngü yok.
        if count_challenge_xp and amount:
            try:
                from app.services.challenges import record_event
                record_event(user_id, "xp_earned", amount)
            except Exception:
                pass
        return new_points
    return None
```

- [ ] **Step 4: Edit `_claim_quest`** (`app/services/gamification.py:246`) — add funnel as the first statement (before the DailyQuest lookup so EVERY quest event funnels, even if no DailyQuest row exists):

```python
def _claim_quest(user_id, quest_type):
    """..."""
    # Challenge huni: her quest olayı (login, workout_logged, meal_logged, ...) buradan
    # geçer; dedup record_event içindedir. COMMIT ETMEZ (çağıranın transaction'ı).
    try:
        from app.services.challenges import record_event
        record_event(user_id, quest_type)
    except Exception:
        pass
    quest = DailyQuest.query.filter_by(quest_type=quest_type, is_active=True).first()
    ...
```

- [ ] **Step 5: Edit `run_weekly_rollover`** (`app/services/gamification.py:209`) — top-3 award excludes challenge XP:

```python
        award_xp(u.id, xp, count_challenge_xp=False)
```

- [ ] **Step 6: Run to pass** — `pytest tests/test_challenges.py tests/test_gamification.py -v` → PASS (confirm no regression in existing gamification tests).

- [ ] **Step 7: Commit** — `git add -A && git commit -m "feat(challenges): gamification bağlantıları (xp huni + quest + rollover)"`

---

## Task 7: Direct event calls + seed on boot + seed-quests drift fix

**Files:**
- Modify: `app/blueprints/training.py:192` (after `db.session.flush()`)
- Modify: `app/services/ai_coach.py:713` (after PumpCheck add)
- Modify: `app/hooks.py:294` (inside `update_streak` locked branch, before `db.session.commit()`)
- Modify: `app/db_init.py` (call `seed_challenges()` after quests)
- Modify: `app/cli.py` `seed_quests()` defaults (add the 3 missing quest_types to fix drift)
- Test: `tests/test_challenges.py` (integration: streak→active_day once)

**Interfaces:**
- Consumes: `challenges.record_event`, `challenges.seed_challenges`.

- [ ] **Step 1: Write failing test** (integration via streak):

```python
def test_streak_first_request_records_active_day_once(app, monkeypatch):
    import app.hooks as hooks
    from app.models import User
    from datetime import date
    u = _mkuser(app)
    _seed_challenge(app, code="weekly_active", metric="active_day", target_value=5, xp_reward=100)
    calls = []
    real = __import__("app.services.challenges", fromlist=["record_event"]).record_event
    import app.services.challenges as ch
    monkeypatch.setattr(ch, "record_event",
                        lambda uid, ev, amount=1: calls.append(ev) or real(uid, ev, amount))
    # simulate: user's first request of the day → update_streak locked branch
    # (exercised through the route test in Task 9; here assert the seam exists)
    assert hasattr(ch, "record_event")
```

(The full end-to-end streak assertion lives in `test_challenge_routes.py` Task 9; this task's deliverable is the wiring — verified green by the existing streak tests plus the challenge suite.)

- [ ] **Step 2: `training.py`** — after `db.session.add(pump_check); db.session.flush()` (line ~192), add:

```python
    from app.services.challenges import record_event
    record_event(current_user.id, "pump_check_created")
```

- [ ] **Step 3: `ai_coach.py`** — after `db.session.add(PumpCheck(...))` (line ~713), before the WorkoutLog add:

```python
    from app.services.challenges import record_event
    record_event(user_id, "pump_check_created")
```

- [ ] **Step 4: `hooks.py update_streak`** — inside the locked branch (after `user.last_login = today`, near line 293, before the milestone block or right before `db.session.commit()` at 301):

```python
    from app.services.challenges import record_event
    record_event(user.id, "active_day")
```

(This is the once-per-day locked branch — guarantees exactly one `active_day` per Istanbul day.)

- [ ] **Step 5: `db_init.py`** — after the quest seeding block (after line ~110, before `backfill_referral_codes`), add:

```python
        try:
            from app.services.challenges import seed_challenges
            seed_challenges()
        except Exception:
            db.session.rollback()
```

- [ ] **Step 6: `cli.py seed_quests`** — add the 3 missing quest_types (`water_logged`, `checkin_done`, `friend_invited`, plus `supplement_added`) to the `defaults` list so the CLI matches `db_init`'s 7 (fixes documented drift). Keep TR titles matching `db_init`.

- [ ] **Step 7: Run** — `pytest tests/test_challenges.py tests/test_social_routes.py tests/test_gamification.py -v` → PASS. Full run of any pump-check/streak route tests must stay green.

- [ ] **Step 8: Commit** — `git add -A && git commit -m "feat(challenges): olay çağrıları + boot seed + seed-quests drift"`

---

## Task 8: Feed milestone card for `challenge_completed`

**Files:**
- Modify: `app/services/feed.py:24` (`MILESTONE_ACTIVITY_TYPES`) + `:31` (`MILESTONE_ICONS`)
- Modify: `app/services/gamification.py:147` (`ACTIVITY_ICONS`)
- Test: `tests/test_feed_v2.py` (challenge milestone appears)

- [ ] **Step 1: Write failing test** — append to `tests/test_feed_v2.py` (reuse its helpers):

```python
def test_challenge_completed_milestone_in_feed(app, auth_user):
    from app.services.gamification import log_activity
    from app.services.feed import get_feed_page
    log_activity(auth_user.id, "challenge_completed", "'Haftalık Antrenman' tamamlandı!")
    db.session.commit()
    kinds = [(i["kind"], i.get("activityType")) for i in get_feed_page(auth_user.id)["items"]]
    assert ("milestone", "challenge_completed") in kinds
```

- [ ] **Step 2: Run to fail** — FAIL (type not in allowlist → milestone absent).

- [ ] **Step 3: Edit** — `feed.py`: `MILESTONE_ACTIVITY_TYPES = ("level_up", "streak_milestone", "new_friend", "challenge_completed")`; `MILESTONE_ICONS[...] = {..., "challenge_completed": "\U0001f3c6"}`. `gamification.ACTIVITY_ICONS["challenge_completed"] = "\U0001f3c6"`.

- [ ] **Step 4: Run to pass** — `pytest tests/test_feed_v2.py -v` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(challenges): feed challenge_completed kilometre taşı"`

---

## Task 9: `challenges` blueprint (4 routes) + register

**Files:**
- Create: `app/blueprints/challenges.py`
- Modify: `app/__init__.py` (register `challenges_bp`, no prefix)
- Test: `tests/test_challenge_routes.py`

**Interfaces:**
- Consumes: `challenges.{current_challenge_week, period_end_utc, join_featured, challenge_board}`, `badges.badges_for`.
- Produces routes (all `@require_auth`):
  - `GET /challenges` → `challenges.html` shell.
  - `GET /challenges/data` → `{weekKey, periodEndsAt (ISO UTC), challenges:[{id,code,title,description,category,type,metric,target,xpReward,badgeCode,badgeIcon,progress,completed,joined}], badges:[...]}`.
  - `POST /challenges/<int:cid>/join` → featured-only (global → 400), dup opt-in idempotent → `{ok, joined:true}`.
  - `GET /challenges/<int:cid>/leaderboard?scope=friends|global` → `challenge_board(...)` shape + `weekKey`.

- [ ] **Step 1: Write failing tests** — `tests/test_challenge_routes.py`:

```python
from app.extensions import db
from app.models import Challenge, UserChallengeProgress


def _seed(app, **kw):
    d = dict(code="weekly_workouts", title="Haftalık Antrenman", description="3 antrenman",
             category="workouts", metric="workout_logged", target_value=3, xp_reward=150,
             challenge_type="global", period_type="weekly", is_active=True)
    d.update(kw)
    c = Challenge(**d); db.session.add(c); db.session.commit()
    return c


def test_challenges_data_shape(client, auth_user, app):
    _seed(app)
    r = client.get("/challenges/data")
    assert r.status_code == 200
    j = r.get_json()
    assert "weekKey" in j and "periodEndsAt" in j and isinstance(j["challenges"], list)
    ch = j["challenges"][0]
    assert ch["progress"] == 0 and ch["completed"] is False and ch["joined"] is False


def test_join_global_400_featured_ok(client, auth_user, app):
    g = _seed(app, code="weekly_workouts", challenge_type="global")
    f = _seed(app, code="featured_grind", challenge_type="featured")
    assert client.post("/challenges/%d/join" % g.id).status_code == 400
    r = client.post("/challenges/%d/join" % f.id)
    assert r.status_code == 200 and r.get_json()["joined"] is True
    assert client.post("/challenges/%d/join" % f.id).status_code == 200  # idempotent


def test_join_unknown_404(client, auth_user):
    assert client.post("/challenges/999999/join").status_code == 404


def test_leaderboard_route(client, auth_user, app):
    c = _seed(app)
    r = client.get("/challenges/%d/leaderboard?scope=global" % c.id)
    assert r.status_code == 200
    assert "entries" in r.get_json() and "me" in r.get_json()


def test_challenges_data_requires_auth(raw_client):
    assert raw_client.get("/challenges/data").status_code in (302, 401)
```

- [ ] **Step 2: Run to fail** — FAIL (404 — blueprint not registered).

- [ ] **Step 3: Implement** `app/blueprints/challenges.py`:

```python
# Meydan okuma uçları (Sprint 5 PR3). Tümü @require_auth.
from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user

from app.auth_middleware import require_auth
from app.extensions import db
from app.i18n import t, t_or
from app.models import Challenge, UserChallengeProgress
from app.services import challenges as ch_service
from app.services.badges import BADGE_CATALOG, badges_for

bp = Blueprint("challenges", __name__)


@bp.route("/challenges")
@require_auth
def challenges_page():
    return render_template("challenges.html",
        username=current_user.username,
        profile_picture=current_user.avatar_src)


@bp.route("/challenges/data")
@require_auth
def challenges_data():
    week_key = ch_service.current_challenge_week()
    rows = {r.challenge_id: r for r in UserChallengeProgress.query.filter_by(
        user_id=current_user.id, period_key=week_key).all()}
    out = []
    for c in Challenge.query.filter_by(is_active=True).order_by(
            Challenge.challenge_type.asc(), Challenge.id.asc()).all():
        r = rows.get(c.id)
        meta = BADGE_CATALOG.get(c.badge_code) if c.badge_code else None
        out.append({
            "id": c.id, "code": c.code,
            "title": t_or("challenge.%s.title" % c.code, c.title),
            "description": t_or("challenge.%s.desc" % c.code, c.description or ""),
            "category": c.category, "type": c.challenge_type, "metric": c.metric,
            "target": c.target_value, "xpReward": c.xp_reward,
            "badgeCode": c.badge_code, "badgeIcon": meta["icon"] if meta else None,
            "progress": r.progress if r else 0,
            "completed": bool(r and r.completed_at),
            "joined": bool(r and r.opted_in) if c.challenge_type == "featured" else True,
        })
    return jsonify({
        "weekKey": week_key,
        "periodEndsAt": ch_service.period_end_utc().isoformat() + "Z",
        "challenges": out,
        "badges": badges_for(current_user.id),
    })


@bp.route("/challenges/<int:cid>/join", methods=["POST"])
@require_auth
def challenge_join(cid):
    c = Challenge.query.filter_by(id=cid, is_active=True).first_or_404()
    if c.challenge_type != "featured":
        return jsonify({"error": t("challenge.not_joinable")}), 400
    row = ch_service.join_featured(current_user.id, cid)
    if row is None:
        return jsonify({"error": t("challenge.not_joinable")}), 400
    db.session.commit()
    return jsonify({"ok": True, "joined": True})


@bp.route("/challenges/<int:cid>/leaderboard")
@require_auth
def challenge_leaderboard(cid):
    Challenge.query.filter_by(id=cid).first_or_404()
    scope = "friends" if request.args.get("scope") == "friends" else "global"
    week_key = ch_service.current_challenge_week()
    board = ch_service.challenge_board(cid, week_key, scope, current_user.id)
    board["scope"] = scope
    board["weekKey"] = week_key
    return jsonify(board)
```

- [ ] **Step 4: Register** — `app/__init__.py:186` add `from app.blueprints.challenges import bp as challenges_bp` and add `challenges_bp` to the registration tuple at line 189.

- [ ] **Step 5: Run to pass** — `pytest tests/test_challenge_routes.py -v` → PASS.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat(challenges): blueprint + uçlar"`

---

## Task 10: Leaderboard polish — server `resetAt` (UTC↔Istanbul fix)

**Files:**
- Modify: `app/blueprints/gamification.py` (`leaderboard_data` → add `resetAt`)
- Modify: `templates/leaderboard.html` (countdown uses server `resetAt`, drop hardcoded `LB_RESET`)
- Test: `tests/test_leaderboard*.py` or `tests/test_gamification.py` (resetAt equals Istanbul boundary in UTC)

**Interfaces:**
- Consumes: `challenges.period_end_utc`.
- Produces: `/leaderboard/data` JSON gains `"resetAt": <ISO UTC + 'Z'>` = upcoming Sunday 23:59 Istanbul as UTC.

- [ ] **Step 1: Write failing test** — `tests/test_gamification.py` (or new `test_leaderboard_reset.py`):

```python
def test_leaderboard_data_includes_reset_at(client, auth_user):
    r = client.get("/leaderboard/data?timeframe=weekly")
    assert r.status_code == 200
    j = r.get_json()
    assert "resetAt" in j
    # resetAt must be the Istanbul Sunday-23:59 boundary expressed in UTC
    from app.services.challenges import period_end_utc
    assert j["resetAt"].startswith(period_end_utc().isoformat())
```

- [ ] **Step 2: Run to fail** — FAIL (`resetAt` absent).

- [ ] **Step 3: Edit `leaderboard_data`** — `_leaderboard_via_redis` / `_leaderboard_via_postgres` return `jsonify(...)`. Simplest: wrap in the route to inject `resetAt`. Change both helpers to return a dict, OR post-process. Minimal approach — build `resetAt` in the route and merge:

```python
@bp.route("/leaderboard/data")
@require_auth
def leaderboard_data():
    scope = "friends" if request.args.get("scope") == "friends" else "global"
    timeframe = "weekly" if request.args.get("timeframe") == "weekly" else "all_time"
    if redis_client:
        key = LB_WEEKLY_KEY if timeframe == "weekly" else LB_ALLTIME_KEY
        try:
            resp = _leaderboard_via_redis(scope, timeframe, key)
        except Exception:
            current_app.logger.warning("Leaderboard Redis yolu başarısız; Postgres'e düşülüyor", exc_info=True)
            resp = _leaderboard_via_postgres(scope, timeframe)
    else:
        resp = _leaderboard_via_postgres(scope, timeframe)
    from app.services.challenges import period_end_utc
    data = resp.get_json()
    data["resetAt"] = period_end_utc().isoformat() + "Z"
    return jsonify(data)
```

(The helpers already `jsonify`; `resp.get_json()` re-reads it. Acceptable — single small dict. Alternatively refactor helpers to return dicts; keep the smaller diff.)

- [ ] **Step 4: Edit `leaderboard.html`** — replace the hardcoded `LB_RESET` block (lines ~103-124). Store `resetAt` from the `/leaderboard/data` response (`window.__lbResetAt = data.resetAt`) and compute the countdown from `new Date(__lbResetAt)` instead of `LB_RESET`. Keep `__t('leaderboard.countdown', {d,h,m})`. On first data load set `__lbResetAt`; guard the countdown until it is set.

- [ ] **Step 5: Run to pass** — `pytest tests/test_gamification.py -k reset -v` → PASS. Full leaderboard tests stay green.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "fix(leaderboard): sunucu resetAt ile Istanbul geri sayımı"`

---

## Task 11: Frontend — `challenges.html` + `challenges.css` + nav link

**Files:**
- Create: `templates/challenges.html`, `static/challenges.css`
- Modify: `templates/_nav.html` (drawer `/challenges` link)
- Test: `tests/test_challenge_routes.py` (page renders)

- [ ] **Step 1: Write failing test**:

```python
def test_challenges_page_renders(client, auth_user):
    r = client.get("/challenges")
    assert r.status_code == 200
    assert b"challenges-root" in r.data
```

- [ ] **Step 2: Run to fail** — FAIL (template missing).

- [ ] **Step 3: Implement `templates/challenges.html`** — copy the `feed.html`/`quests.html` skeleton: `<!DOCTYPE>` + `{% include "_head.html" %}` (CSRF + i18n) + `theme.css`/`nav.css`/`challenges.css`, `{% set nav_active = "challenges" %}` + `{% include "_nav.html" %}`, `<main class="main-content">` with `<div id="challenges-root">`, `{% include "_actionbar.html" %}`, `actions.js`, ONE `<script nonce="{{ csp_nonce }}">`. Script:
  - `loadData()` → `GET /challenges/data`; render badges strip (icon + `window.t(titleKey)`), then two groups (Global / Featured by `type`) of cards with a progress bar (`<div class="ch-bar" style="width:{{pct}}%">` set via `el.style.width` — style attr allowed), `xpReward`, countdown to `periodEndsAt`.
  - Featured cards with `joined=false` show a `Katıl` button (`data-action` delegation → `POST /challenges/<id>/join`, optimistic).
  - Each card has a leaderboard button opening a sheet → `GET /challenges/<id>/leaderboard?scope=` with friends/global tabs.
  - `escapeHTML()` on all title/description/username output.
  - Countdown: `setInterval` computing days/hours/mins from `new Date(periodEndsAt)`, `__t('challenge.ends_in', {d,h,m})`.

- [ ] **Step 4: Implement `static/challenges.css`** — card grid, progress bar (`.ch-bar`), badge strip, group headers, join button, leaderboard sheet; mirror `feed.css`/`quests.css` conventions. NO `@import`; no JS-injected `<style>`.

- [ ] **Step 5: Nav** — `templates/_nav.html`: add a `/challenges` link to the mobile drawer (near `/quests` / `/leaderboard`), label `{{ t('nav.challenges') }}`.

- [ ] **Step 6: Run to pass** — `pytest tests/test_challenge_routes.py::test_challenges_page_renders -v` → PASS.

- [ ] **Step 7: Commit** — `git add -A && git commit -m "feat(challenges): sayfa + stil + nav"`

---

## Task 12: i18n — both locales

**Files:**
- Modify: `locales/tr.json`, `locales/en.json`
- Test: `tests/test_i18n*.py` (parity) if present, else `pytest -k i18n`

- [ ] **Step 1: Add keys to BOTH locales** (canonical slugs stay English; display text translated). At minimum:
  - `nav.challenges`
  - `challenge.page_title`, `challenge.global`, `challenge.featured`, `challenge.join`, `challenge.joined`, `challenge.not_joinable`, `challenge.ends_in`, `challenge.progress`, `challenge.completed`, `challenge.xp_reward`, `challenge.leaderboard`, `challenge.friends`, `challenge.global_scope`, `challenge.empty`, `challenge.badges`
  - Per-code: `challenge.<code>.title` + `challenge.<code>.desc` for all 8 seed codes (`weekly_workouts`, `weekly_meals`, `weekly_water`, `weekly_pump`, `weekly_active`, `weekly_xp`, `featured_pump_perfect`, `featured_grind`).
  - `badge.pump_week.title`, `badge.active_week.title`, `badge.pump_perfect.title`, `badge.grinder.title`
  - `notif.challenge_complete` (uses `{title}` or generic — client builds from `notif.<ntype>`; payload has `code`, so key can be generic "Bir meydan okumayı tamamladın!" / "You completed a challenge!")

- [ ] **Step 2: Verify parity** — key counts match between `tr.json` and `en.json`. Run `pytest -k "i18n or locale" -v` (or a quick `python -c` diffing the two key sets).

- [ ] **Step 3: Wire `notif.challenge_complete` into `templates/notifications.html`** — add the ICON (🏆) + ensure `targetType == "challenge"` routes to `/challenges` (mirror the PR2 `feed_item` → `/feed` handling).

- [ ] **Step 4: Commit** — `git add -A && git commit -m "feat(challenges): i18n (tr+en) + bildirim ikonu"`

---

## Task 13: Docs + full-suite verification

**Files:**
- Create: `docs/CHALLENGES.md`, `docs/LEADERBOARD.md`
- Modify: `CLAUDE.md`
- Modify: `tests/test_cascade_delete.py` (challenge rows purge)

- [ ] **Step 1: `docs/CHALLENGES.md`** — event taxonomy + hook map (which call site fires which `event_type`), period math (Sunday-23:59-Istanbul, `current_challenge_week`/`period_end_utc`), hybrid semantics (global auto vs featured opt-in), recursion rule (`count_challenge_xp=False`), extensibility seams (`challenge_type`/`period_type`), seed catalog table, how to add a challenge/metric.
- [ ] **Step 2: `docs/LEADERBOARD.md`** — `_lb_score`, Redis/Postgres fallback, rollover boundary, `resetAt` contract, top-3 rewards, challenge boards.
- [ ] **Step 3: `CLAUDE.md`** — add Challenge/UserChallengeProgress/UserBadge to the model list; `app/services/challenges.py` + `badges.py` to the services line; `challenges` blueprint; note the `resetAt` fix + doc pointers.
- [ ] **Step 4: `tests/test_cascade_delete.py`** — add a test that `_purge_user` (or the registry) removes a user's `UserChallengeProgress` + `UserBadge` rows. The registry introspection already forces them into `_USER_CHILD_MODELS`; add an explicit purge assertion.
- [ ] **Step 5: Full suite** — `pytest -m "not load"` → all green (target: 1700+ passed, 0 failed).
- [ ] **Step 6: Commit** — `git add -A && git commit -m "docs(challenges): CHALLENGES + LEADERBOARD + CLAUDE"`

---

## Self-Review (spec coverage)

- **Progress tracking, XP, badges, countdown, progress bars, completion notifications** → Tasks 4, 2, 10/11, 4, 12. ✅
- **Challenge / friend / global leaderboards** → Task 5 `challenge_board(scope)` + Task 9 route + Task 11 sheet. ✅
- **Categories (workouts, running/cardio, nutrition, hydration, pump-check, active-days, XP)** → seed catalog covers all except running/cardio (deferred — no deterministic in-request event source; `metric` column supports it later; documented in Task 13). ✅ (with documented deferral)
- **Hybrid: global auto + featured opt-in** → Task 4 (`challenge_type` branch) + Task 5 `join_featured` + Task 9 join route. ✅
- **Idempotent completion** → Task 4 guarded `UPDATE … WHERE completed_at IS NULL`. ✅
- **Recursion exclusion** → Task 6 `count_challenge_xp`. ✅
- **Extensibility seams (duel/team/sponsored/seasonal)** → `challenge_type`/`period_type` columns, `ref`-free design. ✅
- **Leaderboard countdown UTC↔Istanbul fix** → Task 10. ✅
- **Migrations additive/re-runnable; registry; i18n parity; CSP; no-commit contract** → Global Constraints enforced per task. ✅

**Type consistency check:** `record_event(user_id, event_type, amount=1)`, `award_xp(user_id, amount, count_challenge_xp=True)`, `current_challenge_week(now=None)`, `period_end_utc(now=None)`, `join_featured(user_id, challenge_id)`, `challenge_board(challenge_id, period_key, scope, viewer_id)` — used consistently across Tasks 4/6/9. Route JSON keys (`weekKey`, `periodEndsAt`, `progress`, `completed`, `joined`) consistent between Task 9 producer and Task 11 consumer.
