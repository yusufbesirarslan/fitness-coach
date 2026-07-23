# AxisAI Frontend Beta-Readiness Audit

**Audit date:** 22 July 2026  
**Beta horizon:** approximately 55 days  
**Assessment:** Not beta-ready in the reviewed state  
**Current maturity:** Late prototype / early alpha

## Evidence and limits

This audit is based on the supplied AxisAI artifacts:

- `AxisAI — Dashboard.pdf`
- `Feed - AxisAI.pdf`
- `Nutrition — AxisAI.pdf`
- `Workout — AxisAI.pdf`
- Current-style mobile captures: `chatbot-1.jpeg`, `chatbot-2.jpeg`, `insight2.jpeg`, `kalori-ozet.jpeg`, and `search-food.jpeg`
- Directional concept mockups: `main.jpeg`, `menu.jpeg`, `nutrition.jpeg`, and `wear.jpeg`
- The local `landing-page` implementation, rendered at desktop and a 375 px test viewport
- The historical `Axis AI App Tasarımı.zip` screens, used only to understand feature scope and legacy patterns

The polished blue phone mockups and older green FITX screens are not treated as proof of current product behavior. They are references only.

No runnable application source or deployed application was available. Authentication, onboarding, profile, settings, destructive confirmations, true loading/error behavior, keyboard behavior, screen-reader behavior, and real device performance could not be fully verified. The normal in-app browser inspection surface also failed to connect; the landing page was therefore checked with a local headless render. Findings that depend on that render are marked with lower confidence.

## 1. Executive summary

AxisAI has a credible product premise and enough real feature depth to support a beta. The frontend does not yet communicate that maturity. It currently feels like several capable modules placed beside one another rather than one coherent health-coaching workflow.

The strongest product idea is an adaptive daily system connecting nutrition, training, recovery, and wearable data. The current application instead leads with separate dashboards, dense metrics, hidden AI, and exposed secondary features. Users are forced to assemble the product's value themselves.

### Main beta risks

1. **Mobile usability is structurally unsafe.** Floating actions overlap content and the bottom navigation; tab rows clip; wide weekly layouts do not translate to a phone; search results cover adjacent content. These are usability failures, not cosmetic defects.
2. **The navigation does not reflect the product's differentiation.** AI Coach is a floating overlay while `Club` receives a primary navigation slot despite an empty feed. The product says “adaptive intelligence,” but the shell presents a conventional tracker.
3. **Core logging is too difficult.** Nutrition's most important action is buried below summary, empty-state, and quick-add content. Search results lack a clear serving/quantity/add sequence and obscure the page.
4. **Workout state is not trustworthy.** “Today: Off Day” appears with “You completed today's workout,” and unresolved `+{xp}` / `+{bonus}` placeholders are visible. This is a direct beta blocker.
5. **Empty, AI, and data states feel unfinished.** The Feed is almost entirely blank, the AI response exposes raw Markdown, and several zero-value states look like completed dashboards rather than guided starting points.
6. **The visual system is not fully unified.** Electric blue, green, and concept-blue systems coexist across artifacts; type is often too small and low-contrast; emoji are used as structural icons; primary button color changes between screens.
7. **Trust claims are ahead of visible proof.** The landing page promises wearable integrations and daily adaptation, but the reviewed app screens do not consistently explain where recommendations come from, when data was synced, or why a plan changed.

### Launch decision

Do not take the current frontend to an unrestricted beta. A small, moderated alpha could proceed after the critical state bugs are fixed. A real beta should wait until the mobile shell, navigation, nutrition logging, workout state model, and trust/accessibility baseline are corrected.

The right 55-day strategy is not to polish every feature. It is to make four experiences excellent and coherent:

- understand today,
- log food and activity,
- complete or adjust the plan,
- ask the coach and understand why it answered that way.

Community, quests, supplement management, and advanced analytics should not compete with those workflows before beta.

## 2. Product-level assessment

### User perception

**Functional but fragmented.** The product has substantial capability, but the interface distributes that capability across disconnected areas. Dashboard, Nutrition, Workout, Coach, Feed, progress, wearables, menu scanning, and challenges behave like separate products.

**Visually distinctive but low-legibility.** The black/electric-blue identity is recognizable. Excessive low-contrast surfaces, tiny labels, condensed all-caps text, and sparse desktop layouts make the product harder to scan than it should be.

**Data-rich but not decision-oriented.** The screens show calories, macros, weight, XP, scores, weekly totals, and activity levels. They rarely answer the user's immediate question: “What should I do next?”

**Ambitious but occasionally low-trust.** Unresolved placeholders, raw Markdown, contradictory workout messages, unexplained program scores, and unsupported-looking claims make the system feel experimental.

### What is working

