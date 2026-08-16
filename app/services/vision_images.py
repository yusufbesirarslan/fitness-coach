"""Bounded image preparation shared by vision provider call sites."""
import io

from flask import current_app
from PIL import Image


MAX_IMAGE_BYTES = 1_500_000
MAX_IMAGE_PIXELS = 50_000_000
MAX_IMAGE_DIMENSION = 1600
JPEG_QUALITIES = (85, 70, 55, 40, 30)


class ImagePreparationError(ValueError):
    """Image input cannot be prepared safely for a vision provider."""


class ImageTooLargeError(ImagePreparationError):
    """Image canvas exceeds the safe decode limit."""


def prepare_image_for_vision(image_bytes: bytes, media_type: str = "image/jpeg",
                             max_bytes: int = MAX_IMAGE_BYTES) -> tuple[bytes, str]:
    """Return image bytes that satisfy the vision provider byte ceiling."""
    max_bytes = min(max_bytes, MAX_IMAGE_BYTES)
    if not isinstance(image_bytes, bytes) or not image_bytes:
        raise ImagePreparationError("image bytes are required")
    media_type = _safe_media_type(media_type)
    if len(image_bytes) <= max_bytes:
        return image_bytes, media_type

    image = _open_bounded_image(image_bytes)
    image = _rgb_and_resize(image, MAX_IMAGE_DIMENSION)
    for quality in JPEG_QUALITIES:
        encoded = _encode_jpeg(image, quality)
        if len(encoded) <= max_bytes:
            current_app.logger.info(
                "[VISION] event=image_prepared input_bytes=%d output_bytes=%d "
                "width=%d height=%d",
                len(image_bytes), len(encoded), image.width, image.height,
            )
            return encoded, "image/jpeg"
    raise ImagePreparationError("prepared image exceeds byte ceiling")


def _safe_media_type(media_type):
    if not isinstance(media_type, str):
        return "image/jpeg"
    media_type = media_type.split(";", 1)[0].strip().lower()
    if media_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        return "image/jpeg"
    return media_type


def _open_bounded_image(image_bytes):
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        image = Image.open(io.BytesIO(image_bytes))
        width, height = image.size
    except Image.DecompressionBombError as exc:
        current_app.logger.warning("[VISION] image rejected by PIL bomb guard: %s", exc)
        raise ImageTooLargeError(str(exc)) from exc
    except Exception as exc:
        raise ImagePreparationError("image could not be decoded") from exc
    if width * height > MAX_IMAGE_PIXELS:
        current_app.logger.warning(
            "[VISION] image rejected: %dx%d (%d px > %d limit)",
            width, height, width * height, MAX_IMAGE_PIXELS,
        )
        raise ImageTooLargeError(f"image canvas too large: {width}x{height}")
    return image


def _rgb_and_resize(image, max_dimension):
    if image.mode != "RGB":
        image = image.convert("RGB")
    if max(image.size) > max_dimension:
        ratio = max_dimension / max(image.size)
        image = image.resize(
            (int(image.width * ratio), int(image.height * ratio)), Image.LANCZOS
        )
    return image


def _encode_jpeg(image, quality):
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()
