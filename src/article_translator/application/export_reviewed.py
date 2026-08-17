from __future__ import annotations

import re
from collections.abc import Mapping
from difflib import SequenceMatcher

from article_translator.application.footnotes import project_footnotes
from article_translator.application.paragraphs import ParagraphProjection, project_paragraphs
from article_translator.domain.enums import BlockType, ManualInsertionReason, SegmentHandling
from article_translator.domain.models import (
    DocumentTranslation,
    MarkdownExportSettings,
    TranslatedBlock,
)


def compile_text(
    document: DocumentTranslation,
    settings: MarkdownExportSettings,
    *,
    editorial_overrides: Mapping[str, str] | None = None,
    type_overrides: Mapping[str, BlockType] | None = None,
) -> str:
    """Project canonical blocks and effective revisions into readable plain text."""

    overrides = editorial_overrides or {}
    resolved_types = type_overrides or {}
    blocks = [block for page in document.pages for block in page.blocks]
    effective_text_by_id = {
        block.block_id: overrides.get(block.block_id, block.translated_text) for block in blocks
    }
    footnote_projections = project_footnotes(
        blocks,
        effective_text_by_id=effective_text_by_id,
        type_overrides=resolved_types,
    )
    merged_footnote_ids = {
        fragment_id
        for projection in footnote_projections
        for fragment_id in projection.fragment_ids[1:]
    }
    merged_text_by_first_id = {
        projection.first_block.block_id: projection.text for projection in footnote_projections
    }
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
    merged_paragraph_text_by_first_id = {
        projection.paragraph_id: _plain_paragraph(projection)
        for projection in paragraph_projections
        if len(projection.fragments) > 1
    }
    parts: list[str] = []
    list_items: list[str] = []
    for page in document.pages:
        for block in page.blocks:
            block_type = resolved_types.get(block.block_id, block.type)
            if not _should_include(block, settings, block_type=block_type):
                continue
            effective_text = overrides.get(block.block_id, block.translated_text)
            if block.block_id in merged_footnote_ids:
                continue
            if block.block_id in merged_paragraph_ids:
                continue
            if block.block_id in merged_text_by_first_id:
                effective_text = merged_text_by_first_id[block.block_id]
            if block.block_id in merged_paragraph_text_by_first_id:
                effective_text = merged_paragraph_text_by_first_id[block.block_id]
            if block_type is BlockType.LIST_ITEM:
                if effective_text is not None and effective_text.strip():
                    list_items.append(f"- {_plain_text(effective_text)}")
                continue
            _flush_plain_list(parts, list_items)
            rendered = _render_text_block(block, effective_text, block_type=block_type)
            if not rendered:
                continue
            parts.append(rendered)
        _flush_plain_list(parts, list_items)

    rendered_document = "\n\n".join(parts).rstrip()
    return f"{rendered_document}\n" if rendered_document else ""


