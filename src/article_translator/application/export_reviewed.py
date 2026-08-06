from __future__ import annotations

import re
from collections.abc import Mapping

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
) -> str:
    """Project canonical blocks and effective revisions into readable plain text."""

    overrides = editorial_overrides or {}
    parts: list[str] = []
    part_by_block_id: dict[str, int] = {}
    list_items: list[str] = []
    for page in document.pages:
        for block in page.blocks:
            if not _should_include(block, settings):
                continue
            effective_text = overrides.get(block.block_id, block.translated_text)
            if block.type is BlockType.LIST_ITEM:
                if effective_text is not None and effective_text.strip():
                    list_items.append(f"- {_plain_text(effective_text)}")
                continue
            _flush_plain_list(parts, list_items)
            rendered = _render_text_block(block, effective_text)
            if not rendered:
                continue
            target_index = (
                part_by_block_id.get(block.continues_from_block_id)
                if block.continues_from_block_id is not None
                else None
            )
            if target_index is not None:
                parts[target_index] = f"{parts[target_index].rstrip()} {rendered.lstrip()}"
                part_by_block_id[block.block_id] = target_index
            else:
                parts.append(rendered)
                part_by_block_id[block.block_id] = len(parts) - 1
        _flush_plain_list(parts, list_items)

    rendered_document = "\n\n".join(parts).rstrip()
    return f"{rendered_document}\n" if rendered_document else ""


def compile_latex(
    document: DocumentTranslation,
    settings: MarkdownExportSettings,
    *,
    editorial_overrides: Mapping[str, str] | None = None,
) -> str:
    """Project canonical blocks directly into safe XeLaTeX source."""

    overrides = editorial_overrides or {}
    parts: list[str] = []
    part_by_block_id: dict[str, int] = {}
    list_items: list[str] = []
    for page in document.pages:
        page_marker = (
            f"% original-page: {page.original_page_number}"
            if settings.include_page_comments
            else None
        )
        for block in page.blocks:
            if not _should_include(block, settings):
                continue
            effective_text = overrides.get(block.block_id, block.translated_text)
            if block.type is BlockType.LIST_ITEM:
                if effective_text is not None and effective_text.strip():
                    list_items.append(_latex_text(effective_text))
                continue
            if _flush_latex_list(parts, list_items) and page_marker is not None:
                parts.insert(len(parts) - 1, page_marker)
                page_marker = None
            rendered = _render_latex_block(block, effective_text)
            if not rendered:
                continue
            target_index = (
                part_by_block_id.get(block.continues_from_block_id)
                if block.continues_from_block_id is not None
                else None
            )
            if target_index is not None:
                boundary = ""
                if settings.include_page_comments:
                    boundary = (
                        f"\n% original-page: {block.original_page_number}; "
                        f"continues-from: {block.continues_from_block_id}\n"
                    )
                parts[target_index] = f"{parts[target_index].rstrip()}{boundary}{rendered.lstrip()}"
                part_by_block_id[block.block_id] = target_index
                page_marker = None
            else:
                if page_marker is not None:
                    parts.append(page_marker)
                    page_marker = None
                parts.append(rendered)
                part_by_block_id[block.block_id] = len(parts) - 1
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


def _should_include(block: TranslatedBlock, settings: MarkdownExportSettings) -> bool:
    if block.type is BlockType.HEADER:
        return settings.include_headers
    if block.type is BlockType.FOOTER:
        return settings.include_footers
    if block.type is BlockType.PAGE_NUMBER:
        return settings.include_page_numbers
    return True


def _render_text_block(block: TranslatedBlock, effective_text: str | None) -> str:
    if block.segment_handling is SegmentHandling.MANUAL_INSERTION:
        if effective_text is not None and effective_text.strip():
            return (
                _plain_table(effective_text)
                if block.type is BlockType.TABLE
                else _plain_text(effective_text)
            )
        return _manual_insertion_label(block)
    if effective_text is None or not effective_text.strip():
        return ""
    if block.type is BlockType.TABLE:
        return _plain_table(effective_text)
    if block.type is BlockType.FOOTNOTE:
        marker = f" {block.footnote_marker}" if block.footnote_marker else ""
        return f"Footnote{marker}: {_plain_text(effective_text)}"
    return _plain_text(effective_text)


def _render_latex_block(block: TranslatedBlock, effective_text: str | None) -> str:
    if block.segment_handling is SegmentHandling.MANUAL_INSERTION:
        if effective_text is not None and effective_text.strip():
            if block.type is BlockType.TABLE:
                return _latex_table(block, effective_text)
            return _latex_quote(_latex_text(effective_text))
        return _latex_quote(f"\\textbf{{{_latex_text(_manual_insertion_label(block))}}}")
    if effective_text is None or not effective_text.strip():
        return ""
    text = _latex_text(effective_text)
    if block.type is BlockType.TABLE:
        return _latex_table(block, effective_text)
    if block.type is BlockType.TITLE:
        return f"\\section*{{{text}}}"
    if block.type in {BlockType.SUBTITLE, BlockType.HEADING}:
        return f"\\subsection*{{{text}}}"
    if block.type is BlockType.BYLINE:
        return f"\\textit{{{text}}}\\par"
    if block.type is BlockType.QUOTE:
        return _latex_quote(text)
    if block.type is BlockType.CAPTION:
        return f"\\begin{{center}}\\small\\textit{{{text}}}\\end{{center}}"
    if block.type is BlockType.FOOTNOTE:
        marker = f" {block.footnote_marker}" if block.footnote_marker else ""
        label = _latex_text(f"Footnote{marker}")
        return _latex_quote(f"\\small\\textbf{{{label}:}} {text}")
    if block.type is BlockType.EQUATION:
        return _latex_quote(f"\\ttfamily {text}")
    return f"{text}\\par"


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
        cells = [
            f"\\textbf{{{_latex_text(cell)}}}" if index == 0 else _latex_text(cell) for cell in row
        ]
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
