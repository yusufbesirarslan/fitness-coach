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
- It recommends direct "is visible" wording and names the common constructions the validator refuses ("it/this shows|suggests|indicates|looks like", "you have", "signs of"); the validator also refuses "you may/appear to have" and "evidence of", which the prompt does not name.
- Every existing safety rule and the untrusted-context fence stay word for word. The body is shared by EN and TR (it carries the Turkish `sen` note in both); only `content_language_instruction` differs.

Unchanged: schema `pump-check-analysis/v1`, quality tokens, validator, the
single call, `max_tokens=600`, image preparation, model routing, consent,
allowance, storage, comparison and mobile code. Cost: roughly 700 more input
tokens per call (measured) (prompt about 1.4k → 3.7k characters), well inside the
`vision` physical reservation in `ai_input_budget` (12,000 input tokens per
attempt).

## Evidence

- Unit: `tests/test_pump_check_analysis.py` pins the prompt contract and shows
  that specific EN/TR sufficient, limited and insufficient answers (including
  empty lists) pass the unchanged validator. This is **not** quality evidence.
- Mobile: those answer shapes rendered through the shipped DTO, mapper and
  `PumpCheckAnalysisView`, with no mobile change.
- Live quality A/B, run 2026-11-18 on staging (Sonnet 5.5, claude55 routing, real D1 consent and spend guard). The comparison is main's prompt against this one, on identical images and settings: 8 cases × EN/TR. The images are two public-domain US-government photos plus derived variants and one synthetic image; they are not in the repo. Round 1 found two problems. Turkish answers hit the unchanged `max_tokens=600` cap (4 of 8 invalid). And a clear-photo English answer was refused by the validator for "so it shows". Both were fixed in the prompt only (length budget; naming the refused constructions) and retested.

  | Final, 16 pairs | main prompt | this prompt |
  |---|---|---|
  | Valid JSON | 15/16 (1 Turkish truncation) | 16/16 |
  | Expected quality | 11/15 (clear photos rated `limited`) | 16/16 |
  | Invented items on unusable photos | 4 of 4 answers | 0 |
  | `max_tokens` stops | 1 | 0 |
  | Median latency, EN / TR | 3.9 s / 6.2 s | 3.5 s / 5.3 s |
  | Median tokens in / out, EN | 1,504 / 351 | 2,198 / 330 |
  | Median tokens in / out, TR | 1,507 / 565 | 2,201 / 439 |

  On a manual read, this prompt gives the more useful answer in 16 of 16 pairs. The main prompt mostly reviews exercise form and asks for videos or other angles even on clear photos.

## Turkish safety validator (follow-up, PR #532)

The validator used to know only English vocabulary, so Turkish answers relied
on the prompt alone for medical, diagnostic, body-fat, asymmetry and numeric
wording. `parse_analysis` now also checks an accent-folded copy of every text
field (`_tr_folded`: NFKD, combining marks dropped, İ/I/ı → i, hyphens →
spaces, so capitals, decomposed input and text typed without Turkish letters
are covered) against four Turkish patterns:
`_TR_MEDICAL_RE`, `_TR_DIAGNOSTIC_RE`, `_TR_BODY_CLAIM_RE` and
`_TR_MEASUREMENT_RE`. A hit is refused with the same message as its English
equivalent; English checks, schema and quality tokens are unchanged. The
Pump Check comparison validator reuses the same `_plain_text`, so comparisons
get the same Turkish protection.

Everyday words next to the blocked stems stay legal ("gösteriyor", "yüzde"
as *on the face* — a percentage needs a digit, a whole tens word, or a
single-digit word that ends the clause or is followed by "-lik" or a
comparison/rate word, so "yüzde önemli", "yüzde bir gerginlik" pass —
"kırık beyaz", "belirtilen", "tanıdık", "işaret parmağın"). Known residual
(same blind spot as English): vague quantities such as "birkaç kilo"; and
"… işaretleri var" passes so that "tişörtünde bir işaret var" (a mark) is
not refused ("işareti olabilir/olarak" and "işaret ediyor" are refused). Replaying the real staging
answers: all 8 Turkish answers from this prompt pass; 2 of 7 valid Turkish
answers from the previous prompt are refused ("düşündürüyor", "asimetrik"),
which is why the validator ships only together with or after this prompt.

## Weight, judgment, physiology and comparison checks (validator only)

