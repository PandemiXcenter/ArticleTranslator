from __future__ import annotations

import shutil
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
