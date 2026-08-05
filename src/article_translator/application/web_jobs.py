from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from threading import RLock
from uuid import uuid4

from pydantic import SecretStr

from article_translator.application.pipeline import TranslationPipeline
from article_translator.application.prompting import PROMPT_VERSION, TABLE_PROMPT_VERSION
from article_translator.config import ProjectConfig
from article_translator.domain.errors import ArticleTranslatorError, ArtifactError
from article_translator.domain.models import DocumentTranslation, JobManifest, TranslationSettings
from article_translator.ports.artifacts import ArtifactRepository
from article_translator.ports.translation import (
    PageTranslationRequest,
    PageTranslator,
    ProviderDescriptor,
    ProviderResult,
    TableReconstructionRequest,
    TableReconstructionResult,
)

PipelineFactory = Callable[[], TranslationPipeline]
RepositoryFactory = Callable[[Path], ArtifactRepository]
TranslatorFactory = Callable[
    [ProjectConfig, SecretStr | None],
    AbstractContextManager[PageTranslator],
]


class WebJobStatus(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    TRANSLATING = "translating"
    READY = "ready"
    FAILED = "failed"


class WebJobNotFoundError(ArticleTranslatorError):
    """An opaque browser job ID or stable completed-run ID was not found."""


class WebJobNotReadyError(ArticleTranslatorError):
    """Review data was requested before translation completed."""


@dataclass(frozen=True, slots=True)
class WebJobSnapshot:
    job_id: str
    status: WebJobStatus
    filename: str
    current_page: int
    total_pages: int | None
    error: str | None
    translation_run_id: str | None


@dataclass(frozen=True, slots=True)
class WebReviewSnapshot:
    """One completed immutable run available for review after process restart."""

    job_id: str
    status: WebJobStatus
    filename: str
    page_count: int
    continue_page: int
    translation_run_id: str
    updated_at: datetime


@dataclass(slots=True)
class _WebJobRecord:
    job_id: str
    filename: str
    upload_path: Path
    settings: TranslationSettings
    runtime_config: ProjectConfig
    api_key: SecretStr | None
    status: WebJobStatus = WebJobStatus.QUEUED
    current_page: int = 0
    total_pages: int | None = None
    error: str | None = None
    job_dir: Path | None = None
    translation_run_id: str | None = None
    lock: RLock = field(default_factory=RLock)

    def snapshot(self) -> WebJobSnapshot:
        with self.lock:
            return WebJobSnapshot(
                job_id=self.job_id,
                status=self.status,
                filename=self.filename,
                current_page=self.current_page,
                total_pages=self.total_pages,
                error=self.error,
                translation_run_id=self.translation_run_id,
            )


@dataclass(frozen=True, slots=True)
class _CompletedReviewRecord:
    job_dir: Path
    translation_run_id: str
    document_id: str
    filename: str
    page_count: int
    completed_at: datetime


class _ProgressTranslator:
    def __init__(
        self,
        inner: PageTranslator,
        page_started: Callable[[int], None],
    ) -> None:
        self._inner = inner
        self._page_started = page_started

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._inner.descriptor

    def translate_page(self, request: PageTranslationRequest) -> ProviderResult:
        self._page_started(request.original_page_number)
        return self._inner.translate_page(request)

    def reconstruct_tables(
        self,
        request: TableReconstructionRequest,
    ) -> TableReconstructionResult:
        return self._inner.reconstruct_tables(request)


_SAFE_JOB_DIRECTORY = re.compile(r"[a-z0-9][a-z0-9-]{0,59}[a-z0-9]")


class WebJobManager:
    """Bounded, process-local translation runner for the local editor."""

    def __init__(
        self,
        *,
        config: ProjectConfig,
        pipeline_factory: PipelineFactory,
        repository_factory: RepositoryFactory,
        translator_factory: TranslatorFactory,
    ) -> None:
        self._config = config
        self._pipeline_factory = pipeline_factory
        self._repository_factory = repository_factory
        self._translator_factory = translator_factory
        self._records: dict[str, _WebJobRecord] = {}
        self._completed_reviews: dict[str, _CompletedReviewRecord] = {}
        self._ambiguous_review_ids: set[str] = set()
        self._records_lock = RLock()
        self._discover_completed_reviews()
        self._executor = ThreadPoolExecutor(
            max_workers=config.web.max_concurrent_jobs,
            thread_name_prefix="article-translator",
        )

    def submit(
        self,
        *,
        upload_path: Path,
        display_filename: str,
        glossary: dict[str, str],
        runtime_config: ProjectConfig | None = None,
        api_key: SecretStr | None = None,
    ) -> WebJobSnapshot:
        resolved_config = runtime_config or self._config
        resolved_glossary = dict(resolved_config.translation.glossary)
        resolved_glossary.update(glossary)
        settings = TranslationSettings.model_validate(
            resolved_config.translation.model_copy(
                update={"glossary": resolved_glossary}
            ).model_dump(mode="python")
        )
        job_id = uuid4().hex
        record = _WebJobRecord(
            job_id=job_id,
            filename=display_filename,
            upload_path=upload_path,
            settings=settings,
            runtime_config=resolved_config,
            api_key=api_key,
        )
        with self._records_lock:
            self._records[job_id] = record
        try:
            self._executor.submit(self._run, record)
        except Exception:
            with self._records_lock:
                self._records.pop(job_id, None)
            with record.lock:
                record.api_key = None
            raise
        return record.snapshot()

    def get(self, job_id: str) -> WebJobSnapshot:
        _validate_web_identifier(job_id)
        with self._records_lock:
            record = self._records.get(job_id)
            completed = self._completed_reviews.get(job_id)
        if record is not None:
            return record.snapshot()
        if completed is not None:
            return WebJobSnapshot(
                job_id=completed.translation_run_id,
                status=WebJobStatus.READY,
                filename=completed.filename,
                current_page=completed.page_count,
                total_pages=completed.page_count,
                error=None,
                translation_run_id=completed.translation_run_id,
            )
        raise WebJobNotFoundError("Translation job was not found")

    def list_reviews(self) -> list[WebReviewSnapshot]:
        """List every validated completed run known under the artifact root."""

        with self._records_lock:
            completed = list(self._completed_reviews.values())
        snapshots = [self._review_snapshot(record) for record in completed]
        return sorted(
            snapshots,
            key=lambda snapshot: (snapshot.updated_at, snapshot.translation_run_id),
            reverse=True,
        )

    def ready_context(self, job_id: str) -> tuple[Path, str]:
        _validate_web_identifier(job_id)
        with self._records_lock:
            record = self._records.get(job_id)
            completed = self._completed_reviews.get(job_id)
        if record is not None:
            with record.lock:
                if (
                    record.status is not WebJobStatus.READY
                    or record.job_dir is None
                    or record.translation_run_id is None
                ):
                    raise WebJobNotReadyError("Translation is not ready for review")
                return record.job_dir, record.translation_run_id
        if completed is not None:
            return completed.job_dir, completed.translation_run_id
        raise WebJobNotFoundError("Translation job was not found")

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _run(self, record: _WebJobRecord) -> None:
        try:
            pipeline = self._pipeline_factory()
            self._update(record, status=WebJobStatus.PREPARING)
            job_dir = pipeline.prepare_document(
                record.upload_path,
                artifacts_dir=record.runtime_config.paths.artifacts_dir,
                image_dpi=record.runtime_config.extraction.image_dpi,
            )
            repository = self._repository_factory(job_dir)
            manifest = repository.read_manifest()
            if manifest.page_count > record.runtime_config.web.max_pdf_pages:
                raise ArtifactError(
                    f"PDF has {manifest.page_count} pages; configured maximum is "
                    f"{record.runtime_config.web.max_pdf_pages}"
                )
            self._update(
                record,
                status=WebJobStatus.TRANSLATING,
                total_pages=manifest.page_count,
                job_dir=job_dir,
            )

            with self._translator_factory(
                record.runtime_config,
                record.api_key,
            ) as translator:
                descriptor = translator.descriptor
                force_translation = manifest.translation_run_id is not None and (
                    manifest.translation_settings != record.settings
                    or manifest.provider_name != descriptor.provider
                    or manifest.provider_model != descriptor.model
                    or manifest.provider_semantic_configuration
                    != dict(descriptor.semantic_configuration)
                    or manifest.prompt_version != PROMPT_VERSION
                    or manifest.table_prompt_version != TABLE_PROMPT_VERSION
                )
                progress = _ProgressTranslator(
                    translator,
                    lambda page: self._update(record, current_page=page),
                )
                document = pipeline.translate_document(
                    job_dir,
                    settings=record.settings,
                    translator=progress,
                    force=force_translation,
                )
            pipeline.compile_document(
                job_dir,
                settings=record.runtime_config.export,
            )
            self._register_completed_document(job_dir, document)
            self._update(
                record,
                status=WebJobStatus.READY,
                current_page=document.page_count,
                total_pages=document.page_count,
                translation_run_id=document.translation_run_id,
            )
        except Exception as exc:
            self._update(
                record,
                status=WebJobStatus.FAILED,
                error=_public_error_message(exc),
            )
        finally:
            with record.lock:
                record.api_key = None
            _remove_staged_upload(record.upload_path, self._config.paths.artifacts_dir)

    def _discover_completed_reviews(self) -> None:
        artifacts_root = self._config.paths.artifacts_dir.resolve()
        if not artifacts_root.is_dir():
            return
        try:
            candidates = sorted(artifacts_root.iterdir(), key=lambda path: path.name)
        except OSError:
            return
        for candidate in candidates:
            job_dir = _safe_direct_job_directory(artifacts_root, candidate)
            if job_dir is None:
                continue
            try:
                repository = self._repository_factory(job_dir)
                manifest = repository.read_manifest()
                if manifest.job_id != candidate.name:
                    continue
            except (ArticleTranslatorError, OSError, ValueError):
                continue
            for translation_run_id in manifest.translation_run_ids:
                try:
                    document = repository.read_document(translation_run_id)
                    _validate_completed_document(document, manifest, translation_run_id)
                except (ArticleTranslatorError, OSError, ValueError):
                    continue
                self._register_completed_document(job_dir, document)

    def _register_completed_document(
        self,
        job_dir: Path,
        document: DocumentTranslation,
    ) -> None:
        if job_dir.is_symlink():
            raise ArtifactError("Completed review directory cannot be a symbolic link")
        resolved_job_dir = job_dir.resolve()
        artifacts_root = self._config.paths.artifacts_dir.resolve()
        if (
            resolved_job_dir.parent != artifacts_root
            or _SAFE_JOB_DIRECTORY.fullmatch(resolved_job_dir.name) is None
        ):
            raise ArtifactError("Completed review directory is outside the artifact root")
        translation_run_id = document.translation_run_id
        completed_at = max(page.translated_at for page in document.pages)
        completed = _CompletedReviewRecord(
            job_dir=resolved_job_dir,
            translation_run_id=translation_run_id,
            document_id=document.document_id,
            filename=document.source_file_name,
            page_count=document.page_count,
            completed_at=completed_at,
        )
        with self._records_lock:
            if translation_run_id in self._ambiguous_review_ids:
                return
            existing = self._completed_reviews.get(translation_run_id)
            if existing is not None and existing.job_dir != resolved_job_dir:
                self._completed_reviews.pop(translation_run_id, None)
                self._ambiguous_review_ids.add(translation_run_id)
                return
            self._completed_reviews[translation_run_id] = completed

    def _review_snapshot(self, record: _CompletedReviewRecord) -> WebReviewSnapshot:
        continue_page = 1
        updated_at = record.completed_at
        try:
            repository = self._repository_factory(record.job_dir)
            position = repository.read_review_position(
                record.document_id,
                record.translation_run_id,
            )
            if position is not None and position.original_page_number <= record.page_count:
                continue_page = position.original_page_number
                updated_at = max(updated_at, position.updated_at)
        except (ArticleTranslatorError, OSError, ValueError):
            pass
        return WebReviewSnapshot(
            job_id=record.translation_run_id,
            status=WebJobStatus.READY,
            filename=record.filename,
            page_count=record.page_count,
            continue_page=continue_page,
            translation_run_id=record.translation_run_id,
            updated_at=updated_at,
        )

    @staticmethod
    def _update(record: _WebJobRecord, **changes: object) -> None:
        with record.lock:
            for name, value in changes.items():
                setattr(record, name, value)


def _remove_staged_upload(upload_path: Path, artifacts_dir: Path) -> None:
    uploads_root = (artifacts_dir / ".uploads").resolve()
    staging_directory = upload_path.parent.resolve()
    if (
        staging_directory.parent == uploads_root
        and staging_directory.is_dir()
        and upload_path.resolve().is_relative_to(staging_directory)
    ):
        shutil.rmtree(staging_directory)


def _validate_web_identifier(job_id: str) -> None:
    if len(job_id) != 32 or any(character not in "0123456789abcdef" for character in job_id):
        raise WebJobNotFoundError("Translation job was not found")


def _safe_direct_job_directory(artifacts_root: Path, candidate: Path) -> Path | None:
    if (
        candidate.name.startswith(".")
        or _SAFE_JOB_DIRECTORY.fullmatch(candidate.name) is None
        or candidate.is_symlink()
    ):
        return None
    try:
        if not candidate.is_dir():
            return None
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    return resolved if resolved.parent == artifacts_root else None


def _validate_completed_document(
    document: DocumentTranslation,
    manifest: JobManifest,
    translation_run_id: str,
) -> None:
    document_identity = (
        document.job_id,
        document.document_id,
        document.source_file_name,
        document.source_file_sha256,
        document.page_count,
    )
    manifest_identity = (
        manifest.job_id,
        manifest.document_id,
        manifest.source_file_name,
        manifest.source_file_sha256,
        manifest.page_count,
    )
    if document_identity != manifest_identity:
        raise ArtifactError("Completed document identity does not match its manifest")
    if (
        document.translation_run_id != translation_run_id
        or translation_run_id not in manifest.translation_run_ids
    ):
        raise ArtifactError("Completed document run is not indexed by its manifest")


def _public_error_message(exc: Exception) -> str:
    if isinstance(exc, ArticleTranslatorError):
        return " ".join(str(exc).split())[:1_000]
    if isinstance(exc, ValueError):
        return "Translation settings or provider configuration were rejected"
    return f"{type(exc).__name__}: translation failed; private details were not exposed"
