# Phase 5 — Progress Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `/progress-page` into a premium analytics experience on the canonical AxisAI design system — consistency heatmap, deterministic AI insights, and 4 metric tabs (Weight & Body / Nutrition / Workout / Achievements) with weekly/monthly trend charts — backed by additive read-only endpoints, preserving the weekly Check-In flow.

**Architecture:** Five additive read-only `GET /api/progress/*` endpoints (nutrition/workout trends, heatmap, achievements, insights) aggregate existing data (`MealLog`/`WorkoutLog`/`DailyActivity`/`WeeklyCheckIn`/gamification) in Python (DB-agnostic; SQLite local, Postgres prod), each scoped to `current_user`, no writes/migration. A full front-end rewrite of `templates/progress.html` + new `static/progress.js` + `static/progress.css` on canonical tokens; Chart.js (jsdelivr, CSP-allowed) for trend charts; the GitHub heatmap is a pure CSS grid.

**Tech Stack:** Flask + SQLAlchemy, `app/timeutil` (Istanbul day keys), Chart.js 4 (jsdelivr), vanilla JS with `data-action` delegation (`static/actions.js`), canonical CSS tokens, pytest + `node --check`.

## Global Constraints

- **Backend additions are read-only aggregation ONLY** — new `GET` routes in `app/blueprints/tracking.py`, every query scoped to `current_user.id`, no writes, no schema, no migration. Rollback-safe (expand-only).
- **Preserve the weekly Check-In feature** — `POST /checkin` and `GET /checkin-history` unchanged; keep the AI-feedback XSS escaping (`&`/`<`/`>`→entities, `\n`→`<br>`) and the `window.CW.receiveCheckinFeedback` hook.
- **Day keys** — use `app/timeutil`: `app_today()` (Istanbul date), `app_date_of(dt)` (UTC datetime → Istanbul date), `utc_day_bounds(d)` (→ `(start_utc, end_utc)`). `MealLog.tarih`/`DailyActivity.date_key` are ISO `YYYY-MM-DD` strings already. Never use `date.today()`/`utcnow().strftime`.
- **`WORKOUT_COMPLETION_MARKER`** (`app.models`) is the UI-completion sentinel `WorkoutLog` (volume 0): count it as a *session*, EXCLUDE it from *volume*.
- **i18n** — new UI strings via `t()` keys in BOTH `locales/{tr,en}.json`; canonical TR values stay TR.
- **CSP** — Chart.js from `cdn.jsdelivr.net` with its integrity/crossorigin pin (keep the exact tag); inline `<script>` carries `nonce="{{ csp_nonce }}"`; NEVER inject `<style>` from JS; page CSS via `<link href="/static/progress.css?v={{ _v }}">`.
- **Design system** — canonical tokens only in `progress.css` (no `--volt`/raw hex/`rgba()`); reuse `components.css` primitives. Chart.js color literals stay in JS (documented design-system exception).
- **Test:** `python -m pytest -q` stays green; `node --check static/progress.js` after JS tasks. **Do NOT `git add -A`** — untracked scratch at repo root; stage explicit paths only.

## File Structure

- `app/blueprints/tracking.py` — MODIFY: add `_progress_range(range_key)` helper + 5 read-only routes.
- `static/progress.css` — CREATE: canonical page styles (heatmap, insight card, chart card, trend toggle, metric heroes, restyled check-in/sliders/history).
- `templates/progress.html` — REWRITE: canonical shell, header/overview, heatmap, insights, 4 tabs, Check-In sheet.
- `static/progress.js` — CREATE: page JS (tab switching, Chart.js configs, heatmap/insights/tab renderers, preserved check-in flow).
- `locales/tr.json`, `locales/en.json` — MODIFY: new `progress.*` keys.
- `tests/test_progress_api.py` — CREATE: endpoint tests.
- `tests/test_i18n.py` / `tests/test_progress_ui.py` — MODIFY/CREATE: render assertions.
- `docs/handoff.md` — REWRITE at phase end; Workout handoff archived.

---

### Task 1: Nutrition + Workout trend endpoints (backend)

**Files:**
- Modify: `app/blueprints/tracking.py`
- Test: `tests/test_progress_api.py` (create)

**Interfaces:**
- Produces:
  - `_progress_range(range_key) -> (start_date, n_days)` — `"week"`→(today-6, 7), else (today-29, 30). Reused by Tasks 2–3.
  - `GET /api/progress/nutrition?range=week|month` → `{"days":[{"date","kcal","p","c","f"}], "avg":{"kcal","p","c","f"}, "target_kcal":int}` (contiguous day series; avg over logged days).
  - `GET /api/progress/workout?range=week|month` → `{"days":[{"date","sessions","volume","active_min"}], "totals":{"sessions":int,"volume":int}}` (sessions = distinct active days; volume excludes the completion marker).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_progress_api.py` (reuse the suite's `app, client, make_user, login` fixtures; add data via `db.session`):

```python
from app.extensions import db
from app.models import MealLog, WorkoutLog, WORKOUT_COMPLETION_MARKER
from app.timeutil import app_today


def _login(make_user, login, name="prog"):
    u = make_user(name, profile_complete=True)
    login(name)
    return u


def test_nutrition_trend_groups_by_day(app, client, make_user, login):
    u = _login(make_user, login, "nutru")
    today = app_today().isoformat()
    db.session.add(MealLog(user_id=u.id, ogun="Kahvaltı", yemekler="x",
                           kalori=500, protein=30, karb=50, yag=10, tarih=today))
    db.session.add(MealLog(user_id=u.id, ogun="Öğle", yemekler="y",
                           kalori=700, protein=40, karb=60, yag=20, tarih=today))
    db.session.commit()
    r = client.get("/api/progress/nutrition?range=week")
    assert r.status_code == 200
    d = r.get_json()
    assert len(d["days"]) == 7
    last = d["days"][-1]
    assert last["date"] == today and last["kcal"] == 1200 and last["p"] == 70
    assert d["avg"]["kcal"] == 1200   # one logged day


