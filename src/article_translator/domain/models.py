from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Literal, Self, TypeAlias

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
    ManualInsertionReason,
    SegmentContinuation,
    SegmentHandling,
    TranslationStyle,
)

SCHEMA_VERSION: Literal["6.0"] = "6.0"
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
TranslationRunId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
BlockId = Annotated[str, StringConstraints(pattern=r"^p\d{4,}-b\d{4,}$")]
FootnoteIdentifier = Annotated[
    str,
    StringConstraints(pattern=r"^fn-p[1-9]\d*-n[1-9]\d*$"),
]
FootnoteEntrypointToken = Annotated[
    str,
    StringConstraints(pattern=r"^\[\[FOOTNOTE:fn-p[1-9]\d*-n[1-9]\d*\]\]$"),
]
ProviderSetting = str | int | float | bool


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
    footnote_appearance_instructions: str | None = Field(default=None, max_length=4_000)
    glossary: dict[str, str] = Field(default_factory=dict)
    preserve_names: bool = True
    preserve_citations: bool = True
    mark_uncertain_terms: bool = True
    previous_page_context_count: int = Field(default=0, ge=0, le=10)

    @field_validator("custom_instructions", "footnote_appearance_instructions")
    @classmethod
    def normalize_optional_instructions(cls, value: str | None) -> str | None:
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


class FootnoteIdentity(ContractModel):
    """Cross-page note identity plus the source's printed reference text."""

    id: FootnoteIdentifier
    text: str | None = Field(default=None, max_length=64)

    @field_validator("text")
    @classmethod
    def normalize_reference_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if any(character in stripped for character in "\r\n\0"):
            raise ValueError("footnote reference text must be a single line")
        return stripped


class FootnoteDescription(ContractModel):
    """Observable appearance and the model's page-continuation handling."""

    appearance: NonEmptyText
    handling: NonEmptyText


OrdinaryBlockType: TypeAlias = Literal[  # noqa: UP040
    BlockType.TITLE,
    BlockType.SUBTITLE,
    BlockType.BYLINE,
    BlockType.HEADING,
    BlockType.BODY,
    BlockType.LIST_ITEM,
    BlockType.QUOTE,
    BlockType.CAPTION,
    BlockType.PAGE_NUMBER,
    BlockType.HEADER,
    BlockType.FOOTER,
    BlockType.EQUATION,
    BlockType.OTHER,
]


class GeneratedTextBlock(ContractModel):
    """Ordinary provider text that is transcribed and translated."""

    order: int = Field(ge=1)
    type: OrdinaryBlockType
    source_text: NonEmptyText
    translated_text: NonEmptyText
    heading_level: int | None = Field(default=None, ge=1, le=6)
    paragraph_continuation: SegmentContinuation | None
    uncertainties: list[UncertainTerm] = Field(default_factory=list)
    classification_review_required: bool = False

    @model_validator(mode="after")
    def paragraph_continuation_applies_only_to_body_text(self) -> Self:
        if self.type is BlockType.BODY and self.paragraph_continuation is None:
            raise ValueError("body blocks must state their paragraph continuation")
        if self.type is not BlockType.BODY and self.paragraph_continuation is not None:
            raise ValueError("paragraph continuation is valid only for body blocks")
        return self


class GeneratedFootnoteBlock(ContractModel):
    """A functional footnote, regardless of its size or position on the page."""

    order: int = Field(ge=1)
    type: Literal[BlockType.FOOTNOTE]
    source_text: NonEmptyText
    translated_text: NonEmptyText
    footnote_id: FootnoteIdentity
    entrypoint_token: FootnoteEntrypointToken | None
    description: FootnoteDescription
    owner_review_required: bool
    continuation: SegmentContinuation
    uncertainties: list[UncertainTerm] = Field(default_factory=list)
    classification_review_required: bool = False

    @model_validator(mode="after")
    def entrypoint_must_match_identity(self) -> Self:
        if self.entrypoint_token is not None and self.entrypoint_token != (
            f"[[FOOTNOTE:{self.footnote_id.id}]]"
        ):
            raise ValueError("footnote entrypoint token must contain its footnote ID")
        if _continues_from_previous(self.continuation) and self.entrypoint_token is not None:
            raise ValueError("a continued footnote must reuse its earlier entrypoint")
        return self


