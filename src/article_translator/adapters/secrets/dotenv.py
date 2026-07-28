import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock

from pydantic import SecretStr

_GEMINI_API_KEY = "GEMINI_API_KEY"
_ASSIGNMENT_PATTERN = re.compile(
    rf"^[ \t]*(?:export[ \t]+)?{_GEMINI_API_KEY}[ \t]*=(?P<value>.*)\Z"
)
_UNQUOTED_COMMENT_PATTERN = re.compile(r"\s+#.*")
_FORBIDDEN_VALUE_CHARACTERS = frozenset({"\r", "\n", "\0"})


class InvalidSecretValueError(ValueError):
    """Raised when a value cannot safely be persisted as a dotenv secret."""


class DotenvSecretStore:
    """Persist only the Gemini API key in a local dotenv file.

    The store intentionally exposes only boolean status and write operations. It
    never returns the persisted key.
    """

    def __init__(self, path: Path = Path(".env")) -> None:
        self._path = path
        self._lock = RLock()

    def has_gemini_api_key(self) -> bool:
        """Report whether the effective saved definition has a nonblank value."""

        with self._lock:
            lines = self._read_lines()
            last_value: str | None = None
            found = False
            for line in lines:
                match = _match_assignment(line)
                if match is not None:
                    found = True
                    last_value = match.group("value")
            return found and last_value is not None and _is_nonblank_dotenv_value(last_value)

    def save_gemini_api_key(self, value: SecretStr | str) -> None:
        """Atomically save the key without retaining or returning its plaintext."""

        plaintext = value.get_secret_value() if isinstance(value, SecretStr) else value
        _validate_secret_value(plaintext)
        replacement = f"{_GEMINI_API_KEY}={_quote_dotenv_value(plaintext)}"

        with self._lock:
            lines = self._read_lines()
            updated_lines: list[str] = []
            replaced = False
            for line in lines:
                match = _match_assignment(line)
                if match is None:
                    updated_lines.append(line)
                elif not replaced:
                    updated_lines.append(replacement + _line_ending(line))
                    replaced = True

            if not replaced:
                newline = _preferred_newline(lines)
                if lines and not _line_ending(lines[-1]):
                    updated_lines[-1] += newline
                updated_lines.append(replacement + newline)

            self._atomic_write("".join(updated_lines))

    def clear_gemini_api_key(self) -> bool:
        """Remove every Gemini key definition, returning whether one was present."""

        with self._lock:
            if not self._path.is_file():
                return False

            lines = self._read_lines()
            updated_lines = [line for line in lines if _match_assignment(line) is None]
            if len(updated_lines) == len(lines):
                return False

            self._atomic_write("".join(updated_lines))
            return True

    def _read_lines(self) -> list[str]:
        if not self._path.is_file():
            return []
        return self._path.read_bytes().decode("utf-8").splitlines(keepends=True)

    def _atomic_write(self, content: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                if os.name == "posix":
                    os.fchmod(temporary.fileno(), 0o600)
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, self._path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def _match_assignment(line: str) -> re.Match[str] | None:
    body = line.removesuffix(_line_ending(line))
    return _ASSIGNMENT_PATTERN.fullmatch(body)


def _line_ending(line: str) -> str:
    for ending in ("\r\n", "\n", "\r"):
        if line.endswith(ending):
            return ending
    return ""


def _preferred_newline(lines: list[str]) -> str:
    for line in lines:
        if ending := _line_ending(line):
            return ending
    return "\n"


def _validate_secret_value(value: str) -> None:
    if not value.strip():
        raise InvalidSecretValueError("Gemini API key must not be blank")
    if any(character in value for character in _FORBIDDEN_VALUE_CHARACTERS):
        raise InvalidSecretValueError(
            "Gemini API key must not contain line breaks or NUL characters"
        )


def _quote_dotenv_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _is_nonblank_dotenv_value(raw_value: str) -> bool:
    value = raw_value.strip()
    if not value:
        return False
    if value[0] in {'"', "'"}:
        parsed = _parse_quoted_value(value, value[0])
        return parsed is not None and bool(parsed.strip())
    return bool(_UNQUOTED_COMMENT_PATTERN.sub("", value).rstrip())


def _parse_quoted_value(value: str, quote: str) -> str | None:
    parsed: list[str] = []
    escaped = False
    for character in value[1:]:
        if escaped:
            parsed.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == quote:
            return "".join(parsed)
        else:
            parsed.append(character)
    return None
