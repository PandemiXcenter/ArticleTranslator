from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from article_translator.domain.editorial import BlockRevision
from article_translator.domain.enums import BlockType, ExtractionStatus
from article_translator.domain.models import (
    ArtifactRef,
    GeneratedBlock,
    GeneratedPagePayload,
    JobManifest,
    PreparedPage,
)

HASH = "a" * 64


def artifact(path: str, media_type: str) -> ArtifactRef:
    return ArtifactRef(
        path=path,
        sha256=HASH,
        media_type=media_type,
        byte_count=10,
    )


def test_generated_payload_requires_contiguous_reading_order() -> None:
    with pytest.raises(ValidationError, match="block order must be contiguous"):
        GeneratedPagePayload(
            blocks=[
                GeneratedBlock(
                    order=2,
                    type=BlockType.BODY,
                    source_text="Kilde",
                    translated_text="Source",
                )
            ]
        )


def test_provider_schema_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        GeneratedPagePayload.model_validate(
            {
                "blocks": [],
                "confidence": 0.93,
            }
        )


def test_manifest_keeps_physical_page_and_pdf_label_distinct() -> None:
    page = PreparedPage(
        original_page_number=1,
        pdf_page_label="iv",
        markdown=artifact("pages/0001/source.md", "text/markdown"),
        image=artifact("pages/0001/page.png", "image/png"),
        extraction_status=ExtractionStatus.EXTRACTED,
        extracted_character_count=20,
    )
    manifest = JobManifest(
        job_id="example-aaaaaaaaaaaa",
        preparation_id="preparation-1",
        document_id=HASH,
        source_file_name="example.pdf",
        source_file_sha256=HASH,
        image_dpi=150,
        page_count=1,
        pages=[page],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert manifest.pages[0].original_page_number == 1
    assert manifest.pages[0].pdf_page_label == "iv"


def test_manifest_rejects_gaps_in_physical_pages() -> None:
    page = PreparedPage(
        original_page_number=2,
        markdown=artifact("pages/0002/source.md", "text/markdown"),
        image=artifact("pages/0002/page.png", "image/png"),
        extraction_status=ExtractionStatus.EMPTY,
        extracted_character_count=0,
    )
    with pytest.raises(ValidationError, match="manifest pages must be physical pages"):
        JobManifest(
            job_id="example-aaaaaaaaaaaa",
            preparation_id="preparation-1",
            document_id=HASH,
            source_file_name="example.pdf",
            source_file_sha256=HASH,
            image_dpi=150,
            page_count=1,
            pages=[page],
        )


@pytest.mark.parametrize(
    "path",
    ["/absolute/page.png", "../escape.png", "pages/../../escape.png"],
)
def test_artifact_paths_cannot_escape_job(path: str) -> None:
    with pytest.raises(ValidationError, match="artifact paths must be relative"):
        artifact(path, "image/png")


def test_future_revisions_are_scoped_to_an_immutable_translation_run() -> None:
    first = BlockRevision(
        revision_id="revision-1",
        document_id=HASH,
        translation_run_id="run-1",
        block_id="p0001-b0001",
        base_revision=0,
        editorial_text="First edit",
    )
    second = first.model_copy(
        update={
            "revision_id": "revision-2",
            "translation_run_id": "run-2",
            "editorial_text": "Edit for a different machine run",
        }
    )

    assert first.block_id == second.block_id
    assert first.translation_run_id != second.translation_run_id


def test_future_revision_requires_run_scope_and_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="translation_run_id"):
        BlockRevision.model_validate(
            {
                "revision_id": "revision-1",
                "document_id": HASH,
                "block_id": "p0001-b0001",
                "base_revision": 0,
                "editorial_text": "Edit",
                "page_number": 1,
            }
        )
