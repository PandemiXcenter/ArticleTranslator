from __future__ import annotations

import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from article_translator.adapters.secrets import DotenvSecretStore
from article_translator.adapters.storage import FilesystemArtifactRepository
from article_translator.application.editorial import EditorialService
from article_translator.application.pipeline import TranslationPipeline
from article_translator.application.web_jobs import (
    WebJobManager,
    WebJobNotFoundError,
    WebJobStatus,
)
from article_translator.config import ProjectConfig, load_project_config
from article_translator.domain.editorial import ReviewPosition
from article_translator.domain.enums import (
    BlockType,
    ExtractionStatus,
    ReviewStatus,
    SegmentContinuation,
)
from article_translator.domain.models import (
    ArtifactRef,
    DocumentTranslation,
    GeneratedBlock,
    GeneratedPagePayload,
    PreparedPage,
)
from article_translator.hashing import sha256_file
from article_translator.interfaces.web import create_app
from article_translator.ports.translation import (
    PageTranslationRequest,
    PageTranslator,
    ProviderDescriptor,
    ProviderResult,
    TableReconstructionRequest,
    TableReconstructionResult,
)


class OnePageExtractor:
    def extract_pages(
        self,
        source_pdf: Path,
        artifact_root: Path,
        *,
        image_dpi: int,
    ) -> list[PreparedPage]:
        del source_pdf, image_dpi
        page_dir = artifact_root / "pages" / "0001"
        page_dir.mkdir(parents=True)
        markdown_path = page_dir / "source.md"
        image_path = page_dir / "page.png"
        markdown_path.write_text("Kildetekst", encoding="utf-8")
        image_path.write_bytes(b"image")
        return [
            PreparedPage(
                original_page_number=1,
                markdown=_reference(
                    artifact_root,
                    markdown_path,
                    "text/markdown",
                ),
                image=_reference(artifact_root, image_path, "image/png"),
                extraction_status=ExtractionStatus.EXTRACTED,
                extracted_character_count=10,
            )
        ]


class RecordingDpiExtractor:
    def __init__(self) -> None:
        self.image_dpis: list[int] = []

    def extract_pages(
        self,
        source_pdf: Path,
        artifact_root: Path,
        *,
        image_dpi: int,
    ) -> list[PreparedPage]:
        del source_pdf
        self.image_dpis.append(image_dpi)
        page_dir = artifact_root / "pages" / "0001"
        page_dir.mkdir(parents=True)
        markdown_path = page_dir / "source.md"
        image_path = page_dir / "page.png"
        markdown_path.write_text("Kildetekst", encoding="utf-8")
        image_path.write_bytes(f"image-dpi-{image_dpi}".encode())
        return [
            PreparedPage(
                original_page_number=1,
                markdown=_reference(artifact_root, markdown_path, "text/markdown"),
                image=_reference(artifact_root, image_path, "image/png"),
                extraction_status=ExtractionStatus.EXTRACTED,
                extracted_character_count=10,
            )
        ]


class TwoPageExtractor:
    def extract_pages(
        self,
        source_pdf: Path,
        artifact_root: Path,
        *,
        image_dpi: int,
    ) -> list[PreparedPage]:
        del source_pdf, image_dpi
        pages: list[PreparedPage] = []
        for page_number in (1, 2):
            page_dir = artifact_root / "pages" / f"{page_number:04d}"
            page_dir.mkdir(parents=True)
            markdown_path = page_dir / "source.md"
            image_path = page_dir / "page.png"
            markdown = f"Kildetekst {page_number}"
            markdown_path.write_text(markdown, encoding="utf-8")
            image_path.write_bytes(f"image-{page_number}".encode())
            pages.append(
                PreparedPage(
                    original_page_number=page_number,
                    markdown=_reference(
                        artifact_root,
                        markdown_path,
                        "text/markdown",
                    ),
                    image=_reference(artifact_root, image_path, "image/png"),
                    extraction_status=ExtractionStatus.EXTRACTED,
                    extracted_character_count=len(markdown),
                )
            )
        return pages