def test_nutrition_scoped_to_user(app, client, make_user, login):
    other = make_user("nutother", profile_complete=True)
    db.session.add(MealLog(user_id=other.id, ogun="Kahvaltı", yemekler="z",
                           kalori=999, protein=1, karb=1, yag=1,
                           tarih=app_today().isoformat()))
    db.session.commit()
    _login(make_user, login, "nutme")
    d = client.get("/api/progress/nutrition?range=week").get_json()
    assert all(day["kcal"] == 0 for day in d["days"])   # other user's meal not visible


def test_workout_trend_marker_excluded_from_volume(app, client, make_user, login):
    u = _login(make_user, login, "wktrend")
    db.session.add(WorkoutLog(user_id=u.id, exercise_name="Squat",
                              sets=3, reps=10, weight_kg=100, volume=3000))
    db.session.add(WorkoutLog(user_id=u.id, exercise_name=WORKOUT_COMPLETION_MARKER,
                              sets=1, reps=1, weight_kg=0, volume=0))
    db.session.commit()
    d = client.get("/api/progress/workout?range=week").get_json()
    assert d["totals"]["volume"] == 3000          # marker excluded
    assert d["totals"]["sessions"] == 1           # today counts once
    assert d["days"][-1]["sessions"] == 1 and d["days"][-1]["volume"] == 3000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_progress_api.py -q`
Expected: FAIL — 404 / route not found.

- [ ] **Step 3: Implement the helper + routes**

In `app/blueprints/tracking.py`, add near the other routes (imports `MealLog`, `WorkoutLog`, `DailyActivity`, `UserSession`, `app_today`, `app_date_of`, `utc_day_bounds` are already present; add `from datetime import timedelta` at top if absent, and `from app.models import WORKOUT_COMPLETION_MARKER`):

```python
def _progress_range(range_key):
    n = 7 if (range_key or "week") == "week" else 30
    return app_today() - timedelta(days=n - 1), n


@bp.route("/api/progress/nutrition")
@login_required
def progress_nutrition():
    start, n = _progress_range(request.args.get("range"))
    rows = MealLog.query.filter(
        MealLog.user_id == current_user.id,
        MealLog.tarih >= start.isoformat(),
    ).all()
    by_day = {}
    for m in rows:
        d = by_day.setdefault(m.tarih, {"kcal": 0.0, "p": 0.0, "c": 0.0, "f": 0.0})
        d["kcal"] += m.kalori or 0
        d["p"] += m.protein or 0
        d["c"] += m.karb or 0
        d["f"] += m.yag or 0
    days = []
    for i in range(n):
        dt = (start + timedelta(days=i)).isoformat()
        v = by_day.get(dt, {"kcal": 0, "p": 0, "c": 0, "f": 0})
        days.append({"date": dt, "kcal": round(v["kcal"]), "p": round(v["p"]),
                     "c": round(v["c"]), "f": round(v["f"])})
    logged = [d for d in days if d["kcal"] > 0]
    k = len(logged) or 1
    avg = {"kcal": round(sum(d["kcal"] for d in logged) / k),
           "p": round(sum(d["p"] for d in logged) / k),
           "c": round(sum(d["c"] for d in logged) / k),
           "f": round(sum(d["f"] for d in logged) / k)}
    last = UserSession.query.filter_by(user_id=current_user.id)\
        .order_by(UserSession.created_at.desc()).first()
    target = round(getattr(last, "target_calories", 0) or 0) if last else 0
    return jsonify({"days": days, "avg": avg, "target_kcal": target})


@bp.route("/api/progress/workout")
@login_required
def progress_workout():
    start, n = _progress_range(request.args.get("range"))
    start_utc = utc_day_bounds(start)[0]
    logs = WorkoutLog.query.filter(
        WorkoutLog.user_id == current_user.id,
        WorkoutLog.created_at >= start_utc,
    ).all()
    vol_by_day, session_days = {}, set()
    for w in logs:
        d = app_date_of(w.created_at).isoformat()
        session_days.add(d)
        if w.exercise_name != WORKOUT_COMPLETION_MARKER:
            vol_by_day[d] = vol_by_day.get(d, 0.0) + (w.volume or 0)
    acts = DailyActivity.query.filter(
        DailyActivity.user_id == current_user.id,
        DailyActivity.date_key >= start.isoformat(),
    ).all()
    min_by_day = {a.date_key: (a.duration_min or 0) for a in acts}
    days = []
    for i in range(n):
        dt = (start + timedelta(days=i)).isoformat()
        days.append({"date": dt, "sessions": 1 if dt in session_days else 0,
                     "volume": round(vol_by_day.get(dt, 0)),
                     "active_min": round(min_by_day.get(dt, 0))})
    return jsonify({"days": days, "totals": {
        "sessions": len(session_days),
        "volume": round(sum(vol_by_day.values()))}})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_progress_api.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/blueprints/tracking.py tests/test_progress_api.py
git commit -m "Add read-only nutrition + workout progress-trend endpoints"
```

---

### Task 2: Consistency heatmap endpoint (backend)

**Files:**
- Modify: `app/blueprints/tracking.py`
- Test: `tests/test_progress_api.py` (extend)

**Interfaces:**
- Consumes: `_progress_range` is NOT used here (heatmap uses its own `weeks` window); reuses `app_today`, `app_date_of`, `utc_day_bounds`, `MealLog`, `DailyActivity`, `WorkoutLog`, `WeeklyCheckIn`.
- Produces: `GET /api/progress/heatmap?weeks=26` → `{"cells":[{"date","level"}], "weeks":int}` — `weeks` clamped 1–53; `level` 0–4 bucketed from a per-day activity score (each of: any meal / activity / workout / check-in on that day adds 1).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_progress_api.py`:

