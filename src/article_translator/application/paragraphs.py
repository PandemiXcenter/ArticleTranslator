from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from article_translator.domain.enums import BlockType
from article_translator.domain.models import TranslatedBlock


@dataclass(frozen=True, slots=True)
class ParagraphFragment:
    """One page-local fragment in an effective body paragraph."""

    block: TranslatedBlock
    text: str


@dataclass(frozen=True, slots=True)
class ParagraphProjection:
    """One maximal body paragraph assembled from trusted page-local links."""

    paragraph_id: str
    fragments: tuple[ParagraphFragment, ...]

    @property
    def fragment_block_ids(self) -> tuple[str, ...]:
        return tuple(fragment.block.block_id for fragment in self.fragments)

    @property
    def original_page_numbers(self) -> tuple[int, ...]:
        return tuple(fragment.block.original_page_number for fragment in self.fragments)


def project_paragraphs(
    blocks: Sequence[TranslatedBlock],
    *,
    effective_text_by_id: Mapping[str, str | None] | None = None,
    type_overrides: Mapping[str, BlockType] | None = None,
) -> list[ParagraphProjection]:
    """Build maximal effective-body chains from pipeline-owned continuation links.

    Canonical blocks and revision history remain page-local. A trusted link is
    projected only while both its child and predecessor are effective body blocks;
    an editorial type override therefore breaks the derived chain without changing
    the immutable machine data.
    """

    resolved_text = effective_text_by_id or {}
    resolved_types = type_overrides or {}
    ordered_blocks = list(blocks)
    blocks_by_id = {block.block_id: block for block in ordered_blocks}
    if len(blocks_by_id) != len(ordered_blocks):
        raise ValueError("paragraph projection requires unique block IDs")

    body_ids = {
        block.block_id
        for block in ordered_blocks
        if resolved_types.get(block.block_id, block.type) is BlockType.BODY
    }
    parent_by_child: dict[str, str] = {}
    child_by_parent: dict[str, str] = {}
    for block in ordered_blocks:
        parent_id = block.continues_from_block_id
        if block.block_id not in body_ids or parent_id not in body_ids:
            continue
        if parent_id in child_by_parent:
            raise ValueError("paragraph continuation links must not branch")
        parent_by_child[block.block_id] = parent_id
        child_by_parent[parent_id] = block.block_id

    projections: list[ParagraphProjection] = []
    visited: set[str] = set()
    for block in ordered_blocks:
        if block.block_id not in body_ids or block.block_id in parent_by_child:
            continue
        fragments: list[ParagraphFragment] = []
        current = block
        while True:
            if current.block_id in visited:
                raise ValueError("paragraph continuation links must not contain a cycle")
            visited.add(current.block_id)
            text = resolved_text.get(current.block_id, current.translated_text)
            fragments.append(ParagraphFragment(block=current, text=text or ""))
            child_id = child_by_parent.get(current.block_id)
            if child_id is None:
                break
            current = blocks_by_id[child_id]
        projections.append(
            ParagraphProjection(
                paragraph_id=block.block_id,
                fragments=tuple(fragments),
            )
        )

    if visited != body_ids:
        raise ValueError("paragraph continuation links must form rooted chains")
    return projections
