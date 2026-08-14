# Pump Check Comparison Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the backend-only, owner-private, idempotent Sprint 10 PR3 API that compares an explicitly ordered pair of canonical Pump Checks with one bounded two-image Bedrock analysis.

**Architecture:** `PumpCheckComparison` owns the one canonical result for an owner, directional pair, and version; `PumpCheckComparisonRequest` only maps owner-scoped idempotency keys to that result. A short-transaction service validates source rows, converges concurrent requests, claims a fenced analysis generation, performs S3 reads/image preparation/Bedrock outside the transaction, and conditionally finalizes. A promoted shared image utility keeps both existing single-image analysis and the new two-image path below a strict 1.5 MB per-image ceiling without modifying S3 originals.

**Tech Stack:** Python 3.11, Flask, Flask-SQLAlchemy/SQLAlchemy, Alembic, PostgreSQL 16 and SQLite test variants, Pillow, boto3 S3 helper, Anthropic Claude on Amazon Bedrock, pytest, GitHub Actions.

## Global Constraints

- Work only in `C:\Users\yusuf\develop\fitness-coach\.worktrees\sprint10-pr3-pump-check-comparison-intelligence` on branch `sprint10-pr3-pump-check-comparison-intelligence`.
- Preserve preparatory commit `a69c958` as an isolated pre-existing test-harness fix made before every PR3 production change.
- Use comparison version `pump-check-comparison-analysis/v1` and PR1 source version `pump-check-analysis/v1` exactly.
- Keep directional order: never sort baseline/current IDs; require `baseline.captured_at < current.captured_at`.
- Require owner-scoped opaque IDs, exact body-region equality, completed/valid canonical PR1 analyses, private owner S3 keys, and source quality other than `insufficient` before external I/O.
- Keep the original Pump Check S3 object unchanged; prepare only in-memory provider bytes with a strict `1_500_000` byte postcondition and `1_600` pixel longest edge.
- Preserve the existing `_bedrock_validate_image` signature and single-image message layout.
- Do not add history, automatic previous-check selection, image URLs, Flutter changes, progress scores, heatmaps, body-fat estimates, numeric deltas, program rewrites, social behavior, another provider, or PR4 work.
- Do not hold a database transaction or row lock across S3 or Bedrock I/O.
- Never log image content/base64, S3 keys, opaque tokens, descriptions, prompts, provider output, idempotency keys, or fingerprints.
- Keep delivery local: commit the clean worktree; do not push, open a pull request, merge, or deploy.

---

## File Structure

- Create `app/services/vision_images.py`: shared bounded image validation/preparation and failure types.
- Modify `app/services/menu_ocr.py`: compatibility imports for the promoted shared utility.
- Modify `app/services/mobile_pump_checks/analysis.py`: prepare PR1 images before the unchanged single-image adapter.
- Modify `app/services/ai.py`: add the explicit labeled two-image Bedrock adapter only.
- Create `app/services/mobile_pump_check_comparisons/{identity,analysis,service}.py`: opaque identity, strict provider contract, and canonical orchestration.
- Create `app/blueprints/mobile_pump_check_comparisons.py` and modify `app/blueprints/mobile_api.py`: POST/GET transport on the existing versioned blueprint.
- Modify `app/models.py` and `app/cli.py`: persistence authorities and erasure ordering.
- Create `migrations/versions/fa1b2c3d4e5f_add_pump_check_comparisons.py`: additive, create-all-aware migration from `e9f0a1b2c3d4`.
- Add focused unit/API/architecture/PostgreSQL tests; modify `.github/workflows/ci.yml` to enforce the new race module.
- Update canonical Pump Check docs, handoff, plan checkboxes, and the exact 27-section implementation report.

---

### Task 1: Shared bounded vision-image preparation

**Files:**
- Create: `app/services/vision_images.py`
- Modify: `app/services/menu_ocr.py:115-176`
- Modify: `app/services/mobile_pump_checks/analysis.py:123-135`
- Create: `tests/test_vision_images.py`
- Modify: `tests/test_pump_check_analysis.py`
- Modify: `tests/test_menu_ocr.py`

**Interfaces:**
- Consumes: Pillow `Image`, Flask `current_app`, validated image bytes/media types.
- Produces: `prepare_image_for_vision(image_bytes: bytes, media_type: str, max_bytes: int = 1_500_000) -> tuple[bytes, str]`, `ImagePreparationError`, `ImageTooLargeError`, and menu compatibility name `_compress_image_for_vision`.

- [ ] **Step 1: Write failing shared-utility and PR1 characterization tests**

```python
def test_small_valid_image_passes_through_without_reencoding():
    raw = _jpeg_bytes((32, 32), quality=90)
    prepared, media_type = prepare_image_for_vision(raw, 'image/jpeg')
    assert prepared is raw
    assert media_type == 'image/jpeg'


def test_large_valid_image_is_rgb_jpeg_below_provider_ceiling():
    raw = _noisy_png_bytes((2200, 1800))
    prepared, media_type = prepare_image_for_vision(raw, 'image/png')
    assert len(prepared) <= 1_500_000
    assert media_type == 'image/jpeg'
    with Image.open(BytesIO(prepared)) as image:
        assert image.mode == 'RGB'
        assert max(image.size) <= 1600


def test_preparation_fails_closed_when_quality_floor_cannot_meet_ceiling(monkeypatch):
    monkeypatch.setattr(vision_images, '_encode_jpeg',
                        lambda *args: b'x' * 1_500_001)
    with pytest.raises(ImagePreparationError):
        prepare_image_for_vision(_jpeg_bytes((1700, 1700)), 'image/jpeg')


def test_canonical_pr1_normalizes_before_single_image_provider(monkeypatch):
    seen = {}
    monkeypatch.setattr(analysis, 'prepare_image_for_vision',
                        lambda raw, media: (b'bounded', 'image/jpeg'))
    def provider(raw, media, prompt, max_tokens):
        seen.update(raw=raw, media=media)
        return json.dumps(_valid())
    assert analysis.analyze_image(
        b'oversized', 'image/png', {}, provider=provider) == _valid()
    assert seen == {'raw': b'bounded', 'media': 'image/jpeg'}
```

Retain a menu test that imports `_compress_image_for_vision` from `app.services.menu_ocr` and proves its return shape and bomb guard are unchanged.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest -q tests/test_vision_images.py tests/test_pump_check_analysis.py tests/test_menu_ocr.py`

Expected: FAIL during import because `app.services.vision_images` and `prepare_image_for_vision` do not exist.

- [ ] **Step 3: Promote the current algorithm into the shared utility**

```python
MAX_IMAGE_BYTES = 1_500_000
MAX_IMAGE_PIXELS = 50_000_000
MAX_IMAGE_DIMENSION = 1600
JPEG_QUALITIES = (85, 70, 55, 40, 30)


class ImagePreparationError(ValueError):
    pass


class ImageTooLargeError(ImagePreparationError):
    pass


