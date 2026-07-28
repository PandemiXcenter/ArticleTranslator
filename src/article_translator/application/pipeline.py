from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from article_translator.application.compile_markdown import compile_markdown
from article_translator.application.fingerprints import page_input_fingerprint
from article_translator.application.prompting import PROMPT_VERSION, build_page_prompt
from article_translator.domain.enums import JobStatus
from article_translator.domain.errors import (
    ArtifactError,
    PageTranslationError,
    StaleCheckpointError,
)
from article_translator.domain.models import (
    DocumentTranslation,
    JobManifest,
    MarkdownExportSettings,
    PageFailure,
    PageTranslation,
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
        source_hash = sha256_file(source_pdf)
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

        pages = self._extractor.extract_pages(
            source_pdf,
            job_dir,
            image_dpi=image_dpi,
        )
        now = utc_now()
        manifest = JobManifest(
            job_id=job_id,
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
                "prompt_version": PROMPT_VERSION,
                "updated_at": utc_now(),
            }
        )
        repository.write_manifest(manifest)

        translated_pages: list[PageTranslation] = []
        for page in manifest.pages:
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
                    if checkpoint.input_fingerprint != fingerprint:
                        raise StaleCheckpointError(
                            f"Page {page.original_page_number} has a checkpoint from different "
                            "inputs, config, prompt, provider, or model; rerun with --force"
                        )
                    translated_pages.append(checkpoint)
                    continue

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
                    source_markdown=markdown,
                    source_image=page.image,
                    blocks=blocks,
                    input_fingerprint=fingerprint,
                    provider=ProviderMetadata(
                        provider=descriptor.provider,
                        model=descriptor.model,
                        prompt_version=PROMPT_VERSION,
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
                repository.write_page_failure(
                    PageFailure(
                        original_page_number=page.original_page_number,
                        error_type=type(exc).__name__,
                        message=_safe_error_message(exc),
                    )
                )
                self._mark_failed(repository, manifest)
                raise PageTranslationError(page.original_page_number, str(exc)) from exc

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
        document = repository.read_document()
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


def _job_id(source_pdf: Path, source_hash: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", source_pdf.stem.lower()).strip("-")
    return f"{(slug or 'document')[:48]}-{source_hash[:12]}"


def _safe_error_message(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    return (message or type(exc).__name__)[:1_000]
