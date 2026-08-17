import os
from pathlib import Path
from tomllib import load

import pytest

from article_translator.build_executable import (
    BuildTarget,
    ExecutableBuildError,
    build_executable,
    main,
    native_build_target,
)


def _project_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "src" / "article_translator").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (root / "src" / "article_translator" / "executable.py").write_text(
        "def main(): pass\n",
        encoding="utf-8",
    )
    (root / "config" / "default.toml").write_text(
        "config_version = 1\n",
        encoding="utf-8",
    )
    return root


@pytest.mark.parametrize(
    ("platform_name", "target"),
    [("win32", BuildTarget.WINDOWS), ("darwin", BuildTarget.MAC), ("linux", BuildTarget.LINUX)],
)
def test_native_build_target(platform_name: str, target: BuildTarget) -> None:
    assert native_build_target(platform_name) is target


def test_build_command_uses_single_file_resources_and_native_runtime(tmp_path: Path) -> None:
    root = _project_root(tmp_path)
    calls: list[list[str]] = []

    output = build_executable(
        BuildTarget.MAC,
        current_platform="darwin",
        project_root=root,
        runner=calls.append,
    )

    assert output == root / "dist" / "mac" / "ArticleTranslator"
    arguments = calls[0]
    assert "--onefile" in arguments
    assert "--clean" not in arguments
    assert "--collect-data" in arguments
    assert "article_translator" in arguments
    assert "markitdown" in arguments
    assert "magika" in arguments
    assert "pypdfium2" in arguments
    assert f"{root / 'config' / 'default.toml'}{os.pathsep}config" in arguments
    assert arguments[-1] == str(root / "src" / "article_translator" / "executable.py")


def test_clean_build_removes_only_generated_target_and_never_bundles_private_data(
    tmp_path: Path,
) -> None:
    root = _project_root(tmp_path)
    build_marker = root / "build" / "pyinstaller" / "mac" / "work" / "stale.txt"
    distribution_marker = root / "dist" / "mac" / "stale.txt"
    build_marker.parent.mkdir(parents=True)
    distribution_marker.parent.mkdir(parents=True)
    build_marker.write_text("stale", encoding="utf-8")
    distribution_marker.write_text("stale", encoding="utf-8")
    secret = root / ".env"
    personal = root / "config" / "personal.local.toml"
    secret.write_text("GEMINI_API_KEY=private-test-value\n", encoding="utf-8")
    personal.write_text("private_setting = true\n", encoding="utf-8")
    calls: list[list[str]] = []

    build_executable(
        BuildTarget.MAC,
        clean=True,
        current_platform="darwin",
        project_root=root,
        runner=calls.append,
    )

    arguments = calls[0]
    encoded_arguments = "\n".join(arguments)
    assert "--clean" in arguments
    assert not build_marker.exists()
    assert not distribution_marker.exists()
    assert secret.read_text(encoding="utf-8") == "GEMINI_API_KEY=private-test-value\n"
    assert personal.read_text(encoding="utf-8") == "private_setting = true\n"
    assert str(secret) not in encoded_arguments
    assert str(personal) not in encoded_arguments
    assert "private-test-value" not in encoded_arguments


def test_build_command_rejects_cross_compilation_before_running_pyinstaller(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    with pytest.raises(ExecutableBuildError, match="does not cross-compile"):
        build_executable(
            BuildTarget.WINDOWS,
            current_platform="darwin",
            project_root=tmp_path,
            runner=calls.append,
        )

    assert calls == []


def test_compile_script_requires_exactly_one_platform_flag() -> None:
    with pytest.raises(SystemExit) as missing:
        main([])
    with pytest.raises(SystemExit) as conflicting:
        main(["--windows", "--linux"])

    assert missing.value.code == 2
    assert conflicting.value.code == 2


def test_uv_compile_entrypoint_is_declared() -> None:
    with Path("pyproject.toml").open("rb") as handle:
        project = load(handle)

    assert project["project"]["scripts"]["compile"] == ("article_translator.build_executable:main")
