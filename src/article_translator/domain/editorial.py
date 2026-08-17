from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from article_translator.domain.enums import (
    BlockType,
    ManualInsertionReason,
    ReviewStatus,
    SegmentContinuation,
    SegmentHandling,
)
from article_translator.domain.models import (
    BlockId,
    ContractModel,
    FootnoteDescription,
    FootnoteIdentity,
    NonEmptyText,
    Sha256,
    TranslationRunId,
    utc_now,
)


class BlockRevision(ContractModel):
    """One append-only correction scoped to an immutable machine translation run."""

    schema_version: Literal["1.0", "2.0"] = "2.0"
    revision_id: NonEmptyText
    document_id: Sha256
    translation_run_id: TranslationRunId
    block_id: NonEmptyText
    revision_number: int = Field(ge=1)
    base_revision: int = Field(ge=0)
    editorial_text: NonEmptyText
    effective_type: BlockType | None = None
    footnote_owner_block_id: BlockId | None = None
    footnote_anchor_offset: int | None = Field(default=None, ge=0)
    footnote_owner_review_required: bool | None = None
    status: ReviewStatus = ReviewStatus.UNREVIEWED
    editor: str | None = None
    note: str | None = None
    resolved_uncertainty_ids: list[NonEmptyText] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def revision_must_follow_expected_base(self) -> Self:
        if self.revision_number != self.base_revision + 1:
            raise ValueError("revision_number must be exactly one greater than base_revision")
        if len(self.resolved_uncertainty_ids) != len(set(self.resolved_uncertainty_ids)):
            raise ValueError("resolved_uncertainty_ids must be unique")
        if self.effective_type is None:
            if self.schema_version == "2.0":
                raise ValueError("schema 2.0 revisions require effective block metadata")
            if (
                self.footnote_owner_block_id is not None
                or self.footnote_anchor_offset is not None
                or self.footnote_owner_review_required is not None
            ):
                raise ValueError("legacy revisions cannot contain effective block metadata")
        elif self.schema_version == "1.0":
            raise ValueError("schema 1.0 revisions cannot contain effective block metadata")
        elif self.effective_type is BlockType.FOOTNOTE:
            has_owner = self.footnote_owner_block_id is not None
            if has_owner != (self.footnote_anchor_offset is not None):
                raise ValueError("a revised footnote owner and anchor offset belong together")
            if self.footnote_owner_review_required is None:
                raise ValueError("a revised footnote must state whether its owner needs review")
            if has_owner == self.footnote_owner_review_required:
                raise ValueError("a revised footnote must have either an owner or an owner warning")
        elif (
            self.footnote_owner_block_id is not None
            or self.footnote_anchor_offset is not None
            or self.footnote_owner_review_required not in {None, False}
        ):
            raise ValueError("footnote ownership metadata requires an effective footnote type")
        return self


class ReviewPosition(ContractModel):
    """Last physical page visited while reviewing one immutable translation run."""

    schema_version: Literal["1.0"] = "1.0"
    document_id: Sha256
    translation_run_id: TranslationRunId
    original_page_number: int = Field(ge=1)
    updated_at: datetime = Field(default_factory=utc_now)


class UncertaintyHighlight(ContractModel):
    """One stable, replaceable occurrence derived from immutable machine output."""

    highlight_mode: Literal["range"] = "range"
    offset_unit: Literal["unicode_codepoint"] = "unicode_codepoint"
    uncertainty_id: NonEmptyText
    term_group_id: Sha256
    source_term: NonEmptyText
    proposed_translation: NonEmptyText
    reason: NonEmptyText
    alternatives: list[str] = Field(default_factory=list)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=1)
    matching_occurrence_count: int = Field(ge=1)
    can_replace_all: bool

    @model_validator(mode="after")
    def offsets_must_describe_nonempty_text(self) -> Self:
        if self.end_offset <= self.start_offset:
            raise ValueError("end_offset must be greater than start_offset")
        return self


class UncertaintyFallback(ContractModel):
    """Whole-block fallback when an uncertainty cannot be aligned to text offsets."""

    highlight_mode: Literal["block"] = "block"
    uncertainty_id: NonEmptyText
    term_group_id: Sha256
    source_term: NonEmptyText
    proposed_translation: str | None = None
    reason: NonEmptyText
    alternatives: list[str] = Field(default_factory=list)


