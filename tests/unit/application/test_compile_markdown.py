from datetime import UTC, datetime

from article_translator.application.compile_markdown import compile_markdown
from article_translator.domain.enums import (
    BlockType,
    ExtractionStatus,
    ManualInsertionReason,
    SegmentContinuation,
    SegmentHandling,
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
)

HASH = "b" * 64
RUN_ID = "1" * 32
FIXED_TIME = datetime(2026, 1, 2, tzinfo=UTC)


def block(order: int, block_type: BlockType, text: str) -> TranslatedBlock:
    return TranslatedBlock(
        block_id=f"p0001-b{order:04d}",
        original_page_number=1,
        order=order,
        type=block_type,
        source_text=f"source {order}",
        translated_text=text,
    )


def document() -> DocumentTranslation:
    image = ArtifactRef(
        path="pages/0001/page.png",
        sha256=HASH,
        media_type="image/png",
        byte_count=100,
    )
    page = PageTranslation(
        translation_run_id=RUN_ID,
        original_page_number=1,
        extraction_status=ExtractionStatus.EXTRACTED,
        extracted_character_count=6,
        source_markdown="source",
        source_markdown_artifact=ArtifactRef(
            path="prepared/example/pages/0001/source.md",
            sha256=HASH,
            media_type="text/markdown",
            byte_count=6,
        ),
        source_image=image,
        blocks=[
            block(1, BlockType.HEADER, "Journal header"),
            block(2, BlockType.TITLE, "A translated title"),
            block(3, BlockType.BYLINE, "P. Panum"),
            block(4, BlockType.BODY, "First paragraph."),
            block(5, BlockType.LIST_ITEM, "First item"),
            block(6, BlockType.LIST_ITEM, "Second item"),
            block(7, BlockType.QUOTE, "A quotation"),
            block(8, BlockType.CAPTION, "Figure 1"),
            block(9, BlockType.FOOTNOTE, "An explanatory note."),
            block(10, BlockType.PAGE_NUMBER, "17"),
        ],
        input_fingerprint=HASH,
        provider=ProviderMetadata(
            provider="fake",
            model="fake-v1",
            prompt_version="translate-page-v1",
        ),
        translated_at=FIXED_TIME,
    )
    return DocumentTranslation(
        translation_run_id=RUN_ID,
        document_id=HASH,
        job_id="document-bbbbbbbbbbbb",
        source_file_name="document.pdf",
        source_file_sha256=HASH,
        page_count=1,
        translation_settings=TranslationSettings(),
        pages=[page],
        created_at=FIXED_TIME,
    )


def test_compiler_creates_clean_deterministic_markdown() -> None:
    result = compile_markdown(document(), MarkdownExportSettings())

    assert result == (
        "<!-- original-page: 1 -->\n\n"
        "# A translated title\n\n"
        "*P. Panum*\n\n"
        "First paragraph.\n\n"
        "- First item\n"
        "- Second item\n\n"
        "> A quotation\n\n"
        "*Figure 1*\n\n"
        "> **Footnote:** An explanatory note.\n"
    )
    assert "Journal header" not in result
    assert "\n17\n" not in result


def test_compiler_can_include_configured_marginalia() -> None:
    result = compile_markdown(
        document(),
        MarkdownExportSettings(
            include_headers=True,
            include_page_numbers=True,
            include_page_comments=False,
        ),
    )

    assert result.startswith("Journal header\n\n# A translated title")
    assert result.endswith("\n\n17\n")


def test_compiler_marks_unresolved_manual_insertion_and_uses_reviewer_markdown() -> None:
    manual = TranslatedBlock(
        block_id="p0001-b0001",
        original_page_number=1,
        order=1,
        type=BlockType.TABLE,
        segment_handling=SegmentHandling.MANUAL_INSERTION,
        source_text=None,
        translated_text=None,
        manual_insertion_reason=ManualInsertionReason.TABLE_LIKE,
        continuation=SegmentContinuation.COMPLETE,
        legacy_manual_table=True,
    )
    base = document()
    page = base.pages[0].model_copy(update={"blocks": [manual]})
    manual_document = base.model_copy(update={"pages": [page]})

    unresolved = compile_markdown(
        manual_document,
        MarkdownExportSettings(include_page_comments=False),
    )
    assert unresolved == (
        "> **Manual insertion required:** Table-like material on original page 1 (`p0001-b0001`).\n"
    )
    assert (
        compile_markdown(
            manual_document,
            MarkdownExportSettings(include_page_comments=False),
            editorial_overrides={
                manual.block_id: "| Month | Cases |\n| --- | ---: |\n| January | 12 |"
            },
        )
        == "| Month | Cases |\n| --- | ---: |\n| January | 12 |\n"
    )


def test_compiler_emits_machine_reconstructed_table_markdown_exactly() -> None:
    markdown = "| Date | Deaths |\n| --- | ---: |\n| 3 July | 3 |\n| 4 July | 3 |"
    reconstructed = TranslatedBlock(
        block_id="p0001-b0001",
        original_page_number=1,
        order=1,
        type=BlockType.TABLE,
        segment_handling=SegmentHandling.TABLE_RECONSTRUCTION,
        source_text=None,
        translated_text=markdown,
        manual_insertion_reason=ManualInsertionReason.TABLE_LIKE,
        continuation=SegmentContinuation.COMPLETE,
    )
    base = document()
    table_provider = ProviderMetadata(
        provider="fake",
        model="fake-v1",
        prompt_version="reconstruct-tables-v1",
    )
    page = base.pages[0].model_copy(
        update={
            "blocks": [reconstructed],
            "table_reconstruction": TableReconstructionMetadata(
                input_fingerprint=HASH,
                block_ids=[reconstructed.block_id],
                provider=table_provider,
                reconstructed_at=FIXED_TIME,
            ),
        }
    )
    reconstructed_document = base.model_copy(update={"pages": [page]})

    assert (
        compile_markdown(
            reconstructed_document,
            MarkdownExportSettings(include_page_comments=False),
        )
        == f"{markdown}\n"
    )


def test_compiler_labels_footnote_marker_and_continuation() -> None:
    footnote = TranslatedBlock(
        block_id="p0001-b0001",
        original_page_number=1,
        order=1,
        type=BlockType.FOOTNOTE,
        source_text="Fortsat note.",
        translated_text="Continued note.",
        footnote_marker="12",
        continuation=SegmentContinuation.FROM_PREVIOUS_AND_TO_NEXT_PAGE,
    )
    base = document()
    page = base.pages[0].model_copy(update={"blocks": [footnote]})
    footnote_document = base.model_copy(update={"pages": [page]})

    assert (
        compile_markdown(
            footnote_document,
            MarkdownExportSettings(include_page_comments=False),
        )
        == "> **Footnote 12 (continued across pages):** Continued note.\n"
    )
