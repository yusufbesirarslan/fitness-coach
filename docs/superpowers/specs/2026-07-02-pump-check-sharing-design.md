# Pump Check Sharing and Gallery Design

Date: 2026-07-02

## Scope

Enhance the existing Pump Check workout-completion flow without changing the AxisAI design language. The feature adds feed sharing, friend-only sharing, a personal Pump Check Gallery, and navigation changes:

- `/feed` is a separate social timeline page.
- The bottom navigation replaces Club with Feed.
- Club remains available from the hamburger drawer as `/leaderboard`.
- `/friends` remains dedicated to friend search, requests, and friend management.

The existing daily Pump Check completion guard remains intact: one successful Pump Check per user per Istanbul day for workout completion and XP.

## Existing Context

The current app is a Flask monolith using SQLAlchemy, Jinja templates, CSRF-wrapped fetch calls, and inline scripts with CSP nonces. The current Pump Check modal lives in `templates/training.html` and posts to `/workout/complete`. `PumpCheck` already stores `image_key`, `location_type`, `description`, `valid`, `fallback`, `date_key`, and `created_at`.

Social primitives already exist:

- `Friendship` defines accepted friend relationships.
- `Message` supports direct chat and typed messages.
- `User.avatar_src` provides avatar URLs.
- `s3_helper.generate_presigned_url` provides short-lived private image URLs.

## Data Model

Extend `PumpCheck` as the canonical record for completion, gallery, feed, and friend shares.

Add fields:

- `visibility`: string enum-like value: `feed`, `friends`, or `private`. Default `feed` for new UI submissions.
- `shared_friend_ids`: JSON list of user IDs. Empty list unless `visibility == "friends"`.
- `likes_count`: integer denormalized count, default `0`.
- `comments_count`: integer denormalized count, default `0`.

Keep existing fields:

- `id`
- `user_id`
- `image_key` as the private stored image identifier. API responses expose this as `imageUrl` via presigned URLs.
- `location_type`, presented to clients as `environment`.
- `description`
- `created_at`
- `date_key`
- `valid`
- `fallback`

Add tables:

- `PumpCheckLike`
  - `id`
  - `pump_check_id`
  - `user_id`
  - `created_at`
  - unique constraint on `(pump_check_id, user_id)`

- `PumpCheckComment`
  - `id`
  - `pump_check_id`
  - `user_id`
  - `body`
  - `created_at`

Like/comment rows are separate because per-user state and comment bodies do not belong in the `PumpCheck` row. Counts stay on `PumpCheck` for efficient feed cards.

## Backend Flow

`POST /workout/complete` accepts current fields plus:

- `visibility`: `feed`, `friends`, or `private`
- `shared_friend_ids`: array of IDs, required when `visibility == "friends"`

Processing order:

1. Load active training plan.
2. Check current daily Pump Check idempotency.
3. Validate image data URL.
4. Validate `visibility` and friend recipients.
5. Validate Pump Check image/environment with existing `validate_pump_check`.
6. Upload the image to S3 when enabled.
7. Create the `PumpCheck` row.
8. Create the workout marker `WorkoutLog`.
9. Award XP and claim the workout quest as today.
10. If `visibility == "friends"`, create one `Message` per selected friend using `message_type="pump_check"`.
11. Commit all database side effects in one transaction.

If the transaction fails because the daily unique constraint is hit, all side effects roll back and the existing `already_completed` behavior remains.

Friend recipient validation:

- Only accepted friends of `current_user` may be selected.
- Unknown, duplicate, or non-friend IDs are rejected with `400`.
- `friends` visibility requires at least one selected friend.

Feed visibility:

- No extra feed row is created.
- `/feed` queries `PumpCheck.visibility == "feed"` for accepted friends and the current user.

Gallery:

- No separate gallery table is created.
- The gallery queries the current user's `PumpCheck` rows regardless of visibility.

## Routes

Add or extend routes in the social/profile/training area:

- `GET /feed`
  - Renders the separate Feed page.

- `GET /feed/data?page=N`
  - Returns paginated feed cards from accepted friends plus current user, newest first.
  - Includes image URL, user avatar, username, relative/display time, workout score if available, environment, description, like count, comment count, and whether current user liked it.

- `POST /pump-check/<id>/like`
  - Toggles or creates a like depending on chosen implementation detail.
  - Recommended: idempotent explicit like endpoint creates if missing.

- `DELETE /pump-check/<id>/like`
  - Removes current user's like.

- `GET /pump-check/<id>/comments`
  - Returns comments for a feed-visible Pump Check visible to the current user.

- `POST /pump-check/<id>/comments`
  - Adds a comment if current user can see the Pump Check.

- `GET /pump-check-gallery`
  - Renders the personal gallery page.

- `GET /pump-check-gallery/data?page=N`
  - Returns current user's Pump Checks, newest first.

- `DELETE /pump-check-gallery/<id>`
  - Deletes only the current user's Pump Check.

- `GET /friends/select-list?q=...`
  - Returns accepted friends for the Pump Check friend selector.
  - Sort order: recently contacted first by latest direct `Message.timestamp`, then remaining friends by username.

Visibility checks:

- Feed-visible Pump Checks are visible only to the owner and accepted friends of the owner.
- Friend-only Pump Checks are visible only to the owner and explicitly selected `shared_friend_ids`.
- Private Pump Checks are visible only to the owner.
- Gallery endpoints are owner-only.
- Delete is owner-only.

