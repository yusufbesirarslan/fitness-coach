"""PA03-S01..S15: synthetic-only encoded-byte and decoded-metadata checks."""
from io import BytesIO
import json
import traceback

import pytest
from PIL import Image, ImageOps, PngImagePlugin

import s3_helper
from app.services import image_sanitization as core, vision_images
from app.services.mobile_pump_checks.analysis import analyze_image

MARKER = b"SYNTHETIC_PRIVATE_DEVICE_LOCATION_424242"


def tagged(fmt="JPEG", orientation=6, alpha=False):
    image = Image.new("RGBA" if alpha else "RGB", (24, 16), "red")
    image.paste((0, 0, 255, 0) if alpha else "blue", (0, 0, 12, 8))
    exif = Image.Exif()
    exif[274] = orientation
    exif[271] = MARKER.decode()
    exif[272] = "Synthetic camera"
    exif[306] = "2001:02:03 04:05:06"
    exif[34853] = {1: "N", 2: (41.0, 2.0, 3.0), 3: "E", 4: (29.0, 4.0, 5.0)}
    output = BytesIO()
    if fmt == "PNG":
        info = PngImagePlugin.PngInfo()
        info.add_text("Location", MARKER.decode())
        info.add_itxt("XML:com.adobe.xmp", MARKER.decode())
        image.save(output, fmt, pnginfo=info, exif=exif, icc_profile=MARKER)
    elif fmt == "WEBP":
        image.save(output, fmt, exif=exif, xmp=MARKER, icc_profile=MARKER, lossless=True)
    elif fmt == "GIF":
        image.save(output, fmt, comment=MARKER)
    else:
        image.save(output, fmt, exif=exif, comment=MARKER, icc_profile=MARKER)
    return output.getvalue()


def assert_clean(data, mime):
    assert MARKER not in data  # complementary to structured parsing below
    with Image.open(BytesIO(data)) as image:
        image.load()
        assert not image.getexif()
        assert not image.info.get("exif")
        assert not image.info.get("xmp")
        assert not image.info.get("icc_profile")
        assert not image.info.get("comment")
        assert not getattr(image, "text", {})
        assert image.format == {"image/jpeg": "JPEG", "image/png": "PNG"}[mime]
        assert image.n_frames == 1 if hasattr(image, "n_frames") else True
        assert set(image.info) <= {"jfif", "jfif_version", "jfif_unit", "jfif_density"}
    # Inspect actual container fields, independently of Pillow's metadata map.
    if mime == "image/png":
        pos, chunks = 8, []
        while pos < len(data):
            length = int.from_bytes(data[pos:pos+4], "big")
            chunks.append(data[pos+4:pos+8])
            pos += length + 12
        assert set(chunks) <= {b"IHDR", b"IDAT", b"IEND"}
        assert pos == len(data)
    else:
        pos = 2
        while data[pos:pos+2] != b"\xff\xda":
            assert data[pos] == 255
            marker = data[pos+1]
            length = int.from_bytes(data[pos+2:pos+4], "big")
            assert marker not in {0xe1, 0xe2, 0xed, 0xfe}  # EXIF/XMP, ICC, IPTC, comment
            assert not (0xe0 <= marker <= 0xef) or marker == 0xe0
            pos += 2 + length
        assert data.endswith(b"\xff\xd9")


@pytest.mark.parametrize("fmt,mime", [("JPEG", "image/jpeg"), ("PNG", "image/png"),
                                       ("WEBP", "image/png"), ("GIF", "image/png")])
def test_s01_s03_s04_s05_s06_s10_metadata_and_repeated_sanitization(fmt, mime):
    raw = tagged(fmt)
    with Image.open(BytesIO(raw)) as source:
        assert source.getexif() or source.info.get("comment")
        if fmt != "GIF":
            assert source.getexif().get_ifd(34853)[2]  # fixture truly has GPS IFD
    result = core.sanitize_image(raw)
    assert result.data is not raw
    assert result.data != raw
    assert result.media_type == mime
    assert_clean(result.data, mime)
    again = core.sanitize_image(result.data)
    assert_clean(again.data, mime)
    assert (again.width, again.height) == (result.width, result.height)
    assert MARKER.decode() not in repr(result)


