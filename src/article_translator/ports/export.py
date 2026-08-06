from typing import Protocol


class PdfCompiler(Protocol):
    """Compile trusted application-generated LaTeX without exposing adapter details."""

    def compile(self, latex_source: str) -> bytes: ...