def compile_latex(
    document: DocumentTranslation,
    settings: MarkdownExportSettings,
    *,
    editorial_overrides: Mapping[str, str] | None = None,
    type_overrides: Mapping[str, BlockType] | None = None,
    footnote_owner_overrides: Mapping[str, tuple[str | None, int | None, bool]] | None = None,
) -> str:
    """Project canonical blocks directly into safe XeLaTeX source."""

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
    owned_footnotes: dict[str, list[tuple[TranslatedBlock, str, int]]] = {}
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
        note_text = projection.text
        owner = blocks_by_id.get(owner_id) if owner_id is not None else None
        owner_text = effective_text_by_id.get(owner_id) if owner_id is not None else None
        if owner_id is None or owner_review_required:
            continue
        if owner is None or owner_text is None or note_text is None or anchor_offset is None:
            continue
        mapped_offset = _map_anchor_offset(
            owner.translated_text or "",
            owner_text,
            anchor_offset,
        )
        owned_footnotes.setdefault(owner_id, []).append((footnote, note_text, mapped_offset))
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
        projection.paragraph_id: _render_latex_paragraph(
            projection,
            owned_footnotes=owned_footnotes,
            include_page_comments=settings.include_page_comments,
        )
        for projection in paragraph_projections
        if len(projection.fragments) > 1
    }
    parts: list[str] = []
    list_items: list[str] = []
    for page in document.pages:
        page_marker = (
            f"% original-page: {page.original_page_number}"
            if settings.include_page_comments
            and page.original_page_number not in merged_paragraph_pages
            else None
        )
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
            if block_type is BlockType.LIST_ITEM:
                if effective_text is not None and effective_text.strip():
                    list_items.append(_latex_text(effective_text))
                continue
            if _flush_latex_list(parts, list_items) and page_marker is not None:
                parts.insert(len(parts) - 1, page_marker)
                page_marker = None
            rendered = merged_paragraph_text_by_first_id.get(block.block_id)
            if rendered is None:
                rendered = _render_latex_block(
                    block,
                    effective_text,
                    block_type=block_type,
                    inline_footnotes=owned_footnotes.get(block.block_id, []),
                )
            if not rendered:
                continue
            if page_marker is not None:
                parts.append(page_marker)
                page_marker = None
            parts.append(rendered)
        if _flush_latex_list(parts, list_items) and page_marker is not None:
            parts.insert(len(parts) - 1, page_marker)
            page_marker = None
        if page_marker is not None:
            parts.append(page_marker)

    body = "\n\n".join(parts).rstrip()
    title = _latex_text(document.source_file_name)
    return (
        "\\documentclass[11pt,a4paper]{article}\n"
        "\\usepackage{fontspec}\n"
        "\\usepackage[a4paper,margin=28mm]{geometry}\n"
        "\\usepackage{microtype}\n"
        "\\usepackage{array}\n"
        "\\usepackage{booktabs}\n"
        "\\usepackage{longtable}\n"
        "\\usepackage[hidelinks]{hyperref}\n"
        "\\defaultfontfeatures{Ligatures=TeX}\n"
        "\\setlength{\\parindent}{0pt}\n"
        "\\setlength{\\parskip}{0.7em}\n"
        f"\\hypersetup{{pdftitle={{{title}}}}}\n"
        "\\begin{document}\n"
        f"{body}\n"
        "\\end{document}\n"
    )


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


def _render_text_block(
    block: TranslatedBlock,
    effective_text: str | None,
    *,
    block_type: BlockType | None = None,
) -> str:
    resolved_type = block_type or block.type
    if block.segment_handling is SegmentHandling.MANUAL_INSERTION:
        if effective_text is not None and effective_text.strip():
            return (
                _plain_table(effective_text)
                if resolved_type is BlockType.TABLE
                else _plain_text(effective_text)
            )
        return _manual_insertion_label(block)
    if effective_text is None or not effective_text.strip():
        return ""
    if resolved_type is BlockType.TABLE:
        return _plain_table(effective_text)
    if resolved_type is BlockType.FOOTNOTE:
        marker = (
            f" {block.footnote_id.text}"
            if block.footnote_id is not None and block.footnote_id.text
            else ""
        )
        return f"Footnote{marker}: {_plain_text(effective_text)}"
    return _plain_text(effective_text)


def _plain_paragraph(projection: ParagraphProjection) -> str:
    return " ".join(_plain_text(fragment.text).strip() for fragment in projection.fragments)


def _render_latex_paragraph(
    projection: ParagraphProjection,
    *,
    owned_footnotes: Mapping[str, list[tuple[TranslatedBlock, str, int]]],
    include_page_comments: bool,
) -> str:
    rendered: list[str] = []
    previous_block_id: str | None = None
    for fragment in projection.fragments:
        if rendered:
            rendered.append(
                (
                    f"\n% original-page: {fragment.block.original_page_number}; "
                    f"continues-from: {previous_block_id}\n"
                )
                if include_page_comments
                else " "
            )
        rendered.append(
            _latex_text_with_footnotes(
                fragment.text,
                owned_footnotes.get(fragment.block.block_id, []),
            )
        )
        previous_block_id = fragment.block.block_id
    return f"{''.join(rendered).rstrip()}\\par"


