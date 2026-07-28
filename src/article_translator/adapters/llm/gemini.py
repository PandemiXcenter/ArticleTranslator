from __future__ import annotations

from typing import Any

from google import genai
from google.genai import types

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
        timeout_seconds: int = 120,
        attempts: int = 3,
        temperature: float = 0.2,
        client: Any | None = None,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._client = client or genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=timeout_seconds * 1_000,
                retry_options=types.HttpRetryOptions(attempts=attempts),
            ),
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(provider="gemini", model=self._model)

    def translate_page(self, request: PageTranslationRequest) -> ProviderResult:
        image_part = types.Part.from_bytes(
            data=request.image_path.read_bytes(),
            mime_type=request.image_media_type,
        )
        content = types.Content(
            role="user",
            parts=[image_part, types.Part.from_text(text=request.prompt)],
        )
        response = self._client.models.generate_content(
            model=self._model,
            contents=content,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=GeneratedPagePayload,
                temperature=self._temperature,
            ),
        )

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
