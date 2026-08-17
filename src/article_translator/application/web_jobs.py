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

from article_translator.application.editorial import EditorialService
from article_translator.application.pipeline import TranslationPipeline
from article_translator.application.prompting import PROMPT_VERSION, TABLE_PROMPT_VERSION
from article_translator.config import ProjectConfig
from article_translator.domain.enums import JobStatus
from article_translator.domain.errors import ArticleTranslatorError, ArtifactError
from article_translator.domain.models import (
    DocumentTranslation,
    JobManifest,
    TranslationSettings,
    utc_now,
)
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
    CANCELLED = "cancelled"


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
    """One completed translation run available in the Articles catalog."""

    job_id: str
    status: WebJobStatus
    filename: str
    page_count: int
    continue_page: int
    accepted_blocks: int
    total_blocks: int
    review_complete: bool
    translation_run_id: str
    updated_at: datetime


@dataclass(slots=True)
class _WebJobRecord:
    job_id: str
    filename: str
    upload_path: Path | None
    settings: TranslationSettings
    runtime_config: ProjectConfig
    api_key: SecretStr | None
    auto_continue: bool
    auto_continue_attempts: int
    automatic_continuations_remaining: int = 0
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
    document: DocumentTranslation
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
    """Bounded local runner with durable discovery of stopped translation runs."""

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
        self._discover_failed_jobs()
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
        auto_continue: bool | None = None,
    ) -> WebJobSnapshot:
        resolved_config = runtime_config or self._config
        resolved_auto_continue = (
            resolved_config.web.auto_continue_default if auto_continue is None else auto_continue
        )
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
            auto_continue=resolved_auto_continue,
            auto_continue_attempts=resolved_config.web.auto_continue_attempts,
            automatic_continuations_remaining=(
                resolved_config.web.auto_continue_attempts if resolved_auto_continue else 0
            ),
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

    def list_recoverable_jobs(self) -> list[WebJobSnapshot]:
        """List failed or dismissed active runs whose page checkpoints remain on disk."""

        with self._records_lock:
            records = list(self._records.values())
        snapshots = [record.snapshot() for record in records]
        return sorted(
            (
                snapshot
                for snapshot in snapshots
                if snapshot.status in {WebJobStatus.FAILED, WebJobStatus.CANCELLED}
                and snapshot.translation_run_id is not None
            ),
            key=lambda snapshot: snapshot.translation_run_id or "",
        )

    def continue_job(
        self,
        job_id: str,
        *,
        api_key: SecretStr | None = None,
    ) -> WebJobSnapshot:
        """Resume the same failed run; successful page checkpoints are validated and reused."""

        _validate_web_identifier(job_id)
        with self._records_lock:
            record = self._records.get(job_id)
        if record is None:
            raise WebJobNotFoundError("Translation job was not found")
        with record.lock:
            if record.status not in {WebJobStatus.FAILED, WebJobStatus.CANCELLED}:
                raise WebJobNotReadyError("Only a stopped translation can be continued")
            if record.job_dir is None or record.translation_run_id is None:
                raise WebJobNotReadyError("The stopped translation has no resumable run")
            previous_status = record.status
            previous_error = record.error
            record.status = WebJobStatus.QUEUED
            record.error = None
            record.api_key = api_key
            record.automatic_continuations_remaining = (
                record.auto_continue_attempts if record.auto_continue else 0
            )
        try:
            self._executor.submit(self._resume, record)
        except Exception:
            with record.lock:
                record.status = previous_status
                record.error = previous_error
                record.api_key = None
            raise
        return record.snapshot()

    def cancel_job(self, job_id: str) -> WebJobSnapshot:
        """Dismiss a stopped attempt without deleting its run or page checkpoints."""

        _validate_web_identifier(job_id)
        with self._records_lock:
            record = self._records.get(job_id)
        if record is None:
            raise WebJobNotFoundError("Translation job was not found")
        with record.lock:
            if record.status is WebJobStatus.CANCELLED:
                return record.snapshot()
            if record.status is not WebJobStatus.FAILED:
                raise WebJobNotReadyError("Only a failed translation can be cancelled")
            record.status = WebJobStatus.CANCELLED
            record.error = "Translation paused. Completed page checkpoints were kept."
            record.api_key = None
            return record.snapshot()

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

    def delete_review(self, translation_run_id: str) -> None:
        """Delete one completed article run and remove it from the durable run index."""

        _validate_web_identifier(translation_run_id)
        with self._records_lock:
            completed = self._completed_reviews.get(translation_run_id)
        if completed is None:
            raise WebJobNotFoundError("Translation job was not found")

        repository = self._repository_factory(completed.job_dir)
        manifest = repository.read_manifest()
        _validate_completed_document(
            completed.document,
            manifest,
            translation_run_id,
        )
        remaining_run_ids = [
            run_id for run_id in manifest.translation_run_ids if run_id != translation_run_id
        ]
        changes: dict[str, object] = {
            "translation_run_ids": remaining_run_ids,
            "updated_at": utc_now(),
        }
        if manifest.translation_run_id == translation_run_id:
            changes.update(
                {
                    "status": JobStatus.PREPARED,
                    "translation_run_id": None,
                    "translation_settings": None,
                    "provider_name": None,
                    "provider_model": None,
                    "provider_configuration": None,
                    "provider_semantic_configuration": None,
                    "prompt_version": None,
                    "table_prompt_version": None,
                    "export_settings": None,
                    "auto_continue": False,
                    "auto_continue_attempts": 1,
                }
            )
        updated_manifest = manifest.model_copy(update=changes)
        repository.write_manifest(updated_manifest)
        try:
            repository.delete_translation_run(translation_run_id)
        except Exception:
            repository.write_manifest(manifest)
            raise

        with self._records_lock:
            self._completed_reviews.pop(translation_run_id, None)
            stale_aliases = [
                job_id
                for job_id, record in self._records.items()
                if record.translation_run_id == translation_run_id
            ]
            for job_id in stale_aliases:
                self._records.pop(job_id, None)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _run(self, record: _WebJobRecord) -> None:
        try:
            if record.upload_path is None:
                raise ArtifactError("A new translation requires a staged PDF upload")
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
            manifest = manifest.model_copy(
                update={
                    "auto_continue": record.auto_continue,
                    "auto_continue_attempts": record.auto_continue_attempts,
                    "updated_at": utc_now(),
                }
            )
            repository.write_manifest(manifest)
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
            self._capture_failed_run(record)
            if self._continue_automatically(record):
                return
            self._update(
                record,
                status=WebJobStatus.FAILED,
                error=_public_error_message(exc),
            )
        finally:
            with record.lock:
                record.api_key = None
            if record.upload_path is not None:
                _remove_staged_upload(record.upload_path, self._config.paths.artifacts_dir)

    def _resume(self, record: _WebJobRecord) -> None:
        try:
            if record.job_dir is None or record.translation_run_id is None:
                raise ArtifactError("The stopped translation has no resumable run")
            pipeline = self._pipeline_factory()
            repository = self._repository_factory(record.job_dir)
            manifest = repository.read_manifest()
            self._update(record, status=WebJobStatus.TRANSLATING)
            with self._translator_factory(record.runtime_config, record.api_key) as translator:
                _validate_resume_inputs(
                    manifest,
                    record.translation_run_id,
                    record.settings,
                    translator.descriptor,
                )
                progress = _ProgressTranslator(
                    translator,
                    lambda page: self._update(record, current_page=page),
                )
                document = pipeline.translate_document(
                    record.job_dir,
                    settings=record.settings,
                    translator=progress,
                    force=False,
                )
            pipeline.compile_document(
                record.job_dir,
                settings=record.runtime_config.export,
            )
            self._register_completed_document(record.job_dir, document)
            self._update(
                record,
                status=WebJobStatus.READY,
                current_page=document.page_count,
                total_pages=document.page_count,
                error=None,
                translation_run_id=document.translation_run_id,
            )
        except Exception as exc:
            self._capture_failed_run(record)
            if self._continue_automatically(record):
                return
            self._update(
                record,
                status=WebJobStatus.FAILED,
                error=_public_error_message(exc),
            )
        finally:
            with record.lock:
                record.api_key = None

    def _continue_automatically(self, record: _WebJobRecord) -> bool:
        """Retry a failed page inline without deadlocking the bounded executor."""

        with record.lock:
            if (
                not record.auto_continue
                or record.automatic_continuations_remaining <= 0
                or record.job_dir is None
                or record.translation_run_id is None
            ):
                return False
            job_dir = record.job_dir
        try:
            manifest = self._repository_factory(job_dir).read_manifest()
        except (ArticleTranslatorError, OSError, ValueError):
            return False
        if (
            manifest.status is not JobStatus.FAILED
            or manifest.translation_run_id != record.translation_run_id
        ):
            return False
        with record.lock:
            if record.automatic_continuations_remaining <= 0:
                return False
            record.automatic_continuations_remaining -= 1
            record.status = WebJobStatus.QUEUED
            record.error = None
        self._resume(record)
        return True

    def _capture_failed_run(self, record: _WebJobRecord) -> None:
        if record.job_dir is None:
            return
        try:
            manifest = self._repository_factory(record.job_dir).read_manifest()
        except (ArticleTranslatorError, OSError, ValueError):
            return
        if manifest.translation_run_id is not None:
            self._update(
                record,
                translation_run_id=manifest.translation_run_id,
                total_pages=manifest.page_count,
            )

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

    def _discover_failed_jobs(self) -> None:
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
                translation_run_id = manifest.translation_run_id
                if (
                    manifest.status is not JobStatus.FAILED
                    or translation_run_id is None
                    or manifest.translation_settings is None
                    or manifest.provider_name != "gemini"
                    or manifest.provider_model is None
                ):
                    continue
                failure = next(
                    (
                        repository.read_page_failure(translation_run_id, page_number)
                        for page_number in range(1, manifest.page_count + 1)
                        if repository.has_page_failure(translation_run_id, page_number)
                    ),
                    None,
                )
                current_page = failure.original_page_number if failure is not None else 1
                error = (
                    f"Page {current_page}: {failure.message}"
                    if failure is not None
                    else "Translation stopped before the document was complete."
                )
                runtime_config = _runtime_config_for_failed_manifest(self._config, manifest)
            except (ArticleTranslatorError, OSError, ValueError):
                continue
            with self._records_lock:
                self._records.setdefault(
                    translation_run_id,
                    _WebJobRecord(
                        job_id=translation_run_id,
                        filename=manifest.source_file_name,
                        upload_path=None,
                        settings=manifest.translation_settings,
                        runtime_config=runtime_config,
                        api_key=None,
                        auto_continue=manifest.auto_continue,
                        auto_continue_attempts=manifest.auto_continue_attempts,
                        status=WebJobStatus.FAILED,
                        current_page=current_page,
                        total_pages=manifest.page_count,
                        error=error,
                        job_dir=job_dir,
                        translation_run_id=translation_run_id,
                    ),
                )

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
            document=document,
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
        accepted_blocks = 0
        total_blocks = 0
        review_complete = False
        updated_at = record.completed_at
        try:
            repository = self._repository_factory(record.job_dir)
            review = EditorialService(repository).review_document(
                record.document,
                record.translation_run_id,
            )
            blocks = [block for page in review.pages for block in page.blocks]
            total_blocks = len(blocks)
            accepted_blocks = sum(block.review_status.value == "accepted" for block in blocks)
            review_complete = accepted_blocks == total_blocks
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
            accepted_blocks=accepted_blocks,
            total_blocks=total_blocks,
            review_complete=review_complete,
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


