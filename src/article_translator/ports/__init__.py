"""Protocols implemented by infrastructure adapters."""

from article_translator.ports.artifacts import ArtifactRepository
from article_translator.ports.extraction import PageExtractor
from article_translator.ports.translation import (
    PageTranslationRequest,
    PageTranslator,
    ProviderDescriptor,
    ProviderResult,
)

__all__ = [
    "ArtifactRepository",
    "PageExtractor",
    "PageTranslationRequest",
    "PageTranslator",
    "ProviderDescriptor",
    "ProviderResult",
]
