# Pump Check response quality (PUMP-CHECK-RESPONSE-QUALITY-01)

## Problem

Users received generic Pump Check answers about lighting, photo quality and
missing information, and often no meaningful feedback about the physique that
was visible.

## Root cause (source trace)

Path: `POST /api/v1/pump-checks` → `service.create_or_replay` →
`analysis.analyze_image` (`prepare_image_for_vision`, 1024 px) →
`ai._bedrock_validate_image` (one Sonnet vision call, `max_tokens=600`) →
`parse_analysis` (strict v1 schema + safety validator) → persisted
`PumpCheck.analysis` → mobile `PumpCheckAnalysisApiDto` → `PumpCheckApiMapper` →
`PumpCheckAnalysisView` (renders every field as written; empty lists are hidden).

| Stage | Generic text from here? |
|---|---|
| Prompt (`build_prompt`) | **Yes, this is the cause.** It never defined `sufficient`/`limited`/`insufficient`. Its only quality instruction was "State limitations when the image is unreliable", and it gave no positive instruction about what to observe. It did not define what each field is for either, so `focus_areas`, `limitations` and `next_check_guidance` all filled with photo tips and single-photo disclaimers. The repo's own fixtures show this shape (`"Keep lighting even."` as a focus area, `"One image cannot establish change over time."` as a limitation). |
| Quality thresholds | None in the backend. The model chooses `quality`, so its threshold is whatever the prompt implies. |
| Parser / validator | Passes content through unchanged; empty lists were always legal. It does refuse "this shows" / "it suggests" phrasing (`_DIAGNOSTIC_ASSERTION_RE`). That fails the whole analysis (`invalid_output`), so the user sees nothing. The fix steers the model away from this phrasing; the validator is unchanged. |
| Fallback | None. A failure is the `failed` status, never canned analysis text. |
| Mobile rendering | Verbatim. The only fixed copy is the quality callout. For limited and insufficient results, limitations are shown before observations by design; left unchanged. |

## Change

Prompt only (`PROMPT_BODY` in `app/services/mobile_pump_checks/analysis.py`):

- It defines the three quality states by what is visible:
  - `limited` still describes what is visible and names exactly what cannot be judged.
  - `insufficient` returns empty observation, strength and focus lists, names the obstacle, and gives one retake instruction.
  - Dim gym light, slight blur or a mirror selfie is not insufficient on its own.
- One role per field. `focus_areas` are visible body areas, not photo tips; `limitations` are specific, with no general disclaimers; the same point is never repeated in two fields; an empty list beats an invented item.
- Focus on the tagged `body_region`.
- Natural, direct second-person language (informal `sen` in Turkish), with no motivational filler or negative body judgments.
- It recommends direct "is visible" wording over "this shows/suggests", which the validator refuses.
- Every existing safety rule and the untrusted-context fence stay word for word. The body is language-neutral; only `content_language_instruction` differs between EN and TR.

Unchanged: schema `pump-check-analysis/v1`, quality tokens, validator, the
single call, `max_tokens=600`, image preparation, model routing, consent,
allowance, storage, comparison and mobile code. Cost: roughly 450 more input
tokens per call (prompt about 1.5k → 3.5k characters), well inside the
`pump_check` physical reservation (12,000 input tokens per attempt).

## Evidence

- Unit: `tests/test_pump_check_analysis.py` pins the prompt contract and shows
  that specific EN/TR sufficient, limited and insufficient answers (including
  empty lists) pass the unchanged validator. This is **not** quality evidence.
- Mobile: those answer shapes rendered through the shipped DTO, mapper and
  `PumpCheckAnalysisView`, with no mobile change.
- Live quality A/B, main prompt against this one: 8 cases × EN/TR, 32 bounded
  Sonnet calls on staging. Its harness is outside the repo
  (`~/axisai-pump-check-response-quality-01/harness`) and needs an authorized
  staging window plus authorized base photos. Quality is not claimed until it has run.

## Residual risk

The safety validator only knows English vocabulary; Turkish answers rely on the
prompt alone for body-fat, asymmetry and medical wording. The eval scorer
probes for this (`tr_claim_probe`). Making the validator language-aware is a
separate, reviewed change.
