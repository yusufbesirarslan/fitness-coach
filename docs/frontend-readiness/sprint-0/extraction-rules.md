# External Finding Extraction Rules

Version: `1.0.0`

The canonical unit is one material, actionable, independently testable external-audit claim. IDs use `EXT-###` and remain stable across later verification runs.

Include a record for each finding-table row, numbered beta-risk finding, explicit beta-readiness requirement that needs independent verification, and other distinct actionable claim. Preserve every relevant source location in `source_refs`.

Exclude headings, descriptive prose without a testable claim, “what works” observations unless deliberately verified, architecture-diagram nodes, repeated summaries of an existing finding, and general design principles. Recommendations are not separate findings when they merely propose a response to the same underlying problem.

Deduplicate by user-visible failure or decision, not by wording. When the executive summary, screen section, priority list, and backlog describe the same issue, one canonical record carries all source references. Split a row only when its claims require different evidence or could reach different verification outcomes.

Runtime-dependent claims remain `BLOCKED_BY_MISSING_ENVIRONMENT` until the mandatory supported-environment Chromium run provides evidence. Static repository evidence may support architecture findings; product-direction questions remain `PRODUCT_DECISION_REQUIRED`. No diagnostic capture from an unsupported host can confirm a finding.
