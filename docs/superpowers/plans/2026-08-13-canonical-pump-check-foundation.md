# Canonical Pump Check Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an owner-isolated, idempotent mobile Pump Check create/read contract with private media and strictly validated Bedrock analysis while preserving all legacy web and feed behavior.

**Architecture:** The existing `pump_check` table remains the sole persistence authority and receives only nullable/additive canonical fields. A mobile service atomically claims a user-scoped idempotency key, commits before S3/Bedrock work, conditionally claims analysis work, and persists only validated normalized analysis. The `/api/v1` adapter uses multipart upload, Bearer-derived ownership, one canonical serializer, and a persisted random opaque public ID.

**Tech Stack:** Flask 3.1, SQLAlchemy 2, Alembic, PostgreSQL/SQLite test compatibility, Pillow, private Amazon S3, Amazon Bedrock through the existing Anthropic adapter, pytest.

## Global Constraints

- Do not create a second Pump Check table or introduce a second AI/storage provider.
- Do not expose raw database IDs, S3 keys, provider payloads, prompts, user descriptions, or signed URLs in logs.
- Preserve legacy web create/gallery/feed/share semantics and `uq_pump_check_day`.
- Use test-first red/green cycles for every production behavior.
- Keep Bedrock and S3 network calls outside database locks and transactions.
- Do not push, open a PR, merge, deploy, modify Flutter, or begin Sprint 10 PR2.

---

### Task 1: Characterize legacy Pump Check behavior

**Files:**
- Create: `tests/test_pump_check_legacy_characterization.py`

**Interfaces:**
- Consumes: existing `/workout/complete`, gallery, PumpCheck model, S3 URL helper, and feed visibility behavior.
- Produces: regression coverage for owner, image key, environment, validation/fallback, workout score, timestamps, daily uniqueness, owner-only gallery deletion, and feed sharing.

- [ ] **Step 1: Add focused characterization tests with literal expected values.**
- [ ] **Step 2: Run `python -m pytest -q tests/test_pump_check_legacy_characterization.py` and confirm green because these tests describe current behavior.**
- [ ] **Step 3: Commit with `test(api): characterize legacy pump checks`.**

### Task 2: Add canonical persistence fields

**Files:**
- Modify: `app/models.py`
- Create: `migrations/versions/e9f0a1b2c3d4_add_canonical_pump_check_fields.py`
- Create: `tests/test_pump_check_migration.py`

**Interfaces:**
- Consumes: canonical `PumpCheck` ORM authority and Alembic head `d8e9f0a1b2c3`.
- Produces: nullable `captured_at`, `body_region`, `analysis_status`, `analysis`, `analysis_version`, `idempotency_key`, and `idempotency_fingerprint`; unique `(user_id, idempotency_key)`.

- [ ] **Step 1: Write migration/model tests proving the new fields and owner-scoped unique constraint are absent.**
- [ ] **Step 2: Run them and verify RED for missing fields/revision.**
- [ ] **Step 3: Add nullable ORM columns and an additive Alembic migration with no historical backfill.**
- [ ] **Step 4: Run migration/model and legacy tests and verify GREEN.**
- [ ] **Step 5: Commit with `feat(api): add canonical pump check persistence`.**

### Task 3: Add opaque identity and strict analysis domain

**Files:**
- Create: `app/services/mobile_pump_checks/identity.py`
- Create: `app/services/mobile_pump_checks/analysis.py`
- Create: `app/services/mobile_pump_checks/__init__.py`
- Create: `tests/test_mobile_pump_check_identity.py`
- Create: `tests/test_pump_check_analysis.py`

**Interfaces:**
- Produces: `pump_check_id(secret, user_id, row_id)`, `matches_pump_check_id(...)`, `parse_analysis(raw)`, `analyze_image(image_bytes, media_type, context)`, `ANALYSIS_VERSION`, and bounded canonical analysis dictionaries.

- [ ] **Step 1: Write identity tests for stability, owner binding, URL safety, and rejection of malformed tokens; verify RED.**
- [ ] **Step 2: Implement the distinct `axisai/mobile-pump-check/id/v1` HMAC domain; verify GREEN.**
- [ ] **Step 3: Write parser tests for valid output, exact required keys, enum/type/list/string bounds, HTML, false precision, medical claims, malformed JSON, and injection-like context; verify RED.**
- [ ] **Step 4: Implement constants, strict parser, plain-text safety validation, separated prompt/context blocks, and the existing Bedrock image adapter call; verify GREEN.**
- [ ] **Step 5: Commit with `feat(api): structure pump check analysis`.**

