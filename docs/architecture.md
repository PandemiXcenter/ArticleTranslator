# Architecture

## Design goals

The backend must preserve physical page provenance, isolate expensive provider
calls, validate everything persisted, resume safely after failure, and expose one
application boundary that both the CLI and a future editor can use.

The structured document is canonical. Markdown is a deterministic projection,
not a database.

## Dependency direction

```text
CLI now / web interface later
            |
            v
      application use cases
       /                 \
      v                   v
domain contracts        port protocols
                           ^
                           |
       extraction / Gemini / filesystem adapters
```

- `domain` imports the standard library and Pydantic only.
- `application` depends on domain contracts and port protocols.
- `adapters` own MarkItDown, pypdf, PDFium, Gemini, and filesystem details.
- `cli.py` is the composition root; it does not own business rules.
- Gemini SDK types never leave `adapters/llm/gemini.py`.
- A future API/UI calls `TranslationPipeline`, not concrete adapters.

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
published preparations are retained pending an explicit cleanup policy.

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

Uncertainty is not a confidence probability. The initial contract records:

- exact source term;
- proposed translation, when available;
- qualitative reason;
- alternatives.

This is useful to an editor without pretending an uncalibrated model self-report
is a probability.

## Checkpoints and failure behavior

The page fingerprint includes:

- Markdown artifact hash;
- image artifact hash;
- complete translation-settings snapshot;
- provider, model, and output-affecting semantic provider configuration;
- actual prompt hash and semantic prompt version;
- persisted schema version.

Any change invalidates reuse. Without `--force`, stale data stops visibly. Each
valid page is written atomically before the next request. A failed page gets a
small `failure.json`, the manifest enters `failed`, and earlier page translations
remain resumable. Successful retry removes that page's failure artifact.

Operational provider settings such as timeouts, retries, and the inline-size
guard are retained as page/manifest provenance but do not invalidate already
successful content. This allows an operator to raise a transport limit and resume
at the failed page. Model, API/schema semantics, prompt, translation policy, and
page inputs do invalidate content.

Compilation requires the canonical document containing every physical page.
There is intentionally no silent partial-success mode.

## Canonical dataset and Markdown projection

`output/document.json` contains ordered `PageTranslation` records, including the
page source Markdown and the immutable machine-produced blocks.

`output/document.md`:

- emits invisible physical-page comments when configured;
- renders titles/headings/lists/quotes/captions/footnotes logically;
- hides headers, footers, and printed page numbers by default;
- is byte-stable for the same canonical document and export config.

Export config changes do not require another provider call.

## Configuration boundary

`config/default.toml` is the only source of user-adjustable non-secret runtime
choices. Every section and field is required and loaded into strict nested
Pydantic models; there are no hidden Python fallbacks for omitted file settings.
Relative paths resolve from the selected TOML file's directory. CLI arguments
identify the operation and its input; they do not duplicate translation settings.

`.env` contains only `GEMINI_API_KEY`. Secrets must never enter TOML, manifests,
logs, tests, prompt snapshots, or provider responses stored on disk.

## Editorial and UI seam

Machine output must remain immutable. The next phase uses append-only
`BlockRevision` records:

```text
machine translated block
          |
          v
append-only editorial revisions
          |
          v
effective accepted document view
          |
          v
Markdown and other exporters
```

The current Phase 1 `--force` behavior may replace machine checkpoints, so no
revision is persisted yet. Phase 3 must first introduce immutable, coexisting
translation runs. A retranslation creates a new run; a retry completes failed
pages in the same run; published successful run artifacts are never overwritten.

A revision has a stable revision ID, document ID, translation-run ID, target block
ID, expected base revision, edited text, review status, editor/note, and
timestamp. Block IDs are unique only within a run, so the composite
`(document_id, translation_run_id, block_id)` is the revision target. Revisions
never carry automatically to a regenerated run. Optimistic base versions prevent
two editors from silently overwriting one another.

The filesystem repository can initially store revision JSON. A later SQLite
adapter can implement the same application port. A web interface belongs under a
future `interfaces/` package and consumes application queries/commands. Provider
SDK objects, filesystem paths, and prompt construction do not belong in UI code.

## Deliberate extension points

- New LLM: implement `PageTranslator`; keep the generated payload unchanged when
  possible.
- New persistence: implement `ArtifactRepository`.
- Improved extraction/rendering: implement `PageExtractor` and keep the 1:1 page
  invariant.
- New export format: project `DocumentTranslation`; do not parse Markdown.
- Progress UI: add an application event/progress port with page-started,
  page-completed, page-failed, and job-completed events.
- Cross-page context: explicitly version and fingerprint any neighboring context;
  never silently change the one-page request contract.
- Schema evolution: bump `SCHEMA_VERSION` and provide migration or clear
  incompatibility handling.
- Prompt evolution: update the prompt resource, bump `PROMPT_VERSION`, and add
  checkpoint-invalidation tests.
