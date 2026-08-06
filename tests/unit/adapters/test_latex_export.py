from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import article_translator.adapters.export.latex as latex_adapter
from article_translator.adapters.export import LatexPdfCompiler
from article_translator.domain.errors import PdfExportError


def test_latex_compiler_uses_bounded_no_shell_escape_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation: dict[str, object] = {}

    def fake_run(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        invocation.update({"command": command, **options})
        output_argument = next(item for item in command if item.startswith("-output-directory="))
        output_directory = Path(output_argument.split("=", 1)[1])
        (output_directory / "reviewed-translation.pdf").write_bytes(b"%PDF-1.7\ntest")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(latex_adapter.shutil, "which", lambda _: "/usr/bin/xelatex")
    monkeypatch.setattr(latex_adapter.subprocess, "run", fake_run)

    result = LatexPdfCompiler(engine="xelatex", timeout_seconds=17).compile(
        "\\documentclass{article}\\begin{document}Test\\end{document}"
    )

    assert result == b"%PDF-1.7\ntest"
    assert invocation["command"][0] == "/usr/bin/xelatex"  # type: ignore[index]
    assert "-no-shell-escape" in invocation["command"]  # type: ignore[operator]
    assert invocation["timeout"] == 17
    assert invocation["check"] is False


def test_latex_compiler_reports_missing_engine_without_source_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(latex_adapter.shutil, "which", lambda _: None)

    with pytest.raises(PdfExportError, match="requires the configured XeLaTeX"):
        LatexPdfCompiler(engine="xelatex", timeout_seconds=17).compile("private article text")