def prepare_image_for_vision(image_bytes, media_type,
                             max_bytes=MAX_IMAGE_BYTES):
    if not isinstance(image_bytes, bytes) or not image_bytes:
        raise ImagePreparationError('image bytes are required')
    media_type = _safe_media_type(media_type)
    if len(image_bytes) <= max_bytes:
        return image_bytes, media_type
    image = _open_bounded_image(image_bytes)
    image = _rgb_and_resize(image, MAX_IMAGE_DIMENSION)
    for quality in JPEG_QUALITIES:
        encoded = _encode_jpeg(image, quality)
        if len(encoded) <= max_bytes:
            current_app.logger.info(
                '[VISION] event=image_prepared input_bytes=%d '
                'output_bytes=%d width=%d height=%d',
                len(image_bytes), len(encoded), image.width, image.height)
            return encoded, 'image/jpeg'
    raise ImagePreparationError('prepared image exceeds byte ceiling')
```

Move the header-first pixel check, `Image.DecompressionBombError` handling, RGB conversion, LANCZOS resize, and JPEG encoding into focused private helpers. In `menu_ocr.py`, import `ImageTooLargeError` and alias `prepare_image_for_vision` as `_compress_image_for_vision`; do not retain a second implementation. In PR1 `analyze_image`, call preparation immediately before its existing provider call.

- [ ] **Step 4: Run focused tests and existing call-site regressions**

Run: `python -m pytest -q tests/test_vision_images.py tests/test_pump_check_analysis.py tests/test_menu_ocr.py tests/test_menu_extract_helpers.py tests/test_ai_coach.py tests/test_coach_tools.py`

Expected: PASS; PR1 schema and the single-image adapter remain unchanged.

- [ ] **Step 5: Commit**

```bash
git add app/services/vision_images.py app/services/menu_ocr.py app/services/mobile_pump_checks/analysis.py tests/test_vision_images.py tests/test_pump_check_analysis.py tests/test_menu_ocr.py
git commit -m 'refactor(ai): share bounded vision image preparation'
```

---

### Task 2: Strict parser and labeled two-image Bedrock adapter

**Files:**
- Modify: `app/services/ai.py:115-150`
- Create: `app/services/mobile_pump_check_comparisons/__init__.py`
- Create: `app/services/mobile_pump_check_comparisons/analysis.py`
- Create: `tests/test_pump_check_comparison_analysis.py`
- Create: `tests/test_bedrock_comparison_adapter.py`

**Interfaces:**
- Consumes: Task 1 image preparation; PR1 safety semantics; existing Bedrock client/model/token ceiling/concurrency slot.
- Produces: `ANALYSIS_VERSION`, `InvalidComparisonAnalysis`, `parse_analysis(raw: str, source_quality_cap: str) -> tuple[str, dict]`, `build_prompt(context: dict) -> str`, `analyze_images(baseline: tuple[bytes, str], current: tuple[bytes, str], context: dict, source_quality_cap: str, provider=None) -> tuple[str, dict]`, and `_bedrock_compare_images(baseline_bytes, baseline_media_type, current_bytes, current_media_type, prompt, max_tokens=1200, temperature=0.0) -> str`.

- [ ] **Step 1: Write failing exact-schema and safety tests**

```python
def _valid(comparability='comparable'):
    return {
        'summary': 'Framing permits a cautious visual comparison.',
        'observed_changes': ['Shoulder outline appears clearer in Image B.'],
        'stable_areas': ['Camera distance appears consistent.'],
        'focus_areas': ['Keep lighting direction consistent.'],
        'limitations': ['Two images do not establish long-term progress.'],
        'comparability_reasons': ['Body region and framing align.'],
        'next_check_guidance': 'Repeat the same pose and lighting.',
        'comparability': comparability,
    }


def test_parser_promotes_comparability_out_of_analysis():
    comparability, payload = parse_analysis(
        json.dumps(_valid()), 'comparable')
    assert comparability == 'comparable'
    assert set(payload) == set(_valid()) - {'comparability'}


@pytest.mark.parametrize('unsafe', [
    'Body fat dropped 3%.', 'Muscle growth is 2 cm.',
    'This suggests an injury.', '<b>Visible change</b>',
    'Progress score is 82.', 'The workout caused this change.',
])
def test_every_text_field_rejects_unsafe_language(unsafe):
    value = _valid()
    value['comparability_reasons'] = [unsafe]
    with pytest.raises(InvalidComparisonAnalysis):
        parse_analysis(json.dumps(value), 'comparable')


def test_limited_source_rejects_provider_comparable_claim():
    with pytest.raises(InvalidComparisonAnalysis):
        parse_analysis(json.dumps(_valid('comparable')), 'limited')
```

Add parameterized cases for every missing/unknown key, invalid enum, non-string text, empty scalar, 400/300/240 character limits, and list counts 5/4/4/4/5.

- [ ] **Step 2: Write failing payload-order and single-image regression tests**

```python
def test_two_image_adapter_labels_and_orders_baseline_before_current(
        monkeypatch):
    fake = FakeBedrockResponse('comparison-json')
    monkeypatch.setattr(ai, 'bedrock_client', fake.client)
    result = ai._bedrock_compare_images(
        b'baseline', 'image/jpeg', b'current', 'image/png',
        'compare', max_tokens=900)
    content = fake.calls[0]['messages'][0]['content']
    assert [block['type'] for block in content] == [
        'text', 'text', 'image', 'text', 'image']
    assert content[1]['text'] == 'Image A — baseline'
    assert _decoded(content[2]) == b'baseline'
    assert content[3]['text'] == 'Image B — current'
    assert _decoded(content[4]) == b'current'
    assert result == 'comparison-json'


def test_existing_single_image_layout_is_unchanged(monkeypatch):
    fake = FakeBedrockResponse('single-json')
    monkeypatch.setattr(ai, 'bedrock_client', fake.client)
    ai._bedrock_validate_image(b'one', 'image/jpeg', 'inspect')
    content = fake.calls[0]['messages'][0]['content']
    assert [block['type'] for block in content] == ['text', 'image']
```

- [ ] **Step 3: Run tests and verify RED**

Run: `python -m pytest -q tests/test_pump_check_comparison_analysis.py tests/test_bedrock_comparison_adapter.py`

Expected: FAIL because the parser and `_bedrock_compare_images` are absent.

- [ ] **Step 4: Implement the exact contract**

```python
ANALYSIS_VERSION = 'pump-check-comparison-analysis/v1'
COMPARABILITY_VALUES = frozenset({
    'comparable', 'limited', 'not_comparable'})
REQUIRED_FIELDS = frozenset({
    'summary', 'observed_changes', 'stable_areas', 'focus_areas',
    'limitations', 'comparability_reasons', 'next_check_guidance',
    'comparability',
})


