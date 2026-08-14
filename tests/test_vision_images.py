from io import BytesIO
import os

import pytest
from PIL import Image

from app.services import vision_images
from app.services.vision_images import (
    ImagePreparationError,
    prepare_image_for_vision,
)


def _jpeg_bytes(size, quality=90):
    image = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def _noisy_png_bytes(size):
    image = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_small_valid_image_passes_through_without_reencoding():
    raw = _jpeg_bytes((32, 32), quality=90)
    prepared, media_type = prepare_image_for_vision(raw, "image/jpeg")
    assert prepared is raw
    assert media_type == "image/jpeg"


def test_large_valid_image_is_rgb_jpeg_below_provider_ceiling(app):
    raw = _noisy_png_bytes((2200, 1800))
    prepared, media_type = prepare_image_for_vision(raw, "image/png")
    assert len(prepared) <= 1_500_000
    assert media_type == "image/jpeg"
    with Image.open(BytesIO(prepared)) as image:
        assert image.mode == "RGB"
        assert max(image.size) <= 1600


def test_preparation_fails_closed_when_quality_floor_cannot_meet_ceiling(monkeypatch):
    monkeypatch.setattr(
        vision_images, "_encode_jpeg", lambda *args: b"x" * 1_500_001
    )
    with pytest.raises(ImagePreparationError):
        prepare_image_for_vision(_jpeg_bytes((1700, 1700)), "image/jpeg")


def test_caller_cannot_raise_provider_byte_ceiling(app):
    raw = _jpeg_bytes((32, 32))
    raw += b"\x00" * (vision_images.MAX_IMAGE_BYTES + 1 - len(raw))
    prepared, media_type = prepare_image_for_vision(
        raw, "image/jpeg", max_bytes=vision_images.MAX_IMAGE_BYTES + 1
    )
    assert len(prepared) <= vision_images.MAX_IMAGE_BYTES
    assert media_type == "image/jpeg"
