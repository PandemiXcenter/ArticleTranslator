# AGENTS.md

These instructions apply to the entire repository.

## Mission and current scope

ArticleTranslator is a page-preserving PDF translation pipeline with a local
FastAPI workbench for colleagues. The active architecture is:

```text
PDF -> paired per-page Markdown + PNG -> structured page translation pass
    -> conditional batched table reconstruction pass for that page
    -> canonical validated document JSON -> immutable translation run
    -> append-only editorial revisions -> reviewed LaTeX/PDF/Markdown/text projections
```

The web interface is deliberately a small loopback-only internal tool. Preserve
its Translate, Term mappings, Settings, and Articles tabs, but do not add a
database, remote deployment stack, authentication scheme, frontend framework, or
durable task queue unless a task explicitly expands that phase.

The implementation is usable scaffolding, not a claim that live translation
quality or production operations are complete. Never describe an untested live
provider path as verified.

## Start every task this way

1. Read this file, `README.md`, `pyproject.toml`, and the relevant document under
   `docs/`.
2. Run `git status --short` and preserve all unrelated or pre-existing work.
3. Inspect the nearest implementation and its tests before proposing a new
   abstraction.
4. Identify which layer owns the change: domain, application/port, adapter,
   composition/config, interface, or documentation.
5. Make the smallest coherent change in that layer and add proportional tests.
6. Run the relevant quality gates listed below.
7. Report changed behavior, verification performed, and anything still
   unverified—especially live Gemini behavior.

Do not delete or rewrite user PDFs, generated artifacts, or unrelated changes.
Files in `data/` are local user inputs, not disposable fixtures.

## Repository ownership map

```text
config/default.toml
    Complete checked-in non-secret configuration.

src/article_translator/domain/
    Pydantic models, enums, errors, machine schema, editorial revisions/review
    projections.
    Imports only the standard library and Pydantic.

src/article_translator/ports/
    Provider, extraction, artifact, and editorial-revision Protocols. No SDK or
    web implementations.

src/article_translator/application/
    Provider-neutral orchestration, prompt assembly, fingerprints, local job
    lifecycle, editorial commands/queries, and exporters.
    Depends on domain and ports, never concrete infrastructure.

src/article_translator/adapters/extraction/
    pypdf page splitting, MarkItDown conversion, PDFium rendering.

src/article_translator/adapters/export/
    Narrow local XeLaTeX compilation. Generated content remains escaped and
    shell escape remains disabled.

src/article_translator/adapters/llm/
    Gemini SDK boundary. Google SDK types must not escape this package.

src/article_translator/adapters/storage/
    Filesystem machine-artifact and append-only revision implementation.

src/article_translator/adapters/secrets/
    Narrow local `GEMINI_API_KEY` persistence. No other setting or secret.

src/article_translator/prompts/
    Versioned prompt resources. Prompt behavior is part of cache identity.

src/article_translator/interfaces/web/
    FastAPI transport, strict request schemas, and small static colleague UI.
    Calls application services; owns HTTP/CSRF/upload/presentation concerns.

src/article_translator/composition.py
    Shared wiring for CLI and web entry points.

src/article_translator/cli.py
    Command inputs plus `serve` and the zero-argument `uv run app` entry points.
    No pipeline/business rules.

src/article_translator/build_executable.py, executable.py, runtime.py
    Native PyInstaller build orchestration, frozen entry dispatch, and persistent
    per-user runtime paths. No pipeline/business rules.

tests/unit/
    Pure domain/application/config and mocked-adapter behavior.

tests/integration/
    Local filesystem/PDF integrations. Never external network.

tests/e2e/
    Full application execution with fake providers.
```

The web UI must call application services. It must not call Gemini/MarkItDown
directly, accept filesystem paths from the browser, or reconstruct metadata from
Markdown.

## Non-negotiable data invariants

- `runs/<translation-run-id>/output/document.json` is canonical for that
  immutable run. Its primary compiled sibling `document.tex` and additional
  `document.md` projection are reproducible derivatives and must never be parsed
  to recover metadata.
- `original_page_number` is the 1-based physical position in the PDF.
- `pdf_page_label` is PDF metadata. `detected_printed_page_label` is visible page
  text. A `page_number` block is retained content. Never merge these meanings.
- Split the PDF before MarkItDown. Whole-document MarkItDown output does not
  preserve reliable page boundaries, and `extract_pages=True` is ignored by the
  installed MarkItDown version.
- Pair Markdown and image from the exact same physical page. Refuse provider work
  when independent page counts disagree.
