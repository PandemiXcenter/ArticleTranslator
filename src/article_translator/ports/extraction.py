from pathlib import Path
from typing import Protocol

from article_translator.domain.models import PreparedPage


class PageExtractor(Protocol):
    """Creates matched Markdown and image artifacts for every physical page."""

    def extract_pages(
        self,
        source_pdf: Path,
        artifact_root: Path,
        *,
        image_dpi: int,
    ) -> list[PreparedPage]: ...
