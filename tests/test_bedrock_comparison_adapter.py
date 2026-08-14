import base64
from types import SimpleNamespace

import pytest

from app.services import ai


class FakeBedrockResponse:
    def __init__(self, text):
        self.calls = []

        def create(**kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=text)]
            )

        self.client = SimpleNamespace(
            messages=SimpleNamespace(create=create)
        )


def _decoded(block):
    return base64.b64decode(block["source"]["data"])


def test_two_image_adapter_labels_and_orders_baseline_before_current(
        monkeypatch):
    fake = FakeBedrockResponse("comparison-json")
    monkeypatch.setattr(ai, "bedrock_client", fake.client)

    result = ai._bedrock_compare_images(
        b"baseline", "image/jpeg", b"current", "image/png",
        "compare", max_tokens=900,
    )

    content = fake.calls[0]["messages"][0]["content"]
    assert [block["type"] for block in content] == [
        "text", "text", "image", "text", "image"
    ]
    assert content[0]["text"] == "compare"
    assert content[1]["text"] == "Image A — baseline"
    assert content[2]["source"]["media_type"] == "image/jpeg"
    assert _decoded(content[2]) == b"baseline"
    assert content[3]["text"] == "Image B — current"
    assert content[4]["source"]["media_type"] == "image/png"
    assert _decoded(content[4]) == b"current"
    assert result == "comparison-json"


def test_two_image_adapter_reuses_model_token_ceiling_and_temperature(
        monkeypatch):
    fake = FakeBedrockResponse("comparison-json")
    monkeypatch.setattr(ai, "bedrock_client", fake.client)
    monkeypatch.setattr(ai, "BEDROCK_MODEL", "test-model")
    monkeypatch.setattr(ai, "BEDROCK_MAX_TOKENS", 800)

    ai._bedrock_compare_images(
        b"baseline", "image/jpeg", b"current", "image/png",
        "compare", max_tokens=1200, temperature=0.25,
    )

    assert fake.calls[0]["model"] == "test-model"
    assert fake.calls[0]["max_tokens"] == 800
    assert fake.calls[0]["temperature"] == 0.25
    assert fake.calls[0]["messages"][0]["role"] == "user"


def test_existing_single_image_layout_is_unchanged(monkeypatch):
    fake = FakeBedrockResponse("single-json")
    monkeypatch.setattr(ai, "bedrock_client", fake.client)

    result = ai._bedrock_validate_image(
        b"one", "image/jpeg", "inspect"
    )

    content = fake.calls[0]["messages"][0]["content"]
    assert [block["type"] for block in content] == ["text", "image"]
    assert content[0]["text"] == "inspect"
    assert _decoded(content[1]) == b"one"
    assert result == "single-json"


def test_two_image_adapter_preserves_timeout_error_normalization(monkeypatch):
    class FakeRateLimit(Exception):
        pass

    class FakeTimeout(Exception):
        pass

    class FakeConnection(Exception):
        pass

    class FakeAPIError(Exception):
        pass

    def create(**kwargs):
        raise FakeTimeout("timed out")

    monkeypatch.setattr(ai, "anthropic", SimpleNamespace(
        RateLimitError=FakeRateLimit, APITimeoutError=FakeTimeout,
        APIConnectionError=FakeConnection, APIError=FakeAPIError,
    ))
    monkeypatch.setattr(ai, "bedrock_client", SimpleNamespace(
        messages=SimpleNamespace(create=create)))
    with pytest.raises(RuntimeError, match="zaman a\u015f\u0131m\u0131"):
        ai._bedrock_compare_images(
            b"baseline", "image/jpeg", b"current", "image/png", "compare")
