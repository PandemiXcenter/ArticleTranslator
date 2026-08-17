import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from article_translator.adapters.storage.filesystem import FilesystemArtifactRepository
from article_translator.domain.editorial import BlockRevision, ReviewPosition
from article_translator.domain.enums import (
    BlockType,
    ExtractionStatus,
    ManualInsertionReason,
    SegmentContinuation,
    SegmentHandling,
)
from article_translator.domain.errors import ArtifactError
from article_translator.domain.models import (
    ArtifactRef,
    DocumentTranslation,
    FootnoteDescription,
    FootnoteIdentity,
    PageTranslation,
    ProviderMetadata,
    TranslatedBlock,
    TranslationSettings,
)

DOCUMENT_ID = "a" * 64
RUN_ID = "1" * 32
BLOCK_ID = "p0001-b0001"


def _revision(
    number: int,
    *,
    document_id: str = DOCUMENT_ID,
    translation_run_id: str = RUN_ID,
    block_id: str = BLOCK_ID,
    text: str = "Reviewed",
) -> BlockRevision:
    return BlockRevision(
        revision_id=f"revision-{number}",
        document_id=document_id,
        translation_run_id=translation_run_id,
        block_id=block_id,
        revision_number=number,
        base_revision=number - 1,
        editorial_text=text,
        effective_type=BlockType.BODY,
    )


def test_filesystem_revisions_are_append_only_atomic_and_ordered(tmp_path: Path) -> None:
    repository = FilesystemArtifactRepository(tmp_path / "job")
    first = _revision(1, text="First")
    second = _revision(2, text="Second")

    repository.append_block_revision(first)
    repository.append_block_revision(second)

    assert repository.list_block_revisions(DOCUMENT_ID, RUN_ID, BLOCK_ID) == [
        first,
        second,
    ]
    first_path = tmp_path / "job" / "runs" / RUN_ID / "revisions" / BLOCK_ID / "0001.json"
    original = first_path.read_bytes()
    with pytest.raises(ArtifactError, match="stale"):
        repository.append_block_revision(_revision(1, text="Overwrite"))
    assert first_path.read_bytes() == original


def test_filesystem_revision_reads_validate_document_scope(tmp_path: Path) -> None:
    repository = FilesystemArtifactRepository(tmp_path / "job")
    repository.append_block_revision(_revision(1))

    with pytest.raises(ArtifactError, match="incorrect scope"):
        repository.list_block_revisions("b" * 64, RUN_ID, BLOCK_ID)


