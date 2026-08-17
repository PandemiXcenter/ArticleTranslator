from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from threading import RLock
from uuid import uuid4

from article_translator.application.compile_markdown import compile_markdown
from article_translator.application.export_reviewed import compile_latex, compile_text
from article_translator.domain.editorial import (
    BlockRevision,
    ReviewBlock,
    ReviewDocument,
    ReviewPage,
    ReviewPosition,
    UncertaintyFallback,
    UncertaintyHighlight,
)
from article_translator.domain.enums import BlockType, ReviewStatus, SegmentHandling
from article_translator.domain.errors import (
    EditorialError,
    EditorialTargetError,
    ReplaceAllUnavailableError,
    RevisionConflictError,
)
from article_translator.domain.models import (
    DocumentTranslation,
    MarkdownExportSettings,
    TranslatedBlock,
    UncertainTerm,
)
from article_translator.ports.editorial import EditorialRepository, RevisionRepository


@dataclass(frozen=True, slots=True)
class _UncertaintySpec:
    uncertainty_id: str
    term_group_id: str
    block_id: str
    source_term: str
    proposed_translation: str
    reason: str
    alternatives: tuple[str, ...]
    machine_start_offset: int
    machine_end_offset: int


@dataclass(frozen=True, slots=True)
class _FallbackSpec:
    uncertainty_id: str
    term_group_id: str
    source_term: str
    proposed_translation: str | None
    reason: str
    alternatives: tuple[str, ...]


_REVISION_LOCK = RLock()