def parse_analysis(raw, source_quality_cap):
    value = _load_exact_json(raw, REQUIRED_FIELDS, maximum=12_000)
    comparability = _comparability(value.pop('comparability'))
    if source_quality_cap == 'limited' and comparability == 'comparable':
        raise InvalidComparisonAnalysis(
            'source quality caps comparability')
    return comparability, {
        'summary': _plain_text(value['summary'], 400),
        'observed_changes': _text_list(
            value['observed_changes'], 5, 240),
        'stable_areas': _text_list(value['stable_areas'], 4, 240),
        'focus_areas': _text_list(value['focus_areas'], 4, 240),
        'limitations': _text_list(value['limitations'], 4, 240),
        'comparability_reasons': _text_list(
            value['comparability_reasons'], 5, 240),
        'next_check_guidance': _plain_text(
            value['next_check_guidance'], 300),
    }
```

Reuse PR1 safety through neutral shared helpers or focused imports. Add comparison-only rejection for progress scores, image-derived deltas/growth, and causal claims. The prompt enumerates exact keys/bounds, treats context as untrusted JSON, and forbids medical/body-composition/numeric/causal claims.

```python
def _bedrock_compare_images(
        baseline_bytes, baseline_media_type,
        current_bytes, current_media_type, prompt,
        max_tokens=1200, temperature=0.0):
    content = [
        {'type': 'text', 'text': prompt},
        {'type': 'text', 'text': 'Image A — baseline'},
        _image_block(baseline_bytes, baseline_media_type),
        {'type': 'text', 'text': 'Image B — current'},
        _image_block(current_bytes, current_media_type),
    ]
    return _bedrock_image_message(content, max_tokens, temperature)
```

Factor only private encoding/error handling. Keep the single-image signature and `[text, image]` layout intact.

- [ ] **Step 5: Run comparison and existing adapter tests**

Run: `python -m pytest -q tests/test_pump_check_comparison_analysis.py tests/test_bedrock_comparison_adapter.py tests/test_pump_check_analysis.py tests/test_ai_routing.py tests/test_coach_tools.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/ai.py app/services/mobile_pump_check_comparisons tests/test_pump_check_comparison_analysis.py tests/test_bedrock_comparison_adapter.py
git commit -m 'feat(ai): add bounded pump check comparison analysis'
```

---

### Task 3: Persistence authority, identity, and migration

**Files:**
- Modify: `app/models.py:478-532`
- Create: `app/services/mobile_pump_check_comparisons/identity.py`
- Create: `migrations/versions/fa1b2c3d4e5f_add_pump_check_comparisons.py`
- Create: `tests/test_pump_check_comparison_identity.py`
- Create: `tests/test_pump_check_comparison_migration.py`
- Modify: `tests/test_migration_graph.py`
- Modify: `tests/test_cascade_delete.py`

**Interfaces:**
- Consumes: HMAC opaque-ID pattern, `PumpCheck`, `User`, JSONB/SQLite JSON convention, migration head `e9f0a1b2c3d4`.
- Produces: `PumpCheckComparison`, `PumpCheckComparisonRequest`, `new_comparison_id`, `is_valid_comparison_id`, and directional `fingerprint`.

- [ ] **Step 1: Write failing identity/model tests**

```python
def test_comparison_id_is_opaque_owner_bound():
    nonce = b'n' * 32
    first = new_comparison_id('secret', 7, nonce)
    assert len(first) == 24
    assert is_valid_comparison_id(first)
    assert first != new_comparison_id('secret', 8, nonce)


def test_fingerprint_is_versioned_and_directional():
    ab = fingerprint('A' * 24, 'B' * 24, ANALYSIS_VERSION)
    ba = fingerprint('B' * 24, 'A' * 24, ANALYSIS_VERSION)
    assert len(ab) == 64
    assert ab != ba


def test_ledger_has_no_analysis_authority():
    assert {'comparability', 'analysis', 'analysis_attempt'} <= set(
        PumpCheckComparison.__table__.columns)
    assert {'idempotency_key', 'fingerprint', 'comparison_id'} <= set(
        PumpCheckComparisonRequest.__table__.columns)
    assert not {'analysis', 'comparability'} & set(
        PumpCheckComparisonRequest.__table__.columns)
```

Assert every column length/nullability, both unique constraints, distinct-source/status/comparability/terminal checks, and all FK `ondelete='CASCADE'` directions.

- [ ] **Step 2: Write failing migration lifecycle tests**

```python
def test_upgrade_creates_both_tables_on_legacy_schema(tmp_path):
    with _legacy_engine(tmp_path) as connection:
        _run_upgrade(connection)
        tables = set(sa.inspect(connection).get_table_names())
        assert {'pump_check_comparison',
                'pump_check_comparison_request'} <= tables
        _assert_expected_constraints(sa.inspect(connection))


def test_upgrade_accepts_compatible_create_all_and_is_idempotent(app):
    with app.app_context():
        _run_upgrade(db.engine.connect())
        _run_upgrade(db.engine.connect())


def test_upgrade_rejects_incompatible_partial_table(tmp_path):
    with pytest.raises(
            RuntimeError,
            match='incompatible pump_check_comparison schema'):
        _upgrade_partial_schema(tmp_path)


def test_downgrade_preserves_pump_check_sources(tmp_path):
    tables = _upgrade_then_downgrade(tmp_path)
    assert 'pump_check' in tables
    assert 'pump_check_comparison' not in tables
    assert 'pump_check_comparison_request' not in tables
```

- [ ] **Step 3: Run tests and verify RED**

Run: `python -m pytest -q tests/test_pump_check_comparison_identity.py tests/test_pump_check_comparison_migration.py tests/test_migration_graph.py tests/test_cascade_delete.py`

Expected: FAIL because the models, identity helpers, and revision are absent.

- [ ] **Step 4: Add the ORM authorities**

```python
class PumpCheckComparison(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False, index=True)
    baseline_pump_check_id = db.Column(
        db.Integer, db.ForeignKey('pump_check.id', ondelete='CASCADE'),
        nullable=False, index=True)
    current_pump_check_id = db.Column(
        db.Integer, db.ForeignKey('pump_check.id', ondelete='CASCADE'),
        nullable=False, index=True)
    public_id = db.Column(db.String(24), nullable=False)
    status = db.Column(
        db.String(20), nullable=False,
        default='pending', server_default='pending')
    comparability = db.Column(db.String(20), nullable=True)
    analysis = db.Column(
        JSONB().with_variant(db.JSON(), 'sqlite'), nullable=True)
    analysis_version = db.Column(db.String(50), nullable=False)
    analysis_started_at = db.Column(db.DateTime, nullable=True)
    analysis_attempt = db.Column(
        db.Integer, nullable=False, default=0, server_default='0')
    analysis_failure_kind = db.Column(db.String(24), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'baseline_pump_check_id',
            'current_pump_check_id', 'analysis_version',
            name='uq_pump_comparison_pair_version'),
        db.UniqueConstraint(
            'user_id', 'public_id',
            name='uq_pump_comparison_user_public_id'),
        db.CheckConstraint(
            'baseline_pump_check_id <> current_pump_check_id',
            name='ck_pump_comparison_distinct_sources'),
        db.CheckConstraint(STATUS_CHECK_SQL,
                           name='ck_pump_comparison_status'),
        db.CheckConstraint(COMPARABILITY_CHECK_SQL,
                           name='ck_pump_comparison_comparability'),
        db.CheckConstraint(TERMINAL_COHERENCE_SQL,
                           name='ck_pump_comparison_terminal_fields'),
    )


class PumpCheckComparisonRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False, index=True)
    idempotency_key = db.Column(db.String(64), nullable=False)
    fingerprint = db.Column(db.String(64), nullable=False)
    comparison_id = db.Column(
        db.Integer,
        db.ForeignKey('pump_check_comparison.id', ondelete='CASCADE'),
        nullable=False, index=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint(
        'user_id', 'idempotency_key',
        name='uq_pump_comparison_request_user_key'),)
```

Define the three check SQL strings explicitly so completed rows require analysis and comparability, while non-completed rows require both null. Add owner/source/ledger relationships with `passive_deletes=True`; comparison deletion must never delete a source Pump Check.

- [ ] **Step 5: Implement identity and directional fingerprint**

```python
ID_DOMAIN = b'axisai/mobile-pump-check-comparison/id/v1'
FINGERPRINT_DOMAIN = 'axisai/mobile-pump-check-comparison-create/v1'


def fingerprint(baseline_token, current_token, version):
    semantic = {
        'domain': FINGERPRINT_DOMAIN,
        'baseline_pump_check_id': baseline_token,
        'current_pump_check_id': current_token,
        'analysis_version': version,
    }
    encoded = json.dumps(
        semantic, sort_keys=True, separators=(',', ':'),
        ensure_ascii=True)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()
```

- [ ] **Step 6: Implement the create-all-aware migration**

Set `revision = 'fa1b2c3d4e5f'` and `down_revision = 'e9f0a1b2c3d4'`. Define full columns, FKs, indexes, uniques, and checks. Create absent tables in comparison-then-ledger order. If a table exists, inspect schema and raise `RuntimeError` for incompatible partial state. Downgrade ledger first, comparison second. Use PostgreSQL JSONB with SQLite JSON.

- [ ] **Step 7: Verify schema and head**

Run: `python -m pytest -q tests/test_pump_check_comparison_identity.py tests/test_pump_check_comparison_migration.py tests/test_migration_graph.py tests/test_cascade_delete.py`

Run: `flask --app starter db heads`

Expected: tests PASS and the sole head is `fa1b2c3d4e5f`.

- [ ] **Step 8: Commit**

```bash
git add app/models.py app/services/mobile_pump_check_comparisons/identity.py migrations/versions/fa1b2c3d4e5f_add_pump_check_comparisons.py tests/test_pump_check_comparison_identity.py tests/test_pump_check_comparison_migration.py tests/test_migration_graph.py tests/test_cascade_delete.py
git commit -m 'feat(db): add canonical pump check comparisons'
```

---

### Task 4: Deterministic eligibility and public serialization

**Files:**
- Create: `app/services/mobile_pump_check_comparisons/service.py`
- Create: `tests/test_pump_check_comparison_service.py`
- Modify: `s3_helper.py:88-105`
- Modify: `tests/test_s3_helper.py`

**Interfaces:**
- Consumes: Task 2 version/parser; Task 3 models/identity; PR1 `parse_analysis`, `PumpCheck`, and S3 owner-key grammar.
- Produces: `CreateCommand`, `InvalidCommand`, `PumpCheckNotFound`, `ChecksNotComparable`, `create_command`, `resolve_eligible_sources`, `get_owned`, and `serialize_comparison`.

- [ ] **Step 1: Write failing command, privacy, eligibility, and serializer tests**

```python
def test_command_requires_exact_ordered_opaque_tokens():
    command = create_command({
        'baseline_pump_check_id': 'A' * 24,
        'current_pump_check_id': 'B' * 24,
    })
    assert command.baseline_token == 'A' * 24
    assert command.current_token == 'B' * 24
    with pytest.raises(InvalidCommand):
        create_command({
            'baseline_pump_check_id': 'A' * 24,
            'current_pump_check_id': 'B' * 24,
            'extra': True,
        })


@pytest.mark.parametrize('mutation', [
    lambda a, b: setattr(a, 'captured_at', b.captured_at),
    lambda a, b: setattr(b, 'body_region', 'back'),
    lambda a, b: setattr(b, 'analysis_status', 'failed'),
    lambda a, b: setattr(b, 'analysis_version', 'legacy'),
    lambda a, b: setattr(b, 'valid', False),
    lambda a, b: b.analysis.update(quality='insufficient'),
    lambda a, b: setattr(b, 'image_key', None),
])
def test_ineligibility_performs_no_external_work(
        mutation, eligible_pair, external_calls):
    baseline, current = eligible_pair
    mutation(baseline, current)
    db.session.commit()
    with pytest.raises(ChecksNotComparable):
        resolve_eligible_sources(
            baseline.user_id, _command(baseline, current))
    assert external_calls == {'s3': 0, 'provider': 0}


def test_cross_owner_and_unknown_sources_are_same_private_not_found(
        owner, cross_owner_command, unknown_command):
    with pytest.raises(PumpCheckNotFound):
        resolve_eligible_sources(owner.id, cross_owner_command)
    with pytest.raises(PumpCheckNotFound):
        resolve_eligible_sources(owner.id, unknown_command)


def test_serializer_exposes_only_public_directional_contract(comparison):
    payload = serialize_comparison(comparison)
    assert set(payload) == {
        'id', 'baseline_pump_check_id', 'current_pump_check_id',
        'status', 'comparability', 'analysis',
        'analysis_version', 'created_at',
    }
    forbidden = {
        'image_url', 'image_key', 'user_id', 'idempotency_key',
        'fingerprint', 'analysis_attempt', 'analysis_started_at',
        'analysis_failure_kind',
    }
    assert not forbidden & set(payload)
```

Add positive exact-region/chronology/stored-analysis cases, limited-source cap, reversed direction rejection, distinct-source rejection, corrupt stored PR1 JSON, and private-key owner mismatch.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest -q tests/test_pump_check_comparison_service.py tests/test_s3_helper.py`

Expected: FAIL because the comparison service and public owner-key predicate are absent.

- [ ] **Step 3: Implement exact parsing and owner-scoped resolution**

```python
@dataclass(frozen=True)
class CreateCommand:
    baseline_token: str
    current_token: str


def create_command(value):
    if not isinstance(value, dict) or set(value) != {
            'baseline_pump_check_id', 'current_pump_check_id'}:
        raise InvalidCommand()
    baseline = value['baseline_pump_check_id']
    current = value['current_pump_check_id']
    if (not is_valid_pump_check_id(baseline)
            or not is_valid_pump_check_id(current)):
        raise InvalidCommand()
    return CreateCommand(baseline, current)


def _owned_source(user_id, token):
    row = PumpCheck.query.filter_by(
        user_id=user_id, public_id=token).first()
    if row is None:
        raise PumpCheckNotFound()
    return row
```

