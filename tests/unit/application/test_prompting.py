from article_translator.application.prompting import PROMPT_VERSION, build_page_prompt
from article_translator.domain.enums import TranslationStyle
from article_translator.domain.models import TranslationSettings


def test_prompt_contains_resolved_settings_and_delimited_page_markdown() -> None:
    settings = TranslationSettings(
        source_language="Danish",
        target_language="English",
        style=TranslationStyle.FAITHFUL,
        glossary={"Kolera": "cholera"},
    )

    prompt = build_page_prompt(
        page_number=7,
        markdown="# Om Kolera",
        settings=settings,
    )

    assert f"Prompt version: {PROMPT_VERSION}" in prompt
    assert "Physical PDF page: 7" in prompt
    assert '"style": "faithful"' in prompt
    assert '"Kolera": "cholera"' in prompt
    assert "authoritative translation" in prompt
    assert "assertion for this document" in prompt
    assert "SOURCE_MARKDOWN_START\n# Om Kolera\nSOURCE_MARKDOWN_END" in prompt
