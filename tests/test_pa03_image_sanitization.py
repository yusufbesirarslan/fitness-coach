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


def test_s09_animation_detection_does_not_enumerate_entire_gif(monkeypatch):
    from PIL import GifImagePlugin
    raw = tagged("GIF")
    def forbidden(image):
        pytest.fail("full frame enumeration is not a bounded animation probe")
    monkeypatch.setattr(GifImagePlugin.GifImageFile, "n_frames", property(forbidden))
    result = core.sanitize_image(raw)
    assert_clean(result.data, result.media_type)


GRAY16_SAMPLES = [0, 1, 128, 129, 255, 256, 257, 4096, 16384,
                  32767, 32768, 49152, 65000, 65534, 65535]
GRAY8_EXPECTED = [0, 0, 0, 1, 1, 1, 1, 16, 64, 127, 128, 191, 253, 255, 255]


def grayscale16(values=GRAY16_SAMPLES, size=None, orientation=1, transparency=None):
    image = Image.new("I;16", size or (len(values), 1))
    image.putdata(values)
    exif = Image.Exif()
    exif[274] = orientation
    exif[271] = MARKER.decode()
    exif[34853] = {1: "N", 2: (41.0, 2.0, 3.0)}
    info = PngImagePlugin.PngInfo()
    info.add_text("Location", MARKER.decode())
    info.add_itxt("XML:com.adobe.xmp", MARKER.decode())
    options = {} if transparency is None else {"transparency": transparency}
    output = BytesIO()
    image.save(output, "PNG", exif=exif, pnginfo=info, icc_profile=MARKER, **options)
    raw = output.getvalue()
    with Image.open(BytesIO(raw)) as decoded:
        assert decoded.mode in ("I;16", "I")
        assert list(decoded.get_flattened_data()) == list(values)
        assert decoded.getexif().get_ifd(34853)[2]
    return raw


def assert_gray_pixels(data, expected, mode="RGB"):
    with Image.open(BytesIO(data)) as image:
        image.load()
        assert image.mode == mode
        actual = list(image.get_flattened_data())
        assert actual == [(v, v, v) for v in expected]
        return [pixel[0] for pixel in actual]


def test_grayscale16_reviewer_reproduction_and_exact_rounding():
    result = core.sanitize_image(grayscale16([0, 16384, 32768, 65535]))
    assert_gray_pixels(result.data, [0, 64, 128, 255])
    result = core.sanitize_image(grayscale16())
    actual = assert_gray_pixels(result.data, GRAY8_EXPECTED)
    assert actual == sorted(actual)
    assert len(set(actual)) >= 9
    assert_clean(result.data, result.media_type)


