from pathlib import Path
from typing import Protocol

from article_translator.domain.editorial import BlockRevision, ReviewPosition
from article_translator.domain.models import (
    ArtifactRef,
    DocumentTranslation,
    JobManifest,
    PageFailure,
    PageTranslation,
)


class ArtifactRepository(Protocol):
    """Persistence boundary for a single job directory."""

    @property
    def root(self) -> Path: ...

    def has_manifest(self) -> bool: ...

    def read_manifest(self) -> JobManifest: ...

    def write_manifest(self, manifest: JobManifest) -> None: ...

    def resolve(self, reference: ArtifactRef) -> Path: ...

    def read_text(self, reference: ArtifactRef) -> str: ...

    def has_page_translation(self, translation_run_id: str, page_number: int) -> bool: ...

    def read_page_translation(
        self,
        translation_run_id: str,
        page_number: int,
    ) -> PageTranslation: ...

    def write_page_translation(
        self,
        translation_run_id: str,
        translation: PageTranslation,
    ) -> None: ...

    def write_page_failure(self, translation_run_id: str, failure: PageFailure) -> None: ...

    def has_page_failure(self, translation_run_id: str, page_number: int) -> bool: ...

    def read_page_failure(
        self,
        translation_run_id: str,
        page_number: int,
    ) -> PageFailure: ...

    def clear_page_failure(self, translation_run_id: str, page_number: int) -> None: ...

    def write_document(
        self,
        translation_run_id: str,
        document: DocumentTranslation,
    ) -> Path: ...

    def read_document(self, translation_run_id: str) -> DocumentTranslation: ...

    def write_markdown(self, translation_run_id: str, markdown: str) -> Path: ...

    def list_block_revisions(
        self,
        document_id: str,
        translation_run_id: str,
        block_id: str,
    ) -> list[BlockRevision]: ...

    def append_block_revision(self, revision: BlockRevision) -> None: ...

    def read_review_position(
        self,
        document_id: str,
        translation_run_id: str,
    ) -> ReviewPosition | None: ...

    def write_review_position(self, position: ReviewPosition) -> None: ...
