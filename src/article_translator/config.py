from pathlib import Path
from tomllib import TOMLDecodeError, load
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from article_translator.domain.enums import TranslationStyle
from article_translator.domain.errors import ConfigurationError
from article_translator.domain.models import (
    MarkdownExportSettings,
    NonEmptyText,
    TranslationSettings,
)


class SecretSettings(BaseSettings):
    """Secrets only. Every non-secret setting belongs in TOML."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    gemini_api_key: SecretStr | None = Field(
        default=None,
        min_length=1,
        validation_alias="GEMINI_API_KEY",
    )


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PathsConfig(ConfigModel):
    artifacts_dir: Path


class ExtractionConfig(ConfigModel):
    image_dpi: int = Field(ge=72, le=600)


class GeminiConfig(ConfigModel):
    model: NonEmptyText
    selectable_models: list[NonEmptyText]
    api_version: NonEmptyText
    request_timeout_seconds: int = Field(ge=1, le=900)
    request_attempts: int = Field(ge=1, le=10)
    max_inline_request_bytes: int = Field(ge=1_000_000, le=20_000_000)

    @model_validator(mode="after")
    def default_model_must_be_selectable(self) -> Self:
        if len(self.selectable_models) != len(set(self.selectable_models)):
            raise ValueError("selectable_models must be unique")
        if self.model not in self.selectable_models:
            raise ValueError("default model must appear in selectable_models")
        return self


class ProviderConfig(ConfigModel):
    name: Literal["gemini"]
    gemini: GeminiConfig


class WebConfig(ConfigModel):
    host: Literal["127.0.0.1", "::1", "localhost"]
    port: int = Field(ge=1, le=65_535)
    max_upload_bytes: int = Field(ge=1_000_000, le=1_000_000_000)
    max_concurrent_jobs: int = Field(ge=1, le=4)
    max_pdf_pages: int = Field(ge=1, le=10_000)
    max_glossary_entries: int = Field(ge=0, le=10_000)
    max_term_characters: int = Field(ge=1, le=4_000)
    status_poll_interval_ms: int = Field(ge=250, le=30_000)
    auto_continue_default: bool
    auto_continue_attempts: int = Field(ge=1, le=10)


class ConfiguredTranslationSettings(TranslationSettings):
    """A complete TOML section with no fallback to Python defaults."""

    source_language: NonEmptyText
    target_language: NonEmptyText
    style: TranslationStyle
    custom_instructions: str | None
    glossary: dict[str, str]
    preserve_names: bool
    preserve_citations: bool
    mark_uncertain_terms: bool
    previous_page_context_count: int = Field(ge=0, le=10)


class ConfiguredMarkdownExportSettings(MarkdownExportSettings):
    """A complete TOML section with no fallback to Python defaults."""

    include_page_comments: bool
    include_headers: bool
    include_footers: bool
    include_page_numbers: bool


class PdfExportConfig(ConfigModel):
    """Local XeLaTeX compilation settings for reviewed PDF downloads."""

    latex_engine: Literal["xelatex"]
    compile_timeout_seconds: int = Field(ge=1, le=300)


class ProjectConfig(ConfigModel):
    """Fully resolved, non-secret application configuration."""

    config_version: Literal[1]
    paths: PathsConfig
    extraction: ExtractionConfig
    provider: ProviderConfig
    translation: ConfiguredTranslationSettings
    export: ConfiguredMarkdownExportSettings
    pdf_export: PdfExportConfig
    web: WebConfig


def load_project_config(path: Path) -> ProjectConfig:
    if not path.is_file():
        raise ConfigurationError(f"Configuration file does not exist: {path}")
    try:
        with path.open("rb") as handle:
            data = load(handle)
        config = ProjectConfig.model_validate(data)
        artifacts_dir = config.paths.artifacts_dir
        if not artifacts_dir.is_absolute():
            artifacts_dir = (path.resolve().parent / artifacts_dir).resolve()
            config = config.model_copy(
                update={"paths": config.paths.model_copy(update={"artifacts_dir": artifacts_dir})}
            )
        return config
    except TOMLDecodeError as exc:
        raise ConfigurationError(f"Invalid TOML in {path}: {exc}") from exc
    except ValidationError as exc:
        raise ConfigurationError(f"Invalid configuration in {path}: {exc}") from exc
