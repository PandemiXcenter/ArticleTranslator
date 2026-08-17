from __future__ import annotations

from datetime import UTC, datetime

import pytest

from article_translator.application.editorial import EditorialService
from article_translator.domain.editorial import BlockRevision, ReviewPosition
from article_translator.domain.enums import (
    BlockType,
    ExtractionStatus,
    ManualInsertionReason,
    SegmentContinuation,
    SegmentHandling,
)
from article_translator.domain.errors import (
    EditorialTargetError,
    ReplaceAllUnavailableError,
    RevisionConflictError,
)
from article_translator.domain.models import (
    ArtifactRef,
    DocumentTranslation,
    MarkdownExportSettings,
    PageTranslation,
    ProviderMetadata,
    TableReconstructionMetadata,
    TranslatedBlock,
    TranslationSettings,
    UncertainTerm,
)

HASH = "a" * 64
RUN_ID = "1" * 32


class MemoryRevisionRepository:
    def __init__(self) -> None:
        self.revisions: dict[tuple[str, str, str], list[BlockRevision]] = {}
        self.position: ReviewPosition | None = None

    def list_block_revisions(
        self,
        document_id: str,
        translation_run_id: str,
        block_id: str,
    ) -> list[BlockRevision]:
        return list(self.revisions.get((document_id, translation_run_id, block_id), []))

    def append_block_revision(self, revision: BlockRevision) -> None:
        key = (revision.document_id, revision.translation_run_id, revision.block_id)
        self.revisions.setdefault(key, []).append(revision)

    def read_review_position(
        self,
        document_id: str,
        translation_run_id: str,
    ) -> ReviewPosition | None:
        if self.position is None:
            return None
        assert self.position.document_id == document_id
        assert self.position.translation_run_id == translation_run_id
        return self.position

    def write_review_position(self, position: ReviewPosition) -> None:
        self.position = position


def _artifact(path: str, media_type: str) -> ArtifactRef:
    return ArtifactRef(path=path, sha256=HASH, media_type=media_type, byte_count=10)


def _block(
    block_id: str,
    order: int,
    source: str,
    translation: str,
    *,
    uncertain_source: str | None = None,
    uncertain_translation: str | None = None,
) -> TranslatedBlock:
    uncertainties = []
    if uncertain_source is not None:
        uncertainties.append(
            UncertainTerm(
                source_term=uncertain_source,
                proposed_translation=uncertain_translation,
                reason="Archaic usage",
                alternatives=["alternative"],
            )
        )
    return TranslatedBlock(
        block_id=block_id,
        original_page_number=1,
        order=order,
        type=BlockType.BODY,
        source_text=source,
        translated_text=translation,
        uncertainties=uncertainties,
    )


def _manual_table_block() -> TranslatedBlock:
    return TranslatedBlock(
        block_id="p0001-b0001",
        original_page_number=1,
        order=1,
        type=BlockType.TABLE,
        source_text=None,
        translated_text=None,
        segment_handling=SegmentHandling.MANUAL_INSERTION,
        manual_insertion_reason=ManualInsertionReason.TABLE,
        continuation=SegmentContinuation.COMPLETE,
        classification_review_required=True,
        legacy_manual_table=True,
    )