class RecordingTranslator:
    def __init__(self) -> None:
        self.settings_glossaries: list[dict[str, str]] = []
        self.settings_languages: list[tuple[str, str]] = []
        self.model = "fake-web-v1"

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(provider="fake", model=self.model)

    def translate_page(self, request: PageTranslationRequest) -> ProviderResult:
        self.settings_glossaries.append(dict(request.settings.glossary))
        self.settings_languages.append(
            (
                request.settings.source_language,
                request.settings.target_language,
            )
        )
        return ProviderResult(
            payload=GeneratedPagePayload(
                blocks=[
                    GeneratedBlock(
                        order=1,
                        type=BlockType.BODY,
                        source_text=request.markdown,
                        translated_text="Source text",
                        paragraph_continuation=SegmentContinuation.COMPLETE,
                    )
                ]
            )
        )

    def reconstruct_tables(
        self,
        request: TableReconstructionRequest,
    ) -> TableReconstructionResult:
        raise AssertionError(
            f"Unexpected table reconstruction on page {request.original_page_number}"
        )


class StopOnceTranslator(RecordingTranslator):
    def __init__(self, *, fail_once_on: int) -> None:
        super().__init__()
        self.fail_once_on = fail_once_on
        self.calls: list[int] = []
        self.api_version = "v1beta"

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider="gemini",
            model=self.model,
            semantic_configuration={"api_version": self.api_version},
        )

    def translate_page(self, request: PageTranslationRequest) -> ProviderResult:
        self.calls.append(request.original_page_number)
        if self.fail_once_on == request.original_page_number:
            self.fail_once_on = 0
            raise RuntimeError("synthetic stopped page")
        return super().translate_page(request)


class AlwaysStopTranslator(StopOnceTranslator):
    def translate_page(self, request: PageTranslationRequest) -> ProviderResult:
        self.calls.append(request.original_page_number)
        if request.original_page_number == self.fail_once_on:
            raise RuntimeError("synthetic persistent stopped page")
        return RecordingTranslator.translate_page(self, request)


def _reference(root: Path, path: Path, media_type: str) -> ArtifactRef:
    return ArtifactRef(
        path=path.relative_to(root).as_posix(),
        sha256=sha256_file(path),
        media_type=media_type,
        byte_count=path.stat().st_size,
    )


def _stage_pdf(artifacts_dir: Path, staging_id: str) -> Path:
    staging = artifacts_dir / ".uploads" / staging_id
    staging.mkdir(parents=True)
    path = staging / "article.pdf"
    path.write_bytes(b"%PDF-fake")
    return path


def _wait_for_status(
    manager: WebJobManager,
    job_id: str,
    expected: WebJobStatus,
) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if manager.get(job_id).status is expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"Job {job_id} did not reach {expected}")


