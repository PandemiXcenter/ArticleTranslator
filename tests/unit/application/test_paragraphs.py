import pytest

from article_translator.application.paragraphs import project_paragraphs
from article_translator.domain.enums import BlockType, SegmentContinuation
from article_translator.domain.models import TranslatedBlock


def _body(
    page_number: int,
    order: int,
    text: str,
    *,
    continuation: SegmentContinuation = SegmentContinuation.COMPLETE,
    continues_from: str | None = None,
) -> TranslatedBlock:
    return TranslatedBlock(
        block_id=f"p{page_number:04d}-b{order:04d}",
        original_page_number=page_number,
        order=order,
        type=BlockType.BODY,
        source_text=f"Source {page_number}-{order}",
        translated_text=text,
        paragraph_continuation=continuation,
        continues_from_block_id=continues_from,
    )


def test_projects_maximal_body_chains_with_effective_text() -> None:
    first = _body(
        1,
        1,
        "First fragment",
        continuation=SegmentContinuation.TO_NEXT_PAGE,
    )
    second = _body(
        2,
        1,
        "second fragment",
        continuation=SegmentContinuation.FROM_PREVIOUS_AND_TO_NEXT_PAGE,
        continues_from=first.block_id,
    )
    third = _body(
        3,
        1,
        "third fragment.",
        continuation=SegmentContinuation.FROM_PREVIOUS_PAGE,
        continues_from=second.block_id,
    )
    standalone = _body(3, 2, "A separate paragraph.")

    projections = project_paragraphs(
        [first, second, third, standalone],
        effective_text_by_id={second.block_id: "edited middle"},
    )

    assert [projection.paragraph_id for projection in projections] == [
        first.block_id,
        standalone.block_id,
    ]
    assert projections[0].fragment_block_ids == (
        first.block_id,
        second.block_id,
        third.block_id,
    )
    assert projections[0].original_page_numbers == (1, 2, 3)
    assert [fragment.text for fragment in projections[0].fragments] == [
        "First fragment",
        "edited middle",
        "third fragment.",
    ]
    assert projections[1].fragment_block_ids == (standalone.block_id,)


def test_effective_type_override_breaks_a_canonical_chain() -> None:
    first = _body(
        1,
        1,
        "First fragment",
        continuation=SegmentContinuation.TO_NEXT_PAGE,
    )
    second = _body(
        2,
        1,
        "second fragment",
        continuation=SegmentContinuation.FROM_PREVIOUS_AND_TO_NEXT_PAGE,
        continues_from=first.block_id,
    )
    third = _body(
        3,
        1,
        "third fragment.",
        continuation=SegmentContinuation.FROM_PREVIOUS_PAGE,
        continues_from=second.block_id,
    )

    projections = project_paragraphs(
        [first, second, third],
        type_overrides={second.block_id: BlockType.FOOTNOTE},
    )

    assert [projection.fragment_block_ids for projection in projections] == [
        (first.block_id,),
        (third.block_id,),
    ]


def test_rejects_branching_continuation_links() -> None:
    first = _body(
        1,
        1,
        "First fragment",
        continuation=SegmentContinuation.TO_NEXT_PAGE,
    )
    second = _body(
        2,
        1,
        "second fragment",
        continuation=SegmentContinuation.FROM_PREVIOUS_PAGE,
        continues_from=first.block_id,
    )
    competing = _body(
        2,
        2,
        "competing fragment",
        continuation=SegmentContinuation.FROM_PREVIOUS_PAGE,
        continues_from=first.block_id,
    )

    with pytest.raises(ValueError, match="must not branch"):
        project_paragraphs([first, second, competing])
