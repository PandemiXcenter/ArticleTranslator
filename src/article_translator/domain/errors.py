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
