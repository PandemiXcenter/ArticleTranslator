class ArticleTranslatorError(Exception):
    """Base class for expected application failures."""


class ConfigurationError(ArticleTranslatorError):
    """A non-secret TOML configuration file is missing or invalid."""


class ArtifactError(ArticleTranslatorError):
    """A persisted artifact is missing, unsafe, or invalid."""


class StaleCheckpointError(ArticleTranslatorError):
    """A page checkpoint was produced from different inputs or settings."""


class IncompleteDocumentError(ArticleTranslatorError):
    """Compilation was requested before every page had a valid translation."""


class PageTranslationError(ArticleTranslatorError):
    """A provider failed while translating a particular physical page."""

    def __init__(self, page_number: int, message: str) -> None:
        super().__init__(f"Page {page_number}: {message}")
        self.page_number = page_number


class EditorialError(ArticleTranslatorError):
    """An editorial command or review projection could not be completed."""


class EditorialTargetError(EditorialError):
    """A document, run, block, or uncertainty target is not present."""


class RevisionConflictError(EditorialError):
    """An editorial command was based on an out-of-date block revision."""

    def __init__(self, block_id: str, expected: int | None, actual: int) -> None:
        expected_label = "missing" if expected is None else str(expected)
        super().__init__(
            f"Block {block_id} changed during review "
            f"(expected revision {expected_label}, current revision {actual})"
        )
        self.block_id = block_id
        self.expected = expected
        self.actual = actual


class ReplaceAllUnavailableError(EditorialError):
    """Translate All was requested for a term with fewer than two highlights."""
