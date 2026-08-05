from typing import Protocol

from article_translator.domain.editorial import BlockRevision, ReviewPosition


class RevisionRepository(Protocol):
    """Append-only persistence boundary for run-scoped editorial revisions."""

    def list_block_revisions(
        self,
        document_id: str,
        translation_run_id: str,
        block_id: str,
    ) -> list[BlockRevision]: ...

    def append_block_revision(self, revision: BlockRevision) -> None: ...


class ReviewPositionRepository(Protocol):
    """Mutable review cursor kept separate from immutable machine output."""

    def read_review_position(
        self,
        document_id: str,
        translation_run_id: str,
    ) -> ReviewPosition | None: ...

    def write_review_position(self, position: ReviewPosition) -> None: ...


class EditorialRepository(RevisionRepository, ReviewPositionRepository, Protocol):
    """Combined persistence boundary required by the editorial application service."""