```python
def test_heatmap_levels_from_activity(app, client, make_user, login):
    u = _login(make_user, login, "hmuser")
    today = app_today().isoformat()
    db.session.add(MealLog(user_id=u.id, ogun="Kahvaltı", yemekler="x",
                           kalori=100, protein=1, karb=1, yag=1, tarih=today))
    db.session.add(WorkoutLog(user_id=u.id, exercise_name="Squat",
                              sets=1, reps=1, weight_kg=1, volume=1))
    db.session.commit()
    d = client.get("/api/progress/heatmap?weeks=4").get_json()
    assert d["weeks"] == 4 and len(d["cells"]) == 28
    today_cell = [c for c in d["cells"] if c["date"] == today][0]
    assert today_cell["level"] == 2          # meal + workout = score 2
    assert d["cells"][0]["level"] == 0       # an empty earlier day


def test_heatmap_weeks_clamped(app, client, make_user, login):
    _login(make_user, login, "hmclamp")
    d = client.get("/api/progress/heatmap?weeks=999").get_json()
    assert d["weeks"] == 53 and len(d["cells"]) == 53 * 7
```

- [ ] **Step 2: Run test — expect FAIL** (`python -m pytest tests/test_progress_api.py -q -k heatmap`; 404).

- [ ] **Step 3: Implement**

Add to `app/blueprints/tracking.py`:

```python
@bp.route("/api/progress/heatmap")
@login_required
def progress_heatmap():
    try:
        weeks = int(request.args.get("weeks", 26) or 26)
    except (TypeError, ValueError):
        weeks = 26
    weeks = max(1, min(weeks, 53))
    n = weeks * 7
    start = app_today() - timedelta(days=n - 1)
    start_iso, start_utc = start.isoformat(), utc_day_bounds(start)[0]
    score = {}

    def bump(dkey):
        score[dkey] = score.get(dkey, 0) + 1

    for (tarih,) in db.session.query(MealLog.tarih).filter(
            MealLog.user_id == current_user.id, MealLog.tarih >= start_iso).distinct():
        bump(tarih)
    for (dk,) in db.session.query(DailyActivity.date_key).filter(
            DailyActivity.user_id == current_user.id, DailyActivity.date_key >= start_iso).distinct():
        bump(dk)
    for (ca,) in db.session.query(WorkoutLog.created_at).filter(
            WorkoutLog.user_id == current_user.id, WorkoutLog.created_at >= start_utc):
        bump(app_date_of(ca).isoformat())
    for (ca,) in db.session.query(WeeklyCheckIn.created_at).filter(
            WeeklyCheckIn.user_id == current_user.id, WeeklyCheckIn.created_at >= start_utc):
        bump(app_date_of(ca).isoformat())

    cells = []
    for i in range(n):
        dt = (start + timedelta(days=i)).isoformat()
        cells.append({"date": dt, "level": min(score.get(dt, 0), 4)})
    return jsonify({"cells": cells, "weeks": weeks})
```

- [ ] **Step 4: Run tests — expect PASS** (`python -m pytest tests/test_progress_api.py -q -k heatmap`).

- [ ] **Step 5: Commit**

```bash
git add app/blueprints/tracking.py tests/test_progress_api.py
git commit -m "Add read-only consistency-heatmap progress endpoint"
```

---

### Task 3: Achievements + Insights endpoints (backend)

**Files:**
- Modify: `app/blueprints/tracking.py`
- Test: `tests/test_progress_api.py` (extend)

**Interfaces:**
- Consumes: `get_level`/`level_title` (`app.services.gamification`), `UserQuestProgress`, `WeeklyWinner` (`app.models`), `_progress_range`.
- Produces:
  - `GET /api/progress/achievements` → `{"level","title","rank_points","weekly_xp","streak","quests_done","weekly_wins","milestones":[{"key","label","hit"}]}`.
  - `GET /api/progress/insights` → `{"insights":[{"icon","title","body","tone"}]}` — deterministic, computed from the user's own trend data (weight direction, workout count this week, calorie adherence, streak). `tone` ∈ `{"success","warning","info"}` (→ `.badge-*`). Always returns ≥1 insight (an encouraging default when data is sparse).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_progress_api.py`:

```python
def test_achievements_shape(app, client, make_user, login):
    u = _login(make_user, login, "achuser")
    u.rank_points = 1200
    u.streak_count = 9
    db.session.commit()
    d = client.get("/api/progress/achievements").get_json()
    assert d["rank_points"] == 1200 and d["streak"] == 9
    assert isinstance(d["level"], int) and isinstance(d["title"], str)
    assert any(m["key"] == "streak7" and m["hit"] for m in d["milestones"])
    assert any(m["key"] == "streak30" and not m["hit"] for m in d["milestones"])


def test_insights_always_nonempty(app, client, make_user, login):
    _login(make_user, login, "insuser")
    d = client.get("/api/progress/insights").get_json()
    assert isinstance(d["insights"], list) and len(d["insights"]) >= 1
    first = d["insights"][0]
    assert set(("icon", "title", "body", "tone")).issubset(first)
    assert first["tone"] in ("success", "warning", "info")
```

- [ ] **Step 2: Run tests — expect FAIL** (404).

- [ ] **Step 3: Implement**

Add imports at top of `app/blueprints/tracking.py`:
```python
from app.services.gamification import complete_quest_for_user, get_level, level_title
from app.models import (DailyActivity, MealLog, User, UserQuestProgress, UserSession,
                        WaterLog, WearableActivityLog, WeeklyCheckIn, WeeklyLog,
                        WeeklyWinner, WorkoutLog, WORKOUT_COMPLETION_MARKER)
```
(merge with the existing `from app.models import ...` line — keep it one import; add `UserQuestProgress`, `WeeklyWinner`, `WORKOUT_COMPLETION_MARKER`. `complete_quest_for_user` is already imported — add `get_level, level_title`.)

Then add the routes:

