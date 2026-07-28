import os
import stat
from pathlib import Path

import pytest
from pydantic import SecretStr

from article_translator.adapters.secrets import (
    DotenvSecretStore,
    InvalidSecretValueError,
)
from article_translator.config import SecretSettings


def test_missing_store_has_no_key_and_clear_is_a_noop(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    store = DotenvSecretStore(path)

    assert store.has_gemini_api_key() is False
    assert store.clear_gemini_api_key() is False
    assert not path.exists()


def test_save_creates_private_file_without_returning_secret(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    store = DotenvSecretStore(path)

    store.save_gemini_api_key(SecretStr("saved-key"))

    assert store.has_gemini_api_key() is True
    assert path.read_text(encoding="utf-8") == "GEMINI_API_KEY='saved-key'\n"
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_saved_key_is_readable_by_runtime_secret_settings(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    DotenvSecretStore(path).save_gemini_api_key("AIza_saved-key")

    secret = SecretSettings(_env_file=path).gemini_api_key

    assert secret is not None
    assert secret.get_secret_value() == "AIza_saved-key"


def test_save_updates_first_definition_and_removes_duplicates(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_bytes(
        b"# Local settings\r\n"
        b"OTHER=value\r\n"
        b"export GEMINI_API_KEY=old\r\n"
        b"# GEMINI_API_KEY=commented-out\r\n"
        b"GEMINI_API_KEY='duplicate'\r\n"
        b"TAIL=preserved\r\n"
    )
    store = DotenvSecretStore(path)

    store.save_gemini_api_key("new'key\\suffix")

    assert path.read_bytes() == (
        b"# Local settings\r\n"
        b"OTHER=value\r\n"
        b"GEMINI_API_KEY='new\\'key\\\\suffix'\r\n"
        b"# GEMINI_API_KEY=commented-out\r\n"
        b"TAIL=preserved\r\n"
    )
    assert store.has_gemini_api_key() is True


def test_clear_removes_all_definitions_and_preserves_other_lines(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# GEMINI_API_KEY=commented\n"
        "GEMINI_API_KEY=first\n"
        "OTHER=preserved\n"
        " export GEMINI_API_KEY = second\n"
        "\n",
        encoding="utf-8",
    )
    store = DotenvSecretStore(path)

    assert store.clear_gemini_api_key() is True
    assert path.read_text(encoding="utf-8") == ("# GEMINI_API_KEY=commented\nOTHER=preserved\n\n")
    assert store.clear_gemini_api_key() is False


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   \t",
        "key\ninjected",
        "key\rinjected",
        "key\0injected",
        SecretStr(""),
    ],
)
def test_save_rejects_blank_or_injectable_values_without_modifying_file(
    tmp_path: Path,
    value: SecretStr | str,
) -> None:
    path = tmp_path / ".env"
    original = b"# preserve me\nOTHER=value\n"
    path.write_bytes(original)
    store = DotenvSecretStore(path)

    with pytest.raises(InvalidSecretValueError):
        store.save_gemini_api_key(value)

    assert path.read_bytes() == original


def test_status_uses_last_duplicate_and_understands_blank_dotenv_values(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    store = DotenvSecretStore(path)
    path.write_text(
        "GEMINI_API_KEY=present\nGEMINI_API_KEY=   \n",
        encoding="utf-8",
    )

    assert store.has_gemini_api_key() is False

    path.write_text(
        'GEMINI_API_KEY="" # intentionally blank\nGEMINI_API_KEY="present" # active\n',
        encoding="utf-8",
    )

    assert store.has_gemini_api_key() is True


def test_save_appends_without_rewriting_existing_line_endings(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_bytes(b"# existing comment\r\nOTHER=value")
    store = DotenvSecretStore(path)

    store.save_gemini_api_key("saved-key")

    assert path.read_bytes() == (
        b"# existing comment\r\nOTHER=value\r\nGEMINI_API_KEY='saved-key'\r\n"
    )