class ReviewBlock(ContractModel):
    """Side-by-side review projection for one immutable machine block."""

    document_id: Sha256
    translation_run_id: TranslationRunId
    block_id: NonEmptyText
    original_page_number: int = Field(ge=1)
    order: int = Field(ge=1)
    machine_type: BlockType
    type: BlockType
    segment_handling: SegmentHandling
    source_text: NonEmptyText | None
    machine_translated_text: NonEmptyText | None
    effective_translated_text: str
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
    latest_revision_number: int = Field(ge=0)
    review_status: ReviewStatus
    uncertainty_highlights: list[UncertaintyHighlight] = Field(default_factory=list)
    uncertainty_fallbacks: list[UncertaintyFallback] = Field(default_factory=list)

    @model_validator(mode="after")
    def highlights_must_match_effective_text(self) -> Self:
        if self.segment_handling is SegmentHandling.MANUAL_INSERTION:
            if self.type not in {BlockType.TABLE, BlockType.FIGURE}:
                raise ValueError("manual insertion review blocks must be tables or figures")
            if self.manual_insertion_reason is None or self.continuation is None:
                raise ValueError("manual insertion review blocks must retain segment metadata")
            if self.source_text is not None or self.machine_translated_text is not None:
                raise ValueError("manual insertion review blocks cannot contain machine text")
            if self.latest_revision_number == 0 and self.effective_translated_text:
                raise ValueError("unrevised manual insertion blocks must have empty effective text")
            if self.latest_revision_number > 0 and not self.effective_translated_text.strip():
                raise ValueError("revised manual insertion blocks must contain editorial text")
            if self.uncertainty_highlights or self.uncertainty_fallbacks:
                raise ValueError("manual insertion review blocks cannot contain text uncertainty")
        elif self.segment_handling is SegmentHandling.TABLE_RECONSTRUCTION:
            if self.type is not BlockType.TABLE:
                raise ValueError("reconstructed review blocks must be tables")
            if self.source_text is not None:
                raise ValueError("reconstructed table review blocks do not claim source text")
            if self.machine_translated_text is None or not self.effective_translated_text.strip():
                raise ValueError("reconstructed table review blocks require machine Markdown")
            if self.manual_insertion_reason not in {
                ManualInsertionReason.TABLE,
                ManualInsertionReason.TABLE_LIKE,
            }:
                raise ValueError("reconstructed tables must retain their table-region reason")
            if self.continuation is None:
                raise ValueError("reconstructed tables must retain continuation metadata")
        elif (
            self.source_text is None
            or self.machine_translated_text is None
            or not self.effective_translated_text.strip()
        ):
            raise ValueError("translated review blocks must contain source and translated text")
        elif self.manual_insertion_reason is not None:
            raise ValueError("translated review blocks cannot state a manual insertion reason")
        if (
            self.segment_handling is SegmentHandling.TRANSLATE
            and self.type is not BlockType.FOOTNOTE
            and (
                self.footnote_id is not None
                or self.footnote_description is not None
                or self.continuation is not None
                or self.footnote_owner_block_id is not None
                or self.footnote_anchor_offset is not None
                or self.footnote_owner_review_required
                or self.footnote_continues_from_block_id is not None
            )
        ):
            raise ValueError("footnote metadata is valid only for footnote review blocks")
        if self.type is BlockType.FOOTNOTE:
            has_owner = self.footnote_owner_block_id is not None
            if has_owner != (self.footnote_anchor_offset is not None):
                raise ValueError("a review footnote owner and anchor offset belong together")
            if has_owner == self.footnote_owner_review_required:
                raise ValueError("a review footnote must have either an owner or an owner warning")
        if self.type is not BlockType.BODY and (
            self.paragraph_continuation is not None or self.continues_from_block_id is not None
        ):
            raise ValueError("paragraph continuity is valid only for body review blocks")
        if self.continues_from_block_id is not None and self.paragraph_continuation not in {
            SegmentContinuation.FROM_PREVIOUS_PAGE,
            SegmentContinuation.FROM_PREVIOUS_AND_TO_NEXT_PAGE,
        }:
            raise ValueError("a linked review block must continue from the previous page")
        if (
            self.paragraph_continuation
            in {
                SegmentContinuation.FROM_PREVIOUS_PAGE,
                SegmentContinuation.FROM_PREVIOUS_AND_TO_NEXT_PAGE,
            }
            and self.continues_from_block_id is None
        ):
            raise ValueError("an incoming review paragraph must retain its block link")
        highlight_ids = [item.uncertainty_id for item in self.uncertainty_highlights]
        if len(highlight_ids) != len(set(highlight_ids)):
            raise ValueError("uncertainty highlight IDs must be unique within a block")
        fallback_ids = [item.uncertainty_id for item in self.uncertainty_fallbacks]
        if len(fallback_ids) != len(set(fallback_ids)):
            raise ValueError("fallback uncertainty IDs must be unique within a block")
        if set(highlight_ids).intersection(fallback_ids):
            raise ValueError("an uncertainty cannot be both highlighted and unlocated")
        for highlight in self.uncertainty_highlights:
            if highlight.end_offset > len(self.effective_translated_text):
                raise ValueError("uncertainty highlight exceeds effective translated text")
            observed = self.effective_translated_text[highlight.start_offset : highlight.end_offset]
            if observed != highlight.proposed_translation:
                raise ValueError("uncertainty highlight must select its proposed translation")
        return self


class ReviewPage(ContractModel):
    """Review blocks kept on their 1-based physical source page."""

    original_page_number: int = Field(ge=1)
    pdf_page_label: str | None = None
    detected_printed_page_label: str | None = None
    blocks: list[ReviewBlock] = Field(default_factory=list)

    @model_validator(mode="after")
    def blocks_must_match_page(self) -> Self:
        if any(block.original_page_number != self.original_page_number for block in self.blocks):
            raise ValueError("every review block must belong to its containing page")
        return self


class ReviewDocument(ContractModel):
    """Strict effective view consumed by the local review interface."""

    document_id: Sha256
    translation_run_id: TranslationRunId
    source_file_name: NonEmptyText
    page_count: int = Field(ge=1)
    pages: list[ReviewPage] = Field(min_length=1)

    @model_validator(mode="after")
    def pages_must_be_complete_and_ordered(self) -> Self:
        numbers = [page.original_page_number for page in self.pages]
        expected = list(range(1, self.page_count + 1))
        if numbers != expected:
            raise ValueError(f"review pages must be physical pages {expected}")
        blocks = [block for page in self.pages for block in page.blocks]
        block_ids = [block.block_id for block in blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("review block IDs must be unique within a run")
        if any(block.document_id != self.document_id for block in blocks):
            raise ValueError("every review block must belong to its containing document")
        if any(block.translation_run_id != self.translation_run_id for block in blocks):
            raise ValueError("every review block must belong to its containing translation run")
        return self
