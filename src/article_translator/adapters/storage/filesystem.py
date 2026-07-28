from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import BaseModel

from article_translator.domain.errors import ArtifactError
from article_translator.domain.models import (
    ArtifactRef,
    DocumentTranslation,
    JobManifest,
    PageFailure,
    PageTranslation,
)
from article_translator.hashing import sha256_file


class FilesystemArtifactRepository:
    """Human-inspectable JSON artifacts with atomic checkpoint writes."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def has_manifest(self) -> bool:
        return self._manifest_path.is_file()

    def read_manifest(self) -> JobManifest:
        return JobManifest.model_validate_json(self._read(self._manifest_path))

    def write_manifest(self, manifest: JobManifest) -> None:
        self._write_model(self._manifest_path, manifest)

    def resolve(self, reference: ArtifactRef) -> Path:
        candidate = (self._root / reference.path).resolve()
        if not candidate.is_relative_to(self._root):
            raise ArtifactError(f"Artifact path escapes job directory: {reference.path}")
        if not candidate.is_file():
            raise ArtifactError(f"Artifact is missing: {reference.path}")
        if sha256_file(candidate) != reference.sha256:
            raise ArtifactError(f"Artifact hash mismatch: {reference.path}")
        return candidate

    def read_text(self, reference: ArtifactRef) -> str:
        return self.resolve(reference).read_text(encoding="utf-8")

    def has_page_translation(self, page_number: int) -> bool:
        return self._translation_path(page_number).is_file()

    def read_page_translation(self, page_number: int) -> PageTranslation:
        path = self._translation_path(page_number)
        return PageTranslation.model_validate_json(self._read(path))

    def write_page_translation(self, translation: PageTranslation) -> None:
        self._write_model(
            self._translation_path(translation.original_page_number),
            translation,
        )

    def write_page_failure(self, failure: PageFailure) -> None:
        self._write_model(self._failure_path(failure.original_page_number), failure)

    def has_page_failure(self, page_number: int) -> bool:
        return self._failure_path(page_number).is_file()

    def read_page_failure(self, page_number: int) -> PageFailure:
        path = self._failure_path(page_number)
        return PageFailure.model_validate_json(self._read(path))

    def clear_page_failure(self, page_number: int) -> None:
        self._failure_path(page_number).unlink(missing_ok=True)

    def write_document(self, document: DocumentTranslation) -> Path:
        self._write_model(self._document_path, document)
        return self._document_path

    def read_document(self) -> DocumentTranslation:
        return DocumentTranslation.model_validate_json(self._read(self._document_path))

    def write_markdown(self, markdown: str) -> Path:
        self._atomic_write(self._markdown_path, markdown)
        return self._markdown_path

    @property
    def _manifest_path(self) -> Path:
        return self._root / "manifest.json"

    @property
    def _document_path(self) -> Path:
        return self._root / "output" / "document.json"

    @property
    def _markdown_path(self) -> Path:
        return self._root / "output" / "document.md"

    def _translation_path(self, page_number: int) -> Path:
        return self._root / "pages" / f"{page_number:04d}" / "translation.json"

    def _failure_path(self, page_number: int) -> Path:
        return self._root / "pages" / f"{page_number:04d}" / "failure.json"

    @staticmethod
    def _read(path: Path) -> str:
        if not path.is_file():
            raise ArtifactError(f"Artifact is missing: {path}")
        return path.read_text(encoding="utf-8")

    def _write_model(self, path: Path, model: BaseModel) -> None:
        self._atomic_write(path, model.model_dump_json(indent=2) + "\n")

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
