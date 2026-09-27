# Nutrition VNext PR1 evidence

Measured with a seeded active Training plan in Chromium at exact `origin/main`
`d4d446a345d5177b8ea5a2efa25c79b83353d42d` and the PR1 worktree. Both
runs used the same browser fixture, viewport height 900px, and test user state.

| Width | Metric | Before | PR1 |
| --- | --- | ---: | ---: |
| 390px | Training box (x, y, w, h) | 16, 164, 358, 927 | 16, 128, 358, 242 |
| 390px | Nutrition box (x, y, w, h) | 16, 1107, 358, 397 | 16, 386, 358, 343 |
| 390px | Document height | 1696 | 1708 |
| 1366px | Training box (x, y, w, h) | 139, 184, 736, 898 | 139, 128, 536, 291 |
| 1366px | Nutrition box (x, y, w, h) | 907, 184, 320, 352 | 691, 128, 536, 291 |
| 1366px | Document height | 1130 | 1330 |

Neither measured page had horizontal overflow. The old markup placed all
Training detail before Nutrition in reading order; PR1 places both domain
summaries before Training detail. At both widths, the measured initial
non-static requests were `/training` and `/notifications/unread-count` before
and after PR1. The existing optional weekly program fetch remains one request
when enabled, as its browser regression test asserts.

The bounded Plan fact query tests remain at 7 SELECTs with workout sessions
disabled and 9 with sessions enabled. One of those reads is the existing
Supplement count; there are no Nutrition detail or provider reads on Plan.
PR1 adds no asset, endpoint, database model, migration, or client authority.

The PR1 browser suite covers 320, 390, 430, 768, 1024, and 1366px in TR and
EN. The existing Training browser suite separately covers Start/Resume in the
first viewport, stale sessions, recovery, dialogs, keyboard order, and request
behavior. Controlled in-memory mutations verify that the new contract rejects
nesting Nutrition under Training, demoting its heading, promoting Supplements,
removing the Nutrition CTA, restoring the 320px rail, and replacing the
unavailable Nutrition copy path with a fabricated zero.
