from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from article_translator.domain.enums import BlockType, ReviewStatus
from article_translator.domain.models import (
    ContractModel,
    NonEmptyText,
    Sha256,
    TranslationRunId,
    utc_now,
)


class BlockRevision(ContractModel):
    """One append-only correction scoped to an immutable machine translation run."""

    schema_version: Literal["1.0"] = "1.0"
    revision_id: NonEmptyText
    document_id: Sha256
    translation_run_id: TranslationRunId
    block_id: NonEmptyText
    revision_number: int = Field(ge=1)
    base_revision: int = Field(ge=0)
    editorial_text: NonEmptyText
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
        return self


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
    type: BlockType
    source_text: NonEmptyText
    machine_translated_text: NonEmptyText
    effective_translated_text: NonEmptyText
    latest_revision_number: int = Field(ge=0)
    review_status: ReviewStatus
    uncertainty_highlights: list[UncertaintyHighlight] = Field(default_factory=list)
    uncertainty_fallbacks: list[UncertaintyFallback] = Field(default_factory=list)

    @model_validator(mode="after")
    def highlights_must_match_effective_text(self) -> Self:
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
