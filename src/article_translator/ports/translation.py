from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from article_translator.domain.models import (
    GeneratedPagePayload,
    GeneratedTablePayload,
    ProviderSetting,
    TranslationSettings,
)


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    provider: str
    model: str
    configuration: Mapping[str, ProviderSetting] = field(default_factory=dict)
    semantic_configuration: Mapping[str, ProviderSetting] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PageTranslationRequest:
    original_page_number: int
    markdown: str
    image_path: Path
    image_media_type: str
    prompt: str
    settings: TranslationSettings


@dataclass(frozen=True, slots=True)
class ProviderResult:
    payload: GeneratedPagePayload
    response_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class TableReconstructionRequest:
    original_page_number: int
    markdown: str
    image_path: Path
    image_media_type: str
    prompt: str
    settings: TranslationSettings
    expected_block_orders: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class TableReconstructionResult:
    payload: GeneratedTablePayload
    response_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class PageTranslator(Protocol):
    @property
    def descriptor(self) -> ProviderDescriptor: ...

    def translate_page(self, request: PageTranslationRequest) -> ProviderResult: ...

    def reconstruct_tables(
        self,
        request: TableReconstructionRequest,
    ) -> TableReconstructionResult: ...
