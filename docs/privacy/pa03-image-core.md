# PA03 backend image sanitization core

Task AXISAI-PRIV-PA03-BE-CORE-01. Baseline main/merge base:
`8a8f50553479849d9f73771d319e78d6ef7f09bd`. This protects tested central sinks;
it does not establish full PA03 route closure or historical-image safety.

## Contract

`image_sanitization.sanitize_image(raw: bytes) -> SanitizedImage` always verifies
structure, fully decodes, applies EXIF orientation, copies pixels into a fresh
image, encodes with a bounded buffer, then reopens and verifies the output.
The frozen result includes `data` (excluded from repr), actual `media_type`,
`width` and `height`. It carries no input metadata or caller-asserted trust flag.
No public trusted-result fast path exists. Both sinks invoke the sanitizer.

Static JPEG is encoded at quality 90. Static PNG, WebP and GIF become PNG;
alpha is preserved, including palette transparency. Multi-frame/animated images
(including APNG) are refused. Other formats, malformed images, invalid EXIF
orientation, decompression warnings and unsafe global truncated-load mode fail
closed. MIME declarations are hints only: actual output type determines labels
and S3 suffixes. Source EXIF/GPS/XMP/ICC/comments/text are never copied.
Uploaded ICC profiles are omitted without color management; RGB samples are
treated as sRGB. Unsigned 16-bit grayscale PNG (decoded as `I;16`, or `I`
with samples in 0–65535) is explicitly reduced to 8-bit neutral RGB/RGBA:
`floor((sample + 128) / 257)`, nearest-integer full-range scaling. This loses
precision; it does not stretch contrast or claim lossless normalization.
Grayscale transparency keys are evaluated before quantization. Other integer
or floating-point grayscale representations fail closed. The affine transform
uses bounded image buffers; transparency alone uses a fixed 65536-entry table.
Ordinary 8-bit grayscale, CMYK and palette inputs use Pillow conversion. Wide-gamut
color fidelity and repeated JPEG quality loss remain compatibility limitations;
this policy does not claim colorimetric equivalence.

Limits: 20,000,000 input bytes, 40,000,000 pixels, 16,384 per axis and 20,000,000
encoded storage bytes. Existing ingress caps (e.g. 6 MB native Pump Check) still
apply. No global Pillow bomb limit is mutated by the sanitizer. Provider output
is capped at 1,500,000 bytes and 1600 per axis, with five bounded JPEG compression
attempts if needed. Over-limit RGBA is refused rather than flattened. Buffer
writes enforce limits during encoding. Memory is bounded by pixel count but
several decoded buffers may coexist; this is not a production concurrency/load
qualification. No new dependency, schema, flag, consent or route contract.

Errors are typed fixed categories. Parser warnings and exception text are not
logged or propagated in rendered tracebacks. S3 maps sanitizer rejection to
`S3Error("image_sanitization_failed")` before client acquisition or key return.
Vision maps it to existing preparation errors. There is no raw fallback.
S3 keeps AES256, key owner-prefix grammar, timeouts/retries, presign and deletion
authority. Command raw-byte fingerprints remain upstream and unchanged.

## Focused inventory and coverage handoff

Inventory reuses PA03-R1 `03_backend_sanitization_contract.md` and its synthetic
transfer findings; it does not repeat the broad discovery audit. Current source
search finds only `s3_helper.upload_image` calling application `put_object`.
No other application image S3 write was found; future/direct writers remain a
review obligation, not a global architectural guarantee.

