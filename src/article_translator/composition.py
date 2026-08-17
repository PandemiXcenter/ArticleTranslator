from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import SecretStr

from article_translator.adapters.extraction import MarkItDownPageExtractor
from article_translator.adapters.llm import GeminiPageTranslator
from article_translator.adapters.storage import FilesystemArtifactRepository
from article_translator.application.pipeline import TranslationPipeline
from article_translator.config import ProjectConfig, SecretSettings
from article_translator.domain.models import DocumentTranslation
from article_translator.ports.translation import PageTranslator
from article_translator.runtime import default_secret_path


def build_pipeline() -> TranslationPipeline:
    """Compose provider-neutral application services with local adapters."""

    return TranslationPipeline(
        extractor=MarkItDownPageExtractor(),
        repository_factory=lambda root: FilesystemArtifactRepository(root),
    )


@contextmanager
def gemini_translator(
    config: ProjectConfig,
    *,
    api_key: SecretStr | None = None,
) -> Iterator[PageTranslator]:
    """Build one configured Gemini adapter without leaking its secret."""

    secret = api_key or SecretSettings(_env_file=default_secret_path()).gemini_api_key
    if secret is None:
        raise ValueError("GEMINI_API_KEY is required in the environment or an ignored .env file")
    gemini = config.provider.gemini
    with GeminiPageTranslator(
        api_key=secret.get_secret_value(),
        model=gemini.model,
        api_version=gemini.api_version,
        timeout_seconds=gemini.request_timeout_seconds,
        attempts=gemini.request_attempts,
        max_inline_request_bytes=gemini.max_inline_request_bytes,
    ) as translator:
        yield translator


def translate_with_config(
    config: ProjectConfig,
    job_dir: Path,
    *,
    force: bool,
    pipeline: TranslationPipeline | None = None,
) -> DocumentTranslation:
    """Translate through the same composition boundary for CLI and web."""

    active_pipeline = pipeline or build_pipeline()
    with gemini_translator(config) as translator:
        return active_pipeline.translate_document(
            job_dir,
            settings=config.translation,
            translator=translator,
            force=force,
        )
