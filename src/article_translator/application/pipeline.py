from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from uuid import uuid4

from pydantic import ValidationError

from article_translator.application.compile_markdown import compile_markdown
from article_translator.application.fingerprints import page_input_fingerprint
from article_translator.application.prompting import PROMPT_VERSION, build_page_prompt
from article_translator.domain.enums import JobStatus
from article_translator.domain.errors import (
    ArtifactError,
    IncompleteDocumentError,
    PageTranslationError,
    StaleCheckpointError,
)
from article_translator.domain.models import (
    ArtifactRef,
    DocumentTranslation,
    JobManifest,
    MarkdownExportSettings,
    PageFailure,
    PageTranslation,
    PreparedPage,
    ProviderMetadata,
    TranslatedBlock,
    TranslationSettings,
    utc_now,
)
from article_translator.hashing import sha256_file
from article_translator.ports.artifacts import ArtifactRepository
from article_translator.ports.extraction import PageExtractor
from article_translator.ports.translation import PageTranslationRequest, PageTranslator

RepositoryFactory = Callable[[Path], ArtifactRepository]


class TranslationPipeline:
    """Use cases shared by the CLI now and a future UI/API."""

    def __init__(
        self,
        *,
        extractor: PageExtractor,
        repository_factory: RepositoryFactory,
    ) -> None:
        self._extractor = extractor
        self._repository_factory = repository_factory

    def prepare_document(
        self,
        source_pdf: Path,
        *,
        artifacts_dir: Path,
        image_dpi: int,
        force: bool = False,
    ) -> Path:
        source_pdf = source_pdf.resolve()
        if not source_pdf.is_file():
            raise ArtifactError(f"Source PDF does not exist: {source_pdf}")
        artifacts_dir = artifacts_dir.resolve()
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(
            dir=artifacts_dir,
            prefix=".source-snapshot-",
        ) as snapshot_directory:
            source_snapshot = Path(snapshot_directory) / "source.pdf"
            shutil.copyfile(source_pdf, source_snapshot)
            source_hash = sha256_file(source_snapshot)
            job_id = _job_id(source_pdf, source_hash)
            job_dir = (artifacts_dir / job_id).resolve()
            repository = self._repository_factory(job_dir)

            if repository.has_manifest() and not force:
                existing = repository.read_manifest()
                if existing.source_file_sha256 != source_hash:
                    raise ArtifactError("Existing job manifest belongs to a different source file")
                if existing.image_dpi != image_dpi:
                    raise StaleCheckpointError(
                        "Extraction image DPI changed; rerun ingestion with --force"
                    )
                for page in existing.pages:
                    repository.resolve(page.markdown)
                    repository.resolve(page.image)
                return job_dir

            preparation_id, pages = self._prepare_pages_transactionally(
                source_snapshot,
                job_dir,
                image_dpi=image_dpi,
            )
            now = utc_now()
            manifest = JobManifest(
                job_id=job_id,
                preparation_id=preparation_id,
                document_id=source_hash,
                source_file_name=source_pdf.name,
                source_file_sha256=source_hash,
                image_dpi=image_dpi,
                page_count=len(pages),
                pages=pages,
                status=JobStatus.PREPARED,
                created_at=now,
                updated_at=now,
            )
            repository.write_manifest(manifest)
            return job_dir

    def translate_document(
        self,
        job_dir: Path,
        *,
        settings: TranslationSettings,
        translator: PageTranslator,
        force: bool = False,
    ) -> DocumentTranslation:
        repository = self._repository_factory(job_dir)
        manifest = repository.read_manifest()
        descriptor = translator.descriptor
        manifest = manifest.model_copy(
            update={
                "status": JobStatus.TRANSLATING,
                "translation_settings": settings,
                "provider_name": descriptor.provider,
                "provider_model": descriptor.model,
                "provider_configuration": dict(descriptor.configuration),
                "provider_semantic_configuration": dict(descriptor.semantic_configuration),
                "prompt_version": PROMPT_VERSION,
                "updated_at": utc_now(),
            }
        )
        repository.write_manifest(manifest)

        translated_pages: list[PageTranslation] = []
        for page in manifest.pages:
            fingerprint: str | None = None
            try:
                markdown = repository.read_text(page.markdown)
                image_path = repository.resolve(page.image)
                prompt = build_page_prompt(
                    page_number=page.original_page_number,
                    markdown=markdown,
                    settings=settings,
                )
                fingerprint = page_input_fingerprint(
                    page=page,
                    settings=settings,
                    provider=descriptor,
                    prompt=prompt,
                )

                if repository.has_page_translation(page.original_page_number) and not force:
                    checkpoint = repository.read_page_translation(page.original_page_number)
                    retriable_failure = (
                        repository.has_page_failure(page.original_page_number)
                        and repository.read_page_failure(
                            page.original_page_number
                        ).input_fingerprint
                        == fingerprint
                    )
                    if checkpoint.input_fingerprint == fingerprint and not retriable_failure:
                        checkpoint = checkpoint.model_copy(
                            update={
                                "pdf_page_label": page.pdf_page_label,
                                "extraction_status": page.extraction_status,
                                "extracted_character_count": (page.extracted_character_count),
                                "extraction_warnings": page.extraction_warnings,
                                "source_markdown": markdown,
                                "source_markdown_artifact": page.markdown,
                                "source_image": page.image,
                            }
                        )
                        repository.write_page_translation(checkpoint)
                        repository.clear_page_failure(page.original_page_number)
                        translated_pages.append(checkpoint)
                        continue
                    if checkpoint.input_fingerprint != fingerprint and not retriable_failure:
                        raise StaleCheckpointError(
                            f"Page {page.original_page_number} has a checkpoint from different "
                            "inputs, config, prompt, provider, or model; rerun with --force"
                        )

                result = translator.translate_page(
                    PageTranslationRequest(
                        original_page_number=page.original_page_number,
                        markdown=markdown,
                        image_path=image_path,
                        image_media_type=page.image.media_type,
                        prompt=prompt,
                        settings=settings,
                    )
                )
                blocks = [
                    TranslatedBlock(
                        block_id=(f"p{page.original_page_number:04d}-b{block.order:04d}"),
                        original_page_number=page.original_page_number,
                        **block.model_dump(),
                    )
                    for block in result.payload.blocks
                ]
                translation = PageTranslation(
                    original_page_number=page.original_page_number,
                    pdf_page_label=page.pdf_page_label,
                    detected_printed_page_label=result.payload.detected_printed_page_label,
                    extraction_status=page.extraction_status,
                    extracted_character_count=page.extracted_character_count,
                    extraction_warnings=page.extraction_warnings,
                    source_markdown=markdown,
                    source_markdown_artifact=page.markdown,
                    source_image=page.image,
                    blocks=blocks,
                    input_fingerprint=fingerprint,
                    provider=ProviderMetadata(
                        provider=descriptor.provider,
                        model=descriptor.model,
                        prompt_version=PROMPT_VERSION,
                        configuration=dict(descriptor.configuration),
                        semantic_configuration=dict(descriptor.semantic_configuration),
                        response_id=result.response_id,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                    ),
                )
                repository.write_page_translation(translation)
                repository.clear_page_failure(page.original_page_number)
                translated_pages.append(translation)
            except StaleCheckpointError:
                self._mark_failed(repository, manifest)
                raise
            except Exception as exc:
                safe_message = _safe_error_message(exc)
                repository.write_page_failure(
                    PageFailure(
                        original_page_number=page.original_page_number,
                        input_fingerprint=fingerprint,
                        error_type=type(exc).__name__,
                        message=safe_message,
                    )
                )
                self._mark_failed(repository, manifest)
                raise PageTranslationError(page.original_page_number, safe_message) from exc

        document = DocumentTranslation(
            document_id=manifest.document_id,
            job_id=manifest.job_id,
            source_file_name=manifest.source_file_name,
            source_file_sha256=manifest.source_file_sha256,
            page_count=manifest.page_count,
            translation_settings=settings,
            pages=translated_pages,
            created_at=manifest.created_at,
        )
        repository.write_document(document)
        manifest = manifest.model_copy(
            update={"status": JobStatus.TRANSLATED, "updated_at": utc_now()}
        )
        repository.write_manifest(manifest)
        return document

    def compile_document(
        self,
        job_dir: Path,
        *,
        settings: MarkdownExportSettings,
    ) -> Path:
        repository = self._repository_factory(job_dir)
        manifest = repository.read_manifest()
        if manifest.status not in {JobStatus.TRANSLATED, JobStatus.COMPILED}:
            raise IncompleteDocumentError(
                f"Job status is {manifest.status.value!r}; complete translation before compiling"
            )
        document = repository.read_document()
        self._validate_document_for_manifest(document, manifest)
        output = repository.write_markdown(compile_markdown(document, settings))
        manifest = manifest.model_copy(
            update={
                "status": JobStatus.COMPILED,
                "export_settings": settings,
                "updated_at": utc_now(),
            }
        )
        repository.write_manifest(manifest)
        return output

    @staticmethod
    def _mark_failed(repository: ArtifactRepository, manifest: JobManifest) -> None:
        repository.write_manifest(
            manifest.model_copy(update={"status": JobStatus.FAILED, "updated_at": utc_now()})
        )

    def _prepare_pages_transactionally(
        self,
        source_snapshot: Path,
        job_dir: Path,
        *,
        image_dpi: int,
    ) -> tuple[str, list[PreparedPage]]:
        prepared_root = job_dir / "prepared"
        prepared_root.mkdir(parents=True, exist_ok=True)
        staging_root = Path(mkdtemp(dir=prepared_root, prefix=".staging-"))
        try:
            pages = self._extractor.extract_pages(
                source_snapshot,
                staging_root,
                image_dpi=image_dpi,
            )
            preparation_id = uuid4().hex
            preparation_root = prepared_root / preparation_id
            os.replace(staging_root, preparation_root)
            prefix = preparation_root.relative_to(job_dir)
            return preparation_id, [
                page.model_copy(
                    update={
                        "markdown": _prefix_artifact(page.markdown, prefix),
                        "image": _prefix_artifact(page.image, prefix),
                    }
                )
                for page in pages
            ]
        finally:
            if staging_root.is_dir() and staging_root.parent == prepared_root:
                shutil.rmtree(staging_root)

    @staticmethod
    def _validate_document_for_manifest(
        document: DocumentTranslation,
        manifest: JobManifest,
    ) -> None:
        document_identity = (
            document.document_id,
            document.job_id,
            document.source_file_sha256,
            document.page_count,
        )
        manifest_identity = (
            manifest.document_id,
            manifest.job_id,
            manifest.source_file_sha256,
            manifest.page_count,
        )
        if document_identity != manifest_identity:
            raise IncompleteDocumentError(
                "Canonical document identity does not match the current job manifest"
            )
        if (
            manifest.translation_settings is None
            or document.translation_settings != manifest.translation_settings
        ):
            raise IncompleteDocumentError(
                "Canonical document settings do not match the current job manifest"
            )
        for page in document.pages:
            provider = page.provider
            if (
                provider.provider != manifest.provider_name
                or provider.model != manifest.provider_model
                or provider.semantic_configuration
                != (manifest.provider_semantic_configuration or {})
                or provider.prompt_version != manifest.prompt_version
            ):
                raise IncompleteDocumentError(
                    "Canonical page provenance does not match the current job manifest"
                )


def _job_id(source_pdf: Path, source_hash: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", source_pdf.stem.lower()).strip("-")
    return f"{(slug or 'document')[:48]}-{source_hash[:12]}"


def _prefix_artifact(reference: ArtifactRef, prefix: Path) -> ArtifactRef:
    return reference.model_copy(update={"path": (prefix / reference.path).as_posix()})


def _safe_error_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        details = []
        for error in exc.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )[:5]:
            location = ".".join(str(part) for part in error["loc"])
            details.append(f"{location}: {error['msg']}")
        return f"Structured output validation failed: {'; '.join(details)}"[:1_000]
    if not isinstance(
        exc,
        (ArtifactError, ConnectionError, OSError, TimeoutError, ValueError),
    ):
        return f"{type(exc).__name__}: operation failed; raw provider details were not persisted"
    message = " ".join(str(exc).split())
    return (message or type(exc).__name__)[:1_000]
