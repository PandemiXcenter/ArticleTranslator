from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from article_translator.adapters.secrets import DotenvSecretStore
from article_translator.application.web_jobs import (
    WebJobManager,
    WebJobNotFoundError,
    WebJobSnapshot,
    WebJobStatus,
    WebReviewSnapshot,
)
from article_translator.config import ProjectConfig, load_project_config
from article_translator.interfaces.web import create_app


class RecordingJobManager:
    def __init__(self) -> None:
        self.submissions: list[
            tuple[
                bytes,
                str,
                dict[str, str],
                Path,
                ProjectConfig,
                SecretStr | None,
            ]
        ] = []
        self.snapshot = WebJobSnapshot(
            job_id="a" * 32,
            status=WebJobStatus.QUEUED,
            filename="article.pdf",
            current_page=0,
            total_pages=None,
            error=None,
            translation_run_id=None,
        )

    def submit(
        self,
        *,
        upload_path: Path,
        display_filename: str,
        glossary: dict[str, str],
        runtime_config: ProjectConfig,
        api_key: SecretStr | None,
    ) -> WebJobSnapshot:
        self.submissions.append(
            (
                upload_path.read_bytes(),
                display_filename,
                glossary,
                upload_path,
                runtime_config,
                api_key,
            )
        )
        shutil.rmtree(upload_path.parent)
        return self.snapshot

    def get(self, job_id: str) -> WebJobSnapshot:
        if job_id != self.snapshot.job_id:
            raise WebJobNotFoundError("Translation job was not found")
        return self.snapshot

    def list_reviews(self) -> list[WebReviewSnapshot]:
        return [
            WebReviewSnapshot(
                job_id="b" * 32,
                status=WebJobStatus.READY,
                filename="completed.pdf",
                page_count=95,
                continue_page=42,
                translation_run_id="b" * 32,
                updated_at=datetime(2026, 8, 4, 12, tzinfo=UTC),
            )
        ]

    def shutdown(self) -> None:
        return None


def configured_for_tmp(tmp_path: Path) -> ProjectConfig:
    config = load_project_config(Path("config/default.toml"))
    return config.model_copy(
        update={"paths": config.paths.model_copy(update={"artifacts_dir": tmp_path / "artifacts"})}
    )


def test_index_and_public_config_never_expose_secret(tmp_path: Path) -> None:
    config = configured_for_tmp(tmp_path)
    manager = RecordingJobManager()
    app = create_app(config, job_manager=cast(WebJobManager, manager))

    with TestClient(app) as client:
        index = client.get("/")
        public_config = client.get("/api/config")

    assert index.status_code == 200
    assert "ArticleTranslator" in index.text
    assert "Bring an old text into clear view" not in index.text
    for expected_control in (
        'data-tab="translate"',
        'data-tab="mappings"',
        'data-tab="settings"',
        'data-tab="review"',
        "Input language",
        "Output language",
        "Save on this computer",
        "Original page",
        "Translated text",
        "Translate One",
        "Translate All",
    ):
        assert expected_control in index.text
    assert index.headers["cache-control"] == "no-store"
    assert public_config.status_code == 200
    payload_text = public_config.text
    assert "GEMINI_API_KEY" not in payload_text
    assert "gemini_api_key" not in payload_text
    payload = public_config.json()
    assert payload["translation"]["source_language"] == "Danish"
    assert payload["translation"]["target_language"] == "English"
    assert payload["provider"]["model"] == "gemini-3.6-flash"
    assert "gemini-3.5-flash-lite" in payload["provider"]["selectable_models"]
    assert "review_context_pages" not in payload["limits"]


def test_blank_environment_key_does_not_prevent_settings_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    config = configured_for_tmp(tmp_path)
    manager = RecordingJobManager()
    app = create_app(
        config,
        job_manager=cast(WebJobManager, manager),
        secret_store=DotenvSecretStore(tmp_path / ".env"),
    )

    with TestClient(app) as client:
        response = client.get("/api/config")

    assert response.status_code == 200
    assert response.json()["api_key_configured"] is False


def test_review_catalog_returns_stable_completed_runs(tmp_path: Path) -> None:
    config = configured_for_tmp(tmp_path)
    manager = RecordingJobManager()
    app = create_app(config, job_manager=cast(WebJobManager, manager))

    with TestClient(app) as client:
        response = client.get("/api/jobs")

    assert response.status_code == 200
    assert response.json() == {
        "jobs": [
            {
                "job_id": "b" * 32,
                "status": "ready",
                "filename": "completed.pdf",
                "page_count": 95,
                "continue_page": 42,
                "translation_run_id": "b" * 32,
                "updated_at": "2026-08-04T12:00:00Z",
            }
        ]
    }