class GeneratedManualInsertionBlock(ContractModel):
    """A text-free first-pass table tag or figure requiring manual insertion."""

    order: int = Field(ge=1)
    type: Literal[BlockType.TABLE, BlockType.FIGURE]
    manual_insertion_reason: ManualInsertionReason
    continuation: SegmentContinuation
    classification_review_required: bool = False

    @model_validator(mode="after")
    def reason_must_match_block_type(self) -> Self:
        _validate_manual_reason(self.type, self.manual_insertion_reason)
        return self


# Preserve the existing ordinary-block constructor while the page payload uses
# distinct provider variants for footnotes and manual regions.
GeneratedBlock = GeneratedTextBlock
GeneratedBlockVariant: TypeAlias = (  # noqa: UP040
    GeneratedTextBlock | GeneratedFootnoteBlock | GeneratedManualInsertionBlock
)


class GeneratedPagePayload(ContractModel):
    """Provider output before trusted provenance is attached."""

    detected_printed_page_label: str | None = None
    blocks: list[GeneratedBlockVariant] = Field(default_factory=list)

    @model_validator(mode="after")
    def block_order_must_be_unique_and_contiguous(self) -> Self:
        orders = [block.order for block in self.blocks]
        expected = list(range(1, len(orders) + 1))
        if orders != expected:
            raise ValueError(f"block order must be contiguous and ordered; expected {expected}")
        body_blocks = [block for block in self.blocks if block.type is BlockType.BODY]
        incoming = [
            block
            for block in body_blocks
            if isinstance(block, GeneratedTextBlock)
            and _continues_from_previous(block.paragraph_continuation)
        ]
        outgoing = [
            block
            for block in body_blocks
            if isinstance(block, GeneratedTextBlock)
            and _continues_to_next(block.paragraph_continuation)
        ]
        if incoming and incoming != body_blocks[:1]:
            raise ValueError("only the first body block may continue a previous-page paragraph")
        if outgoing and outgoing != body_blocks[-1:]:
            raise ValueError("only the last body block may continue onto the next page")
        incoming_flow_blocks = [
            block
            for block in self.blocks
            if block.type
            not in {
                BlockType.HEADER,
                BlockType.FOOTER,
                BlockType.PAGE_NUMBER,
            }
        ]
        outgoing_flow_blocks = [
            block for block in incoming_flow_blocks if block.type is not BlockType.FOOTNOTE
        ]
        if incoming and incoming_flow_blocks[:1] != incoming:
            raise ValueError(
                "a previous-page paragraph continuation must be the first main-flow block"
            )
        if outgoing and outgoing_flow_blocks[-1:] != outgoing:
            raise ValueError(
                "an unfinished paragraph must be the final main-flow block on its page"
            )
        footnotes = [block for block in self.blocks if isinstance(block, GeneratedFootnoteBlock)]
        footnote_ids = [block.footnote_id.id for block in footnotes]
        if len(footnote_ids) != len(set(footnote_ids)):
            raise ValueError("a footnote ID may occur only once on one physical page")
        declared_tokens = [
            block.entrypoint_token for block in footnotes if block.entrypoint_token is not None
        ]
        if len(declared_tokens) != len(set(declared_tokens)):
            raise ValueError("footnote owner reference tokens must be unique")
        translated_owner_text = [
            block.translated_text for block in self.blocks if isinstance(block, GeneratedTextBlock)
        ]
        all_translated_text = [
            block.translated_text
            for block in self.blocks
            if isinstance(block, (GeneratedTextBlock, GeneratedFootnoteBlock))
        ]
        for footnote in footnotes:
            token = footnote.entrypoint_token
            if token is None:
                if (
                    not _continues_from_previous(footnote.continuation)
                    and not footnote.owner_review_required
                ):
                    raise ValueError("an unowned footnote must require owner review")
                continue
            if footnote.owner_review_required:
                raise ValueError("a resolved footnote owner cannot require owner review")
            if sum(text.count(token) for text in translated_owner_text) != 1:
                raise ValueError(
                    "a footnote owner reference token must occur exactly once in ordinary "
                    "translated text"
                )
        embedded_tokens = {
            token
            for text in all_translated_text
            for token in re.findall(r"\[\[FOOTNOTE:fn-p[1-9]\d*-n[1-9]\d*\]\]", text)
        }
        if embedded_tokens != set(declared_tokens):
            raise ValueError("translated text contains an undeclared footnote reference token")
        return self


