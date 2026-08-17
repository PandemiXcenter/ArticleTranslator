import pytest
from pydantic import ValidationError

from article_translator.domain.enums import ExtractionStatus
from article_translator.domain.models import ArtifactRef, JobManifest, PreparedPage

HASH = "a" * 64
RUN_ID = "1" * 32


def _artifact(path: str, media_type: str) -> ArtifactRef:
    return ArtifactRef(
        path=path,
        sha256=HASH,
        media_type=media_type,
        byte_count=1,
    )


def _manifest(**updates: object) -> JobManifest:
    values: dict[str, object] = {
        "job_id": "example-aaaaaaaaaaaa",
        "preparation_id": "preparation-1",
        "document_id": HASH,
        "source_file_name": "example.pdf",
        "source_file_sha256": HASH,
        "image_dpi": 150,
        "page_count": 1,
        "pages": [
            PreparedPage(
                original_page_number=1,
                markdown=_artifact("prepared/one/pages/0001/source.md", "text/markdown"),
                image=_artifact("prepared/one/pages/0001/page.png", "image/png"),
                extraction_status=ExtractionStatus.EXTRACTED,
                extracted_character_count=1,
            )
        ],
        "translation_run_id": RUN_ID,
        "translation_run_ids": [RUN_ID],
    }
    values.update(updates)
    return JobManifest.model_validate(values)


def test_manifest_round_trips_ordered_translation_run_index() -> None:
    manifest = _manifest(auto_continue=True, auto_continue_attempts=3)

    restored = JobManifest.model_validate_json(manifest.model_dump_json())

    assert restored.translation_run_id == RUN_ID
    assert restored.translation_run_ids == [RUN_ID]
    assert restored.schema_version == "6.0"
    assert restored.auto_continue is True
    assert restored.auto_continue_attempts == 3


def test_manifest_defaults_auto_continue_for_older_schema_five_artifacts() -> None:
    payload = _manifest().model_dump(mode="json")
    payload.pop("auto_continue")
    payload.pop("auto_continue_attempts")

    restored = JobManifest.model_validate(payload)

    assert restored.auto_continue is False
    assert restored.auto_continue_attempts == 1


def test_manifest_rejects_active_run_outside_ordered_index() -> None:
    with pytest.raises(ValidationError, match="active translation run"):
        _manifest(translation_run_ids=[])


def test_manifest_rejects_duplicate_run_ids() -> None:
    with pytest.raises(ValidationError, match="must be unique"):
        _manifest(translation_run_ids=[RUN_ID, RUN_ID])


def test_manifest_rejects_pre_run_schema_artifacts() -> None:
    payload = _manifest().model_dump(mode="json")
    payload["schema_version"] = "1.0"

    with pytest.raises(ValidationError, match="schema_version"):
        JobManifest.model_validate(payload)
