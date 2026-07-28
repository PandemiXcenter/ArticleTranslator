from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import SecretStr

from article_translator.adapters.storage import FilesystemArtifactRepository
from article_translator.application.pipeline import TranslationPipeline
from article_translator.application.web_jobs import WebJobManager, WebJobStatus
from article_translator.config import ProjectConfig, load_project_config
from article_translator.domain.enums import BlockType, ExtractionStatus
from article_translator.domain.models import (
    ArtifactRef,
    GeneratedBlock,
    GeneratedPagePayload,
    PreparedPage,
)
from article_translator.hashing import sha256_file
from article_translator.ports.translation import (
    PageTranslationRequest,
    PageTranslator,
    ProviderDescriptor,
    ProviderResult,
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
                    )
                ]
            )
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
