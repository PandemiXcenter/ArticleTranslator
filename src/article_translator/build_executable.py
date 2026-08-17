from __future__ import annotations

import argparse
import os
import shutil
import sys
from collections.abc import Callable, Sequence
from enum import StrEnum
from importlib import import_module
from pathlib import Path
from typing import cast


class BuildTarget(StrEnum):
    WINDOWS = "windows"
    MAC = "mac"
    LINUX = "linux"


class ExecutableBuildError(RuntimeError):
    """Raised before PyInstaller when a requested build cannot be produced safely."""


PyInstallerRunner = Callable[[list[str]], None]


def native_build_target(current_platform: str = sys.platform) -> BuildTarget:
    if current_platform == "win32":
        return BuildTarget.WINDOWS
    if current_platform == "darwin":
        return BuildTarget.MAC
    if current_platform.startswith("linux"):
        return BuildTarget.LINUX
    raise ExecutableBuildError(f"Unsupported build platform: {current_platform}")


def build_executable(
    target: BuildTarget,
    *,
    clean: bool = False,
    current_platform: str = sys.platform,
    project_root: Path | None = None,
    runner: PyInstallerRunner | None = None,
) -> Path:
    """Build one native single-file executable with all application resources."""

    native_target = native_build_target(current_platform)
    if target is not native_target:
        raise ExecutableBuildError(
            f"PyInstaller does not cross-compile: --{target.value} must run on {target.value}. "
            f"This host can build only --{native_target.value}."
        )

    root = (project_root or Path(__file__).resolve().parents[2]).resolve()
    entrypoint = root / "src" / "article_translator" / "executable.py"
    default_config = root / "config" / "default.toml"
    for required_path in (root / "pyproject.toml", entrypoint, default_config):
        if not required_path.is_file():
            raise ExecutableBuildError(f"Required build input does not exist: {required_path}")

    build_root = root / "build" / "pyinstaller" / target.value
    distribution_root = root / "dist" / target.value
    if clean:
        _remove_prior_build(root, build_root, distribution_root)
    specification_root = build_root / "spec"
    work_root = build_root / "work"
    distribution_root.mkdir(parents=True, exist_ok=True)
    specification_root.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)

    arguments = [
        "--noconfirm",
        "--onefile",
        "--name",
        "ArticleTranslator",
        "--distpath",
        str(distribution_root),
        "--workpath",
        str(work_root),
        "--specpath",
        str(specification_root),
        "--paths",
        str(root / "src"),
        "--add-data",
        f"{default_config}{os.pathsep}config",
        "--collect-data",
        "article_translator",
        "--collect-all",
        "markitdown",
        "--collect-all",
        "magika",
        "--collect-all",
        "pypdfium2",
        "--copy-metadata",
        "markitdown",
        "--hidden-import",
        "uvicorn.logging",
        "--hidden-import",
        "uvicorn.loops.auto",
        "--hidden-import",
        "uvicorn.protocols.http.auto",
        "--hidden-import",
        "uvicorn.protocols.websockets.auto",
        "--hidden-import",
        "uvicorn.lifespan.on",
        str(entrypoint),
    ]
    if clean:
        arguments.insert(1, "--clean")
    active_runner = runner or _pyinstaller_runner()
    active_runner(arguments)

    executable_name = (
        "ArticleTranslator.exe" if target is BuildTarget.WINDOWS else "ArticleTranslator"
    )
    executable = distribution_root / executable_name
    if runner is None and not executable.is_file():
        raise ExecutableBuildError(f"PyInstaller completed without producing {executable}")
    return executable


def _remove_prior_build(root: Path, *paths: Path) -> None:
    """Remove only target-scoped generated paths for an explicit clean build."""

    for path in paths:
        resolved = path.resolve()
        if root not in resolved.parents or path.is_symlink():
            raise ExecutableBuildError(f"Refusing to clean unsafe build path: {path}")
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def _pyinstaller_runner() -> PyInstallerRunner:
    module = import_module("PyInstaller.__main__")
    return cast(PyInstallerRunner, module.__dict__["run"])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="compile",
        description="Build one native ArticleTranslator executable with PyInstaller.",
    )
    targets = parser.add_mutually_exclusive_group(required=True)
    targets.add_argument("--windows", action="store_const", dest="target", const="windows")
    targets.add_argument("--mac", action="store_const", dest="target", const="mac")
    targets.add_argument("--linux", action="store_const", dest="target", const="linux")
    parser.add_argument(
        "--clean",
        action="store_true",
        help=(
            "Discard this target's prior build cache and rebuild with only the checked-in "
            "default config; local keys, personal config, and artifacts are never bundled."
        ),
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> None:
    parser = _parser()
    parsed = parser.parse_args(arguments)
    try:
        output = build_executable(BuildTarget(parsed.target), clean=parsed.clean)
    except ExecutableBuildError as exc:
        parser.error(str(exc))
    print(f"Built {output}")


if __name__ == "__main__":
    main()