Validate all seven design eligibility rules before any S3 read/Bedrock call. Re-run PR1 `parse_analysis(json.dumps(row.analysis))` so corrupt stored JSON fails closed. Expose `key_belongs_to_user(key, user_id) -> bool` from `s3_helper.py` as a thin public wrapper over its current segment check. Return `EligibleSources(baseline, current, source_quality_cap)`, where the cap is `limited` if either source is limited, otherwise `comparable`.

- [ ] **Step 4: Implement private lookup and serializer**

```python
def get_owned(user_id, token):
    if not is_valid_comparison_id(token):
        raise ComparisonNotFound()
    row = PumpCheckComparison.query.filter_by(
        user_id=user_id, public_id=token).first()
    if row is None:
        raise ComparisonNotFound()
    return row


def serialize_comparison(row):
    return {
        'id': row.public_id,
        'baseline_pump_check_id': row.baseline_pump_check.public_id,
        'current_pump_check_id': row.current_pump_check.public_id,
        'status': row.status,
        'comparability': row.comparability,
        'analysis': row.analysis,
        'analysis_version': row.analysis_version,
        'created_at': _iso(row.created_at),
    }
```

- [ ] **Step 5: Run focused regressions**

Run: `python -m pytest -q tests/test_pump_check_comparison_service.py tests/test_pump_check_analysis.py tests/test_s3_helper.py`

Expected: PASS and every deterministic rejection proves zero external calls.

- [ ] **Step 6: Commit**

```bash
git add app/services/mobile_pump_check_comparisons/service.py s3_helper.py tests/test_pump_check_comparison_service.py tests/test_s3_helper.py
git commit -m 'feat(api): validate pump check comparison pairs'
```

---

### Task 5: Idempotent convergence, leases, and external analysis

**Files:**
- Modify: `app/services/mobile_pump_check_comparisons/service.py`
- Create: `tests/test_pump_check_comparison_lifecycle.py`

**Interfaces:**
- Consumes: Tasks 1-4, `s3_helper.get_object_bytes`, `media_type_for_key`, and conditional SQLAlchemy updates.
- Produces: `create_or_replay(user_id, key, command) -> tuple[PumpCheckComparison, bool]`, `IdempotencyConflict`, `ComparisonUnavailable`, `MediaNotComparable`, `_claim_analysis`, `_finalize_success`, and `_finalize_failure`.

- [ ] **Step 1: Write failing ledger-order and convergence tests**

```python
def test_existing_key_conflicts_before_source_lookup(
        monkeypatch, owner, eligible_pair):
    _seed_ledger(key='comparison-key-0001', command=_command(a, b))
    monkeypatch.setattr(
        service, 'resolve_eligible_sources',
        lambda *args: pytest.fail('source lookup must not run'))
    with pytest.raises(IdempotencyConflict):
        create_or_replay(
            owner.id, 'comparison-key-0001', _command(b, a))


def test_same_key_same_command_replays_without_external_work(
        owner, command, key, counts):
    first, created = create_or_replay(owner.id, key, command)
    second, replay_created = create_or_replay(owner.id, key, command)
    assert created is True
    assert replay_created is False
    assert first.id == second.id
    assert counts == {'s3': 2, 'bedrock': 1}


def test_different_keys_same_pair_converge(
        owner, command, counts):
    first, _ = create_or_replay(owner.id, 'comparison-key-0001', command)
    second, _ = create_or_replay(owner.id, 'comparison-key-0002', command)
    assert first.id == second.id
    assert PumpCheckComparison.query.count() == 1
    assert PumpCheckComparisonRequest.query.count() == 2
    assert counts['bedrock'] == 1
```

Add cross-owner identical-key independence and proof that ineligible new commands create neither canonical nor ledger row.

- [ ] **Step 2: Write failing lease, retry, fencing, and I/O tests**

```python
def test_unexpired_lease_returns_analyzing_without_external_io(
        owner, command, key, counts):
    row = _seed_comparison(
        status='analyzing', started_at=datetime.utcnow(), attempt=3)
    replay, created = create_or_replay(owner.id, key, command)
    assert replay.id == row.id
    assert created is False
    assert counts == {'s3': 0, 'bedrock': 0}


def test_stale_lease_reclaims_and_old_generation_cannot_finalize(
        owner, command, key):
    row = _seed_comparison(
        status='analyzing',
        started_at=datetime.utcnow() - timedelta(minutes=20),
        attempt=3)
    create_or_replay(owner.id, key, command)
    assert _finalize_success(
        row.id, owner.id, 3, 'comparable', _analysis()) is False
    db.session.refresh(row)
    assert row.analysis_attempt == 4


def test_s3_and_bedrock_run_outside_transaction(
        monkeypatch, owner, command, key, valid_image):
    def read(*args, **kwargs):
        assert db.session().in_transaction() is False
        return valid_image
    def analyze(*args, **kwargs):
        assert db.session().in_transaction() is False
        return 'comparable', _analysis()
    monkeypatch.setattr(service.s3_helper, 'get_object_bytes', read)
    monkeypatch.setattr(service, 'analyze_images', analyze)
    create_or_replay(owner.id, key, command)
```

Also cover missing/corrupt media as terminal 422/no Bedrock, transient S3 retry using the same ledger, invalid provider output retry, provider `not_comparable` as completed, and persistence reconciliation failure as unavailable.

```python
def test_comparison_reads_originals_without_upload_or_replacement(
        monkeypatch, owner, command, key, valid_image):
    reads = []
    monkeypatch.setattr(
        service.s3_helper, 'get_object_bytes',
        lambda object_key, expected_user_id: (
            reads.append((object_key, expected_user_id)) or valid_image))
    monkeypatch.setattr(
        service.s3_helper, 'upload_image',
        lambda *args, **kwargs: pytest.fail(
            'comparison must not write S3'))
    create_or_replay(owner.id, key, command)
    assert len(reads) == 2
```

- [ ] **Step 3: Run lifecycle tests and verify RED**

Run: `python -m pytest -q tests/test_pump_check_comparison_lifecycle.py`

Expected: FAIL because convergence/claim/finalization is absent.

- [ ] **Step 4: Implement ledger-first pair convergence**

```python
def create_or_replay(user_id, key, command):
    digest = fingerprint(
        command.baseline_token, command.current_token,
        ANALYSIS_VERSION)
    existing_request = _request_for_key(user_id, key)
    if existing_request is not None:
        if existing_request.fingerprint != digest:
            raise IdempotencyConflict()
        row, created = existing_request.comparison, False
    else:
        sources = resolve_eligible_sources(user_id, command)
        row, created = _create_or_get_pair_and_request(
            user_id, key, digest, sources)
    return _run_or_reuse_analysis(row, user_id, created)
```

