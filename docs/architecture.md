# Architecture

## Design goals

The system must preserve physical page provenance, isolate expensive provider
calls, validate everything persisted, resume safely after failure, and expose
application boundaries that both the CLI and the implemented local editor use.

The machine `DocumentTranslation` is canonical for its immutable translation
run. Editorial revisions are append-only. Machine and reviewed Markdown are
projections, not databases.

## Dependency direction

```text
CLI commands                  browser assets
      \                            |
       \                       FastAPI interface
        \                          /
         shared composition + application use cases
                  /                         \
                 v                           v
         domain contracts                 port protocols
                                               ^
                                               |
                      extraction / Gemini / filesystem / secret adapters
```

- `domain` imports the standard library and Pydantic only.
- `application` depends on domain contracts and port protocols.
- `adapters` own MarkItDown, pypdf, PDFium, Gemini, and filesystem details.
- `composition.py` wires shared concrete adapters.
- `cli.py` owns command inputs and starts the local server; it does not own
  business rules.
- `interfaces/web/` owns HTTP schemas, CSRF/upload handling, JSON projections, and
  the static tabbed interface. It calls application services and never reads
  canonical metadata from Markdown.
- Gemini SDK types never leave `adapters/llm/gemini.py`.
- Browser code never calls Gemini, MarkItDown, or filesystem adapters directly.

## Local web execution

The `serve` command constructs the same pipeline and provider adapter used by the
CLI, then starts FastAPI/Uvicorn on the configured loopback host and port:

```bash
uv run article-translator --config config/personal.local.toml serve
```

The server boundary is intentionally small:

1. `GET /api/config` returns safe non-secret defaults, limits, selectable models,
   and boolean API-key status.
2. `POST /api/jobs` streams a multipart PDF into an opaque staging directory,
   checks configured size limits, a safe `.pdf` display name, and the PDF header,
   then resolves strict per-job settings and term mappings.
3. `WebJobManager` records an opaque browser job ID and runs prepare, translate,
   and compile in a bounded `ThreadPoolExecutor`.
4. The browser polls process-local progress. A ready job can be projected through
   `EditorialService` for review, revisions, uncertainty replacement, and
   reviewed Markdown download.
5. The staged upload and any ephemeral backend key are discarded after the
   background run. Durable artifacts remain under the configured artifact root.

The manager, queue, progress records, and browser job IDs exist only in one
server process. Restarting the server loses those handles. Completed machine
artifacts and revisions remain on disk, but there is no job discovery screen yet.
The server has no authentication, durable queue, cancellation, multi-process
coordination, or remote deployment contract. `WebConfig.host` accepts loopback
addresses only.

Mutating routes require a same-site CSRF cookie/header pair. Responses use
no-store and basic browser hardening headers. These controls reduce accidental
local misuse; they do not turn the interface into a remotely safe multi-user
service.

## Preparation

MarkItDown 0.1.6 processes individual PDF pages internally but joins them with
ordinary blank lines; its `extract_pages=True` keyword is accepted and ignored.
Preparation therefore:

1. copies the source into a short-lived immutable snapshot and hashes it;
2. establishes the physical page count with pypdf;
3. creates an in-memory one-page PDF for every physical page;
4. sends each stream through MarkItDown;
5. renders that same indexed page to PNG with PDFium;
6. stores the Markdown/image pair and hashes in a staging directory;
7. refuses to continue if the independent readers disagree about page count;
8. atomically publishes `prepared/<preparation-id>` and then switches the
   manifest to it before any provider request.

An empty or failed Markdown extraction does not erase the page. Its status and
warning are retained, and the image can still provide the page content.
Failed preparation leaves the prior manifest and preparation untouched. Old
published preparations are retained pending an explicit cleanup policy. A
successful forced preparation preserves the manifest's ordered translation-run
index, resets its active run, and leaves every prior run directory untouched.

## Domain ownership

The language model owns only fields it can observe or infer:

```text
block reading order
block type
source-text segmentation
translated text
heading level
visibly printed page label
qualitative uncertainty terms/reasons/alternatives
```

The pipeline owns trusted provenance:

