from pathlib import Path

import pytest

from article_translator.adapters.storage import FilesystemArtifactRepository
from article_translator.application.pipeline import TranslationPipeline
from article_translator.domain.enums import BlockType, ExtractionStatus
from article_translator.domain.errors import (
    IncompleteDocumentError,
    PageTranslationError,
    StaleCheckpointError,
)
from article_translator.domain.models import (
    ArtifactRef,
    GeneratedBlock,
    GeneratedPagePayload,
    MarkdownExportSettings,
    PreparedPage,
    TranslationSettings,
)
from article_translator.hashing import sha256_file
from article_translator.ports.translation import (
    PageTranslationRequest,
    ProviderDescriptor,
    ProviderResult,
)


class FakeExtractor:
    def extract_pages(
        self,
        source_pdf: Path,
        artifact_root: Path,
        *,
        image_dpi: int,
    ) -> list[PreparedPage]:
        del source_pdf, image_dpi
        pages: list[PreparedPage] = []
        for number in (1, 2):
            page_dir = artifact_root / "pages" / f"{number:04d}"
            page_dir.mkdir(parents=True, exist_ok=True)
            markdown_path = page_dir / "source.md"
            image_path = page_dir / "page.png"
            markdown_path.write_text(f"Source page {number}", encoding="utf-8")
            image_path.write_bytes(f"image-{number}".encode())
            pages.append(
                PreparedPage(
                    original_page_number=number,
                    markdown=_ref(artifact_root, markdown_path, "text/markdown"),
                    image=_ref(artifact_root, image_path, "image/png"),
                    extraction_status=ExtractionStatus.EXTRACTED,
                    extracted_character_count=len(f"Source page {number}"),
                )
            )
        return pages


class FakeTranslator:
    def __init__(
        self,
        *,
        fail_once_on: int | None = None,
        configuration: dict[str, str | int | float | bool] | None = None,
        semantic_configuration: dict[str, str | int | float | bool] | None = None,
    ) -> None:
        self.calls: list[int] = []
        self.fail_once_on = fail_once_on
        self.configuration = configuration or {}
        self.semantic_configuration = semantic_configuration or {}

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider="fake",
            model="fake-v1",
            configuration=self.configuration,
            semantic_configuration=self.semantic_configuration,
        )

    def translate_page(self, request: PageTranslationRequest) -> ProviderResult:
        self.calls.append(request.original_page_number)
        if self.fail_once_on == request.original_page_number:
            self.fail_once_on = None
            raise TimeoutError("temporary provider timeout")
        return ProviderResult(
            payload=GeneratedPagePayload(
                blocks=[
                    GeneratedBlock(
                        order=1,
                        type=BlockType.BODY,
                        source_text=request.markdown,
                        translated_text=f"Translated page {request.original_page_number}",
                    )
                ]
            ),
            response_id=f"fake-{request.original_page_number}",
        )


class FailingExtractor:
    def extract_pages(
        self,
        source_pdf: Path,
        artifact_root: Path,
        *,
        image_dpi: int,
    ) -> list[PreparedPage]:
        del source_pdf, image_dpi
        partial = artifact_root / "pages" / "0001"
        partial.mkdir(parents=True)
        (partial / "source.md").write_text("partial replacement", encoding="utf-8")
        raise RuntimeError("synthetic preparation failure")


class InvalidOutputTranslator:
    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(provider="invalid", model="invalid-v1")

    def translate_page(self, request: PageTranslationRequest) -> ProviderResult:
        GeneratedPagePayload.model_validate(
            {
                "blocks": [],
                "raw_page_content": request.markdown,
            }
        )
        raise AssertionError("invalid payload should have raised")


def _ref(root: Path, path: Path, media_type: str) -> ArtifactRef:
    return ArtifactRef(
        path=path.relative_to(root).as_posix(),
        sha256=sha256_file(path),
        media_type=media_type,
        byte_count=path.stat().st_size,
    )


def _pipeline() -> TranslationPipeline:
    return TranslationPipeline(
        extractor=FakeExtractor(),
        repository_factory=lambda root: FilesystemArtifactRepository(root),
    )


def _prepared_job(tmp_path: Path) -> tuple[TranslationPipeline, Path]:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-fake")
    pipeline = _pipeline()
    job_dir = pipeline.prepare_document(
        source,
        artifacts_dir=tmp_path / "artifacts",
        image_dpi=150,
    )
    return pipeline, job_dir


