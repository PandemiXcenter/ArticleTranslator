from pathlib import Path

import pytest

import article_translator.runtime as runtime


def test_application_data_directory_uses_native_user_locations() -> None:
    home = Path("/users/colleague")

    assert (
        runtime.application_data_directory(
            current_platform="darwin",
            environment={},
            home=home,
        )
        == home / "Library" / "Application Support" / "ArticleTranslator"
    )
    assert runtime.application_data_directory(
        current_platform="win32",
        environment={"LOCALAPPDATA": "C:/Users/colleague/AppData/Local"},
        home=home,
    ) == Path("C:/Users/colleague/AppData/Local/ArticleTranslator")
    assert runtime.application_data_directory(
        current_platform="linux",
        environment={"XDG_DATA_HOME": "/users/colleague/.data"},
        home=home,
    ) == Path("/users/colleague/.data/article-translator")


def test_repository_runtime_paths_remain_relative_to_working_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime, "is_frozen", lambda: False)

    paths = runtime.runtime_paths()

    assert paths == runtime.RuntimePaths(
        default_config=Path("config/default.toml"),
        personal_config=Path("config/personal.local.toml"),
        secret_file=Path(".env"),
    )


def test_frozen_runtime_refreshes_owned_default_and_preserves_personal_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_root = tmp_path / "bundle"
    bundled_config = bundle_root / "config" / "default.toml"
    bundled_config.parent.mkdir(parents=True)
    bundled_config.write_text("config_version = 1\n", encoding="utf-8")
    application_root = tmp_path / "application-data"
    personal = application_root / "config" / "personal.local.toml"
    personal.parent.mkdir(parents=True)
    personal.write_text("personal = true\n", encoding="utf-8")
    old_default = application_root / "config" / "default.toml"
    old_default.write_text("old = true\n", encoding="utf-8")

    monkeypatch.setattr(runtime, "is_frozen", lambda: True)
    monkeypatch.setattr(runtime, "application_data_directory", lambda: application_root)
    monkeypatch.setattr(runtime.sys, "_MEIPASS", str(bundle_root), raising=False)

    paths = runtime.runtime_paths()

    assert paths.default_config == old_default
    assert paths.personal_config == personal
    assert paths.secret_file == application_root / ".env"
    assert old_default.read_text(encoding="utf-8") == "config_version = 1\n"
    assert personal.read_text(encoding="utf-8") == "personal = true\n"
