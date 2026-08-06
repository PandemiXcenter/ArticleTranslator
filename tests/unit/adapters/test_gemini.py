from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import errors, types

from article_translator.adapters.llm.gemini import GeminiPageTranslator, GeminiRequestError
from article_translator.domain.enums import BlockType
from article_translator.domain.models import (
    GeneratedBlock,
    GeneratedPagePayload,
    GeneratedTableMarkdown,
    GeneratedTablePayload,
    TranslationSettings,
)
from article_translator.ports.translation import (
    PageTranslationRequest,
    TableReconstructionRequest,
)


class FakeModels:
    def __init__(self, response: object) -> None:
        self.response = response
        self.kwargs: dict[str, Any] = {}

    def generate_content(self, **kwargs: Any) -> object:
        self.kwargs = kwargs
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeClient:
    def __init__(self, response: object) -> None:
        self.models = FakeModels(response)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_gemini_adapter_maps_multimodal_structured_output(tmp_path: Path) -> None:
    image = tmp_path / "page.png"
    image.write_bytes(b"not-a-real-png")
    payload = GeneratedPagePayload(
        detected_printed_page_label="12",
        blocks=[
            GeneratedBlock(
                order=1,
                type=BlockType.TITLE,
                source_text="Titel",
                translated_text="Title",
                paragraph_continuation=None,
            )
        ],
    )
    response = SimpleNamespace(
        parsed=payload,
        text=None,
        response_id="response-123",
        usage_metadata=SimpleNamespace(
            prompt_token_count=101,
            candidates_token_count=22,
        ),
    )
    client = FakeClient(response)
    translator = GeminiPageTranslator(
        api_key="unused",
        model="gemini-test",
        api_version="v1",
        timeout_seconds=120,
        attempts=3,
        max_inline_request_bytes=19_000_000,
        client=client,
    )

    result = translator.translate_page(
        PageTranslationRequest(
            original_page_number=1,
            markdown="# Titel",
            image_path=image,
            image_media_type="image/png",
            prompt="Translate this page",
            settings=TranslationSettings(),
        )
    )

    assert result.payload == payload
    assert result.response_id == "response-123"
    assert result.input_tokens == 101
    assert translator.descriptor.configuration == {
        "api_version": "v1",
        "request_timeout_seconds": 120,
        "request_attempts": 3,
        "max_inline_request_bytes": 19_000_000,
    }
    assert translator.descriptor.semantic_configuration == {"api_version": "v1"}
    assert client.models.kwargs["model"] == "gemini-test"
    assert isinstance(client.models.kwargs["contents"], types.Content)
    generation_config = client.models.kwargs["config"]
    assert generation_config.response_schema is None
    assert generation_config.response_json_schema == GeneratedPagePayload.model_json_schema()
    assert generation_config.temperature is None

    translator.close()
    assert client.closed is True


def test_gemini_adapter_maps_table_reconstruction_multimodal_output(tmp_path: Path) -> None:
    image = tmp_path / "page.png"
    image_bytes = b"table-page-image"
    image.write_bytes(image_bytes)
    payload = GeneratedTablePayload(
        tables=[
            GeneratedTableMarkdown(
                order=3,
                translated_markdown="| Date | Deaths |\n| --- | ---: |\n| 3 July | 3 |",
            )
        ]
    )
    response = SimpleNamespace(
        parsed=payload.model_dump(mode="json"),
        text=None,
        response_id="table-response-123",
        usage_metadata=SimpleNamespace(
            prompt_token_count=77,
            candidates_token_count=18,
        ),
    )
    client = FakeClient(response)
    translator = GeminiPageTranslator(
        api_key="unused",
        model="gemini-test",
        api_version="v1",
        timeout_seconds=120,
        attempts=3,
        max_inline_request_bytes=19_000_000,
        client=client,
    )

    result = translator.reconstruct_tables(
        TableReconstructionRequest(
            original_page_number=4,
            markdown="3 dead, on 3rd of July; 3, 4th",
            image_path=image,
            image_media_type="image/png",
            prompt="Reconstruct block 3 as a table",
            settings=TranslationSettings(),
            expected_block_orders=(3,),
        )
    )

    assert result.payload == payload
    assert result.response_id == "table-response-123"
    assert result.input_tokens == 77
    assert result.output_tokens == 18
    assert client.models.kwargs["model"] == "gemini-test"
    content = client.models.kwargs["contents"]
    assert isinstance(content, types.Content)
    assert content.parts is not None
    assert content.parts[0].inline_data is not None
    assert content.parts[0].inline_data.data == image_bytes
    assert content.parts[0].inline_data.mime_type == "image/png"
    assert content.parts[1].text == "Reconstruct block 3 as a table"
    generation_config = client.models.kwargs["config"]
    assert generation_config.response_mime_type == "application/json"
    assert generation_config.response_schema is None
    assert generation_config.response_json_schema == GeneratedTablePayload.model_json_schema()