```python
@bp.route("/api/progress/achievements")
@login_required
def progress_achievements():
    xp = current_user.rank_points or 0
    level = get_level(xp)
    quests_done = UserQuestProgress.query.filter_by(user_id=current_user.id).count()
    weekly_wins = WeeklyWinner.query.filter_by(user_id=current_user.id).count()
    streak = current_user.streak_count or 0
    milestones = [
        {"key": "streak7",  "label": t("progress.ms_streak7"),  "hit": streak >= 7},
        {"key": "streak30", "label": t("progress.ms_streak30"), "hit": streak >= 30},
        {"key": "level5",   "label": t("progress.ms_level5"),   "hit": level >= 5},
        {"key": "quests10", "label": t("progress.ms_quests10"), "hit": quests_done >= 10},
        {"key": "winner",   "label": t("progress.ms_winner"),   "hit": weekly_wins >= 1},
    ]
    return jsonify({"level": level, "title": level_title(level), "rank_points": xp,
                    "weekly_xp": current_user.weekly_xp or 0, "streak": streak,
                    "quests_done": quests_done, "weekly_wins": weekly_wins,
                    "milestones": milestones})


@bp.route("/api/progress/insights")
@login_required
def progress_insights():
    insights = []
    # 1) Weight direction over the last two check-ins
    cis = WeeklyCheckIn.query.filter_by(user_id=current_user.id)\
        .filter(WeeklyCheckIn.yogunluk.isnot(None))\
        .order_by(WeeklyCheckIn.created_at.desc()).limit(2).all()
    if len(cis) == 2 and cis[0].weight and cis[1].weight:
        delta = round(cis[0].weight - cis[1].weight, 1)
        if delta != 0:
            insights.append({"icon": "⚖️", "title": t("progress.ins_weight_title"),
                             "body": t("progress.ins_weight_body", delta=("%+g" % delta)),
                             "tone": "info"})
    # 2) Workout sessions in the last 7 app-days
    start_utc = utc_day_bounds(app_today() - timedelta(days=6))[0]
    wdays = {app_date_of(w.created_at).isoformat() for w in WorkoutLog.query.filter(
        WorkoutLog.user_id == current_user.id, WorkoutLog.created_at >= start_utc).all()}
    if wdays:
        insights.append({"icon": "🏋️", "title": t("progress.ins_workout_title"),
                         "body": t("progress.ins_workout_body", n=len(wdays)),
                         "tone": "success" if len(wdays) >= 3 else "warning"})
    # 3) Calorie adherence today vs target
    last = UserSession.query.filter_by(user_id=current_user.id)\
        .order_by(UserSession.created_at.desc()).first()
    target = getattr(last, "target_calories", 0) or 0 if last else 0
    if target:
        eaten = sum((m.kalori or 0) for m in MealLog.query.filter_by(
            user_id=current_user.id, tarih=app_today().isoformat()).all())
        if eaten:
            pct = round(eaten / target * 100)
            insights.append({"icon": "🍽️", "title": t("progress.ins_cal_title"),
                             "body": t("progress.ins_cal_body", pct=pct),
                             "tone": "success" if 80 <= pct <= 110 else "warning"})
    # 4) Streak encouragement (always available)
    streak = current_user.streak_count or 0
    insights.append({"icon": "🔥", "title": t("progress.ins_streak_title"),
                     "body": t("progress.ins_streak_body", n=streak),
                     "tone": "success" if streak >= 3 else "info"})
    return jsonify({"insights": insights})
```

> `t(key, **kw)` supports named interpolation (see existing `t('training.workout_done', xp=...)` usage). The `progress.ins_*` / `progress.ms_*` keys are added in Task 10; until then `t()` returns the key — the tests assert structure/tone, not copy, so they pass regardless.

- [ ] **Step 4: Run tests — expect PASS.**

- [ ] **Step 5: Commit**

```bash
git add app/blueprints/tracking.py tests/test_progress_api.py
git commit -m "Add read-only achievements + deterministic insights endpoints"
```

---

### Task 4: `static/progress.css` — canonical stylesheet

**Files:**
- Create: `static/progress.css`

**Interfaces:**
- Produces classes consumed by Tasks 5–9: `.prog-overview`/`.po-*`, `.heatmap`/`.hm-grid`/`.hm-cell`(`.lvl-0..4`)/`.hm-legend`/`.hm-month`, `.insight-row`/`.insight-card`/`.ic-*`, `.trend-toggle`/`.tt-btn`, `.chart-card`/`.chart-title`/`.chart-container`/`.no-data`, `.metric-stats`, restyled `.checkin-*`/sliders/`.overload-*`/`.feedback-*`/`.history-*`.

- [ ] **Step 1: Write the stylesheet**

Create `static/progress.css`. Canonical tokens only (no `--volt`/hex/rgba). Read `static/nutrition.css` for page-CSS density. Representative core blocks (fill remaining states to that density):