def _render_latex_block(
    block: TranslatedBlock,
    effective_text: str | None,
    *,
    block_type: BlockType | None = None,
    inline_footnotes: list[tuple[TranslatedBlock, str, int]] | None = None,
) -> str:
    if block.segment_handling is SegmentHandling.MANUAL_INSERTION:
        if effective_text is not None and effective_text.strip():
            if block.type is BlockType.TABLE:
                return _latex_table(block, effective_text)
            return _latex_quote(_latex_text(effective_text))
        return _latex_quote(f"\\textbf{{{_latex_text(_manual_insertion_label(block))}}}")
    if effective_text is None or not effective_text.strip():
        return ""
    resolved_type = block_type or block.type
    text = _latex_text_with_footnotes(effective_text, inline_footnotes or [])
    if resolved_type is BlockType.TABLE:
        return _latex_table(block, effective_text)
    if resolved_type is BlockType.TITLE:
        return f"\\section*{{{text}}}"
    if resolved_type in {BlockType.SUBTITLE, BlockType.HEADING}:
        return f"\\subsection*{{{text}}}"
    if resolved_type is BlockType.BYLINE:
        return f"\\textit{{{text}}}\\par"
    if resolved_type is BlockType.QUOTE:
        return _latex_quote(text)
    if resolved_type is BlockType.CAPTION:
        return f"\\begin{{center}}\\small\\textit{{{text}}}\\end{{center}}"
    if resolved_type is BlockType.FOOTNOTE:
        return _latex_quote(f"\\small\\textbf{{Footnote (owner requires review):}} {text}")
    if resolved_type is BlockType.EQUATION:
        return _latex_quote(f"\\ttfamily {text}")
    return f"{text}\\par"


def _latex_text_with_footnotes(
    value: str,
    footnotes: list[tuple[TranslatedBlock, str, int]],
) -> str:
    if not footnotes:
        return _latex_text(value)
    parts: list[str] = []
    cursor = 0
    for _, note_text, offset in sorted(
        footnotes,
        key=lambda item: (item[2], item[0].order),
    ):
        bounded_offset = min(max(offset, cursor), len(value))
        parts.append(_latex_inline_fragment(value[cursor:bounded_offset]))
        parts.append(f"\\footnote{{{_latex_text(note_text)}}}")
        cursor = bounded_offset
    parts.append(_latex_inline_fragment(value[cursor:]))
    return "".join(parts)


def _latex_inline_fragment(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "$": r"\$",
        "&": r"\&",
        "#": r"\#",
        "%": r"\%",
        "_": r"\_",
        "^": r"\textasciicircum{}",
        "~": r"\textasciitilde{}",
    }
    return "".join(replacements.get(character, character) for character in value).replace("\n", " ")


def _map_anchor_offset(machine_text: str, effective_text: str, offset: int) -> int:
    """Keep an inline marker aligned when surrounding owner text was edited."""

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


def _manual_insertion_label(block: TranslatedBlock) -> str:
    reason = {
        ManualInsertionReason.TABLE: "table",
        ManualInsertionReason.TABLE_LIKE: "table-like material",
        ManualInsertionReason.FIGURE: "figure",
        None: "material",
    }[block.manual_insertion_reason]
    return f"Manual insertion required: {reason} on original page {block.original_page_number}."


def _plain_text(value: str) -> str:
    return "\n".join(line.rstrip() for line in value.strip().splitlines())


def _flush_plain_list(parts: list[str], items: list[str]) -> None:
    if items:
        parts.append("\n".join(items))
        items.clear()


def _flush_latex_list(parts: list[str], items: list[str]) -> bool:
    if not items:
        return False
    rendered = "\n".join(f"\\item {item}" for item in items)
    parts.append(f"\\begin{{itemize}}\n{rendered}\n\\end{{itemize}}")
    items.clear()
    return True


def _latex_quote(value: str) -> str:
    return f"\\begin{{quote}}\n{value}\n\\end{{quote}}"


def _latex_text(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "$": r"\$",
        "&": r"\&",
        "#": r"\#",
        "%": r"\%",
        "_": r"\_",
        "^": r"\textasciicircum{}",
        "~": r"\textasciitilde{}",
    }
    escaped = "".join(replacements.get(character, character) for character in value.strip())
    paragraphs = re.split(r"\n\s*\n", escaped)
    return "\\par\n".join(part.replace("\n", " ") for part in paragraphs)


