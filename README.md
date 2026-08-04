# ArticleTranslator

ArticleTranslator is a local tool for translating and reviewing page-preserving
PDFs. It turns each physical page into matched Markdown and an image, sends one
multimodal structured translation request per page, validates the response with
Pydantic, and compiles the canonical dataset into clean Markdown.

The repository includes the backend pipeline, CLI, and a small tabbed FastAPI
interface for colleagues. The interface provides PDF upload, per-job language
selection, authoritative term mappings, Gemini settings, side-by-side review,
append-only corrections, qualitative uncertainty review, and reviewed Markdown
download. It is a loopback-only workstation tool, not a remotely deployed or
multi-user application.

## Pipeline

```text
PDF
  -> split into one-page PDF streams
  -> MarkItDown Markdown + matching rendered PNG per physical page
  -> one Gemini image+Markdown request per page
  -> strict structured-output validation and trusted provenance
  -> canonical runs/<translation-run-id>/output/document.json
  -> clean runs/<translation-run-id>/output/document.md
```

MarkItDown does not retain usable page boundaries when converting a complete PDF.
The ingestion adapter therefore splits first and runs MarkItDown once per page.
The rendered image remains the visual source of truth; extracted Markdown is
supplemental context, which matters for scans and pages with weak text layers.

## Quick start: local interface

The project uses `uv` exclusively for Python, environments, dependencies, scripts,
and developer tools.

```bash
uv sync --all-groups
cp config/default.toml config/personal.local.toml
```

Start the local interface:

```bash
uv run article-translator \
  --config config/personal.local.toml \
  serve
```

Open `http://127.0.0.1:8000` unless the selected TOML file configures another
loopback host or port. The interface is organized as an internal workbench:

- **Translate** selects a laptop PDF and the input/output languages. The fields
  start from TOML defaults; the checked-in defaults are Danish to English.
- **Term mappings** lets you define authoritative source-term to target-term
  mappings for the next job. These are added to the configured glossary and sent
  with each page prompt.
- **Settings** selects a Gemini model from the TOML allowlist and a translation
  style. A Gemini key can be used for the current browser session, or **Save on
  this computer** can store it locally for later sessions. The saved value is
  never returned to or displayed by the interface.
- **Review** shows original and translated blocks side by side. Scroll the
  translated side; the original follows the same `original_page_number`. To keep
  long documents responsive, the browser mounts only the active page plus the
  configured number of neighboring pages on either side (two by default), then
  shifts that window as you scroll. Edits create revisions without changing
  machine output. Uncertain terms are visibly marked and can be corrected once
  or, when multiple unresolved model-annotated occurrences exist, all at once.
  The reviewed document can be downloaded as Markdown.

The configured provider receives one page image and that page's complete
extracted Markdown for each request. Files, canonical translations, and
editorial revisions remain in the configured local artifact directory.

The web server binds only to a configured loopback address. Its bounded job
queue and browser job IDs exist only in the running process. There is no
authentication, remote deployment support, or cancellation, so do not expose it
on a network.

## CLI workflow

To use the CLI instead, copy the secret template and put only the Gemini key in
the ignored local file:

```bash
cp .env.example .env
```

```dotenv
GEMINI_API_KEY=your-key-here
```

Validate the selected non-secret TOML configuration:

```bash
uv run article-translator \
  --config config/personal.local.toml \
  show-config
```

Run the full backend pipeline:

```bash
uv run article-translator \
  --config config/personal.local.toml \
  run data/article.pdf
```

A 95-page PDF makes 95 provider requests unless matching page checkpoints
already exist.

## Staged commands

The full run can be split into independently inspectable stages:

```bash
# No external API call
uv run article-translator --config config/personal.local.toml \
  ingest data/article.pdf

# Uses GEMINI_API_KEY and resumes matching checkpoints
uv run article-translator --config config/personal.local.toml \
  translate <configured-artifacts-dir>/<job-id>

# No external API call; regenerates Markdown from canonical JSON
uv run article-translator --config config/personal.local.toml \
  compile <configured-artifacts-dir>/<job-id>
```