class EditorialService:
    """Build effective review views and append optimistic editorial revisions."""

    def __init__(self, repository: EditorialRepository) -> None:
        self._repository = repository
        self._lock = _REVISION_LOCK

    def get_review_position(
        self,
        document: DocumentTranslation,
        translation_run_id: str,
    ) -> ReviewPosition | None:
        with self._lock:
            _assert_document_run(document, translation_run_id)
            position = self._repository.read_review_position(
                document.document_id,
                translation_run_id,
            )
            if position is not None:
                _assert_review_page(document, position.original_page_number)
            return position

    def save_review_position(
        self,
        document: DocumentTranslation,
        translation_run_id: str,
        original_page_number: int,
    ) -> ReviewPosition:
        with self._lock:
            _assert_document_run(document, translation_run_id)
            _assert_review_page(document, original_page_number)
            position = ReviewPosition(
                document_id=document.document_id,
                translation_run_id=translation_run_id,
                original_page_number=original_page_number,
            )
            self._repository.write_review_position(position)
            return position

    def review_document(
        self,
        document: DocumentTranslation,
        translation_run_id: str,
    ) -> ReviewDocument:
        with self._lock:
            return self._review_document_unlocked(document, translation_run_id)

    def revise_block(
        self,
        document: DocumentTranslation,
        translation_run_id: str,
        block_id: str,
        editorial_text: str,
        *,
        expected_base_revision: int,
        block_type: BlockType | None = None,
        footnote_owner_block_id: str | None = None,
        footnote_anchor_offset: int | None = None,
        status: ReviewStatus = ReviewStatus.IN_REVIEW,
        editor: str | None = None,
        note: str | None = None,
    ) -> BlockRevision:
        with self._lock:
            review = self._review_document_unlocked(document, translation_run_id)
            block = _find_review_block(review, block_id)
            self._assert_base_revision(block, expected_base_revision)
            preserve_metadata = block_type is None
            block_type = block_type or block.type
            if preserve_metadata:
                footnote_owner_block_id = block.footnote_owner_block_id
            if block.segment_handling is not SegmentHandling.TRANSLATE and block_type != block.type:
                raise EditorialTargetError(
                    "Table and manual-insertion block types cannot be changed in this editor"
                )
            if block_type in {BlockType.TABLE, BlockType.FIGURE} and block_type != block.type:
                raise EditorialTargetError(
                    "Use the dedicated table or figure workflow for that section type"
                )
            if block_type is BlockType.FOOTNOTE and any(
                candidate.block_id != block.block_id
                and candidate.footnote_owner_block_id == block.block_id
                for page in review.pages
                for candidate in page.blocks
            ):
                raise EditorialTargetError(
                    "Reassign this section's owned footnotes before changing it to a footnote"
                )
            owner_offset = None
            owner_review_required = False
            if block_type is BlockType.FOOTNOTE:
                if footnote_owner_block_id is None:
                    owner_review_required = True
                else:
                    owner = _find_review_block(review, footnote_owner_block_id)
                    if owner.block_id == block.block_id or owner.type is BlockType.FOOTNOTE:
                        raise EditorialTargetError(
                            "A footnote owner must be a different, non-footnote text section"
                        )
                    if owner.segment_handling is not SegmentHandling.TRANSLATE:
                        raise EditorialTargetError(
                            "A footnote owner must be an ordinary translated text section"
                        )
                    if footnote_anchor_offset is not None:
                        if footnote_anchor_offset > len(owner.effective_translated_text):
                            raise EditorialTargetError(
                                "The footnote marker position is outside the owner text"
                            )
                        owner_offset = footnote_anchor_offset
                    else:
                        owner_offset = (
                            block.footnote_anchor_offset
                            if footnote_owner_block_id == block.footnote_owner_block_id
                            and block.footnote_anchor_offset is not None
                            else len(owner.effective_translated_text)
                        )
            resolved_ids = _resolved_ids_for_block(
                self._repository,
                document.document_id,
                translation_run_id,
                block_id,
            )
            revision = _make_revision(
                document=document,
                translation_run_id=translation_run_id,
                block=block,
                editorial_text=editorial_text,
                effective_type=block_type,
                footnote_owner_block_id=footnote_owner_block_id,
                footnote_anchor_offset=owner_offset,
                footnote_owner_review_required=owner_review_required,
                status=status,
                editor=editor,
                note=note,
                resolved_uncertainty_ids=resolved_ids,
            )
            self._repository.append_block_revision(revision)
            return revision

    def replace_uncertainty(
        self,
        document: DocumentTranslation,
        translation_run_id: str,
        uncertainty_id: str,
        replacement: str,
        *,
        replace_all: bool,
        expected_base_revisions: Mapping[str, int],
        status: ReviewStatus = ReviewStatus.IN_REVIEW,
        editor: str | None = None,
        note: str | None = None,
    ) -> list[BlockRevision]:
        """Replace one highlight or every matching annotated occurrence.

        The command never calls ``str.replace`` over an entire block. It uses only
        the exact offsets exposed in the current review projection, so identical
        text introduced by a reviewer is not silently treated as model uncertainty.
        """

        with self._lock:
            review = self._review_document_unlocked(document, translation_run_id)
            selected = _find_highlight(review, uncertainty_id)
            matches = [
                (block, highlight)
                for block in _iter_review_blocks(review)
                for highlight in block.uncertainty_highlights
                if highlight.term_group_id == selected.term_group_id
            ]
            if replace_all:
                if len(matches) <= 1:
                    raise ReplaceAllUnavailableError(
                        "Translate All requires at least two unresolved annotated occurrences"
                    )
                targets = matches
            else:
                targets = [
                    (block, highlight)
                    for block, highlight in matches
                    if highlight.uncertainty_id == uncertainty_id
                ]

            targets_by_block: dict[str, list[UncertaintyHighlight]] = defaultdict(list)
            blocks: dict[str, ReviewBlock] = {}
            for block, highlight in targets:
                targets_by_block[block.block_id].append(highlight)
                blocks[block.block_id] = block

            for block_id, block in blocks.items():
                self._assert_base_revision(
                    block,
                    expected_base_revisions.get(block_id),
                )

            revisions: list[BlockRevision] = []
            for block_id, highlights in targets_by_block.items():
                block = blocks[block_id]
                editorial_text = _replace_highlight_offsets(
                    block.effective_translated_text,
                    highlights,
                    replacement,
                )
                resolved_ids = _resolved_ids_for_block(
                    self._repository,
                    document.document_id,
                    translation_run_id,
                    block_id,
                )
                resolved_ids.update(highlight.uncertainty_id for highlight in highlights)
                revisions.append(
                    _make_revision(
                        document=document,
                        translation_run_id=translation_run_id,
                        block=block,
                        editorial_text=editorial_text,
                        status=status,
                        editor=editor,
                        note=note,
                        resolved_uncertainty_ids=resolved_ids,
                    )
                )

            for revision in revisions:
                self._repository.append_block_revision(revision)
            return revisions

    def compile_reviewed_markdown(
        self,
        document: DocumentTranslation,
        translation_run_id: str,
        settings: MarkdownExportSettings,
    ) -> str:
        """Render the latest editorial text without mutating canonical machine data."""

        with self._lock:
            review = self._review_document_unlocked(document, translation_run_id)
            effective_text = {
                block.block_id: block.effective_translated_text
                for block in _iter_review_blocks(review)
            }
            return compile_markdown(
                document,
                settings,
                editorial_overrides=effective_text,
                type_overrides={
                    block.block_id: block.type for block in _iter_review_blocks(review)
                },
                footnote_owner_overrides={
                    block.block_id: (
                        block.footnote_owner_block_id,
                        block.footnote_anchor_offset,
                        block.footnote_owner_review_required,
                    )
                    for block in _iter_review_blocks(review)
                    if block.type is BlockType.FOOTNOTE and block.latest_revision_number > 0
                },
            )

    def compile_reviewed_text(
        self,
        document: DocumentTranslation,
        translation_run_id: str,
        settings: MarkdownExportSettings,
    ) -> str:
        """Render the latest editorial text directly as plain text."""

        with self._lock:
            review = self._review_document_unlocked(document, translation_run_id)
            return compile_text(
                document,
                settings,
                editorial_overrides=_effective_text_by_block(review),
                type_overrides={
                    block.block_id: block.type for block in _iter_review_blocks(review)
                },
            )

    def compile_reviewed_latex(
        self,
        document: DocumentTranslation,
        translation_run_id: str,
        settings: MarkdownExportSettings,
    ) -> str:
        """Render the latest editorial text directly as safe XeLaTeX source."""

        with self._lock:
            review = self._review_document_unlocked(document, translation_run_id)
            return compile_latex(
                document,
                settings,
                editorial_overrides=_effective_text_by_block(review),
                type_overrides={
                    block.block_id: block.type for block in _iter_review_blocks(review)
                },
                footnote_owner_overrides={
                    block.block_id: (
                        block.footnote_owner_block_id,
                        block.footnote_anchor_offset,
                        block.footnote_owner_review_required,
                    )
                    for block in _iter_review_blocks(review)
                    if block.type is BlockType.FOOTNOTE and block.latest_revision_number > 0
                },
            )

    def _review_document_unlocked(
        self,
        document: DocumentTranslation,
        translation_run_id: str,
    ) -> ReviewDocument:
        _assert_document_run(document, translation_run_id)
        block_states: dict[
            str,
            tuple[
                str,
                int,
                ReviewStatus,
                set[str],
                BlockType,
                str | None,
                int | None,
                bool,
            ],
        ] = {}
        specs_by_block: dict[str, list[_UncertaintySpec]] = {}
        fallbacks_by_block: dict[str, list[UncertaintyFallback]] = {}

        for block in _iter_machine_blocks(document):
            revisions = _validated_revisions(
                self._repository.list_block_revisions(
                    document.document_id,
                    translation_run_id,
                    block.block_id,
                ),
                document.document_id,
                translation_run_id,
                block.block_id,
            )
            if revisions:
                latest = revisions[-1]
                effective_text = latest.editorial_text
                latest_number = latest.revision_number
                status = latest.status
                resolved_ids = set(latest.resolved_uncertainty_ids)
                effective_type = latest.effective_type or block.type
                if latest.effective_type is None:
                    owner_block_id = block.footnote_owner_block_id
                    owner_offset = block.footnote_anchor_offset
                    owner_review_required = block.footnote_owner_review_required
                else:
                    owner_block_id = latest.footnote_owner_block_id
                    owner_offset = latest.footnote_anchor_offset
                    owner_review_required = bool(latest.footnote_owner_review_required)
            else:
                effective_text = block.translated_text or ""
                latest_number = 0
                status = ReviewStatus.UNREVIEWED
                resolved_ids = set()
                effective_type = block.type
                owner_block_id = block.footnote_owner_block_id
                owner_offset = block.footnote_anchor_offset
                owner_review_required = block.footnote_owner_review_required
            block_states[block.block_id] = (
                effective_text,
                latest_number,
                status,
                resolved_ids,
                effective_type,
                owner_block_id,
                owner_offset,
                owner_review_required,
            )
            specs, fallbacks = _machine_uncertainty_specs(block)
            specs_by_block[block.block_id] = specs
            fallbacks_by_block[block.block_id] = [
                _to_fallback(fallback)
                for fallback in fallbacks
                if fallback.uncertainty_id not in resolved_ids
            ]

        highlights_by_block: dict[str, list[UncertaintyHighlight]] = {}
        all_highlights: list[UncertaintyHighlight] = []
        for block in _iter_machine_blocks(document):
            effective_text, _, _, resolved_ids, _, _, _, _ = block_states[block.block_id]
            highlights, newly_unlocated = _locate_unresolved_specs(
                block.translated_text or "",
                effective_text,
                specs_by_block[block.block_id],
                resolved_ids,
            )
            highlights_by_block[block.block_id] = highlights
            fallbacks_by_block[block.block_id].extend(
                _to_fallback(fallback) for fallback in newly_unlocated
            )
            all_highlights.extend(highlights)

        occurrence_counts: dict[str, int] = defaultdict(int)
        for highlight in all_highlights:
            occurrence_counts[highlight.term_group_id] += 1
        for block_id, highlights in highlights_by_block.items():
            highlights_by_block[block_id] = [
                highlight.model_copy(
                    update={
                        "matching_occurrence_count": occurrence_counts[highlight.term_group_id],
                        "can_replace_all": occurrence_counts[highlight.term_group_id] > 1,
                    }
                )
                for highlight in highlights
            ]

        pages: list[ReviewPage] = []
        for page in document.pages:
            review_blocks: list[ReviewBlock] = []
            for block in page.blocks:
                (
                    effective_text,
                    revision_number,
                    status,
                    _,
                    effective_type,
                    owner_block_id,
                    owner_offset,
                    owner_review_required,
                ) = block_states[block.block_id]
                effective_is_footnote = effective_type is BlockType.FOOTNOTE
                review_blocks.append(
                    ReviewBlock(
                        document_id=document.document_id,
                        translation_run_id=translation_run_id,
                        block_id=block.block_id,
                        original_page_number=block.original_page_number,
                        order=block.order,
                        machine_type=block.type,
                        type=effective_type,
                        segment_handling=block.segment_handling,
                        source_text=block.source_text,
                        machine_translated_text=block.translated_text,
                        effective_translated_text=effective_text,
                        manual_insertion_reason=block.manual_insertion_reason,
                        footnote_id=(block.footnote_id if effective_is_footnote else None),
                        footnote_description=(
                            block.footnote_description if effective_is_footnote else None
                        ),
                        footnote_owner_block_id=owner_block_id,
                        footnote_anchor_offset=owner_offset,
                        footnote_owner_review_required=owner_review_required,
                        continuation=(
                            block.continuation
                            if effective_is_footnote
                            or block.segment_handling is not SegmentHandling.TRANSLATE
                            else None
                        ),
                        footnote_continues_from_block_id=(
                            block.footnote_continues_from_block_id
                            if effective_is_footnote
                            else None
                        ),
                        paragraph_continuation=(
                            block.paragraph_continuation
                            if effective_type is BlockType.BODY
                            else None
                        ),
                        continues_from_block_id=(
                            block.continues_from_block_id
                            if effective_type is BlockType.BODY
                            else None
                        ),
                        classification_review_required=block.classification_review_required,
                        latest_revision_number=revision_number,
                        review_status=status,
                        uncertainty_highlights=highlights_by_block[block.block_id],
                        uncertainty_fallbacks=fallbacks_by_block[block.block_id],
                    )
                )
            pages.append(
                ReviewPage(
                    original_page_number=page.original_page_number,
                    pdf_page_label=page.pdf_page_label,
                    detected_printed_page_label=page.detected_printed_page_label,
                    blocks=review_blocks,
                )
            )
        return ReviewDocument(
            document_id=document.document_id,
            translation_run_id=translation_run_id,
            source_file_name=document.source_file_name,
            page_count=document.page_count,
            pages=pages,
        )

    @staticmethod
    def _assert_base_revision(
        block: ReviewBlock,
        expected_base_revision: int | None,
    ) -> None:
        if expected_base_revision != block.latest_revision_number:
            raise RevisionConflictError(
                block.block_id,
                expected_base_revision,
                block.latest_revision_number,
            )