def test_full_pipeline_is_resumable_and_config_invalidates_cache(tmp_path: Path) -> None:
    pipeline, job_dir = _prepared_job(tmp_path)
    settings = TranslationSettings(source_language="Danish", target_language="English")
    translator = FakeTranslator()

    document = pipeline.translate_document(
        job_dir,
        settings=settings,
        translator=translator,
    )
    markdown_path = pipeline.compile_document(
        job_dir,
        settings=MarkdownExportSettings(),
    )

    assert translator.calls == [1, 2]
    assert [page.original_page_number for page in document.pages] == [1, 2]
    assert all(page.translation_run_id == document.translation_run_id for page in document.pages)
    assert document.pages[0].extraction_status is ExtractionStatus.EXTRACTED
    assert document.pages[0].source_markdown_artifact.path.startswith("prepared/")
    assert markdown_path == (
        job_dir / "runs" / document.translation_run_id / "output" / "document.md"
    )
    assert markdown_path.read_text(encoding="utf-8").endswith("Translated page 2\n")

    pipeline.translate_document(job_dir, settings=settings, translator=translator)
    assert translator.calls == [1, 2]

    changed_settings = settings.model_copy(update={"target_language": "French"})
    with pytest.raises(StaleCheckpointError):
        pipeline.translate_document(
            job_dir,
            settings=changed_settings,
            translator=translator,
        )
    with pytest.raises(IncompleteDocumentError, match="complete translation"):
        pipeline.compile_document(
            job_dir,
            settings=MarkdownExportSettings(),
        )

    pipeline.translate_document(
        job_dir,
        settings=changed_settings,
        translator=translator,
        force=True,
    )
    assert translator.calls == [1, 2, 1, 2]


def test_resume_after_failure_does_not_repeat_completed_page(tmp_path: Path) -> None:
    pipeline, job_dir = _prepared_job(tmp_path)
    settings = TranslationSettings()
    failing = FakeTranslator(fail_once_on=2)

    with pytest.raises(PageTranslationError, match="Page 2"):
        pipeline.translate_document(job_dir, settings=settings, translator=failing)
    assert failing.calls == [1, 2]
    failed_manifest = FilesystemArtifactRepository(job_dir).read_manifest()
    failed_run_id = failed_manifest.translation_run_id
    assert failed_run_id is not None
    assert (job_dir / "runs" / failed_run_id / "pages" / "0002" / "failure.json").is_file()

    resumed = FakeTranslator()
    document = pipeline.translate_document(
        job_dir,
        settings=settings,
        translator=resumed,
    )

    assert resumed.calls == [2]
    assert document.translation_run_id == failed_run_id
    assert FilesystemArtifactRepository(job_dir).read_manifest().translation_run_id == failed_run_id
    assert not (job_dir / "runs" / failed_run_id / "pages" / "0002" / "failure.json").exists()


def test_forced_translations_create_coexisting_immutable_runs(tmp_path: Path) -> None:
    pipeline, job_dir = _prepared_job(tmp_path)
    settings = TranslationSettings()
    translator = FakeTranslator()
    repository = FilesystemArtifactRepository(job_dir)

    first = pipeline.translate_document(
        job_dir,
        settings=settings,
        translator=translator,
        force=True,
    )
    first_markdown = pipeline.compile_document(
        job_dir,
        settings=MarkdownExportSettings(),
    )
    first_document_path = job_dir / "runs" / first.translation_run_id / "output" / "document.json"
    first_page_path = (
        job_dir / "runs" / first.translation_run_id / "pages" / "0001" / "translation.json"
    )
    first_document_bytes = first_document_path.read_bytes()
    first_page_bytes = first_page_path.read_bytes()
    first_markdown_bytes = first_markdown.read_bytes()

    second = pipeline.translate_document(
        job_dir,
        settings=settings,
        translator=translator,
        force=True,
    )
    second_markdown = pipeline.compile_document(
        job_dir,
        settings=MarkdownExportSettings(),
    )

    assert translator.calls == [1, 2, 1, 2]
    assert second.translation_run_id != first.translation_run_id
    assert repository.read_manifest().translation_run_ids == [
        first.translation_run_id,
        second.translation_run_id,
    ]
    assert repository.read_document(first.translation_run_id) == first
    assert repository.read_document(second.translation_run_id) == second
    assert first_document_path.read_bytes() == first_document_bytes
    assert first_page_path.read_bytes() == first_page_bytes
    assert first_markdown.read_bytes() == first_markdown_bytes
    assert second_markdown == (
        job_dir / "runs" / second.translation_run_id / "output" / "document.md"
    )


def test_operational_provider_change_resumes_but_semantic_change_invalidates(
    tmp_path: Path,
) -> None:
    pipeline, job_dir = _prepared_job(tmp_path)
    settings = TranslationSettings()
    original = FakeTranslator(
        configuration={"request_attempts": 3},
        semantic_configuration={"api_version": "v1"},
    )
    pipeline.translate_document(job_dir, settings=settings, translator=original)

    operational_change = FakeTranslator(
        configuration={"request_attempts": 5},
        semantic_configuration={"api_version": "v1"},
    )
    pipeline.translate_document(
        job_dir,
        settings=settings,
        translator=operational_change,
    )
    assert operational_change.calls == []

    semantic_change = FakeTranslator(
        configuration={"request_attempts": 5},
        semantic_configuration={"api_version": "v2"},
    )
    with pytest.raises(StaleCheckpointError):
        pipeline.translate_document(
            job_dir,
            settings=settings,
            translator=semantic_change,
        )


