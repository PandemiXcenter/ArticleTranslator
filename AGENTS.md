# AGENTS.md

These instructions apply to the entire repository.

## Mission and current scope

ArticleTranslator is a page-preserving PDF translation pipeline with a local
FastAPI workbench for colleagues. The active architecture is:

```text
PDF -> paired per-page Markdown + PNG -> one structured LLM call per page
    -> canonical validated document JSON -> immutable translation run
    -> append-only editorial revisions -> reviewed Markdown projection
```

The web interface is deliberately a small loopback-only internal tool. Preserve
its Translate, Term mappings, Settings, and Review tabs, but do not add a
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
    Command inputs and `serve` entry point only. No pipeline/business rules.

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
  immutable run. Its sibling `document.md` is a reproducible derivative and must
  never be parsed to recover metadata.
- `original_page_number` is the 1-based physical position in the PDF.
- `pdf_page_label` is PDF metadata. `detected_printed_page_label` is visible page
  text. A `page_number` block is retained content. Never merge these meanings.
- Split the PDF before MarkItDown. Whole-document MarkItDown output does not
  preserve reliable page boundaries, and `extract_pages=True` is ignored by the
  installed MarkItDown version.
- Pair Markdown and image from the exact same physical page. Refuse provider work
  when independent page counts disagree.
- Treat the rendered image as primary page evidence and MarkItDown Markdown as
  supplemental context. Empty Markdown must not remove an image page.
- Every provider request contains exactly one page image and the complete
  Markdown for that page.
- The model owns segmentation, order, block type, source text, translated text,
  optional printed label, and qualitative uncertainty only.
- The pipeline owns IDs, physical page provenance, hashes, provider/model,
  prompt/schema versions, token metadata, timestamps, and fingerprints.
- Pages are independently serializable, retriable, cacheable, and resumable.
- The checkpoint fingerprint includes both source hashes, complete translation
  config, every resolved provider setting capable of changing output/schema
  semantics, provider/model, actual prompt hash/version, and schema version.
  Timeouts, retry counts, and request-size guards are persisted operational
  provenance but do not invalidate successful translations. Never key a cache by
  page number alone.
- Assembly sorts and requires all physical pages. Never silently skip a failed
  page or mark a partial document complete.
- Preserve Unicode and source wording. Do not apply irreversible normalization.
- Do not invent confidence/probability values. Model uncertainty is qualitative:
  exact term, proposed rendering, reason, and alternatives.
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
the selectable-model allowlist. The UI may submit explicit per-job input/output
languages, model, style, and term mappings. Resolve those through strict request
models and a per-job config copy, then persist the resolved non-secret run
provenance. Do not turn them into hidden browser, Python, or environment defaults.

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

- Update Pydantic models in `domain/`.
- Keep `extra="forbid"` and semantic validators.
- Bump `SCHEMA_VERSION` for an incompatible persisted change.
- Decide and document migration or explicit rejection of old artifacts.
- Add JSON round-trip, invalid-input, and compatibility tests.
- Update architecture and config if behavior changes.

### Prompt behavior

- Edit the resource under `prompts/`, not an inline provider string.
- Bump `PROMPT_VERSION` for a semantic change after the initial version ships.
- Keep page Markdown visibly delimited as document data.
- Confirm the fingerprint changes and add/update prompt tests.
- Never include secrets or unrelated document pages.

### Provider integration

- Implement or change the `PageTranslator` adapter only.
- Keep provider SDK request/response types inside that adapter.
- Return the provider-neutral `ProviderResult`.
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
  Review owns the page-synchronized original/effective translation view,
  validation, uncertainty correction, and reviewed Markdown download.
- Keep only the translated review pane user-scrollable. Use
  `original_page_number` to drive the corresponding original page and preserve
  keyboard/focus/accessibility behavior when rerendering edited blocks.
- Render uncertainty text from structured offsets or the structured whole-block
  fallback. Offer one-occurrence replacement always for a range highlight and
  all-occurrence replacement only when the API says more than one unresolved
  annotated match exists.
- Keep exporter policy explicit. The current reviewed download uses the latest
  effective revision, regardless of review status; do not silently change it to
  accepted-only behavior.
- Treat the server as loopback-only and process-local. Do not imply that the
  bounded thread executor is a durable queue or that browser job IDs survive a
  restart.
- Add concurrency/history tests before multi-editor behavior.

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
- prompt: resolved config and prompt-version behavior;
- compiler: deterministic golden output for affected block types;
- extraction: physical page/image/Markdown pairing;
- provider: mocked multimodal payload and structured response mapping;
- pipeline: fake-provider end-to-end path, failure/resume, cache invalidation;
- config: valid defaults, unknown-key failure, and changed behavior;
- filesystem: atomic persistence and safe relative artifact resolution;
- editorial: revision scope/history, stale-base conflicts, effective views,
  uncertainty offset safety, one/all semantics, and reviewed export;
- web: CSRF, upload limits/type/path confinement, per-job config resolution,
  model allowlisting, API-key redaction/save/clear behavior, job lifecycle,
  review commands, and reviewed download.

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
  page images and extracted text.
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
