from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from article_translator.domain.enums import ReviewStatus, TranslationStyle
from article_translator.domain.models import NonEmptyText


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GlossaryEntry(ApiModel):
    source_term: NonEmptyText
    target_translation: NonEmptyText


class JobTranslationSettings(ApiModel):
    model: NonEmptyText = Field(max_length=200)
    source_language: NonEmptyText = Field(max_length=100)
    target_language: NonEmptyText = Field(max_length=100)
    style: TranslationStyle


class ApiKeySettingsRequest(ApiModel):
    api_key: SecretStr = Field(min_length=1, max_length=4_000)
    save_on_computer: bool

    @field_validator("api_key")
    @classmethod
    def api_key_must_be_single_line(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if not secret.strip() or any(character in secret for character in "\r\n\0"):
            raise ValueError("API key must be a nonblank single-line value")
        return SecretStr(secret.strip())


class BlockRevisionRequest(ApiModel):
    block_id: NonEmptyText
    editorial_text: NonEmptyText
    expected_base_revision: int = Field(ge=0)
    status: ReviewStatus


class UncertaintyReplacementRequest(ApiModel):
    replacement: NonEmptyText
    scope: Literal["one", "all"]
    expected_versions: dict[str, int]
