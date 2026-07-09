# AxisAI Phase 6 Auth Review

Date: 2026-07-09

## Authentication Architecture Overview

AxisAI uses Flask-Login sessions with username/password auth and optional Amazon Cognito flows. The server-side auth routes remain in `app/blueprints/auth.py`; profile onboarding remains in `app/blueprints/profile.py`. Phase 6 preserves backend authentication behavior and focuses on the frontend experience.

Key controls already present:

- Login clears the pre-auth session before `login_user`, reducing session fixation risk.
- Login failures use generic credential errors for local auth.
- Registration validates username, email, and password server-side.
- Mutating fetch requests use the existing CSRF token wrapper from `_head.html` and `static/csrf.js`.
- Logout has a same-site navigation guard.
- Cognito verification and resend routes keep their existing rate limits.
- Protected app routes continue to redirect anonymous users to login.

## UX Improvements

- Landing, login, register, email verification, and onboarding now use a shared Phase 6 auth surface: `static/auth.css` and `static/auth.js`.
- Repeated inline page CSS/JS was removed from the legacy auth/onboarding templates.
- Login now has password visibility control, autocomplete hints, loading/disabled states, and live-region error/success feedback.
- Registration now has inline client validation, a password visibility control, a password strength meter, autocomplete hints, and duplicate-submit prevention.
- Email verification now uses the same polished card shell, live feedback, disabled loading states, one-time-code autocomplete, and resend handling.
- Onboarding now collects optional target weight during setup and posts it through the already-supported backend field.
- Landing was rebuilt around a mobile-first hero, clear `Get Started` and `Login` calls to action, and concise AI fitness coaching value props.

## Security Observations

- No passwords or sensitive tokens are stored in `localStorage` or `sessionStorage`.
- Auth POSTs continue to rely on the shared CSRF fetch wrapper.
- Auth forms prevent duplicate submissions by disabling the active button while a request is in flight.
- Password fields use proper password input types and only switch to text through an explicit user action.
- Error rendering uses `textContent`, avoiding HTML injection in frontend error states.
- The auth pages load shared static assets instead of page-specific inline script/style blocks, reducing CSP inline surface.
- Browser autofill hints were added for username, current password, new password, email, and one-time verification codes.

## Remaining Risks

- Forgot password and password reset are not currently implemented in backend routes or in `app/services/cognito_idp.py`. Adding a real reset flow requires compatible Cognito/local-auth backend endpoints.
- Cognito native error messages are produced by the existing helper and are user-safe, but some verification states still depend on Cognito's exact error class.
- Keyboard activation for option-card radios is visually focusable, but full arrow-key radiogroup behavior would be a future accessibility enhancement.

## Future Recommendations

- Add backend-compatible forgot-password and reset-password routes:
  - Cognito: `forgot_password` and `confirm_forgot_password`.
  - Local auth: signed, expiring reset tokens with single-use persistence.
- Add Playwright visual checks at 320, 390, 768, and desktop widths for auth surfaces.
- Add a small accessibility test pass for radiogroup keyboard behavior and focus order.
- Consider moving the remaining auth strings for password strength labels into `locales/*.json`.
