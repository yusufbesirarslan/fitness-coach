# PostHog Flutter SDK — synthetic capture evidence (2026-11-27)

Purpose: replace the prior static-only findings A1/A2 (#541/#211 audits) with
measured bytes, so the gateway-vs-SDK decision rests on evidence.

## Setup (synthetic only)

| Item | Value |
|---|---|
| Plugin | `posthog_flutter` 5.61.1 (the app's exact pin) |
| Native | `posthog-ios` 3.98.2, revision `a4deb7222130189d7cab89682fd3475ee0ed6e2d` (same revision as the prior hosted app build) |
| Config | The app's `privatePosthogConfig` copied **verbatim** from mobile `797e6e35` `lib/core/analytics/posthog_analytics.dart:101-140` (sha256 `a1558743…9b6f49`). Only deviations: `host` → loopback sink; Dart `beforeSend` = pass-through (to see what native adds after Dart). |
| Info.plist | `AUTO_INIT=false`, both push-capture keys `false` (as the app) + local-network ATS for the sink |
| Token | `phc_SYNTHETIC_HARNESS_TOKEN_NOT_A_REAL_PROJECT` — no project exists |
| Device | Fresh iPhone 17 simulator, iOS 27.0, created and deleted by the run |
| Sink | `capture_server.py`, 127.0.0.1 only, records every request; can answer 503 |
| Not covered | Android (`posthog-android` [3.77.0, 4.0) not resolved/measured); a physical device; a real PostHog project |

Raw evidence: `~/axisai-first-public-analytics-20261127/sdk-capture/`
(`capture-pass1-both-runs-scenarioA.jsonl`, `capture-pass2-A-then-B.jsonl`,
`SHA256SUMS`, harness source, extracted SDK privacy manifests).

## Measured results

| # | Finding | Evidence (pass 2 unless noted) |
|---|---|---|
| S1 | **Native enrichment cannot be removed from Dart.** Every event carries 27 `$`-properties added after Dart `beforeSend`: `$app_build/$app_name/$app_namespace/$app_version`, `$device_manufacturer/$device_model/$device_name/$device_type`, `$os_name/$os_version`, `$locale`, `$timezone`, `$screen_width/$screen_height`, `$network_wifi/$network_cellular`, `$is_emulator/$is_testflight/$is_sideloaded/$is_ios_running_on_mac/$is_mac_catalyst_app`, `$session_id`, `$lib/$lib_version`, `$recording_status`, `$sdk_debug_pending_queue_size`, `$is_identified`, `$process_person_profile`, `$geoip_disable`. Headers add `Accept-Language` (device locale) and `User-Agent: posthog-flutter/5.61.1`. | pass 1 batch, event `axis_mobile_v1_app_opened` |
| S2 | **Unrequested vendor calls.** `POST /flags?v=2` is sent after `optIn()` although `preloadFeatureFlags=false`, carrying `$anon_distinct_id`, `$device_id`, `person_properties` (app/OS/lib versions) and `timezone`. `GET /array/<token>/config` (remote config) is fetched on `setup()` **while still opted out** (and again on relaunch). | pass 2 t=8.36 s (setup, opted out), t=14.48 s, t=65.44 s |
| S3 | **Post-withdrawal delivery.** Events captured before the app's withdrawal sequence (`optOut()` then `reset()`, exactly `_stopNative`) are delivered **after** it: a batch that got 503 while granted was re-sent after withdrawal once the sink recovered. `/flags` is also called after `optOut()`. | pass 2: 503 at t=26.12 s, withdrawal at 26.70 s, delivery of the same native `uuid`s at t=38.88 s |
| S4 | **Stable install identifier across withdrawal/regrant.** `$device_id` (`01a21e59-16f1-…`) is identical before withdrawal, after `reset()`, and after relaunch + regrant; `reset()` only rotates `distinct_id`. The vendor can therefore link the "fresh" pseudonym to the old one. | pass 2 `/flags` bodies at t=14.48, 26.77, 65.44 s |
| S5 | **Disk queue survives relaunch; regrant revives it; duplicates.** Relaunch + `reset()`+`optIn()` delivered pre-withdrawal events under the OLD pseudonym, plus a second copy of events already acknowledged 200 in the previous run (same native `uuid`). | pass 1, t=86.00 s batch (uuids `01a21e52-b3af…`, `cc32…`, `cc49…`) |
| S6 | Good: no traffic before `setup()`; no batch while opted out; an event captured after withdrawal was not sent; persisted SDK files: `posthog.anonymousId`, `posthog.deviceId`, queue/replay/logs folders under Application Support. | both passes; `native-storage-after-B.txt` |

SDK privacy manifests (shipped in any binary that links the SDK):
`posthog-ios` 3.98.2 declares ProductInteraction + OtherUsageData (Analytics,
**Linked=false**, Tracking=false) and required-reason APIs UserDefaults
`CA92.1`, SystemBootTime `35F9.1`, FileTimestamp `C617.1`;
`posthog_flutter` 5.61.1 declares the same two data types and UserDefaults.

## Consequence

The direct SDK path cannot meet the first-public requirements as written
("disable unnecessary device metadata and unapproved vendor processing",
"withdrawal", "pseudonymous identity") without a native bridge that
(a) strips S1 on the native side, (b) suppresses S2, (c) purges the disk queue
and rotates `$device_id` without flushing (S3–S5), and (d) is re-qualified for
every native version in the floating ranges. That is a larger and riskier
change than the server-mediated path, which removes S1–S5 by construction
(no SDK initialized, no device queue, wire built on the server).
