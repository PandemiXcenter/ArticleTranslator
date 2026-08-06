from __future__ import annotations

from collections.abc import Mapping

from article_translator.domain.enums import (
    BlockType,
    ManualInsertionReason,
    SegmentContinuation,
    SegmentHandling,
)
from article_translator.domain.models import (
    DocumentTranslation,
    MarkdownExportSettings,
    TranslatedBlock,
)


def compile_markdown(
    document: DocumentTranslation,
    settings: MarkdownExportSettings,
    *,
    editorial_overrides: Mapping[str, str] | None = None,
) -> str:
    """Project canonical data and optional effective editorial text into Markdown."""

    overrides = editorial_overrides or {}
    parts: list[str] = []
    part_by_block_id: dict[str, int] = {}
    for page in document.pages:
        page_marker = (
            f"<!-- original-page: {page.original_page_number} -->"
            if settings.include_page_comments
            else None
        )
        page_has_output = False
        list_items: list[str] = []

        for block in page.blocks:
            if not _should_include(block, settings):
                continue
            effective_text = overrides.get(block.block_id, block.translated_text)
            if block.type is BlockType.LIST_ITEM:
                if effective_text is not None:
                    list_items.append(_render_list_item(effective_text))
                continue
            if _flush_list(parts, list_items):
                if page_marker is not None:
                    parts.insert(len(parts) - 1, page_marker)
                    page_marker = None
                page_has_output = True
            rendered = _render_block(block, effective_text)
            if not rendered:
                continue
            target_index = (
                part_by_block_id.get(block.continues_from_block_id)
                if block.continues_from_block_id is not None
                else None
            )
            if target_index is not None and not page_has_output:
                boundary = (
                    f"<!-- original-page: {page.original_page_number}; "
                    f"continues-from: {block.continues_from_block_id} -->"
                    if settings.include_page_comments
                    else None
                )
                parts[target_index] = _join_paragraph_parts(
                    parts[target_index],
                    rendered,
                    boundary,
                )
                part_by_block_id[block.block_id] = target_index
                page_marker = None
            else:
                if page_marker is not None:
                    parts.append(page_marker)
                    page_marker = None
                parts.append(rendered)
                part_by_block_id[block.block_id] = len(parts) - 1
            page_has_output = True
        if _flush_list(parts, list_items):
            if page_marker is not None:
                parts.insert(len(parts) - 1, page_marker)
                page_marker = None
            page_has_output = True

        if page_marker is not None:
            parts.append(page_marker)

    rendered_document = "\n\n".join(parts).rstrip()
    return f"{rendered_document}\n" if rendered_document else ""


def _flush_list(parts: list[str], list_items: list[str]) -> bool:
    if list_items:
        parts.append("\n".join(list_items))
        list_items.clear()
        return True
    return False


def _join_paragraph_parts(left: str, right: str, boundary: str | None) -> str:
    components = [left.rstrip()]
    if boundary is not None:
        components.append(boundary)
    components.append(right.lstrip())
    return " ".join(components)


def _should_include(block: TranslatedBlock, settings: MarkdownExportSettings) -> bool:
    if block.type is BlockType.HEADER:
        return settings.include_headers
    if block.type is BlockType.FOOTER:
        return settings.include_footers
    if block.type is BlockType.PAGE_NUMBER:
        return settings.include_page_numbers
    return True


def _render_block(block: TranslatedBlock, effective_text: str | None) -> str:
    if block.segment_handling is SegmentHandling.MANUAL_INSERTION:
        if effective_text is not None and effective_text.strip():
            text = effective_text.strip()
            return _place_table(block, text) if block.type is BlockType.TABLE else text
        reason = "Table, table-like material, or figure"
        if block.manual_insertion_reason is not None:
            reason = {
                ManualInsertionReason.TABLE: "Table",
                ManualInsertionReason.TABLE_LIKE: "Table-like material",
                ManualInsertionReason.FIGURE: "Figure",
            }[block.manual_insertion_reason]
        placeholder = (
            f"> **Manual insertion required:** {reason} on original page "
            f"{block.original_page_number} (`{block.block_id}`)."
        )
        return _place_table(block, placeholder) if block.type is BlockType.TABLE else placeholder
    if effective_text is None:
        return ""
    text = effective_text.strip()
    if not text:
        return ""

    if block.type is BlockType.TABLE:
        return _place_table(block, text)

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
        label = _footnote_label(block)
        return "\n".join([f"> **{label}:** {lines[0]}", *(f"> {line}" for line in lines[1:])])
    if block.type is BlockType.EQUATION:
        return f"$$\n{text}\n$$"
    return text


def _render_list_item(text: str) -> str:
    lines = text.strip().splitlines()
    if not lines:
        return ""
    return "\n".join([f"- {lines[0]}", *(f"  {line}" for line in lines[1:])])


def _place_table(block: TranslatedBlock, text: str) -> str:
    anchor = (
        f"<!-- table-placement: [H!]; original-page: {block.original_page_number}; "
        f"block-id: {block.block_id} -->"
    )
    return f"{anchor}\n{text}"


def _single_line(text: str) -> str:
    return " ".join(text.splitlines())


def _footnote_label(block: TranslatedBlock) -> str:
    label = "Footnote"
    if block.footnote_marker is not None:
        label = f"{label} {_escape_markdown_label(block.footnote_marker)}"
    continuation = None
    if block.continuation is not None:
        continuation = {
            SegmentContinuation.COMPLETE: "self-contained",
            SegmentContinuation.FROM_PREVIOUS_PAGE: "continued from previous page",
            SegmentContinuation.TO_NEXT_PAGE: "continues on next page",
            SegmentContinuation.FROM_PREVIOUS_AND_TO_NEXT_PAGE: "continued across pages",
            SegmentContinuation.UNKNOWN: "continuation unclear",
        }[block.continuation]
    if continuation is not None:
        label = f"{label} ({continuation})"
    return label


def _escape_markdown_label(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for character in "*_[]`":
        escaped = escaped.replace(character, f"\\{character}")
    return escaped