def test_filesystem_reads_v2_document_without_rewriting_immutable_artifact(
    tmp_path: Path,
) -> None:
    repository = FilesystemArtifactRepository(tmp_path / "job")
    reference = ArtifactRef(
        path="prepared/source.txt",
        sha256=DOCUMENT_ID,
        media_type="text/plain",
        byte_count=1,
    )
    page = PageTranslation(
        translation_run_id=RUN_ID,
        original_page_number=1,
        extraction_status=ExtractionStatus.EXTRACTED,
        extracted_character_count=1,
        source_markdown="A  B\n1  2",
        source_markdown_artifact=reference,
        source_image=reference.model_copy(update={"media_type": "image/png"}),
        blocks=[
            TranslatedBlock(
                block_id=BLOCK_ID,
                original_page_number=1,
                order=1,
                type=BlockType.TABLE,
                source_text="A  B\n1  2",
                translated_text="A  B\n1  2",
                legacy_translated_table=True,
            )
        ],
        input_fingerprint=DOCUMENT_ID,
        provider=ProviderMetadata(
            provider="legacy",
            model="legacy-v2",
            prompt_version="legacy-v2",
        ),
        translated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    document = DocumentTranslation(
        translation_run_id=RUN_ID,
        document_id=DOCUMENT_ID,
        job_id="legacy-job",
        source_file_name="legacy.pdf",
        source_file_sha256=DOCUMENT_ID,
        page_count=1,
        translation_settings=TranslationSettings(),
        pages=[page],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    payload = document.model_dump(mode="json")
    payload["schema_version"] = "2.0"
    payload["pages"][0]["schema_version"] = "2.0"
    legacy_block = payload["pages"][0]["blocks"][0]
    for field in (
        "segment_handling",
        "manual_insertion_reason",
        "continuation",
        "classification_review_required",
        "legacy_translated_table",
        "legacy_manual_table",
    ):
        legacy_block.pop(field)
    document_path = tmp_path / "job" / "runs" / RUN_ID / "output" / "document.json"
    document_path.parent.mkdir(parents=True)
    document_path.write_text(json.dumps(payload), encoding="utf-8")

    restored = repository.read_document(RUN_ID)

    assert restored.schema_version == "6.0"
    assert restored.pages[0].schema_version == "6.0"
    assert restored.pages[0].blocks[0].legacy_translated_table is True
    assert restored.pages[0].blocks[0].type is BlockType.TABLE
    assert json.loads(document_path.read_text(encoding="utf-8"))["schema_version"] == "2.0"


def test_filesystem_reads_v3_manual_table_as_legacy_without_reconstruction_or_rewrite(
    tmp_path: Path,
) -> None:
    repository = FilesystemArtifactRepository(tmp_path / "job")
    reference = ArtifactRef(
        path="prepared/source.txt",
        sha256=DOCUMENT_ID,
        media_type="text/plain",
        byte_count=1,
    )
    manual = TranslatedBlock(
        block_id=BLOCK_ID,
        original_page_number=1,
        order=1,
        type=BlockType.TABLE,
        source_text=None,
        translated_text=None,
        segment_handling=SegmentHandling.MANUAL_INSERTION,
        manual_insertion_reason=ManualInsertionReason.TABLE_LIKE,
        continuation=SegmentContinuation.COMPLETE,
        legacy_manual_table=True,
    )
    page = PageTranslation(
        translation_run_id=RUN_ID,
        original_page_number=1,
        extraction_status=ExtractionStatus.EXTRACTED,
        extracted_character_count=1,
        source_markdown="table OCR",
        source_markdown_artifact=reference,
        source_image=reference.model_copy(update={"media_type": "image/png"}),
        blocks=[manual],
        input_fingerprint=DOCUMENT_ID,
        provider=ProviderMetadata(
            provider="legacy",
            model="legacy-v3",
            prompt_version="translate-page-v3",
        ),
    )
    document = DocumentTranslation(
        translation_run_id=RUN_ID,
        document_id=DOCUMENT_ID,
        job_id="legacy-job",
        source_file_name="legacy.pdf",
        source_file_sha256=DOCUMENT_ID,
        page_count=1,
        translation_settings=TranslationSettings(),
        pages=[page],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    payload = document.model_dump(mode="json")
    payload["schema_version"] = "3.0"
    payload["pages"][0]["schema_version"] = "3.0"
    payload["pages"][0].pop("table_reconstruction")
    payload["pages"][0]["blocks"][0].pop("legacy_manual_table")
    document_path = tmp_path / "job" / "runs" / RUN_ID / "output" / "document.json"
    document_path.parent.mkdir(parents=True)
    original_bytes = json.dumps(payload).encode()
    document_path.write_bytes(original_bytes)

    restored = repository.read_document(RUN_ID)

    assert restored.schema_version == "6.0"
    assert restored.pages[0].blocks[0].legacy_manual_table is True
    assert restored.pages[0].blocks[0].segment_handling is SegmentHandling.MANUAL_INSERTION
    assert document_path.read_bytes() == original_bytes


def test_filesystem_reads_v4_footnote_with_unknown_owner_without_rewrite(tmp_path: Path) -> None:
    repository = FilesystemArtifactRepository(tmp_path / "job")
    reference = ArtifactRef(
        path="prepared/source.txt",
        sha256=DOCUMENT_ID,
        media_type="text/plain",
        byte_count=1,
    )
    footnote = TranslatedBlock(
        block_id=BLOCK_ID,
        original_page_number=1,
        order=1,
        type=BlockType.FOOTNOTE,
        source_text="Note",
        translated_text="Footnote",
        footnote_id=FootnoteIdentity(id="fn-p1-n1", text=None),
        footnote_description=FootnoteDescription(
            appearance="Small type below a rule.",
            handling="Starts and ends on this page.",
        ),
        footnote_owner_review_required=True,
        continuation=SegmentContinuation.COMPLETE,
    )
    page = PageTranslation(
        translation_run_id=RUN_ID,
        original_page_number=1,
        extraction_status=ExtractionStatus.EXTRACTED,
        extracted_character_count=1,
        source_markdown="note OCR",
        source_markdown_artifact=reference,
        source_image=reference.model_copy(update={"media_type": "image/png"}),
        blocks=[footnote],
        input_fingerprint=DOCUMENT_ID,
        provider=ProviderMetadata(
            provider="legacy",
            model="legacy-v4",
            prompt_version="translate-page-v5",
        ),
    )
    document = DocumentTranslation(
        translation_run_id=RUN_ID,
        document_id=DOCUMENT_ID,
        job_id="legacy-job",
        source_file_name="legacy.pdf",
        source_file_sha256=DOCUMENT_ID,
        page_count=1,
        translation_settings=TranslationSettings(),
        pages=[page],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    payload = document.model_dump(mode="json")
    payload["schema_version"] = "4.0"
    payload["pages"][0]["schema_version"] = "4.0"
    for field in (
        "footnote_owner_block_id",
        "footnote_anchor_offset",
        "footnote_owner_review_required",
        "footnote_id",
        "footnote_description",
        "footnote_continues_from_block_id",
    ):
        payload["pages"][0]["blocks"][0].pop(field)
    document_path = tmp_path / "job" / "runs" / RUN_ID / "output" / "document.json"
    document_path.parent.mkdir(parents=True)
    original_bytes = json.dumps(payload).encode()
    document_path.write_bytes(original_bytes)

    restored = repository.read_document(RUN_ID)

    restored_footnote = restored.pages[0].blocks[0]
    assert restored.schema_version == "6.0"
    assert restored.pages[0].blocks[0].footnote_id == FootnoteIdentity(
        id="fn-p1-n1",
        text=None,
    )
    assert restored_footnote.footnote_owner_block_id is None
    assert restored_footnote.footnote_anchor_offset is None
    assert restored_footnote.footnote_owner_review_required is True
    assert document_path.read_bytes() == original_bytes


def test_filesystem_review_position_is_atomic_mutable_and_run_scoped(tmp_path: Path) -> None:
    repository = FilesystemArtifactRepository(tmp_path / "job")
    assert repository.read_review_position(DOCUMENT_ID, RUN_ID) is None
    first = ReviewPosition(
        document_id=DOCUMENT_ID,
        translation_run_id=RUN_ID,
        original_page_number=1,
    )
    second = first.model_copy(update={"original_page_number": 3})

    repository.write_review_position(first)
    position_path = tmp_path / "job" / "runs" / RUN_ID / "review" / "position.json"
    assert position_path.is_file()
    assert repository.read_review_position(DOCUMENT_ID, RUN_ID) == first

    repository.write_review_position(second)

    assert repository.read_review_position(DOCUMENT_ID, RUN_ID) == second
    assert list(position_path.parent.glob("*.tmp")) == []
    with pytest.raises(ArtifactError, match="incorrect scope"):
        repository.read_review_position("b" * 64, RUN_ID)


def test_review_position_contract_is_strict_and_round_trips_json() -> None:
    position = ReviewPosition(
        document_id=DOCUMENT_ID,
        translation_run_id=RUN_ID,
        original_page_number=3,
    )

    assert ReviewPosition.model_validate_json(position.model_dump_json()) == position
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ReviewPosition.model_validate(
            {
                **position.model_dump(mode="python"),
                "unexpected": True,
            }
        )
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        ReviewPosition.model_validate(
            {
                **position.model_dump(mode="python"),
                "original_page_number": 0,
            }
        )


def test_filesystem_review_position_rejects_unsafe_run_id(tmp_path: Path) -> None:
    repository = FilesystemArtifactRepository(tmp_path / "job")

    with pytest.raises(ArtifactError, match="Translation run ID"):
        repository.read_review_position(DOCUMENT_ID, "../escape")


@pytest.mark.parametrize(
    ("run_id", "block_id"),
    [
        ("../escape", BLOCK_ID),
        (RUN_ID, "../escape"),
    ],
)
def test_filesystem_revision_paths_reject_unsafe_scope(
    tmp_path: Path,
    run_id: str,
    block_id: str,
) -> None:
    repository = FilesystemArtifactRepository(tmp_path / "job")

    with pytest.raises(ArtifactError):
        repository.list_block_revisions(DOCUMENT_ID, run_id, block_id)
