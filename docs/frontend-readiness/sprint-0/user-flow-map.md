# User Flow Map

All date-sensitive flows use the fixed scenario record selected before request handling; wearable sync, workout day, streak, and history boundaries therefore do not drift with the host clock.

| Flow | Route sequence | Scenarios | Critical checks |
|---|---|---|---|
| First run | welcome → register → verify → setup → dashboard | anonymous, partial, new-empty | validation, back-state, optional wearable, plan preview |
| Recovery | login → forgot password → reset password → login | anonymous | rate limits, expired/mismatch, autofill, clear errors |
| Daily plan | dashboard → training or nutrition → dashboard | rest-day, active-workout, wearable states | one next action, state copy, sync provenance |
| Food logging | nutrition → search/scan → serving → confirm → history | new-empty, active-workout | quantity/unit, macro preview, Undo, one scroll owner |
| Workout | training → selected day → start/complete → progress | rest-day, active, completed | explicit day state, no placeholders, recovery action |
| Coach | dashboard/Coach → send/stream/stop/retry → history | coach-history | safe rendering, error recovery, preserved conversation |
| Progress | dashboard → progress → check-in/history | new-empty, progress-history | charts, text alternative, history boundaries |
| Social | feed → friends → chat | social-empty, active-workout | useful empty state, privacy, return path |
| Connections | edit profile → connect/disconnect → dashboard | wearable-connected/disconnected | optionality, last-sync time, manual conflict |

The automated runner captures declared states but does not fabricate third-party service responses. External integrations are disabled and fixtures are local. Runtime outcomes remain pending the supported Chromium execution.
