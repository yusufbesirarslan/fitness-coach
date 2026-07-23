# Implementation Backlog

No redesign is implemented in Sprint 0. Ordering below is evidence-gated; runtime items must be reprioritized after the supported full run.

## Sprint 1 entry point

1. Establish supported Chromium evidence and resolve all `BLOCKED_BY_MISSING_ENVIRONMENT` statuses.
2. Fix confirmed mobile overlap/overflow and shared safe-area/action-layer ownership first (`EXT-001`, `EXT-007`, `EXT-023`, `EXT-032`, `EXT-045`). This shared shell work has the broadest downstream effect.
3. Implement an explicit workout state model and state tests (`EXT-031`, `EXT-033`, `EXT-035`).
4. Complete food logging as one unambiguous sheet flow (`EXT-022`, `EXT-024`, `EXT-025`, `EXT-030`).
5. Harden Coach rendering, provenance, and failure states (`EXT-038`–`EXT-044`).

## Product decisions required before code

- Approve or reject Today / Plan / Coach / Progress and Community demotion (`EXT-009`, `EXT-019`, `EXT-021`).
- Define public AI/integration claim language (`EXT-003`).
- Decide Menu Scan and wearable placement (`EXT-049`, `EXT-050`).
- Decide landing compression and proof strategy (`EXT-004`, `EXT-005`).

## Platform enablers

- Introduce a typed, centralized UI flag mechanism only for approved capability decisions.
- Preserve deterministic scenario clock injection and hermetic fixtures as test infrastructure.
- Add manifest comparison only after a reviewed visual baseline exists; never auto-approve new images.
- Keep the datetime warning cleanup out of this sprint; compare only with the frozen command/environment.

## Definition of done per item

Each item needs a canonical finding ID, failing regression test where practical, implementation, focused tests, supported-browser evidence for affected inventory cells, accessibility/state checks, and updated report cross-references. Curated evidence includes only decision-useful captures; the complete raw run remains an artifact.
