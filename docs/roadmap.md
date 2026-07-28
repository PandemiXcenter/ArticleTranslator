# Roadmap

The architecture reserves seams for later work without installing a web
framework, database, task queue, or speculative frontend today.

## Phase 1 — backend contracts and local execution

Implemented:

- `uv` project, lockfile, package entry point, lint/type/test tooling;
- strict TOML configuration for every non-secret setting;
- API-key-only `.env`;
- versioned Pydantic domain and provider payloads;
- page-preserving MarkItDown extraction and matching PNG rendering;
- structured Gemini image+Markdown adapter;
- qualitative uncertain-term output;
- per-page atomic checkpoints and safe resume;
- canonical JSON dataset and clean Markdown compiler;
- unit, local-integration, and fake-provider end-to-end tests.

Before calling the backend production-ready:

- exercise representative born-digital and scanned PDFs;
- add provider error classification and telemetry without page text;
- decide rate/cost limits and bounded concurrency;
- add progress events;
- add explicit cancellation and job locking;
- test very large, malformed, rotated, and password-protected PDFs;
- decide retention/deletion policy for generated page images.

## Phase 2 — translation quality controls

- Evaluate prompt/schema output on historical Danish medical text.
- Add reusable glossaries and document-level terminology context in TOML or
  referenced config assets.
- Define how neighboring-page context is supplied without losing independent
  page checkpoints.
- Add source-coverage and duplicate-block diagnostics as warnings, not invented
  confidence.
- Add opt-in schema repair with a strict attempt/cost cap.
- Add provider adapters only when needed; the application contract stays
  provider-neutral.

## Phase 3 — editorial revision model

- Introduce immutable, coexisting translation runs before storing any editorial
  data. Retranslation starts a new run; retry stays in the same run.
- Persist append-only `BlockRevision` records rather than overwriting machine
  translation, scoped to document, translation run, and block.
- Compute an effective block view from machine text plus accepted revision.
- Support review states, editor notes, and optimistic version checks.
- Turn uncertainty entries into a review queue with resolve/defer actions.
- Add block split/merge and type correction while retaining provenance.
- Make every export choose machine, latest editorial, or accepted text by policy.
- Add revision history and reproducible re-export tests.

## Phase 4 — application API

- Add query/use-case services for jobs, pages, blocks, revisions, and progress.
- Replace or complement filesystem persistence with a SQLite adapter after access
  patterns are known.
- Add background execution and cancellation only when the UI needs them.
- Keep provider calls, prompt building, and artifact layout behind application
  ports.
- Add authentication/authorization before multi-user or remote deployment.

## Phase 5 — editor UI

The first UI should provide:

- PDF page image beside source Markdown and structured translated blocks;
- editable translated text and block type;
- visible physical, PDF-label, and printed-page provenance;
- uncertainty highlighting and alternatives;
- translation settings preview before a run;
- per-page retry and job progress;
- filtering by unreviewed, uncertain, failed, or changed blocks;
- accepted-revision export to clean Markdown.

The UI must never call Gemini directly or treat Markdown as canonical storage. It
uses the same application services as the CLI.

## Explicitly deferred

- Pixel-perfect PDF layout reconstruction;
- calibrated probabilities from models that do not expose them;
- silent partial-document compilation;
- arbitrary raw-provider-response storage;
- automatic source-document upload to any provider without a visible user action
  and privacy notice;
- framework selection for the UI before backend/editor access patterns are known.
