"""Validated domain contracts shared by every interface and adapter."""

from article_translator.domain.enums import (
    BlockType,
    ExtractionStatus,
    JobStatus,
    ReviewStatus,
    TranslationStyle,
)
from article_translator.domain.models import (
    DocumentTranslation,
    GeneratedBlock,
    GeneratedPagePayload,
    JobManifest,
    PageTranslation,
    PreparedPage,
    TranslatedBlock,
    TranslationSettings,
)

__all__ = [
    "BlockType",
    "DocumentTranslation",
    "ExtractionStatus",
    "GeneratedBlock",
    "GeneratedPagePayload",
    "JobManifest",
    "JobStatus",
    "PageTranslation",
    "PreparedPage",
    "ReviewStatus",
    "TranslatedBlock",
    "TranslationSettings",
    "TranslationStyle",
]