def test_grayscale16_full_range_gradient():
    # Independent integer arithmetic checks every possible source sample,
    # including every rounding boundary; no tolerance or float oracle.
    raw = grayscale16(range(65536), (256, 256))
    result = core.sanitize_image(raw)
    actual = assert_gray_pixels(result.data, [(v * 255 + 32767) // 65535 for v in range(65536)])
    assert set(actual) == set(range(256))
    assert actual == sorted(actual)
    assert actual.count(255) == 129
    assert_clean(result.data, result.media_type)


@pytest.mark.parametrize("orientation,expected,size", [
    (1, [0, 51, 102, 153, 204, 255], (3, 2)),
    (2, [102, 51, 0, 255, 204, 153], (3, 2)),
    (3, [255, 204, 153, 102, 51, 0], (3, 2)),
    (4, [153, 204, 255, 0, 51, 102], (3, 2)),
    (5, [0, 153, 51, 204, 102, 255], (2, 3)),
    (6, [153, 0, 204, 51, 255, 102], (2, 3)),
    (7, [255, 102, 204, 51, 153, 0], (2, 3)),
    (8, [102, 255, 51, 204, 0, 153], (2, 3)),
])
def test_grayscale16_orientation_and_metadata(orientation, expected, size):
    raw = grayscale16([0, 13107, 26214, 39321, 52428, 65535], (3, 2), orientation)
    result = core.sanitize_image(raw)
    assert (result.width, result.height) == size
    assert_gray_pixels(result.data, expected)
    assert_clean(result.data, result.media_type)


def test_grayscale16_transparency_before_quantization():
    result = core.sanitize_image(grayscale16([32767, 32768, 32769], transparency=32768))
    with Image.open(BytesIO(result.data)) as output:
        assert list(output.get_flattened_data()) == [(127, 127, 127, 255),
                                                   (128, 128, 128, 0), (128, 128, 128, 255)]
    assert_clean(result.data, result.media_type)


@pytest.mark.parametrize("mode,values", [
    ("L", [0, 1, 64, 128, 254, 255]),
    ("RGB", [(0, 1, 2), (64, 128, 255)]),
    ("RGBA", [(0, 1, 2, 0), (64, 128, 255, 128)]),
])
def test_grayscale16_fix_preserves_8bit_behavior(mode, values):
    image = Image.new(mode, (len(values), 1))
    image.putdata(values)
    raw = BytesIO()
    image.save(raw, "PNG")
    result = core.sanitize_image(raw.getvalue())
    with Image.open(BytesIO(result.data)) as output:
        assert output.mode == ("RGB" if mode == "L" else mode)
        assert list(output.get_flattened_data()) == ([(v, v, v) for v in values] if mode == "L" else values)


def test_grayscale16_actual_storage_and_vision_sinks(monkeypatch):
    calls = []
    class Sink:
        def put_object(self, **kwargs):
            calls.append(kwargs)
    monkeypatch.setattr(s3_helper, "_get_client", lambda: Sink())
    raw = grayscale16()
    key = s3_helper.upload_image(raw, "image/jpeg", "pump-checks", 7)
    assert key.startswith("pump-checks/7/") and key.endswith(".png")
    assert calls[0]["ContentType"] == "image/png"
    assert_gray_pixels(calls[0]["Body"], GRAY8_EXPECTED)
    assert_clean(calls[0]["Body"], "image/png")
    data, mime = vision_images.prepare_image_for_vision(raw, "image/jpeg")
    assert mime == "image/png" and len(data) <= 1_500_000
    assert_gray_pixels(data, GRAY8_EXPECTED)
    assert_clean(data, mime)
    data, mime = vision_images.prepare_image_for_vision(grayscale16(range(2048), (2048, 1)))
    with Image.open(BytesIO(data)) as output:
        assert output.size == (1600, 1)
    assert mime == "image/png" and len(data) <= 1_500_000


@pytest.mark.parametrize("kind", ["truncated", "tiff-float", "tiff-integer"])
def test_grayscale16_malformed_or_unsupported_no_transfer(monkeypatch, kind):
    if kind == "truncated":
        raw = grayscale16()[:80]
    else:
        output = BytesIO()
        Image.new("F" if kind == "tiff-float" else "I", (4, 1)).save(output, "TIFF")
        raw = output.getvalue()
    def forbidden():
        pytest.fail("invalid image must fail before S3 client acquisition")
    monkeypatch.setattr(s3_helper, "_get_client", forbidden)
    with pytest.raises(core.ImageSanitizationError):
        core.sanitize_image(raw)
    with pytest.raises(s3_helper.S3Error, match="image_sanitization_failed"):
        s3_helper.upload_image(raw)
    seen = []
    with pytest.raises(vision_images.ImagePreparationError):
        analyze_image(raw, "image/png", {}, provider=lambda *a, **k: seen.append(a))
    assert seen == []


@pytest.mark.parametrize("mode,value,fmt", [("I", -1, "PNG"), ("I", 65536, "PNG"),
                                            ("F", 0.5, "PNG"), ("I;16B", 1, "PNG"),
                                            ("I", 128, "TIFF")])
def test_grayscale16_unrecognized_sample_domain_fails_closed(mode, value, fmt):
    with Image.new(mode, (1, 1), value) as image:
        with pytest.raises(core.ImageSanitizationError, match="image_mode_unsupported"):
            core._grayscale16(image, fmt)


def test_grayscale16_legacy_integer_decoder_representation():
    with Image.new("I", (len(GRAY16_SAMPLES), 1)) as image:
        image.putdata(GRAY16_SAMPLES)
        with core._grayscale16(image, "PNG") as converted:
            assert list(converted.get_flattened_data()) == [(v, v, v) for v in GRAY8_EXPECTED]


def test_grayscale16_resource_limits_preserved(monkeypatch):
    raw = grayscale16()
    with pytest.raises(core.ImageResourceError, match="image_output_limit"):
        core.sanitize_image(raw, max_bytes=10)
    monkeypatch.setattr(core, "MAX_INPUT_BYTES", len(raw) - 1)
    with pytest.raises(core.ImageResourceError, match="image_input_limit"):
        core.sanitize_image(raw)
    monkeypatch.setattr(core, "MAX_INPUT_BYTES", len(raw))
    monkeypatch.setattr(core, "MAX_PIXELS", 1)
    with pytest.raises(core.ImageResourceError, match="image_pixel_limit"):
        core.sanitize_image(raw)
