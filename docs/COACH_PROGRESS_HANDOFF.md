# Progress to Coach continuation

## Transport and authority

The CTA remains `/coach?review=progress-insight`. It contains a constant kind,
never an owner id, evidence, recommendation, or Progress snapshot.

Previously, the page derived Axis Insight and interpolated its interpretation,
meaning and action into the composer. The question-only send contract made that
generated paragraph appear as user speech in chat history and memory.

The page now derives a compact preview and localized editable intent:
`Help me plan this week.` / `Bu hafta nasıl ilerleyeyim?`. Rendering never sends
the draft. The semantic aside identifies Progress as the source and offers a
keyboard-accessible dismiss button. Initial render leaves focus alone; dismissal
returns focus to the composer. No live region is added.

While the preview remains active, the first request carries only
`handoff: "progress-insight"`. Both Coach routes allowlist that exact value and
pass the authenticated owner id separately. Unknown and malformed markers are
ignored; client facts and ids never determine canonical context.

At send time, the server rebuilds the owner's current `build_progress_insights`
read model. This handles state changes since navigation. It projects public
interpretation, meaning, action, bounded evidence and the exact canonical volume
adjustment into model context. Existing Python copy tables are reused; the volume
formatter is shared with #356. No new decision mapping or planning authority is
introduced. The existing canonical guidance header and response grounding remain.

A successful handoff projection replaces the ordinary Coach plan projection in
that turn: one canonical build, no second report. If projection fails, rollback
restores database usability and normal Coach context continues. No raw fallback
or fabricated Progress facts are supplied. A failed page derivation renders
normal Coach with a usable composer.

## History boundary

The question stays separate throughout routes, pipeline and provider messages.
`record_turn` receives the user's actual submitted text and assistant response.
Hidden context and the marker are not stored as user speech, browser history,
rolling memory, summaries or a new handoff record. Existing memory, model/tool
loops and deferred summarization behavior are preserved.

## Lifecycle

| Event | Result |
| --- | --- |
| Constant CTA / initial refresh | Fresh server preview, short unsent draft |
| Edit or replace draft | Visible preview stays active; current context accompanies first send |
| Delete draft | Empty input sends nothing; preview can be dismissed |
| Dismiss | Preview and request/retry markers clear; URL becomes `/coach` |
| First successful reply | Preview and markers clear; URL becomes `/coach` |
| Stream error, blocking fallback error, stop or incomplete stream | Marker retained for explicit retry |
| Retry before success | Same user request, fresh server facts, existing retry path |
| Regenerate after success or dismissal | Normal Coach context; no hidden stale marker |
| Second message / normal `/coach` visit | No handoff metadata |
| Navigate away and return via constant CTA | Explicit new continuation; fresh derivation |

The visible preview determines whether context is active, including when wording
is replaced with an unrelated question. Dismiss it to continue without Progress
context. No intent classifier or persistent handoff store is added. A browser
restoring an unsent page may restore its preview; send still rederives facts.

## Performance and validation

Page render has one insight build before and after. A successful first turn has
one canonical plan/insight build before and after. Normal Coach never builds a
handoff. There is no summarization request for the handoff, new dependency,
polling, browser Progress API fetch or additional response-generation call.
Existing tool rounds and memory summarization are unchanged.

Synthetic public-copy measurements (characters, not tokens; unrelated prompt
sections excluded) reduce EN drafts from 258–311 to 23, and TR from 264–314 to
27 across baseline, progression and deload examples. Canonical evidence remains
bounded to the existing read-model contract. Hidden context replaces duplicated
guidance and removes the large user paragraph; it is not retained in storage.

Focused tests cover both route allowlists, owner derivation, send freshness,
exact planner adjustment, one build, provider invocation count, exact stored user
text, failure degradation and existing #356 grounding. Hermetic Chromium covers
EN/TR at 320, 390, 430, 768, 1024 and 1366 pixels, no horizontal overflow, preview
height below 150px, edited/unchanged send, stream and blocking fallback retry,
dismissal, consumption and normal entry. Physical mobile keyboard behavior is
not emulated by desktop Chromium. The repository's full Linux CI remains the
authoritative broader gate; existing Windows `node -e` tests exceed the platform
command-length limit.

## Scope

No AdaptivePlan decisions, Progress calculations or semantic copy, Coach-wide
design/style, Weekly Check-in Feedback, planning tools, or nutrition flows change.
No concrete Weekly Check-in follow-up defect was established in this work.