- The brand has a recognizable dark, athletic identity.
- The landing proposition connects nutrition, training, recovery, and wearables clearly.
- Calorie, macro, weight, and program concepts are understandable at a glance.
- Active navigation states use a visible blue accent.
- The product already anticipates empty states, AI assistance, wearable integration, menu scanning, and progress tracking.
- The landing waitlist form includes visible labeling and dedicated success/error containers.
- The mobile concept screens demonstrate that a cleaner, card-based hierarchy is achievable.

### Structural problem

The application is organized around feature ownership rather than the user's day. AxisAI should feel like one adaptive daily loop:

1. Read today's state.
2. See the recommended plan.
3. Perform or log the next action.
4. Get feedback and adaptation.
5. Review progress.

Today, users move among separate trackers and overlays without a strong sense of that loop.

## 3. Screen-by-screen findings

### 3.1 Landing page

#### What works

- The headline communicates the three pillars: nutrition, training, and recovery.
- “Join the Waitlist” is the dominant action and repeats at sensible points.
- The hero product visualization gives the abstract “adaptive system” idea a tangible form.
- The page includes legal links, a labeled email field, skip link, reduced-motion handling, and explicit success/error regions.
- The desktop hero is visually coherent and feels more mature than the application screens.

#### Findings

| Severity | Finding and user impact | Recommended fix |
|---|---|---|
| **Critical** (medium confidence) | A 375 px local render showed the header CTA, hero copy, and cookie actions extending beyond the visible viewport. If reproduced on a real device, the first visit is partially unusable. | Test at 320, 360, 375, and 390 CSS px on real Safari and Chrome. Remove any effective minimum width, let the header CTA collapse to a compact label, and stack cookie actions vertically with 16 px gutters. Treat any horizontal scroll as a release blocker. |
| **High** | The cookie banner dominates most of the first mobile viewport and blocks the value proposition. | Use a compact two-stage consent pattern: short copy plus “Accept” and “Manage,” with secondary choices in the settings sheet. Keep all actions at least 44 px high. |
| **High** | Credibility relies on product assertions rather than evidence. “AI-personalized, never templated” and named wearable support are strong claims, but no availability/status qualification is visible. | Mark integrations as “available,” “in beta,” or “planned.” Add a short “How recommendations are generated” explanation and avoid absolute claims that the beta cannot prove. |
| **Medium** | The page is long and repeats the same system/adaptation story across problem, solution, features, process, outcomes, and final CTA. Mobile users must scroll through too much positioning before signup. | Reduce the mobile landing page to hero, three outcome-led benefits, how it works, trust/privacy, and CTA. Keep deeper feature detail behind expandable sections. |
| **Medium** | The founder statement is presented in the social-proof area but is not external proof. This weakens trust rather than increasing it. | Rename the section “Why we are building AxisAI,” or replace it with pilot quotes, advisor credentials, waitlist numbers, or documented product validation. |
| **Medium** | The landing typography and surface treatment feel more polished than the current application. The transition from marketing to product would be jarring. | Share typography, color, spacing, radius, and motion tokens between landing and application before beta. |

### 3.2 Global application shell

#### What works

- The logo, avatar, and active bottom-navigation state are recognizable.
- Four bottom items remain within the recommended maximum of five.
- Persistent access to core areas is useful on mobile.

#### Findings

| Severity | Finding and user impact | Recommended fix |
|---|---|---|
| **Critical** | Floating plus and chat buttons overlap list content, cards, and bottom navigation in current mobile captures. Users can miss content or tap the wrong control. | Replace the two floating circles with one contextual, labeled primary action per screen. Reserve bottom safe-area padding equal to navigation height plus 16 px. Never allow floating controls to cover scroll content. |
| **High** | The shell has hamburger navigation, bottom navigation, avatar navigation, and floating actions at once. Users cannot tell which layer owns which feature. | Define one primary mobile navigation model. Use the avatar for account/settings, bottom navigation for top-level destinations, and contextual in-page actions for logging. Remove the hamburger on mobile unless it contains clearly secondary destinations unavailable elsewhere. |
| **High** | `Club` is primary while AI Coach—the product differentiator—is a floating overlay. Feature prominence contradicts the value proposition. | Promote Coach to primary navigation. Demote Community/Club until it has sufficient content and a clear reason to return. |
| **High** | Desktop exports place small content islands in very large black canvases. The application looks underbuilt and makes important data hard to read. | Use a responsive 12-column desktop grid with a 1120-1280 px content width. Increase body text and card density moderately; use whitespace to group content, not to expose empty canvas. |
| **Medium** | Labels vary between `Training`, `Workout Program`, `Club`, `Friends Feed`, `Coach`, and AI Fitness Coach. | Establish a product vocabulary and use one label per concept. Recommended: Today, Plan, Coach, Progress, Community. Inside Plan: Training, Nutrition, Recovery. |

