from __future__ import annotations

import json
import secrets
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Cookie, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import SecretStr, ValidationError
from starlette.middleware.base import RequestResponseEndpoint

from article_translator.adapters.export import LatexPdfCompiler
from article_translator.adapters.secrets import DotenvSecretStore
from article_translator.adapters.storage import FilesystemArtifactRepository
from article_translator.application.editorial import EditorialService
from article_translator.application.web_jobs import (
    WebJobManager,
    WebJobNotFoundError,
    WebJobNotReadyError,
)
from article_translator.composition import build_pipeline, gemini_translator
from article_translator.config import ProjectConfig, SecretSettings
from article_translator.domain.editorial import (
    ReviewDocument,
    UncertaintyFallback,
    UncertaintyHighlight,
)
from article_translator.domain.enums import UncertaintyLevel
from article_translator.domain.errors import (
    ArtifactError,
    EditorialError,
    EditorialTargetError,
    PdfExportError,
    ReplaceAllUnavailableError,
    RevisionConflictError,
)
from article_translator.domain.models import DocumentTranslation
from article_translator.interfaces.web.schemas import (
    ApiKeySettingsRequest,
    BlockRevisionRequest,
    ContinueJobRequest,
    GlossaryEntry,
    JobTranslationSettings,
    ParagraphRevisionRequest,
    ReviewPositionRequest,
    UncertaintyReplacementRequest,
)
from article_translator.ports.export import PdfCompiler


def _asset(name: str) -> str:
    return (
        files("article_translator.interfaces.web")
        .joinpath("static", name)
        .read_text(encoding="utf-8")
    )


