from enum import StrEnum


class BlockType(StrEnum):
    """Logical roles supported by the initial structured-output schema."""

    TITLE = "title"
    SUBTITLE = "subtitle"
    BYLINE = "byline"
    HEADING = "heading"
    BODY = "body"
    LIST_ITEM = "list_item"
    QUOTE = "quote"
    TABLE = "table"
    FIGURE = "figure"
    CAPTION = "caption"
    FOOTNOTE = "footnote"
    PAGE_NUMBER = "page_number"
    HEADER = "header"
    FOOTER = "footer"
    EQUATION = "equation"
    OTHER = "other"


class SegmentHandling(StrEnum):
    """How machine or reviewer text is produced for a page segment."""

    TRANSLATE = "translate"
    TABLE_RECONSTRUCTION = "table_reconstruction"
    MANUAL_INSERTION = "manual_insertion"


class ManualInsertionReason(StrEnum):
    """Kinds of page regions intentionally withheld from transcription."""

    TABLE = "table"
    TABLE_LIKE = "table_like"
    FIGURE = "figure"


class SegmentContinuation(StrEnum):
    """Page-local evidence about a segment's relationship to adjacent pages."""

    COMPLETE = "complete"
    FROM_PREVIOUS_PAGE = "from_previous_page"
    TO_NEXT_PAGE = "to_next_page"
    FROM_PREVIOUS_AND_TO_NEXT_PAGE = "from_previous_and_to_next_page"
    UNKNOWN = "unknown"


class TranslationStyle(StrEnum):
    """Provider-neutral translation policy."""

    FAITHFUL = "faithful"
    BALANCED = "balanced"
    READABLE = "readable"


class ExtractionStatus(StrEnum):
    EXTRACTED = "extracted"
    EMPTY = "empty"
    FAILED = "failed"


class JobStatus(StrEnum):
    PREPARED = "prepared"
    TRANSLATING = "translating"
    FAILED = "failed"
    TRANSLATED = "translated"
    COMPILED = "compiled"


class ReviewStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    IN_REVIEW = "in_review"
    ACCEPTED = "accepted"
    NEEDS_WORK = "needs_work"
