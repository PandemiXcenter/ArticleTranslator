import pytest

import article_translator.application.fingerprints as fingerprints
from article_translator.domain.enums import (
    BlockType,
    ExtractionStatus,
    ManualInsertionReason,
    SegmentContinuation,
    SegmentHandling,
    UncertaintyLevel,
)
from article_translator.domain.models import (
    ArtifactRef,
    PreparedPage,
    TranslatedBlock,
    TranslationSettings,
)
from article_translator.ports.translation import ProviderDescriptor

HASH = "a" * 64


def _page() -> PreparedPage:
    markdown = ArtifactRef(
        path="prepared/0001/source.md",
        sha256=HASH,
        media_type="text/markdown",
        byte_count=10,
    )
    return PreparedPage(
        original_page_number=1,
        markdown=markdown,
        image=markdown.model_copy(
            update={"path": "prepared/0001/page.png", "media_type": "image/png"}
        ),
        extraction_status=ExtractionStatus.EXTRACTED,
        extracted_character_count=10,
    )


def _table(continuation: SegmentContinuation) -> TranslatedBlock:
    return TranslatedBlock(
        block_id="p0001-b0002",
        original_page_number=1,
        order=2,
        type=BlockType.TABLE,
        source_text=None,
        translated_text=None,
        segment_handling=SegmentHandling.MANUAL_INSERTION,
        manual_insertion_reason=ManualInsertionReason.TABLE_LIKE,
        continuation=continuation,
    )


def test_page_fingerprint_includes_table_prompt_contract_even_without_a_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _page()
    settings = TranslationSettings()
    provider = ProviderDescriptor(provider="fake", model="fake-v1")
    original = fingerprints.page_input_fingerprint(
        page=page,
        settings=settings,
        provider=provider,
        prompt="page prompt",
    )

    monkeypatch.setattr(fingerprints, "table_prompt_contract_sha256", lambda: "b" * 64)
    changed = fingerprints.page_input_fingerprint(
        page=page,
        settings=settings,
        provider=provider,
        prompt="page prompt",
    )

    assert changed != original


def test_page_fingerprint_changes_with_footnote_appearance_guidance() -> None:
    page = _page()
    provider = ProviderDescriptor(provider="fake", model="fake-v1")
    original = fingerprints.page_input_fingerprint(
        page=page,
        settings=TranslationSettings(),
        provider=provider,
        prompt="page prompt",
    )
    changed = fingerprints.page_input_fingerprint(
        page=page,
        settings=TranslationSettings(
            footnote_appearance_instructions="Small type below a short rule."
        ),
        provider=provider,
        prompt="page prompt",
    )

    assert changed != original


def test_page_fingerprint_changes_with_uncertainty_policy() -> None:
    page = _page()
    provider = ProviderDescriptor(provider="fake", model="fake-v1")
    original = fingerprints.page_input_fingerprint(
        page=page,
        settings=TranslationSettings(),
        provider=provider,
        prompt="page prompt",
    )
    high = fingerprints.page_input_fingerprint(
        page=page,
        settings=TranslationSettings(uncertainty_level=UncertaintyLevel.HIGH),
        provider=provider,
        prompt="page prompt",
    )
    instructed = fingerprints.page_input_fingerprint(
        page=page,
        settings=TranslationSettings(uncertainty_instructions="Mark all numbers."),
        provider=provider,
        prompt="page prompt",
    )
    disabled = fingerprints.page_input_fingerprint(
        page=page,
        settings=TranslationSettings(mark_uncertain_terms=False),
        provider=provider,
        prompt="page prompt",
    )

    assert len({original, high, instructed, disabled}) == 4


def test_table_fingerprint_includes_prompt_first_pass_and_target_metadata() -> None:
    page = _page()
    settings = TranslationSettings(previous_page_context_count=2)
    provider = ProviderDescriptor(provider="fake", model="fake-v1")
    base = fingerprints.table_input_fingerprint(
        page=page,
        settings=settings,
        provider=provider,
        prompt="table prompt with preceding translations",
        first_pass_fingerprint=HASH,
        table_blocks=[_table(SegmentContinuation.COMPLETE)],
    )
    changed_prompt = fingerprints.table_input_fingerprint(
        page=page,
        settings=settings,
        provider=provider,
        prompt="changed table prompt",
        first_pass_fingerprint=HASH,
        table_blocks=[_table(SegmentContinuation.COMPLETE)],
    )
    changed_target = fingerprints.table_input_fingerprint(
        page=page,
        settings=settings,
        provider=provider,
        prompt="table prompt with preceding translations",
        first_pass_fingerprint=HASH,
        table_blocks=[_table(SegmentContinuation.TO_NEXT_PAGE)],
    )

    assert len({base, changed_prompt, changed_target}) == 3
