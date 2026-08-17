from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from article_translator.domain.editorial import BlockRevision
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
    GeneratedBlock,
    GeneratedFootnoteBlock,
    GeneratedManualInsertionBlock,
    GeneratedPagePayload,
    GeneratedTableMarkdown,
    GeneratedTablePayload,
    JobManifest,
    PageTranslation,
    PreparedPage,
    ProviderMetadata,
    TableReconstructionMetadata,
    TranslatedBlock,
    TranslationSettings,
)

HASH = "a" * 64
RUN_ID = "1" * 32


def artifact(path: str, media_type: str) -> ArtifactRef:
    return ArtifactRef(
        path=path,
        sha256=HASH,
        media_type=media_type,
        byte_count=10,
    )


def translated_page(number: int, *blocks: TranslatedBlock) -> PageTranslation:
    return PageTranslation(
        translation_run_id=RUN_ID,
        original_page_number=number,
        extraction_status=ExtractionStatus.EXTRACTED,
        extracted_character_count=10,
        source_markdown="source OCR",
        source_markdown_artifact=artifact(
            f"prepared/{number:04d}/source.md",
            "text/markdown",
        ),
        source_image=artifact(f"prepared/{number:04d}/page.png", "image/png"),
        blocks=list(blocks),
        input_fingerprint=HASH,
        provider=ProviderMetadata(
            provider="fake",
            model="fake-v1",
            prompt_version="translate-page-v5",
        ),
    )


def test_generated_payload_requires_contiguous_reading_order() -> None:
    with pytest.raises(ValidationError, match="block order must be contiguous"):
        GeneratedPagePayload(
            blocks=[
                GeneratedBlock(
                    order=2,
                    type=BlockType.BODY,
                    source_text="Kilde",
                    translated_text="Source",
                    paragraph_continuation=SegmentContinuation.COMPLETE,
                )
            ]
        )


def test_provider_schema_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        GeneratedPagePayload.model_validate(
            {
                "blocks": [],
                "confidence": 0.93,
            }
        )


def test_generated_manual_insertion_has_no_table_transcription() -> None:
    block = GeneratedManualInsertionBlock(
        order=1,
        type=BlockType.TABLE,
        manual_insertion_reason=ManualInsertionReason.TABLE_LIKE,
        continuation=SegmentContinuation.COMPLETE,
    )

    restored = GeneratedManualInsertionBlock.model_validate_json(block.model_dump_json())

    assert restored == block
    assert "source_text" not in restored.model_fields_set
    assert "translated_text" not in restored.model_fields_set


def test_generated_table_payload_requires_strict_ascending_gfm_tables() -> None:
    payload = GeneratedTablePayload(
        tables=[
            GeneratedTableMarkdown(
                order=2,
                translated_markdown="| Date | Deaths |\n| --- | ---: |\n| 3 July | 3 |",
            ),
            GeneratedTableMarkdown(
                order=4,
                translated_markdown="Name | Count\n--- | ---\nA | 1",
            ),
        ]
    )

    assert GeneratedTablePayload.model_validate_json(payload.model_dump_json()) == payload


@pytest.mark.parametrize(
    "markdown",
    [
        "not a table",
        "| A | B |\n| x | y |",
        "```markdown\n| A | B |\n| --- | --- |\n```",
        "| A | B |\n| --- | --- |",
        "| A | B |\n| --- | --- |\n| 1 |",
        "| A | B |\n| --- | --- |\nprose after the delimiter",
    ],
)
def test_generated_table_payload_rejects_non_gfm_or_fenced_markdown(markdown: str) -> None:
    with pytest.raises(ValidationError, match="table Markdown"):
        GeneratedTableMarkdown(order=1, translated_markdown=markdown)


def test_generated_table_payload_rejects_duplicate_or_reordered_targets() -> None:
    table = "| A |\n| --- |\n| 1 |"
    with pytest.raises(ValidationError, match="unique and ascending"):
        GeneratedTablePayload(
            tables=[
                GeneratedTableMarkdown(order=2, translated_markdown=table),
                GeneratedTableMarkdown(order=1, translated_markdown=table),
            ]
        )