Use `--force` on `ingest`, `translate`, or `run` when intentionally starting from
new prepared inputs or a new immutable translation run. Without it, a checkpoint
produced with different page content, prompt, model, or translation config fails
visibly instead of being reused.

## Configuration

[`config/default.toml`](config/default.toml) is the complete checked-in example and
contains:

- artifact location and page-image DPI;
- provider, default model, selectable-model allowlist, stable API version,
  timeout, bounded attempts, and inline-request size guard;
- source/target languages;
- `faithful`, `balanced`, or `readable` translation style;
- custom translator instructions and glossary;
- name, citation, and qualitative uncertainty policies;
- Markdown page-comment and marginalia behavior;
- loopback web host/port, upload/page/glossary limits, status polling, review-page
  context window, and bounded local concurrency.

Personal files should use `config/*.local.toml`, which Git ignores. Unknown keys,
missing settings, invalid enum values, and out-of-range settings fail before work
begins. Relative paths resolve from the selected TOML file's directory. Resolved
translation, provider, and export settings are stored in the job manifest; API
keys are never stored there.

The interface uses TOML values as defaults and limits. Input language, output
language, model, translation style, and term mappings are explicit per-job
selections; the resolved values are passed through the same application boundary
and persisted as run provenance. A model can be selected only when it appears in
`provider.gemini.selectable_models`.

`.env` is exclusively for `GEMINI_API_KEY`. Do not add model names, paths,
translation choices, or other non-secret behavior to environment variables. The
Settings checkbox labeled **Save on this computer** writes only this key to the
local `.env`; leaving it unchecked keeps the entered key in browser/process
memory for the current session and job. The API exposes only boolean key-status
flags, never the key value.

## Canonical data and output artifacts

The active run's `document.json` is the source of truth for the editor. It
preserves the translation-run identity, source page, block type, source text,
machine translation, uncertainty notes, hashes, prompt/model provenance, and
translation settings. `document.md` is a derived presentation and can be
regenerated at any time.

```text
<configured-artifacts-dir>/<job-id>/
├── manifest.json
├── prepared/
│   └── <preparation-id>/
│       └── pages/
│           ├── 0001/
│           │   ├── source.md
│           │   └── page.png
│           └── 0002/
│               └── ...
└── runs/
    ├── <translation-run-id>/
    │   ├── pages/
    │   │   ├── 0001/
    │   │   │   ├── translation.json
    │   │   │   └── failure.json   # only while that page has failed
    │   │   └── 0002/
    │   │       └── ...
    │   ├── revisions/
    │   │   └── p0001-b0001/
    │   │       ├── 0001.json      # append-only editorial revision
    │   │       └── 0002.json
    │   └── output/
    │       ├── document.json      # canonical machine dataset for this run
    │       └── document.md        # clean derived export for this run
    └── <older-translation-run-id>/
        └── ...                    # retained immutable machine artifacts
```

`original_page_number` always means the 1-based physical position in the PDF.
`pdf_page_label` is label metadata embedded in the PDF, while
`detected_printed_page_label` is text visibly printed on the page. They are
separate because front matter often uses Roman numerals or no printed number.

Page translations are written atomically and independently within one immutable
translation run. If page 43 fails, pages 1–42 remain valid checkpoints and a
retry resumes page 43 in the same run. Forced translation appends a new run
instead of overwriting prior successful bytes. A checkpoint fingerprint includes
both page hashes, the complete resolved translation settings, provider/model,
output-affecting provider semantics, prompt content/version, and schema version.
Operational provider settings are persisted for provenance but do not invalidate
successful checkpoints.

Editorial changes are separate from machine output. Each block revision is
created atomically under the active immutable run and includes its expected base
revision, effective text, review state, and resolved uncertainty IDs. The review
projection combines machine blocks with the latest valid revision. Downloading
reviewed Markdown projects that effective view without changing either canonical
`document.json` or the machine `document.md`.

