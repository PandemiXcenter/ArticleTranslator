from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from article_translator.domain.enums import BlockType
from article_translator.domain.models import TranslatedBlock


@dataclass(frozen=True)
class FootnoteProjection:
    """One logical note assembled from its page-local canonical fragments."""

    identity: str
    first_block: TranslatedBlock
    fragment_ids: tuple[str, ...]
    text: str
    owner_block_id: str | None
    anchor_offset: int | None
    owner_review_required: bool


def project_footnotes(
    blocks: Sequence[TranslatedBlock],
    *,
    effective_text_by_id: Mapping[str, str | None],
    type_overrides: Mapping[str, BlockType],
    owner_overrides: Mapping[str, tuple[str | None, int | None, bool]] | None = None,
) -> list[FootnoteProjection]:
    """Merge same-ID fragments while preserving the first entrypoint and page order."""

    grouped: dict[str, list[TranslatedBlock]] = {}
    for block in blocks:
        if type_overrides.get(block.block_id, block.type) is not BlockType.FOOTNOTE:
            continue
        identity = block.footnote_id.id if block.footnote_id is not None else block.block_id
        grouped.setdefault(identity, []).append(block)

    resolved_owner_overrides = owner_overrides or {}
    projections: list[FootnoteProjection] = []
    for identity, fragments in grouped.items():
        text = "\n\n".join(
            fragment_text.strip()
            for fragment in fragments
            if (fragment_text := effective_text_by_id.get(fragment.block_id)) is not None
            and fragment_text.strip()
        )
        explicit_owner = next(
            (
                resolved_owner_overrides[fragment.block_id]
                for fragment in fragments
                if fragment.block_id in resolved_owner_overrides
            ),
            None,
        )
        owner = explicit_owner or (
            fragments[0].footnote_owner_block_id,
            fragments[0].footnote_anchor_offset,
            fragments[0].footnote_owner_review_required,
        )
        projections.append(
            FootnoteProjection(
                identity=identity,
                first_block=fragments[0],
                fragment_ids=tuple(fragment.block_id for fragment in fragments),
                text=text,
                owner_block_id=owner[0],
                anchor_offset=owner[1],
                owner_review_required=owner[2],
            )
        )
    return projections