### 3.3 Home / Dashboard

#### What works

- Calories, weight, BMR/TDEE, and streak information are easy to identify.
- The greeting gives the experience a personal tone.
- The layout distinguishes daily metrics from a longer-term weight chart.

#### Findings

| Severity | Finding and user impact | Recommended fix |
|---|---|---|
| **High** | There is no obvious primary action. Calories, activity controls, weight input, tip, BMR/TDEE, and XP compete at similar emphasis. | Make the first card “Today's plan” with one next action: start workout, log first meal, connect wearable, or recover. Move raw metrics below it. |
| **High** | `0 consumed` and `2728 remaining` can look like perfect adherence rather than “nothing logged.” | Distinguish “not logged” from zero. Use an incomplete-state label and a clear “Log first meal” action. Do not calculate reassuring progress from missing data. |
| **High** | Activity intensity buttons plus a step-count field create unclear manual tracking and possible double counting with wearables. | Ask for one input method. If a wearable is connected, show its sync status and hide manual estimates. Otherwise offer a single “Log activity” flow with duration and intensity. |
| **Medium** | Weight entry is permanently exposed and consumes high-value dashboard space. | Show the latest weight and trend; open weight entry from a labeled “Update weight” action or scheduled check-in card. |
| **Medium** | “Tip of the Day” receives a full side panel but does not advance the user's plan. | Replace it with “Why today's plan changed” or a specific coach insight tied to the user's data. Move generic tips to a secondary feed. |
| **Medium** | BMR, TDEE, target, XP, rank, multiplier, and streak add cognitive load without explaining their relevance. | Keep calorie target and streak visible; move BMR/TDEE into goal details and XP/rank into Progress or Challenges. Add plain-language definitions. |

### 3.4 Friends Feed / Club

#### What works

- The screen does state why it is empty.
- The term “training circle” suggests a privacy-aware social model.

#### Findings

| Severity | Finding and user impact | Recommended fix |
|---|---|---|
| **High** | The primary destination is almost entirely blank and provides no action. It makes the product feel abandoned. | Hide Community from primary navigation for beta unless there is seeded content. If retained, include “Invite a friend,” “Find people,” and a sample/privacy explanation. |
| **High** | `Friends Feed`, `Club`, and `Pump Check` describe the same area with three different mental models. | Rename the area `Community`. Use `Workout updates` as a content type. Avoid internal jargon such as Pump Check until users learn it. |
| **Medium** | The empty state depends on users completing a workout or adding friends but provides neither route. | Add two explicit buttons and explain audience/privacy before the first post. |
| **Medium** | Social posting appears premature for a 55-day beta when core tracking is unfinished. | Demote it to an invite-only experiment or postpone it. Do not spend beta-critical capacity polishing a feed without content density. |

### 3.5 Nutrition

#### What works

- Calories and macros are grouped together.
- Quick-add meal templates can reduce logging effort.
- History and water concepts are present.
- The manual free-text input aligns with the AI-assisted product promise.

#### Findings

| Severity | Finding and user impact | Recommended fix |
|---|---|---|
| **Critical** | The current food-search dropdown overlays meal sections and sits above floating actions and navigation. Search results do not expose a complete quantity, unit, serving, and confirmation sequence. Users can select the wrong food or log an incorrect amount. | Make search a dedicated full-height sheet. Flow: query -> result -> serving unit and quantity -> macro preview -> `Add to Lunch`. Keep one scroll container and a persistent labeled confirmation button. |
| **Critical** | The mobile History tab row is visibly clipped at the left edge, and floating controls obscure history content. | Replace the five top tabs with three: Today, Plan, History. Move Water into Today and Diary into Today. Use a scroll-safe segmented control only if all labels remain fully visible. |
| **High** | Manual logging—the core task—is below macro summary, empty state, and five quick-add rows. | Put `Log food` at the top of Today. Offer Scan barcode, Scan menu, Search food, and Describe meal as options in one action sheet. |
| **High** | The empty state says to use “the form below,” forcing users to search down the page. | Place the CTA inside the empty state and move focus/scroll directly to the logging sheet. |
| **High** | Emoji are used as structural meal icons. Their rendering is inconsistent and they are weak accessibility anchors. | Use one SVG icon set with consistent size and stroke. Pair every icon with a text label. |
| **High** | Water is both a tab and a quick-add item, creating duplicate architecture. | Keep hydration as a card on Today with one-tap increments and history inside the card detail. |
| **Medium** | The interface mixes English labels with Turkish meal content and date formats. | Apply one locale to labels, food names, decimal units, and dates. If bilingual support is intended, provide an explicit language setting. |
| **Medium** | Zero-value macro bars and a large empty section dominate before the user logs anything. | Use a guided first-log state; reveal detailed macro charts after data exists. |
| **Medium** | Quick-add `Plan A` meals expose calories/macros but not portion, ingredients, or whether adding will duplicate a prior entry. | Show a concise meal preview and confirmation. After logging, change the action to `Logged` with Undo. |

