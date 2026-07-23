# AxisAI Frontend Readiness Sprint 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` or `superpowers:executing-plans` and
> execute tasks in order with red-green test cycles and phase commits.

**Goal:** Produce a deterministic, evidence-backed frontend audit and reusable
visual-QA harness without redesigning AxisAI.

**Architecture:** A local-only Flask wrapper and Python Playwright runner consume
versioned inventory/scenario schemas. Browser work runs with the app and SQLite
fixture database in one supported environment.

**Tech stack:** Flask/Jinja, SQLite, Python 3, Playwright 1.61.0, JSON Schema
Draft 2020-12, pytest.

## Constraints

- Preserve Flask/Jinja/vanilla-JS/plain-CSS architecture and production behavior.
- Do not redesign, restructure routes, change feature exposure, or push remotely.
- Chromium in a supported environment is mandatory for completion.
- Preserve the source audit unchanged with checksum provenance.
- Version every machine-readable artifact as `1.0.0`.

## Ordered tasks

1. Freeze dependencies, diagnostics, warnings, provenance, design, and plan.
2. Add strict schemas and verifier contracts.
3. Add deterministic audit clocks and dated scenarios.
4. Generate inventory, static maps, and coverage tiers.
5. Build the hermetic audit app and seed scenarios.
6. Implement CLI tiers, capture planning, checks, and result recording.
7. Validate Docker/Linux Chromium and run preflight.
8. Extract findings and execute inventory-driven verification.
9. Write all nine reports and commit curated evidence.
10. Run final gates and append Sprint 0 to `docs/handoff.md`.

Each task ends with focused tests, regression tests, diff review, and a small
commit before the next task begins.