def create_app(
    config: ProjectConfig,
    *,
    job_manager: WebJobManager | None = None,
    secret_store: DotenvSecretStore | None = None,
    pdf_compiler: PdfCompiler | None = None,
) -> FastAPI:
    """Create the local editor without exposing secrets or filesystem paths."""

    owns_manager = job_manager is None
    manager = job_manager or WebJobManager(
        config=config,
        pipeline_factory=build_pipeline,
        repository_factory=FilesystemArtifactRepository,
        translator_factory=lambda runtime_config, api_key: gemini_translator(
            runtime_config,
            api_key=api_key,
        ),
    )
    local_secret_store = secret_store or DotenvSecretStore(Path(".env"))
    local_pdf_compiler = pdf_compiler or LatexPdfCompiler(
        engine=config.pdf_export.latex_engine,
        timeout_seconds=config.pdf_export.compile_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owns_manager:
            manager.shutdown()

    app = FastAPI(
        title="ArticleTranslator",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.project_config = config
    app.state.job_manager = manager

    @app.middleware("http")
    async def private_response_headers(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        response = HTMLResponse(_asset("index.html"))
        response.set_cookie(
            "at_csrf",
            secrets.token_urlsafe(32),
            httponly=False,
            samesite="strict",
            secure=False,
        )
        return response

    @app.get("/assets/styles.css")
    def styles() -> Response:
        return Response(_asset("styles.css"), media_type="text/css")

    @app.get("/assets/app.js")
    def javascript() -> Response:
        return Response(_asset("app.js"), media_type="text/javascript")

    @app.get("/api/config")
    def public_config() -> dict[str, object]:
        return {
            "translation": config.translation.model_dump(mode="json"),
            "provider": {
                "name": config.provider.name,
                "model": config.provider.gemini.model,
                "selectable_models": config.provider.gemini.selectable_models,
            },
            "extraction": config.extraction.model_dump(mode="json"),
            "automation": {
                "auto_continue_default": config.web.auto_continue_default,
                "auto_continue_attempts": config.web.auto_continue_attempts,
            },
            "review": {
                "zoom_levels": config.web.review_zoom_levels,
                "zoom_default_percent": config.web.review_zoom_default_percent,
            },
            "uncertainty": {
                "level_choices": config.web.uncertainty_level_choices,
            },
            "api_key_configured": (
                _environment_api_key_configured() or local_secret_store.has_gemini_api_key()
            ),
            "api_key_saved_on_computer": local_secret_store.has_gemini_api_key(),
            "limits": {
                "max_upload_bytes": config.web.max_upload_bytes,
                "max_pdf_pages": config.web.max_pdf_pages,
                "max_glossary_entries": config.web.max_glossary_entries,
                "max_term_characters": config.web.max_term_characters,
                "max_instruction_characters": config.web.max_instruction_characters,
                "status_poll_interval_ms": config.web.status_poll_interval_ms,
            },
        }

    @app.get("/api/jobs")
    def list_review_jobs() -> dict[str, object]:
        return {"jobs": [asdict(snapshot) for snapshot in manager.list_reviews()]}

    @app.get("/api/jobs/recoverable")
    def list_recoverable_jobs() -> dict[str, object]:
        return {"jobs": [asdict(snapshot) for snapshot in manager.list_recoverable_jobs()]}

    @app.delete("/api/jobs/{job_id}", status_code=204)
    def delete_review_job(
        job_id: str,
        at_csrf: Annotated[str | None, Cookie()] = None,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> Response:
        _require_csrf(at_csrf, x_csrf_token)
        try:
            manager.delete_review(job_id)
        except WebJobNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ArtifactError, OSError) as exc:
            raise HTTPException(
                status_code=409, detail="Article could not be deleted safely"
            ) from exc
        return Response(status_code=204)

    @app.post("/api/jobs", status_code=202)
    async def create_job(
        pdf: Annotated[UploadFile, File()],
        glossary: Annotated[str, Form()] = "[]",
        settings: Annotated[str | None, Form()] = None,
        gemini_api_key: Annotated[str | None, Form()] = None,
        at_csrf: Annotated[str | None, Cookie()] = None,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        _require_csrf(at_csrf, x_csrf_token)
        resolved_glossary = _parse_glossary(glossary, config)
        runtime_config, auto_continue = _parse_job_settings(settings, config)
        session_api_key = _parse_session_api_key(gemini_api_key)
        upload_path, display_filename = await _stage_upload(pdf, config)
        try:
            snapshot = manager.submit(
                upload_path=upload_path,
                display_filename=display_filename,
                glossary=resolved_glossary,
                runtime_config=runtime_config,
                api_key=session_api_key,
                auto_continue=auto_continue,
            )
        except Exception:
            _remove_upload_directory(upload_path, config.paths.artifacts_dir)
            raise
        return JSONResponse(asdict(snapshot), status_code=202)

    @app.post("/api/settings/api-key")
    def save_api_key(
        command: ApiKeySettingsRequest,
        at_csrf: Annotated[str | None, Cookie()] = None,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> dict[str, bool]:
        _require_csrf(at_csrf, x_csrf_token)
        if command.save_on_computer:
            local_secret_store.save_gemini_api_key(command.api_key)
        return _api_key_status(local_secret_store)

    @app.delete("/api/settings/api-key")
    def clear_saved_api_key(
        at_csrf: Annotated[str | None, Cookie()] = None,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> dict[str, bool]:
        _require_csrf(at_csrf, x_csrf_token)
        local_secret_store.clear_gemini_api_key()
        return _api_key_status(local_secret_store)

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict[str, object]:
        try:
            return asdict(manager.get(job_id))
        except WebJobNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/jobs/{job_id}/continue", status_code=202)
    def continue_job(
        job_id: str,
        command: ContinueJobRequest,
        at_csrf: Annotated[str | None, Cookie()] = None,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        _require_csrf(at_csrf, x_csrf_token)
        try:
            snapshot = manager.continue_job(job_id, api_key=command.api_key)
        except WebJobNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse(asdict(snapshot), status_code=202)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(
        job_id: str,
        at_csrf: Annotated[str | None, Cookie()] = None,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        _require_csrf(at_csrf, x_csrf_token)
        try:
            return asdict(manager.cancel_job(job_id))
        except WebJobNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/jobs/{job_id}/review")
    def review_document(job_id: str) -> dict[str, object]:
        document, translation_run_id, service, review = _review_context(manager, job_id)
        position = service.get_review_position(document, translation_run_id)
        payload = _review_payload(review)
        payload["continue_page"] = (
            position.original_page_number
            if position is not None
            else _default_continue_page(review)
        )
        return payload

    @app.get("/api/jobs/{job_id}/pages/{page_number}/image")
    def review_page_image(job_id: str, page_number: int) -> FileResponse:
        repository, document, _ = _document_context(manager, job_id)
        if page_number < 1 or page_number > document.page_count:
            raise HTTPException(status_code=404, detail="Physical page was not found")
        page = document.pages[page_number - 1]
        if page.original_page_number != page_number:
            raise ArtifactError("Canonical document pages are not in physical order")
        if page.source_image.media_type != "image/png":
            raise ArtifactError("Review page image has an unsupported media type")
        return FileResponse(repository.resolve(page.source_image), media_type="image/png")

    @app.put("/api/jobs/{job_id}/review-position")
    def save_review_position(
        job_id: str,
        command: ReviewPositionRequest,
        at_csrf: Annotated[str | None, Cookie()] = None,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        _require_csrf(at_csrf, x_csrf_token)
        repository, document, translation_run_id = _document_context(manager, job_id)
        service = EditorialService(repository)
        position = service.save_review_position(
            document,
            translation_run_id,
            command.original_page_number,
        )
        return {
            "translation_run_id": position.translation_run_id,
            "original_page_number": position.original_page_number,
            "updated_at": position.updated_at,
        }

    @app.post("/api/jobs/{job_id}/revisions")
    def revise_block(
        job_id: str,
        command: BlockRevisionRequest,
        at_csrf: Annotated[str | None, Cookie()] = None,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        _require_csrf(at_csrf, x_csrf_token)
        document, translation_run_id, service, _ = _review_context(manager, job_id)
        service.revise_block(
            document,
            translation_run_id,
            command.block_id,
            command.editorial_text,
            block_type=command.type,
            footnote_owner_block_id=command.footnote_owner_block_id,
            footnote_anchor_offset=command.footnote_anchor_offset,
            expected_base_revision=command.expected_base_revision,
            status=command.status,
        )
        return _review_payload(service.review_document(document, translation_run_id))

    @app.post("/api/jobs/{job_id}/paragraph-revisions")
    def revise_paragraph(
        job_id: str,
        command: ParagraphRevisionRequest,
        at_csrf: Annotated[str | None, Cookie()] = None,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        _require_csrf(at_csrf, x_csrf_token)
        document, translation_run_id, service, _ = _review_context(manager, job_id)
        service.revise_paragraph(
            document,
            translation_run_id,
            command.paragraph_id,
            [
                (
                    fragment.block_id,
                    fragment.editorial_text,
                    fragment.expected_base_revision,
                )
                for fragment in command.fragments
            ],
            status=command.status,
        )
        return _review_payload(service.review_document(document, translation_run_id))

    @app.post("/api/jobs/{job_id}/uncertainties/{uncertainty_id}/replace")
    def replace_uncertainty(
        job_id: str,
        uncertainty_id: str,
        command: UncertaintyReplacementRequest,
        at_csrf: Annotated[str | None, Cookie()] = None,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        _require_csrf(at_csrf, x_csrf_token)
        document, translation_run_id, service, _ = _review_context(manager, job_id)
        service.replace_uncertainty(
            document,
            translation_run_id,
            uncertainty_id,
            command.replacement,
            replace_all=command.scope == "all",
            expected_base_revisions=command.expected_versions,
        )
        return _review_payload(service.review_document(document, translation_run_id))

    @app.get("/api/jobs/{job_id}/export.md")
    def export_reviewed_markdown(job_id: str) -> Response:
        document, translation_run_id, service, _ = _review_context(manager, job_id)
        markdown = service.compile_reviewed_markdown(
            document,
            translation_run_id,
            config.export,
        )
        return Response(
            markdown,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="reviewed-translation.md"'},
        )

    @app.get("/api/jobs/{job_id}/export.txt")
    def export_reviewed_text(job_id: str) -> Response:
        document, translation_run_id, service, _ = _review_context(manager, job_id)
        plain_text = service.compile_reviewed_text(
            document,
            translation_run_id,
            config.export,
        )
        return Response(
            plain_text,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="reviewed-translation.txt"'},
        )

    @app.get("/api/jobs/{job_id}/export.pdf")
    def export_reviewed_pdf(job_id: str) -> Response:
        document, translation_run_id, service, _ = _review_context(manager, job_id)
        latex = service.compile_reviewed_latex(
            document,
            translation_run_id,
            config.export,
        )
        return Response(
            local_pdf_compiler.compile(latex),
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="reviewed-translation.pdf"'},
        )

    @app.get("/api/jobs/{job_id}/export.tex")
    def export_reviewed_latex(job_id: str) -> Response:
        document, translation_run_id, service, _ = _review_context(manager, job_id)
        latex = service.compile_reviewed_latex(
            document,
            translation_run_id,
            config.export,
        )
        return Response(
            latex,
            media_type="application/x-tex; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="reviewed-translation.tex"'},
        )

    @app.exception_handler(WebJobNotReadyError)
    def job_not_ready(_: Request, exc: WebJobNotReadyError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(RequestValidationError)
    def invalid_request(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse({"detail": "Request validation failed"}, status_code=422)

    @app.exception_handler(RevisionConflictError)
    def revision_conflict(_: Request, exc: RevisionConflictError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ReplaceAllUnavailableError)
    def replace_all_unavailable(
        _: Request,
        exc: ReplaceAllUnavailableError,
    ) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(EditorialTargetError)
    def editorial_target_missing(_: Request, exc: EditorialTargetError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(EditorialError)
    def editorial_command_failed(_: Request, exc: EditorialError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(PdfExportError)
    def pdf_export_failed(_: Request, __: PdfExportError) -> JSONResponse:
        return JSONResponse(
            {"detail": "The reviewed PDF could not be generated on this computer"},
            status_code=503,
        )

    @app.exception_handler(ArtifactError)
    def review_artifact_failed(_: Request, __: ArtifactError) -> JSONResponse:
        return JSONResponse(
            {"detail": "Review artifacts are unavailable or invalid"},
            status_code=409,
        )

    return app


def _require_csrf(cookie_token: str | None, header_token: str | None) -> None:
    if (
        cookie_token is None
        or header_token is None
        or not secrets.compare_digest(cookie_token, header_token)
    ):
        raise HTTPException(status_code=403, detail="Invalid request token")


def _parse_glossary(raw: str, config: ProjectConfig) -> dict[str, str]:
    try:
        decoded = json.loads(raw)
        if not isinstance(decoded, list):
            raise TypeError
        entries = [GlossaryEntry.model_validate(item) for item in decoded]
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail="Glossary must contain valid term mappings"
        ) from exc

    if len(entries) > config.web.max_glossary_entries:
        raise HTTPException(
            status_code=413,
            detail=f"Glossary exceeds the configured {config.web.max_glossary_entries}-entry limit",
        )

    glossary: dict[str, str] = {}
    normalized_sources: set[str] = set()
    for entry in entries:
        source = entry.source_term.strip()
        target = entry.target_translation.strip()
        if (
            len(source) > config.web.max_term_characters
            or len(target) > config.web.max_term_characters
        ):
            raise HTTPException(
                status_code=422,
                detail="A glossary term exceeds the configured character limit",
            )
        normalized = source.casefold()
        if normalized in normalized_sources:
            raise HTTPException(status_code=422, detail=f"Duplicate glossary term: {source}")
        normalized_sources.add(normalized)
        glossary[source] = target
    return glossary


def _parse_job_settings(
    raw: str | None,
    config: ProjectConfig,
) -> tuple[ProjectConfig, bool]:
    if raw is None or not raw.strip():
        requested = JobTranslationSettings(
            model=config.provider.gemini.model,
            source_language=config.translation.source_language,
            target_language=config.translation.target_language,
            style=config.translation.style,
            footnote_appearance_instructions=(config.translation.footnote_appearance_instructions),
            uncertainty_level=(
                config.translation.uncertainty_level.value
                if config.translation.mark_uncertain_terms
                else "off"
            ),
            uncertainty_instructions=config.translation.uncertainty_instructions,
            previous_page_context_count=config.translation.previous_page_context_count,
            image_dpi=config.extraction.image_dpi,
            auto_continue=config.web.auto_continue_default,
        )
    else:
        try:
            requested = JobTranslationSettings.model_validate_json(raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="Translation settings are invalid",
            ) from exc

    if requested.model not in config.provider.gemini.selectable_models:
        raise HTTPException(
            status_code=422,
            detail="Select a model allowed by the project configuration",
        )
    if len(requested.footnote_appearance_instructions or "") > (
        config.web.max_instruction_characters
    ):
        raise HTTPException(
            status_code=422,
            detail="Footnote appearance guidance exceeds the configured character limit",
        )
    selected_uncertainty_level = requested.uncertainty_level or (
        config.translation.uncertainty_level.value
        if config.translation.mark_uncertain_terms
        else "off"
    )
    if selected_uncertainty_level not in config.web.uncertainty_level_choices:
        raise HTTPException(
            status_code=422,
            detail="Select an uncertainty level allowed by the project configuration",
        )
    if len(requested.uncertainty_instructions or "") > config.web.max_instruction_characters:
        raise HTTPException(
            status_code=422,
            detail="Uncertainty instructions exceed the configured character limit",
        )

    provider = config.provider.model_copy(
        update={"gemini": config.provider.gemini.model_copy(update={"model": requested.model})}
    )
    translation = config.translation.model_copy(
        update={
            "source_language": requested.source_language,
            "target_language": requested.target_language,
            "style": requested.style,
            "footnote_appearance_instructions": (
                requested.footnote_appearance_instructions
                if "footnote_appearance_instructions" in requested.model_fields_set
                else config.translation.footnote_appearance_instructions
            ),
            "mark_uncertain_terms": selected_uncertainty_level != "off",
            "uncertainty_level": (
                UncertaintyLevel(selected_uncertainty_level)
                if selected_uncertainty_level != "off"
                else config.translation.uncertainty_level
            ),
            "uncertainty_instructions": (
                requested.uncertainty_instructions
                if "uncertainty_instructions" in requested.model_fields_set
                else config.translation.uncertainty_instructions
            ),
            "previous_page_context_count": requested.previous_page_context_count,
        }
    )
    extraction = config.extraction.model_copy(update={"image_dpi": requested.image_dpi})
    return (
        config.model_copy(
            update={
                "provider": provider,
                "translation": translation,
                "extraction": extraction,
            }
        ),
        requested.auto_continue,
    )


def _parse_session_api_key(raw: str | None) -> SecretStr | None:
    if raw is None or not raw.strip():
        return None
    try:
        return ApiKeySettingsRequest(
            api_key=raw,
            save_on_computer=False,
        ).api_key
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="Gemini API key must be a nonblank single-line value",
        ) from exc


def _api_key_status(secret_store: DotenvSecretStore) -> dict[str, bool]:
    return {
        "api_key_configured": (
            _environment_api_key_configured() or secret_store.has_gemini_api_key()
        ),
        "saved_on_computer": secret_store.has_gemini_api_key(),
    }


def _environment_api_key_configured() -> bool:
    try:
        return SecretSettings().gemini_api_key is not None
    except ValidationError:
        return False


async def _stage_upload(
    upload: UploadFile,
    config: ProjectConfig,
) -> tuple[Path, str]:
    display_filename = _safe_pdf_filename(upload.filename)
    uploads_root = config.paths.artifacts_dir / ".uploads"
    staging_directory = uploads_root / uuid4().hex
    staging_directory.mkdir(parents=True, exist_ok=False)
    upload_path = staging_directory / display_filename
    byte_count = 0
    first_bytes = bytearray()
    try:
        with upload_path.open("xb") as destination:
            while chunk := await upload.read(1024 * 1024):
                byte_count += len(chunk)
                if byte_count > config.web.max_upload_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail="PDF exceeds the configured upload limit",
                    )
                if len(first_bytes) < 1024:
                    first_bytes.extend(chunk[: 1024 - len(first_bytes)])
                destination.write(chunk)
        if byte_count == 0 or b"%PDF-" not in bytes(first_bytes):
            raise HTTPException(status_code=415, detail="Selected file is not a valid PDF")
        return upload_path, display_filename
    except Exception:
        _remove_upload_directory(upload_path, config.paths.artifacts_dir)
        raise
    finally:
        await upload.close()


def _safe_pdf_filename(filename: str | None) -> str:
    candidate = (filename or "document.pdf").replace("\\", "/").rsplit("/", 1)[-1]
    candidate = "".join(character for character in candidate if character.isprintable())
    candidate = candidate.strip().strip(".")[:180]
    if not candidate.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="Select a file with a .pdf extension")
    return candidate or "document.pdf"


def _remove_upload_directory(upload_path: Path, artifacts_dir: Path) -> None:
    uploads_root = (artifacts_dir / ".uploads").resolve()
    staging_directory = upload_path.parent.resolve()
    if staging_directory.parent == uploads_root and staging_directory.is_dir():
        shutil.rmtree(staging_directory)


def _review_context(
    manager: WebJobManager,
    job_id: str,
) -> tuple[DocumentTranslation, str, EditorialService, ReviewDocument]:
    repository, document, translation_run_id = _document_context(manager, job_id)
    service = EditorialService(repository)
    review = service.review_document(document, translation_run_id)
    return document, translation_run_id, service, review


def _document_context(
    manager: WebJobManager,
    job_id: str,
) -> tuple[FilesystemArtifactRepository, DocumentTranslation, str]:
    try:
        job_dir, translation_run_id = manager.ready_context(job_id)
    except WebJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    repository = FilesystemArtifactRepository(job_dir)
    document = repository.read_document(translation_run_id)
    return repository, document, translation_run_id


def _review_payload(review: ReviewDocument) -> dict[str, object]:
    return {
        "document_id": review.document_id,
        "translation_run_id": review.translation_run_id,
        "source_file_name": review.source_file_name,
        "page_count": review.page_count,
        "uncertainty_groups": _uncertainty_group_payload(review),
        "paragraph_groups": [group.model_dump(mode="json") for group in review.paragraph_groups],
        "pages": [
            {
                "original_page_number": page.original_page_number,
                "pdf_page_label": page.pdf_page_label,
                "detected_printed_page_label": page.detected_printed_page_label,
                "blocks": [
                    {
                        "block_id": block.block_id,
                        "original_page_number": block.original_page_number,
                        "order": block.order,
                        "machine_type": block.machine_type.value,
                        "type": block.type.value,
                        "segment_handling": block.segment_handling.value,
                        "source_text": block.source_text,
                        "machine_text": block.machine_translated_text,
                        "effective_text": block.effective_translated_text,
                        "manual_insertion_reason": (
                            block.manual_insertion_reason.value
                            if block.manual_insertion_reason is not None
                            else None
                        ),
                        "footnote_id": (
                            block.footnote_id.model_dump(mode="json")
                            if block.footnote_id is not None
                            else None
                        ),
                        "footnote_description": (
                            block.footnote_description.model_dump(mode="json")
                            if block.footnote_description is not None
                            else None
                        ),
                        "footnote_owner_block_id": block.footnote_owner_block_id,
                        "footnote_anchor_offset": block.footnote_anchor_offset,
                        "footnote_owner_review_required": (block.footnote_owner_review_required),
                        "continuation": (
                            block.continuation.value if block.continuation is not None else None
                        ),
                        "footnote_continues_from_block_id": (
                            block.footnote_continues_from_block_id
                        ),
                        "paragraph_continuation": (
                            block.paragraph_continuation.value
                            if block.paragraph_continuation is not None
                            else None
                        ),
                        "continues_from_block_id": block.continues_from_block_id,
                        "classification_review_required": (block.classification_review_required),
                        "base_revision": block.latest_revision_number,
                        "review_status": block.review_status.value,
                        "uncertainties": [
                            *[
                                _range_uncertainty_payload(highlight)
                                for highlight in block.uncertainty_highlights
                            ],
                            *[
                                _fallback_uncertainty_payload(fallback)
                                for fallback in block.uncertainty_fallbacks
                            ],
                        ],
                    }
                    for block in page.blocks
                ],
            }
            for page in review.pages
        ],
    }


@dataclass(slots=True)
class _UncertaintyGroup:
    term_group_id: str
    source_term: str
    proposed_translation: str | None
    reason: str
    alternatives: list[str]
    occurrence_count: int = 0
    page_numbers: set[int] = field(default_factory=set)
    uncertainty_ids: list[str] = field(default_factory=list)


def _uncertainty_group_payload(review: ReviewDocument) -> list[dict[str, object]]:
    groups: dict[str, _UncertaintyGroup] = {}
    for page in review.pages:
        for block in page.blocks:
            uncertainties: list[UncertaintyHighlight | UncertaintyFallback] = [
                *block.uncertainty_highlights,
                *block.uncertainty_fallbacks,
            ]
            for uncertainty in uncertainties:
                group = groups.setdefault(
                    uncertainty.term_group_id,
                    _UncertaintyGroup(
                        term_group_id=uncertainty.term_group_id,
                        source_term=uncertainty.source_term,
                        proposed_translation=uncertainty.proposed_translation,
                        reason=uncertainty.reason,
                        alternatives=list(uncertainty.alternatives),
                    ),
                )
                group.occurrence_count += 1
                group.page_numbers.add(page.original_page_number)
                group.uncertainty_ids.append(uncertainty.uncertainty_id)
                for alternative in uncertainty.alternatives:
                    if alternative not in group.alternatives:
                        group.alternatives.append(alternative)

    ordered = sorted(
        groups.values(),
        key=lambda group: (
            -group.occurrence_count,
            group.source_term.casefold(),
            (group.proposed_translation or "").casefold(),
            group.term_group_id,
        ),
    )
    return [
        {
            "term_group_id": group.term_group_id,
            "source_term": group.source_term,
            "proposed_translation": group.proposed_translation,
            "reason": group.reason,
            "alternatives": group.alternatives,
            "occurrence_count": group.occurrence_count,
            "page_numbers": sorted(group.page_numbers),
            "first_uncertainty_id": group.uncertainty_ids[0],
        }
        for group in ordered
    ]


def _default_continue_page(review: ReviewDocument) -> int:
    for page in review.pages:
        if any(block.review_status.value != "accepted" for block in page.blocks):
            return page.original_page_number
    return review.pages[-1].original_page_number


def _range_uncertainty_payload(
    highlight: UncertaintyHighlight,
) -> dict[str, object]:
    return {
        "uncertainty_id": highlight.uncertainty_id,
        "term_group_id": highlight.term_group_id,
        "source_term": highlight.source_term,
        "proposed_translation": highlight.proposed_translation,
        "reason": highlight.reason,
        "alternatives": highlight.alternatives,
        "highlight_text": highlight.proposed_translation,
        "highlight_mode": highlight.highlight_mode,
        "start_offset": highlight.start_offset,
        "end_offset": highlight.end_offset,
        "matching_occurrence_count": highlight.matching_occurrence_count,
        "can_replace_all": highlight.can_replace_all,
        "resolved": False,
    }


def _fallback_uncertainty_payload(
    fallback: UncertaintyFallback,
) -> dict[str, object]:
    return {
        "uncertainty_id": fallback.uncertainty_id,
        "term_group_id": fallback.term_group_id,
        "source_term": fallback.source_term,
        "proposed_translation": fallback.proposed_translation,
        "reason": fallback.reason,
        "alternatives": fallback.alternatives,
        "highlight_text": fallback.proposed_translation or fallback.source_term,
        "highlight_mode": fallback.highlight_mode,
        "matching_occurrence_count": 1,
        "can_replace_all": False,
        "resolved": False,
    }
