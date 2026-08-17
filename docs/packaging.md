# Native executable packaging

ArticleTranslator uses PyInstaller to build one native executable containing the
Python runtime, application modules, local web assets, prompt resources,
MarkItDown, Magika data, and PDFium binaries. PyInstaller is managed in the uv
development dependency group; do not install or invoke it outside uv.

## Build commands

Run exactly one command on the matching operating system:

```bash
uv run compile --windows
uv run compile --mac
uv run compile --linux
```

For a distributable rebuild, add `--clean`:

```bash
uv run compile --mac --clean
```

Clean mode deletes only `build/pyinstaller/<platform>/` and
`dist/<platform>/`, asks PyInstaller to discard its analysis cache, and bundles
the checked-in `config/default.toml`. It never bundles or deletes the developer's
`.env`, `config/personal.local.toml`, artifacts, source PDFs, or saved review data.
Non-clean builds use the same private-data exclusion; the flag additionally
guarantees a cache-free target rebuild.

PyInstaller is a native packager, not a cross-compiler. A Windows executable must
be built on Windows, a macOS executable on macOS, and a Linux executable on Linux.
The command rejects a mismatched target instead of producing a misleading
artifact. Linux builds should use the oldest glibc-based distribution that the
team intends to support. macOS builds use the architecture of the uv-managed
Python interpreter; build separately when both Apple Silicon and Intel binaries
are required.

The output is:

```text
dist/windows/ArticleTranslator.exe
dist/mac/ArticleTranslator
dist/linux/ArticleTranslator
```

`build/pyinstaller/<platform>/` contains disposable intermediate analysis and
specification files. Both `build/` and `dist/` are ignored by Git.

## Executable behavior

Running the executable without arguments starts the same loopback FastAPI
workbench as `uv run app`:

```bash
./dist/mac/ArticleTranslator
```

After the server is ready, the launcher opens the configured loopback URL in the
operating system's default browser. Set `web.open_browser_on_start = false` in a
personal TOML file to suppress this behavior. A missing or unavailable desktop
browser does not stop the server; its URL can still be opened manually.

Passing arguments exposes the complete existing CLI through the same file:

```bash
./dist/mac/ArticleTranslator --help
./dist/mac/ArticleTranslator run /path/to/article.pdf
./dist/mac/ArticleTranslator serve
```

The bundled `config/default.toml` is refreshed into an app-owned per-user data
directory on launch. A colleague may copy it to `config/personal.local.toml` in
that directory for durable overrides; the application never overwrites the
personal file. Artifacts and the narrow `.env` containing only `GEMINI_API_KEY`
remain in the same persistent application-data root:

- Windows: `%LOCALAPPDATA%\ArticleTranslator`
- macOS: `~/Library/Application Support/ArticleTranslator`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/article-translator`

The one-file executable extracts its bundled runtime into a temporary directory
while running, as PyInstaller one-file applications normally do. Canonical jobs,
settings, and secrets never use that temporary directory.

Clean build mode affects only the developer's generated build directories. It
does not make the resulting application stateless: each recipient can still save
their key, personal TOML, translation jobs, and editorial metadata in their own
application-data directory.

## External requirements and verification

The executable does not bundle a TeX distribution. Reviewed Markdown, text, and
LaTeX source work without TeX; reviewed PDF export still requires the configured
`xelatex` executable on the workstation. This keeps the application artifact
bounded and preserves the existing constrained XeLaTeX adapter.

After each native build, verify at minimum:

1. `ArticleTranslator --help` starts without an import error.
2. Running without arguments serves `GET /` and `/api/config` on the configured
   loopback address.
3. A small synthetic PDF can be prepared without contacting Gemini.
4. The Settings API-key path writes only to the native application-data root.
5. Live Gemini translation is tested only with explicit opt-in and a real key.

Unsigned executables may trigger operating-system trust warnings when shared.
Signing and notarization are release/distribution concerns and are intentionally
separate from this reproducible local build command.