def test_failed_web_job_survives_restart_and_continues_same_run(
    tmp_path: Path,
) -> None:
    config = load_project_config(Path("config/default.toml"))
    config = config.model_copy(
        update={"paths": config.paths.model_copy(update={"artifacts_dir": tmp_path / "artifacts"})}
    )
    translator = StopOnceTranslator(fail_once_on=2)

    @contextmanager
    def translator_context(
        runtime_config: ProjectConfig,
        api_key: SecretStr | None,
    ) -> Iterator[PageTranslator]:
        assert api_key is None
        translator.model = runtime_config.provider.gemini.model
        translator.api_version = runtime_config.provider.gemini.api_version
        yield translator

    def pipeline_factory() -> TranslationPipeline:
        return TranslationPipeline(
            extractor=TwoPageExtractor(),
            repository_factory=FilesystemArtifactRepository,
        )

    manager = WebJobManager(
        config=config,
        pipeline_factory=pipeline_factory,
        repository_factory=FilesystemArtifactRepository,
        translator_factory=translator_context,
    )
    submitted = manager.submit(
        upload_path=_stage_pdf(config.paths.artifacts_dir, "stopped"),
        display_filename="article.pdf",
        glossary={},
    )
    _wait_for_status(manager, submitted.job_id, WebJobStatus.FAILED)
    failed = manager.get(submitted.job_id)
    assert failed.current_page == 2
    assert failed.total_pages == 2
    assert failed.translation_run_id is not None
    run_id = failed.translation_run_id
    manager.shutdown()

    job_dirs = [
        path
        for path in config.paths.artifacts_dir.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    ]
    assert len(job_dirs) == 1
    checkpoint = job_dirs[0] / "runs" / run_id / "pages" / "0001" / "translation.json"
    assert checkpoint.is_file()
    assert translator.calls == [1, 2]

    restarted = WebJobManager(
        config=config,
        pipeline_factory=pipeline_factory,
        repository_factory=FilesystemArtifactRepository,
        translator_factory=translator_context,
    )
    recoverable = restarted.list_recoverable_jobs()
    assert [snapshot.job_id for snapshot in recoverable] == [run_id]
    assert recoverable[0].current_page == 2
    cancelled = restarted.cancel_job(run_id)
    assert cancelled.status is WebJobStatus.CANCELLED
    assert checkpoint.is_file()

    queued = restarted.continue_job(run_id)
    assert queued.status is WebJobStatus.QUEUED
    restarted.shutdown()
    ready = restarted.get(run_id)
    assert ready.status is WebJobStatus.READY
    assert ready.translation_run_id == run_id
    assert translator.calls == [1, 2, 2]
    assert not (job_dirs[0] / "runs" / run_id / "pages" / "0002" / "failure.json").exists()


def test_auto_continue_retries_once_and_persists_policy(tmp_path: Path) -> None:
    config = load_project_config(Path("config/default.toml"))
    config = config.model_copy(
        update={"paths": config.paths.model_copy(update={"artifacts_dir": tmp_path / "artifacts"})}
    )
    translator = StopOnceTranslator(fail_once_on=2)

    @contextmanager
    def translator_context(
        runtime_config: ProjectConfig,
        api_key: SecretStr | None,
    ) -> Iterator[PageTranslator]:
        assert api_key is None
        translator.model = runtime_config.provider.gemini.model
        translator.api_version = runtime_config.provider.gemini.api_version
        yield translator

    manager = WebJobManager(
        config=config,
        pipeline_factory=lambda: TranslationPipeline(
            extractor=TwoPageExtractor(),
            repository_factory=FilesystemArtifactRepository,
        ),
        repository_factory=FilesystemArtifactRepository,
        translator_factory=translator_context,
    )
    submitted = manager.submit(
        upload_path=_stage_pdf(config.paths.artifacts_dir, "auto-continue"),
        display_filename="article.pdf",
        glossary={},
        auto_continue=True,
    )
    manager.shutdown()

    ready = manager.get(submitted.job_id)
    assert ready.status is WebJobStatus.READY
    assert translator.calls == [1, 2, 2]
    job_dir, run_id = manager.ready_context(submitted.job_id)
    manifest = FilesystemArtifactRepository(job_dir).read_manifest()
    assert manifest.translation_run_id == run_id
    assert manifest.auto_continue is True
    assert manifest.auto_continue_attempts == 1


def test_auto_continue_stops_after_configured_attempts(tmp_path: Path) -> None:
    config = load_project_config(Path("config/default.toml"))
    config = config.model_copy(
        update={"paths": config.paths.model_copy(update={"artifacts_dir": tmp_path / "artifacts"})}
    )
    translator = AlwaysStopTranslator(fail_once_on=2)

    @contextmanager
    def translator_context(
        runtime_config: ProjectConfig,
        api_key: SecretStr | None,
    ) -> Iterator[PageTranslator]:
        assert api_key is None
        translator.model = runtime_config.provider.gemini.model
        translator.api_version = runtime_config.provider.gemini.api_version
        yield translator

    manager = WebJobManager(
        config=config,
        pipeline_factory=lambda: TranslationPipeline(
            extractor=TwoPageExtractor(),
            repository_factory=FilesystemArtifactRepository,
        ),
        repository_factory=FilesystemArtifactRepository,
        translator_factory=translator_context,
    )
    submitted = manager.submit(
        upload_path=_stage_pdf(config.paths.artifacts_dir, "bounded-auto-continue"),
        display_filename="article.pdf",
        glossary={},
        auto_continue=True,
    )
    manager.shutdown()

    failed = manager.get(submitted.job_id)
    assert failed.status is WebJobStatus.FAILED
    assert failed.current_page == 2
    assert translator.calls == [1, 2, 2]