def test_review_frontend_mounts_all_pages_and_uses_delegated_handlers(
    tmp_path: Path,
) -> None:
    config = configured_for_tmp(tmp_path)
    manager = RecordingJobManager()
    app = create_app(config, job_manager=cast(WebJobManager, manager))

    with TestClient(app) as client:
        html = client.get("/").text
        javascript = client.get("/assets/app.js").text

    assert "review_context_pages" not in javascript
    assert "function renderAllReviewPages" in javascript
    assert "function requestReviewWindowShift" not in javascript
    assert "for (const page of state.reviewPages)" in javascript
    assert "function showSourcePage" in javascript
    assert "function persistReviewPosition" in javascript
    assert 'reviewList.addEventListener("click", handleReviewListClick)' in javascript
    assert "state.reviewDrafts.get" in javascript
    assert 'translationContent.addEventListener("click", handleReviewClick)' in javascript
    assert 'translationContent.addEventListener("input", handleReviewInput)' in javascript
    assert 'translationContent.addEventListener("paste", handleReviewPaste)' in javascript
    assert 'mappingBody.addEventListener("click", handleMappingClick)' in javascript
    assert "Machine-reconstructed table" in javascript
    assert "Show original machine reconstruction" in javascript
    assert "Table-bearing pages send that page again" in html
    assert "previous_page_context_count" in javascript

    mapping_factory = javascript.split("function addMapping", 1)[1].split(
        "function handleMappingClick",
        1,
    )[0]
    review_factory = javascript.split("function makeTranslationBlock", 1)[1].split(
        "function captureDrafts",
        1,
    )[0]
    assert ".addEventListener" not in mapping_factory
    assert ".addEventListener" not in review_factory


def test_upload_requires_csrf_and_confines_hostile_filename(tmp_path: Path) -> None:
    config = configured_for_tmp(tmp_path)
    manager = RecordingJobManager()
    app = create_app(config, job_manager=cast(WebJobManager, manager))
    file_payload = b"%PDF-1.7\nsynthetic"
    glossary = '[{"source_term":"Vattersot","target_translation":"Dropsy"}]'

    with TestClient(app) as client:
        rejected = client.post(
            "/api/jobs",
            files={"pdf": ("../../article.pdf", file_payload, "application/pdf")},
            data={"glossary": glossary},
        )
        client.get("/")
        token = client.cookies["at_csrf"]
        accepted = client.post(
            "/api/jobs",
            files={"pdf": ("../../article.pdf", file_payload, "text/plain")},
            data={"glossary": glossary},
            headers={"X-CSRF-Token": token},
        )

    assert rejected.status_code == 403
    assert accepted.status_code == 202
    assert manager.submissions[0][:3] == (
        file_payload,
        "article.pdf",
        {"Vattersot": "Dropsy"},
    )
    staged_path = manager.submissions[0][3]
    runtime_config = manager.submissions[0][4]
    assert runtime_config.translation.source_language == "Danish"
    assert runtime_config.translation.target_language == "English"
    assert manager.submissions[0][5] is None
    assert staged_path.parent.parent == config.paths.artifacts_dir / ".uploads"
    assert not staged_path.exists()


def test_invalid_pdf_and_duplicate_glossary_fail_before_submission(
    tmp_path: Path,
) -> None:
    config = configured_for_tmp(tmp_path)
    manager = RecordingJobManager()
    app = create_app(config, job_manager=cast(WebJobManager, manager))

    with TestClient(app) as client:
        client.get("/")
        token = client.cookies["at_csrf"]
        invalid_pdf = client.post(
            "/api/jobs",
            files={"pdf": ("article.pdf", b"plain text", "application/pdf")},
            data={"glossary": "[]"},
            headers={"X-CSRF-Token": token},
        )
        duplicate_glossary = client.post(
            "/api/jobs",
            files={"pdf": ("article.pdf", b"%PDF-1.7", "application/pdf")},
            data={
                "glossary": (
                    '[{"source_term":"Term","target_translation":"First"},'
                    '{"source_term":" term ","target_translation":"Second"}]'
                )
            },
            headers={"X-CSRF-Token": token},
        )

    assert invalid_pdf.status_code == 415
    assert duplicate_glossary.status_code == 422
    assert manager.submissions == []
    uploads = config.paths.artifacts_dir / ".uploads"
    assert not uploads.exists() or not list(uploads.iterdir())