def test_gemini_adapter_validates_table_reconstruction_response_text(tmp_path: Path) -> None:
    image = tmp_path / "page.png"
    image.write_bytes(b"table-page-image")
    payload = GeneratedTablePayload(
        tables=[
            GeneratedTableMarkdown(
                order=1,
                translated_markdown="| Date | Deaths |\n| --- | --- |\n| 4 July | 3 |",
            )
        ]
    )
    translator = GeminiPageTranslator(
        api_key="unused",
        model="gemini-test",
        api_version="v1",
        timeout_seconds=120,
        attempts=3,
        max_inline_request_bytes=19_000_000,
        client=FakeClient(
            SimpleNamespace(
                parsed=None,
                text=payload.model_dump_json(),
                response_id=None,
                usage_metadata=None,
            )
        ),
    )

    result = translator.reconstruct_tables(
        TableReconstructionRequest(
            original_page_number=1,
            markdown="source OCR",
            image_path=image,
            image_media_type="image/png",
            prompt="reconstruct",
            settings=TranslationSettings(),
            expected_block_orders=(1,),
        )
    )

    assert result.payload == payload
    assert result.input_tokens is None
    assert result.output_tokens is None


def test_gemini_adapter_rejects_oversized_inline_request_before_call(
    tmp_path: Path,
) -> None:
    image = tmp_path / "large.png"
    image.write_bytes(b"x" * 100)
    client = FakeClient(response=None)
    translator = GeminiPageTranslator(
        api_key="unused",
        model="gemini-test",
        api_version="v1",
        timeout_seconds=120,
        attempts=3,
        max_inline_request_bytes=100,
        client=client,
    )

    with pytest.raises(ValueError, match=r"lower extraction\.image_dpi"):
        translator.translate_page(
            PageTranslationRequest(
                original_page_number=1,
                markdown="source",
                image_path=image,
                image_media_type="image/png",
                prompt="translate",
                settings=TranslationSettings(),
            )
        )

    assert client.models.kwargs == {}


def test_gemini_adapter_rejects_oversized_table_request_before_call(tmp_path: Path) -> None:
    image = tmp_path / "large.png"
    image.write_bytes(b"x" * 100)
    client = FakeClient(response=None)
    translator = GeminiPageTranslator(
        api_key="unused",
        model="gemini-test",
        api_version="v1",
        timeout_seconds=120,
        attempts=3,
        max_inline_request_bytes=100,
        client=client,
    )

    with pytest.raises(ValueError, match=r"lower extraction\.image_dpi"):
        translator.reconstruct_tables(
            TableReconstructionRequest(
                original_page_number=1,
                markdown="source",
                image_path=image,
                image_media_type="image/png",
                prompt="reconstruct",
                settings=TranslationSettings(),
                expected_block_orders=(1,),
            )
        )

    assert client.models.kwargs == {}


@pytest.mark.parametrize(
    ("code", "status", "expected_guidance"),
    [
        (400, "INVALID_ARGUMENT", "Re-enter the API key"),
        (401, "UNAUTHENTICATED", "verify that it is active"),
        (403, "PERMISSION_DENIED", "API-key restrictions"),
        (404, "NOT_FOUND", "selected model"),
        (408, "DEADLINE_EXCEEDED", "provider timeout"),
        (429, "RESOURCE_EXHAUSTED", "quota and rate limits"),
        (503, "UNAVAILABLE", "server failure"),
    ],
)
def test_gemini_adapter_maps_provider_errors_without_raw_details(
    tmp_path: Path,
    code: int,
    status: str,
    expected_guidance: str,
) -> None:
    image = tmp_path / "page.png"
    image.write_bytes(b"not-a-real-png")
    error_class = errors.ClientError if code < 500 else errors.ServerError
    provider_error = error_class(
        code,
        {
            "error": {
                "status": status,
                "message": "raw provider detail containing private request data",
            }
        },
    )
    translator = GeminiPageTranslator(
        api_key="unused",
        model="gemini-test",
        api_version="v1",
        timeout_seconds=120,
        attempts=3,
        max_inline_request_bytes=19_000_000,
        client=FakeClient(provider_error),
    )

    with pytest.raises(GeminiRequestError) as error:
        translator.translate_page(
            PageTranslationRequest(
                original_page_number=1,
                markdown="source",
                image_path=image,
                image_media_type="image/png",
                prompt="translate",
                settings=TranslationSettings(),
            )
        )

    message = str(error.value)
    assert f"HTTP {code} {status}" in message
    assert expected_guidance in message
    assert "raw provider detail" not in message
    assert "private request data" not in message


def test_gemini_adapter_does_not_expose_untrusted_provider_status(tmp_path: Path) -> None:
    image = tmp_path / "page.png"
    image.write_bytes(b"not-a-real-png")
    translator = GeminiPageTranslator(
        api_key="unused",
        model="gemini-test",
        api_version="v1",
        timeout_seconds=120,
        attempts=3,
        max_inline_request_bytes=19_000_000,
        client=FakeClient(
            errors.APIError(
                418,
                {
                    "error": {
                        "status": "unsafe status with page text",
                        "message": "more raw provider detail",
                    }
                },
            )
        ),
    )

    with pytest.raises(GeminiRequestError) as error:
        translator.translate_page(
            PageTranslationRequest(
                original_page_number=1,
                markdown="source",
                image_path=image,
                image_media_type="image/png",
                prompt="translate",
                settings=TranslationSettings(),
            )
        )

    assert str(error.value) == (
        "Gemini request failed (HTTP 418). Check the provider configuration and retry."
    )
