# Sprint 1 — Frontend Audit & Design System Foundation

Date: 2026-07-13  
Product: AxisAI  
Status: Approved for implementation planning

## Objective

Audit every user-facing AxisAI screen at desktop, tablet, and mobile widths, then
resolve shared frontend inconsistencies at their design-system source. Sprint 1
creates a production-ready foundation for later page-specific redesign sprints;
it does not redesign individual screens or change product behavior.

## Success Definition

Sprint 1 is successful when:

- every public, authentication, onboarding, core-product, social, and secondary
  user-facing screen has a recorded audit result;
- the audit ranks findings consistently and distinguishes observed defects from
  risks that need interaction or assistive-technology verification;
- the existing token and component system has a documented inventory;
- repeated visual and interaction defects are fixed through shared tokens,
  components, utilities, or shell rules rather than page-local patches;
- global components produce no unintended horizontal overflow at the validated
  viewport matrix;
- shared controls have consistent hover, active, focus-visible, loading, and
  disabled behavior;
- focused frontend contract tests and the relevant existing regression suite
  pass; and
- the reports and implementation are independently reviewable before Sprint 2.

## Scope

### Public, authentication, and onboarding

- Landing (`/welcome`)
- Login (`/login`)
- Register (`/register`)
- Forgot password (`/forgot-password`)
- Reset password (`/reset-password`)
- Email verification (`/verify`)
- Onboarding/setup (`/setup`)
- Public invitation and any other publicly reachable HTML surface
- Error pages (`404`, `500`)

### Core product and metrics

- Dashboard/Home
- AI Coach, including the shared coach widget and its expanded interaction
- Nutrition and its logging/planning states
- Training and plan/preference states
- Progress
- Weekly check-in
- Profile/edit profile

### Social and engagement

- Feed
- Friends and invitation states
- Chat and suggestion cards
- Leaderboard
- Quests
- Pump Check and gallery/sharing states

### Secondary product surfaces

- Menu scanner and menu-result states
- Supplements/stack management
- Premium
- Settings/profile hub destinations
- Wearable or integration surfaces that render HTML
- Any remaining user-facing templates or client-rendered screen states found
  during the route/template inventory

Backend-only APIs, CLI output, email HTML, infrastructure dashboards, and MCP
interfaces are not frontend screens for this sprint.

## Constraints

- Do not redesign an individual page.
- Do not change business logic, backend behavior, route contracts, models, or
  database schema.
- Do not introduce new product features.
- Preserve the established AxisAI identity: electric blue primary color,
  dark-first surfaces, light-theme support, Bebas Neue display typography, and
  Inter body typography.
- Prefer existing canonical tokens and components. Add or change a shared token
  only when it represents a genuine reusable semantic role.
- Do not add a page-specific override to conceal a shared defect.
- Keep CSP nonce, CSRF, localization, analytics, and authentication contracts
  intact.
- Preserve unrelated and pre-existing uncommitted work.

## Audit Method

### 1. Inventory

Build an executable inventory from Flask's route map, Jinja templates, linked
stylesheets, JavaScript modules, locale catalogs, and existing frontend tests.
Map every screen to:

- route and authentication requirement;
- template and included partials;
- page and shared CSS;
- page and shared JavaScript;
- primary components and important visual states;
- test coverage; and
- required seed/session state for inspection.

The inventory is the completion checklist. A screen is not considered audited
until it has an entry, even if it has no findings.

### 2. Visual and responsive inspection

Inspect each screen with representative content and, where applicable, empty,
loading, validation-error, success, and disabled states. Validate at minimum:

- Mobile: 390 × 844
- Tablet: 768 × 1024
- Desktop: 1280 × 900
- Large desktop spot-check: 1440 × 900

Also run a narrow-phone overflow check at 320px where feasible. The 390, 768,
and 1280 widths are the acceptance matrix; 320 and 1440 are stress checks.

For each screen record:

- horizontal and vertical overflow;
- clipped, obscured, or overlapping content;
- flex/grid wrapping and min-width behavior;
- container width and large-screen balance;
- spacing, alignment, hierarchy, typography, and density;
- navigation and current-location clarity;
- form labels, help, validation, recovery, and submission feedback;
- empty, loading, error, and success states;
- hover, active, focus-visible, loading, and disabled states;
- dark/light theme behavior where the control is exposed;
- Turkish/English expansion and copy consistency; and
- keyboard order and obvious screen-reader semantics.

### 3. Static design-system inspection

Audit `tokens.css`, `components.css`, `nav.css`, `auth.css`, `theme.css`, all
page stylesheets, inline styles, and client-generated markup. Search for:

- legacy token aliases and direct primitive use outside the token source;
- repeated raw colors, spacing, radii, shadows, typography declarations, and
  breakpoints;
- duplicate button, input, card, badge, modal, dropdown, tab, navigation, icon,
  empty-state, and loading-state implementations;
