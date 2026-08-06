from pathlib import Path

import pytest
from pydantic import ValidationError

from article_translator.config import SecretSettings, load_project_config
from article_translator.domain.enums import TranslationStyle
from article_translator.domain.errors import ConfigurationError


def test_default_toml_is_the_complete_non_secret_configuration() -> None:
    config = load_project_config(Path("config/default.toml"))

    assert config.provider.gemini.model == "gemini-3.6-flash"
    assert config.provider.gemini.selectable_models == [
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
    ]
    assert config.provider.gemini.api_version == "v1"
    assert config.provider.gemini.max_inline_request_bytes == 19_000_000
    assert config.translation.style is TranslationStyle.BALANCED
    assert config.translation.source_language == "Danish"
    assert config.translation.target_language == "English"
    assert config.translation.previous_page_context_count == 2
    assert config.extraction.image_dpi == 150
    assert config.export.include_page_comments is True
    assert config.pdf_export.latex_engine == "xelatex"
    assert config.pdf_export.compile_timeout_seconds == 120
    assert config.paths.artifacts_dir == Path("artifacts").resolve()
    assert config.web.host == "127.0.0.1"
    assert config.web.max_concurrent_jobs == 1
    assert config.web.max_pdf_pages == 500


def test_unknown_config_keys_fail_fast(tmp_path: Path) -> None:
    path = tmp_path / "invalid.toml"
    path.write_text(
        """
config_version = 1
unexpected = "typo"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="unexpected"):
        load_project_config(path)


def test_missing_config_sections_do_not_fall_back_to_python_defaults(
    tmp_path: Path,
) -> None:
    path = tmp_path / "incomplete.toml"
    path.write_text("config_version = 1\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="Field required"):
        load_project_config(path)


def test_missing_nested_setting_is_rejected(tmp_path: Path) -> None:
    source = Path("config/default.toml").read_text(encoding="utf-8")
    path = tmp_path / "missing-model.toml"
    path.write_text(
        source.replace('model = "gemini-3.6-flash"\n', ""),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match=r"provider\.gemini\.model"):
        load_project_config(path)


def test_previous_page_context_count_is_required_and_bounded(tmp_path: Path) -> None:
    source = Path("config/default.toml").read_text(encoding="utf-8")
    missing = tmp_path / "missing-context-count.toml"
    missing.write_text(
        source.replace("previous_page_context_count = 2\n", ""),
        encoding="utf-8",
    )
    invalid = tmp_path / "invalid-context-count.toml"
    invalid.write_text(
        source.replace("previous_page_context_count = 2", "previous_page_context_count = 11"),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="previous_page_context_count"):
        load_project_config(missing)
    with pytest.raises(ConfigurationError, match="less than or equal to 10"):
        load_project_config(invalid)


def test_pdf_export_timeout_is_required_and_bounded(tmp_path: Path) -> None:
    source = Path("config/default.toml").read_text(encoding="utf-8")
    missing = tmp_path / "missing-pdf-timeout.toml"
    missing.write_text(
        source.replace("compile_timeout_seconds = 120\n", ""),
        encoding="utf-8",
    )
    invalid = tmp_path / "invalid-pdf-timeout.toml"
    invalid.write_text(
        source.replace("compile_timeout_seconds = 120", "compile_timeout_seconds = 301"),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="compile_timeout_seconds"):
        load_project_config(missing)
    with pytest.raises(ConfigurationError, match="less than or equal to 300"):
        load_project_config(invalid)


def test_blank_gemini_key_is_not_treated_as_a_configured_secret() -> None:
    with pytest.raises(ValidationError, match="at least 1"):
        SecretSettings.model_validate({"GEMINI_API_KEY": ""})


def test_default_model_must_be_in_selectable_models(tmp_path: Path) -> None:
    source = Path("config/default.toml").read_text(encoding="utf-8")
    path = tmp_path / "invalid-model-list.toml"
    path.write_text(
        source.replace(
            'model = "gemini-3.6-flash"',
            'model = "gemini-not-in-list"',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="default model must appear"):
        load_project_config(path)


def test_web_server_rejects_non_loopback_host(tmp_path: Path) -> None:
    source = Path("config/default.toml").read_text(encoding="utf-8")
    path = tmp_path / "public-web.toml"
    path.write_text(
        source.replace('host = "127.0.0.1"', 'host = "0.0.0.0"'),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match=r"web\.host"):
        load_project_config(path)
