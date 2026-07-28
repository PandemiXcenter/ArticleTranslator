from typing import Protocol

from article_translator.domain.editorial import BlockRevision


class RevisionRepository(Protocol):
    """Append-only persistence boundary for run-scoped editorial revisions."""

    def list_block_revisions(
        self,
        document_id: str,
        translation_run_id: str,
        block_id: str,
    ) -> list[BlockRevision]: ...

    def append_block_revision(self, revision: BlockRevision) -> None: ...