- selectors whose specificity or load order creates fragile overrides;
- fixed widths/min-widths and viewport units that create responsive defects;
- interaction styles without equivalent keyboard or disabled states; and
- page-local patterns that should consume an existing shared component.

Static findings must be confirmed against rendered behavior before being called
visual defects. Maintainability debt may be reported independently.

### 4. Accessibility inspection

Review against WCAG 2.2 AA-oriented expectations, including:

- normal text contrast of at least 4.5:1 and large text/UI graphics of at least
  3:1 where applicable;
- visible focus indicators and logical keyboard order;
- control names, labels, descriptions, roles, states, and live-region behavior;
- minimum 44 × 44 CSS-pixel touch targets for primary mobile interactions, or
  an equivalently sized hit area;
- heading and landmark structure;
- errors associated with their fields and announced without color alone;
- zoom/text expansion without clipping or loss of function; and
- reduced-motion behavior for shared animation.

A screenshot cannot prove keyboard or screen-reader behavior; those items must
be inspected in rendered DOM and interaction tests.

## Severity Model

| Severity | Definition | Sprint 1 handling |
|---|---|---|
| Critical | Prevents a core task, hides required content, creates unavoidable overflow on a supported viewport, or introduces a severe accessibility barrier across screens | Fix immediately through the shared foundation and add regression coverage |
| High | Repeatedly harms usability, navigation, form completion, responsive stability, or accessibility across multiple screens | Fix in Sprint 1 when the root cause is shared |
| Medium | Noticeable consistency, hierarchy, state, localization, or maintainability defect without blocking a task | Fix when a shared token/component change is safe; otherwise document for the owning page sprint |
| Low | Cosmetic polish, isolated debt, or enhancement with limited user impact | Document and defer unless eliminated incidentally by an approved shared change |

Page-specific findings remain in the reports for later sprints. Sprint 1 only
implements a page-level change when it is the minimum integration necessary to
adopt or verify a shared foundation fix.

## Design System Foundation

### Token policy

`static/tokens.css` remains the canonical source for:

- semantic colors and theme mappings;
- typography families, sizes, weights, line heights, and tracking;
- 4/8px-based spacing;
- radius, border width, elevation, opacity, icon size, motion, z-index, layout,
  and breakpoint reference values.

New production CSS must consume canonical names. Legacy aliases are not used by
new code. An alias may be removed only after all consumers are migrated and the
computed result is verified in both themes.

### Shared component policy

`static/components.css` is the reusable component layer. Sprint 1 audits and,
where evidence supports it, standardizes:

- primary, secondary/ghost, and danger buttons;
- inputs, selects, textareas, labels, helper text, and validation states;
- cards and surface/elevation variants;
- badges and chips;
- modals, sheets, dropdown/popover behavior, and close controls;
- tabs and navigation items;
- empty, loading, error, and success states;
- icons and icon-only control sizing;
- focus-visible, hover, pressed, loading, and disabled states; and
- shared layout/container utilities.

Existing public class names are treated as API. Consolidation should migrate
consumers or add compatible composition classes; it should not silently break
markup on other screens.

### Layout policy

Shared shells and containers must:

- use fluid width with consistent responsive gutters and a documented max-width;
- allow grid/flex children to shrink through correct `min-width: 0` behavior;
- wrap or stack at the canonical breakpoints;
- reserve space for fixed navigation and mobile safe areas;
- avoid `100vw` and fixed widths that include scrollbar or gutter overflow;
- preserve deliberate internal scrolling without allowing document-level
  horizontal scrolling; and
- remain usable with translated copy and 200% text zoom.

Global clipping is a safety net, not evidence that overflow is fixed. Any child
that exceeds its container must be corrected at its layout source.

### Typography policy

- Use Bebas Neue only for deliberate display/headline roles.
- Use Inter for body, labels, controls, and data support text.
- Maintain sequential heading semantics independent of visual styling.
- Use canonical text, weight, leading, and tracking tokens.
- Keep body and form text readable on mobile; avoid tiny all-uppercase copy for
  essential instructions.
- Prefer wrapping to truncation for required content.

## Shared Implementation Boundary

Allowed Sprint 1 changes include:

- token corrections and additions with dark/light parity;
- reusable component consolidation;
- global focus, form, button, card, and interaction-state improvements;
- shared shell/navigation consistency;
- responsive container and overflow root-cause fixes;
- shared accessibility semantics or utility classes;
- removal of proven duplicate or dead shared styles; and
- contract tests that prevent shared regressions.

Deferred to later page sprints:

- page composition changes;
- new hero, bento, card, or dashboard layouts;
- page-specific typography art direction;
- new workflows or changed information architecture;
- restructuring individual forms beyond adopting shared form behavior;
- new empty-state content or feature-specific illustrations; and
- isolated cosmetic changes without a shared root cause.

