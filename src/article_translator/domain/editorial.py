from datetime import datetime
from typing import Literal

from pydantic import Field

from article_translator.domain.enums import ReviewStatus
from article_translator.domain.models import ContractModel, NonEmptyText, utc_now


class BlockRevision(ContractModel):
    """Future append-only correction; machine translations stay immutable."""

    schema_version: Literal["1.0"] = "1.0"
    revision_id: NonEmptyText
    block_id: NonEmptyText
    base_revision: int = Field(ge=0)
    editorial_text: NonEmptyText
    status: ReviewStatus = ReviewStatus.UNREVIEWED
    editor: str | None = None
    note: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