```css
/* Phase 5 progress page — canonical tokens, mobile-first. */

/* ── Consistency heatmap (GitHub-style CSS grid) ── */
.heatmap { overflow-x: auto; -webkit-overflow-scrolling: touch; padding-bottom: var(--space-1); }
.hm-grid { display: grid; grid-auto-flow: column; grid-template-rows: repeat(7, 1fr);
  gap: 3px; width: max-content; }
.hm-cell { width: 12px; height: 12px; border-radius: var(--radius-xs);
  background: var(--overlay-4); }
.hm-cell.lvl-1 { background: var(--color-primary-soft); }
.hm-cell.lvl-2 { background: var(--color-primary-glow); }
.hm-cell.lvl-3 { background: var(--color-primary); }
.hm-cell.lvl-4 { background: var(--color-primary-strong); }
.hm-legend { display: flex; align-items: center; gap: var(--space-2);
  justify-content: flex-end; margin-top: var(--space-2);
  color: var(--color-text-3); font-size: var(--text-2xs); }

/* ── AI Insight cards ── */
.insight-row { display: flex; gap: var(--space-3); overflow-x: auto;
  -webkit-overflow-scrolling: touch; padding-bottom: var(--space-1); }
.insight-card { flex: 0 0 auto; width: min(78vw, 300px);
  background: var(--color-surface-2);
  border: var(--border-w-1) solid var(--color-border-1);
  border-radius: var(--radius-lg); padding: var(--space-4); }
.ic-head { display: flex; align-items: center; gap: var(--space-2); margin-bottom: var(--space-2); }
.ic-icon { font-size: 20px; }
.ic-title { font-weight: var(--weight-semibold); color: var(--color-text-1); }
.ic-body { color: var(--color-text-2); font-size: var(--text-sm); line-height: var(--leading-normal); }

/* ── Trend toggle (week/month) ── */
.trend-toggle { display: inline-flex; gap: 2px; background: var(--overlay-3);
  border-radius: var(--radius-md); padding: 3px; }
.tt-btn { min-height: 34px; padding: 0 var(--space-3); border: none; background: none;
  color: var(--color-text-3); border-radius: var(--radius-sm); cursor: pointer;
  font: inherit; font-weight: var(--weight-semibold); }
.tt-btn.active { background: var(--color-primary-soft); color: var(--color-primary); }

/* ── Chart card (canonical restyle of the old .chart-card) ── */
.chart-card { background: var(--color-surface-2);
  border: var(--border-w-1) solid var(--color-border-1);
  border-radius: var(--radius-lg); padding: var(--space-5); margin-bottom: var(--space-3); }
.chart-title { font-family: var(--font-display); letter-spacing: 2px;
  color: var(--color-text-1); margin-bottom: var(--space-4);
  display: flex; align-items: center; gap: var(--space-3); }
.chart-container { position: relative; height: 220px; }
.no-data { color: var(--color-text-3); font-size: var(--text-sm); font-style: italic;
  text-align: center; padding: var(--space-8) 0; }

/* ── Metric stat row ── */
.metric-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--space-2);
  margin-bottom: var(--space-3); }
@media (max-width: 420px) { .metric-stats { grid-template-columns: repeat(2, 1fr); } }

/* ── Check-in sliders / overload / feedback (canonical restyle) ── */
/* Migrate the old --volt/theme.css rules for .slider-*, .overload-chip,
   .feedback-card, .history-* here using canonical tokens (see the old
   progress.html <style> block for the exact rules; swap --volt→--color-primary,
   --surface-2→--color-surface-2, --r-*→--radius-*, --t-fast→--duration-fast
   var(--ease-standard), --text*→--color-text-*, --border→--color-border-1). */

@media (prefers-reduced-motion: reduce) { .tt-btn, .hm-cell { transition: none; } }
```

- [ ] **Step 2: Token sanity** — `grep -nE "\-\-volt|#[0-9a-fA-F]{3,6}|rgba\(" static/progress.css` → no matches. Fix stragglers.

- [ ] **Step 3: Commit**

```bash
git add static/progress.css
git commit -m "Add canonical progress.css for Phase 5 progress redesign"
```

---

### Task 5: Rewrite template shell + `static/progress.js` scaffold + Check-In sheet

**Files:**
- Rewrite: `templates/progress.html`
- Create: `static/progress.js`

**Interfaces:**
- Consumes: `progress.css` (Task 4), Chart.js (jsdelivr), `components.css` primitives, `POST /checkin`, `GET /checkin-history`.
- Produces DOM hooks for Tasks 6–9: `#heatmap`, `#insight-row`, tabs `.tab-btn[data-args]` for `weight`/`nutrition`/`workout`/`achievements`, panels `#tab-weight`/`#tab-nutrition`/`#tab-workout`/`#tab-achievements`, `#checkin-sheet`, chart canvases. JS globals: `switchTab`, `openCheckin`/`closeCheckin`/`submitCheckin`, `selectOverload`, `showToast`, `escapeHTML`.

- [ ] **Step 1: Rewrite `templates/progress.html`**

Structure:
- `<head>`: keep `_head.html` + `theme.css` + `nav.css`; **keep the exact Chart.js `<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/..." integrity="..." crossorigin="anonymous">`**; add `<link rel="stylesheet" href="/static/progress.css?v={{ _v }}">`; delete the inline `<style>` block (rules now in `progress.css`).
- `_nav.html` (`nav_active='progress'`), page header + **overview** (`.prog-overview`: streak / level / this-week snapshot — populated by JS from `/api/progress/achievements`).
- **Heatmap** section (`.card` > `.sec-label` + `#heatmap` + `.hm-legend`).
- **Insights** section (`.sec-label` + `#insight-row`).
- **Tabs** (`.tab-bar` of 4: Weight & Body / Nutrition / Workout / Achievements) + 4 `.tab-panel`s (`#tab-weight` active).
- **Check-In sheet** (`.sheet-backdrop#checkin-sheet` > `.sheet`): the weight input + 4 wellness sliders (`data-action-input="fxSetText"`) + overload chips (`data-action="selectOverload"`) + submit (`data-action="submitCheckin"`) + the feedback card. A "＋ Check-In" button in the Weight & Body tab opens it (`data-action="openCheckin"`).
- Script tail: `<script src="/static/progress.js?v={{ _v }}"></script>`, `coach_widget.js`, `actions.js` (all `?v={{ _v }}`).

- [ ] **Step 2: Create `static/progress.js` scaffold + preserved check-in**

Port these verbatim from the old inline script (they are the preserved behavior): `showToast`, `escapeHTML`, `selectOverload`, and `submitCheckin` (POST `/checkin`, the `&/</>/\n`→escape of `coach_feedback`, `window.CW.receiveCheckinFeedback`). Add:

```javascript
var __t = (window.t) || function (k) { return k; };
var _EN = (window.LOCALE === 'en');

function switchTab(name, btn) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  if (btn) btn.classList.add('active');
  var panel = document.getElementById('tab-' + name);
  if (panel) panel.classList.add('active');
  if (name === 'weight')       loadWeightTab();        // Task 7
  if (name === 'nutrition')    loadNutritionTab();     // Task 8
  if (name === 'workout')      loadWorkoutTab();       // Task 8
  if (name === 'achievements') loadAchievementsTab();  // Task 9
}
function openCheckin()  { document.getElementById('checkin-sheet').classList.add('open'); }
function closeCheckin() { document.getElementById('checkin-sheet').classList.remove('open'); }

// Task 6/7/8/9 define the loaders + renderHeatmap/renderInsights/overview.
function initProgress() { loadOverviewAndExtras(); loadWeightTab(); }
document.addEventListener('DOMContentLoaded', initProgress);
```

