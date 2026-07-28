# ArticleTranslator

ArticleTranslator turns a PDF into matched page Markdown and page images, sends
one multimodal structured translation request per physical page, validates the
response with Pydantic, and compiles the canonical dataset into clean Markdown.

The repository is a backend-first scaffold with a working CLI, MarkItDown/PDFium
ingestion, Gemini adapter, resumable page checkpoints, canonical JSON output,
deterministic Markdown export, and a no-network test suite. The editorial UI,
revision workflow, and cross-page translation features are deliberately mapped
but not implemented yet.

## Pipeline

```text
PDF
  -> split into one-page PDF streams
  -> MarkItDown Markdown + matching rendered PNG per physical page
  -> one Gemini image+Markdown request per page
  -> strict structured-output validation and trusted provenance
  -> canonical output/document.json
  -> clean output/document.md
```

MarkItDown does not retain usable page boundaries when converting a complete PDF.
The ingestion adapter therefore splits first and runs MarkItDown once per page.
The rendered image remains the visual source of truth; extracted Markdown is
supplemental context, which matters for scans and pages with weak text layers.

## Quick start

The project uses `uv` exclusively for Python, environments, dependencies, scripts,
and developer tools.

```bash
uv sync --all-groups
cp .env.example .env
cp config/default.toml config/personal.local.toml
```

Put only the Gemini key in `.env`:

```dotenv
GEMINI_API_KEY=your-key-here
```

Configure every non-secret setting in the selected TOML file, then validate it:

```bash
uv run article-translator \
  --config config/personal.local.toml \
  show-config
```

Run the full pipeline:

```bash
uv run article-translator \
  --config config/personal.local.toml \
  run data/article.pdf
```

The configured external provider receives each page image and that page's complete
extracted Markdown. A 95-page PDF therefore makes 95 provider requests unless
matching page checkpoints already exist.

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

Use `--force` on `ingest`, `translate`, or `run` when intentionally replacing
artifacts. Without it, a checkpoint produced with different page content, prompt,
model, or translation config fails visibly instead of being reused.

## Configuration

[`config/default.toml`](config/default.toml) is the complete checked-in example and
contains:

- artifact location and page-image DPI;
- provider, model, stable API version, timeout, bounded attempts, and inline-request
  size guard;
- source/target languages;
- `faithful`, `balanced`, or `readable` translation style;
- custom translator instructions and glossary;
- name, citation, and qualitative uncertainty policies;
- Markdown page-comment and marginalia behavior.

Personal files should use `config/*.local.toml`, which Git ignores. Unknown keys,
missing settings, invalid enum values, and out-of-range settings fail before work
begins. Relative paths resolve from the selected TOML file's directory. Resolved
translation, provider, and export settings are stored in the job manifest; API
keys are never stored there.

`.env` is exclusively for `GEMINI_API_KEY`. Do not add model names, paths,
translation choices, or other non-secret behavior to environment variables.

## Canonical data and output artifacts

`document.json` is the source of truth for the future editor. It preserves the
source page, block type, source text, machine translation, uncertainty notes,
hashes, prompt/model provenance, and translation settings. `document.md` is a
derived presentation and can be regenerated at any time.

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
├── pages/
│   ├── 0001/
│   │   ├── translation.json
│   │   └── failure.json       # only while that page has failed
│   └── 0002/
│       └── ...
└── output/
    ├── document.json          # canonical editable dataset
    └── document.md            # clean derived export
```

`original_page_number` always means the 1-based physical position in the PDF.
`pdf_page_label` is label metadata embedded in the PDF, while
`detected_printed_page_label` is text visibly printed on the page. They are
separate because front matter often uses Roman numerals or no printed number.

Page translations are written atomically and independently. If page 43 fails,
pages 1–42 remain valid checkpoints and the next run resumes at page 43. A
checkpoint fingerprint includes both page hashes, the complete resolved
translation settings, provider/model, output-affecting provider semantics,
prompt content/version, and schema version. Operational provider settings are
persisted for provenance but do not invalidate successful checkpoints.

Preparation is transactional: extraction uses an immutable temporary snapshot of
the source and publishes a new `<preparation-id>` only after every page pair is
ready. The manifest then switches to that preparation atomically. Older
preparations are retained until a future explicit cleanup policy exists.

## Repository map

```text
.
├── config/default.toml             # every non-secret runtime setting
├── data/README.md                   # location for ignored local source PDFs
├── docs/
│   ├── architecture.md             # boundaries, contracts, artifact lifecycle
│   └── roadmap.md                  # backend, editorial, and UI phases
├── src/article_translator/
│   ├── cli.py                      # composition and command input only
│   ├── config.py                   # TOML validation + API-key loading
│   ├── domain/                     # Pydantic contracts, enums, errors, revisions
│   ├── ports/                      # extractor, provider, repository protocols
│   ├── application/                # prompts, fingerprints, orchestration, export
│   ├── adapters/
│   │   ├── extraction/             # MarkItDown + PDFium page pairing
│   │   ├── llm/                    # Gemini SDK boundary
│   │   └── storage/                # atomic filesystem artifacts
│   └── prompts/                    # versioned prompt resources
└── tests/
    ├── unit/
    ├── integration/                # local adapters; never network
    └── e2e/                        # complete fake-provider pipeline
```

The dependency direction is interface → application → domain/ports, with adapters
implementing the ports. A future web UI will call the same application service as
the CLI; it will not call Gemini or MarkItDown directly.

See [architecture.md](docs/architecture.md) for the detailed model and
[roadmap.md](docs/roadmap.md) for planned corrections, uncertain-term review, and
UI work.

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
execution. A future live test must be explicitly marked `live`, require an
opt-in flag as well as a key, and remain excluded from ordinary verification.

## Current limitations

- Translation is sequential and page-isolated; no adjacent-page context is sent.
- Block boundaries and types are model decisions and may vary between runs.
- Weak OCR/text extraction can still affect output, though the page image is also
  supplied.
- Uncertainty is qualitative because model token probabilities are unavailable;
  no confidence number is fabricated.
- Password-protected PDFs are not supported.
- Markdown preserves logical content and provenance, not exact PDF layout.
- `--force` can replace the current machine checkpoints today. Before editorial
  revisions are persisted, the next phase will introduce immutable translation
  runs; revisions are already modeled to target `(document, run, block)` rather
  than a block ID alone.
- The live Gemini path requires a real account/key and incurs provider cost; the
  automated suite verifies the adapter without making a live request.