def test_job_uses_selected_languages_model_style_and_session_key(
    tmp_path: Path,
) -> None:
    config = configured_for_tmp(tmp_path)
    manager = RecordingJobManager()
    app = create_app(config, job_manager=cast(WebJobManager, manager))
    settings = {
        "model": "gemini-3.5-flash-lite",
        "source_language": "Latin",
        "target_language": "German",
        "style": "faithful",
    }

    with TestClient(app) as client:
        client.get("/")
        token = client.cookies["at_csrf"]
        response = client.post(
            "/api/jobs",
            files={"pdf": ("article.pdf", b"%PDF-1.7", "application/pdf")},
            data={
                "glossary": "[]",
                "settings": json.dumps(settings),
                "gemini_api_key": "temporary_key_123",
            },
            headers={"X-CSRF-Token": token},
        )

    assert response.status_code == 202
    assert "temporary_key_123" not in response.text
    runtime_config = manager.submissions[0][4]
    assert runtime_config.provider.gemini.model == "gemini-3.5-flash-lite"
    assert runtime_config.translation.source_language == "Latin"
    assert runtime_config.translation.target_language == "German"
    assert runtime_config.translation.style.value == "faithful"
    submitted_secret = manager.submissions[0][5]
    assert submitted_secret is not None
    assert submitted_secret.get_secret_value() == "temporary_key_123"


def test_job_rejects_model_outside_config_allowlist(tmp_path: Path) -> None:
    config = configured_for_tmp(tmp_path)
    manager = RecordingJobManager()
    app = create_app(config, job_manager=cast(WebJobManager, manager))

    with TestClient(app) as client:
        client.get("/")
        token = client.cookies["at_csrf"]
        response = client.post(
            "/api/jobs",
            files={"pdf": ("article.pdf", b"%PDF-1.7", "application/pdf")},
            data={
                "glossary": "[]",
                "settings": json.dumps(
                    {
                        "model": "unconfigured-model",
                        "source_language": "Danish",
                        "target_language": "English",
                        "style": "balanced",
                    }
                ),
            },
            headers={"X-CSRF-Token": token},
        )

    assert response.status_code == 422
    assert manager.submissions == []
    assert not config.paths.artifacts_dir.exists()


def test_settings_can_keep_key_for_session_or_save_and_clear_it_locally(
    tmp_path: Path,
) -> None:
    config = configured_for_tmp(tmp_path)
    manager = RecordingJobManager()
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("# colleague settings\nOTHER=preserved\n", encoding="utf-8")
    store = DotenvSecretStore(dotenv_path)
    app = create_app(
        config,
        job_manager=cast(WebJobManager, manager),
        secret_store=store,
    )

    with TestClient(app) as client:
        client.get("/")
        token = client.cookies["at_csrf"]
        invalid = client.post(
            "/api/settings/api-key",
            json={
                "api_key": "must-not-leak\ninjected",
                "save_on_computer": True,
            },
            headers={"X-CSRF-Token": token},
        )
        session_only = client.post(
            "/api/settings/api-key",
            json={
                "api_key": "session_key_123",
                "save_on_computer": False,
            },
            headers={"X-CSRF-Token": token},
        )
        session_only_content = dotenv_path.read_text(encoding="utf-8")
        saved = client.post(
            "/api/settings/api-key",
            json={
                "api_key": "saved_key_456",
                "save_on_computer": True,
            },
            headers={"X-CSRF-Token": token},
        )
        saved_content = dotenv_path.read_text(encoding="utf-8")
        cleared = client.delete(
            "/api/settings/api-key",
            headers={"X-CSRF-Token": token},
        )

    assert invalid.status_code == 422
    assert "must-not-leak" not in invalid.text
    assert session_only.status_code == 200
    assert "session_key_123" not in session_only.text
    assert session_only_content == "# colleague settings\nOTHER=preserved\n"
    assert saved.status_code == 200
    assert "saved_key_456" not in saved.text
    assert saved.json()["saved_on_computer"] is True
    assert saved_content == (
        "# colleague settings\nOTHER=preserved\nGEMINI_API_KEY='saved_key_456'\n"
    )
    assert cleared.status_code == 200
    assert cleared.json()["saved_on_computer"] is False
    assert dotenv_path.read_text(encoding="utf-8") == ("# colleague settings\nOTHER=preserved\n")


def test_unknown_job_does_not_create_artifact_directory(tmp_path: Path) -> None:
    config = configured_for_tmp(tmp_path)
    manager = RecordingJobManager()
    app = create_app(config, job_manager=cast(WebJobManager, manager))

    with TestClient(app) as client:
        response = client.get(f"/api/jobs/{'b' * 32}")

    assert response.status_code == 404
    assert not config.paths.artifacts_dir.exists()
