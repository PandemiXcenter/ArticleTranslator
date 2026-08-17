from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, cast

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from article_translator.domain.models import GeneratedPagePayload, GeneratedTablePayload
from article_translator.ports.translation import (
    PageTranslationRequest,
    ProviderDescriptor,
    ProviderResult,
    TableReconstructionRequest,
    TableReconstructionResult,
)

MultimodalRequest = PageTranslationRequest | TableReconstructionRequest


@dataclass(frozen=True, slots=True)
class _StructuredResponse[PayloadT: BaseModel]:
    payload: PayloadT
    response_id: str | None
    input_tokens: int | None
    output_tokens: int | None


class GeminiPageTranslator:
    """Gemini multimodal structured-output adapter."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        api_version: str,
        timeout_seconds: int,
        attempts: int,
        max_inline_request_bytes: int,
        client: Any | None = None,
    ) -> None:
        self._model = model
        self._api_version = api_version
        self._timeout_seconds = timeout_seconds
        self._attempts = attempts
        self._max_inline_request_bytes = max_inline_request_bytes
        self._client = client or genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                api_version=api_version,
                timeout=timeout_seconds * 1_000,
                retry_options=types.HttpRetryOptions(attempts=attempts),
            ),
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider="gemini",
            model=self._model,
            configuration={
                "api_version": self._api_version,
                "request_timeout_seconds": self._timeout_seconds,
                "request_attempts": self._attempts,
                "max_inline_request_bytes": self._max_inline_request_bytes,
            },
            semantic_configuration={"api_version": self._api_version},
        )

    def translate_page(self, request: PageTranslationRequest) -> ProviderResult:
        response = self._generate_structured(
            request=request,
            payload_model=GeneratedPagePayload,
        )
        return ProviderResult(
            payload=response.payload,
            response_id=response.response_id,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )

    def reconstruct_tables(
        self,
        request: TableReconstructionRequest,
    ) -> TableReconstructionResult:
        response = self._generate_structured(
            request=request,
            payload_model=GeneratedTablePayload,
        )
        return TableReconstructionResult(
            payload=response.payload,
            response_id=response.response_id,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )

    def _generate_structured[PayloadT: BaseModel](
        self,
        *,
        request: MultimodalRequest,
        payload_model: type[PayloadT],
    ) -> _StructuredResponse[PayloadT]:
        image_bytes = request.image_path.read_bytes()
        self._guard_inline_request_size(image_bytes=image_bytes, prompt=request.prompt)
        content = types.Content(
            role="user",
            parts=[
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type=request.image_media_type,
                ),
                types.Part.from_text(text=request.prompt),
            ],
        )
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=content,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=payload_model.model_json_schema(),
                ),
            )
        except errors.APIError as exc:
            raise GeminiRequestError(_safe_api_error_message(exc)) from exc

        parsed: object = response.parsed
        if parsed is None:
            if not response.text:
                raise ValueError("Gemini returned neither parsed structured data nor response text")
            parsed = json.loads(response.text)
        if payload_model is GeneratedPagePayload:
            parsed = _repair_impossible_page_continuations(parsed)
        payload = payload_model.model_validate(parsed)

        usage = response.usage_metadata
        return _StructuredResponse(
            payload=payload,
            response_id=response.response_id,
            input_tokens=usage.prompt_token_count if usage is not None else None,
            output_tokens=usage.candidates_token_count if usage is not None else None,
        )

    def _guard_inline_request_size(self, *, image_bytes: bytes, prompt: str) -> None:
        encoded_image_bytes = 4 * ((len(image_bytes) + 2) // 3)
        estimated_request_bytes = encoded_image_bytes + len(prompt.encode("utf-8")) + 16_384
        if estimated_request_bytes >= self._max_inline_request_bytes:
            raise ValueError(
                "Gemini inline request would exceed the configured byte limit; "
                "lower extraction.image_dpi or add a File API transport"
            )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GeminiPageTranslator:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class GeminiRequestError(ValueError):
    """A provider failure reduced to safe, actionable public fields."""


def _repair_impossible_page_continuations(parsed: object) -> object:
    """Downgrade contradictory page-edge claims and require human review.

    Gemini's JSON schema cannot express that only the first/last flow body may
    cross a physical page boundary. Valid output is returned untouched. An
    impossible edge claim cannot be linked safely, so retain any non-conflicting
    direction, otherwise use ``unknown``, and make the ambiguity visible during
    editorial review.
    """

    if isinstance(parsed, BaseModel):
        return parsed
    if not isinstance(parsed, dict) or not isinstance(parsed.get("blocks"), list):
        return parsed
    repaired = deepcopy(parsed)
    blocks = cast(list[object], repaired["blocks"])
    mappings = [block for block in blocks if isinstance(block, dict)]
    body_blocks = [block for block in mappings if block.get("type") == "body"]
    if not body_blocks:
        return repaired
    incoming_flow = [
        block for block in mappings if block.get("type") not in {"header", "footer", "page_number"}
    ]
    outgoing_flow = [block for block in incoming_flow if block.get("type") != "footnote"]
    allowed_incoming = (
        body_blocks[0] if incoming_flow and incoming_flow[0] is body_blocks[0] else None
    )
    allowed_outgoing = (
        body_blocks[-1] if outgoing_flow and outgoing_flow[-1] is body_blocks[-1] else None
    )
    incoming_values = {"from_previous_page", "from_previous_and_to_next_page"}
    outgoing_values = {"to_next_page", "from_previous_and_to_next_page"}
    for block in body_blocks:
        continuation = block.get("paragraph_continuation")
        has_incoming = continuation in incoming_values
        has_outgoing = continuation in outgoing_values
        invalid_incoming = has_incoming and block is not allowed_incoming
        invalid_outgoing = has_outgoing and block is not allowed_outgoing
        if not invalid_incoming and not invalid_outgoing:
            continue
        keep_incoming = has_incoming and not invalid_incoming
        keep_outgoing = has_outgoing and not invalid_outgoing
        if keep_incoming and keep_outgoing:
            repaired_value = "from_previous_and_to_next_page"
        elif keep_incoming:
            repaired_value = "from_previous_page"
        elif keep_outgoing:
            repaired_value = "to_next_page"
        else:
            repaired_value = "unknown"
        block["paragraph_continuation"] = repaired_value
        block["classification_review_required"] = True
    return repaired


def _safe_api_error_message(exc: errors.APIError) -> str:
    code = exc.code if isinstance(exc.code, int) and 100 <= exc.code <= 599 else None
    status = exc.status if isinstance(exc.status, str) else None
    if status is None or not status.replace("_", "").isalnum() or not status.isupper():
        status = None
    provider_code = " ".join(
        part for part in (f"HTTP {code}" if code is not None else None, status) if part
    )
    provider_code = provider_code or "unknown provider status"

    if code == 400:
        guidance = (
            "Re-enter the API key; if it is valid, check the selected model, API version, "
            "and structured-output schema."
        )
    elif code == 401:
        guidance = "Re-enter the API key and verify that it is active."
    elif code == 403:
        guidance = "Check API-key restrictions and whether the Gemini API is enabled."
    elif code == 404:
        guidance = "Check that the selected model is available for the configured API version."
    elif code == 408:
        guidance = "Retry the request or increase the configured provider timeout."
    elif code == 429:
        guidance = "Check Gemini quota and rate limits, then retry later."
    elif code is not None and 500 <= code <= 599:
        guidance = "The provider reported a server failure; retry later."
    else:
        guidance = "Check the provider configuration and retry."

    return f"Gemini request failed ({provider_code}). {guidance}"