def _runtime_config_for_failed_manifest(
    base_config: ProjectConfig,
    manifest: JobManifest,
) -> ProjectConfig:
    """Restore output choices while retaining current operational limits."""

    if manifest.translation_settings is None or manifest.provider_model is None:
        raise ArtifactError("Failed translation manifest is missing resume settings")
    translation = base_config.translation.model_copy(
        update=manifest.translation_settings.model_dump(mode="python")
    )
    extraction = base_config.extraction.model_copy(update={"image_dpi": manifest.image_dpi})
    gemini = base_config.provider.gemini.model_copy(update={"model": manifest.provider_model})
    provider = base_config.provider.model_copy(update={"gemini": gemini})
    return base_config.model_copy(
        update={
            "translation": translation,
            "extraction": extraction,
            "provider": provider,
        }
    )


def _validate_resume_inputs(
    manifest: JobManifest,
    translation_run_id: str,
    settings: TranslationSettings,
    descriptor: ProviderDescriptor,
) -> None:
    """Refuse to mutate a stopped run when output semantics no longer match."""

    if manifest.status is not JobStatus.FAILED:
        raise WebJobNotReadyError("The translation run is not stopped at a failed page")
    if manifest.translation_run_id != translation_run_id:
        raise ArtifactError("The stopped translation is no longer the active run")
    if manifest.translation_settings != settings:
        raise ArtifactError("Resume settings no longer match the stopped translation")
    if (
        manifest.provider_name != descriptor.provider
        or manifest.provider_model != descriptor.model
        or (manifest.provider_semantic_configuration or {})
        != dict(descriptor.semantic_configuration)
        or manifest.prompt_version != PROMPT_VERSION
        or manifest.table_prompt_version != TABLE_PROMPT_VERSION
    ):
        raise ArtifactError(
            "Provider or prompt semantics changed since this translation stopped; "
            "the existing run was left unchanged"
        )


def _public_error_message(exc: Exception) -> str:
    if isinstance(exc, ArticleTranslatorError):
        return " ".join(str(exc).split())[:1_000]
    if isinstance(exc, ValueError):
        return "Translation settings or provider configuration were rejected"
    return f"{type(exc).__name__}: translation failed; private details were not exposed"
