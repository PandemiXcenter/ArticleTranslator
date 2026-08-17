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
    FootnoteDescription,
    FootnoteIdentity,
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
        footnote_id=(
            FootnoteIdentity(id=f"fn-p1-n{order}", text=None)
            if block_type is BlockType.FOOTNOTE
            else None
        ),
        footnote_description=(
            FootnoteDescription(
                appearance="Small type below a rule.",
                handling="Starts and ends on this page.",
            )
            if block_type is BlockType.FOOTNOTE
            else None
        ),
        footnote_owner_review_required=block_type is BlockType.FOOTNOTE,
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
        "<!-- table-placement: [H!]; original-page: 1; block-id: p0001-b0001 -->\n"
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
        == "<!-- table-placement: [H!]; original-page: 1; block-id: p0001-b0001 -->\n"
        "| Month | Cases |\n| --- | ---: |\n| January | 12 |\n"
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
        == "<!-- table-placement: [H!]; original-page: 1; block-id: p0001-b0001 -->\n"
        f"{markdown}\n"
    )


def test_compiler_links_confirmed_cross_page_body_as_one_paragraph() -> None:
    base = document()
    first = TranslatedBlock(
        block_id="p0001-b0001",
        original_page_number=1,
        order=1,
        type=BlockType.BODY,
        source_text="Første halvdel",
        translated_text="The first half",
        paragraph_continuation=SegmentContinuation.TO_NEXT_PAGE,
    )
    second = TranslatedBlock(
        block_id="p0002-b0001",
        original_page_number=2,
        order=1,
        type=BlockType.BODY,
        source_text="anden halvdel.",
        translated_text="the second half.",
        paragraph_continuation=SegmentContinuation.FROM_PREVIOUS_PAGE,
        continues_from_block_id=first.block_id,
    )
    first_page = base.pages[0].model_copy(update={"blocks": [first]})
    second_page = PageTranslation(
        **{
            **base.pages[0].model_dump(mode="python"),
            "original_page_number": 2,
            "blocks": [second],
        }
    )
    continued_document = base.model_copy(
        update={"page_count": 2, "pages": [first_page, second_page]}
    )

    assert compile_markdown(continued_document, MarkdownExportSettings()) == (
        "<!-- original-page: 1 -->\n\n"
        "The first half <!-- original-page: 2; continues-from: p0001-b0001 --> "
        "the second half.\n"
    )


def test_compiler_keeps_table_anchor_between_surrounding_paragraphs() -> None:
    base = document()
    before = block(1, BlockType.BODY, "Before the table.")
    table = TranslatedBlock(
        block_id="p0001-b0002",
        original_page_number=1,
        order=2,
        type=BlockType.TABLE,
        segment_handling=SegmentHandling.TABLE_RECONSTRUCTION,
        source_text=None,
        translated_text="| A | B |\n| --- | --- |\n| 1 | 2 |",
        manual_insertion_reason=ManualInsertionReason.TABLE,
        continuation=SegmentContinuation.COMPLETE,
    )
    after = block(3, BlockType.BODY, "After the table.")
    metadata = TableReconstructionMetadata(
        input_fingerprint=HASH,
        block_ids=[table.block_id],
        provider=ProviderMetadata(
            provider="fake",
            model="fake-v1",
            prompt_version="reconstruct-tables-v1",
        ),
        reconstructed_at=FIXED_TIME,
    )
    page = base.pages[0].model_copy(
        update={"blocks": [before, table, after], "table_reconstruction": metadata}
    )
    anchored = compile_markdown(
        base.model_copy(update={"pages": [page]}),
        MarkdownExportSettings(include_page_comments=False),
    )

    assert anchored.index("Before the table.") < anchored.index("table-placement: [H!]")
    assert anchored.index("table-placement: [H!]") < anchored.index("After the table.")


def test_compiler_labels_footnote_reference_text_and_continuation() -> None:
    footnote = TranslatedBlock(
        block_id="p0001-b0001",
        original_page_number=1,
        order=1,
        type=BlockType.FOOTNOTE,
        source_text="Fortsat note.",
        translated_text="Continued note.",
        footnote_id=FootnoteIdentity(id="fn-p1-n1", text="12"),
        footnote_description=FootnoteDescription(
            appearance="Numbered note in small type.",
            handling="Starts here and continues on the next page.",
        ),
        footnote_owner_review_required=True,
        continuation=SegmentContinuation.TO_NEXT_PAGE,
    )
    base = document()
    page = base.pages[0].model_copy(update={"blocks": [footnote]})
    footnote_document = base.model_copy(update={"pages": [page]})

    assert (
        compile_markdown(
            footnote_document,
            MarkdownExportSettings(include_page_comments=False),
        )
        == "> **Footnote 12 (continues on next page):** Continued note.\n"
    )