Add temporary no-op stubs so this task runs standalone (replaced in Tasks 6–9):
```javascript
function loadOverviewAndExtras() {}
function loadWeightTab() {}
function loadNutritionTab() {}
function loadWorkoutTab() {}
function loadAchievementsTab() {}
```

- [ ] **Step 3: Verify** — `node --check static/progress.js`; `python -m pytest tests/test_i18n.py -q -k progress` (or template renders without Jinja error). If a render test asserts old markup, update it in Task 10.

- [ ] **Step 4: Commit**

```bash
git add templates/progress.html static/progress.js
git commit -m "Rewrite progress shell: overview, heatmap/insights sections, 4 tabs, check-in sheet"
```

---

### Task 6: Consistency heatmap + AI Insights + overview render (JS)

**Files:**
- Modify: `static/progress.js`

**Interfaces:**
- Consumes: `GET /api/progress/heatmap` (Task 2), `GET /api/progress/insights` (Task 3), `GET /api/progress/achievements` (Task 3).
- Produces: `renderHeatmap(cells)`, `renderInsights(list)`, `renderOverview(ach)`; replaces the `loadOverviewAndExtras` stub.

- [ ] **Step 1: Implement** (replace the stub)

```javascript
async function loadOverviewAndExtras() {
  try {
    var [hm, ins, ach] = await Promise.all([
      fetch('/api/progress/heatmap?weeks=26').then(r => r.json()),
      fetch('/api/progress/insights').then(r => r.json()),
      fetch('/api/progress/achievements').then(r => r.json()),
    ]);
    renderHeatmap(hm.cells || []);
    renderInsights(ins.insights || []);
    renderOverview(ach);
  } catch (e) {}
}

function renderHeatmap(cells) {
  var grid = document.getElementById('heatmap-grid');
  if (!grid) return;
  grid.innerHTML = cells.map(function (c) {
    return '<div class="hm-cell lvl-' + (c.level || 0) + '" title="' +
      escapeHTML(c.date) + '"></div>';
  }).join('');
}

function renderInsights(list) {
  var row = document.getElementById('insight-row');
  if (!row) return;
  if (!list.length) { row.innerHTML = ''; return; }
  row.innerHTML = list.map(function (n) {
    return '<div class="insight-card"><div class="ic-head"><span class="ic-icon">' +
      escapeHTML(n.icon || '💡') + '</span><span class="ic-title badge badge-' +
      (n.tone || 'info') + '">' + escapeHTML(n.title) + '</span></div>' +
      '<div class="ic-body">' + escapeHTML(n.body) + '</div></div>';
  }).join('');
}

function renderOverview(a) {
  if (!a) return;
  var set = function (id, v) { var el = document.getElementById(id); if (el) el.textContent = v; };
  set('po-streak', (a.streak || 0));
  set('po-level', (a.level || 0));
  set('po-xp', (a.weekly_xp || 0));
}

// Shared render helper (used by the Weight/Workout/Achievements tabs, Tasks 7–9).
function statCard(v, label) {
  return '<div class="stat-card"><div class="stat-value">' + v +
    '</div><div class="stat-label">' + escapeHTML(label) + '</div></div>';
}
```

- [ ] **Step 2: Verify** — `node --check`; manual: heatmap grid fills (26×7 cells), insight cards render, overview numbers populate.

- [ ] **Step 3: Commit**

```bash
git add static/progress.js
git commit -m "Render consistency heatmap + AI insights + overview"
```

---

### Task 7: Weight & Body tab (JS)

**Files:**
- Modify: `static/progress.js`

**Interfaces:**
- Consumes: `GET /checkin-history` (existing: `[{tarih,kilo,yogunluk,fatigue,uyku,beslenme,overload,feedback}]`), the bootstrap `window.__PROGRESS` (`current_weight`, `height_cm`, `goal_weight`).
- Produces: `loadWeightTab()` (replaces stub), `weightChart`/`wellnessChart` Chart.js instances, BMI/stat-card render.

- [ ] **Step 1: Bootstrap server values** — in `templates/progress.html` add before `progress.js`:
```html
<script nonce="{{ csp_nonce }}">window.__PROGRESS = { current_weight: {{ current_weight|tojson }}, height_cm: {{ (height or 0)|tojson }}, goal_weight: {{ (goal_weight or 0)|tojson }} };</script>
```
(Confirm `progress_page` passes `current_weight`; add `height`/`goal_weight` to its `render_template(...)` from `current_user`/last session — a template-context-only change, no new route.)

- [ ] **Step 2: Implement `loadWeightTab`** (replace stub) — weight trend line + wellness lines from `/checkin-history` (reuse the old Chart.js configs, colors kept as JS literals), plus `.stat-card`s for current weight, BMI (`weight / (h/100)^2`), and change rate (first vs last check-in). Guard empty data with `.no-data`. Destroy+recreate charts on re-entry (as the old code did).

```javascript
var weightChart, wellnessChart;
async function loadWeightTab() {
  var data = await fetch('/checkin-history').then(r => r.json());
  renderBodyStats(data);
  if (!data.length) { /* show .no-data, return */ return; }
  var labels = data.map(d => d.tarih);
  if (weightChart) weightChart.destroy();
  weightChart = new Chart(document.getElementById('weightChart'), {
    type: 'line',
    data: { labels, datasets: [{ label: __t('progress.chart_weight'),
      data: data.map(d => d.kilo), borderColor: '#3D8BFF',
      backgroundColor: 'rgba(61,139,255,0.08)', fill: true, tension: 0.35,
      pointRadius: 4, borderWidth: 2 }] },
    options: _chartBase({ beginAtZero: false })
  });
  // wellnessChart: intensity/fatigue/sleep/nutrition (reuse old config)
}
function renderBodyStats(data) {
  var p = window.__PROGRESS || {};
  var latest = data.length ? data[data.length - 1].kilo : p.current_weight;
  var bmi = (latest && p.height_cm) ? (latest / Math.pow(p.height_cm / 100, 2)) : null;
  // write .stat-card values into #body-stats (current weight, BMI, Δ vs first)
}
```
`_chartBase(yOpts)` = the shared responsive/no-aspect Chart.js options (grid/text colors as JS literals) extracted from the old `baseOpts`.