- When a rerun changes `extraction.image_dpi`, automatically publish a new
  preparation and clear the active run while preserving prior immutable runs. Do
  not require `--force` merely to regenerate page renders at a different DPI.
- Treat the rendered image as primary page evidence and MarkItDown Markdown as
  supplemental context. Empty Markdown must not remove an image page.
- Every provider request contains exactly one page image and the complete
  Markdown for that page. Prior-page continuity context never adds another page
  image or replaces the complete current-page Markdown.
- The first model pass owns segmentation, order, block type, translated-region
  source/text, optional printed label, qualitative uncertainty, segment
  continuation, and classification-review flags only. It emits table,
  table-like, and figure regions as ordered text-free tags. Never smuggle their
  contents into another first-pass block.
- A newly starting model footnote uses one unique page-local
  `[[FOOTNOTE_N]]` token at the exact point in its owning translated-text block
  and repeats that token in the footnote payload. The pipeline removes the token
  and owns the resulting block ID and Unicode character offset. If ownership is
  not visible, persist an explicit owner-review flag; never guess. Export owned
  notes as inline LaTeX footnotes and preserve unresolved notes as reviewable
  standalone content.
- A table-bearing page immediately receives one batched structured table pass for
  all of its tagged table and table-like regions. That pass receives the same PNG
  and complete page Markdown/OCR. It may modernize historical shorthand into a
  useful target-language GFM row/column structure, but it must preserve supported
  facts and must never invent names, values, dates, units, categories, totals, or
  rows.
- Reconstructed table blocks use
  `segment_handling="table_reconstruction"`, retain their region reason and
  continuation metadata, contain machine GFM Markdown in `translated_text`, and
  keep `source_text` null because the pipeline does not claim an exact cell-level
  transcription. Figures remain text-free manual insertions.
- Previous-page context contains only the configured number of finalized,
  preceding, same-run machine page translations. It is a read-only projection of
  page identity and ordered block content: no prior images, editorial revisions,
  provider metadata, IDs, hashes, tokens, or timestamps. Prompts must require
  current-page-only output and must never repeat, revise, or move prior content.
  This context can help interpret split words and continuations on the current
  page; it cannot retroactively repair a previously finalized page.
- Every new body block carries `paragraph_continuation`; non-body text uses null.
  Only the first main-flow body may continue from the preceding physical page and
  only the final main-flow body may remain unfinished. When the next page confirms
  continuity, the pipeline adds `continues_from_block_id` to the preceding page's
  final body block. The model never owns or returns that trusted link.
- Canonical paragraph fragments remain page-local. Markdown compilation joins a
  confirmed link into one paragraph and puts the new physical-page marker inline
  at the join; editorial revisions remain scoped to the original blocks.
- The pipeline owns IDs, physical page provenance, hashes, provider/model,
  prompt/schema versions, token metadata, timestamps, and fingerprints.
- Pages are independently serializable, retriable, cacheable, and resumable.
- The first-pass checkpoint fingerprint includes both source hashes, complete
  translation config (including previous-page context count), every resolved
  provider setting capable of changing output/schema semantics, provider/model,
  actual main prompt/context hash and version, the table prompt contract hash and
  version, and schema version. The table-pass fingerprint additionally includes
  its exact prompt, first-pass fingerprint, and ordered table targets.
  Timeouts, retry counts, and request-size guards are persisted operational
  provenance but do not invalidate successful translations. Never key a cache by
  page number alone.
- Assembly sorts and requires all physical pages. A current-schema table tag must
  be reconstructed before assembly; only explicitly migrated legacy tables may
  remain translated or manual. Never silently skip a failed page or mark a
  partial document complete.
- Preserve Unicode and source wording. Do not apply irreversible normalization.
- Do not invent confidence/probability values. Model uncertainty is qualitative:
  exact term, proposed rendering, reason, and alternatives.
- Classify footnotes by document function, not size or position. Preserve an
  optional marker and cross-page continuation state; a footnote may fill an
  entire page. Keep page numbers, running matter, watermarks, and printer
  gathering signatures distinct from footnotes.
- Machine output and immutable translation runs already exist. Corrections are
  append-only revisions scoped to
  `(document_id, translation_run_id, block_id)`; never attach a revision to a
  regenerated block by page/order ID alone.
- `ReviewDocument` is an effective projection. It combines immutable machine
  text with the latest contiguous revision history; it is not a second canonical
  machine dataset.
- Review synchronization uses `original_page_number`. Preserve the distinction
  between page identity and visual scroll position; the translated pane drives
  the read-only original pane.