### 3.6 Workout Program

#### What works

- The full week is visible on desktop.
- Workout/rest-day distinction exists.
- Weekly calories and minutes provide useful summary context.
- Exercise previews make each training day more concrete.

#### Findings

| Severity | Finding and user impact | Recommended fix |
|---|---|---|
| **Critical** | The screen shows `TODAY: OFF DAY` while also saying “You completed today's workout,” and exposes `+{xp} XP! (photo +{bonus})`. This is broken production state and destroys trust. | Model day state explicitly: upcoming, active, completed, skipped, rest. Render copy only from validated state and never expose interpolation placeholders. Add automated UI tests for every state. |
| **Critical** | A seven-column weekly grid produces tiny cards and is not mobile-safe. | On mobile, show Today first and a horizontal day selector or vertical week list. Open a day into a focused workout detail. Keep the full grid only at desktop widths. |
| **High** | The screen lacks a dominant `Start workout` / `View recovery plan` action. | Give each day one state-dependent CTA. On rest days, offer recovery guidance or next-session preview. |
| **High** | Program score `8.3 / 10 - Excellent Program` is unexplained and may look arbitrary or falsely authoritative. | Rename it `Plan fit` only if the scoring basis can be explained. Add “Based on equipment, schedule, goal, and recovery” with a details link; otherwise remove it. |
| **High** | `Reset Program / Create New Plan` combines a destructive action with a constructive action and leaves it exposed. | Separate `Edit plan` from `Delete/reset plan`. Put reset in plan settings, require confirmation, and explain what data is preserved. |
| **Medium** | Exercise and day labels mix Turkish and English. | Localize the complete program, including exercise names, duration units, status, and dates. |
| **Medium** | The program summary emphasizes estimated calories, which can imply false precision. | Emphasize planned sessions, completed sets, volume, and progression. Label calorie estimates clearly and demote them. |

### 3.7 AI Fitness Coach

#### What works

- The coach asks a relevant dietary follow-up instead of immediately guessing.
- The input remains reachable while reading the conversation.
- QR/menu scanning is connected conceptually to the coach.

#### Findings

| Severity | Finding and user impact | Recommended fix |
|---|---|---|
| **Critical** | Assistant output exposes raw Markdown (`###`) and reads like an unformatted text dump. This makes the AI feel unfinished. | Render a safe Markdown subset into headings, lists, links, and cards. Strip unsupported syntax and test long answers, citations, tables, and error responses. |
| **High** | Coach is presented as a large overlay while the underlying page and bottom navigation remain visible. The hierarchy is ambiguous and the conversation competes with global actions. | Make Coach a full primary destination on mobile. If opened contextually, use a true full-screen sheet with one clear close/back path and preserved conversation state. |
| **High** | The QR control is icon-only, and the assistant must explain where it is. | Label it `Scan menu` or expose an accessible tooltip/action sheet. Maintain a minimum 44 x 44 px target. |
| **High** | Nutrition guidance is delivered without visible data provenance, uncertainty, or health-safety boundary. | Show which profile facts informed the answer, allow correction, add a concise non-medical guidance notice, and provide escalation language for allergies or medical conditions. |
| **Medium** | Long prose is difficult to scan and pushes the input against the bottom. | Use recommendation cards with calories/protein, “Why this fits,” and follow-up chips. Collapse supporting detail. |
| **Medium** | Timestamps and secondary text are tiny and low-contrast. | Increase secondary text to at least 12-14 px with tested contrast; timestamps should be subordinate but readable. |
| **Medium** | No visible retry, stop, regeneration, or failed-message state was supplied. | Add sending, streaming, stop, retry, offline, and partial-response states before beta. |

### 3.8 Progress / History / Analytics

#### What works

- Weekly calorie history and daily macro totals support accountability.
- Weight trend and streak concepts can motivate consistent behavior.

#### Findings

| Severity | Finding and user impact | Recommended fix |
|---|---|---|
| **High** | History entries are obscured by floating controls and fixed navigation on mobile. | Add bottom content inset and remove competing floating controls. Verify the final item remains fully visible above the safe area. |
| **High** | Charts use small labels and primarily visual color encoding. Exact values and meaning are difficult to read. | Provide tap/keyboard values, text summaries, units, accessible series labels, and a table/list alternative. |
| **Medium** | The chart reports values but not the insight or action they imply. | Lead with a plain-language trend: “Average intake was 8% below target; recovery remained stable.” Link to the plan adjustment. |
| **Medium** | Date, unit, and locale presentation is inconsistent. | Use locale-aware dates and units from one formatting layer. |

