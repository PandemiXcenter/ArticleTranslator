# ArticleTranslator

ArticleTranslator is a local tool for translating and reviewing page-preserving
PDFs. It turns each physical page into matched Markdown and an image, sends a
structured multimodal page-translation request, conditionally follows it with a
dedicated table-reconstruction request, validates both responses with Pydantic,
and compiles the canonical dataset into clean Markdown.

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
  -> one Gemini image+Markdown page-translation request
  -> when tagged: one batched table-reconstruction request for that page
  -> strict structured-output validation and per-pass trusted provenance
  -> canonical runs/<translation-run-id>/output/document.json
  -> clean runs/<translation-run-id>/output/document.md
```

MarkItDown does not retain usable page boundaries when converting a complete PDF.
The ingestion adapter therefore splits first and runs MarkItDown once per page.
The rendered image remains the visual source of truth; extracted Markdown is
supplemental context, which matters for scans and pages with weak text layers.

### Segment policy for historical pages

The model returns page regions in reading order rather than treating a page as
one undifferentiated transcription. A table or table-like region is any region
whose meaning depends on two-dimensional row and column alignment, including
unruled schedules, registers, statistical arrays, and aligned date, age, or count
series. The first pass emits each such region as an ordered, text-free tag. If a
page has one or more tags, the pipeline immediately sends one batched structured
follow-up containing the same page PNG, that page's complete MarkItDown/OCR, and
the first-pass segmentation. Only that region is reconstructed: surrounding
prose, headings, captions, and table notes remain separate translated blocks.

The follow-up produces one target-language GitHub-flavored Markdown table per
tag. It may apply bounded structural modernization: infer strongly supported
headers, fill down plainly implied labels, expand ditto marks or shorthand, and
turn sentence-like records into explicit rows. For example, “3 dead, on 3rd of
July” followed by “3, 4th” can become a `Date | Deaths` table. This freedom is
structural only; it must not invent or change facts, names, values, dates, units,
categories, totals, or rows. A reconstructed table has
`segment_handling="table_reconstruction"`, machine GFM Markdown in
`translated_text`, and no claimed `source_text`. Figures remain text-free manual
insertions for a reviewer. Markdown export keeps every table between the exact
surrounding blocks selected by first-pass reading order and prefixes it with an
invisible `table-placement: [H!]` anchor containing its physical page and block
identity. The table remains ordinary inline GFM rather than a floating object.

Footnotes are classified by function, not by font size, position, or share of the
page. A note may be a short marked passage below a rule, an unmarked continuation,
or nearly a whole page. Footnote blocks retain an optional printed marker and an
explicit page-continuation state, and their text is translated. Running heads,
page numbers, digitizer marks, catchwords, and printer gathering signatures are
not footnotes.

### Previous-page continuity context

Both passes can receive finalized machine translations from preceding physical
pages in the same run. `translation.previous_page_context_count` controls the
window from 0 through 10; the checked-in default is 2. The projection contains
page identity and ordered source/translated block content, but no prior page
images, editorial revisions, provider metadata, IDs, hashes, tokens, or
timestamps. It is delimited as untrusted, read-only context, and the model must
return only current-page content.

For body text, the primary model must populate a `paragraph_continuation` state.
On the next physical page it uses the prior projection to decide whether the
first body block continues the preceding page's final body block. A confirmed
continuation receives a pipeline-owned `continues_from_block_id` link. Canonical
blocks remain page-local and independently revisable, while Markdown export joins
the linked fragments into one paragraph and places the new physical-page comment
inline at the join. This cannot retroactively rewrite the preceding fragment.

The same context can resolve a word, sentence, heading, footnote, or table
continuation that enters the current page. The main contract is versioned as
`translate-page-v5`; the table contract is `reconstruct-tables-v1`.

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
- **Review** opens with a catalog of every valid completed translation run found
  under the configured artifact root. A stable translation-run ID reopens the
  same canonical artifacts after a browser or server restart. The complete
  translated document is mounted and scrollable; only the active physical
  page's original PNG is fetched and displayed alongside it. The last physical
  page visited is stored per run, so the catalog can offer **Continue from page
  X**. Edits create append-only revisions without changing machine output.
  Uncertain terms are visibly marked and can be corrected once or, when multiple
  unresolved model-annotated occurrences exist, all at once. **Uncertain terms**
  opens a complete unresolved list ordered from most to least occurrences and
  can jump to the first marked instance. The reviewed document can be downloaded
  as Markdown.

For each request, the configured provider receives one current-page image and
that page's complete extracted Markdown. A table-bearing page therefore makes a
second request with the same current-page evidence. Requests can also include the
configured read-only projection of prior finalized machine translations. Source
files, canonical translations, and editorial revisions remain in the configured
local artifact directory.

The web server binds only to a configured loopback address. A newly submitted
translation receives a temporary browser job ID; that alias, its progress record,
and the bounded execution queue exist only in the running process. Completed
runs are different: the server discovers their canonical artifacts at startup
and exposes them by stable translation-run ID in Review. This is filesystem
discovery, not a database or durable task queue. There is no authentication,
remote deployment support, or cancellation, so do not expose the server on a
network.

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

A 95-page PDF makes 95 primary provider requests plus one table request for each
table-bearing page, unless matching checkpoints already exist. Multiple tables
on one page are batched into that page's single follow-up.

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
- finalized previous-page translation context count (0–10, default 2);
- Markdown page-comment and marginalia behavior;
- loopback web host/port, upload/page/glossary limits, status polling, and bounded
  local concurrency.

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
preserves the translation-run identity, source page, block type, source text for
translated regions, machine translation or reconstructed table Markdown,
uncertainty notes, hashes, prompt/model provenance, and translation settings.
`document.md` is a derived presentation and can be regenerated at any time.

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
    │   ├── review/
    │   │   └── position.json      # mutable last physical page visited
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

Page translations are written atomically within one immutable translation run.
If a first pass tags tables, its validated `translation.json` is written before
the table call. It is an intermediate stage checkpoint until every tagged table
has been reconstructed. If that follow-up fails, `failure.json` records the
`table_reconstruction` stage and a retry repeats only pass two; it does not repay
for or potentially change the first-pass segmentation. If page 43 fails, earlier
finalized page checkpoints remain resumable in the same run. Forced translation
appends a new run instead of overwriting prior successful bytes.

The first-pass fingerprint includes both page hashes, complete resolved
translation settings, provider/model and output-affecting provider semantics,
the exact main prompt and prior-page context, the main/table prompt versions and
table prompt contract hash, and schema version. The table fingerprint also binds
the exact table prompt, first-pass fingerprint, and ordered table targets. Main
and table provider response IDs, token usage, prompt versions, and fingerprints
are persisted separately as bounded provenance. Operational timeout/retry/size
settings remain provenance but do not invalidate successful checkpoints.

Editorial changes are separate from machine output. Each block revision is
created atomically under the active immutable run and includes its expected base
revision, effective text, review state, and resolved uncertainty IDs. The review
projection combines machine blocks with the latest valid revision. Downloading
reviewed Markdown projects that effective view without changing either canonical
`document.json` or the machine `document.md`. The interface labels whether each
effective block is still the machine translation, was reviewed unchanged, or is
the result of a manual revision. The run-scoped `review/position.json` sidecar
stores only the current physical-page cursor; it does not alter the translation
or revision history.

Preparation is transactional: extraction uses an immutable temporary snapshot of
the source and publishes a new `<preparation-id>` only after every page pair is
ready. The manifest then switches to that preparation atomically. Older
preparations are retained until a future explicit cleanup policy exists. Forced
preparation preserves the ordered translation-run index but clears the active run
so the next translation receives a new identity.

New core artifacts use schema version 4.0 for table-reconstruction handling and
per-pass provenance. The filesystem reader validates and upgrades supported
schema 2.0 and 3.0 artifacts in memory without rewriting immutable files. Schema
2.0 translated tables remain explicitly marked legacy translated tables; schema
3.0 manual table placeholders remain explicitly marked legacy manual tables.
Neither compatibility form is silently reinterpreted as a schema 4.0
reconstruction. A translated figure is not valid schema 2.0 legacy data. Version
1.0 artifacts predate translation-run identity and remain explicitly rejected
rather than being attached to a guessed run.

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

- Translation is sequential. Only finalized preceding same-run page translations
  are available as context; no following-page image/text is sent. The next page
  can confirm and link a paragraph continuation, but it cannot rewrite the
  preceding page's already persisted fragment.
- Block boundaries and types are model decisions and may vary between runs.
- Reconstructed tables are model-produced modern GFM structures, not claimed
  cell-for-cell source transcriptions, and still require editorial review.
- Weak OCR/text extraction can still affect output, though the page image is also
  supplied.
- Uncertainty is qualitative because model token probabilities are unavailable;
  no confidence number is fabricated.
- Password-protected PDFs are not supported.
- Markdown preserves logical content and provenance, not exact PDF layout.
- `--force` starts a new immutable translation run; it never overwrites an older
  run's machine checkpoints.
- In-progress browser job aliases, progress state, and the bounded queue are
  process-local and are lost on restart. Valid completed runs remain available
  through the filesystem-backed Review catalog and their stable run IDs.
- The interface has no authentication, remote deployment configuration, durable
  task queue, multi-process coordination, or job cancellation. It is deliberately
  restricted to loopback use.
- Review currently edits whole block text and review status. Block split/merge,
  type correction, editor identities, and a dedicated revision-history screen
  remain deferred.
- The live Gemini path requires a real account/key and incurs provider cost; the
  automated suite verifies the adapter boundary without making a live request.