## Pump Check Modal UX

Keep the existing modal style, spacing, type, buttons, and animation.

Add a `SHARE TO` section below the description field:

- Segmented control or radio-style pills using existing `goal-card`/`pump-field` patterns.
- Default: `Feed`.
- Options: `Feed`, `Friends`.

When `Friends` is selected:

- Reveal the friend selector in the same modal.
- Include search input, recently contacted friend list, selected chips, and multi-select behavior.
- Disable Complete Workout until at least one friend is selected.

Image preview:

- Keep existing dropzone.
- Add client-side compression before base64 assignment where browser APIs support it.
- Show a smooth preview transition and upload/progress state.

Submit state:

- Keep existing `pump-busy` spinner.
- Add a lightweight progress bar state for upload/verification.
- Show success feedback before closing, without changing the modal visual language.

Accessibility:

- Share control uses real buttons or radio inputs with keyboard support.
- Friend chips have remove buttons with accessible labels.
- Modal focus remains trapped by existing close/Escape behavior where practical.

## Feed Page UI

`/feed` uses the existing dark premium shell:

- `theme.css`
- `nav.css`
- `_nav.html`
- `_actionbar.html`
- `nav_active="feed"`

Feed card content:

- User avatar
- Username
- Time posted
- Workout photo
- Workout score if available
- Environment label/value
- Description label/value
- Like count
- Comment count
- Like and comment controls

Card layout follows the requested metadata style:

```text
Environment:
Gym

Description:
Upper body session. Massive pump today.
```

Loading and empty states:

- Initial skeleton cards.
- Infinite scroll or "load more" pagination. Recommended: paginated load-more with IntersectionObserver auto-load.
- Empty state suggests completing a workout or adding friends.

## Friend-Only Chat Card

Use existing `Message` with `message_type="pump_check"`.

Store a compact JSON body:

```json
{
  "pump_check_id": 123,
  "image_key": "pump-checks/...",
  "environment": "Gym",
  "description": "Great push workout today.",
  "created_at": "2026-07-02T..."
}
```

`templates/chat.html` renders this message type as a Pump Check card:

- Photo
- Environment
- Description
- Timestamp

If the Pump Check has been deleted or the recipient is not authorized, render an unavailable card instead of a broken image.

## Gallery Page UI

`/pump-check-gallery` is linked from Profile (`/edit-profile`) as a small section/card matching existing profile sections.

Gallery page:

- Existing shell and nav.
- Responsive image grid.
- Lazy-loaded images.
- Upload date, workout score if available, environment, and short description.
- Skeleton loaders.
- Paginated data loading.

Detail modal:

- Full-screen dark modal.
- Large image.
- Environment.
- Description.
- Date.
- Workout score if available.
- Sharing status: `Shared to Feed`, `Shared to Friends`, or `Private`.
- Delete button with confirmation.

Deletion:

- Owner-only.
- Removes feed visibility because the canonical Pump Check is deleted.
- Friend chat cards referencing deleted content show unavailable state.

## Navigation

Shared navigation should be preferred where possible.

Bottom action bar:

- Home
- Training
- Nutrition
- Feed

Drawer menu:

- Keep existing links.
- Add Feed if useful for desktop parity.
- Add Club/Leaderboard as `/leaderboard`.
- Friends remains `/friends` and only manages friends.

`nav_active` values:

- `feed` for `/feed`
- `club` for `/leaderboard`
- `friends` for `/friends`

Some existing templates hardcode nav instead of including `_nav.html` and `_actionbar.html`. The implementation may update the relevant repeated nav blocks in-place, but unrelated layout refactors are out of scope.

## Image Handling

Stored Pump Check images remain private S3 objects. API responses generate presigned URLs after authorization checks.

Client compression target:

- Max dimension around 1600px.
- JPEG/WebP quality around 0.82 where supported.
- Preserve current 5MB validation as a server-side guard.

Server behavior:

- Continue accepting base64 data URLs through `validate_pump_check_image`.
- Continue upload fail-open behavior for workout completion, but feed/gallery cards without image should handle missing images gracefully.

## Testing Plan

Tests follow test-first implementation.

Backend tests:

- `/workout/complete` defaults to feed visibility when omitted.
- Feed visibility saves `PumpCheck.visibility == "feed"` and appears in a friend's feed.
- Non-friends cannot see feed posts.
- Friends visibility requires selected accepted friends.
- Friend-only share creates chat messages only for selected friends.
- Unselected friends cannot access friend-only images/data.
- Gallery includes feed, friends, and private Pump Checks for owner.
- Gallery delete is owner-only and removes the record.
- Like endpoint enforces one like per user and updates `likes_count`.
- Comment endpoint only allows authorized viewers and updates `comments_count`.
- Daily idempotency still prevents a second completion and rolls back side effects.

Frontend smoke checks:

- Pump Check modal defaults to Feed.
- Selecting Friends reveals the selector and selected chips.
- `/feed` renders skeleton, empty, and card states.
- `/pump-check-gallery` renders grid and detail modal.
- Bottom nav shows Feed instead of Club.

## Rollout Notes

Alembic migration is required for `PumpCheck` columns and new like/comment tables. Existing Pump Checks should backfill `visibility` to `private` or `feed` depending on desired product behavior. Recommended: backfill existing rows to `private` to avoid unexpectedly publishing historical workout photos.

The app boot process already auto-applies pending migrations in production, so the migration should be reviewed carefully before commit.
