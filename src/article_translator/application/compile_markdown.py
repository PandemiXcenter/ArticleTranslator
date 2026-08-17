from __future__ import annotations

from collections.abc import Mapping
from difflib import SequenceMatcher

from article_translator.application.footnotes import project_footnotes
from article_translator.application.paragraphs import ParagraphProjection, project_paragraphs
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
    type_overrides: Mapping[str, BlockType] | None = None,
    footnote_owner_overrides: Mapping[str, tuple[str | None, int | None, bool]] | None = None,
) -> str:
    """Project canonical data and optional effective editorial text into Markdown."""

    overrides = editorial_overrides or {}
    resolved_types = type_overrides or {}
    resolved_owners = footnote_owner_overrides or {}
    blocks = [block for page in document.pages for block in page.blocks]
    blocks_by_id = {block.block_id: block for block in blocks}
    effective_text_by_id = {
        block.block_id: overrides.get(block.block_id, block.translated_text) for block in blocks
    }
    footnote_projections = project_footnotes(
        blocks,
        effective_text_by_id=effective_text_by_id,
        type_overrides=resolved_types,
        owner_overrides=resolved_owners,
    )
    owned_footnotes: dict[str, list[tuple[TranslatedBlock, str, int, str]]] = {}
    owned_footnote_ids: set[str] = set()
    merged_footnote_ids = {
        fragment_id
        for projection in footnote_projections
        for fragment_id in projection.fragment_ids[1:]
    }
    merged_text_by_first_id = {
        projection.first_block.block_id: projection.text for projection in footnote_projections
    }
    for projection in footnote_projections:
        footnote = projection.first_block
        owner_id = projection.owner_block_id
        anchor_offset = projection.anchor_offset
        owner_review_required = projection.owner_review_required
        if owner_id is None or owner_review_required:
            continue
        owner = blocks_by_id.get(owner_id)
        owner_text = effective_text_by_id.get(owner_id)
        note_text = projection.text
        if owner is None or owner_text is None or note_text is None or anchor_offset is None:
            continue
        mapped_offset = _map_anchor_offset(owner.translated_text or "", owner_text, anchor_offset)
        owned_footnotes.setdefault(owner_id, []).append(
            (footnote, note_text, mapped_offset, projection.identity)
        )
        owned_footnote_ids.update(projection.fragment_ids)
    paragraph_projections = project_paragraphs(
        blocks,
        effective_text_by_id=effective_text_by_id,
        type_overrides=resolved_types,
    )
    merged_paragraph_ids = {
        fragment.block.block_id
        for projection in paragraph_projections
        for fragment in projection.fragments[1:]
    }
    merged_paragraph_pages = {
        fragment.block.original_page_number
        for projection in paragraph_projections
        for fragment in projection.fragments[1:]
    }
    merged_paragraph_text_by_first_id = {
        projection.paragraph_id: _render_markdown_paragraph(
            projection,
            owned_footnotes=owned_footnotes,
            include_page_comments=settings.include_page_comments,
        )
        for projection in paragraph_projections
        if len(projection.fragments) > 1
    }
    parts: list[str] = []
    for page in document.pages:
        page_marker = (
            f"<!-- original-page: {page.original_page_number} -->"
            if settings.include_page_comments
            and page.original_page_number not in merged_paragraph_pages
            else None
        )
        list_items: list[str] = []

        for block in page.blocks:
            block_type = resolved_types.get(block.block_id, block.type)
            if not _should_include(block, settings, block_type=block_type):
                continue
            effective_text = overrides.get(block.block_id, block.translated_text)
            if block.block_id in owned_footnote_ids:
                continue
            if block.block_id in merged_footnote_ids:
                continue
            if block.block_id in merged_paragraph_ids:
                continue
            if block.block_id in merged_text_by_first_id:
                effective_text = merged_text_by_first_id[block.block_id]
            if block.block_id in merged_paragraph_text_by_first_id:
                rendered = merged_paragraph_text_by_first_id[block.block_id]
            elif effective_text is not None and block.block_id in owned_footnotes:
                effective_text = _insert_markdown_footnote_references(
                    effective_text,
                    owned_footnotes[block.block_id],
                )
                rendered = _render_block(block, effective_text, block_type=block_type)
            else:
                rendered = _render_block(block, effective_text, block_type=block_type)
            if block_type is BlockType.LIST_ITEM:
                if effective_text is not None:
                    list_items.append(_render_list_item(effective_text))
                continue
            if _flush_list(parts, list_items) and page_marker is not None:
                parts.insert(len(parts) - 1, page_marker)
                page_marker = None
            if not rendered:
                continue
            if page_marker is not None:
                parts.append(page_marker)
                page_marker = None
            parts.append(rendered)
        if _flush_list(parts, list_items) and page_marker is not None:
            parts.insert(len(parts) - 1, page_marker)
            page_marker = None

        if page_marker is not None:
            parts.append(page_marker)

    definitions = [
        _markdown_footnote_definition(footnote, note_text, identity)
        for footnotes in owned_footnotes.values()
        for footnote, note_text, _, identity in footnotes
    ]
    rendered_document = "\n\n".join([*parts, *definitions]).rstrip()
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


