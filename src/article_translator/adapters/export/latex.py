from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from article_translator.domain.errors import PdfExportError


class LatexPdfCompiler:
    """Compile application-generated LaTeX in an isolated temporary directory."""

    def __init__(self, *, engine: str, timeout_seconds: int) -> None:
        self._engine = engine
        self._timeout_seconds = timeout_seconds

    def compile(self, latex_source: str) -> bytes:
        engine_path = shutil.which(self._engine)
        if engine_path is None:
            raise PdfExportError(
                "PDF export requires the configured XeLaTeX executable on this computer"
            )
        try:
            with TemporaryDirectory(prefix="article-translator-pdf-") as directory:
                work_dir = Path(directory)
                source_path = work_dir / "reviewed-translation.tex"
                output_path = work_dir / "reviewed-translation.pdf"
                source_path.write_text(latex_source, encoding="utf-8")
                result = subprocess.run(
                    [
                        engine_path,
                        "-interaction=nonstopmode",
                        "-halt-on-error",
                        "-no-shell-escape",
                        f"-output-directory={work_dir}",
                        source_path.name,
                    ],
                    cwd=work_dir,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=self._timeout_seconds,
                )
                if result.returncode != 0 or not output_path.is_file():
                    raise PdfExportError("The reviewed translation could not be typeset as PDF")
                pdf = output_path.read_bytes()
        except subprocess.TimeoutExpired as exc:
            raise PdfExportError("PDF export exceeded the configured compilation timeout") from exc
        except OSError as exc:
            raise PdfExportError("The configured XeLaTeX executable could not be started") from exc
        if not pdf.startswith(b"%PDF-"):
            raise PdfExportError("XeLaTeX did not produce a valid PDF document")
        return pdf