### 3.9 Menu Scan and Wearables

These were available only as concept mockups, so current behavior cannot be verified.

| Severity | Finding and user impact | Recommended fix |
|---|---|---|
| **High** | Treating Menu Scan as an independent feature risks navigation sprawl. | Position it as an entry method inside Nutrition's `Log food` flow. Return ranked choices, confidence, portion controls, and a clear add action. |
| **High** | Wearables appear as their own destination in concept material, although users mainly care about the resulting guidance. | Put connection management under Profile -> Connections. Surface sync status and resulting readiness/plan changes on Today. |
| **Medium** | Recommendation mockups present precise calories and ranking without showing source or confidence. | Show restaurant/menu source, last scan time, detected serving assumptions, and an edit/report path. |

### 3.10 Authentication, onboarding, profile, and settings

No reliable current screens were supplied. These areas cannot be declared beta-ready.

They must be validated before launch against this minimum:

- Email/password and recovery flows have clear errors, password visibility, autofill, and rate-limit feedback.
- Onboarding explains why each health datum is needed.
- Goal, units, dietary restrictions, injuries, equipment, schedule, experience, and consent can be reviewed and edited.
- Wearable connection is optional and skippable.
- The user sees a plan preview before completion.
- Progress is saved between steps; back navigation does not lose input.
- Health-data permissions, privacy, AI limitations, account export, and account deletion are findable.
- Destructive actions require confirmation and describe consequences.

Recommended onboarding sequence:

1. Value and expectations.
2. Account creation.
3. Goal and measurement units.
4. Dietary restrictions and health/safety constraints.
5. Training experience, equipment, and weekly availability.
6. Optional wearable connection.
7. Review inputs and preview the first adaptive plan.

Do not ask every possible question upfront. Gather the minimum needed for the first useful plan and progressively request the rest.

## 4. Navigation and feature architecture review

### Current architecture problem

The present shell appears to prioritize Home, Training, Nutrition, and Club, with Coach and logging as floating actions. This architecture says “four separate trackers plus utilities.” It does not say “one adaptive health system.”

### Beta recommendation

#### Primary mobile navigation

1. **Today** - readiness, next action, current targets, sync status.
2. **Plan** - Training, Nutrition, and Recovery as clear sub-sections.
3. **Coach** - persistent AI conversation and contextual recommendations.
4. **Progress** - trends, check-ins, achievements, adherence.

Add **Community** as a fifth item only after it has sufficient content, privacy controls, and a reason to return. Until then, expose it from a secondary menu or controlled invitation.

#### Secondary navigation

The avatar opens:

- Profile
- Goals and preferences
- Connections / wearables
- Notifications
- Privacy and data
- Help and safety
- Subscription
- Sign out

#### Contextual actions

- `Log` belongs on Today and Nutrition, not as an unlabeled global plus.
- `Start workout` belongs on Today and the selected training day.
- `Scan menu` and `Scan barcode` belong inside `Log food`.
- `Update weight` belongs in the check-in card and Progress.
- `Reset plan` belongs in Plan settings, separated from `Edit plan`.

### Feature decisions

| Feature | Decision | Reason |
|---|---|---|
| AI Coach | **Promote** to primary navigation | It is the clearest product differentiator and supports every domain. |
| Training + Nutrition | **Merge structurally** under Plan, retain clear sub-sections | They should influence each other without becoming undiscoverable. |
| Recovery | **Promote within Plan and Today** | The landing page promises it, but current app surfaces underrepresent it. |
| Community / Club / Feed | **Rename and demote for beta** | Empty social space harms trust; terminology is inconsistent. |
| Pump Check | **Rename** to Workout update | Internal jargon adds friction before the social model is established. |
| Water | **Merge** into Nutrition Today | It does not justify a top-level tab. |
| Diary | **Merge** into Nutrition Today | “Today” should be the diary; duplicate labels confuse users. |
| Menu Scan | **Move** into Log food | It is an input method, not a destination. |
| Wearables | **Move** to Connections; surface results on Today | Users care about adaptation, not integration management. |
| XP, rank, quests | **Demote** to Progress/Challenges | Gamification should reinforce core behavior, not compete with it. |
| Supplement cabinet | **Postpone or hide** | High scope and health-risk overhead; not required to prove beta value. |
| Tip of the Day | **Replace** with personalized plan rationale | Generic content weakens the adaptive positioning. |

## 5. Mobile responsiveness review

### Beta-blocking patterns

- Fixed bottom navigation and floating actions overlap content.
- Nutrition tabs clip rather than adapt.
- Weekly workout cards are designed as a desktop grid, not a mobile workflow.
- Food-search results create a nested, covering scroll surface.
- Long coach output fills an overlay while underlying navigation remains active.
- The local 375 px landing render showed clipping and an oversized consent experience; this must be confirmed on real devices.

