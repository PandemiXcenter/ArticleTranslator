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
from article_translator.application.fingerprints import (
    page_input_fingerprint,
    table_input_fingerprint,
)
from article_translator.application.prompting import (
    PROMPT_VERSION,
    TABLE_PROMPT_VERSION,
    build_page_prompt,
    build_table_prompt,
)
from article_translator.domain.enums import BlockType, JobStatus, SegmentHandling
from article_translator.domain.errors import (
    ArtifactError,
    IncompleteDocumentError,
    PageTranslationError,
    StaleCheckpointError,
)
from article_translator.domain.models import (
    ArtifactRef,
    DocumentTranslation,
    GeneratedBlockVariant,
    GeneratedFootnoteBlock,
    GeneratedManualInsertionBlock,
    GeneratedTableMarkdown,
    JobManifest,
    MarkdownExportSettings,
    PageFailure,
    PageTranslation,
    PreparedPage,
    ProviderMetadata,
    TableReconstructionMetadata,
    TranslatedBlock,
    TranslationSettings,
    utc_now,
)
from article_translator.hashing import sha256_file
from article_translator.ports.artifacts import ArtifactRepository
from article_translator.ports.extraction import PageExtractor
from article_translator.ports.translation import (
    PageTranslationRequest,
    PageTranslator,
    ProviderDescriptor,
    TableReconstructionRequest,
)

RepositoryFactory = Callable[[Path], ArtifactRepository]