@pytest.mark.parametrize(
    "extra_field",
    [
        {"source_text": "1  2  3"},
        {"translated_text": "1  2  3"},
        {"uncertainties": []},
    ],
)
def test_generated_manual_insertion_rejects_text_fields(extra_field: dict[str, object]) -> None:
    values: dict[str, object] = {
        "order": 1,
        "type": BlockType.TABLE,
        "manual_insertion_reason": ManualInsertionReason.TABLE,
        "continuation": SegmentContinuation.COMPLETE,
    }
    values.update(extra_field)

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        GeneratedManualInsertionBlock.model_validate(values)


def test_generated_table_cannot_use_translation_handling() -> None:
    with pytest.raises(ValidationError, match="Input should be"):
        GeneratedBlock(
            order=1,
            type=BlockType.TABLE,
            source_text="1  2  3",
            translated_text="1  2  3",
            paragraph_continuation=None,
        )


def test_generated_body_requires_paragraph_continuation_and_limits_page_edges() -> None:
    with pytest.raises(ValidationError, match="paragraph_continuation"):
        GeneratedBlock.model_validate(
            {
                "order": 1,
                "type": "body",
                "source_text": "Uafsluttet",
                "translated_text": "Unfinished",
            }
        )

    first = GeneratedBlock(
        order=1,
        type=BlockType.BODY,
        source_text="Fortsat",
        translated_text="Continued",
        paragraph_continuation=SegmentContinuation.FROM_PREVIOUS_PAGE,
    )
    second = GeneratedBlock(
        order=2,
        type=BlockType.BODY,
        source_text="Ny tekst",
        translated_text="New text",
        paragraph_continuation=SegmentContinuation.COMPLETE,
    )
    assert GeneratedPagePayload(blocks=[first, second]).blocks[0] == first

    with pytest.raises(ValidationError, match="first body block"):
        GeneratedPagePayload(
            blocks=[
                second.model_copy(update={"order": 1}),
                first.model_copy(update={"order": 2}),
            ]
        )


def test_non_body_generated_text_requires_null_paragraph_continuation() -> None:
    with pytest.raises(ValidationError, match="valid only for body blocks"):
        GeneratedBlock(
            order=1,
            type=BlockType.TITLE,
            source_text="Titel",
            translated_text="Title",
            paragraph_continuation=SegmentContinuation.COMPLETE,
        )


def test_persisted_page_rejects_paragraph_continuation_away_from_flow_edge() -> None:
    first = TranslatedBlock(
        block_id="p0002-b0001",
        original_page_number=2,
        order=1,
        type=BlockType.BODY,
        source_text="Ny tekst",
        translated_text="New text",
        paragraph_continuation=SegmentContinuation.COMPLETE,
    )
    incoming = TranslatedBlock(
        block_id="p0002-b0002",
        original_page_number=2,
        order=2,
        type=BlockType.BODY,
        source_text="Fortsat",
        translated_text="Continued",
        paragraph_continuation=SegmentContinuation.FROM_PREVIOUS_PAGE,
        continues_from_block_id="p0001-b0001",
    )

    with pytest.raises(ValidationError, match="first body block"):
        translated_page(2, first, incoming)

    with pytest.raises(ValidationError, match="last body block"):
        translated_page(
            2,
            first.model_copy(update={"paragraph_continuation": SegmentContinuation.TO_NEXT_PAGE}),
            incoming.model_copy(
                update={
                    "paragraph_continuation": SegmentContinuation.COMPLETE,
                    "continues_from_block_id": None,
                }
            ),
        )

    leading_footnote = TranslatedBlock(
        block_id="p0002-b0001",
        original_page_number=2,
        order=1,
        type=BlockType.FOOTNOTE,
        source_text="Fortsat note",
        translated_text="Continued note",
        footnote_marker=None,
        footnote_owner_review_required=True,
        continuation=SegmentContinuation.FROM_PREVIOUS_PAGE,
    )
    with pytest.raises(ValidationError, match="first main-flow block"):
        translated_page(
            2,
            leading_footnote,
            incoming.model_copy(update={"block_id": "p0002-b0002"}),
        )