- [ ] **Step 3: Verify** — `node --check`; manual: weight tab shows trend + BMI/stat cards + wellness chart; empty user shows `.no-data`.

- [ ] **Step 4: Commit**

```bash
git add templates/progress.html static/progress.js
git commit -m "Add Weight & Body tab: weight/wellness charts + BMI stats"
```

---

### Task 8: Nutrition + Workout tabs + week/month toggle (JS)

**Files:**
- Modify: `static/progress.js`, `templates/progress.html` (toggle markup if not added in Task 5)

**Interfaces:**
- Consumes: `GET /api/progress/nutrition?range=` (Task 1), `GET /api/progress/workout?range=` (Task 1).
- Produces: `loadNutritionTab()`, `loadWorkoutTab()` (replace stubs), `setTrendRange(range)`, `nutritionChart`/`macroChart`/`workoutChart` instances, a module `_trendRange` state (`'week'|'month'`).

- [ ] **Step 1: Implement the toggle + loaders**

```javascript
var _trendRange = 'week';
function setTrendRange(range, btn) {
  _trendRange = range;
  document.querySelectorAll('.tt-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  var active = document.querySelector('.tab-panel.active');
  if (active && active.id === 'tab-nutrition') loadNutritionTab();
  else if (active && active.id === 'tab-workout') loadWorkoutTab();
}

var nutritionChart, macroChart, workoutChart;
async function loadNutritionTab() {
  var d = await fetch('/api/progress/nutrition?range=' + _trendRange).then(r => r.json());
  var labels = d.days.map(x => x.date.slice(5));   // MM-DD
  if (nutritionChart) nutritionChart.destroy();
  nutritionChart = new Chart(document.getElementById('nutritionChart'), {
    type: 'bar',
    data: { labels, datasets: [{ label: __t('progress.chart_calories'),
      data: d.days.map(x => x.kcal), backgroundColor: 'rgba(61,139,255,0.55)' }] },
    options: _chartBase({ beginAtZero: true })
  });
  // macroChart: P/C/F stacked lines; adherence .stat-card from d.avg vs d.target_kcal
}
async function loadWorkoutTab() {
  var d = await fetch('/api/progress/workout?range=' + _trendRange).then(r => r.json());
  var labels = d.days.map(x => x.date.slice(5));
  if (workoutChart) workoutChart.destroy();
  workoutChart = new Chart(document.getElementById('workoutChart'), {
    type: 'bar',
    data: { labels, datasets: [{ label: __t('progress.chart_volume'),
      data: d.days.map(x => x.volume), backgroundColor: 'rgba(61,139,255,0.55)' }] },
    options: _chartBase({ beginAtZero: true })
  });
  // .stat-card: totals.sessions, totals.volume, active minutes
}
```

The `.trend-toggle` (two `.tt-btn` with `data-action="setTrendRange" data-args='["week"]'`/`'["month"]'`) sits in both the Nutrition and Workout panels.

- [ ] **Step 2: Verify** — `node --check`; manual: nutrition/workout tabs render charts; week/month toggle re-fetches + redraws; empty user shows zeros/`.no-data`.

- [ ] **Step 3: Commit**

```bash
git add static/progress.js templates/progress.html
git commit -m "Add Nutrition + Workout trend tabs with week/month toggle"
```

---

### Task 9: Achievements tab (JS)

**Files:**
- Modify: `static/progress.js`

**Interfaces:**
- Consumes: `GET /api/progress/achievements` (Task 3).
- Produces: `loadAchievementsTab()` (replaces stub) — level `.ring-*` + XP/streak/quests/wins `.stat-card`s + milestone `.badge`s.

- [ ] **Step 1: Implement**

```javascript
async function loadAchievementsTab() {
  var a = await fetch('/api/progress/achievements').then(r => r.json());
  var box = document.getElementById('achievements-body');
  if (!box) return;
  var stats =
    statCard(a.level, __t('progress.level')) +
    statCard(a.rank_points, 'XP') +
    statCard(a.streak, __t('progress.streak')) +
    statCard(a.quests_done, __t('progress.quests')) +
    statCard(a.weekly_wins, __t('progress.wins'));
  var badges = (a.milestones || []).map(function (m) {
    return '<span class="badge badge-' + (m.hit ? 'success' : 'neutral') + '">' +
      (m.hit ? '✓ ' : '') + escapeHTML(m.label) + '</span>';
  }).join(' ');
  box.innerHTML = '<div class="metric-stats">' + stats + '</div>' +
    '<div class="ach-title">' + escapeHTML(a.title || '') + '</div>' +
    '<div class="ach-badges">' + badges + '</div>';
}
// statCard(v, label) is the shared helper defined in Task 6 — reuse it, do not redefine.
```

- [ ] **Step 2: Verify** — `node --check`; manual: achievements tab shows level/XP/streak/quests/wins + milestone badges (hit vs neutral).

- [ ] **Step 3: Commit**

```bash
git add static/progress.js
git commit -m "Add Achievements tab: level, XP, streak, quests, milestones"
```

---

### Task 10: i18n keys (TR/EN)

**Files:**
- Modify: `locales/tr.json`, `locales/en.json`, `tests/test_i18n.py` (only if parity test needs it)

**Interfaces:** new `progress.*` keys used by Tasks 3–9.

- [ ] **Step 1: Enumerate + add**

`grep -rhoE "progress\.[a-z_0-9]+" templates/progress.html static/progress.js app/blueprints/tracking.py | sort -u` → the full used set. Add every missing key to BOTH locales (TR display + EN translation), matching file format; don't duplicate existing `progress.*` keys. New keys include the tab names, heatmap legend (`less`/`more`), `ins_*`/`ms_*` insight & milestone copy (with `{delta}`/`{n}`/`{pct}` placeholders as used by `t(...)`), `level`/`streak`/`quests`/`wins`, `chart_calories`/`chart_volume`.