class GeneratedTableMarkdown(ContractModel):
    """One table reconstructed in target-language GitHub-flavored Markdown."""

    order: int = Field(ge=1)
    translated_markdown: NonEmptyText
    uncertainties: list[UncertainTerm] = Field(default_factory=list)

    @field_validator("translated_markdown")
    @classmethod
    def markdown_must_be_an_unfenced_gfm_table(cls, value: str) -> str:
        if "```" in value:
            raise ValueError("table Markdown must not use a code fence")
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        if len(lines) < 3:
            raise ValueError("table Markdown must contain a header, delimiter, and data row")
        rows = [_split_gfm_row(line) for line in lines]
        if any(row is None for row in rows):
            raise ValueError("every table Markdown line must be a pipe-delimited row")
        parsed_rows = [row for row in rows if row is not None]
        column_count = len(parsed_rows[0])
        if column_count < 1 or any(len(row) != column_count for row in parsed_rows[1:]):
            raise ValueError("table Markdown rows must have matching column counts")
        if not _is_gfm_delimiter_row(parsed_rows[1]):
            raise ValueError("table Markdown must contain a GitHub-flavored table delimiter row")
        return value


class GeneratedTablePayload(ContractModel):
    """Atomic reconstruction output for every table tagged in one page pass."""

    tables: list[GeneratedTableMarkdown] = Field(min_length=1)

    @model_validator(mode="after")
    def table_orders_must_be_unique_and_ascending(self) -> Self:
        orders = [table.order for table in self.tables]
        if orders != sorted(set(orders)):
            raise ValueError("table orders must be unique and ascending")
        return self


def _split_gfm_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if "|" not in stripped:
        return None
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith(r"\|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", stripped)]


