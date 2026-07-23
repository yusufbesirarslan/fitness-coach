# Template and JavaScript Map

The route-to-template and route-to-asset mapping is machine-readable in `inventory.json`. Shared foundations are `templates/_head.html`, `_nav.html`, `_actionbar.html`, `static/tokens.css`, `components.css`, `theme.css`, `nav.css`, `actions.js`, `csrf.js`, and `i18n.js`.

Core mappings:

| Area | Template | Primary JavaScript | Route owner |
|---|---|---|---|
| Landing/auth | `landing.html`, `login.html`, `register.html`, `forgot_password.html`, `reset_password.html`, `verify.html` | `auth.js`, `actions.js` | pages/auth |
| Onboarding/profile | `setup.html`, `edit_profile.html` | `auth.js`, `profile.js` | profile |
| Today/Coach shell | `index.html` | `coach_widget.js`, `actions.js`, Chart.js | tracking |
| Nutrition | `nutrition.html` | `nutrition.js`, `coach_widget.js` | nutrition |
| Training | `training.html` | `training.js`, `coach_widget.js` | training |
| Progress | `progress.html` | `progress.js`, Chart.js | tracking |
| Social | `feed.html`, `friends.html`, `chat.html` | template-local handlers, `actions.js` | social |
| Gamification | `leaderboard.html`, `quests.html`, `challenges.html` | template-local handlers | gamification/challenges |
| Secondary | `pump_check_gallery.html`, `manage_stack.html`, `premium.html`, `notifications.html` | template-local handlers | profile/supplements/pages/notifications |

Static risk notes: Coach is injected as a shared widget and persists messages in local storage; several secondary templates still contain page-local script identifiers recorded as `inline-*`; Chart.js is externally consumed by Dashboard/Progress; and shared navigation plus contextual controls affect nearly every authenticated route. These dependencies drive full coverage for routes that would otherwise look secondary.
