"""Bounded, metadata-free image preparation shared by vision call sites."""
import io

from PIL import Image

from app.services.image_sanitization import (
    FORMATS, ImageResourceError, ImageSanitizationError, sanitize_image,
    encode_pixels, verify_output,
)

MAX_IMAGE_BYTES = 1_500_000
MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_DIMENSION = 1600
JPEG_QUALITIES = (85, 70, 55, 40, 30)


class ImagePreparationError(ValueError):
    """Image input cannot be prepared safely for a vision provider."""


class ImageTooLargeError(ImagePreparationError):
    """Image exceeds a resource limit."""


def prepare_image_for_vision(image_bytes: bytes, media_type: str = "image/jpeg",
                             max_bytes: int = MAX_IMAGE_BYTES) -> tuple[bytes, str]:
    """Always sanitize; the supplied MIME label cannot certify image content."""
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ImagePreparationError("image_output_limit")
    max_bytes = min(max_bytes, MAX_IMAGE_BYTES)
    try:
        result = sanitize_image(image_bytes, max_dimension=MAX_IMAGE_DIMENSION)
        if len(result.data) <= max_bytes:
            return result.data, result.media_type
        with Image.open(io.BytesIO(result.data)) as image:
            image.load()
            # Transparency is never silently flattened to fit the JPEG policy.
            if image.mode == "RGBA":
                raise ImagePreparationError("image_output_limit")
            pixels = Image.new("RGB", image.size)
            pixels.paste(image.convert("RGB"))
            try:
                for quality in JPEG_QUALITIES:
                    try:
                        encoded = _encode_jpeg(pixels, quality)
                    except ImageResourceError:
                        continue
                    if len(encoded) <= max_bytes:
                        verify_output(encoded, "image/jpeg", pixels.size)
                        return encoded, "image/jpeg"
            finally:
                pixels.close()
        raise ImagePreparationError("image_output_limit")
    except ImageResourceError:
        raise ImageTooLargeError("image_resource_limit") from None
    except ImageSanitizationError:
        raise ImagePreparationError("image_sanitization_failed") from None
    except ImagePreparationError:
        raise
    except Exception:
        raise ImagePreparationError("image_encoding_failed") from None


def fit_standard_envelope(image_bytes: bytes, media_type: str = "image/jpeg") -> tuple[bytes, str]:
    """Return an image no larger than the Sonnet 4.5 vision envelope.

    Claude 5.5 models read images at up to 2576 px (~3x the image tokens).
    Downscaling to the envelope Sonnet 4.5 applied server-side (<= 1568 px
    long edge, <= 1.15 MP) keeps the image tokens -- and what the model sees --
    at the pre-migration level. An image already inside it is returned as is.
    A larger one is re-encoded only from sanitized pixels (PA03), never by a
    raw save of the decoded input.
    """
    from app.services.ai_input_budget import (STANDARD_IMAGE_MAX_EDGE,
                                              STANDARD_IMAGE_MAX_PIXELS,
                                              within_standard_envelope)
    media_type = _safe_media_type(media_type)
    width, height = _image_size(image_bytes)
    if within_standard_envelope(width, height):
        return image_bytes, media_type
    scale = min(STANDARD_IMAGE_MAX_EDGE / max(width, height),
                (STANDARD_IMAGE_MAX_PIXELS / (width * height)) ** 0.5)
    edge = max(1, int(max(width, height) * scale))
    try:
        result = sanitize_image(image_bytes, max_dimension=edge)
        with Image.open(io.BytesIO(result.data)) as image:
            image.load()
            pixels = Image.new(image.mode, image.size)
            pixels.paste(image)
        try:
            while not within_standard_envelope(pixels.width, pixels.height):
                edge = max(pixels.size) - 1
                pixels.thumbnail((edge, edge), Image.Resampling.LANCZOS)
            if pixels.mode == "RGBA":
                # Transparency is never silently flattened to fit the JPEG policy.
                encoded = encode_pixels(pixels, format="PNG", max_bytes=MAX_IMAGE_BYTES)
                verify_output(encoded, "image/png", pixels.size)
                return encoded, "image/png"
            for quality in JPEG_QUALITIES:
                try:
                    encoded = _encode_jpeg(pixels, quality)
                except ImageResourceError:
                    continue
                verify_output(encoded, "image/jpeg", pixels.size)
                return encoded, "image/jpeg"
        finally:
            pixels.close()
        raise ImagePreparationError("image_output_limit")
    except ImageResourceError:
        raise ImageTooLargeError("image_resource_limit") from None
    except ImageSanitizationError:
        raise ImagePreparationError("image_sanitization_failed") from None
    except ImagePreparationError:
        raise
    except Exception:
        raise ImagePreparationError("image_encoding_failed") from None


def _safe_media_type(media_type):
    if not isinstance(media_type, str):
        return "image/jpeg"
    media_type = media_type.split(";", 1)[0].strip().lower()
    if media_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        return "image/jpeg"
    return media_type


def _image_size(image_bytes):
    """Header-only size probe; no pixels are decoded and no global is mutated."""
    if not isinstance(image_bytes, bytes) or not image_bytes:
        raise ImagePreparationError("image_input_invalid")
    try:
        with Image.open(io.BytesIO(image_bytes), formats=FORMATS) as image:
            width, height = image.size
    except Image.DecompressionBombError:
        raise ImageTooLargeError("image_resource_limit") from None
    except Exception:
        raise ImagePreparationError("image_sanitization_failed") from None
    if min(width, height) <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise ImageTooLargeError("image_resource_limit")
    return width, height


def _encode_jpeg(image, quality):
    return encode_pixels(image, format="JPEG", max_bytes=MAX_IMAGE_BYTES, quality=quality)


# Formats a vision proof may be sent as. The keys are Pillow's own format names
# (decoded from the leading magic bytes, never a filename or a client header);
# every value is a media type the Bedrock image block accepts.
_PROOF_MEDIA_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}


def detect_image_media_type(image_bytes) -> str | None:
    """Return the media type the BYTES actually are, or None.

    None means "not a structurally valid image of a supported format" --
    malformed, truncated past the header, a decompression bomb, an
    unsupported format or not an image at all. The caller must treat None as
    unusable proof; it is never a reason to fall back to a default type.
    """
    if not isinstance(image_bytes, bytes) or not image_bytes:
        return None
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        image = Image.open(io.BytesIO(image_bytes))
        media_type = _PROOF_MEDIA_TYPES.get((image.format or "").upper())
        width, height = image.size
        # verify() must follow open() directly and leaves the image unusable.
        image.verify()
    except Exception:
        return None
    if media_type is None or width * height > MAX_IMAGE_PIXELS:
        return None
    return media_type