## Deliverables

### 1. Frontend audit report

Create a dated report containing:

- executive summary;
- audited-screen matrix;
- severity matrix and counts;
- Critical, High, Medium, and Low findings; and
- one record per issue with title, severity, affected locations, observed
  behavior, impact, likely root cause, recommended solution, Sprint 1 disposition,
  and evidence/viewport.

Repeated issues are grouped as patterns with every affected surface listed.

### 2. Design system report

Document:

- the current token and component inventory;
- duplicate and near-duplicate component families;
- unsupported or inconsistent states;
- token violations and proposed canonical mapping;
- shared changes implemented in Sprint 1; and
- remaining component debt assigned to later sprints.

Update `docs/design-system.md` when the canonical contract changes.

### 3. Responsive audit report

Document desktop, tablet, mobile, narrow-phone, and large-desktop findings,
including recurring causes and the viewport evidence for each. Distinguish
document overflow from intentional component-level scrolling.

### 4. Accessibility report

Document contrast, focus, keyboard, touch target, form, semantic, live-region,
reduced-motion, and screen-reader concerns. Mark each item as verified, fixed,
deferred, or requiring manual assistive-technology validation.

### 5. Implementation and verification evidence

Provide the shared CSS/template/JavaScript changes, focused regression tests,
before/after viewport evidence for affected shared patterns, and a final test
summary. No report may claim a screen or state was tested without recorded
evidence.

## Testing Strategy

Follow test-first development for every shared behavior change.

### Static and rendering contracts

- Extend the existing design-system tests for canonical tokens, component
  selectors, stylesheet order, and prohibited legacy patterns.
- Add template/route inventory coverage so every user-facing screen renders and
  is represented in the audit matrix.
- Add structural checks for shared form labels, autocomplete, live regions,
  navigation state, and reusable component adoption where appropriate.

### Browser validation

- Use deterministic local data and authenticated sessions.
- Capture the required viewport matrix for every screen.
- Test keyboard traversal and focus visibility on representative instances of
  every shared interactive component.
- Exercise hover, active, loading, disabled, error, success, and empty states
  where those states can be reached safely without external services.
- Verify Turkish and English on representative high-expansion screens.
- Check dark and light themes on surfaces exposing the theme switch.

### Regression suite

Run focused frontend/design-system/auth/navigation tests during development,
then the relevant broader suite. Because the existing Windows suite is large,
allow sufficient runtime and report the exact command, pass/fail count, and any
pre-existing warnings separately.

## Work Decomposition

Sprint 1 is one independently reviewable deliverable but should be implemented
in ordered stages:

1. Build the route/screen/state inventory and local browser audit harness.
2. Capture the complete responsive and accessibility evidence baseline.
3. Write the four audit reports and rank shared versus page-specific findings.
4. Add failing contract tests for the approved shared foundation defects.
5. Implement token, component, container, navigation, and state fixes in small
   shared increments.
6. Re-capture affected surfaces and confirm that shared fixes do not regress
   unrelated pages.
7. Update design-system documentation and finalize the reports with actual
   dispositions and verification evidence.
8. Run the focused and broader regression suites and prepare the Sprint 1
   review handoff.

## Risks and Mitigations

- **Large screen count:** use the executable inventory as a checklist; a zero-
  finding screen still needs evidence.
- **Shared CSS blast radius:** test first, change one shared primitive at a time,
  and visually sample all consumers after each change.
- **Dirty worktree:** modify only frontend/report/test files required by this
  sprint and never overwrite unrelated auth/infrastructure edits.
- **State-dependent screens:** create deterministic local fixtures; document any
  external-service state that cannot be reproduced rather than guessing.
- **False overflow confidence:** do not treat global `overflow-x: clip` as a fix;
  inspect bounding boxes and correct the overflowing child.
- **Scope creep into redesign:** every implementation change must name the
  repeated defect and shared layer it resolves. Otherwise it is deferred.
- **Accessibility overclaim:** separate automated checks from manual keyboard,
  zoom, contrast, and assistive-technology verification.

## Acceptance Criteria

- Every discovered user-facing screen appears in the audited-screen matrix.
- Required desktop, tablet, and mobile evidence exists for every screen.
- The severity matrix and all four reports are complete.
- The design-system inventory and duplicate-component analysis are complete.
- Approved shared inconsistencies are resolved at their common source.
- No shared component causes unintended document-level horizontal overflow in
  the acceptance viewport matrix.
- Shared interactive components expose consistent hover, active, focus-visible,
  loading, and disabled states.
- Shared form controls retain visible labels and accessible feedback behavior.
- Design-system documentation matches the implemented contract.
- Focused tests and the relevant regression suite pass with recorded evidence.
- No backend/business-logic behavior or page-specific redesign is included.
- Sprint 1 is ready for an explicit review gate before Sprint 2 begins.