def test_forced_preparation_preserves_run_index_and_starts_a_new_active_run(
    tmp_path: Path,
) -> None:
    pipeline, job_dir = _prepared_job(tmp_path)
    source = tmp_path / "source.pdf"
    settings = TranslationSettings()
    translator = FakeTranslator()
    original = pipeline.translate_document(
        job_dir,
        settings=settings,
        translator=translator,
    )
    original_run_id = original.translation_run_id
    original_source_path = original.pages[0].source_image.path
    original_page_bytes = (
        job_dir / "runs" / original_run_id / "pages" / "0001" / "translation.json"
    ).read_bytes()

    pipeline.prepare_document(
        source,
        artifacts_dir=tmp_path / "artifacts",
        image_dpi=150,
        force=True,
    )
    prepared_manifest = FilesystemArtifactRepository(job_dir).read_manifest()
    assert prepared_manifest.translation_run_id is None
    assert prepared_manifest.translation_run_ids == [original_run_id]

    rebound = pipeline.translate_document(
        job_dir,
        settings=settings,
        translator=translator,
    )

    assert translator.calls == [1, 2, 1, 2]
    assert rebound.translation_run_id != original_run_id
    assert rebound.pages[0].source_image.path != original_source_path
    assert FilesystemArtifactRepository(job_dir).resolve(rebound.pages[0].source_image).is_file()
    assert (
        job_dir / "runs" / original_run_id / "pages" / "0001" / "translation.json"
    ).read_bytes() == original_page_bytes
    assert FilesystemArtifactRepository(job_dir).read_manifest().translation_run_ids == [
        original_run_id,
        rebound.translation_run_id,
    ]


def test_failed_forced_preparation_does_not_corrupt_current_manifest(
    tmp_path: Path,
) -> None:
    _, job_dir = _prepared_job(tmp_path)
    source = tmp_path / "source.pdf"
    repository = FilesystemArtifactRepository(job_dir)
    original_manifest = repository.read_manifest()
    original_markdown = repository.read_text(original_manifest.pages[0].markdown)
    failing_pipeline = TranslationPipeline(
        extractor=FailingExtractor(),
        repository_factory=lambda root: FilesystemArtifactRepository(root),
    )

    with pytest.raises(RuntimeError, match="synthetic preparation failure"):
        failing_pipeline.prepare_document(
            source,
            artifacts_dir=tmp_path / "artifacts",
            image_dpi=200,
            force=True,
        )

    current_manifest = repository.read_manifest()
    assert current_manifest == original_manifest
    assert repository.read_text(current_manifest.pages[0].markdown) == original_markdown
    assert not list((job_dir / "prepared").glob(".staging-*"))


def test_provider_validation_failure_does_not_persist_page_content(
    tmp_path: Path,
) -> None:
    pipeline, job_dir = _prepared_job(tmp_path)

    with pytest.raises(PageTranslationError, match="Structured output validation failed"):
        pipeline.translate_document(
            job_dir,
            settings=TranslationSettings(),
            translator=InvalidOutputTranslator(),
        )

    repository = FilesystemArtifactRepository(job_dir)
    translation_run_id = repository.read_manifest().translation_run_id
    assert translation_run_id is not None
    failure = repository.read_page_failure(translation_run_id, 1)
    assert "Source page 1" not in failure.message


def test_changed_config_failure_can_resume_without_repeating_new_pages(
    tmp_path: Path,
) -> None:
    pipeline, job_dir = _prepared_job(tmp_path)
    original_settings = TranslationSettings(target_language="English")
    pipeline.translate_document(
        job_dir,
        settings=original_settings,
        translator=FakeTranslator(),
    )

    changed_settings = TranslationSettings(target_language="French")
    failing = FakeTranslator(fail_once_on=2)
    with pytest.raises(PageTranslationError, match="Page 2"):
        pipeline.translate_document(
            job_dir,
            settings=changed_settings,
            translator=failing,
            force=True,
        )
    assert failing.calls == [1, 2]
    failed_run_id = FilesystemArtifactRepository(job_dir).read_manifest().translation_run_id
    assert failed_run_id is not None

    resumed = FakeTranslator()
    resumed_document = pipeline.translate_document(
        job_dir,
        settings=changed_settings,
        translator=resumed,
    )

    assert resumed.calls == [2]
    assert resumed_document.translation_run_id == failed_run_id


def test_same_config_forced_failure_retries_the_failed_page(
    tmp_path: Path,
) -> None:
    pipeline, job_dir = _prepared_job(tmp_path)
    settings = TranslationSettings()
    pipeline.translate_document(
        job_dir,
        settings=settings,
        translator=FakeTranslator(),
    )

    failing = FakeTranslator(fail_once_on=2)
    with pytest.raises(PageTranslationError, match="Page 2"):
        pipeline.translate_document(
            job_dir,
            settings=settings,
            translator=failing,
            force=True,
        )
    assert failing.calls == [1, 2]
    failed_run_id = FilesystemArtifactRepository(job_dir).read_manifest().translation_run_id
    assert failed_run_id is not None

    resumed = FakeTranslator()
    resumed_document = pipeline.translate_document(
        job_dir,
        settings=settings,
        translator=resumed,
    )

    assert resumed.calls == [2]
    assert resumed_document.translation_run_id == failed_run_id