- Uncertainty replacement operates only on exact unresolved occurrences annotated
  by the model and aligned by the editorial service. “Translate All” is available
  only for more than one matching annotated occurrence. Never use unrestricted
  string replacement over reviewer-authored text.

## Configuration and secrets

All user-adjustable non-secret runtime defaults, limits, and available choices
must flow through TOML. Explicit per-job UI selections resolve from that
configuration rather than creating new hidden defaults:

1. add or update a strict nested model in `config.py` or the appropriate domain
   settings model;
2. add the value to `config/default.toml`;
3. wire the resolved value through the composition root/application boundary;
4. persist the relevant resolved snapshot in the manifest or canonical document;
5. add config validation and behavior/checkpoint tests;
6. update README/docs.

Do not hide user-adjustable settings in module constants, CLI defaults, or
environment variables. Schema versions and prompt versions are contracts, not
user settings, and may remain constants.

`.env` and `.env.example` contain only `GEMINI_API_KEY`. Do not add model, path,
language, style, retry, timeout, generation, export, or other non-secret settings
there. Never place secrets in TOML, source, fixtures, manifests, logs, prompts,
provider responses, or test snapshots.

TOML owns all web defaults and limits, including loopback host/port, upload/page
limits, bounded concurrency, status polling, default languages/style/model, and
the selectable-model allowlist. It also owns the default and bounded attempt
count for per-job Auto continue. Persist that resolved operational policy in the
active manifest without adding it to page fingerprints. It also owns
`translation.previous_page_context_count`, which defaults to 2 and is constrained
to 0–10; zero disables prior-page context. The UI may submit explicit per-job
input/output languages, model, style, and term mappings. Resolve those through
strict request models and a per-job config copy, then persist the resolved
non-secret run provenance. Do not turn them into hidden browser, Python, or
environment defaults.

The Settings label is **Save on this computer**. When checked, the narrow secret
adapter writes `GEMINI_API_KEY` to the ignored local `.env`; when unchecked,
the key remains ephemeral and is cleared from the backend job record after use.
Never return, redisplay, prefill, log, or serialize a key. Public status endpoints
may return booleans such as `api_key_configured` and `saved_on_computer`, never the
secret value. Clearing the saved key must remove only `GEMINI_API_KEY` and
preserve unrelated safe file content if such content is encountered.

CLI parameters may identify an input PDF/job, select a TOML file, or authorize an
operation such as `--force`; behavioral translation/extraction/export choices
belong in TOML.

## uv-only environment and dependency workflow

Use `uv` exclusively:

```bash
uv sync --all-groups
uv add <runtime-package>
uv add --dev <development-package>
uv remove <package>
uv run <command>
uv lock --check
uv sync --locked
```

Never use `pip`, `uv pip`, Poetry, Pipenv, Conda, or a manually maintained
`requirements.txt`. Add/remove dependencies through `uv`; do not hand-edit
`uv.lock`. Commit `pyproject.toml` and `uv.lock` together after dependency
changes. Keep `.python-version` and `requires-python` aligned. Prefer the standard
library when adequate.

## Change playbooks

### Persisted schema or block taxonomy

- The current core schema is 5.0. Supported schema 2.0, 3.0, and 4.0 reads migrate only
  in memory: schema 2.0 translated tables remain legacy translated tables and
  schema 3.0 manual table placeholders remain legacy manual tables. Do not
  rewrite or silently schedule either form for reconstruction. Migrated
  footnotes have unknown owners and require review.
- Update Pydantic models in `domain/`.
- Keep `extra="forbid"` and semantic validators.
- Bump `SCHEMA_VERSION` for an incompatible persisted change.
- Decide and document migration or explicit rejection of old artifacts.
- Add JSON round-trip, invalid-input, and compatibility tests.
- Update architecture and config if behavior changes.

### Prompt behavior

- Edit the resource under `prompts/`, not an inline provider string.
- Bump `PROMPT_VERSION` for a semantic main-pass change and
  `TABLE_PROMPT_VERSION` for a semantic table-pass change. The current contracts
  are `translate-page-v6` and `reconstruct-tables-v1`.
- Keep page Markdown visibly delimited as document data.
- Delimit any previous finalized page projection as untrusted, read-only context
  and require current-page-only output.
- Confirm both relevant fingerprints change and add/update prompt tests.
- Never include secrets, prior page images/revisions/provider metadata, or
  unrelated document pages.

### Provider integration

- Implement or change the `PageTranslator` adapter only.
- Keep provider SDK request/response types inside that adapter.
- Return provider-neutral `ProviderResult` and `TableReconstructionResult`
  values from the two protocol methods.
