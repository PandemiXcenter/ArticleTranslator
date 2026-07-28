from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient

from article_translator.adapters.secrets import DotenvSecretStore
from article_translator.adapters.storage import FilesystemArtifactRepository
from article_translator.application.web_jobs import WebJobManager
from article_translator.config import load_project_config
from article_translator.domain.enums import BlockType, ExtractionStatus
from article_translator.domain.models import (
    ArtifactRef,
    DocumentTranslation,
    PageTranslation,
    ProviderMetadata,
    TranslatedBlock,
    TranslationSettings,
    UncertainTerm,
)
from article_translator.interfaces.web import create_app

HASH = "a" * 64
RUN_ID = "1" * 32
BLOCK_ID = "p0001-b0001"


class ReadyJobManager:
    def __init__(self, job_dir: Path) -> None:
        self.job_dir = job_dir

    def ready_context(self, job_id: str) -> tuple[Path, str]:
        assert job_id == "b" * 32
        return self.job_dir, RUN_ID

    def shutdown(self) -> None:
        return None


def _artifact(path: str, media_type: str) -> ArtifactRef:
    return ArtifactRef(
        path=path,
        sha256=HASH,
        media_type=media_type,
        byte_count=10,
    )


def _document() -> DocumentTranslation:
    block = TranslatedBlock(
        block_id=BLOCK_ID,
        original_page_number=1,
        order=1,
        type=BlockType.BODY,
        source_text="gammel og gammel",
        translated_text="olde and olde",
        uncertainties=[
            UncertainTerm(
                source_term="gammel",
                proposed_translation="olde",
                reason="Archaic usage",
                alternatives=["old"],
            )
        ],
    )
    page = PageTranslation(
        translation_run_id=RUN_ID,
        original_page_number=1,
        pdf_page_label="i",
        detected_printed_page_label="1",
        extraction_status=ExtractionStatus.EXTRACTED,
        extracted_character_count=16,
        source_markdown="gammel og gammel",
        source_markdown_artifact=_artifact("prepared/source.md", "text/markdown"),
        source_image=_artifact("prepared/page.png", "image/png"),
        blocks=[block],
        input_fingerprint=HASH,
        provider=ProviderMetadata(
            provider="fake",
            model="fake-model",
            prompt_version="test",
        ),
        translated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return DocumentTranslation(
        translation_run_id=RUN_ID,
        document_id=HASH,
        job_id="job-one",
        source_file_name="source.pdf",
        source_file_sha256=HASH,
        page_count=1,
        translation_settings=TranslationSettings(),
        pages=[page],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_review_revision_replace_all_and_export_contract(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    FilesystemArtifactRepository(job_dir).write_document(RUN_ID, _document())
    config = load_project_config(Path("config/default.toml"))
    config = config.model_copy(
        update={"paths": config.paths.model_copy(update={"artifacts_dir": tmp_path / "artifacts"})}
    )
    manager = ReadyJobManager(job_dir)
    app = create_app(
        config,
        job_manager=cast(WebJobManager, manager),
        secret_store=DotenvSecretStore(tmp_path / ".env"),
    )
    job_path = f"/api/jobs/{'b' * 32}"

    with TestClient(app) as client:
        initial = client.get(f"{job_path}/review")
        client.get("/")
        token = client.cookies["at_csrf"]
        revised = client.post(
            f"{job_path}/revisions",
            json={
                "block_id": BLOCK_ID,
                "editorial_text": "olde and olde",
                "expected_base_revision": 0,
                "status": "accepted",
            },
            headers={"X-CSRF-Token": token},
        )
        conflict = client.post(
            f"{job_path}/revisions",
            json={
                "block_id": BLOCK_ID,
                "editorial_text": "stale edit",
                "expected_base_revision": 0,
                "status": "accepted",
            },
            headers={"X-CSRF-Token": token},
        )
        uncertainty_id = revised.json()["pages"][0]["blocks"][0]["uncertainties"][0][
            "uncertainty_id"
        ]
        replaced = client.post(
            f"{job_path}/uncertainties/{uncertainty_id}/replace",
            json={
                "replacement": "old",
                "scope": "all",
                "expected_versions": {BLOCK_ID: 1},
            },
            headers={"X-CSRF-Token": token},
        )
        exported = client.get(f"{job_path}/export.md")

    assert initial.status_code == 200
    initial_block = initial.json()["pages"][0]["blocks"][0]
    assert initial_block["original_page_number"] == 1
    assert initial_block["source_text"] == "gammel og gammel"
    assert initial_block["machine_text"] == "olde and olde"
    assert initial_block["effective_text"] == "olde and olde"
    uncertainties = initial_block["uncertainties"]
    assert len(uncertainties) == 2
    assert all(item["can_replace_all"] for item in uncertainties)
    assert all(item["matching_occurrence_count"] == 2 for item in uncertainties)
    assert len({item["term_group_id"] for item in uncertainties}) == 1

    assert revised.status_code == 200
    revised_block = revised.json()["pages"][0]["blocks"][0]
    assert revised_block["base_revision"] == 1
    assert revised_block["review_status"] == "accepted"
    assert conflict.status_code == 409

    assert replaced.status_code == 200
    replaced_block = replaced.json()["pages"][0]["blocks"][0]
    assert replaced_block["effective_text"] == "old and old"
    assert replaced_block["base_revision"] == 2
    assert replaced_block["uncertainties"] == []
    assert exported.status_code == 200
    assert "old and old" in exported.text
    assert "olde and olde" not in exported.text
