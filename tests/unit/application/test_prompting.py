from article_translator.application.prompting import (
    PROMPT_VERSION,
    TABLE_PROMPT_VERSION,
    build_page_prompt,
    build_table_prompt,
)
from article_translator.domain.enums import (
    BlockType,
    ExtractionStatus,
    ManualInsertionReason,
    SegmentContinuation,
    SegmentHandling,
    TranslationStyle,
)
from article_translator.domain.models import (
    ArtifactRef,
    PageTranslation,
    ProviderMetadata,
    TranslatedBlock,
    TranslationSettings,
)

HASH = "a" * 64
RUN_ID = "1" * 32


def _page(number: int, *blocks: TranslatedBlock) -> PageTranslation:
    artifact = ArtifactRef(
        path=f"prepared/{number}.md",
        sha256=HASH,
        media_type="text/markdown",
        byte_count=1,
    )
    return PageTranslation(
        translation_run_id=RUN_ID,
        original_page_number=number,
        extraction_status=ExtractionStatus.EXTRACTED,
        extracted_character_count=1,
        source_markdown=f"OCR {number}",
        source_markdown_artifact=artifact,
        source_image=artifact.model_copy(update={"media_type": "image/png"}),
        blocks=list(blocks),
        input_fingerprint=HASH,
        provider=ProviderMetadata(
            provider="fake",
            model="fake-v1",
            prompt_version=PROMPT_VERSION,
        ),
    )


def _body(page: int, text: str, *, order: int = 1) -> TranslatedBlock:
    return TranslatedBlock(
        block_id=f"p{page:04d}-b{order:04d}",
        original_page_number=page,
        order=order,
        type=BlockType.BODY,
        source_text=f"source {text}",
        translated_text=text,
    )


def test_prompt_contains_resolved_settings_and_delimited_page_markdown() -> None:
    settings = TranslationSettings(
        source_language="Danish",
        target_language="English",
        style=TranslationStyle.FAITHFUL,
        glossary={"Kolera": "cholera"},
    )

    prompt = build_page_prompt(
        page_number=7,
        markdown="# Om Kolera",
        settings=settings,
    )

    assert f"Prompt version: {PROMPT_VERSION}" in prompt
    assert "Physical PDF page: 7" in prompt
    assert '"style": "faithful"' in prompt
    assert '"Kolera": "cholera"' in prompt
    assert "authoritative translation" in prompt
    assert "assertion for this document" in prompt
    assert "one structured block variant" in prompt
    assert "dedicated second model" in prompt
    assert f"Table follow-up prompt version: {TABLE_PROMPT_VERSION}" in prompt
    assert "Continuous prose printed in columns" in prompt
    assert "Translate captions" in prompt
    assert "nearly an entire\n   page" in prompt
    assert "from_previous_page" in prompt
    assert "from_previous_and_to_next_page" in prompt
    assert "printer\n   signatures" in prompt
    assert 'type="figure"' in prompt
    assert "classification_review_required=true" in prompt
    assert "paragraph_continuation" in prompt
    assert "unfinished-paragraph variable" in prompt
    assert "whether the first body block continues" in prompt
    assert "SOURCE_MARKDOWN_START\n# Om Kolera\nSOURCE_MARKDOWN_END" in prompt


def test_prompt_includes_only_configured_previous_finalized_page_context() -> None:
    previous = [_page(number, _body(number, f"translation-{number}")) for number in (1, 2, 3)]

    prompt = build_page_prompt(
        page_number=4,
        markdown="current OCR",
        settings=TranslationSettings(previous_page_context_count=2),
        previous_pages=previous,
    )

    context = prompt.split("PREVIOUS_TRANSLATED_PAGES_START\n", 1)[1].split(
        "\nPREVIOUS_TRANSLATED_PAGES_END", 1
    )[0]
    assert '"original_page_number": 2' in context
    assert '"original_page_number": 3' in context
    assert "translation-2" in context
    assert "translation-3" in context
    assert '"paragraph_continuation": null' in context
    assert "translation-1" not in context
    assert "block_id" not in context
    assert "provider" not in context
    assert "translated_at" not in context


def test_table_prompt_contains_complete_ocr_targets_segmentation_and_context() -> None:
    preceding = _page(1, _body(1, "The sentence continues"))
    body = _body(2, "Introduction", order=1)
    table = TranslatedBlock(
        block_id="p0002-b0002",
        original_page_number=2,
        order=2,
        type=BlockType.TABLE,
        source_text=None,
        translated_text=None,
        segment_handling=SegmentHandling.MANUAL_INSERTION,
        manual_insertion_reason=ManualInsertionReason.TABLE_LIKE,
        continuation=SegmentContinuation.FROM_PREVIOUS_PAGE,
    )
    stage = _page(2, body, table)

    prompt = build_table_prompt(
        page_translation=stage,
        markdown="3 dead, on 3rd of July\n3, 4th",
        settings=TranslationSettings(
            source_language="Danish",
            target_language="English",
            glossary={"døde": "deaths"},
            previous_page_context_count=1,
        ),
        previous_pages=[preceding],
    )

    assert f"Prompt version: {TABLE_PROMPT_VERSION}" in prompt
    assert '"order": 2' in prompt
    assert '"manual_insertion_reason": "table_like"' in prompt
    assert '"continuation": "from_previous_page"' in prompt
    assert '"translated_text": "Introduction"' in prompt
    assert '"døde": "deaths"' in prompt
    assert "The sentence continues" in prompt
    assert "3 dead, on 3rd of July\n3, 4th" in prompt
    assert "Date | Deaths" in prompt
    assert "changing or inventing facts" in prompt
    assert "footnote or note-reference markers" in prompt
    assert "renders literally in its table cell" in prompt
    assert "TABLE_TARGETS_START" in prompt
    assert "FIRST_PASS_SEGMENTATION_START" in prompt