- Use structured output and revalidate it locally.
- Bound timeout/retry behavior from TOML; do not retry permanent errors forever.
- Mock the SDK in normal tests. A live call requires explicit user intent.

### Extraction/rendering

- Preserve the split-first 1:1 physical page contract.
- Keep `original_page_number`, PDF label, Markdown hash, image hash, extraction
  status, and warnings.
- Test with a synthetic multi-page PDF in `tmp_path`.
- Do not make the large `data/panum1850.pdf` a routine test dependency.

### Checkpoint/persistence

- Write canonical page JSON atomically in the same directory before replacement.
- Persist the validated first pass to that page's `translation.json` before a
  required table pass. While it still contains current-schema table tags it is an
  intermediate checkpoint, not an assemblable final page. If table reconstruction
  fails, record the `table_reconstruction` stage and resume from that checkpoint
  without repeating pass one.
- Replace the intermediate page atomically with the completed page and persist
  distinct main/table prompt, response, token, and fingerprint provenance.
- Do not checkpoint base64 image data, keys, or raw provider responses.
- Make failures visible and preserve earlier successful pages.
- Add tests for resume and every new cache-invalidation input.
- Avoid destructive artifact cleanup unless explicitly requested and scoped.

### Editorial/UI work

- Preserve immutable, coexisting translation runs. Retranslation creates a new
  run; retry completes the same failed/in-progress run.
- Store corrections as append-only `BlockRevision` records scoped to document,
  run, and block, with stable IDs and optimistic base versions.
- Build the effective `ReviewDocument` through `EditorialService`; retain machine
  text unchanged and never write corrections into `document.json`.
- Put HTTP and browser code in `interfaces/web/` above application services.
  Routes may compose repositories/services, but browser code must not know
  artifact paths, provider SDK types, or prompt construction.
- Keep the interface a compact colleague workbench. Use the existing tabs and
  plain operational labels. Do not add hero copy, product slogans, testimonials,
  pricing language, onboarding theater, or decorative AI-generated imagery.
- Translate owns PDF selection and per-job language direction. Term mappings owns
  authoritative glossary rows. Settings owns model/style and API-key handling.
  Articles owns the catalog, page-synchronized original/effective translation
  view, validation, uncertainty correction, reading, and reviewed exports.
- Keep only the translated review pane user-scrollable. Use
  `original_page_number` to drive the corresponding original page and preserve
  keyboard/focus/accessibility behavior when rerendering edited blocks.
- Mount the complete translated document. Fetch and display only the active
  physical page image as the translated pane crosses page boundaries; do not
  eagerly load every source image.
- Articles opens as a filesystem-backed catalog of validated completed runs. Use
  the immutable translation-run ID as the stable review identifier after a
  restart, and store the latest physical review page in the run-scoped review
  position sidecar. Do not imply that in-progress jobs or executor state survive.
- Delegate generated mapping-row and review-block input/click/keyboard/paste
  events from their stable containers. Do not attach listeners per rendered
  block, uncertainty, editor, or mapping row.
- Present `table_reconstruction` as machine-reconstructed table content, not as
  exact source transcription or reviewer-authored text. Preserve the append-only
  revision path for corrections. Emit every table at its canonical ordered block
  position with the invisible `[H!]` placement anchor.
- Render uncertainty text from structured offsets or the structured whole-block
  fallback. Offer one-occurrence replacement always for a range highlight and
  all-occurrence replacement only when the API says more than one unresolved
  annotated match exists. The Articles uncertainty list groups unresolved items by
  structured term identity and orders groups by descending occurrence count.
- Section-type, footnote-owner, and marker-offset corrections are append-only
  revision metadata. Prevent footnotes from owning other footnotes, prevent a
  section with owned notes from becoming a footnote until those notes are
  reassigned, and retain an explicit unknown-owner review state.
- Keep exporter policy explicit. LaTeX, Markdown, plain text, and PDF use the latest
  effective revision, regardless of review status; do not silently change them
  to accepted-only behavior. Project each format from canonical data and the
  effective review view; never parse a compiled projection to recover structure.
- Treat the server as loopback-only. The bounded executor, upload aliases, and
  live running state are process-local; completed canonical runs and stopped
  failed manifests are rediscovered from disk by stable translation-run ID.
  Continue a failed attempt in the same run after revalidating its checkpoints;
  cancellation of a stopped attempt must retain them. Do not call that discovery
  a durable queue. Auto continue must remain bounded by TOML and run inline so a
  single-worker executor cannot deadlock by waiting on its own queue.
- Add concurrency/history tests before multi-editor behavior.

### Native executable packaging

