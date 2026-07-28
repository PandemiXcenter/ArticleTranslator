from article_translator.application.prompting import PROMPT_VERSION
from article_translator.domain.models import (
    SCHEMA_VERSION,
    PreparedPage,
    TranslationSettings,
)
from article_translator.hashing import sha256_bytes, sha256_json
from article_translator.ports.translation import ProviderDescriptor


def page_input_fingerprint(
    *,
    page: PreparedPage,
    settings: TranslationSettings,
    provider: ProviderDescriptor,
    prompt: str,
) -> str:
    """Hash every input whose change must invalidate a page checkpoint."""

    return sha256_json(
        {
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
            "markdown_sha256": page.markdown.sha256,
            "image_sha256": page.image.sha256,
            "provider": provider.provider,
            "model": provider.model,
            "provider_semantic_configuration": dict(provider.semantic_configuration),
            "settings": settings.model_dump(mode="json"),
        }
    )