def test_document_paragraph_link_targets_previous_page_final_body() -> None:
    first = TranslatedBlock(
        block_id="p0001-b0001",
        original_page_number=1,
        order=1,
        type=BlockType.BODY,
        source_text="Første",
        translated_text="First",
        paragraph_continuation=SegmentContinuation.COMPLETE,
    )
    final = first.model_copy(
        update={
            "block_id": "p0001-b0002",
            "order": 2,
            "source_text": "Anden",
            "translated_text": "Second",
            "paragraph_continuation": SegmentContinuation.TO_NEXT_PAGE,
        }
    )
    incoming = TranslatedBlock(
        block_id="p0002-b0001",
        original_page_number=2,
        order=1,
        type=BlockType.BODY,
        source_text="fortsat.",
        translated_text="continued.",
        paragraph_continuation=SegmentContinuation.FROM_PREVIOUS_PAGE,
        continues_from_block_id=first.block_id,
    )
    fields = {
        "translation_run_id": RUN_ID,
        "document_id": HASH,
        "job_id": "job-one",
        "source_file_name": "source.pdf",
        "source_file_sha256": HASH,
        "page_count": 2,
        "translation_settings": TranslationSettings(),
        "pages": [translated_page(1, first, final), translated_page(2, incoming)],
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }

    with pytest.raises(ValidationError, match="final body block"):
        DocumentTranslation.model_validate(fields)

    linked = incoming.model_copy(update={"continues_from_block_id": final.block_id})
    document = DocumentTranslation.model_validate(
        {**fields, "pages": [translated_page(1, first, final), translated_page(2, linked)]}
    )
    assert DocumentTranslation.model_validate_json(document.model_dump_json()) == document


def test_generated_footnote_requires_explicit_continuation_metadata() -> None:
    with pytest.raises(ValidationError, match="continuation"):
        GeneratedFootnoteBlock.model_validate(
            {
                "order": 1,
                "type": "footnote",
                "source_text": "fortsat note",
                "translated_text": "continued note",
                "footnote_marker": None,
            }
        )

    block = GeneratedFootnoteBlock(
        order=1,
        type=BlockType.FOOTNOTE,
        source_text="fortsat note",
        translated_text="continued note",
        footnote_marker=None,
        owner_reference_token=None,
        owner_review_required=True,
        continuation=SegmentContinuation.FROM_PREVIOUS_PAGE,
    )

    assert block.footnote_marker is None
    assert block.continuation is SegmentContinuation.FROM_PREVIOUS_PAGE


def test_generated_footnote_owner_token_must_resolve_exactly_once() -> None:
    payload = GeneratedPagePayload(
        blocks=[
            GeneratedBlock(
                order=1,
                type=BlockType.BODY,
                source_text="Tekst* fortsætter.",
                translated_text="Text[[FOOTNOTE_1]] continues.",
                paragraph_continuation=SegmentContinuation.COMPLETE,
            ),
            GeneratedFootnoteBlock(
                order=2,
                type=BlockType.FOOTNOTE,
                source_text="Notetekst.",
                translated_text="Note text.",
                footnote_marker="*",
                owner_reference_token="[[FOOTNOTE_1]]",
                owner_review_required=False,
                continuation=SegmentContinuation.COMPLETE,
            ),
        ]
    )

    generated_footnote = payload.blocks[1]
    assert isinstance(generated_footnote, GeneratedFootnoteBlock)
    assert generated_footnote.owner_reference_token == "[[FOOTNOTE_1]]"
    with pytest.raises(ValidationError, match="exactly once"):
        GeneratedPagePayload(
            blocks=[
                payload.blocks[0].model_copy(
                    update={"translated_text": "Text without a reference."}
                ),
                payload.blocks[1],
            ]
        )
    with pytest.raises(ValidationError, match="unowned footnote"):
        GeneratedPagePayload(
            blocks=[
                payload.blocks[0].model_copy(
                    update={"translated_text": "Text without a reference."}
                ),
                payload.blocks[1].model_copy(
                    update={
                        "owner_reference_token": None,
                        "owner_review_required": False,
                    }
                ),
            ]
        )