def _assert_document_run(
    document: DocumentTranslation,
    translation_run_id: str,
) -> None:
    if document.translation_run_id != translation_run_id:
        raise EditorialTargetError(
            f"Document belongs to translation run {document.translation_run_id}, "
            f"not {translation_run_id}"
        )


def _assert_review_page(
    document: DocumentTranslation,
    original_page_number: int,
) -> None:
    if original_page_number < 1 or original_page_number > document.page_count:
        raise EditorialTargetError(
            f"Physical page {original_page_number} is outside this "
            f"{document.page_count}-page document"
        )


def _iter_machine_blocks(document: DocumentTranslation) -> list[TranslatedBlock]:
    return [block for page in document.pages for block in page.blocks]


def _iter_review_blocks(document: ReviewDocument) -> list[ReviewBlock]:
    return [block for page in document.pages for block in page.blocks]


def _effective_text_by_block(document: ReviewDocument) -> dict[str, str]:
    return {
        block.block_id: block.effective_translated_text for block in _iter_review_blocks(document)
    }


def _find_review_block(document: ReviewDocument, block_id: str) -> ReviewBlock:
    matches = [block for block in _iter_review_blocks(document) if block.block_id == block_id]
    if len(matches) != 1:
        raise EditorialTargetError(f"Review block not found: {block_id}")
    return matches[0]


