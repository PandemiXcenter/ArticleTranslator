from pathlib import Path

import pytest

from article_translator.adapters.storage.filesystem import FilesystemArtifactRepository
from article_translator.domain.editorial import BlockRevision
from article_translator.domain.errors import ArtifactError

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
