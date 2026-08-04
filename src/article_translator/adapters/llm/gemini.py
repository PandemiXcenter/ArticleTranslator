from __future__ import annotations

from typing import Any

from google import genai
from google.genai import errors, types

from article_translator.domain.models import GeneratedPagePayload
from article_translator.ports.translation import (
    PageTranslationRequest,
    ProviderDescriptor,
    ProviderResult,
)


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
        image_bytes = request.image_path.read_bytes()
        encoded_image_bytes = 4 * ((len(image_bytes) + 2) // 3)
        estimated_request_bytes = encoded_image_bytes + len(request.prompt.encode("utf-8")) + 16_384
        if estimated_request_bytes >= self._max_inline_request_bytes:
            raise ValueError(
                "Gemini inline request would exceed the configured byte limit; "
                "lower extraction.image_dpi or add a File API transport"
            )

        image_part = types.Part.from_bytes(
            data=image_bytes,
            mime_type=request.image_media_type,
        )
        content = types.Content(
            role="user",
            parts=[image_part, types.Part.from_text(text=request.prompt)],
        )
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=content,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=GeneratedPagePayload.model_json_schema(),
                ),
            )
        except errors.APIError as exc:
            raise GeminiRequestError(_safe_api_error_message(exc)) from exc

        parsed = response.parsed
        if parsed is None:
            if not response.text:
                raise ValueError("Gemini returned neither parsed structured data nor response text")
            payload = GeneratedPagePayload.model_validate_json(response.text)
        else:
            payload = GeneratedPagePayload.model_validate(parsed)

        usage = response.usage_metadata
        return ProviderResult(
            payload=payload,
            response_id=response.response_id,
            input_tokens=usage.prompt_token_count if usage is not None else None,
            output_tokens=usage.candidates_token_count if usage is not None else None,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GeminiPageTranslator:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class GeminiRequestError(ValueError):
    """A provider failure reduced to safe, actionable public fields."""


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
