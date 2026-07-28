from pathlib import Path

import pytest
from pydantic import ValidationError

from article_translator.config import SecretSettings, load_project_config
from article_translator.domain.enums import TranslationStyle
from article_translator.domain.errors import ConfigurationError


def test_default_toml_is_the_complete_non_secret_configuration() -> None:
    config = load_project_config(Path("config/default.toml"))

    assert config.provider.gemini.model == "gemini-3.6-flash"
    assert config.provider.gemini.api_version == "v1"
    assert config.provider.gemini.max_inline_request_bytes == 19_000_000
    assert config.translation.style is TranslationStyle.BALANCED
    assert config.extraction.image_dpi == 150
    assert config.export.include_page_comments is True
    assert config.paths.artifacts_dir == Path("artifacts").resolve()


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


def test_blank_gemini_key_is_not_treated_as_a_configured_secret() -> None:
    with pytest.raises(ValidationError, match="at least 1"):
        SecretSettings.model_validate({"GEMINI_API_KEY": ""})