### Required responsive standards

- Test widths: 320, 360, 375, 390, 430, 768, 1024, and 1440 px.
- Test iOS Safari and Android Chrome with browser chrome visible.
- No horizontal page scroll at any supported width.
- Minimum 44 x 44 px touch targets with at least 8 px separation.
- Body and form text at least 16 px on mobile.
- Fixed navigation must reserve content space and respect safe-area insets.
- Use `100dvh` behavior for full-screen sheets; account for virtual keyboard resizing.
- Only one main vertical scroll container per screen or sheet.
- Preserve scroll, filters, and form state when navigating back.
- Test 200% text size, landscape orientation, reduced motion, and slow network states.

### Standard responsive patterns

| Content | Mobile pattern | Desktop pattern |
|---|---|---|
| Weekly plan | Today card + day selector/list | Seven-day grid |
| Nutrition logging | Full-screen sheet | Side sheet or centered dialog |
| Charts | Summary + simplified chart + details | Full chart with comparison controls |
| Navigation | Four-item bottom bar | Persistent left rail or compact top/side navigation |
| Coach | Full destination | Persistent panel or destination |
| Tabs | Maximum three visible segments | Full tab row |
| Dense records | Stacked cards | Table/grid where useful |

## 6. UI consistency review

### Design-system maturity

The current artifacts show at least three visual directions: live black/electric blue, legacy black/neon green, and navy/cobalt concept cards. Pick one system now. The strongest basis is the current black/electric-blue brand, improved with clearer surfaces and more readable typography.

### Recommended tokens and rules

- **Color:** one blue primary action; green only for success/connected states; amber for warnings; red for destructive/error states.
- **Surfaces:** three dark levels with visible 3:1 non-text boundary contrast where controls require it. Do not rely on near-black borders on black backgrounds.
- **Typography:** condensed display face only for major athletic headings; a high-legibility sans-serif for body, forms, tables, and AI responses.
- **Type scale:** 12 metadata, 14 secondary, 16 body/input, 18 card title, 24 section title, 32+ page title. Avoid sub-12 px operational text.
- **Spacing:** 4/8 px base; use 16 px card padding on mobile and 20-24 px on larger surfaces.
- **Radius:** define small, medium, and sheet/card radii; remove arbitrary variations.
- **Icons:** one SVG family, consistent 1.5-2 px stroke. No emoji as structural icons.
- **Buttons:** one primary per view, then secondary, tertiary, and danger. Do not switch the primary action from blue to green by screen.
- **Motion:** 150-300 ms, meaningful, interruptible, and disabled/reduced when requested.
- **Focus:** visible 2-4 px focus ring with sufficient contrast on every interactive element.

### Trust details

- Show last sync time and source for wearable-derived values.
- Explain why a plan changed.
- Mark calorie and recovery estimates as estimates.
- Never expose template tokens, raw Markdown, or contradictory state copy.
- Keep AI advice editable/correctable and show which user facts informed it.
- Clearly separate health guidance from medical advice.

## 7. Beta launch priorities

### Must fix before beta

1. Remove all mobile overlap, clipping, and horizontal overflow.
2. Replace the current navigation hierarchy; promote Coach and demote empty Community.
3. Rebuild food logging as a complete, unambiguous mobile flow.
4. Fix workout state logic and remove all unresolved placeholders.
5. Make Today action-oriented with one next-best action.
6. Add designed loading, empty, offline, retry, validation, success, and permission-denied states to every core flow.
7. Complete accessibility baseline: contrast, focus, semantics, labels, touch targets, text scaling, reduced motion.
8. Unify tokens, buttons, typography, icons, and localization.
9. Validate authentication, onboarding, privacy, account recovery, and destructive settings.
10. Qualify integration/AI claims and expose data provenance/sync status.
11. Test the responsive matrix on real iOS and Android devices.
12. Add UI-state tests for nutrition logging, workout states, navigation state preservation, and AI rendering.

### Should fix before beta if possible

- Simplify the landing page and reduce the mobile consent footprint.
- Move BMR/TDEE and gamification out of the dashboard's primary hierarchy.
- Add plan-rationale explanations and progress insights.
- Improve chart summaries and accessible alternatives.
- Provide coach quick actions, retry, and structured response cards.
- Add Undo to logging and deletion actions.
- Improve first-use states for nutrition, training, and progress.
- Add connection status and manual/wearable conflict handling.

### Can wait until after beta

- Public or broad Community feed.
- Rich quests, ranks, XP multipliers, and social challenges.
- Supplement cabinet.
- Advanced chart export and deep analytics.
- Decorative motion, complex micro-interactions, and full light-theme expansion, provided the dark theme is accessible.
- Extensive menu-scan ranking polish beyond a reliable core scan/edit/log flow.