def test_web_jobs_run_in_order_and_preserve_distinct_glossary_runs(
    tmp_path: Path,
) -> None:
    config = load_project_config(Path("config/default.toml"))
    config = config.model_copy(
        update={"paths": config.paths.model_copy(update={"artifacts_dir": tmp_path / "artifacts"})}
    )
    translator = RecordingTranslator()

    @contextmanager
    def translator_context(
        runtime_config: ProjectConfig,
        api_key: SecretStr | None,
    ) -> Iterator[PageTranslator]:
        assert api_key is None
        translator.model = runtime_config.provider.gemini.model
        yield translator

    def pipeline_factory() -> TranslationPipeline:
        return TranslationPipeline(
            extractor=OnePageExtractor(),
            repository_factory=lambda root: FilesystemArtifactRepository(root),
        )

    manager = WebJobManager(
        config=config,
        pipeline_factory=pipeline_factory,
        repository_factory=FilesystemArtifactRepository,
        translator_factory=translator_context,
    )
    first = manager.submit(
        upload_path=_stage_pdf(config.paths.artifacts_dir, "first"),
        display_filename="article.pdf",
        glossary={"Vattersot": "Dropsy"},
    )
    second = manager.submit(
        upload_path=_stage_pdf(config.paths.artifacts_dir, "second"),
        display_filename="article.pdf",
        glossary={"Vattersot": "Edema"},
    )
    french_config = config.model_copy(
        update={
            "provider": config.provider.model_copy(
                update={
                    "gemini": config.provider.gemini.model_copy(
                        update={"model": "gemini-3.5-flash"}
                    )
                }
            ),
            "translation": config.translation.model_copy(update={"target_language": "French"}),
        }
    )
    third = manager.submit(
        upload_path=_stage_pdf(config.paths.artifacts_dir, "third"),
        display_filename="article.pdf",
        glossary={"Vattersot": "Edema"},
        runtime_config=french_config,
    )
    manager.shutdown()

    first_ready = manager.get(first.job_id)
    second_ready = manager.get(second.job_id)
    third_ready = manager.get(third.job_id)
    assert first_ready.status is WebJobStatus.READY
    assert second_ready.status is WebJobStatus.READY
    assert third_ready.status is WebJobStatus.READY
    assert first_ready.translation_run_id != second_ready.translation_run_id
    assert third_ready.translation_run_id not in {
        first_ready.translation_run_id,
        second_ready.translation_run_id,
    }
    assert translator.settings_glossaries == [
        {"Vattersot": "Dropsy"},
        {"Vattersot": "Edema"},
        {"Vattersot": "Edema"},
    ]
    assert translator.settings_languages == [
        ("Danish", "English"),
        ("Danish", "English"),
        ("Danish", "French"),
    ]
    assert not (config.paths.artifacts_dir / ".uploads" / "first").exists()
    assert not (config.paths.artifacts_dir / ".uploads" / "second").exists()
    assert not (config.paths.artifacts_dir / ".uploads" / "third").exists()

    first_job_dir, first_run_id = manager.ready_context(first.job_id)
    second_job_dir, second_run_id = manager.ready_context(second.job_id)
    third_job_dir, third_run_id = manager.ready_context(third.job_id)
    assert first_job_dir == second_job_dir == third_job_dir
    repository = FilesystemArtifactRepository(first_job_dir)
    assert repository.read_document(first_run_id).translation_settings.glossary == {
        "Vattersot": "Dropsy"
    }
    assert repository.read_document(second_run_id).translation_settings.glossary == {
        "Vattersot": "Edema"
    }
    assert repository.read_document(third_run_id).translation_settings.target_language == "French"
    assert repository.read_document(third_run_id).pages[0].provider.model == "gemini-3.5-flash"
    assert (first_job_dir / "runs" / first_run_id / "output" / "document.md").is_file()
    assert (first_job_dir / "runs" / second_run_id / "output" / "document.md").is_file()
    assert (first_job_dir / "runs" / third_run_id / "output" / "document.md").is_file()

    repository.write_review_position(
        ReviewPosition(
            document_id=repository.read_document(second_run_id).document_id,
            translation_run_id=second_run_id,
            original_page_number=1,
        )
    )
    live_reviews = manager.list_reviews()
    assert {review.translation_run_id for review in live_reviews} == {
        first_run_id,
        second_run_id,
        third_run_id,
    }
    assert all(review.job_id == review.translation_run_id for review in live_reviews)

    assert all(review.status is WebJobStatus.READY for review in live_reviews)
    assert all(review.accepted_blocks == 0 for review in live_reviews)
    assert all(review.total_blocks == 1 for review in live_reviews)
    assert not any(review.review_complete for review in live_reviews)

    first_document = repository.read_document(first_run_id)
    EditorialService(repository).revise_block(
        first_document,
        first_run_id,
        first_document.pages[0].blocks[0].block_id,
        "Reviewed source text",
        expected_base_revision=0,
        status=ReviewStatus.ACCEPTED,
    )
    reviewed_snapshot = next(
        review for review in manager.list_reviews() if review.translation_run_id == first_run_id
    )
    assert reviewed_snapshot.accepted_blocks == 1
    assert reviewed_snapshot.total_blocks == 1
    assert reviewed_snapshot.review_complete is True

    malformed = config.paths.artifacts_dir / "malformed"
    malformed.mkdir()
    (malformed / "manifest.json").write_text("not json", encoding="utf-8")

    mismatched = config.paths.artifacts_dir / "mismatched-aaaaaaaaaaaa"
    shutil.copytree(first_job_dir, mismatched)
    mismatched_repository = FilesystemArtifactRepository(mismatched)
    mismatched_manifest = mismatched_repository.read_manifest().model_copy(
        update={"job_id": mismatched.name}
    )
    mismatched_repository.write_manifest(mismatched_manifest)

    incomplete = config.paths.artifacts_dir / "incomplete-aaaaaaaaaaaa"
    incomplete_repository = FilesystemArtifactRepository(incomplete)
    incomplete_run_id = "f" * 32
    incomplete_repository.write_manifest(
        repository.read_manifest().model_copy(
            update={
                "job_id": incomplete.name,
                "translation_run_id": incomplete_run_id,
                "translation_run_ids": [incomplete_run_id],
            }
        )
    )

    unsafe_name = config.paths.artifacts_dir / "Unsafe Job"
    shutil.copytree(first_job_dir, unsafe_name)
    unsafe_repository = FilesystemArtifactRepository(unsafe_name)
    unsafe_repository.write_manifest(
        unsafe_repository.read_manifest().model_copy(update={"job_id": unsafe_name.name})
    )

    ignored_upload = config.paths.artifacts_dir / ".uploads" / "ignored"
    ignored_upload.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (config.paths.artifacts_dir / "linked-job").symlink_to(outside, target_is_directory=True)

    restarted = WebJobManager(
        config=config,
        pipeline_factory=pipeline_factory,
        repository_factory=FilesystemArtifactRepository,
        translator_factory=translator_context,
    )
    restarted_reviews = restarted.list_reviews()
    restarted.shutdown()

    assert {review.translation_run_id for review in restarted_reviews} == {
        first_run_id,
        second_run_id,
        third_run_id,
    }
    second_review = next(
        review for review in restarted_reviews if review.translation_run_id == second_run_id
    )
    assert second_review.continue_page == 1
    assert second_review.filename == "article.pdf"
    assert second_review.page_count == 1
    assert restarted.get(first_run_id).status is WebJobStatus.READY
    assert restarted.ready_context(first_run_id) == (first_job_dir, first_run_id)

    duplicate = config.paths.artifacts_dir / "duplicate-aaaaaaaaaaaa"
    shutil.copytree(first_job_dir, duplicate)
    duplicate_repository = FilesystemArtifactRepository(duplicate)
    duplicate_repository.write_manifest(
        duplicate_repository.read_manifest().model_copy(
            update={
                "job_id": duplicate.name,
                "translation_run_id": first_run_id,
                "translation_run_ids": [first_run_id],
            }
        )
    )
    duplicate_repository.write_document(
        first_run_id,
        duplicate_repository.read_document(first_run_id).model_copy(
            update={"job_id": duplicate.name}
        ),
    )

    ambiguous = WebJobManager(
        config=config,
        pipeline_factory=pipeline_factory,
        repository_factory=FilesystemArtifactRepository,
        translator_factory=translator_context,
    )
    ambiguous_reviews = ambiguous.list_reviews()
    with pytest.raises(WebJobNotFoundError, match="not found"):
        ambiguous.ready_context(first_run_id)
    ambiguous.shutdown()

    assert {review.translation_run_id for review in ambiguous_reviews} == {
        second_run_id,
        third_run_id,
    }