@pytest.mark.parametrize("orientation", range(1, 9))
def test_s02_all_orientation_states(orientation):
    raw = tagged("PNG", orientation)
    with Image.open(BytesIO(raw)) as source:
        expected = ImageOps.exif_transpose(source).convert("RGB")
        result = core.sanitize_image(raw)
        with Image.open(BytesIO(result.data)) as output:
            assert output.size == expected.size
            assert output.convert("RGB").tobytes() == expected.tobytes()
            assert not output.getexif()


@pytest.mark.parametrize("fmt", ["PNG", "WEBP", "GIF"])
def test_s06_animation_explicitly_rejected(fmt):
    output = BytesIO()
    Image.new("RGB", (8, 8), "red").save(output, fmt, save_all=True,
        append_images=[Image.new("RGB", (8, 8), "blue")], duration=100, loop=0)
    with pytest.raises(core.ImageSanitizationError, match="image_animation_unsupported"):
        core.sanitize_image(output.getvalue())


def test_s07_mime_mismatch_and_s15_alpha():
    raw = tagged("PNG", alpha=True)
    data, mime = vision_images.prepare_image_for_vision(raw, "image/jpeg")
    assert mime == "image/png"
    assert_clean(data, mime)
    with Image.open(BytesIO(data)) as output:
        assert output.mode == "RGBA"
        assert 0 in output.getchannel("A").getextrema()


@pytest.mark.parametrize("raw", [b"", b"not an image" + MARKER, tagged()[:100], tagged()[:-30]])
def test_s08_corrupt_input_no_fallback(raw):
    with pytest.raises(core.ImageSanitizationError):
        core.sanitize_image(raw)
    with pytest.raises(vision_images.ImagePreparationError):
        vision_images.prepare_image_for_vision(raw)


def test_s09_resource_budgets(monkeypatch):
    raw = tagged()
    monkeypatch.setattr(core, "MAX_INPUT_BYTES", len(raw)-1)
    with pytest.raises(core.ImageResourceError, match="image_input_limit"):
        core.sanitize_image(raw)
    monkeypatch.setattr(core, "MAX_INPUT_BYTES", len(raw))
    monkeypatch.setattr(core, "MAX_PIXELS", 10)
    with pytest.raises(core.ImageResourceError, match="image_pixel_limit"):
        core.sanitize_image(raw)
    monkeypatch.setattr(core, "MAX_PIXELS", 40_000_000)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)
    with pytest.raises(core.ImageResourceError, match="image_decode_limit"):
        core.sanitize_image(raw)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 200)
    with pytest.raises(core.ImageResourceError, match="image_decode_limit"):
        core.sanitize_image(raw)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 40_000_000)
    with pytest.raises(core.ImageResourceError, match="image_output_limit"):
        core.sanitize_image(raw, max_bytes=10)
    with pytest.raises(vision_images.ImagePreparationError):
        vision_images.prepare_image_for_vision(raw, max_bytes=10)


def test_s11_storage_bytes_type_key_owner_encryption_and_zero_failed_writes(monkeypatch):
    calls = []
    class Fake:
        def put_object(self, **kwargs):
            calls.append(kwargs)
    monkeypatch.setattr(s3_helper, "_get_client", lambda: Fake())
    raw = tagged("WEBP")
    key = s3_helper.upload_image(raw, "image/jpeg", "pump-checks", 7)
    assert key.startswith("pump-checks/7/") and key.endswith(".png")
    assert s3_helper.key_belongs_to_user(key, 7)
    assert not s3_helper.key_belongs_to_user(key, 8)
    assert calls[0]["ServerSideEncryption"] == "AES256"
    assert calls[0]["ContentType"] == "image/png"
    assert_clean(calls[0]["Body"], calls[0]["ContentType"])
    with pytest.raises(s3_helper.S3Error, match="image_sanitization_failed"):
        s3_helper.upload_image(MARKER)
    assert len(calls) == 1


