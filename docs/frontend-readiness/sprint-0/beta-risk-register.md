# Beta Risk Register

| Risk | Canonical findings | Likelihood / impact | Current control | Exit criterion |
|---|---|---|---|---|
| Mobile obstruction or overflow | EXT-001, EXT-002, EXT-007, EXT-023, EXT-032, EXT-045 | High / critical | full responsive inventory + stress tiers | zero unexpected horizontal overflow; final content unobscured |
| Incorrect food logging | EXT-022, EXT-024, EXT-025, EXT-030 | High / critical | deterministic empty/active scenarios | serving/unit/quantity/preview/confirm/Undo verified |
| Contradictory workout state | EXT-031, EXT-033, EXT-035 | High / critical | five explicit scenario states and fixed clock | every state renders coherent action/copy; no tokens |
| Untrusted Coach output | EXT-038–EXT-044 | High / critical | coach history/error state inventory | sanitized structured output, provenance, retry/stop verified |
| Fragmented navigation | EXT-008, EXT-009, EXT-011, EXT-019 | High / high | architecture map and feature-flag assessment | product decision plus one consistent hierarchy |
| Auth/onboarding incompleteness | EXT-052–EXT-059 | Medium / critical | full widths, keyboard/text stress | all readiness requirements independently verified |
| Inaccessible data presentation | EXT-043, EXT-046, EXT-047 | High / high | text/reduced-motion and chart states | readable contrast, keyboard/tap values, text alternative |
| Unqualified health/integration claims | EXT-003, EXT-041, EXT-051 | Medium / critical | source mapping | availability, source, uncertainty, and safety language approved |
| Evidence false confidence | all runtime findings | High / critical | supported-environment gate and schema validation | full supported Chromium manifest validates |
| Warning drift hides regressions | warning baseline | Medium / medium | exact command/environment freeze | no new category; material count increase explained |

Runtime likelihood assessments are provisional because supported browser evidence is blocked. Product decisions are not converted into bugs merely because the external audit recommends a direction.