Example (`tr.json`): `"progress.ins_workout_body": "Son 7 günde {n} antrenman. Böyle devam!"`, `"progress.ms_streak7": "7 Gün Seri"`, `"progress.tab_weight": "Kilo & Vücut"`. (`en.json`: `"Weight & Body"`, etc.)

- [ ] **Step 2: Verify** — `python -m pytest tests/test_i18n.py -q` (TR/EN parity + progress render). Green.

- [ ] **Step 3: Commit**

```bash
git add locales/tr.json locales/en.json tests/test_i18n.py
git commit -m "Add i18n keys for progress redesign"
```

---

### Task 11: Polish + regression tests (a11y, empty/loading, responsive)

**Files:**
- Modify: `static/progress.css`, `templates/progress.html`, `static/progress.js`
- Create: `tests/test_progress_ui.py`

- [ ] **Step 1: Render test** — create `tests/test_progress_ui.py` (mirror `tests/test_i18n.py` fixtures): assert `client.get("/progress-page")` 200 contains `id="heatmap-grid"`, `id="insight-row"`, `data-action="switchTab"`, all four `tab-` panels, `/static/progress.js`, `/static/progress.css`, and `'--volt' not in html`. Run → PASS.
- [ ] **Step 2: A11y** — `.tab-bar` `role="tablist"` + `aria-selected` synced on switch; check-in sheet `role="dialog"`/`aria-modal`/Esc-close/focus-on-open; heatmap cells `title=` date; charts have an adjacent text summary or `aria-label`; ≥44px targets; `:focus-visible`.
- [ ] **Step 3: Loading/empty** — `.skeleton` in chart containers while fetching; `.no-data`/`.empty-state` for users with no history; insights row hides cleanly when empty.
- [ ] **Step 4: Responsive + reduced-motion** — verify 360/768/1024px: heatmap scrolls horizontally (never overflows body), insight row scrolls, tabs wrap; `@media (prefers-reduced-motion: reduce)` covers chart-less transitions.
- [ ] **Step 5: Full verification** — `node --check static/progress.js`; `python -m pytest -q` all green. Manual: every tab + heatmap + insights + week/month toggle + check-in submit; EN locale; 360px. `git diff --stat` shows only `app/blueprints/tracking.py` in `app/` (read-only routes).
- [ ] **Step 6: Commit**

```bash
git add static/progress.css templates/progress.html static/progress.js tests/test_progress_ui.py
git commit -m "Progress a11y, empty/loading states, responsive polish + render tests"
```

---

### Task 12: Handoff doc

**Files:**
- Create: `docs/archive/handoff-2026-07-07-phase5-workout.md` (move current `docs/handoff.md`)
- Rewrite: `docs/handoff.md`

- [ ] **Step 1: Archive + write** — `git mv docs/handoff.md docs/archive/handoff-2026-07-07-phase5-workout.md`; write a new `docs/handoff.md` per the phase-5 "End" checklist (Completed work, Files modified, Components created/refactored, Architectural decisions [read-only additive endpoints, deterministic insights, heatmap CSS grid, Body merged into Weight], Remaining tasks, Known issues, Next steps [Profile surface], + the quality review: Responsiveness / Accessibility / Visual consistency / Code maintainability / Reusability / Performance / UX clarity). Note deferred body-measurement logging.
- [ ] **Step 2: Commit**

```bash
git add docs/handoff.md docs/archive/handoff-2026-07-07-phase5-workout.md docs/superpowers/plans/2026-07-07-phase5-progress-redesign.md
git commit -m "Phase 5 progress redesign handoff"
```

---

## Self-Review

**Spec coverage:** Weight & Body tab → Task 7 (+ Task 5 check-in). Nutrition/Workout tabs + week/month trends → Task 1 (endpoints) + Task 8 (UI). GitHub heatmap → Task 2 (endpoint) + Task 6 (CSS-grid render). Achievements → Task 3 + Task 9. AI Insights (deterministic) → Task 3 + Task 6. Charts (Chart.js) → Tasks 7–8. Check-In preserved → Task 5. Additive read-only backend → Tasks 1–3. Canonical CSS + JS extraction → Tasks 4–5. i18n → Task 10. a11y/empty/responsive + tests → Task 11. Handoff → Task 12. All spec sections covered.

**Placeholder scan:** No "TBD/TODO". The Task 3 `>` note (t()-returns-key until Task 10) and Task 4/7 "migrate old rules / reuse old config" notes point at concrete existing code (the old `progress.html` `<style>`/Chart.js configs), not deferrals. Tasks 5 ships intentional stubs replaced in Tasks 6–9 (each task runs green standalone).

**Type consistency:** `_progress_range(range_key) -> (start, n)` defined Task 1, reused Tasks 2–3. Endpoint JSON shapes are identical between the producing route (Tasks 1–3) and the consuming loader (Tasks 6–9): `nutrition.days[].{date,kcal,p,c,f}`, `workout.days[].{date,sessions,volume,active_min}`+`totals`, `heatmap.cells[].{date,level}`, `achievements.{level,title,rank_points,weekly_xp,streak,quests_done,weekly_wins,milestones[]}`, `insights.insights[].{icon,title,body,tone}`. `statCard(v,label)` defined once (Task 9) — Task 7/8 stat cards reuse it (Task 9 lands its definition; Tasks 7–8 that also call `statCard` must ensure it exists — define `statCard` in Task 6 alongside the other shared render helpers, and have Tasks 7–9 consume it). `_chartBase(yOpts)` shared helper defined in Task 7, reused Task 8. `switchTab`/`loadXTab` names consistent Tasks 5–9. `renderHeatmap`/`renderInsights`/`renderOverview` consistent Task 5↔6.

**Fix applied inline:** moved `statCard` definition to Task 6 (shared render helpers) so Tasks 7–9 can all use it without ordering hazard.
