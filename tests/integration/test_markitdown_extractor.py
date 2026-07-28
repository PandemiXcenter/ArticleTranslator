from pathlib import Path
from struct import unpack
from typing import cast

import pytest
from markitdown import MarkItDown
from pypdf import PdfWriter
from pypdf.constants import PageLabelStyle

from article_translator.adapters.extraction import MarkItDownPageExtractor
from article_translator.domain.enums import ExtractionStatus


@pytest.mark.integration
def test_extractor_preserves_two_physical_blank_pages(tmp_path: Path) -> None:
    source = tmp_path / "two-pages.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    writer.add_blank_page(width=500, height=200)
    writer.set_page_label(
        0,
        0,
        style=cast(PageLabelStyle, PageLabelStyle.DECIMAL),
        prefix="front-",
        start=1,
    )
    writer.set_page_label(
        1,
        1,
        style=cast(PageLabelStyle, PageLabelStyle.DECIMAL),
        prefix="body-",
        start=1,
    )
    with source.open("wb") as handle:
        writer.write(handle)

    artifact_root = tmp_path / "job"
    pages = MarkItDownPageExtractor().extract_pages(
        source,
        artifact_root,
        image_dpi=72,
    )

    assert [page.original_page_number for page in pages] == [1, 2]
    assert [page.pdf_page_label for page in pages] == ["front-1", "body-1"]
    assert all(page.extraction_status is ExtractionStatus.EMPTY for page in pages)
    image_sizes: list[tuple[int, int]] = []
    for page in pages:
        assert (artifact_root / page.markdown.path).is_file()
        image = artifact_root / page.image.path
        image_bytes = image.read_bytes()
        assert image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        image_sizes.append(unpack(">II", image_bytes[16:24]))
    assert image_sizes == [(300, 400), (500, 200)]


class FailingMarkItDown:
    def convert_stream(self, *_: object, **__: object) -> object:
        raise RuntimeError("synthetic extraction failure")


@pytest.mark.integration
def test_markdown_failure_preserves_the_matching_page_image(tmp_path: Path) -> None:
    source = tmp_path / "one-page.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=240, height=320)
    with source.open("wb") as handle:
        writer.write(handle)

    artifact_root = tmp_path / "job"
    extractor = MarkItDownPageExtractor(converter=cast(MarkItDown, FailingMarkItDown()))
    [page] = extractor.extract_pages(
        source,
        artifact_root,
        image_dpi=72,
    )

    assert page.extraction_status is ExtractionStatus.FAILED
    assert page.extraction_warnings == [
        "MarkItDown failed: RuntimeError: synthetic extraction failure"
    ]
    image_bytes = (artifact_root / page.image.path).read_bytes()
    assert unpack(">II", image_bytes[16:24]) == (240, 320)