def test_provider_schema_uses_strict_block_variants() -> None:
    schema = GeneratedPagePayload.model_json_schema()
    block_items = schema["properties"]["blocks"]["items"]

    assert [item["$ref"] for item in block_items["anyOf"]] == [
        "#/$defs/GeneratedTextBlock",
        "#/$defs/GeneratedFootnoteBlock",
        "#/$defs/GeneratedManualInsertionBlock",
    ]
    assert "paragraph_continuation" in schema["$defs"]["GeneratedTextBlock"]["required"]


def test_generated_figure_requires_figure_reason() -> None:
    with pytest.raises(ValidationError, match="figure manual insertion reason"):
        GeneratedManualInsertionBlock(
            order=1,
            type=BlockType.FIGURE,
            manual_insertion_reason=ManualInsertionReason.TABLE,
            continuation=SegmentContinuation.COMPLETE,
        )


def test_explicit_v2_legacy_table_marker_preserves_translated_table() -> None:
    legacy_table = TranslatedBlock.model_validate(
        {
            "block_id": "p0001-b0001",
            "original_page_number": 1,
            "order": 1,
            "type": "table",
            "source_text": "A  B\n1  2",
            "translated_text": "A  B\n1  2",
            "legacy_translated_table": True,
        }
    )
    legacy_footnote = TranslatedBlock.model_validate(
        {
            "block_id": "p0001-b0002",
            "original_page_number": 1,
            "order": 2,
            "type": "footnote",
            "source_text": "Ældre note",
            "translated_text": "Older note",
            "footnote_owner_review_required": True,
        }
    )

    assert legacy_table.segment_handling is SegmentHandling.TRANSLATE
    assert legacy_table.manual_insertion_reason is None
    assert legacy_footnote.footnote_marker is None
    assert legacy_footnote.continuation is None


def test_new_translated_table_requires_table_specific_handling() -> None:
    with pytest.raises(ValidationError, match="table blocks must use table reconstruction"):
        TranslatedBlock(
            block_id="p0001-b0001",
            original_page_number=1,
            order=1,
            type=BlockType.TABLE,
            source_text="A  B\n1  2",
            translated_text="A  B\n1  2",
        )


def test_reconstructed_table_retains_region_metadata_without_claiming_source_text() -> None:
    block = TranslatedBlock(
        block_id="p0001-b0001",
        original_page_number=1,
        order=1,
        type=BlockType.TABLE,
        source_text=None,
        translated_text="| Date | Deaths |\n| --- | ---: |\n| 3 July | 3 |",
        segment_handling=SegmentHandling.TABLE_RECONSTRUCTION,
        manual_insertion_reason=ManualInsertionReason.TABLE_LIKE,
        continuation=SegmentContinuation.FROM_PREVIOUS_PAGE,
    )

    assert block.model_validate_json(block.model_dump_json()) == block


def test_table_reconstruction_handling_rejects_non_table_blocks() -> None:
    with pytest.raises(ValidationError, match="valid only for table blocks"):
        TranslatedBlock(
            block_id="p0001-b0001",
            original_page_number=1,
            order=1,
            type=BlockType.BODY,
            source_text=None,
            translated_text="| A |\n| --- |\n| 1 |",
            segment_handling=SegmentHandling.TABLE_RECONSTRUCTION,
            manual_insertion_reason=ManualInsertionReason.TABLE,
            continuation=SegmentContinuation.COMPLETE,
        )