Probes on main after #532 showed that wording the prompt forbids could still
reach the client in both languages: weight or fat change ("birkaç kilo yağ
kaybetmişsin", "You need to lose a few kilos"), weight-loss instructions
("kilo vermelisin"), negative judgments about the body, physiological
inferences (swelling, dehydration, overtraining) and physique comparisons that
one photo cannot support ("Your arms are bigger than in your last photo").
None of the 24 real staging answers contained them; the prompt is unchanged.

`_plain_text` now also refuses, in EN (raw text, IGNORECASE) and TR
(`_tr_folded`):

| Check | EN / TR pattern | Message |
|---|---|---|
| Weight / body-fat change, weight-loss directives | `_WEIGHT_CHANGE_RE` / `_TR_WEIGHT_CHANGE_RE` | prohibited body claim |
| Negative body judgment | `_NEGATIVE_BODY_RE` / `_TR_NEGATIVE_BODY_RE` | negative body judgment |
| Medical / physiological inference | `_PHYSIOLOGICAL_RE` / `_TR_PHYSIOLOGICAL_RE` | safety policy |
| Comparison with other people or norms | `_NORM_COMPARISON_RE` / `_TR_NORM_COMPARISON_RE` | unsupported comparison |

`_single_image_text` (single-image `parse_analysis` only) also refuses a
comparison with an earlier photo: a growth/shrink verb on the body
(`_PHYSIQUE_CHANGE_RE`, `_TR_PHYSIQUE_CHANGE_RE`), or a past reference
(`_PAST_REFERENCE_RE`, `_TR_PAST_REFERENCE_RE`) together with a physique
comparative in the same item. The two-photo comparison validator reuses
`_plain_text` and so gets the first four checks; it does not get this one,
because describing an A-to-B difference is its job.

Each pattern names the claim, not the word. These stay legal and are pinned by
`EN_HARDENING_ORDINARY` / `TR_HARDENING_ORDINARY`: gym weights ("lower the
weight", "drop the weight", "fat grips", "kilo düşür", "birkaç kilo daha
ekle", "fazla kilolarla çalışma"); ordinary exercise advice ("stay hydrated",
"hips are sagging" in a plank, "aşırı yüklenme", "kalçan sarkıyor"); neutral
photo observations ("a lean, well-developed physique", "flat/soft light",
"zayıf ışık", "yağlı cilt", "su şişesi", "sisli", "gelişmiş omuz hattı",
"pumped"); and same-photo comparisons ("softer than your thighs", "diğer
bölgelere göre daha az", "sağdakinden daha belirgin"). Guidance that only
mentions an earlier photo ("same angle as your last photo", "İlk fotoğrafa
göre aynı ışığı kullan") passes, because it makes no physique comparison.

Replay of every stored staging answer (`~/axisai-pump-check-response-quality-01`,
135 answers, including the 32 final A/B pairs, and all 435 distinct text
items): no verdict changes against main.

EN checks run on `_en_normalized` (curly apostrophes → `'`, Unicode format
characters such as soft hyphens dropped); `_tr_folded` drops format
characters too. "şişlik" (swelling) folds to "sislik" (haziness), so it is
matched on a Turkish-lowercased copy that keeps the letters
(`_TR_LETTER_PHYSIOLOGICAL_RE`). The earlier-photo check needs an explicit
earlier reference ("than before", "than they were", "since your last
photo", "over the past weeks", "önceki fotoğrafa göre", "geçen seferden";
not bare "now", "recently", "zamanla", "artık") and a body comparative. A
comparative that describes the photo setup itself ("a wider frame than in
your last photo", "the light is softer than before", "kadrajı daha geniş
tut") is removed before that check (`_PHOTO_SETUP_PHRASE_RE`,
`_TR_PHOTO_SETUP_PHRASE_RE`); a setup word elsewhere in the item does not
exempt it ("bigger than in your last shot", "…; focus on the chest next" are
refused). Only an article or adverb may sit between the comparative and
the setup noun ("bigger in frame", "fuller on camera" are not setup), and in
Turkish only bare/object setup forms count ("kadrajı", "ışık"; not "ışıkta",
"bu açıdan") with no body noun in between; a purpose clause ("daha belirgin
görünsün diye") is advice. Turkish change verbs are matched as -mış forms,
finite -di forms (not questions: "büyüdü mü") and nominalized -dIk forms
("büyüdüğünü"), but not "büyüdükçe"/"büyüdüğünde". All English
checks, including the pre-existing ones, run on `_en_normalized`.

Independent review rounds 1–3 (REQUEST_CHANGES) found Turkish -di bypasses,
curly-apostrophe and soft-hyphen bypasses, and refused guidance such as
"Zamanla ilerlemeyi görmek için…", "Kademeli kilo artışı…", "Keep training to
gain muscle…", "Birçok kişi bu pozda…"; round 2 found that a setup word
anywhere in the item exempted it, "artık" and "than most other areas"
false positives, and stem collisions ("büyüdükçe", "kolay", "sırt dolmuş").
All of them are now pinned in `REVIEW_PROHIBITED` / `REVIEW_ORDINARY`.

Known residuals:
- an earlier-photo comparison written as a setup phrase ("a bigger frame
  than last time" is treated as setup);
- soft adjectives and idioms the patterns do not list ("the midsection is
  soft", "yeni başlayan biri için kaslısın", "for a beginner" praise other
  than impressive/great/good/solid/strong/excellent);
- "sislik" written without Turkish letters (indistinguishable from haziness);
- praise relative to a group the patterns do not list ("for a beginner",
  which is also ordinary advice);
- pump wording that uses "swollen"/"şişmiş" is refused, as specified; watch
  for it in real outputs, since this product is called Pump Check;
- judgments written as adjectives the patterns do not list.