def _find_highlight(document: ReviewDocument, uncertainty_id: str) -> UncertaintyHighlight:
    matches = [
        highlight
        for block in _iter_review_blocks(document)
        for highlight in block.uncertainty_highlights
        if highlight.uncertainty_id == uncertainty_id
    ]
    if len(matches) != 1:
        raise EditorialTargetError(f"Uncertainty highlight not found: {uncertainty_id}")
    return matches[0]


def _validated_revisions(
    revisions: list[BlockRevision],
    document_id: str,
    translation_run_id: str,
    block_id: str,
) -> list[BlockRevision]:
    ordered = sorted(revisions, key=lambda revision: revision.revision_number)
    previous_number = 0
    previous_resolved: set[str] = set()
    for revision in ordered:
        if (
            revision.document_id != document_id
            or revision.translation_run_id != translation_run_id
            or revision.block_id != block_id
        ):
            raise EditorialError("Revision repository returned an incorrectly scoped revision")
        if (
            revision.base_revision != previous_number
            or revision.revision_number != previous_number + 1
        ):
            raise EditorialError(f"Revision history is not contiguous for block {block_id}")
        resolved = set(revision.resolved_uncertainty_ids)
        if not previous_resolved.issubset(resolved):
            raise EditorialError(f"Resolved uncertainties regressed for block {block_id}")
        previous_number = revision.revision_number
        previous_resolved = resolved
    return ordered