def _is_gfm_delimiter_row(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{3,}:?", cell) is not None for cell in cells)


class ProviderMetadata(ContractModel):
    provider: NonEmptyText
    model: NonEmptyText
    prompt_version: NonEmptyText
    configuration: dict[str, ProviderSetting] = Field(default_factory=dict)
    semantic_configuration: dict[str, ProviderSetting] = Field(default_factory=dict)
    response_id: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class TableReconstructionMetadata(ContractModel):
    """Bounded provenance for the optional second provider pass on one page."""

    input_fingerprint: Sha256
    block_ids: list[NonEmptyText] = Field(min_length=1)
    provider: ProviderMetadata
    reconstructed_at: datetime = Field(default_factory=utc_now)

    @field_validator("block_ids")
    @classmethod
    def block_ids_must_be_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("table reconstruction block IDs must be unique")
        return value


class TranslatedBlock(ContractModel):
    """Validated model output enriched with pipeline-owned identity."""

    block_id: BlockId
    original_page_number: int = Field(ge=1)
    order: int = Field(ge=1)
    type: BlockType
    source_text: NonEmptyText | None
    translated_text: NonEmptyText | None
    heading_level: int | None = Field(default=None, ge=1, le=6)
    uncertainties: list[UncertainTerm] = Field(default_factory=list)
    segment_handling: SegmentHandling = SegmentHandling.TRANSLATE
    manual_insertion_reason: ManualInsertionReason | None = None
    footnote_id: FootnoteIdentity | None = None
    footnote_description: FootnoteDescription | None = None
    footnote_owner_block_id: BlockId | None = None
    footnote_anchor_offset: int | None = Field(default=None, ge=0)
    footnote_owner_review_required: bool = False
    continuation: SegmentContinuation | None = None
    footnote_continues_from_block_id: BlockId | None = None
    paragraph_continuation: SegmentContinuation | None = None
    continues_from_block_id: BlockId | None = None
    classification_review_required: bool = False
    legacy_translated_table: bool = False
    legacy_manual_table: bool = False

    @model_validator(mode="after")
    def segment_contract_must_be_consistent(self) -> Self:
        _validate_segment_contract(self)
        return self


def _validate_segment_contract(block: TranslatedBlock) -> None:
    if block.segment_handling is SegmentHandling.MANUAL_INSERTION:
        if block.legacy_translated_table:
            raise ValueError("manual insertion blocks cannot be legacy translated tables")
        if block.legacy_manual_table and block.type is not BlockType.TABLE:
            raise ValueError("legacy manual-table compatibility applies only to table blocks")
        if block.type not in {BlockType.TABLE, BlockType.FIGURE}:
            raise ValueError("manual insertion is valid only for table and figure blocks")
        if block.manual_insertion_reason is None:
            raise ValueError("manual insertion blocks must state a reason")
        _validate_manual_reason(block.type, block.manual_insertion_reason)
        if block.source_text is not None or block.translated_text is not None:
            raise ValueError("manual insertion blocks must not contain source or translated text")
        if block.heading_level is not None:
            raise ValueError("manual insertion blocks cannot have a heading level")
        if block.uncertainties:
            raise ValueError("manual insertion blocks cannot contain text uncertainties")
        if (
            block.footnote_id is not None
            or block.footnote_description is not None
            or block.footnote_owner_block_id is not None
            or block.footnote_anchor_offset is not None
            or block.footnote_owner_review_required
            or block.footnote_continues_from_block_id is not None
        ):
            raise ValueError("manual insertion blocks cannot contain footnote metadata")
        if block.continuation is None:
            raise ValueError("manual insertion blocks must state their continuation relationship")
        if block.paragraph_continuation is not None or block.continues_from_block_id is not None:
            raise ValueError("manual insertion blocks cannot contain paragraph continuity")
        return

    if block.segment_handling is SegmentHandling.TABLE_RECONSTRUCTION:
        if block.type is not BlockType.TABLE:
            raise ValueError("table reconstruction handling is valid only for table blocks")
        if block.source_text is not None or block.translated_text is None:
            raise ValueError("reconstructed tables must contain only translated Markdown")
        if block.manual_insertion_reason not in {
            ManualInsertionReason.TABLE,
            ManualInsertionReason.TABLE_LIKE,
        }:
            raise ValueError("reconstructed tables must retain their table-region reason")
        if block.continuation is None:
            raise ValueError("reconstructed tables must retain continuation metadata")
        if (
            block.heading_level is not None
            or block.footnote_id is not None
            or block.footnote_description is not None
            or block.footnote_owner_block_id is not None
            or block.footnote_anchor_offset is not None
            or block.footnote_owner_review_required
            or block.footnote_continues_from_block_id is not None
        ):
            raise ValueError("reconstructed tables cannot contain heading or footnote metadata")
        if block.legacy_translated_table or block.legacy_manual_table:
            raise ValueError("reconstructed tables cannot use legacy compatibility markers")
        if block.paragraph_continuation is not None or block.continues_from_block_id is not None:
            raise ValueError("reconstructed tables cannot contain paragraph continuity")
        return

    if block.manual_insertion_reason is not None:
        raise ValueError("translated blocks cannot state a manual insertion reason")
    if block.source_text is None or block.translated_text is None:
        raise ValueError("translated blocks must contain source and translated text")
    if block.type is BlockType.FIGURE:
        raise ValueError("figure blocks must use manual insertion handling")
    if block.type is BlockType.TABLE and not block.legacy_translated_table:
        raise ValueError("new table blocks must use table reconstruction handling")
    if block.type is not BlockType.TABLE and block.legacy_translated_table:
        raise ValueError("legacy translated table compatibility applies only to table blocks")
    if block.legacy_manual_table:
        raise ValueError("translated blocks cannot be legacy manual tables")
    footnote_metadata_present = (
        block.footnote_id is not None
        or block.footnote_description is not None
        or block.continuation is not None
        or block.footnote_owner_block_id is not None
        or block.footnote_anchor_offset is not None
        or block.footnote_owner_review_required
        or block.footnote_continues_from_block_id is not None
    )
    if block.type is not BlockType.FOOTNOTE and footnote_metadata_present:
        raise ValueError("footnote metadata is valid only for footnote blocks")
    if block.type is BlockType.FOOTNOTE:
        if block.footnote_id is None or block.footnote_description is None:
            raise ValueError("footnotes must retain identity and description metadata")
        has_owner = block.footnote_owner_block_id is not None
        if has_owner != (block.footnote_anchor_offset is not None):
            raise ValueError("a footnote owner block and anchor offset must be stored together")
        if has_owner and block.footnote_owner_review_required:
            raise ValueError("a resolved footnote owner cannot require owner review")
        if not has_owner and not block.footnote_owner_review_required:
            raise ValueError("an unowned footnote must require owner review")
        incoming = _continues_from_previous(block.continuation)
        if incoming != (block.footnote_continues_from_block_id is not None):
            raise ValueError("incoming footnotes must link their previous-page fragment")
    if block.type is BlockType.BODY:
        if _continues_from_previous(block.paragraph_continuation):
            if block.continues_from_block_id is None:
                raise ValueError("incoming paragraph continuations must link a previous block")
        elif block.continues_from_block_id is not None:
            raise ValueError("only incoming paragraph continuations may link a previous block")
    elif block.paragraph_continuation is not None or block.continues_from_block_id is not None:
        raise ValueError("paragraph continuity is valid only for body blocks")


def _continues_from_previous(value: SegmentContinuation | None) -> bool:
    return value in {
        SegmentContinuation.FROM_PREVIOUS_PAGE,
        SegmentContinuation.FROM_PREVIOUS_AND_TO_NEXT_PAGE,
    }


def _continues_to_next(value: SegmentContinuation | None) -> bool:
    return value in {
        SegmentContinuation.TO_NEXT_PAGE,
        SegmentContinuation.FROM_PREVIOUS_AND_TO_NEXT_PAGE,
    }


def _validate_manual_reason(
    block_type: BlockType,
    reason: ManualInsertionReason,
) -> None:
    if block_type is BlockType.FIGURE and reason is not ManualInsertionReason.FIGURE:
        raise ValueError("figure blocks must use the figure manual insertion reason")
    if block_type is BlockType.TABLE and reason is ManualInsertionReason.FIGURE:
        raise ValueError("table blocks must use a table or table-like insertion reason")


class PageTranslation(ContractModel):
    """One independently retriable and cacheable page result."""

    schema_version: Literal["6.0"] = SCHEMA_VERSION
    translation_run_id: TranslationRunId
    original_page_number: int = Field(ge=1)
    pdf_page_label: str | None = None
    detected_printed_page_label: str | None = None
    extraction_status: ExtractionStatus
    extracted_character_count: int = Field(ge=0)
    extraction_warnings: list[str] = Field(default_factory=list)
    source_markdown: str
    source_markdown_artifact: ArtifactRef
    source_image: ArtifactRef
    blocks: list[TranslatedBlock] = Field(default_factory=list)
    input_fingerprint: Sha256
    provider: ProviderMetadata
    table_reconstruction: TableReconstructionMetadata | None = None
    translated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def blocks_must_belong_to_page(self) -> Self:
        orders = [block.order for block in self.blocks]
        if orders != list(range(1, len(orders) + 1)):
            raise ValueError("translated blocks must be in contiguous reading order")
        if any(block.original_page_number != self.original_page_number for block in self.blocks):
            raise ValueError("every block must belong to its containing page")
        body_blocks = [block for block in self.blocks if block.type is BlockType.BODY]
        incoming = [
            block for block in body_blocks if _continues_from_previous(block.paragraph_continuation)
        ]
        outgoing = [
            block for block in body_blocks if _continues_to_next(block.paragraph_continuation)
        ]
        if incoming and incoming != body_blocks[:1]:
            raise ValueError("only the first body block may continue a previous-page paragraph")
        if outgoing and outgoing != body_blocks[-1:]:
            raise ValueError("only the last body block may continue onto the next page")
        incoming_flow_blocks = [
            block
            for block in self.blocks
            if block.type
            not in {
                BlockType.HEADER,
                BlockType.FOOTER,
                BlockType.PAGE_NUMBER,
            }
        ]
        outgoing_flow_blocks = [
            block for block in incoming_flow_blocks if block.type is not BlockType.FOOTNOTE
        ]
        if incoming and incoming_flow_blocks[:1] != incoming:
            raise ValueError(
                "a previous-page paragraph continuation must be the first main-flow block"
            )
        if outgoing and outgoing_flow_blocks[-1:] != outgoing:
            raise ValueError(
                "an unfinished paragraph must be the final main-flow block on its page"
            )
        blocks_by_id = {block.block_id: block for block in self.blocks}
        for footnote in (block for block in self.blocks if block.type is BlockType.FOOTNOTE):
            if footnote.footnote_continues_from_block_id is not None:
                previous_page_number = int(
                    footnote.footnote_continues_from_block_id.split("-", 1)[0][1:]
                )
                if previous_page_number != self.original_page_number - 1:
                    raise ValueError("footnote continuation must link the previous physical page")
            if footnote.footnote_owner_block_id is None:
                continue
            owner = blocks_by_id.get(footnote.footnote_owner_block_id)
            if owner is None and footnote.footnote_continues_from_block_id is not None:
                continue
            if owner is None or owner.type is BlockType.FOOTNOTE:
                raise ValueError("a footnote owner must be a non-footnote block on the same page")
            if (
                owner.translated_text is None
                or footnote.footnote_anchor_offset is None
                or footnote.footnote_anchor_offset > len(owner.translated_text)
            ):
                raise ValueError("a footnote anchor offset must fall within its owner text")
        reconstructed_ids = [
            block.block_id
            for block in self.blocks
            if block.segment_handling is SegmentHandling.TABLE_RECONSTRUCTION
        ]
        if reconstructed_ids:
            if self.table_reconstruction is None:
                raise ValueError("reconstructed table blocks require table-pass provenance")
            if self.table_reconstruction.block_ids != reconstructed_ids:
                raise ValueError("table-pass provenance must name every reconstructed table block")
        elif self.table_reconstruction is not None:
            raise ValueError("table-pass provenance requires reconstructed table blocks")
        return self


class JobManifest(ContractModel):
    """Mutable run index; page artifacts remain the recovery source of truth."""

    schema_version: Literal["6.0"] = SCHEMA_VERSION
    job_id: NonEmptyText
    preparation_id: NonEmptyText
    document_id: Sha256
    source_file_name: NonEmptyText
    source_file_sha256: Sha256
    image_dpi: int = Field(ge=72, le=600)
    page_count: int = Field(ge=1)
    pages: list[PreparedPage] = Field(min_length=1)
    status: JobStatus = JobStatus.PREPARED
    translation_run_id: TranslationRunId | None = None
    translation_run_ids: list[TranslationRunId] = Field(default_factory=list)
    translation_settings: TranslationSettings | None = None
    provider_name: str | None = None
    provider_model: str | None = None
    provider_configuration: dict[str, ProviderSetting] | None = None
    provider_semantic_configuration: dict[str, ProviderSetting] | None = None
    prompt_version: str | None = None
    table_prompt_version: str | None = None
    export_settings: MarkdownExportSettings | None = None
    auto_continue: bool = False
    auto_continue_attempts: int = Field(default=1, ge=1, le=10)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def pages_must_match_manifest(self) -> Self:
        numbers = [page.original_page_number for page in self.pages]
        expected = list(range(1, self.page_count + 1))
        if numbers != expected:
            raise ValueError(f"manifest pages must be physical pages {expected}")
        if len(self.translation_run_ids) != len(set(self.translation_run_ids)):
            raise ValueError("manifest translation run IDs must be unique")
        if (
            self.translation_run_id is not None
            and self.translation_run_id not in self.translation_run_ids
        ):
            raise ValueError("active translation run must appear in the ordered run index")
        return self


class PageFailure(ContractModel):
    schema_version: Literal["6.0"] = SCHEMA_VERSION
    original_page_number: int = Field(ge=1)
    input_fingerprint: Sha256 | None = None
    stage: Literal["page_translation", "table_reconstruction"] = "page_translation"
    error_type: NonEmptyText
    message: NonEmptyText
    occurred_at: datetime = Field(default_factory=utc_now)


class DocumentTranslation(ContractModel):
    """Canonical dataset consumed by the future editor and all exporters."""

    schema_version: Literal["6.0"] = SCHEMA_VERSION
    translation_run_id: TranslationRunId
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
        if any(page.translation_run_id != self.translation_run_id for page in self.pages):
            raise ValueError("every page must belong to the document translation run")
        unresolved_tables = [
            block.block_id
            for page in self.pages
            for block in page.blocks
            if block.type is BlockType.TABLE
            and block.segment_handling is SegmentHandling.MANUAL_INSERTION
            and not block.legacy_manual_table
        ]
        if unresolved_tables:
            raise ValueError("document contains tables awaiting reconstruction")
        blocks_by_id = {block.block_id: block for page in self.pages for block in page.blocks}
        for page in self.pages:
            for block in page.blocks:
                if block.continues_from_block_id is None:
                    continue
                previous = blocks_by_id.get(block.continues_from_block_id)
                if previous is None:
                    raise ValueError("paragraph continuation links an unknown block")
                if (
                    previous.type is not BlockType.BODY
                    or previous.original_page_number != block.original_page_number - 1
                ):
                    raise ValueError(
                        "paragraph continuation must link a body block on the previous page"
                    )
                previous_bodies = [
                    candidate
                    for candidate in self.pages[block.original_page_number - 2].blocks
                    if candidate.type is BlockType.BODY
                ]
                if not previous_bodies or previous_bodies[-1].block_id != previous.block_id:
                    raise ValueError(
                        "paragraph continuation must link the previous page's final body block"
                    )
        for block in blocks_by_id.values():
            previous_footnote_id = block.footnote_continues_from_block_id
            if previous_footnote_id is None:
                continue
            previous = blocks_by_id.get(previous_footnote_id)
            if previous is None:
                raise ValueError("footnote continuation links an unknown block")
            if (
                block.type is not BlockType.FOOTNOTE
                or previous.type is not BlockType.FOOTNOTE
                or previous.original_page_number != block.original_page_number - 1
                or previous.footnote_id != block.footnote_id
                or previous.continuation
                not in {
                    SegmentContinuation.TO_NEXT_PAGE,
                    SegmentContinuation.FROM_PREVIOUS_AND_TO_NEXT_PAGE,
                    SegmentContinuation.UNKNOWN,
                }
            ):
                raise ValueError(
                    "footnote continuation must reuse the same ID from the previous page"
                )
            if (
                block.footnote_owner_block_id != previous.footnote_owner_block_id
                or block.footnote_anchor_offset != previous.footnote_anchor_offset
                or block.footnote_owner_review_required != previous.footnote_owner_review_required
            ):
                raise ValueError("continued footnotes must retain their original entrypoint")
        return self
