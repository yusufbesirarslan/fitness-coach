"""Fail-closed raster normalization. Source metadata never enters an encoder.

Static JPEG stays JPEG; PNG/WebP/GIF become PNG (including alpha). Animated
and multi-frame inputs are refused. Uploaded profiles are omitted; unprofiled
RGB values are treated as sRGB. No caller MIME declaration is authoritative.
"""
from dataclasses import dataclass, field
import io
import warnings

from PIL import Image, ImageFile, ImageOps

MAX_INPUT_BYTES = 20_000_000  # transport ceiling; ingress routes retain tighter caps
MAX_OUTPUT_BYTES = 20_000_000
MAX_PIXELS = 40_000_000
MAX_DIMENSION = 16_384
FORMATS = ("JPEG", "PNG", "WEBP", "GIF")


class ImageSanitizationError(ValueError):
    """Only fixed error codes may cross this boundary."""


class ImageResourceError(ImageSanitizationError):
    """A byte, dimension, pixel or encoded-output budget was exceeded."""


@dataclass(frozen=True)
class SanitizedImage:
    data: bytes = field(repr=False)
    media_type: str
    width: int
    height: int


class _BoundedBuffer(io.BytesIO):
    def __init__(self, limit):
        super().__init__()
        self.limit = limit

    def write(self, data):
        if self.tell() + len(data) > self.limit:
            raise ImageResourceError("image_output_limit")
        return super().write(data)


def encode_pixels(image, *, format, max_bytes, quality=90):
    """Internal encoder: callers must supply a fresh metadata-free pixel image."""
    with _BoundedBuffer(max_bytes) as output:
        options = {"quality": quality} if format == "JPEG" else {}
        image.save(output, format=format, **options)
        return output.getvalue()


def _dimensions(image):
    w, h = image.size
    if min(w, h) <= 0 or max(w, h) > MAX_DIMENSION or w * h > MAX_PIXELS:
        raise ImageResourceError("image_pixel_limit")


def _grayscale16(image, source_format):
    """Reduce unsigned PNG grayscale to 8 bits, rounding v/257 to nearest.

    This is precision loss, not contrast enhancement. Apply transparency in
    the original sample domain so quantization cannot erase a color key.
    """
    if source_format != "PNG" or image.mode not in ("I", "I;16"):
        raise ImageSanitizationError("image_mode_unsupported")
    with image.convert("I") as integer:
        low, high = integer.getextrema()
        if low < 0 or high > 65535:
            raise ImageSanitizationError("image_mode_unsupported")
        # Affine point transform uses no per-pixel Python buffer or LUT.
        with integer.point(lambda v: (v + 128) / 257) as scaled:
            with scaled.convert("L") as gray:
                converted = gray.convert("RGBA" if "transparency" in image.info else "RGB")
        if "transparency" in image.info:
            key = image.info["transparency"]
            if type(key) is not int or not 0 <= key <= 65535:
                converted.close()
                raise ImageSanitizationError("image_mode_unsupported")
            # Fixed 65536-entry table, independent of image dimensions.
            with integer.point([0 if v == key else 255 for v in range(65536)], "L") as alpha:
                converted.putalpha(alpha)
        return converted


def _decode_pixels(raw):
    # Do not mutate Pillow's process-global bomb or truncated-file settings.
    # Explicit caps apply even if another legacy helper changed its bomb limit.
    if ImageFile.LOAD_TRUNCATED_IMAGES:
        raise ImageSanitizationError("image_decoder_unsafe")
    with Image.open(io.BytesIO(raw), formats=FORMATS) as probe:
        _dimensions(probe)
        probe.verify()
    with Image.open(io.BytesIO(raw), formats=FORMATS) as source:
        _dimensions(source)
        # Seeking only frame 1 avoids GIF n_frames scanning an attacker-sized
        # frame sequence just to decide whether animation is supported.
        try:
            source.seek(1)
        except EOFError:
            source.seek(0)
        else:
            raise ImageSanitizationError("image_animation_unsupported")
        source.load()  # verify() alone does not validate compressed pixels
        orientation = source.getexif().get(274, 1)
        if type(orientation) is not int or orientation not in range(1, 9):
            raise ImageSanitizationError("image_orientation_invalid")
        fmt = "JPEG" if source.format == "JPEG" else "PNG"
        oriented = ImageOps.exif_transpose(source)
        mode = "RGBA" if "A" in oriented.getbands() or "transparency" in oriented.info else "RGB"
        if oriented.mode.startswith("I") or oriented.mode == "F":
            converted = _grayscale16(oriented, source.format)
        else:
            converted = oriented.convert(mode)
        # A new image, rather than copy(), drops info, EXIF and plugin state.
        pixels = Image.new(mode, converted.size)
        pixels.paste(converted)
        converted.close()
        return pixels, fmt


def verify_output(data, media_type, size):
    """Reopen serialized bytes and require only approved structural metadata."""
    expected = {"image/jpeg": "JPEG", "image/png": "PNG"}[media_type]
    with Image.open(io.BytesIO(data), formats=[expected]) as output:
        output.load()
        if output.size != size or output.getexif() or getattr(output, "n_frames", 1) != 1:
            raise ImageSanitizationError("image_output_invalid")
        allowed = {"jfif", "jfif_version", "jfif_unit", "jfif_density"} if expected == "JPEG" else set()
        if set(output.info) - allowed:
            raise ImageSanitizationError("image_output_metadata")


def sanitize_image(raw: bytes, *, max_bytes=MAX_OUTPUT_BYTES,
                   max_dimension=None, jpeg_quality=90) -> SanitizedImage:
    """Decode, orient, isolate pixels, encode and verify before release.

    MIME mismatch is handled by ignoring the declaration at sink adapters and
    returning the actual output encoding. This function takes no MIME hint.
    """
    if not isinstance(raw, bytes) or not raw:
        raise ImageSanitizationError("image_input_invalid")
    if len(raw) > MAX_INPUT_BYTES:
        raise ImageResourceError("image_input_limit")
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ImageResourceError("image_output_limit")
    max_bytes = min(max_bytes, MAX_OUTPUT_BYTES)
    try:
        with warnings.catch_warnings():
            # Parser warnings may contain private source text; never emit them.
            warnings.simplefilter("error")
            pixels, fmt = _decode_pixels(raw)
            try:
                if max_dimension is not None:
                    pixels.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
                data = encode_pixels(pixels, format=fmt, max_bytes=max_bytes, quality=jpeg_quality)
                media_type = "image/jpeg" if fmt == "JPEG" else "image/png"
                verify_output(data, media_type, pixels.size)
                return SanitizedImage(data, media_type, *pixels.size)
            finally:
                pixels.close()
    except ImageSanitizationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageResourceError("image_decode_limit") from None
    except Exception:
        # No parser exception text or cause may escape to caller logs.
        raise ImageSanitizationError("image_decode_failed") from None
