from pathlib import Path
from tomllib import TOMLDecodeError, load
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from article_translator.domain.errors import ConfigurationError
from article_translator.domain.models import MarkdownExportSettings, TranslationSettings


class SecretSettings(BaseSettings):
    """Secrets only. Every non-secret setting belongs in TOML."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    gemini_api_key: SecretStr | None = Field(default=None, validation_alias="GEMINI_API_KEY")


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PathsConfig(ConfigModel):
    artifacts_dir: Path = Path("artifacts")


class ExtractionConfig(ConfigModel):
    image_dpi: int = Field(default=150, ge=72, le=600)


class GeminiConfig(ConfigModel):
    model: str = "gemini-3.6-flash"
    request_timeout_seconds: int = Field(default=120, ge=1, le=900)
    request_attempts: int = Field(default=3, ge=1, le=10)
    temperature: float = Field(default=0.2, ge=0, le=2)


class ProviderConfig(ConfigModel):
    name: Literal["gemini"] = "gemini"
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)


class ProjectConfig(ConfigModel):
    """Fully resolved, non-secret application configuration."""

    config_version: Literal[1] = 1
    paths: PathsConfig = Field(default_factory=PathsConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    translation: TranslationSettings = Field(default_factory=TranslationSettings)
    export: MarkdownExportSettings = Field(default_factory=MarkdownExportSettings)


def load_project_config(path: Path) -> ProjectConfig:
    if not path.is_file():
        raise ConfigurationError(f"Configuration file does not exist: {path}")
    try:
        with path.open("rb") as handle:
            data = load(handle)
        return ProjectConfig.model_validate(data)
    except TOMLDecodeError as exc:
        raise ConfigurationError(f"Invalid TOML in {path}: {exc}") from exc
    except ValidationError as exc:
        raise ConfigurationError(f"Invalid configuration in {path}: {exc}") from exc