| Entry/source → destination | Initial classification | Core guaranteed? / alternate raw path | Post-implementation privacy gate | Owner / evidence |
|---|---|---|---|---|
| Native Pump Check `mobile_pump_checks.service`: validated raw command → S3 and `analysis.analyze_image` provider | CORE_COVERED | Yes at both sinks; raw command identity stays unchanged | CORE_PROTECTED at tested sinks; route qualification pending | PA03 core: metadata S3/provider tests, native API, identity and lifecycle regressions |
| Browser training `training.py`: data URL validator → proof via `menu_extract.validate_pump_check` and S3 | CORE_COVERED | Yes through proof prep and upload; provider gate unchanged | CORE_PROTECTED at shared boundaries; full browser route qualification pending | PA03 route integration: existing LP13 proof regressions retained |
| Native workout `mobile_workout_sessions.py`: multipart validator → proof and optional S3 | CORE_COVERED | Shared sinks protected; no route changes | ROUTE_INTEGRATION_PENDING after K6 stabilizes | K6 owns completion source; later PA03 owns route/photo-proof qualification |
| Meal log `nutrition/meallog.py`: optional data URL validator → S3 | CORE_COVERED | Yes on upload; unavailable optional upload may omit photo | CORE_PROTECTED storage sink; route qualification pending | PA03 integration; existing S3 tests |
| Avatar `avatars.set_user_avatar`: validated data URL → S3 OR inline DB | NEEDS_ROUTE_INTEGRATION | S3 yes; disabled/error S3 persists original data URL in DB | ROUTE_INTEGRATION_PENDING | PA03 avatar integration; lifecycle regressions do not certify DB metadata |
| Comparison `mobile_pump_check_comparisons.service._load_image`: owner-checked stored read → prep → compare adapter | CORE_COVERED | Both images prepared; `get_object_bytes` itself remains raw/unbounded | CORE_PROTECTED preparation sink; historical storage/read qualification pending | PA03 comparison integration; comparison API/service/analysis regressions |
| Coach `ai_coach`: owned S3 read → optional compression → direct adapter | NEEDS_ROUTE_INTEGRATION | No unconditional prep; small/raw-on-error bypass remains | ROUTE_INTEGRATION_PENDING | PA03 legacy-provider follow-up; no protection claim |
| Retained `menu_ocr._extract_text_from_image`: raw/rendered image → OpenAI data URL | NEEDS_ROUTE_INTEGRATION | Only large-image prep; small bypass remains; current menu acquisition refuses remote image/PDF | ROUTE_INTEGRATION_PENDING | PA03 retained-helper integration; no acquisition support added |
| Terminal `ai._image_block`, `_bedrock_validate_image`, `_bedrock_compare_images` | NEEDS_ROUTE_INTEGRATION | Serialize supplied bytes; direct callers can bypass shared prep. Claude 5.5 `fit_standard_envelope` (#456) returns in-envelope bytes unchanged and re-encodes larger images only from sanitized pixels | ROUTE_INTEGRATION_PENDING | PA03 adapter integration; tested callers protected only when prep runs |
| Stored S3/DB avatar, gallery, progress and friend display/presigned delivery | HISTORICAL_IMAGE_ONLY | Display/read does not sanitize existing objects or inline DB data | HISTORICAL_EVIDENCE_PENDING | Separate K5; no customer objects listed/read/rewritten |
| Image writes outside upload helper | UNKNOWN | None found in bounded current application Python search; no general future guarantee | NOT_ASSESSED beyond inventory search | Later PA03 integration architecture check |
| Native iOS stripping and local preview/pending files | OUT_OF_SCOPE | No client implementation | NOT_ASSESSED | PA03 iOS/device qualification |
| Photo consent, D1 disclosure registry, LEGAL-V2 | OUT_OF_SCOPE | Unchanged; registry remains empty | NOT_ASSESSED / launch HOLD | D1/legal workstreams |

`get_object_bytes` is intentionally not a sanitizing read boundary. Original
historical data and direct presigned delivery remain unqualified. The prepared
comparison payload being clean does not remediate its stored source. No live
image access is authorized by this work.

## Qualification and review

Synthetic metadata suite covers GPS IFDs, timestamps/device fields, orientation
1–8, PNG text/XMP/ICC, WebP EXIF/XMP/ICC, GIF comments, static/animated/transparent
formats, MIME mismatch, corrupt/truncated input, resource limits, repeated
sanitization, S3 body/type/key/encryption, provider bytes, no-call failures and
private parser errors/warnings. Checks combine decoded metadata with JPEG marker
and PNG chunk inspection. External mutation probes restore raw passthrough and
raw storage behavior and require actual test failures.

Only image payload expectations/fixtures change in existing tests; completion,
authorization, idempotency, D1 and PA06 assertions remain. CI adds this branch to
the existing push trigger to bind all jobs/shard manifests to exact PR HEAD.
No workflow jobs or security checks are weakened.

Independent review must bind final head and CI, inspect coverage boundaries and
raw-path residuals, and assess format/color/resource compatibility. No merge,
deployment, launch approval, historical remediation or feature activation is
part of this task. Full PA03 route qualification, K5 evidence, iOS stripping,
D1/legal readiness and K6/photo-proof qualification remain separate gates.
