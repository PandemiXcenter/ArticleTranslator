from collections.abc import Sequence

from article_translator.application.prompting import (
    PROMPT_VERSION,
    TABLE_PROMPT_VERSION,
    table_prompt_contract_sha256,
)
from article_translator.domain.models import (
    SCHEMA_VERSION,
    PreparedPage,
    TranslatedBlock,
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
            "table_prompt_version": TABLE_PROMPT_VERSION,
            "table_prompt_sha256": table_prompt_contract_sha256(),
            "markdown_sha256": page.markdown.sha256,
            "image_sha256": page.image.sha256,
            "provider": provider.provider,
            "model": provider.model,
            "provider_semantic_configuration": dict(provider.semantic_configuration),
            "settings": settings.model_dump(mode="json"),
        }
    )


def table_input_fingerprint(
    *,
    page: PreparedPage,
    settings: TranslationSettings,
    provider: ProviderDescriptor,
    prompt: str,
    first_pass_fingerprint: str,
    table_blocks: Sequence[TranslatedBlock],
) -> str:
    """Hash the exact second-pass inputs and first-pass table target contract."""

    return sha256_json(
        {
            "schema_version": SCHEMA_VERSION,
            "prompt_version": TABLE_PROMPT_VERSION,
            "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
            "markdown_sha256": page.markdown.sha256,
            "image_sha256": page.image.sha256,
            "first_pass_fingerprint": first_pass_fingerprint,
            "table_targets": [
                {
                    "order": block.order,
                    "manual_insertion_reason": block.manual_insertion_reason,
                    "continuation": block.continuation,
                }
                for block in table_blocks
            ],
            "provider": provider.provider,
            "model": provider.model,
            "provider_semantic_configuration": dict(provider.semantic_configuration),
            "settings": settings.model_dump(mode="json"),
        }
    )