Use unique-constraint insert/rollback/owner-scoped re-query loops for pair and ledger. Compare the persisted fingerprint on a ledger race. Attach a new key to the persisted pair on a pair race. Never classify convergence `IntegrityError` as a provider failure.

- [ ] **Step 5: Implement claim and generation-fenced finalization**

```python
ANALYSIS_LEASE_SECONDS = 900


def _claim_analysis(row_id, user_id, now=None):
    now = now or datetime.utcnow()
    stale_before = now - timedelta(seconds=ANALYSIS_LEASE_SECONDS)
    claimed = PumpCheckComparison.query.filter(
        PumpCheckComparison.id == row_id,
        PumpCheckComparison.user_id == user_id,
        or_(
            PumpCheckComparison.status == 'pending',
            and_(PumpCheckComparison.status == 'failed',
                 PumpCheckComparison.analysis_failure_kind.in_(
                     RETRYABLE_FAILURES)),
            and_(PumpCheckComparison.status == 'analyzing',
                 PumpCheckComparison.analysis_started_at < stale_before),
        ),
    ).update({
        PumpCheckComparison.status: 'analyzing',
        PumpCheckComparison.analysis_started_at: now,
        PumpCheckComparison.analysis_attempt:
            PumpCheckComparison.analysis_attempt + 1,
        PumpCheckComparison.analysis_failure_kind: None,
    }, synchronize_session=False)
    db.session.commit()
    if not claimed:
        return None
    attempt = db.session.query(
        PumpCheckComparison.analysis_attempt).filter_by(
            id=row_id, user_id=user_id).scalar()
    db.session.rollback()
    return attempt
```

Condition both finalizers on owner/id/`status='analyzing'`/attempt. Persist only `storage`, `invalid_media`, `invalid_output`, `provider`, or `persistence`; terminal invalid media is not reclaimable.

- [ ] **Step 6: Implement external work after transaction end**

```python
baseline_raw = s3_helper.get_object_bytes(
    baseline.image_key, expected_user_id=user_id)
current_raw = s3_helper.get_object_bytes(
    current.image_key, expected_user_id=user_id)
baseline_image = prepare_image_for_vision(
    baseline_raw, s3_helper.media_type_for_key(baseline.image_key))
current_image = prepare_image_for_vision(
    current_raw, s3_helper.media_type_for_key(current.image_key))
comparability, analysis = analyze_images(
    baseline_image, current_image,
    {'body_region': baseline.body_region}, source_quality_cap)
```

Never pass stored PR1 narratives to Bedrock. Map corrupt/bomb/unbounded image failures to terminal `invalid_media`; map transient S3/provider/output failures to reclaimable bounded failures.

Emit only generic structured lifecycle logs through a helper with this interface:

```python
def _log_outcome(event, *, status, comparability=None,
                 attempt=None, duration_ms=None):
    current_app.logger.info(
        'pump_check_comparison event=%s status=%s '
        'comparability=%s attempt=%s duration_ms=%s',
        event, status, comparability, attempt, duration_ms)
```

The helper must not accept IDs, keys, fingerprints, descriptions, prompts, image bytes, or provider output; architecture tests inspect the signature and format string.

- [ ] **Step 7: Run lifecycle regressions**

Run: `python -m pytest -q tests/test_pump_check_comparison_lifecycle.py tests/test_pump_check_comparison_service.py tests/test_mobile_pump_check_pg.py`

Expected: comparison tests PASS; the existing opt-in PG module skips unless enabled.

- [ ] **Step 8: Commit**

```bash
git add app/services/mobile_pump_check_comparisons/service.py tests/test_pump_check_comparison_lifecycle.py
git commit -m 'feat(api): orchestrate pump check comparison analysis'
```

---

### Task 6: Owner-only HTTP contract

**Files:**
- Create: `app/blueprints/mobile_pump_check_comparisons.py`
- Modify: `app/blueprints/mobile_api.py:205-206`
- Create: `tests/test_mobile_pump_check_comparison_api.py`
- Create: `tests/test_mobile_pump_check_comparison_architecture.py`

**Interfaces:**
- Consumes: Task 5 service; existing mobile blueprint/error envelope, bearer middleware, limiter, shared AI gate, and idempotency parser.
- Produces: POST `/api/v1/pump-check-comparisons` and GET `/api/v1/pump-check-comparisons/<comparison_id>`.

- [ ] **Step 1: Write failing API/privacy tests**

```python
def test_create_requires_bearer_json_and_idempotency(
        client, auth_headers):
    assert client.post(PATH, json=_command()).status_code == 401
    assert client.post(
        PATH, data='not-json', headers=auth_headers,
        content_type='text/plain').status_code == 400
    headers = {
        key: value for key, value in auth_headers.items()
        if key != 'Idempotency-Key'}
    assert client.post(
        PATH, json=_command(), headers=headers).status_code == 400


def test_create_and_get_share_private_shape(
        client, auth_headers, dependencies):
    created = client.post(PATH, json=_command(), headers=auth_headers)
    assert created.status_code == 201
    comparison = created.get_json()['pump_check_comparison']
    assert set(comparison) == {
        'id', 'baseline_pump_check_id', 'current_pump_check_id',
        'status', 'comparability', 'analysis',
        'analysis_version', 'created_at',
    }
    fetched = client.get(
        f"{PATH}/{comparison['id']}", headers=auth_headers)
    assert fetched.status_code == 200
    assert fetched.get_json()['pump_check_comparison'] == comparison
    assert dependencies['bedrock'] == 1


def test_get_is_read_only(monkeypatch, client, auth_headers, comparison):
    monkeypatch.setattr(
        service, '_claim_analysis',
        lambda *args: pytest.fail('GET must not claim'))
    response = client.get(
        f'{PATH}/{comparison.public_id}', headers=auth_headers)
    assert response.status_code == 200
```

Add cross-owner/unknown/malformed comparison private 404 equality and exact cases for: malformed 400, source-private 404, deterministic/media 422, idempotency 409, gate-busy 503 with `Retry-After`, transient 503, unexpired analyzing 200, new 201, and replay/converged 200.

- [ ] **Step 2: Write failing architecture guards**

```python
def test_only_explicit_create_and_read_routes_exist():
    rules = {rule.rule for rule in create_app().url_map.iter_rules()}
    assert '/api/v1/pump-check-comparisons' in rules
    assert (
        '/api/v1/pump-check-comparisons/<comparison_id>'
        in rules)
    for rule in rules:
        assert not any(word in rule for word in (
            'history', 'timeline', 'automatic', 'previous'))


def test_comparison_code_has_no_second_provider_or_sensitive_output():
    source = _comparison_source()
    for forbidden in (
            'openai_client', 'generate_presigned_url', 'image_url',
            'idempotency_key=%', 'fingerprint=%', 'provider_output'):
        assert forbidden not in source
```