def test_reconstructed_page_requires_exact_table_pass_provenance() -> None:
    reconstructed = TranslatedBlock(
        block_id="p0001-b0001",
        original_page_number=1,
        order=1,
        type=BlockType.TABLE,
        source_text=None,
        translated_text="| Date | Deaths |\n| --- | ---: |\n| 3 July | 3 |",
        segment_handling=SegmentHandling.TABLE_RECONSTRUCTION,
        manual_insertion_reason=ManualInsertionReason.TABLE_LIKE,
        continuation=SegmentContinuation.COMPLETE,
    )
    page_fields = {
        "translation_run_id": RUN_ID,
        "original_page_number": 1,
        "extraction_status": ExtractionStatus.EXTRACTED,
        "extracted_character_count": 10,
        "source_markdown": "source OCR",
        "source_markdown_artifact": artifact("prepared/0001/source.md", "text/markdown"),
        "source_image": artifact("prepared/0001/page.png", "image/png"),
        "blocks": [reconstructed],
        "input_fingerprint": HASH,
        "provider": ProviderMetadata(
            provider="fake",
            model="fake-v1",
            prompt_version="translate-page-v4",
        ),
    }
    with pytest.raises(ValidationError, match="require table-pass provenance"):
        PageTranslation.model_validate(page_fields)

    metadata = TableReconstructionMetadata(
        input_fingerprint=HASH,
        block_ids=[reconstructed.block_id],
        provider=ProviderMetadata(
            provider="fake",
            model="fake-v1",
            prompt_version="reconstruct-tables-v1",
        ),
    )
    restored = PageTranslation.model_validate({**page_fields, "table_reconstruction": metadata})
    assert restored.table_reconstruction == metadata