def _plain_table(value: str) -> str:
    parsed = _parse_gfm_table(value)
    if parsed is None:
        return _plain_text(value)
    _, rows, _ = parsed
    return "\n".join("\t".join(cell.strip() for cell in row) for row in rows)


def _latex_table(block: TranslatedBlock, value: str) -> str:
    parsed = _parse_gfm_table(value)
    anchor = (
        f"% table-placement: [H!]; original-page: {block.original_page_number}; "
        f"block-id: {block.block_id}"
    )
    if parsed is None:
        fallback = _latex_quote("\\ttfamily " + _latex_text(value))
        return f"{anchor}\n{fallback}"
    alignments, rows, column_count = parsed
    width = 0.86 / column_count
    columns = []
    for alignment in alignments:
        ragged = "raggedleft" if alignment == "right" else "raggedright"
        columns.append(f">{{\\{ragged}\\arraybackslash}}p{{{width:.4f}\\linewidth}}")
    specification = "@{}" + "".join(columns) + "@{}"
    rendered_rows: list[str] = []
    for index, row in enumerate(rows):
        cells = [_latex_table_cell(cell, header=index == 0) for cell in row]
        rendered_rows.append(" & ".join(cells) + " \\\\")
        if index == 0:
            rendered_rows.append(r"\midrule")
    contents = "\n".join(rendered_rows)
    return (
        f"{anchor}\n"
        f"\\begin{{longtable}}{{{specification}}}\n"
        "\\toprule\n"
        f"{contents}\n"
        "\\bottomrule\n"
        "\\end{longtable}"
    )


def _latex_table_cell(value: str, *, header: bool) -> str:
    rendered = _latex_gfm_strong(value, render_emphasis=not header)
    return f"\\textbf{{{rendered}}}" if header else rendered


def _latex_gfm_strong(value: str, *, render_emphasis: bool = True) -> str:
    """Render the small GFM inline subset permitted in reconstructed table cells."""

    parts: list[str] = []
    plain: list[str] = []

    def flush_plain() -> None:
        if plain:
            parts.append(_latex_inline_fragment("".join(plain)))
            plain.clear()

    cursor = 0
    while cursor < len(value):
        if value[cursor] == "\\" and cursor + 1 < len(value):
            escaped_character = value[cursor + 1]
            if escaped_character in _MARKDOWN_ESCAPABLE_CHARACTERS:
                plain.append(escaped_character)
                cursor += 2
                continue
        if value.startswith("**", cursor):
            closing = _find_unescaped_strong_close(value, cursor + 2)
            if closing is not None:
                content = value[cursor + 2 : closing]
                if content and not content[0].isspace() and not content[-1].isspace():
                    flush_plain()
                    rendered_content = _latex_gfm_strong(content, render_emphasis=False)
                    parts.append(
                        f"\\textbf{{{rendered_content}}}" if render_emphasis else rendered_content
                    )
                    cursor = closing + 2
                    continue
        plain.append(value[cursor])
        cursor += 1
    flush_plain()
    return "".join(parts)


def _find_unescaped_strong_close(value: str, start: int) -> int | None:
    cursor = start
    while cursor < len(value) - 1:
        if value[cursor] == "\\" and cursor + 1 < len(value):
            cursor += 2
            continue
        if value.startswith("**", cursor):
            return cursor
        cursor += 1
    return None


_MARKDOWN_ESCAPABLE_CHARACTERS = frozenset("!\\\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")


def _parse_gfm_table(value: str) -> tuple[list[str], list[list[str]], int] | None:
    lines = [line.strip() for line in value.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    header = _split_gfm_row(lines[0])
    separators = _split_gfm_row(lines[1])
    if not header or len(header) != len(separators):
        return None
    if any(re.fullmatch(r":?-{3,}:?", separator.strip()) is None for separator in separators):
        return None
    alignments = [
        "right" if separator.strip().endswith(":") else "left" for separator in separators
    ]
    rows = [header]
    for line in lines[2:]:
        row = _split_gfm_row(line)
        if len(row) < len(header):
            row.extend([""] * (len(header) - len(row)))
        rows.append(row[: len(header)])
    return alignments, rows, len(header)


def _split_gfm_row(line: str) -> list[str]:
    content = line.strip().removeprefix("|").removesuffix("|")
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for character in content:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip())
    return cells