def test_s12_provider_payload_and_failed_input_zero_calls():
    seen = []
    def provider(data, mime, prompt, **kwargs):
        seen.append((data, mime))
        assert_clean(data, mime)
        return json.dumps(dict(summary="Visible framing.", observations=[], strengths=[],
            focus_areas=[], limitations=[], next_check_guidance="Keep framing consistent.", quality="sufficient"))
    analyze_image(tagged(), "image/png", {}, provider=provider)
    with pytest.raises(vision_images.ImagePreparationError):
        analyze_image(MARKER, "image/jpeg", {}, provider=provider)
    assert len(seen) == 1


def test_s13_old_passthrough_mutation_is_detected(monkeypatch):
    raw = tagged()
    monkeypatch.setattr(vision_images, "prepare_image_for_vision", lambda raw, mime: (raw, mime))
    data, mime = vision_images.prepare_image_for_vision(raw, "image/jpeg")
    with pytest.raises(AssertionError):
        assert_clean(data, mime)


def test_s14_private_parser_errors_and_warnings_never_escape(monkeypatch, caplog):
    def unsafe(*args, **kwargs):
        raise ValueError(MARKER.decode())
    monkeypatch.setattr(core, "_decode_pixels", unsafe)
    with pytest.raises(core.ImageSanitizationError) as caught:
        core.sanitize_image(tagged())
    rendered = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert MARKER.decode() not in rendered + caplog.text
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("fmt", ["PNG", "WEBP", "GIF"])
def test_s08_truncated_supported_containers_fail(fmt):
    raw = tagged(fmt)
    with pytest.raises(core.ImageSanitizationError):
        core.sanitize_image(raw[:len(raw)//2])


def test_s09_excessive_dimension_before_pixel_decode(monkeypatch):
    monkeypatch.setattr(core, "MAX_DIMENSION", 20)
    with pytest.raises(core.ImageResourceError, match="image_pixel_limit"):
        core.sanitize_image(tagged())


def test_s08_permissive_global_decoder_setting_cannot_weaken_boundary(monkeypatch):
    from PIL import ImageFile
    monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", True)
    with pytest.raises(core.ImageSanitizationError, match="image_decoder_unsafe"):
        core.sanitize_image(tagged())


def test_s11_output_verification_failure_prevents_storage(monkeypatch):
    def reject(*args):
        raise core.ImageSanitizationError("image_output_metadata")
    def forbidden():
        pytest.fail("S3 client must not be acquired after sanitization failure")
    monkeypatch.setattr(core, "verify_output", reject)
    monkeypatch.setattr(s3_helper, "_get_client", forbidden)
    with pytest.raises(s3_helper.S3Error, match="image_sanitization_failed"):
        s3_helper.upload_image(tagged())


def test_s14_private_parser_warning_never_escapes(monkeypatch, caplog):
    import warnings
    def unsafe(*args):
        warnings.warn(MARKER.decode(), UserWarning)
    monkeypatch.setattr(core, "_decode_pixels", unsafe)
    with pytest.raises(core.ImageSanitizationError) as caught:
        core.sanitize_image(tagged())
    rendered = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert MARKER.decode() not in rendered + caplog.text


@pytest.mark.parametrize("fmt", ["WEBP", "GIF"])
def test_s15_transparent_format_normalization(fmt):
    result = core.sanitize_image(tagged(fmt, alpha=True))
    assert_clean(result.data, result.media_type)
    with Image.open(BytesIO(result.data)) as image:
        assert image.mode == "RGBA"
        assert image.getchannel("A").getextrema()[0] == 0


def test_s09_transparent_output_limit_has_no_lossy_fallback():
    with pytest.raises(vision_images.ImagePreparationError, match="image_output_limit"):
        vision_images.prepare_image_for_vision(tagged("PNG", alpha=True), max_bytes=10)