def test_web_rerun_with_changed_image_dpi_automatically_renders_and_translates(
    tmp_path: Path,
) -> None:
    config = load_project_config(Path("config/default.toml"))
    config = config.model_copy(
        update={"paths": config.paths.model_copy(update={"artifacts_dir": tmp_path / "artifacts"})}
    )
    rerun_config = config.model_copy(
        update={"extraction": config.extraction.model_copy(update={"image_dpi": 225})}
    )
    extractor = RecordingDpiExtractor()
    translator = RecordingTranslator()

    @contextmanager
    def translator_context(
        runtime_config: ProjectConfig,
        api_key: SecretStr | None,
    ) -> Iterator[PageTranslator]:
        del runtime_config, api_key
        yield translator

    manager = WebJobManager(
        config=config,
        pipeline_factory=lambda: TranslationPipeline(
            extractor=extractor,
            repository_factory=FilesystemArtifactRepository,
        ),
        repository_factory=FilesystemArtifactRepository,
        translator_factory=translator_context,
    )
    first = manager.submit(
        upload_path=_stage_pdf(config.paths.artifacts_dir, "first-dpi"),
        display_filename="article.pdf",
        glossary={},
    )
    rerun = manager.submit(
        upload_path=_stage_pdf(config.paths.artifacts_dir, "second-dpi"),
        display_filename="article.pdf",
        glossary={},
        runtime_config=rerun_config,
    )
    manager.shutdown()

    first_ready = manager.get(first.job_id)
    rerun_ready = manager.get(rerun.job_id)
    assert first_ready.status is WebJobStatus.READY
    assert rerun_ready.status is WebJobStatus.READY
    assert first_ready.translation_run_id != rerun_ready.translation_run_id
    assert extractor.image_dpis == [150, 225]
    assert translator.settings_languages == [("Danish", "English"), ("Danish", "English")]
    job_dir, rerun_id = manager.ready_context(rerun.job_id)
    manifest = FilesystemArtifactRepository(job_dir).read_manifest()
    assert manifest.image_dpi == 225
    assert manifest.translation_run_id == rerun_id
    assert manifest.translation_run_ids == [
        first_ready.translation_run_id,
        rerun_ready.translation_run_id,
    ]


