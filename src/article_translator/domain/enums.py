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
    CAPTION = "caption"
    FOOTNOTE = "footnote"
    PAGE_NUMBER = "page_number"
    HEADER = "header"
    FOOTER = "footer"
    EQUATION = "equation"
    OTHER = "other"


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
