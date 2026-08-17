from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkstemp
from typing import cast


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    """Local paths used by repository and frozen executable entry points."""

    default_config: Path
    personal_config: Path
    secret_file: Path


def is_frozen() -> bool:
    """Return whether Python is running from a PyInstaller bundle."""

    return bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")


def application_data_directory(
    *,
    current_platform: str | None = None,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve a writable per-user application directory without creating it."""

    platform_name = current_platform or sys.platform
    environment_values = os.environ if environment is None else environment
    home_directory = Path.home() if home is None else home
    if platform_name == "win32":
        base = Path(
            environment_values.get(
                "LOCALAPPDATA",
                str(home_directory / "AppData" / "Local"),
            )
        )
        return base / "ArticleTranslator"
    if platform_name == "darwin":
        return home_directory / "Library" / "Application Support" / "ArticleTranslator"
    if platform_name.startswith("linux"):
        base = Path(environment_values.get("XDG_DATA_HOME", str(home_directory / ".local/share")))
        return base / "article-translator"
    raise RuntimeError(f"Unsupported executable platform: {platform_name}")


def materialize_default_config(source: Path, destination: Path) -> None:
    """Atomically refresh the app-owned default while preserving personal config."""

    source_bytes = source.read_bytes()
    if destination.is_file() and destination.read_bytes() == source_bytes:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = mkstemp(
        prefix=".default-",
        suffix=".toml.tmp",
        dir=destination.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(source_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def runtime_paths() -> RuntimePaths:
    """Resolve conventional repository paths or persistent frozen-app paths."""

    if not is_frozen():
        return RuntimePaths(
            default_config=Path("config/default.toml"),
            personal_config=Path("config/personal.local.toml"),
            secret_file=Path(".env"),
        )

    bundle_root = Path(cast(str, sys.__dict__["_MEIPASS"]))
    application_root = application_data_directory()
    default_config = application_root / "config" / "default.toml"
    materialize_default_config(bundle_root / "config" / "default.toml", default_config)
    return RuntimePaths(
        default_config=default_config,
        personal_config=application_root / "config" / "personal.local.toml",
        secret_file=application_root / ".env",
    )


def default_secret_path() -> Path:
    """Return the narrow GEMINI_API_KEY file for the active runtime."""

    return runtime_paths().secret_file