def _document(*blocks: TranslatedBlock) -> DocumentTranslation:
    page_fields: dict[str, object] = {
        "original_page_number": 1,
        "pdf_page_label": "i",
        "detected_printed_page_label": "1",
        "extraction_status": ExtractionStatus.EXTRACTED,
        "extracted_character_count": 20,
        "source_markdown": "source",
        "source_markdown_artifact": _artifact("prepared/source.md", "text/markdown"),
        "source_image": _artifact("prepared/page.png", "image/png"),
        "blocks": list(blocks),
        "input_fingerprint": HASH,
        "provider": ProviderMetadata(
            provider="fake",
            model="fake-model",
            prompt_version="test",
        ),
        "translated_at": datetime(2026, 1, 1, tzinfo=UTC),
        "translation_run_id": RUN_ID,
    }
    reconstructed_ids = [
        block.block_id
        for block in blocks
        if block.segment_handling is SegmentHandling.TABLE_RECONSTRUCTION
    ]
    if reconstructed_ids:
        page_fields["table_reconstruction"] = TableReconstructionMetadata(
            input_fingerprint=HASH,
            block_ids=reconstructed_ids,
            provider=ProviderMetadata(
                provider="fake",
                model="fake-model",
                prompt_version="reconstruct-tables-v1",
            ),
            reconstructed_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    document_fields: dict[str, object] = {
        "document_id": HASH,
        "job_id": "job-one",
        "source_file_name": "source.pdf",
        "source_file_sha256": HASH,
        "page_count": 1,
        "translation_settings": TranslationSettings(),
        "pages": [PageTranslation.model_validate(page_fields)],
        "translation_run_id": RUN_ID,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    return DocumentTranslation.model_validate(document_fields)


def test_review_projection_keeps_source_machine_text_and_physical_page() -> None:
    document = _document(
        _block(
            "p0001-b0001",
            1,
            "gammel og gammel",
            "olde and olde",
            uncertain_source="gammel",
            uncertain_translation="olde",
        )
    )
    service = EditorialService(MemoryRevisionRepository())

    review = service.review_document(document, RUN_ID)
    block = review.pages[0].blocks[0]

    assert review.pages[0].original_page_number == 1
    assert block.source_text == "gammel og gammel"
    assert block.machine_translated_text == "olde and olde"
    assert block.effective_translated_text == "olde and olde"
    assert [item.uncertainty_id for item in block.uncertainty_highlights] == [
        "p0001-b0001-u0001-o0001",
        "p0001-b0001-u0001-o0002",
    ]
    assert all(item.can_replace_all for item in block.uncertainty_highlights)


def test_review_position_is_run_scoped_and_validated_against_physical_pages() -> None:
    document = _document(_block("p0001-b0001", 1, "kilde", "machine"))
    repository = MemoryRevisionRepository()
    service = EditorialService(repository)

    assert service.get_review_position(document, RUN_ID) is None

    saved = service.save_review_position(document, RUN_ID, 1)

    assert saved.document_id == document.document_id
    assert saved.translation_run_id == RUN_ID
    assert saved.original_page_number == 1
    assert service.get_review_position(document, RUN_ID) == saved

    with pytest.raises(EditorialTargetError, match="outside this 1-page document"):
        service.save_review_position(document, RUN_ID, 2)
    with pytest.raises(EditorialTargetError, match="belongs to translation run"):
        service.save_review_position(document, "2" * 32, 1)


def test_out_of_range_persisted_review_position_is_rejected() -> None:
    document = _document(_block("p0001-b0001", 1, "kilde", "machine"))
    repository = MemoryRevisionRepository()
    repository.position = ReviewPosition(
        document_id=document.document_id,
        translation_run_id=RUN_ID,
        original_page_number=2,
    )

    with pytest.raises(EditorialTargetError, match="outside this 1-page document"):
        EditorialService(repository).get_review_position(document, RUN_ID)


def test_highlight_offsets_are_explicit_unicode_codepoint_indexes() -> None:
    document = _document(
        _block(
            "p0001-b0001",
            1,
            "gammel",
            "🙂 olde",
            uncertain_source="gammel",
            uncertain_translation="olde",
        )
    )

    highlight = (
        EditorialService(MemoryRevisionRepository())
        .review_document(
            document,
            RUN_ID,
        )
        .pages[0]
        .blocks[0]
        .uncertainty_highlights[0]
    )

    assert highlight.offset_unit == "unicode_codepoint"
    assert (highlight.start_offset, highlight.end_offset) == (2, 6)


def test_unlocated_uncertainty_is_a_structured_whole_block_fallback() -> None:
    document = _document(
        _block(
            "p0001-b0001",
            1,
            "ukendt",
            "rendering",
            uncertain_source="ukendt",
            uncertain_translation=None,
        )
    )

    block = (
        EditorialService(MemoryRevisionRepository())
        .review_document(
            document,
            RUN_ID,
        )
        .pages[0]
        .blocks[0]
    )

    assert block.uncertainty_highlights == []
    assert len(block.uncertainty_fallbacks) == 1
    fallback = block.uncertainty_fallbacks[0]
    assert fallback.highlight_mode == "block"
    assert fallback.source_term == "ukendt"
    assert fallback.proposed_translation is None
    assert fallback.reason == "Archaic usage"
    assert fallback.alternatives == ["alternative"]


def test_editorial_text_that_cannot_be_aligned_retains_uncertainty_details() -> None:
    document = _document(
        _block(
            "p0001-b0001",
            1,
            "gammel",
            "olde",
            uncertain_source="gammel",
            uncertain_translation="olde",
        )
    )
    service = EditorialService(MemoryRevisionRepository())
    service.revise_block(
        document,
        RUN_ID,
        "p0001-b0001",
        "a completely different rendering",
        expected_base_revision=0,
    )

    block = service.review_document(document, RUN_ID).pages[0].blocks[0]

    assert block.uncertainty_highlights == []
    assert block.uncertainty_fallbacks[0].uncertainty_id.endswith("-o0001")
    assert block.uncertainty_fallbacks[0].proposed_translation == "olde"
    assert block.uncertainty_fallbacks[0].reason == "Archaic usage"


def test_manual_revision_is_append_only_and_uses_optimistic_base() -> None:
    document = _document(_block("p0001-b0001", 1, "kilde", "machine"))
    repository = MemoryRevisionRepository()
    service = EditorialService(repository)

    revision = service.revise_block(
        document,
        RUN_ID,
        "p0001-b0001",
        "reviewed",
        expected_base_revision=0,
    )

    assert revision.revision_number == 1
    review = service.review_document(document, RUN_ID)
    assert review.pages[0].blocks[0].machine_translated_text == "machine"
    assert review.pages[0].blocks[0].effective_translated_text == "reviewed"
    assert document.pages[0].blocks[0].translated_text == "machine"

    with pytest.raises(RevisionConflictError, match="expected revision 0"):
        service.revise_block(
            document,
            RUN_ID,
            "p0001-b0001",
            "stale edit",
            expected_base_revision=0,
        )


def test_manual_insertion_starts_empty_and_exports_only_reviewer_text() -> None:
    document = _document(_manual_table_block())
    service = EditorialService(MemoryRevisionRepository())

    initial = service.review_document(document, RUN_ID).pages[0].blocks[0]

    assert initial.segment_handling is SegmentHandling.MANUAL_INSERTION
    assert initial.source_text is None
    assert initial.machine_translated_text is None
    assert initial.effective_translated_text == ""
    assert initial.manual_insertion_reason is ManualInsertionReason.TABLE
    assert initial.continuation is SegmentContinuation.COMPLETE
    assert initial.classification_review_required is True

    unresolved = service.compile_reviewed_markdown(
        document,
        RUN_ID,
        MarkdownExportSettings(include_page_comments=False),
    )
    assert "Manual insertion required" in unresolved

    service.revise_block(
        document,
        RUN_ID,
        "p0001-b0001",
        "| Age | Cases |\n| --- | --- |\n| 20 | 4 |",
        expected_base_revision=0,
    )

    current = service.review_document(document, RUN_ID).pages[0].blocks[0]
    assert current.machine_translated_text is None
    assert current.effective_translated_text.startswith("| Age |")
    reviewed = service.compile_reviewed_markdown(
        document,
        RUN_ID,
        MarkdownExportSettings(include_page_comments=False),
    )
    assert reviewed == (
        "<!-- table-placement: [H!]; original-page: 1; block-id: p0001-b0001 -->\n"
        "| Age | Cases |\n| --- | --- |\n| 20 | 4 |\n"
    )
    assert document.pages[0].blocks[0].translated_text is None


def test_reconstructed_table_starts_from_machine_markdown_and_keeps_revision_history() -> None:
    machine = "| Date | Deaths |\n| --- | ---: |\n| 3 July | 3 |"
    table = TranslatedBlock(
        block_id="p0001-b0001",
        original_page_number=1,
        order=1,
        type=BlockType.TABLE,
        source_text=None,
        translated_text=machine,
        segment_handling=SegmentHandling.TABLE_RECONSTRUCTION,
        manual_insertion_reason=ManualInsertionReason.TABLE_LIKE,
        continuation=SegmentContinuation.COMPLETE,
    )
    document = _document(table)
    service = EditorialService(MemoryRevisionRepository())

    initial = service.review_document(document, RUN_ID).pages[0].blocks[0]
    assert initial.segment_handling is SegmentHandling.TABLE_RECONSTRUCTION
    assert initial.source_text is None
    assert initial.machine_translated_text == machine
    assert initial.effective_translated_text == machine

    revised = f"{machine}\n| 4 July | 3 |"
    revision = service.revise_block(
        document,
        RUN_ID,
        table.block_id,
        revised,
        expected_base_revision=0,
    )

    assert revision.revision_number == 1
    current = service.review_document(document, RUN_ID).pages[0].blocks[0]
    assert current.machine_translated_text == machine
    assert current.effective_translated_text == revised
    assert document.pages[0].blocks[0].translated_text == machine
    assert service.compile_reviewed_markdown(
        document,
        RUN_ID,
        MarkdownExportSettings(include_page_comments=False),
    ) == (f"<!-- table-placement: [H!]; original-page: 1; block-id: p0001-b0001 -->\n{revised}\n")


def test_translate_all_changes_only_machine_annotated_occurrences() -> None:
    document = _document(
        _block(
            "p0001-b0001",
            1,
            "gammel og gammel",
            "olde and olde",
            uncertain_source="gammel",
            uncertain_translation="olde",
        )
    )
    repository = MemoryRevisionRepository()
    service = EditorialService(repository)
    service.revise_block(
        document,
        RUN_ID,
        "p0001-b0001",
        "olde and olde plus olde",
        expected_base_revision=0,
    )
    review = service.review_document(document, RUN_ID)
    highlights = review.pages[0].blocks[0].uncertainty_highlights

    assert len(highlights) == 2
    revisions = service.replace_uncertainty(
        document,
        RUN_ID,
        highlights[0].uncertainty_id,
        "modern",
        replace_all=True,
        expected_base_revisions={"p0001-b0001": 1},
    )

    assert len(revisions) == 1
    assert revisions[0].editorial_text == "modern and modern plus olde"
    assert revisions[0].resolved_uncertainty_ids == [
        "p0001-b0001-u0001-o0001",
        "p0001-b0001-u0001-o0002",
    ]


def test_translate_one_removes_one_highlight_and_disables_translate_all() -> None:
    document = _document(
        _block(
            "p0001-b0001",
            1,
            "gammel og gammel",
            "olde and olde",
            uncertain_source="gammel",
            uncertain_translation="olde",
        )
    )
    service = EditorialService(MemoryRevisionRepository())
    initial = service.review_document(document, RUN_ID).pages[0].blocks[0]

    service.replace_uncertainty(
        document,
        RUN_ID,
        initial.uncertainty_highlights[0].uncertainty_id,
        "modern",
        replace_all=False,
        expected_base_revisions={"p0001-b0001": 0},
    )
    current = service.review_document(document, RUN_ID).pages[0].blocks[0]

    assert current.effective_translated_text == "modern and olde"
    assert len(current.uncertainty_highlights) == 1
    assert not current.uncertainty_highlights[0].can_replace_all
    with pytest.raises(ReplaceAllUnavailableError, match="at least two"):
        service.replace_uncertainty(
            document,
            RUN_ID,
            current.uncertainty_highlights[0].uncertainty_id,
            "new",
            replace_all=True,
            expected_base_revisions={"p0001-b0001": 1},
        )


def test_translate_all_can_group_matching_annotations_across_blocks() -> None:
    document = _document(
        _block(
            "p0001-b0001",
            1,
            "gammel",
            "olde",
            uncertain_source="gammel",
            uncertain_translation="olde",
        ),
        _block(
            "p0001-b0002",
            2,
            "gammel",
            "olde",
            uncertain_source="gammel",
            uncertain_translation="olde",
        ),
    )
    service = EditorialService(MemoryRevisionRepository())
    review = service.review_document(document, RUN_ID)
    selected = review.pages[0].blocks[0].uncertainty_highlights[0]

    revisions = service.replace_uncertainty(
        document,
        RUN_ID,
        selected.uncertainty_id,
        "modern",
        replace_all=True,
        expected_base_revisions={"p0001-b0001": 0, "p0001-b0002": 0},
    )

    assert [revision.block_id for revision in revisions] == [
        "p0001-b0001",
        "p0001-b0002",
    ]
    assert all(revision.editorial_text == "modern" for revision in revisions)


def test_reviewed_markdown_projects_effective_text_without_mutating_machine_data() -> None:
    document = _document(_block("p0001-b0001", 1, "kilde", "machine"))
    service = EditorialService(MemoryRevisionRepository())
    service.revise_block(
        document,
        RUN_ID,
        "p0001-b0001",
        "reviewed",
        expected_base_revision=0,
    )

    markdown = service.compile_reviewed_markdown(
        document,
        RUN_ID,
        MarkdownExportSettings(
            include_page_comments=False,
            include_headers=False,
            include_footers=False,
            include_page_numbers=False,
        ),
    )

    assert markdown == "reviewed\n"
    assert document.pages[0].blocks[0].translated_text == "machine"


def test_reviewed_text_and_latex_project_effective_text_directly() -> None:
    document = _document(_block("p0001-b0001", 1, "kilde", "machine & old"))
    service = EditorialService(MemoryRevisionRepository())
    service.revise_block(
        document,
        RUN_ID,
        "p0001-b0001",
        "reviewed & current",
        expected_base_revision=0,
    )
    settings = MarkdownExportSettings(include_page_comments=True)

    plain_text = service.compile_reviewed_text(document, RUN_ID, settings)
    latex = service.compile_reviewed_latex(document, RUN_ID, settings)

    assert plain_text == "reviewed & current\n"
    assert "machine" not in latex
    assert "reviewed \\& current\\par" in latex
    assert "% original-page: 1" in latex
    assert latex.startswith("\\documentclass[11pt,a4paper]{article}")
    assert latex.endswith("\\end{document}\n")


def test_reviewed_latex_converts_canonical_gfm_table_without_floating() -> None:
    machine = "| Date | Deaths |\n| --- | ---: |\n| 3 July | 3 |"
    table = TranslatedBlock(
        block_id="p0001-b0001",
        original_page_number=1,
        order=1,
        type=BlockType.TABLE,
        source_text=None,
        translated_text=machine,
        segment_handling=SegmentHandling.TABLE_RECONSTRUCTION,
        manual_insertion_reason=ManualInsertionReason.TABLE,
        continuation=SegmentContinuation.COMPLETE,
    )
    service = EditorialService(MemoryRevisionRepository())

    latex = service.compile_reviewed_latex(
        _document(table),
        RUN_ID,
        MarkdownExportSettings(include_page_comments=False),
    )

    assert "% table-placement: [H!]; original-page: 1; block-id: p0001-b0001" in latex
    assert "\\begin{longtable}" in latex
    assert "\\textbf{Date} & \\textbf{Deaths}" in latex
    assert "3 July & 3" in latex
    assert "\\begin{table}" not in latex


def test_reviewed_latex_places_owned_footnote_inline() -> None:
    owner = _block(
        "p0001-b0001",
        1,
        "Tekst* fortsætter.",
        "Text continues.",
    )
    footnote = TranslatedBlock(
        block_id="p0001-b0002",
        original_page_number=1,
        order=2,
        type=BlockType.FOOTNOTE,
        source_text="Notetekst.",
        translated_text="Note & explanation.",
        footnote_marker="*",
        footnote_owner_block_id=owner.block_id,
        footnote_anchor_offset=4,
        footnote_owner_review_required=False,
        continuation=SegmentContinuation.COMPLETE,
    )

    latex = EditorialService(MemoryRevisionRepository()).compile_reviewed_latex(
        _document(owner, footnote),
        RUN_ID,
        MarkdownExportSettings(include_page_comments=False),
    )

    assert "Text\\footnote{Note \\& explanation.} continues." in latex
    assert "owner requires review" not in latex


def test_editor_can_change_section_type_and_assign_footnote_owner_append_only() -> None:
    owner = _block("p0001-b0001", 1, "Ejer", "Owner text")
    candidate = _block("p0001-b0002", 2, "Note", "Candidate note")
    document = _document(owner, candidate)
    repository = MemoryRevisionRepository()
    service = EditorialService(repository)

    revision = service.revise_block(
        document,
        RUN_ID,
        candidate.block_id,
        "Reviewed note",
        block_type=BlockType.FOOTNOTE,
        footnote_owner_block_id=owner.block_id,
        footnote_anchor_offset=5,
        expected_base_revision=0,
    )
    reviewed = service.review_document(document, RUN_ID).pages[0].blocks[1]

    assert revision.effective_type is BlockType.FOOTNOTE
    assert revision.footnote_owner_block_id == owner.block_id
    assert revision.footnote_anchor_offset == 5
    assert revision.schema_version == "2.0"
    assert reviewed.machine_type is BlockType.BODY
    assert reviewed.type is BlockType.FOOTNOTE
    assert reviewed.footnote_owner_block_id == owner.block_id
    assert reviewed.footnote_owner_review_required is False
    assert document.pages[0].blocks[1].type is BlockType.BODY
    assert "Owner\\footnote{Reviewed note} text" in service.compile_reviewed_latex(
        document,
        RUN_ID,
        MarkdownExportSettings(include_page_comments=False),
    )
    markdown = service.compile_reviewed_markdown(
        document,
        RUN_ID,
        MarkdownExportSettings(include_page_comments=False),
    )
    assert "Owner[^p0001-b0002] text" in markdown
    assert "[^p0001-b0002]: Reviewed note" in markdown

    service.revise_block(
        document,
        RUN_ID,
        candidate.block_id,
        "Reviewed note",
        block_type=BlockType.FOOTNOTE,
        footnote_owner_block_id=None,
        expected_base_revision=1,
    )
    unresolved = service.review_document(document, RUN_ID).pages[0].blocks[1]
    assert unresolved.footnote_owner_block_id is None
    assert unresolved.footnote_owner_review_required is True


def test_owner_with_attached_footnote_cannot_be_retyped_as_footnote() -> None:
    owner = _block("p0001-b0001", 1, "Ejer", "Owner")
    footnote = TranslatedBlock(
        block_id="p0001-b0002",
        original_page_number=1,
        order=2,
        type=BlockType.FOOTNOTE,
        source_text="Note",
        translated_text="Footnote",
        footnote_owner_block_id=owner.block_id,
        footnote_anchor_offset=5,
        footnote_owner_review_required=False,
        continuation=SegmentContinuation.COMPLETE,
    )
    service = EditorialService(MemoryRevisionRepository())

    with pytest.raises(EditorialTargetError, match="Reassign"):
        service.revise_block(
            _document(owner, footnote),
            RUN_ID,
            owner.block_id,
            "Owner",
            block_type=BlockType.FOOTNOTE,
            expected_base_revision=0,
        )


def test_retyping_body_as_footnote_removes_body_continuation_from_review() -> None:
    body = _block("p0001-b0001", 1, "Tekst", "Text").model_copy(
        update={"paragraph_continuation": SegmentContinuation.TO_NEXT_PAGE}
    )
    service = EditorialService(MemoryRevisionRepository())
    service.revise_block(
        _document(body),
        RUN_ID,
        body.block_id,
        "Text",
        block_type=BlockType.FOOTNOTE,
        expected_base_revision=0,
    )

    reviewed = service.review_document(_document(body), RUN_ID).pages[0].blocks[0]
    assert reviewed.type is BlockType.FOOTNOTE
    assert reviewed.paragraph_continuation is None
    assert reviewed.continues_from_block_id is None
