from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from article_translator.adapters.secrets import DotenvSecretStore
from article_translator.adapters.storage import FilesystemArtifactRepository
from article_translator.application.editorial import EditorialService
from article_translator.application.web_jobs import WebJobManager
from article_translator.config import load_project_config
from article_translator.domain.enums import (
    BlockType,
    ExtractionStatus,
    ManualInsertionReason,
    SegmentContinuation,
    SegmentHandling,
)
from article_translator.domain.models import (
    ArtifactRef,
    DocumentTranslation,
    PageTranslation,
    ProviderMetadata,
    TranslatedBlock,
    TranslationSettings,
    UncertainTerm,
)
from article_translator.hashing import sha256_file
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


def _document(
    *,
    source_image: ArtifactRef | None = None,
    translated_block: TranslatedBlock | None = None,
) -> DocumentTranslation:
    block = translated_block or TranslatedBlock(
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
        source_image=source_image or _artifact("prepared/page.png", "image/png"),
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


def test_review_page_image_and_position_are_scoped_and_persisted(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    image_path = job_dir / "prepared" / "page.png"
    image_path.parent.mkdir(parents=True)
    image_bytes = b"\x89PNG\r\n\x1a\nsynthetic-page"
    image_path.write_bytes(image_bytes)
    image_reference = ArtifactRef(
        path="prepared/page.png",
        sha256=sha256_file(image_path),
        media_type="image/png",
        byte_count=len(image_bytes),
    )
    repository = FilesystemArtifactRepository(job_dir)
    repository.write_document(RUN_ID, _document(source_image=image_reference))
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
        image = client.get(f"{job_path}/pages/1/image")
        missing_page = client.get(f"{job_path}/pages/2/image")
        rejected_position = client.put(
            f"{job_path}/review-position",
            json={"original_page_number": 1},
        )
        client.get("/")
        token = client.cookies["at_csrf"]
        saved_position = client.put(
            f"{job_path}/review-position",
            json={"original_page_number": 1},
            headers={"X-CSRF-Token": token},
        )
        invalid_position = client.put(
            f"{job_path}/review-position",
            json={"original_page_number": 2},
            headers={"X-CSRF-Token": token},
        )
        reopened = client.get(f"{job_path}/review")

    assert image.status_code == 200
    assert image.content == image_bytes
    assert image.headers["content-type"] == "image/png"
    assert image.headers["cache-control"] == "no-store"
    assert missing_page.status_code == 404
    assert rejected_position.status_code == 403
    assert saved_position.status_code == 200
    assert saved_position.json()["original_page_number"] == 1
    assert invalid_position.status_code == 404
    assert reopened.json()["continue_page"] == 1
    position = repository.read_review_position(HASH, RUN_ID)
    assert position is not None
    assert position.original_page_number == 1


def test_saving_review_position_does_not_build_full_review_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_dir = tmp_path / "job"
    FilesystemArtifactRepository(job_dir).write_document(RUN_ID, _document())
    config = load_project_config(Path("config/default.toml"))
    config = config.model_copy(
        update={"paths": config.paths.model_copy(update={"artifacts_dir": tmp_path / "artifacts"})}
    )
    app = create_app(
        config,
        job_manager=cast(WebJobManager, ReadyJobManager(job_dir)),
        secret_store=DotenvSecretStore(tmp_path / ".env"),
    )

    def unexpected_projection(*_: object) -> None:
        raise AssertionError("cursor writes must not build the full review projection")

    monkeypatch.setattr(EditorialService, "review_document", unexpected_projection)
    with TestClient(app) as client:
        client.get("/")
        response = client.put(
            f"/api/jobs/{'b' * 32}/review-position",
            json={"original_page_number": 1},
            headers={"X-CSRF-Token": client.cookies["at_csrf"]},
        )

    assert response.status_code == 200
    assert response.json()["original_page_number"] == 1


def test_manual_insertion_metadata_and_revision_flow_through_review_api(
    tmp_path: Path,
) -> None:
    manual_block = TranslatedBlock(
        block_id=BLOCK_ID,
        original_page_number=1,
        order=1,
        type=BlockType.TABLE,
        source_text=None,
        translated_text=None,
        segment_handling=SegmentHandling.MANUAL_INSERTION,
        manual_insertion_reason=ManualInsertionReason.TABLE_LIKE,
        continuation=SegmentContinuation.TO_NEXT_PAGE,
        classification_review_required=True,
        legacy_manual_table=True,
    )
    job_dir = tmp_path / "job"
    FilesystemArtifactRepository(job_dir).write_document(
        RUN_ID,
        _document(translated_block=manual_block),
    )
    config = load_project_config(Path("config/default.toml"))
    config = config.model_copy(
        update={"paths": config.paths.model_copy(update={"artifacts_dir": tmp_path / "artifacts"})}
    )
    app = create_app(
        config,
        job_manager=cast(WebJobManager, ReadyJobManager(job_dir)),
        secret_store=DotenvSecretStore(tmp_path / ".env"),
    )
    job_path = f"/api/jobs/{'b' * 32}"
    reviewer_table = "| Age | Cases |\n| --- | ---: |\n| 0-4 | 12 |"

    with TestClient(app) as client:
        initial = client.get(f"{job_path}/review")
        client.get("/")
        revised = client.post(
            f"{job_path}/revisions",
            json={
                "block_id": BLOCK_ID,
                "editorial_text": reviewer_table,
                "expected_base_revision": 0,
                "status": "accepted",
            },
            headers={"X-CSRF-Token": client.cookies["at_csrf"]},
        )
        exported = client.get(f"{job_path}/export.md")

    assert initial.status_code == 200
    initial_block = initial.json()["pages"][0]["blocks"][0]
    assert initial_block == {
        "block_id": BLOCK_ID,
        "original_page_number": 1,
        "order": 1,
        "type": "table",
        "segment_handling": "manual_insertion",
        "source_text": None,
        "machine_text": None,
        "effective_text": "",
        "manual_insertion_reason": "table_like",
        "footnote_marker": None,
        "continuation": "to_next_page",
        "classification_review_required": True,
        "base_revision": 0,
        "review_status": "unreviewed",
        "uncertainties": [],
    }
    revised_block = revised.json()["pages"][0]["blocks"][0]
    assert revised_block["effective_text"] == reviewer_table
    assert revised_block["base_revision"] == 1
    assert revised_block["review_status"] == "accepted"
    assert reviewer_table in exported.text
    assert "Manual insertion required" not in exported.text