Also assert only `PumpCheckComparison` owns analysis, GET source has no claim/analyze call, POST uses `g.mobile_user.id`, and serialization excludes internal IDs/images.

- [ ] **Step 3: Run tests and verify RED**

Run: `python -m pytest -q tests/test_mobile_pump_check_comparison_api.py tests/test_mobile_pump_check_comparison_architecture.py`

Expected: FAIL because routes are absent.

- [ ] **Step 4: Implement POST/GET and error mapping**

```python
@bp.post('/pump-check-comparisons')
@require_mobile_auth
@limiter.limit(
    BEDROCK_RATELIMIT, key_func=lambda: str(g.mobile_user.id))
@mobile_ai_concurrency_gate(
    'PUMP_CHECK_COMPARISON_PROVIDER_BUSY',
    'Pump Check comparison analysis is busy.')
def create_pump_check_comparison():
    key = meal_idempotency.read_idempotency_key()
    if key is None:
        return mobile_error(
            'INVALID_IDEMPOTENCY_KEY',
            'A valid Idempotency-Key is required.', 400, False)
    try:
        command = service.create_command(request.get_json(silent=True))
        row, created = service.create_or_replay(
            g.mobile_user.id, key, command)
        return _response(row, 201 if created else 200)
    except service.InvalidCommand:
        return mobile_error(
            'INVALID_PUMP_CHECK_COMPARISON',
            'Invalid Pump Check comparison input.', 400, False)
```

Map all remaining exceptions exactly to the approved taxonomy without details. Import the route module at the bottom of `mobile_api.py` beside `mobile_pump_checks`. GET only calls `get_owned` and `_response`.

- [ ] **Step 5: Run API and adjacent regressions**

Run: `python -m pytest -q tests/test_mobile_pump_check_comparison_api.py tests/test_mobile_pump_check_comparison_architecture.py tests/test_mobile_pump_check_api.py tests/test_mobile_pump_check_architecture.py tests/test_mobile_auth_api.py tests/test_write_rate_limits.py`

Expected: PASS and PR1 routes remain unchanged.

- [ ] **Step 6: Commit**

```bash
git add app/blueprints/mobile_pump_check_comparisons.py app/blueprints/mobile_api.py tests/test_mobile_pump_check_comparison_api.py tests/test_mobile_pump_check_comparison_architecture.py
git commit -m 'feat(api): expose owner-only pump check comparisons'
```

---

### Task 7: Explicit account-erasure ordering

**Files:**
- Modify: `app/cli.py:96-195`
- Modify: `tests/test_cascade_delete.py`

**Interfaces:**
- Consumes: Task 3 models/FKs and the existing explicit user-data deletion command.
- Produces: ledger deletion before comparison deletion before source Pump Checks.

- [ ] **Step 1: Write failing erasure regression**

```python
def test_delete_user_removes_comparison_records_without_other_sources(
        owner, other_user, seeded_comparison):
    comparison, request, other_check = seeded_comparison
    _invoke_delete_user(owner.id)
    assert db.session.get(
        PumpCheckComparisonRequest, request.id) is None
    assert db.session.get(
        PumpCheckComparison, comparison.id) is None
    assert db.session.get(PumpCheck, other_check.id) is not None
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest -q tests/test_cascade_delete.py`

Expected: FAIL because the explicit CLI registry/order omits both new models.

- [ ] **Step 3: Implement FK-safe explicit ordering**

Import both models in `app/cli.py`; delete owner-ledger rows first, owner comparison rows second, then current Pump Check children and Pump Checks. Keep FK cascades as defense in depth.

- [ ] **Step 4: Verify**

Run: `python -m pytest -q tests/test_cascade_delete.py tests/test_pump_check_comparison_migration.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/cli.py tests/test_cascade_delete.py
git commit -m 'fix(privacy): erase pump check comparison records'
```

---

### Task 8: Real PostgreSQL convergence and generation races

**Files:**
- Create: `tests/test_mobile_pump_check_comparison_pg.py`
- Modify: `.github/workflows/ci.yml:116-124`
- Modify: `tests/test_mobile_pump_check_comparison_architecture.py`

**Interfaces:**
- Consumes: Task 5 service, PostgreSQL 16 CI service, `FITX_PG_CONCURRENCY_TEST`, and `PG_TEST_DATABASE_URL`.
- Produces: real-database uniqueness/fencing proof and CI enforcement.

- [ ] **Step 1: Write opt-in PostgreSQL race tests**

```python
pytestmark = pytest.mark.pg_concurrency


def test_same_key_same_command_converges_once(pg_comparison_app):
    outcomes = _race([
        contender(owner_id, 'comparison-key-0001', command),
        contender(owner_id, 'comparison-key-0001', command),
    ])
    assert {outcome[0] for outcome in outcomes} == {'ok'}
    assert len({outcome[1] for outcome in outcomes}) == 1
    assert counts['bedrock'] == 1


def test_different_keys_same_pair_converge(pg_comparison_app):
    outcomes = _race([
        contender(owner_id, 'comparison-key-0001', command),
        contender(owner_id, 'comparison-key-0002', command),
    ])
    assert len({outcome[1] for outcome in outcomes}) == 1
    assert PumpCheckComparison.query.count() == 1
    assert PumpCheckComparisonRequest.query.count() == 2


def test_stale_generation_cannot_overwrite_newer_result(
        pg_comparison_app):
    old_attempt, new_attempt = _race_stale_reclaim(comparison_id)
    assert old_attempt < new_attempt
    assert _finalize_success(
        comparison_id, owner_id, old_attempt,
        'limited', _old_analysis()) is False
    assert _stored_analysis(comparison_id) == _new_analysis()
```

Add cross-user identical-key independence and same-key/different-command one-winner conflict. Use a barrier, separate Flask contexts/sessions, a strictly disposable URL, and 30-second join assertions.

- [ ] **Step 2: Verify safe local skip**

Run: `python -m pytest -q tests/test_mobile_pump_check_comparison_pg.py`

Expected without opt-in environment: one module-level skip and no database mutation.

- [ ] **Step 3: Add the module to PostgreSQL CI**

Append `tests/test_mobile_pump_check_comparison_pg.py` to the existing `mobile-pg-concurrency` command. Extend the architecture test to require that exact path.

- [ ] **Step 4: Run real PostgreSQL proof when available**

```powershell
$env:FITX_PG_CONCURRENCY_TEST='1'
$env:PG_TEST_DATABASE_URL='postgresql://postgres:postgres@localhost:5432/fitx_mobile_race'
python -m pytest -m pg_concurrency -q tests/test_mobile_pump_check_pg.py tests/test_mobile_pump_check_comparison_pg.py
```

Expected: PASS. If disposable PostgreSQL 16 is unavailable, record the exact failure and reserve `READY WITH CONDITIONS`; SQLite is not a substitute.

- [ ] **Step 5: Verify workflow guards**

