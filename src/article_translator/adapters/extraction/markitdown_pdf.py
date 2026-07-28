from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pypdfium2 as pdfium
from markitdown import MarkItDown, StreamInfo
from pypdf import PdfReader, PdfWriter

from article_translator.domain.enums import ExtractionStatus
from article_translator.domain.errors import ArtifactError
from article_translator.domain.models import ArtifactRef, PreparedPage
from article_translator.hashing import sha256_file


class MarkItDownPageExtractor:
    """Splits first so MarkItDown cannot discard physical page boundaries."""

    def __init__(self, converter: MarkItDown | None = None) -> None:
        self._converter = converter or MarkItDown()

    def extract_pages(
        self,
        source_pdf: Path,
        artifact_root: Path,
        *,
        image_dpi: int,
    ) -> list[PreparedPage]:
        if source_pdf.suffix.lower() != ".pdf":
            raise ArtifactError(f"Expected a PDF, got: {source_pdf.name}")
        if not source_pdf.is_file():
            raise ArtifactError(f"Source PDF does not exist: {source_pdf}")

        reader = PdfReader(str(source_pdf))
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise ArtifactError("Password-protected PDFs are not supported yet")

        page_count = len(reader.pages)
        if page_count == 0:
            raise ArtifactError("The PDF contains no pages")

        try:
            page_labels: list[str | None] = [str(label) for label in reader.page_labels]
        except Exception:  # malformed page-label metadata should not block translation
            page_labels = [None] * page_count

        pages: list[PreparedPage] = []
        with pdfium.PdfDocument(str(source_pdf)) as rendered_pdf:
            if len(rendered_pdf) != page_count:
                raise ArtifactError(
                    "PDF readers disagree about page count; refusing to mis-pair text and images"
                )

            for page_index in range(page_count):
                original_page_number = page_index + 1
                page_dir = artifact_root / "pages" / f"{original_page_number:04d}"
                page_dir.mkdir(parents=True, exist_ok=True)

                markdown, status, warnings = self._extract_markdown(
                    reader=reader,
                    page_index=page_index,
                    page_number=original_page_number,
                )
                markdown_path = page_dir / "source.md"
                markdown_path.write_text(markdown, encoding="utf-8")

                image_path = page_dir / "page.png"
                self._render_page(
                    rendered_pdf=rendered_pdf,
                    page_index=page_index,
                    image_path=image_path,
                    image_dpi=image_dpi,
                )

                label = page_labels[page_index] if page_index < len(page_labels) else None
                pages.append(
                    PreparedPage(
                        original_page_number=original_page_number,
                        pdf_page_label=str(label) if label is not None else None,
                        markdown=self._reference(
                            artifact_root,
                            markdown_path,
                            "text/markdown; charset=utf-8",
                        ),
                        image=self._reference(artifact_root, image_path, "image/png"),
                        extraction_status=status,
                        extracted_character_count=len(markdown),
                        extraction_warnings=warnings,
                    )
                )
        return pages

    def _extract_markdown(
        self,
        *,
        reader: PdfReader,
        page_index: int,
        page_number: int,
    ) -> tuple[str, ExtractionStatus, list[str]]:
        try:
            page_pdf = BytesIO()
            writer = PdfWriter()
            writer.add_page(reader.pages[page_index])
            writer.write(page_pdf)
            page_pdf.seek(0)
            result = self._converter.convert_stream(
                page_pdf,
                stream_info=StreamInfo(
                    mimetype="application/pdf",
                    extension=".pdf",
                    filename=f"page-{page_number:04d}.pdf",
                ),
            )
            markdown = result.markdown
        except Exception as exc:
            warning = f"MarkItDown failed: {type(exc).__name__}: {exc}"
            return "", ExtractionStatus.FAILED, [warning]

        status = ExtractionStatus.EXTRACTED if markdown.strip() else ExtractionStatus.EMPTY
        return markdown, status, []

    @staticmethod
    def _render_page(
        *,
        rendered_pdf: pdfium.PdfDocument,
        page_index: int,
        image_path: Path,
        image_dpi: int,
    ) -> None:
        page = rendered_pdf[page_index]
        bitmap = page.render(scale=image_dpi / 72)
        image = bitmap.to_pil()
        try:
            image.save(image_path, format="PNG", compress_level=6)
        finally:
            image.close()
            bitmap.close()
            page.close()

    @staticmethod
    def _reference(root: Path, path: Path, media_type: str) -> ArtifactRef:
        return ArtifactRef(
            path=path.relative_to(root).as_posix(),
            sha256=sha256_file(path),
            media_type=media_type,
            byte_count=path.stat().st_size,
        )