def _resolved_ids_for_block(
    repository: RevisionRepository,
    document_id: str,
    translation_run_id: str,
    block_id: str,
) -> set[str]:
    revisions = _validated_revisions(
        repository.list_block_revisions(document_id, translation_run_id, block_id),
        document_id,
        translation_run_id,
        block_id,
    )
    return set(revisions[-1].resolved_uncertainty_ids) if revisions else set()


def _machine_uncertainty_specs(
    block: TranslatedBlock,
) -> tuple[list[_UncertaintySpec], list[_FallbackSpec]]:
    if block.translated_text is None:
        return [], []
    specs: list[_UncertaintySpec] = []
    unlocated: list[_FallbackSpec] = []
    seen_terms: set[tuple[str, str | None]] = set()
    for uncertainty_number, uncertainty in enumerate(block.uncertainties, start=1):
        term_key = (uncertainty.source_term, uncertainty.proposed_translation)
        if term_key in seen_terms:
            continue
        seen_terms.add(term_key)
        group_id = _term_group_id(uncertainty)
        proposed = uncertainty.proposed_translation
        if not proposed:
            unlocated.append(
                _FallbackSpec(
                    uncertainty_id=f"{block.block_id}-u{uncertainty_number:04d}-o0000",
                    term_group_id=group_id,
                    source_term=uncertainty.source_term,
                    proposed_translation=proposed,
                    reason=uncertainty.reason,
                    alternatives=tuple(uncertainty.alternatives),
                )
            )
            continue
        positions = _find_occurrences(block.translated_text, proposed)
        if not positions:
            unlocated.append(
                _FallbackSpec(
                    uncertainty_id=f"{block.block_id}-u{uncertainty_number:04d}-o0000",
                    term_group_id=group_id,
                    source_term=uncertainty.source_term,
                    proposed_translation=proposed,
                    reason=uncertainty.reason,
                    alternatives=tuple(uncertainty.alternatives),
                )
            )
            continue
        for occurrence_number, (start, end) in enumerate(positions, start=1):
            specs.append(
                _UncertaintySpec(
                    uncertainty_id=(
                        f"{block.block_id}-u{uncertainty_number:04d}-o{occurrence_number:04d}"
                    ),
                    term_group_id=group_id,
                    block_id=block.block_id,
                    source_term=uncertainty.source_term,
                    proposed_translation=proposed,
                    reason=uncertainty.reason,
                    alternatives=tuple(uncertainty.alternatives),
                    machine_start_offset=start,
                    machine_end_offset=end,
                )
            )
    return specs, unlocated