def test_completed_multi_page_review_reopens_after_manager_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_project_config(Path("config/default.toml"))
    config = config.model_copy(
        update={"paths": config.paths.model_copy(update={"artifacts_dir": tmp_path / "artifacts"})}
    )
    translator = RecordingTranslator()

    @contextmanager
    def translator_context(
        runtime_config: ProjectConfig,
        api_key: SecretStr | None,
    ) -> Iterator[PageTranslator]:
        del runtime_config
        assert api_key is None
        yield translator

    def pipeline_factory() -> TranslationPipeline:
        return TranslationPipeline(
            extractor=TwoPageExtractor(),
            repository_factory=FilesystemArtifactRepository,
        )

    manager = WebJobManager(
        config=config,
        pipeline_factory=pipeline_factory,
        repository_factory=FilesystemArtifactRepository,
        translator_factory=translator_context,
    )
    submitted = manager.submit(
        upload_path=_stage_pdf(config.paths.artifacts_dir, "restart"),
        display_filename="article.pdf",
        glossary={},
    )
    manager.shutdown()
    ready = manager.get(submitted.job_id)
    assert ready.translation_run_id is not None
    job_dir, run_id = manager.ready_context(submitted.job_id)
    repository = FilesystemArtifactRepository(job_dir)
    document = repository.read_document(run_id)
    second_block = document.pages[1].blocks[0]
    editorial = EditorialService(repository)
    editorial.revise_block(
        document,
        run_id,
        second_block.block_id,
        "Reviewed page two",
        expected_base_revision=0,
        status=ReviewStatus.ACCEPTED,
    )
    editorial.save_review_position(document, run_id, 2)

    original_read_document = FilesystemArtifactRepository.read_document
    read_count = 0

    def counted_read_document(
        self: FilesystemArtifactRepository,
        translation_run_id: str,
    ) -> DocumentTranslation:
        nonlocal read_count
        read_count += 1
        return original_read_document(self, translation_run_id)

    monkeypatch.setattr(
        FilesystemArtifactRepository,
        "read_document",
        counted_read_document,
    )
    restarted = WebJobManager(
        config=config,
        pipeline_factory=pipeline_factory,
        repository_factory=FilesystemArtifactRepository,
        translator_factory=translator_context,
    )
    discovery_reads = read_count
    app = create_app(
        config,
        job_manager=restarted,
        secret_store=DotenvSecretStore(tmp_path / ".env"),
    )
    with TestClient(app) as client:
        catalog = client.get("/api/jobs")
        catalog_reads = read_count
        review = client.get(f"/api/jobs/{run_id}/review")
        second_image = client.get(f"/api/jobs/{run_id}/pages/2/image")
    restarted.shutdown()

    assert catalog.status_code == 200
    assert read_count > catalog_reads
    assert catalog_reads == discovery_reads
    assert catalog.json()["jobs"] == [
        {
            "job_id": run_id,
            "status": "ready",
            "filename": "article.pdf",
            "page_count": 2,
            "continue_page": 2,
            "accepted_blocks": 1,
            "total_blocks": 2,
            "review_complete": False,
            "translation_run_id": run_id,
            "updated_at": catalog.json()["jobs"][0]["updated_at"],
        }
    ]
    assert review.status_code == 200
    assert review.json()["continue_page"] == 2
    assert review.json()["pages"][1]["blocks"][0]["machine_text"] == "Source text"
    assert review.json()["pages"][1]["blocks"][0]["effective_text"] == ("Reviewed page two")
    assert review.json()["pages"][1]["blocks"][0]["review_status"] == "accepted"
    assert second_image.status_code == 200
    assert second_image.headers["content-type"] == "image/png"
    assert second_image.content == b"image-2"

    restarted.delete_review(run_id)
    deleted_manifest = repository.read_manifest()
    assert deleted_manifest.translation_run_ids == []
    assert deleted_manifest.translation_run_id is None
    assert deleted_manifest.status.value == "prepared"
    assert not (job_dir / "runs" / run_id).exists()
    assert restarted.list_reviews() == []
    with pytest.raises(WebJobNotFoundError, match="not found"):
        restarted.ready_context(run_id)