```text
document/job identity
physical original page number
block IDs
PDF page-label metadata
artifact paths and hashes
provider/model and response identity
prompt/schema versions
token usage and timestamps
checkpoint fingerprint
```

The provider-facing `GeneratedPagePayload` is intentionally smaller than
`PageTranslation`. Pydantic forbids unknown fields and validates contiguous block
order before the application adds trusted metadata.

## Page-number meanings

Never overload the phrase “page number”:

- `original_page_number`: 1-based physical position in the source PDF;
- `pdf_page_label`: optional label embedded in the PDF page tree;
- `detected_printed_page_label`: optional label visibly printed and detected by
  the model;
- a block of type `page_number`: printed page-number text retained with the other
  blocks, normally hidden from clean Markdown.

## Translation and uncertainty

Every request contains exactly one page PNG, that page's complete Markdown, and
the fully resolved translation settings. The prompt treats Markdown as untrusted
document content, describes all three translation styles, and uses Gemini
structured output with the Pydantic payload schema.

Configured glossary entries and the per-job Term mappings table are merged into
the resolved `TranslationSettings`. The prompt states that these source-to-target
mappings are authoritative; they are not suggestions inferred by the model.
Input language, output language, model, and style can be selected for one web
job. Their initial values and the selectable model allowlist come from TOML, and
the resolved values participate in run provenance and checkpoint identity.

Uncertainty is not a confidence probability. The initial contract records:

- exact source term;
- proposed translation, when available;
- qualitative reason;
- alternatives.

This is useful to an editor without pretending an uncalibrated model self-report
is a probability.

The editorial service derives stable uncertainty occurrence IDs and exact Unicode
code-point offsets when a proposed translation can be located in effective text.
Replacement uses only those annotated offsets. “Translate All” groups unresolved
occurrences by the structured term identity and is allowed only when more than
one annotated match exists; it never performs unrestricted global string
replacement. If an uncertainty cannot be aligned, the review projection retains
its reason and alternatives as a whole-block fallback rather than inventing an
offset.

## Checkpoints and failure behavior

The page fingerprint includes:

- Markdown artifact hash;
- image artifact hash;
- complete translation-settings snapshot;
- provider, model, and output-affecting semantic provider configuration;
- actual prompt hash and semantic prompt version;
- persisted schema version.

Any change invalidates reuse. Without `--force`, stale data stops visibly. The
first translation appends a UUID run ID to the manifest and makes it active.
Each valid page is written atomically below `runs/<run-id>/` before the next
request. A failed page gets a small run-scoped `failure.json`, the manifest
enters `failed`, and earlier page translations remain resumable in the same run.
Successful retry removes that page's failure artifact. Forced translation
appends and activates a new run, leaving older successful run bytes untouched.

Operational provider settings such as timeouts, retries, and the inline-size
guard are retained as page/manifest provenance but do not invalidate already
successful content. This allows an operator to raise a transport limit and resume
at the failed page. Model, API/schema semantics, prompt, translation policy, and
page inputs do invalidate content.

Compilation requires the canonical document containing every physical page.
There is intentionally no silent partial-success mode.

## Canonical dataset and Markdown projection

`runs/<run-id>/output/document.json` contains ordered `PageTranslation` records,
including the shared translation-run ID, page source Markdown, and immutable
machine-produced blocks.

`runs/<run-id>/output/document.md`:

- emits invisible physical-page comments when configured;
- renders titles/headings/lists/quotes/captions/footnotes logically;
- hides headers, footers, and printed page numbers by default;
- is byte-stable for the same canonical document and export config.

Export config changes do not require another provider call.

## Configuration boundary

`config/default.toml` is the complete source of non-secret runtime defaults,
limits, and selectable choices. Every section and field is required and loaded
into strict nested Pydantic models; there are no hidden Python fallbacks for
omitted file settings. Relative paths resolve from the selected TOML file's
directory. CLI arguments identify the operation and its input; they do not
duplicate translation settings.

The local interface uses those settings as defaults and limits. Explicit
per-job language, model, style, and term-mapping inputs are validated at the HTTP
boundary and applied to a copied runtime config. The default Gemini model must be
present in `selectable_models`; the interface rejects any model outside that
allowlist.