def _term_group_id(uncertainty: UncertainTerm) -> str:
    serialized = json.dumps(
        [uncertainty.source_term, uncertainty.proposed_translation],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _find_occurrences(text: str, needle: str) -> list[tuple[int, int]]:
    if not needle:
        return []
    positions: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index < 0:
            return positions
        end = index + len(needle)
        positions.append((index, end))
        start = end


def _locate_unresolved_specs(
    machine_text: str,
    effective_text: str,
    specs: list[_UncertaintySpec],
    resolved_ids: set[str],
) -> tuple[list[UncertaintyHighlight], list[_FallbackSpec]]:
    specs_by_group: dict[str, list[_UncertaintySpec]] = defaultdict(list)
    for spec in specs:
        if spec.uncertainty_id not in resolved_ids:
            specs_by_group[spec.term_group_id].append(spec)

    highlights: list[UncertaintyHighlight] = []
    unlocated: list[_FallbackSpec] = []
    equal_spans = [
        (machine_start, machine_end, effective_start, effective_end)
        for tag, machine_start, machine_end, effective_start, effective_end in SequenceMatcher(
            None,
            machine_text,
            effective_text,
            autojunk=False,
        ).get_opcodes()
        if tag == "equal"
    ]
    for grouped_specs in specs_by_group.values():
        used_positions: set[tuple[int, int]] = set()
        pending_specs: list[_UncertaintySpec] = []
        located: list[tuple[_UncertaintySpec, int, int]] = []
        for spec in grouped_specs:
            mapped = _map_equal_machine_span(spec, equal_spans)
            if (
                mapped is not None
                and effective_text[mapped[0] : mapped[1]] == spec.proposed_translation
            ):
                used_positions.add(mapped)
                located.append((spec, mapped[0], mapped[1]))
            else:
                pending_specs.append(spec)
        available_positions = [
            position
            for position in _find_occurrences(
                effective_text,
                grouped_specs[0].proposed_translation,
            )
            if position not in used_positions
        ]
        for spec, (start, end) in zip(pending_specs, available_positions, strict=False):
            located.append((spec, start, end))
        unlocated.extend(
            _FallbackSpec(
                uncertainty_id=spec.uncertainty_id,
                term_group_id=spec.term_group_id,
                source_term=spec.source_term,
                proposed_translation=spec.proposed_translation,
                reason=spec.reason,
                alternatives=spec.alternatives,
            )
            for spec in pending_specs[len(available_positions) :]
        )
        for spec, start, end in located:
            highlights.append(
                UncertaintyHighlight(
                    uncertainty_id=spec.uncertainty_id,
                    term_group_id=spec.term_group_id,
                    source_term=spec.source_term,
                    proposed_translation=spec.proposed_translation,
                    reason=spec.reason,
                    alternatives=list(spec.alternatives),
                    start_offset=start,
                    end_offset=end,
                    matching_occurrence_count=1,
                    can_replace_all=False,
                )
            )
    return (
        sorted(highlights, key=lambda highlight: (highlight.start_offset, highlight.end_offset)),
        unlocated,
    )


def _to_fallback(spec: _FallbackSpec) -> UncertaintyFallback:
    return UncertaintyFallback(
        uncertainty_id=spec.uncertainty_id,
        term_group_id=spec.term_group_id,
        source_term=spec.source_term,
        proposed_translation=spec.proposed_translation,
        reason=spec.reason,
        alternatives=list(spec.alternatives),
    )


def _map_equal_machine_span(
    spec: _UncertaintySpec,
    equal_spans: list[tuple[int, int, int, int]],
) -> tuple[int, int] | None:
    for machine_start, machine_end, effective_start, _ in equal_spans:
        if machine_start <= spec.machine_start_offset and spec.machine_end_offset <= machine_end:
            start = effective_start + spec.machine_start_offset - machine_start
            return start, start + len(spec.proposed_translation)
    return None


def _replace_highlight_offsets(
    text: str,
    highlights: list[UncertaintyHighlight],
    replacement: str,
) -> str:
    if not replacement.strip():
        raise EditorialError("Replacement must not be blank")
    result = text
    for highlight in sorted(highlights, key=lambda item: item.start_offset, reverse=True):
        observed = result[highlight.start_offset : highlight.end_offset]
        if observed != highlight.proposed_translation:
            raise EditorialError("Uncertainty offsets no longer match the effective text")
        result = result[: highlight.start_offset] + replacement + result[highlight.end_offset :]
    return result


def _make_revision(
    *,
    document: DocumentTranslation,
    translation_run_id: str,
    block: ReviewBlock,
    editorial_text: str,
    effective_type: BlockType | None = None,
    footnote_owner_block_id: str | None = None,
    footnote_anchor_offset: int | None = None,
    footnote_owner_review_required: bool | None = None,
    status: ReviewStatus,
    editor: str | None,
    note: str | None,
    resolved_uncertainty_ids: set[str],
) -> BlockRevision:
    return BlockRevision(
        schema_version="2.0",
        revision_id=f"revision-{uuid4()}",
        document_id=document.document_id,
        translation_run_id=translation_run_id,
        block_id=block.block_id,
        revision_number=block.latest_revision_number + 1,
        base_revision=block.latest_revision_number,
        editorial_text=editorial_text,
        effective_type=effective_type or block.type,
        footnote_owner_block_id=(
            footnote_owner_block_id if effective_type is not None else block.footnote_owner_block_id
        ),
        footnote_anchor_offset=(
            footnote_anchor_offset if effective_type is not None else block.footnote_anchor_offset
        ),
        footnote_owner_review_required=(
            footnote_owner_review_required
            if effective_type is not None
            else block.footnote_owner_review_required
        ),
        status=status,
        editor=editor,
        note=note,
        resolved_uncertainty_ids=sorted(resolved_uncertainty_ids),
    )
