import json
from functools import lru_cache
from importlib.resources import files

from article_translator.domain.models import TranslationSettings

PROMPT_VERSION = "translate-page-v2"


@lru_cache(maxsize=1)
def _prompt_preamble() -> str:
    resource = files("article_translator.prompts").joinpath("translate_page_v2.md")
    return resource.read_text(encoding="utf-8").strip()


def build_page_prompt(
    *,
    page_number: int,
    markdown: str,
    settings: TranslationSettings,
) -> str:
    """Build a versioned prompt; page Markdown remains visibly delimited data."""

    resolved_settings = json.dumps(
        settings.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return (
        f"{_prompt_preamble()}\n\n"
        f"Prompt version: {PROMPT_VERSION}\n"
        f"Physical PDF page: {page_number}\n"
        f"Resolved translation settings:\n{resolved_settings}\n\n"
        "SOURCE_MARKDOWN_START\n"
        f"{markdown}\n"
        "SOURCE_MARKDOWN_END\n"
    )
