from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from article_translator.domain.enums import (
    BlockType,
    ExtractionStatus,
    JobStatus,
    TranslationStyle,
)

SCHEMA_VERSION: Literal["1.0"] = "1.0"
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def utc_now() -> datetime:
    return datetime.now(UTC)


class ContractModel(BaseModel):
    """Base for persisted and provider-facing contracts."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ArtifactRef(ContractModel):
    """Portable reference to a file below a job's artifact root."""

    path: NonEmptyText
    sha256: Sha256
    media_type: NonEmptyText
    byte_count: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def path_must_be_relative(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("artifact paths must be relative and may not contain '..'")
        return normalized


class PreparedPage(ContractModel):
    """A physical PDF page paired with its Markdown and rendered image."""

    original_page_number: int = Field(ge=1)
    pdf_page_label: str | None = None
    markdown: ArtifactRef
    image: ArtifactRef
    extraction_status: ExtractionStatus
    extracted_character_count: int = Field(ge=0)
    extraction_warnings: list[str] = Field(default_factory=list)


class TranslationSettings(ContractModel):
    """Provider-neutral choices that affect translation meaning."""

    source_language: NonEmptyText = "auto"
    target_language: NonEmptyText = "English"
    style: TranslationStyle = TranslationStyle.BALANCED
    custom_instructions: str | None = Field(default=None, max_length=4_000)
    glossary: dict[str, str] = Field(default_factory=dict)
    preserve_names: bool = True
    preserve_citations: bool = True
    mark_uncertain_terms: bool = True

    @field_validator("custom_instructions")
    @classmethod
    def normalize_custom_instructions(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("glossary")
    @classmethod
    def glossary_must_not_have_blank_entries(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for source, target in value.items():
            source_term = source.strip()
            target_term = target.strip()
            if not source_term or not target_term:
                raise ValueError("glossary terms and translations must not be blank")
            normalized[source_term] = target_term
        return normalized


class MarkdownExportSettings(ContractModel):
    """Choices for the derived, human-facing Markdown projection."""

    include_page_comments: bool = True
    include_headers: bool = False
    include_footers: bool = False
    include_page_numbers: bool = False


class UncertainTerm(ContractModel):
    """Qualitative uncertainty without a fabricated probability."""

    source_term: NonEmptyText
    proposed_translation: str | None = None
    reason: NonEmptyText
    alternatives: list[str] = Field(default_factory=list)


class GeneratedBlock(ContractModel):
    """The intentionally small schema owned by the language model."""

    order: int = Field(ge=1)
    type: BlockType
    source_text: NonEmptyText
    translated_text: NonEmptyText
    heading_level: int | None = Field(default=None, ge=1, le=6)
    uncertainties: list[UncertainTerm] = Field(default_factory=list)


class GeneratedPagePayload(ContractModel):
    """Provider output before trusted provenance is attached."""

    detected_printed_page_label: str | None = None
    blocks: list[GeneratedBlock] = Field(default_factory=list)

    @model_validator(mode="after")
    def block_order_must_be_unique_and_contiguous(self) -> Self:
        orders = [block.order for block in self.blocks]
        expected = list(range(1, len(orders) + 1))
        if orders != expected:
            raise ValueError(f"block order must be contiguous and ordered; expected {expected}")
        return self


class ProviderMetadata(ContractModel):
    provider: NonEmptyText
    model: NonEmptyText
    prompt_version: NonEmptyText
    response_id: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class TranslatedBlock(ContractModel):
    """Validated model output enriched with pipeline-owned identity."""

    block_id: Annotated[str, StringConstraints(pattern=r"^p\d{4,}-b\d{4,}$")]
    original_page_number: int = Field(ge=1)
    order: int = Field(ge=1)
    type: BlockType
    source_text: NonEmptyText
    translated_text: NonEmptyText
    heading_level: int | None = Field(default=None, ge=1, le=6)
    uncertainties: list[UncertainTerm] = Field(default_factory=list)


class PageTranslation(ContractModel):
    """One independently retriable and cacheable page result."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    original_page_number: int = Field(ge=1)
    pdf_page_label: str | None = None
    detected_printed_page_label: str | None = None
    source_markdown: str
    source_image: ArtifactRef
    blocks: list[TranslatedBlock] = Field(default_factory=list)
    input_fingerprint: Sha256
    provider: ProviderMetadata
    translated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def blocks_must_belong_to_page(self) -> Self:
        orders = [block.order for block in self.blocks]
        if orders != list(range(1, len(orders) + 1)):
            raise ValueError("translated blocks must be in contiguous reading order")
        if any(block.original_page_number != self.original_page_number for block in self.blocks):
            raise ValueError("every block must belong to its containing page")
        return self


class JobManifest(ContractModel):
    """Mutable run index; page artifacts remain the recovery source of truth."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    job_id: NonEmptyText
    document_id: Sha256
    source_file_name: NonEmptyText
    source_file_sha256: Sha256
    image_dpi: int = Field(ge=72, le=600)
    page_count: int = Field(ge=1)
    pages: list[PreparedPage] = Field(min_length=1)
    status: JobStatus = JobStatus.PREPARED
    translation_settings: TranslationSettings | None = None
    provider_name: str | None = None
    provider_model: str | None = None
    prompt_version: str | None = None
    export_settings: MarkdownExportSettings | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def pages_must_match_manifest(self) -> Self:
        numbers = [page.original_page_number for page in self.pages]
        expected = list(range(1, self.page_count + 1))
        if numbers != expected:
            raise ValueError(f"manifest pages must be physical pages {expected}")
        return self


class PageFailure(ContractModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    original_page_number: int = Field(ge=1)
    error_type: NonEmptyText
    message: NonEmptyText
    occurred_at: datetime = Field(default_factory=utc_now)


class DocumentTranslation(ContractModel):
    """Canonical dataset consumed by the future editor and all exporters."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    document_id: Sha256
    job_id: NonEmptyText
    source_file_name: NonEmptyText
    source_file_sha256: Sha256
    page_count: int = Field(ge=1)
    translation_settings: TranslationSettings
    pages: list[PageTranslation] = Field(min_length=1)
    created_at: datetime

    @model_validator(mode="after")
    def translated_pages_must_be_complete(self) -> Self:
        numbers = [page.original_page_number for page in self.pages]
        expected = list(range(1, self.page_count + 1))
        if numbers != expected:
            raise ValueError(f"document must contain translated physical pages {expected}")
        return self
