# AGENTS.md

These instructions apply to the entire repository.

## Mission and current scope

ArticleTranslator is a backend-first, page-preserving PDF translation pipeline.
The active architecture is:

```text
PDF -> paired per-page Markdown + PNG -> one structured LLM call per page
    -> canonical validated document JSON -> clean Markdown projection
```

Maintain clean seams for a later correction/review UI, but do not add a web
framework, frontend, database, task queue, or speculative deployment stack unless
the task explicitly requires that phase.

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
   composition/config, or documentation.
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
    Pydantic models, enums, errors, schema semantics, future editorial revisions.
    Imports only the standard library and Pydantic.

src/article_translator/ports/
    Provider, extraction, and artifact Protocols. No SDK implementations.

src/article_translator/application/
    Provider-neutral orchestration, prompt assembly, fingerprints, and exporters.
    Depends on domain and ports, never concrete infrastructure.

src/article_translator/adapters/extraction/
    pypdf page splitting, MarkItDown conversion, PDFium rendering.

src/article_translator/adapters/llm/
    Gemini SDK boundary. Google SDK types must not escape this package.

src/article_translator/adapters/storage/
    Filesystem artifact implementation and atomic writes.

src/article_translator/prompts/
    Versioned prompt resources. Prompt behavior is part of cache identity.

src/article_translator/cli.py
    Composition root and command inputs only. No pipeline/business rules.

tests/unit/
    Pure domain/application/config and mocked-adapter behavior.

tests/integration/
    Local filesystem/PDF integrations. Never external network.

tests/e2e/
    Full application execution with fake providers.
```

A future UI belongs in a new interface layer and must call application services.
It must not call Gemini/MarkItDown directly or reconstruct metadata from Markdown.

## Non-negotiable data invariants

- `output/document.json` is canonical. `output/document.md` is a reproducible
  derivative and must never be parsed to recover metadata.
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
- Before editorial data is persisted, introduce immutable translation runs.
  Corrections then become append-only revisions scoped to
  `(document_id, translation_run_id, block_id)`; never attach a revision to a
  regenerated block by page/order ID alone.

## Configuration and secrets

All user-adjustable non-secret runtime settings must flow through TOML:

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

- First add immutable, coexisting translation runs. Retranslation creates a new
  run; retry completes the same run.
- Add corrections as `BlockRevision`-style records scoped to document, run, and
  block, with stable IDs and optimistic base versions.
- Build an effective document view; retain machine text unchanged.
- Put interface code above application services.
- Keep exporter policy explicit: machine, latest revision, or accepted revision.
- Add concurrency/history tests before multi-editor behavior.

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
- filesystem: atomic persistence and safe relative artifact resolution.

Any future live test must use `@pytest.mark.live`, be skipped unless both an
explicit opt-in flag and key exist, and be excluded from ordinary CI/development
runs.

## Privacy and artifacts

- Source PDFs, generated page images, extracted page text, translations,
  checkpoints, raw responses, and `.env` are ignored local data.
- Tests generate synthetic documents in temporary directories.
- Do not log or print keys, base64 images, full page text, or full provider
  responses.
- A user-facing interface must disclose that the configured provider receives
  page images and extracted text.
- The source PDF is copied to a short-lived immutable extraction snapshot, hashed
  there, and never copied into the durable job artifact directory.
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