## 8. Recommended frontend direction

AxisAI should become **simpler, more guided, workflow-centric, and quietly premium**.

It should not become a denser analytics dashboard. The product can retain rich data, but data should support a decision rather than become the destination.

### Direction principles

1. **Today before totals.** Lead with the next action and the reason behind it.
2. **One adaptive plan.** Training, nutrition, and recovery should visibly affect one another.
3. **Coach as a product layer.** Coach is not a chat bubble; it is a primary way to understand and modify the plan.
4. **Progressive disclosure.** Show the useful conclusion first, then allow users to inspect BMR, TDEE, raw macros, charts, and model rationale.
5. **Functional premium.** Use high contrast, disciplined spacing, crisp typography, restrained motion, and credible feedback. Avoid decorative complexity.
6. **Mobile first.** Design the actual phone workflow first; let desktop reveal more context, not a different product.
7. **Trust through explanation.** Every automated adjustment should answer “what changed, why, and what can I do about it?”

The UI/UX benchmark reinforced a single-primary-action, mobile-first direction with strong contrast, 44 px touch targets, visible feedback, and controlled navigation. AxisAI should keep its existing electric-blue identity rather than adopt a generic new accent; the larger need is consistency and legibility, not rebranding.

## 9. Actionable implementation backlog

### Beta-critical fixes

| What to change | Why it matters | Severity | Implementation notes |
|---|---|---|---|
| Implement explicit workout day-state model | Prevents contradictory copy and broken placeholders | **Critical** | Enumerate upcoming/active/completed/skipped/rest; snapshot-test every state and interpolation. |
| Rebuild food add flow | Logging accuracy is core product value | **Critical** | Dedicated sheet; serving/unit/quantity; macro preview; confirmation; success + Undo. |
| Remove content/FAB/nav collisions | Current mobile content is obstructed | **Critical** | One contextual action, safe-area insets, bottom padding, z-index tokens. |
| Make weekly plan responsive | Seven tiny columns are unusable on phones | **Critical** | Day selector/list below 768 px; desktop grid above. |
| Fix or disprove landing overflow | First-visit acquisition may be unusable | **Critical** | Real-device test at 320-430 px; zero horizontal scroll; compact consent. |
| Render AI responses safely | Raw Markdown makes core AI look broken | **Critical** | Sanitized renderer, structured cards, long-content tests, error fallback. |

### Navigation changes

| What to change | Why it matters | Severity | Implementation notes |
|---|---|---|---|
| Adopt Today / Plan / Coach / Progress | Aligns IA to user intent and product differentiation | **High** | Preserve route state; desktop adapts to rail; add Community later. |
| Remove mobile hamburger duplication | Reduces competing navigation systems | **High** | Avatar owns account/settings; secondary destinations live there. |
| Demote Club/Feed | Empty primary destination damages trust | **High** | Hide behind invitation or secondary menu until seeded. |
| Consolidate Nutrition tabs | Current row clips and duplicates concepts | **High** | Today / Plan / History; Water inside Today. |
| Move Menu Scan and barcode into Log | Input methods should not become destinations | **Medium** | Action sheet from Nutrition and Today. |

### Layout changes

| What to change | Why it matters | Severity | Implementation notes |
|---|---|---|---|
| Redesign Today around next action | Removes dashboard indecision | **High** | First card has state-dependent CTA and rationale. |
| Demote weight entry, tip, BMR/TDEE, XP | Reduces competing hierarchy | **Medium** | Place in check-in, details, or Progress. |
| Use responsive desktop grid | Eliminates small islands in empty canvas | **Medium** | 1120-1280 px content width; 12-column grid; readable card sizes. |
| Convert Coach overlay to destination | Clarifies navigation and protects conversation space | **High** | Full screen mobile; optional panel desktop. |

### Responsive fixes

| What to change | Why it matters | Severity | Implementation notes |
|---|---|---|---|
| Establish breakpoint contract | Current patterns adapt inconsistently | **High** | 375/768/1024/1440 plus 320 safety; document per component. |
| Standardize safe-area and fixed-bar spacing | Prevents hidden content and mis-taps | **Critical** | Shared shell tokens using safe-area insets and measured nav height. |
| Remove nested scrolling in search/chat | Competing scroll regions cause loss of context | **High** | One scroll owner; keyboard-aware sheet height. |
| Test dynamic text and landscape | Fitness data layouts are vulnerable to clipping | **High** | 200% text; largest iOS text; landscape phone; wrap before truncate. |

### Visual polish

