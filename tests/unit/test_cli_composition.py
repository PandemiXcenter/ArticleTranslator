from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar, Self, cast

import pytest
from typer.testing import CliRunner

import article_translator.cli as cli
import article_translator.composition as composition
from article_translator.application.pipeline import TranslationPipeline
from article_translator.config import ProjectConfig, load_project_config
from article_translator.domain.models import (
    DocumentTranslation,
    MarkdownExportSettings,
    TranslationSettings,
)


class RecordingPipeline:
    def __init__(self) -> None:
        self.prepared_with: tuple[Path, Path, int, bool] | None = None
        self.translated_with: tuple[Path, TranslationSettings, object, bool] | None = None
        self.compiled_with: tuple[Path, MarkdownExportSettings] | None = None

    def prepare_document(
        self,
        source_pdf: Path,
        *,
        artifacts_dir: Path,
        image_dpi: int,
        force: bool,
    ) -> Path:
        self.prepared_with = (source_pdf, artifacts_dir, image_dpi, force)
        return Path("resolved-job")

    def translate_document(
        self,
        job_dir: Path,
        *,
        settings: TranslationSettings,
        translator: object,
        force: bool,
    ) -> DocumentTranslation:
        self.translated_with = (job_dir, settings, translator, force)
        return cast(DocumentTranslation, SimpleNamespace(pages=[object()]))

    def compile_document(
        self,
        job_dir: Path,
        *,
        settings: MarkdownExportSettings,
    ) -> Path:
        self.compiled_with = (job_dir, settings)
        return job_dir / "output" / "document.md"


class RecordingGeminiTranslator:
    constructor: ClassVar[dict[str, object]] = {}

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        api_version: str,
        timeout_seconds: int,
        attempts: int,
        max_inline_request_bytes: int,
    ) -> None:
        type(self).constructor = {
            "api_key": api_key,
            "model": model,
            "api_version": api_version,
            "timeout_seconds": timeout_seconds,
            "attempts": attempts,
            "max_inline_request_bytes": max_inline_request_bytes,
        }

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def test_full_run_wires_path_extraction_and_export_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_project_config(Path("config/default.toml"))
    pipeline = RecordingPipeline()
    translated: dict[str, object] = {}

    def fake_translate(
        runtime_config: ProjectConfig,
        job_dir: Path,
        *,
        force: bool,
        pipeline: object,
    ) -> DocumentTranslation:
        translated.update(
            {
                "config": runtime_config,
                "job_dir": job_dir,
                "force": force,
                "pipeline": pipeline,
            }
        )
        return cast(DocumentTranslation, SimpleNamespace(pages=[object()]))

    monkeypatch.setattr(cli, "_pipeline", lambda: pipeline)
    monkeypatch.setattr(cli, "_translate", fake_translate)

    result = CliRunner().invoke(
        cli.app,
        ["--config", "config/default.toml", "run", "example.pdf", "--force"],
    )

    assert result.exit_code == 0, result.output
    assert pipeline.prepared_with == (
        Path("example.pdf"),
        config.paths.artifacts_dir,
        config.extraction.image_dpi,
        True,
    )
    assert translated == {
        "config": config,
        "job_dir": Path("resolved-job"),
        "force": True,
        "pipeline": pipeline,
    }
    assert pipeline.compiled_with == (Path("resolved-job"), config.export)


def test_provider_and_translation_settings_are_composed_from_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_project_config(Path("config/default.toml"))
    pipeline = RecordingPipeline()
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-key")
    monkeypatch.setattr(
        composition,
        "GeminiPageTranslator",
        RecordingGeminiTranslator,
    )

    cli._translate(
        config,
        Path("job"),
        force=False,
        pipeline=cast(TranslationPipeline, pipeline),
    )

    gemini = config.provider.gemini
    assert RecordingGeminiTranslator.constructor == {
        "api_key": "test-only-key",
        "model": gemini.model,
        "api_version": gemini.api_version,
        "timeout_seconds": gemini.request_timeout_seconds,
        "attempts": gemini.request_attempts,
        "max_inline_request_bytes": gemini.max_inline_request_bytes,
    }
    assert pipeline.translated_with is not None
    assert pipeline.translated_with[1] == config.translation
