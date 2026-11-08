# Launch V1 social boundary

Founder decision (Launch V1): friends, user-to-user messages and suggestions,
the feed, Pump Check sharing and engagement, social notifications and
friends-only rankings are unavailable to every account, whatever its age,
cohort, subscription or eligibility record. Global leaderboards and global
challenge rankings are a separate decision and are unchanged.

This boundary is a product and safety decision. It makes no claim about
AxisAI's classification under Law 5651, and it does not change F01 health
consent, D1 AI consent or 13+ eligibility. Those remain separately gated.

## Owner

`app/services/launch_social.py`

- `SOCIAL_AVAILABLE = False` is a code constant. It has no environment,
  config, query, cookie or header channel, and it does not read
  `ACCOUNT_ELIGIBILITY_ENABLED`. Reopening it is a reviewed code release.
- `install(app)` adds one `before_request` hook, registered right after the
  request id and CSP nonce hooks. It runs before CSRF, the Bearer binder,
  eligibility, rate limits and every view, and it reads nothing from the
  database.
- Refusal: HTTP 404, `Cache-Control: no-store`. Page navigations
  (`GET /friends`, `/feed`, `/chat/<username>`) get the app's 404 page.
  Every other request gets JSON `{"error": ..., "code": "SOCIAL_FEATURE_UNAVAILABLE"}`.
- The same answer applies to anonymous users, browser sessions and Bearer
  clients.

## Route coverage (d13c9fa1, 32 route/method pairs)

The whole `social` blueprint is closed, so a route added to it later is
covered too. `tests/test_launch_v1_social_boundary.py` builds the matrix from
the live URL map.

| Area | Method and path | Data | Launch V1 |
|---|---|---|---|
| Friends | GET `/friends`, `/friends/list`, `/friends/select-list`, `/friends/search` | other users' identities, relationships | 404 |
| Friends | POST `/friend/request/<username>`, `/friend/accept/<id>`, `/friend/reject/<id>` | relationship writes, notifications | 404 |
| Friends | DELETE `/friend/<username>`, `/friend/request/<id>` | relationship writes | 404 |
| Messages | GET `/chat/<username>`, `/chat/<username>/messages` | DMs, shared Pump Check images | 404 |
| Messages | POST `/chat/<username>/read`, `/chat/<username>/send` | DM writes | 404 |
| Suggestions | POST `/suggest/<username>`, `/suggest/respond/<id>` | meal suggestion DMs, MealLog writes | 404 |
| Feed | GET `/feed`, `/feed/data` | friends' Pump Checks, reposts, activity | 404 |
| Feed | POST `/feed/repost`; DELETE `/feed/item/<id>` | repost writes | 404 |
| Feed | POST/DELETE `/feed/item/<id>/like`; GET/POST `/feed/item/<id>/comments`; DELETE `/feed/item/<id>/comments/<cid>` | engagement | 404 |
| Moderation | POST `/feed/hide`, `/feed/unhide`, `/feed/report` | feed-only controls | 404 |
| Pump Check engagement | POST/DELETE `/pump-check/<id>/like`; GET/POST `/pump-check/<id>/comments`; DELETE `/pump-check/<id>/comments/<cid>` | other users' checks | 404 |
| Rankings | GET `/leaderboard/data?scope=friends` | friends' XP | 404 |
| Rankings | GET `/challenges/<id>/leaderboard?scope=friends` | friends' progress | 404 |
| Rankings | GET `/leaderboard/data` (global), `/challenges/<id>/leaderboard` (global) | global ranking | unchanged |

## Pump Check

- `pump_checks.can_view_pump_check` treats every stored visibility as
  private (`launch_social.effective_visibility`), so only the owner can read a
  check or receive a signed image URL. Stored `visibility` and
  `shared_friend_ids` values are never rewritten.
- `serialize_pump_check_card` reports `visibility: "private"` and omits
  `sharedFriendIds`.
- Web `POST /workout/complete`: a missing `visibility` now means `private`
  (the legacy default was `feed`). An explicit `feed` or `friends` is refused
  with 403 `SOCIAL_FEATURE_UNAVAILABLE` before the AI photo check, the S3
  upload and any write.
- `workout_completion._fan_out_friend_messages` writes no Message while
  social is closed, whatever the caller sends.
- The owner gallery (`/pump-check-gallery*`), deletion, mobile Pump Checks and
  comparisons are owner-scoped and unchanged.

## Notifications

- `notifications.visible_notifications` keeps only
  `NON_SOCIAL_NOTIFICATION_TYPES` (currently `challenge_complete`). It is an
  allowlist, so unknown types stay hidden. It applies to list, unread count
  and mark-read.
- `notify()` writes no social notification type.
- Historical rows are kept.

## Presentation

- `app/nav.py`: the `friends` and `feed` destinations are removed.
- Profile hub: the Friends and Feed links are removed.
- Leaderboard: global tab only, and the empty-state link to `/friends` is gone.
- Challenges: global ranking tab only.
- Gallery: the social share action is removed.
- Notifications: no links to the feed or friends pages.
- Mobile (`axisai-mobile` 687efd5): no social screen, endpoint or deep link.
  It parses the `social` capability and refuses `true`. No change needed.

## Preserved

Private Pump Check (create, read, list, delete, comparisons), XP, levels,
streaks, quests, badges, personal challenge progress, global leaderboard and
global challenge ranking, non-social notifications, AI features, nutrition,
training, account deletion (`_purge_user` is unchanged and still removes
social rows).

## Remaining exposure, reported and not changed

- Global leaderboard (`/leaderboard/data`) and global challenge ranking show
  other users' username, full name, avatar, level, XP and streak or progress.
  This needs a separate founder decision. It may be material to the 5651
  assessment.
- Challenge completion still writes the existing activity and feed rows
  through `challenges.record_event`. The feed and activity reads are closed,
  so nobody else can see them.
- Owner Pump Check cards still carry historical `likesCount`, `commentsCount`
  and `repostsCount` totals (numbers only, no identities).
- `fitx_mcp` `get_friend_activities` is a stdio developer tool and is not part
  of the web or mobile runtime.
- Signed media URLs issued before deployment are not revoked. They expire on
  their own TTL (1 hour for Pump Check images).

## Reopening

Change the constant in a reviewed release. Nothing was deleted or migrated.
The legacy social suites keep the dormant code tested through the test-only
`social_reopened` fixture (`tests/conftest.py`).
