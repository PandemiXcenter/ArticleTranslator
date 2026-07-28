from pathlib import Path
from tomllib import TOMLDecodeError, load
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
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
    api_version: NonEmptyText
    request_timeout_seconds: int = Field(ge=1, le=900)
    request_attempts: int = Field(ge=1, le=10)
    max_inline_request_bytes: int = Field(ge=1_000_000, le=20_000_000)


class ProviderConfig(ConfigModel):
    name: Literal["gemini"]
    gemini: GeminiConfig


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


class ConfiguredMarkdownExportSettings(MarkdownExportSettings):
    """A complete TOML section with no fallback to Python defaults."""

    include_page_comments: bool
    include_headers: bool
    include_footers: bool
    include_page_numbers: bool


class ProjectConfig(ConfigModel):
    """Fully resolved, non-secret application configuration."""

    config_version: Literal[1]
    paths: PathsConfig
    extraction: ExtractionConfig
    provider: ProviderConfig
    translation: ConfiguredTranslationSettings
    export: ConfiguredMarkdownExportSettings


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