Preparation is transactional: extraction uses an immutable temporary snapshot of
the source and publishes a new `<preparation-id>` only after every page pair is
ready. The manifest then switches to that preparation atomically. Older
preparations are retained until a future explicit cleanup policy exists. Forced
preparation preserves the ordered translation-run index but clears the active run
so the next translation receives a new identity.

Persisted core artifacts use schema version 2.0. Version 1.0 artifacts predate
translation-run identity and are rejected explicitly rather than being attached
to a guessed run.

## Repository map

```text
.
├── config/default.toml             # every non-secret runtime setting
├── data/README.md                   # location for ignored local source PDFs
├── docs/
│   ├── architecture.md             # boundaries, contracts, artifact lifecycle
│   └── roadmap.md                  # backend, editorial, and UI phases
├── src/article_translator/
│   ├── cli.py                      # command inputs and local server command
│   ├── composition.py              # shared adapter/application wiring
│   ├── config.py                   # TOML validation + API-key loading
│   ├── domain/                     # Pydantic contracts, editorial views, errors
│   ├── ports/                      # extractor, provider, artifact/revision ports
│   ├── application/                # pipeline, jobs, editorial service, export
│   ├── adapters/
│   │   ├── extraction/             # MarkItDown + PDFium page pairing
│   │   ├── llm/                    # Gemini SDK boundary
│   │   ├── secrets/                # narrow local GEMINI_API_KEY persistence
│   │   └── storage/                # atomic machine and revision artifacts
│   ├── interfaces/web/             # FastAPI endpoints + tabbed local UI
│   └── prompts/                    # versioned prompt resources
└── tests/
    ├── unit/
    ├── integration/                # local adapters; never network
    └── e2e/                        # complete fake-provider pipeline
```

The dependency direction is interface → application → domain/ports, with adapters
implementing the ports. The web interface and CLI share composition and
application services; browser code never calls Gemini or MarkItDown directly and
never reconstructs metadata from Markdown.

See [architecture.md](docs/architecture.md) for the detailed model and
[roadmap.md](docs/roadmap.md) for implemented scope and deliberately deferred
work.

## Development

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv lock --check
```

Normal tests never contact Gemini. They generate synthetic PDFs in temporary
directories, mock provider responses, and use fake providers for end-to-end
execution. Interface tests use an in-process FastAPI client and fake job/provider
boundaries. A future live test must be explicitly marked `live`, require an
opt-in flag as well as a key, and remain excluded from ordinary verification.

Gemini failures retain only a safe HTTP code/status and operator guidance. Raw
provider messages, responses, page content, and keys are not written to failure
artifacts or returned by the interface. For example, `400 INVALID_ARGUMENT`
usually requires checking the entered key and request compatibility, `403
PERMISSION_DENIED` requires checking key restrictions/API access, and `429
RESOURCE_EXHAUSTED` requires checking quota or rate limits.

The adapter supplies Gemini with the Pydantic contract through the SDK's current
JSON Schema field, then validates the returned JSON locally before persistence.

The live Gemini path has not been verified by the automated suite.

## Current limitations

- Translation is sequential and page-isolated; no adjacent-page context is sent.
- Block boundaries and types are model decisions and may vary between runs.
- Weak OCR/text extraction can still affect output, though the page image is also
  supplied.
- Uncertainty is qualitative because model token probabilities are unavailable;
  no confidence number is fabricated.
- Password-protected PDFs are not supported.
- Markdown preserves logical content and provenance, not exact PDF layout.
- `--force` starts a new immutable translation run; it never overwrites an older
  run's machine checkpoints.
- The browser job registry, progress state, and bounded queue are process-local;
  restarting the server loses those handles even though completed artifacts
  remain on disk.
- The interface has no authentication, remote deployment configuration, durable
  task queue, multi-process coordination, or job cancellation. It is deliberately
  restricted to loopback use.
- Review currently edits whole block text and review status. Block split/merge,
  type correction, editor identities, and a dedicated revision-history screen
  remain deferred.
- The live Gemini path requires a real account/key and incurs provider cost; the
  automated suite verifies the adapter boundary without making a live request.
