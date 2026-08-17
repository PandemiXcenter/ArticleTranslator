from datetime import UTC, datetime

from article_translator.application.export_reviewed import compile_latex, compile_text
from article_translator.domain.enums import BlockType, ExtractionStatus, SegmentContinuation
from article_translator.domain.models import (
    ArtifactRef,
    DocumentTranslation,
    MarkdownExportSettings,
    PageTranslation,
    ProviderMetadata,
    TranslatedBlock,
    TranslationSettings,
)

HASH = "c" * 64
RUN_ID = "2" * 32
FIXED_TIME = datetime(2026, 1, 2, tzinfo=UTC)


def _artifact(page_number: int, suffix: str, media_type: str) -> ArtifactRef:
    return ArtifactRef(
        path=f"prepared/pages/{page_number:04d}/{suffix}",
        sha256=HASH,
        media_type=media_type,
        byte_count=10,
    )


def _body(
    page_number: int,
    text: str,
    *,
    continuation: SegmentContinuation,
    continues_from: str | None = None,
) -> TranslatedBlock:
    return TranslatedBlock(
        block_id=f"p{page_number:04d}-b0001",
        original_page_number=page_number,
        order=1,
        type=BlockType.BODY,
        source_text=f"Source page {page_number}",
        translated_text=text,
        paragraph_continuation=continuation,
        continues_from_block_id=continues_from,
    )


def _continued_document() -> DocumentTranslation:
    first = _body(
        1,
        "The paragraph begins",
        continuation=SegmentContinuation.TO_NEXT_PAGE,
    )
    second = _body(
        2,
        "and crosses another page",
        continuation=SegmentContinuation.FROM_PREVIOUS_AND_TO_NEXT_PAGE,
        continues_from=first.block_id,
    )
    third = _body(
        3,
        "before ending.",
        continuation=SegmentContinuation.FROM_PREVIOUS_PAGE,
        continues_from=second.block_id,
    )
    pages = []
    for page_number, block in enumerate((first, second, third), start=1):
        pages.append(
            PageTranslation(
                translation_run_id=RUN_ID,
                original_page_number=page_number,
                extraction_status=ExtractionStatus.EXTRACTED,
                extracted_character_count=6,
                source_markdown=f"source {page_number}",
                source_markdown_artifact=_artifact(
                    page_number,
                    "source.md",
                    "text/markdown",
                ),
                source_image=_artifact(page_number, "page.png", "image/png"),
                blocks=[block],
                input_fingerprint=HASH,
                provider=ProviderMetadata(
                    provider="fake",
                    model="fake-v1",
                    prompt_version="translate-page-v8",
                ),
                translated_at=FIXED_TIME,
            )
        )
    return DocumentTranslation(
        translation_run_id=RUN_ID,
        document_id=HASH,
        job_id="document-cccccccccccc",
        source_file_name="continued.pdf",
        source_file_sha256=HASH,
        page_count=3,
        translation_settings=TranslationSettings(),
        pages=pages,
        created_at=FIXED_TIME,
    )


def _latex_body(source: str) -> str:
    return source.split("\\begin{document}\n", 1)[1].split("\n\\end{document}", 1)[0]


def test_plain_text_joins_three_page_paragraph_with_effective_edits() -> None:
    document = _continued_document()
    middle_id = document.pages[1].blocks[0].block_id

    assert (
        compile_text(
            document,
            MarkdownExportSettings(include_page_comments=False),
            editorial_overrides={middle_id: "and contains an edit"},
        )
        == "The paragraph begins and contains an edit before ending.\n"
    )


def test_latex_renders_one_paragraph_and_one_final_par_command() -> None:
    document = _continued_document()
    middle_id = document.pages[1].blocks[0].block_id

    body = _latex_body(
        compile_latex(
            document,
            MarkdownExportSettings(include_page_comments=False),
            editorial_overrides={middle_id: "and contains an edit"},
        )
    )

    assert body == "The paragraph begins and contains an edit before ending.\\par"
    assert body.count("\\par") == 1


def test_latex_keeps_physical_page_comments_inline_without_paragraph_breaks() -> None:
    document = _continued_document()

    body = _latex_body(compile_latex(document, MarkdownExportSettings()))

    assert body == (
        "% original-page: 1\n\n"
        "The paragraph begins\n"
        "% original-page: 2; continues-from: p0001-b0001\n"
        "and crosses another page\n"
        "% original-page: 3; continues-from: p0002-b0001\n"
        "before ending.\\par"
    )
    assert body.count("\\par") == 1


def test_effective_non_body_type_breaks_paragraph_projection_for_export() -> None:
    document = _continued_document()
    middle_id = document.pages[1].blocks[0].block_id

    body = _latex_body(
        compile_latex(
            document,
            MarkdownExportSettings(include_page_comments=False),
            type_overrides={middle_id: BlockType.HEADING},
        )
    )

    assert body == (
        "The paragraph begins\\par\n\n"
        "\\subsection*{and crosses another page}\n\n"
        "before ending.\\par"
    )