Run: `python -m pytest -q tests/test_mobile_pump_check_comparison_architecture.py tests/test_mobile_pump_check_architecture.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/test_mobile_pump_check_comparison_pg.py tests/test_mobile_pump_check_comparison_architecture.py .github/workflows/ci.yml
git commit -m 'test: cover pump comparison postgres races'
```

---

### Task 9: Documentation, authoritative verification, and report

**Files:**
- Modify: `docs/PUMP_CHECK.md`
- Modify: `docs/PUMP_CHECK_DESIGN.md`
- Modify: `docs/MOBILE_PUMP_CHECK.md`
- Modify: `docs/handoff.md`
- Modify: `docs/superpowers/plans/2026-08-14-pump-check-comparison-intelligence.md`
- Create: `docs/reports/2026-08-14-sprint10-pr3-pump-check-comparison-implementation.md`
- Modify: `tests/test_mobile_pump_check_comparison_architecture.py`

**Interfaces:**
- Consumes: Tasks 1-8 and the exact 27-section contract in `cf-sprint10-pr3.txt`.
- Produces: canonical docs, checked plan, local report, verification evidence, and a clean committed worktree.

- [ ] **Step 1: Write a failing documentation guard**

```python
def test_canonical_docs_name_version_routes_and_privacy_contract():
    docs = '\n'.join(
        Path(path).read_text(encoding='utf-8')
        for path in DOC_PATHS)
    for required in (
        'pump-check-comparison-analysis/v1',
        'POST /api/v1/pump-check-comparisons',
        'GET /api/v1/pump-check-comparisons/<comparison_id>',
        'baseline_pump_check_id',
        'current_pump_check_id',
        'not_comparable',
    ):
        assert required in docs
```

- [ ] **Step 2: Run guard and verify RED**

Run: `python -m pytest -q tests/test_mobile_pump_check_comparison_architecture.py`

Expected: FAIL because canonical docs do not yet describe PR3.

- [ ] **Step 3: Update canonical docs and handoff**

Document exact request/response/error schemas, privacy, directionality, eligibility, source-quality ceiling, no image URLs, image normalization, Bedrock-only behavior, uniqueness/idempotency, leases, GET read-only behavior, and exclusions. State verbatim in `docs/handoff.md` that commit `a69c958 test: isolate audit app database configuration` fixed a deterministic pre-existing test-harness isolation defect discovered during mandatory baseline validation before any Sprint 10 PR3 production changes.

- [ ] **Step 4: Run focused PR3 and adjacent suites**

```powershell
python -m pytest -q tests/test_vision_images.py tests/test_pump_check_analysis.py tests/test_pump_check_comparison_analysis.py tests/test_bedrock_comparison_adapter.py tests/test_pump_check_comparison_identity.py tests/test_pump_check_comparison_migration.py tests/test_pump_check_comparison_service.py tests/test_pump_check_comparison_lifecycle.py tests/test_mobile_pump_check_comparison_api.py tests/test_mobile_pump_check_comparison_architecture.py tests/test_cascade_delete.py tests/test_mobile_pump_check_api.py tests/test_mobile_pump_check_architecture.py
```

Expected: PASS with zero failures.

- [ ] **Step 5: Verify Alembic head and PostgreSQL drift**

Run: `flask --app starter db heads`

Expected: exactly `fa1b2c3d4e5f`.

Against disposable PostgreSQL 16 with CI environment values, run `flask --app starter db upgrade` and `flask --app starter db check`.

Expected: both exit zero and no model/migration drift.

- [ ] **Step 6: Re-run mandatory harness regression sequence**

Run: `python -m pytest -q tests/test_frontend_audit_app.py tests/test_gamification_routes.py::test_leaderboard_orders_by_xp_then_streak`

Run: `python -m pytest -q tests/test_gamification_routes.py::test_leaderboard_orders_by_xp_then_streak`

Expected: both PASS, preserving `a69c958`.

- [ ] **Step 7: Run all authoritative baseline shards**

Regenerate and execute the same deterministic modulo-8 file shards:

```powershell
$testFiles = @(
    Get-ChildItem tests -Recurse -Filter 'test_*.py' |
    Sort-Object FullName |
    ForEach-Object { Resolve-Path -Relative $_.FullName }
)
foreach ($shardNumber in 1..8) {
    $shardIndex = $shardNumber - 1
    $shardFiles = @(
        for ($index = $shardIndex;
             $index -lt $testFiles.Count;
             $index += 8) {
            $testFiles[$index]
        }
    )
    python -m pytest -q @shardFiles
    if ($LASTEXITCODE -ne 0) {
        throw "authoritative shard $shardNumber failed"
    }
}
python -m pytest --collect-only -q
```

Record each shard's passed/skipped/deselected totals and collection totals; require zero failures.

- [ ] **Step 8: Run the complete real PostgreSQL race suite**

```powershell
$env:FITX_PG_CONCURRENCY_TEST='1'
$env:PG_TEST_DATABASE_URL='postgresql://postgres:postgres@localhost:5432/fitx_mobile_race'
python -m pytest -m pg_concurrency -q tests/test_mobile_auth_pg.py tests/test_mobile_log_food_pg.py tests/test_mobile_diary_mutation_pg.py tests/test_mobile_pump_check_pg.py tests/test_mobile_pump_check_comparison_pg.py
```

Expected: PASS. Without reachable PostgreSQL, use only `READY WITH CONDITIONS`.

- [ ] **Step 9: Write and audit the exact 27-section report**

Re-open `C:\Users\yusuf\OneDrive\Masaüstü\cf-sprint10-pr3.txt`, copy its 27 headings verbatim and in order, and fill them with file/commit/test evidence. Separate `a69c958` from PR3 commits; include normalization, provider payload, idempotency/races, migration/drift, exclusions, no Flutter/PR4 work, and one exact verdict: `READY FOR REVIEW`, `READY WITH CONDITIONS`, or `NOT READY`.

- [ ] **Step 10: Run final scope/diff checks**

Run: `git diff --check origin/main...HEAD`

Run: `git diff --name-only origin/main...HEAD`

Run: `git status --short`

Expected: no whitespace errors; backend/tests/docs/CI only; no Flutter, history/timeline, or PR4 implementation.

- [ ] **Step 11: Commit docs and report**

```bash
git add docs/PUMP_CHECK.md docs/PUMP_CHECK_DESIGN.md docs/MOBILE_PUMP_CHECK.md docs/handoff.md docs/superpowers/plans/2026-08-14-pump-check-comparison-intelligence.md docs/reports/2026-08-14-sprint10-pr3-pump-check-comparison-implementation.md tests/test_mobile_pump_check_comparison_architecture.py
git commit -m 'docs(api): document pump check comparisons'
```

- [ ] **Step 12: Verify committed worktree**

Run: `git status --short --branch`

Run: `git log --oneline origin/main..HEAD`

Expected: clean branch with `a69c958` isolated before design/plan/implementation commits. Do not push or open a PR.
