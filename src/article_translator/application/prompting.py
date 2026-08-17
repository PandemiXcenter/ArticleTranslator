import json
from collections.abc import Sequence
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files

from article_translator.domain.enums import BlockType, SegmentHandling
from article_translator.domain.models import PageTranslation, TranslationSettings

PROMPT_VERSION = "translate-page-v10"
TABLE_PROMPT_VERSION = "reconstruct-tables-v2"


@lru_cache(maxsize=1)
def _prompt_preamble() -> str:
    resource = files("article_translator.prompts").joinpath("translate_page_v10.md")
    return resource.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def _table_prompt_preamble() -> str:
    resource = files("article_translator.prompts").joinpath("reconstruct_tables_v2.md")
    return resource.read_text(encoding="utf-8").strip()


def table_prompt_contract_sha256() -> str:
    """Hash the table prompt resource even on pages that contain no table."""

    return sha256(_table_prompt_preamble().encode("utf-8")).hexdigest()


def build_page_prompt(
    *,
    page_number: int,
    markdown: str,
    settings: TranslationSettings,
    previous_pages: Sequence[PageTranslation] = (),
) -> str:
    """Build a versioned prompt; page Markdown remains visibly delimited data."""

    resolved_settings = json.dumps(
        settings.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return (
        f"{_prompt_preamble()}\n\n"
        f"Prompt version: {PROMPT_VERSION}\n"
        f"Physical PDF page: {page_number}\n"
        f"Table follow-up prompt version: {TABLE_PROMPT_VERSION}\n"
        f"Resolved translation settings:\n{resolved_settings}\n\n"
        f"{_previous_page_context(previous_pages, settings)}\n\n"
        "SOURCE_MARKDOWN_START\n"
        f"{markdown}\n"
        "SOURCE_MARKDOWN_END\n"
    )


def build_table_prompt(
    *,
    page_translation: PageTranslation,
    markdown: str,
    settings: TranslationSettings,
    previous_pages: Sequence[PageTranslation] = (),
) -> str:
    """Build the second-pass prompt for all table regions tagged on one page."""

    table_blocks = [
        block
        for block in page_translation.blocks
        if block.type is BlockType.TABLE
        and block.segment_handling is SegmentHandling.MANUAL_INSERTION
        and not block.legacy_manual_table
    ]
    if not table_blocks:
        raise ValueError("table reconstruction prompt requires at least one current table target")
    resolved_settings = json.dumps(
        settings.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    targets = json.dumps(
        [
            {
                "order": block.order,
                "manual_insertion_reason": block.manual_insertion_reason,
                "continuation": block.continuation,
            }
            for block in table_blocks
        ],
        ensure_ascii=False,
        indent=2,
    )
    segmentation = json.dumps(
        [
            {
                "order": block.order,
                "type": block.type,
                "segment_handling": block.segment_handling,
                "source_text": block.source_text,
                "translated_text": block.translated_text,
                "manual_insertion_reason": block.manual_insertion_reason,
                "footnote_id": (
                    block.footnote_id.model_dump(mode="json")
                    if block.footnote_id is not None
                    else None
                ),
                "footnote_description": (
                    block.footnote_description.model_dump(mode="json")
                    if block.footnote_description is not None
                    else None
                ),
                "continuation": block.continuation,
                "paragraph_continuation": block.paragraph_continuation,
                "classification_review_required": block.classification_review_required,
            }
            for block in page_translation.blocks
        ],
        ensure_ascii=False,
        indent=2,
    )
    return (
        f"{_table_prompt_preamble()}\n\n"
        f"Prompt version: {TABLE_PROMPT_VERSION}\n"
        f"Physical PDF page: {page_translation.original_page_number}\n"
        f"Resolved translation settings:\n{resolved_settings}\n\n"
        f"{_previous_page_context(previous_pages, settings)}\n\n"
        "TABLE_TARGETS_START\n"
        f"{targets}\n"
        "TABLE_TARGETS_END\n\n"
        "FIRST_PASS_SEGMENTATION_START\n"
        f"{segmentation}\n"
        "FIRST_PASS_SEGMENTATION_END\n\n"
        "SOURCE_MARKDOWN_START\n"
        f"{markdown}\n"
        "SOURCE_MARKDOWN_END\n"
    )


def _previous_page_context(
    pages: Sequence[PageTranslation],
    settings: TranslationSettings,
) -> str:
    count = settings.previous_page_context_count
    selected = list(pages[-count:]) if count else []
    projection = [
        {
            "original_page_number": page.original_page_number,
            "pdf_page_label": page.pdf_page_label,
            "detected_printed_page_label": page.detected_printed_page_label,
            "blocks": [
                {
                    "order": block.order,
                    "type": block.type,
                    "source_text": block.source_text,
                    "translated_text": block.translated_text,
                    "segment_handling": block.segment_handling,
                    "manual_insertion_reason": block.manual_insertion_reason,
                    "footnote_id": (
                        block.footnote_id.model_dump(mode="json")
                        if block.footnote_id is not None
                        else None
                    ),
                    "footnote_description": (
                        block.footnote_description.model_dump(mode="json")
                        if block.footnote_description is not None
                        else None
                    ),
                    "continuation": block.continuation,
                    "paragraph_continuation": block.paragraph_continuation,
                }
                for block in page.blocks
            ],
        }
        for page in selected
    ]
    encoded = json.dumps(projection, ensure_ascii=False, indent=2)
    return f"PREVIOUS_TRANSLATED_PAGES_START\n{encoded}\nPREVIOUS_TRANSLATED_PAGES_END"