- Use only the uv-managed PyInstaller development dependency.
- Preserve one console-capable executable so no arguments launch the workbench
  and CLI arguments retain the complete backend workflow.
- Reject cross-compilation. Build Windows on Windows, macOS on macOS, and Linux
  on Linux; use the oldest intended Linux baseline.
- Bundle the checked-in default TOML, package data, prompts, static web assets,
  MarkItDown/Magika data, and PDFium binaries. Never bundle `.env`, source PDFs,
  generated artifacts, or provider responses.
- `uv run compile --<platform> --clean` may remove only that platform's ignored
  `build/pyinstaller/` and `dist/` subdirectories. It must preserve local secrets,
  personal TOML, artifacts, and editorial metadata.
- Keep frozen config, `.env`, and artifacts in the native per-user application
  data directory, never PyInstaller's temporary extraction directory.
- Keep XeLaTeX external and document that reviewed PDF export requires it.
- Unit-test platform validation, PyInstaller arguments, frozen path materialization,
  and zero-argument/CLI dispatch. Smoke-test the native artifact without Gemini.

For a web change, inspect `interfaces/web/app.py`, its strict schemas and static
assets, `application/web_jobs.py`, `application/editorial.py`, and the matching
tests before editing. Keep upload validation, path confinement, CSRF checks,
no-store headers, model allowlisting, secret redaction, and staged-upload cleanup
intact. Use fake managers/providers in tests; do not spend Gemini tokens to
exercise the interface.

## Testing and verification

Normal tests must never contact an external service. Do not use a real API call to
test wiring.

Run the complete gate before handoff for cross-cutting changes:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
uv lock --check
```

Use focused tests while iterating. Required coverage by change:

- models: strict validation, semantic validation, JSON round-trip;
- prompt: resolved config, bounded previous-page projection, and main/table
  prompt-version behavior;
- compiler: deterministic golden output for affected block types, including
  reconstructed GFM tables and remaining manual figures;
- extraction: physical page/image/Markdown pairing;
- provider: mocked primary and table multimodal payload/schema mapping;
- pipeline: fake-provider end-to-end path, context selection, table batching,
  stage-specific failure/resume, and cache invalidation;
- config: valid defaults, unknown-key failure, and changed behavior;
- filesystem: atomic persistence, schema 2.0/3.0 compatibility, and safe relative
  artifact resolution;
- editorial: revision scope/history, stale-base conflicts, effective views,
  uncertainty offset safety, one/all semantics, and reviewed multi-format export;
- web: CSRF, upload limits/type/path confinement, per-job config resolution,
  model allowlisting, API-key redaction/save/clear behavior, job lifecycle,
  review commands, Articles progress/actions, and reviewed downloads.

Useful focused gate for interface/editorial work:

```bash
uv run pytest \
  tests/unit/interfaces/web \
  tests/unit/application/test_web_jobs.py \
  tests/unit/application/test_editorial.py \
  tests/unit/adapters/test_filesystem_editorial.py
```

Static UI changes also require a local non-provider smoke check of tab navigation,
form validation, translated-pane scroll synchronization, edit controls, and the
conditional Translate All action. If the browser runtime is unavailable, state
that visual/browser behavior was not verified rather than substituting a live
Gemini call.

Any future live test must use `@pytest.mark.live`, be skipped unless both an
explicit opt-in flag and key exist, and be excluded from ordinary CI/development
runs.

## Privacy and artifacts

- Source PDFs, generated page images, extracted page text, translations,
  checkpoints, raw responses, and `.env` are ignored local data.
- Tests generate synthetic documents in temporary directories.
- Do not log or print keys, base64 images, full page text, or full provider
  responses.
- The interface must continue to disclose that the configured provider receives
  current-page images, complete extracted page text, and the configured
  read-only projection of prior finalized machine translations. A table-bearing
  page sends the current image/text twice.
- The source PDF is copied to a short-lived immutable extraction snapshot, hashed
  there, and never copied into the durable job artifact directory.
- Browser uploads are staged under the configured artifact root, use opaque
  server-generated directories, and are removed after the background run.
- The local UI has no authentication. Keep `web.host` restricted by validation
  to loopback literals until an explicit authenticated remote-deployment design
  is approved.
- Do not add a raw-response debug mode without an explicit retention/privacy
  design.

## Definition of done

A task is done only when:

- the change lives in the correct architectural layer;
- relevant strict models and config are wired end-to-end;
- tests cover the success path and important failure/invalidation path;
- README/architecture/roadmap reflect new commands, artifacts, or boundaries;
- configured quality gates pass;
- no secret, source document, or generated artifact was added;
- the handoff states exactly what passed and what was not verified live.