def test_document_rejects_current_generation_table_stage() -> None:
    pending = TranslatedBlock(
        block_id="p0001-b0001",
        original_page_number=1,
        order=1,
        type=BlockType.TABLE,
        source_text=None,
        translated_text=None,
        segment_handling=SegmentHandling.MANUAL_INSERTION,
        manual_insertion_reason=ManualInsertionReason.TABLE,
        continuation=SegmentContinuation.COMPLETE,
    )
    page = PageTranslation(
        translation_run_id=RUN_ID,
        original_page_number=1,
        extraction_status=ExtractionStatus.EXTRACTED,
        extracted_character_count=10,
        source_markdown="source OCR",
        source_markdown_artifact=artifact("prepared/0001/source.md", "text/markdown"),
        source_image=artifact("prepared/0001/page.png", "image/png"),
        blocks=[pending],
        input_fingerprint=HASH,
        provider=ProviderMetadata(
            provider="fake",
            model="fake-v1",
            prompt_version="translate-page-v4",
        ),
    )

    with pytest.raises(ValidationError, match="tables awaiting reconstruction"):
        DocumentTranslation(
            translation_run_id=RUN_ID,
            document_id=HASH,
            job_id="job-one",
            source_file_name="source.pdf",
            source_file_sha256=HASH,
            page_count=1,
            translation_settings=TranslationSettings(),
            pages=[page],
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_translated_figure_is_not_a_valid_legacy_block() -> None:
    with pytest.raises(ValidationError, match="figure blocks must use manual insertion"):
        TranslatedBlock(
            block_id="p0001-b0001",
            original_page_number=1,
            order=1,
            type=BlockType.FIGURE,
            source_text="Et kort",
            translated_text="A map",
        )


def test_trusted_manual_block_is_text_free_but_keeps_review_metadata() -> None:
    block = TranslatedBlock(
        block_id="p0001-b0001",
        original_page_number=1,
        order=1,
        type=BlockType.FIGURE,
        source_text=None,
        translated_text=None,
        segment_handling=SegmentHandling.MANUAL_INSERTION,
        manual_insertion_reason=ManualInsertionReason.FIGURE,
        continuation=SegmentContinuation.TO_NEXT_PAGE,
        classification_review_required=True,
    )

    assert block.model_validate_json(block.model_dump_json()) == block


def test_manifest_keeps_physical_page_and_pdf_label_distinct() -> None:
    page = PreparedPage(
        original_page_number=1,
        pdf_page_label="iv",
        markdown=artifact("pages/0001/source.md", "text/markdown"),
        image=artifact("pages/0001/page.png", "image/png"),
        extraction_status=ExtractionStatus.EXTRACTED,
        extracted_character_count=20,
    )
    manifest = JobManifest(
        job_id="example-aaaaaaaaaaaa",
        preparation_id="preparation-1",
        document_id=HASH,
        source_file_name="example.pdf",
        source_file_sha256=HASH,
        image_dpi=150,
        page_count=1,
        pages=[page],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert manifest.pages[0].original_page_number == 1
    assert manifest.pages[0].pdf_page_label == "iv"


def test_manifest_rejects_gaps_in_physical_pages() -> None:
    page = PreparedPage(
        original_page_number=2,
        markdown=artifact("pages/0002/source.md", "text/markdown"),
        image=artifact("pages/0002/page.png", "image/png"),
        extraction_status=ExtractionStatus.EMPTY,
        extracted_character_count=0,
    )
    with pytest.raises(ValidationError, match="manifest pages must be physical pages"):
        JobManifest(
            job_id="example-aaaaaaaaaaaa",
            preparation_id="preparation-1",
            document_id=HASH,
            source_file_name="example.pdf",
            source_file_sha256=HASH,
            image_dpi=150,
            page_count=1,
            pages=[page],
        )


@pytest.mark.parametrize(
    "path",
    ["/absolute/page.png", "../escape.png", "pages/../../escape.png"],
)
def test_artifact_paths_cannot_escape_job(path: str) -> None:
    with pytest.raises(ValidationError, match="artifact paths must be relative"):
        artifact(path, "image/png")


def test_future_revisions_are_scoped_to_an_immutable_translation_run() -> None:
    first = BlockRevision(
        revision_id="revision-1",
        document_id=HASH,
        translation_run_id=RUN_ID,
        block_id="p0001-b0001",
        revision_number=1,
        base_revision=0,
        editorial_text="First edit",
        effective_type=BlockType.BODY,
    )
    second = first.model_copy(
        update={
            "revision_id": "revision-2",
            "translation_run_id": "2" * 32,
            "editorial_text": "Edit for a different machine run",
        }
    )

    assert first.block_id == second.block_id
    assert first.translation_run_id != second.translation_run_id


def test_revision_schema_two_requires_effective_metadata_and_schema_one_rejects_it() -> None:
    fields = {
        "revision_id": "revision-1",
        "document_id": HASH,
        "translation_run_id": RUN_ID,
        "block_id": "p0001-b0001",
        "revision_number": 1,
        "base_revision": 0,
        "editorial_text": "Edit",
    }
    with pytest.raises(ValidationError, match="require effective block metadata"):
        BlockRevision(schema_version="2.0", **fields)
    with pytest.raises(ValidationError, match="cannot contain effective block metadata"):
        BlockRevision(schema_version="1.0", effective_type=BlockType.BODY, **fields)


def test_future_revision_requires_run_scope_and_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="translation_run_id"):
        BlockRevision.model_validate(
            {
                "revision_id": "revision-1",
                "document_id": HASH,
                "block_id": "p0001-b0001",
                "revision_number": 1,
                "base_revision": 0,
                "editorial_text": "Edit",
                "page_number": 1,
            }
        )


def test_revision_number_must_follow_base_and_resolved_ids_are_unique() -> None:
    with pytest.raises(ValidationError, match="exactly one greater"):
        BlockRevision(
            revision_id="revision-1",
            document_id=HASH,
            translation_run_id=RUN_ID,
            block_id="p0001-b0001",
            revision_number=3,
            base_revision=1,
            editorial_text="Edit",
        )

    with pytest.raises(ValidationError, match="must be unique"):
        BlockRevision(
            revision_id="revision-1",
            document_id=HASH,
            translation_run_id=RUN_ID,
            block_id="p0001-b0001",
            revision_number=1,
            base_revision=0,
            editorial_text="Edit",
            resolved_uncertainty_ids=["uncertainty-1", "uncertainty-1"],
        )