### Task 4: Harden image validation and S3 logging

**Files:**
- Modify: `app/services/validators.py`
- Modify: `s3_helper.py`
- Modify: `tests/test_validators.py`
- Modify: `tests/test_s3_helper.py`

**Interfaces:**
- Produces: `validate_uploaded_pump_check_image(file_storage)` returning validated bytes and detected canonical MIME; existing S3 methods retain signatures while logs reveal no key, URL, bucket, or owner ID.

- [ ] **Step 1: Write failing upload tests for byte bound, MIME/content mismatch, malformed image, unsupported type, and pixel limit.**
- [ ] **Step 2: Implement the minimal multipart validator using Pillow format detection and the existing resource bounds.**
- [ ] **Step 3: Write failing log-capture tests for upload/download/presign failures and ownership rejection.**
- [ ] **Step 4: Replace sensitive log fields with bounded event and exception-type classifications; verify all S3 tests GREEN.**
- [ ] **Step 5: Commit with `test(api): harden pump check image privacy`.**

### Task 5: Implement idempotent canonical service

**Files:**
- Create: `app/services/mobile_pump_checks/service.py`
- Create: `tests/test_mobile_pump_check_service.py`
- Create: `tests/test_mobile_pump_check_pg.py`

**Interfaces:**
- Consumes: validated command, private S3 helper, strict analysis adapter, PumpCheck ORM.
- Produces: `parse_create_command(form, image)`, `create_or_replay(user_id, key, command)`, `get_owned(user_id, token)`, and exceptions for validation, conflict, unavailable storage/provider, and private not-found.

- [ ] **Step 1: Write failing semantic fingerprint/replay/conflict/cross-user tests.**
- [ ] **Step 2: Implement typed fingerprint domain `axisai/mobile-pump-check-create/v1` using image SHA-256, normalized region/environment/description, and canonical UTC captured time.**
- [ ] **Step 3: Implement insert-race resolution through the database unique constraint and verify GREEN.**
- [ ] **Step 4: Write failing pending/analyzing/completed/failed replay tests, including response-loss behavior and no duplicate Bedrock call.**
- [ ] **Step 5: Implement conditional state claims with commits before S3/Bedrock, immediate private-key persistence after upload, and deterministic finalization/failure.**
- [ ] **Step 6: Add opt-in real PostgreSQL tests for same-command race, conflicting-command race, and cross-user same-key independence.**
- [ ] **Step 7: Commit with `feat(api): make pump check creation idempotent`.**

### Task 6: Expose the minimum mobile API

**Files:**
- Create: `app/blueprints/mobile_pump_checks.py`
- Modify: `app/blueprints/mobile_api.py`
- Create: `tests/test_mobile_pump_check_api.py`
- Create: `tests/test_mobile_pump_check_architecture.py`

**Interfaces:**
- Produces: `POST /api/v1/pump-checks`, `GET /api/v1/pump-checks/<PumpCheckId>`, and `serialize_pump_check(check, user_id, secret)` reused by both routes.

- [ ] **Step 1: Write failing API tests for Bearer-only auth, multipart validation, required Idempotency-Key, create/replay status, GET, private 404, no raw IDs/keys, secure signed URL, and GET side-effect freedom.**
- [ ] **Step 2: Implement canonical serializer and thin route adapter with existing mobile error envelope, Bedrock rate limit, and AI concurrency gate.**
- [ ] **Step 3: Write and pass architecture guards for one table, Bearer decorators, serializer reuse, provider separation, no history/comparison routes, and no raw provider response.**
- [ ] **Step 4: Commit with `feat(api): expose mobile pump check contract`.**

### Task 7: Document, review, and validate

**Files:**
- Create: `docs/PUMP_CHECK.md`
- Create: `docs/MOBILE_PUMP_CHECK.md`
- Modify: `docs/handoff.md`

**Interfaces:**
- Produces: persistence matrix, API/error/retry contract, privacy and threat model, performance/transaction review, and exact PR2 assumptions/deferred scope.

- [ ] **Step 1: Document final code truth, including legacy-null policy and PostgreSQL gates.**
- [ ] **Step 2: Run focused Pump Check, auth, S3, AI, feed/share, architecture, migration, compile, Alembic-head, and diff gates.**
- [ ] **Step 3: Run the complete deterministic eight-shard suite and aggregate exact counts.**
- [ ] **Step 4: Run PostgreSQL race/schema-drift checks if configured; otherwise record them as the only review condition.**
- [ ] **Step 5: Independently inspect the final diff for P0/P1 issues and commit documentation with `docs(api): document Sprint 10 pump check contract`.**
