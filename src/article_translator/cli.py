from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Never

import typer
from rich.console import Console

from article_translator.adapters.extraction import MarkItDownPageExtractor
from article_translator.adapters.llm import GeminiPageTranslator
from article_translator.adapters.storage import FilesystemArtifactRepository
from article_translator.application.pipeline import TranslationPipeline
from article_translator.config import ProjectConfig, SecretSettings, load_project_config
from article_translator.domain.errors import ArticleTranslatorError
from article_translator.domain.models import DocumentTranslation

app = typer.Typer(
    name="article-translator",
    help="Prepare, translate, and compile page-preserving PDF translation jobs.",
    no_args_is_help=True,
)
console = Console()
error_console = Console(stderr=True)


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    config_path: Path
    config: ProjectConfig


@app.callback()
def configure(
    context: typer.Context,
    config_path: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            help="TOML file containing every non-secret setting.",
            dir_okay=False,
        ),
    ] = Path("config/default.toml"),
) -> None:
    """Load one explicit, validated configuration for the whole command."""

    try:
        context.obj = RuntimeContext(
            config_path=config_path,
            config=load_project_config(config_path),
        )
    except ArticleTranslatorError as exc:
        raise typer.BadParameter(str(exc), param_hint="--config") from exc


@app.command()
def ingest(
    context: typer.Context,
    pdf: Annotated[
        Path,
        typer.Argument(help="Source PDF to split into matched Markdown/image pages."),
    ],
    force: Annotated[
        bool,
        typer.Option(help="Rebuild prepared artifacts for this PDF."),
    ] = False,
) -> None:
    """Prepare page artifacts without contacting Gemini."""

    runtime = _runtime(context)
    try:
        job_dir = _pipeline().prepare_document(
            pdf,
            artifacts_dir=runtime.config.paths.artifacts_dir,
            image_dpi=runtime.config.extraction.image_dpi,
            force=force,
        )
        manifest = FilesystemArtifactRepository(job_dir).read_manifest()
    except ArticleTranslatorError as exc:
        _abort(exc)
    console.print(f"Prepared [bold]{manifest.page_count}[/bold] pages in {job_dir}")


@app.command()
def translate(
    context: typer.Context,
    job_dir: Annotated[
        Path,
        typer.Argument(help="Prepared job directory created by `ingest`."),
    ],
    force: Annotated[
        bool,
        typer.Option(help="Replace page checkpoints even when their fingerprints differ."),
    ] = False,
) -> None:
    """Translate all pages, resuming matching checkpoints."""

    runtime = _runtime(context)
    try:
        document = _translate(runtime.config, job_dir, force=force)
    except (ArticleTranslatorError, ValueError) as exc:
        _abort(exc)
    console.print(
        f"Validated [bold]{len(document.pages)}[/bold] translated pages; "
        f"canonical dataset: {job_dir / 'output' / 'document.json'}"
    )


@app.command(name="compile")
def compile_command(
    context: typer.Context,
    job_dir: Annotated[
        Path,
        typer.Argument(help="Translated job directory."),
    ],
) -> None:
    """Compile canonical document JSON into clean Markdown."""

    runtime = _runtime(context)
    try:
        output = _pipeline().compile_document(
            job_dir,
            settings=runtime.config.export,
        )
    except ArticleTranslatorError as exc:
        _abort(exc)
    console.print(f"Markdown written to {output}")


@app.command()
def run(
    context: typer.Context,
    pdf: Annotated[
        Path,
        typer.Argument(help="Source PDF to prepare, translate, and compile."),
    ],
    force: Annotated[
        bool,
        typer.Option(help="Rebuild page artifacts and replace translation checkpoints."),
    ] = False,
) -> None:
    """Run the full backend pipeline with the selected TOML configuration."""

    runtime = _runtime(context)
    config = runtime.config
    try:
        pipeline = _pipeline()
        job_dir = pipeline.prepare_document(
            pdf,
            artifacts_dir=config.paths.artifacts_dir,
            image_dpi=config.extraction.image_dpi,
            force=force,
        )
        console.print(
            "[yellow]Gemini will receive one page image and that page's extracted "
            "Markdown per request.[/yellow]"
        )
        document = _translate(config, job_dir, force=force, pipeline=pipeline)
        output = pipeline.compile_document(job_dir, settings=config.export)
    except (ArticleTranslatorError, ValueError) as exc:
        _abort(exc)
    console.print(f"Completed [bold]{len(document.pages)}[/bold] pages. Markdown: {output}")


@app.command(name="show-config")
def show_config(context: typer.Context) -> None:
    """Validate and print the resolved non-secret configuration."""

    runtime = _runtime(context)
    console.print_json(runtime.config.model_dump_json(indent=2))


def _translate(
    config: ProjectConfig,
    job_dir: Path,
    *,
    force: bool,
    pipeline: TranslationPipeline | None = None,
) -> DocumentTranslation:
    secret = SecretSettings().gemini_api_key
    if secret is None:
        raise ValueError("GEMINI_API_KEY is required in the environment or an ignored .env file")
    gemini = config.provider.gemini
    with GeminiPageTranslator(
        api_key=secret.get_secret_value(),
        model=gemini.model,
        api_version=gemini.api_version,
        timeout_seconds=gemini.request_timeout_seconds,
        attempts=gemini.request_attempts,
        max_inline_request_bytes=gemini.max_inline_request_bytes,
    ) as translator:
        return (pipeline or _pipeline()).translate_document(
            job_dir,
            settings=config.translation,
            translator=translator,
            force=force,
        )


def _pipeline() -> TranslationPipeline:
    return TranslationPipeline(
        extractor=MarkItDownPageExtractor(),
        repository_factory=lambda root: FilesystemArtifactRepository(root),
    )


def _runtime(context: typer.Context) -> RuntimeContext:
    runtime = context.obj
    if not isinstance(runtime, RuntimeContext):
        raise RuntimeError("CLI runtime was not configured")
    return runtime


def _abort(exc: Exception) -> Never:
    error_console.print(f"[red]{type(exc).__name__}:[/red] {exc}")
    raise typer.Exit(code=1)
