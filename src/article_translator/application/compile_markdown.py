from __future__ import annotations

from article_translator.domain.enums import BlockType
from article_translator.domain.models import (
    DocumentTranslation,
    MarkdownExportSettings,
    TranslatedBlock,
)


def compile_markdown(
    document: DocumentTranslation,
    settings: MarkdownExportSettings,
) -> str:
    """Project canonical structured data into deterministic, clean Markdown."""

    rendered_pages: list[str] = []
    for page in document.pages:
        parts: list[str] = []
        if settings.include_page_comments:
            parts.append(f"<!-- original-page: {page.original_page_number} -->")

        list_items: list[str] = []

        for block in page.blocks:
            if not _should_include(block, settings):
                continue
            if block.type is BlockType.LIST_ITEM:
                list_items.append(_render_list_item(block.translated_text))
                continue
            _flush_list(parts, list_items)
            rendered = _render_block(block)
            if rendered:
                parts.append(rendered)
        _flush_list(parts, list_items)

        if parts:
            rendered_pages.append("\n\n".join(parts))

    rendered_document = "\n\n".join(rendered_pages).rstrip()
    return f"{rendered_document}\n" if rendered_document else ""


def _flush_list(parts: list[str], list_items: list[str]) -> None:
    if list_items:
        parts.append("\n".join(list_items))
        list_items.clear()


def _should_include(block: TranslatedBlock, settings: MarkdownExportSettings) -> bool:
    if block.type is BlockType.HEADER:
        return settings.include_headers
    if block.type is BlockType.FOOTER:
        return settings.include_footers
    if block.type is BlockType.PAGE_NUMBER:
        return settings.include_page_numbers
    return True


def _render_block(block: TranslatedBlock) -> str:
    text = block.translated_text.strip()
    if not text:
        return ""

    if block.type is BlockType.TITLE:
        return f"# {_single_line(text)}"
    if block.type is BlockType.SUBTITLE:
        return f"## {_single_line(text)}"
    if block.type is BlockType.HEADING:
        level = max(2, block.heading_level or 2)
        return f"{'#' * level} {_single_line(text)}"
    if block.type is BlockType.BYLINE:
        return f"*{text}*"
    if block.type is BlockType.QUOTE:
        return "\n".join(f"> {line}" for line in text.splitlines())
    if block.type is BlockType.CAPTION:
        return f"*{text}*"
    if block.type is BlockType.FOOTNOTE:
        lines = text.splitlines()
        return "\n".join([f"> **Footnote:** {lines[0]}", *(f"> {line}" for line in lines[1:])])
    if block.type is BlockType.EQUATION:
        return f"$$\n{text}\n$$"
    return text


def _render_list_item(text: str) -> str:
    lines = text.strip().splitlines()
    if not lines:
        return ""
    return "\n".join([f"- {lines[0]}", *(f"  {line}" for line in lines[1:])])


def _single_line(text: str) -> str:
    return " ".join(text.splitlines())