| What to change | Why it matters | Severity | Implementation notes |
|---|---|---|---|
| Consolidate color and surface tokens | Mixed green/blue states look inconsistent | **High** | Blue primary; semantic success/warn/danger; test both text and controls. |
| Replace emoji icons | Improves consistency and accessibility | **Medium** | One SVG family; label all actions. |
| Increase body/metadata legibility | Current tiny gray text is hard to read | **High** | 16 px body/input; 12-14 px metadata; WCAG AA contrast. |
| Standardize card, border, radius, shadow | Product should feel like one system | **Medium** | Three surface levels and three radius tokens maximum. |

### UX flow improvements

| What to change | Why it matters | Severity | Implementation notes |
|---|---|---|---|
| Differentiate missing data from zero | Prevents false success and bad recommendations | **High** | `Not logged` states; onboarding CTA; suppress derived progress. |
| Add state-based empty screens | Blank screens make the beta feel abandoned | **High** | Explain value, next step, and privacy; include CTA. |
| Explain automated changes | Builds trust in adaptive plans | **High** | “Changed because…” card with data source and user override. |
| Add Undo and recovery | Reduces fear and accidental data loss | **High** | Undo toast for logs/deletes; drafts for long inputs; retry for network errors. |
| Localize labels and units consistently | Mixed language undermines polish and comprehension | **Medium** | Central locale/date/unit formatting; translate exercise and food names. |

### Accessibility improvements

| What to change | Why it matters | Severity | Implementation notes |
|---|---|---|---|
| Enforce 44 x 44 px controls | Small plus, close, and icon actions are hard to tap | **High** | Expand hit area; maintain 8 px separation. |
| Add labels/roles/states | Icon-only and custom controls may be silent | **High** | Accessible names; selected/expanded/disabled semantics; logical focus order. |
| Verify contrast and focus | Dark gray-on-black UI risks exclusion | **High** | Automated scan plus manual check; visible 2-4 px focus ring. |
| Add chart text alternatives | Color/shape alone is insufficient | **Medium** | Plain-language summary, data list/table, keyboard/tap values. |
| Support reduced motion and text scaling | Required for usable responsive UI | **High** | Disable decorative motion; no clipped text at 200%. |

## 10. Ideal information architecture from scratch

### Product model

AxisAI should be designed as a daily decision engine, not a collection of trackers.

```text
AxisAI
├── Today
│   ├── Readiness and sync status
│   ├── Today's plan
│   ├── Next best action
│   ├── Quick log
│   └── Why the plan changed
├── Plan
│   ├── Training
│   │   ├── Today / week
│   │   ├── Workout detail
│   │   └── Plan settings
│   ├── Nutrition
│   │   ├── Today
│   │   ├── Meal plan
│   │   ├── Log food / barcode / menu scan
│   │   └── History
│   └── Recovery
│       ├── Sleep and readiness
│       ├── Recovery actions
│       └── Adaptation history
├── Coach
│   ├── Conversation
│   ├── Suggested questions
│   ├── Recommendation cards
│   └── Sources, assumptions, and corrections
├── Progress
│   ├── Overview
│   ├── Body and weight
│   ├── Training
│   ├── Nutrition adherence
│   ├── Recovery
│   └── Check-ins and achievements
├── Community (post-beta or controlled beta)
│   ├── Activity
│   ├── Friends / circles
│   ├── Challenges
│   └── Privacy controls
└── Account (avatar)
    ├── Profile and goals
    ├── Preferences and units
    ├── Connections / wearables
    ├── Notifications
    ├── Privacy and data
    ├── Help and safety
    └── Subscription / sign out
```

### Navigation behavior

**Mobile beta:** four labeled bottom items: Today, Plan, Coach, Progress. The avatar opens Account. A labeled `Log` action appears contextually on Today and Nutrition; it is not a permanent unlabeled floating button.

**Mobile growth state:** add Community as the fifth item only when it is active and useful.

**Desktop:** use a compact left rail with the same hierarchy and labels. Keep Today/Plan/Coach/Progress order identical across breakpoints. Do not introduce a second hierarchy merely because more space exists.

**Deep pages:** preserve persistent primary navigation, add a clear title/back path, and restore prior scroll/filter/input state.

### Ideal first-run journey

```text
Landing -> Account -> Minimum profile -> Optional wearable -> First plan preview
        -> Today -> Log or start action -> Coach explanation -> Progress feedback
```

This sequence proves the full AxisAI promise quickly. It avoids sending a new user into a blank dashboard or empty community before the system has useful data.

## Final verdict

AxisAI should spend the next 55 days becoming a coherent daily coach, not a broader feature platform. The beta can tolerate limited analytics, dark-mode-only presentation, and postponed social features. It cannot tolerate obstructed mobile content, ambiguous food logging, contradictory workout state, raw AI formatting, or unclear trust boundaries.

If the team fixes the critical flows, promotes Coach, restructures Today and Plan, and hides premature features, the product can reach a credible focused beta. If it attempts to polish every current surface without changing the architecture, the beta will still feel fragmented and unfinished.