`.env` contains only `GEMINI_API_KEY`. The Settings interface labels persistence
as **Save on this computer**. Checked persistence writes the key through a narrow
local secret adapter; unchecked use keeps it ephemeral for the current browser
session/job. The API returns only key-availability booleans and never returns,
prefills, logs, or persists the key in run artifacts. Secrets must never enter
TOML, manifests, logs, tests, prompt snapshots, or stored provider responses.

## Editorial model

Machine output is immutable and coexists in UUID-keyed translation runs. The
manifest keeps an ordered `translation_run_ids` index and a nullable active
`translation_run_id`. A retry completes the active failed/in-progress run;
retranslation creates a new run; compilation reads only the active run.

Editorial correction uses append-only `BlockRevision` records:

```text
machine translated block
          |
          v
append-only editorial revisions
          |
          v
effective reviewed document view
          |
          v
Markdown and other exporters
```

A revision has a stable revision ID, document ID, translation-run ID, target block
ID, expected base revision, edited text, review status, editor/note, and
timestamp. Block IDs are unique only within a run, so the composite
`(document_id, translation_run_id, block_id)` is the revision target. Revisions
never carry automatically to a regenerated run. Optimistic base versions prevent
two editors from silently overwriting one another.

The filesystem repository stores each history as:

```text
runs/<translation-run-id>/revisions/<block-id>/<revision-number>.json
```

Creation is atomic and refuses to replace an existing revision. Reading validates
scope, filenames, and contiguous base/revision numbers. `EditorialService`
projects a strict `ReviewDocument` by combining immutable machine text with the
latest valid revision for each block.

The reviewed Markdown route compiles this effective view without changing
`runs/<run-id>/output/document.json` or its machine `document.md`. The current
policy uses the latest effective revision whether its status is `in_review`,
`accepted`, or `needs_work`; an accepted-only export policy is not implemented.

## Tabbed colleague interface

The browser interface is intentionally operational rather than promotional:

- **Translate** selects a PDF and per-job input/output languages. TOML supplies
  the defaults; the checked-in direction is Danish to English.
- **Term mappings** supplies authoritative archaic or specialist translations
  for the next job.
- **Settings** selects an allowlisted Gemini model, translation style, and key
  persistence behavior.
- **Review** renders source and effective translated blocks side by side. Only
  the translated pane is user-scrollable; its current
  `original_page_number` drives the corresponding source page. The browser keeps
  the full strict review projection indexed in memory but mounts only the active
  page plus `web.review_context_pages` pages on each side. At a rendered boundary
  it shifts the window while anchoring the active page and retaining unsaved
  drafts. Generated block, uncertainty, editor, and mapping controls use stable
  container-level delegated handlers rather than one listener per element.
  Editing and validation append revisions. Structured uncertainties are
  highlighted and expose one/all replacement according to the service contract.
  Reviewed Markdown is downloaded from the effective document view.

The interface does not expose artifact paths, raw provider objects, or raw
responses. It does not parse `document.md` to rebuild pages or blocks.

## Deliberate extension points

- New LLM: implement `PageTranslator`; keep the generated payload unchanged when
  possible.
- New persistence: implement `ArtifactRepository`.
- New revision persistence: implement `RevisionRepository` and preserve
  append-only/optimistic semantics.
- Improved extraction/rendering: implement `PageExtractor` and keep the 1:1 page
  invariant.
- New export format: project `DocumentTranslation`; do not parse Markdown.
- Durable/multi-process jobs: replace the process-local manager behind an
  application port, then add discovery, cancellation, locking, and recovery
  semantics before changing the interface.
- Remote use: design authentication, authorization, CSRF/origin policy, encrypted
  secret storage, retention, and deployment explicitly before allowing a
  non-loopback host.
- Cross-page context: explicitly version and fingerprint any neighboring context;
  never silently change the one-page request contract.
- Schema evolution: bump `SCHEMA_VERSION` and provide migration or clear
  incompatibility handling.
- Prompt evolution: update the prompt resource, bump `PROMPT_VERSION`, and add
  checkpoint-invalidation tests.

Core persisted artifacts use schema version 2.0 because translation-run identity
is required at every page/document boundary. Version 1.0 manifests and
translations are rejected explicitly; no run identity is inferred for them.