class TranslationPipeline:
    """Provider-neutral use cases shared by the CLI and local web interface."""

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
            existing_manifest = repository.read_manifest() if repository.has_manifest() else None

            if existing_manifest is not None and not force:
                if existing_manifest.source_file_sha256 != source_hash:
                    raise ArtifactError("Existing job manifest belongs to a different source file")
                if existing_manifest.image_dpi != image_dpi:
                    raise StaleCheckpointError(
                        "Extraction image DPI changed; rerun ingestion with --force"
                    )
                for page in existing_manifest.pages:
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
                translation_run_id=None,
                translation_run_ids=(
                    list(existing_manifest.translation_run_ids)
                    if existing_manifest is not None
                    else []
                ),
                created_at=existing_manifest.created_at if existing_manifest is not None else now,
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
        if force or manifest.translation_run_id is None:
            translation_run_id = _new_translation_run_id(manifest.translation_run_ids)
            translation_run_ids = [*manifest.translation_run_ids, translation_run_id]
        else:
            translation_run_id = manifest.translation_run_id
            translation_run_ids = manifest.translation_run_ids
        manifest = manifest.model_copy(
            update={
                "status": JobStatus.TRANSLATING,
                "translation_run_id": translation_run_id,
                "translation_run_ids": translation_run_ids,
                "translation_settings": settings,
                "provider_name": descriptor.provider,
                "provider_model": descriptor.model,
                "provider_configuration": dict(descriptor.configuration),
                "provider_semantic_configuration": dict(descriptor.semantic_configuration),
                "prompt_version": PROMPT_VERSION,
                "table_prompt_version": TABLE_PROMPT_VERSION,
                "updated_at": utc_now(),
            }
        )
        repository.write_manifest(manifest)

        translated_pages: list[PageTranslation] = []
        for page in manifest.pages:
            fingerprint: str | None = None
            failure_stage = "page_translation"
            try:
                markdown = repository.read_text(page.markdown)
                image_path = repository.resolve(page.image)
                prompt = build_page_prompt(
                    page_number=page.original_page_number,
                    markdown=markdown,
                    settings=settings,
                    previous_pages=translated_pages,
                )
                fingerprint = page_input_fingerprint(
                    page=page,
                    settings=settings,
                    provider=descriptor,
                    prompt=prompt,
                )

                translation: PageTranslation | None = None
                if repository.has_page_translation(
                    translation_run_id,
                    page.original_page_number,
                ):
                    checkpoint = repository.read_page_translation(
                        translation_run_id,
                        page.original_page_number,
                    )
                    if checkpoint.input_fingerprint != fingerprint:
                        raise StaleCheckpointError(
                            f"Page {page.original_page_number} has a checkpoint from different "
                            "inputs, config, prompt, provider, or model; rerun with --force"
                        )
                    translation = _refresh_page_evidence(checkpoint, page, markdown)
                    if not _pending_table_blocks(translation):
                        _validate_completed_table_checkpoint(
                            translation=translation,
                            page=page,
                            markdown=markdown,
                            settings=settings,
                            descriptor=descriptor,
                            previous_pages=translated_pages,
                        )
                        repository.write_page_translation(translation_run_id, translation)
                        repository.clear_page_failure(
                            translation_run_id,
                            page.original_page_number,
                        )
                        translated_pages.append(translation)
                        continue

                if translation is None:
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
                        _to_translated_block(page.original_page_number, block)
                        for block in result.payload.blocks
                    ]
                    translation = PageTranslation(
                        translation_run_id=translation_run_id,
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
                    # The first-pass result is a durable stage checkpoint. If the
                    # table call fails, resume starts here instead of paying for
                    # and potentially changing the page segmentation again.
                    repository.write_page_translation(translation_run_id, translation)

                table_blocks = _pending_table_blocks(translation)
                if table_blocks:
                    failure_stage = "table_reconstruction"
                    table_prompt = build_table_prompt(
                        page_translation=translation,
                        markdown=markdown,
                        settings=settings,
                        previous_pages=translated_pages,
                    )
                    fingerprint = table_input_fingerprint(
                        page=page,
                        settings=settings,
                        provider=descriptor,
                        prompt=table_prompt,
                        first_pass_fingerprint=translation.input_fingerprint,
                        table_blocks=table_blocks,
                    )
                    table_result = translator.reconstruct_tables(
                        TableReconstructionRequest(
                            original_page_number=page.original_page_number,
                            markdown=markdown,
                            image_path=image_path,
                            image_media_type=page.image.media_type,
                            prompt=table_prompt,
                            settings=settings,
                            expected_block_orders=tuple(block.order for block in table_blocks),
                        )
                    )
                    translation = _apply_table_reconstruction(
                        translation,
                        table_result.payload.tables,
                        table_fingerprint=fingerprint,
                        descriptor=descriptor,
                        response_id=table_result.response_id,
                        input_tokens=table_result.input_tokens,
                        output_tokens=table_result.output_tokens,
                    )
                repository.write_page_translation(translation_run_id, translation)
                repository.clear_page_failure(
                    translation_run_id,
                    page.original_page_number,
                )
                translated_pages.append(translation)
            except StaleCheckpointError:
                self._mark_failed(repository, manifest)
                raise
            except Exception as exc:
                safe_message = _safe_error_message(exc)
                repository.write_page_failure(
                    translation_run_id,
                    PageFailure(
                        original_page_number=page.original_page_number,
                        input_fingerprint=fingerprint,
                        stage=failure_stage,
                        error_type=type(exc).__name__,
                        message=safe_message,
                    ),
                )
                self._mark_failed(repository, manifest)
                raise PageTranslationError(page.original_page_number, safe_message) from exc

        document = DocumentTranslation(
            translation_run_id=translation_run_id,
            document_id=manifest.document_id,
            job_id=manifest.job_id,
            source_file_name=manifest.source_file_name,
            source_file_sha256=manifest.source_file_sha256,
            page_count=manifest.page_count,
            translation_settings=settings,
            pages=translated_pages,
            created_at=manifest.created_at,
        )
        repository.write_document(translation_run_id, document)
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
        if manifest.translation_run_id is None:
            raise IncompleteDocumentError("Job has no active translation run to compile")
        translation_run_id = manifest.translation_run_id
        document = repository.read_document(translation_run_id)
        self._validate_document_for_manifest(document, manifest)
        output = repository.write_markdown(
            translation_run_id,
            compile_markdown(document, settings),
        )
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
            manifest.translation_run_id is None
            or document.translation_run_id != manifest.translation_run_id
        ):
            raise IncompleteDocumentError(
                "Canonical document does not belong to the active translation run"
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
            table_metadata = page.table_reconstruction
            if table_metadata is not None and (
                manifest.table_prompt_version is None
                or table_metadata.provider.prompt_version != manifest.table_prompt_version
                or table_metadata.provider.provider != manifest.provider_name
                or table_metadata.provider.model != manifest.provider_model
                or table_metadata.provider.semantic_configuration
                != (manifest.provider_semantic_configuration or {})
            ):
                raise IncompleteDocumentError(
                    "Canonical table-pass provenance does not match the current job manifest"
                )


def _to_translated_block(
    original_page_number: int,
    block: GeneratedBlockVariant,
) -> TranslatedBlock:
    block_id = f"p{original_page_number:04d}-b{block.order:04d}"
    if isinstance(block, GeneratedManualInsertionBlock):
        return TranslatedBlock(
            block_id=block_id,
            original_page_number=original_page_number,
            order=block.order,
            type=block.type,
            source_text=None,
            translated_text=None,
            segment_handling=SegmentHandling.MANUAL_INSERTION,
            manual_insertion_reason=block.manual_insertion_reason,
            continuation=block.continuation,
            classification_review_required=block.classification_review_required,
        )
    if isinstance(block, GeneratedFootnoteBlock):
        return TranslatedBlock(
            block_id=block_id,
            original_page_number=original_page_number,
            order=block.order,
            type=block.type,
            source_text=block.source_text,
            translated_text=block.translated_text,
            segment_handling=SegmentHandling.TRANSLATE,
            footnote_marker=block.footnote_marker,
            continuation=block.continuation,
            uncertainties=block.uncertainties,
            classification_review_required=block.classification_review_required,
        )
    return TranslatedBlock(
        block_id=block_id,
        original_page_number=original_page_number,
        order=block.order,
        type=block.type,
        source_text=block.source_text,
        translated_text=block.translated_text,
        heading_level=block.heading_level,
        uncertainties=block.uncertainties,
        segment_handling=SegmentHandling.TRANSLATE,
        classification_review_required=block.classification_review_required,
    )


def _pending_table_blocks(translation: PageTranslation) -> list[TranslatedBlock]:
    return [
        block
        for block in translation.blocks
        if block.type is BlockType.TABLE
        and block.segment_handling is SegmentHandling.MANUAL_INSERTION
        and not block.legacy_manual_table
    ]


def _validate_completed_table_checkpoint(
    *,
    translation: PageTranslation,
    page: PreparedPage,
    markdown: str,
    settings: TranslationSettings,
    descriptor: ProviderDescriptor,
    previous_pages: list[PageTranslation],
) -> None:
    metadata = translation.table_reconstruction
    if metadata is None:
        return
    first_pass = _restore_first_pass_table_tags(translation)
    table_blocks = _pending_table_blocks(first_pass)
    table_prompt = build_table_prompt(
        page_translation=first_pass,
        markdown=markdown,
        settings=settings,
        previous_pages=previous_pages,
    )
    expected_fingerprint = table_input_fingerprint(
        page=page,
        settings=settings,
        provider=descriptor,
        prompt=table_prompt,
        first_pass_fingerprint=translation.input_fingerprint,
        table_blocks=table_blocks,
    )
    if metadata.input_fingerprint != expected_fingerprint:
        raise StaleCheckpointError(
            f"Page {page.original_page_number} has a table checkpoint from different "
            "inputs, context, targets, config, prompt, provider, or model; rerun with --force"
        )


def _restore_first_pass_table_tags(translation: PageTranslation) -> PageTranslation:
    blocks: list[TranslatedBlock] = []
    for block in translation.blocks:
        if block.segment_handling is not SegmentHandling.TABLE_RECONSTRUCTION:
            blocks.append(block)
            continue
        blocks.append(
            TranslatedBlock.model_validate(
                {
                    **block.model_dump(mode="python"),
                    "translated_text": None,
                    "uncertainties": [],
                    "segment_handling": SegmentHandling.MANUAL_INSERTION,
                }
            )
        )
    return PageTranslation.model_validate(
        {
            **translation.model_dump(mode="python"),
            "blocks": blocks,
            "table_reconstruction": None,
        }
    )


def _refresh_page_evidence(
    checkpoint: PageTranslation,
    page: PreparedPage,
    markdown: str,
) -> PageTranslation:
    return PageTranslation.model_validate(
        {
            **checkpoint.model_dump(mode="python"),
            "pdf_page_label": page.pdf_page_label,
            "extraction_status": page.extraction_status,
            "extracted_character_count": page.extracted_character_count,
            "extraction_warnings": page.extraction_warnings,
            "source_markdown": markdown,
            "source_markdown_artifact": page.markdown,
            "source_image": page.image,
        }
    )


def _apply_table_reconstruction(
    translation: PageTranslation,
    generated_tables: list[GeneratedTableMarkdown],
    *,
    table_fingerprint: str,
    descriptor: ProviderDescriptor,
    response_id: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
) -> PageTranslation:
    table_blocks = _pending_table_blocks(translation)
    expected_orders = [block.order for block in table_blocks]
    returned_orders = [table.order for table in generated_tables]
    if returned_orders != expected_orders:
        raise ValueError(
            "Table reconstruction must return exactly the requested block orders; "
            f"expected {expected_orders}, received {returned_orders}"
        )
    generated_by_order = {table.order: table for table in generated_tables}
    reconstructed_blocks: list[TranslatedBlock] = []
    reconstructed_ids: list[str] = []
    for block in translation.blocks:
        generated = generated_by_order.get(block.order)
        if generated is None:
            reconstructed_blocks.append(block)
            continue
        reconstructed_ids.append(block.block_id)
        reconstructed_blocks.append(
            TranslatedBlock.model_validate(
                {
                    **block.model_dump(mode="python"),
                    "source_text": None,
                    "translated_text": generated.translated_markdown,
                    "uncertainties": generated.uncertainties,
                    "segment_handling": SegmentHandling.TABLE_RECONSTRUCTION,
                }
            )
        )
    metadata = TableReconstructionMetadata(
        input_fingerprint=table_fingerprint,
        block_ids=reconstructed_ids,
        provider=ProviderMetadata(
            provider=descriptor.provider,
            model=descriptor.model,
            prompt_version=TABLE_PROMPT_VERSION,
            configuration=dict(descriptor.configuration),
            semantic_configuration=dict(descriptor.semantic_configuration),
            response_id=response_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
    )
    return PageTranslation.model_validate(
        {
            **translation.model_dump(mode="python"),
            "blocks": reconstructed_blocks,
            "table_reconstruction": metadata,
        }
    )


def _job_id(source_pdf: Path, source_hash: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", source_pdf.stem.lower()).strip("-")
    return f"{(slug or 'document')[:48]}-{source_hash[:12]}"


def _new_translation_run_id(existing_run_ids: list[str]) -> str:
    run_id = uuid4().hex
    while run_id in existing_run_ids:
        run_id = uuid4().hex
    return run_id


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