def _render_markdown_paragraph(
    projection: ParagraphProjection,
    *,
    owned_footnotes: Mapping[str, list[tuple[TranslatedBlock, str, int, str]]],
    include_page_comments: bool,
) -> str:
    rendered = ""
    previous_block_id: str | None = None
    for fragment in projection.fragments:
        text = fragment.text
        if fragment.block.block_id in owned_footnotes:
            text = _insert_markdown_footnote_references(
                text,
                owned_footnotes[fragment.block.block_id],
            )
        current = _render_block(fragment.block, text, block_type=BlockType.BODY)
        if not rendered:
            rendered = current
        else:
            boundary = (
                f"<!-- original-page: {fragment.block.original_page_number}; "
                f"continues-from: {previous_block_id} -->"
                if include_page_comments
                else None
            )
            rendered = _join_paragraph_parts(rendered, current, boundary)
        previous_block_id = fragment.block.block_id
    return rendered


def _should_include(
    block: TranslatedBlock,
    settings: MarkdownExportSettings,
    *,
    block_type: BlockType | None = None,
) -> bool:
    resolved_type = block_type or block.type
    if resolved_type is BlockType.HEADER:
        return settings.include_headers
    if resolved_type is BlockType.FOOTER:
        return settings.include_footers
    if resolved_type is BlockType.PAGE_NUMBER:
        return settings.include_page_numbers
    return True


def _render_block(
    block: TranslatedBlock,
    effective_text: str | None,
    *,
    block_type: BlockType | None = None,
) -> str:
    resolved_type = block_type or block.type
    if block.segment_handling is SegmentHandling.MANUAL_INSERTION:
        if effective_text is not None and effective_text.strip():
            text = effective_text.strip()
            return _place_table(block, text) if resolved_type is BlockType.TABLE else text
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
        return _place_table(block, placeholder) if resolved_type is BlockType.TABLE else placeholder
    if effective_text is None:
        return ""
    text = effective_text.strip()
    if not text:
        return ""

    if resolved_type is BlockType.TABLE:
        return _place_table(block, text)

    if resolved_type is BlockType.TITLE:
        return f"# {_single_line(text)}"
    if resolved_type is BlockType.SUBTITLE:
        return f"## {_single_line(text)}"
    if resolved_type is BlockType.HEADING:
        level = max(2, block.heading_level or 2)
        return f"{'#' * level} {_single_line(text)}"
    if resolved_type is BlockType.BYLINE:
        return f"*{text}*"
    if resolved_type is BlockType.QUOTE:
        return "\n".join(f"> {line}" for line in text.splitlines())
    if resolved_type is BlockType.CAPTION:
        return f"*{text}*"
    if resolved_type is BlockType.FOOTNOTE:
        lines = text.splitlines()
        label = _footnote_label(block)
        return "\n".join([f"> **{label}:** {lines[0]}", *(f"> {line}" for line in lines[1:])])
    if resolved_type is BlockType.EQUATION:
        return f"$$\n{text}\n$$"
    return text


def _insert_markdown_footnote_references(
    value: str,
    footnotes: list[tuple[TranslatedBlock, str, int, str]],
) -> str:
    result = value
    for _footnote, _, offset, identity in sorted(
        footnotes,
        key=lambda item: (item[2], item[0].order),
        reverse=True,
    ):
        bounded = min(max(offset, 0), len(result))
        result = f"{result[:bounded]}[^{identity}]{result[bounded:]}"
    return result


def _markdown_footnote_definition(
    footnote: TranslatedBlock,
    value: str,
    identity: str | None = None,
) -> str:
    lines = value.strip().splitlines()
    reference = identity or (footnote.footnote_id.id if footnote.footnote_id else footnote.block_id)
    return "\n".join([f"[^{reference}]: {lines[0]}", *(f"    {line}" for line in lines[1:])])


def _map_anchor_offset(machine_text: str, effective_text: str, offset: int) -> int:
    bounded = min(max(offset, 0), len(machine_text))
    if machine_text == effective_text:
        return min(bounded, len(effective_text))
    matcher = SequenceMatcher(a=machine_text, b=effective_text, autojunk=False)
    for tag, machine_start, machine_end, effective_start, effective_end in matcher.get_opcodes():
        if machine_start <= bounded <= machine_end:
            if tag == "equal":
                return effective_start + min(
                    bounded - machine_start, effective_end - effective_start
                )
            return effective_end
    return len(effective_text)


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
    if block.footnote_id is not None and block.footnote_id.text is not None:
        label = f"{label} {_escape_markdown_label(block.footnote_id.text)}"
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
